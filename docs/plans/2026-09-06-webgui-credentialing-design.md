# Credentialing the webgui for public access (2026-09-06)

Put the NiceGUI web GUI on a real hostname with TLS and a login, so it can be
reached from **any browser on any machine** — including one you cannot install
software on — while the box stays as closed as it is today.

**This is not a hardening pass on the existing setup.** Today's control is the
`127.0.0.1` bind plus an SSH key, and that control is sound; nothing about it is
broken. What it cannot do is let you in from a machine that has no key and no
tunnel. This design replaces *the reachability constraint* with an authentication
one, and the whole of the risk is in that trade: after this, the login is the
perimeter.

**It is also not a performance project**, but it does have a measured load
consequence, which is why [Load and exposure](#load-and-exposure) is a first-class
section rather than a footnote. The credentialing itself is free. The *exposure*
it brings is not.

## The organising idea

> One public hostname carrying an authenticated GUI. One private tailnet
> hostname carrying everything else. The wall stays on loopback and never learns
> what a session is.

Three destinations, three postures — and each one is the least-privileged option
that still does its job.

## What is behind the login

Worth stating plainly, because it sets the bar for everything below. A session on
this app can:

- read the full paper book, the driver's track record and every number on 34 pages
- open and adjust paper positions, and apply rescue adjustments
- **arm the autonomous driver**
- **stop the entire stack** (`/terminate`)
- spend the Claude API budget

Real orders stay out of reach — `PAPER_TRADE` is a module constant not settable
over HTTP, and the proxy's `/orders`, `/accounts`, `/positions` and
`/transactions` already carry `Depends(require_secret)`. Everything else on that
list is live.

Two consequences drive the design: **default-deny**, and **a test that fails when
route #44 is added without an auth decision**.

## Shape

```
Internet ──443──► Caddy (system unit)  trading.<yourdomain>
                    ├─ /wall*          → 404, never leaves the box
                    ├─ /login          → rate-limited
                    └─ everything else → 127.0.0.1:8500  + header_up X-Edge
                                              │
                                    webgui AuthMiddleware (pure ASGI)
                                    session valid? ──no──► 303 /login?next=…

Tailnet ──────────► tailscale serve ──► 127.0.0.1:8100  proxy /auth + /health
                                    └─► 127.0.0.1:9500  dev webgui

Loopback ─────────► kiosk Chrome ─────► 127.0.0.1:8500/wall
```

**The webgui keeps its `127.0.0.1` bind.** Caddy is the only thing that talks to
it, so the standing "never change either bind to `0.0.0.0`" rule survives intact
— this design does not weaken it, it puts a gate in front of it.

## Why Caddy and not a tunnel

Cloudflare Tunnel would be less work: no open ports, no cert to renew, and
Cloudflare Access would give stronger authentication than anything written here.
It is rejected on one ground, and it is not complexity.

**Cloudflare terminates TLS.** Every position, every dollar figure, every
account-derived number on those 34 pages would be readable in plaintext by a
third party, and the outage of that third party would be your lockout. With Caddy
the private key never leaves the VPS.

Tailscale Funnel was the alternative before a domain was in hand — it also
terminates TLS *on your own node*, so it shares Caddy's privacy property. With a
registered domain, Caddy wins on the hostname alone. **Tailscale stays regardless**,
for the proxy and as lockout insurance; see [Ops](#ops).

## The gate

One pure-ASGI middleware, in `webgui/auth_middleware.py`:

```
scope not http/websocket                                   → pass
path ∈ /login, /_nicegui/*, /_nicegui_ws/*, /favicon.ico    → pass
wall kiosk exemption (both conditions below)                → pass
valid session OR valid remember-device cookie               → pass
otherwise → http: 303 /login?next=…   ·   websocket: close 1008
```

**Pure ASGI, not `BaseHTTPMiddleware`.** The latter only sees `http` scopes, and
this is a websocket app. It also has known interactions with streaming responses.

### The open list is not a weakness, and it is worth being honest about why

`/_nicegui_ws/` and `/_nicegui/{version}/*` **must** be reachable before login, or
the login page cannot render or submit — and `/_nicegui_ws/` carries socket.io's
HTTP long-polling transport as well as the websocket, so it cannot be gated on
scope type either. Verified against the installed NiceGUI 3.13.0
(`nicegui.py:54,65`).

So **the websocket is not the security boundary. The page gate is.** That is
sound, but only for a specific reason worth writing down: NiceGUI client ids are
random, minted server-side at page render, and scoped to one page's element tree.
To open a socket that can do anything you need a client id, and you only get one
for `/desk` by rendering `/desk` — which the HTTP gate refuses. An unauthenticated
caller can obtain a client id for `/login` and nothing else.

The websocket branch in the gate is therefore **defence in depth, not the
control**. Do not let a later refactor treat it as the control.

### The wall exemption requires two conditions, and that is the point

The kiosk Chrome that feeds the YouTube stream runs *on the box* and opens
`/wall`, whose three iframes are the real `/desk`, `/market` and
`/sentiment/momentum` pages. It is exempt when **all** of:

1. the peer address is loopback, **and**
2. the request carries no `X-Edge` header (Caddy sets it with `header_up`, which
   *replaces* any client-supplied value), **and**
3. the path is one of `/wall`, `/desk`, `/market`, `/sentiment/momentum`

Either condition alone would be a bypass. Requiring both means that if the bind
is ever widened by accident, or a port gets forwarded, an external client's peer
address is its own IP — so it can never take that branch. Condition 2 covers the
converse: a request that *did* come through Caddy can never claim to be the kiosk.

**The alternative — giving the kiosk a long-lived session cookie — is rejected.**
The stream is unattended for nine hours on a public channel. The day that cookie
expires mid-session, the failure mode is *a login page on a public YouTube
broadcast*. Loopback is a property that cannot expire.

Accepted consequence: `/desk`, `/market` and `/sentiment/momentum` are readable
without a session **from the box itself**. Anyone with shell there can read the
token files anyway, so this grants nothing new.

## Credentials

| Piece | Choice |
|---|---|
| Password | Argon2id hash, in `shared/webgui_auth.json` |
| Second factor | TOTP (`pyotp`), ±1 step drift, last-accepted counter persisted so a code cannot be replayed inside its own window |
| Session | `app.storage.user` over Starlette `SessionMiddleware`, `https_only=True`, `same_site="lax"`, bounded `max_age` |
| Remember device | Stateless `itsdangerous`-signed cookie carrying `{issued_at, epoch}` |

`ui.run()` currently passes **no** `storage_secret`, which is why `app.storage.user`
is inert today; it gains one, plus `session_middleware_kwargs`.

**TOTP over passkeys, for one reason: reliability on a machine you do not own.**
WebAuthn is phishing-proof and genuinely stronger, and a public login page is
exactly what gets phished. But on a borrowed machine it needs the cross-device QR
flow, which depends on Bluetooth and sometimes simply does not work — and "any
machine" is the requirement this whole design exists to satisfy. A TOTP code works
in any browser ever made. Passkeys are **deferred, not precluded**; nothing here
blocks adding them later as a fast path for owned devices.

**Remember-device is stateless on purpose.** No server-side device registry to
maintain, expire or leak; "sign out everywhere" is `epoch += 1`. The trade-off,
stated plainly: **you can revoke all devices, not one.** For a single user with two
or three machines, re-trusting them beats maintaining a registry.

**Failure handling.** One generic "Sign-in failed" that never distinguishes a bad
password from a bad code. Per-IP *and* global failed-attempt backoff, held in
memory — it resets on restart, which is acceptable, and it keeps a disk write off
the authentication path. Every failure to `logs/webgui.log`.

## Load and exposure

Measured on the live box, not estimated.

**vps2**: 4 vCPU (Xeon E3-12xx v2, Ivy Bridge — has AES-NI), **7.9 GB RAM, no
swap**, 142 GB disk with 124 GB free. Ports 80 and 443 are both free.

| Day | CPU (4 cores) | RAM used | Net out |
|---|---|---|---|
| **Fri 2026-09-05** — stack only, stream off | **1.6%** (peak 3.6%) | 0.8 GB / 10% | 32 kB/s |
| **Thu 2026-09-04** — stream running 08:30–15:20 | **~55%** sustained | 2.3 GB / 29% | ~2 Mbps during the window |

The eight-unit trading stack costs **~0.07 of one core**. The wall stream —
Xvfb + kiosk Chrome + ffmpeg encoding 1080p — costs **~2.2 cores**. It is 97% of
the load on this box and everything else is rounding error. Largest single unit by
memory is `options_svc` at 331 MB; all eight total ~870 MB.

**Headroom during the worst hours: ~1.7 idle cores and ~5.2 GB available.**

### What this design adds in steady state

| Component | CPU | RAM |
|---|---|---|
| Caddy, idle + TLS for one user | <0.5% of a core | 20–40 MB |
| Auth middleware per request (HMAC cookie check) | microseconds | — |
| Each additional browser tab (phone + laptop) | ~0.02% of a core | negligible |
| Caddy access logs + certs | — | tens of MB/year |

TLS is free here: the CPU has AES-NI and the payload is a dashboard's KB/s of JSON
deltas. **Steady state goes from ~55% to ~56% and is not measurable in practice.**

### The one real risk: Argon2 on a public endpoint

Argon2 is *deliberately* expensive — that is the entire point of a password hash.
`argon2-cffi`'s defaults are time_cost 3, **memory_cost 64 MiB**, parallelism 4:
roughly 50–150 ms and 64 MB **per verification attempt**. On a public endpoint
that is also an amplifier. Ten concurrent POSTs to `/login` would consume ~640 MB
and all four cores.

During stream hours there are 1.7 cores free **and no swap**. So the visible
symptom would not be a slow login page — it would be **the public YouTube stream
dropping frames** because a bot found the login form. With no swap, a memory spike
does not degrade gracefully; it gets OOM-killed.

**This is not hypothetical, because of how the hostname gets found.** Every
Let's Encrypt certificate is published to Certificate Transparency logs, and bots
watch that firehose. The hostname will be probed **within an hour of issuance**,
whether or not anyone is told it exists. Expect a permanent background of
`/wp-login.php`, `/.env` and `/admin` requests from day one.

### Four mitigations, all cheap

1. **Rate-limit `/login` at Caddy**, so a flood never reaches Python at all.
2. **Check the lockout counter *before* calling Argon2**, never after — otherwise
   the throttle sits behind the expensive thing it is throttling, which is no
   throttle at all.
3. **Tune Argon2 to ~19 MiB / time_cost 2** (OWASP's floor) rather than the 64 MiB
   default. Still far beyond what one strong password needs, and it cuts the
   amplification factor 3.4×.
4. **`MemoryMax=` on the webgui unit.** With no swap this is the difference
   between a bounded spike and the OOM killer choosing its own victim.

### Contention policy

Under CPU contention systemd shares cores by `CPUWeight` (default 100 everywhere).
**Raise the stream unit's `CPUWeight`** so that under pressure the broadcast wins.

This is a preference, not a technical fact, and it is recorded here so a future
reader knows it was a choice: a stuttering public broadcast is the more costly and
more visible failure, and there is a tailnet fallback for operator access when the
GUI is sluggish. Reverse it if that judgement ever changes.

## Components

**New:**

| File | Holds |
|---|---|
| `webgui/auth.py` | Pure logic — verify password, verify TOTP with drift + replay window, mint/verify the remember-device token, lockout arithmetic. Pure functions, so they test without a browser or a server. |
| `webgui/auth_middleware.py` | The ASGI gate. |
| `webgui/pages/login.py` | `@ui.page("/login")`, in the existing dark-navy token vocabulary. |
| `tools/webgui_credentials.py` | CLI: `set-password`, `enroll-totp` (prints the `otpauth://` URI and a terminal QR), `revoke-devices`, `show`. |
| `deploy/caddy/generate_caddyfile.py` | Generates `/etc/caddy/Caddyfile` from `repo_paths`. |
| `shared/webgui_auth.json` + `.example.json` | Gitignored, mode 600, beside `appsettings.json` / `tokens.json`. |

**Modified:** `webgui/main.py` (`ui.run(storage_secret=…)`, register the
middleware, `/logout`), `config/env.local.toml` (`public_host`),
`requirements.txt` **and `requirements.lock`**.

### Three placement decisions, each following an existing rule

- **The Caddyfile is generated, not committed** — same reasoning as
  `deploy/systemd/generate_units.py`. A committed config would be a second copy of
  the ports and the checkout root, free to drift, and the drift would surface as a
  502 in prod rather than as a failing test.
- **The domain goes in `config/env.local.toml`, not a `config/*.toml`.** By this
  repo's own test, a config file earns its place by deduplicating a value across
  modules that cannot import each other; this has exactly one consumer. It *is*
  machine-local deployment identity, which is precisely what `env.local.toml`
  already holds and why it is gitignored — so `git pull` can never carry a hostname
  between checkouts.
- **`argon2-cffi` and `pyotp` are added to `requirements.lock` by hand**, not by
  `pip freeze`. The lock is what prod installs, and a wholesale refresh sweeps
  unrelated local state into the commit.

## Ops

**Caddy is a *system* unit, not a `--user` unit.** It needs :443, and it must not
die with a login session. This is the one deliberate exception to the
`--user`-only rule, and it is safe because Caddy is not the app: it holds no
credentials, reaches nothing but a loopback port, and cannot restart a sibling.

- `ufw allow 80,443/tcp`; DNS A record → the VPS.
- **`tailscale serve` publishes the proxy's `:8100`** (`/auth` + `/health`) on the
  tailnet. This is better than today: the weekly Schwab re-mint becomes a URL on
  your phone instead of an SSH tunnel, and it is **never public**. Dev's `:9500`
  stays tailnet-only for the same reason — it shares Redis with prod and runs the
  same command handlers.
- **Tailscale is the lockout insurance.** If the cert or Caddy breaks, the way in
  must not depend on the thing that broke.

**The proxy does not go on the domain.** Its market-data reads and `/auth` are
unauthenticated by design, and its CORS is `allow_origins=["*"]`.

### Documentation that this commit makes false, and therefore fixes

The repo's own standing warning is that docs rot silently because nothing fails
when they go stale. Two files become **wrong** the moment this ships:

- `tools/open_webgui.ps1` — its docstring says the app has "NO AUTHENTICATION OF
  ANY KIND" and that a tunnel is the only safe route.
- `SECURITY.md` — its threat model is built on the loopback bind being the primary
  control.

Both are corrected in the same commit. While there, `SECURITY.md`'s "known gaps"
claim that the proxy's trading endpoints are unauthenticated is **already stale** —
they carry `Depends(require_secret)` — and gets fixed too.

## Testing

- **Drive the real app object** through Starlette's `TestClient`: unauthenticated
  `/desk` → 303, `/login` → 200, post-login `/desk` → 200. A consumer-side guard
  proves nothing until a test drives it from the producer — this repo has paid for
  that lesson more than once.
- **The wall exemption's negative cases**, which are the ones that matter: loopback
  *with* `X-Edge` → denied; external *without* `X-Edge` → denied.
- **The coverage guard.** Enumerate every `app.routes` entry and every `@ui.page`
  route, and assert each is either in the explicit open-list or refuses an
  unauthenticated request. Same shape as `test_no_inline_style.py`: it fails when
  someone adds a route and does not think about auth. **This is the test that
  matters most**, because it is the only one that catches a mistake nobody made
  yet.
- TOTP drift and replay; lockout arithmetic, including that it is consulted before
  the hash; remember-cookie tamper, expiry, and epoch invalidation.

## Deliberately out of scope

Passkeys · per-device revocation · multi-user or roles · putting the proxy on the
public domain · changing either bind address.

## Open question for implementation

Whether "Sign out" gets a row in `SYSTEM_RAIL` (above the danger button) or lives
in Settings. The rail version touches
`test_nav_sections_partition_the_rail_with_nothing_lost_or_doubled`, which is the
guard that catches a regrouping dropping or doubling an item.
