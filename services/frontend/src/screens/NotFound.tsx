// 404 surface for unknown paths INSIDE the authenticated AppLayout subtree.
//
// Rendered as the catch-all child of the <AppLayout> route, so it appears inside
// AppLayout's <main id="main-content"> landmark. It must therefore NOT render its
// own <main> (that would nest landmarks) — it uses a <section> labelled by its
// heading. Reuses the shared ErrorState chrome (error-state.svg) + a link home.

import { Link } from "react-router-dom";
import { ErrorState } from "../components/EmptyState";

export default function NotFound(): JSX.Element {
  return (
    <section aria-labelledby="not-found-heading" data-not-found>
      <h1 id="not-found-heading">Page not found</h1>
      <ErrorState
        title="We couldn't find that page"
        description="The page you're looking for doesn't exist or has moved."
        action={
          <Link to="/" data-not-found-home>
            Back to dashboard
          </Link>
        }
      />
    </section>
  );
}
