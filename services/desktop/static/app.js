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
    teams: loadMyTeams,
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

  // --- team join requests (top-left notifications) -------------------------- //

  var joinReqPanel = byId("join-requests");
  var jrList = byId("jr-list");
  var joinReqTimer = null;
  var JR_POLL_MS = 20000;

  function joinRequestRow(r) {
    var li = document.createElement("li");

    var who = document.createElement("div");
    who.className = "jr-who";
    var name = document.createElement("div");
    name.className = "jr-name";
    name.textContent = r.displayName || "A player";
    var team = document.createElement("div");
    team.className = "jr-team";
    team.textContent = "→ " + (r.teamName || "your team");
    who.appendChild(name);
    who.appendChild(team);
    li.appendChild(who);

    var approve = document.createElement("button");
    approve.className = "jr-btn jr-approve";
    approve.type = "button";
    approve.textContent = "✓";
    approve.title = "Approve";
    approve.setAttribute("aria-label", "Approve " + (r.displayName || "player"));

    var reject = document.createElement("button");
    reject.className = "jr-btn jr-reject";
    reject.type = "button";
    reject.textContent = "✗";
    reject.title = "Reject";
    reject.setAttribute("aria-label", "Reject " + (r.displayName || "player"));

    approve.addEventListener("click", function () {
      actOnJoinRequest("approve", r, li, [approve, reject]);
    });
    reject.addEventListener("click", function () {
      actOnJoinRequest("reject", r, li, [approve, reject]);
    });
    li.appendChild(approve);
    li.appendChild(reject);
    return li;
  }

  function actOnJoinRequest(action, r, li, btns) {
    if (!lol) return;
    btns.forEach(function (b) { b.disabled = true; });
    var call = action === "approve"
      ? lol.approveJoinRequest(r.teamId, r.userId)
      : lol.rejectJoinRequest(r.teamId, r.userId);
    call.then(function (res) {
      if (res && res.ok) {
        if (li && li.parentNode) li.parentNode.removeChild(li);
        if (jrList && jrList.children.length === 0) show(joinReqPanel, false);
        // Refresh My Teams so an approved member's team reflects the new roster
        // next time it's expanded.
        loadMyTeams();
      } else {
        btns.forEach(function (b) { b.disabled = false; });
      }
    }).catch(function () {
      btns.forEach(function (b) { b.disabled = false; });
    });
  }

  function renderJoinRequests(res) {
    if (!res || !res.ok) return; // fail quietly (keep the last view)
    var reqs = res.requests || [];
    clear(jrList);
    reqs.forEach(function (r) { jrList.appendChild(joinRequestRow(r)); });
    show(joinReqPanel, reqs.length > 0);
  }

  function pollJoinRequests() {
    if (!lol || !lol.teamJoinRequests) return;
    lol.teamJoinRequests().then(renderJoinRequests).catch(function () {});
  }

  function startJoinRequests() {
    if (joinReqTimer !== null) return;
    pollJoinRequests();
    joinReqTimer = setInterval(pollJoinRequests, JR_POLL_MS);
  }

  function stopJoinRequests() {
    if (joinReqTimer !== null) {
      clearInterval(joinReqTimer);
      joinReqTimer = null;
    }
    show(joinReqPanel, false);
  }

  // --- sign-in state -> nav + bell + polling -------------------------------- //

  function setSignedIn(signedIn) {
    show(nav, signedIn);
    show(bell, signedIn);
    if (signedIn) {
      startPresence();
      startJoinRequests();
    } else {
      stopPresence();
      stopJoinRequests();
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
  var tourTeam = byId("tour-team");
  var tourJoinBtn = byId("tour-join-btn");
  var tourJoinStatus = byId("tour-join-status");
  var tourRefreshBtn = byId("tour-refresh-btn");
  var tourList = byId("tour-list");
  var tourEmpty = byId("tour-empty");

  // Populate the "which team enters" dropdown with the teams the user CAPTAINS
  // (only a captain can enrol a team in a 5v5). Keeps the leading "Auto" option.
  function loadCaptainTeamOptions() {
    if (!lol || !lol.myTeams || !tourTeam) return;
    lol.myTeams().then(function (res) {
      // Clear all but the first ("Auto") option.
      while (tourTeam.options.length > 1) tourTeam.remove(1);
      if (res && res.ok) {
        res.teams
          .filter(function (t) { return t.role === "captain"; })
          .forEach(function (t) {
            var opt = document.createElement("option");
            opt.value = t.teamId;
            opt.textContent = t.name;
            tourTeam.appendChild(opt);
          });
      }
    }).catch(function () {});
  }

  function tournamentRow(t) {
    var li = document.createElement("li");
    // Click the name to expand/collapse the bracket inline.
    var title = document.createElement("div");
    title.className = "row-title";
    title.style.cursor = "pointer";
    title.title = "Show bracket";
    title.textContent = "▸ " + (t.name || "Untitled tournament");
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

    var panel = document.createElement("div");
    panel.className = "bracket-panel";
    panel.style.display = "none";
    li.appendChild(panel);

    var open = false;
    var loaded = false;
    title.addEventListener("click", function () {
      open = !open;
      title.textContent = (open ? "▾ " : "▸ ") + (t.name || "Untitled tournament");
      panel.style.display = open ? "" : "none";
      if (open && !loaded) {
        loaded = true;
        loadBracketInto(t, panel);
      }
    });
    return li;
  }

  function loadBracketInto(t, panel) {
    if (!lol || !lol.tournamentBracket) return;
    panel.textContent = "Loading bracket…";
    lol.tournamentBracket(t.id).then(function (res) {
      panel.textContent = "";
      if (res && res.ok) {
        if (!res.matches.length) {
          panel.textContent = "Bracket not generated yet — the tournament hasn't started.";
        } else {
          renderBracket(panel, res.matches);
        }
      } else {
        panel.textContent = (res && res.detail) || "Could not load the bracket.";
      }
    }).catch(function () {
      panel.textContent = "Error loading the bracket.";
    });
  }

  // Label a round by its distance from the final (works for any bracket size).
  function roundLabel(round, maxRound) {
    var fromEnd = maxRound - round;
    if (fromEnd === 0) return "Final";
    if (fromEnd === 1) return "Semifinals";
    if (fromEnd === 2) return "Quarterfinals";
    return "Round " + round;
  }

  function teamLine(name, isWinner) {
    var row = document.createElement("div");
    row.className = "bm-team" + (isWinner ? " bm-win" : "");
    var nm = document.createElement("span");
    nm.className = "bm-name";
    nm.textContent = name || "TBD";
    row.appendChild(nm);
    if (isWinner) {
      var chk = document.createElement("span");
      chk.className = "bm-check";
      chk.textContent = "✓";
      row.appendChild(chk);
    }
    return row;
  }

  function matchCard(m) {
    var card = document.createElement("div");
    card.className = "bracket-match";
    var w = m.winnerTeamId;
    card.appendChild(teamLine(m.teamAName, !!(w && m.teamAId === w)));
    card.appendChild(teamLine(m.teamBName, !!(w && m.teamBId === w)));
    return card;
  }

  function renderBracket(panel, matches) {
    var rounds = {};
    matches.forEach(function (m) {
      (rounds[m.round] = rounds[m.round] || []).push(m);
    });
    var keys = Object.keys(rounds).map(Number).sort(function (a, b) { return a - b; });
    var maxRound = keys[keys.length - 1];
    var wrap = document.createElement("div");
    wrap.className = "bracket";
    keys.forEach(function (rk) {
      var col = document.createElement("div");
      col.className = "bracket-round";
      var h = document.createElement("div");
      h.className = "bracket-round-title";
      h.textContent = roundLabel(rk, maxRound);
      col.appendChild(h);
      rounds[rk].sort(function (a, b) { return a.slot - b.slot; }).forEach(function (m) {
        col.appendChild(matchCard(m));
      });
      wrap.appendChild(col);
    });
    panel.appendChild(wrap);
  }

  function loadMyTournaments() {
    loadCaptainTeamOptions();
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
    var teamId = tourTeam ? tourTeam.value : "";
    tourJoinBtn.disabled = true;
    setStatus(tourJoinStatus, "info", "Joining…");
    lol.joinTournament(code, teamId).then(function (res) {
      tourJoinBtn.disabled = false;
      if (res && res.ok) {
        setStatus(tourJoinStatus, "ok", "Joined " + (res.tournament.name || "the tournament") + ".");
        tourCode.value = "";
        loadMyTournaments();
      } else {
        var r = res || { status: 0 };
        var d = (r.detail || "");
        var msg;
        if (r.status === 404) msg = "Invalid join code.";
        else if (r.status === 409) msg = "That tournament is full.";
        else if (d.indexOf("multiple teams") !== -1) msg = "You captain several teams — pick which one enters from the Team dropdown above.";
        else if (d.indexOf("create a team") !== -1) msg = "This is a 5v5 — create a team first, then enter as its captain.";
        else if (d.indexOf("captain") !== -1) msg = "Only a team captain can enter a team.";
        else if (r.status === 400) msg = d || "That tournament isn't accepting entries.";
        else msg = d || "Could not join the tournament.";
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
        loadMyTeams();
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
        setStatus(
          teamJoinStatus,
          "ok",
          "Request sent to " + (res.team.name || "the team") + " — waiting for the captain to approve.",
        );
        teamCode.value = "";
        loadMyTeams();
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

  if (teamCreateBtn) teamCreateBtn.addEventListener("click", onCreateTeam);
  if (teamJoinBtn) teamJoinBtn.addEventListener("click", onJoinTeam);
  if (teamCreatedCopy) teamCreatedCopy.addEventListener("click", function () {
    copyText(teamCreatedCode.textContent || "", teamCreatedCopy);
  });

  // --- My Teams (membership list + self-service leave) ---------------------- //

  var myTeamsList = byId("myteams-list");
  var myTeamsEmpty = byId("myteams-empty");
  var myTeamsStatus = byId("myteams-status");
  var myTeamsRefreshBtn = byId("myteams-refresh-btn");

  function myTeamRow(t) {
    var li = document.createElement("li");

    var head = document.createElement("div");
    head.className = "row-actions";
    // Click the name to expand/collapse the roster inline (accordion).
    var title = document.createElement("div");
    title.className = "row-title";
    title.style.cursor = "pointer";
    title.title = "Show members";
    var caret = "▸";
    title.textContent = caret + " " + t.name;
    head.appendChild(title);

    // Captains disband the whole team; plain members just leave.
    if (t.role === "captain") {
      head.appendChild(confirmDangerBtn("Disband", "Disbanding…", function (btn) {
        disbandMyTeam(t, btn);
      }));
    } else {
      head.appendChild(confirmDangerBtn("Leave", "Leaving…", function (btn) {
        leaveMyTeam(t, btn);
      }));
    }
    li.appendChild(head);

    var meta = document.createElement("div");
    meta.className = "row-meta";
    var rolePill = document.createElement("span");
    rolePill.className = "pill";
    rolePill.textContent = t.role === "captain" ? "captain (leader)" : t.role;
    meta.appendChild(rolePill);
    if (t.status === "pending") {
      var st = document.createElement("span");
      st.className = "pill";
      st.textContent = "pending approval";
      meta.appendChild(st);
    }
    li.appendChild(meta);

    // The collapsible roster panel (lazy-loaded on first open).
    var panel = document.createElement("ul");
    panel.className = "data-list roster-nested";
    panel.style.display = "none";
    li.appendChild(panel);

    var open = false;
    var loaded = false;
    title.addEventListener("click", function () {
      open = !open;
      title.textContent = (open ? "▾" : "▸") + " " + t.name;
      panel.style.display = open ? "" : "none";
      if (open && !loaded) {
        loaded = true;
        loadRosterInto(t, panel);
      }
    });
    return li;
  }

  function loadRosterInto(t, panel) {
    clear(panel);
    var loading = document.createElement("li");
    loading.textContent = "Loading members…";
    panel.appendChild(loading);
    lol.teamMembers(t.teamId).then(function (res) {
      clear(panel);
      if (res && res.ok) {
        if (!res.members.length) {
          var e = document.createElement("li");
          e.textContent = "No members.";
          panel.appendChild(e);
          return;
        }
        res.members.forEach(function (m) {
          panel.appendChild(rosterMemberRow(t, m, panel));
        });
      } else {
        var er = document.createElement("li");
        er.textContent = (res && res.detail) || "Could not load members.";
        panel.appendChild(er);
      }
    }).catch(function () {
      clear(panel);
      var er = document.createElement("li");
      er.textContent = "Error loading members.";
      panel.appendChild(er);
    });
  }

  function rosterMemberRow(t, m, panel) {
    var li = document.createElement("li");
    var head = document.createElement("div");
    head.className = "row-actions";
    var name = document.createElement("div");
    name.className = "row-title";
    name.textContent = m.displayName || m.userId;
    head.appendChild(name);
    // The captain can remove any non-captain member.
    if (t.role === "captain" && m.role !== "captain") {
      head.appendChild(confirmDangerBtn("Remove", "Removing…", function (btn) {
        lol.removeMember(t.teamId, m.userId).then(function (res) {
          if (res && res.ok) {
            loadRosterInto(t, panel); // refresh the panel
          } else {
            setStatus(myTeamsStatus, "err", (res && res.detail) || "Could not remove the member.");
            btn.disabled = false;
            btn.textContent = "Remove";
          }
        }).catch(function () {
          btn.disabled = false;
          btn.textContent = "Remove";
        });
      }));
    }
    li.appendChild(head);
    var meta = document.createElement("div");
    meta.className = "row-meta";
    var pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = m.role;
    meta.appendChild(pill);
    li.appendChild(meta);
    return li;
  }

  // A danger button with a two-tap "Confirm?" guard; calls onConfirm(btn) on the
  // second tap. The handler resets btn on failure (with its own label).
  function confirmDangerBtn(label, busyLabel, onConfirm) {
    var btn = document.createElement("button");
    btn.className = "btn-danger";
    btn.type = "button";
    var confirming = false;
    btn.textContent = label;
    btn.addEventListener("click", function () {
      if (!confirming) {
        confirming = true;
        btn.textContent = "Confirm?";
        setTimeout(function () {
          if (confirming) { confirming = false; btn.textContent = label; }
        }, 3500);
        return;
      }
      confirming = false;
      btn.disabled = true;
      btn.textContent = busyLabel;
      onConfirm(btn);
    });
    return btn;
  }

  function leaveMyTeam(t, btn) {
    if (!lol || !lol.leaveTeam) return;
    lol.leaveTeam(t.teamId).then(function (res) {
      if (res && res.ok) {
        setStatus(myTeamsStatus, "ok", "Left " + t.name + ".");
        loadMyTeams();
      } else {
        var r = res || { status: 0 };
        var msg = r.status === 409
          ? "Captains can't leave — disband the team instead."
          : (r.detail || "Could not leave the team.");
        setStatus(myTeamsStatus, "err", msg);
        if (btn) { btn.disabled = false; btn.textContent = "Leave"; }
      }
    }).catch(function () {
      setStatus(myTeamsStatus, "err", "Unexpected error leaving the team.");
      if (btn) { btn.disabled = false; btn.textContent = "Leave"; }
    });
  }

  function disbandMyTeam(t, btn) {
    if (!lol || !lol.deleteTeam) return;
    lol.deleteTeam(t.teamId).then(function (res) {
      if (res && res.ok) {
        setStatus(myTeamsStatus, "ok", "Disbanded " + t.name + ".");
        loadMyTeams();
      } else {
        var r = res || { status: 0 };
        var msg = r.status === 403 ? "Only the captain can disband the team."
          : (r.detail || "Could not disband the team.");
        setStatus(myTeamsStatus, "err", msg);
        if (btn) { btn.disabled = false; btn.textContent = "Disband"; }
      }
    }).catch(function () {
      setStatus(myTeamsStatus, "err", "Unexpected error disbanding the team.");
      if (btn) { btn.disabled = false; btn.textContent = "Disband"; }
    });
  }

  function loadMyTeams() {
    if (!lol || !lol.myTeams || !myTeamsList) return;
    setStatus(myTeamsStatus, "info", "");
    lol.myTeams().then(function (res) {
      clear(myTeamsList);
      if (res && res.ok) {
        res.teams.forEach(function (t) { myTeamsList.appendChild(myTeamRow(t)); });
        show(myTeamsEmpty, res.teams.length === 0);
      } else if (!isAuthGone(res)) {
        show(myTeamsEmpty, false);
        setStatus(myTeamsStatus, "err", (res && res.detail) || "Could not load your teams.");
      }
    }).catch(function () {});
  }

  if (myTeamsRefreshBtn) myTeamsRefreshBtn.addEventListener("click", loadMyTeams);

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
        // If that was the LAST linked account, the session has nothing left to
        // act on → sign out and return to pairing (step 1). With >=1 remaining,
        // just refresh the list and stay on the account screen (step 2).
        finishUnlink(who);
      } else {
        setStatus(acctStatus, "err", (res && res.detail) || "Could not unregister the account.");
        loadAccounts();
      }
    }).catch(function () {
      setStatus(acctStatus, "err", "Unexpected error unregistering the account.");
      loadAccounts();
    });
  }

  function finishUnlink(who) {
    if (!lol || !lol.listAccounts) {
      setStatus(acctStatus, "ok", who + " unregistered.");
      loadAccounts();
      return;
    }
    lol.listAccounts().then(function (list) {
      var count = list && list.ok && list.accounts ? list.accounts.length : null;
      if (count === 0 && window.lolOnboarding && window.lolOnboarding.resetToPairing) {
        window.lolOnboarding.resetToPairing(
          who + " unregistered. Signed out — enter a code to link a new account.",
        );
      } else {
        setStatus(acctStatus, "ok", who + " unregistered.");
        loadAccounts();
      }
    }).catch(function () {
      setStatus(acctStatus, "ok", who + " unregistered.");
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
