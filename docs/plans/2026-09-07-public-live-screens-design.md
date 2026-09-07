# Public live screens — design

**Date:** 2026-09-07
**Status:** approved, not yet built

Fourteen read-only screens, published live and unauthenticated at
`live.neuralstrike.co`, reached from a thumbnail grid that fills the
`deploy/site/live.html` placeholder the repo has been holding for them.

## What this is, and what it replaces

`deploy/site/live.html` shipped as a deliberate placeholder with a "Live view
slot" and a comment saying what may mount in it. That comment ruled out an
`<iframe>` onto the app, and it was right to: the public site is a **static file
server** whose three constraints — nothing dynamic, no link to the app, no
third-party origin — are pinned by `deploy/tests/test_site.py`.

This design does not relax those constraints. It **adds a fourth origin**
instead. The static site stays static and keeps every test it has; the live
screens are served by a *second NiceGUI process* that is a peer of the app, not
a window onto it.

```
             neuralstrike.co  ──── Caddy file_server ──→ deploy/site/
             (static, unchanged)      live.html = the thumbnail grid
                                            │ links out
                                            ▼
          live.neuralstrike.co ──── Caddy reverse_proxy ──→ 127.0.0.1:8501
             (public, NO login)                             webgui/live_main.py
                                                                   │
           app.neuralstrike.co ──── Caddy reverse_proxy ──→ 127.0.0.1:8500
             (login + TOTP, unchanged)                      webgui/main.py
                                                                   │
                                                                   ▼
                                                             Redis (Tier 3)
```

Both webgui processes are Tier-1 readers of one Redis cache. The 3-tier
architecture already makes that free — Tier 1 imports no engines, makes no
Schwab calls, and holds no credentials — so a second reader costs a process and
nothing else.

## Why a separate process, and not `/live/*` on the app

Path-gating the running webgui was considered and rejected. It would put the
public internet and `/terminate` (Stop All Services) in **one process behind one
path filter**, and would make a public traffic spike a trading-UI outage. The
existing design already chose separate origins for the app and the site; this
follows it.

Purpose-built read-only re-implementations of the fourteen screens were also
rejected. `webgui/wall.py` states the argument in full: a second renderer that
re-derives so much as a rounding rule becomes "a screen quietly disagreeing with
the one it mirrors, which is the shape of a bug this repo already carries one
documented instance of." The live screens therefore **import the real page
modules**.

## ⚠ The trap that shapes the whole build: `import main`

`webgui/pages/options/gamma.py` does `import main as _shell`, for three
functions — `subtab_slot`, `bind_breadcrumb_leaf`, `_view_name`.

In a second process, `import main` executes `main.py`'s module body, and that
body registers **every** `@_page` route. A live process that imports `main`
therefore publishes `/terminate`, `/settings`, `/driver` and the paper book to
the internet, silently, while looking correct.

So those three functions move to a new leaf module **`webgui/shell.py`**, which
both entrypoints provide. This is what makes the public process *structurally
incapable* of holding the app's route table, rather than incapable by
inspection. A source-level test asserts `live_main` never imports `main`, in the
same shape as the existing Tier-1 allow-list guard.

## The fourteen screens

Pinned state is passed as an **optional keyword on the real `render()`**. The
precedent already exists: `sentiment_momentum.render(level="industry")`. The app
keeps calling `render()` bare, so the private screens are unchanged, and because
there is exactly one implementation the public screen cannot drift.

| # | Screen | Route | Renders | Pinned |
|---|---|---|---|---|
| 1 | The Desk | `/desk` | `desk.render()` | — |
| 2 | Opportunity Board | `/opportunity` | `matrix.render()` | — |
| 3 | Flow Alerts | `/flow` | `flow.render()` | — |
| 4 | Macro Board | `/macro` | `market.render()` | `macro_skin="B"` (Heat Lattice) |
| 5 | Sentiment | `/sentiment` | `sentiment.render()` | — |
| 6 | Bull / Bear Map | `/bullbear` | `sentiment_bullbear.render()` | — |
| 7 | Sector & Industry | `/sectors` | `sentiment_sectors.render()` | collapsed |
| 8 | Sector Rotation | `/rotation` | `sentiment_rotation.render()` | — |
| 9 | RRG | `/rrg` | `sentiment_rrg.render()` | — |
| 10 | Momentum | `/momentum` | `sentiment_momentum.render(level="industry")` | Industries |
| 11 | Gamma | `/gamma` | `gamma.render(symbol="$SPX", view="GEX")` | SPX only |
| 12 | Net Prem | `/net-premium` | `gamma.render(view="Net Prem")` | group `indices`, symbols `SPY QQQ BIG10`, mode `dollars` |
| 13 | Premium Divergence | `/premium-divergence/spy` | `gamma.render(symbol="SPY", view="Flow")` | SPY |
| 14 | Premium Divergence | `/premium-divergence/qqq` | `gamma.render(symbol="QQQ", view="Flow")` | QQQ |

Four of the fourteen are views of `/options/gamma`; `BIG10` is a symbol inside
the `indices` group in `config/symbols.toml`, not a group of its own.

## Read only, enforced at four layers

"Read only" is a property to be enforced, not a label. On a public origin the
stakes are concrete: `bus_client.request` reaches `gamma_analyze` and
`gamma_explain`, which are **paid Claude calls**, and `gamma_refresh` plus
sentiment `refresh`, which fan out **Schwab fetches against a budget already
running 68–76k/day**. Unauthenticated and unrefused, that is an open tap on
money.

1. **Redis ACL.** The live process connects as a Redis user granted read
   commands only. `Bus.__init__` already accepts a `url`, so this is a
   credential passed in the environment, not a redesign. This is the structural
   control: it is the difference between "the public process does not write" and
   "the public process cannot".
2. **`bus_client.request()` refuses** in the live entrypoint, installed before
   any page is imported. One chokepoint covers every command on every page.
3. **`app_settings` freezes** to the fixed overlay above: `set()` is a no-op,
   `get()` returns the pins. ⚠ This is not only about pinning defaults.
   `settings.json` is a **single-user store whose in-memory cache assumes one
   writer in one process**; unfrozen, the live process would read your live
   preferences (changing your own Macro Board skin would re-skin the public
   site) and race you for the file.
4. **The live shell renders no rail, no Settings, no Terminate, no Sign-out** —
   it never imports `main`, so those routes do not exist in the process.

Per-visitor view state that writes nothing — expanding a sector, sorting a
board, hovering a chart — stays interactive. It costs a Redis read at most and
is what makes the screens worth looking at.

## Exposure: an accepted decision, recorded

The published screens are **unredacted**. `/desk` renders merged paper and
driver positions with rescue flags; `/opportunity` ranks actionable signals;
`/flow` carries live alerts. Anyone may read the book and mirror the entries in
real time.

This was chosen deliberately over redaction and over a 15-minute delay. The book
is **paper only**, and full transparency is the brand argument the site already
makes. Recorded here so that a later reader finds a decision rather than an
oversight.

## Thumbnails

`tools/capture_live_shots.py` drives headless Chrome — the same binary
resolution `tools/stream_wall.sh` already does — against `127.0.0.1:8501`,
writing `deploy/site/live/<slug>.webp` for each of the fourteen routes. No Xvfb:
`--headless --screenshot` renders offscreen. No `WALL_PATHS` exemption either —
the live process has no auth middleware, so loopback reaches it plainly.

A systemd timer fires it **every 15 minutes** inside a new
`[windows.live_capture]` in `config/sessions.toml`. That window is held
**separate from `[windows.collection]`** for the reason that file already gives
for `[windows.stream]`: widening collection must not silently extend a public
surface. Outside the window the script exits **0** — a holiday is a normal
outcome, and a non-zero exit would restart-storm into `StartLimitBurst`.

⚠ **The captures are gitignored, and that is load-bearing.**
`deploy/site/live/*.webp` is generated state inside the served root, the same
shape as `webgui/data/`. Committed, they would dirty prod's tree the moment the
timer first fires, and `tools/promote.sh` refuses a dirty tree. Gitignored files
do not dirty it, so promote stays clean and the captures regenerate on the box
that serves them. `test_every_internal_reference_resolves_to_a_file` gets an
explicit exemption for that directory, with a comment saying why.

**The grid carries no timestamp, deliberately.** Baking a freshness line into
static HTML at capture time would make a committed source file a build artifact.
The grid is *navigation*; the live page carries its own staleness, which these
pages already surface. A menu that claims to be fresh is worse than one that
does not.

## Failure modes

| Failure | Result |
|---|---|
| Live process down | Grid serves normally; tiles link to a 502. Static site unaffected. |
| Capture timer down | Tiles go stale. Live pages still current. |
| Redis down | Live pages take their existing "Waiting for … service" path. |
| First run, no captures | The fourteen `<img>` paths 404. Tiles ship with `alt` text and reserved dimensions, so the grid is usable text-and-boxes before the first capture. |
| Public traffic spike | Isolated to the live process, on its own port and origin. |

## Integration points

| File | Change |
|---|---|
| `config/ports.toml` | `nicegui_live = 8501` |
| `repo_paths.py` | `_derive_ports` offsets it (dev → 9501); exports `NICEGUI_LIVE_PORT`, `LIVE_HOST` |
| `webgui/shell.py` *(new)* | the three shell functions, out of `main.py` |
| `webgui/live_main.py` *(new)* | the live entrypoint: refusals, freeze, fourteen routes |
| `deploy/systemd/generate_units.py` | one line in `components()` → `trading-<env>-webgui_live`, plus the capture timer |
| `deploy/caddy/generate_caddyfile.py` | a third host block for `LIVE_HOST` |
| `deploy/site/live.html` | placeholder → the thumbnail grid |
| `config/sessions.toml` | `[windows.live_capture]` |

⚠ `ports.toml` warns that a top-level port is **not** offset unless
`_derive_ports` is taught about it, and that this is "a BUG for one it does
[start]" — dev would bind prod's port and the collision would be invisible in
the config. `nicegui_live` therefore goes through the same `+ off` as `nicegui`.

## Tests that would actually catch a regression

- **The route-set guard, the critical one.** The live app registers exactly the
  fourteen routes and *not* `/terminate`, `/settings`, `/driver`,
  `/options/paper` — asserted against the running app's route table.
- **`live_main` never imports `main`**, source-level, in the shape of the
  existing Tier-1 allow-list test.
- **`bus_client.request` refuses**, driven **from the live app**, not by calling
  the refusal directly. ⚠ A consumer-side assertion that is never driven from
  the producer proves nothing — the `signal_band` incident in `CLAUDE.md` is the
  standing example, where a correct test passed for weeks against a payload the
  service never wrote.
- **`app_settings` frozen**: `set()` writes nothing to disk; `get()` returns the
  pins.
- **`deploy/tests/test_site.py`**: `live.html` references all fourteen tiles,
  each declaring its size; `LIVE_HOST` joins `ALLOWED_OUTBOUND`; and —
  unchanged — no page names `APP_HOST`, loopback, or the app port.
- **`tests/test_env_profile.py`**: `nicegui_live` offsets to 9501 in dev.
- **`tests/test_systemd_units.py`**: the unit exists and its component name
  equals what `status.restart_spec` emits — the seam that otherwise surfaces
  only as a Restart button that errors.
- **`deploy/caddy/tests/test_caddyfile.py`**: the live block exists; the app
  host is still not advertised by the public blocks.

## Deliberately not built

- **A delayed feed.** Considered for exposure control; the unredacted decision
  above removes the need, and a delay buffer would be the most complex piece in
  the design.
- **Live iframes as thumbnails.** One visitor opening the grid would spin up
  fourteen page instances with fourteen websockets, multiplied by every
  concurrent visitor.
- **A timestamp on the grid.** See above.
- **Rate limiting.** Not designed in. If the public origin proves to attract
  load, it belongs at Caddy, not in the app.
