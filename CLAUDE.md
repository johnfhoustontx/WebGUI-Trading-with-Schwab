# CLAUDE.md — WebGUI Trading with Schwab

Guidance for Claude Code sessions working in this repository. Read this first,
then the per-app `CLAUDE.md` for the folder you are editing.

> **Maintenance:** This document is the living architecture/tech record for the
> project and is **updated regularly** as the build progresses (an explicit
> standing requirement). After any structural change — new page, new dependency,
> port change, copied/removed module — update the relevant section here.

### Where a fact belongs (read before adding anything to this file)

This file is loaded **in full at the start of every session**, so its length is a
per-session cost paid by every future conversation. It holds the **durable** record only.
Six homes, and the test for each is what a future session needs to *act*:

| Write it in | When |
|---|---|
| **CLAUDE.md** (here) | A durable invariant, convention, standard, or gotcha — something still true next month that changes how you write code |
| **[docs/CHANGELOG.md](docs/CHANGELOG.md)** | Dated shipping narrative: what shipped, the pieces, commit SHAs, test counts at the time, live-verification logs |
| **[docs/webgui-routes.md](docs/webgui-routes.md)** | Per-page behaviour detail — what a specific route renders, its cache keys, its own quirks |
| **`docs/plans/<date>-<feature>-{design,plan}.md`** | The reasoning and step plan for a feature, written as you build it |
| **`docs/reference/<topic>.md`** | The DETAIL behind an invariant: the incident, the measurement, the worked numbers, the reasoning. This file keeps the rule in a line or two and links there |
| **[docs/manuals/](docs/manuals/README.md)** | Anything a **user** reads: the five built manuals. A user-visible behaviour change lands here too, not only in the CHANGELOG |

⚠ **The manuals rot silently, because nothing fails when they go stale.** A
2026-08-16 audit against the running stack found the User Guide still documenting
the order-approval queue removed in July, three of four cadences wrong in the
Technical Reference, and `driver_svc` commands in the API Reference that no longer
exist. If a change moves a cadence, renames a page, or removes a control, fix the
manual in the same commit — and remember `webgui/page_help.py` is a manual too.

**Three rules that keep it that way:**

1. **A shipped feature is not an entry here.** Add a design/plan pair and a CHANGELOG
   entry. Touch this file only if the feature changed an invariant — a new port, a new
   convention, a new trap. Prose beginning "**Feature X — DONE (date)**" belongs in the
   CHANGELOG, always.
2. **Correct in place; never append a correction.** When something is superseded, **edit
   the sentence**. Do not add "⚠ SUPERSEDED — the text below is wrong" and leave the wrong
   text underneath; that is how this file reached 285 KB, and it makes the file actively
   misleading rather than merely long.
3. **No test counts, no "verified live", no commit SHAs.** They are stale within a week.
   The one exception is the dated baselines in `docs/reference/testing.md`, which exist
   precisely to be compared against — and even there, compare the failing *set*, never
   the count.

**History:** the *Last updated / Prior —* chain moved to the CHANGELOG on 2026-08-07. On
**2026-08-16** the file was audited again and cut **285 KB → ~100 KB**: per-route detail to
`docs/webgui-routes.md`, and ~60 accumulated feature narratives to the CHANGELOG. Nothing
was deleted — it was relocated. The rules above exist because the 2026-08-07 split fixed the
symptom and the file refilled in nine days.

On **2026-10-04** it was cut a third time, **327 KB → under 100 KB** (audit CQ-09):
eight blocks of detail moved, verbatim, to `docs/reference/`, and each left its
rules behind here. `tests/test_claude_md_size.py` now fails when this file passes
its ceiling, so the next refill is caught by a test rather than by an audit.
**Before changing code in an area, open that area's reference file** — the rules
below say what not to break; the reference says why and how it was found.

## What this project is

A **self-contained NiceGUI web GUI** for the Schwab trading stack. It is a fork
of the active backend of the original `D:\Trading With Schwab` monorepo, with the
old per-app UIs (Dash, built React, Tk desktop) **replaced by a single NiceGUI
multi-page web app** (`webgui/`).

The original repo (`D:\Trading With Schwab`) is **reference only** — this project
does not import from it or depend on it at runtime.

## Tech stack

| Layer            | Technology                                                        |
|------------------|-------------------------------------------------------------------|
| Web GUI          | **NiceGUI** (`>=2.0`) — single multi-page app, Python-only        |
| Charts / gauges  | **Highcharts** via `nicegui[highcharts]` (`ui.highchart`) — all webgui charts + gauges |
| API gateway      | FastAPI + uvicorn (`schwab-proxy`)                                |
| Brokerage SDK    | `schwab-py` (`schwab` package) — auth, market data, streaming     |
| Data / numerics  | pandas, numpy, scipy                                              |
| Scheduling       | per-service asyncio loops (`services/*/scheduler.py`)            |
| Notifications    | Telegram · Discord · SMS-over-SMTP · X — all HTTP/SMTP, no OS hooks |
| Spreadsheet I/O  | openpyxl                                                          |
| Testing          | pytest                                                            |
| Runtime          | Python 3.11+, **Linux** (Ubuntu 24.04 LTS), systemd user units, single-user |

## Architecture

```
schwab-proxy (:8100)  ──HTTP──>  webgui NiceGUI app (:8500)
        │                              │
   owns Schwab auth/                   ├─ Options  page  → options-scanner engines
   tokens + market data               ├─ Sentiment page → sentiment-dashboard scoring
                                       ├─ Trade    page  → trade-analyzer src/analysis
                                       └─ Portfolio page → portfolio-analyzer src (live)
        │
   shared/analysis_lib  ← shared library (technical, sector_analysis, config)
```

**The proxy must be running first.** All feature backends resolve their Schwab
client and market data through `http://127.0.0.1:8100`.

## 3-tier architecture (approved 2026-06-15 — migration COMPLETE)

The monorepo was re-tiered (strangler-fig) into three **physically separate** tiers over a
**Redis backbone**. **All five domains are migrated** — sentiment, options,
portfolio, trade, market — and every page reads Redis (the autonomous driver,
once a sixth, was removed 2026-09-22). The sixth service today, **`news_svc`**
(2026-09-26), was born in the tiers rather than migrated: it polls free public
feeds and calls neither the proxy nor Claude, and reads one optional credential,
`FRED_API_KEY` (from the stack `.env`, never `.env.live` or config; without it the
calendar's values come from FRED's key-free CSV). The shape:

**The Tier-1 import allow-list, stated exactly** (audited 2026-08-21 across all
153 non-test `webgui/**/*.py`, extended 2026-08-21, and again 2026-08-25, and 2026-09-15):
`nicegui` · `shared.bus`
(never `redis` directly) · `shared.market_calendar` · `shared.symbols` (which
also holds the ONE ticker allow-list, `SYMBOL_RE` / `clean_symbol`: a
user-typed ticker becomes part of a Redis KEY NAME — `cache:options:dossier:<SYMBOL>`
— so the `/symbol` page and `options_svc` must refuse exactly the same strings,
and neither may grow a regex of its own) ·
`shared.calibration` (pure arithmetic — `import math` and nothing else; Tier 1
takes only `bucket_key` from it, so the DB's `scanner_type` '0DTE' and the
page's `trade_type` '0-DTE' cannot key differently — exactly the cross-tier
mirror `test_cross_tier_mirrors.py` exists to prevent) ·
`shared.book_caps` (since 2026-09-15; pure — `math` only; the Paper
dialog's preview (`pages/options/book_fit.py`) — and, through it, the
checklist's Paper book line (`pages/options/checks.py`, which also takes
`book_caps.describe` for the blocked wording) — evaluates the SAME rungs the
service enforces, so it imports the one cap module rather than a Tier-1 copy, and
`webgui/tests/test_book_caps_tier1.py` pins its exact import set) ·
`shared.config_toml` (since 2026-09-19; stdlib-only — `tomllib`/`os`/`json`/`re`
— used by `config_store.py` behind Settings → Configuration to read the shipped
files and write the operator's `config/local/` overrides) ·
`shared.public_scan` (since 2026-09-21; the public Strategy Finder's stream
name, request builder, result keys and config - `shared.symbols` +
`shared.config_toml` and nothing else, pinned by
`shared/tests/test_public_scan.py`) ·
`shared.public_rescue` (since 2026-09-21; the public Rescue form's stream,
request builders, field validators, result keys and config - the same import
set, pinned by `shared/tests/test_public_rescue.py`) ·
`shared.public_tools` (since 2026-09-21; the public Calculator and Simulator's
two streams, request builders, validators, result keys and config - pinned by
`shared/tests/test_public_tools.py`, and imported by `bus_client`, `live_main`
(the tab-storage age) and the pages `public_handoff`, `calc_live` and
`sim_live`) ·
`shared.public_gamma` (since 2026-09-22; the public Gamma page's
stream, request builder, status and dropdown-list keys and hot-set config -
`shared.symbols` + `shared.config_toml` only, pinned by
`shared/tests/test_public_gamma.py`) ·
`shared.x_text` (since 2026-09-22; X's weighted length, hashtags and
fitting a post into 280 - stdlib only, pinned by `shared/tests/test_x_text.py`,
so the `/x` page's live count is the service's own computation) ·
`shared.news_config` (since 2026-09-26; `config/news.toml`'s loader - stdlib +
`shared.config_toml` + `shared.symbols` + `repo_paths` only; Tier 1 reads just
the ticker set and `[trending] window_h` from it) ·
`repo_paths` · `requests` — **only** for the
`/health` fan-out the shell and Status page run · `fastapi.responses` for the
report routes · the lazy `edge_tts` in `voice.py` · and, since 2026-09-06, the
three **credential primitives** the login uses — `argon2` (password hashing),
`pyotp` (the TOTP second factor) and `itsdangerous` (the signed session and
remember-device cookies). They join the list on the same footing `edge_tts` did:
none is an engine, none touches a DB or Schwab, and each is a leaf library over
bytes. They live in `auth.py`, `auth_store.py`, `auth_middleware.py` and
`login_page.py` and nowhere else. **Zero** engine imports, zero
`sqlite3`, zero Schwab calls, and — since 2026-08-21 — zero `sys.path` glue into
a hyphenated app folder (`webgui/proxy.py` held the last of it for two dead
client singletons; `test_proxy.py` now guards it at source level). ⚠ The
shorthand "only nicegui + shared.bus + shared.contracts" was repeated in several
places and was wrong on the last term: **the webgui imports `shared.contracts`
nowhere at all** — see the contracts note below.

⚠ **Tier 1 has TWO entrypoints since 2026-09-07** — `webgui/main.py` (the app,
behind the login) and `webgui/live_main.py` (the public read-only screens). The
allow-list binds both; the public one adds a stricter rule of its own — **no page
may `import main`** — for the reason in “The public live screens” below.

**Contracts are a WRITE-side gate on SOME views, not the typed API both tiers
share.** The design says "both tiers import them; validated on write and read".
Measured 2026-08-21: the webgui reads ~50 distinct cache views, `shared/contracts/`
defines 18 models, and services validate at ~15 of 74 `cache_set` sites. Read-side
validation does not exist. Treat a contract as a guarantee only for the views that
actually construct one (`ScanResult`, `NetPremiumSnapshot`, `MatrixSnapshot`, …);
for the rest the payload shape is whatever the builder last emitted. ⚠ Documenting
a view as "validated by X" does not make it so — `cache:options:matrix` carried
that claim in this file and its design doc while `MatrixSnapshot` was used only in
its own unit test, from 2026-07-20 until it was actually wired on 2026-08-21.
Since 2026-10-04 the five money-path views (paper account, rescue summary, Paper
Ledger, the Ledger's caps book, the Paper button's answer) are gated on write by
small models; a payload that fails is not published and is counted as a degrade.

The shape:

```
TIER 1 GUI         webgui/ NiceGUI (:8500) — render() only; reads Redis cache on
                   page build, subscribes to pub/sub for repaints, enqueues commands.
                   No engine imports, no Schwab calls, no sys.path glue.
        ▲ cache read / subscribe          │ commands
TIER 3 STORE+COMM  Redis (:6379): cache:{domain}:{view} (replaces _CACHE/_LAST_RESULTS),
                   events:{domain}:{view} pub/sub (replaces bridge file + version polling),
                   cmd:{domain} Redis Streams (GUI→service RPC). shared/contracts/ (typed
                   payloads = the API) + shared/bus/ (redis-py wrapper, fakeredis under pytest).
                   On-disk DBs unchanged. sentiment_bridge.json kept as dual-write shim.
        ▲ publish                          │ consume
TIER 2 PROCESSING  services/{domain}_svc FastAPI (options/sentiment/trade/portfolio/market/news):
                   each imports ONLY its engines, owns its scheduler/auto-scan + command
                   consumer, validates+caches+publishes. Separate processes ⇒ the scoring/
                   notifier sys.path collision class CANNOT occur (options_scoring() guard is
                   DELETED, not ported). Calls schwab-proxy (:8100) for data.
```

**Unit order: Redis → proxy → services → webgui**, expressed as `Requires=`/`After=`.** Ports: `memurai=6379` plus one
per service (8210–8213, 8215, 8216; 8214 was the removed driver's). One shim survives by decision — `sentiment_bridge.json` is still
dual-written for `regime_filter`; retiring it (making `regime_filter` read Redis) is the last
open migration item. Full design:
[3-tier design doc](docs/plans/2026-06-15-three-tier-architecture-design.md).

## Folder map (what was copied in)

| Folder                 | Role                                                        | UI status        |
|------------------------|------------------------------------------------------------|------------------|
| `schwab-proxy/`        | Central Schwab API gateway / token manager, plus an in-memory store of what it has fetched (`market_store.py`; it answers from that store only when `config/marketdata.toml` `mode = "on"`). **Start FIRST.**| backend, :8100   |
| `options-scanner/`     | GEX/options scanner engines, scoring, paper engine, simulator. **`gex_history.db` stores FIVE view strings per symbol per minute** — `gex`/`charm`/`dex`/`vanna` plus **`prem`** (2026-08-15, per-strike traded premium from `flow_skew.premium_by_strike`, feeding the Premium Divergence strike ladder). `view` is free-form and a premium cell is `{call, put, net}` floats — exactly what the columnar float32 packer gates on — so the fifth view needed **no schema change**, and costs ~**+25%** on that DB. | engines only (Dash UI dropped) |
| `sentiment-dashboard/` | Market sentiment `scoring/` + `history_backfill` + `live_composite.py` (live intraday composite + bridge payload) + `publish_bridge.py` (headless bridge writer) + bridge + `sectors_ref.py`. **Its `market_calendar.py` was absorbed into `shared/market_calendar.py` and DELETED (2026-08-02)** — same module name and same three function names, but *inclusive* `prev/next_trading_day` vs the shared module's *exclusive*, an invisible one-day trap. | ported to NiceGUI `/sentiment` |
| `trade-analyzer/`      | `src/analysis` — fundamentals, recommendation, scoring, sector. | engines only (Tk UI dropped) |
| `portfolio-analyzer/`  | `src/` — sector breakdown, vs-sector perf, live streaming.  | engines only (Tk UI dropped) |
| `services/`            | The six Tier-2 services — `sentiment`/`options`/`portfolio`/`trade`/`market`/`news` `_svc` (:8210–8213, 8215, 8216) — plus `_scaffold`/`_degrade`/`_heartbeat`. **`news_svc`** is the one with no copied engine: RSS/EDGAR and economic-calendar adapters → `services/news_svc/data/news.db` → eight views, no Schwab or Claude call. **`trade_svc` runs a scheduler** (since 2026-09-26) with one job, the daily watchlist dividend pull through the proxy into `shared/dividends.py`'s store; `news_svc` opens that store read-only and never calls the proxy. | backend          |
| `shared/`              | `analysis_lib/` (technical · sector_analysis · config) + secret templates/values. | library          |
| `tools/`               | `check_env.py`, `db_admin.py` maintenance utilities.        | CLI              |
| `webgui/`              | **NEW** NiceGUI multi-page front-end. Shell + Options section built. | the new UI, :8500 |

> The old UI entrypoints (`dashboard.py`, `sentiment_dashboard.py`,
> `trade_analyzer.py`, `portfolio_analyzer.py`, the React `frontend/dist`) were
> **not copied**. When porting a feature to NiceGUI, read those from the source
> repo `D:\Trading With Schwab` for reference.

## The web tier (`webgui/`)

Detail: [the shell](docs/reference/webgui-shell.md) · [development notes and NiceGUI gotchas](docs/reference/webgui-dev-notes.md) · [the public live screens](docs/reference/public-live-screens.md) · per-page behaviour in [docs/webgui-routes.md](docs/webgui-routes.md).

**The shell.** `webgui/main.py` is the server and nav shell; pages live in
`webgui/pages/`, each exposing `render()`. The left rail's ORDER is data
(`NAV_SECTIONS`), entries name their group or page so a typo raises at import, and
`SYSTEM_RAIL` (System Status, Settings, Stop All Services, Sign out) sits at the
foot. A group's child pages are a tab strip under the header. A colour set on a
rail element needs three classes and `!important`, or it is decorative.

**Adding a page.** A leaf module with `render()`; a `@ui.page` in `main.py` inside
`_layout`; the route in `test_shell.py`; the page in `test_no_inline_style.py`.
Build it from the page kit (`pages/ui_kit.py`): `kit.page`, `kit.header`,
`kit.button`, `kit.table`, `kit.confirm`, `kit.toast`. `tests/test_ui_kit_guard.py`
fails on a raw `ui.button` / `ui.dialog` / `ui.notify` / `ui.table`.

**Styling is Tailwind-first (mandatory).** All component styling is utility
classes through `.classes()`. Banned: `.style(...)`, inline `style=`,
`.props("style=…")`. A data-driven colour maps a finite state to a FIXED class;
never a runtime-built colour class. Theme tokens are Python constants in
`pages/options/theme.py`; `config/theme.toml` restyles without code. The one
escape hatch is `ui.add_css` for Quasar-internal or teleported DOM. Out of scope:
raw `HTMLResponse` documents, `ui.html` fragments, Highcharts option dicts.
⚠ The bundled Tailwind JIT does not generate a class containing `var(...)`.

**Shared page vocabulary.** `pages/fmt.py` (numbers: `num` is strict, `float_or`
permissive, and they differ on purpose), `pages/copy.py` (sentences more than one
screen shows), `pages/view_watch.watch_view` (the version-gated poll),
`pages/ui_guard` (wrap every timer and handler: a deleted client must be a no-op).
Labels are written from the reader's side; a `Credit` column is wrong wherever a
book holds debits, and an `entry_*` field under a bare label reads as live.

**Reading the bus.** `bus_client.read` hands each caller its own parse.
`read_shared` hands every tab at one version the SAME parsed object: use it for a
large view and treat what it returns as READ-ONLY. `read_gated` deserializes only
when the version moved. A poll probes `:ver` counters; payload reads go off the
event loop (`run.io_bound`).

**NiceGUI traps that have each cost real time** (the reference has the full list):
- `ui.html` strips `<style>`/`<iframe>` and sanitizes through DOMPurify: SVG
  `dominant-baseline` and `vector-effect` are stripped. Use `dy="0.35em"`, and
  percentage `<line>`s instead of a scaled `viewBox`. Test each builder's tags
  against the shipped allow-list.
- A `@ui.page` function's parameters become query parameters a stranger can set.
  Bind a loop variable in an enclosing function, never as a default.
- `ui.highchart` reflows once at mount: a chart that mounts hidden collapses. Give
  it an explicit height and reflow it when shown.
- A PLAIN chart that updates in place must not load the Highcharts `stock` module
  (it blanks the chart). `type="stockChart"` elements are a separate case.
- An interpolated heatmap needs a uniform strike grid (`gamma.uniform_strike_grid`).
- `ui.slider` keeps `min`/`max` in `_props`; assigning `slider.max` does nothing.
- A server-paged `ui.table` announces its own pagination when it mounts and
  NiceGUI writes it over the element's. Keep the pagination the page last sent in
  page state and put it back (`scanner.py`). The pager is `kit.page_of`.
- A CSS animation on a row a painter rebuilds restarts; `forwards` outranks hover.
- In the automation browser, transitions freeze and `getComputedStyle` lies.

**Verify in a browser.** There is no dev environment: `tools/ui_harness.py` renders
one page on a fake bus (loopback only; stop it when done). A failed bind is silent,
so confirm the port is free. For a 3-tier check, enqueue a command and read the
cache view through `Bus()`.

**The public live screens are a SECOND Tier-1 process** (`webgui/live_main.py`,
unauthenticated, `live.neuralstrike.co`). It renders the real page modules; the set
is data in `webgui/live_screens.py`.
- ⚠ **No page may `import main`, and neither may `live_main`**: importing `main`
  registers every private route. The seam a page needs is `webgui/shell.py`.
- Read-only is four layers: the Redis ACL user (`REDIS_LIVE_URL`; prod refuses to
  serve without it and proves at start that a `SET` and an `XADD` are refused),
  `bus_client.set_read_only(True)`, `app_settings.freeze(pins)`, and no private
  route existing in the process. All three in-process layers install BEFORE any
  page import.
- Exactly three write paths exist, each one XADD-only stream with its own worker
  loop: the Strategy Finder (`cmd:finder_public`), the Rescue form
  (`cmd:rescue_public`), and the Calculator/Simulator (`cmd:tools_public`,
  `cmd:tools_public_math`). `test_bus_client.py` pins the set of writers.
- ⚠ Redis is readable by the public process, so a quote written there is a quote
  published. A new public reader of the news feed or of flow alerts must take the
  public view or the `flow._shown` filter; the ACL is no layer for those.
- The unit loads `.env.live`, never the stack's `.env`. Never bind it to `0.0.0.0`.

## Sentiment and regime scoring

Detail: [development notes](docs/reference/webgui-dev-notes.md) (the NaN section, ADX, the two RRG engines, the regime names).

- ⚠ **A NaN clamps to the HIGH bound**, so "no reading" renders as an extreme
  reading. Filter non-finite inputs at the CALL SITE (`_finite_pcts`,
  `_finite_score_price`, each scorer's `_finite`); never change `clamp` itself,
  because only the caller knows what a missing input means.
- `is not None` and `or default` do not mean "present": a score of exactly 0.0 is
  a real, maximally bearish reading.
- A total function has no absence branch: `signal_band` publishes `None, None,
  None` when `live_composite.composite_reading` says there is no composite, and
  the producer publishes `total_score: None`. A consumer-side guard proves nothing
  until a test drives it from the PRODUCER.
- The volatility scorers never rise as volatility rises. `sectors_score` is `None`
  for no data and 1..10 for a real day. The Week and Month gauges score price with
  no VWAP term and one timeframe of one. Velocity, the regime-break flag and
  `derived.prev_total` compare the live composite with LIVE closes, never with the
  stored history.
- A session-scoped scorer gets ONE session (`_session_frame`);
  `calculate_ema_alignment` raises on a timeframe name it does not know.
- `technical.calculate_adx` and `_adx_series` seed differently on purpose: compare
  them on a long series or not at all. A characterization test pins what code
  DOES; only a test against an independent reference pins what it should.
- There are TWO RRG engines with different momentum definitions
  (`scoring/rotation` and `sector_rotation_assessment`). Characterise a rotation
  change on real bars, never on synthetic series.
- ⚠ Open: Sector & Industry and Sector Rotation can print opposite regime verdicts
  (different quantities on different scales). Do not "align the thresholds".
- The five regime KEYS are a contract and never renamed; the display words
  (Balanced, Whipsaw, Stressed, and the direction words) are mirrored across three
  tiers and pinned by `test_cross_tier_mirrors.py`. A direction is named only when
  both direction reads agree.

## Paths, ports and configuration

Detail: [configuration files](docs/reference/config-files.md).

`repo_paths.py` at the repo root is the single source for cross-app paths and
ports (`config/ports.toml`); each entrypoint puts the repo root on `sys.path` and
imports what it needs. **Never hard-code a `D:\` path or a port number.**

**STANDING RULE — configurable by default (the user's instruction, 2026-09-19).**
A value an operator could reasonably want to tune — a threshold, a window, a
cadence, a limit, a symbol list, a model name, a TTL — lives in a `config/*.toml`
read through `shared/config_toml.toml_loader`, **not** as a literal in code:
1. **New code** puts every such value in config from the start.
2. **Code you touch** that already hard-codes one moves it while you are there.
Each value also gets an entry in `webgui/config_schema.py` (Settings →
Configuration); `webgui/tests/test_config_schema.py` fails on a key the catalogue
lacks. Exceptions: mathematical constants, unit conventions, data-driven colour
maps, and anything whose change needs code to follow it.

- Loaders return built-in defaults for a missing file, key or bad value and never
  raise. Load order: defaults ← tracked `config/<name>.toml` ← the operator's
  gitignored `config/local/<name>.toml`. **The app writes only the override**; a
  tracked config file written by the app dirties the checkout and blocks a promote.
  Under pytest the override layer is ignored.
- Consumers resolve module constants at import (edit + restart). So the test that
  proves a value is READ monkeypatches the accessor and `importlib.reload`s the
  consumer; asserting equality proves nothing. `config/marketdata.toml` is the
  exception: read per request, no restart.
- A config dict is the CACHED mapping: treat it as read-only.
- The files: `ports`, `environments`, `sessions` (windows, `[slots]`, activation),
  `scanner` (selection floors and, since 2026-10-04, `[selection]` strike rules),
  `trade_mgmt`, `paper`, `symbols`, `sectors`, `marketdata`, `services`,
  `flow_alerts`, `notify`, `commissions`, `theme`, `news`, `edge`, and the public
  tools' files.
- `shared/market_calendar.py` is the single source for the NYSE calendar and the
  session/window predicates. Add no holiday literal or window constant elsewhere.
- `options_calculator.RISK_FREE_RATE` is the one pricing rate; import it.
- ⚠ **A timezone-naive datetime means CENTRAL time.** There is one settlement
  instant (16:00 New York) and one helper per tier
  (`options_calculator.expiry_time_to_years`,
  `options_svc.compute.time_to_expiry_years`). Never compute a time-to-expiry
  inline. `proxy_client` history frames are naive UTC: convert before pricing.

## Secrets

Live in `shared/` and are **all gitignored**. Real values were copied locally so
the app runs out-of-the-box; only the `*.example.*` templates are committed.

| Real file (gitignored)         | Template                                | Holds               |
|--------------------------------|-----------------------------------------|---------------------|
| `shared/appsettings.json`      | `shared/appsettings.example.json`       | Schwab API keys     |
| `shared/tokens.json`           | `shared/tokens.example.json`            | Schwab OAuth tokens |
| `shared/sentiment_bridge.json` | `shared/sentiment_bridge.example.json`  | Sentiment bridge    |
| `shared/notifications.json`    | `shared/notifications.example.json`     | Telegram/Discord/Fi-SMS push creds |

`schwab-proxy/proxy_tokens.json` and `**/config_notifications.py` are also
gitignored. **Never commit real keys, tokens, or account numbers.**

## Running, environments and deployment

Detail: [running and environments](docs/reference/running-and-environments.md) · operator runbook [docs/dev-prod-environments.md](docs/dev-prod-environments.md).

The stack is `systemd --user` units behind `trading-<env>.target`, GENERATED by
`deploy/systemd/generate_units.py --install` from `repo_paths` (no `.service` file
is in git; `--install` also arms the timers). Start order: Redis (a system unit)
→ proxy `:8100` → the six services (8210–8213, 8215, 8216) → `webgui` `:8500`;
`webgui_live` `:8501` needs only Redis. Logs are the journal.

- ⚠ **This box runs ONE checkout, at `/home/administrator/dev`, and it is PROD.**
  There is no dev environment and `/home/administrator/prod` does not exist. The
  dev profile (ports +1000, Redis db 1, schedulers / Claude / notifications off,
  borrows prod's proxy) is working code with nothing running it.
- **THE DEVELOPMENT RULE (mandatory).** Work is committed in a worktree, verified,
  merged to `main`, pushed, and reaches prod ONLY through `tools/promote.sh`. Never
  `git pull`, `merge`, `checkout` or `reset` in the prod checkout
  (`.claude/hooks/guard_prod_promote.py` blocks it). With no dev environment the
  "verified running" step has nowhere to run: say which substitute you used (the
  local page harness, a Redis-driven check, or verifying a read-only change on
  prod after it lands). The promote, verbatim:

  ```bash
  ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
  ```

  Nothing goes after it. It checks everything that can fail BEFORE the stop, and
  rolls back to the previous commit if anything after the stop fails
  (`--rollback` does it on request).
- ⚠ A new dependency goes in `requirements.lock` by hand, not only
  `requirements.txt`: prod has its own venv and reinstalls only when the lock
  moves. Never regenerate the lock with `pip freeze`.
- Under pytest the process presents as PROD with every suppression forced on; a
  dev branch is exercised only by monkeypatch.
- The apps bind `127.0.0.1` and Caddy fronts them (`app.` behind password + TOTP,
  `live.` with no login). **Never change a bind to `0.0.0.0`.** The proxy is
  never on the public domain; a link a browser opens uses `PROXY_PUBLIC_URL`.
- The proxy's account routes fail CLOSED without `PROXY_SHARED_SECRET`
  (`X-Proxy-Secret`), so do `/track` and `/untrack`, and it has no order route:
  `trader_request` is GET-only. This app is paper-only.
- A unit that ends FAILED sends a push; `/health` reports `up: false` with a
  reason when a scheduler has stopped or gone silent. Two units carry a memory
  cap (`webgui_live`, `webgui`); never add one to a service or the proxy as a
  drive-by. Path isolation is silently not applied in a `--user` unit here.
- The nightly backup's file list is derived from `.gitignore` by a test; restore
  with `tools/restore_backup.py`. It does not restore Redis.
- Never name a service module after a stdlib module (`secrets`, `token`, `types`,
  `queue`, …): a service's own folder is on `sys.path` when its `app.py` runs as a
  script, so the module shadows the stdlib one and the service dies on launch
  while the suite stays green. Putting several hyphenated app folders on
  `sys.path` makes same-named modules (`scoring`, `notifier`, `config`) collide.
- `shared/analysis_lib` is a LIBRARY of three modules (`technical`,
  `sector_analysis`, `config`); `shared/tests/test_analysis_lib_surface.py` fails
  if the old app grows back.
- `schwab_client.get_quotes` returns a FLATTENED mapping (`last`, `change`,
  `change_pct`, `high`, `low`, `volume`), not Schwab's envelope: reading
  `q["quote"][…]` yields `None` for every symbol with no error. A `0.00%` cell is
  not proof of a flat tape; only an omitted symbol is a real absence.
- Data flows one way: `tools/snapshot_from_prod.py`, run from dev, copies prod's
  stores and never `cmd:*` (a stream is a queue dev would execute).
- Three suppressions and where each is enforced: notifications
  (`shared/notify/channels.load_config`), Claude (the client factory), schedulers
  (`_scaffold._schedulers_enabled`). X has ONE posting path
  (`shared/notify/x_post.post`), called only from `options_svc`.

## Tests

Detail: [testing](docs/reference/testing.md) (the commands per suite, the baselines, the infrastructure).

Each app's tests run from inside its folder; each service suite runs on its own
from the repo root (never `pytest services` over several: hyphenated app folders
collide on `config` / `scoring` / `notifier`). The venv lives inside the checkout;
a worktree needs its absolute path.

- **There are no known baseline failures.** Compare the failing SET and the
  skipped SET, never the count: `pytest` defaults to `-rf`.
- The fake bus is ONE Redis per running test, as in prod. A test that needs an
  empty cache says so (`reset_fake_bus()`).
- The suite cannot open a live SQLite store or reach the network: the repo-root
  `conftest.py` guards `sqlite3.connect` and both HTTP stacks. Prefer
  `db_path=None` resolved at call time in a new store; a default bound at `def`
  time cannot be redirected.
- CI runs every suite as blocking, with a `typecheck` job;
  `tests/test_ci_covers_every_suite.py` fails when a suite loses its row. A new
  test folder needs a row.
- The lint gate runs at commit (`tools/git-hooks/pre-commit`) with `E9, F63, F7,
  F82, F401, F811`. The last two are reported and NEVER fixed automatically: the
  editor hook runs `ruff --fix` on every edit, and would otherwise delete an
  import the moment it was added and the second of two same-named tests. A
  deliberate re-export carries `# noqa: F401` with who reads it.
- `pyrightconfig.json` is deliberately narrow (the bus, the contracts, the config
  loader, `bus_client`). It is clean and stays clean; do not widen it.
- Three size ceilings can only be lowered: `options_svc/compute.py`'s line count
  and the size of `gamma.render`, `desk.render`, `calculator.render`. New service
  code goes in a sibling module that imports nothing from `compute`.
- Cross-tier duplicates are pinned by `shared/tests/test_cross_tier_mirrors.py`.
  `scoring/_common.py` holds `clamp` and `num`; `shared/numeric.py` holds the
  named numeric guards (`finite`, `parsed_finite`), and
  `shared/tests/test_numeric.py` fails on a NEW private `_num` / `_finite`.
- A heredoc mangles backslash escapes into a test that asserts nothing: write
  test files with the editor, not `cat <<EOF`.

## Options, paper books and trade selection

Detail: [options engine invariants](docs/reference/options-engine-invariants.md). **Read it before changing the paper engine, Rescue, the scanner's gates or settlement**: every rule below has an incident and a measurement behind it there.

**The two paper books.**
- An equity lot is cash CONVERTED, never a buying-power reservation: a lot never
  touches `buying_power_reserved`, the purchase is `debit_cash`, never
  `realize_pnl`, and `_assign_shares` releases nothing a second time.
- The risk envelope has SIX rungs (per trade, per symbol count and risk, per
  expiry, deployment, sector), evaluated in ONE module, `shared/book_caps.py`;
  the Account's `concentration_reject` is an adapter over it. Edit the rungs
  there. A concentration breach on the Account SKIPS without recording an order.
- The Ledger checks the risk it would BOOK (`book_caps.booked_risk`), refuses a
  non-positive or non-finite risk before evaluating, and answers every Paper click
  on `cache:options:paper_create`. The dialog previews the same decision
  (`book_fit.preview`), and a dialog that cannot preview never blocks.
- Every mutation of either book runs under `paper_lock.BOOK_LOCK`
  (`@paper_lock.serialized`); a close is one transaction on a row still OPEN. The
  lock covers one process.
- The five money-path views (paper account, rescue summary, ledger, ledger caps,
  the Paper answer) are validated by small models before they are published; an
  invalid payload is not published and is counted as a degrade.

**Selection.**
- ⚠ The "0-DTE" bucket spans DTE 0..4. The earnings gate's decision is
  `scanner_engine.earnings_gate_applies(trade_type, dte)`, called per signal by
  both mirror sites; `compute.scan_earnings` is the one lookup. The Strategy
  Finder FLAGS a trade open through a report; the Scanner and Income Window DROP it.
- Selling premium has a volatility floor, `shared/vol_gate.blocks`, keyed on the
  candidate's own VEGA SIGN, never its name. A raw scanner row's `net_vega` /
  `net_theta` are `short − long`, not position-signed: read a sign through
  `shared.structures.position_greek`. Absence skips the gate; 0 means off. The
  drop count is its own field (`vol_filtered`).
- A short-delta band governs the SHORT legs only, aims at its midpoint, and
  enforces only its ceiling.
- The width search sizes against the real per-trade limit
  (`config_paper.MAX_RISK_PER_TRADE`); a width nothing can size is not emitted.
- The NAKED reward gate is an annualised rate; `dte <= 0` returns `None`.
- The scanner's strike rules are `config/scanner.toml [selection]`.

**Exits and marks.**
- Exit rules are PER STRUCTURE: `shared/trade_mgmt.structure_rules(strategy)`.
  `loss_rules = false` for a cash-secured put and a covered call (each loss rule
  is inverted for them); `manage_dte` closes a PROFITABLE position.
- A DEBIT position stores `entry_credit` NEGATIVE: `recommend` dispatches it to
  `_recommend_debit` before any credit rule, and `close_paper_trade` books by
  direction. Debit levels are sourced, not fitted; the loss side ships off.
- `entry_short_delta` of `None` means "not recorded"; never write `0.0`.
- A single-leg mark is the OPTION LEG only (`realistic_single_fill`); a covered
  call's `unrealized_pnl` is not the position's economics.
- A quote or Greek that is not usable is ABSENT: `shared/greeks.py` refuses
  Schwab's `-999`; a zero bid under a small offer is still a market.
- Book Greeks are net per position and signed by side; `None` is "not computed",
  never zero; a cross-symbol delta total is a direction, not a hedge ratio.
- The profit-lock ladder can only RAISE a stop; the peak passed is this cycle's.
- `shared/structures.py` is the one taxonomy (side, shape, legs, canonical name);
  a test fails on a new copy. A CCS keeps its strikes in `short_strike` /
  `long_strike`; only an IC uses `call_short` / `call_long`.

**Settlement.** `paper_engine.settlement_underlying` is the ONE rule for all three
books: the regular-session last on expiry day at or after 15:00 CT, the expiration
date's daily close on any later day, and `None` (defer) with no usable price.
`[slots.paper_settle]` (15:05 CT) is settle-ONLY and must never become a manage
cycle. `signal_repricer.expiry_value` values a captured signal; `intrinsic_value`
is the Account's.

**Rescue.**
- Single-leg positions route to `single_candidates` (advisory).
- A roll or a narrow reserves the REMAINING position's own risk; a convert's
  credit joins `entry_credit`.
- `rescue_apply` applies the SERVICE's own candidate from the menu it published,
  found by `rescue.candidate_key` (the action and the contracts); the page's echo
  supplies an identity and nothing else.
- Earnings and an expiry-day pin are modifiers on the risk read, never triggers.

**Commands.** `cmd:options` is a table (`handlers._COMMANDS`, one `_cmd_*` each;
never a branch in `handle_command`). Replay is stopped twice: the consumer drops
a command older than `[age] replay_max_sec` unless it is `SAFE_LATE`, and the
dispatcher refuses a `_REPLAY_GUARDED` command older than 180 s. That is an age
gate, not idempotency. The long, self-contained commands (`handlers.SLOW_LANE`:
the scans, the briefings, the rating, the Symbol lookup, X posts) run on the
queue's slow lane; nothing that changes a paper book may be put there.

**Other standing facts.**
- A STOCK leg counts 100-share LOTS; go through `leg_editor.legs_ready`; only the
  Calculator offers the stock structures; a Finder covered call must never gain a
  Paper button.
- The Finder's payoff math has two valuation paths and the single-expiry one must
  stay byte-identical; a later leg is priced at the volatility ITS OWN MARK
  implies. Its probability of profit is lognormal over the time left.
- The Income board is captured for calibration under `scanner_type = "INCOME"` and
  `run_entry_cycle` refuses that type: capturing is not trading. Captured rows are
  per SHARE.
- "Vol Rank" ranks IV inside REALIZED volatility; the true IV history is
  `shared/iv_history.py`, and a clamped reading is never stored.
- The scheduled briefings bill the subscription (`claude_cli.py`); the child
  environment strips `ANTHROPIC_API_KEY`, and the API counter counts billed calls.

## Observability and performance

Detail: [observability and performance](docs/reference/observability-and-performance.md).

- **A swallowed exception must leave a trace.** A guard of 15 lines or more calls
  `_degrade.degraded("<area>")` (WARNING with traceback, counted on `/health` as
  `degrades_total`, shown on the Status card). `test_no_silent_degrades.py` reads
  the handler's call nodes across `services/`, `options-scanner/` and `shared/`.
  Small parse guards stay silent. Do not enable ruff `BLE001` instead.
- **Version probes are cheap; payload reads are not.** `cache_set` keeps `:ver`,
  `:ts` and `:sig` side keys. `skip_unchanged=True` decides from the `:sig`
  digest without reading the payload back, refreshes `:ts`, and bumps no version.
  ⚠ A `skip_unchanged` view must carry no timestamp of its own.
- **Measure before optimising a localhost read**: a few hundred KB once per click
  is single-digit milliseconds. Optimise what runs 43,200 times a day.
- A cropped payload is not a bounded payload: split what the reader does not read
  (the gamma history keys), and write history keys BEFORE the main key.
- **Threads.** Each command stream has a thread of its own (read and handler);
  scheduler branches share a bounded pool (`config/services.toml [pool]`). A due
  scheduler branch is a keyed background task and can only delay itself.
- **Every service shares the proxy's 5 requests a second.** A scheduled chain
  burst stays off the quarter hours (the autoscan owns :00/:15/:30/:45); read the
  proxy's access log before scheduling a new fan-out. The one-minute poll's
  requests go first (`X-Priority`, a header, never a parameter); that lane is the
  poll's alone.
- **The proxy can answer from memory** (`schwab-proxy/market_store.py`,
  `config/marketdata.toml`, shipped `mode = "shadow"`). A local answer is not a
  Schwab call. A chain is cut to a narrower window only between PLAIN requests. An
  entry never crosses a session change and is never served past its limit because
  Schwab failed. A caller that must have a real fetch sends `maxAge=0`; a paper
  fill and a Rescue apply do. With a tail interval above 1, watchlist-only symbols
  are CARRIED between fetches (stored rows carry `carried_age_sec`; studies filter
  on `gex_history_db.fetched_only_clause`), not handed to the volume detectors,
  and refetched in the same poll when the carry's gamma cap binds. A changed store
  rule goes through `shadow` before `on`.
- I/O-bound proxy fan-outs use `services/_parallel.parallel_map`, pools of 8 or
  fewer. Highcharts update in place; data pages version-gate their repaints.

## External processes (not in this repo)

None. The ML prediction servers (MES 8000 / MNQ 8001 / ES 8004 / NQ 8005) and the
options analytics service on 8200 are separate processes this repo no longer
calls: the claude-driver scripts that did were removed 2026-09-11, and the
`claude-driver/` folder, its `config.py` and those ports' `config/ports.toml`
entries (plus `approval` and `dashboard_frontend`) went on 2026-09-19.

## Design / plan docs

- [`docs/plans/2026-06-15-three-tier-architecture-design.md`](docs/plans/2026-06-15-three-tier-architecture-design.md) — **3-tier re-architecture** (GUI / per-domain services / Redis-Memurai backbone)
- [`docs/plans/2026-06-15-three-tier-architecture-plan.md`](docs/plans/2026-06-15-three-tier-architecture-plan.md) — bite-sized TDD implementation plan for the above
- [`docs/plans/2026-06-14-nicegui-webgui-design.md`](docs/plans/2026-06-14-nicegui-webgui-design.md)
- [`docs/plans/2026-06-14-nicegui-webgui-plan.md`](docs/plans/2026-06-14-nicegui-webgui-plan.md)
- [`docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md`](docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md) — **`/sentiment` Day/Week/Month ring graphics** (four gauges → two concentric SVG rings; the Week structural horizon; the Signals tile stack)
- [`docs/plans/2026-08-14-sentiment-trend-ring-graphics-plan.md`](docs/plans/2026-08-14-sentiment-trend-ring-graphics-plan.md) — bite-sized TDD implementation plan for the above
- [`docs/plans/2026-08-15-gamma-plasma-palette-design.md`](docs/plans/2026-08-15-gamma-plasma-palette-design.md) — **Dealer Positioning plasma palette + wash** (GEX/Charm/DEX/Vanna + Term recoloured cyan/magenta; why the `plotBackgroundColor` ban was narrower than it read)
- [`docs/plans/2026-08-16-app-reference-guide-design.md`](docs/plans/2026-08-16-app-reference-guide-design.md) — **the Reference Guide** (a fourth manual; the per-page template, and why the audience level drives the plain-language + external-citation rules)
- [`docs/plans/2026-08-17-sector-heat-grid-design.md`](docs/plans/2026-08-17-sector-heat-grid-design.md) — **Sector & Industry heat grid** (magnitude-forward tiles; why the column scale is p90 and not the max)
- [`docs/plans/2026-08-17-sector-rotation-board-design.md`](docs/plans/2026-08-17-sector-rotation-board-design.md) — **Sector Rotation board** (diverging spread gauge, weight-proportional flow band, quadrant panels)
- [`docs/plans/2026-08-17-rrg-plot-design.md`](docs/plans/2026-08-17-rrg-plot-design.md) — **RRG hand-drawn plot** (marker area = S&P weight, smoothed 5-reading trails; why the domain is computed and `vector-effect` unusable)
- [`docs/plans/2026-08-17-momentum-guided-page-design.md`](docs/plans/2026-08-17-momentum-guided-page-design.md) — **Momentum guided page** (a numbered argument; leaderboard behind a toggle; the ragged `rank_history` trap)
- [`docs/plans/2026-10-03-market-data-store-design.md`](docs/plans/2026-10-03-market-data-store-design.md) — **the proxy's local market-data store** (what one day's calls were, what a store can and cannot remove, the collector's two tiers, what review changed)
- [`docs/plans/2026-10-03-market-data-store-plan.md`](docs/plans/2026-10-03-market-data-store-plan.md) — bite-sized TDD implementation plan for the above

## User-facing manuals

**Five** manuals under [`docs/manuals/`](docs/manuals/README.md), each authored once
in Markdown and built by `build_docs.py` into HTML + `.docx`. They are surfaced
in-app at **More → User Manuals** via `webgui/pages/manuals.py:MANUALS` — **a new
manual must be added in BOTH places** (`build_docs.py:MANUALS` to build it,
`pages/manuals.py:MANUALS` to serve it; the latter is also the path whitelist, so an
unlisted file is refused rather than served).

| Manual | Answers |
|---|---|
| **User Guide** | *How do I do this?* — task-oriented operation |
| **Reference Guide** | *What is this tab for, and when do I open it?* — per-tab depth over a one-page orientation |
| **Technical Reference** | *Where does this number come from?* — formulas, weights, cadences |
| **API / Developer Reference** | *How do I integrate with this?* — contracts, bus, commands, proxy |
| **Options Glossary** | *What does this word mean?* — the vocabulary the other four assume |

⚠ **`webgui/page_help.py` is a manual too — and the most-read prose in the app** —
the per-page hover guides. It is the least likely thing to be touched when a feature
moves, so it rots first: the 2026-08-16 audit found it claiming a 5-minute paper
cycle that is hourly, a fixed $500 driver target that ratchets $250–$1,000, and
three flow-alert detectors where there are four. Treat it as documentation with a
test suite, not as code.
