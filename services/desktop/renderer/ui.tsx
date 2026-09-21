// Shared UI primitives for the desktop renderer. Every primitive is a thin
// wrapper over the `.ll-*` classes in styles.ts, so hover / focus / disabled
// states come from the stylesheet and screens only pass layout.
import React, { useState } from 'react';
import { theme as t } from './theme';
import { Icon, type IconName } from './icons';

type Div = React.HTMLAttributes<HTMLDivElement>;
type Btn = React.ButtonHTMLAttributes<HTMLButtonElement>;

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

// ------------------------------------------------------------------ type ---
export const Kicker: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <div className="ll-kicker" style={style}>{children}</div>
);

export const H1: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <h1 className="ll-h1" style={style}>{children}</h1>
);

export const H2: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <h2 className="ll-h2" style={style}>{children}</h2>
);

/** Screen header: kicker + title on the left, an optional action slot on the right. */
export const PageHead: React.FC<{ kicker: string; title: React.ReactNode; intro?: React.ReactNode; actions?: React.ReactNode }> = ({ kicker, title, intro, actions }) => (
  <div className="ll-page__head">
    <div style={{ minWidth: 0 }}>
      <Kicker>{kicker}</Kicker>
      <H1>{title}</H1>
      {intro && <p className="ll-page__intro">{intro}</p>}
    </div>
    {actions && <div style={{ display: 'flex', gap: 10, flexShrink: 0 }}>{actions}</div>}
  </div>
);

// ---------------------------------------------------------------- panels ---
export const Panel: React.FC<Div & { featured?: boolean; tone?: 'teal' | 'danger' | 'dashed'; tight?: boolean; flush?: boolean; sunken?: boolean }> = ({ featured, tone, tight, flush, sunken, className, children, ...rest }) => (
  <div
    {...rest}
    className={cx('ll-panel', featured && 'll-panel--featured', tone && `ll-panel--${tone}`, tight && 'll-panel--tight', flush && 'll-panel--flush', sunken && 'll-panel--sunken', className)}
  >
    {children}
  </div>
);

// --------------------------------------------------------------- buttons ---
export type ButtonVariant = 'primary' | 'ghost' | 'muted' | 'danger' | 'teal';
export type ButtonSize = 'xs' | 'sm' | 'md' | 'lg';

export const Button: React.FC<Btn & { variant?: ButtonVariant; size?: ButtonSize; block?: boolean; icon?: IconName; busy?: boolean }> = ({ variant = 'muted', size = 'md', block, icon, busy, className, children, disabled, type, ...rest }) => (
  <button
    {...rest}
    type={type ?? 'button'}
    disabled={disabled || busy}
    className={cx('ll-btn', `ll-btn--${variant}`, size !== 'md' && `ll-btn--${size}`, block && 'll-btn--block', className)}
  >
    {busy ? <Spinner size={14} /> : icon ? <Icon name={icon} size={16} /> : null}
    {children}
  </button>
);

export const PrimaryButton: React.FC<Btn & { size?: ButtonSize; block?: boolean; icon?: IconName; busy?: boolean }> = (p) => <Button {...p} variant="primary" />;

export const GhostButton: React.FC<Btn & { tone?: 'default' | 'danger' | 'gold' | 'teal'; size?: ButtonSize; block?: boolean; icon?: IconName; busy?: boolean }> = ({ tone = 'default', ...p }) => (
  <Button {...p} variant={tone === 'danger' ? 'danger' : tone === 'gold' ? 'ghost' : tone === 'teal' ? 'teal' : 'muted'} />
);

export const LinkButton: React.FC<Btn> = ({ className, type, ...p }) => <button {...p} type={type ?? 'button'} className={cx('ll-link', className)} />;

// ---------------------------------------------------------------- inputs ---
export const Input: React.FC<React.InputHTMLAttributes<HTMLInputElement> & { code?: boolean; hero?: boolean }> = ({ code, hero, className, ...p }) => (
  <input {...p} className={cx('ll-input', code && 'll-input--code', hero && 'll-input--hero', className)} spellCheck={false} />
);

export const Switch: React.FC<{ on: boolean; disabled?: boolean; onToggle: () => void; label: string }> = ({ on, disabled, onToggle, label }) => (
  <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={onToggle} className={cx('ll-switch', on && 'is-on')} />
);

// ---------------------------------------------------------------- badges ---
export type Tone = 'gold' | 'teal' | 'red' | 'dim' | 'solid';

export const Badge: React.FC<{ tone?: Tone; dot?: boolean; pulse?: boolean; children: React.ReactNode; style?: React.CSSProperties }> = ({ tone, dot, pulse, children, style }) => (
  <span className={cx('ll-badge', tone && `ll-badge--${tone}`)} style={style}>
    {dot && <Dot tone={tone === 'red' ? 'red' : tone === 'teal' ? 'teal' : tone === 'dim' ? 'dim' : 'gold'} pulse={pulse} size={6} />}
    {children}
  </span>
);

export const Dot: React.FC<{ tone: 'teal' | 'gold' | 'red' | 'dim'; pulse?: boolean; size?: number }> = ({ tone, pulse, size }) => (
  <span className={cx('ll-dot', `ll-dot--${tone}`, pulse && 'll-dot--pulse')} style={size ? { width: size, height: size } : undefined} />
);

export const Chip: React.FC<{ icon?: IconName; children: React.ReactNode; title?: string }> = ({ icon, children, title }) => (
  <span className="ll-chip" title={title}>{icon && <Icon name={icon} size={13} />}{children}</span>
);

// ---------------------------------------------------------------- notice ---
export const Notice: React.FC<{ tone?: 'ok' | 'err' | 'gold' | 'plain'; children: React.ReactNode; style?: React.CSSProperties }> = ({ tone = 'plain', children, style }) => (
  <div className={cx('ll-notice', tone !== 'plain' && `ll-notice--${tone}`)} style={style} role={tone === 'err' ? 'alert' : undefined}>
    <Icon name={tone === 'ok' ? 'check' : tone === 'err' ? 'alert' : 'info'} size={16} />
    <span>{children}</span>
  </div>
);

/** A one-line feedback message: green when it ends in ✓, red otherwise. */
export const Feedback: React.FC<{ msg: string | null; style?: React.CSSProperties }> = ({ msg, style }) => {
  if (!msg) return null;
  const ok = msg.includes('✓');
  return <Notice tone={ok ? 'ok' : 'err'} style={style}>{msg.replace(' ✓', '').replace('✓', '').trim()}</Notice>;
};

// ------------------------------------------------------------------ stat ---
export const StatCard: React.FC<{ label: string; value: React.ReactNode; accent?: string; sub?: string }> = ({ label, value, accent = t.gold, sub }) => (
  <div className={cx('ll-stat', accent === t.teal && 'll-stat--teal')}>
    <div className="ll-stat__label">{label}</div>
    <div className="ll-stat__value">{value}</div>
    {sub && <div className="ll-stat__sub">{sub}</div>}
  </div>
);

// ----------------------------------------------------------------- rows ----
export const Avatar: React.FC<{ name: string; tone?: 'gold' | 'teal' | 'dim'; size?: 'sm' | 'md' }> = ({ name, tone = 'gold', size = 'md' }) => (
  <span className={cx('ll-avatar', size === 'sm' && 'll-avatar--sm', tone === 'teal' && 'll-avatar--teal', tone === 'dim' && 'll-avatar--dim')}>{initials(name)}</span>
);

export const MemberRow: React.FC<{ initials?: string; name: string; role: string; roleColor?: string; dim?: boolean; you?: boolean; trailing?: React.ReactNode }> = ({ name, role, roleColor, dim, you, trailing }) => (
  <div className="ll-row">
    <Avatar name={name} tone={dim ? 'dim' : role.toLowerCase() === 'captain' ? 'gold' : 'teal'} size="sm" />
    <span className="ll-row__name">{name}{you && <span className="ll-you"> · you</span>}</span>
    <span className="ll-row__meta" style={roleColor ? { color: roleColor } : undefined}>{role}</span>
    {trailing}
  </div>
);

// ---------------------------------------------------------------- async ----
export const Spinner: React.FC<{ size?: number }> = ({ size = 18 }) => (
  <span className="ll-spinner" style={{ width: size, height: size }} />
);

/** Standard async panel: loading spinner, error banner, or children. */
export const AsyncState: React.FC<{ loading: boolean; error?: string | null; children: React.ReactNode; label?: string }> = ({ loading, error, children, label = 'Loading…' }) => {
  if (loading) return <div className="ll-loading"><Spinner /> {label}</div>;
  if (error) return <Notice tone="err">{error}</Notice>;
  return <>{children}</>;
};

export const EmptyState: React.FC<{ icon?: IconName; title: string; children?: React.ReactNode; action?: React.ReactNode; style?: React.CSSProperties }> = ({ icon = 'amphora', title, children, action, style }) => (
  <div className="ll-empty" style={style}>
    <div className="ll-empty__mark"><Icon name={icon} size={56} /></div>
    <div className="ll-empty__title">{title}</div>
    {children && <p className="ll-empty__copy">{children}</p>}
    {action && <div style={{ marginTop: 20, display: 'flex', justifyContent: 'center', gap: 10 }}>{action}</div>}
  </div>
);

// ------------------------------------------------------------- credentials --
/** Copy to the clipboard without ever throwing: the async Clipboard API when the
 *  window may use it, else the legacy execCommand path (a hidden textarea).
 *  Resolves false when neither worked so the button simply stays as it was. */
async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Permission denied / window unfocused — fall through to the legacy path.
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={cx('ll-copy', done && 'is-done')}
      aria-label={done ? 'Copied' : `Copy ${label}`}
      title={done ? 'Copied' : `Copy ${label}`}
      onClick={() => {
        void copyText(value).then((ok) => {
          if (!ok) return;
          setDone(true);
          setTimeout(() => setDone(false), 1600);
        });
      }}
    >
      <Icon name={done ? 'check' : 'copy'} size={14} />
    </button>
  );
}

export function Credential({ label, value, copy = true }: { label: string; value: string; copy?: boolean }) {
  return (
    <div className="ll-cred">
      <div className="ll-cred__label">{label}</div>
      <div className={cx('ll-cred__value', !copy && 'll-cred__value--plain')}>
        <span>{value}</span>
        {copy && <CopyButton value={value} label={label.toLowerCase()} />}
      </div>
    </div>
  );
}

export function initials(name: string): string {
  const base = (name || '?').split('#')[0]!.trim();
  return base.slice(0, 2) || '?';
}
