// Teams + roster management (PLAN.md §3.4).
//
// Guild-scoped (the API client stamps X-Guild-Id from the acting guild). Lets a
// guild admin: list teams, create a team, drill into a team to add/remove
// members, and leave a team. Data access goes through useApi()/lol-clients-ts;
// request state via useAsyncData/useAsyncAction. All server strings render as
// React children (escaped) — no raw HTML.

import { useCallback, useState, type FormEvent } from "react";
import { useApi } from "../../api/ApiProvider";
import { useAsyncAction, useAsyncData } from "../../api/useAsync";
import {
  Button,
  ConfirmDialog,
  DataTable,
  ErrorBanner,
  Modal,
  TextInput,
  type Column,
} from "../../components";
import type {
  TeamMemberResponse,
  TeamMemberRole,
  TeamResponse,
} from "@lol/clients-ts";

const MEMBER_ROLES: ReadonlyArray<TeamMemberRole> = [
  "player",
  "substitute",
  "captain",
];

export default function TeamsScreen(): JSX.Element {
  const api = useApi();

  const teamsQuery = useAsyncData<TeamResponse[]>(
    useCallback(() => api.teams.list(), [api]),
    [api],
  );

  const [createOpen, setCreateOpen] = useState(false);
  const [activeTeam, setActiveTeam] = useState<TeamResponse | null>(null);

  const teams = teamsQuery.data ?? [];

  const columns: ReadonlyArray<Column<TeamResponse>> = [
    { id: "name", header: "Name", cell: (t) => t.name },
    { id: "captain", header: "Captain", cell: (t) => t.captain_user_id },
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
      <header>
        <h1 id="teams-heading">Teams</h1>
        <Button variant="primary" onClick={() => setCreateOpen(true)}>
          Create team
        </Button>
      </header>

      <ErrorBanner error={teamsQuery.error} onRetry={teamsQuery.reload} />

      <DataTable
        caption="Teams in the acting guild"
        columns={columns}
        rows={teams}
        rowKey={(t) => t.id}
        loading={teamsQuery.loading}
        emptyMessage="No teams yet. Create one to get started."
      />

      <CreateTeamModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          teamsQuery.reload();
        }}
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

interface CreateTeamModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (team: TeamResponse) => void;
}

function CreateTeamModal({
  open,
  onClose,
  onCreated,
}: CreateTeamModalProps): JSX.Element {
  const api = useApi();
  const [name, setName] = useState("");
  const [captainUserId, setCaptainUserId] = useState("");

  const create = useAsyncAction(
    useCallback(
      (body: { name: string; captain_user_id: string }) =>
        api.teams.create(body),
      [api],
    ),
  );

  const onSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const team = await create.run({
        name: name.trim(),
        captain_user_id: captainUserId.trim(),
      });
      setName("");
      setCaptainUserId("");
      onCreated(team);
    },
    [create, name, captainUserId, onCreated],
  );

  const valid = name.trim().length > 0 && captainUserId.trim().length > 0;

  return (
    <Modal
      open={open}
      title="Create team"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={create.loading}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="create-team-form"
            busy={create.loading}
            disabled={!valid}
          >
            Create
          </Button>
        </>
      }
    >
      <form id="create-team-form" onSubmit={onSubmit}>
        <ErrorBanner error={create.error} />
        <TextInput
          label="Team name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <TextInput
          label="Captain user ID"
          hint="The Discord/user id of the team captain."
          required
          value={captainUserId}
          onChange={(e) => setCaptainUserId(e.target.value)}
        />
      </form>
    </Modal>
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
  const [role, setRole] = useState<TeamMemberRole>("player");
  const [leaveOpen, setLeaveOpen] = useState(false);

  const addMember = useAsyncAction(
    useCallback(
      (body: { user_id: string; role: TeamMemberRole }) =>
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

  const onAdd = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      await addMember.run({ user_id: userId.trim(), role });
      setUserId("");
      setRole("player");
      membersQuery.reload();
    },
    [addMember, userId, role, membersQuery],
  );

  const onRemove = useCallback(
    async (memberUserId: string) => {
      await removeMember.run(memberUserId);
      membersQuery.reload();
    },
    [removeMember, membersQuery],
  );

  const members = membersQuery.data ?? [];
  const roleSelectId = `add-member-role-${team.id}`;

  const columns: ReadonlyArray<Column<TeamMemberResponse>> = [
    { id: "user", header: "User", cell: (m) => m.user_id },
    { id: "role", header: "Role", cell: (m) => m.role },
    {
      id: "actions",
      header: "Actions",
      cell: (m) => (
        <Button
          variant="danger"
          onClick={() => onRemove(m.user_id)}
          busy={removeMember.loading}
          aria-label={`Remove ${m.user_id}`}
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
            onChange={(e) => setRole(e.target.value as TeamMemberRole)}
          >
            {MEMBER_ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
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
          await leave.run();
          setLeaveOpen(false);
          onLeft();
        }}
      />
    </Modal>
  );
}
