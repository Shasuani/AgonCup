# GitLab CI/CD — setup and operation

Pipeline: [`.gitlab-ci.yml`](../.gitlab-ci.yml) · Stack: [`infra/docker-compose.prod.yml`](../infra/docker-compose.prod.yml) ·
Edge: [`infra/docker-compose.edge.yml`](../infra/docker-compose.edge.yml) ·
Server script: [`infra/deploy.sh`](../infra/deploy.sh)

```
 test ────────────────► build ──────────► security ────────► deploy
 ruff / mypy / pytest   python-base       trivy secrets      ssh + scp
 RLS matrix (real PG)   node-base         trivy images       compose pull/up
 vitest (SPA)           4 service images  (CRITICAL blocks)  migrate + caddy reload
 desktop tsc + tests    -> GitLab registry
```

**One environment for now:** `development`, host **`lol.gameras.gr`**.

| Trigger | Tests | Build | Deploy |
|---------|-------|-------|--------|
| Push to any branch | ✅ | — | — |
| MR → `devel` | ✅ | ✅ | 🔘 manual `deploy:review` button |
| Merge into `devel` | ✅ | ✅ | ✅ automatic |

`deploy:review` and `deploy:devel` hit the **same server and domain** — a review
deploy replaces what devel is serving until the next merge. That is deliberate
while there is one host.

Production is not wired up. A commented `deploy:production` job sits at the
bottom of `.gitlab-ci.yml`; `STACK=prod` keeps its containers, data directory and
Caddy site file separate, so it can share this server or move to another one.

---

## 1. Create the `devel` branch

The default branch is currently `shasuani`. Create `devel` from it and make it
the default so MRs target it:

```bash
git checkout -b devel
git push -u origin devel
```

Then **Settings → Repository → Branch defaults** → set `devel` as default, and
**Protected branches** → protect `devel` (so protected CI variables are exposed
to its pipelines).

> Set the CI/CD variables (§3) **before** pushing `devel`, or the first pipeline
> fails at the deploy job with an empty `DEPLOY_HOST`.

---

## 2. Server prerequisites

The runner and the app live on the same machine here, but the deploy still goes
over SSH so nothing changes if they are split later.

**The actual host is `rust.gameras.gr` (144.76.88.206) and the deploy user is
`loltournament`.** Note the SSH target and the served domain are *different
names for the same machine*: you connect to `rust.gameras.gr`, Caddy serves
`lol.gameras.gr`. Both A records already point at the same IP.

```bash
# Docker + compose plugin
curl -fsSL https://get.docker.com | sh

# Deploy user, in the docker group (the user already exists here — only the
# group membership and the key are new)
useradd -m -s /bin/bash loltournament   # skip if present
usermod -aG docker loltournament

# Deploy key (generate it on your workstation, see §3)
mkdir -p /home/loltournament/.ssh
echo "<public key>" >> /home/loltournament/.ssh/authorized_keys
chmod 700 /home/loltournament/.ssh
chmod 600 /home/loltournament/.ssh/authorized_keys
chown -R loltournament:loltournament /home/loltournament/.ssh
```

Also required:

- **DNS**: `lol.gameras.gr` → this server's public IP, resolving *before* the
  first deploy. Caddy's ACME challenge fails otherwise and there is no certificate.
  (Already satisfied: both `lol.` and `rust.gameras.gr` resolve to 144.76.88.206.)
- **Ports 80 and 443 free *and reachable from the internet*.** Caddy binds both.
  If nginx/apache/another stack already holds them the edge container will not
  start — and if a host or cloud firewall blocks them, ACME cannot complete and
  there is no certificate. As of this writing neither port answers from outside;
  verify with `ss -lntp | grep -E ':(80|443)\b'` and check the Hetzner cloud
  firewall before the first deploy.
- Nothing else to install — no Python, no Node, no repo clone. The server only
  pulls images.

> **Gotcha — `DEPLOY_HOST` with a docker executor.** The deploy job runs *inside a
> container* on this machine, so `127.0.0.1` means "the job container", not the
> host. Use the server's real hostname or public IP even when deploying to itself.

---

## 3. CI/CD variables

**Settings → CI/CD → Variables.** Mark everything secret as **Masked**, and
**Protected** (then only protected branches like `devel` can read it).

> **Protected variables break `deploy:review`.** An MR pipeline runs on the
> *source* branch, so an MR from an unprotected feature branch cannot read a
> protected variable — the manual review deploy then fails on an empty
> `DEPLOY_HOST`. Tests and builds are unaffected (they need no secrets). If you
> want the review button to work, either protect the feature branches too
> (wildcard, e.g. `feat-*`) or leave the deploy variables unprotected.

**You need to create 6.** Everything else either has a sensible default or is
generated on the server (§4a). Realistically make it **9** — add `RIOT_API_KEY`
and the two `DISCORD_CLIENT_*` or the app cannot verify accounts or log anyone
in. With one environment, leave every variable's scope at **All (default)**.

Minimum set: `SSH_PRIVATE_KEY`, `DEPLOY_HOST`, `DEPLOY_USER`,
`REGISTRY_DEPLOY_USER`, `REGISTRY_DEPLOY_TOKEN`, `ACME_EMAIL`.

Values are written into a compose `.env` on the server, which is both the
interpolation source and the container environment. So: **no spaces, no `#`, and
no `$` inside any secret.** `secrets.token_urlsafe()` output is always safe.

### Server access

`SSH_PRIVATE_KEY` is a **dedicated, passphrase-less** deploy key — never a
personal one. It is stored in a CI variable, so every job and every Maintainer
can read it; a passphrase is impossible because the deploy job just writes the
key to a file (no agent). Generate it with:

```bash
ssh-keygen -t ed25519 -C "gitlab-ci@loltournament" -f secrets/lol_deploy_ci -N ""
```

`secrets/` is gitignored *and* dockerignored (the service builds use the repo
root as their build context). The `.pub` half goes in the server's
`authorized_keys` (§2); the private half goes in the variable below. The job
writes it to `~/.ssh/id_rsa` — that is only a filename, an ed25519 key works.

| Variable | Required | Example / note |
|----------|----------|----------------|
| `SSH_PRIVATE_KEY` | ✅ | Full private key including the BEGIN/END lines. Do **not** mask (multiline). |
| `DEPLOY_HOST` | ✅ | `rust.gameras.gr` (or `144.76.88.206`) — the SSH target, **not** the served domain. Never `127.0.0.1` |
| `DEPLOY_USER` | ✅ | `loltournament` |
| `DEPLOY_SSH_PORT` | — | default `22` |
| `DEPLOY_PATH` | — | default `/home/$DEPLOY_USER/lol` → `/home/loltournament/lol`. Leave unset |

### Registry pull (server side)

The CI job authenticates to the registry with GitLab's built-in job token — you
do not configure that. But the job token **dies with the job**, so the server
gets its own long-lived credential. Create it at **Settings → Repository →
Deploy tokens** with scope **`read_registry`**.

| Variable | Required | Note |
|----------|----------|------|
| `REGISTRY_DEPLOY_USER` | ✅ | the deploy token's username |
| `REGISTRY_DEPLOY_TOKEN` | ✅ | the deploy token itself |

Without this, `docker compose up -d` after a server reboot cannot pull images.

### TLS

| Variable | Required | Note |
|----------|----------|------|
| `ACME_EMAIL` | ✅ | Let's Encrypt contact for expiry notices |

`APP_DOMAIN` is **not** a GitLab variable — it is set to `lol.gameras.gr` in
`.gitlab-ci.yml`. Override it with an environment-scoped variable of the same
name when a second environment appears.

### Application secrets

Six secrets are **not** in this list on purpose — `POSTGRES_PASSWORD`,
`APP_DB_PASSWORD`, `JWT_SECRET`, `PII_ENCRYPTION_KEY`, `BOT_SERVICE_TOKEN` and
`LIFECYCLE_SERVICE_TOKEN`. Nobody issues them; they just have to be random and
stable, so `infra/deploy.sh` generates them on the server on the first deploy and
persists them in `<APP_DIR>/.secrets.env` (chmod 600), reusing them verbatim
afterwards. See §4a.

| Variable | Required | Note |
|----------|----------|------|
| `RIOT_API_KEY` | ✅¹ | developer.riotgames.com personal key |
| `RIOT_REGION` | — | default `europe` |
| `DISCORD_CLIENT_ID` | —² | Discord developer portal |
| `DISCORD_CLIENT_SECRET` | —² | |
| `DISCORD_BOT_TOKEN` | —³ | |
| `LOG_LEVEL` | — | default `INFO` |

¹ Account-V1 is **required** for account verification — `/accounts/verify-desktop`
resolves the canonical PUUID server-side and rejects without a key.
² Without them Discord OAuth login is unavailable (the SPA's dev-login is
development-only and this deploy sets `ENVIRONMENT=production`).
³ **Presence of `DISCORD_BOT_TOKEN` is what enables the bot container.** The
deploy writes `COMPOSE_PROFILES=bot` only when it is set — otherwise the bot is
left out of the stack instead of crash-looping on a missing token.

`DISCORD_REDIRECT_URI` is derived automatically as
`https://lol.gameras.gr/api/v1/auth/callback`. Register exactly that in the
Discord developer portal.

---

## 4. What lands on the server

```
/home/loltournament/lol/
├── devel/                       # one directory per stack (STACK=devel)
│   ├── .env                     # generated per deploy, chmod 600
│   ├── .secrets.env             # SELF-MANAGED SECRETS — back this up (§4a)
│   ├── .overrides.env           # CI-provided overrides, usually empty
│   ├── .registry.env            # deploy token, chmod 600
│   ├── docker-compose.prod.yml
│   ├── deploy.sh
│   ├── backups/
│   └── data/{postgres,redis}/   # ALL persistent state — back this up
└── edge/                        # shared Caddy, independent of any stack
    ├── .env                     # ACME_EMAIL
    ├── Caddyfile                # global options + import sites/*.caddy
    ├── docker-compose.edge.yml
    ├── sites/devel.caddy        # generated; each stack writes only its own
    └── data/caddy/              # CERTIFICATES — losing this re-issues them,
                                 # and Let's Encrypt rate-limits that
```

Containers: `lol-devel-{postgres,redis,backend,worker,bot,frontend}` plus
`lol-caddy`. **No host ports except Caddy's 80/443** — Postgres and Redis are
reachable only from inside the stack.

Routing (`sites/devel.caddy`): `/api/*` and `/healthz` → backend:8000,
`/metrics*` → 404 (not public), everything else → the SPA's nginx. The realtime
WebSocket at `/api/v1/realtime/ws` rides the same `/api/*` rule — Caddy passes
upgrades through untouched.

---

## 4a. Self-managed secrets

On the first deploy `infra/deploy.sh` generates these and writes them to
`<APP_DIR>/.secrets.env`:

| Secret | Used for |
|--------|----------|
| `POSTGRES_PASSWORD` | owner/migration DSN |
| `APP_DB_PASSWORD` | the non-`BYPASSRLS` `app_rw` role the request path uses |
| `JWT_SECRET` | access-token signing |
| `PII_ENCRYPTION_KEY` | Fernet key for OAuth tokens + verification state at rest |
| `BOT_SERVICE_TOKEN` | bot → backend `/integrations/discord` bridge |
| `LIFECYCLE_SERVICE_TOKEN` | lifecycle / purge endpoints |

Every later deploy reuses them byte-for-byte. Values are URL-safe base64 with the
padding stripped, so they are safe both inside a compose `.env` and embedded in a
DSN. `DATABASE_URL` and `APP_DATABASE_URL` are composed on the server from the
resolved passwords.

> ⚠️ **`.secrets.env` is the crown jewels.** Lose it and the app can no longer
> authenticate to its own database, every session is invalidated, and previously
> stored PII becomes **undecryptable** — `PII_ENCRYPTION_KEY` is the only thing
> that can read it back. Back it up together with `data/`.

**Overriding.** Setting a CI variable with the same name wins: the deploy job
ships it in `.overrides.env` and `deploy.sh` prefers it. The generated value
stays in `.secrets.env`, so removing the override reverts to it.

Two caveats when overriding:

- **`POSTGRES_PASSWORD` does not re-key an existing cluster.** The password is
  baked into the data directory at `initdb` time; changing the variable only
  changes what the app *tries* to connect with. To actually rotate it:
  `docker compose exec postgres psql -U lol -c "ALTER ROLE lol PASSWORD '...'"`
  and then set the variable to match.
- **Rotating `PII_ENCRYPTION_KEY` needs both keys.** The field accepts a
  comma-separated list, newest first — put the new key first and keep the old one
  so existing rows stay readable.

`APP_DB_PASSWORD` is the exception: `migrate.sh` runs `ALTER ROLE app_rw` on every
deploy, so changing it takes effect immediately.

## 5. Migrations

`infra/deploy.sh` runs [`services/backend/scripts/migrate.sh`](../services/backend/scripts/migrate.sh)
inside the backend container, which is a plain `alembic upgrade head` — it works
on an empty database and an existing one alike.

That was not always true. The former `0001..0012` chain could not be replayed on
a fresh database: migration `0002` built its tables from the **live ORM metadata**
(`Base.metadata.sorted_tables`), so against an empty database it already produced
the fully-evolved schema, and `0004..0012` — plain `ADD COLUMN` steps — aborted
with `DuplicateColumnError: column "code" of relation "verification_attempts"
already exists`. The chain is now a single **static** baseline
([`0001_baseline.py`](../services/backend/alembic/versions/0001_baseline.py))
that also creates the `app_rw` role, the per-guild `guild_isolation` policies and
`self_role_read`.

The rule that fixed it, worth keeping: **a migration must never derive DDL from
`app.models`.** Migrations are immutable historical records; models keep moving.
The `test:db-rls` job runs `alembic check` after migrating, which fails the
pipeline if the migrated schema and the models have drifted.

`migrate.sh` also rotates the **`app_rw` password**. The baseline hardcodes a dev
password (`app_rw`/`app_rw`) that is public in the repo, and `app_rw` is the
non-`BYPASSRLS` role the request path authenticates as. Every deploy `ALTER ROLE`s
it to `APP_DB_PASSWORD`, which is generated and persisted on the server (§4a) —
so the public default never survives the first deploy.

---

## 6. Rollback

Images are tagged with the commit SHA, so any past build can be re-run:

```bash
ssh loltournament@rust.gameras.gr
cd /home/loltournament/lol/devel
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=<older-short-sha>/' .env
docker compose -f docker-compose.prod.yml up -d
```

Or re-run the `deploy:devel` job of an older pipeline from the GitLab UI.

Note this rolls back **code only** — a migration that already ran is not undone.

---

## 7. Troubleshooting

**`Permission denied (publickey)`** — the public half of `SSH_PRIVATE_KEY` is not
in the deploy user's `authorized_keys`, or `DEPLOY_USER` is wrong. Test:
`ssh -i <key> -p <port> loltournament@rust.gameras.gr`.

**Deploy hangs / `Connection refused` to the host** — with a docker executor,
`DEPLOY_HOST=127.0.0.1` addresses the job container. Use the real hostname/IP.

**`/healthz` times out at the end of a first deploy** — usually Caddy still doing
the ACME handshake, or DNS not pointing here yet. Check:
`docker logs lol-caddy --tail 50`.

**Caddy will not start** — the `import /etc/caddy/sites/*.caddy` glob matched
nothing (the deploy always writes the site file first, so this only happens if
`sites/` was cleared), or something else already holds port 80/443:
`ss -ltnp | grep -E ':(80|443)'`.

**`docker compose up` cannot pull** — the deploy token expired or lacks
`read_registry`. Re-test on the server: `docker login registry.gitlab.com`.

**Bot container missing** — expected when `DISCORD_BOT_TOKEN` is unset; it is
gated behind the `bot` compose profile.

**Zero rows from every guild-scoped query** — `APP_DATABASE_URL` is not being
used, or the `app_rw` password is out of sync with `APP_DB_PASSWORD`. RLS fails
closed by design.

---

## 8. Known follow-ups this deployment creates

1. **Rate limiting behind a reverse proxy.** `app/ratelimit.py` keys on the
   socket peer and deliberately does not trust `X-Forwarded-For`. Behind Caddy
   every request arrives from Caddy's IP, so all clients share one bucket and the
   verify/pair/refresh limiters over-throttle. It fails *closed*, not open, but
   the fix its docstring calls for — a proxy-set header validated against a known
   proxy allowlist — is worth doing before real traffic.
2. **The desktop client still defaults to `http://localhost:8000`.** Ship builds
   with `BACKEND_URL=https://lol.gameras.gr` and
   `REALTIME_URL=wss://lol.gameras.gr/api/v1/realtime/ws`.
3. **The Postgres integration tests are not idempotent.** `test_rls_isolation.py`
   and `test_integration_pg.py` insert fixed `discord_guild_id` values and never
   clean up, so a second run against the same database fails on
   `uq_guilds_discord_guild_id`. Harmless in CI (each run gets a fresh Postgres
   service) but confusing locally — drop and re-migrate between runs.
4. **No automated backups.** `data/postgres` is a plain directory and `backups/`
   is mounted into the Postgres container, but nothing schedules a `pg_dump`.
