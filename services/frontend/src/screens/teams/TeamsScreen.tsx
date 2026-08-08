// Teams + roster management (PLAN.md §3.4).
//
// Guild-scoped (the API client stamps X-Guild-Id from the acting guild). Lets a
// guild admin: list teams, drill into a team to add/remove members, and leave a
// team. Data access goes through useApi()/lol-clients-ts; request state via
// useAsyncData/useAsyncAction. All server strings render as React children
// (escaped) — no raw HTML.
//
// Teams are NOT created here: creation is a player action in the desktop client
// (the creator becomes captain and gets a join code to share, PLAN.md §3.1), so
// the admin SPA only observes and manages existing rosters.

import { useCallback, useState, type FormEvent } from "react";
import { useApi } from "../../api/ApiProvider";
import { useAsyncAction, useAsyncData } from "../../api/useAsync";
import {
  Button,
  ConfirmDialog,
  DataTable,
  ErrorBanner,
  IdChip,
  Modal,
  ScreenHeader,
  Spinner,
  TextInput,
  type Column,
} from "../../components";
import { TEAM_ROLE_LABELS, label as labelFor } from "../../lib/labels";
import type {
  AddableTeamMemberRole,
  TeamMemberResponse,
  TeamResponse,
} from "@lol/clients-ts";

import "./TeamsScreen.css";

// The roles a captain may ASSIGN — the wire values, not the display text. Two
// of the three options here used to be dead ends: `substitute` is not the enum
// value (it is `sub`, so the request 422'd) and `captain` is only ever created
// with the team, so add_member rejects it with a 400. Both are gone; the label
// map turns `sub` into "Substitute" for the human reading the dropdown.
const MEMBER_ROLES: ReadonlyArray<AddableTeamMemberRole> = ["player", "sub"];

/** A roster row's human name, falling back to the id when the JOIN found none. */
function memberName(m: TeamMemberResponse): string {
  const name = m.display_name?.trim();
  return name !== undefined && name !== "" ? name : m.user_id;
}

export default function TeamsScreen(): JSX.Element {
  const api = useApi();

  const teamsQuery = useAsyncData<TeamResponse[]>(
    useCallback(() => api.teams.list(), [api]),
    [api],
  );

  const [activeTeam, setActiveTeam] = useState<TeamResponse | null>(null);
  // Which teams have their roster open. A Set (not a single id) so an admin can
  // compare two rosters side by side instead of losing one to open the other.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());

  const toggleExpanded = useCallback((team: TeamResponse) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(team.id)) next.add(team.id);
      return next;
    });
  }, []);

  const teams = teamsQuery.data ?? [];

  const columns: ReadonlyArray<Column<TeamResponse>> = [
    { id: "name", header: "Name", cell: (t) => <span data-team-name>{t.name}</span> },
    {
      id: "captain",
      header: "Leader",
      // The list route JOINs the captain's display name. Before that it showed
      // a raw 36-char UUID as the cell's entire content — unreadable, and not
      // an answer to "who runs this team?". The id stays, one line down, so it
      // is still copyable for the add/remove endpoints that key on it.
      cell: (t) => (
        <div data-roster-member>
          <span data-member-name>{t.captain_name?.trim() || "Unknown"}</span>
          <IdChip value={t.captain_user_id} kind="captain user id" />
        </div>
      ),
    },
    {
      id: "members",
      header: "Members",
      cell: (t) =>
        // null means "the server did not compute it on this route" — say so
        // rather than printing a 0 that would read as an empty team.
        t.member_count == null ? (
          <span data-muted>—</span>
        ) : (
          <span data-member-count>
            {t.member_count} {t.member_count === 1 ? "player" : "players"}
          </span>
        ),
    },
    {
      id: "actions",
      header: "Actions",
      cell: (t) => (
        <Button onClick={() => setActiveTeam(t)} aria-label={`Manage ${t.name}`}>
          Manage roster
        </Button>
      ),
    },
  ];

  return (
    <section aria-labelledby="teams-heading">
      <ScreenHeader
        id="teams-heading"
        title="Teams"
        intro="Teams in the Discord server you are currently managing. Players create their own team in the desktop client — this is the admin view of the rosters."
        actions={
          <Button
            variant="secondary"
            onClick={teamsQuery.reload}
            disabled={teamsQuery.loading}
          >
            {teamsQuery.loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      <ErrorBanner error={teamsQuery.error} onRetry={teamsQuery.reload} />

      <DataTable
        caption="Teams in the acting guild"
        hideCaption
        columns={columns}
        rows={teams}
        rowKey={(t) => t.id}
        loading={teamsQuery.loading}
        loadingRows={4}
        emptyMessage="No teams yet. Players create teams from the desktop client."
        renderExpanded={(t) => <TeamRoster teamId={t.id} />}
        expandedKeys={expanded}
        onToggleExpand={toggleExpanded}
        expandLabel={(t) => `Show the roster of ${t.name}`}
      />

      {activeTeam ? (
        <RosterModal
          team={activeTeam}
          onClose={() => setActiveTeam(null)}
          onLeft={() => {
            setActiveTeam(null);
            teamsQuery.reload();
          }}
        />
      ) : null}
    </section>
  );
}

/**
 * The read-only roster shown when a team row is expanded: who else is on the
 * team, and in what role. Mounted only while open, so opening one team does not
 * fetch every other team's members. Managing the roster (add/remove/leave)
 * stays in the modal — this answers "who is in it?", which used to require
 * opening that modal.
 */
function TeamRoster({ teamId }: { teamId: string }): JSX.Element {
  const api = useApi();
  const membersQuery = useAsyncData<TeamMemberResponse[]>(
    useCallback(() => api.teams.listMembers(teamId), [api, teamId]),
    [api, teamId],
  );

  if (membersQuery.loading && membersQuery.data === null) {
    return (
      <p data-roster-inline-status>
        <Spinner label="Loading roster" /> Loading roster…
      </p>
    );
  }

  if (membersQuery.error) {
    return <ErrorBanner error={membersQuery.error} onRetry={membersQuery.reload} />;
  }

  const members = membersQuery.data ?? [];
  if (members.length === 0) {
    return <p data-roster-inline-status>Nobody has joined this team yet.</p>;
  }

  return (
    <ul data-roster-inline>
      {members.map((m) => (
        <li key={m.user_id} data-roster-inline-item>
          <span data-member-name>{memberName(m)}</span>
          <span data-status-badge data-status={m.role === "captain" ? "confirmed" : "active"}>
            {labelFor(TEAM_ROLE_LABELS, m.role)}
          </span>
        </li>
      ))}
    </ul>
  );
}

interface RosterModalProps {
  team: TeamResponse;
  onClose: () => void;
  onLeft: () => void;
}

function RosterModal({ team, onClose, onLeft }: RosterModalProps): JSX.Element {
  const api = useApi();

  const membersQuery = useAsyncData<TeamMemberResponse[]>(
    useCallback(() => api.teams.listMembers(team.id), [api, team.id]),
    [api, team.id],
  );

  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<AddableTeamMemberRole>("player");
  const [leaveOpen, setLeaveOpen] = useState(false);

  const addMember = useAsyncAction(
    useCallback(
      (body: { user_id: string; role: AddableTeamMemberRole }) =>
        api.teams.addMember(team.id, body),
      [api, team.id],
    ),
  );

  const removeMember = useAsyncAction(
    useCallback(
      (memberUserId: string) => api.teams.removeMember(team.id, memberUserId),
      [api, team.id],
    ),
  );

  const leave = useAsyncAction(
    useCallback(() => api.teams.leave(team.id), [api, team.id]),
  );

  // `useAsyncAction.run` RE-THROWS after recording the error so imperative
  // callers can branch on it. These handlers are the terminal caller: swallowing
  // the rejection here is what keeps a failed request from surfacing as an
  // "Uncaught (in promise)" — the error is already on screen via <ErrorBanner>.
  // Returning early also stops the success-path cleanup (clearing the form,
  // reloading) from running on a request that did not happen.
  const onAdd = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      try {
        await addMember.run({ user_id: userId.trim(), role });
      } catch {
        return;
      }
      setUserId("");
      setRole("player");
      membersQuery.reload();
    },
    [addMember, userId, role, membersQuery],
  );

  const onRemove = useCallback(
    async (memberUserId: string) => {
      try {
        await removeMember.run(memberUserId);
      } catch {
        return;
      }
      membersQuery.reload();
    },
    [removeMember, membersQuery],
  );

  const members = membersQuery.data ?? [];
  const roleSelectId = `add-member-role-${team.id}`;

  const columns: ReadonlyArray<Column<TeamMemberResponse>> = [
    {
      id: "user",
      header: "User",
      // The roster listing JOINs `users`, so every row carries a display_name —
      // this cell used to show only the raw UUID. The id stays as a copyable
      // secondary line: it is what the add/remove endpoints key on, and it
      // disambiguates two members with the same Discord display name.
      cell: (m) => (
        <div data-roster-member>
          <span data-member-name>{memberName(m)}</span>
          <IdChip value={m.user_id} kind="user id" />
        </div>
      ),
    },
    { id: "role", header: "Role", cell: (m) => labelFor(TEAM_ROLE_LABELS, m.role) },
    {
      id: "actions",
      header: "Actions",
      cell: (m) => (
        <Button
          variant="danger"
          onClick={() => onRemove(m.user_id)}
          busy={removeMember.loading}
          aria-label={`Remove ${memberName(m)}`}
        >
          Remove
        </Button>
      ),
    },
  ];

  return (
    <Modal
      open
      title={`Roster — ${team.name}`}
      onClose={onClose}
      footer={
        <>
          <Button
            variant="danger"
            onClick={() => setLeaveOpen(true)}
            disabled={leave.loading}
          >
            Leave team
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      <ErrorBanner error={membersQuery.error} onRetry={membersQuery.reload} />

      <DataTable
        caption={`Members of ${team.name}`}
        columns={columns}
        rows={members}
        rowKey={(m) => m.user_id}
        loading={membersQuery.loading}
        loadingRows={3}
        emptyMessage="No members yet."
      />

      <form onSubmit={onAdd} aria-label="Add member">
        <h3>Add member</h3>
        <ErrorBanner error={addMember.error} />
        <TextInput
          label="User ID"
          required
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
        />
        <div data-form-field>
          <label htmlFor={roleSelectId}>Role</label>
          <select
            id={roleSelectId}
            value={role}
            onChange={(e) => setRole(e.target.value as AddableTeamMemberRole)}
          >
            {MEMBER_ROLES.map((r) => (
              <option key={r} value={r}>
                {labelFor(TEAM_ROLE_LABELS, r)}
              </option>
            ))}
          </select>
        </div>
        <Button
          variant="primary"
          type="submit"
          busy={addMember.loading}
          disabled={userId.trim().length === 0}
        >
          Add member
        </Button>
      </form>

      <ErrorBanner error={leave.error} />
      <ConfirmDialog
        open={leaveOpen}
        title="Leave team"
        message={`Leave ${team.name}? You will be removed from its roster.`}
        confirmLabel="Leave"
        destructive
        busy={leave.loading}
        onCancel={() => setLeaveOpen(false)}
        onConfirm={async () => {
          try {
            await leave.run();
          } catch {
            // Dismiss the confirm — it is stacked ON TOP of the roster modal, so
            // leaving it open would hide the very banner reporting the failure.
            // `onLeft` is deliberately NOT called: we are still on the team.
            setLeaveOpen(false);
            return;
          }
          setLeaveOpen(false);
          onLeft();
        }}
      />
    </Modal>
  );
}
