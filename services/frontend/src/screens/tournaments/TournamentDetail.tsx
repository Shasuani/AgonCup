// Tournament detail view: summary, registrations + check-in, an admin-only
// Start action (generates the bracket), and the bracket visualizer with a
// manual-override entry point that routes to the results/dispute queue.
//
// Registration/check-in take a team id (TeamRef); Start is gated to guild_admin
// (platform_operator also passes) via RoleGate and the backend re-checks authz.

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import type {
  RegistrationResponse,
  StartResponse,
  TournamentResponse,
} from "@lol/clients-ts";
import { Button, ErrorBanner, Spinner, TextInput } from "../../components";
import { BracketView, type BracketMatch } from "../../components/BracketView";
import { RoleGate } from "../../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "../../auth/roles";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData, useAsyncAction } from "../../api/useAsync";

const ADMIN_ROLES = [GUILD_ADMIN, PLATFORM_OPERATOR] as const;

export interface TournamentDetailProps {
  tournamentId: string;
  /**
   * Optional pre-fetched bracket matches (presentational). The current backend
   * does not expose a GET-matches route via the client, so callers that have
   * match data feed it here; otherwise the bracket renders its empty state
   * until the tournament is started.
   */
  matches?: ReadonlyArray<BracketMatch>;
}

export function TournamentDetail({ tournamentId, matches = [] }: TournamentDetailProps): JSX.Element {
  const api = useApi();
  const navigate = useNavigate();

  const tournament = useAsyncData<TournamentResponse>(
    () => api.tournaments.get(tournamentId),
    [tournamentId],
  );

  const [teamId, setTeamId] = useState("");
  const [registrations, setRegistrations] = useState<RegistrationResponse[]>([]);
  const [startResult, setStartResult] = useState<StartResponse | null>(null);

  const register = useAsyncAction(() =>
    api.tournaments.register(tournamentId, { team_id: teamId.trim() }),
  );
  const checkIn = useAsyncAction((id: string) =>
    api.tournaments.checkIn(tournamentId, { team_id: id }),
  );
  const start = useAsyncAction(() => api.tournaments.start(tournamentId));

  function upsertRegistration(reg: RegistrationResponse): void {
    setRegistrations((prev) => {
      const next = prev.filter((r) => r.team_id !== reg.team_id);
      next.push(reg);
      return next;
    });
  }

  async function handleRegister(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (teamId.trim().length === 0) return;
    const reg = await register.run();
    upsertRegistration(reg);
    setTeamId("");
  }

  async function handleCheckIn(id: string): Promise<void> {
    const reg = await checkIn.run(id);
    upsertRegistration(reg);
  }

  async function handleStart(): Promise<void> {
    const result = await start.run();
    setStartResult(result);
    tournament.reload();
  }

  function handleOverride(match: BracketMatch): void {
    // Route to the dispute/override queue (the admin escape hatch for results
    // that fail corroboration — PLAN.md §3.6.6).
    navigate(`/disputes?match=${encodeURIComponent(match.id)}`);
  }

  const t = tournament.data;

  return (
    <section aria-labelledby="tournament-detail-heading" data-tournament-detail>
      <h2 id="tournament-detail-heading">{t ? t.name : "Tournament"}</h2>

      {tournament.loading ? <Spinner label="Loading tournament" /> : null}
      {tournament.error ? (
        <ErrorBanner error={tournament.error} onRetry={tournament.reload} />
      ) : null}

      {t ? (
        <dl data-tournament-summary>
          <div>
            <dt>Status</dt>
            <dd>
              <span data-status-badge data-status={t.status}>
                {t.status}
              </span>
            </dd>
          </div>
          <div>
            <dt>Format</dt>
            <dd>{t.format}</dd>
          </div>
          <div>
            <dt>Max teams</dt>
            <dd>{t.max_teams}</dd>
          </div>
        </dl>
      ) : null}

      <section aria-labelledby="registrations-heading" data-registrations>
        <h3 id="registrations-heading">Registrations</h3>

        <form onSubmit={handleRegister} aria-label="Register a team">
          <TextInput
            label="Team ID"
            required
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
          />
          {register.error ? <ErrorBanner error={register.error} /> : null}
          <Button
            type="submit"
            variant="primary"
            busy={register.loading}
            disabled={teamId.trim().length === 0 || register.loading}
          >
            Register team
          </Button>
        </form>

        {checkIn.error ? <ErrorBanner error={checkIn.error} /> : null}

        {registrations.length === 0 ? (
          <p data-no-registrations>No teams registered in this session.</p>
        ) : (
          <ul data-registration-list>
            {registrations.map((reg) => (
              <li key={reg.team_id} data-registration>
                <span data-registration-team>{reg.team_id}</span>
                <span data-registration-status>{reg.status}</span>
                <Button
                  variant="secondary"
                  busy={checkIn.loading}
                  onClick={() => handleCheckIn(reg.team_id)}
                >
                  Check in
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <RoleGate anyOf={ADMIN_ROLES}>
        <section aria-labelledby="start-heading" data-start>
          <h3 id="start-heading">Start tournament</h3>
          <p>Generates the single-elimination bracket and seeds checked-in teams.</p>
          {start.error ? <ErrorBanner error={start.error} /> : null}
          {startResult ? (
            <p data-start-result role="status">
              Bracket generated: {startResult.matches_created} matches across{" "}
              {startResult.rounds} rounds ({startResult.byes} byes).
            </p>
          ) : null}
          <Button variant="primary" busy={start.loading} onClick={handleStart}>
            Start
          </Button>
        </section>
      </RoleGate>

      <section aria-labelledby="bracket-heading" data-bracket-section>
        <h3 id="bracket-heading">Bracket</h3>
        <BracketView
          matches={matches}
          onOverride={handleOverride}
          caption={t ? `${t.name} bracket` : "Tournament bracket"}
        />
      </section>
    </section>
  );
}
