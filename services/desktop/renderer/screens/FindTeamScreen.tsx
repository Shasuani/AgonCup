import React, { useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, PrimaryButton, GhostButton, AsyncState, Panel, Badge, Feedback, Notice, EmptyState, Avatar, Button, Input } from '../ui';
import { Icon } from '../icons';
import { RankEmblem } from '../emblems';
import { lol, useAsync, errText, type TeamListing } from '../bridge';
import { timeAgo } from '../format';
import { regionShort } from './TournamentsScreen';

/** "Find a team": teams from every AgonCup server that still need players. */
export default function FindTeamScreen() {
  const b = lol();
  // Only servers the player has a verified account on: an LCU invite is per
  // server, so a team on another one could never pull them into its lobby.
  const accounts = useAsync(async () => {
    const r = await b!.listAccounts();
    if (!r.ok) throw new Error(errText(r));
    return r.accounts.filter((a) => a.verified && a.region);
  }, []);
  const regions = Array.from(new Set((accounts.data ?? []).map((a) => a.region!.toLowerCase())));
  const [picked, setPicked] = useState<string | null>(null);
  const region = picked ?? regions[0] ?? null;

  const listings = useAsync(async () => {
    if (!b?.listListings) throw new Error('Update the app to see teams looking for players.');
    const r = await b.listListings(region ?? undefined);
    if (!r.ok) throw new Error(errText(r));
    return r.listings;
  }, [region], 30_000);

  const [query, setQuery] = useState('');
  // One team open at a time: the grid stays scannable, the open one spans the row.
  const [openId, setOpenId] = useState<string | null>(null);
  const all = listings.data ?? [];
  const q = query.trim().toLowerCase();
  // Client-side: the list is small, and searching what's already on screen
  // keeps the 30s refresh untouched.
  const rows = q
    ? all.filter((l) =>
        [l.teamName, l.serverName, l.message ?? '', ...l.members.map((m) => m.displayName)]
          .some((s) => s.toLowerCase().includes(q)))
    : all;

  return (
    <div>
      <PageHead
        kicker="Find a team"
        title="Teams looking for players"
        intro="Teams from every AgonCup server post here when they need players. Ask to join: the captain approves, and our bot sends you an invite to the team's Discord server."
        actions={<Button size="sm" icon="refresh" onClick={listings.reload}>Refresh</Button>}
      />

      {!accounts.loading && regions.length === 0 && (
        <Notice tone="gold" style={{ marginBottom: 16 }}>
          Link a Riot account in <b>Accounts</b> first. You can only join teams on a server where you have a verified account.
        </Notice>
      )}

      {regions.length > 1 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }} role="tablist" aria-label="Server">
          {regions.map((r) => (
            <Button key={r} size="sm" variant={r === region ? 'primary' : 'muted'} onClick={() => setPicked(r)}>{regionShort(r)}</Button>
          ))}
        </div>
      )}

      {all.length > 0 && (
        <div style={{ position: 'relative', marginBottom: 16 }}>
          <Icon name="search" size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: t.textDim, pointerEvents: 'none' }} />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search teams, servers or players"
            aria-label="Search teams"
            style={{ width: '100%', paddingLeft: 36 }}
          />
        </div>
      )}

      <AsyncState loading={listings.loading} error={listings.error} label="Looking for teams…">
        {rows.length === 0 && q ? (
          <Panel tone="dashed">
            <EmptyState icon="search" title={`No teams match “${query.trim()}”`}
              action={<Button size="sm" icon="x" onClick={() => setQuery('')}>Clear search</Button>} />
          </Panel>
        ) : rows.length === 0 ? (
          <Panel tone="dashed">
            <EmptyState icon="users" title={region ? `No ${regionShort(region)} teams are looking right now` : 'No teams are looking right now'}>
              Check back later — or make your own team in <b>Teams</b> and post it here.
            </EmptyState>
          </Panel>
        ) : (
          <Panel flush role="table" aria-label="Teams looking for players">
            <div className="ll-dgrid__row ll-dgrid__head" role="row" style={{ gridTemplateColumns: COLS }}>
              <span />
              <span role="columnheader">Team</span>
              <span role="columnheader">Discord server</span>
              <span role="columnheader">Server</span>
              <span role="columnheader">Players</span>
              <span role="columnheader">Open</span>
              <span role="columnheader" style={{ textAlign: 'right' }}>Posted</span>
            </div>
            {rows.map((l) => (
              <ListingRow key={l.id} listing={l} expanded={openId === l.id}
                onToggle={() => setOpenId(openId === l.id ? null : l.id)}
                canAsk={regions.includes(l.region.toLowerCase())} onChange={listings.reload} />
            ))}
          </Panel>
        )}
      </AsyncState>
    </div>
  );
}

const COLS = '28px minmax(0, 1.6fr) minmax(0, 1.3fr) 70px 170px 70px 80px';

/** One record of the grid; a click (or Enter/Space) expands the team under it. */
function ListingRow({ listing: l, expanded, onToggle, canAsk, onChange }: { listing: TeamListing; expanded: boolean; onToggle: () => void; canAsk: boolean; onChange: () => void }) {
  const open = Math.max(0, l.maxMembers - l.memberCount);
  return (
    <div className={expanded ? 'll-dgrid__rec is-open' : 'll-dgrid__rec'}>
      <div
        className="ll-dgrid__row" role="row" tabIndex={0} aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
        style={{ gridTemplateColumns: COLS }}
      >
        <Icon name="chevron" size={16} style={{ color: t.gold, transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease' }} />
        <span role="cell" style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <b className="ll-dgrid__name">{l.teamName}</b>
          {l.viewerStatus === 'captain' && <Badge tone="gold">Yours</Badge>}
          {l.viewerStatus === 'member' && <Badge tone="teal">Joined</Badge>}
          {l.viewerStatus === 'pending' && <Badge tone="dim" dot pulse>Sent</Badge>}
        </span>
        <span role="cell" className="ll-dgrid__muted">{l.serverName}</span>
        <span role="cell" className="ll-dgrid__muted">{regionShort(l.region)}</span>
        <span role="cell" style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          {l.members.map((m, i) => (
            <span key={`${m.displayName}-${i}`} title={m.displayName} style={{ display: 'inline-flex' }}>
              <Avatar name={m.displayName} size="sm" tone={m.role === 'captain' ? 'gold' : 'teal'} />
            </span>
          ))}
          {Array.from({ length: open }, (_, i) => (
            <span key={`open-${i}`} title="Open slot" style={{ width: 28, height: 28, borderRadius: '50%', border: `1px dashed ${t.lineSoft}`, display: 'inline-block' }} />
          ))}
        </span>
        <span role="cell"><Badge tone={open > 0 ? 'gold' : 'dim'}>{open}</Badge></span>
        <span role="cell" className="ll-dgrid__muted" style={{ textAlign: 'right' }}>{timeAgo(l.createdAt)}</span>
      </div>
      {expanded && <ListingDetails listing={l} canAsk={canAsk} onChange={onChange} />}
    </div>
  );
}

/** The expanded part of a record: the captain's note, the roster, the actions. */
function ListingDetails({ listing: l, canAsk, onChange }: { listing: TeamListing; canAsk: boolean; onChange: () => void }) {
  const b = lol();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const open = Math.max(0, l.maxMembers - l.memberCount);

  async function ask() {
    if (!b?.requestToJoinListing) return;
    setBusy(true); setMsg(null);
    const r = await b.requestToJoinListing(l.id);
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    setMsg(r.requestStatus === 'pending' ? `Request sent to ${r.teamName} ✓ — the captain decides.` : `You're already on ${r.teamName}.`);
    onChange();
  }
  async function remove() {
    if (!b?.deleteListing) return;
    setBusy(true); setMsg(null);
    const r = await b.deleteListing(l.id);
    setBusy(false);
    if (!r.ok) { setMsg(errText(r)); return; }
    onChange();
  }

  return (
    <div className="ll-dgrid__details">
      {l.message && (
        <div style={{ marginBottom: 6, fontSize: 14, color: t.text, borderLeft: `2px solid ${t.gold}`, paddingLeft: 10 }}>“{l.message}”</div>
      )}

      <div style={{ marginTop: 8 }}>
        {l.members.map((m, i) => (
          <div key={`${m.displayName}-${i}`} className="ll-row" style={{ padding: '7px 0' }}>
            <Avatar name={m.displayName} size="sm" tone={m.role === 'captain' ? 'gold' : 'teal'} />
            <span className="ll-row__name">{m.displayName}</span>
            {m.role === 'captain' && <Icon name="crown" size={14} style={{ color: t.gold }} title="Captain" />}
            <span className="ll-row__meta" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <RankEmblem rankText={m.rankText} size={20} /> {m.rankText}
            </span>
          </div>
        ))}
        {Array.from({ length: open }, (_, i) => (
          <div key={`open-${i}`} className="ll-row ll-dim" style={{ padding: '7px 0', fontSize: 13 }}>
            <span style={{ width: 28, height: 28, borderRadius: '50%', border: `1px dashed ${t.lineSoft}`, display: 'inline-block' }} />
            <span>Open slot</span>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 14 }}>
        {l.viewerStatus === 'captain' && <Badge tone="gold">Your team</Badge>}
        {l.viewerStatus === 'member' && <Badge tone="teal">You're on this team</Badge>}
        {l.viewerStatus === 'pending' && <Badge tone="dim" dot pulse>Request sent</Badge>}
        {l.viewerStatus === null && (
          <PrimaryButton size="sm" icon="plus" busy={busy} disabled={!canAsk} onClick={() => void ask()}
            title={canAsk ? undefined : `You need a verified ${regionShort(l.region)} account to join this team.`}>
            Ask to join
          </PrimaryButton>
        )}
        <span style={{ flex: 1 }} />
        {l.canRemove && (
          <GhostButton tone="danger" size="sm" icon="x" disabled={busy} onClick={() => void remove()}>Delete post</GhostButton>
        )}
      </div>
      <Feedback msg={msg} style={{ marginTop: 10 }} />
    </div>
  );
}
