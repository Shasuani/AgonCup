import React, { useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, GhostButton, PrimaryButton, AsyncState, Input, Panel, Badge, Feedback, Button, Credential, EmptyState, Avatar } from '../ui';
import { Icon } from '../icons';
import { lol, useAsync, errText, type MyTeam, type JoinRequest, type TeamListing } from '../bridge';
import { regionShort } from './TournamentsScreen';

export default function TeamsScreen({ onFindTeam }: { onFindTeam?: () => void } = {}) {
  const b = lol();
  const teams = useAsync(async () => {
    const r = await b!.myTeams();
    if (!r.ok) throw new Error(errText(r));
    return r.teams;
  }, []);
  const requests = useAsync(async () => {
    const r = await b!.teamJoinRequests();
    if (!r.ok) throw new Error(errText(r));
    return r.requests;
  }, []);
  // "Find a team" posts (every region) — a captain sees whether theirs is up.
  const listings = useAsync(async () => {
    if (!b?.listListings) return [];
    const r = await b.listListings();
    return r.ok ? r.listings : [];
  }, []);
  // Servers the captain can post on: where they hold a verified account.
  const regions = useAsync(async () => {
    const r = await b!.listAccounts();
    if (!r.ok) return [];
    return Array.from(new Set(r.accounts.filter((a) => a.verified && a.region).map((a) => a.region!.toLowerCase())));
  }, []);

  const [name, setName] = useState('');
  const [joinCode, setJoinCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function reloadAll() { teams.reload(); requests.reload(); listings.reload(); }

  async function create() {
    if (!b || !name.trim()) return;
    setBusy(true); setMsg(null);
    const r = await b.createTeam(name.trim());
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    setMsg(`Created ${r.team.name} · code ${r.team.joinCode} ✓`); setName(''); reloadAll();
  }
  async function joinByCode() {
    if (!b || !joinCode.trim()) return;
    setBusy(true); setMsg(null);
    const r = await b.joinTeam(joinCode.trim());
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    setMsg(`Requested to join ${r.team.name} ✓`); setJoinCode(''); reloadAll();
  }

  const myTeams = teams.data ?? [];

  return (
    <div>
      <PageHead kicker="Teams" title="My rosters" intro="Create a team and share its code, or join a friend's with theirs. Captains enter the team into 5v5 tournaments."
        actions={onFindTeam && <GhostButton tone="gold" size="sm" icon="search" onClick={onFindTeam}>Find a team</GhostButton>} />

      {/* Create / join bar */}
      <div className="ll-grid-2" style={{ marginBottom: 14 }}>
        <form className="ll-field" onSubmit={(e) => { e.preventDefault(); void create(); }}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="New team name…" aria-label="New team name" />
          <PrimaryButton type="submit" icon="plus" busy={busy} disabled={!name.trim()} style={{ height: 44 }}>Create</PrimaryButton>
        </form>
        <form className="ll-field" onSubmit={(e) => { e.preventDefault(); void joinByCode(); }}>
          <Input value={joinCode} onChange={(e) => setJoinCode(e.target.value)} placeholder="Join a team by code…" aria-label="Team join code" code />
          <GhostButton tone="gold" type="submit" busy={busy} disabled={!joinCode.trim()} style={{ height: 44 }}>Join</GhostButton>
        </form>
      </div>
      <Feedback msg={msg} style={{ marginBottom: 16 }} />

      <AsyncState loading={teams.loading} error={teams.error} label="Loading your teams…">
        {myTeams.length === 0 && (
          <Panel tone="dashed">
            <EmptyState icon="users" title="You're not on a team yet">
              Create one above and share the join code with four friends, or paste the code a captain gave you.
            </EmptyState>
          </Panel>
        )}
        <div className="ll-stack" style={{ gap: 18 }}>
          {myTeams.map((team) => (
            <TeamCard key={team.teamId} team={team} requests={(requests.data ?? []).filter((r) => r.teamId === team.teamId)}
              listing={(listings.data ?? []).find((l) => l.teamId === team.teamId) ?? null} regions={regions.data ?? []} onChange={reloadAll} />
          ))}
        </div>
      </AsyncState>
    </div>
  );
}

function TeamCard({ team, requests, listing, regions, onChange }: { team: MyTeam; requests: JoinRequest[]; listing: TeamListing | null; regions: string[]; onChange: () => void }) {
  const b = lol();
  const isCaptain = team.role === 'captain';
  const members = useAsync(async () => {
    const r = await b!.teamMembers(team.teamId, team.guildId);
    if (!r.ok) throw new Error(errText(r));
    return r.members;
  }, [team.teamId]);
  const code = useAsync(async () => {
    if (!isCaptain) return null;
    const r = await b!.teamJoinCode(team.teamId, team.guildId);
    return r.ok ? r.joinCode : null;
  }, [team.teamId]);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<{ ok: boolean; detail?: string; status?: number }>) {
    setBusy(true);
    await fn();
    setBusy(false);
    members.reload(); onChange();
  }

  const roster = members.data ?? [];

  return (
    <Panel featured={isCaptain}>
      <div className="ll-panel__head" style={{ alignItems: 'flex-start' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <h2 className="ll-h2" style={{ fontSize: 24 }}>{team.name}</h2>
            <Badge tone={isCaptain ? 'gold' : 'dim'}>{isCaptain ? 'You are captain' : team.role}</Badge>
            <Badge tone={team.status === 'active' ? 'teal' : 'dim'}>{team.status}</Badge>
          </div>
          <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 6 }}>{roster.length} {roster.length === 1 ? 'member' : 'members'}</div>
        </div>
        {code.data && (
          <div style={{ flexShrink: 0 }}>
            <Credential label="Join code · share with your team" value={code.data} />
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0 40px', marginTop: 18 }}>
        <AsyncState loading={members.loading} error={members.error} label="Loading roster…">
          {roster.map((m) => (
            <div key={m.userId} className="ll-row" style={{ padding: '11px 0' }}>
              <Avatar name={m.displayName} size="sm" tone={m.role === 'captain' ? 'gold' : 'teal'} />
              <span className="ll-row__name">{m.displayName}</span>
              <span className="ll-row__meta" style={m.role === 'captain' ? { color: t.gold } : undefined}>{m.role}</span>
              {isCaptain && m.role !== 'captain' && (
                <Button size="xs" onClick={() => act(() => b!.removeMember(team.teamId, m.userId, team.guildId))} disabled={busy}>Remove</Button>
              )}
            </div>
          ))}
        </AsyncState>
      </div>

      {/* Pending join requests (captain only) */}
      {isCaptain && requests.length > 0 && (
        <div style={{ marginTop: 20, borderTop: `1px solid ${t.lineSoft}`, paddingTop: 16 }}>
          <div className="ll-kicker" style={{ letterSpacing: '.16em', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Icon name="users" size={14} /> Wants to join ({requests.length})
          </div>
          {requests.map((r) => (
            <div key={r.userId} className="ll-row" style={{ padding: '8px 0' }}>
              <Avatar name={r.displayName} size="sm" tone="dim" />
              <span className="ll-row__name">{r.displayName}</span>
              <GhostButton tone="teal" size="sm" icon="check" onClick={() => act(() => b!.approveJoinRequest(team.teamId, r.userId, team.guildId))} disabled={busy}>Approve</GhostButton>
              <GhostButton tone="danger" size="sm" icon="x" onClick={() => act(() => b!.rejectJoinRequest(team.teamId, r.userId, team.guildId))} disabled={busy}>Reject</GhostButton>
            </div>
          ))}
        </div>
      )}

      {isCaptain && team.status === 'active' && b?.listListings && (
        <LookingForPlayers team={team} full={roster.length >= 5} listing={listing} regions={regions} onChange={onChange} />
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 22, paddingTop: 18, borderTop: `1px solid ${t.lineSoft}` }}>
        {isCaptain
          ? <GhostButton tone="danger" size="sm" onClick={() => act(() => b!.deleteTeam(team.teamId, team.guildId))} disabled={busy}>Disband team</GhostButton>
          : <GhostButton tone="danger" size="sm" onClick={() => act(() => b!.leaveTeam(team.teamId, team.guildId))} disabled={busy}>Leave team</GhostButton>}
        <GhostButton size="sm" disabled title="Transfer captaincy isn't exposed on the desktop bridge yet — do it from the admin app.">Transfer captaincy</GhostButton>
      </div>
    </Panel>
  );
}

/** The captain's "Find a team" post: one per team, delete it to post a new one. */
function LookingForPlayers({ team, full, listing, regions, onChange }: { team: MyTeam; full: boolean; listing: TeamListing | null; regions: string[]; onChange: () => void }) {
  const b = lol();
  const [message, setMessage] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const region = picked ?? regions[0] ?? null;

  async function post() {
    if (!b?.createListing || !region) return;
    setBusy(true); setMsg(null);
    const r = await b.createListing(team.teamId, team.guildId, region, message.trim() || undefined);
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    setMessage(''); onChange();
  }
  async function remove() {
    if (!b?.deleteListing || !listing) return;
    setBusy(true); setMsg(null);
    const r = await b.deleteListing(listing.id);
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    onChange();
  }

  return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${t.lineSoft}`, paddingTop: 16 }}>
      <div className="ll-kicker" style={{ letterSpacing: '.16em', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="search" size={14} /> Looking for players
      </div>
      {listing ? (
        <div className="ll-row" style={{ padding: '4px 0', gap: 10 }}>
          <Badge tone="teal" dot>Posted · {regionShort(listing.region)}</Badge>
          <span className="ll-dim" style={{ flex: 1, fontSize: 13 }}>{listing.message ? `“${listing.message}”` : 'Every AgonCup server can see your team in Find a team.'}</span>
          <GhostButton tone="danger" size="sm" icon="x" disabled={busy} onClick={() => void remove()}>Delete post</GhostButton>
        </div>
      ) : full ? (
        <div className="ll-dim" style={{ fontSize: 13 }}>Your roster is full.</div>
      ) : regions.length === 0 ? (
        <div className="ll-dim" style={{ fontSize: 13 }}>Link a Riot account in Accounts to post your team.</div>
      ) : (
        <form className="ll-field" onSubmit={(e) => { e.preventDefault(); void post(); }}>
          {regions.length > 1 && regions.map((r) => (
            <Button key={r} size="sm" variant={r === region ? 'primary' : 'muted'} onClick={() => setPicked(r)}>{regionShort(r)}</Button>
          ))}
          <Input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={200}
            placeholder={`What do you need? e.g. "a support, Gold+" (${regionShort(region)})`} aria-label="What your team needs" />
          <PrimaryButton type="submit" size="sm" icon="plus" busy={busy} style={{ height: 44 }}>Post in Find a team</PrimaryButton>
        </form>
      )}
      {!full && regions.length > 0 && (
        <div className="ll-dim" style={{ fontSize: 12, marginTop: 8 }}>
          Your post stays up while AgonCup is open. Close the app for more than 5 minutes and it comes down.
        </div>
      )}
      <Feedback msg={msg} style={{ marginTop: 8 }} />
    </div>
  );
}
