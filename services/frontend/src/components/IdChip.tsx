// A UUID, shown as an identifier instead of as content.
//
// Several screens print raw 36-char UUIDs as a table cell's primary value (team
// captain, team members, audit guild, guilds list). They dominate the row, they
// are unreadable, and the one thing anyone actually wants to do with them —
// paste one somewhere else — was impossible without selecting the text by hand.
//
// This shows a truncated, monospaced prefix with the full value as the title,
// and copies the WHOLE id on click. The click target is a real <button> so it is
// keyboard reachable; the copy result is announced via a polite live region.
//
// Clipboard access is not universal (insecure origins, jsdom, older Safari). If
// `navigator.clipboard` is unavailable or rejects, the chip stays a chip and
// says so rather than silently pretending it copied.

import { useCallback, useEffect, useRef, useState } from "react";

import "./IdChip.css";

/** How many leading characters of the id to show. A v4 UUID's first block. */
const PREFIX_LENGTH = 8;

export interface IdChipProps {
  /** The full identifier. Rendered truncated; copied in full. */
  value: string;
  /**
   * What this id identifies, e.g. "team" — used for the accessible label
   * ("Copy team id"). Defaults to a generic "id".
   */
  kind?: string;
  /** Show the whole value instead of truncating (short ids, e.g. join codes). */
  full?: boolean;
}

type CopyState = "idle" | "copied" | "failed";

export function IdChip({ value, kind = "id", full = false }: IdChipProps): JSX.Element {
  const [state, setState] = useState<CopyState>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Don't leave a timer running against an unmounted chip (rows unmount on
  // every table reload).
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  const flash = useCallback((next: CopyState) => {
    setState(next);
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setState("idle"), 1600);
  }, []);

  const copy = useCallback(async () => {
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    if (clipboard === undefined) {
      flash("failed");
      return;
    }
    try {
      await clipboard.writeText(value);
      flash("copied");
    } catch {
      flash("failed");
    }
  }, [value, flash]);

  const shown = full || value.length <= PREFIX_LENGTH ? value : value.slice(0, PREFIX_LENGTH);
  const truncated = shown !== value;

  return (
    <span data-id-chip data-state={state === "idle" ? undefined : state}>
      <button
        type="button"
        data-id-chip-button
        title={value}
        aria-label={`Copy ${kind} ${value}`}
        onClick={copy}
      >
        <code data-id-chip-value>
          {shown}
          {truncated ? <span aria-hidden="true">…</span> : null}
        </code>
        <span data-id-chip-hint aria-hidden="true">
          {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
        </span>
      </button>
      <span data-id-chip-live role="status" aria-live="polite">
        {state === "copied"
          ? `${kind} copied`
          : state === "failed"
            ? "Copy failed — select the id manually"
            : ""}
      </span>
    </span>
  );
}
