/*
 * Renderer-side logic for sign-in (pairing) + League account linking.
 *
 * Runs in the sandboxed renderer (contextIsolation:true, nodeIntegration:false).
 * It has NO node / network access — it talks only to the main process through
 * the `window.lol` bridge exposed by preload.cts:
 *   - window.lol.getSession()      -> pairing vs verify view on load.
 *   - window.lol.pair(code, label) -> exchange a one-time code for a session.
 *   - window.lol.verify()          -> authenticated step-2 LCU account link.
 *   - window.lol.onLcuStatus(cb)   -> persistent LCU reachability badge.
 *   - window.lol.onAuthLost(cb)    -> session expired -> back to pairing.
 *
 * Loaded as an external script under CSP `default-src 'self'` (no inline JS).
 */
(function () {
  "use strict";

  var codeInput = document.getElementById("code");
  var pairBtn = document.getElementById("pair-btn");
  var pairStatus = document.getElementById("pair-status");
  var pairCard = document.getElementById("pair-card");

  var verifyCard = document.getElementById("verify-card");
  var verifyBtn = document.getElementById("verify-btn");
  var verifyStatus = document.getElementById("verify-status");

  var lcuDot = document.querySelector("#lcu-badge .dot");
  var lcuLabel = document.getElementById("lcu-label");

  // Latest LCU status; "Verify with League" is enabled ONLY when online.
  var lcuStatus = "offline";
  var deviceLabel = navigator.platform ? "Desktop — " + navigator.platform : "Desktop app";

  function setStatus(el, kind, message) {
    el.className = "status " + kind;
    el.textContent = message;
  }

  function show(el, visible) {
    if (visible) {
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  }

  // --- view switching ------------------------------------------------------- //

  function showPairing() {
    show(pairCard, true);
    show(verifyCard, false);
    codeInput.disabled = false;
    pairBtn.disabled = false;
  }

  function showVerify() {
    show(pairCard, false);
    show(verifyCard, true);
    applyLcuStatus(lcuStatus);
  }

  // --- LCU badge ------------------------------------------------------------ //

  function applyLcuStatus(status) {
    lcuStatus = status;
    var cls = "dot ";
    var text = "";
    if (status === "online") {
      cls += "ok";
      text = "League client connected — ready to verify.";
    } else if (status === "loading") {
      cls += "warn";
      text = "League client starting… sign in to your account.";
    } else {
      cls += "off";
      text = "League client not running. Open League of Legends.";
    }
    if (lcuDot) lcuDot.className = cls;
    if (lcuLabel) lcuLabel.textContent = text;
    // Verify is gated on the client being fully online (green).
    if (!verifyCard.classList.contains("hidden")) {
      verifyBtn.disabled = status !== "online";
    }
  }

  // --- code formatting ------------------------------------------------------ //

  // Crockford-base32 alphabet minus ambiguous glyphs (mirrors the backend).
  var ALLOWED = /[ABCDEFGHJKMNPQRSTUVWXYZ23456789]/g;

  function formatCode(raw) {
    var cleaned = (raw.toUpperCase().match(ALLOWED) || []).join("").slice(0, 8);
    if (cleaned.length > 4) {
      return cleaned.slice(0, 4) + "-" + cleaned.slice(4);
    }
    return cleaned;
  }

  codeInput.addEventListener("input", function () {
    var caretAtEnd = codeInput.selectionStart === codeInput.value.length;
    codeInput.value = formatCode(codeInput.value);
    if (caretAtEnd) {
      codeInput.selectionStart = codeInput.selectionEnd = codeInput.value.length;
    }
  });

  // --- pairing (step 1) ----------------------------------------------------- //

  function describePairError(result) {
    if (result.status === 401) {
      return "That code is invalid or expired. Run /register again for a fresh one.";
    }
    if (result.status === 422) {
      return "That code looks malformed. Double-check it and try again.";
    }
    if (result.status === 429) {
      return "Too many attempts. Wait a minute and try again.";
    }
    if (result.status === 0) {
      return result.detail || "Could not reach the backend. Is it running?";
    }
    return result.detail || "Sign-in failed. Please try again.";
  }

  async function onPair() {
    if (!window.lol) {
      setStatus(pairStatus, "err", "Desktop bridge unavailable. Restart the app.");
      return;
    }
    var code = codeInput.value.trim();
    if (code.replace(/-/g, "").length < 8) {
      setStatus(pairStatus, "err", "Enter the full 8-character code from Discord.");
      return;
    }

    pairBtn.disabled = true;
    setStatus(pairStatus, "info", "Signing in…");
    try {
      var result = await window.lol.pair(code, deviceLabel);
      if (result && result.ok) {
        setStatus(pairStatus, "ok", "Signed in! Now link your League account.");
        showVerify();
        if (result.account && result.account.linked && result.account.verified) {
          setStatus(
            verifyStatus,
            "ok",
            "Your League account is already linked" +
              (result.account.rankText ? " — " + result.account.rankText : "") +
              ".",
          );
        }
      } else {
        setStatus(pairStatus, "err", describePairError(result || { status: 0 }));
        pairBtn.disabled = false;
      }
    } catch (err) {
      setStatus(pairStatus, "err", "Unexpected error: " + String(err));
      pairBtn.disabled = false;
    }
  }

  // --- verify (step 2) ------------------------------------------------------ //

  function describeVerifyError(result) {
    if (result.status === 401) {
      return result.detail || "Your session expired. Enter a new code to sign in.";
    }
    if (result.status === 404) {
      return "Your Riot ID was not found. Make sure you're signed into the right account.";
    }
    if (result.status === 409) {
      return "This Riot account is already linked to another user.";
    }
    if (result.status === 422) {
      return "Your region looks unsupported. Double-check your League client.";
    }
    if (result.status === 502) {
      return "Riot's account service is unavailable. Try again in a moment.";
    }
    if (result.status === 0) {
      return result.detail || "Could not read your League client or reach the backend.";
    }
    return result.detail || "Verification failed. Please try again.";
  }

  async function onVerify() {
    if (!window.lol) {
      setStatus(verifyStatus, "err", "Desktop bridge unavailable. Restart the app.");
      return;
    }
    if (lcuStatus !== "online") {
      setStatus(verifyStatus, "err", "Open League of Legends and sign in first.");
      return;
    }

    verifyBtn.disabled = true;
    setStatus(verifyStatus, "info", "Reading your League client…");
    try {
      var result = await window.lol.verify();
      if (result && result.ok) {
        var linked = "Linked!";
        if (result.rankText) {
          linked = "Linked! " + result.rankText;
        }
        setStatus(verifyStatus, "ok", linked + " You can close this window and return to Discord.");
      } else {
        var r = result || { status: 0 };
        setStatus(verifyStatus, "err", describeVerifyError(r));
        // A 401 means the session is gone — drop back to the pairing screen.
        if (r.status === 401) {
          showPairing();
          setStatus(pairStatus, "err", "Your session expired. Enter a new code to sign in.");
        } else {
          verifyBtn.disabled = lcuStatus !== "online";
        }
      }
    } catch (err) {
      setStatus(verifyStatus, "err", "Unexpected error: " + String(err));
      verifyBtn.disabled = lcuStatus !== "online";
    }
  }

  // --- wiring --------------------------------------------------------------- //

  pairBtn.addEventListener("click", onPair);
  codeInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") onPair();
  });
  verifyBtn.addEventListener("click", onVerify);

  if (window.lol) {
    window.lol.onLcuStatus(applyLcuStatus);
    window.lol.onAuthLost(function () {
      showPairing();
      setStatus(pairStatus, "err", "Your session expired. Enter a new code to sign in.");
    });

    // Choose the initial view from the current session state.
    window.lol
      .getSession()
      .then(function (state) {
        if (state && state.signedIn) {
          showVerify();
        } else {
          showPairing();
        }
      })
      .catch(function () {
        showPairing();
      });
  } else {
    showPairing();
    setStatus(pairStatus, "err", "Desktop bridge unavailable. Restart the app.");
  }
})();
