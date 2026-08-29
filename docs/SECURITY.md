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

## 2. Shared secret on the trading endpoints (OFF by default)

The sensitive endpoints — `/accounts`, `/orders/{hash}`, `/positions`,
`/positions/{hash}`, `/transactions/{hash}` — can require an `X-Proxy-Secret` header.
**Enforced only when a secret is configured**; unset → no check (unchanged).

To enable:

1. Pick a random secret and put it in **either**:
   - the env var `PROXY_SHARED_SECRET`, **or**
   - a gitignored file `shared/proxy_secret.txt` (one line).
2. Restart the proxy. It logs `auth ENABLED` on startup.
3. The repo's own clients (`SchwabProxyClient` / `SchwabPyProxyClient`) resolve the **same**
   source and attach the header automatically, so the services keep working. Any *other*
   tool that calls the proxy must send the same header or it gets `401`.

The compare is timing-safe (`hmac.compare_digest`). Market-data endpoints are unguarded
(they're read-only and non-sensitive).

## 3. Redis (Redis) password (OFF by default)

The Bus reads `MEMURAI_PASSWORD` (the env var kept its name). Prod sets it. To require a password:

1. Set `requirepass <password>` in the Redis/Redis config and restart the service.
2. Set the env var `MEMURAI_PASSWORD=<password>` for **every** process that starts a Bus
   (proxy is unaffected; the six services + the webgui all use the Bus). The simplest way
   is to set it machine-wide (`setx MEMURAI_PASSWORD ...`) or export it in the launcher.
3. Restart the stack. Unset → `password=None` → no AUTH, exactly as before.

## What is intentionally NOT done

- **TLS between tiers** — everything is loopback; the auth gap above was the real issue.
- **Secrets-at-rest encryption** — `tokens.json` / `appsettings.json` / `anthropic_key.txt`
  remain plaintext under default user ACLs (inherent to a local token cache). Tighten the
  file ACLs to your user if you want; DPAPI encryption is a possible future step.
- **API versioning / gating `/docs`** — low priority for a personal loopback app.

See the best-practices audit (`docs/audits/2026-07-02-best-practices-validation.md`, items
11–12) for the fuller threat-model discussion.
