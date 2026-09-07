import React, { useState } from 'react';
import { theme as t } from '../theme';
import { PageHead, PrimaryButton, AsyncState, Panel, Badge, Button, Feedback, Notice } from '../ui';
import { Icon } from '../icons';
import { RankEmblem, rankParts } from '../emblems';
import { lol, useAsync, errText, type LinkedAccount, type LcuStatus } from '../bridge';
import { regionShort } from './TournamentsScreen';

function riotId(a: LinkedAccount): string {
  return a.gameName && a.tagLine ? `${a.gameName}#${a.tagLine}` : (a.gameName ?? a.accountId);
}

export default function AccountsScreen({ lcu }: { lcu?: LcuStatus }) {
  const b = lol();
  const { data, loading, error, reload } = useAsync(async () => {
    const r = await b!.listAccounts();
    if (!r.ok) throw new Error(errText(r));
    return r.accounts;
  }, []);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function unlink(id: string) {
    if (!b) return;
    setBusy(id); setMsg(null);
    const r = await b.unlinkAccount(id);
    setBusy(null);
    if (!r.ok) { setMsg(errText(r)); return; }
    reload();
  }

  async function link() {
    if (!b) return;
    setBusy('link'); setMsg(null);
    const s = await b.readSummoner();
    if (!s.ok) { setBusy(null); setMsg(s.detail ?? s.reason ?? 'Could not read the local League client. Is it running?'); return; }
    const v = await b.verify();
    setBusy(null);
    if (!v.ok) { setMsg(v.detail ?? 'Verification failed.'); return; }
    setMsg(`Linked ${v.gameName ?? ''}${v.tagLine ? '#' + v.tagLine : ''} ✓`);
    reload();
  }

  const accounts = data ?? [];
  const lcuOffline = lcu === 'offline';

  return (
    <div>
      <PageHead kicker="Accounts" title="Linked Riot accounts" intro="The League accounts tied to your Discord identity. A verified account is what tournaments check when you enter one." />

      <div className="ll-stack" style={{ maxWidth: 780 }}>
        <Feedback msg={msg} />

        <Panel>
          <AsyncState loading={loading} error={error}>
            {accounts.length === 0 && (
              <div className="ll-dim" style={{ fontSize: 14 }}>No linked accounts yet. Link one from your League client below.</div>
            )}
            {accounts.map((a, i) => (
              <div key={a.accountId} style={{ display: 'flex', alignItems: 'center', gap: 16, padding: i === 0 ? '0 0 18px' : '18px 0', borderTop: i > 0 ? `1px solid ${t.lineSoft}` : 'none', paddingBottom: i === accounts.length - 1 ? 0 : 18 }}>
                <RankEmblem rankText={a.rankText} size={56} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="ll-strong" style={{ fontSize: 17, fontWeight: 600 }}>{riotId(a)}</div>
                  <div className="ll-dim" style={{ fontSize: 13, marginTop: 3, display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span>{regionShort(a.region) ?? '—'}</span>
                    <span style={{ opacity: 0.5 }}>·</span>
                    <span style={{ color: t.gold }}>{rankParts(a.rankText).label}{rankParts(a.rankText).lp !== null ? ` · ${rankParts(a.rankText).lp} LP` : ''}</span>
                  </div>
                </div>
                <Badge tone={a.verified ? 'teal' : 'gold'} dot>{a.verified ? 'Verified' : 'Pending'}</Badge>
                <Button variant="danger" size="sm" onClick={() => unlink(a.accountId)} busy={busy === a.accountId}>Unlink</Button>
              </div>
            ))}
          </AsyncState>
        </Panel>

        <Panel tone="dashed">
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
            <span style={{ color: t.gold, display: 'inline-flex', marginTop: 2 }}><Icon name="link" size={22} /></span>
            <div style={{ flex: 1 }}>
              <h2 className="ll-h2" style={{ fontSize: 18 }}>Link the account you're logged into</h2>
              <p className="ll-lead" style={{ margin: '10px 0 16px' }}>
                Reads your Riot ID from the open League client and confirms it with Riot.
                Read-only — nothing is changed in your client.
              </p>
              {lcuOffline && (
                <div style={{ marginBottom: 14 }}>
                  <Notice tone="gold">Open the League of Legends client and log in first — it has to be running for this to work.</Notice>
                </div>
              )}
              <PrimaryButton icon="link" onClick={link} busy={busy === 'link'} disabled={lcuOffline}>
                {busy === 'link' ? 'Reading…' : 'Read from League client'}
              </PrimaryButton>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
