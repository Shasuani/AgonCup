import React, { useState } from 'react';
import { theme as t, keyframesCss } from './theme';
import { lol, useAsync, useLcuStatus, type BridgeUser, type LcuStatus } from './bridge';
import HomeScreen from './screens/HomeScreen';
import LiveMatchScreen from './screens/LiveMatchScreen';
import TournamentsScreen from './screens/TournamentsScreen';
import TeamsScreen from './screens/TeamsScreen';
import AccountsScreen from './screens/AccountsScreen';

type Screen = 'home' | 'match' | 'tournaments' | 'teams' | 'accounts';

const NAV: { key: Screen; label: string; icon: string; badge?: string }[] = [
  { key: 'home', label: 'Home', icon: '◆' },
  { key: 'match', label: 'Live Match', icon: '▸' },
  { key: 'tournaments', label: 'Tournaments', icon: '❖' },
  { key: 'teams', label: 'Teams', icon: '⬡' },
  { key: 'accounts', label: 'Accounts', icon: '⬢' },
];

export default function DesktopClient({ user, onSignOut }: { user: BridgeUser | null; onSignOut: () => void }) {
  const [screen, setScreen] = useState<Screen>('home');
  const b = lol();
  const lcu = useLcuStatus();

  const summoner = useAsync(async () => {
    const s = b ? await b.readSummoner() : null;
    return s && s.ok ? s : null;
  }, []);
  const accounts = useAsync(async () => { const r = b ? await b.listAccounts() : null; return r && r.ok ? r.accounts : []; }, []);
  const presence = useAsync(async () => { const r = b ? await b.presence() : null; return r && r.ok ? r.playersInGame : 0; }, []);

  const acc = (accounts.data ?? [])[0];
  const riotId = summoner.data?.gameName && summoner.data?.tagLine
    ? `${summoner.data.gameName}#${summoner.data.tagLine}`
    : (acc?.gameName && acc?.tagLine ? `${acc.gameName}#${acc.tagLine}` : (user?.displayName ?? 'Summoner'));
  const region = summoner.data?.region ?? acc?.region ?? '—';
  const rank = acc?.rankText || 'Unranked';

  async function signOut() {
    if (b) await b.signOut();
    onSignOut();
  }

  return (
    <div style={{ width: '100vw', height: '100vh', minWidth: 1100, background: t.bg, fontFamily: t.body, color: t.text, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
      <style>{keyframesCss}</style>

      {/* Title bar */}
      <div style={{ height: 44, flexShrink: 0, background: 'linear-gradient(180deg,#0a1626,#06101c)', borderBottom: `1px solid ${t.line}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: t.gold, fontSize: 14 }}>{'◆'}</span>
          <span style={{ fontFamily: t.display, fontWeight: 600, fontSize: 15, letterSpacing: '.14em', color: t.goldBright }}>LEAGUE<span style={{ color: t.gold }}>OF</span>LEAGUES</span>
          <span style={{ fontSize: 11, letterSpacing: '.16em', color: t.textDim, textTransform: 'uppercase', borderLeft: `1px solid ${t.line}`, paddingLeft: 12 }}>Desktop Client · v3</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <LcuBadge status={lcu} />
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Sidebar */}
        <div style={{ width: 250, flexShrink: 0, background: 'linear-gradient(180deg,#0a1626,#070f1a)', borderRight: `1px solid ${t.line}`, display: 'flex', flexDirection: 'column', padding: '22px 0' }}>
          <div style={{ padding: '0 22px 22px' }}>
            <div style={{ fontSize: 11, letterSpacing: '.22em', textTransform: 'uppercase', color: t.textDim }}>Signed in via Discord</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
              <div style={{ width: 42, height: 42, borderRadius: '50%', background: `linear-gradient(135deg,${t.goldDim},${t.gold})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: t.display, fontSize: 18, color: '#0a1626', fontWeight: 700 }}>{(riotId[0] ?? 'S').toUpperCase()}</div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 15, color: t.textStrong, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 150 }}>{riotId}</div>
                <div style={{ fontSize: 12, color: t.teal }}>{'◆'} {region} · {rank}</div>
              </div>
            </div>
          </div>

          <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '0 12px', marginTop: 8 }}>
            {NAV.map((item) => {
              const active = screen === item.key;
              return (
                <button key={item.key} onClick={() => setScreen(item.key)} style={{
                  display: 'flex', alignItems: 'center', gap: 14, width: '100%', fontFamily: t.body, fontSize: 15, letterSpacing: '.02em', padding: '13px 16px', border: 'none', cursor: 'pointer', borderRadius: 2, textAlign: 'left',
                  ...(active ? { background: `linear-gradient(90deg,${t.gold},${t.goldDim})`, color: '#0a1626', fontWeight: 600 } : { background: 'transparent', color: t.text }),
                }}>
                  <span style={{ width: 18, textAlign: 'center', color: active ? '#0a1626' : (item.badge ? t.teal : t.gold) }}>{item.icon}</span>
                  <span style={{ flex: 1, textAlign: 'left' }}>{item.label}</span>
                  {item.badge && <span style={{ fontSize: 11, background: t.gold, color: '#0a1626', fontWeight: 700, borderRadius: 2, padding: '2px 7px' }}>{item.badge}</span>}
                </button>
              );
            })}
          </nav>

          <div style={{ marginTop: 'auto', padding: '0 22px' }}>
            <div style={{ borderTop: `1px solid ${t.lineSoft}`, paddingTop: 18 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 12, letterSpacing: '.1em', textTransform: 'uppercase', color: t.textDim }}>Presence</span>
                <span style={{ fontSize: 12, color: t.teal }}>{'◆'} {presence.data ?? 0} in game</span>
              </div>
              <button onClick={signOut} style={{ marginTop: 16, width: '100%', fontFamily: t.body, fontSize: 12, background: 'transparent', color: t.textDim, border: `1px solid ${t.lineSoft}`, padding: '9px 0', borderRadius: 2, cursor: 'pointer' }}>Sign out</button>
            </div>
          </div>
        </div>

        {/* Main content */}
        <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', background: 'radial-gradient(120% 80% at 50% -10%,#0d1c30 0%,#010a13 60%)' }}>
          <div style={{ padding: '36px 44px' }}>
            {screen === 'home' && <HomeScreen user={user} onOpenMatch={() => setScreen('match')} onNav={setScreen} />}
            {screen === 'match' && <LiveMatchScreen />}
            {screen === 'tournaments' && <TournamentsScreen />}
            {screen === 'teams' && <TeamsScreen />}
            {screen === 'accounts' && <AccountsScreen />}
          </div>
        </div>
      </div>
    </div>
  );
}

function LcuBadge({ status }: { status: LcuStatus }) {
  const online = status === 'online';
  const loading = status === 'loading';
  const c = online ? t.teal : loading ? t.gold : t.red;
  const label = online ? 'LCU Connected' : loading ? 'LCU …' : 'LCU Offline';
  const bg = online ? 'rgba(10,200,185,.08)' : loading ? 'rgba(200,170,110,.08)' : 'rgba(196,74,74,.08)';
  const bd = online ? 'rgba(10,200,185,.35)' : loading ? 'rgba(200,170,110,.35)' : 'rgba(196,74,74,.35)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, background: bg, border: `1px solid ${bd}`, borderRadius: 2, padding: '6px 12px' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, boxShadow: `0 0 8px ${c}`, animation: 'llBlink 2.2s ease-in-out infinite' }} />
      <span style={{ fontSize: 12, letterSpacing: '.08em', color: c, textTransform: 'uppercase' }}>{label}</span>
    </div>
  );
}
