import React, { useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, PrimaryButton, GhostButton, AsyncState, Panel, Badge, Chip, Feedback, Notice, EmptyState, Avatar, Button } from '../ui';
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

  const rows = listings.data ?? [];

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

      <AsyncState loading={listings.loading} error={listings.error} label="Looking for teams…">
        {rows.length === 0 ? (
          <Panel tone="dashed">
            <EmptyState icon="users" title={region ? `No ${regionShort(region)} teams are looking right now` : 'No teams are looking right now'}>
              Check back later — or make your own team in <b>Teams</b> and post it here.
            </EmptyState>
          </Panel>
        ) : (
          <div className="ll-grid-2" style={{ alignItems: 'start' }}>
            {rows.map((l) => (
              <ListingCard key={l.id} listing={l} canAsk={regions.includes(l.region.toLowerCase())} onChange={listings.reload} />
            ))}
          </div>
        )}
      </AsyncState>
    </div>
  );
}

function ListingCard({ listing: l, canAsk, onChange }: { listing: TeamListing; canAsk: boolean; onChange: () => void }) {
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
    <Panel featured={l.viewerStatus === 'captain'}>
      <div className="ll-panel__head" style={{ alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0 }}>
          <h2 className="ll-h2" style={{ fontSize: 22 }}>{l.teamName}</h2>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            <Chip icon="map">{regionShort(l.region)}</Chip>
            <Chip icon="discord" title="The team's Discord server">{l.serverName}</Chip>
          </div>
        </div>
        <Badge tone={open > 0 ? 'gold' : 'dim'}>{l.memberCount}/{l.maxMembers} · {open} open</Badge>
      </div>

      {l.message && (
        <div style={{ marginTop: 14, fontSize: 14, color: t.text, borderLeft: `2px solid ${t.gold}`, paddingLeft: 10 }}>“{l.message}”</div>
      )}

      <div style={{ marginTop: 14 }}>
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

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 16, paddingTop: 14, borderTop: `1px solid ${t.lineSoft}` }}>
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
        <span className="ll-dim" style={{ fontSize: 12 }}>{timeAgo(l.createdAt)}</span>
        {l.canRemove && (
          <GhostButton tone="danger" size="sm" icon="x" disabled={busy} onClick={() => void remove()}>Delete post</GhostButton>
        )}
      </div>
      <Feedback msg={msg} style={{ marginTop: 10 }} />
    </Panel>
  );
}
