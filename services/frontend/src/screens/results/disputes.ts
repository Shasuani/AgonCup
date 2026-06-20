// Shared types + data access for the dispute / override queue (PLAN.md §3.6.6).
//
// The dispute queue is the admin escape hatch for match results that fail
// corroboration: matches whose reported outcome is contested or whose anomaly
// signals tripped a review threshold. An admin reviews the two teams + the raw
// report summary + any anomaly flags, then records a decided winner (an admin
// override) with a reason.
//
// NOTE ON THE READ PATH: the typed client (lol-clients-ts) exposes the decided-
// winner *write* endpoint (`results.recordWinner`) but no typed *list* endpoint
// for the review queue yet — that backend router lands in a later phase. So the
// queue read goes through the client's documented low-level `request` escape
// hatch at a stable path. The shapes below are the hand-authored contract; when
// the backend ships the list route + OpenAPI, these move into the generated
// client and the `request` call is swapped for a typed method. All fields are
// rendered as React children (escaped) — never as raw HTML.

import type { ApiClient } from "@lol/clients-ts";

/** A team participating in a disputed match. */
export interface DisputeTeam {
  team_id: string;
  /** Display name (server-provided; rendered escaped). */
  name: string;
}

/** A single reported outcome contributing to the dispute. */
export interface DisputeReport {
  /** Who/what filed this report (e.g. a captain's display name). */
  reporter: string;
  /** The team this report claims won, by id (matched against the teams list). */
  claimed_winner_team_id: string;
  /** Optional free-text note from the reporter (server-provided; escaped). */
  note?: string | null;
}

/** An anomaly signal that tripped review (server-provided; escaped). */
export interface DisputeAnomaly {
  /** Stable code, e.g. "score_mismatch", "duplicate_report". */
  code: string;
  /** Human-readable detail (server-provided; escaped). */
  detail: string;
  /** Optional severity hint ("low" | "medium" | "high"); rendered as text. */
  severity?: string | null;
}

/** A match sitting in the dispute / override queue. */
export interface DisputedMatch {
  match_id: string;
  tournament_id: string;
  /** Optional tournament display name (server-provided; escaped). */
  tournament_name?: string | null;
  /** Optional round label, e.g. "Quarterfinal" (server-provided; escaped). */
  round_label?: string | null;
  /** Why this match is in the queue: e.g. "disputed", "contested". */
  status: string;
  /** The two (or more) participating teams. */
  teams: DisputeTeam[];
  /** The conflicting reported outcomes, if any. */
  reports?: DisputeReport[];
  /** Anomaly flags raised during corroboration, if any. */
  anomalies?: DisputeAnomaly[];
}

/** Read path under the API base (escape-hatch GET; see file header). */
export const DISPUTE_QUEUE_PATH = "/results/disputes";

/**
 * Fetch the current dispute / override queue for the acting guild. Uses the
 * client's low-level request runner so it carries the same Bearer + X-Guild-Id
 * headers as every other tenant-scoped call.
 */
export function fetchDisputeQueue(client: ApiClient): Promise<DisputedMatch[]> {
  return client.request<DisputedMatch[]>({
    method: "GET",
    path: DISPUTE_QUEUE_PATH,
  });
}

/** Find a team's display name within a match (falls back to the id). */
export function teamName(match: DisputedMatch, teamId: string): string {
  const team = match.teams.find((t) => t.team_id === teamId);
  return team ? team.name : teamId;
}
