# Compliance Checklist (Riot + Discord)

> Living checklist. Items gating **beta** are marked **[BETA-GATE]**. See PLAN.md
> §3.3, §3.5, §8, §9.

## Riot — API & policy
- [ ] Register a product on the Riot Developer Portal; obtain a **personal/standard
      Account-V1 key** (no Tournament API, no production-key review needed).
- [ ] Host **`riot.txt`** on the public domain; stand up the public marketing site.
- [ ] Publish a public **privacy policy** ([privacy-policy.md](privacy-policy.md)).
- [ ] Mandatory disclaimer: **"LeagueOfLeagues isn't endorsed by Riot Games…"** on
      all surfaces; use **approved assets only**.
- [ ] **Monetization:** free-only for v1; no gambling/wagering. (Entry-fee events,
      if ever: ≥20 participants, transparent win conditions, ≥70% prize-pool —
      out of scope for v1.)

## Riot — LCU automation (the headline risk)
- [ ] **[BETA-GATE] LCU-write ToS spike → Riot DevRel.** Confirm whether a
      distributed desktop client that, on community members' own clients,
      **auto-accepts invites** and **(host-side) auto-creates/starts** a custom
      lobby at tournament cadence — possibly while the player isn't clicking — is
      within ToS. Call out the three risk multipliers: **cross-account
      distribution** (9 guests, not self-use), **unattended action**, **action rate**.
- [ ] Register the LCU **write** endpoints (host + guest) on the Developer Portal.
- [ ] Ship the named write controls: per-session **default-OFF opt-in**, **presence
      gate**, **human-paced rate limits**, recorded **consent**, global **kill-switch**,
      single audited `lol-lcu` adapter.
- [ ] **Hard-exclude Korea** from LCU write automation.
- [ ] **No in-match gameplay automation, ever** (reads of `:2999` only in-game).
- [ ] Pin a validated client patch; CI-validate `eog-stats-block`/`eventdata`
      parsers against live fixtures + a Swagger mirror each patch.

## Desktop distribution — code signing
> Per-OS Electron packaging runs in `.github/workflows/desktop-build.yml`
> (macOS + Windows matrix). Unsigned **dev** builds run with no secrets; signed
> release builds require the certs below. PLAN.md §3.3, §9.
- [ ] **[BETA-GATE]** The **LCU-write ToS spike** (above) remains the gate before
      any signed desktop client ships — signing is necessary but not sufficient.
- [ ] **Apple Developer Program** membership → **Developer ID Application** cert
      for macOS signing + notarization. CI secrets: `APPLE_DEVELOPER_ID`,
      `MAC_CERT_P12_BASE64`, `MAC_CERT_PASSWORD`, `APPLE_ID`,
      `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`.
- [ ] **Windows code-signing cert** (Authenticode, EV recommended for SmartScreen
      reputation). CI secrets: `WINDOWS_CERT` (base64 `.pfx`),
      `WINDOWS_CERT_PASSWORD`.
- [ ] Until the certs exist, CI produces **unsigned dev installers** (signing
      steps are gated on the secrets being present, so the matrix still runs).

## Discord
- [ ] Create the application; bot uses the **non-privileged `guilds` intent** only.
- [ ] OAuth2 (auth-code + `state` for web; PKCE for desktop).
- [ ] Plan **app verification** (Stripe identity + public privacy policy), submit
      ~75 servers; `AutoShardedBot` from day one.
- [ ] `AllowedMentions` / markdown neutralization on the central feed.

## Data protection
- [ ] Encrypt OAuth tokens + verification state at rest.
- [ ] User/guild deletion + `on_guild_remove` purge; automated Postgres backups
      (RPO/RTO) once real data exists.
