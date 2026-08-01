import React from 'react';
import { theme as t } from './theme';

export const Kicker: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 12, letterSpacing: '.28em', textTransform: 'uppercase', color: t.gold }}>{children}</div>
);

export const H1: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <h1 style={{ fontFamily: t.display, fontWeight: 700, fontSize: 32, color: t.goldBright, margin: '10px 0 0', ...style }}>{children}</h1>
);

export const Panel: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <div style={{ border: `1px solid ${t.line}`, background: t.panel, padding: '26px 28px', ...style }}>{children}</div>
);

export const PrimaryButton: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({ style, disabled, ...p }) => (
  <button {...p} disabled={disabled} style={{ fontFamily: t.body, fontWeight: 600, fontSize: 13, letterSpacing: '.06em', background: disabled ? '#3a4759' : `linear-gradient(180deg,${t.gold},${t.goldDim})`, color: disabled ? '#0a1626' : '#0a1626', border: 'none', padding: '12px 26px', borderRadius: 2, cursor: disabled ? 'not-allowed' : 'pointer', textTransform: 'uppercase', opacity: disabled ? 0.55 : 1, ...style }} />
);

export const GhostButton: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'default' | 'danger' | 'gold' }> = ({ style, tone = 'default', disabled, ...p }) => {
  const color = tone === 'danger' ? t.red : tone === 'gold' ? t.gold : t.text;
  const border = tone === 'danger' ? 'rgba(196,74,74,.5)' : t.line;
  return <button {...p} disabled={disabled} style={{ fontFamily: t.body, fontSize: 13, background: 'transparent', color, border: `1px solid ${border}`, padding: '11px 22px', borderRadius: 2, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.45 : 1, ...style }} />;
};

export const StatCard: React.FC<{ label: string; value: React.ReactNode; accent?: string }> = ({ label, value, accent = t.gold }) => (
  <div style={{ border: `1px solid ${t.line}`, borderTop: `2px solid ${accent}`, background: t.panel, padding: '22px 24px' }}>
    <div style={{ fontSize: 12, letterSpacing: '.14em', textTransform: 'uppercase', color: t.textDim }}>{label}</div>
    <div style={{ fontFamily: t.display, fontSize: 38, color: accent === t.teal ? t.teal : t.goldBright, marginTop: 8 }}>{value}</div>
  </div>
);

export const MemberRow: React.FC<{ initials: string; name: string; role: string; roleColor?: string; dim?: boolean }> = ({ initials, name, role, roleColor = t.textDim, dim }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '11px 0', borderBottom: `1px solid ${t.lineSoft}` }}>
    <span style={{ width: 30, height: 30, borderRadius: '50%', background: t.panel3, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: dim ? t.gold : t.teal }}>{initials}</span>
    <span style={{ flex: 1, color: dim ? t.text : t.textStrong }}>{name}</span>
    <span style={{ fontSize: 11, color: roleColor, letterSpacing: '.06em' }}>{role}</span>
  </div>
);

export const Spinner: React.FC<{ size?: number }> = ({ size = 18 }) => (
  <span style={{ display: 'inline-block', width: size, height: size, border: `2px solid ${t.lineSoft}`, borderTopColor: t.gold, borderRadius: '50%', animation: 'llSpin .8s linear infinite' }} />
);

/** Standard async panel: loading spinner, error banner, or children. */
export const AsyncState: React.FC<{ loading: boolean; error?: string | null; children: React.ReactNode }> = ({ loading, error, children }) => {
  if (loading) return <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: t.textDim, padding: '18px 0' }}><Spinner /> Loading…</div>;
  if (error) return <div style={{ color: t.red, fontSize: 13, border: `1px solid rgba(196,74,74,.4)`, background: 'rgba(196,74,74,.06)', padding: '12px 16px', borderRadius: 2 }}>{error}</div>;
  return <>{children}</>;
};

export function initials(name: string): string {
  const base = (name || '?').split('#')[0]!.trim();
  return base.slice(0, 2) || '?';
}
