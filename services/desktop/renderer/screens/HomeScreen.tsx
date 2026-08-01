import React from 'react';
import { theme as t } from '../theme';
import { Kicker, StatCard, PrimaryButton, GhostButton, AsyncState, MemberRow, initials } from '../ui';
import { lol, useAsync, errText, type BridgeUser } from '../bridge';

export default function HomeScreen({ user, onOpenMatch, onNav }: { user: BridgeUser | null; onOpenMatch: () => void; onNav: (s: 'teams' | 'accounts' | 'tournaments') => void }) {
  const b = lol();
  const accounts = useAsync(async () => { const r = await b!.listAccounts(); if (!r.ok) throw new Error(errText(r)); return r.accounts; }, []);
  const teams = useAsync(async () => { const r = await b!.myTeams(); if (!r.ok) throw new Error(errText(r)); return r.teams; }, []);
  const tournaments = useAsync(async () => { const r = await b!.listMyTournaments(); if (!r.ok) throw new Error(errText(r)); return r.tournaments; }, []);

  const running = (tournaments.data ?? []).find((tr) => tr.status === 'running');
  const primaryTeam = (teams.data ?? []).find((x) => x.role === 'captain') ?? (teams.data ?? [])[0];
  const members = useAsync(async () => {
    if (!primaryTeam) return [];
    const r = await b!.teamMembers(primaryTeam.teamId);
    return r.ok ? r.members : [];
  }, [primaryTeam?.teamId]);

  const firstName = (user?.displayName ?? 'summoner').split(/[#\s]/)[0];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <Kicker>Home</Kicker>
          <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 36, color: t.goldBright, margin: '10px 0 0' }}>Welcome back, {firstName}</h1>
        </div>
      </div>

      {/* Match-ready banner — only when a tournament is live */}
      {running ? (
        <div style={{ marginTop: 28, border: `1px solid ${t.gold}`, background: 'linear-gradient(135deg,rgba(200,170,110,.10),rgba(10,200,185,.04))', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'stretch' }}>
            <div style={{ width: 6, background: t.gold }} />
            <div style={{ flex: 1, padding: '26px 30px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <span style={{ fontSize: 12, letterSpacing: '.2em', textTransform: 'uppercase', color: t.gold }}>Tournament Live</span>
                <span style={{ fontSize: 12, color: t.red, letterSpacing: '.1em', animation: 'llBlink 1.6s ease-in-out infinite' }}>{'●'} IN PROGRESS</span>
              </div>
              <div style={{ fontFamily: t.display, fontSize: 26, color: t.goldBright, marginTop: 12 }}>{running.name}</div>
              <div style={{ fontSize: 15, marginTop: 8, color: t.text }}>Best of {running.bestOf} · {running.gameMode === 'aram_1v1' ? '1v1 ARAM' : "Summoner's Rift 5v5"}</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10, padding: '26px 30px', background: 'rgba(0,0,0,.25)' }}>
              <PrimaryButton onClick={onOpenMatch} style={{ padding: '14px 34px', fontSize: 15 }}>Open Match</PrimaryButton>
            </div>
          </div>
        </div>
      ) : (
        <div style={{ marginTop: 28, border: `1px solid ${t.line}`, background: t.panel, borderRadius: 3, padding: '22px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ color: t.text, fontSize: 14 }}>No live match right now. Join a tournament to get matched.</div>
          <GhostButton tone="gold" onClick={() => onNav('tournaments')}>Browse tournaments</GhostButton>
        </div>
      )}

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 18, marginTop: 28 }}>
        <StatCard label="Linked Accounts" value={accounts.loading ? '—' : (accounts.data?.length ?? 0)} />
        <StatCard label="My Teams" value={teams.loading ? '—' : (teams.data?.length ?? 0)} />
        <StatCard label="My Tournaments" value={tournaments.loading ? '—' : (tournaments.data?.length ?? 0)} />
        <StatCard label="Live Now" value={tournaments.loading ? '—' : ((tournaments.data ?? []).filter((x) => x.status === 'running').length)} accent={t.teal} />
      </div>

      {/* Team + accounts */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 24, marginTop: 24 }}>
        <div style={{ border: `1px solid ${t.line}`, background: t.panel, padding: '26px 28px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontFamily: t.display, fontSize: 20, color: t.goldBright }}>{primaryTeam?.name ?? 'No team'}</span>
            {primaryTeam && <span style={{ fontSize: 12, letterSpacing: '.1em', textTransform: 'uppercase', color: t.gold }}>{primaryTeam.role}</span>}
          </div>
          <AsyncState loading={teams.loading} error={teams.error}>
            {!primaryTeam
              ? <div style={{ color: t.textDim, fontSize: 14, marginTop: 14 }}>Create or join a team to get started. <button onClick={() => onNav('teams')} style={{ background: 'none', border: 'none', color: t.gold, cursor: 'pointer', fontFamily: t.body, fontSize: 14 }}>Go to Teams →</button></div>
              : <div style={{ display: 'flex', flexDirection: 'column', gap: 1, marginTop: 20 }}>
                  {(members.data ?? []).map((m) => (
                    <MemberRow key={m.userId} initials={initials(m.displayName)} name={m.displayName} role={m.role.toUpperCase()} roleColor={m.role === 'captain' ? t.gold : t.textDim} />
                  ))}
                </div>}
          </AsyncState>
        </div>

        <div style={{ border: `1px solid ${t.line}`, background: t.panel, padding: '26px 28px' }}>
          <span style={{ fontFamily: t.display, fontSize: 20, color: t.goldBright }}>Riot Accounts</span>
          <AsyncState loading={accounts.loading} error={accounts.error}>
            <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {(accounts.data ?? []).map((a) => (
                <div key={a.accountId} style={{ border: `1px solid ${t.lineSoft}`, padding: '16px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: t.textStrong, fontWeight: 600 }}>{a.gameName && a.tagLine ? `${a.gameName}#${a.tagLine}` : a.accountId}</span>
                    <span style={{ fontSize: 11, color: a.verified ? t.teal : t.gold, letterSpacing: '.08em' }}>{'◆'} {a.verified ? 'VERIFIED' : 'PENDING'}</span>
                  </div>
                  <div style={{ fontFamily: t.mono, fontSize: 12, color: t.textDim, marginTop: 8 }}>{a.region ?? '—'} · {a.rankText || 'Unranked'}</div>
                </div>
              ))}
              <button onClick={() => onNav('accounts')} style={{ fontFamily: t.body, fontSize: 13, background: 'transparent', color: t.gold, border: `1px dashed ${t.line}`, padding: 12, borderRadius: 2, cursor: 'pointer' }}>+ Link via local client</button>
            </div>
          </AsyncState>
        </div>
      </div>
    </div>
  );
}
