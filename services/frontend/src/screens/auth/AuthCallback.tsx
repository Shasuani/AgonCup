// OAuth callback handler.
//
// The backend completes Discord OAuth and 303s the browser to:
//     /auth/callback#access_token=<jwt>
// (built by `_callback_redirect` in services/backend/src/app/auth/router.py).
// We read the token from the URL fragment (NOT the query string — a fragment is
// never sent to servers / proxies / logs), install the session, scrub the
// fragment from history, and navigate into the app.
//
// Only the SHORT-LIVED access token comes this way. The refresh token arrives on
// the same response as an HttpOnly cookie, so the long-lived credential never
// touches the URL (where it would linger in history and screenshots) and is
// never visible to JavaScript.

import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { Spinner } from "../../components/Spinner";
import { ErrorBanner } from "../../components/ErrorBanner";

/** Parse `#access_token=..` from a fragment string. */
export function parseCallbackFragment(hash: string): { accessToken: string } | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const accessToken = params.get("access_token");
  if (!accessToken) return null;
  return { accessToken };
}

export default function AuthCallback(): JSX.Element {
  const { setSession, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const handledRef = useRef(false);

  useEffect(() => {
    if (handledRef.current) return;
    handledRef.current = true;

    const parsed = parseCallbackFragment(window.location.hash);
    if (!parsed) {
      setError("Sign-in did not return the expected tokens. Please try again.");
      return;
    }
    setSession({ accessToken: parsed.accessToken });

    // Scrub the tokens from the URL so they don't linger in history.
    window.history.replaceState(null, "", window.location.pathname);
    navigate("/", { replace: true });
  }, [setSession, navigate]);

  if (error) {
    return (
      <main data-auth-callback>
        <ErrorBanner error={error} />
        <Navigate to="/login" replace />
      </main>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  return (
    <main data-auth-callback>
      <Spinner label="Completing sign-in" />
    </main>
  );
}
