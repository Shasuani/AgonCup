// Shown when an authenticated user lacks the role for a gated route. The
// backend is the real enforcer; this is the friendly UX surface.

import { Link } from "react-router-dom";

export default function NotAuthorizedScreen(): JSX.Element {
  return (
    <main aria-labelledby="not-authorized-heading" data-not-authorized>
      <h1 id="not-authorized-heading">Not authorized</h1>
      <p>Your account doesn&apos;t have permission to view this page.</p>
      <p>
        <Link to="/">Back to home</Link>
      </p>
    </main>
  );
}
