# Security

This is a **single-user, localhost, Windows** personal trading tool. Its security model
is calibrated accordingly — but because it holds Schwab OAuth tokens and can place
(real) orders through the proxy, a few things matter.

## Threat model

The realistic adversaries are:
1. **The public internet.** Since 2026-09-06 the web GUI is reachable at
   `https://app.neuralstrike.co`. The hostname is probed within an hour of its
   certificate reaching Certificate Transparency logs, and `neuralstrike.co` is
   advertised in a Discord and a Telegram — so this is deliberate enumeration,
   not background noise.
2. **Other local processes / software** running as your user (can read plaintext token files).
3. **Malicious websites** open in your browser (can reach `http://127.0.0.1:*` cross-origin).
4. **Accidental exposure** — a bind widened to `0.0.0.0` or a forwarded port putting a
   service on the LAN.

All eight servers still bind **`127.0.0.1`**; Caddy is the only thing that talks to
`:8500`. **The loopback bind is no longer the sole control for the web GUI** — a
password + TOTP login is, and the two are layered deliberately.

## The web GUI login (2026-09-06)

What a session can do, which sets the bar: read the full paper book, open and
adjust paper positions, **arm the autonomous driver**, **stop the entire stack**
(`/terminate`), and spend the Claude API budget. Real orders stay out of reach —
`PAPER_TRADE` is a module constant not settable over HTTP.

- **Argon2id** password hashing at **19 MiB / t=2** — deliberately *below*
  argon2-cffi's 64 MiB default. On a public endpoint the default is a memory
  amplifier, and this box has 4 cores, **no swap**, and a live video encoder using
  ~2.2 of them during market hours.
- **TOTP** with a ±1 window drift tolerance and **replay refusal**: the accepted
  counter is persisted, and `counter <= last_counter` is rejected. `pyotp.verify`
  alone would accept the same code repeatedly for up to 90 seconds.
- **Default-deny at the gate**, above the wall exemption. An unconfigured or
  corrupt credentials file refuses everything — including the wall — rather than
  reading "no password set" as "nothing to check".
- **Three token kinds** (session / remember-device / login-form), none
  interchangeable. The remember-device cookie authorises **nothing** on its own;
  its only power is letting the next sign-in skip the TOTP prompt.
- **Throttled before the hash.** The lockout counter and the form token are both
  checked *before* Argon2 runs, so a blind POST costs an HMAC rather than 19 MiB.
- **Revocation is all-or-nothing:** `tools/webgui_credentials.py revoke-devices`
  bumps an epoch that invalidates every outstanding token. **There are no recovery
  codes** — a lost authenticator means SSH to the box and re-enrolling.

⚠ **`/wall` never leaves the box.** Caddy 404s it, and the app exempts it only
when the peer is loopback **and** the request carries no `X-Edge` header **and**
the path is one the wall actually frames. All three, so a widened bind cannot turn
the exemption into a bypass.

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

- **schwab-proxy CORS is `allow_origins=["*"]`.** The *trading* half of this finding is
  **already fixed and this file said otherwise for months**: `/accounts`, `/orders`,
  `/positions` and `/transactions` all carry `Depends(require_secret)` (verified
  2026-09-06). What remains open is the **market-data** reads and `/auth`, which are
  unauthenticated, so a malicious local page can still read quotes and chains through
  it. **Mitigation:** restrict CORS to the webgui origin. The proxy is not on the
  public domain — `tailscale serve` publishes it to the tailnet only — so this is a
  local-origin problem, not an internet-facing one.
- **Memurai/Redis has no password** — any local process can read the cache or inject commands
  on `cmd:*`. **Mitigation:** set a Memurai `requirepass`.
- **Dependencies:** pin via `requirements.lock`; `pip-audit` runs in CI (currently non-blocking).
  **The CVE baseline was cleared on 2026-08-19** — it stood at **31 advisories across four
  packages** (pillow 13, setuptools 4, aiohttp 3+1, cryptography 1) and now reports none.
  `setuptools` is **pinned** as of that date; while it was unpinned the lockfile said nothing
  about it, so the audited version differed between a developer machine and the CI runner —
  which is how a four-CVE package sat unnoticed in a lockfile whose purpose is reproducibility.
  **Pin anything the audit can see, not just what the app imports.** `pip` itself is pinned
  for the same reason and proved the point immediately: with the four packages fixed, dev
  audited clean while **prod still carried pip 24.0 with six advisories**, purely because the
  two venvs were created at different times and nothing pinned it. Audit **both** environments
  — a clean dev tells you nothing about prod.

## The autonomous driver

The Claude decision layer is **paper-only** (`config.PAPER_TRADE = True`, a module constant not
settable over HTTP) and never sizes its own risk — `services/driver_svc/guardrails.py` is the
code-authoritative safety core (defined-risk allowlist, quantity clamp, halt states). The model
can only pick from menu ids the scanner already produced.

## Reporting

This is a personal repository — there is no external disclosure process. If you are reviewing it
and find an issue, contact the owner directly.
