/*
 * Renderer-side logic for the "Link your account" form.
 *
 * Runs in the sandboxed renderer (contextIsolation:true, nodeIntegration:false).
 * It has NO node / network access — it talks only to the main process through
 * the `window.lol` bridge exposed by preload.ts:
 *   - window.lol.readSummoner()        -> confirm League is running.
 *   - window.lol.submitVerification()  -> read LCU PUUID + POST to the backend.
 *
 * Loaded as an external script under CSP `default-src 'self'` (no inline JS).
 */
(function () {
  "use strict";

  var codeInput = document.getElementById("code");
  var btn = document.getElementById("verify-btn");
  var statusEl = document.getElementById("status");

  function setStatus(kind, message) {
    statusEl.className = "status " + kind;
    statusEl.textContent = message;
  }

  // Crockford-base32 alphabet minus ambiguous glyphs (mirrors the backend).
  var ALLOWED = /[ABCDEFGHJKMNPQRSTUVWXYZ23456789]/g;

  // Auto-uppercase, strip invalid chars, and re-insert the XXXX-XXXX hyphen.
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

  function describeError(result) {
    if (result.status === 404) {
      return "That code is invalid or expired. Run /register again for a fresh one.";
    }
    if (result.status === 409) {
      return "This Riot account is already linked to another user.";
    }
    if (result.status === 422) {
      return "The code or region looks malformed. Double-check and try again.";
    }
    if (result.status === 429) {
      return "Too many attempts. Wait a minute and try again.";
    }
    if (result.status === 0) {
      return result.detail || "Could not reach the backend. Is it running?";
    }
    return result.detail || "Verification failed. Please try again.";
  }

  async function onVerify() {
    if (!window.lol) {
      setStatus("err", "Desktop bridge unavailable. Restart the app.");
      return;
    }

    var code = codeInput.value.trim();
    if (code.replace(/-/g, "").length < 8) {
      setStatus("err", "Enter the full 8-character code from Discord.");
      return;
    }

    btn.disabled = true;
    setStatus("info", "Checking your League client…");

    try {
      var summoner = await window.lol.readSummoner();
      if (!summoner || !summoner.ok) {
        setStatus(
          "err",
          "Open League of Legends and sign in, then try again.",
        );
        return;
      }

      var riotId =
        summoner.gameName && summoner.tagLine
          ? summoner.gameName + "#" + summoner.tagLine
          : "your account";
      setStatus("info", "Linking " + riotId + "…");

      var result = await window.lol.submitVerification({ code: code });

      if (result && result.ok) {
        var linked = "Linked!";
        if (result.rankText) {
          linked = "Linked! " + riotId + " — " + result.rankText;
        }
        setStatus("ok", linked + " You can close this window and return to Discord.");
        codeInput.disabled = true;
      } else {
        setStatus("err", describeError(result || { status: 0 }));
      }
    } catch (err) {
      setStatus("err", "Unexpected error: " + String(err));
    } finally {
      if (statusEl.className.indexOf("ok") === -1) {
        btn.disabled = false;
      }
    }
  }

  btn.addEventListener("click", onVerify);
  codeInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      onVerify();
    }
  });
})();
