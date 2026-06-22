// SkeletonTable — a loading placeholder that mirrors the DataTable chrome
// ([data-table]) so swapping from loading to loaded does not shift layout. It
// renders a real <table> skeleton (header + N placeholder rows of M cells).
//
// Accessibility: the table is decorative-while-loading. It carries
// aria-hidden + role="presentation" so AT does not announce the placeholder
// grid; the owning region should announce "loading" (e.g. via an existing
// Spinner or aria-busy). A visually-hidden <caption> keeps the markup valid.

import "./Skeleton.css";
import { Skeleton } from "./Skeleton";

export interface SkeletonTableProps {
  /** Number of placeholder body rows. Defaults to 5. */
  rows?: number;
  /** Number of columns. Defaults to 4. */
  columns?: number;
  /**
   * Accessible caption text. Kept for valid table markup; the table itself is
   * aria-hidden so this is not announced. Defaults to "Loading table".
   */
  caption?: string;
}

export function SkeletonTable({
  rows = 5,
  columns = 4,
  caption = "Loading table",
}: SkeletonTableProps): JSX.Element {
  const rowCount = Math.max(1, Math.floor(rows));
  const columnCount = Math.max(1, Math.floor(columns));
  const columnIndexes = Array.from({ length: columnCount }, (_, i) => i);
  const rowIndexes = Array.from({ length: rowCount }, (_, i) => i);

  return (
    <table data-skeleton-table aria-hidden="true" role="presentation">
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columnIndexes.map((col) => (
            <th key={col} scope="col">
              <Skeleton variant="line" />
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rowIndexes.map((row) => (
          <tr key={row}>
            {columnIndexes.map((col) => (
              <td key={col}>
                {/* First column reads as a primary identifier (wider); the
                    remaining columns are narrower secondary fields. */}
                <Skeleton variant="line" width={col === 0 ? "80%" : "55%"} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
