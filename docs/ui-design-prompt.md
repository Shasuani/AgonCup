# LeagueOfLeagues — Desktop Client UI Design Prompt

> Paste everything below the line into a design tool (v0, Lovable, Figma Make, Claude,
> Stitch…). It describes the **real** app as built in `services/desktop/renderer/`.
> Trim the sections you don't need. Delete this header first.

---

## Role

You are designing the UI for **LeagueOfLeagues**, a desktop companion app for
*League of Legends* tournaments. Produce a high-fidelity, production-ready design —
not a moodboard. I want screens I can implement directly in React.

## What the product is

A tournament platform for *League of Legends* communities. Discord servers ("guilds")
run their own tournaments — up to 16 teams each, many tournaments in parallel. The
desktop client is what **players** use; admins use a separate web dashboard and a
Discord bot.

The thing that makes this app unusual: **there is no official Riot tournament API for
custom games**, so the app drives matches through the player's *local* League client.
When a match is ready, one player's machine is elected host, and the app creates the
custom lobby, invites the other nine players by PUUID, auto-accepts, and starts the
game — automatically. If the host's client dies mid-flight, hosting fails over to
another player. So this app is a **background agent that occasionally becomes the most
important window on screen**, not a dashboard people stare at.

Match results are self-reported by all ten clients and cross-checked for agreement
(quorum + anomaly detection + admin override), because any single client's report is
forgeable. The UI should never imply results are authoritative or tamper-proof.

## Who uses it, and when

Players aged ~16–30 who already live inside the League client and Discord. They open
this app **before** a match and glance at it **during** one. Most sessions are short
and goal-directed: "am I in the bracket", "is my team ready", "why isn't the lobby
starting". Long idle periods where the app sits in the background matter as much as
active use — design for the glance.

## Platform & hard constraints

These are not negotiable; the implementation will break otherwise.

- **Electron desktop app**, Windows + macOS. Not a website, not responsive-to-mobile.
- **Window: 1280×820 default, 1120×720 minimum.** Design at 1280×820. No mobile
  breakpoints. Content scrolls vertically in the main pane only.
- **Strict Content-Security-Policy (`default-src 'self'`)** — the app cannot fetch
  anything from the network for presentation. That means:
  - **No CDN fonts.** Google Fonts will not load. Any typeface must be bundled or be
    a system fallback.
  - **No icon libraries loaded at runtime, no remote images, no external CSS.**
    Icons must be **inline SVG** or bundled assets. (The current build uses bare
    Unicode glyphs — `◆ ▸ ❖ ⬡ ⬢` — as icons, which is a weakness I want you to fix
    with a proper bundled icon set.)
  - No remote avatars — render initials in a shape instead.
- **Dark theme only.** This app sits next to the League client; a light mode would be
  wrong. Do not design one.
- Custom title bar, 44px tall, app-drag region.

## Existing design language (keep and elevate)

The current build uses a "hextech" look derived from League's own UI: deep navy-black,
antique gold, and a teal accent. **Keep this direction** — refine it, don't replace it.
Exact tokens in use:

```
bg          #010a13   (near-black navy — app background)
panel       #0a1626   (card / sidebar surface)
panel2      #0e1e33
panel3      #12253d
gold        #c8aa6e   (primary accent, borders, active nav)
goldBright  #f0e6d2   (headings, emphasis text)
goldDim     #785a28   (deep gold, gradients)
teal        #0ac8b9   (success / live / connected)
tealDeep    #0596aa
red         #c44a4a   (errors, destructive, "live" pulse)
text        #a09b8c   (body)
textStrong  #f0e6d2
textDim     #5b5a4d   (labels, metadata)
line        rgba(200,170,110,.22)   (hairline borders)
lineSoft    rgba(200,170,110,.10)
```

Type: **Cinzel** (serif display — headings, team/tournament names) + **Barlow**
(sans body) + a monospace for codes/IDs. Both must be bundled or degrade to
`Georgia, serif` / `Helvetica, sans-serif`. Uppercase micro-labels with wide letter
spacing (`.14em`–`.22em`) are a signature of the current look — keep that.

Shape language: **sharp** — 2–3px radii, hairline gold borders, no soft rounded
cards, no drop shadows. Depth comes from thin borders and subtle radial gradients,
not elevation.

**One caution:** body text `#a09b8c` on `#010a13` is on the edge of legibility, and
`textDim #5b5a4d` fails WCAG AA outright. Please keep the mood but raise contrast to
at least AA (4.5:1) for anything a user actually needs to read; reserve the dimmest
tone for decorative labels only.

## Global layout

```
┌────────────────────────────────────────────────────────────────┐
│ ◆ LEAGUEOFLEAGUES  · Desktop Client v3        [LCU Connected ●] │ 44px title bar
├──────────────┬─────────────────────────────────────────────────┤
│ SIGNED IN    │                                                 │
│ (avatar)     │                                                 │
│ Riot ID      │           main content, scrolls                 │
│ region·rank  │           padding 36px 44px                     │
│              │                                                 │
│ ◆ Home       │                                                 │
│ ▸ Live Match │                                                 │
│ ❖ Tournaments│                                                 │
│ ⬡ Teams      │                                                 │
│ ⬢ Accounts   │                                                 │
│              │                                                 │
│ Presence  ◆3 │                                                 │
│ [Sign out]   │                                                 │
└──────────────┴─────────────────────────────────────────────────┘
   250px fixed
```

- **LCU badge** (title bar, right): the single most important piece of ambient status.
  Three states — `LCU Connected` (teal, slow pulse), `LCU …` (gold, connecting),
  `LCU Offline` (red). If this is offline, nothing automatic can happen, so an offline
  state needs to be *impossible to miss* without being obnoxious. Improve on the
  current small pill.
- **Sidebar identity block**: circular gold-gradient avatar with initials, Riot ID
  (`Name#TAG`), then `region · rank` (e.g. `EUW · Gold II`).
- **Active nav item**: gold gradient fill with dark text. Nav needs an unread/attention
  badge treatment for when a match needs the player *now*.
- **Presence counter**: "◆ 3 in game" — how many people across the platform are
  currently in a match.

## Screens to design

### 1. Pairing / sign-in (pre-auth, no sidebar)

The only screen before authentication. The user runs `/pair` in Discord, gets a
short code, and types it here. Full-window, centered, needs to carry the brand since
it's the first impression. States: idle, code entered, verifying, error (expired /
invalid / already used), success → transitions into the app.

### 2. Home

The landing screen. Contents in order:

- `Welcome back, {firstName}` — large Cinzel heading.
- **Match banner** — the hero element, and the highest-stakes piece of design here.
  Two variants:
  - *Live*: gold-bordered panel, "TOURNAMENT LIVE" + blinking red "● IN PROGRESS",
    tournament name, `Best of 3 · Summoner's Rift 5v5`, and a prominent **Open Match**
    button.
  - *Idle*: quiet panel, "No live match right now. Join a tournament to get matched."
    + a "Browse tournaments" ghost button.
- **KPI row**, 4 cards: Linked Accounts · My Teams · My Tournaments · Live Now
  (the last one accented teal). These are currently plain number tiles — make them
  earn their space or argue for replacing them.
- **Two-column split (1.4fr / 1fr)**: primary team roster (member rows with initials,
  name, CAPTAIN/MEMBER role tag) and linked Riot accounts (Riot ID, `region · rank`,
  VERIFIED/PENDING chip, plus a dashed "+ Link via local client" affordance).

### 3. Live Match — **the most important screen, and currently unbuilt**

This is where the app earns its existence, and today it's just an empty state. Design
it properly. During a match the app must show, at a glance from across the room:

- **Which phase** the lobby is in: waiting for players → lobby created → invites sent →
  players accepting → champion select → in game → reporting result.
- **All 10 players** and their individual accept/join state (pending / accepted /
  declined / disconnected), split into two teams.
- **Who is hosting**, and clear feedback when hosting **fails over** to someone else
  mid-flight (this is confusing and needs to be explained in-place, not hidden).
- **Host-only controls**, visible only to the host, plus the fact that the app is
  driving their League client automatically — the user must never feel the app is
  doing something behind their back. Include a visible **kill switch / abort**.
- **The ready-up gate**: before a match starts, each team's captain must press
  **Ready** within a **5-minute window**. Design the countdown, the opponent's
  ready state, and the outcome where one team readies and the other doesn't
  (**walkover** — the ready team advances automatically).
- Empty state, for when there's no live match.

### 4. Tournaments

- **Join by code**: a monospace input + Join button at the top, with success
  ("Joined X ✓", teal) and error (red) feedback inline.
- **List of the user's tournaments**, each an expandable accordion row showing:
  name (Cinzel), meta line (`Single Elimination · 8 teams · Bo3 · Summoner's Rift 5v5`),
  a status chip, and a start time. Statuses and their accents:
  `draft` (dim) · `open` (gold) · `check_in` (gold) · `running` (teal, featured with a
  gold border) · `completed` (dim) · `cancelled` (red).
- **Expanded: the bracket.** Horizontally scrolling columns, one per round
  ("ROUND 1", "ROUND 2"…). Each match card shows two sides with team name, a small
  ready dot (teal when ready), a winner marker on the victor, and a status line
  (`pending` / `in progress` / `completed`). Unresolved sides read `TBD`.
  Supports **single and double elimination** (double elim needs a winners/losers
  bracket treatment) and **1v1 ARAM** as well as 5v5. This refreshes live every 3s —
  design how a state change announces itself without the list jumping around.

### 5. Teams

- **Create a team** (name input + Create) and **join a team by code**, side by side.
  Creating returns a shareable join code — that code needs a proper moment, since it's
  what the captain sends to teammates.
- **My rosters**: each team with its members (initials avatar, display name,
  CAPTAIN/MEMBER), the team's join code, and captain-only actions —
  **approve/deny join requests**, remove a member, **transfer captaincy**, disband.
- **Incoming join requests** need a visible queue for captains.

### 6. Accounts

- **Linked Riot accounts** list: Riot ID (large), `region · rank` in mono, a
  VERIFIED (teal) / PENDING (gold) chip, and an Unlink button (red outline).
- **"Link a new account"** panel explaining the flow in plain language: it reads the
  Riot ID from the running League client and double-verifies it server-side. Emphasize
  **read-only** — this is a trust-sensitive moment and the copy should reassure.
  Button: "Read from local client". Error case: League client not running.

## States you must design (not just the happy path)

For every screen: **loading**, **empty**, and **error**. Specifically:

- League client not running (blocks account linking and all match automation).
- Backend unreachable — the app is a client to a server that can be down.
- A tournament with no bracket yet ("teams register first, then an admin starts it").
- User in zero teams / zero tournaments (first-run — what pulls them forward?).
- Join code rejected: expired, invalid, tournament full, already a member.
- Disconnected mid-match, and reconnecting.

## Motion

Restrained. The existing vocabulary is a slow blink for "live"/pulse, a marching
progress bar, and a spinner. Live/urgent states may pulse; nothing else should move.
No page transitions, no parallax, no decorative animation. This app sits open for
hours.

## What I want back

1. All six screens at 1280×820, dark theme.
2. The Live Match screen in depth — it's the least defined and the most important:
   phase tracker, 10-player accept grid, host controls, ready countdown, failover.
3. A short component inventory: buttons (primary gold / ghost / destructive), inputs,
   status chips, cards, member rows, empty states, toasts or inline alerts.
4. The icon set as inline SVG, replacing the current Unicode glyphs.
5. Notes on anything in the current structure you'd change — I'd rather be told the
   information architecture is wrong than get a prettier version of a bad layout.

## Vocabulary (use these words exactly)

Guild (a Discord server) · Tournament · Team / Roster · Captain · Match · Bracket ·
Round · Ready up · Walkover · Lobby · Host · Riot ID · PUUID · Region · Solo/Duo rank ·
LCU (the local League client connection) · Join code · Pairing code.
