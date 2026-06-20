// Riot account linking / verification (PLAN.md §3.4).
//
// Accounts routes are GLOBAL (no acting-guild header) — the client omits
// X-Guild-Id for these calls. Flow: submit gameName/tagLine/region -> the
// backend opens an ownership challenge and returns an attempt_id + the
// target_icon_id the user must set on their Riot profile. We render those
// verification instructions and poll verifyStatus until verified/expired.
//
// All server-provided strings render as React children (escaped). No raw HTML.

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useApi } from "../../api/ApiProvider";
import { useAsyncAction } from "../../api/useAsync";
import { toError } from "../../api/useAsync";
import { Button, ErrorBanner, Spinner, TextInput } from "../../components";
import type {
  AttemptStatusResponse,
  LinkAccountResponse,
} from "@lol/clients-ts";

const POLL_INTERVAL_MS = 4000;

export default function AccountsScreen(): JSX.Element {
  const api = useApi();

  const [gameName, setGameName] = useState("");
  const [tagLine, setTagLine] = useState("");
  const [region, setRegion] = useState("");

  const link = useAsyncAction(
    useCallback(
      (body: { gameName: string; tagLine: string; region: string }) =>
        api.accounts.link(body),
      [api],
    ),
  );

  const linked = link.data;

  const onSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      await link.run({
        gameName: gameName.trim(),
        tagLine: tagLine.trim(),
        region: region.trim(),
      });
    },
    [link, gameName, tagLine, region],
  );

  const valid =
    gameName.trim().length > 0 &&
    tagLine.trim().length > 0 &&
    region.trim().length > 0;

  return (
    <section aria-labelledby="accounts-heading">
      <h1 id="accounts-heading">Accounts</h1>
      <p>
        Link your Riot account by proving ownership: set the requested profile
        icon, then we verify it.
      </p>

      <form onSubmit={onSubmit} aria-label="Link Riot account">
        <ErrorBanner error={link.error} />
        <TextInput
          label="Game name"
          hint="The part before the # in your Riot ID."
          required
          value={gameName}
          onChange={(e) => setGameName(e.target.value)}
        />
        <TextInput
          label="Tag line"
          hint="The part after the # (without the #)."
          required
          value={tagLine}
          onChange={(e) => setTagLine(e.target.value)}
        />
        <TextInput
          label="Region"
          hint="e.g. euw1, na1, kr."
          required
          value={region}
          onChange={(e) => setRegion(e.target.value)}
        />
        <Button
          variant="primary"
          type="submit"
          busy={link.loading}
          disabled={!valid}
        >
          Link account
        </Button>
      </form>

      {linked ? <VerificationPanel linked={linked} /> : null}
    </section>
  );
}

interface VerificationPanelProps {
  linked: LinkAccountResponse;
}

function VerificationPanel({ linked }: VerificationPanelProps): JSX.Element {
  const api = useApi();
  const [status, setStatus] = useState<AttemptStatusResponse | null>(null);
  const [pollError, setPollError] = useState<Error | null>(null);

  const verified = status?.verified ?? linked.verified;
  const iconId = status?.target_icon_id ?? linked.target_icon_id;
  const expiresAt = status?.expires_at ?? linked.expires_at;
  const expired = isExpired(expiresAt);
  // Keep polling while not yet verified and the challenge has not expired.
  const polling = !verified && !expired;

  const attemptId = linked.attempt_id;

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async (): Promise<void> => {
      try {
        const next = await api.accounts.verifyStatus(attemptId);
        if (cancelled) return;
        setStatus(next);
        setPollError(null);
        if (!next.verified && !isExpired(next.expires_at)) {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err: unknown) {
        if (cancelled) return;
        setPollError(toError(err));
        timer = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };

    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [api, attemptId, polling]);

  return (
    <section aria-labelledby="verify-heading" data-verification-panel>
      <h2 id="verify-heading">Verify ownership</h2>
      <dl>
        <dt>Riot ID</dt>
        <dd>
          {linked.game_name}#{linked.tag_line}
        </dd>
        <dt>Attempt</dt>
        <dd>{linked.attempt_id}</dd>
      </dl>

      {verified ? (
        <p role="status" data-verify-status="verified">
          ✅ Account verified. You can stop changing your icon.
        </p>
      ) : expired ? (
        <p role="alert" data-verify-status="expired">
          This verification attempt expired. Re-link your account to try again.
        </p>
      ) : (
        <div data-verify-instructions>
          <p>
            Open the League of Legends client and set your profile icon to icon{" "}
            <strong>#{iconId}</strong> (the verification nonce). Keep this page
            open — we check automatically.
          </p>
          <p>
            Challenge expires at <time dateTime={expiresAt}>{expiresAt}</time>.
          </p>
          <p data-polling>
            <Spinner label="Waiting for verification" /> Checking your profile…
          </p>
        </div>
      )}

      <ErrorBanner error={pollError} />
    </section>
  );
}

/** True when an ISO-8601 expiry is in the past (invalid/empty -> not expired). */
function isExpired(iso: string): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return false;
  return t <= Date.now();
}
