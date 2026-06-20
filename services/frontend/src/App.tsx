// Admin SPA shell (PLAN.md §3.4). Phase-0 placeholder.
//
// Auth is Discord OAuth (admin role gate). Real screens land in Phase 5: CRUD for
// accounts/guilds/tournaments/teams/registrations/matches, a bracket visualizer,
// and — specific to the v3 autonomous model — the **dispute / override queue**
// where admins resolve results that fail corroboration (PLAN.md §3.6.6 / §3.4).

export function App(): JSX.Element {
  const sections = [
    "Accounts",
    "Guilds",
    "Tournaments",
    "Teams",
    "Matches & Brackets",
    "Dispute / Override queue",
    "Audit log",
  ];

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>LeagueOfLeagues — Admin</h1>
      <p>Phase-0 scaffold. Screens are implemented in Phase 5.</p>
      <ul>
        {sections.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </main>
  );
}
