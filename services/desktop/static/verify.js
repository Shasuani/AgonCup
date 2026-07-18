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
  var linkedList = document.getElementById("linked-list");

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

  // --- linked-accounts list (this session) ---------------------------------- //

  // Tracks Riot IDs already shown so re-verifying the same account (the backend
  // dedupes on PUUID) doesn't add a duplicate row.
  var linkedSeen = {};

  // Append one linked account to the list. Accepts either the pair `account`
  // summary or a verify result — both expose gameName / tagLine / rankText.
  function addLinkedAccount(acc) {
    if (!acc || !linkedList) return;
    var rid = acc.gameName && acc.tagLine ? acc.gameName + "#" + acc.tagLine : null;
    var key = rid || acc.puuid || "acct";
    if (linkedSeen[key]) return;
    linkedSeen[key] = true;

    var li = document.createElement("li");
    var chk = document.createElement("span");
    chk.className = "chk";
    chk.textContent = "✓";
    li.appendChild(chk);

    var name = document.createElement("span");
    name.className = "rid";
    name.textContent = rid || "Account linked";
    li.appendChild(name);

    if (acc.rankText) {
      var rank = document.createElement("span");
      rank.className = "rank";
      rank.textContent = acc.rankText;
      li.appendChild(rank);
    }
    linkedList.appendChild(li);
    show(linkedList, true);
  }

  // Once at least one account is linked, the verify button becomes "Link another".
  function markHasLinked() {
    verifyBtn.textContent = "Link another account";
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
    loadLinkedAccounts();
  }

  // Clear the local session and return to step 1 (pairing). Used when a signed-in
  // session has zero linked accounts — on boot, or right after the user
  // unregisters their LAST account. An account-less session can orchestrate
  // nothing, so we sign out rather than strand the user on the verify step.
  function resetToPairing(message) {
    var toPairing = function () {
      if (window.lolNav) window.lolNav.setSignedIn(false);
      showPairing();
      if (message) setStatus(pairStatus, "info", message);
    };
    if (window.lol && window.lol.signOut) {
      window.lol.signOut().then(toPairing).catch(toPairing);
    } else {
      toPairing();
    }
  }

  // Pull the signed-in user's already-linked accounts from the backend so the
  // list + "Link another account" affordance show on a cold reopen (not just
  // after a fresh verify this session). Non-fatal on any failure.
  function loadLinkedAccounts() {
    if (!window.lol || !window.lol.listAccounts) return;
    window.lol
      .listAccounts()
      .then(function (res) {
        if (res && res.ok && res.accounts && res.accounts.length) {
          res.accounts.forEach(addLinkedAccount);
          markHasLinked();
        }
      })
      .catch(function () {
        /* best-effort — the verify flow still works without the prefilled list */
      });
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
        // Reveal the top-level nav + live-presence bell (owned by app.js).
        if (window.lolNav) window.lolNav.setSignedIn(true);
        showVerify();
        if (result.account && result.account.linked && result.account.verified) {
          addLinkedAccount(result.account);
          markHasLinked();
          setStatus(
            verifyStatus,
            "ok",
            "Already linked. To add another account, switch your League client and verify again.",
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
        addLinkedAccount(result);
        markHasLinked();
        var who =
          result.gameName && result.tagLine ? result.gameName + "#" + result.tagLine : "your account";
        var rank = result.rankText ? " (" + result.rankText + ")" : "";
        setStatus(
          verifyStatus,
          "ok",
          "Linked " + who + rank + ". To add another, switch your League client and verify again.",
        );
        // Re-enable so the player can link a second account (gated on LCU online).
        verifyBtn.disabled = lcuStatus !== "online";
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

    // Choose the initial view from session + linked-account count:
    //   not signed in            -> pairing (step 1)
    //   signed in, >=1 account   -> verify / manage (step 2)
    //   signed in, 0 accounts    -> nothing to act on: sign out -> pairing (step 1)
    window.lol
      .getSession()
      .then(function (state) {
        if (!state || !state.signedIn) {
          showPairing();
          return;
        }
        if (!window.lol.listAccounts) {
          showVerify();
          return;
        }
        window.lol
          .listAccounts()
          .then(function (res) {
            var count = res && res.ok && res.accounts ? res.accounts.length : null;
            if (count === 0) {
              resetToPairing();
            } else {
              showVerify();
            }
          })
          .catch(function () {
            showVerify();
          });
      })
      .catch(function () {
        showPairing();
      });

    // Let app.js (owner of the account screen) return here after the user
    // unregisters their last account.
    window.lolOnboarding = { showPairing: showPairing, resetToPairing: resetToPairing };
  } else {
    showPairing();
    setStatus(pairStatus, "err", "Desktop bridge unavailable. Restart the app.");
  }
})();
