// Dispute / Override queue — the admin escape hatch for results that fail
// corroboration (PLAN.md §3.6.6 / §3.4).
//
// Lists matches whose reported result is disputed/contested, showing the two
// teams, the raw report summary, and any anomaly flags. An admin resolves a
// match via `results.recordWinner` (decided_by = admin_override) with a reason,
// behind a ConfirmDialog. The route is already role-gated to guild_admin /
// platform_operator (App.tsx RequireRole), and the action block is additionally
// wrapped in a RoleGate so a non-admin who reaches the screen cannot override.
//
// All server-provided strings render as React children (escaped by default) —
// no dangerouslySetInnerHTML anywhere.

import { useCallback, useState } from "react";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData, useAsyncAction } from "../../api/useAsync";
import { RoleGate } from "../../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "../../auth/roles";
import {
  Button,
  ConfirmDialog,
  DataTable,
  ErrorBanner,
  FormField,
  IdChip,
  ScreenHeader,
  Spinner,
  StatusBadge,
  type Column,
} from "../../components";
import { MATCH_STATUS_LABELS } from "../../lib/labels";

import "./DisputeQueueScreen.css";
import {
  fetchDisputeQueue,
  teamName,
  type DisputedMatch,
  type DisputeReport,
  type DisputeAnomaly,
} from "./disputes";

const ADMIN_ROLES = [GUILD_ADMIN, PLATFORM_OPERATOR] as const;

/** The in-flight override selection (which match + which winning team). */
interface OverrideDraft {
  match: DisputedMatch;
  winnerTeamId: string;
}

export default function DisputeQueueScreen(): JSX.Element {
  const api = useApi();

  const {
    data: matches,
    loading,
    error,
    reload,
  } = useAsyncData<DisputedMatch[]>(() => fetchDisputeQueue(api), [api]);

  // The override mutation (decided winner). Resolves to the recorded result.
  const override = useAsyncAction((matchId: string, winnerTeamId: string, reason: string) =>
    api.results.recordWinner(matchId, {
      winner_team_id: winnerTeamId,
      decided_by: "admin_override",
      reason: reason.trim() ? reason.trim() : null,
    }),
  );

  const [draft, setDraft] = useState<OverrideDraft | null>(null);
  const [reason, setReason] = useState("");

  const openOverride = useCallback((match: DisputedMatch, winnerTeamId: string) => {
    setDraft({ match, winnerTeamId });
    setReason("");
    override.reset();
  }, [override]);

  const closeOverride = useCallback(() => {
    setDraft(null);
    setReason("");
  }, []);

  const confirmOverride = useCallback(async () => {
    if (!draft) return;
    try {
      await override.run(draft.match.match_id, draft.winnerTeamId, reason);
      closeOverride();
      reload();
    } catch {
      // Error is surfaced inside the dialog via override.error; keep it open.
    }
  }, [draft, override, reason, closeOverride, reload]);

  const rows = matches ?? [];

  const columns: ReadonlyArray<Column<DisputedMatch>> = [
    {
      id: "match",
      header: "Match",
      cell: (m) => <MatchCell match={m} />,
    },
    {
      id: "teams",
      header: "Teams",
      cell: (m) => <TeamsCell match={m} />,
    },
    {
      id: "reports",
      header: "Reported outcomes",
      cell: (m) => <ReportsCell match={m} />,
    },
    {
      id: "anomalies",
      header: "Anomaly flags",
      cell: (m) => <AnomaliesCell anomalies={m.anomalies} />,
    },
    {
      id: "actions",
      header: "Override",
      cell: (m) => (
        <RoleGate
          anyOf={ADMIN_ROLES}
          fallback={<span data-no-permission>Admin only</span>}
        >
          <div data-override-actions>
            {m.teams.map((t) => (
              <Button
                key={t.team_id}
                variant="primary"
                onClick={() => openOverride(m, t.team_id)}
              >
                Winner: {t.name}
              </Button>
            ))}
          </div>
        </RoleGate>
      ),
    },
  ];

  return (
    <section aria-labelledby="disputes-heading">
      <ScreenHeader
        id="disputes-heading"
        title="Dispute / Override queue"
        intro="Matches whose reported result failed corroboration. Review the teams, reported outcomes, and anomaly flags, then record a decided winner. An override is final and is written to the audit log."
        actions={
          <Button variant="secondary" onClick={reload} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      <ErrorBanner error={error} onRetry={reload} />

      <DataTable<DisputedMatch>
        caption="Disputed matches awaiting an admin decision"
        hideCaption
        columns={columns}
        rows={rows}
        rowKey={(m) => m.match_id}
        loading={loading}
        loadingRows={3}
        emptyMessage="No disputed matches. The queue is clear."
      />

      <ConfirmDialog
        open={draft !== null}
        title="Record decided winner"
        destructive
        busy={override.loading}
        confirmLabel="Record override"
        cancelLabel="Cancel"
        onCancel={closeOverride}
        onConfirm={confirmOverride}
        message={
          draft ? (
            <div data-override-confirm>
              <p>
                Record{" "}
                <strong>{teamName(draft.match, draft.winnerTeamId)}</strong> as
                the winner of this match? This advances the bracket and cannot be
                undone here.
              </p>
              <FormField
                label="Reason (recorded in the audit log)"
                hint="Optional but recommended. Max 500 characters."
              >
                {({ inputId, describedBy }) => (
                  <textarea
                    id={inputId}
                    aria-describedby={describedBy}
                    value={reason}
                    maxLength={500}
                    rows={3}
                    onChange={(e) => setReason(e.target.value)}
                    data-override-reason
                  />
                )}
              </FormField>
              {override.loading ? <Spinner label="Recording override" /> : null}
              <ErrorBanner error={override.error} />
            </div>
          ) : null
        }
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Cell sub-components (all values rendered as escaped React children)
// ---------------------------------------------------------------------------

function MatchCell({ match }: { match: DisputedMatch }): JSX.Element {
  return (
    <div data-match-cell>
      {match.tournament_name ? (
        <div data-match-tournament>{match.tournament_name}</div>
      ) : null}
      {match.round_label ? <div data-match-round>{match.round_label}</div> : null}
      <div data-match-status>
        <StatusBadge status={match.status} labels={MATCH_STATUS_LABELS} />
      </div>
      <IdChip value={match.match_id} kind="match id" />
    </div>
  );
}

function TeamsCell({ match }: { match: DisputedMatch }): JSX.Element {
  if (match.teams.length === 0) {
    return <span data-empty>No teams recorded.</span>;
  }
  return (
    <ul data-teams-list>
      {match.teams.map((t) => (
        <li key={t.team_id}>{t.name}</li>
      ))}
    </ul>
  );
}

function ReportsCell({ match }: { match: DisputedMatch }): JSX.Element {
  const reports = match.reports ?? [];
  if (reports.length === 0) {
    return <span data-empty>No reports filed.</span>;
  }
  return (
    <ul data-reports-list>
      {reports.map((r, i) => (
        <ReportRow key={`${r.reporter}-${i}`} report={r} match={match} />
      ))}
    </ul>
  );
}

function ReportRow({
  report,
  match,
}: {
  report: DisputeReport;
  match: DisputedMatch;
}): JSX.Element {
  return (
    <li data-report-row>
      <span data-report-reporter>{report.reporter}</span>
      {" claimed "}
      <span data-report-winner>
        {teamName(match, report.claimed_winner_team_id)}
      </span>
      {report.note ? <span data-report-note> — {report.note}</span> : null}
    </li>
  );
}

function AnomaliesCell({
  anomalies,
}: {
  anomalies?: DisputeAnomaly[];
}): JSX.Element {
  const flags = anomalies ?? [];
  if (flags.length === 0) {
    return <span data-empty>None</span>;
  }
  return (
    <ul data-anomalies-list>
      {flags.map((a, i) => (
        <li key={`${a.code}-${i}`} data-anomaly-row>
          <span data-anomaly-code>{a.code}</span>
          {a.severity ? <span data-anomaly-severity> [{a.severity}]</span> : null}
          {": "}
          <span data-anomaly-detail>{a.detail}</span>
        </li>
      ))}
    </ul>
  );
}
