// List toolbar: a search box, a segmented filter, and a running count, laid out
// on one line above a table. The Accounts roster had this first (as one-off
// data-roster-* hooks); Tournaments and Teams now share the same three parts so
// the "find the row I want" affordance looks and behaves the same on every list.
//
// Purely presentational — the caller owns the query/filter state and does the
// filtering itself (every list here is small and already fully loaded).

import type { HTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { Icon } from "./Icon";

export function Toolbar({ children, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div data-toolbar {...rest}>
      {children}
    </div>
  );
}

export interface ToolbarSearchProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "aria-label"> {
  /** Accessible name of the search box (also its placeholder when none given). */
  label: string;
}

/** A pill search field with a leading magnifier. Renders a real `type=search`
 * input so it is announced as a searchbox and gets the browser's clear control. */
export function ToolbarSearch({ label, placeholder, ...rest }: ToolbarSearchProps): JSX.Element {
  return (
    <div data-toolbar-search>
      <Icon name="search" size={16} />
      <input type="search" aria-label={label} placeholder={placeholder ?? label} {...rest} />
    </div>
  );
}

export interface SegmentedOption<T extends string> {
  id: T;
  label: ReactNode;
}

export interface SegmentedProps<T extends string> {
  /** Accessible name for the group ("Filter by status"). */
  label: string;
  options: ReadonlyArray<SegmentedOption<T>>;
  value: T;
  onChange: (next: T) => void;
}

/** A segmented control: one pressed option at a time, exposed as toggle
 * buttons in a named group (what the Accounts tests already assert). */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: SegmentedProps<T>): JSX.Element {
  return (
    <div data-segmented role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          data-segmented-option
          data-active={value === o.id ? "true" : undefined}
          aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** "12 of 38 tournaments" — right-aligned, tabular figures. */
export function ToolbarCount({ children }: { children: ReactNode }): JSX.Element {
  return <p data-toolbar-count>{children}</p>;
}

/** The count line for a filtered list: total when nothing is filtered out,
 * "N of M" otherwise. `noun` is the singular; plural is `noun + "s"`. */
export function countLabel(shown: number, total: number, noun: string): string {
  const plural = (n: number): string => `${n} ${n === 1 ? noun : `${noun}s`}`;
  return shown === total ? plural(total) : `${shown} of ${plural(total)}`;
}
