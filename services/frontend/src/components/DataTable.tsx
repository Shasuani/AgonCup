// Generic, accessible data table. Column-driven; renders a <table> with a
// <caption> for screen readers. Cell values are rendered as React children
// (escaped by default) — never as raw HTML.
//
// LOADING + EMPTY live here on purpose. Every screen that shows a list used to
// invent its own treatment (a bare spinner, a one-line <p>, a full illustrated
// panel, or nothing), so the same wait looked different on each tab. A table
// knows its own column count and header widths, so it is the only place that
// can render a placeholder which does not shift the layout when the data lands:
//
//   loading (first load) -> shimmer rows UNDER the real header
//   loading (refresh)    -> the existing rows stay, marked aria-busy
//   empty                -> one centered panel spanning every column
//
// A refresh deliberately keeps stale rows on screen: blanking a table the admin
// is reading, to show a spinner for 200ms, loses their place for no gain.

import { Fragment } from "react";
import type { Key, ReactNode } from "react";
import { Skeleton } from "./Skeleton";

export interface Column<Row> {
  /** Stable column id (also the header key). */
  id: string;
  header: ReactNode;
  /** Cell renderer for a row. */
  cell: (row: Row) => ReactNode;
}

export interface DataTableProps<Row> {
  caption: string;
  columns: ReadonlyArray<Column<Row>>;
  rows: ReadonlyArray<Row>;
  /** Stable per-row key extractor. */
  rowKey: (row: Row) => Key;
  loading?: boolean;
  /** Shown when there are no rows and not loading. */
  emptyMessage?: ReactNode;
  /**
   * Optional action rendered under the empty message (typically a <Button>
   * that creates the first record, or clears a filter).
   */
  emptyAction?: ReactNode;
  /** How many shimmer rows to draw on the first load. Defaults to 5. */
  loadingRows?: number;
  /**
   * Hide the caption visually (it stays in the accessibility tree). Use when
   * the surrounding <ScreenHeader> already names the table on screen.
   */
  hideCaption?: boolean;
  /**
   * Makes rows expandable. Returns the detail content for a row; when set, a
   * leading toggle column is added automatically and the detail renders in a
   * full-width row underneath. Omit for a plain table.
   */
  renderExpanded?: (row: Row) => ReactNode;
  /** Keys of the currently expanded rows (see `rowKey`). */
  expandedKeys?: ReadonlySet<Key>;
  /** Called with the row whose toggle was activated. */
  onToggleExpand?: (row: Row) => void;
  /** Accessible name for a row's toggle, e.g. `(t) => \`roster of ${t.name}\``. */
  expandLabel?: (row: Row) => string;
}

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  loading = false,
  emptyMessage = "No records.",
  emptyAction,
  loadingRows = 5,
  hideCaption = false,
  renderExpanded,
  expandedKeys,
  onToggleExpand,
  expandLabel,
}: DataTableProps<Row>): JSX.Element {
  // Only the FIRST load gets placeholders; a refresh over existing rows keeps
  // them visible (see the note above).
  const showSkeleton = loading && rows.length === 0;
  const showEmpty = !loading && rows.length === 0;
  const skeletonRows = Array.from(
    { length: Math.max(1, loadingRows) },
    (_, i) => i,
  );

  const expandable = renderExpanded !== undefined;
  // The toggle occupies a column of its own, so every colSpan below has to
  // account for it or the empty/detail rows come up one cell short.
  const totalColumns = columns.length + (expandable ? 1 : 0);

  return (
    <div data-table-scroll>
      <table
        data-table
        data-expandable={expandable || undefined}
        aria-busy={loading || undefined}
      >
        <caption data-caption-hidden={hideCaption || undefined}>
          {caption}
        </caption>
        <thead>
          <tr>
            {expandable ? (
              <th scope="col" data-expand-col>
                <span data-visually-hidden>Expand</span>
              </th>
            ) : null}
            {columns.map((col) => (
              <th key={col.id} scope="col">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {showSkeleton ? (
            skeletonRows.map((r) => (
              // Placeholder rows are decorative: aria-busy on the table already
              // tells AT the data is coming.
              <tr key={`skeleton-${r}`} aria-hidden="true">
                {expandable ? <td /> : null}
                {columns.map((col, i) => (
                  <td key={col.id}>
                    {/* Column 0 reads as the primary identifier, so it is wider. */}
                    <Skeleton variant="line" width={i === 0 ? "70%" : "45%"} />
                  </td>
                ))}
              </tr>
            ))
          ) : showEmpty ? (
            <tr>
              <td colSpan={totalColumns} data-empty>
                <div data-empty-inner>
                  <p data-empty-message>{emptyMessage}</p>
                  {emptyAction !== undefined && emptyAction !== null ? (
                    <div data-empty-action>{emptyAction}</div>
                  ) : null}
                </div>
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const key = rowKey(row);
              const open = expandable && expandedKeys?.has(key) === true;
              return (
                <Fragment key={key}>
                  <tr data-row-open={open || undefined}>
                    {expandable ? (
                      <td data-expand-cell>
                        <button
                          type="button"
                          data-expand-toggle
                          aria-expanded={open}
                          aria-label={
                            expandLabel ? expandLabel(row) : "Show details"
                          }
                          onClick={() => onToggleExpand?.(row)}
                        >
                          <span aria-hidden="true">›</span>
                        </button>
                      </td>
                    ) : null}
                    {columns.map((col) => (
                      <td key={col.id}>{col.cell(row)}</td>
                    ))}
                  </tr>
                  {open ? (
                    <tr data-expanded-row>
                      <td colSpan={totalColumns}>{renderExpanded?.(row)}</td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
