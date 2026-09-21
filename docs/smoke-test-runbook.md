# Smoke test runbook — πρώτο πραγματικό tournament

Ένα μικρό live test με πραγματικά League clients. Χρειάζεσαι:

- Νέο **DISCORD_BOT_TOKEN** στο `services/bot/.env`.
- Φρέσκο **RIOT_API_KEY** στο `services/backend/.env` (το dev key λήγει κάθε 24h).
- **2 Riot accounts** και **2 μηχανήματα** με League (ή 1 δικό σου + 1 φίλο).

Κάθε βήμα είναι ένα πράγμα. Κάνε τα με τη σειρά.

## Α. Σήκωσε τα services

1. Ξεκίνα Postgres + Redis (αν δεν τρέχουν ήδη).
2. Ξεκίνα backend + frontend: `make dev` (ή VS Code Run-with-Debug).
3. Ξεκίνα τον worker: `cd services/worker && PYTHONPATH=src uv run arq app.main.WorkerSettings`
   (τώρα τρέχει και τα cron sweeps: watchdog κάθε 30s, corroboration timeout κάθε 1′).
4. Ξεκίνα το bot: `make dev-bot`. Δες στο log ότι συνδέθηκε.

## Β. Εγκατέστησε τον client (πάντα τελευταία έκδοση)

5. `BACKEND_URL=http://<IP-του-Mac-σου>:8000 make desktop-mac`
   - Για το prod: σκέτο `make desktop-mac` (default: lol.gameras.gr).
   - Στο 2ο μηχάνημα: ίδιο BACKEND_URL, με το IP σου ορατό στο LAN.
6. Άνοιξε την εφαρμογή από το Spotlight (AgonCup).

## Γ. Register + pair (και οι 2 παίκτες)

7. Στο Discord: `/register` → παίρνεις κωδικό.
8. Στον client: βάλε τον κωδικό → γίνεται pair.
9. Με το League **ανοιχτό**: ο client διαβάζει το Riot ID σου → verify
   (θα έρθει DM από το bot όταν δέσει ο λογαριασμός).

## Δ. Tournament

10. Στο Discord (admin): `/tournament create` → παίρνεις join code.
11. Στον client (και οι 2): Join με τον κωδικό.
12. Στον client (και οι 2): **Automation → Turn on** (θυμάται πλέον την επιλογή,
    και το lock-in στέλνει και τη συναίνεση hosting).
13. Στον client (και οι 2): **Lock in**.
14. Στο Discord (admin): `/tournament start`.

## Ε. Το ματς

15. Ο client του host φτιάχνει το custom lobby **μόνος του** και καλεί τον άλλον.
16. Ο client του guest κάνει **auto-accept** το invite.
17. Παίξτε (ή κλείστε το game νωρίς) → οι clients στέλνουν reports.
18. Σε 1v1/2 reports που συμφωνούν, ο νικητής γράφεται **αυτόματα** (quorum).
    Αν τα reports κολλήσουν, σε ~10′ το ματς πάει μόνο του στο admin dispute queue.

## Αν κάτι κολλήσει

- Ο client δεν βλέπει match → κοίτα `matches/mine` (πρέπει να δείχνει state + lobby name/password).
- Δεν φτιάχνεται lobby → βεβαιώσου ότι το Automation είναι ON και το League ανοιχτό·
  αλλιώς φτιάξε το lobby με το χέρι με το όνομα+κωδικό που δείχνει ο client (manual fallback).
- Ο host «χάθηκε» → ο watchdog προάγει τον επόμενο σε ~10-15s (νέο epoch).
- Αμφισβήτηση αποτελέσματος → web → Dispute queue → όρισε νικητή (τώρα δουλεύει το resolve).
