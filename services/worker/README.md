# lol-worker

Background **arq** worker for AgonCup (PLAN.md §1.1 / §3 / §7).

Owns the retriable / scheduled work that must not run on the web event loop:

- **`lobby_watchdog`** — per-match host-heartbeat + **failover state machine**
  (PLAN.md §3.6.1). *Phase-0 stub.*
- **`score_corroboration`** — quorum + anomaly scoring over the 10 client result
  reports, delegating the verdict to `lol_core.adjudication` (PLAN.md §3.6.6).
  *Phase-0 stub.*
- **`account_verification_poll`** — Account-V1 nonce / profile-icon ownership
  poll (PLAN.md §3.1). *Phase-0 stub.*
- **`ping`** — real liveness job used by tests.

## Run

```bash
arq app.main.WorkerSettings        # needs REDIS_URL reachable
```

In the stack it runs as the `worker` service (`make up`). Trust note: every
result report this tier scores is unsigned and forgeable — corroboration +
anomaly scoring + admin override is the model; no single source is authoritative
(PLAN.md §9).
