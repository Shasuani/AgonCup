// Authenticated landing page. Minimal; the real dashboard lands in the Screens
// phase. Links into the primary sections.
import { Link } from "react-router-dom";

export default function HomeScreen(): JSX.Element {
  return (
    <section aria-labelledby="home-heading">
      <h1 id="home-heading">Dashboard</h1>
      <p>Welcome to the LeagueOfLeagues admin console.</p>
      <ul>
        <li>
          <Link to="/tournaments">Tournaments</Link>
        </li>
        <li>
          <Link to="/teams">Teams</Link>
        </li>
        <li>
          <Link to="/accounts">Accounts</Link>
        </li>
        <li>
          <Link to="/disputes">Dispute / Override queue</Link>
        </li>
        <li>
          <Link to="/audit">Audit log</Link>
        </li>
      </ul>
    </section>
  );
}
