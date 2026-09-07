// The self-update strip under the title bar. Driven entirely by the main
// process's UpdateState (src/update/coordinator.ts): it only renders what the
// state says and sends two intents back — install, or open the manual
// installer. "Later" is local (the download keeps going; the footer's version
// line brings the banner back), and it is not offered for a REQUIRED update.
import React, { useState } from 'react';
import { lol, type UpdateState } from './bridge';
import { Icon } from './icons';
import { GhostButton, PrimaryButton, LinkButton, Spinner } from './ui';

export function updateNeedsAttention(s: UpdateState | null): s is UpdateState {
  return s !== null && s.latest !== null && ['available', 'downloading', 'ready', 'installing', 'failed'].includes(s.phase);
}

export default function UpdateBanner({ state, onLater }: { state: UpdateState; onLater: () => void }) {
  const b = lol();
  const [busy, setBusy] = useState(false);
  const version = state.latest ?? '';

  async function install() {
    if (!b || typeof b.installUpdate !== 'function') return;
    setBusy(true);
    try { await b.installUpdate(); } finally { setBusy(false); }
  }
  function download() {
    if (b && typeof b.openExternal === 'function' && state.downloadUrl) void b.openExternal(state.downloadUrl);
  }

  const later = !state.required && state.phase !== 'installing' ? <GhostButton size="sm" onClick={onLater}>Later</GhostButton> : null;
  const manual = state.downloadUrl ? <LinkButton onClick={download} style={{ fontSize: 12 }}>Download manually</LinkButton> : null;

  let title: React.ReactNode;
  let sub: React.ReactNode = null;
  let actions: React.ReactNode = null;
  let bar: React.ReactNode = null;
  switch (state.phase) {
    case 'downloading':
      title = `Downloading version ${version}…`;
      sub = state.progress !== null ? `${state.progress}% — you can keep playing, nothing changes until you restart.` : 'You can keep playing; nothing changes until you restart.';
      bar = <div className="ll-progress ll-update__bar"><span style={{ width: `${state.progress ?? 0}%` }} /></div>;
      actions = later;
      break;
    case 'ready':
      title = `Version ${version} is downloaded`;
      sub = state.required ? 'This update is required to keep playing. The app closes and reopens on the new version.' : 'Restart to finish — the app closes and reopens on the new version in a few seconds.';
      actions = <>{later}<PrimaryButton size="sm" icon="refresh" busy={busy} onClick={install}>Restart now</PrimaryButton></>;
      break;
    case 'installing':
      title = 'Restarting…';
      sub = 'Installing the update. The app reopens by itself.';
      actions = <Spinner size={16} />;
      break;
    case 'failed':
      title = 'Update failed';
      sub = state.error ?? 'Something went wrong.';
      actions = <>{manual}{state.supported && <PrimaryButton size="sm" icon="refresh" busy={busy} onClick={install}>Try again</PrimaryButton>}{later}</>;
      break;
    default: // available
      title = state.required ? `Version ${version} is required` : `Version ${version} is available`;
      sub = state.supported
        ? (state.required ? 'Update to keep playing — it takes a few seconds and the app reopens by itself.' : 'It takes a few seconds and the app reopens by itself.')
        : 'This copy cannot update itself — download the installer and run it.';
      actions = state.supported
        ? <>{later}<PrimaryButton size="sm" icon="bolt" busy={busy} onClick={install}>Update now</PrimaryButton></>
        : <>{later}<PrimaryButton size="sm" icon="link" onClick={download}>Download</PrimaryButton></>;
  }

  return (
    <div className={`ll-update${state.required ? ' ll-update--required' : ''}${state.phase === 'failed' ? ' ll-update--failed' : ''}`} role="status" aria-live="polite" data-update-phase={state.phase}>
      <span className="ll-update__icon"><Icon name={state.phase === 'failed' ? 'alert' : 'bolt'} size={16} /></span>
      <div className="ll-update__body">
        <div className="ll-update__title">{title}</div>
        {sub && <div className="ll-update__sub">{sub}</div>}
        {bar}
      </div>
      <div className="ll-update__actions">{actions}</div>
    </div>
  );
}

/** The sidebar footer line: "v0.2.0 · Check for updates", with a short verdict after a manual check. */
export function VersionLine({ state, onShowUpdate }: { state: UpdateState | null; onShowUpdate: () => void }) {
  const b = lol();
  const [checking, setChecking] = useState(false);
  const [note, setNote] = useState<{ text: string; err: boolean } | null>(null);
  const canCheck = b !== null && typeof b.checkForUpdates === 'function';

  async function check() {
    if (!b || typeof b.checkForUpdates !== 'function') return;
    setChecking(true); setNote(null);
    try {
      const s = await b.checkForUpdates();
      if (s.error) setNote({ text: s.error, err: true });
      else if (s.phase === 'up_to_date') setNote({ text: `Up to date${s.lastCheckedAt ? ' ✓' : ''}`, err: false });
      else if (s.phase === 'unknown') setNote({ text: 'Could not read the release feed', err: true });
      else if (s.latest) { setNote({ text: `${s.latest} available`, err: false }); onShowUpdate(); }
    } finally {
      setChecking(false);
      setTimeout(() => setNote(null), 6000);
    }
  }

  const version = state?.current ? `v${state.current}` : '';
  return (
    <div className="ll-version">
      <span className="ll-version__tag" title={state?.lastCheckedAt ? `Last checked ${new Date(state.lastCheckedAt).toLocaleString()}` : undefined}>{version}</span>
      {note
        ? <span className={`ll-version__note${note.err ? ' ll-version__note--err' : ''}`} title={note.text}>{note.text}</span>
        : updateNeedsAttention(state)
          ? <LinkButton onClick={onShowUpdate} style={{ fontSize: 11.5 }}>Update available →</LinkButton>
          : canCheck
            ? <LinkButton onClick={() => void check()} disabled={checking} style={{ fontSize: 11.5 }}>{checking ? 'Checking…' : 'Check for updates'}</LinkButton>
            : null}
    </div>
  );
}
