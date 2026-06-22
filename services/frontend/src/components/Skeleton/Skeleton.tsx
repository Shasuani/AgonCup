// Presentational loading placeholders ("skeletons"). These render an inert,
// shimmering block while real content is being fetched. The shimmer is a pure
// CSS animation that collapses to a static muted block under
// prefers-reduced-motion (handled in Skeleton.css).
//
// Accessibility: skeletons are decorative — they convey "loading" visually only.
// The surrounding region should own the live announcement (e.g. an existing
// Spinner with role=status, or aria-busy on a container). Each Skeleton root is
// therefore aria-hidden so assistive tech does not read placeholder geometry.

import type { CSSProperties, HTMLAttributes } from "react";

import "./Skeleton.css";

export type SkeletonVariant = "line" | "rect" | "circle";

export interface SkeletonProps
  extends Omit<HTMLAttributes<HTMLSpanElement>, "children"> {
  /** Shape of the placeholder. Defaults to a single text line. */
  variant?: SkeletonVariant;
  /** CSS width (number => px). Defaults to 100% for line/rect. */
  width?: number | string;
  /** CSS height (number => px). Lines derive height from the type scale. */
  height?: number | string;
  /**
   * For variant="line": how many stacked lines to render. The last line is
   * rendered slightly shorter to mimic a ragged paragraph edge. Defaults to 1.
   */
  lines?: number;
  /** Override the corner radius (number => px). */
  radius?: number | string;
}

function toCssSize(value: number | string | undefined): string | undefined {
  if (value === undefined) return undefined;
  return typeof value === "number" ? `${value}px` : value;
}

export function Skeleton({
  variant = "line",
  width,
  height,
  lines = 1,
  radius,
  className,
  style,
  ...rest
}: SkeletonProps): JSX.Element {
  const rootClassName = ["lol-skeleton", className].filter(Boolean).join(" ");

  // A circle is square: a single size drives both axes.
  if (variant === "circle") {
    const dimension = toCssSize(width ?? height) ?? "40px";
    const circleStyle: CSSProperties = {
      width: dimension,
      height: dimension,
      ...style,
    };
    return (
      <span
        className={rootClassName}
        data-skeleton="circle"
        aria-hidden="true"
        style={circleStyle}
        {...rest}
      />
    );
  }

  if (variant === "rect") {
    const rectStyle: CSSProperties = {
      width: toCssSize(width),
      height: toCssSize(height) ?? "120px",
      borderRadius: toCssSize(radius),
      ...style,
    };
    return (
      <span
        className={rootClassName}
        data-skeleton="rect"
        aria-hidden="true"
        style={rectStyle}
        {...rest}
      />
    );
  }

  // variant === "line": render `lines` stacked bars. A single line is just one
  // bar; multiple lines get a wrapper so they stack with consistent rhythm.
  const count = Math.max(1, Math.floor(lines));
  const lineStyle = (index: number): CSSProperties => {
    const isLast = index === count - 1 && count > 1;
    return {
      width: isLast ? "60%" : toCssSize(width),
      height: toCssSize(height),
      borderRadius: toCssSize(radius),
    };
  };

  if (count === 1) {
    return (
      <span
        className={rootClassName}
        data-skeleton="line"
        aria-hidden="true"
        style={{ ...lineStyle(0), ...style }}
        {...rest}
      />
    );
  }

  return (
    <span
      className={["lol-skeleton-lines", className].filter(Boolean).join(" ")}
      data-skeleton="lines"
      aria-hidden="true"
      style={style}
      {...rest}
    >
      {Array.from({ length: count }, (_, index) => (
        <span
          key={index}
          className="lol-skeleton"
          data-skeleton="line"
          style={lineStyle(index)}
        />
      ))}
    </span>
  );
}
