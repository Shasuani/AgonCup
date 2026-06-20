# lol-frontend

Admin SPA (React + TypeScript + Vite) for LeagueOfLeagues (PLAN.md §3.4).

Discord-OAuth, admin-role-gated. CRUD for accounts/guilds/tournaments/teams/
matches, a bracket visualizer, and the **dispute / override queue** that admins
use to resolve results failing corroboration (PLAN.md §3.6.6).

Consumes the backend via the **generated** TypeScript client in
`libs/lol-clients-ts` (`make gen-clients`). Real screens land in **Phase 5**.

## Dev

```bash
npm install
npm run dev        # http://localhost:5173
npm run typecheck  # tsc --noEmit
```

Production image is a multi-stage build that serves the static bundle from nginx.
