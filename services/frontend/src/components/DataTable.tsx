// Generic, accessible data table. Column-driven; renders a <table> with a
// <caption> for screen readers. Cell values are rendered as React children
// (escaped by default) — never as raw HTML.

import type { Key, ReactNode } from "react";
import { Spinner } from "./Spinner";

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
}

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  loading = false,
  emptyMessage = "No records.",
}: DataTableProps<Row>): JSX.Element {
  return (
    <table data-table>
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col.id} scope="col">
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {loading ? (
          <tr>
            <td colSpan={columns.length}>
              <Spinner label="Loading rows" />
            </td>
          </tr>
        ) : rows.length === 0 ? (
          <tr>
            <td colSpan={columns.length} data-empty>
              {emptyMessage}
            </td>
          </tr>
        ) : (
          rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((col) => (
                <td key={col.id}>{col.cell(row)}</td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
