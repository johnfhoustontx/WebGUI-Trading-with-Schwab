# Security

This is a **single-user, localhost, Windows** personal trading tool. Its security model
is calibrated accordingly — but because it holds Schwab OAuth tokens and can place
(real) orders through the proxy, a few things matter.

## Threat model

The realistic adversaries are:
1. **Other local processes / software** running as your user (can read plaintext token files).
2. **Malicious websites** open in your browser (can reach `http://127.0.0.1:*` cross-origin).
3. **Accidental exposure** — a bind widened to `0.0.0.0` or a forwarded port putting a
   service on the LAN.

All eight servers bind **`127.0.0.1`** (verified) — the loopback bind is the primary control.

## Secret handling

- Real secrets live in `shared/` and are **all gitignored** (`appsettings.json`, `tokens.json`,
  `anthropic_key.txt`, `driver_model.txt`, `sentiment_bridge.json`, `schwab-proxy/proxy_tokens.json`).
  Only `*.example.*` templates are committed. Verified: no real secret has ever been committed.
- Secrets are **plaintext on disk** with default user ACLs — any process in your session can
  read them (inherent to a local token cache). Consider tightening the token-file ACL to your
  user, or DPAPI-encrypting it. Rotate the Schwab tokens and `ANTHROPIC_API_KEY` if a machine
  is ever shared or compromised.
- Never paste real keys/tokens/account numbers into issues, logs, or commits.

## Known gaps (see docs/audits/2026-07-01-technical-audit.md — Security pillar, still open)

- **schwab-proxy CORS is `allow_origins=["*"]` with no auth on the trading endpoints**
  (`/orders`, `/accounts`, `/positions`). A malicious page in your browser could read account
  data or POST a real order. **Mitigation (planned):** restrict CORS to the webgui origin and
  require a shared-secret header on the proxy trading endpoints.
- **Memurai/Redis has no password** — any local process can read the cache or inject commands
  on `cmd:*`. **Mitigation:** set a Memurai `requirepass`.
- **Dependencies:** pin via `requirements.lock`; `pip-audit` runs in CI (currently non-blocking).
  **The CVE baseline was cleared on 2026-08-19** — it stood at **31 advisories across four
  packages** (pillow 13, setuptools 4, aiohttp 3+1, cryptography 1) and now reports none.
  `setuptools` is **pinned** as of that date; while it was unpinned the lockfile said nothing
  about it, so the audited version differed between a developer machine and the CI runner —
  which is how a four-CVE package sat unnoticed in a lockfile whose purpose is reproducibility.
  **Pin anything the audit can see, not just what the app imports.**

## The autonomous driver

The Claude decision layer is **paper-only** (`config.PAPER_TRADE = True`, a module constant not
settable over HTTP) and never sizes its own risk — `services/driver_svc/guardrails.py` is the
code-authoritative safety core (defined-risk allowlist, quantity clamp, halt states). The model
can only pick from menu ids the scanner already produced.

## Reporting

This is a personal repository — there is no external disclosure process. If you are reviewing it
and find an issue, contact the owner directly.
