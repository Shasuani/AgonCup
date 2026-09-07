// Breadcrumb trail derived from the current path + a label map.
//
// The home crumb is always "Dashboard" -> "/". Subsequent path segments are
// mapped to their human label via NAV_LABELS (mirrors AppLayout's NAV_ITEMS) and
// rendered as <Link>s, except the last crumb which is the current page (marked
// aria-current="page", not a link). Unknown segments fall back to a title-cased
// version of the raw segment so deep links never render empty.

import { Fragment } from "react";
import { Link, useLocation } from "react-router-dom";
import { Icon } from "../components/Icon";

import "./Breadcrumbs.css";

/** Human labels for top-level route segments (mirrors NAV_ITEMS). */
const NAV_LABELS: Record<string, string> = {
  servers: "Servers",
  tournaments: "Tournaments",
  teams: "Teams",
  accounts: "Accounts",
  history: "Match history",
  disputes: "Disputes",
  audit: "Audit log",
  guilds: "Guilds",
};

function labelFor(segment: string): string {
  const known = NAV_LABELS[segment];
  if (known) return known;
  // Fall back to a readable title-cased version of the raw segment.
  return segment
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

interface Crumb {
  label: string;
  to: string;
}

export function Breadcrumbs(): JSX.Element {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);

  const crumbs: Crumb[] = [{ label: "Dashboard", to: "/" }];
  let acc = "";
  for (const seg of segments) {
    acc += `/${seg}`;
    crumbs.push({ label: labelFor(seg), to: acc });
  }

  return (
    <nav aria-label="Breadcrumb" data-breadcrumbs>
      <ol>
        {crumbs.map((crumb, i) => {
          const isLast = i === crumbs.length - 1;
          return (
            <Fragment key={crumb.to}>
              <li>
                {isLast ? (
                  <span aria-current="page" data-breadcrumb-current>
                    {crumb.label}
                  </span>
                ) : (
                  <Link to={crumb.to}>{crumb.label}</Link>
                )}
              </li>
              {isLast ? null : (
                <li aria-hidden="true" data-breadcrumb-sep>
                  <Icon name="chevron-right" size={14} />
                </li>
              )}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
