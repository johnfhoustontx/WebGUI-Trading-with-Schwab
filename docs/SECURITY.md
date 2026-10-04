# Security hardening — proxy CORS / auth + Redis password

This is a single-user, localhost tool, but the **schwab-proxy holds live Schwab OAuth
tokens and can place orders**, so it is worth closing the one real remote-risk path. The
hardening below is **all backward-compatible and opt-in**: with nothing configured the
stack behaves exactly as before. Turn each on when you want it.

## 1. CORS allowlist (ON by default now)

Previously the proxy sent `Access-Control-Allow-Origin: *`, so any website open in your
browser could issue requests to the proxy on `http://127.0.0.1:8100`. The proxy now
defaults its CORS allowlist to the **local webgui + proxy origins** only:

```
http://127.0.0.1:8500, http://localhost:8500, http://127.0.0.1:8100, http://localhost:8100
```

Nothing to do — this is the new default. To change it, set `PROXY_CORS_ORIGINS` (comma-
separated) before starting the proxy. Setting it to `*` explicitly restores the old
wildcard (logged as a warning) if some other browser-based tool needs it.

## 2. Shared secret on the account routes (REQUIRED — they fail closed)

The routes that read the brokerage account — `/accounts`, `/positions`,
`/positions/{hash}`, `/transactions/{hash}` — require an `X-Proxy-Secret` header.
**With no secret configured they refuse every caller** (HTTP 503 naming
`PROXY_SHARED_SECRET`), and the Portfolio page shows that reason. Until 2026-10-03
the check was skipped when no secret was set, which is how production ran: any
caller that could reach the port, loopback or tailnet, could read the account.

**There is no order route.** `POST /orders/{hash}` forwarded a real order to
Schwab and nothing called it; it was deleted, and `trader_request` refuses any
method but GET. This application is paper-only.

To set the secret:

1. Generate a random value and put it in **either**:
   - the stack's `.env` as `PROXY_SHARED_SECRET=<value>` (every unit loads it), **or**
   - a gitignored file `shared/proxy_secret.txt` (one line).
2. Restart the stack. `GET /health` on the proxy then reports
   `"account_routes": "secret_required"` (it reads `locked_no_secret` without one).
3. The repo's clients resolve the **same** source and attach the header:
   `SchwabProxyClient` / `SchwabPyProxyClient`, the Portfolio data client and the
   Deep Dive report's client. Any *other* tool that calls an account route must
   send the header or it gets `401`.

The compare is timing-safe (`hmac.compare_digest`).

`/passthrough` forwards only five named market-data endpoints
(`/expirationchain`, `/quotes`, `/instruments`, `/pricehistory`, `/chains`),
matched exactly, and carries the secret check when a secret is configured. The
other market-data routes are unguarded (read-only, not account data).

## 3. Redis (Redis) password (OFF by default)

The Bus reads `MEMURAI_PASSWORD` (the env var kept its name). Prod sets it. To require a password:

1. Set `requirepass <password>` in the Redis/Redis config and restart the service.
2. Set the env var `MEMURAI_PASSWORD=<password>` for **every** process that starts a Bus
   (proxy is unaffected; the six services, the webgui and the public `webgui_live`
   process all use the Bus — the last of those connects as its own read-only Redis
   ACL user via `REDIS_LIVE_URL`, which carries its own credential). On Linux that
   means the checkout's `.env`, which every unit loads via `EnvironmentFile=`.

   ⚠ **`webgui_live` is the exception and loads `.env.live` instead**, a file of
   its own holding only `REDIS_LIVE_URL` and `MEMURAI_PASSWORD`. It is the one
   internet-facing, unauthenticated process in the fleet, and `.env` carries
   `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `PROXY_SHARED_SECRET`,
   `SMS_SMTP_APP_PASSWORD`, `DISCORD_WEBHOOK_URL` and
   `GAMMA_BRIEFING_WEBHOOK_URL`. No code path in that process reads any of them
   today; the split bounds what an RCE in NiceGUI would reach. Setup is
   `docs/dev-prod-environments.md` §2 step 4b; the generator is
   `deploy/systemd/generate_units.py:_live_env_file`.

   ⚠ **Unset or empty, `REDIS_LIVE_URL` falls back to the stack's ordinary full
   read/write credential.** `live_main.require_acl_url` refuses to serve prod in
   that state rather than starting a public process holding it; dev warns and
   continues, since dev's live origin is not fronted by the edge.
3. Restart the stack. Unset → `password=None` → no AUTH, exactly as before.

## What is intentionally NOT done

- **TLS between tiers** — everything is loopback; the auth gap above was the real issue.
- **Secrets-at-rest encryption** — `tokens.json` / `appsettings.json` / `anthropic_key.txt`
  remain plaintext under default user ACLs (inherent to a local token cache). Tighten the
  file ACLs to your user if you want; DPAPI encryption is a possible future step.
- **API versioning / gating `/docs`** — low priority for a personal loopback app.

See the best-practices audit (`docs/audits/2026-07-02-best-practices-validation.md`, items
11–12) for the fuller threat-model discussion.
