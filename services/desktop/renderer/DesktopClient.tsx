import React, { useState } from 'react';
import { chromeStyle, dragRegion, noDrag, isMac, TRAFFIC_LIGHT_INSET, TITLE_BAR_HEIGHT } from './theme';
import { lol, useAsync, useLcuStatus, useUpdateState, type BridgeUser, type LcuStatus } from './bridge';
import UpdateBanner, { VersionLine, updateNeedsAttention } from './UpdateBanner';
import { Icon, BrandMark, type IconName } from './icons';
import { Button, Dot } from './ui';
import { ProfileIcon } from './champion';
import HomeScreen from './screens/HomeScreen';
import LiveMatchScreen from './screens/LiveMatchScreen';
import TournamentsScreen from './screens/TournamentsScreen';
import TeamsScreen from './screens/TeamsScreen';
import AccountsScreen from './screens/AccountsScreen';
import HistoryScreen from './screens/HistoryScreen';

type Screen = 'home' | 'match' | 'tournaments' | 'teams' | 'history' | 'accounts';

const NAV: { key: Screen; label: string; icon: IconName }[] = [
  { key: 'home', label: 'Home', icon: 'home' },
  { key: 'match', label: 'Live Match', icon: 'swords' },
  { key: 'tournaments', label: 'Tournaments', icon: 'trophy' },
  { key: 'teams', label: 'Teams', icon: 'users' },
  { key: 'history', label: 'Match history', icon: 'clock' },
  { key: 'accounts', label: 'Accounts', icon: 'id' },
];

/** Short server labels players actually say ("EUNE", not "eun1"). */
const REGION_SHORT: Record<string, string> = {
  eun1: 'EUNE', euw1: 'EUW', na1: 'NA', br1: 'BR', la1: 'LAN', la2: 'LAS',
  oc1: 'OCE', tr1: 'TR', ru: 'RU', jp1: 'JP',
};

export default function DesktopClient({ user, onSignOut }: { user: BridgeUser | null; onSignOut: () => void }) {
  const [screen, setScreen] = useState<Screen>('home');
  const b = lol();
  const lcu = useLcuStatus();

  const summoner = useAsync(async () => {
    const s = b ? await b.readSummoner() : null;
    return s && s.ok ? s : null;
  }, []);
  const accounts = useAsync(async () => { const r = b ? await b.listAccounts() : null; return r && r.ok ? r.accounts : []; }, []);
  const presence = useAsync(async () => { const r = b ? await b.presence() : null; return r && r.ok ? r.playersInGame : 0; }, [], 15_000);
  // The Live Match tab gets a badge while one of the player's matches is at a
  // phase where their attention is needed (a lobby exists or a game is running).
  const matches = useAsync(async () => { const r = b ? await b.listMyMatches() : null; return r && r.ok ? r.matches : []; }, [], 10_000);
  const liveCount = (matches.data ?? []).filter((m) => m.lobbyState !== null && m.lobbyState !== 'ended' && m.lobbyState !== 'aborted').length;

  // The summoner icon: live from the LCU when League is open, else the last one
  // this device saw (persisted by the main process), else initials.
  const profile = useAsync(async () => (b && typeof b.getProfile === 'function' ? await b.getProfile() : { ok: true as const }), []);
  const profileIconId = summoner.data?.profileIconId ?? profile.data?.profileIconId ?? null;
  const level = summoner.data?.summonerLevel ?? profile.data?.summonerLevel ?? null;

  // Self-update: the banner shows whenever the main process says a version is
  // pending; "Later" hides it for that version only (never for a required one).
  const update = useUpdateState();
  const [laterFor, setLaterFor] = useState<string | null>(null);
  const showUpdate = updateNeedsAttention(update) && (update.required || laterFor !== update.latest);

  const acc = (accounts.data ?? [])[0];
  const riotId = summoner.data?.gameName && summoner.data?.tagLine
    ? `${summoner.data.gameName}#${summoner.data.tagLine}`
    : (acc?.gameName && acc?.tagLine ? `${acc.gameName}#${acc.tagLine}` : (user?.displayName ?? 'Summoner'));
  const regionRaw = summoner.data?.region ?? acc?.region ?? null;
  const region = regionRaw ? (REGION_SHORT[regionRaw.toLowerCase()] ?? regionRaw.toUpperCase()) : '—';
  const rank = acc?.rankText || 'Unranked';

  async function signOut() {
    if (b) await b.signOut();
    onSignOut();
  }

  return (
    <div className="ll-app">
      {/* Title bar */}
      <div className="ll-titlebar" style={{ ...chromeStyle, ...dragRegion, height: TITLE_BAR_HEIGHT, padding: `0 16px 0 ${isMac ? TRAFFIC_LIGHT_INSET : 18}px` }}>
        <span className="ll-wordmark"><BrandMark size={22} />LEAGUE<em>OF</em>LEAGUES</span>
        <div style={{ ...noDrag, display: 'flex', alignItems: 'center', gap: 12 }}>
          <LcuBadge status={lcu} />
        </div>
      </div>

      {showUpdate && <UpdateBanner state={update} onLater={() => setLaterFor(update.latest)} />}

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Sidebar */}
        <aside className="ll-sidebar" style={chromeStyle}>
          <div className="ll-user">
            <div className="ll-user__label">Signed in via Discord</div>
            <div className="ll-user__row">
              <ProfileIcon id={profileIconId} name={riotId} size={42} />
              <div style={{ minWidth: 0 }}>
                <div className="ll-user__name" title={riotId}>{riotId}{level !== null && <span className="ll-user__lvl">{level}</span>}</div>
                <div className="ll-user__meta"><Dot tone="teal" size={6} /> {region} · {rank}</div>
              </div>
            </div>
          </div>

          <nav className="ll-nav" aria-label="Primary">
            {NAV.map((item) => {
              const active = screen === item.key;
              const badge = item.key === 'match' && liveCount > 0 ? liveCount : null;
              return (
                <button key={item.key} type="button" onClick={() => setScreen(item.key)} className={`ll-nav__item${active ? ' is-active' : ''}`} aria-current={active ? 'page' : undefined}>
                  <Icon name={item.icon} size={18} />
                  <span style={{ flex: 1 }}>{item.label}</span>
                  {badge !== null && <span className="ll-nav__badge">{badge}</span>}
                </button>
              );
            })}
          </nav>

          <div className="ll-sidebar__foot">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <span className="ll-cap">Presence</span>
              <span style={{ fontSize: 12, color: '#0ac8b9', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <Dot tone="teal" size={6} pulse={(presence.data ?? 0) > 0} /> {presence.data ?? 0} in game
              </span>
            </div>
            <VersionLine state={update} onShowUpdate={() => setLaterFor(null)} />
            <Button size="sm" block icon="logout" onClick={signOut}>Sign out</Button>
          </div>
        </aside>

        {/* Main content */}
        <main className="ll-content">
          <div className="ll-page" key={screen}>
            {screen === 'home' && <HomeScreen user={user} onOpenMatch={() => setScreen('match')} onNav={setScreen} />}
            {screen === 'match' && <LiveMatchScreen lcu={lcu} />}
            {screen === 'tournaments' && <TournamentsScreen />}
            {screen === 'teams' && <TeamsScreen />}
            {screen === 'history' && <HistoryScreen user={user} />}
            {screen === 'accounts' && <AccountsScreen lcu={lcu} />}
          </div>
        </main>
      </div>
    </div>
  );
}

function LcuBadge({ status }: { status: LcuStatus }) {
  const online = status === 'online';
  const loading = status === 'loading';
  const tone = online ? 'teal' : loading ? 'gold' : 'red';
  const label = online ? 'League client connected' : loading ? 'Looking for League…' : 'League client offline';
  return (
    <div className={`ll-pill ll-pill--${tone}`} title={online ? 'The League client is open and reachable.' : 'Open the League of Legends client to link accounts and play matches.'}>
      <Dot tone={tone} size={7} pulse />
      <span>{label}</span>
    </div>
  );
}
