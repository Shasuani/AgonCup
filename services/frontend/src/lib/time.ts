// Relative + short time formatting shared by the dashboard feeds.

/** "just now", "5 min ago", "3 h ago", "2 d ago"; falls back to a local date. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d} d ago`;
  return new Date(at).toLocaleDateString();
}

/** "in 2d 4h", "in 1h 12m", "in 45 min"; null once the time has passed. */
export function timeUntil(iso: string, now: number = Date.now()): string | null {
  const at = Date.parse(iso);
  if (Number.isNaN(at) || at <= now) return null;
  const s = Math.floor((at - now) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `in ${d}d ${h}h`;
  if (h > 0) return `in ${h}h ${String(m).padStart(2, "0")}m`;
  return `in ${Math.max(1, m)} min`;
}

/** Short local date-time ("7 Sep, 21:00"), or the raw value if unparseable. */
export function shortDateTime(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
