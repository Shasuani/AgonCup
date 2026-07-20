# Edge Cases — LeagueOfLeagues

> **Living design doc.** Καταγράφουμε εδώ τα edge cases ανά περιοχή + την
> απόφαση για το πώς τα αντιμετωπίζουμε. **Τίποτα δεν υλοποιείται** μέχρι ο
> χρήστης να πει «τελειώσαμε». Κάθε case έχει: **Σενάριο**, **Τι κάνει σήμερα**
> (αν ξέρουμε), **Απόφαση** (τι θέλουμε), **Status**.

Status legend: `🔵 open` (συζητάμε) · `🟡 decided` (κλείδωσε η απόφαση, μένει η υλοποίηση) · `🟢 done`.

---

## 0. Team readiness lifecycle (roster stability γύρω από το "ready")

Το roster μιας ομάδας περνάει από **3 φάσεις** με διαφορετικούς κανόνες:

### 0.1 Πριν το "ready" — roster ρευστό
- **Σενάριο:** η ομάδα έχει κάνει register σε tournament αλλά **δεν** έχει πατήσει
  ready ακόμα. Κάποιος φεύγει.
- **Απόφαση:** Ελεύθερες αλλαγές. Αυτός που έφυγε μπορεί να **ξαναμπεί**, ή ο
  **team leader τον διώχνει και βάζει άλλον**. Απλώς πρέπει να είναι 5/5 (για 5v5)
  για να μπορέσουν να πατήσουν ready.
- **Status:** 🟡 decided

### 0.2 Μετά το "ready" — 5λεπτο grace window
- **Σενάριο:** η ομάδα πάτησε **ready** και μετά κάποιος βγαίνει/χάνεται.
- **Απόφαση:** Η ομάδα έχει **5 λεπτά** να ξαναγίνει πλήρης. Αν όχι → **η ομάδα
  χάνει** (forfeit).
- **Υλοποιημένο (match-ready gate):** per-match ready window (`matches.ready_deadline_at`
  + `team_a/b_ready_at`, migration 0012). `POST /tournaments/matches/{id}/ready`
  (team captain). Ο `run_readiness_tick` (lifespan loop, 5' default μέσω
  `ready_window_seconds`) οπλίζει το window όταν είναι και οι 2 ομάδες παρούσες,
  και στο deadline: μία ready + άλλη όχι → **walkover** στην έτοιμη· καμία → re-arm.
- **Ανοιχτά (λεπτομέρειες roster στο lobby):** (α) στο 5λεπτο επιτρέπεται μόνο **ο
  ίδιος** να ξαναμπεί ή και **αντικαταστάτης**; (β) roster lock στους 5 τη στιγμή
  του ready; — ο in-lobby member-drop καλύπτεται από presence/heartbeat + watchdog.
- **Status:** 🟡 decided (match-ready gate υλοποιημένο· in-lobby roster λεπτομέρειες open)

### 0.3 Μέσα στο παιχνίδι — leave → League `/pause` (30')
- **Σενάριο:** έχει ξεκινήσει το match και κάποιος κάνει leave.
- **Απόφαση:** Βασιζόμαστε στο **built-in `/pause` του League (30 λεπτά)** — δεν
  κάνουμε δικό μας orchestration εκεί. Αν ο παίκτης δεν επιστρέψει εντός του pause,
  το outcome το κρίνει το ίδιο το παιχνίδι.
- **Ανοιχτά:** Χρειάζεται δικό μας forfeit/timer αν δεν επιστρέψει, ή το αφήνουμε
  100% στο League;
- **Status:** 🔵 open

---

## 1. Teams

### 1.1 Join request σε γεμάτη ομάδα (5/5)
- **Σενάριο:** παίκτης βάζει code για team που είναι ήδη 5/5.
- **Σήμερα:** `TeamFullError` στο `join_by_code` + στο `approve_join_request`.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 1.2 Ίδιος παίκτης κάνει join request 2 φορές στην ίδια ομάδα
- **Σενάριο:** ξαναβάζει το code ενώ έχει ήδη PENDING request (ή είναι ήδη ACTIVE).
- **Σήμερα:** _TBD (να το ελέγξουμε)_
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 1.3 Leader κάνει disband ενώ υπάρχουν PENDING requests
- **Σενάριο:** η ομάδα διαλύεται· τι γίνονται τα εκκρεμή requests / τα notifications.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 1.4 Leader φεύγει / γίνεται remove ο εαυτός του
- **Σενάριο:** ο μοναδικός leader θέλει να φύγει. Μεταβίβαση captaincy; ή disband;
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 1.5 Παίκτης σε πολλές ομάδες + tournament σύγκρουση
- **Σενάριο:** είσαι σε 2 ομάδες που δηλώνονται στο ίδιο tournament.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 2. Tournaments

### 2.1 Join σε tournament που είναι ήδη γεμάτο (max_teams)
- **Σενάριο:** το bracket γέμισε, μπαίνει άλλο ένα team/solo.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 2.2 Team κάνει join, μετά χάνει μέλη → κάτω από 5
- **Σενάριο:** δηλώθηκε 5/5, πριν το start κάποιος έφυγε → 4 παίκτες.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 2.3 Scheduled: registration_opens μετά το start (λάθος admin input)
- **Σενάριο:** ο admin βάζει ασυνεπείς ώρες.
- **Σήμερα:** validation στο `create_tournament` (να επιβεβαιώσουμε τι ακριβώς).
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 2.4 Admin κάνει manual start πριν το scheduled start_at
- **Σενάριο:** override του χρονοδιαγράμματος.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 2.5 Μηδέν / 1 συμμετοχή τη στιγμή του start
- **Σενάριο:** ανοίγει το bracket με 0 ή 1 teams.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 3. Account verification / identity

### 3.1 Δύο Discord users δηλώνουν το ίδιο Riot account (PUUID)
- **Σήμερα:** dedupe στο PUUID → 409 αν ανήκει σε άλλον user.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 3.2 Λήξη / re-use του one-time code
- **Σενάριο:** 15-min TTL, single-use, superseded on re-register.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 3.3 Riot API down όταν γίνεται verify
- **Σενάριο:** Account-V1 timeout/5xx.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 4. Lobby orchestration / desktop client (LCU)

### 4.1 Host player φεύγει offline στη μέση
- **Σενάριο:** watchdog failover σε άλλον host.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 4.2 Παίκτης δεν κάνει accept το invite
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 4.3 Client κλείνει / χάνει σύνδεση στη μέση
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 5. Results / corroboration

### 5.1 Οι 10 παίκτες στέλνουν αντικρουόμενα αποτελέσματα
- **Σήμερα:** quorum + anomaly + admin override.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 5.2 Λιγότερα από quorum reports
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 6. Notifications / Discord

### 6.1 Το notify channel δεν υπάρχει / no permissions
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

### 6.2 Bot offline όταν γίνεται publish notification
- **Σενάριο:** το envelope πάει στο Redis αλλά κανείς δεν το διαβάζει.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 7. Multi-tenant / RLS

### 7.1 Cross-guild join code
- **Σήμερα:** guard στο `_register_team_idempotent` → 404 αν διαφορετικό guild.
- **Απόφαση:** _TBD_
- **Status:** 🔵 open

---

## 8. Autonomous match lifecycle (lobby → game → advancement)

Ο πλήρης αυτόνομος κύκλος ενός αγώνα, από το lobby μέχρι το επόμενο round.

### 8.1 Auto-create custom lobby από τον host leader
- **Ροή:** Το σύστημα **στέλνει αυτόματα** σε έναν team leader εντολή να κάνει
  create custom tournament lobby με **προκαθορισμένο name + code** που τα
  **λαμβάνει αυτόματα** (δεν τα πληκτρολογεί).
- **Μετά το create:** μας έρχεται **confirmation** ότι δημιουργήθηκε.
- **Ανοιχτά:** ποιος team leader είναι ο host (seeding/τυχαία;)· το "code" είναι
  LCU lobby password ή δικό μας join token; (η υπάρχουσα σχεδίαση είναι
  **invite-based** — να το reconcile-άρουμε).
- **Status:** 🔵 open

### 8.2 Auto-join sequencing (αυτόματα, με σειρά)
- **Ροή:**
  1. Host leader δημιουργεί το lobby.
  2. **Αυτόματα** μπαίνουν οι **4 συμπαίκτες** του host.
  3. Μπαίνει ο **team leader της αντίπαλης** ομάδας.
  4. **Αυτόματα** μπαίνουν οι **συμπαίκτες** του.
- Όλα αυτόνομα μέσω LCU (invite/auto-accept + pacing + kill-switch).
- **Υπάρχει ήδη:** `initialize_lobby` εκδίδει `CreateLobby` + `Invite` σε όλους τους
  checked-in· ο desktop controller κάνει `createLobby`+`invite` (host) και
  `autoAccept` (guests). **Νέο:** το `auto_initialize_lobby` το πυροδοτεί αυτόματα
  μόλις πατήσουν ready και οι 2 ομάδες (§8.5), όχι πια μόνο admin.
- **Status:** 🟡 decided (auto-trigger υλοποιημένο)

### 8.3 Game status reporting → backend
- **Απόφαση:** Ο client στέλνει στο backend **όλα τα διαθέσιμα steps/state** που
  δίνει το παιχνίδι (LCU gameflow phases: Lobby → ChampSelect → InProgress →
  WaitingForStats → EndOfGame / PreEndOfGame κ.λπ.).
- **Υπάρχει ήδη:** `POST /orchestration/matches/{id}/heartbeat` (epoch-fenced,
  κουβαλά `phase: LobbyPhase`) + `.../lobby-state` (ack → advance) + realtime hub.
  Ο desktop `OrchestrationController` κάνει poll `getGameflowPhase` και στέλνει.
- **Status:** 🟡 decided (backend + client υπάρχουν· live-announce per phase = §9.2)

### 8.4 Win → bracket advancement
- **Απόφαση:** Μετά το τέλος, βγαίνει **status ποια ομάδα κέρδισε** και **περνάει
  αυτόματα στο επόμενο bracket**.
- **Σύνδεση:** εδώ κουμπώνει το corroboration/quorum (§5) για το ποιος κέρδισε.
- **Status:** 🔵 open

### 8.5 Wait-for-opponent + 5λεπτο ready στο επόμενο round
- **Απόφαση:** Η ομάδα που προκρίθηκε **περιμένει** τον νικητή του άλλου match του
  bracket. Μόλις καθοριστεί ο αντίπαλος → **5 λεπτά** για να πατήσουν **ready**.
- **Υλοποιημένο:** το advancement γεμίζει το επόμενο placeholder· μόλις μπουν και
  οι 2 ομάδες (`team_a` & `team_b`), ο `run_readiness_tick` το οπλίζει αυτόματα +
  announce «ready up». Δες §0.2.
- **Desktop UI:** στο bracket accordion, ο captain κάθε πλευράς βλέπει κουμπί
  **«Ready up»** + countdown (X min left) + κατάσταση αντιπάλου (✓/•)· «Waiting for
  the other match's winner…» όταν ο αντίπαλος δεν έχει κριθεί ακόμα.
  (`markReady` → `POST /matches/{id}/ready`.)
- **Status:** 🟢 done (backend + auto-lobby + desktop UI)

---

## 9. Visualization & live status

### 9.1 API-first
- **Απόφαση:** Το bracket/tournament visualization πρέπει να είναι **διαθέσιμο σαν
  API πρώτα** (το UI/announce καταναλώνουν το API).
- **Status:** 🟡 decided

### 9.2 Discord announce channel (owner-defined)
- **Απόφαση:** Σε **channel που ορίζει ο Discord owner** (= το υπάρχον
  `notify_channel_id`, ρυθμιζόμενο guild-side), κάνουμε announce: tournament news,
  **live status**, αποτελέσματα, προκρίσεις.
- **Υλοποιημένο (milestones, όχι spam):** registration open / starting (scheduler),
  **ready-up** + **walkover/no-show** (readiness tick), **lobby open** + **🔴 now
  live** (orchestration `_announce_phase`), **result** (results path). Όλα μέσω
  `tournament_announced` → bot subscriber → notify channel. Best-effort (ένα
  Redis/notify σφάλμα δεν μπλοκάρει ποτέ το ack).
- **Ανοιχτό (optional):** ξεχωριστό dedicated "tournament feed" channel — σήμερα
  χρησιμοποιείται το notify channel· εύκολο add-on αν το θελήσεις.
- **Status:** 🟡 decided (υλοποιημένο στο notify channel)

---

## 10. ⚠️ Στρατηγικό: Tournament Codes vs autonomous-LCU

Έρευνα (Reddit/Riot DevRel/tournament platforms) έδειξε ότι η θεμελιώδης υπόθεση
«δεν υπάρχει Riot Tournament API» είναι **λανθασμένη**: τα **Tournament Codes**
(Tournament-V5) αυτο-δημιουργούν το custom lobby και επιστρέφουν **authoritative
αποτέλεσμα μέσω callback** — είναι ο επίσημος δρόμος (Battlefy/Challengermode).
Το autonomous-LCU που χτίσαμε είναι ο **ToS-ρίσκο** δρόμος (το beta gate).

➡️ Πλήρης ανάλυση + απόφαση + απαιτούμενη ενέργεια: **`docs/decision-tournament-codes.md`**.

- **Status:** 🟡 recommended — pivot σε Tournament Codes ως primary path· χρειάζεται
  ο owner να κάνει apply για Tournament API production access (callback URL).

---

## Παρακάτω: δικά σου cases
<!-- Πρόσθεσε εδώ ό,τι θέλεις — τα οργανώνουμε μαζί -->
