// Login screen. Single CTA that starts the backend Discord-OAuth flow. If the
// user is already authenticated, it redirects to the app home.

import { Navigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { Button } from "../../components";

export default function LoginScreen(): JSX.Element {
  const { isAuthenticated, login } = useAuth();

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  return (
    <main aria-labelledby="login-heading" data-login>
      <h1 id="login-heading">LeagueOfLeagues — Admin</h1>
      <p>Sign in with Discord to manage tournaments, teams, and disputes.</p>
      <Button variant="primary" onClick={login}>
        Sign in with Discord
      </Button>
    </main>
  );
}
