import React from 'react';
import { theme as t } from '../theme';
import { PageHead, PrimaryButton, GhostButton, AsyncState, MemberRow, Badge, Chip, Panel, LinkButton, EmptyState } from '../ui';
import { Icon } from '../icons';
import { RankEmblem, rankParts } from '../emblems';
import { ChampIcon, ProfileIcon, championSplashUrl, useChampions } from '../champion';
import { countdown, findMe, kdaRatio, myRecord, myRiotIds, shortWhen, timeAgo, topChampion, useNow } from '../format';
import { lol, useAsync, errText, type BridgeUser, type Entrant, type HistoryGame, type Tournament } from '../bridge';
import { useIdentity } from '../identity';
import { regionShort } from './TournamentsScreen';

type Nav = 'teams' | 'accounts' | 'tournaments' | 'history' | 'match';

const STATUS_LABEL: Record<string, string> = { running: 'Running', check_in: 'Check-in', open: 'Open', draft: 'Draft', completed: 'Finished', cancelled: 'Cancelled' };

export default function HomeScreen({ user, onOpenMatch, onNav }: { user: BridgeUser | null; onOpenMatch: () => void; onNav: (s: Nav) => void }) {
  const b = lol();
  // Name, rank, icon and level of the account signed in to League right now
  // (else the last one seen on this device) — one account, not a mix.
  const me = useIdentity(user);
  const teams = useAsync(async () => { const r = await b!.myTeams(); if (!r.ok) throw new Error(errText(r)); return r.teams; }, []);
  const tournaments = useAsync(async () => { const r = await b!.listMyTournaments(); if (!r.ok) throw new Error(errText(r)); return r.tournaments; }, [], 15_000);
  const matches = useAsync(async () => { const r = await b!.listMyMatches(); return r.ok ? r.matches : []; }, [], 10_000);
  const history = useAsync(async () => {
    if (!b || typeof b.myHistory !== 'function') return [] as HistoryGame[];
    const r = await b.myHistory();
    return r.ok ? r.games : [];
  }, []);
  const { profileIconId, level, riotId, account: acc } = me;
  const rank = rankParts(acc?.rankText);
  const ids = myRiotIds(me.accounts);
  const rec = myRecord(history.data ?? [], ids, user?.displayName ?? null);
  const fav = topChampion(rec);
  const winRate = rec.games > 0 ? Math.round((rec.wins / rec.games) * 100) : null;
  // Ambient art: the champion you play most, from Riot's Data Dragon, faded
  // into the panel so the stats stay readable on top of it.
  const champions = useChampions();
  const splash = fav !== null ? championSplashUrl(champions, fav) : null;

  const list = tournaments.data ?? [];
  const running = list.find((tr) => tr.status === 'running');
  const active = list.filter((tr) => tr.status !== 'completed' && tr.status !== 'cancelled');
  const liveMatch = (matches.data ?? []).find((m) => m.lobbyState !== null && m.lobbyState !== 'ended' && m.lobbyState !== 'aborted');
  const primaryTeam = (teams.data ?? []).find((x) => x.role === 'captain') ?? (teams.data ?? [])[0];
  const members = useAsync(async () => {
    if (!primaryTeam) return [];
    const r = await b!.teamMembers(primaryTeam.teamId, primaryTeam.guildId);
    return r.ok ? r.members : [];
  }, [primaryTeam?.teamId]);

  // Greet the account that is signed in, not the Discord name.
  const firstName = me.gameName ?? (user?.displayName ?? 'summoner').split(/[#\s]/)[0];
  const recent = (history.data ?? []).slice(0, 5);

  return (
    <div>
      <PageHead kicker="Home" title={<>Welcome back, {firstName}</>} />

      {/* Row 1: who you are + what is next */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 16 }}>
        <div className="ll-hero" style={splash ? { backgroundImage: `linear-gradient(90deg, #0d1d33 34%, rgba(13,29,51,0.82) 55%, rgba(13,29,51,0.35) 100%), url("${splash}")`, backgroundSize: 'cover', backgroundPosition: 'right 20% center' } : undefined}>
          <div className="ll-hero__art">
            <ProfileIcon id={profileIconId} name={riotId} size={84} />
            <div className="ll-hero__emblem"><RankEmblem rankText={acc?.rankText} size={52} /></div>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="ll-hero__name" title={riotId}>{riotId}{level !== null && <span className="ll-user__lvl" style={{ fontSize: 11, lineHeight: '18px', marginLeft: 10, verticalAlign: 6 }}>Level {level}</span>}</div>
            <div className="ll-hero__rank">{acc ? `${rank.label}${rank.lp !== null ? ` · ${rank.lp} LP` : ''}` : 'Not linked'}</div>
            <div className="ll-hero__meta">
              {me.region && <Chip icon="map">{regionShort(me.region)}</Chip>}
              {acc && <Chip icon={acc.verified ? 'check' : 'alert'}>{acc.verified ? 'Verified' : 'Pending'}</Chip>}
              {fav !== null && <Chip><ChampIcon id={fav} size={14} ring="none" /> Most played</Chip>}
              {!acc && !me.accountsLoading && <LinkButton onClick={() => onNav('accounts')}>{me.accounts.length > 0 ? 'Link this account →' : 'Link your Riot account →'}</LinkButton>}
            </div>
          </div>
          <div className="ll-hero__stats">
            <div className="ll-hero__stat">
              <div className="ll-hero__num">{rec.games}</div>
              <div className="ll-hero__lbl">Games</div>
            </div>
            <div className="ll-hero__stat">
              <div className="ll-hero__num ll-hero__num--teal">{rec.wins}<span style={{ color: t.textDim, fontSize: 16 }}> / </span><span className="ll-hero__num--red" style={{ fontSize: 22 }}>{rec.games - rec.wins}</span></div>
              <div className="ll-hero__lbl">Win / loss</div>
            </div>
            <div className="ll-hero__stat">
              <div className="ll-hero__num">{winRate === null ? '—' : `${winRate}%`}</div>
              <div className="ll-hero__lbl">Win rate</div>
            </div>
          </div>
        </div>

        <NextUp running={running} liveState={liveMatch?.lobbyState ?? null} tournaments={active} onOpenMatch={onOpenMatch} onNav={onNav} />
      </div>

      {/* Row 2: recent games */}
      <Panel style={{ marginTop: 16, padding: '20px 24px' }}>
        <div className="ll-panel__head">
          <h2 className="ll-h2">Recent games</h2>
          {recent.length > 0 && <LinkButton onClick={() => onNav('history')}>Full history →</LinkButton>}
        </div>
        <AsyncState loading={history.loading} error={history.error} label="Loading your games…">
          {recent.length === 0 ? (
            <div className="ll-dim" style={{ fontSize: 13.5, marginTop: 10 }}>No games yet. Your scoreboards land here after your first tournament match.</div>
          ) : (
            <div className="ll-games" style={{ marginTop: 14 }}>
              {recent.map((g) => {
                const me = findMe(g, ids, user?.displayName ?? null);
                const won = me ? g.teams[me.team]?.isWinner === true : false;
                return (
                  <div key={`${g.matchId}:${g.lcuGameId}`} className={`ll-game ${me ? (won ? 'll-game--win' : 'll-game--loss') : ''}`} onClick={() => onNav('history')} style={{ cursor: 'pointer' }}>
                    <ChampIcon id={me?.player.championId ?? 0} size={40} ring={me ? (won ? 'teal' : 'red') : 'none'} />
                    <div className="ll-game__body">
                      <div className="ll-game__result">{me ? (won ? 'Victory' : 'Defeat') : 'Played'}</div>
                      <div className="ll-game__kda">{me ? `${me.player.kills} / ${me.player.deaths} / ${me.player.assists}` : '—'}</div>
                      <div className="ll-game__sub">{g.tournamentName ?? 'Match'} · {timeAgo(g.playedAt)}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </AsyncState>
      </Panel>

      {/* Row 3: tournaments in progress + team */}
      <div className="ll-split" style={{ marginTop: 16 }}>
        <Panel>
          <div className="ll-panel__head">
            <h2 className="ll-h2">Your tournaments</h2>
            <LinkButton onClick={() => onNav('tournaments')}>All →</LinkButton>
          </div>
          <AsyncState loading={tournaments.loading} error={tournaments.error}>
            {active.length === 0 ? (
              <div style={{ marginTop: 6 }}>
                <EmptyState icon="trophy" title="Nothing on the calendar" style={{ padding: '28px 10px 8px' }}>
                  Paste a join code from your Discord server to enter a tournament.
                </EmptyState>
                <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
                  <GhostButton tone="gold" size="sm" onClick={() => onNav('tournaments')}>Join with a code</GhostButton>
                </div>
              </div>
            ) : (
              <div style={{ marginTop: 6 }}>
                {active.slice(0, 4).map((tr) => <TournamentRow key={tr.id} tr={tr} />)}
              </div>
            )}
          </AsyncState>
        </Panel>

        <Panel>
          <div className="ll-panel__head">
            <h2 className="ll-h2">{primaryTeam?.name ?? 'Your team'}</h2>
            {primaryTeam && <Badge tone={primaryTeam.role === 'captain' ? 'gold' : 'dim'}>{primaryTeam.role}</Badge>}
          </div>
          <AsyncState loading={teams.loading} error={teams.error}>
            {!primaryTeam
              ? (
                <p className="ll-lead" style={{ marginTop: 12 }}>
                  Create a team or join one with a code to play 5v5 tournaments.{' '}
                  <LinkButton onClick={() => onNav('teams')}>Go to Teams →</LinkButton>
                </p>
              )
              : (
                <div style={{ marginTop: 10 }}>
                  {(members.data ?? []).map((m) => (
                    <MemberRow key={m.userId} name={m.displayName} role={m.role} roleColor={m.role === 'captain' ? t.gold : undefined} you={m.userId === user?.userId} />
                  ))}
                  {members.data && members.data.length === 0 && <div className="ll-dim" style={{ fontSize: 13 }}>No members yet.</div>}
                  <div style={{ marginTop: 12 }}><LinkButton onClick={() => onNav('teams')}>Manage rosters →</LinkButton></div>
                </div>
              )}
          </AsyncState>
        </Panel>
      </div>
    </div>
  );
}

/** The "what happens next" card: a live lobby, else a countdown to the next
 *  scheduled tournament, else the way in. */
function NextUp({ running, liveState, tournaments, onOpenMatch, onNav }: { running: Tournament | undefined; liveState: string | null; tournaments: Tournament[]; onOpenMatch: () => void; onNav: (s: Nav) => void }) {
  const now = useNow(1000);
  const upcoming = tournaments
    .filter((tr) => tr.startAt && new Date(tr.startAt).getTime() > now)
    .sort((a, c) => new Date(a.startAt!).getTime() - new Date(c.startAt!).getTime())[0];

  if (running || liveState) {
    const name = running?.name ?? 'Your match';
    const inGame = liveState === 'in_game' || liveState === 'starting';
    return (
      <Panel featured className="ll-next" style={{ padding: '22px 24px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span className="ll-kicker">Right now</span>
            <Badge tone="teal" dot pulse>{inGame ? 'In game' : 'Live'}</Badge>
          </div>
          <div className="ll-countdown ll-countdown--live" style={{ marginTop: 14, fontSize: 26 }}>{name}</div>
          <div className="ll-lead" style={{ marginTop: 8 }}>
            {liveState === null
              ? 'The bracket is running — your match opens here the moment it is drawn.'
              : inGame ? 'Your game is on. Results are recorded automatically.' : 'A lobby is forming for you. Keep this app and League open.'}
          </div>
        </div>
        <PrimaryButton icon="swords" onClick={onOpenMatch} style={{ marginTop: 18 }}>Open match</PrimaryButton>
      </Panel>
    );
  }

  if (upcoming) {
    const cd = countdown(upcoming.startAt!, now);
    return (
      <Panel className="ll-next" style={{ padding: '22px 24px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span className="ll-kicker">Next up</span>
            <Badge tone="gold">{STATUS_LABEL[upcoming.status] ?? upcoming.status}</Badge>
          </div>
          <div className="ll-countdown" style={{ marginTop: 14 }}>{cd ?? 'Starting'}</div>
          <div className="ll-strong" style={{ fontFamily: t.display, fontSize: 17, marginTop: 8 }}>{upcoming.name}</div>
          <div className="ll-dim" style={{ fontSize: 12.5, marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icon name="clock" size={13} /> {shortWhen(upcoming.startAt!)}
          </div>
        </div>
        <GhostButton tone="gold" size="sm" onClick={() => onNav('tournaments')} style={{ marginTop: 18 }}>Check your lock-in</GhostButton>
      </Panel>
    );
  }

  return (
    <Panel tone="dashed" className="ll-next" style={{ padding: '22px 24px' }}>
      <div>
        <span className="ll-kicker">Next up</span>
        <div className="ll-strong" style={{ fontFamily: t.display, fontSize: 20, marginTop: 12 }}>No match scheduled</div>
        <div className="ll-lead" style={{ marginTop: 8 }}>Ask an admin in your Discord server for a join code, or wait for your captain to enter the team.</div>
      </div>
      <GhostButton tone="gold" size="sm" onClick={() => onNav('tournaments')} style={{ marginTop: 18 }}>Join a tournament</GhostButton>
    </Panel>
  );
}

/** One tournament with its lock-in progress; entrants are fetched per row
 *  (max four rows on Home, so this is cheap) and refresh every 15s. */
function TournamentRow({ tr }: { tr: Tournament }) {
  const b = lol();
  const entrants = useAsync(async () => {
    const r = await b!.tournamentEntrants(tr.id, tr.guildId);
    return r.ok ? r.entrants : ([] as Entrant[]);
  }, [tr.id], 15_000);
  const list = entrants.data ?? [];
  const locked = list.filter((e) => e.lockedIn).length;
  const me = list.find((e) => e.isYou);
  const pct = tr.maxTeams > 0 ? Math.min(100, Math.round((locked / tr.maxTeams) * 100)) : 0;
  const running = tr.status === 'running';
  return (
    <div className="ll-trow">
      <div className="ll-trow__main">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="ll-trow__name">{tr.name}</span>
          <Badge tone={running ? 'teal' : tr.status === 'check_in' || tr.status === 'open' ? 'gold' : 'dim'} dot={running} pulse={running}>{STATUS_LABEL[tr.status] ?? tr.status}</Badge>
        </div>
        <div className="ll-trow__sub">
          <span>{tr.gameMode === 'aram_1v1' ? '1v1 ARAM' : '5v5 Rift'}</span>
          <span>·</span>
          <span>{locked}/{tr.maxTeams} locked in</span>
          {me && <><span>·</span><span style={{ color: me.lockedIn ? t.teal : t.gold }}>{me.lockedIn ? 'you are in' : 'lock in needed'}</span></>}
          {tr.startAt && <><span>·</span><span>{shortWhen(tr.startAt)}</span></>}
        </div>
        <div className={`ll-progress${running ? ' ll-progress--teal' : ''}`} style={{ marginTop: 8 }}><span style={{ width: `${pct}%` }} /></div>
      </div>
    </div>
  );
}
