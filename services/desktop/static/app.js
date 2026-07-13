/*
 * Renderer-side view-manager + Tournaments / Teams / Account screens + the live
 * presence bell. Loaded BEFORE verify.js so it can expose `window.lolNav` (nav
 * visibility + screen switching) that the onboarding flow calls on sign-in /
 * auth-loss. Onboarding (pair + verify) stays owned by verify.js — this module
 * only shows/hides the nav and the top-level screens around it.
 *
 * Runs in the sandboxed renderer (contextIsolation:true, nodeIntegration:false):
 * NO node / network access — it talks only to the main process through the
 * `window.lol` bridge exposed by preload.cts. Loaded as an external script under
 * CSP `default-src 'self'` (no inline JS).
 */
(function () {
  "use strict";

  var lol = window.lol;

  function byId(id) {
    return document.getElementById(id);
  }
  function show(el, visible) {
    if (!el) return;
    if (visible) el.classList.remove("hidden");
    else el.classList.add("hidden");
  }
  function setStatus(el, kind, message) {
    if (!el) return;
    el.className = "status " + kind;
    el.textContent = message;
  }
  function clear(el) {
    if (el) while (el.firstChild) el.removeChild(el.firstChild);
  }

  // --- view manager --------------------------------------------------------- //

  var nav = byId("nav");
  var navButtons = nav ? Array.prototype.slice.call(nav.querySelectorAll("button")) : [];

  function screenEl(name) {
    return byId("screen-" + name);
  }

  var loaders = {
    tournaments: loadMyTournaments,
    account: loadAccounts,
  };

  function showScreen(name) {
    ["onboarding", "tournaments", "teams", "account"].forEach(function (s) {
      show(screenEl(s), s === name);
    });
    navButtons.forEach(function (b) {
      if (b.getAttribute("data-screen") === name) b.classList.add("active");
      else b.classList.remove("active");
    });
    if (loaders[name]) loaders[name]();
  }

  navButtons.forEach(function (b) {
    b.addEventListener("click", function () {
      showScreen(b.getAttribute("data-screen"));
    });
  });

  // --- presence (bell) ------------------------------------------------------ //

  var bell = byId("bell");
  var bellCount = byId("bell-count");
  var presencePanel = byId("presence-panel");
  var presenceList = byId("presence-list");
  var presenceEmpty = byId("presence-empty");
  var presenceTimer = null;
  var POLL_MS = 25000;

  function renderPresence(res) {
    if (!res || !res.ok) {
      // Unreachable / error: keep the last badge, fail quietly (no popups).
      return;
    }
    var count = res.playersInGame || 0;
    if (bellCount) {
      bellCount.textContent = String(count);
      show(bellCount, count > 0);
    }
    clear(presenceList);
    var entries = res.entries || [];
    show(presenceEmpty, entries.length === 0);
    entries.forEach(function (e) {
      var li = document.createElement("li");
      var who = document.createElement("div");
      who.className = "who";
      who.textContent = (e.players && e.players.length) ? e.players.join(" vs ") : "Players in game";
      li.appendChild(who);
      var meta = document.createElement("div");
      meta.className = "meta";
      var parts = [];
      if (e.guildName) parts.push("Server: " + e.guildName);
      if (e.tournamentName) parts.push("Tournament: " + e.tournamentName);
      if (e.gameMode) parts.push("Mode: " + e.gameMode);
      meta.textContent = parts.join("  •  ");
      li.appendChild(meta);
      presenceList.appendChild(li);
    });
  }

  function pollPresence() {
    if (!lol || !lol.presence) return;
    lol.presence().then(renderPresence).catch(function () {});
  }

  function startPresence() {
    if (presenceTimer !== null) return;
    pollPresence();
    presenceTimer = setInterval(pollPresence, POLL_MS);
  }

  function stopPresence() {
    if (presenceTimer !== null) {
      clearInterval(presenceTimer);
      presenceTimer = null;
    }
    if (bellCount) show(bellCount, false);
  }

  if (bell) {
    bell.addEventListener("click", function () {
      var opening = presencePanel && presencePanel.classList.contains("hidden");
      show(presencePanel, opening);
      if (opening) pollPresence();
    });
  }

  // --- sign-in state -> nav + bell + polling -------------------------------- //

  function setSignedIn(signedIn) {
    show(nav, signedIn);
    show(bell, signedIn);
    if (signedIn) {
      startPresence();
    } else {
      stopPresence();
      show(presencePanel, false);
      showScreen("onboarding");
    }
  }

  // Exposed for verify.js (pair success / auth loss) + internal use.
  window.lolNav = { setSignedIn: setSignedIn, showScreen: showScreen };

  // --- helpers for authed calls that may report an expired session ---------- //

  // On an unrecoverable 401 the main process already emits auth-lost; here we
  // just surface it on the given status line without a popup.
  function isAuthGone(res) {
    return res && !res.ok && res.status === 401;
  }

  function copyText(text, btn) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        if (btn) {
          var prev = btn.textContent;
          btn.textContent = "Copied";
          setTimeout(function () {
            btn.textContent = prev;
          }, 1400);
        }
      }).catch(function () {});
    }
  }

  // --- Tournaments ---------------------------------------------------------- //

  var tourCode = byId("tour-code");
  var tourJoinBtn = byId("tour-join-btn");
  var tourJoinStatus = byId("tour-join-status");
  var tourRefreshBtn = byId("tour-refresh-btn");
  var tourList = byId("tour-list");
  var tourEmpty = byId("tour-empty");

  function tournamentRow(t) {
    var li = document.createElement("li");
    var title = document.createElement("div");
    title.className = "row-title";
    title.textContent = t.name || "Untitled tournament";
    li.appendChild(title);
    var meta = document.createElement("div");
    meta.className = "row-meta";
    function pill(text) {
      var s = document.createElement("span");
      s.className = "pill";
      s.textContent = text;
      meta.appendChild(s);
    }
    if (t.status) pill(t.status);
    if (t.format) pill(t.format);
    if (t.gameMode) pill(t.gameMode);
    if (t.bestOf) pill("Bo" + t.bestOf);
    if (t.maxTeams) pill(t.maxTeams + " teams");
    if (t.isSharedToCentral) pill("shared");
    li.appendChild(meta);
    return li;
  }

  function loadMyTournaments() {
    if (!lol || !lol.listMyTournaments || !tourList) return;
    lol.listMyTournaments().then(function (res) {
      clear(tourList);
      if (res && res.ok) {
        res.tournaments.forEach(function (t) {
          tourList.appendChild(tournamentRow(t));
        });
        show(tourEmpty, res.tournaments.length === 0);
      } else if (!isAuthGone(res)) {
        show(tourEmpty, false);
      }
    }).catch(function () {});
  }

  function onJoinTournament() {
    if (!lol || !lol.joinTournament) return;
    var code = (tourCode.value || "").trim();
    if (!code) {
      setStatus(tourJoinStatus, "err", "Enter a tournament code.");
      return;
    }
    tourJoinBtn.disabled = true;
    setStatus(tourJoinStatus, "info", "Joining…");
    lol.joinTournament(code).then(function (res) {
      tourJoinBtn.disabled = false;
      if (res && res.ok) {
        setStatus(tourJoinStatus, "ok", "Joined " + (res.tournament.name || "the tournament") + ".");
        tourCode.value = "";
        loadMyTournaments();
      } else {
        var r = res || { status: 0 };
        var msg = r.status === 404 ? "Invalid join code."
          : r.status === 400 ? "That tournament is closed."
          : (r.detail || "Could not join the tournament.");
        setStatus(tourJoinStatus, "err", msg);
      }
    }).catch(function () {
      tourJoinBtn.disabled = false;
      setStatus(tourJoinStatus, "err", "Unexpected error joining the tournament.");
    });
  }

  if (tourJoinBtn) tourJoinBtn.addEventListener("click", onJoinTournament);
  if (tourRefreshBtn) tourRefreshBtn.addEventListener("click", loadMyTournaments);

  // --- Teams ---------------------------------------------------------------- //

  var teamName = byId("team-name");
  var teamCreateBtn = byId("team-create-btn");
  var teamCreateStatus = byId("team-create-status");
  var teamCreated = byId("team-created");
  var teamCreatedCode = byId("team-created-code");
  var teamCreatedCopy = byId("team-created-copy");

  var teamCode = byId("team-code");
  var teamJoinBtn = byId("team-join-btn");
  var teamJoinStatus = byId("team-join-status");

  var teamIdInput = byId("team-id");
  var teamViewBtn = byId("team-view-btn");
  var teamViewStatus = byId("team-view-status");
  var teamViewCode = byId("team-view-code");
  var teamViewCodeValue = byId("team-view-code-value");
  var teamViewCodeCopy = byId("team-view-code-copy");
  var teamRoster = byId("team-roster");

  function onCreateTeam() {
    if (!lol || !lol.createTeam) return;
    var name = (teamName.value || "").trim();
    if (!name) {
      setStatus(teamCreateStatus, "err", "Enter a team name.");
      return;
    }
    teamCreateBtn.disabled = true;
    setStatus(teamCreateStatus, "info", "Creating…");
    lol.createTeam(name).then(function (res) {
      teamCreateBtn.disabled = false;
      if (res && res.ok) {
        setStatus(teamCreateStatus, "ok", "Team created. You're the captain.");
        teamCreatedCode.textContent = res.team.joinCode;
        show(teamCreated, true);
        if (teamIdInput) teamIdInput.value = res.team.id;
      } else {
        setStatus(teamCreateStatus, "err", (res && res.detail) || "Could not create the team.");
      }
    }).catch(function () {
      teamCreateBtn.disabled = false;
      setStatus(teamCreateStatus, "err", "Unexpected error creating the team.");
    });
  }

  function onJoinTeam() {
    if (!lol || !lol.joinTeam) return;
    var code = (teamCode.value || "").trim();
    if (!code) {
      setStatus(teamJoinStatus, "err", "Enter a team code.");
      return;
    }
    teamJoinBtn.disabled = true;
    setStatus(teamJoinStatus, "info", "Joining…");
    lol.joinTeam(code).then(function (res) {
      teamJoinBtn.disabled = false;
      if (res && res.ok) {
        setStatus(teamJoinStatus, "ok", "Joined " + (res.team.name || "the team") + ".");
        teamCode.value = "";
        if (teamIdInput) teamIdInput.value = res.team.id;
      } else {
        var r = res || { status: 0 };
        var msg = r.status === 404 ? "Invalid team code." : (r.detail || "Could not join the team.");
        setStatus(teamJoinStatus, "err", msg);
      }
    }).catch(function () {
      teamJoinBtn.disabled = false;
      setStatus(teamJoinStatus, "err", "Unexpected error joining the team.");
    });
  }

  function memberRow(m) {
    var li = document.createElement("li");
    var title = document.createElement("div");
    title.className = "row-title";
    title.textContent = m.userId;
    li.appendChild(title);
    var meta = document.createElement("div");
    meta.className = "row-meta";
    var pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = m.role;
    meta.appendChild(pill);
    li.appendChild(meta);
    return li;
  }

  function onViewTeam() {
    if (!lol || !lol.teamMembers) return;
    var id = (teamIdInput.value || "").trim();
    if (!id) {
      setStatus(teamViewStatus, "err", "Enter a team ID.");
      return;
    }
    teamViewBtn.disabled = true;
    setStatus(teamViewStatus, "info", "Loading roster…");
    show(teamViewCode, false);
    clear(teamRoster);
    lol.teamMembers(id).then(function (res) {
      teamViewBtn.disabled = false;
      if (res && res.ok) {
        res.members.forEach(function (m) {
          teamRoster.appendChild(memberRow(m));
        });
        setStatus(teamViewStatus, "ok", res.members.length + " member(s).");
      } else {
        var r = res || { status: 0 };
        var msg = r.status === 404 ? "Team not found." : (r.detail || "Could not load the roster.");
        setStatus(teamViewStatus, "err", msg);
      }
    }).catch(function () {
      teamViewBtn.disabled = false;
      setStatus(teamViewStatus, "err", "Unexpected error loading the roster.");
    });
    // Best-effort: captains also get the join code (non-captains 403 -> hidden).
    if (lol.teamJoinCode) {
      lol.teamJoinCode(id).then(function (res) {
        if (res && res.ok && res.joinCode) {
          teamViewCodeValue.textContent = res.joinCode;
          show(teamViewCode, true);
        }
      }).catch(function () {});
    }
  }

  if (teamCreateBtn) teamCreateBtn.addEventListener("click", onCreateTeam);
  if (teamJoinBtn) teamJoinBtn.addEventListener("click", onJoinTeam);
  if (teamViewBtn) teamViewBtn.addEventListener("click", onViewTeam);
  if (teamCreatedCopy) teamCreatedCopy.addEventListener("click", function () {
    copyText(teamCreatedCode.textContent || "", teamCreatedCopy);
  });
  if (teamViewCodeCopy) teamViewCodeCopy.addEventListener("click", function () {
    copyText(teamViewCodeValue.textContent || "", teamViewCodeCopy);
  });

  // --- Account (linked Riot IDs + unregister) ------------------------------- //

  var acctList = byId("acct-list");
  var acctEmpty = byId("acct-empty");
  var acctStatus = byId("acct-status");
  var acctRefreshBtn = byId("acct-refresh-btn");

  function accountRow(a) {
    var li = document.createElement("li");
    var actions = document.createElement("div");
    actions.className = "row-actions";

    var title = document.createElement("div");
    title.className = "row-title";
    var rid = a.gameName && a.tagLine ? a.gameName + "#" + a.tagLine : "Linked account";
    title.textContent = rid;
    actions.appendChild(title);

    var btn = document.createElement("button");
    btn.className = "btn-danger";
    btn.type = "button";
    var confirming = false;
    btn.textContent = "Unregister";
    btn.addEventListener("click", function () {
      if (!confirming) {
        confirming = true;
        btn.textContent = "Confirm?";
        setTimeout(function () {
          if (confirming) {
            confirming = false;
            btn.textContent = "Unregister";
          }
        }, 3500);
        return;
      }
      confirming = false;
      btn.disabled = true;
      btn.textContent = "Removing…";
      unlink(a.accountId);
    });
    actions.appendChild(btn);
    li.appendChild(actions);

    var meta = document.createElement("div");
    meta.className = "row-meta";
    function pill(text) {
      var s = document.createElement("span");
      s.className = "pill";
      s.textContent = text;
      meta.appendChild(s);
    }
    if (a.region) pill(a.region);
    pill(a.verified ? "verified" : "unverified");
    if (a.rankText) pill(a.rankText);
    li.appendChild(meta);
    return li;
  }

  function loadAccounts() {
    if (!lol || !lol.listAccounts || !acctList) return;
    setStatus(acctStatus, "info", "");
    lol.listAccounts().then(function (res) {
      clear(acctList);
      if (res && res.ok) {
        res.accounts.forEach(function (a) {
          acctList.appendChild(accountRow(a));
        });
        show(acctEmpty, res.accounts.length === 0);
      } else if (!isAuthGone(res)) {
        show(acctEmpty, false);
        setStatus(acctStatus, "err", (res && res.detail) || "Could not load your accounts.");
      }
    }).catch(function () {});
  }

  function unlink(accountId) {
    if (!lol || !lol.unlinkAccount) return;
    setStatus(acctStatus, "info", "Unregistering…");
    lol.unlinkAccount(accountId).then(function (res) {
      if (res && res.ok) {
        var who = res.account && res.account.gameName && res.account.tagLine
          ? res.account.gameName + "#" + res.account.tagLine : "Account";
        setStatus(acctStatus, "ok", who + " unregistered.");
        loadAccounts();
      } else {
        setStatus(acctStatus, "err", (res && res.detail) || "Could not unregister the account.");
        loadAccounts();
      }
    }).catch(function () {
      setStatus(acctStatus, "err", "Unexpected error unregistering the account.");
      loadAccounts();
    });
  }

  if (acctRefreshBtn) acctRefreshBtn.addEventListener("click", loadAccounts);

  // --- init ----------------------------------------------------------------- //

  if (lol) {
    if (lol.onAuthLost) {
      lol.onAuthLost(function () {
        setSignedIn(false);
      });
    }
    if (lol.getSession) {
      lol.getSession().then(function (state) {
        setSignedIn(!!(state && state.signedIn));
      }).catch(function () {
        setSignedIn(false);
      });
    }
  }
})();
