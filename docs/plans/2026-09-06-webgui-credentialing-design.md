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
Internet ──443──► Caddy (system unit)
                    │
                    ├── neuralstrike.co, www.neuralstrike.co
                    │     └─ file_server → deploy/site/   PUBLIC, no login
                    │        (YouTube live link, Discord, Telegram)
                    │
                    └── app.neuralstrike.co
                          ├─ /wall*          → 404, never leaves the box
                          ├─ /login          → rate-limited (own zone)
                          └─ everything else → 127.0.0.1:8500 + header_up X-Edge
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

## Two origins, and why not two paths

`neuralstrike.co` carries a public one-page site — the YouTube live link, Discord
and Telegram. `app.neuralstrike.co` carries the trading GUI behind the login. They
are **separate Caddy site blocks on separate hostnames**, not two paths on one.

A path split (`/` public, `/app/*` gated) was rejected on mechanics first: NiceGUI
emits absolute `/_nicegui/...` URLs, so serving it under a prefix means rewriting
them, and that is a fragile thing to put between you and your login page.

**But the real reason is origin isolation, and it is the load-bearing one.** The
public page carries **third-party embeds** — a YouTube iframe, likely a Discord
widget. Those are other people's scripts and frames. Sharing an origin with the
app would mean an XSS or a compromised widget on the marketing page is an XSS *in
the app origin*, with the session cookie in reach. Separate hostnames make that
structurally impossible instead of something to keep getting right.

Three rules follow, and each is one attribute or header away from being wrong:

- **Cookies are host-only. Never `Domain=.neuralstrike.co`.** A `Domain=` cookie
  goes to the parent domain *and every subdomain, forever* — so the session would
  travel to the public page on every view, and to any subdomain added later. It is
  a one-word "convenience" that silently undoes the split above.
- **`Content-Security-Policy: frame-ancestors 'self'` on the app origin.** Blocks
  external framing while still permitting `/wall`'s three same-origin iframes.
- **Per-host certificates, not a wildcard.** A wildcard's private key covers every
  subdomain you will ever have. Rate-limit zones are per-host too, so marketing
  traffic can never trip the login limiter.

### The public site is a `file_server`, and its root is a trap

Caddy serves `deploy/site/` directly out of the checkout, so a `promote.sh` updates
the site. **The root must be exactly that directory and never the repo root** — a
`file_server` rooted one level too high serves `shared/webgui_auth.json`,
`shared/tokens.json` and `config/env.local.toml` to the internet. This is the
single most damaging mistake available in this design, it is one wrong path away,
and nothing about the site would look broken. Task 12 pins it with a test.

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
scope neither http nor websocket                  → pass (lifespan MUST pass, or the app never boots)
path ∈ /login, /favicon.ico                       → pass
no usable credentials                             → REFUSE, ahead of everything below
wall kiosk exemption (all three conditions below)  → pass
valid SESSION cookie                              → pass
otherwise → http: 303 /login?next=…   ·   websocket: close 1008
```

⚠ **The remember-device cookie authorises NOTHING at the gate.** An earlier draft
of this line read "valid session **or** valid remember-device cookie → pass",
and that is wrong. The remember cookie is a 30-day credential sitting on disk
whose entire intended power is letting the *next* sign-in skip the TOTP prompt —
which still demands the password. Honouring it at the gate would make a stolen
month-old cookie equivalent to a full session with **neither** factor, promoting
the weakest and longest-lived credential in the system into the strongest. That
is precisely the substitution the `kind` discriminator was added to prevent;
accepting it here re-opens the same hole from the other end.

⚠ **Default-deny sits ABOVE the kiosk branch, not merely above the cookie
check.** The tempting alternative — let the kiosk through when nothing is
configured, since its exemption never rested on a credential — makes "an
unconfigured app serves nothing" a rule with an exception, and an exception on a
security boundary is a thing to remember rather than a thing that holds. The cost
is that the wall is blank until setup, which is the correct thing for it to be.

**Pure ASGI, not `BaseHTTPMiddleware`.** The latter only sees `http` scopes, and
this is a websocket app. It also has known interactions with streaming responses.

### The login page carries no NiceGUI runtime, and that is what keeps the open list to two entries

`/login` is a **plain `HTMLResponse` form posting to a plain `@app.post("/login")`**
— not a `@ui.page`. It joins the nine raw routes `main.py` already serves, and the
repo already documents standalone HTML documents (the EOD reports, the wall, the
Explain infographics) as out of scope for the Tailwind-first rule.

That choice is load-bearing three times over, and each one is a trap avoided:

1. **A `@ui.page` login would force `/_nicegui_ws/` and `/_nicegui/{version}/*`
   open before authentication**, and `/_nicegui_ws/` carries socket.io's HTTP
   long-polling transport as well as the websocket, so it could not be gated on
   scope type either (verified against the installed NiceGUI 3.13.0 —
   `nicegui.py:54,65`). With a raw form, nothing needs them before login, so
   **both are gated** and the websocket is a real boundary rather than defence in
   depth.
2. **A NiceGUI form submits over the websocket, where you cannot set a cookie.**
   That single fact is why the standard NiceGUI auth pattern reaches for
   `app.storage.user` and its browser-id indirection at all. A plain POST sets the
   session cookie on an ordinary HTTP response and needs none of it.
3. **`ui.run()` sits inside `if __name__ in {"__main__", "__mp_main__"}`**
   (`main.py:2335`), so under pytest `storage_secret` is never applied and
   `SessionMiddleware` is never installed. Anything built on `app.storage.user`
   would be untestable without re-creating that wiring in a fixture — a fixture
   that would then be asserting against a setup prod does not use.

So the session is an `itsdangerous`-signed cookie this app sets and verifies
itself, `storage_secret` is not needed, and the entire gate is drivable by
Starlette's `TestClient`.

### The wall exemption requires two conditions, and that is the point

The kiosk Chrome that feeds the YouTube stream runs *on the box* and opens
`/wall`, whose three iframes are the real `/desk`, `/market` and
`/sentiment/momentum` pages. It is exempt when **all** of:

1. the peer address is loopback, **and**
2. the request carries no `X-Edge` header (Caddy sets it with `header_up`, which
   *replaces* any client-supplied value), **and**
3. the path is one of `/wall`, `/desk`, `/market`, `/sentiment/momentum`, or a
   NiceGUI runtime path (`/_nicegui/*`, `/_nicegui_ws/*`, `/static/*`) — the
   iframes are real NiceGUI pages and need their own assets and socket

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
| Session | `itsdangerous`-signed cookie set by this app — `Secure` + `HttpOnly` + `SameSite=Lax`, bounded `max_age`, and **host-only (no `Domain=`)** |
| Remember device | Stateless `itsdangerous`-signed cookie carrying `{issued_at, epoch}`, same attributes |
| Form token | Signed, short-lived, issued by `GET /login` and required by the POST — see mitigation 5 |

Both cookies are signed with a `session_secret` generated into
`shared/webgui_auth.json`. Neither NiceGUI's `storage_secret` nor
`app.storage.user` is used — see [the login page](#the-login-page-carries-no-nicegui-runtime-and-that-is-what-keeps-the-open-list-to-two-entries)
for why that is the simplification and not a shortcut.

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

⚠ **Each token carries a `kind`, and the two are not interchangeable.** The first
implementation put only `{epoch, issued_at}` in the payload, which made the
session and remember tokens **byte-identical** — the sole difference being which
`max_age_sec` the verifier happened to pass. Measured: a remember-device token
was accepted in the session slot and vice versa. That silently promotes the
weaker, longer-lived, on-disk credential into the stronger one: a stolen 30-day
remember cookie replayed as a session cookie is a **full authenticated session,
no password and no TOTP** — where its intended power was only "skip the TOTP
prompt at next login". `kind` is keyword-only with **no default** on both mint and
verify, because a default is exactly how a future call site would silently
re-open it.

⚠ **An unusable TOTP secret must REFUSE, not degrade — it is a fail-OPEN
otherwise.** `base64.b32decode("")` succeeds and yields an empty HMAC key, so
`pyotp.TOTP("")` derives a valid-looking code from the clock alone. Measured:
`verify_totp("", "489721", ...)` returned **accepted**. An empty secret therefore
does not *disable* the second factor — it replaces it with a sequence anyone can
compute. `_usable_secret` refuses anything empty, shorter than
`MIN_TOTP_SECRET_LEN` (16 chars / 80 bits), or not base32, and logs a WARNING
naming the cause; this also converts the corrupt-file `binascii.Error` into a
refusal rather than a traceback on a public page.

**Failure handling.** One generic "Sign-in failed" that never distinguishes a bad
password from a bad code. Per-IP *and* global failed-attempt backoff, held in
memory — it resets on restart, which is acceptable, and it keeps a disk write off
the authentication path. Every failure to `logs/webgui.log`.

⚠ **The global counter's penalty is 60 s, not the per-client 900 s, and the
asymmetry is the whole point.** This is a single-user app: a global lock that
lasts a quarter of an hour hands any bored stranger a trivial denial of service
against the owner — spray 50 failures at `/login` and the one person who matters
is locked out of the UI that arms the trading driver and stops the stack. Since
the domain is advertised in a Discord and a Telegram and is probed within an hour
of its certificate hitting CT logs, 50 failures in 15 minutes is a Tuesday, not
an attack.

Removing the global counter is equally wrong: its job is **resource** protection,
not brute-force prevention. Measured, a 60 s penalty holds a sustained flood to
50 attempts per minute — **0.83 Argon2/s**, well under one core-second per second
even at 3× the local 24 ms — which is the entire benefit, while making the
owner-facing failure self-healing in a minute.

**It does not eliminate the owner-DoS, it bounds the recovery.** While a flood is
actually in progress the owner is still refused, because each new failure re-arms
the window. The durable fix is to let the global lock refuse only the *expensive*
path — a caller presenting a valid session or remember-device token costs nothing
to check and is self-evidently not the flood — and that belongs with the route
wiring, not with the counter.

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

**This is not hypothetical, and the public site makes it less so.** Every
Let's Encrypt certificate is published to Certificate Transparency logs, and bots
watch that firehose — so `app.neuralstrike.co` is probed within an hour of
issuance whether or not anyone is told it exists. On top of that background,
`neuralstrike.co` is going to be **advertised in a Discord, a Telegram and a
YouTube description**. That is no longer passive scanning; it is people and bots
deliberately enumerating what else lives on the domain.

Expect a permanent background of `/wp-login.php`, `/.env` and `/admin` requests
from day one, and more of it than a private hostname would draw.

A related judgement, recorded because it looks like a security decision and is
not: **the public page carries no link to the app.** Obscurity is not a control
and the subdomain is in CT logs regardless — this is purely about how much junk
reaches the login form, and you will be bookmarking it anyway.

### Five mitigations, all cheap

1. **Rate-limit `/login` at Caddy**, so a flood never reaches Python at all.
2. **Check the lockout counter *before* calling Argon2**, never after — otherwise
   the throttle sits behind the expensive thing it is throttling, which is no
   throttle at all.
3. **Tune Argon2 to ~19 MiB / time_cost 2** (OWASP's floor) rather than the 64 MiB
   default. Still far beyond what one strong password needs, and it cuts the
   amplification factor 3.4×.
4. **`MemoryMax=` on the webgui unit.** With no swap this is the difference
   between a bounded spike and the OOM killer choosing its own victim.
5. **A signed, short-lived form token**, issued by `GET /login` and required by
   `POST /login`, checked **before Argon2**.

   This is the mitigation the public site earns. The overwhelming majority of
   credential-stuffing bots POST blind at `/login` without ever fetching the
   form; every one of those is now rejected for the cost of an HMAC instead of
   19 MiB and ~100 ms. It is a third pre-hash rejection alongside the lockout
   counter and the edge limiter, and it closes login-CSRF as a side effect.

   **What it is not:** protection against a targeted attacker, who will simply
   fetch the form first. That case is the lockout counter's job. This filters
   volume, and volume is what the Discord and Telegram links will bring.

   ⚠ **The form token is a THIRD `kind`, never a reused session kind.** Minting
   it as `KIND_SESSION` would mean `GET /login` hands every anonymous visitor a
   valid session token — the login page issuing the credential it exists to
   withhold. Three kinds, all distinct, all keyword-only with no default.

### Watching whether the public site becomes a load problem

The one-pager is served from this box by choice — one config, one deploy path,
and a static page is genuinely cheap. The exposure is that a widely-shared link
lands on the same four cores encoding the stream, where TLS handshakes cost more
than the bytes do. The number to watch is `sar -u` during stream hours against
the ~55% baseline in the table above; moving the apex block to a static host is
a config change, not a redesign.

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
| `webgui/login_page.py` | The `/login` GET form and POST handler — raw `HTMLResponse`, no NiceGUI runtime. Styled to match the dark-navy palette by hand, as the other standalone documents are. |
| `tools/webgui_credentials.py` | CLI: `set-password`, `enroll-totp` (prints the `otpauth://` URI and a terminal QR), `revoke-devices`, `show`. |
| `deploy/caddy/generate_caddyfile.py` | Generates `/etc/caddy/Caddyfile` from `repo_paths` — both site blocks. |
| `deploy/site/` | The public one-pager. **The `file_server` root, and nothing above it.** |
| `shared/webgui_auth.json` + `.example.json` | Gitignored, mode 600, beside `appsettings.json` / `tokens.json`. |

**Modified:** `webgui/main.py` (register the middleware at **module scope**, not
inside the `__main__` guard, so tests and prod share one wiring; mount `/login`
and `/logout`), `config/env.local.toml` (`public_host`), `requirements.txt`
**and `requirements.lock`**.

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
