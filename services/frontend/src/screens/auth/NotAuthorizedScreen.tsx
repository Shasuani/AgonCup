// Shown when an authenticated user lacks the role for a gated route. The
// backend is the real enforcer; this is the friendly UX surface. Reached via
// <Navigate> (replaces the route), so it is the sole landmark on screen and may
// safely render its own <main> + data-not-authorized hook (preserved).

import { Link } from "react-router-dom";
import { ErrorState } from "../../components/EmptyState";

export default function NotAuthorizedScreen(): JSX.Element {
  return (
    <main aria-labelledby="not-authorized-heading" data-not-authorized>
      <h1 id="not-authorized-heading">Not authorized</h1>
      <ErrorState
        title="You don't have access to this page"
        description="Your account doesn't have permission to view this page. If you think this is a mistake, contact a platform operator."
        action={
          <Link to="/" data-not-authorized-home>
            Back to home
          </Link>
        }
      />
    </main>
  );
}
