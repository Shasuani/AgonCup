import React, { useState } from 'react';
import { theme as t } from '../theme';
import { Kicker, GhostButton, PrimaryButton, AsyncState, initials } from '../ui';
import { lol, useAsync, errText, type MyTeam, type JoinRequest } from '../bridge';

export default function TeamsScreen() {
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

  const [name, setName] = useState('');
  const [joinCode, setJoinCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function reloadAll() { teams.reload(); requests.reload(); }

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
      <Kicker>Teams</Kicker>
      <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 32, color: t.goldBright, margin: '10px 0 22px' }}>My rosters</h1>

      {/* Create / join bar */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 22 }}>
        <div style={{ display: 'flex', gap: 10 }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New team name…" style={inp} />
          <PrimaryButton onClick={create} disabled={busy || !name.trim()} style={{ padding: '0 22px' }}>Create</PrimaryButton>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <input value={joinCode} onChange={(e) => setJoinCode(e.target.value)} placeholder="Join a team by code…" style={{ ...inp, fontFamily: t.mono }} />
          <GhostButton tone="gold" onClick={joinByCode} disabled={busy || !joinCode.trim()} style={{ padding: '0 22px' }}>Join</GhostButton>
        </div>
      </div>
      {msg && <div style={{ marginBottom: 18, color: msg.includes('✓') ? t.teal : t.red, fontSize: 13 }}>{msg}</div>}

      <AsyncState loading={teams.loading} error={teams.error}>
        {myTeams.length === 0 && <div style={{ color: t.textDim, fontSize: 14 }}>You&rsquo;re not on any team yet. Create one or join by code above.</div>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {myTeams.map((team) => (
            <TeamCard key={team.teamId} team={team} requests={(requests.data ?? []).filter((r) => r.teamId === team.teamId)} onChange={reloadAll} />
          ))}
        </div>
      </AsyncState>
    </div>
  );
}

const inp: React.CSSProperties = { flex: 1, background: t.panel, border: `1px solid ${t.line}`, color: t.textStrong, fontSize: 14, padding: '12px 16px', borderRadius: 2 };

function TeamCard({ team, requests, onChange }: { team: MyTeam; requests: JoinRequest[]; onChange: () => void }) {
  const b = lol();
  const isCaptain = team.role === 'captain';
  const members = useAsync(async () => {
    const r = await b!.teamMembers(team.teamId);
    if (!r.ok) throw new Error(errText(r));
    return r.members;
  }, [team.teamId]);
  const code = useAsync(async () => {
    if (!isCaptain) return null;
    const r = await b!.teamJoinCode(team.teamId);
    return r.ok ? r.joinCode : null;
  }, [team.teamId]);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<{ ok: boolean; detail?: string; status?: number }>) {
    setBusy(true);
    await fn();
    setBusy(false);
    members.reload(); onChange();
  }

  return (
    <div style={{ border: `1px solid ${t.line}`, background: t.panel, padding: '28px 30px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <span style={{ fontFamily: t.display, fontSize: 24, color: t.goldBright }}>{team.name}</span>
          <span style={{ fontSize: 12, color: isCaptain ? t.gold : t.textDim, letterSpacing: '.1em', marginLeft: 14, textTransform: 'uppercase' }}>{isCaptain ? 'You are captain' : team.role} · {team.status}</span>
        </div>
        {code.data && <div style={{ fontFamily: t.mono, fontSize: 13, color: t.textDim }}>join code · {code.data}</div>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: '6px 40px', marginTop: 22 }}>
        <AsyncState loading={members.loading} error={members.error}>
          {(members.data ?? []).map((m) => (
            <div key={m.userId} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 0', borderBottom: `1px solid ${t.lineSoft}` }}>
              <span style={{ width: 32, height: 32, borderRadius: '50%', background: t.panel3, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: m.role === 'captain' ? t.gold : t.teal }}>{initials(m.displayName)}</span>
              <span style={{ flex: 1, color: t.textStrong }}>{m.displayName}</span>
              <span style={{ fontSize: 12, color: m.role === 'captain' ? t.gold : t.textDim, letterSpacing: '.06em', textTransform: 'uppercase' }}>{m.role}</span>
              {isCaptain && m.role !== 'captain' && (
                <button onClick={() => act(() => b!.removeMember(team.teamId, m.userId))} disabled={busy} style={xBtn}>remove</button>
              )}
            </div>
          ))}
        </AsyncState>
      </div>

      {/* Pending join requests (captain only) */}
      {isCaptain && requests.length > 0 && (
        <div style={{ marginTop: 22, borderTop: `1px solid ${t.lineSoft}`, paddingTop: 18 }}>
          <div style={{ fontSize: 12, letterSpacing: '.14em', textTransform: 'uppercase', color: t.gold, marginBottom: 12 }}>Pending approval ({requests.length})</div>
          {requests.map((r) => (
            <div key={r.userId} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '9px 0' }}>
              <span style={{ flex: 1, color: t.textStrong }}>{r.displayName}</span>
              <GhostButton tone="gold" onClick={() => act(() => b!.approveJoinRequest(team.teamId, r.userId))} disabled={busy} style={{ padding: '7px 16px' }}>Approve</GhostButton>
              <GhostButton tone="danger" onClick={() => act(() => b!.rejectJoinRequest(team.teamId, r.userId))} disabled={busy} style={{ padding: '7px 16px' }}>Reject</GhostButton>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
        {isCaptain
          ? <GhostButton tone="danger" onClick={() => act(() => b!.deleteTeam(team.teamId))} disabled={busy}>Disband team</GhostButton>
          : <GhostButton tone="danger" onClick={() => act(() => b!.leaveTeam(team.teamId))} disabled={busy}>Leave team</GhostButton>}
        <GhostButton disabled title="Transfer captaincy isn't exposed on the desktop bridge yet — do it from the admin app.">Transfer captaincy</GhostButton>
      </div>
    </div>
  );
}

const xBtn: React.CSSProperties = { fontFamily: t.body, fontSize: 11, background: 'transparent', color: t.textDim, border: `1px solid ${t.lineSoft}`, padding: '4px 10px', borderRadius: 2, cursor: 'pointer' };
