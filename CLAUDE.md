# CLAUDE.md — WebGUI Trading with Schwab

Guidance for Claude Code sessions working in this repository. Read this first,
then the per-app `CLAUDE.md` for the folder you are editing.

> **Maintenance:** This document is the living architecture/tech record for the
> project and is **updated regularly** as the build progresses (an explicit
> standing requirement). After any structural change — new page, new dependency,
> port change, copied/removed module — update the relevant section here.

**Changelog moved (2026-08-07):** the running log of dated session entries (the old *Last updated / Prior —* chain) now lives in [docs/CHANGELOG.md](docs/CHANGELOG.md), newest first. **Append new session entries there**, not here. This file holds the DURABLE record — architecture, conventions, per-subsystem guidance — which you still update in place whenever the structure it describes changes.

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
| Scheduling       | APScheduler (claude-driver)                                       |
| Notifications    | winotify (Windows toast)                                          |
| Spreadsheet I/O  | openpyxl                                                          |
| Testing          | pytest                                                            |
| Runtime          | Python 3.11+, Windows-first, single-user                         |

## Architecture

```
schwab-proxy (:8100)  ──HTTP──>  webgui NiceGUI app (:8500)
        │                              │
   owns Schwab auth/                   ├─ Options  page  → options-scanner engines
   tokens + market data               ├─ Sentiment page → sentiment-dashboard scoring
                                       ├─ Trade    page  → trade-analyzer src/analysis
                                       ├─ Portfolio page → portfolio-analyzer src (live)
                                       └─ Driver   page  → claude-driver orchestration
        │
   shared/analysis_lib  ← shared library (schwab_client, market_data, technical, mtf…)
```

**The proxy must be running first.** All feature backends resolve their Schwab
client and market data through `http://127.0.0.1:8100`.

## Planned 3-tier architecture (approved 2026-06-15 — migrating)

The monorepo is being re-tiered (strangler-fig) into three **physically separate**
tiers over a **Redis (Memurai) backbone**. Until a domain is migrated it keeps the
in-process model above; check the design doc for current step status. Target shape:

```
TIER 1 GUI         webgui/ NiceGUI (:8500) — render() only; reads Redis cache on
                   page build, subscribes to pub/sub for repaints, enqueues commands.
                   No engine imports, no Schwab calls, no sys.path glue.
        ▲ cache read / subscribe          │ commands
TIER 3 STORE+COMM  Memurai (:6379): cache:{domain}:{view} (replaces _CACHE/_LAST_RESULTS),
                   events:{domain}:{view} pub/sub (replaces bridge file + version polling),
                   cmd:{domain} Redis Streams (GUI→service RPC). shared/contracts/ (typed
                   payloads = the API) + shared/bus/ (redis-py wrapper, fakeredis under pytest).
                   On-disk DBs unchanged. sentiment_bridge.json kept as dual-write shim.
        ▲ publish                          │ consume
TIER 2 PROCESSING  services/{domain}_svc FastAPI (options/sentiment/trade/portfolio/driver):
                   each imports ONLY its engines, owns its scheduler/auto-scan + command
                   consumer, validates+caches+publishes. Separate processes ⇒ the scoring/
                   notifier sys.path collision class CANNOT occur (options_scoring() guard is
                   DELETED, not ported). Calls schwab-proxy (:8100) for data.
```

**Migration order:** Sentiment (reference) → Options → Portfolio → Trade → Driver →
retire shims (regime_filter reads Redis; drop bridge file). New ports to add:
`memurai=6379` + one per service (~8210–8214). `start_all` order: Memurai → proxy →
services → webgui. Full design: [3-tier design doc](docs/plans/2026-06-15-three-tier-architecture-design.md).

## Folder map (what was copied in)

| Folder                 | Role                                                        | UI status        |
|------------------------|------------------------------------------------------------|------------------|
| `schwab-proxy/`        | Central Schwab API gateway / token manager. **Start FIRST.**| backend, :8100   |
| `options-scanner/`     | GEX/options scanner engines, scoring, paper engine, simulator. | engines only (Dash UI dropped) |
| `sentiment-dashboard/` | Market sentiment `scoring/` + `history_backfill` + `live_composite.py` (live intraday composite + bridge payload) + `publish_bridge.py` (headless bridge writer) + bridge + `sectors_ref.py`. **Its `market_calendar.py` was absorbed into `shared/market_calendar.py` and DELETED (2026-08-02)** — same module name and same three function names, but *inclusive* `prev/next_trading_day` vs the shared module's *exclusive*, an invisible one-day trap. | ported to NiceGUI `/sentiment` |
| `trade-analyzer/`      | `src/analysis` — fundamentals, recommendation, scoring, sector. | engines only (Tk UI dropped) |
| `portfolio-analyzer/`  | `src/` — sector breakdown, vs-sector perf, live streaming.  | engines only (Tk UI dropped) |
| `claude-driver/`       | Morning/intraday orchestration + order approval logic.      | engines only (approval UI to be NiceGUI) |
| `shared/`              | `analysis_lib/` shared library + secret templates/values.   | library          |
| `tools/`               | `check_env.py`, `db_admin.py` maintenance utilities.        | CLI              |
| `webgui/`              | **NEW** NiceGUI multi-page front-end. Shell + Options section built. | the new UI, :8500 |

> The old UI entrypoints (`dashboard.py`, `sentiment_dashboard.py`,
> `trade_analyzer.py`, `portfolio_analyzer.py`, the React `frontend/dist`) were
> **not copied**. When porting a feature to NiceGUI, read those from the source
> repo `D:\Trading With Schwab` for reference.

## webgui structure (NiceGUI app)

`webgui/main.py` is the server + nav shell (**sub-menus are TABS** since
2026-07-11; the drawer became an **ICON RAIL** 2026-07-15; **reorganized
2026-07-27; **Strategy Tools group added 2026-07-28**; **system pages moved to
the drawer FOOT 2026-08-12**): the left drawer is a **FLAT main menu** of **13
items** — one per group (**Options**, **Strategy Tools**, **Market Trend &
Sentiment**, **More**), the **three standalone `OPTIONS_RAIL` pages** that sit
directly under the Options group (**Dealer Positioning**, **Opportunity Board**,
**Flow Alerts**), the flat Trade Analyzer / Portfolio / Claude Trades items, and
a bottom-pinned **`SYSTEM_RAIL`** block (**System Status**, **Stop All
Services**, **Settings**) — and the active group's
**child pages render as a compact TAB STRIP across the top of the page**
(`_NAV_GROUPS` + `_group_children(active)`; a `ui.tabs` under the header with
`.compact-tabs` small padding — q-tab min-height 30px — clicking a tab
navigates; More's strip is EOD Report + the Settings children, e.g. User Manuals).
**A rail page has NO tab strip** (`_group_children` → None) and its breadcrumb is
just the page name. **`SYSTEM_RAIL` is those machine-level controls** — health,
shutdown, configuration — lifted out of the More tab strip and given their own
block at the foot of the rail (the conventional place for them, and they are not
a step in any analysis workflow). They render like `OPTIONS_RAIL` (standalone
`_nav_link`s, no tab strip) but after a hairline separator with **`mt-auto`**,
which eats the leftover column height so the block sits on the bottom edge —
note `mt-auto` + `my-*` on one element fight over `margin-top`, so the separator
uses `mb-2`. **Settings therefore no longer owns User Manuals** as a sub-page;
`SETTINGS_CHILDREN` survives as a More tab, a peer of EOD Report. **Market Dashboard is the FIRST tab of the Market Trend &
Sentiment group** (it was a flat item until 2026-07-27), and since
`_nav_group_link` navigates to `children[0]`, that group's rail item lands on
`/market`.

**The drawer is a 64px ICON RAIL that expands to 248px on hover and OVERLAYS
the page.** It is LAID OUT at `NAV_WIDTH_RAIL=64` via Quasar's `width` prop
(`drawer_width(pinned)` → 64, or `NAV_WIDTH_OPEN=248` when pinned — `ui.left_drawer`
has **no `width` kwarg**, so it goes through `.props(f"width={...}")`), and
`_NAV_CSS` widens it on hover/`:focus-within` with
`.q-drawer:has(> .nav-drawer:not(.nav-pinned))` → `width: 248px !important`.
**Quasar's LAYOUT still uses 64, so `.q-page-container`'s padding never changes —
the expanded menu OVERLAYS content rather than reflowing it.** That is deliberate:
this app's Highcharts have no ResizeObserver, so a reflow on every hover would
leave charts mis-sized. No Quasar mini-mode, no JS, no hover round-trips. Because
only the icon is visible when collapsed, **the icon is the affordance** (the
`icon` arg is live again — the earlier colored-dot indicator is retired; a test
guards that the 7 drawer icons stay non-empty + mutually distinct). Labels/title
clip and fade in via opacity; `.nav-drawer { overflow-x: hidden }` stops the
248px of content raising a scrollbar in the rail. The **hamburger pins/unpins**
(`_toggle_pin`, persisted in `app_settings` `nav_pinned`, default False) rather
than show/hide: pinned lays out at 248 (the page genuinely reflows — correct for
an explicit choice) and the `.nav-pinned` class opts out of the hover rule. The
**active-icon accent** is `.nav-drawer .nav-active .nav-icon` — 3 classes +
`!important`, which it must be to out-specify `theme.build_nav_css`'s
`[menu].text` rule (see the gotchas). Per-page alert badges **float on the tabs**
(`_badge_refs`) and, in the drawer, on each **icon's top-right corner** (Quasar
`floating` on a `relative` wrapper — so a collapsed rail still reports counts);
each drawer group item carries the **SUM of its children's badges**
(`_group_badge_refs`, updated by the 2s watcher). `_count_badge`/`_set_badge` are
the shared build/update pair. The old expandable sub-menus / `_NAV_OPEN` /
`_settings_group` are GONE. Tabs are **pill-style** (raised rounded container,
active pill a soft navy tint). A page with its
own view tabs mounts them as a **subtab row flush under the strip** via
`main.subtab_slot()` + `.compact-subtabs` (e.g. the Gamma
GEX/Charm/DEX/Vanna/Flow/Term picker, a `ui.tabs` since 2026-07-11 — same
value/on_value_change API as the old `ui.toggle`). Pages live in
`webgui/pages/`; each leaf exposes `render()` called inside the shell
`_layout`. `webgui/proxy.py` wraps `schwab-proxy/proxy_client.py` and adds
`health()`. Pure transforms / SVG builders are unit-tested (`webgui/tests/`);
heavy engine calls run off-thread via `nicegui.run.io_bound`.

**App-wide alerts + nav badges (DONE — 2026-06-17).** `_layout` mounts a hidden
`<audio>` + a `ui.timer(2s)` watcher (`alerts.py` pure helpers + `main._run_watcher`)
that runs on **every** page: it chimes a bundled WAV (`webgui/static/sounds/{chime,
bell,ping}.wav`, served at `/static`) — and optionally fires a desktop
`Notification` — on new qualifying scanner signals (gated by enable/market-hours/
min-score in `app_settings`; view-staleness alerts use PER-VIEW thresholds — `alerts.stale_after`/`STALE_OVERRIDES`, `options:scan` = 20 min so the 15-min autoscan isn't falsely flagged between scans), and maintains red count badges on **Scanner** (new
signal keys), **Captured Signals**, and **Driver** (pending approval) nav items
(`_NAV_BADGES`, single-user like `_NAV_OPEN`; cleared when you open that page).
GUI prefs persist via `webgui/app_settings.py` → `webgui/data/settings.json`
(**gitignored** since 2026-08-09; missing keys — and a missing file — regenerate
from `DEFAULTS`, so each checkout carries its own preferences. It was tracked
before that, which made `tools\promote.bat` refuse on a dirty tree every time
anyone changed a setting in prod's GUI; the file is runtime state the app writes,
so it can never be clean. Untracking it means a **pull deletes the existing copy**
in any checkout that had it — back it up and restore it across that one promote).
The **Settings** page (`/settings`,
`pages/settings.py`) binds the alert toggles/sound/volume/market-hours/min-score +
desktop-notification controls. The drawer is restyled (`.nav-drawer` CSS: active
pill, hover, right-aligned badges, title block). Browsers block autoplay until a
user gesture — clicking any nav link or **Test sound** unlocks it. Design/plan:
[design](docs/plans/2026-06-17-scanner-alerts-settings-badges-design.md) /
[plan](docs/plans/2026-06-17-scanner-alerts-settings-badges-plan.md).

Routes:

| Route | Page | Status |
|-------|------|--------|
| `/` | Options · Market Scanner (0-4 / 5-15 DTE, two-pane + detail panel; **THREE folder-style SUBTABS since 2026-07-16 — 0-DTE / Swing / Directional**. **Directional** renders the engine's `signals_directional` (single-leg LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT) via the SHARED `strategy_table` builders, scored on **Fit+Quality** (never beside a premium composite — see the Last-updated entry); naked shorts show `Max L = ∞` + an undefined-risk badge and no Paper button. **Since 2026-08-06 the ENGINE only emits non-Weak candidates scoring ≥ 50** (`scanner_engine.SINGLE_LEG_MIN_SCORE` / `SINGLE_LEG_EXCLUDED_GRADES`, cut before the per-symbol cap) — an empty Directional tab now means "nothing cleared the bar", not a failure, and long CALLS largely vanish because the documented unbounded-profit R:R artifact scores them ~14 points below long puts. **The tables read `cache:options:scan_day`** (the day union) not `cache:options:scan`, so the day's signals persist to EOD with dropped-out ones **dimmed + frozen + "Dropped HH:MM"** and **no Paper button** (frozen price + verbatim `entry_credit` = a fictional entry); the render is **gated on the envelope's CT date** and surfaces a `truncated` notice. The status bar still reads the LIVE key (the day envelope carries no timestamp/errors) and says "N live signals" so it can't be read as the day count. **"New" = unseen since you last VIEWED the page** (acknowledged only on initial paint), keyed on the engine's unique `id` — this fixed a real bug where the key collapsed to `SPY|PCS|None|None|07/17`; **a webgui restart re-marks everything New** (page-side state, deliberate). ⚠ the nav badge/chime still count credit spreads ONLY — a Fit+Quality score isn't commensurable with the premium composite the min-score alert threshold gates on;  under the main tab strip** (2026-07-11, `main.subtab_slot()` + `.compact-subtabs`; amber/blue tab text kept) with **live signal counts** (`tab_label`); **Run scan is right-aligned flush with the table** (`.scan-panels` drops the q-tab-panel padding); a new qualifying signal pops an **in-app toast** (`fiber_new`, blue-8 — matching the row "new" badge) alongside the chime/desktop notification; **Run scan** is the app's solid 3D button (`color=None` + `.scan-btn`); the per-row **Send to Calculator** now transfers correctly — `_prefill` stashes `pending_legs` + `load_symbol()` so legs apply AFTER the chain loads, instead of being wiped by strike-coercion against an empty chain (see [[calculator-leg-transfer-needs-chain-first]])) | built |
| `/options/matrix` | Opportunity Board (**NEW 2026-07-20** — a **main-menu (left-rail) item directly under the Options group** (`main.OPTIONS_RAIL`, standalone page, NOT an Options tab-strip entry): at-a-glance **sortable grid of every watchlist stock** (~45 symbols = `collection_symbols()` minus `$VIX`), one row/symbol — Ticker/Spot/Day %/**Intraday trend**/**Call+Put flow acceleration**/P/C ratio/Net premium $M/**GEX regime**/# Signals/# Flow alerts/**Buy-Neutral-Sell** flow composite/**Hotness** (default sort, hottest first). Pure Tier-1 reader of **`cache:options:matrix`** (`webgui/pages/options/matrix.py`, version-polls ~2 s, in-place sortable `ui.table`, Tailwind-first colored cells) published by a new `options_svc` aggregator — pure `services/options_svc/matrix.py` (trend/accel/composite/hotness) + `compute.build_matrix` over `gex_history.db` (`load_flow_series` + cheap `latest_flip`) + per-symbol counts from `scan_day` (signals) + the **uncapped** `flow_alert_cooldowns` seen-map (flow alerts — not the capped `flow_alerts` rolling list, `_FLOW_ALERTS_MAX`=300 since 2026-08-09); built on the 1-min GEX branch + a ~30 s live spot/day% overlay on the header tick. Counts gate on `session_date`. See the 2026-07-20 "Last updated" entry) | built |
| `/options/flow` | Flow Alerts (**NEW 2026-08-09** — a **main-menu (left-rail) item under the Options group** (`main.OPTIONS_RAIL`, standalone page, NOT an Options tab-strip entry — it's a market-wide read, not a step in that strip's per-signal find→analyze→track→repair workflow): the **durable view of today's options-flow alerts**, which until now only chimed + toasted (miss the toast and the alert was gone; the only trace was the Opportunity Board's per-symbol count). Pure Tier-1 reader of **`cache:options:flow_alerts`** (`webgui/pages/options/flow.py`) — **no new service, command, or cache key** — version-polling ~2 s: a chronological table **newest first** (the service appends oldest-first) — Time (CT) / **Age** / Symbol / Type / Side / Detail / Alert — over the three detector types (**Crossover** premium-lead flip · **Unusual activity** contract vol-vs-OI · **Gamma flip** spot crossing the dealer flip), with per-type `alert_detail` cells and rows tinted from a finite `(type, side)` → Tailwind class map bound via `:class` (Tailwind-first, no `:style`). Kind + symbol filters run **client-side** over already-read rows, so toggling is instant. **ONE 2 s timer serves two cadences**: the payload is re-read only when the cache VERSION moves, while the **Age** column recomputes against the rows already on screen — age stays live without churning the table. **Row click → Dealer Positioning for that symbol** (`handoff.send_to_gamma` + a one-shot stash consumed at `gamma.render()`'s build-time symbol sync, which already sets the dropdown BEFORE wiring `on_value_change` — so the handed symbol beats the cached one without a spurious refresh, then one explicit `_request_refresh()` moves the snapshot to it). **Two Tier-2 lines came with it** (both confirmed against the live key, which held exactly 50 alerts of which only 18 had a timestamp): `_FLOW_ALERTS_MAX` **50 → 300** (50 dropped the morning's alerts on a busy day) and a **`ts` stamped on UOA alerts** in the drain loop — `flow_alerts.detect_uoa` never emitted one, so unusual-activity alerts had **no time at all** while crossover/gamma_flip did. ⚠ UOA timestamps appear only on alerts published AFTER an `options_svc` restart; older rows legitimately render a blank Time. **Today only**, resets overnight; no badge, no history, and the toast/chime/phone-push/Settings toggle are unchanged) | built |
| `/options/paper` | Paper Ledger (ledger table + shared detail panel. **Live unrealized P&L** — `compute.paper_trades_view(reprice=True)` reprices OPEN ledger trades via `signal_repricer` (per-spread × qty), **market-hours gated**, on reload + the 5-min manage tick; the **P&L** column shows realized (closed) or live unrealized (open), **2-decimals + green/red colored**; **Credit/Risk** show 2-decimals; headers are **Credit / Risk / P&L** (no `$`); **newest-first** default sort; **Delete / Delete-all-closed buttons are red** (needed `color=None` so `.pt-danger` beats Quasar's `bg-primary`); the **Analyze** button pops a **descriptive dialog** (verdict + rationale + unrealized P&L / % / current price / DTE / target / breakeven + close X) — `compute.analyze_paper` enriched with `rationale` + `metrics`; row-click analyses update the detail panel silently. Detail panel: the **speedometer falls back to PoP** for paper trades (was stuck at 0 — no stored composite score) and the "Underlying" label is now **"Current price"**) | built |
| `/options/captured` | Captured Signals | built |
| `/options/portfolio` | Paper Account (the engine's paper account) | built |
| `/options/calculator` | Calculator (summary tiles + P&L heatmap — grid rows = **±N real chain strikes around spot** via the **Number of strikes** input (default 24, strictly around spot; `strikes_window`→`price_rows`); **intraday time-to-expiry** — the grid's first column is **"Now"** (current mark-to-market value, priced at calendar hours-to-4pm-ET /365) and the last is **"Exp"** (expiration payoff), fixing 0DTE which previously showed only the payoff everywhere; summary tiles + PoP also use the intraday "Now" T (was an `or 1/365` clamp that over-priced 0DTE ~20×); the **IV** button **implies IV from the traded contract's mark** ThinkorSwim-style via a `calc_iv` command → `cache:options:calc_iv` (`compute.calc_iv` → engine `implied_vol` bisection), falling back to ATM chain `volatility` pre-strike-pick; **multi-leg strategy builder** — a Strategy dropdown (singles + verticals credit/debit + condors iron/all-same + butterflies long/iron + calendars/diagonals) over the shared **editable leg-editor** (`leg_editor.py`: per-leg kind/side/strike/expiry/qty + Add/Remove), per-leg expiry so **calendars** price each leg at its own T, a **generic-numeric summary** for non-PCS/CCS/IC structures, and a **Copy to Simulator** button; **persists full UI state across navigation** (symbol/strategy/legs/fields/Number-of-strikes) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **Loads on tab-out (`focusout`) / Enter** (deduped via `inputs.should_load`; the Load button still force-reloads) with a **centered full-screen wait overlay** (`overlay.py`, `LOAD_TIMEOUT_SEC=30s` backstop) until the chain lands; the **top-level Expiry propagates to all legs** (`leg_editor.apply_expiry`, re-syncs strikes); **compact leg cells** (`leg-row`) + the **"Actions" header dropped**; **Send-to-Calculator from the Scanner now lands correctly** — `_prefill` stashes `pending_legs` + `load_symbol()` so the legs apply once the chain is loaded (applying them first wiped every strike via the leg-editor's strike-coercion — see [[calculator-leg-transfer-needs-chain-first]])) | built |
| `/options/swing` | Strategy Finder (**multi-strategy**, single-symbol: builds + ranks candidates across **Directional** (long/naked call+put), **Spreads** (debit bull-call/bear-put + credit PCS/CCS), and **Neutral** (iron condor) families on ONE unified **0–100 Fit+Quality** score; **Diagonals** are a later phase. The scanner **infers a market view** (direction/conviction + IV vol-regime) from the symbol's technicals + IV and ranks each structure by FIT to that view + STRUCTURAL QUALITY — so a long call and a put-credit-spread are comparable. A **Strategy-families multiselect** (default all; empty ⇒ all) + an inferred-**view banner** + strategy-agnostic columns (Strategy/Bias/Legs/Debit-Credit/Max P/Max L/R:R/PoP/BE/Score/Grade, colored by score+bias; the **Grade is quality-gated** — color-coded green/amber/red with a `grade_reason` tooltip, driven by structural quality + per-family hard gates, NOT view-fit). **Since 2026-08-06 the SERVICE only emits non-Weak candidates scoring ≥ 50, across every family** (`compute.SWING_MIN_SCORE` / `SWING_EXCLUDED_GRADES`, cut before `assign_ids`), so Weak rows no longer reach the table at all and the status line reports **"N below the quality bar"** (`swing.status_text` off the payload's additive `filtered_out`) — that count is what tells an all-cut scan apart from a scan that found nothing. Per-row **Send to Calculator / Expected Move** work for ALL types via the canonical `legs`; **Send to Paper** works for credit structures (PCS/CCS/IC) **AND defined-risk debit structures (LONG_CALL/LONG_PUT/BULL_CALL/BEAR_PUT)** as of 2026-07-13 (naked shorts excluded — undefined risk; see the "Last updated" entry). See the "Multi-strategy Swing Scanner" section below) | built |
| `/options/gamma` | Dealer Positioning (GEX/Charm/DEX/Vanna bars + flip/**single Call+Put walls** + intraday heatmap; **fixed ±20-strike window** around spot for bars+heatmap (`strikes_around`, consistent candle/cell size all day; heatmap cropped to the window; the visible strikes are first resampled onto an EVEN ladder by `gamma.uniform_strike_grid` and `rowsize` is that ladder's step — the median gap alone striped **$NDX**, the one symbol quoting mixed 5/10-wide strikes, see the gotchas); **blended interpolated heatmaps** (intraday **and Term**) — smooth image, no lines, dark `HEAT_STOPS` colorscale (zero→transparent like the candlestick chart), transparent bg, off-white spot line, no fade, **press-and-hold tooltip** (`_HEAT_PRESS_TOOLTIP_JS`); bar/heatmap **width split grows with session** snapshot count; **flicker-free** in-place Highcharts updates; **symbol is a dropdown** — default `$SPX`, populated from the collected universe (watchlist minus `$VIX`) via `cache:options:gamma_symbols`, **syncs to the cached symbol on build + selecting auto-refreshes + repaints ignore foreign-symbol snapshots** (no revert to `$SPX`); Term shows the **next 5 expirations regardless of cadence** (`_term_chain`) and draws a **1px hairline between each expiry column** (`expiry_separators` → xAxis plotLines at `i+0.5`, `rgba(255,255,255,0.22)`) — interpolation blends Term the same way it blends intraday, but its x axis is DAYS, so the blending smears one expiration's exposure into the next when nothing varies continuously between them; **pre/post-market persistence (2026-07-11)** — the charts show the most-recent-available session 24/7 with NO overnight blanking: the by-strike bars come from the live chain (which returns data off-hours) and the heatmap from `active_session_date` (the PRIOR session premarket, flipping to today once the 08:00 CT collection starts) + `load_date_with_grid`; off-hours `spot=None` degrades gracefully; **RTH-ONLY display window (2026-07-28)** — the heatmap **and** the Flow chart plot only **08:30–15:00 CT** (`compute._rth_bounds`/`_rth_only`) while collection stays 08:00–15:20, and `_display_session_date` shows the PRIOR session during the 08:00–08:30 pre-open gap so they're never blank; the **flip + call/put wall levels run ACROSS the heatmap** as yAxis plotLines (`wall_plot_lines`, always-emitted key so an in-place update can't leave the previous view's lines behind; Spot is deliberately omitted — it's already a moving series); an optional **"Level movement"** switch (`app_settings.gamma_level_tracks`, **off by default**) overlays the intraday MOVEMENT of the flip + walls as **step** lines (`compute._level_track` recomputes walls per snapshot from its own grid — the stored `top_pos/top_neg` columns are a different metric and disagreed on 383/383 live rows; runs before `_crop_gamma_views`; GEX/DEX walls only, Charm/Vanna flip-only; the 3 series are always emitted so the count stays fixed); a dashed amber **"Proj. flip"** level on **all four views** (`compute.gamma_snapshot` → `projected_flip`) — where the DEX curve crosses zero once each strike's OWN 0-DTE charm drift is applied; it is one 0-DTE DELTA level drawn everywhere as a shared reference, and the gap between it and the actual flip IS the hedging drift in price (None whenever the nearest expiry isn't today); a compact signed-column **0-DTE hedge-pressure panel** directly UNDER the heatmap sharing its time categories (`hedge_figure`/`hedge_summary_text` over the snapshot's `hedge_history`, plotted in **$B** and colored per point by sign — green = dealers must BUY into the close, red = SELL; its OWN element because pressure is in DOLLARS while the heatmap's y-axis is STRIKE, and hidden wherever the heatmap is); the by-strike bars overlay a **"Projected close"** OUTLINE (transparent fill + amber border, `grouping:False` so it overlays rather than halving the bar width) showing each strike's net after its own drift — an outline, not a ghost bar behind, so it reads whether the projection extends PAST the bar or pulls BACK inside it, and only on strikes that actually hold 0-DTE interest; a collapsed **"How to read the 0-DTE close projection"** expander under the status strip renders `page_help.PROJECTION_HELP_MD` — the SAME text as the nav hover guide, mounted on the page because that tooltip is `pointer-events:none` and Quasar sizes it to the space under its nav item (measured: ~466px of a ~1400px guide), so long-form help parked there alone is unreachable, not merely below the fold; a **Spot** picker (2026-07-28) draws the price overlay as **Line / Candles / OHLC** with a **Bar** picker for the 1/5/15-min bucket (`app_settings.gamma_spot_style` / `gamma_spot_interval`; bar size hidden for the line). Bars are DERIVED from the 1-min spot samples (`ohlc_bars` — open carried from the prior close; **wicks understate the true intra-minute range**) and drawn as CORE `columnrange` + `errorbar` with per-point up/down colors (`candle_points`) because the stock module's candlestick/ohlc series would break this chart's in-place update — see the gotchas. Total heatmap series is **fixed at 9**; a **Flow** view (inserted before Term) charts the symbol's intraday **price** + daily-cumulative **call/put premium ($M)** + a **net-premium (call−put)** signed panel from the snapshot's `flow` series (`flow_figure`/`flow_summary_text`; premium is mid-based, unsigned, forward-only); a **Net Prem** view (2026-08-05, between Flow and Term) plots **net premium (call$ − put$)** for any combination of **28 symbols** in three groups (Indices & Broad / SPDR Sectors / Mega-caps) — the group tab only FILTERS the checkboxes, the **selection persists across tabs**, each symbol keeps a **fixed colour**, and a **Dollars ($M) / Skew %** picker rescales the axis because the magnitudes span four orders (live: SPY −$375M beside DIA +$0.1M → −46.6% / +2.5% in skew); reads `cache:options:net_premium` (published on the 1-min GEX branch) and **filters client-side** so toggles are instant, with `net_prem_status_text` telling "not collected yet" apart from "the publisher is failing" (see the 2026-08-05 entry); Explain works per-selected-symbol; **Analyze** calls Claude (forced `submit_analysis` tool) and opens an **infographic** tab — regime + bias gauge, per-index price-level ladder + tiles + **what-if** (rally/sell-off/chop), bottom **"Why is this happening"**; **code-authoritative 1-day Exp. move**; also **auto-runs 4×/day** (premarket / ~18 min after open / midday / close) into per-slot keys with **Auto briefings** buttons + a **History picker** (date + slot dropdown → a report regenerated from the persisted briefing history at `/options/gamma-history`) — see the "Gamma Analyze" section below) | built |
| `/options/simulator` | Simulator (**Replay / What-if / IV-shock as SUBTABS under the main strip** + **Controls+Strategy merged side-by-side in one card** (2026-07-11); **multi-leg strategy builder** — a Strategy dropdown over the shared **editable leg-editor** (`leg_editor.py`) replaces the old single-contract selector — driving all three legacy tabs: **Replay** (re-prices the **netted** position along the underlying's recent path → stacked price + 5-Greek panels over a gap-compressed integer x-axis w/ a client-side scrub cursor) + What-if (a **dollar profit/loss payoff from entry**: P/L = position value (×100 contract multiplier) minus the **entry mark** (`whatif_baseline` = value at spot *now*) — so profit caps at the net credit, loss floors at width−credit, **matching the Calculator** — with a green profit fill above / red loss fill below breakeven (area `threshold:0` + `color`/`negativeColor`) + faint Profit/Loss washes + labels; Δt is **elapsed** days from now, per-leg decay → **calendars** correct, theta visible as Δt slides) + IV-shock; **Copy to Calculator** button; **dark-navy dashboard theme** via shared `theme.py`; **persists full UI state across navigation** (symbol/strategy/legs/sliders/active tab) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **Fetches the snapshot on tab-out (`focusout`) / Enter** (deduped) with the same **centered wait overlay** (`overlay.py`) until the meta lands; **compact leg cells** + no "Actions" header (shared `leg_editor`)) | built |
| `/options/expected-move` | Expected Move (candlestick price history (6-mo daily) + forward **ATM-IV expected-move cone** to the option's expiration (green/red dashed, √-time fan) + leg **strike lines** (short solid / long dashed, put/call colored) + axis **crosshair** w/ Date(X)+Price(Y) label boxes; opened in a **new browser tab** via stash-handoff from Scanner/Paper/Captured/Calculator, or standalone. **Expiry + strike are chain-driven DROPDOWNS since 2026-08-12** (was free text, where a typo silently produced "No ATM IV for …"): typing a symbol (tab-out/Enter via the shared `bind_symbol_load`) enqueues a new **`em_chain`** command → **`cache:options:em_chain`** (`compute.em_chain_meta`, today→+90d chain reduced SERVICE-side to `{expirations, strikes{expiry: ladder}, spot}` — measured 10.5 MB raw for a 90-day SPY chain vs **28.8 KB** of ladders, which is why this does NOT publish the raw chain the way the Calculator's `calc_chain` does); the expiry list carries a **DTE suffix** (`2026-08-12  (0d)`) so weeklies stay scannable, and strikes are **deduped across call+put** (put-vs-call is the toggle's job, so the ladder doesn't change under it). Picking an expiry redraws; picking a strike/put-call is a **LOCAL-only** repaint via `expected_move_figure(..., legs=…)` — no round trip, since the strike is only a plotLine. **Also since 2026-08-12 the CURRENT-DAY candle is drawn**: Schwab's `periodType=year&period=1` daily history ends at the PREVIOUS trading day, so `compute.today_candle` synthesizes the forming bar from the RAW quote (`schwab_py_client.get_quotes` — the normalized `get_quote` drops `openPrice`; the normalized client stays as a spot FALLBACK), gated on a trading day at/after the 08:30 CT open (premarket `openPrice` is still the prior session's) and no-op'd if the history ever includes today. Schwab's daily-candle epoch is **midnight CT** (verified live), so `_RTH_START`/`_PROJ_CT_TZ` are reused rather than host-local time. This also fixed the cone, which anchored at `candles[-1][0]` (yesterday) while sized from TODAY's spot and so overshot the expiry by a day. ⚠ after 15:00 CT the bar's close is `lastPrice`, which includes post-market prints, and Schwab's high/low may include extended hours — sub-tick on a 6-month chart, documented not fixed. Three page-state traps are commented in `render()`: `state["drawn_symbol"]` forces a redraw on a symbol switch that KEEPS the same expiry string (a shared monthly makes the `.value` write a no-op, so `on_value_change` never fires and the chart would pair the old symbol's candles with the new symbol's ladder), `state["strike_touched"]` keeps a look-back change from reverting a locally-picked strike while still letting an UNTOUCHED multi-leg handoff resend its own legs, and `state["seeding"]` must wrap `.update()` (not just the `.value=` write) because `ChoiceElement._update_options` re-validates and can re-null the value). **⚠ This page's IV + Expected move DELIBERATELY do not match ThinkorSwim, and the difference was measured, not guessed (2026-08-12, PLTR 2026-10-16, 65 DTE) — do NOT "fix" either number to match ToS without first deciding which definition you want.** TWO independent differences that push OPPOSITE ways: (1) **IV source** — `atm_iv_from_chain` reads the single strike nearest spot, which on an equity smile is its **MINIMUM** (measured: 46.08% at K=165, **45.59% at K=170≈spot**, 49.03% at K=175, 48.79% at K=145), while ToS publishes a per-SERIES IV aggregated across strikes and so necessarily sits above the ATM trough (52.11%). Schwab reports the SAME `volatility` for the ATM call and put, so put/call skew is NOT a factor — that's a dead end, don't re-investigate it. (2) **Move definition** — ours is **1 standard deviation** `S·σ·√(t/365)` (a 68% containment band, the correct basis for a *cone*); ToS's chain-header parenthetical is the **expected ABSOLUTE move**, smaller by exactly **√(2/π) ≈ 0.798**, which is what an ATM straddle prices. Reconciliation: 1σ at our IV = **32.90** (what we show) · 1σ at ToS's IV = 37.61 · abs-move at ToS's IV = **30.01** vs ToS's displayed **30.433** (1.4% off, = spot drift between the two readings). **The trap:** the two differences NEARLY CANCEL here (32.90 vs 30.43, ~8%), which is luck, not calibration — on a symbol with a flatter smile our IV would approach ToS's and our move would then read ~25% LARGER. The same `atm_iv` also sizes the drawn cone, so changing the definition changes the chart, not just the text line. The actual ATM straddle mark was 27.25 (real market price, model-free) if a third reference is ever wanted | built |
| `/options/rescue` | Rescue (last tab of the Options strip; bare dense table, no wrapper cards since 2026-07-12; at-risk credit spreads (PCS/CCS/IC) → **at-risk table** (paper+captured, heat-colored) → select a position → ranked **commission-aware adjustment menu**: close / partial-close / narrow / convert-IC / butterfly / roll-down/out/down-out / broken-wing / inverted / futures-hedge; each card shows gross/commission/net + metrics + legs + rationale + strategic context + warnings + score; execute cards have **Apply → confirm → `rescue_apply`** behind a stale-price guard, advisory cards show "manual"; nav badge from `cache:options:rescue_summary`) | built |
| `/sentiment` | Sentiment — nav group **Market Trend & Sentiment** since 2026-07-11 (three-column top: a **Market Sentiment ring** + a **Market Trend ring** + the **Signals** tile stack. **Since 2026-08-14 the four semicircular Highcharts gauges are TWO concentric SVG rings**, each carrying **Day / Week / Month** on one dial — `webgui/pages/rings.py:ring_svg`, mounted with `ui.html` and updated via `el.content`; the Sentiment ring's arcs are the live composite / 5-session mean / full-history mean (`sentiment_arcs`), the Trend ring's are `derived.trend` / `derived.trend_7d` / `derived.trend_30d_ago` (`trend_arcs`). **A horizon with no usable reading draws its track only + an em-dash** — the thing a needle structurally cannot say; see the ring-graphics section below for why that keys on CONFIDENCE, not key presence. **The Today trend reading's state label + regime badge show the FIVE-STATE (direction × aggression) vocabulary** — short labels **Bull / Weak Bull / Neutral / Resilient / Bear**, badge label+description e.g. "Lack of Bearishness — Refuses to drop, puts cheap/undefended — favor PCS" — and the press-and-hold **TREND DETAIL popup gained a "Why" evidence section** (direction/effort/skew/flow/session/rejection/profile/order-flow/option-flow/aggression lines). The **0–100 arc value is unchanged** (still the direction score); the **structural Week/Month arcs deliberately KEEP the old band vocabulary** (structural read = no aggression axis), so the panel carries both. See the root five-state entry above. / component table; the **Signals column is a 1×4 vertical stack of glowing tiles** (BIAS / SIGNAL / YESTERDAY / CHANGE, each icon + letter-spaced label + neon `text-shadow` value + hairline-and-dot rule + footer descriptor), with the service's **velocity + divergence lines restored beneath it**; a **"Market Regime"** expander (2026-07-23) = the blended STRUCTURAL read — committed label + confidence, a **transition line** ("Balanced → Rallying · 60%", hidden when stable), the classifier's evidence chips, and a **percent-stacked area chart** of today's membership mix (one band per regime, fixed order, plain chart + synthetic contiguous axis; reads `cache:sentiment:regime` + `:regime_history` on their OWN 5-min-cadence version probe; "Waiting for regime…" when nothing is published, "Unclear" when the evidence is genuinely weak) — see the root Market Regime entry; collapsed **"Daily Sentiment & Trend"** expander = two value-colorized (green/yellow/red) **2-min intraday graphs** (Daily Market Sentiment 0–10 + Daily Market Trend 0–100), rolling **last 5 trading days**, session gaps collapsed, **recorded going forward** by `sentiment_svc` (RTH-gated) into `SENTIMENT_INTRADAY_DB` → `cache:sentiment:intraday_history` (replaced the old 30-day-history line + rolling-avg/velocity/divergence text) — **expanded by default since 2026-07-12**; bottom status bar; **persists across navigation**; **server-side 120s auto-refresh + bridge publish, tab-independent**. **Since 2026-07-12** the Sector & Industry table, Sector Rotation, and the RRG chart are SEPARATE tabs (below) — this page still reads `cache:sentiment:sectors` only to fill the Components popup's Rotation/Sector-Value cells) | built |
| `/sentiment/sectors` | Sector & Industry (NEW tab 2026-07-12, `pages.sentiment_sectors`, inserted between Sentiment and Sector Rotation): the **Sector & Industry Performance** table lifted out of `/sentiment` — Day/Week/Month %, P/C, RRG quadrant, rotation banner, cap-weighted summary line, **expandable industries w/ P/C+RRG**; Refresh / Expand All / Collapse All. Tier-3 reader of `cache:sentiment:sectors`; **reuses the PURE builders from `pages.sentiment`** (`sector_table_rows`/`sector_summary`/`rotation_banner`/`industry_rows` + color helpers) so the display logic + its tests stay single-source) | built |
| `/sentiment/rotation` | Sector Rotation (RRG-vs-SPY assessment: Risk-ON/OFF headline + spread; **top row** = quadrant-map table (left) + tight ROTATING FROM/INTO w/ S&P weights (right). **Since 2026-07-12 the RRG CHART moved to its own `/sentiment/rrg` tab** — this page is now the headline + quadrant map + rotating-from/into only; reuses `sector_rotation_assessment`; cached, **manual Refresh only**) | built |
| `/sentiment/rrg` | RRG (NEW tab 2026-07-12, `pages.sentiment_rrg`, last tab after Sector Rotation): the **full-width RRG** chart lifted out of `/sentiment/rotation` — Risk-ON/OFF headline for context + per-sector "meteor tails" (engine `assess_sector` retains a `tail` of `TAIL_LENGTH=12` RS-Ratio/RS-Mom points sampled every `TAIL_STRIDE=2` days; **one spline series per sector** = faded trail line + single bright head dot) with native Highcharts hover-isolation (`plotOptions.series.states.inactive`). Tier-3 reader of `cache:sentiment:rotation`; **reuses `rrg_scatter_figure`/`headline_parts` from `pages.sentiment_rotation`**; cached, **manual Refresh only**) | built |
| `/sentiment/momentum` | Momentum (NEW tab 2026-07-28, `pages.sentiment_momentum`, last tab in the Market Trend & Sentiment group): the **momentum cascade** — a regime-conditioned momentum score across **3 levels** (11 sectors, **70** industry ETFs, 311 stocks from the workbook's new **Stocks** tab). **Recomputed ONCE NIGHTLY** (`sentiment_svc scheduler.momentum_due`, 16:20 CT weekdays) — daily bars change once a day, so ~390 regressions on the 120 s tick would be waste. Tier-1 reader of **`cache:sentiment:momentum`** (`MomentumSnapshot`): a **regime banner** (favorable / neutral / **suppressed** = momentum-crash risk, plus the lookback that state implies — in `suppressed` the banner is the loud element and the leaderboard dims), a **quadrant scatter** (score x, acceleration y, series per sector; Leading / Improving / Weakening / Lagging — deliberately the SAME four names as the RRG tab, since both are 2x2 strength-vs-rate-of-change scatters in one nav group), a **rank ribbon** over recent sessions, and a **top/bottom-15 leaderboard** showing every component column + a 3-block sector/industry/stock **alignment** flag. Footer counts `excluded` symbols (liquidity / insufficient_bars / no_quote / duplicate_etf) with a hover listing them — how a renamed or delisted ticker becomes visible instead of silently vanishing. **NOT a sentiment component**: `scoring/__init__.WEIGHTS` and the bridge are untouched, by design | built |
| `/trade` | Trade Analyzer (nav label since 2026-07-11; on-demand single-symbol analysis: **Position (1–8wk)** + **Investor (months+)** Buy/Hold/Sell verdicts w/ score + top reasons + hard gates + expandable factor breakdown. The **Position** verdict is now a **backtested, IC-weighted cross-sectional factor model** (`swing_model.json` artifact → live `swing_model.py` scorer): the headline is the **validated** BUY/SELL/HOLD off a **calibration band** + an outcome line (percentile · expected fwd return / horizon · beat-SPY hit-rate) + a **"Why — validated factors"** evidence expander (per-factor z/weight/contribution/IC + model version & OOS IC), with the **legacy heuristic** verdict tucked into a collapsed expander (Investor unchanged); **MTF EMA alignment** (per-timeframe); momentum strip (RSI/ADX/MACD/VWAP/RelVol); sector strength; **Fundamentals card** (P/E/PEG/growth/ROE/margins via proxy `/instruments`); **Markov Forecast card** (third **equal-width frame in the verdict row**, alongside Position + Investor: 5-band composite-score Markov chain → stacked-area band-probability forecast + P(BUY)/P(SELL)/E[score] at 5/10/20d + a bounded confidence-weighted drift-tilt `markov_adjusted_score` headline, verdict label unchanged; **chart plots the dense near-term `trajectory` now/1/2/3/5/10/20d** so it differs by score — the 5/10/20d tail converges to the bull-leaning prior stationary; chart is dark-navy themed); **dark-navy "dashboard" theme** (`.calc-v2` via shared `theme.py`, `items-start` compact cards); **tab-out (`focusout`) = Analyze** (deduped); **persists last analyzed symbol** + analysis across nav. **Deep Dive + AI Query buttons (2026-08-04)** run the migrated **EquityDeepDive** engine (`services/trade_svc/deepdive/`) for the current symbol: **Deep Dive** opens a self-contained HTML report (technicals + fundamentals/short-interest + options analytics [ATM IV, implied move, max pain, 25Δ skew, IV term structure, cm30 IV, net GEX/flip, OI walls] + IV/RV rank) in a new tab via `/trade/deepdive`; **AI Query** opens a copyable chat-prompt (digest injected, no API call) via `/trade/deepdive-query`. On-demand IV history (`repo_paths.IV_HISTORY_DB`), IV rank "building" until snapshots accrue; `ai_analyst.py` NOT migrated — see the 2026-08-04 Last-updated entry) | built |
| `/driver` | Claude Trades (nav label since 2026-07-11; **autonomous monitor + override** [level B]: a **Claude decision layer** (Opus 4.8 default; `DRIVER_MODEL` env / `shared/driver_model.txt` override → e.g. Sonnet 5) auto-selects/sizes **defined-risk option spreads (PCS/CCS/IC) from the scanner** (`cache:options:scan`) toward **net $500/day** in **paper**, gated by a **`cache:driver:control`** master switch + confirm-gated **STOP** kill-switch; the page shows day-P&L-vs-$500 progress, open-driver-positions, a newest-first **decision-log** audit (`cache:driver:autonomous`, times in **CST**), and a **Performance scorecard** (win-rate / profit-factor / avg win-loss / P&L by symbol & strategy — `cache:options:driver_paper_perf`), all reading the Driver's **own isolated paper book** (`cache:options:driver_paper_account`, separate from the manual account), with **Enable/Disable** + **Run now**; 09:28-ET morning + 30-min autonomous **entry-window** checkpoints (**09:45–15:30 ET** — the open's first ~15 min skipped so the post-open structure is readable, and **no NEW entries in the last 30 min before the close**; management/exits are unaffected, on options_svc's separate 5-min manage cycle) run `build_packet`→`decider.decide`→**`guardrails.apply_guardrails`** (PURE code clamps size + halts at banked-$500/loss-cap/VIX — the model never sizes its own risk)→`cmd:options` **`driver_paper_create`** (opens into the dedicated `paper_account_driver.db`, repriced + auto-exited on the 5-min manage tick — fully separate from the user's manual paper trades). A **Performance** view shows the driver's **closed trades + realized P&L** from its isolated paper account (`cache:options:driver_paper_account['closed_positions']` — reader-friendly columns Closed/Symbol/Strategy/Qty/Exit-reason/Realized-P&L, colored, newest-first, updated every 5-min manage cycle + the 2s version-poll; a **Refresh** button forces a `driver_paper_manage` reprice). **The legacy morning-agent order-approval queue + its `claude-driver` engine were REMOVED (2026-07-08)** — the page is now purely the autonomous monitor + this Performance view. Orders simulated (`PAPER_TRADE=True`). **Root-cause fix (2026-06-27): the driver had NEVER opened a position** — `compute.open_driver_position` read `signal_id`/`strategy`/`entry_credit` but the driver feeds RAW scanner signals keyed `id`/`type`/`credit`, so every open `KeyError`'d on `'signal_id'` and the defensive `try/except` swallowed it to `status=error`; the decision log showed "executed" (only the ENQUEUE) while the account stayed empty. Fixed by normalizing the signal shape — open positions now appear + the scorecard P&L populates. See [[driver-feeds-raw-scanner-signal-shape]]. **Second root-cause fix (2026-07-02): $SPX/MU logged "Executed" but never opened** — a **100× units mismatch**: `guardrails.clamp_quantity` sized affordability off the scanner's **PER-SHARE** `max_loss` (~$7) while the paper account's `size_contracts` correctly used **per-CONTRACT** dollars (`(width−credit)×100`, ~$705), so the driver kept proposing $SPX/MU whose real per-contract risk ($409–$1,833) exceeded the paper sizer's $250 cap → `RISK_TOO_HIGH` → **silently rejected** (the "Executed" in the log is only the ENQUEUE; the true outcome is in the account view's `last_open_results`, cap 25). Fixed: the guardrail evaluates **per-contract dollars** (`CONTRACT_MULTIPLIER`); the driver's caps raised to **$1,500/$4,500** and the paper open path given its own **`_DRIVER_MAX_RISK_PER_TRADE=$1,500`** (manual account unchanged at $250) — $SPX/MU now open. See [[driver-executed-but-rejected-risk-too-high]]. **Market-context block (2026-07-08):** the decider's
packet now carries an additive **`market_read`** — per-index gamma **flip/walls/what-if** from the
freshest `gamma_analyze` briefing + a **live spot** (spot-vs-flip **posture**), dashboard **breadth +
risk-on/off**, and the **sentiment 0-10 score** — as **reasoning context only** (never filters the
menu; `guardrails.py` untouched — the wall-aware gate is deferred). Its one-line summary shows on each
decision-log row) | built |
| `/settings` | Settings (GUI prefs via `app_settings`: scanner **audio alert** on/off + sound + volume, only-during-market-hours, min-score-to-alert; desktop-notification toggle + permission grant + Test sound; ticker toggle/speed; **Appearance** — edits every `config/theme.toml` knob in-app (7 sections: palette / semantic / 3D buttons / gauge / charts / typography / menu; color pickers + text inputs, `theme.knob_label` humanized labels) with **Save** (comment-preserving `theme.save_theme_values`), **Save & restart web GUI** (reuses the Status page's windowless self-restart), and a confirm-gated **Reset to defaults**; **API usage** (2026-07-13) — outbound Schwab API-call counts Today / last 7 / last 30 days, read off-thread from the proxy's `GET /stats/api_calls`, **plus Claude (Anthropic) call counts** from the cross-tier `shared/anthropic_counter.py` store (`shared/data/anthropic_call_counts.db`, WAL — recorded immediately before every `messages.create` at the three call sites: driver decider / Gamma Analyze / market-ticker summary; services need a restart to start counting) (counted per actual HTTP request at the marketdata rate-limit chokepoint + the trader loop → per-day rows in `schwab-proxy/data/api_call_counts.db`, forward-only; requires a proxy restart to start counting); **Maintenance** (2026-07-13) — a confirm-gated **Vacuum GEX history DB** button (optional purge-first switch) that runs `tools/vacuum_gex.py` as a subprocess off-thread and prints the before→after size — the tool still refuses while the collector is active) | built |
| `/portfolio` | Portfolio (3-tier, `services/portfolio_svc` :8212: **Holdings / Sectors / Performance** tabs over the portfolio model — sector breakdown, vs-sector RS, since-purchase excess, benchmark over/under-weight, tailwind; **Performance** scorecard (return/capital/risk/entry grades + composite + ann. return + drawdown) with a per-position **advisory suggestions** detail pane; **live-streaming P&L** via the service's proxy SSE consumer republishing each tick; proxy/stream status bar; persists across nav) | built |
| `/eod` · `/eod/detail` | EOD Report (pure-webgui aggregator over `options:*` + `driver:*` caches. **Summary** = headline tiles + a **verbose Daily / Weekly(WTD) / MTD performance** block **per book** — the manual paper **ledger** (`options:paper_trades`) and the **Driver** account (`options:driver_paper_account`, incl. its new `closed_positions`) shown separately (realized P&L bucketed by **exit** date; opened/credit by **entry** date; a per-book now-line = equity/session-P&L/open-unrealized/open-count). **Detailed** = the same performance + **trade-type breakdowns** (by **strategy** PCS/CCS/IC, by **0-DTE/Swing**, by **status** Open/Closed/Expired) for each book + full trade/scanner/captured/driver tables. **Navigation**: a jump-link **TOC** + every section in a native **`<details>`** (collapsible, **no JS** — works in-app AND in the exported files). **Generate** snapshots the caches → standalone `summary.html` + `detail.html` archived under `webgui/data/eod/<date>/`; `/eod/file` serves them raw. Pure builders (`normalize_trades`/`period_buckets`/`breakdown_rows`/`performance_table_html`/`breakdown_table_html`/`toc`/`details_section`) unit-tested. Realized reads `$0`/`—` until trades close — by design, not a bug) | built |
| `/market` | Market Dashboard (3-tier, `services/market_svc` :8215: a live grid of ~48 macro tickers from `symbol_categories.csv`, grouped into a **framed panel per category** laid out macro→tape→rotation (Volatility/Options-Sentiment/Internals/Currency · Cash-Index/Futures/Broad-ETF/**Top 10** · Sector/Thematic/Factor/Fixed-Income/Crypto/Countries). Each **tile** shows symbol + description (hover tooltip) + last + net/%-change on a **semantic risk-on/off colored background** (green risk-on / red risk-off / grey no-data, intensity by magnitude) — **polarity-aware** (VIX/SKEW/put-call/TLT/UUP shade RED on up-moves). The **Top 10** frame (renamed from "Magnificent 7" on 2026-07-21) leads with a **composite `BIG10` tile** = the equal-weighted avg day %-move of its **10 members** (NVDA/MSFT/GOOGL/AMZN/META/AAPL/TSLA + AVGO/PLTR/AMD) + a breadth subline (e.g. "8/10 up"), colored by the avg (a `kind="basket"` tile whose members are also its 10 constituent tiles). **Per-symbol premium sublines (2026-07-21):** the SPX/NDX, SPY/DIA/QQQ/IWM and Top-10 tiles carry a small **call/put PREMIUM skew** line ("Call 37%"/"Put 11%", from `cache:options:matrix` rows' `call_prem`/`put_prem`), the BIG10 tile shows the **dollar-weighted net of its 10**, and **every tile is a fixed `min-h-[92px]` so a frame's tiles are all the same height** whether or not they have the subline. `market_svc` polls the proxy's raw `/quotes` on a **~2 s RTH cadence** (5 s off-hours — futures trade ~24h so off-hours stays snappy), normalizes change across INDEX/EQUITY/FUTURE, computes the `$ADVN-$DECN` breadth spread + the `MAG7` basket, and reads the app's own cap-weighted put/call from `cache:sentiment:composite` **+ the dollar-weighted call/put PREMIUM skew ("Net Prem" tile) from `cache:options:matrix`→`premium`** (added 2026-07-21; "Call 46%"/"Put 22%" + a net-$ subline, a money-weighted P/C over the ~45 collected symbols, NOT net buying) → publishes `cache:market:dashboard`; the page version-polls + **updates tiles in place** (no per-tick rebuild). **Four frames are LEADERBOARDS (2026-08-05)** — **Top 10**, **Sector SPDR**, **Thematic / Industry ETF** and **Countries** are emitted **ranked descending by day %-move** (`symbols.SORTED_CATEGORIES` + the pure `compute.rank_tiles`), with the **BIG10 composite PINNED leftmost** (it carries its members' average as its own `change_pct`, so it would otherwise sort into the middle of them) and no-data tiles last; **every other frame keeps its curated symbol-map order by design** (Broad-Market ETF's SPY/DIA/QQQ/IWM ordering, Volatility's VIX-then-tenors, etc. — that layout IS the information). The page mirrors the rank as a Tailwind flex **`order-N`** class (`market.order_class`), swapping the **tracked-previous** class in place, so a re-rank is one class swap and never rebuilds the board. **CSV→Schwab symbol map** handles the translations (`SPX`→`$SPX`, `VIX`→`$VIX`, `/ES[U26]`→`/ESU26`) + **equivalents for symbols Schwab can't quote** (`$DXY`→`UUP`; `$PCALL`/`$PCSP`→the sentiment cap-weighted P/C tile). See the "Market Dashboard" section below) | built |
| `/status` | System Status (pure-webgui health board: overall up/down banner + per-component cards probing **Memurai** PING, **schwab-proxy** `/health`, **Schwab Authorization** (OAuth token state, with an **Authorize** button → proxy `/auth`), the **six domain services** `/health` (incl. `market_svc` :8215), and **webgui** itself; plus a **published-data-freshness** table — each domain's cache version + age (incl. `market:dashboard`), flagging stale scheduled views; a **Restart button on every component card** (proxy + the six services + Memurai + the webgui itself, shown up or down) — proxy/services/webgui relaunch **windowlessly** via `tools\restart_one.bat` (`CREATE_NO_WINDOW` → hidden `pythonw`, logs to `logs\`), Memurai via `Restart-Service`; the auth card shows **Authorize** instead; off-thread sweep, auto-refresh 15 s + manual) | built |
| `/terminate` | Stop All Services (guarded "stop the whole local stack" page: red **Stop all services** button behind a confirm dialog → spawns `stop_all.bat` detached via `cmd /c start`, which kills the proxy + 6 services + this web app by listening port; **Memurai is left running**; the page goes unresponsive after confirm, by design) | built |

**Market Dashboard (`/market`) — DONE (2026-07-07).** A new **More → Market Dashboard**
page streaming a live grid of ~48 macro tickers (from `symbol_categories.csv`), grouped
into a **framed panel per category** and colored by **semantic risk-on/off market
condition**. Sixth Tier-2 service. Pieces:
- **New service `services/market_svc` (:8215, read-only).** A scheduler polls the proxy's
  **raw `/quotes`** endpoint (not `SchwabProxyClient.get_quotes`, which discards
  `assetMainType`/`futurePercentChange`) for all real symbols in ONE batched call on a
  **~3 s RTH cadence** (`scheduler.poll_interval`, 15 s off-hours, 60 s deep-weekend when
  futures are closed — Sat all day / Sun before the 17:00 CT reopen — NOT throttled harder
  off-hours because the equity-index futures trade ~24h Sun-Fri and are the main off-hours
  mover; the shared `_HOLIDAYS` gate drives it. **Cadence tuned 2026-08-02 from 2 s/5 s →
  3 s/15 s** — the dashboard updates tiles in place so the slower poll is imperceptible, and
  it roughly halved this service's Schwab `/quotes` volume, ~24k → ~12k calls/day, the stack's
  #2 Schwab caller after GEX collection), normalizes change across INDEX/EQUITY/FUTURE,
  computes the `$ADVN-$DECN` breadth spread, reads the app's own cap-weighted put/call, derives a per-tile
  `color_state`, and publishes **`cache:market:dashboard`** (`skip_unchanged=True`, so no
  repaint on byte-identical ticks). No command handler — the page only reads.
- **Frame ordering (2026-08-05).** `symbols.CATEGORY_ORDER` sets the frame layout; **within** a
  frame, `symbols.SORTED_CATEGORIES` (Top 10 · Sector SPDR · Thematic / Industry ETF · Countries)
  marks the four **leaderboard** frames whose tiles `compute.rank_tiles` orders **descending by day
  %-move**. Three bands: composite **baskets pinned first** (BIG10 — its `change_pct` is the members'
  average, so value-sorting would bury it among its own constituents), then quoted tiles by
  `-change_pct`, then **no-data / value-only tiles last**; the sort is **stable**, so equal movers
  keep symbol-map order rather than jittering poll to poll. The remaining frames are ranked
  **deliberately not** — their curated order is meaningful (SPY/DIA/QQQ/IWM before the equal-weights;
  VIX before its tenors) and a test pins that a big QQQ move must not reshuffle the broad ETFs. The
  page applies the rank as a Tailwind flex `order-N` class rather than moving DOM nodes, preserving
  the build-once / update-in-place property (see the `/market` route-table entry).
- **PURE modules.** `symbols.py` = the **CSV→Schwab symbol map** (single source of truth):
  69 tiles (USO joined the Thematic / Industry ETF frame 2026-08-12) with per-symbol
  **polarity** (`normal` up=risk-on / `inverted` up=risk-off) +
  `kind` (`quote`/`spread`/`external`), encoding the translations (`SPX`→`$SPX`, `VIX`→
  `$VIX`, `SKEW`→`$SKEW`, `/ES[U26]`→`/ESU26`, ToS `IMGTN:CGI`→API **`$MGTN`** [CBOE
  Magnificent Ten Index]) and the **equivalents for symbols Schwab
  can't quote** (`$DXY`→**`UUP`**; `$PCALL`+`$PCSP`→one **"Put/Call"** tile
  fed from `cache:sentiment:composite` → `live.sector_pcr`). Two `external` tiles now share
  the **Options Sentiment** frame: **Put/Call** (`source="sentiment_pcr"`) and **Net Prem**
  (`source="options_net_prem"`, added 2026-07-21) — the dollar-weighted call/put premium skew
  fed from `cache:options:matrix`→`premium` via `compute.read_net_prem` (`build_dashboard`
  branches the external kind on `e["source"]`). `classify.py` = pure
  `normalize_quote` (asset-type-aware % field), `spread_value` (`$ADVN-$DECN` = leg last
  diff, colored by SIGN not magnitude since a count isn't a %), and `color_state`
  (polarity × sign × intensity → 6 buckets +
  `no_data`). `compute.build_dashboard` is PURE over an already-fetched raw dict + pcr; the
  `SYMBOL_MAP` whitelist iteration means the proxy's `errors` bucket can never become a
  bogus tile.
- **Coloring (design decision — semantic, not literal up/down).** Green = risk-on, red =
  risk-off, grey = flat/no-data, intensity by magnitude. **Inverted** instruments shade RED
  on up-moves: VIX/VIX1D/VIX3M, SKEW, the put/call tile, `UUP` (dollar strength), `TLT`
  (long-duration flight-to-safety). Defensive equity sectors (XLP/XLU/XLV) stay **literal**
  up=green (deliberate). Contract `shared/contracts/market.py:MarketDashboard`.
- **Page `webgui/pages/market.py` (Tier-1, engine-free).** Reads `cache:market:dashboard`,
  paints framed category panels (macro→tape→rotation frame order) of colored tiles
  (symbol + description tooltip + last + net/%-change), version-polls, and **updates tiles
  IN PLACE** (build-once + `.classes(remove=…, add=…)` bg swap keyed by the unique display —
  no per-tick DOM rebuild). Tailwind-first (data-driven colors from a finite `_BG` map, no
  `.style()`). Wired into `MORE_CHILDREN` + `/market` route; surfaced on `/status` (health
  board + freshness) and killed by `/terminate` (`stop_all.py` iterates `SERVICE_PORTS`).
- **"Streamed" caveat.** Schwab's SSE streamer is equities-only (indices/internals/VIX have
  NO streaming service; futures would need a proxy `LEVELONE_FUTURES` bridge), so ~half the
  symbols are REST-only regardless — the honest uniform path is the ~2 s poll (visually
  continuous). **Launch:** `start_all.bat`/`start_all_wt.bat` launch it as the 8th window/tab.
  **Restart `market_svc` (+ the webgui to pick up the new route)** to see it live.
  market_svc **30** + shared/contracts **43** + webgui **687** green; **live-verified
  end-to-end** (real proxy+Redis → all 47 tiles populated with correct semantic colors, incl.
  UUP/put-call equivalents; VIX+3.6%→risk_off_strong, TLT−1.1%→risk_on_strong,
  $ADVN-$DECN=−465→risk_off_mild). Built subagent-by-subagent (TDD, two-stage spec+quality
  review per layer). Design/plan:
  [design](docs/plans/2026-07-07-market-dashboard-design.md) /
  [plan](docs/plans/2026-07-07-market-dashboard-plan.md).

**Market Summary Ticker (every page) — DONE (2026-07-08).** A fixed scrolling marquee pinned
to the **bottom of every page** (rendered in `main.py` `_layout`) that gives an at-a-glance
market read synthesized from the app's own data. **Hybrid content:** it leads with a short
**Claude-written verdict** (the "why", refreshed on a schedule) then scrolls **live,
color-coded data items** (the fast numbers). Pieces:
- **Narrative — Claude, scheduled (`market_svc`).** `compute.build_summary_packet` (PURE)
  distills the dashboard + sentiment/trend caches into a compact packet;
  `compute.generate_summary` calls Claude (Sonnet 5, thinking disabled, `max_tokens≈220`,
  client built with `timeout=30/max_retries=1`) for a 1–2 sentence verdict; the scheduler's
  `summary_due` gate (~20 min RTH / ~60 min off-hours) publishes **`cache:market:summary`**
  (`MarketSummary` contract, `skip_unchanged=True`). Reuses the Gamma-Analyze pattern (lazy
  `anthropic`, key via `ANTHROPIC_API_KEY`→`shared/anthropic_key.txt`) — fully defensive:
  no key / API error → empty narrative → the ticker shows live items only. **Test hygiene:**
  a market_svc `conftest` autouse fixture monkeypatches `_make_summary_client→None` so the
  suite NEVER makes a live Claude call.
- **Live items — rule-based, Tier-1 (`webgui/pages/ticker.py`).** PURE `ticker_items(dashboard,
  sentiment)` composes `{text, tone}` items (sentiment score/bias, trend label/score, breadth,
  VIX/VIX1D/VIX3M/SKEW, put/call, SPX/NDX, top-4 sector/thematic movers by |Δ|); `item_class`
  maps `tone`→fixed Tailwind class (Tailwind-first, no `.style()`). Zero API cost, updates live.
- **Render.** `render_ticker(active)` (called in `_layout`, gated by the Settings toggle) is a
  fixed `ui.footer` marquee — the `@keyframes` animation is the ONE `ui.add_css` escape hatch;
  scroll speed is a **finite `speed_class`** (slow/med/fast), not an inline style. A `@guard`ed
  version-gated `ui.timer` reads the three cache versions and **only rebuilds when the RENDERED
  content signature changes** (not on every 2 s dashboard bump) → the marquee scrolls smoothly
  without tearing/jumping. Content column gets `pb-10` so the footer never covers content; the
  page-help now lives on the nav tabs + drawer items as 2 s-delayed hover tooltips (`main._help_tooltip`, 2026-07-12 — the header "?" fab is gone).
- **Control.** `app_settings` `ticker_enabled` (default on) + `ticker_speed`; a **Settings**
  page toggle (Show + Slow/Medium/Fast). When off, `render_ticker` renders nothing.
- **The toggle also gates the Claude call (2026-07-14).** `ticker_enabled` used to be
  Tier-1 only, so switching the ticker off merely hid the marquee while market_svc kept
  generating (and paying for) the verdict — it was the stack's **biggest Claude caller**
  (~21 of ~39 calls/day). The toggle now writes through: `settings.apply_ticker_enabled`
  enqueues `enable_summary`/`disable_summary` on **`cmd:market`** (market_svc's FIRST
  command handler — `handlers.handle_command`, wired in `app.py`) → `set_summary_enabled`
  records **`cache:market:summary_enabled`** → the scheduler reads it each cycle
  (`handlers.summary_enabled`) and feeds `summary_due(..., enabled=…)`, which
  short-circuits. **Defaults to enabled** on a missing key / unreadable bus (the flag can
  only turn the verdict OFF explicitly), and `secs_since` keeps accumulating while off so
  re-enabling yields a fresh verdict at once. Because a wiped Memurai drops the key (→
  back to enabled), `main.sync_ticker_setting` re-asserts settings.json at **webgui
  startup** — registered **inside the `__main__` guard**, NOT at module scope: pages
  `import main` lazily at request time and the entry script runs as `__main__`, so a
  module-level `app.on_startup` re-registers after NiceGUI started → `RuntimeError` → every
  page 500s (learned the hard way; pinned by `test_shell.py`'s reimport probe).
  **`SUMMARY_RTH_SEC` 20 → 40 min** the same day (the live items refresh on the 2 s poll,
  so a slower narrative costs the reader little). Steady state ~39 → ~18 calls/day.
- market_svc **35** + shared/contracts **38** + webgui **687** green (no live API calls in the
  suite); **live-verified** end-to-end (real Claude verdict published to `cache:market:summary`
  + 14 correct color-coded live items from the live caches). **Restart `market_svc` + the
  webgui** to see it. Built subagent-by-subagent (TDD, two-stage spec+quality review). Design/plan:
  [design](docs/plans/2026-07-08-market-summary-ticker-design.md) /
  [plan](docs/plans/2026-07-08-market-summary-ticker-plan.md).

**Multi-strategy Swing Scanner (`/options/swing`) — Phase 1 DONE (2026-06-30).** The
Swing Scanner was expanded from a credit-spread-only premium scanner to a **unified,
single-symbol multi-strategy scanner** that builds + ranks candidate structures across
strategy families on **one comparable 0–100 score**. The crux: the legacy `scoring.py`
9-factor model is a *premium-seller's* score (it punishes long calls/debit spreads —
negative theta, low PoP, undefined R:R, wants to avoid the expected move), so the heart
of this feature is a **new unified Fit+Quality scorer** that makes a long call and a
put-credit-spread comparable. Architecture = **two new PURE engine modules** (in
`options-scanner/`, process-isolated so no `scoring` collision) feeding the existing
options-service swing path. Pieces:
- **`options-scanner/strategy_scanner.py`** (PURE builders + payoff economics): emits a
  **normalized signal** for each candidate — a canonical `legs` list
  (`{kind,side,strike,expiration,qty,mark,delta,theta,vega,gamma,iv}`) + payoff
  economics computed off the structure. `payoff_metrics(legs, spot)` derives
  net_debit/credit, max_profit/loss, breakevens, capital, R:R, net greeks, and an
  **analytic `unbounded` flag** from the call-tail coefficient (`Σ sign·qty` over CALL
  legs: >0 → unbounded profit / <0 → unbounded loss / ==0 → bounded; the downside is
  always bounded at S=0) — bounded extrema read at payoff BREAKPOINTS (`{0} ∪ strikes ∪
  far-high`), NOT a spot-relative grid (so a short put's true `strike−credit` max loss
  is correct). `pop_from_payoff` = normal-terminal probability of the profit region.
  Builders: `build_directional` (LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT, delta-targeted)
  + `build_debit_verticals` (BULL_CALL/BEAR_PUT). `adapt_credit_spread`/`adapt_iron_condor`
  normalize the existing `screen_spreads` PCS/CCS + `build_iron_condors` IC dicts into the
  same shape (source economics stay authoritative; structural keys filled from the legs,
  with a source-derived breakeven fallback when leg marks are absent).
- **`options-scanner/strategy_scoring.py`** (PURE): `infer_market_view(technicals,
  iv_analysis)` → `{direction (bullish/bearish/neutral), conviction 0..1, vol_regime
  (low/mid/high)}` from the REAL upstream keys (`trend` UPPERCASE incl.
  RECOVERING→bullish/WEAKENING→bearish, `rsi14`, `sma20`/`price`; `iv_rank` PRIMARY,
  `current_iv`/`hv_current` IV/HV ratio FALLBACK when iv_rank is None). The **two-part
  score**: **Thesis-Fit** = `fit_directional(net_delta, view)` (per-SHARE scale ~±0.5,
  tanh-clamped; a bullish structure scores high in a high-conviction bull view, a
  delta-neutral structure scores high only at low conviction) + `fit_vol(net_vega,
  vol_regime)` (long-vega fits LOW iv, short-vega fits HIGH); **Structural-Quality** =
  liquidity (`scoring.norm_liquidity` across legs) + R:R/capital-efficiency +
  breakeven-vs-EM + PoP. **Quality-gated grading (2026-06-30 — the grade reflects trade
  QUALITY, not view-fit):** `score_strategy` composite is **quality-dominant**
  (`0.7·quality + 0.3·fit`; fit is a ranking tiebreaker, no longer half the grade), and the
  **grade is capped by per-family HARD GATES** on liquidity, R:R (or capital-efficiency for
  naked shorts, whose R:R is undefined), and PoP — `GATE_BARS`/`gate_profile`/`evaluate_gates`.
  A trade that FAILS any minimum bar → **Weak** (composite capped ≤`GATE_FAIL_CAP`39) + a
  **`grade_reason`** naming the failed dims (e.g. "Fails: liquidity, PoP"); pass all mins →
  **Good** (≥`GOOD_MIN`58)/**Marginal**; pass the **excellent** bars on every gated dim +
  composite ≥`STRONG_MIN`78 → **Strong** (genuinely rare). **Since 2026-08-06 `compute.swing_scan`
  DROPS the Weak ones before publishing, so the Finder's table no longer renders a "Fails: …" row
  — the grade machinery still runs and now DECIDES the cut, and the count of dropped rows surfaces
  in the status line instead.** Bars are per-family (credit = high
  PoP/low R:R; long = low PoP/high R:R with unbounded-profit auto-passing reward; naked =
  capital-efficiency, so its low cap-eff keeps it below Strong by design). **Making the
  liquidity gate real** required carrying `bid`/`ask`/`volume`/`oi` onto the normalized legs
  (`strategy_scanner._leg_from` + the adapters' short legs; `scanner_engine.build_iron_condors`
  now forwards put-short + call-short `bid`/`ask`/`volume`/`call_*` so the IC liq gate isn't
  inert). `q_liq` degrades to 50 for a leg genuinely missing bid/ask (no false-fail). The page
  shows a **color-coded Grade** (Strong/Good→green, Marginal→amber, Weak→red via
  `strategy_table.grade_class`) with the `grade_reason` in a tooltip. `score_all` scores +
  sorts desc; all per-signal defensive. Design/plan:
  [design](docs/plans/2026-06-30-swing-quality-gated-grading-design.md) /
  [plan](docs/plans/2026-06-30-swing-quality-gated-grading.md).
- **`services/options_svc/compute.swing_scan`** now returns `{"signals", "view"}` and
  takes a `families` arg (None ⇒ all of DIRECTIONAL/VERTICAL/NEUTRAL). It keeps the
  existing fetch (chain/quote/spot/hist/tech/iv/dem), derives **`atm_iv` as a DECIMAL
  from the engine's authoritative dollar daily EM** (`atm_iv = dem·√365/spot`, sidesteps
  the percent/decimal trap — `run_iv_analysis.current_iv` is a PERCENT) + `em_1sd =
  dem·√dte_min`, infers the view, builds the selected families (`screen_spreads` run ONCE
  and shared between the VERTICAL credit set + the NEUTRAL iron condors), scores via
  `strategy_scoring.score_all`, and early-returns `{"signals": [], "view": {}}` on a
  missing chain. `strategy_scanner`/`strategy_scoring` are imported LAZILY (the documented
  cross-app `scoring`-collision discipline). The handler adds `families` to
  `_SWING_DEFAULTS` and caches `view` alongside `signals` under the unchanged
  `cache:options:swing`.
- **Page** `webgui/pages/options/swing.py` + PURE `strategy_table.py`: a **Strategy-families
  multiselect** (Directional/Spreads/Neutral; default all; empty ⇒ all, with an explicit
  notify), an inferred-**view banner** (`view_banner_text`), and strategy-agnostic
  **columns/rows** (`strategy_columns`/`strategy_rows` — Strategy/Bias/Legs/Debit-Credit/
  Max P/Max L/R:R/PoP/BE/Score/Grade, with `:class` finite-map coloring by score+bias,
  Tailwind-first). The legacy delta/credit gates moved into an **"Advanced — credit
  spreads"** expander (they only constrain PCS/CCS). Row-click feeds `detail_signal(sig)`
  (fills `credit`/`breakeven` from the normalized keys) to the shared detail panel.
  **Handoff** (`handoff.py`): `send_signal_to_calculator` + the extended
  `signal_to_em_payload` route the canonical `legs` to the Calculator / Expected-Move for
  ALL types (back-compatible with old spread dicts lacking `legs`); `add_strategy_row_actions`
  shows Paper-trade when `row._allow_paper` — credit spreads (PCS/CCS/IC) **plus the defined-risk
  debit structures (LONG_CALL/LONG_PUT/BULL_CALL/BEAR_PUT)** as of 2026-07-13 (the ledger grows a
  legs-based DEBIT trade; naked shorts stay excluded — undefined risk).
- **Scope:** single-symbol; Phase 1 = Directional + Verticals + Neutral(IC). **Phase 2**
  (condor/butterfly/iron-fly) + **Phase 3** (diagonals — multi-expiration) are planned in
  the plan doc. Built subagent-by-subagent (TDD, two-stage spec+quality review per unit +
  a final holistic review). Test counts: strategy_scanner **18** + strategy_scoring **35**
  + options_svc **313** + webgui **650** green. **Live-verified** end-to-end: the REAL
  `compute.swing_scan` against the live proxy (SPY + NVDA) produced `{signals, view}` with
  an inferred bearish view and bearish structures (LONG_PUT/BEAR_PUT) correctly ranked on
  top, all scored + sorted. Design/plan:
  [design](docs/plans/2026-06-30-multi-strategy-swing-scanner-design.md) /
  [plan](docs/plans/2026-06-30-multi-strategy-swing-scanner.md).

The `pages/options/` subpackage shares `header.py` (compact quotes/VIX/sentiment
strip), `detail.py` (collapsible Trade detail panel, reused by all signal
tables), `svg.py` (gradient-bar / range-marker SVG — the composite-score
speedometer is now the shared Highcharts gauge in `pages/gauge.py`), `inputs.py`
(`select_all_on_focus` + `should_load` symbol-input helpers — `should_load` dedups
the symbol tab-out/Enter Load trigger), **`overlay.py`** (the shared full-screen
**wait overlay** — `build_loading_overlay()` → a handle with `.show(msg)`/`.hide()`,
plus a shared `LOAD_TIMEOUT_SEC` backstop; both the Calculator + Simulator show it
centered while a symbol Loads/Fetches), **`strategies.py`** (PURE shared
strategy/leg model — the normalized leg dict + `STRATEGY_TEMPLATES`/`STRATEGY_GROUPS`
+ `build_default_legs` + analytic-vs-numeric `summary_code`; imported by **both** the
Calculator and Simulator so templates never drift), **`leg_editor.py`** (the shared
**editable multi-leg-table widget** both pages mount — `state['legs']` is the source
of truth, each page injects its own `strikes_for`/`expiries_for` + `show_premium`;
`apply_expiry(expiry)` propagates the Calculator's top-level Expiry to **all** legs;
the header table drops the "Actions" label and renders **compact `leg-row` cells**),
and **`handoff.py`** (cross-page
signal hand-off — Scanner/Swing "Send to Calculator" via a module-level `_pending`
stash + "Send to Paper trade" which enqueues a `paper_create` command on
`cmd:options`, plus the shared `add_row_actions` per-row action-button slot, **and the
Simulator↔Calculator leg-copy stashes** (`send_to_simulator`/`send_to_calculator_legs`
+ `take_pending_simulator`/`take_pending_calculator_legs`), **and the Flow-Alerts→Dealer-
Positioning symbol stash** (`send_to_gamma`/`take_pending_gamma`, one-shot — a symbol left
in the stash would silently re-hijack the gamma dropdown on the page's next build); engine-free),
**`strategy_menu.py`** (the shared cascading **Strategy picker** — a
`ui.select`-compatible button → nested family→variant Quasar submenu driven by
`strategies.STRATEGY_MENU`/`strategy_label`; both pages mount it so the picker
never drifts; `boxed=True` styles the trigger for the navy theme), and
**`theme.py`** (the shared dark-navy **"dashboard" theme** — now a vocabulary of
**Tailwind design-token constants** (`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/
`BTN_PRIMARY`/`STRATEGY_BTN`/`TXT_*`/`BTN_3D*`) applied via `.classes(CARD)`, plus the
slim **`QUASAR_INTERNAL_CSS`** escape-hatch the Calculator/Simulator/Trade inject for the
Quasar-internal DOM scoped under the `.calc-v2`/`.strategy-menu-btn`/`.leg-*` hooks
(filled navy input boxes, compact leg cells, dark transparent tabs, the teleported
`strat-menu-navy` popup); the legacy `DASHBOARD_CSS` string was **deleted in the
Tailwind-first migration** — see the "App theme — dark-navy" canonical section below), and
**`page_state.py`** (the shared PURE persistence helpers — `snapshot` /
`merge_restore` / `pick_seed` — both pages use to restore their full UI state across
navigation via a single-user module snapshot; see the route table). Options design + plan: [`docs/plans/2026-06-14-options-section-expansion-design.md`](docs/plans/2026-06-14-options-section-expansion-design.md)
/ [`-plan.md`](docs/plans/2026-06-14-options-section-expansion-plan.md).
Gamma/Simulator: [`docs/plans/2026-06-14-gamma-simulator-design.md`](docs/plans/2026-06-14-gamma-simulator-design.md) / [`-plan.md`](docs/plans/2026-06-14-gamma-simulator-plan.md).

**App theme — dark-navy "dashboard" (Tailwind-first; the canonical reference).**
The shared dark-navy look (page-scoped via the `.calc-v2` scope hook; promotable
app-wide) is now a set of **Tailwind design-token constants** in
**`webgui/pages/options/theme.py`** applied via `.classes(CARD)` etc., plus a slim
**`QUASAR_INTERNAL_CSS`** escape-hatch (the only `ui.add_css` a page injects) for the
Quasar/Highcharts-internal DOM that component `.classes()` can't reach (`q-field__control`,
the `leg-*` cells, the `q-tab*` chrome, the teleported `.strat-menu-navy` popup). The
**Calculator**, **Simulator**, and **Trade** pages all use this. **`DASHBOARD_CSS` is
deleted** (Phase 4) — `theme.py` = tokens + `QUASAR_INTERNAL_CSS`. **This section +
`theme.py` are the single source — look here to apply or change the theme.**
- **App identity — `[brand]` in `config/theme.toml` (2026-07-27).** The app NAME and the
  header lockup are config, not code: `name_a`/`name_b` (the wordmark's two halves, so
  each carries its own gradient — "Neural" gold / "Strike" blue), `font_family`/`font_url`/
  `font_weight` (the **wordmark-only** brand face — Montserrat ExtraBold, loaded SEPARATELY
  from `[typography].font_url` so the body/data font stays IBM Plex), the four gradient
  stops (**sampled from `webgui/static/img/neuralstrike-logo.jpg`**, not eyeballed), and
  `mark` (the monogram URL under `/static`; `""` = wordmark only). Consumed via
  `theme.BRAND_NAME`/`BRAND_CSS`/`BRAND_FONT_HEAD_HTML` + `main.brand_lockup_html()`/
  `brand_mark_src()` (the latter renders the image ONLY if the file really exists — no
  broken-image icon). Renaming the app = editing `name_a`/`name_b` + the launcher `.bat`
  titles. **Not** in Settings → Appearance (that editor's sections are single-kind;
  `[brand]` mixes colors with text). The wordmark rules are RAW CSS — gradients +
  `background-clip:text` are exactly what the Tailwind JIT won't emit.
- **Restyle WITHOUT code edits (2026-07-09): `config/theme.toml`.** Every color
  (`repo_paths.THEME_TOML`, all knobs commented in-file) — surfaces/cards/text,
  secondary+primary buttons, the **3D gradient buttons**, the semantic
  positive/warning/negative/neutral set, the **speedometer gauge face + needle**
  (`pages/gauge.py`), the Sentiment/Rotation chart palette (`sentiment.py CLR_*`),
  plus **`[typography]`** (app-wide font family + text-category sizes:
  titles/.text-h6 · subtitles/.text-subtitle1 · sections/.text-subtitle2 · body ·
  small/.text-xs+EYEBROW → `build_typography_css`, injected app-wide by
  `main._layout`) and **`[menu]`** (the application menu: `accent` → `ui.colors(
  primary=…)`, which reaches **only Quasar-colored controls** — switches, sliders,
  `color=primary` buttons — **NOT** the header bar (decoupled via `header_bg`) and
  **NOT** the active nav pill / tab fills / icon accent (hardcoded rgba in
  `main._NAV_CSS` — see the JIT gotcha below); `drawer_bg`/`text`/
  `hover_bg`/`title` emit override CSS via `build_nav_css` — every `[menu]` knob
  defaults `""` = stock look, no rule emitted) — is loaded ONCE at webgui startup
  (`theme.load_theme()` → `build_tokens`/`build_quasar_css`; missing
  file/keys/malformed values → the built-in defaults, never raises). **Edit the
  TOML → restart the webgui → hard-refresh.** NOT config-driven (deliberate):
  per-chart Highcharts colorscales (e.g. the Gamma heatmap), data-driven
  table-cell zone maps (score/heat/P&L), and the standalone EOD/Analyze report
  documents. **JIT gotcha (2026-07-09; NARROWED 2026-08-14):** the bundled
  Tailwind browser JIT does NOT generate an arbitrary class containing
  `var(...)` — the nav pill's old `bg-[var(--q-primary)]` silently produced no
  rule; it is now a plain `.nav-active` rule in `_NAV_CSS` with a **hardcoded
  rgba wash**, so it does **not** follow the `accent` knob (nor do the tab-strip
  fills or the active icon accent). Changing `accent` moves the Quasar controls
  only; to move the nav accents, edit `main._NAV_CSS` as well. **`rgba(...)` was
  wrongly caught by that ban until 2026-08-14** — this line read "`var(...)`
  **or `rgba(...)`**", which is overstated and cost a real workaround: probed
  live while building the Signals tiles, `shadow-[0_0_18px_-6px_rgba(…)]`
  generates fine (the Refresh button's shadow is a live example), as do
  `bg-gradient-to-b from-[#hex] to-[#hex]`, `[text-shadow:0_0_12px_#hex]` and
  `drop-shadow-[…]`. The limitation is **`var(...)`**, not parenthesized
  functions generally. Note `rgba()` must be written with **no spaces** (a
  Tailwind arbitrary value cannot contain them, and underscores are the escape),
  and a `box-shadow` arbitrary needs the **rgba form, not a hex**.
- **Apply to a new page:**
  ```python
  from pages.options.theme import QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, LABEL, BTN_PRIMARY
  ui.add_css(QUASAR_INTERNAL_CSS)
  with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):    # .calc-v2 = CSS scope hook
      ui.label("Title").classes(f"text-h6 {LABEL}")            # Tailwind token, not .style()
      with ui.column().classes(f"{CARD} w-full gap-3"):        # bordered navy panel
          ui.input("Symbol")                                    # auto-boxed (q-field)
          ui.button("Go", color=None).props("no-caps").classes(BTN_PRIMARY)
  ```
  Inputs / selects / tabs inside `.calc-v2` are auto-restyled by `QUASAR_INTERNAL_CSS`;
  **buttons need `color=None`** (drops Quasar's `bg-primary`) + a `BTN` / `BTN_PRIMARY` token.
  Reactive (repainted-in-place) label colors swap via `.classes(remove=<finite set>, add=…)`
  so repeated repaints don't stack conflicting `text-[…]` classes.
- **Token vocabulary** (`.classes(<TOKEN>)`, all in `theme.py`): `PAGE` navy radial-gradient
  page wrap · `CARD` bordered navy panel · `EYEBROW` small muted label · `LABEL` / `MUTED`
  text · `BTN` / `BTN_PRIMARY` secondary / primary button · `STRATEGY_BTN` boxed Strategy
  trigger box (applied alongside the `strategy-menu-btn` scope hook via
  `strategy_menu.build_strategy_menu(..., boxed=True)`) · `TXT_POS/TXT_WARN/TXT_NEG/TXT_NEUTRAL`
  semantic state text colors (+ `STATE_TEXT_CLASSES` for the reactive `remove=`) · `BTN_3D` /
  `BTN_3D_DANGER` 3D gradient buttons. **CSS-only hooks** (`QUASAR_INTERNAL_CSS`, scoped under
  `.calc-v2` except the popup): `.calc-v2` scope hook (the page itself uses the `PAGE` token for
  the gradient) · `.strat-menu-navy` the teleported Strategy-menu popup (**GLOBAL** — Quasar
  menus mount on `<body>`, outside `.calc-v2`) · `.leg-head` / `.leg-row` / `.leg-strike`
  leg-table cells (`leg_editor.build_leg_editor(..., header=True)`).
- **Palette** (hex → role): page bg `#16243f→#0c1424→#0a0f1c` (radial) / border `#1d2942`
  · card `#101a30` / border `#213152` · input box `#0c1426` / border `#243353` / focus
  ring `#3b82f6` · input text `#e7edf8` · base text `#cdd8ee` · muted label `#7f8db0` ·
  icon + eyebrow `#8794b4` · title `#eaf0fb` · secondary btn `#15213b` (hover `#1b2950`)
  · primary btn `#2563eb` (hover `#1d4fd1`) · tab active `#e7edf8` / inactive `#8794b4`
  / indicator `#3b82f6` · **P/L payoff** profit-green `#34d399` / loss-red `#f87171`
  (gradient fills + faint `rgba(...,.06)` washes — `simulator.whatif_figure`).

**UI styling standard — Tailwind-first (mandatory for all NiceGUI UI).** All
component styling MUST be expressed as **Tailwind utility classes via `.classes()`**.
This is a hard standard — every new page, and every page touched during the migration,
follows it. Scope decided **pragmatic** + intent **convert + light polish** (preserve
today's dark-navy look, standardize it into the token vocabulary, fix obvious
inconsistencies as each screen is converted — no gratuitous redesign). The five rules:
- **Banned:** `.style(...)`, inline `style=` attribute strings, `.props("style=…")`, and
  fixed pixel measurements outside Tailwind classes. Use Tailwind scale utilities, or
  `[...]` **arbitrary values** when an exact value is required (`w-[37px]`,
  `text-[#eaf0fb]`) — never `.style("width:37px")`.
- **Dynamic / data-driven values → MAP TO FIXED PALETTE CLASSES.** NiceGUI 3.x bundles
  the **Tailwind browser JIT** (`tailwindcss.min.js`), so a runtime-built arbitrary-value
  class (`.classes(f"text-[{hex}]")`) *does* generate (verified) — but we deliberately do
  **NOT** do that. A data-driven color/size is mapped from its **known finite set** to a
  **static, semantic** Tailwind class via a small pure lookup — a regime/score *state* →
  `text-emerald-400` / `text-amber-400` / `text-rose-400`; a collapsed/expanded width →
  `w-11` / `w-[360px]` — with a neutral-class fallback, so the vocabulary stays clean and
  deduped (no scattered magic hexes). Prefer mapping a semantic state the payload **already
  carries** (e.g. a `label`/`bias`) page-side; only when no clean state exists, refactor the
  **Tier-2** source to emit one (allowed — this is the documented exception to webgui-only).
  NEVER set the dynamic value via `.style()`. (Exception: a **genuinely continuous** value with
  no finite set — e.g. a computed `flex-grow` ratio — may use a **runtime arbitrary-value class**
  (`flex-[{w}_1_0%]`, JIT-generated) reset via `.classes(remove=prev, add=new)`; this is distinct
  from data-driven COLORS, which always map to a fixed finite palette.)
- **Design tokens, NOT semantic CSS classes.** The dark-navy theme is a vocabulary of
  **Python Tailwind-class-string constants** in `webgui/pages/options/theme.py`
  (`PAGE` / `CARD` / `EYEBROW` / `BTN` / `BTN_PRIMARY` / `LABEL` / `MUTED` / …), each a
  reusable utility string encoding the palette above, applied with `.classes(CARD)`.
  No `.calc-card { … }`-style CSS rules. (Tailwind ships bundled in NiceGUI; custom
  theme colors aren't trivially configurable, so tokens carry `[#hex]` arbitrary values.)
- **The ONE escape hatch.** A single documented `ui.add_css` block per theme is allowed
  **only** for Quasar-internal / teleported DOM that component `.classes()` physically
  cannot reach: `q-field__control` field internals (boxed inputs), `q-tab*`, the
  `.nicegui-expansion-content` gap, and body-mounted popups like `.strat-menu-navy`.
  Nothing else belongs in `ui.add_css`.
- **Out of scope** (NOT NiceGUI components, so the rule doesn't bind them): standalone
  documents served as raw `HTMLResponse` — EOD `summary/detail.html`, the Gamma
  Explain/Analyze infographics — **raw `ui.html()` HTML-string fragments** built with inline
  `style=` attributes (e.g. the Calculator P&L heatmap grid, the Gamma Explain blocks), since
  they aren't NiceGUI components with `.classes()` — and **Highcharts option dicts** (chart
  colors are chart config, not CSS).

The migration runs in **phases (menu first, then each screen by logical group)** — see
the design doc
[`docs/plans/2026-06-28-tailwind-first-ui-migration-design.md`](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md).
Status snapshot: **✅ COMPLETE — Phases 0–8 done; the ENTIRE webgui is Tailwind-only** (2026-06-28,
607 webgui tests green; **zero `.style()`/`:style=` anywhere in `webgui/pages`**, verified by grep +
the `test_no_inline_style.py` guard covering every converted page). The only inline styling that
remains is the **documented out-of-scope set**: Highcharts option dicts (chart config), raw
`ui.html()` HTML-string fragments + their CSS (`EOD_CSS`/`EXPLAIN_CSS`/the Gamma Analyze infographic
/ the EOD export docs), and Quasar `color=` props. The ONE escape hatch is per-page **Quasar-internal**
`ui.add_css` (`QUASAR_INTERNAL_CSS` field/tab/menu internals; `_NAV_CSS`; the table-internal
`SCAN_CSS`/`PAPER_CSS`/`CAPTURED_CSS`/`_RESCUE_CSS`/`DRIVER_CSS` sticky-thead/`.q-table__middle`).
**P0** — `theme.py` ships the
Tailwind token vocabulary (`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/`BTN_PRIMARY`/
`STRATEGY_BTN` + the semantic state-color tokens `TXT_POS`/`TXT_WARN`/`TXT_NEG`/
`TXT_NEUTRAL` + `STATE_TEXT_CLASSES` + the 3D-button tokens `BTN_3D`/`BTN_3D_DANGER`) + the
**`QUASAR_INTERNAL_CSS`** escape-hatch block (the field/tab/menu internals scoped under the
`.calc-v2`/`.strategy-menu-btn`/`.leg-*` hooks). **The legacy `DASHBOARD_CSS` was DELETED in P4
(its last consumer, Trade, flipped) — `theme.py` is now tokens + `QUASAR_INTERNAL_CSS` only.**
**P1** — the **nav shell** (`main.py`) is fully Tailwind; `_NAV_CSS` is now
Quasar-internal-only. **P2** — the shared **`pages/options/*` helpers**
(`detail.py`/`header.py`/`overlay.py`) are `.style()`-free: dynamic data-driven colors are
**palette-mapped** (a finite state/label → a fixed token — detail tiles via `TXT_*`; the
header VIX-regime badge + sentiment dot via local label→class maps
`regime_badge_class`/`sentiment_dot_class`), and reactive recolors use
`.classes(remove=…, add=…)` to avoid class accumulation; `leg_editor.py`/`strategy_menu.py`
were already inline-style-free. **P3a** — the six **signal-table screens**
(`scanner`/`swing`/`captured`/`paper`/`portfolio`/`rescue`) are free of `.style()` AND every
Vue `:style=` slot binding: dynamic **table-cell** colors stamp a Tailwind **class** field
from a finite-set map (`score_zone_class`/`rec_class`/`pnl_class`/`verdict_class`/
`heat_bg_class`/`heat_border_class`/`cash_class`) and bind `:class` (JIT-generated); the **3D
gradient buttons** use `BTN_3D`/`BTN_3D_DANGER` (`color=None`); per-page `ui.add_css` is
slimmed to Quasar-table-internals (cell padding, sticky `thead`, `.q-table__middle`, scanner
`.q-tab*`). A `test_no_inline_style.py` guard pins all helper + 3a pages. **P3b** — the
**Calculator + Simulator** (the heaviest `DASHBOARD_CSS` consumers) now use the tokens:
`.calc-card`→`CARD`, `.cv2-btn`→`BTN`, `.cv2-btn-primary`→`BTN_PRIMARY`, `.calc-eyebrow`→
`EYEBROW`, `.strategy-menu-btn`→`STRATEGY_BTN` (in the shared `strategy_menu.py`), title →
`LABEL`, calc summary tiles palette-mapped via `tile_color_class`; both pages now inject
`QUASAR_INTERNAL_CSS` (NOT `DASHBOARD_CSS`) and keep `.calc-v2`/`.strategy-menu-btn`/`.leg-*`
ONLY as **scope hooks** for the Quasar field/tab/menu internals; the dead `CALC_CSS` was
deleted. The Calculator **P&L heatmap is a raw `ui.html()` grid → out of scope** (documented).
**`DASHBOARD_CSS` is now consumed ONLY by `trade.py`** — Phase 4 flips Trade, then the cleanup
deletes the now-dead `.calc-card`/`.cv2-btn*` semantic rules. **P3c** — **Gamma + Expected-Move**:
gamma's 2 dynamic colors palette-mapped (hedge tile → `TXT_*`; collector status bar → a local
`status_color_class` map, reactive `remove/add`) and its 6 **panel-flex** `.style()` → a runtime
arbitrary `flex-[{w}_1_0%]` class (the documented **continuous-value** exception — no finite
palette — reset via tracked-previous `_set_flex_class`); Expected-Move was already clean.
Highcharts option dicts + the Explain/Analyze HTML (Tier-2) + `EXPLAIN_CSS` (styles a `ui.html()`
fragment) stay **out of scope**. **Phase 3 (every Options screen) is COMPLETE.** **P4** —
**Trade** (the LAST `DASHBOARD_CSS` consumer) converted: `.calc-card`→`CARD`, `.calc-eyebrow`→
`EYEBROW`, `.cv2-btn-primary`→`BTN_PRIMARY`, `.calc-v2` kept as hook + `PAGE`; its 10 `.style()`
colors palette-mapped via **LOCAL** maps (`verdict_text_class`/`bias_text_class` — the verdict
3-set `#2e7d32`/`#f9a825`/`#c62828` is DARKER than `TXT_*`, deliberately not shared — +
`markov_band_bg_class` for the 5-band chip; Highcharts `_MK_*` untouched), reactive verdict/chip
labels via `remove/add`. Then the **LEGACY CLEANUP**: `DASHBOARD_CSS` had zero consumers → **DELETED**
from `theme.py`; the dead `verdict_color`/`bias_color` hex fns + `*_COLOR` constants removed;
`test_theme.py` now asserts `not hasattr(theme,"DASHBOARD_CSS")`; the "App theme" section + example
rewritten to the token reality. **`theme.py` = tokens + `QUASAR_INTERNAL_CSS` ONLY** — the migration's
payoff. **The entire Options section + Trade are Tailwind-only** (587 green; Calc/Sim un-regressed
post-deletion). **P5** — **Sentiment + Sector Rotation** (the heaviest phase, ~58 `.style()`):
static widths/flex → arbitrary Tailwind; ~20 dynamic colors from finite sets → **LOCAL Tailwind
class maps** (these pages keep their OWN palette `#66bb6a`/`#ef5350`/`#ffd54f`/`#9e9e9e`/`#3fb6c7` —
yellow/cyan/flat differ from `TXT_*`, so deliberately NOT shared; pages do NOT adopt `PAGE`/`CARD` —
their look is preserved); the `.sent-sectors` `ui.add_css` block → Tailwind row borders/hover; the
**auto-refresh** in-place recolors (traffic tiles bg + bias/regime/rotation/headline labels) use
`remove/add` (verified live: no class stacking across refresh cycles). Gauges + history/RRG
Highcharts charts stay **out of scope**. **P6** — **Portfolio**: the proxy/stream status-bar colors
(local `#2e9e6b`/`#e24b4a`/`#888888`) on persistent labels via `remove/add`; static pre-wrap →
`whitespace-pre-wrap`. **P7** — **Driver**: the grade + control-state **badge backgrounds**
(`grade_bg_class` 5-set / `control_bg_class` 3-set, rebuilt per repaint → `add=`) and the perf **P&L
table cell** — a Vue `:style=` slot → **`:class`** with a stamped `_pnl_class` row field (a guard test
pins the slot references it); `DRIVER_CSS` kept (Quasar-internal sticky-thead). **P8** — **Status**
(3 static `min-w-[…]` widths; Quasar `color=` props left), and **Settings/Terminate/Manuals** were
already `.style()`-free; **EOD**'s `EOD_CSS` styles a `ui.html()` fragment → **out of scope**. All
five pages joined the `test_no_inline_style.py` guard. **✅ The migration is COMPLETE — Phases 0–8,
the entire webgui is Tailwind-only (607 green, live-verified across every page).** Design/plan docs:
[design](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md) + the per-phase plans
`2026-06-28-tailwind-first-ui-migration-{plan,phase2,phase3a,phase3b,phase3c,phase4,phase5,phase6-8}-plan.md`.

**`pages/ui_guard.py` (cross-cutting, load-bearing — used by ~15 pages).** Provides
`guard` / `guard_async` decorators that make a NiceGUI callback a clean no-op when
the owning client/slot has been deleted (browser tab navigated away / closed /
reconnected) — swallowing the `RuntimeError('… has been deleted.')` that `ui.timer`
and post-`await` event handlers otherwise raise (and that NiceGUI's `handle_exception`
re-raises, doubling the noise). Wrap every timer callback and `on_click`/`.on(...)`
handler that mutates page widgets in it. **One path the decorators can't reach:**
NiceGUI's `Timer._run_in_loop` acquires its parent-slot context (`timer.py` line 90)
*before* the `_should_stop()` deleted-check on the next line, so on a disconnect/
reconnect race a timer touches a deleted slot and raises `RuntimeError('The parent
slot of the element has been deleted.')` **before the wrapped callback runs** —
escaping the decorator and surfacing as a noisy traceback via NiceGUI's default
`log.exception` handler. `ui_guard.install_deleted_slot_log_filter()` (called once at
`main.py` startup) attaches a `logging.Filter` to the `nicegui` logger that drops
**only** that benign record (client gone → nothing to update); every other error
still logs in full.

## webgui development notes (read before adding a page)

**Page pattern.** Add a leaf module `webgui/pages/<name>.py` exposing `render()`.
In `webgui/main.py`, add a `@ui.page("/route")` that does `with _layout(active,
title): from pages import <name>; <name>.render()`, and add the item to `NAV`
(flat) — `_layout` handles header, drawer, and the proxy-down banner. Register
the route in `test_shell.py`'s expected set.

**Import an app's engine (sys.path glue).** App folders have hyphens / no package
init, so a page adds the app dir to `sys.path` then imports the module by name —
e.g. `from repo_paths import TRADE_ANALYZER; sys.path.insert(0,
str(TRADE_ANALYZER))` then `import <engine>`. `webgui/conftest.py` already puts
the repo root + `webgui` on `sys.path` for tests. The proxy client is
`proxy.schwab_py_client` (schwab-py compatible) and `proxy.schwab_client`
(SchwabClient compatible).

> **Cross-app module-name collisions (IMPORTANT, bitten us).** Putting multiple
> app dirs on `sys.path` means same-named top-level modules clash process-wide
> (one `sys.modules` entry wins). Known clashes: **`scoring`** (options-scanner
> `scoring.py` vs sentiment-dashboard `scoring/` package) and **`notifier`**.
> Once the Sentiment page loads, `import scoring` resolves to sentiment's, which
> broke `scanner_engine.run_full_scan`'s lazy `from scoring import …`. Mitigation:
> `pages/options/engines.py` `options_scoring()` context manager pins the options
> `scoring` for the duration of an options engine call and restores after — used
> in `scanner.py`/`swing.py`. When wiring Trade/Portfolio/Driver, watch for the
> same trap (e.g. `notifier`); prefer importing engine deps eagerly at module
> load (binds the name once) and/or wrap lazy engine calls similarly.

> **Stdlib collisions via the script-launch path (IMPORTANT, bitten us 2026-06-24).**
> A service's OWN dir lands on `sys.path` when its `app.py` runs **as a script**
> (`python services/<svc>/app.py`), so a module there named after a **Python stdlib
> module** shadows it process-wide. `services/driver_svc/secrets.py` (an API-key
> resolver) shadowed the stdlib `secrets`, so starlette's `from secrets import
> token_hex` (pulled in by FastAPI) crashed `driver_svc` **on launch** — but NOT in
> tests (pytest runs from the repo root, a different `sys.path`, so the suite was
> green while the service couldn't start). Fixed by renaming it to `api_keys.py`;
> `driver_svc/tests/test_api_keys.py::test_no_module_shadows_stdlib` now guards every
> service module name against `sys.stdlib_module_names`. **Rule:** never name a
> service module after a stdlib module (`secrets`/`token`/`types`/`queue`/`select`/…).

**Structure for testability.** Keep pure transforms/figure-builders as
module-level functions (TDD them with sample dicts); keep `render()` thin
(widgets + wiring). Heavy/blocking engine calls go through
`await nicegui.run.io_bound(fn, ...)` with a spinner + try/except → `ui.notify`.

**NiceGUI gotchas (learned, costly):**
- `ui.html(...)` **strips `<style>` and `<iframe>`**. For CSS use `ui.add_css(css)`
  (rules only, scope with a class); render HTML *fragments*, not full documents.
  See `pages/options/gamma.py` Explain (`EXPLAIN_CSS` + `wrap_explain`).
- **`ui.html` sanitizes through the BUNDLED DOMPurify, and its allow-list is
  READABLE — so a stripped attribute is a testable invariant, not a mystery
  (cost: every label on the new /sentiment rings silently mis-positioned, with a
  fully green suite).** `html.js` calls `setHTML`, but that is NOT the native
  API: NiceGUI monkeypatches it at `templates/index.html:144` —
  `Element.prototype.setHTML = function (html) { this.innerHTML =
  DOMPurify.sanitize(html); }`, its own comment explaining that native `setHTML`
  strips class attributes. So the effective allow-list is **DOMPurify's
  default**, which is laxer in some places and stricter in others. It allows
  `alignment-baseline` and `baseline-shift` but **NOT `dominant-baseline`** —
  the obvious spelling for vertically centring SVG `<text>`, and the one
  `rings._text` shipped with. Client-side every label dropped to the alphabetic
  baseline while the **server-side string stayed correct**, so nothing in the
  suite could see it. Fixed with the pre-`dominant-baseline` idiom, `dy="0.35em"`
  (`rings._BASELINE_DY`), which is allow-listed and depends on no allow-list
  detail that can change under us; `sanitize=False` was considered and rejected
  as disproportionate for a dial. **The general fix is the test:**
  `webgui/tests/test_rings.py::test_ring_svg_emits_nothing_dompurify_would_strip`
  extracts the allow-list out of the shipped `nicegui/static/dompurify.mjs` (long
  runs of quoted lowercase tokens, **dropping any run containing `script`** —
  DOMPurify also ships DENY lists, and unioning those in blessed `<use>`) and
  asserts every tag and attribute the builder emits survives it.
- **`getBBox()` on an SVG `<text>` returns the EM box, not the ink — so it is the
  wrong tool for optical centring.** Centring the ring's value/caption pair on
  `getBBox()` left it visibly low: the box reported a 4.2px offset where the
  measured INK offset was **10.7px**, because an em box carries ascender and
  descender space that lining digits and all-caps captions never fill. Measure
  with canvas `actualBoundingBoxAscent`/`actualBoundingBoxDescent` instead. (The
  same reason `rings._BASELINE_DY` is 0.35em ≈ half the app font's cap height,
  and deliberately not a reproduction of `dominant-baseline:middle`, which
  centres on the *x*-height and so sits ~0.09em high for cap-height glyphs.)
- **A drawer's `.nav-drawer` class is NOT the `<aside>` — you cannot size the drawer
  through it (cost: hours; the CSS silently did nothing).** NiceGUI puts the classes
  you pass to `ui.left_drawer(...).classes(...)` on Quasar's **inner**
  `div.q-drawer__content.fit.scroll.nicegui-drawer.nav-drawer`; the **parent
  `<aside class="q-drawer">` is what carries the inline `style="width:…"`** written by
  the `width` prop. Styling the child resizes a CHILD of the width-holder → no visible
  effect. Reach the aside via **`:has(> .nav-drawer)`** (see `main._NAV_CSS`). Related,
  verified: `ui.left_drawer` has **no `width` kwarg** — width goes through
  `.props("width=64")`; and an unlayered author `!important` (what `ui.add_css` emits)
  **does** beat the aside's inline width, but it does **NOT** beat NiceGUI's
  `layer(quasar_importants)` rules (e.g. `.fit{width:100%!important}` — which is on the
  CONTENT div, and 100% of a 248px aside is what you want anyway). Know that asymmetry
  before fighting a width.
- **CSS transitions FREEZE in the automation browser, so `getComputedStyle`
  measurements LIE (cost: hours chasing phantom values).** The Claude Browser pane's
  tab is backgrounded (`document.visibilityState === "hidden"`), so
  `document.timeline.currentTime` stays **0 forever**. Every CSS transition sits
  `playState:"running"` at `currentTime: 0` indefinitely, pinning its property at its
  **START** value — and a running transition outranks even author `!important`. So a
  transitioned property reads its **pre-change** value forever: a phantom `width: 300px`
  while the inline style says `64px`; a label stuck at `opacity: 0` while its
  `opacity: 1` rule is present, matching, and higher-specificity. **The tell:** one
  property from a selector applies instantly while another property from the *identical*
  selector doesn't. **Fix before measuring:** inject
  `* { transition: none !important; animation: none !important; }`. (Related preview
  caveats: `computer{action:"screenshot"}` times out on this app — verify via DOM eval;
  and hover-by-`ref` works while hover-by-coordinate requires a screenshot first.)
- **`ui.highchart` inside an inactive `ui.tab_panel` COLLAPSES (cost: the IV-shock
  bug).** The `nicegui-highcharts` Vue component reflows **once** at `mounted()` and
  has **NO ResizeObserver** (`update()` calls `chart.update()`, which does NOT resize
  to the container). A chart that mounts while its tab is hidden (`display:none`)
  measures a 0×0 container and renders collapsed (title-height, ~600px wide) and
  never recovers when the tab is shown. Fix: (a) give the figure an **explicit
  `chart.height`** (so it never depends on container measurement — the default-active
  Replay tab's chart already did, the hidden What-if/IV-shock didn't), AND (b) on
  `tabs.on_value_change`, **reflow** each chart after the panel is visible:
  `ui.timer(0.05, lambda: ui.run_javascript(f"getElement({el.id})?.chart?.reflow()"),
  once=True)` (`getElement(id)` → the Vue component; `.chart` is the Highcharts
  instance). See `pages/options/simulator.py`.
- Charts: **Highcharts** via `ui.highchart(options)` (the `nicegui-highcharts`
  element) — NOT Plotly. Build the options dict in a pure function so it's
  unit-testable; update in place with `el.options = fig; el.update()` (replaces the
  old `update_figure`). Gauges are the shared `pages/gauge.py` angular gauge
  (painted red→yellow→green rainbow face + needle). Heatmaps/bars in Gamma,
  line/column in Simulator, spline RRG in Sector Rotation, history line in Sentiment.
  **Gotchas (cost real time):** the `gauge` type auto-loads via `loadMore`, so pass
  NO `extras` — `extras=["highcharts-more"]` THROWS; `solid-gauge`/`heatmap` ARE
  valid explicit extras (both bundled). **Candlestick** is a Highcharts **Stock**
  series — pass `extras=["stock"]` (the `stock` module is bundled; it enables the
  candlestick/ohlc series + axis `crosshair.label` boxes). **Crosshair gotcha (cost
  hours):** the **datetime X-axis** `crosshair.label` box renders the RAW epoch-ms
  value and IGNORES both `label.format` (date tokens) AND a `label.formatter` function
  — verified on a plain `chart` AND `stockChart` (the formatter, shippable via NiceGUI's
  `:`-prefixed dynamic-property → `new Function`, IS attached + returns the right date
  but Highcharts never calls it). A NUMERIC y-axis crosshair label DOES honor `format`
  (e.g. `{value:.2f}`). So: keep the X crosshair LINE but disable its label box, show
  price on the Y label, put the DATE in the tooltip header (`tooltip.xDateFormat`) —
  see `pages/options/expected_move.py`. **`ui.highchart` accepts `type=`** ("chart"
  default / "stockChart" / "mapChart") → renders `Highcharts.stockChart` etc. (the
  JS does `Highcharts[this.type]`); `chart.update()` still applies in place. Use
  `type="stockChart"` for an **ordinal x-axis** that COLLAPSES non-trading-day gaps
  (weekends/holidays) in candlestick data automatically with NO calendar — but a
  forward series you generate yourself (e.g. the EM cone) must ALSO omit non-trading
  days or ordinal re-opens the gap (see `expected_move.py` + `compute.em_cone(...,
  trading_days_only=True)`). **The STOCK MODULE and in-place updates DON'T MIX — it is
  the MODULE, not `type="stockChart"` (2026-07-06 cost: the frozen sentiment intraday
  graphs; RE-DIAGNOSED 2026-07-28 at the cost of a failed Gamma candlestick attempt):**
  loading the stock module patches `Chart.update`, which then throws
  `Cannot read properties of undefined (reading 'enabled')` on a chart that lacks the
  stock scaffolding. The 2026-07-06 note blamed `type="stockChart"`; in fact **merely
  passing `extras=["stock"]` to a PLAIN chart is enough**, and the failure is worse
  there — the throw aborts the update mid-way and leaves the chart with **ZERO series**
  (a blank panel), not merely frozen. Live-verified on `/options/gamma`: with `stock`
  loaded the heatmap did not draw **even with the spot overlay set to a plain line**.
  Disabling `navigator`/`scrollbar`/`rangeSelector` did NOT help, and neither did
  pre-seeding every series at element creation (the trick that DOES work for
  `colorAxis`). **So: any element that repaints via `el.options = …; el.update()` can
  never load the stock module** — which rules out the `candlestick`/`ohlc`/`flags`
  series types on it. Draw bars from CORE series instead: a `columnrange` body +
  an `errorbar` wick, each point carrying its own `color` (see
  `gamma.candle_points` — one series then holds both up and down bars). `columnrange`
  /`errorbar` need **no `extras` at all** (the `more` module auto-loads, same as
  `gauge`), so nothing patches `update`. For a NON-updating chart, `type="stockChart"`
  remains fine (see the Expected Move page); pack time gaps with a synthetic category
  axis instead (`xAxis.breaks` is no substitute — it renders zero ticks). A
  `ui.highchart` added DYNAMICALLY on a page
  with no chart at first render fails `Failed to resolve module specifier
  nicegui-highcharts` (the ESM import map is set at initial render) — keep a chart
  present at page build (e.g. a persistent element, as `detail.py` does). A
  Highcharts `bar` reverses its xAxis by default (`reversed:False` = high values at
  top). `chart.update()` MERGES options, so a series-TYPE switch leaks the old type's
  plotLines/colorAxis — RECREATE the element on kind-change (bar↔heatmap), don't
  update in place (see `gamma._set_chart`). A **green-above-zero / red-below-zero P&L
  payoff** = an `area` series with `threshold:0` + `color`/`negativeColor` (line) +
  `fillColor`/`negativeFillColor` (fill); you MUST set an explicit base `color`/
  `fillColor` or Highcharts paints a default-blue (`#2caffe`) base path UNDER the
  green/red split (cost: a stray blue area — see `simulator.whatif_figure`).
  `accessibility.enabled:False` silences
  the a11y-module console nag (house pattern). (`plotly` was never a Python dep —
  `ui.plotly(dict)` rendered via bundled plotly.js.)
- **Blended heatmap + colorAxis alpha:** `series.interpolation:True` (Highcharts 12,
  in the bundled `heatmap` module) renders the heatmap as ONE smooth interpolated
  `<image>` (no per-cell `<rect>`s, so no borders/separator mesh) — the "blended"
  look. **colorAxis `stops` honor rgba alpha** through the interpolated image, so a
  `rgba(...,0)` stop at the zero-point makes net≈0 fade to transparent and the dark
  page shows through (`gamma.HEAT_STOPS` + `chart.backgroundColor:"transparent"`).
  Drop `plotBackgroundColor` (the mesh) and set `borderWidth:0`. **`states:{inactive:
  {enabled:False},hover:{enabled:False}}`** stops the hover-dim/fade.
- **An interpolated heatmap needs a UNIFORM data grid, or it combs (2026-08-11, cost:
  a long misdiagnosis).** `interpolation:True` rasterizes onto a canvas laid out on ONE
  row height — `rowsize`, which `gamma._strike_step` derives as the MEDIAN strike gap.
  Feed it a chain whose strikes are unevenly spaced and the finer strikes collide
  two-into-one canvas row while the cells between them are never written; upscaled, that
  reads as a **comb of vertical stripes**, not a smooth field. **$NDX is the only symbol
  in this app that hits it** — it quotes **5-wide near the money among 10-wide** (measured
  live: 28 gaps of 5 among 56 of 10), where $SPX is uniformly 5, SPY/QQQ/IWM 1, AMD 2.5.
  Fix = `gamma.uniform_strike_grid(strikes, z)`: fill the ladder to the FINEST gap,
  linearly interpolating inserted rows between their bracketing real strikes (it invents
  nothing the chart wasn't already implying — an interpolated heatmap shades between
  samples regardless; it just does it on a grid the rasterizer can represent). Real
  strikes pass through untouched, a row bracketed by a missing sample stays `None` so
  genuine holes stay holes, an already-even ladder returns the SAME objects (no cost),
  and `_MAX_UNIFORM_ROWS`=240 stops one stray half-strike exploding the window. Applied
  to the collected cells AND the projection band; run on the VISIBLE strikes only (the
  full chain spans ~3000–9800 with wide wing gaps → the cap would refuse it).
  **The Term heatmap is immune** — its axes are CATEGORICAL and points are addressed by
  row INDEX, so an uneven ladder cannot collide rows (the trade-off being that its y axis
  is ordinal, not proportional to price). **Diagnosing this class:** the tell is *smooth
  stored values under striped pixels* — read the series out of the cache and compare it
  to the rendered PNG's per-column alpha (`canvas.getImageData`). Rule out the overlay
  series first by hiding them in the DOM; a full-rectangle source grid plus a striped
  image means the rasterizer, never the data.
- **Tooltip ONLY on press-and-hold (or click), not hover** (the `nicegui-highcharts`
  way): you can't do it purely in config, and the component **clobbers**
  `plotOptions.series.point.events.click` (it wires its own `pointClick` `$emit`). The
  trick (`gamma._HEAT_PRESS_TOOLTIP_JS`, shipped as a `:`-dynamic `chart.events.load`
  function): monkeypatch `chart.tooltip.refresh` to a gated no-op, then a container
  `mousedown` opens the gate + `runPointActions` shows the point under the cursor
  (Highcharts' own mousemove keeps it following while held), and a `document`
  `mouseup` closes the gate + `tooltip.hide(0)`. **Gotcha:** `chart.events.load` fires
  ONCE at element creation, and a persistent `ui.highchart` (created with one fig,
  then `el.options=…` BEFORE the client mounts) mounts with the LAST-set options — so
  the load hook must be on the figure the element actually mounts with (carry it in
  the figure BUILDER, e.g. `heatmap_figure`/`term_heatmap`, not just the init fig).
  Don't re-set the global `tooltip`/`chart.events` on in-place updates or you rebuild
  the tooltip and lose the runtime monkeypatch.
- Tables: `ui.table(columns=[{name,label,field,...}], rows=[...], row_key="id")`;
  selection via `selection="single"` + `table.selected`; row click via
  `table.on("rowClick", handler)` where `event.args[1]` is the row dict.
- Number/select/slider/toggle fire `on_value_change`. Set values with
  `el.value = ...; el.update()`. Auto-refresh + autoload via `ui.timer(secs, fn)`
  and `ui.timer(0.1, fn, once=True)` (see Gamma).
- A page is built per request inside `_layout`; keep page state in a local dict
  closure, not module globals.

**Verify in the browser.** `.claude/launch.json` defines the `webgui` dev server
on **:8500** (`autoPort:false` — the NiceGUI port is fixed). Use the Claude
Preview tool (start `webgui`, screenshot). Restart the preview after code changes
to pick them up. To drive Quasar inputs from the preview, set the native value +
dispatch `input`/`change`/`blur` events. **Caveats (seen):** the **screenshot**
tool TIMES OUT on heavy multi-panel Highcharts pages (e.g. the Replay 6-panel
stack) — it works on lighter single-chart pages (EM); when it hangs, verify via
DOM `preview_eval` (read `.highcharts-series`/axis geometry) instead. A **Quasar
`q-slider` can't be driven by synthetic mouse/pointer/keyboard events** (no native
input) — assert slider→handler wiring with a unit test, not the preview. For
3-tier pages, the most reliable end-to-end check is **Redis-driven**: enqueue a
command with `Bus().enqueue_command("cmd:<domain>", {...})` and read the result
with `Bus().cache_get("cache:<domain>:<view>")` — bypasses the browser entirely.
Service code changes require **restarting that service** (the running one is
stale); the proxy's REST market data works even when `/health` shows
`token_expired:true` (auto-refresh) — only a missing/expired **refresh** token is
fatal.

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (**1336 green**,
measured 2026-08-14 — this line said 772 for a long time, then 1190; see the
Tests section for the standing warning about stale counts and for the
worktree/subshell caveat).
TDD pure functions; smoke-verify `render()` with a screenshot.
`tests/test_no_inline_style.py` guards every migrated page against `.style(`/`:style=`
(the Tailwind-first standard) — add any new page to it.

**Environment quirks to expect:**
- The proxy on `:8100` may be the *source* repo's proxy (its `/health`
  `token_file` points at `D:\Trading With Schwab\...`). Fine for reading data;
  just know live data isn't this repo's proxy.
- Weekend / off-hours → sparse 0-DTE option data (e.g. "no non-zero GEX within
  ±2%", swing scans returning 0). Not a bug.
- `options-scanner/data/Top 20.xlsx` (scanner watchlist) is present locally but
  **gitignored** (`data/`), like the real secrets — a fresh clone degrades to
  base symbols. Same applies to the paper-trading DBs (start empty).
- **Proxy `/pricehistory` (fixed 2026-06-19):** `schwab_proxy.get_price_history`
  now calls `/pricehistory?symbol=…` directly (symbol is a query param). The old
  code tried `/{symbol}/pricehistory` first — a guaranteed 404 that `api_request`
  retried `MAX_RETRIES`× with backoff, flooding `errors.log` (~99% of all ERRORs)
  and wasting ~0.75 s/fetch. If you see a `D:\Trading With Schwab` source-repo proxy
  still on `:8100`, it may not have this fix.

**Sentiment (`/sentiment`) — DONE.** `webgui/pages/sentiment.py` reuses the
copied `history_backfill.backfill_history(...)` engine (latest completed-session
composite + 30d history) + `scoring` (`composite.velocity/divergence`,
`trend_regime.classify/commit_state`) + the ported `sectors_ref.load_sectors_data`.
**Layout:** a three-column top region — **Market Sentiment ring** / **Market Trend
ring** / **Signals** — each column an equal-width `min-w-[300px]`. **Since
2026-08-14 the four semicircular gauges are two concentric Day/Week/Month rings**
(see "Sentiment Day/Week/Month rings" below); the Sentiment ring keeps bias +
size/conf beneath it, the Trend ring keeps the regime badge/desc and the **TREND
DETAIL** press-and-hold popup showing the four sub-scores (Price/Breadth/Sector/VIX)
+ confidences (`trend_subscore_rows`). Trend values come from `derived["trend"]` /
`derived["trend_7d"]` / `derived["trend_30d_ago"]` **published by `sentiment_svc`**
via the **intraday Market Trend model** (see the dedicated section below). The
component **table** (Value/Score[2dp]/Weight/Conf — Contrib computed for
reconciliation but not shown; credit_pulse excluded per v4.3 `WEIGHTS`) and the
Trend detail are **press-and-hold popups** (`ui.menu().props("no-parent-event")`),
not always-visible columns. The Signals column is a **1×4 vertical stack of glowing
tiles** (`SIGNAL_TILE_DEFS` = **Bias/Signal/Yesterday/Change** — Modifier dropped per
design), each tile icon + letter-spaced label / big neon-shadowed value / hairline
rule + dot / footer icon + descriptor, tinted from a finite four-key `TONE_CLASSES`
map (pos/neg/warn/flat), with the service's **velocity + divergence lines** beneath
(`velocity_lines`). (A dollar-weighted call/put **premium**
skew tile lives on the **Market Dashboard** OPTIONS SENTIMENT frame, NOT here — see the
"Market Dashboard" section + the 2026-07-21 Last-updated entry.) Below that, an **expanded-by-default**
(since 2026-07-12) `ui.expansion("Daily Sentiment & Trend")` holds **two stacked value-colorized 2-min
intraday graphs** — Daily Market Sentiment (0–10) + Daily Market Trend (**shown 0–10**, stored
0–100 ×0.1), each a Highcharts line colorized green/yellow/red by value via
`series.zones`/`zoneAxis:"y"` (`build_sentiment_intraday_figure`/`build_trend_intraday_figure`;
sentiment bands ≤4.5/≤6.5, trend bands ≤3/≤7 on the 0–10 scale), over a **synthetic contiguous
index (category) x-axis** — trading days PACK together with no overnight/weekend dead space, a
null slot breaks the line between days, tick labels at day boundaries, CT date+time in each
point's tooltip `name` — rolling the **last 5 trading days**. Deliberately a PLAIN chart, NOT a
stockChart (whose in-place `chart.update()` throws and silently freezes an open page — see the
NiceGUI gotchas). The series is **recorded going forward** (no
backfill) by `sentiment_svc` — each 120 s `refresh()` records one `(ts, sentiment, trend)`
point **RTH-gated** (Mon–Fri 08:30–15:00 CT) into the SQLite store
`services/sentiment_svc/intraday_history_db.py` (`repo_paths.SENTIMENT_INTRADAY_DB` =
`sentiment-dashboard/data/sentiment_intraday.db`; rolling window = last 5 distinct local
dates; one shared connection serialized by `handlers._INTRADAY_LOCK` across the
multi-worker executor), then publishes `cache:sentiment:intraday_history`
(`{"points":[{ts,sentiment,trend},…]}`; additive `IntradayHistory` contract). The page
reads that view in `_read_cache` (it rides the composite version bump — published in the
same refresh cycle), paints both charts in `_apply`, and **reflows on expand** (a
`@guard`-wrapped worker — charts built inside a collapsed expander measure 0×0, the
documented Simulator-hidden-tab fix). This **replaced** the old 30-day composite-history
chart + 5d/20d rolling-average + velocity/divergence text lines. **NOTE (2026-07-12):** the
**Sector & Industry Performance** table below was **moved to its own `/sentiment/sectors` tab**
(`pages.sentiment_sectors`) — the description that follows now documents THAT page (the builders +
cache view are unchanged; only the containing route moved). The
full-width
**Sector & Industry Performance** table
(11 sectors × Day/Week/Month %, P/C, RRG; per-cell colored; subtle gridlines + row
hover via a `.sent-sectors` `ui.add_css` block) with a rotation banner
(`scoring.rotation.compute_rotation`) + "% green | Cap-wtd | Score" summary, and a
bottom **status bar** (Updated/Next/Sectors/Proxy — proxy checked off-thread in
`load()`, cached, not on the status timer). Each sector **expands** into its industry
sub-rows (▷ toggle or Expand/Collapse All; lazy-fetched via `_load_industries` +
cached; industries show Day/Week/Month % **and P/C + RRG**).
The sector load (`_load_sector_perf`, ~24 proxy calls incl. 11 `/chains` for P/C)
runs at startup + on manual Refresh **+ once per RTH hour in the service** (`sentiment_svc scheduler.sectors_due`, 2026-07-09 — the P/C is a live option-VOLUME ratio that is empty premarket, so a premarket stack start used to leave the P/C column blank all day). **Auto-refresh is server-side and
tab-independent:** a module-level background task (`start_background_refresh` →
`refresh_cache`, started from `main.py` `@app.on_startup`, mirroring
`scanner.start_autoscan`) updates `_CACHE` (composite-only) **and republishes the
bridge every 120 s** regardless of any open/active tab — even with no browser open.
The page **never fetches on activation**: it paints from `_CACHE` instantly and a
fetch-free `ui.timer(120, _repaint_from_cache)` tracks the background cache;
**manual Refresh** is the only page-driven fetch. Persists across navigation via
`_CACHE` (single-user), incl. expanded sectors. Verified against the live proxy to
match the source dashboard exactly (82% green | Cap-wtd +0.70% | Score 7.8/10; real
industry rows on expand). Designs/plans:
[base](docs/plans/2026-06-14-sentiment-page-design.md) /
[sector-perf](docs/plans/2026-06-14-sentiment-sector-perf-design.md) /
[persistence+industries](docs/plans/2026-06-14-sentiment-persistence-industries-design.md)
(+ matching `-plan.md` files).
**Intraday Market Trend model (DONE — 2026-06-19).** The Market Trend panel is
driven by a **directional 0–100 score** (50 = neutral, 100 = max bull) recomputed
**every 15 min**, replacing the old slow daily 5-state SPY classifier. Pieces:
- **Pure scoring** `sentiment-dashboard/scoring/intraday_trend.py` (scalar in/out, no
  I/O): four directional sub-scores — `score_price` (45%: MTF EMA alignment + VWAP +
  MACD/RSI, **ADX-scaled** so chop hugs 50), `score_breadth_dir` (25%: A/D + %>50DMA +
  H/L), `score_sector_participation` (20%: # green + cyclical-vs-defensive day%-spread),
  `score_vix_context` (10%: level/change/term) + `vol_confidence_factor` (a VIX-spike
  damper on aggregate confidence) — blended by `blend_trend` (confidence-weighted, same
  idiom as `composite.blend`). `score_to_state` maps the score → the existing 5-state
  vocabulary (**80 bull / 70 pullback / 30–70 range / 20 bear_rally / bear**); hysteresis
  reuses `trend_regime.commit_state` (2 reads to flip). `TrendSub` is a local float
  dataclass (NOT the int-only `ScoreResult`).
- **Service compute** `services/sentiment_svc/compute.py`: `compute_intraday_trend`
  (fetches SPY intraday 5/15-min + daily via the proxy's new
  `get_intraday_history`, breadth/sector/VIX quotes — reuses `live_composite._BREADTH/_last/
  _VIX_SYMS` and standalone `technical`) and **two structural horizons**,
  `compute_30d_trend` and — added 2026-08-14 for the Trend ring's Week arc —
  **`compute_7d_trend`**. Both structural functions are thin wrappers over the shared
  **`_structural_trend(spy_daily_df, sector_pcts, cyc_def_scale)`** (price + sector only,
  no VWAP/breadth/VIX, no smoothing or hysteresis) and differ ONLY in which horizon's
  sector %-moves they pass (`week_pct` vs `month_pct`) and in `cyc_def_scale`
  (`_CYC_DEF_SCALE_7D = 1.5` vs `_CYC_DEF_SCALE_30D = 3.0`). Each owns its own TTL cache
  on the self-fetching path (`TREND_7D_TTL_SEC = 1800` / `TREND_30D_TTL_SEC = 3600`;
  explicit-args calls bypass it). All three are defensive → neutral on any failure —
  **and that neutral is a trap, see the ring section below.**
  ⚠ **`compute_30d_trend` is MISNAMED** and always was: it is a monthly-HORIZON
  *structural* read, not the trend as it stood 30 days ago and not a 30-day average.
  ⚠ **KNOWN LIMITATION:** the Week and Month horizons **share the same daily price
  sub-score** — `technical.calculate_ema_alignment`'s EMA periods are fixed, so handing
  it a shorter frame changes nothing. The two arcs therefore track each other and diverge
  mainly on **sector rotation**. A genuinely weekly price read needs weekly-resampled SPY
  bars; deliberately deferred.
  **One sector fan-out serves both horizons** — `_fetch_sector_pcts` (TTL
  `SECTOR_PCTS_TTL_SEC = 3600`, an EMPTY result deliberately NOT cached so a proxy blip
  can't poison the hour) returns `{"week": …, "month": …}` off ONE `_fetch_closes` call,
  which already derived both, so **the Week arc costs ZERO extra Schwab calls** on a stack
  measured at ~68–76k/day. `_fetch_sector_week_pcts`/`_fetch_sector_month_pcts` are views
  on it.
- **15-min cadence + persisted state** in `handlers.refresh` via the module-level
  `_TREND` holder (lock-guarded; `scheduler.trend_due`/`TREND_INTERVAL_SEC=900`): the
  EMA-smoothing + hysteresis state thread across reads; the held trend rides inside the
  existing `cache:sentiment:composite` as `derived.trend` / **`derived.trend_7d`** /
  `derived.trend_30d_ago` (no new Redis key). The two structural horizons carry **no
  hysteresis of their own — they are simply HELD** in `_TREND` and republished on gated
  (non-recompute) refreshes, so every composite write carries all three ring arcs.
  `derive_composite_extras` takes `trend_7d` **last** so the existing positional call
  shape is unaffected.
- **Bridge** (`live_composite.build_bridge_payload` + `compute._bridge_trend`): the
  intraday `state`/`confidence` + additive `trend_score`/`sub_scores` are merged onto the
  daily `classify` `sma_*`/`drawdown` (kept for the additive-only contract). `regime_filter`
  reads `state`/`confidence` unchanged (state strings + vote map identical). The standalone
  `publish_bridge` GEX path stays on the daily classify.
- **Page** `webgui/pages/sentiment.py`: `trend_gauge_value` returns the score directly;
  `trend_subscore_rows` feeds the TREND DETAIL popup. Verified live end-to-end (compute →
  Redis → bridge → rendered gauges + popup). Design/plan:
  [design](docs/plans/2026-06-19-intraday-market-trend-redesign-design.md) /
  [plan](docs/plans/2026-06-19-intraday-market-trend-redesign-plan.md).

**Sentiment Day/Week/Month rings (`webgui/pages/rings.py`) — 2026-08-14.** The
four semicircular Highcharts gauges on `/sentiment` are now **two concentric SVG
rings**, each showing **Day / Week / Month** on one dial. Four gauges could show
two horizons; two rings show six readings in less space, and — the substantive
reason — a ring can say **"no data"** where a needle cannot.
- **`ring_svg(arcs, uid, size=280)`** is a pure SVG-string builder (no NiceGUI
  import), mounted with `ui.html` and updated in place via `el.content`. Chosen
  over a Highcharts `solidgauge` and over a CSS conic-gradient: **rounded arc
  caps are impossible in CSS**, and a plain string sidesteps both documented
  `ui.highchart` hazards at once — the ESM-import-map trap (a chart added to a
  page that had none at first render fails `Failed to resolve module specifier
  nicegui-highcharts`) and the `chart.update()` merge/stock-module minefield.
  Precedent: `pages/options/svg.py`.
- **Geometry.** 270° sweep, start 225° / end 135°, measured **clockwise from 12
  o'clock** — so 0 is lower-left, 50 is top, 100 is lower-right, with a 90° gap
  at the bottom that the Week/Month legend lives in. Radii 112/90/68 (outer =
  Day), stroke 13, ticks at r=132, fixed `viewBox="0 0 280 280"`; **`size` sets
  only width/height**, so the dial scales itself and every internal coordinate
  stays in the 280-space. `_value_angle` REQUIRES a pre-clamped 0–100 — past 133
  the sweep exceeds 360° and wraps into a *short* arc that reads as a LOW value.
- Each arc's colour comes from **its own value** via `gauge._ramp_color`, so
  `config/theme.toml [gauge]` still drives the palette. The glow is a **layered
  halo** (a wide translucent copy of the path under a normal-width bright one),
  deliberately **not** an SVG `<filter>` — see the DOMPurify gotcha.
- **`uid` is REQUIRED**: both rings live on the same page and a duplicate DOM id
  makes them collide.
- **`pages/gauge.py` is UNCHANGED** and still serves the options detail-panel
  speedometer (`pages/options/detail.py`), so **the app now carries two gauge
  idioms** — Highcharts needle for a single value in a panel, SVG ring for
  multi-horizon. `rings.py` reuses its `_esc`/`_ramp_color` rather than forking
  them.
- **Page builders** (`pages/sentiment.py`): `sentiment_avg_or_none(snaps, n)` /
  `sentiment_avg` (`WEEK_SNAPS = 5` — the backfill is one snapshot per COMPLETED
  session, so a week is 5 rows, not 7), `sentiment_arcs(live, snaps)`,
  `trend_arcs(derived)`, `_composite_arc_value`, `_trend_arc_value`.
  **`sentiment_30d_avg` was DELETED** (its only caller was a removed gauge; a
  `hasattr` test pins that).
- **The defect class this redesign exists to fix — six instances of ONE failure:
  a missing or garbage input rendering as a CONFIDENT reading.** A non-finite
  composite becoming a full 100 arc (`min(100.0, nan)` is `100.0`, and these
  payloads cross Redis as JSON, which both emits and accepts `NaN`/`Infinity`, so
  a service-side divide-by-zero round-trips intact); an unparseable score
  becoming a maximally-BEARISH 0 via `_safe_float`'s 0.0 default; a NaN sector
  pct becoming **maximum cyclical leadership at full confidence** (measured:
  `score_sector_participation(5, 11, nan)` → `TrendSub(67.27, confidence=1.0)`,
  because `intraday_trend._clamp` is `max(lo, min(hi, v))` and that returns the
  HIGH bound for NaN — hence `compute._finite_pcts`, which DROPS non-finite
  sector moves so the missing sector lowers `n_total` and with it the
  sub-score's confidence, as it should); and **the one that actually fires in
  production** — `compute_7d_trend`/`compute_30d_trend` swallowing their own
  exceptions to return a fully shaped **`score 50.0 / confidence 0.0`** dict, so
  on any proxy blip a good reading is replaced by a confident-looking neutral 50
  and **every absent-key guard misses it**. That is why **`_trend_arc_value`
  keys on CONFIDENCE, not on key presence**. Confidence is a sound
  discriminator here and was verified rather than assumed: `blend_trend`
  weights each sub-score by its own confidence, so the aggregate rounds to 0.0
  only when there was no usable evidence at all — a genuinely neutral but
  well-evidenced 50/50 read scores agg 0.65 and passes straight through.
  `rings._safe_value` is deliberately NOT `gauge._safe_float` for the same
  reason: `None` → track-only + em-dash is how the ring says nothing, and a
  needle has no such state.
- **⚠ OUTSTANDING FOLLOW-UP — the PRICE sub-score has the same NaN exposure as
  the sector one, and it is NOT fixed.** `_finite_pcts` guards only the sector
  input. Measured on the live scorer: an all-NaN read of the structural price
  inputs (`macd_hist`/`rsi`/`adx` at `compute.py:1248-1253`, feeding
  `score_price` with a hardcoded `vwap_pct=0.0`) scores **82.50 — near-maximum
  bullish — at UNCHANGED confidence (0.333)**, where a sane read scores 56.25;
  the same all-NaN read in **`compute_intraday_trend`, the LIVE Day gauge**
  (`compute.py:439-443`) scores **92.50**. Deferred to its own task because the
  fix must cover both call sites with one shared filter.
- **Styling** stays Tailwind-first with **no `ui.add_css`** on the page. Note
  `theme.TILE_3D` is deliberately FLAT (its own comment: "a hairline border +
  12px radius, **NO bevel or drop shadow**", from the Deep Slate flattening) and
  was **not** redefined — the Signals tiles' glow tokens are LOCAL to
  `sentiment.py` (`_tone_classes`/`TONE_CLASSES`). The rings already carry a
  halo, so **the page is now mixed**: two glowing elements against a token
  vocabulary that says flat. A third would mean the theme has moved in practice
  and `theme.py` should be changed to match rather than routed around again.
  Reactive recolours swap via `.classes(remove=TONE_*_CLASSES, add=…)` — one
  remove-set per element type (value text / tile shell / rule / dot), since a
  partial set stacks across the version-poll repaint.
- **`_word_tone` — BIAS and SIGNAL carry `live_composite.signal_band`'s OWN
  vocabularies** (`Long / Neutral / Cautious / Short` and `Strong Bull … Strong
  Bear`), which are **NOT** the composite's `bias` field. `bias_color` only
  substring-matches bull/bear, so "Long" and "Short" read amber forever. Each
  tile now colours from its own word, and **`bias_text_class` delegates to
  `_word_tone`** so the headline under the ring can no longer contradict the tile
  beside it — it was rendering "7.28 · Long" in amber directly above a green
  "Long".
- **`velocity` and `divergence` are rendered again.** The service had been
  computing and publishing both on **every** refresh with **no renderer at all**
  since the intraday graphs replaced the old text block — a silent regression, an
  accident of that layout change, not a decision. `velocity_lines(derived)`
  returns `{text, flag, divergence}`; the flag and the divergence note hide when
  empty (empty means "no regime break", not "unknown").
- **Test isolation the change forced.** An autouse `conftest` fixture now resets
  `_SECTOR_PCTS_CACHE` / `_TREND_7D_CACHE` / `_TREND_30D_CACHE` **before and
  after** every sentiment_svc test — without it, any test stubbing `_fetch_closes`
  leaves its FIXTURE values in those module globals and a later un-monkeypatched
  self-fetching call silently consumes them (a probe `compute_30d_trend()` scored
  its sector sub-score 73.33 off stale stub data with ZERO fan-outs). The suite
  only stayed green because `pytest-randomly` isn't installed and the ordering
  happened to be kind. Design/plan:
  [design](docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md) /
  [plan](docs/plans/2026-08-14-sentiment-trend-ring-graphics-plan.md).

**Market Regime — display names + the direction axis (2026-08-14).** The five
regimes were renamed **for display only** and gained a direction word. **The
internal KEYS are unchanged** (`mean_reversion`/`trending`/`breakout`/`choppy`/
`crisis`) — they are the `RegimeState` contract, the `regime_intraday` DB
columns, and the driver packet, so renaming them would be a migration with no
user-visible benefit. Only the words moved:

| key | was | now | why |
|---|---|---|---|
| `mean_reversion` | Mean Reversion | **Balanced** | Its five inputs (low ADX, flat EMA, mid-band width, balanced profile, above the gamma flip) all say price is **AT** the mean. Nothing measures an extreme, so the old name promised a fade the model never tested — and it was the only name naming a *strategy* rather than the tape. |
| `choppy` | Choppy | **Whipsaw** | Same "not trending" axis as Balanced; the distinguishing feature is ENERGY (high ATR + low ADX, failed breaks, two-sided wicks). Balanced/Whipsaw carries that contrast; Mean Reversion/Choppy did not. |
| `crisis` | Volatile | **Stressed** | `VIX_STRESS_LO` is 22 and the fast-attack fires near VIX 30 — stress, not crisis; and "Volatile" also describes breakout/whipsaw days. The distinctive evidence is FEAR (VIX level/spike, term inversion, unfilled gap, deep below flip). |

**Direction (`market_regime.direction_sign`/`commit_direction`/`regime_label`).**
`trending` and `breakout` now render **Rallying/Firming** (up), **Retreating/
Softening** (down) and **Breakdown**; Balanced/Whipsaw/Stressed are directionless
by construction. The **intensity math stays sign-blind** (`ramp(abs(slope),…)`)
— "is this a trend day" is answered identically up or down — so this is a
five-member simplex with a label adornment, **NOT** a sixth regime: splitting
`trending` would need a DB column, a chart series, a contract change, and would
tear the membership across two bins when the slope flips mid-session, defeating
the blended model's whole point.

**How the contradiction risk is avoided (the load-bearing part).** The app has
TWO direction reads: this module's signed `ema_slope_atr` (SPY price, 5-min) and
the Market Trend composite score (price+breadth+sector+VIX, 15-min, hysteresis-
committed). They diverge on a real condition — index up on narrow leadership
while breadth is negative — so a word from either alone can contradict the other
panel. `direction_sign` names a direction **only when both agree past their
deadbands** (`DIRECTION_SLOPE_DEADBAND` = `EMA_TREND_LO`; `DIRECTION_TREND_DEADBAND`
= 3 points either side of 50); otherwise the neutral base label renders, which is
exactly the pre-2026-08-14 behaviour. `handlers._committed_trend_score()` reads
the SAME `smoothed_score` the gauge renders and is taken **before** `_REGIME_LOCK`
(so `_TREND_LOCK` is never nested inside it). `commit_direction` is deliberately
**asymmetric** — two consecutive reads to CLAIM a direction, one to drop back to
neutral: never keep asserting a direction the evidence stopped backing.

**Two rendering rules that are easy to get wrong.** (1) The stacked-area **series
names stay the BASE words** — the fixed order + stable names ARE the band's
reading position, so a legend that renames itself intra-session defeats it;
direction belongs on the headline + transition line only. (2) The headline
**colour follows the direction** for the two directional regimes
(`_DIRECTION_TEXT`), because the fixed green would paint "Retreating" as though
it were bullish. The label is also **re-derived page-side** from
`(committed_label, direction)` rather than echoing the payload's `label`, so a
held sample can't outlive a rename — but an `unclear` sample short-circuits to
"Unclear" regardless of the held key. The words are **duplicated in four tiers**
(`scoring/market_regime.REGIME_DISPLAY` is the source; `webgui/pages/sentiment.py`,
`driver_svc/compute.py` and `options_svc/market_snapshot.py` mirror it) because
none of those may import that package — Tier-1 takes no engine imports and the
services would hit the documented cross-app `scoring` collision. Keep them in
step. The push snapshot's transition line also stopped rendering RAW KEYS
("mean_reversion → trending") at the same time.

**Live intraday + bridge (DONE).** `sentiment-dashboard/live_composite.py`
`compute_live(schwab, sector_data)` computes a **live** composite from current
quotes reusing the pure scoring modules (the live analog of
`history_backfill._score_one_day`); `build_bridge_payload(...)` + `bridge.write_bridge`
publish `shared/sentiment_bridge.json` (consumed by `options-scanner/regime_filter`).
The **GEX collector** (`options-scanner/gex_collector.py`) publishes the bridge each
5-min cycle via a **subprocess** (`publish_bridge.py`, sentiment dir on `sys.path[0]`
to dodge the `scoring` package-vs-module collision) — independent of the webgui. The
webgui page's **headline always uses the live composite** (`compute_live`; the
`is_rth` flag now only labels the date as "live intraday" vs "latest — market
closed"), falling back to the backfill snapshot only if the live compute fails;
**backfill feeds only the 30-day history chart**. This keeps the web matched to
the legacy v4.3 methodology around the clock — Put/Call uses cap-weighted sector
P/C (not `$CPCE`) and Rotation uses dual-momentum (not the blended `compute_rotation`);
off-hours, Put/Call/Breadth may read 0 when there's no option/market volume, exactly
as the legacy "Fetch Live" does. Component labels match the legacy
("Put/Call (sectors)", "Market Breadth", "Sector Performance"; VIX value `T{t}-1D{d}-S{s}`).
The page also publishes the bridge on each load. Verified live: `compute_live`
reproduces the legacy component scores (VIX T8-1D1-S3=5, Breadth 10, Rotation 7
dual-momentum) and `regime_filter.evaluate_regime()` reads the written bridge.
Design/plans: [live-bridge](docs/plans/2026-06-14-live-sentiment-bridge-design.md) +
[web/legacy reconcile](docs/plans/2026-06-15-sentiment-web-legacy-reconcile-design.md)
(+ `-plan.md` files).

**Next session — remaining pages (Phase 3.3–3.5 of the webgui plan):**
- **Trade** (`/trade`): **DONE — 2026-06-16 (3-tier, `services/trade_svc` :8213).**
  See "Trade page (`/trade`) — DONE" below.
- **Portfolio** (`/portfolio`): **DONE — 2026-06-16 (3-tier, `services/portfolio_svc`
  :8212).** See "Portfolio page (`/portfolio`) — DONE" below.
- **Driver** (`/driver`): **DONE — 2026-06-16 (3-tier, `services/driver_svc` :8214).**
  See "Driver page (`/driver`) — DONE" below.
- Reuse the page pattern above; verify each engine function's real signature in
  the copied module before wiring (explorations of source can drift from copy).
- Optional follow-ups: (none outstanding for the Simulator — the **Replay** tab
  was migrated 2026-06-20: `compute.sim_replay` + `sim_replay` command +
  `cache:options:sim_replay` + the page's Replay tab. See "Simulator Replay tab"
  below.) (The Gamma intraday-heatmap **collector** now runs inside `options_svc`
  — see below — so the heatmap populates all session whenever the service is up.)

**Gamma intraday-heatmap collection (DONE — 2026-06-15; expanded 2026-06-18).** Intraday GEX history
(`gex_history.db`, read by the Gamma strike×time heatmap) is now collected by the
**options service** itself, not a separate window. `services/options_svc/scheduler.py`
`gex_due()` fires once per 1-min slot within 08:00–15:20 CT on trading days (mirrors
`gex_collector`'s window/cadence); the tick runs `handlers.collect_gex_history` →
`compute.collect_gex_snapshots`, which reuses `options-scanner/gex_collector.poll_once`
(engine compute + `gex_history_db.insert_snapshot`) VERBATIM with the shared
`_proxy.schwab_py_client`. It takes the collector's advisory lock
(`data/gex_collector.lock`) so a manually-run standalone `gex_collector.py` defers.
**Symbol universe + cadence (2026-06-18):** `poll_once` iterates
`gex_collector.collection_symbols()` = the index base (`$SPX`/`$VIX`/`SPY`/`QQQ`) ∪
`watchlist.get_scan_symbols()` (`Top 20.xlsx`), deduped/order-preserving, defensive
fallback to the base on watchlist failure — so the heatmap has live data for every
watchlist symbol. The poll interval dropped **5→2 min** (`POLL_INTERVAL_MIN=2`,
`scheduler._GEX_INTERVAL_MIN=2`, `gex_status.STALE_AFTER_SEC=240` — a
`test_scheduler.py` drift-guard asserts these stay in lockstep). The Gamma page's
symbol **dropdown** reads `cache:options:gamma_symbols` (= collected universe minus
`$VIX`, `$SPX` first), published once at scheduler startup by
`handlers.publish_gamma_symbols`. Term-structure collection stays SPX-only. Design/plan:
[design](docs/plans/2026-06-18-gex-watchlist-gamma-dropdown-design.md) /
[plan](docs/plans/2026-06-18-gex-watchlist-gamma-dropdown-plan.md).
**Root cause this fixed:** previously the only writer was the standalone
`gex_collector.py` window launched by `start_all.bat`; when that window died
(closed / sleep / double-launch lock contention) collection stopped silently and the
heatmap froze at the first snapshots ("no data past the first hour"). `start_all.bat`
no longer launches a separate collector window (the standalone script remains a manual
fallback). NOTE: this path does NOT republish the sentiment bridge (the old collector
loop did); `sentiment_svc` already republishes the bridge every 120 s, so the bridge
is unaffected.
**Off-hours display persistence (2026-06-24).** Collection still stops at ~15:20 CT,
but the Gamma **display** now holds the last session's candles + heatmap until the
**next trading day's midnight CT**, then clears (Fri persists through the weekend /
holidays until the pre-session midnight). Pure helpers
`scheduler.active_session_date(now)` (today once collection starts at 08:00 CT on a
trading day, else the most recent prior trading day) drives it — there is NO overnight
blanking, so the charts show PRE- and POST-market (`gamma_cleared` was removed); `gex_history_db.load_date_with_grid(conn,
symbol, view, date)` loads a prior session's rows by explicit local date
(`load_today_with_grid` now delegates to it). `compute.gamma_snapshot` returns
`None` (→ handler caches a graceful-empty view) in the cleared window and loads the
**active session date** for the heatmap; the candles re-compute from the live chain
(which off-hours returns the last session's data). DB-backed, so it survives a
service restart. **Term-structure** collection stays SPX-only, but the Gamma page's
**Term view** fetches the **next 5 expirations regardless of cadence** at render
(`compute._term_chain` widens the chain window — weekly/monthly-only names show 5
columns, not 1).

**Gamma Analyze — Claude infographic + auto-run (DONE — 2026-06-27).** The
`/options/gamma` **Analyze** button evolved from a copy-paste prompt dialog into a
live Claude call that renders an **infographic** in a new browser tab (and runs
itself four times a day). All in `services/options_svc/compute.py` (Tier-2) +
`webgui/main.py` route + `webgui/pages/options/gamma.py` (Tier-1). Pieces:
- **Forced tool-use call.** `compute.gamma_analyze(client=None, label=None)` bundles
  the live $SPX/SPY/QQQ GEX/Charm/DEX/Vanna blocks (`_gamma_blocks_for` →
  `build_summary_prompt_bundled`) and calls **Claude Sonnet 5** (`_ANALYZE_MODEL`,
  `thinking={"type":"disabled"}`, `max_tokens=1500`) forcing the **`submit_analysis`**
  tool (`_ANALYZE_TOOL`) — the reply is one structured tool_use block, never free
  text. `_parse_analysis` normalizes it (total over adversarial input, mirrors
  `decider.parse_decision`). The `anthropic` import is LAZY; the key resolves in
  `compute._anthropic_api_key` (env `ANTHROPIC_API_KEY` → gitignored
  `shared/anthropic_key.txt`) — **options_svc does NOT import driver_svc** (kept local
  to avoid the cross-app collision).
- **Infographic render (pure, testable).** `analyze_infographic_html(data, subtitle)`
  → a regime banner + **bias meter** (`_bias_meter_html`, −100…+100, sign-colored
  marker); a **per-index card** (`_index_card_html`) = a **price-level ladder**
  (`_ladder_svg`: spot vs gamma flip / call+put walls / expected-move band, with
  **label de-collision** so clustered levels stay readable) + **metric tiles**
  (`_metric_tiles_html`) + note + a **per-symbol what-if** (`_whatif_html`: ▲ rally /
  ▼ sell-off / ▬ chop); and a bottom **"Why is this happening"** section. Wrapped by
  `_analyze_doc` (the standalone dark doc + `_ANALYZE_CSS`). Output carries **no
  disclaimers** (system-prompt-enforced).
- **Code-authoritative Exp. move.** The engine's `calc_expected_move_from_chain` is a
  **0-DTE remaining-hours-to-close** EM (`hours_left` clamps to 0.1h off-hours / at the
  close → collapses to ~0; the bug that surfaced SPX EM ≈ 3). `compute._session_expected_move`
  computes a stable **1-day** EM (`spot · ATM_IV · √(1/365)`, reusing the engine's
  static `_find_nearest_exp_key`/`_get_atm_iv`) — used both in the prompt and as a
  per-symbol **override** of the model's copied value, so the displayed EM is
  engine-computed, not AI-echoed.
- **4×/day auto-run.** `scheduler.analyze_slot_due(now, ran_slots)` fires once per
  trading day per slot (CT: premarket 08:00 / open 08:48 [~18 min after the 09:30 ET
  open] / midday 11:30 / close 14:58 → 09:00/09:48/12:30/15:58 ET) within a 20-min
  grace (tolerates a missed tick / mid-window start, no stale backfill); the loop
  latches `analyze_ran` BEFORE the blocking call so a slow call can't double-fire.
  `handlers.run_scheduled_gamma_analyze(bus, slot)` runs `gamma_analyze(label=…)` and
  caches under that slot's **own key** (`CACHE_GAMMA_ANALYZE_SCHED` =
  `cache:options:gamma_analyze_{premarket,open,midday,close}`) — **separate** from the
  ad-hoc `cache:options:gamma_analyze` so a scheduled run never trips the page's
  `_watch_analyze` (which auto-opens a tab). The doc subtitle is stamped with the slot
  + CT time.
- **Serving + page.** `webgui/main.py` `@app.get("/options/analyze")` serves the cached
  HTML raw (`analyze_html`); `?slot=premarket|open|midday|close` (`analyze_view_for`)
  serves the auto-briefings, no slot → the ad-hoc result (mirrors `/options/explain`).
  `gamma.py` `_watch_analyze` opens `/options/analyze?v=<version>` in a new tab on the
  version-poll (like `_watch_explain`); a row of **Auto briefings** buttons opens each
  slot's `/options/analyze?slot=…` (enabled once that slot's version is present).
- **Graceful degradation everywhere** — no live chains (market closed) / no API key /
  API error / no tool reply each return a readable HTML page so the tab always opens.
- Tests: `services/options_svc/tests/{test_compute,test_handlers,test_scheduler}.py`
  (tool-use render, EM override, parse defensiveness, slot cadence, scheduled-cache
  isolation) + `webgui/tests/test_analyze_route.py`. Verified live end-to-end (real
  Claude call → infographic; EM SPX 2.96→45.9 / SPY 0.27→4.22 / QQQ 0.52→8.0).

**Gamma briefing history — store + CLI utility + in-app viewer (DONE — 2026-07-08).**
Every briefing above is now persisted so past briefings can be browsed/regenerated.
The design decision (deliberate): **store the STRUCTURED analysis payload, regenerate
the report on demand** — compact, queryable, and future-proof (old briefings re-render
in the current infographic design; the raw GEX numbers already live in
`gex_history.db`, so only the AI's structured read is kept). Pieces:
- **Store** `options-scanner/gamma_briefing_history_db.py` (Tier-3 SQLite,
  `repo_paths.GAMMA_BRIEFING_DB` = `options-scanner/data/gamma_briefings.db`). One row
  per **`(date, slot)`** (scheduled slots are unique/day → re-run REPLACEs; ad-hoc/
  manual use time-stamped slots like `adhoc-1842` so each is kept). Columns: date,
  slot, generated_at, symbol_scope, model, **bias**, **headline** (pulled out for
  cheap trend queries) + **`analysis_json`** (the full structured dict = source of
  truth). `connect`/`insert_briefing`/`get_briefing`/`briefings_for_date`/
  `list_briefings`/`purge(keep_days)`; every fn takes an explicit conn for temp-DB tests.
- **Persistence** `handlers._persist_briefing(res, slot, now)` (best-effort; only runs
  with a real `analysis` — degraded no-chains/no-key/error pages are skipped; never
  raises) wired into `run_scheduled_gamma_analyze` + the ad-hoc `gamma_analyze` command.
- **Report builder** PURE `compute.analyze_history_doc(briefings, title)` — combines N
  stored briefings into one standalone doc (each re-rendered via
  `analyze_infographic_html` under a date/slot header), reusing `_ANALYZE_CSS`.
- **In-app viewer.** `handlers.publish_gamma_briefing_index` publishes the metadata
  index **`cache:options:gamma_briefings`** (startup + after each persist); the
  **`gamma_history`** command (`run_gamma_history(bus, date, slot=None)`) regenerates a
  date's (or a single slot's) report → **`cache:options:gamma_history`**, served raw at
  **`/options/gamma-history`** (`webgui/main.py`). The `/options/gamma` page's
  **History picker** — a date dropdown (from the index via the pure `history_dates`) +
  a slot select (All / the four slots) + **Open** — enqueues `gamma_history` and opens
  the regenerated report in a new tab on the version-poll (mirrors `_watch_analyze`).
- **CLI utility** `services/options_svc/gamma_briefing_report.py` (run MANUALLY, never
  in a request path): `--list [--days N]` / `--date YYYY-MM-DD [--slot S]` (single day,
  slots combined) / `--range START END` / `--generate [--slot L]` (fresh run via
  `compute.gamma_analyze` → store → report; needs the proxy + ANTHROPIC key). Writes
  HTML under `options-scanner/data/gamma_reports/` (or `--out`).
- **Restart `options_svc`** so persistence + the index publish go live (the DB starts
  empty and fills going forward). gamma_briefing_history_db **7** + options_svc
  handlers/scheduler/compute + webgui **689** green; verified live end-to-end (index
  published, picker populated + Open→regenerate→serve, CLI `--list`/`--date` combined
  report). Built per-layer TDD.

**Options GUI polish batch (DONE — 2026-06-16).** A set of UI/UX fixes across the
Options section (design/plan:
[design](docs/plans/2026-06-16-options-gui-polish-design.md) /
[plan](docs/plans/2026-06-16-options-gui-polish-plan.md)):
- **Nav dropdowns persist** across navigation — `webgui/main.py` stores each
  `ui.expansion` open/closed state in a module-level `_NAV_OPEN` dict (single-user,
  like `_CACHE`); first visit still auto-opens the active group.
- **Scanner**: signals colored by quality via `score_zone_color` (zones match the
  speedometer) on a `body-cell-composite_score` slot; the VIX term label is plain
  English via `term_text` ("VIX term: Contango (near-term calm) · as of 1:32 PM");
  newly-appeared signals get a **NEW** badge via a session diff (`mark_new`,
  page-side, resets on reload — both 0-DTE + Swing tables).
- **Paper Trades**: the detail panel now re-renders for the selected row on each
  data refresh (`paper.py` tracks `sel_id`, re-calls `detail_panel.update`).
- **Captured Signals**: drift shown as `x.xx` (numeric value kept for sort, a
  `body-cell-score_drift` slot renders `toFixed(2)`); rows colored by
  recommendation (`rec_color`: HOLD amber / TAKE_PROFIT green / CUT red).
- **Calculator**: P&L grid range is symmetric about spot and widened to span the
  strikes — `compute.symmetric_price_range` in `calc_compute` (engine untouched).
- **Symbol inputs auto-select on focus** (calculator/gamma/simulator/swing) via the
  shared `webgui/pages/options/inputs.py` `select_all_on_focus` helper.
- **Gamma status bar**: `compute.gex_status_view` (collector status via
  `gex_status.classify_collector_status` + `gex_history_db.last_snapshot_age`, plus
  last/next 1-min scan within 08:00–15:20 CT, reusing the scheduler's `_GEX_*`
  constants) is published each 30 s tick by `handlers.publish_gex_status`
  (`cache:options:gex_status`); `gamma.py` shows **Collector / Last scan / Next scan**
  alongside the existing "Next refresh" countdown.
- Pure transforms are unit-tested (webgui + options_svc suites).

> **Paper auto-manage (DONE — supersedes the old "manual-only" TODO).** The
> `options_svc` scheduler reprices + auto-closes paper positions on its own. **Two
> distinct cadences (changed 2026-07-10):** the **MANUAL Paper Portfolio** runs
> **entry + manage once at the top of each hour, 09:00–14:00 CT** (last run 14:00 /
> 2pm; **NO 15:00 run** at the regular-session close) — `scheduler.paper_cycle_due`
> (trading days only, once-per-hour within a 20-min grace, mirrors
> `analyze_slot_due`) → `handlers.run_paper_entry_and_manage` (opens new paper
> trades from current captured signals via `compute.run_entry_cycle`, guarded on an
> existing account + its own try/except so an entry failure can't skip manage, then
> `run_manage_and_refresh`). The **isolated DRIVER paper account** stays on the
> old **5-min** `manage_due` slot (`run_driver_manage_and_refresh`). Both windows
> are trading-day/market-hours gated. The "Run Manage Cycle" button is still a
> manual trigger of the manage cycle. (Tick cadence reference: each 30 s scheduler
> tick also runs `refresh_header` + `publish_gex_status`; the 2-min GEX collect +
> 5-min driver manage are slot-gated within their CT windows. **Trade-off to know:**
> the manual account's live P&L + target/stop auto-close now update **hourly**, not
> every 5 min.)

**Gamma panels / walls / flicker batch (DONE — 2026-06-16).** Four fixes from a
live-screenshot review (design/plan:
[design](docs/plans/2026-06-16-gamma-panels-walls-flicker-design.md) /
[plan](docs/plans/2026-06-16-gamma-panels-walls-flicker-plan.md)):
- **Proportional panels**: `gamma.panel_flex(n_cols)` sets the bar/heatmap column
  flex ratio from the intraday snapshot count (heat fraction lerps 0.28→0.70 over
  ~82 five-min slots), so the heatmap expands and the bars shrink as the session
  fills in. Term view → bars full width, heatmap hidden.
- **GAMMA dead space**: `gamma.significant_strikes(bars, frac=0.03)` feeds the
  shared y-range from strikes with |net| ≥ 3 % of peak, cropping GEX's near-zero
  edge strikes (other views were already tight). Both panels share the range.
- **Flicker**: the two Highcharts elements are created **once** and updated in
  place (`el.options = …; el.update()`); `_render_view` no longer
  `clear()`s/rebuilds the canvas. Message labels toggle via `set_visibility`.
  (Now `ui.highchart`; the bar↔Term kind switch recreates via `_set_chart`.)
- **Single walls**: `services/options_svc/compute.gamma_walls` returns one Put +
  one Call wall via the engine's `get_directional_walls` (call = max-call-GEX
  strike above spot, put = most-negative-put-GEX below) instead of the old
  `get_gex_walls`/`get_dex_walls` top-5; the page renders them unchanged. DEX
  per-strike map remapped `dex`→`gex` for the picker.

**Trade page (`/trade`) — DONE (2026-06-16, born 3-tier — Phase 4).** The Trade
Analyzer was built directly on the 3-tier model (no in-process stage). New
service `services/trade_svc` (:8213, `SERVICE_PORTS["trade"]`), **on-demand only
(no scheduler)** — the page enqueues an `analyze` command on `cmd:trade`; the
service computes and writes `cache:trade:analysis` (one latest-result view, like
sim/calc) + publishes `events:trade:analysis`; the page version-polls and
repaints (persists across nav). Pieces:
- **Contract** `shared/contracts/trade.py:TradeAnalysis` — validates the analyze
  envelope (symbol + verdict/momentum/sector sub-dicts) before caching.
- **`trade_svc/compute.analyze(symbol)`** ports the legacy desktop
  `trade_analyzer.py` `analyze()` flow (the un-copied orchestration): fetch MTF
  data via the proxy (`_proxy.schwab_client`; 1/5/15/60-min + daily, SPY +
  sector-ETF daily), compute indicators **reusing `shared/analysis_lib/technical`**
  (`calculate_ema_alignment`/`calculate_rsi`/`calculate_adx`/`calculate_macd`/
  `calculate_vwap`/`calculate_relative_volume`/`calculate_volume_profile`), build
  `PositionInputs`/`InvestorInputs`, and score the copied
  `trade-analyzer/src/analysis/recommendation` verdict engines. **Defensive**
  (degrades to an `errors` payload, never raises). `technical` is imported
  **standalone** (its dir on `sys.path`) to dodge the `shared.analysis_lib`
  package `__init__` (which eagerly imports a broken `schwab_client`); safe
  because the service is its own process (same isolation `sentiment_svc` uses for
  `scoring`). Symbol→sector via a built-in large-cap map (`_SYMBOL_SECTOR`) with a
  **neutral** SectorStrength fallback when unknown.
- **`trade_svc/handlers.analyze`** runs compute → `TradeAnalysis` gate → cache +
  publish; `handle_command` dispatches `analyze`. **`trade_svc/app.py`** =
  `make_app("trade", command_handler=…)` (no scheduler).
- **Page** `webgui/pages/trade.py`: symbol input (+Enter) → Analyze; renders a
  header (symbol/price/bias/vol), two verdict cards (verdict colored BUY-green/
  HOLD-amber/SELL-red, score, top reasons, ⛔ hard gates, expandable factor
  breakdown table), MTF-alignment card, momentum strip, sector card. Pure builders
  (`verdict_color`/`bias_color`/`momentum_rows`/`breakdown_rows`/`alignment_rows`)
  unit-tested in `webgui/tests/test_trade.py`.
- **Fundamentals wired via the proxy (2026-06-16).** `compute.analyze` fetches
  Schwab fundamentals through a new proxy endpoint
  `GET /instruments?symbol=X&projection=fundamental`
  (`SchwabProxyClient.get_fundamentals` → unwraps `instruments[0].fundamental`)
  and parses them with `parse_schwab_fundamentals`. `InvestorVerdict` now runs on
  real data and `fundamentals_available` = `Fundamentals.is_sufficient()`; the
  page shows a **Fundamentals card** (P/E, PEG, rev/EPS growth, ROE, margin
  trend) when available, else the insufficient-data note. **Parser is a superset**
  (`trade-analyzer/src/analysis/fundamentals.py`): the *real* Schwab fields
  (`revChangeTTM`/`epsChangePercentTTM` in percent→fraction, `returnOnEquity` as
  percent via a `>2` magnitude heuristic, `operatingMarginTTM` vs `MRQ` for the
  margin trend) are primary, the legacy speculative names are fallback (all old
  tests stay green). The instruments payload has **no** next-earnings date / EPS
  surprises / guidance / FCF, so those degrade to None (the Position earnings gate
  never fires; `days_to_earnings` is None). Fetch is defensive — a proxy/parse
  failure degrades to insufficient-data HOLD, never raises.
- Tests: `services/trade_svc/tests` (compute/handler/app) + `webgui/tests/test_trade.py`.
  Design/plan: Phase 4 of the [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**Validated swing evaluation (Trade page) — DONE (2026-06-28).** The `/trade`
**Position** verdict's hand-weighted, never-validated swing scoring is replaced by a
**backtested, IC-weighted cross-sectional factor model** whose weights are learned from
forward returns. Investing (months+) is **deferred** — it can't be backtested without a
point-in-time fundamentals source. Honest framing: the model is *validated* (it shows a
small **positive out-of-sample IC** + a calibrated quintile spread), not *guaranteed* —
the edge is thin and regime-dependent. Architecture = **offline fit → versioned artifact
→ online score** (the C-ready shape from the design: a single regime key `"all"` today;
`"trend"/"chop"/"highvol"` drop into the same loader/scorer later). Pieces:
- **PURE factor library** `trade-analyzer/src/analysis/factors.py` — each factor is
  `(daily_df) → pd.Series` over a daily-OHLCV frame, **sign-corrected so higher =
  bullish**, **causal** (the value at bar *t* uses only data ≤ *t* — no look-ahead).
  Winsorization/standardization are NOT per-factor; they happen **cross-sectionally at
  scoring** (`zscore_by_date`, across symbols per date → no temporal leakage). The live
  value is the Series' last element, so the SAME code feeds the backtest and the live
  scorer (no drift). The 10 registered factors: **mom_12_1** (12-1 intermediate
  momentum, skip-month), **mom_6_1**, **pth** (price ÷ 252-day high — George & Hwang
  anchoring), **str_5d** (short-term 5-day reversal, sign-corrected), **vol_adj_mom**
  (3-mo return ÷ realized vol), **trend_quality** (distance above the 50/200-EMA stack),
  **low_vol** (−60-day realized vol), **rs_spy** (63-day excess return vs SPY),
  **rs_sector** (63-day excess vs the sector ETF), **turnover** (volume ÷ 63-day avg —
  the conditioning var). The `FACTORS` registry is the single source of truth; the
  **harness's IC decides which earn weight** (not the hand-picked list).
- **OFFLINE harness** `trade-analyzer/src/analysis/backtest.py` (pure — operates on a
  `(date,symbol)`-MultiIndex panel + forward Series, no I/O): `factor_ic`
  (per-date cross-sectional **Spearman rank IC** → mean_ic/icir/n_days; ICIR only
  trusted with ≥5 IC-days + real dispersion), `quantile_spread` (top-minus-bottom),
  `zscore_by_date` (cross-sectional winsorize @ 2/98 + standardize, look-ahead-free),
  **`signed_ic_weights`** (the production weighter: `weight_k = mean_ic_k / Σ|mean_ic|`,
  **keeping the sign**, above an n-independent noise floor — so a wrong-sign-but-
  predictive factor like low_vol carries a **NEGATIVE** weight and contributes with the
  correct sign; chosen over ICIR-/t-stat-weighting because those are n-dependent and
  unstable across small per-fold samples), `composite`, **`walk_forward`**
  (rolling train→test, weights fit per train window, composite OOS IC on the unseen test
  window; train/test never overlap), `calibrate` (bucket composite into quantile bands →
  per-band score range + mean forward + hit-rate `P(fwd>0)`, **isotonic-smoothed** so a
  higher-ranked band never shows a lower stat). The orchestrator
  **`trade-analyzer/fit_swing_model.py`** (run manually/periodically, **NEVER imported by
  a service**) pulls ~78 liquid symbols' (curated `UNIVERSE_SECTOR` → sector ETF) **5-yr**
  daily history via the proxy (concurrent), builds the panel with **20-day forward
  EXCESS-return-vs-SPY** labels (the prediction target — factors are causal so the future
  H-bar label is legitimate), runs the engine (train/test/step **378/63/63**), and writes
  the artifact + a markdown research report.
- **Artifact** `trade-analyzer/data/swing_model.json` (gitignored under `data/`; path
  `repo_paths.SWING_MODEL`, report `SWING_MODEL_REPORT`) — `version` (the fit date),
  `fit_universe_n`, `horizon`, and per regime: signed **`weights`**, **`factor_ic`**
  (mean_ic/icir/n_days per factor), the cross-sectional **`norm`** (per-factor
  time-averaged winsorized cross-sectional mean/std — the basis the calibration was built
  on), the score→outcome **`calibration`** (5 quantile bands → score range / mean_fwd /
  hit_rate / n), and **`oos_ic`** + `oos_ic_by_fold` + `n_folds`.
- **LIVE scorer** `services/trade_svc/swing_model.py` (on-demand, defensive → returns
  `None` so `analyze()` falls back to the legacy verdict on ANY failure): loads the
  artifact, z-scores the symbol's current factors **CROSS-SECTIONALLY against the current
  universe snapshot** (PRIMARY — re-centered to today's regime, matching how the per-date
  calibration was built; the artifact's time-averaged norm is a FALLBACK only, used when
  the snapshot is too thin, <5 names), **clips z to ±3** (`Z_CLIP` —
  matches the fit's per-date 2/98 winsorization; stops a live outlier like a turnover
  spike hijacking the signed composite), `composite = Σ signed_weight × z`, then reads the
  **calibration band** containing the composite → **BUY** (top band) / **SELL** (bottom) /
  **HOLD**, a band-quantile **percentile**, the band's expected forward return + beat-SPY
  hit-rate, and per-factor contributions (z · weight · contribution · historical IC).
  `analyze()` fetches **2-yr daily** so every long-warmup factor (mom_12_1 needs 273 bars;
  pth/low_vol roll 252) populates at the last bar.
- **Fix — "Position always BUY" (2026-06-28):** the live scorer originally used the
  artifact's **time-averaged** norm as the PRIMARY z-basis, which does NOT re-center to the
  current regime. In this elevated-momentum/-vol bull period every symbol's z shifted
  positive (most starkly `low_vol`: tiny norm std × big negative weight → a saturated ±3 z
  → ≈ +1.0 contribution that dominated), so the composite cleared the top band and **every
  symbol scored BUY**. Fixed by re-centering to the **current cross-section** (the snapshot,
  PRIMARY) — matching the per-date calibration basis — and **widening** that snapshot to the
  artifact's `fit_universe` (~78 names, was ~17) for stable z's. Verified live: the universe
  now scores ≈ **8 BUY / 49 HOLD / 8 SELL** (was ~all BUY); NVDA/PLTR flipped BUY → HOLD.
- **Contract / cache:** additive optional **`swing_model`** block on `TradeAnalysis`
  (→ `cache:trade:analysis`); `compute.get_universe_snapshot()` lazily rebuilds a daily
  **`cache:trade:universe_factors`** snapshot ({factor: [values across the artifact's
  **`fit_universe`** ~78-name fit cross-section]}) as the PRIMARY cross-sectional scoring
  basis — `_swing_universe()` reads `fit_universe`, falling back to the smaller `_MK_UNIVERSE`.
- **UI** `webgui/pages/trade.py` (Position card): the validated swing verdict is the
  **headline** + a calibrated outcome line (e.g. `90th pctile · +1.3% excess / 20d · 52%
  beat-SPY` via `swing_headline`), a **"Why — validated factors"** expander (per-factor z
  / weight / contribution / historical IC + the model version & OOS IC via
  `swing_contrib_rows`/`swing_model_meta`), and the **legacy heuristic** verdict tucked
  into a collapsed **"Legacy heuristic"** expander (`_legacy_verdict_body`). Falls back to
  the legacy body verbatim when `swing_model` is absent. Investor + Markov cards
  unchanged — **the Markov card still forecasts the legacy technical-momentum
  `composite_daily`**, NOT the validated composite (a separate lens; a documented
  coexistence, not a bug).
- **Validated result (current fit, `version` 2026-06-28):** fit universe **78** symbols,
  horizon **20d**, **13** walk-forward folds. Composite **OOS IC ≈ +0.0367** — but **5 of
  13 folds are NEGATIVE**, so the edge is thin and **regime-dependent**. Calibration: top
  quintile (band 4) ≈ **+1.35% / 4 wk at 52.3% beat-SPY**, bottom (band 0) ≈ **−0.80% /
  43.3%**. Signed weights (the ONLY factors that cleared the |IC| floor): **low_vol
  −0.34** (reclaimed with a NEGATIVE weight — high-vol names outperformed in this 5-yr
  large-cap bull period, IC −0.066), **mom_12_1 +0.21**, **mom_6_1 +0.17**,
  **trend_quality +0.12**, **rs_sector +0.08**, **turnover +0.07** (pth / str_5d /
  vol_adj_mom / rs_spy fell below the floor → weight 0).
- **Honest caveats (state these):** the edge is small + regime-dependent; it leans on
  **low_vol's inverted sign** reflecting this bull-ish large-cap period (it could flip);
  **survivorship bias** (the fit universe is today's liquid survivors) + **regime
  non-stationarity** (a 5-yr fit may not hold forward); the LIVE cross-section
  (~watchlist) is thin vs the fit universe. Validation reduces self-deception, it does not
  guarantee forward performance — **re-run `fit_swing_model.py` periodically**.
  **Regime-conditional weighting (Option C)** is the planned next step (same harness, new
  regime keys); ML (B) is gated on universe expansion.
- Tests: factor library + harness + live scorer + contract + page builders are unit-
  tested (TDD by layer, the design's acceptance gate = positive OOS IC + a meaningful
  spread on real data). Design/plan:
  [design](docs/plans/2026-06-22-swing-validated-evaluation-design.md) /
  [plan](docs/plans/2026-06-22-swing-validated-evaluation.md).

**Markov 2.0 (Trade page) — CARD REMOVED from the UI (2026-06-28); engine retained.**
The Markov Forecast card was **deleted** from `/trade`. It forecast the **LEGACY**
technical composite, so it contradicted the validated Position read (it showed
"Strong-Bear" while the validated model said BUY on the same 1–8 wk horizon).
`compute.analyze()` no longer builds the block either — it was a wasted pooled-prior
rebuild + history fetch per request for a block nobody rendered (pinned by
`test_analyze_does_not_build_markov_block`). **Retained but unused:** the PURE engine
(`trade-analyzer/src/analysis/markov.py`, 34 tests), the compute helpers
(`reconstruct_daily_composite`/`_symbol_band_series`/`build_pooled_prior`/`get_prior`/
`build_markov_block`), and the additive `markov` contract field. **Reviving it against
the VALIDATED composite is NOT a small change:** that composite is a per-date
CROSS-SECTIONAL score, so a symbol's history is *not* reconstructable live — the OFFLINE
fit would have to emit per-symbol transition matrices, and non-fit-universe symbols would
fall back to a generic pooled forecast. The historical description follows.
A probabilistic, forward-looking
layer on the `PositionVerdict`: model the composite score as a 5-state Markov chain,
surface where it's heading, and apply a bounded tilt to the score (the BUY/HOLD/SELL
label is untouched). Pieces:
- **States = composite-score bands** anchored at the decision boundaries (S1
  Strong-Bear `[-100,-40)` = SELL · S2 Weak-Bear `[-40,-15)` · S3 Neutral `[-15,15)`
  · S4 Weak-Bull `[15,40)` · S5 Strong-Bull `[40,100]` = BUY), so a forecast reads
  directly as P(cross into BUY/SELL).
- **PURE engine** `trade-analyzer/src/analysis/markov.py` (no I/O): `classify_band`,
  `count_matrix` (NaN/None breaks the chain), `pooled_prior`, `shrink`
  (Dirichlet-multinomial, α≈30), `project` (`dist·P^n`), `forecast`
  (P(BUY)/P(SELL)/E[score]/persistence/**stationary via power-iteration** — robust to
  reducible chains), `row_confidence`, `drift_tilt` (clamped ±12, confidence-weighted).
- **Daily score reconstruction** `trade_svc/compute.reconstruct_daily_composite(daily,
  spy, sector_hist)` builds a parallel **"Markov base score" (`composite_daily`)** from
  ONLY daily-reconstructable factors (the live verdict's intraday VWAP/rel-vol/MTF-EMA
  can't be rebuilt for past bars), renormalized to 100, fully vectorized; a missing-close
  bar → NaN (no observation). The chain runs on `composite_daily`, which also **dissolves
  the feedback loop** — the tilt is added to the displayed `composite_full`, never to
  `composite_daily`, so it can't feed back into the matrix.
- **Hybrid matrix:** per-symbol day-to-day counts `shrink`-blended toward a pooled prior
  built across a curated 17-symbol universe (`build_pooled_prior` / `get_prior`, cached at
  `cache:trade:markov_prior`, lazy daily refresh, uniform fallback on failure).
- **Wiring:** `build_markov_block` runs **defensively** inside `compute.analyze()`
  (any failure → `markov: None`, verdict unchanged) and rides an additive optional
  `markov` block on the `TradeAnalysis` contract → `cache:trade:analysis`.
- **Page** `webgui/pages/trade.py`: the Markov Forecast card sits in the **verdict row as
  the third equal-width card** alongside **Position · 1–8 wk** and **Investor · months+**
  (all `flex-1 min-w-[280px]`, `items-stretch` — three equal frames in one row, wrapping on
  narrow screens). The row and its three cards are **persistent** and the two verdict cards
  are **refilled in place** (`_fill_verdict_card`) so the Markov card's Highcharts element
  is never destroyed by a `clear()` (it's built once per the ESM-import-map gotcha, with an
  explicit `chart.height` + reflow-on-show, updated in place). The card holds a band chip, a
  stacked-area band-probability-over-horizon chart (now/5/10/20d), per-horizon
  P(BUY)/P(SELL)/E[score], and a drift/tilt/persistence line; the Position card headline
  shows the `markov_adjusted_score` (with a `base … · Markov …` subtitle) — **the
  BUY/HOLD/SELL label is unchanged** (the tilt is advisory on the score). When `markov` is
  absent the card hides and the row falls back to Position + Investor (two equal cards). Pure
  builders (`markov_band_chip`/`markov_metric_rows`/`markov_drift_row`/
  `markov_forecast_figure`/`position_headline`) unit-tested.
- Design/plan: [design](docs/plans/2026-06-21-markov-trade-analyzer-design.md) /
  [plan](docs/plans/2026-06-21-markov-trade-analyzer.md).

**Autonomous Driver — Claude decision layer (`/driver`) — DONE (2026-06-24).**
The driver's hardcoded `trade_selector` rule tree is replaced by a strategy-agnostic
**Claude decision layer** (autonomy **level B** — autonomous **paper** execution, NO
approval gate) that pursues **net $500/day** by selecting + sizing **defined-risk
option credit spreads (PCS/CCS/IC)** from the scanner. *Honest framing:* it **targets**
$500/day (presses when edge exists, **stands down** when it doesn't, **banks** the day
at +$500, hard-capped on the downside) — no decision-maker can guarantee it. Pieces
(all `services/driver_svc`):
- **PURE safety core** `guardrails.py` (the load-bearing module — the model PROPOSES,
  this code DECIDES, it never trusts the model with risk): `is_allowed` (defined-risk
  allowlist PCS/CCS/IC, structure read from `structure`→`type`→`trade_type` so the RAW
  scanner signal classifies correctly — real signals store it in `type`),
  `clamp_quantity` (resize to `min(request, per-trade cap, daily-budget cap)`, floored;
  0 on unaffordable / NaN / inf), `halt_state` (banked-$500 → daily-loss-cap → VIX>25),
  `apply_guardrails` (halt → stand-down → per-trade resolve-from-menu / allowlist /
  clamp / max-trades+concurrent, tracking the remaining budget across trades).
  Exhaustively unit-tested.
- **Decider** `decider.py`: `build_packet`'s model-facing prompt + a forced
  `submit_decision` tool-use call to **Claude Opus 4.8** (`anthropic` SDK, LAZY import,
  key via `api_keys.anthropic_api_key()` — env / gitignored `shared/anthropic_key.txt`);
  `parse_decision` is total over adversarial JSON and **every failure → stand-down**
  (never raises, never trades blind).
- **Compute**: `build_packet` (top-N composite-scored menu + day-P&L gap-to-target +
  `menu_by_id`→raw signal; day-P&L from the paper snapshot's `session_pnl`; real
  scanner keys `type`/`expiration`/`pop_pct`) + `run_cycle` (`build_packet → decide →
  apply_guardrails`, never raises) + `fetch_market_context`.
- **Handlers**: `run_autonomous_cycle` (gate on control → run_cycle → enqueue
  `cmd:options` `paper_create` per survivor [a `source="driver"` COPY + the CLAMPED
  qty; each enqueue isolated so a mid-loop failure can't skip the latch/publish] →
  latch the kill-switch on halt → publish `AutonomousState`); control read/write;
  `cycle`/`enable`/`disable`/`stop` commands.
- **Scheduler**: `checkpoint_due` (autonomous **entry window 09:45–15:30 ET**, 30-min slots — open's first ~15 min skipped + no new entries in the last 30 min; tuned 2026-06-29 to match the daily playbook; **trading days only** — weekend + NYSE-holiday gated via the service's own `_HOLIDAYS` (2026-07-05), so no Claude call fires on a market holiday) + `should_rearm`
  (next-day halt clear) wired into the loop on the executor, each branch guarded; the
  legacy 09:28 `morning_due`→`run_morning` path coexists (autonomy is gated OFF by
  default so they don't conflict).
- **Contracts** `DriverControl` (`cache:driver:control` — master switch + STOP latch)
  + `AutonomousState` (`cache:driver:autonomous` — the monitor view). Tunables in
  `settings.py` (`DAILY_TARGET=500`, `PER_TRADE_MAX_RISK=3000`, `DAILY_RISK_BUDGET=12000`,
  `MAX_CONCURRENT=10`, `MAX_TRADES_PER_CYCLE=5`, `VIX_MAX=35`, `MENU_TOP_N=15`,
  `DAILY_LOSS_HALT=1500` — the **"Very Aggressive" risk profile (2026-07-02, user choice)**:
  the driver presses toward $500/day and tolerates real drawdown (~half the $25k paper book
  deployable, ~12%/trade, a $1,500 daily-loss stop = 3× the target). **All risk knobs now live
  in `settings.py`**; `compute._daily_max_loss` reads `DAILY_LOSS_HALT` first (legacy
  `config.RISK_LIMITS` is only a fallback — this replaced the old $250 halt that stopped the
  day after one losing $SPX). The guardrail evaluates affordability in per-contract dollars
  (`guardrails.CONTRACT_MULTIPLIER=100` — the scanner's `max_loss` is PER-SHARE) and the paper
  open path uses its own matching `options_svc.compute._DRIVER_MAX_RISK_PER_TRADE=3000` so the
  user's MANUAL account stays at `config_paper.MAX_RISK_PER_TRADE=250`. The `decider._SYSTEM`
  prompt is an AGGRESSIVE mandate (take reasonably-scored trades to build toward the target;
  stand down only on genuinely poor edge / hostile conditions). `MODEL="claude-opus-4-8"` (build
  default; the **`DRIVER_MODEL`** env var / gitignored `shared/driver_model.txt` override it
  per-deployment — e.g. `claude-sonnet-5`), `CHECKPOINT_MIN=30`).
- **Page** `webgui/pages/driver.py`: a Tier-1 **monitor + override** (Enable/Disable,
  confirm-gated **STOP**, **Run now**, $500 progress, open-driver-positions, newest-
  first decision-log audit) reading `cache:driver:autonomous`/`control` + version-
  polling; engine-free (3-tier rule). The legacy approval queue + Performance UI stays.
**Real `/ES` `/MES` FOP shelved** — Schwab can't serve FOP chains or place futures/FOP
orders (equity+option only); see [[schwab-api-instrument-limits]]. v1 executable
universe is scoped to scanner spreads (equities + Claude-managed exits + live/level-C
are v2 — see the design doc). **Master switch defaults OFF** — set `ANTHROPIC_API_KEY`
and Enable on `/driver` to run it; with no key it safely stands down. driver_svc **130**
+ contracts **34** + webgui **483** green (incl. a Redis-driven e2e proving qty=3
clamps to 1 through the real pipeline; built subagent-by-subagent w/ per-unit TDD +
spec/quality review). Design/plan:
[design](docs/plans/2026-06-24-driver-autonomous-claude-decider-design.md) /
[plan](docs/plans/2026-06-24-driver-autonomous-claude-decider-plan.md).

**Driver isolated paper account + performance scorecard (`/driver`) — DONE
(2026-06-25).** The autonomous Driver now trades into — and grades itself against — its
**own dedicated paper book**, isolated from the user's manual paper account, so its real
performance is measurable. This also fixed a latent **write/read split**: the Driver
*wrote* `paper_create` into the flat LEDGER (`trades.db` — no repricing/auto-manage, so
its trades were inert rows and its `source="driver"` tag was dropped) but *read* its
day-P&L/$500-target/halt from the user's ENGINE account (`paper_account.db`) — measuring
the wrong book and never repricing its own trades. Pieces:
- **Dedicated account.** `repo_paths.DRIVER_PAPER_DB =
  options-scanner/data/paper_account_driver.db` ($25k start). Every `paper_account_db`/
  `paper_engine` fn already takes a `db_path`, so a second DB file is a fully independent
  single-account store — **zero schema change** (the `CHECK(id=1)` single-account
  constraint is sidestepped by using a separate file).
- **`services/options_svc` (owns ALL `paper_engine` imports):**
  `compute.open_driver_position(signal, qty)` (extracted from `run_entry_cycle`'s
  per-signal block — simulated fill → re-size on the ACTUAL fill credit → reserve BP →
  `paper_engine._record_order` (preserves the `entry_order_id` link) → `insert_position`;
  the guardrail qty is a **CEILING**, `open_qty = min(clamped, sized-on-fill)`; never
  raises); `run_driver_manage_cycle()` (`paper_engine.run_manage_cycle(db_path=
  DRIVER_PAPER_DB)` — reprice + auto-exit + session roll + halt, try/except never-raise);
  `driver_account_view()` / `driver_account_perf()`; the PURE
  **`driver_perf.build_scorecard(positions, snapshot)`** (# trades, open/closed, **win
  rate**, **profit factor** [None when no losses yet → render "—"], avg win/loss,
  realized/unrealized/total P&L, best/worst [drawn from the None-pnl-excluded set],
  **P&L by symbol & by strategy**). Handlers: `refresh_driver_paper` publishes **both**
  views (**NO rescue overlay** — that reads the manual book) + `run_driver_manage_and_refresh`;
  commands `driver_paper_create` / `driver_paper_manage` / `driver_paper_reset`.
  Scheduler: the 5-min `manage_due` slot reprices the driver account in its **OWN guarded
  branch** so a driver-side failure can't skip the manual refresh.
- **`services/driver_svc` (engine-free re the paper account — only enqueues + reads
  cache; it must NOT import `paper_engine`/`paper_account_db`, which transitively pull
  `scoring`/`signal_repricer` → the documented cross-app module collision):**
  `run_autonomous_cycle` enqueues **`driver_paper_create`** (not `paper_create`), reads
  day-P&L + open positions from `cache:options:driver_paper_account`
  (`CACHE_OPT_DRIVER_PAPER`), and attaches the scorecard (`cache:options:driver_paper_perf`)
  to the published **`AutonomousState.perf`** (new additive field). `build_packet`
  open-position attribution is correct-by-construction (the whole driver DB is the
  driver's — the dead `source=="driver"` filter falls back to the full account).
- **Cache views:** `cache:options:driver_paper_account` (snapshot + open positions) +
  `cache:options:driver_paper_perf` (the scorecard) — published on each
  `driver_paper_create` and every 5-min manage tick.
- **Page** `webgui/pages/driver.py`: the monitor's Day-P&L bar / summary / open positions
  **re-point** to `cache:options:driver_paper_account` (was the manual `paper_account`); a
  new **Performance scorecard card** (pure builders `scorecard_headline_chips` /
  `scorecard_quality_chips` / `scorecard_symbol_rows` / `scorecard_strategy_rows` /
  `best_worst_text`) renders `cache:options:driver_paper_perf` directly (live — refreshes
  on the 5-min tick, not just the 30-min cycle). Engine-free (3-tier rule).
- **PAPER ONLY** — `config.PAPER_TRADE` stays True; the driver never flips it. The
  historical ledger MU trades are left where they are (the driver starts fresh in its
  dedicated account). options_svc **285** + driver_svc **138** + contracts **35** +
  webgui **510** green (incl. a Redis-driven e2e proving `driver_paper_create` lands ONLY
  in the driver DB — manual account untouched — and both views + the scorecard reflect it,
  with a non-vacuity leak check). Built subagent-by-subagent (TDD, two-stage spec+quality
  review per unit). Branch `Using_Highcharts`. Design/plan:
  [design](docs/plans/2026-06-25-driver-isolated-paper-account-design.md) /
  [plan](docs/plans/2026-06-25-driver-isolated-paper-account-plan.md).

**Driver page (`/driver`) — DONE (2026-06-16, born 3-tier — Phase 5).** The
order-approval queue was built directly on the 3-tier model. New service
`services/driver_svc` (:8214, `SERVICE_PORTS["driver"]`), **scheduled (09:28 ET)
+ command-driven**. The order-approval queue is a Redis Streams flow: the morning
pipeline produces a *pending* approval cached at `cache:driver:approvals`; the
GUI APPROVE/SKIP buttons enqueue `cmd:driver` commands the consumer acts on.
Pieces:
- **Contracts** `shared/contracts/driver.py`: `ApprovalState` (the
  pending/decided morning payload — grade, grade_reasons, conditions, pnl, a
  loose `proposed_trades: list[dict]`, status pending/no_trade/error/approved/
  skipped, decision, results, reasons, error) + `PerfReport` (summary + trades).
  Validate the envelope shape before caching, like `ScanResult`/`TradeAnalysis`.
- **`driver_svc/compute`** ports the legacy `morning_agent.run_morning_agent()`
  orchestration **minus** its side effects (no `pending_trade.json` write, no
  HTTP post to the :8300 approval server): `run_morning()` calls the SAME
  building blocks (`check_service_health`/`fetch_all_ml_signals`/
  `fetch_gex_snapshot`/`fetch_market_conditions`/`fetch_current_pnl`/`grade_day`
  → `trade_selector.select_trades`) and **returns** the payload; `execute()` →
  `order_executor.execute_trades` (**`config.PAPER_TRADE=True` → simulated**, not
  modified); `build_perf_report()` → `perf_report.build_report`. All **defensive**
  (degrade to an `error`/empty payload, never raise). claude-driver engines are
  imported **standalone** (its dir on `sys.path`) — safe because the service is
  its own process (same isolation `sentiment_svc`/`trade_svc` use; note: importing
  both `driver_svc` and `trade_svc` engines in **one** process re-triggers the
  documented `config` module-name collision, so run service test suites **per
  folder**, never `pytest services` over all of them).
- **`driver_svc/handlers`**: `run`→cache pending approval; `approve`→**only if
  still pending**, `execute` the proposed trades + re-cache as `approved` w/
  results; `skip`→mark skipped; `perf`→cache `cache:driver:performance`. Each
  validates + caches + publishes an event. **`driver_svc/scheduler`**:
  `morning_due(now, last_run_date)` fires `run_morning` once/day at/after 09:28 ET
  on weekdays (holiday short-circuit lives in `compute.run_morning`) + keeps the
  perf view warm; **the scheduler NEVER executes orders** (only an explicit
  `approve` does). **`driver_svc/app`** = `make_app("driver", scheduler=loop,
  command_handler=…)`.
- **Page** `webgui/pages/driver.py`: Run-morning-agent + Refresh-performance
  buttons; an approval card (grade chip, conditions strip, grade rationale,
  per-bucket proposed-trade cards) with **APPROVE (confirm dialog)** / **SKIP**
  when pending, else a decision banner (approved/skipped/no_trade/error); a
  Performance section (summary line + trade table). Version-polls
  `driver:approvals` + `driver:performance`; persists across nav. Pure builders
  (`grade_color`/`status_text`/`condition_rows`/`proposed_trade_lines`/`perf_*`)
  unit-tested in `webgui/tests/test_driver.py`.
- Tests: `services/driver_svc/tests` (compute/handlers/scheduler/app) +
  `webgui/tests/test_driver.py`. Design/plan: Phase 5 of the
  [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**Portfolio page (`/portfolio`) — DONE (2026-06-16, born 3-tier — Phase 3).** The
stub Portfolio page was built directly on the 3-tier model. New service
`services/portfolio_svc` (:8212, `SERVICE_PORTS["portfolio"]`), **scheduled +
command-driven** — uniquely it keeps a **live model in memory** that a background
SSE consumer updates tick-by-tick. Pieces:
- **Contract** `shared/contracts/portfolio.py:PortfolioModel` (one view,
  `cache:portfolio:positions`): display-ready `holdings_rows` / `sector_rows` /
  `performance_rows` (already formatted by the engine `view_model` in Tier 2),
  the per-symbol `suggestions` map, and `proxy_up`/`streaming` meta. Validates the
  envelope shape before caching, like the other domain contracts.
- **`portfolio_svc/compute`** reuses `portfolio-analyzer/src` **verbatim**:
  `build_portfolio` (sector breakdown + the four comparisons: vs-sector RS,
  benchmark over/under-weight, since-purchase excess, tailwind), `compute_baseline`
  (slow per-EQUITY history stats — ports the desktop `_compute_baselines` worker),
  `evaluate_portfolio` + `suggest` (the live scorecard + advisory rules), and the
  app's `view_model` formatters. **Formatting lives in Tier 2** (`format_payload`)
  so the GUI stays a thin renderer. `src` is imported **standalone** (PORTFOLIO_ANALYZER
  on `sys.path`) — safe because the service is its own process (note: `portfolio-analyzer`
  AND `trade-analyzer` BOTH expose a top-level `src` package, so they collide if
  imported in one process — another reason to run service suites **per folder**).
  All functions defensive (degrade, never raise).
- **`portfolio_svc/state`** holds the in-memory `PortfolioState` singleton (raw
  model + baselines + `dirty`/`rebuild_requested` flags + a lock) shared by the
  scheduler thread and the command handler.
- **`portfolio_svc/handlers`**: `rebuild` (proxy health → daily trade sync →
  `build_portfolio` → baselines into state → publish), `publish_current` (re-format
  the current state + cache+publish — called on each throttled tick), `handle_command`
  (`refresh` → sets `rebuild_requested` for the scheduler, which owns the stream
  restart). **`portfolio_svc/scheduler`**: builds the model, runs a background SSE
  worker (`compute.make_data().stream_quotes` → `apply_tick` to the shared model),
  and a 2 s publish loop that republishes when ticks are pending + does a full
  rebuild every ~10 min or on a pending refresh (restarting the stream on fresh
  holdings). Pure `rebuild_due`/`apply_tick_to_state` are unit-tested. **`portfolio_svc/app`**
  = `make_app("portfolio", scheduler=loop, command_handler=…)`.
- **Page** `webgui/pages/portfolio.py`: Refresh button + proxy/stream status bar;
  **Holdings / Sectors / Performance** tabs (`ui.table` rendering the cached display
  rows directly); the Performance tab adds a suggestion detail pane (row click →
  full advisory reasons). Version-polls `portfolio:positions` (live P&L via the
  service's per-tick republish); persists across nav. Pure builders
  (`proxy_status`/`stream_status`/`suggestion_text`/`status_line` + column defs)
  unit-tested in `webgui/tests/test_portfolio.py`. (Removed the now-orphaned
  `main.py:_stub` — Portfolio was the last stub page.)
- Tests: `services/portfolio_svc/tests` (compute/handlers/scheduler/app, 20) +
  `webgui/tests/test_portfolio.py` (8). Design/plan: Phase 3 of the
  [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**EOD Report redesign — DONE (2026-06-27).** The summary/detail were rebuilt around
**Daily / Weekly(WTD) / MTD performance, per book** (manual paper ledger + Driver
account, shown separately), plus **trade-type breakdowns** (by strategy / 0-DTE-Swing /
status) and **TOC + collapsible `<details>` navigation** (no JS — works in-app AND in
the exported standalone files). New pure builders in `webgui/pages/eod.py`:
`normalize_trades(raw, *, kind)` (one uniform `{symbol, strategy, trade_type, status,
entry_date, exit_date, realized_pnl, credit}` shape — the ledger keys `entry_time`/
`exit_time`/`entry_credit_total`/`trade_type`, the driver positions key `entry_ts`/
`exit_ts`/`entry_credit`+`quantity` and carry **no** `trade_type`); `period_buckets`
(realized/closed by **exit** date, opened/credit by **entry** date, week-to-date =
Monday→today, month-to-date = 1st→today); `breakdown_rows(trades, key)`;
`performance_table_html` / `breakdown_table_html` / `toc` / `details_section` /
`_book_now_line`. The summary keeps its activity tiles, adds a per-book performance
block; the detail adds the breakdowns + reuses the existing section builders inside
`<details>`. **One additive service change**: `compute.driver_account_view()` now also
returns `closed_positions` (the open-only view couldn't date-bucket the driver's closed
trades) — requires an `options_svc` restart + a republish (`driver_paper_manage`) to
appear. **Realized reads `$0`/`—` until trades close** — correct by design, not a bug
(both books currently have only open positions). webgui **539** + options_svc green;
verified live (summary perf tables, detail breakdowns PCS 19/CCS 9 + SWING 21/0-DTE 7,
`<details>` collapse, exported files). Design/plan:
[design](docs/plans/2026-06-27-eod-report-redesign-design.md) /
[plan](docs/plans/2026-06-27-eod-report-redesign-plan.md).

**EOD Report page (`/eod` + `/eod/detail`) — DONE (2026-06-18; redesigned 2026-06-27,
see above).** A **pure-webgui**
end-of-day report — no new service/port. It reads the caches the existing services
already publish (`options:scan` / `options:captured` / `options:paper_trades` /
`options:paper_account` + `driver:approvals` / `driver:performance`
+ `options:driver_paper_account` / `options:driver_paper_perf`) and rolls them
into a **Summary** and a **Detailed** report. Scope is
**Options activity + Driver** only (portfolio/sentiment intentionally excluded).
Built entirely in `webgui/pages/eod.py` — honors the 3-tier rule (webgui imports
only `nicegui` + `shared.bus` + `shared.contracts`). Pieces:
- **Single-source body.** Pure builders produce one HTML **fragment** + a scoped
  `EOD_CSS` string (mirrors the `gamma.py` Explain pattern — `ui.html` strips
  `<style>`, so CSS goes through `ui.add_css` in-app and is inlined into the file
  on export). `summary_fragment(snap, detail_href)` / `detail_fragment(snap)` +
  per-section builders (`captured_section` / `paper_section` / `scanner_section` /
  `driver_section`) are all **defensive** (missing/empty cache → a "No data" note,
  never raises) and unit-tested in `webgui/tests/test_eod.py` (16).
- **Generate + archive.** The **Generate** button calls `generate()` →
  `read_snapshot()` (snapshots the live caches) → `wrap_document(...)` wraps the
  same fragment+CSS into standalone `<html>` docs → `write_archive(...)` writes
  `summary.html` + `detail.html` into `webgui/data/eod/<CT-date>/` (gitignored, like
  the rest of `webgui/data/`; same date overwrites). The summary page lists past
  archived dates (`archive_dates`, newest first). The **in-file** summary→detail
  link is the relative `detail.html`; the **in-app** link is the route `/eod/detail`
  (the fragment takes the link target as a parameter — the only in-app/file diff).
- **File serving.** `main.py` adds `@app.get("/eod/file")` (mirrors
  `/options/explain`): returns an archived file as a raw `HTMLResponse` so its own
  `<style>` applies. The page's "Open summary/detail file" buttons + archive links
  open it in a new tab.
- **Wiring.** `("/eod", "EOD Report", "summarize")` in `FLAT_NAV`; `@ui.page("/eod")`
  → `eod.render()` and `@ui.page("/eod/detail")` → `eod.render_detail()` (both active
  `/eod` so the nav item highlights on detail too); `/eod` + `/eod/detail` added to
  `test_shell.py`. Design/plan:
  [design](docs/plans/2026-06-18-eod-report-design.md) /
  [plan](docs/plans/2026-06-18-eod-report-plan.md).

**Expected Move page (`/options/expected-move`) — DONE (2026-06-20).** A standalone
Tier-3 page that charts a symbol's recent price action plus a forward expected-move
cone for a given option strike/expiration. Reached via a **new-browser-tab handoff**
button on Scanner, Paper Trades, Captured Signals, and Calculator (or standalone from
the Options nav with manual symbol+expiry). Pieces:
- **Compute (Tier 2, `services/options_svc/compute.py`):** `compute_expected_move(symbol,
  expiry, legs, lookback="auto")` fetches a **DTE-aware** trailing-history window
  (`em_lookback_spec` → `_fetch_em_candles`: auto ≈ **3× DTE** trading days clamped to
  [20, 252], short DTE ≤2 → intraday 30-min; or a fixed `1mo`/`3mo`/`6mo`/`1y` override —
  replaces the old fixed `_EM_HISTORY_BARS=130`, partial bars skipped), the option chain,
  and spot (live quote else last close), then derives ATM IV for the expiry
  (`atm_iv_from_chain` — nearest-strike `volatility`, percent→decimal, exact-then-nearest
  fallback) and the cone (`em_cone`: one point/day, `width(t)=spot·atm_iv·√(t/365)`,
  anchored at spot). Fully defensive — returns a JSON-safe dict with `error` set on any
  failure (candles still drawn even when IV is unavailable). On-demand only.
- **Command/cache (`handlers.py`):** the `expected_move` command → `cache:options:expected_move`
  + `events:options:expected_move` (one latest-result view, like `calc_result`/`sim_result`).
- **Page (`webgui/pages/options/expected_move.py`):** engine-free reader — enqueues the
  command and version-polls the view. Pure builders `expected_move_figure` (Highcharts
  **candlestick** via `extras=["stock"]` + Upper/Lower EM dashed line series + datetime
  xAxis + x/y `crosshair.label` boxes) and `leg_lines` (yAxis plotLines: short solid /
  long dashed, put-red/call-blue) are unit-tested. One persistent `ui.highchart` built at
  render (ESM-import-map gotcha), updated in place; `@guard` on handlers. A **Look-back**
  dropdown (`em_lookback_options`: Auto≈3×DTE / 1mo / 3mo / 6mo / 1y) re-runs the last
  query with the chosen window; the active spec label shows in the status line.
  **No blank non-trading-day gaps:** the chart renders via `ui.highchart(...,
  type="stockChart")` so the x-axis is **ordinal** (`xAxis.ordinal:True`) — weekend/
  holiday gaps in the historical candles collapse automatically — and the forward
  cone (`em_cone(..., holidays=scheduler._HOLIDAYS, trading_days_only=True)`) omits
  weekend/holiday points so it lines up contiguously with the candles.
- **Handoff (`handoff.py`):** `signal_to_em_payload(signal)` normalizes a scanner/captured/
  paper signal dict → `{symbol, expiry, legs}` (per-type strikes via `_EM_LEG_FIELDS`);
  `send_to_expected_move` stashes (`_pending["expected_move"]`) + opens the page in a new
  tab. Scanner/Swing keep the shared 3-button `add_row_actions`; Paper/Captured use the
  Expected-Move-only `add_expected_move_action` (their rows map via `synth_from_trade`/
  `synth_from_captured`, which expose `strategy`→`type`); Calculator builds the payload
  from its `leg_inputs`. Design/plan:
  [design](docs/plans/2026-06-20-expected-move-page-design.md) /
  [plan](docs/plans/2026-06-20-expected-move-page.md).

**Simulator Replay tab (`/options/simulator`) — DONE (2026-06-20).** The third
legacy Tk simulator tab (`Replay`, alongside the already-migrated What-if /
IV-shock) was migrated to the 3-tier model. The in-process `ChainSnapshot` that
`sim_fetch` already stashes (and which carries `price_history`) is re-priced
along the underlying's recent path by the existing pure
`options_simulator.ReplayEngine`. Pieces:
- **Compute (Tier 2, `services/options_svc/compute.py`):** `sim_replay(symbol,
  expiry, kind, strike, direction, lookback="auto")` runs `ReplayEngine.full_trace`
  via `aggregate_position`, then ports the legacy window's **gap-compression /
  session** layout (overnight/weekend breaks collapsed onto a consecutive integer
  x-axis; `gaps`/`sessions`/`ticks`/`resolution`) into a **JSON-safe** dict
  (`x`/`prices`/`greeks{delta,gamma,theta,vega,rho}`/`timestamps`/`lookback`). The
  re-priced path is a **DTE-aware** window fetched here (`replay_lookback_spec` →
  `_fetch_replay_history` via the proxy: 0-DTE → 1-min/1d · ≤5 → 5-min/3d · ≤15 →
  5-min/5d · >15 → daily/~½×DTE; or a fixed override key), **NOT** the snapshot's
  fixed 2-day history — the expiry/DTE is only known at replay time. Defensive:
  `{}` on missing snapshot/contract, `{"error": …}` on IV≤0 / no price history.
  It is a **separate command/cache view** from `sim_run` (replay depends only on
  the contract selector + look-back, NOT the dt/mult sliders — so slider drags
  stay cheap).
- **Command/cache (`handlers.py`):** the `sim_replay` command →
  `cache:options:sim_replay` + `events:options:sim_replay`.
- **Page (`webgui/pages/options/simulator.py`):** a third **Replay** tab (now the
  default). Pure builder `replay_figure(trace, cursor)` draws ONE Highcharts
  element with **6 stacked yAxes** (Price + 5 Greeks) over the integer x-axis;
  session boundaries are dashed xAxis plotLines and the **scrub slider** is a
  client-side cursor plotLine (no command — same idiom as the ΔS overlay). The
  x-axis stays NUMERIC (dates in the tooltip / readout) to sidestep the datetime
  crosshair epoch-ms gotcha; the hover tooltip is capped at **2 decimals**
  (`tooltip.valueDecimals`). A **Look-back** dropdown (`lookback_options`: Auto-by-DTE
  / 1-min·1d / 5-min·3d / 5-min·5d / 15-min·10d / Daily·20d) overrides the DTE-driven
  window; the active spec label shows in the cursor readout. Built once at render
  (ESM import-map gotcha), updated in place; version-polls `options:sim_replay`;
  enqueues only on contract-selector / look-back changes. Pure builders unit-tested;
  verified live end-to-end (SPY → 62-bar 1-min trace → rendered 6-panel stack). Plans:
  [replay](docs/plans/2026-06-20-simulator-replay-tab-plan.md) /
  [DTE look-back](docs/plans/2026-06-20-dte-aware-lookback-plan.md).

**System Status page (`/status`) — DONE (2026-06-19).** A **pure-webgui** at-a-glance
health board (no new service/port). It honors the 3-tier import rule — `webgui/pages/status.py`
imports only `nicegui` + `bus_client`/`proxy` + `repo_paths`. Pieces:
- **Component sweep.** `component_targets()` enumerates the components from
  `repo_paths` (Memurai :6379, schwab-proxy :8100, **Schwab Authorization** (the
  proxy's OAuth token state), the five services :8210–8214 via `SERVICE_URLS`,
  webgui itself). `_probe_one` checks each by `kind`: **memurai** →
  `bus_client.ping()` (new helper: `bus()._r.ping()`, never raises); **proxy** →
  `proxy.health()`; **auth** → `auth_status(proxy.health())` reads `has_token`/
  `token_expired`/`refresh_token_expired` (access-token-expired is still "authorized"
  since the proxy auto-refreshes; only a missing token or **expired refresh token**
  is red); **service** → HTTP `GET /health` (the `make_app` scaffold's probe,
  `{"up": True}`); **self** → always up. `_sweep` fetches `proxy.health()` **once**
  and shares it with both the proxy + auth cards. The whole sweep runs off-thread
  via `nicegui.run.io_bound` (short 2.5 s per-probe timeout so a dead component fails
  fast). `overall_status` rolls the results into a green/red/grey banner naming any
  down components.
- **Schwab Authorization card (2026-06-19).** Surfaces OAuth token validity
  separately from "is the proxy process up". When the proxy is reachable the card
  shows an **Authorize** / **Re-authorize** button (`AUTH_URL = {PROXY_URL}/auth`)
  that opens the proxy's OAuth re-login page (`GET /auth`) in a new tab via
  `ui.navigate.to(..., new_tab=True)`; when the proxy is down the card is grey
  ("can't check"). The card is **not** restartable (`restart_spec` → None) — its
  action is the login link, not a process relaunch.
- **Data-freshness table.** Below the cards, per-domain rows read each representative
  cache view's version + ts (new `bus_client.read_meta(view)` → `(version, ts)`) and
  show `age_text` + a STALE flag for **scheduled** views older than 600 s (`is_stale`);
  **on-demand** views (trade/driver) are never flagged. This distinguishes "service
  answers /health" from "service is actively publishing".
- **Per-component Restart (2026-06-19; every card 2026-07-10).** **Every**
  component card carries a **Restart** button — shown regardless of up/down state,
  so you can also restart a wedged-but-listening service — covering the proxy, all
  **six** Tier-2 services (sentiment/options/portfolio/trade/driver/market), Memurai,
  and **the webgui itself**. Only the **auth** card is excepted (its action is
  **Authorize**, a link to `/auth`, not a process restart). `restart_spec(target)`
  maps a component to how it restarts — a **script** spec (proxy / service / **self**:
  free the port then launch the venv python on the entry script; services pass
  `wait_port=8100` so they wait for the proxy, the webgui uses `wait_port=0`) or a
  **service** spec (Memurai → `Restart-Service`, falling back to `Start-Service` if
  stopped — works up or down; may need an elevated session). **Windowless (2026-07-10):**
  `restart_command(spec)` builds a `cmd /c tools\restart_one.bat <kill_port>
  <wait_port> <name> <script>` argv and `_do_restart` spawns it with
  **`CREATE_NO_WINDOW`** — nothing flashes. `restart_one.bat` taskkills the port's
  LISTENING owner (`/F /PID`, no `/T` — so the webgui's own self-restart doesn't take
  the spawn down with it), waits for the dependency (`ping`-based sleep, no console
  needed), then launches the component **hidden** via `pythonw` +
  `Start-Process -WindowStyle Hidden` with stdout/stderr → `logs\<name>.out.log` /
  `.err.log` (mirrors `start_all_wt.bat nowindow`). The **webgui self-restart** frees
  :8500 and relaunches even though it kills the current page — the click handler toasts
  "this page will disconnect; reload" and skips the re-sweep. Every other restart
  toasts + schedules a 7s re-sweep. Verified: a live proxy restart (prior turn) bound
  :8100 in ~1s; the windowless `restart_one.bat` launch primitive is smoke-tested
  (hidden `pythonw`, output captured to `logs\`).
- **Wiring.** `("/status", "System Status", "monitor_heart")` in the **More** nav
  group; `@ui.page("/status")` → `status.render()`; `/status` added to
  `test_shell.py`. Auto-refresh `ui.timer(15s)` + manual Refresh button (with
  spinner + re-entrancy guard). Pure builders (`component_targets`/`status_word`/
  `status_color`/`status_icon`/`overall_status`/`age_text`/`is_stale`/`freshness_row`
  + `restart_spec`/`restart_command` + `auth_status`) unit-tested in
  `webgui/tests/test_status.py` (35); render + live restart + live auth-card
  verified by screenshot.

**Rescue tested trades (`/options/rescue`) — DONE (2026-06-21).** An advisory +
one-click-apply rescue feature for **tested credit spreads** (PCS/CCS/IC). Architecture
is **"Approach C (hybrid)"**: cheap **at-risk detection** rides the existing 5-min
manage cycle (tags paper-account rows + publishes a summary for a nav badge), while the
expensive **ranked candidate menu** is computed **on-demand** via a command, and **apply**
executes through new paper-engine primitives behind a stale-price guard. Pieces:
- **Commission source of truth** `config/commissions.toml` — Schwab standard rates
  (options **$0.65/contract per leg**, futures $2.25/side, index-exchange-fee passthrough);
  loaded by `services/options_svc/commission.py` (`commission_for`/`futures_commission`/
  `is_index_symbol`). **Rule: don't hard-code rates** — add them here.
- **Contracts** `shared/contracts/options.py`: new `RescueAdvisory` + `RescueCandidate`
  (+ `RescueLeg`/`RescueMark`) — validate the advisory envelope before caching.
- **PURE engine** `services/options_svc/rescue.py`: `assess_position_risk` (ok/watch/
  tested/critical + 0-100 **heat**, thresholds mirror the manage-cycle stops),
  `strategic_context` (dealer-gamma/regime/settlement notes+flags), **11 candidate
  builders** (close, partial_close, narrow, convert_ic, convert_butterfly, broken_wing
  [advisory], roll_down, roll_out, roll_down_out, inverted [advisory], futures_hedge
  [advisory]), `score_candidate` (max-loss-reduction-per-net-$ + delta + credit-vs-debit
  penalty + GEX/regime modifiers), and the `rescue_candidates` orchestrator (ranks +
  attaches context/warnings; **per-item construction** so one bad candidate can't sink
  the advisory).
- **Compute (Tier 2)** `services/options_svc/compute.py`: `compute_rescue(position_id)`
  (loads the position, reprices via `signal_repricer.reprice_swing`, fills underlying from
  the `gamma_snapshot` spot when the live quote is missing off-hours, pulls regime from the
  sentiment bridge, runs the engine, returns a contract-validated dict — fully defensive),
  `assess_open_positions()` (cheap stored-marks pass for the badge), `_make_leg_pricer(symbol)`
  (per-expiry chain-mid pricer).
- **Handlers** `services/options_svc/handlers.py`: the manage-cycle overlay merges
  `rescue_state`/`heat` onto the paper-account view; `publish_rescue_summary`; `rescue` +
  `rescue_apply` command handlers (`rescue_apply` refuses non-paper/captured ids and **never
  mutates on a stale re-price**). Cache keys: `cache:options:rescue:<position_id>` (one
  per-position advisory) + `cache:options:rescue_summary` (n_tested + n_critical for the badge).
- **Apply primitives** `options-scanner/paper_adjust.py` (NEW): `apply_close`/
  `apply_partial_close`/`apply_narrow`/`apply_convert_ic`/`apply_convert_butterfly`/
  `apply_roll`/`apply_inverted` mutate the paper DB inside the existing cash/buying-power
  mechanism (reconciling reserved BP to the new max-loss), write an audit row, and the
  `apply_adjustment` dispatcher re-prices the candidate legs and **aborts without mutation**
  if economics drifted > tolerance or the position isn't OPEN. `options-scanner/
  paper_account_db.py` grows a `position_adjustments` audit table + a `parent_position_id`
  column on `paper_positions` (linked rolls) + `insert_adjustment`/`list_adjustments`.
- **Page** `webgui/pages/options/rescue.py` (Tier-1, engine-free): `render()` + pure builders
  (`heat_color`/`at_risk_rows`/`candidate_card_rows`/`cash_text`/`summary_line`) — an at-risk
  table (paper+captured, heat-colored) → select a position → enqueues `rescue` → version-polls
  `cache:options:rescue:<id>` → ranked candidate cards (execute cards Apply→confirm→
  `rescue_apply`; advisory cards show "manual"). One persistent `ui.highchart` (ESM-import-map
  gotcha); `@guard` on handlers; degrades to a waiting-for-service placeholder.
- **Wiring** `webgui/main.py`: `("/options/rescue", "Rescue", "healing")` in the Options nav
  group + `@ui.page("/options/rescue")` route + a red count badge (key `/options/rescue`) fed
  from `cache:options:rescue_summary`, cleared on page open; `/options/rescue` in
  `test_shell.py`; a `webgui/page_help.py` guide entry.
- **At-risk row highlights:** the `rescue_state`/`heat` overlay lands on
  `cache:options:paper_account`, so the heat-colored at-risk row tint is wired on the **Paper
  Portfolio** page (`/options/portfolio`, `pages/options/portfolio.py` `rescue_highlight` +
  `body-cell-symbol` slot) — live there. The earlier-wired tints in `paper.py` (paper_trades
  ledger) + `captured.py` render different views that don't carry the overlay, so they stay
  **dormant no-ops** (kept defensively; captured is forward-compatible if signals are ever
  flagged). Primary at-risk surfaces (Rescue page table + nav badge) work regardless.
- **Captured CUT signals as advisory candidates (2026-06-22).** A captured signal whose
  `recommendation` is **CUT** (a money/delta/time loss stop — not `TARGET_HIT`) is now an
  **advisory** rescue candidate. `compute.reprice_captured` tags each signal row with
  `rescue_state`/`heat` via `assess_position_risk`, **escalating a CUT to at least `tested`**
  (heat floored ≥60) so it lands on the rescue board; `compute_rescue(position_id, source=
  "captured")` loads the signal via `signal_db.get_signal`, runs the engine, and **forces every
  candidate `apply_kind="advisory"`** (a captured signal has no executable paper position — the
  cards show the roll/convert/close mechanics + economics for *manual* placement, no Apply). The
  `rescue` command carries an optional `source` arg (paper|captured; paper id coerced to int,
  captured passes the string `signal_id`); `RescueAdvisory` gained `source` + a `position_id:
  int | str`. The page (`at_risk_rows` keyed by `signal_id`, row-select passes `source`) surfaces
  them. Apply safety is enforced at three layers (forced-advisory cards · `rescue_apply` refuses
  non-paper ids · the apply branch int-coerces). The nav **badge stays paper-only** (captured
  CUTs show on the board, not the badge). Design: [captured-signals](docs/plans/2026-06-22-rescue-captured-signals-design.md).
- Design/plan: [design](docs/plans/2026-06-21-rescue-tested-trades-design.md) /
  [plan](docs/plans/2026-06-21-rescue-tested-trades-plan.md).

**Signal push notifications — Telegram / Discord / Google Fi SMS — DONE (2026-07-05).**
The always-on options service now pushes a **phone notification** the moment it
publishes a **new scanner signal** or a **new captured signal** — server-side, so the
phone is pinged 24/7 regardless of whether a browser tab is open (this deliberately does
NOT reuse the browser-gated webgui alert watcher). Self-contained, service-owned module
**`services/options_svc/push_notify.py`** (headless; ports the proven Telegram/Discord
formatters from the legacy `options-scanner/notifier.py` rather than importing it — that
module drags in `winsound`/`winotify` and `notifier` is a documented cross-app name
collision). Three channels, each **self-gating on config presence** (missing creds →
silent no-op): **Telegram** (Bot API, HTML, one msg per new signal), **Discord** (webhook
embed, one per new signal), and **SMS via Google Fi** (`smtplib` emails a **batched**
summary to `<10-digit-Fi-number>@msg.fi.google.com` — Fi's proprietary email-to-text
gateway, still functional in 2026 unlike the deprecated `@vtext`/`@tmomail` carrier
gateways — sent from Gmail over `smtp.gmail.com:587` STARTTLS with an **app password**).
**Triggers** are hooked at the existing publish points in `handlers.py`: `rescan` (new
scanner signals) and `refresh_captured` + the `captured_reprice` branch (new captured
signals; `remove_closed_from_captured` is deliberately NOT wired — a manual close is not a
new signal). Each hook is **best-effort + try/except-wrapped AFTER the cache_set/publish**
so a notify failure can never block the scan/publish path. **"New" detection** is
single-source + restart-safe: a stable signal key (symbol/type/strikes/expiration, IC
folds the call legs) diffed against a **date-scoped Redis seen-set**
(`cache:options:notified_scan` / `cache:options:notified_captured`, a `{date, keys[]}`
envelope that resets on a new trading date) — keys are marked seen **when diffed (before
gating)**, mirroring the webgui watcher's unconditional `alerted |= keys`, so each signal
is considered once; a signal first seen off-hours/disabled/below-min-score is absorbed and
not deferred. On the service's **first publish after (re)start** the set is seeded
**silently** (no re-notify storm). Gates: a master `enabled`, an optional `market_hours_only`
(a local weekday + 08:00–15:00 CT + holiday check copied byte-for-byte from
`webgui/alerts.py` to avoid importing NiceGUI into the service — update the `_HOLIDAYS`
copy yearly alongside `alerts._HOLIDAYS`), and a scanner-only `min_score` (captured signals
carry no `composite_score`). **Config**: gitignored `shared/notifications.json`
(+ committed `shared/notifications.example.json`; `repo_paths.NOTIFICATIONS_CONFIG`), env
vars override file values (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`DISCORD_WEBHOOK_URL`/
`FI_SMS_NUMBER`/`SMS_SMTP_USER`/`SMS_SMTP_APP_PASSWORD`/`NOTIFY_ENABLED`). **Setup**:
Telegram bot via `@BotFather` (token) + `.../getUpdates` (chat_id); Discord channel →
Integrations → Webhooks; SMS = your 10-digit Fi number + a Gmail **App Password** (Google
Account → Security → 2-Step Verification → App passwords). **Out of scope (YAGNI)**:
per-channel Settings-page toggles, and trade-executed/error notifications. push_notify
**27** + options_svc handlers **45** green. Built subagent-by-subagent (TDD, two-stage
spec+quality review). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-05-signal-push-notifications-design.md) /
[plan](docs/plans/2026-07-05-signal-push-notifications-plan.md).

**Twitter/X public-post channel + grade (2026-07-20).** A **fourth** channel on the SAME
`notify_signals` fan-out posts new SCANNER (0-DTE + swing) signals to a **public** X account.
Unlike the other three (which push to YOU), this PUBLISHES, so it is deliberately different:
`notify_twitter` is **scanner-only** with its OWN gates (a public `min_score` + a persisted
per-day `daily_cap`), `twitter_signal_text` is a **≤280-char** formatter (compact body + a
config-driven footer: hashtags/Discord-link/extra-text/disclaimer, footer preserved on
truncation), and `send_twitter` is a **tweepy OAuth 1.0a** sender (best-effort — 187/429/network
errors caught). It is wired into `notify_signals` guarded so a Twitter failure can't break the
private sends, and **ships OFF** (`twitter.enabled:false` + `dry_run:true`) — inert until OAuth
keys are added + both flags flipped (account creation + the go-live flip are the USER's; nothing
publishes by default). The signal **grade** was added to the tweet + `telegram_signal_text` +
`discord_signal_embed` at the same time. Config: the `twitter` block in `shared/notifications.json`
(+ `TWITTER_*` env). New dep `tweepy>=4.14`. **Restart `options_svc`.** push_notify **73** green.

## Paths and ports: `repo_paths.py` + `config/ports.toml`

`repo_paths.py` at the repo root is the single source of truth for cross-app
paths and ports. Each entrypoint prepends the repo root to `sys.path` and imports
the constants it needs:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL, APPSETTINGS, TOKENS, NICEGUI_PORT  # etc.
```

`config/ports.toml`:

```toml
proxy = 8100
options_analytics = 8200
approval = 8300
dashboard_frontend = 5173
nicegui = 8500            # the NiceGUI app
memurai = 6379            # Redis backbone (Tier 3)

[ml_servers]              # external processes — not started by this repo
MES = 8000
MNQ = 8001
ES  = 8004
NQ  = 8005

[services]                # Tier-2 domain services (repo_paths → SERVICE_PORTS/SERVICE_URLS)
sentiment = 8210
options   = 8211
portfolio = 8212
trade     = 8213
driver    = 8214
market    = 8215
```

**Rule: never hard-code `D:\` paths or port numbers in the apps.** Add them to
`repo_paths.py` / `config/ports.toml` and import them.

`config/commissions.toml` is the single source of truth for **commission rates**
(Schwab standard: options $0.65/contract per leg, futures $2.25/side, index-exchange-fee
passthrough), loaded by `services/options_svc/commission.py` (used by the Rescue
candidate menu). **Rule: don't hard-code commission rates** — add them here.

`config/theme.toml` is the single source of truth for the **webgui styling palette**
(surfaces/cards/text, buttons incl. the 3D gradients, semantic state colors, the
speedometer gauge face, the Sentiment/Rotation chart palette), loaded once at webgui
startup by `webgui/pages/options/theme.py:load_theme()` — edit + restart the webgui to
restyle without code changes; missing keys fall back to the built-in dark-navy defaults.
See the "App theme — dark-navy 'dashboard'" section.

`config/flow_alerts.toml` is the single source of truth for the **options-flow alert
thresholds** (crossover `band`/`min_premium`/`cooldown_min`; UOA `k`/`vol_floor`/
`premium_floor`/`top_n`; **gamma_flip `enabled`/`band_pct`/`cooldown_min`/`symbols`** — the
dealer gamma-regime flip alert; the `enabled` server kill-switch), loaded by
`services/options_svc/flow_alerts.py:load_thresholds()` (defaults if the file is missing) —
edit + restart `options_svc` to tune. See the 2026-07-18 + 2026-07-22 "Last updated" entries.

`config/sessions.toml` is the single source of truth for **market session windows +
the extended-hours activation date** (2026-08-02). All times are **CT** (ET and CT
shift together for DST, so the values are stable year-round). It holds
`[activation] extended_hours_from` (**2026-08-17** — every ETH branch is inert
before it, so a Cboe slip is a one-line edit), the three sessions
(`[sessions.gth|regular|curb]`), five named operating windows
(`[windows.scan|collection|session_flip|market_snapshot|driver_entry]`, each
optionally carrying its own `tz` and `end_exclusive`), and
`[alerts] fire_in_extended_hours`. Loaded by
**`shared/market_calendar.py:load_config()`** (mtime-cached, mirroring
`flow_alerts.load_thresholds`; a malformed file degrades to built-in defaults for
bad **values and bad shapes** and never raises). **Edit + restart the affected
service.** Two knobs are load-bearing and easy to get wrong:
`[windows.session_flip].at` is held SEPARATE from `collection.start` so widening
GTH collection can't silently move the Gamma display flip, and
`[windows.driver_entry].end_exclusive` matches the driver's legacy `hm >= RTH_END`
gate — flipping it to inclusive re-opens a checkpoint slot at 15:30 ET inside the
no-new-entries window. See the 2026-08-02 "Last updated" entry.

**`shared/market_calendar.py` is the single source of truth for the NYSE calendar**
(holidays **derived algorithmically** — no yearly edit) **and session/window
predicates**. Ten duplicated holiday sets and fourteen hardcoded window constants
were consolidated onto it. **Do not add a new holiday literal or window constant
anywhere** — add it here, or to `config/sessions.toml`. The one site left outside
it is `claude-driver/config.py` (legacy; its morning-agent consumers were deleted
2026-07-08, only `RISK_LIMITS` is still read), deliberately exempt.
`shared/` is a namespace package, so
`from shared.market_calendar import ...` resolves once the repo root is on
`sys.path`; legacy app-dir callers (`options-scanner/scanner.py`,
`scanner_engine.py`, `gex_status.py`) carry the three-line bootstrap.

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

## Running

The simplest path is `start_all.bat` (Memurai check → proxy → sentiment_svc →
options_svc → portfolio_svc → trade_svc → driver_svc → web gui, opening the
browser). It opens the proxy + 5 services + web gui in **7 separate console
windows**.

**One-window alternative — `start_all_wt.bat`** (requires Windows Terminal):
launches the same 8 processes as **8 tabs in a single Windows Terminal window**
(live logs preserved, but far less desktop clutter). The processes stay 8
separate OS processes — required, since merging services into one Python process
would re-introduce the `config`/`scoring`/`notifier`/`src` top-level
module-name collisions the 3-tier split exists to prevent. Each tab waits for the
proxy (:8100) before starting via `tools\wait_and_run.bat <wait_port|0> <script>`
(the proxy tab passes `0` to start immediately), preserving the same ordering as
the multi-window launcher; tabs run under `cmd /k` so they stay open with live
output. Close the window (or a tab) to stop the services.

**Double-click launcher — `start_all_hidden.bat`**: the click-to-run entry point.
Double-click it in Windows Explorer (or a desktop **shortcut** to it — right-click
→ Send to → Desktop) to launch the whole stack **windowless**. Because a `.bat`
double-clicked always opens its own console, it **relaunches itself hidden** (via
`powershell Start-Process -WindowStyle Hidden`, so you see at most a brief flash)
and then runs `start_all_wt.bat nowindow`. Net effect: click → nothing visible →
the browser opens to the web GUI, with all 8 processes hidden. Stop with
`stop_all.bat` or More → Terminate.

**No-window mode — `start_all_wt.bat nowindow`** (aliases `-nowindow` /
`/nowindow` / `hidden`): the same launcher, but every process runs **hidden with
NO window at all** — each is spawned via PowerShell `Start-Process -WindowStyle
Hidden` using the venv **`pythonw.exe`** (falls back to `python.exe`), with
stdout/stderr redirected to `logs\<name>.out.log` / `.err.log` at the repo root.
Same proxy-first ordering (it waits for :8100 before starting the six services +
web GUI) and it still opens the browser. Since there are no consoles to close,
**stop a windowless stack with `stop_all.bat`** (or the GUI's More → Terminate).
The default (no-arg) mode is unchanged (WT tabs with live logs). NOTE: the
proxy + six services already write their own rotating log files regardless; the
redirect additionally captures the **web GUI** output, which otherwise only goes
to its console. (The System Status page's per-component **Restart** buttons are
**also windowless** — they spawn `tools\restart_one.bat` with `CREATE_NO_WINDOW`,
which relaunches the component hidden via `pythonw` + `Start-Process -WindowStyle
Hidden`, logs to `logs\<name>.out.log`.)

**Stopping — `stop_all.bat`** (also reachable from the GUI's **More → Terminate**
page): runs `tools\stop_all.py`, which reads the ports from `repo_paths` and kills
whatever is LISTENING on the proxy + 5 service ports + the web GUI (web GUI last).
**Memurai (:6379) is intentionally left running** — it's a shared Windows service,
not something this repo starts. The Terminate button spawns the batch fully
detached (`cmd /c start`) so it isn't in the web app's process tree (it taskkills
the web app itself, so a child would otherwise kill itself mid-run).

Manual order:

```powershell
# 1. Activate the venv
.\.venv\Scripts\Activate.ps1

# 2. Memurai (Redis backbone) must be running on :6379 — it installs as a native
#    Windows service; start it from services.msc if needed. (3-tier cache/pub-sub/commands.)

# 3. Start the proxy (waits to bind :8100) — everything reads market data through it
python schwab-proxy\schwab_proxy.py

# 4. Start the migrated domain services (each owns its refresh/scheduling; publishes to Redis).
#    Sentiment + Options + Portfolio + Trade + Driver are all migrated.
python services\sentiment_svc\app.py      # :8210  (composite + rotation
                                          #          + the NIGHTLY momentum cascade at 16:20 CT
                                          #            → cache:sentiment:momentum)
python services\options_svc\app.py        # :8211  (scan/swing/header/gamma/paper/captured/calculator
                                          #          + 1-min intraday GEX history collection, 08:00–15:20 CT)
python services\portfolio_svc\app.py      # :8212  (sector breakdown + vs-sector perf + live-streaming P&L)
python services\trade_svc\app.py          # :8213  (on-demand symbol analysis: MTF + Position/Investor verdicts)
python services\market_svc\app.py         # :8215  (live macro-ticker Market Dashboard: ~3s RTH / 15s off-hours poll of /quotes → cache:market:dashboard)
python services\driver_svc\app.py         # :8214  (morning-agent order-approval queue: 09:28-ET run + approve/skip;
                                          #          orders simulated — config.PAPER_TRADE=True)

# 5. In another terminal, start the NiceGUI app (reads cache:* from Redis; no engine imports)
python webgui\main.py      # serves http://127.0.0.1:8500
```

> **3-tier note:** Once a domain is migrated, the web GUI no longer computes anything
> for it — its **service must be running** (and Memurai up) or the page shows a
> "Waiting for … service" placeholder. **Sentiment, the entire Options section,
> Portfolio, Trade, and Driver are migrated** (`services/sentiment_svc`,
> `services/options_svc`, `services/portfolio_svc`, `services/trade_svc`,
> `services/driver_svc`) — **every page now reads Redis**; the webgui imports
> ONLY `nicegui` + `shared.bus` + `shared.contracts` — no app engines, so the documented
> `scoring`/`notifier` cross-app collision can no longer occur. See the "Planned 3-tier
> architecture" section.

## Environments (dev / prod)

Two checkouts of this repo run **simultaneously on one machine**: an always-on
**prod** stack pinned to `main`, and a **dev** checkout where code is edited.
Operator runbook: [`docs/dev-prod-environments.md`](docs/dev-prod-environments.md).
Rationale: [design](docs/plans/2026-08-08-dev-prod-environments-design.md).

| | prod | dev |
|---|---|---|
| Folder | `D:\WebGUI Trading Prod` (clone, pinned to `main`) | `D:\WebGUI Trading with Schwab` |
| schwab-proxy | **owns** it, `:8100` | **borrows** prod's — starts none |
| sentiment / options / portfolio / trade / driver / market | 8210–8215 | 9210–9215 |
| webgui | `:8500` | `:9500` |
| Redis (Memurai `:6379`) | **db 0** | **db 1** |
| SQLite, `logs\`, `webgui\data` | its own | its own |
| Schedulers · Claude · notifications · autonomous driver | live | **off** |
| Launcher | `start_all_wt.bat` | `start_dev.bat` (7 processes, no proxy) |

Prod's ports are byte-identical to the pre-environment numbers, so prod is a
relocation, not a reconfiguration. Dev borrows prod's proxy because the Schwab
OAuth **refresh token is a single rotating credential** — two proxies holding it
can invalidate each other's session. **Accepted consequence: dev's on-demand
fetches need prod's proxy up.**

**Two config files decide identity.** `config/environments.toml` is **tracked** —
both profiles (`port_offset`, `proxy_port`, `redis_db`, `owns_proxy`, the four
behaviour flags). `config/env.local.toml` is **gitignored** — `name = "dev" |
"prod"` plus an optional machine-local `peer_root`. **A missing marker resolves to
`prod`**, so any checkout without one behaves exactly as this repo did before
environments existed, and because it is gitignored **`git pull` can never carry an
identity between checkouts**. Template: `config/env.local.example.toml`. ⚠ a
Windows `peer_root` must be a TOML **literal** string (`'D:\WebGUI Trading Prod'`)
— in a basic string `\W` is an invalid escape that discards the WHOLE document,
`name` included, and the checkout silently resolves to prod.

**Resolution lives in `repo_paths.py`** (not a new module — it already parses
`ports.toml` and is imported by ~40 files, so a module in front of it would be an
import-order hazard). It exports `ENV_NAME` / `ENV_FLAGS` / `IS_DEV` /
`OWNS_PROXY` / `REDIS_DB` / `PEER_ROOT`, and `SERVICE_PORTS` / `NICEGUI_PORT` /
`PROXY_PORT` / `MEMURAI_URL` become profile-derived — so every existing consumer
(services, launchers, `tools/stop_all.py`, `tools/restart_one.bat`, the Status
page) follows the environment with no edit of its own. `[services]` in
`ports.toml` is offset automatically; a **top-level** port is not, which is
correct for a process this repo does not start and a bug for one it does.

**The four suppressions, and where each is enforced** — each reuses a degrade path
the code already has, so a suppressed dev cannot take a code path prod never takes:

| Flag | Enforced in | Effect |
|---|---|---|
| `allow_notifications` | `shared/notify/channels.py:load_config` | recursively zeroes **every** `enabled` key, LAST so it also overrides the `NOTIFY_ENABLED`/`TWITTER_ENABLED` env escapes — kills Telegram, Discord, Fi-SMS, the public **X/Twitter** poster and the sentiment state-transition alert in one stroke. `options_svc/push_notify.load_config` delegates here, so this is the single chokepoint |
| `allow_claude` | the three client factories — `options_svc/compute.py`, `market_svc/compute.py`, `driver_svc/decider.py` — return `None` | falls into the existing *no-API-key* path: the briefing renders its explanatory page, the ticker narrative is empty, the decider stands down |
| `schedulers` | `services/_scaffold.py:_schedulers_enabled` (consumed by `make_app`) | all six services stop collecting and polling; **command handlers still run**, so the UI stays fully usable off the snapshot |
| `autonomous_trading` | `driver_svc/handlers.py:run_autonomous_cycle` early-returns | belt-and-braces: `cycle` is also a *command* and the arm state lives in Redis, so the scheduler skip alone would not stop a snapshot that carried `cache:driver:control` enabled |

**Escape hatch:** `set TRADING_ENABLE_SCHEDULERS=1` before launching turns
schedulers on for that session — the one dev case that genuinely needs collection
(testing the collectors themselves). It makes dev issue **real Schwab calls** on
top of prod's ~68–76k/day; the other three suppressions stay on.

**Under pytest the process PRESENTS AS PROD** regardless of the marker — ports,
Redis DB, `owns_proxy` **and `ENV_NAME` itself** — with all four suppressions
forced ON. Tests are hermetic (the bus is already fakeredis), so this keeps the
existing suites passing unchanged inside a dev checkout while guaranteeing no test
can reach Anthropic or a notification channel. **Consequence: dev's own `IS_DEV`
branches are only ever exercised by monkeypatch** — patch a flag with
`monkeypatch.setitem(repo_paths.ENV_FLAGS, …)`, but patch a by-value export like
`IS_DEV`/`OWNS_PROXY` with `monkeypatch.setattr` **on the module that consumed
it**. Verifying that dev really withholds the proxy/Memurai restart buttons is a
**manual check with the app running**.

**Cross-environment safety rails:** `tools/stop_all.py` drops the proxy from its
kill list when `owns_proxy` is false and matches the HUD by **this checkout's root
path** (so dev's Terminate stops only dev's seven processes); the Status page
renders the proxy card read-only as *"shared — owned by prod"* and **hides the
Memurai restart in dev** (one Redis server serves both); dev's webgui carries a
`DEV` chip in the header lockup and a `DEV ·` tab-title prefix.

**THE DEVELOPMENT RULE (mandatory).** Work you are given lands in **dev**, is
**verified running in dev**, and only then moves to prod — via `tools\promote.bat`
and nothing else. Never `git pull`, `merge`, `checkout` or `reset` in the prod
checkout: that skips the dirty-tree refusal, the stop, the conditional dependency
reinstall and the restart, all of which exist because prod is a live trading
stack pinned to `main`.

The order is: commit in dev (or a worktree) → fast-forward `Using_Highcharts` and
`main` → **run it in dev and confirm the change actually works** → then promote.
"Tests pass" is not "verified in dev" for anything with a runtime surface; the
DEV chip, the Status-page restart gating and the launcher guards were all
green in tests and wrong in practice.

**Enforced mechanically**, because knowing the rule was not enough: the whole
environment split was built in a session that then bypassed `promote.bat` on
every commit, since `git pull` in prod is one keystroke shorter.
`.claude/hooks/guard_prod_promote.py` (PreToolUse on `Bash|PowerShell`, wired in
`.claude/settings.json`) blocks a mutating git verb whose target is the prod
checkout — by explicit leading `cd`, or by the Bash tool's persistent cwd, so a
bare `git pull` is caught too. Read-only git in prod stays open (inspecting prod
is how you decide to promote), as does everything in dev and worktrees. The `cd`
match is **anchored at the start of the command**: an unanchored version also
fired on commands that merely *wrote* the prod path into a file, which it did
within a minute of going live.

**Launcher guards (added 2026-08-09, after both bit in the first hour of use).**
Two failures, one root: a launcher that starts the *wrong* stack, or a *second*
copy of the right one, looks like success until you read the ports.

- **A PROD launcher refuses in a dev checkout.** `start_all.bat`,
  `start_all_wt.bat` and `start_all_hidden.bat` probe `repo_paths.IS_DEV` and
  exit. This is the mirror of `start_dev.bat`'s existing guard and it is the one
  that actually cost something: those launchers start a **proxy**, and dev's
  `PROXY_PORT` *is* prod's `:8100`, so run from dev they bind prod's port while
  prod never starts — the stack looks healthy while a dev-checkout process serves
  its market data. In `start_all_hidden.bat` the guard sits ahead of the
  `__hidden` dispatch, so it fires on the **visible** first pass, before the
  self-relaunch and before the HUD.
- **No launcher starts a stack that is already running.** All four call
  `tools/check_stack_down.py`, which imports **`stop_all._targets()`** and carries
  no port literal of its own — so the starter and the stopper cannot disagree
  about what this environment owns. It names the busy ports and the checkout,
  because with two stacks up "already running" is ambiguous. `--only LABEL`
  narrows it for single-process launchers; a probe that cannot run degrades to
  **allow** (its cost is a duplicate process, not a down stack).
- **`start_webgui.bat` derives its port from `repo_paths`** instead of the `:8500`
  it used to hardcode in its title, banner, proxy hint and browser helper — from
  dev it started on `:9500` while announcing prod's port and opening a browser
  there. `_open_webgui.bat` takes the port as an argument. ⚠ two batch
  metacharacter traps live here and both were hit: `for /f "usebackq"` strips the
  quotes around an interpreter path containing spaces, and **`%` in a `-c`
  argument is eaten by cmd** (`'set X=%s' % v` → `'set X= v`). Emit `set` lines to
  a temp batch and use concatenation, never `%`-formatting; a test rejects
  `%s`/`%d` in any `-c` line of that file.
- **`start_dev.bat`'s waits are bounded.** Its WT branch now calls
  `:wait_prod_proxy` before opening seven tabs that would all sit blocked, and
  `:wait_web` returns a failure instead of spinning forever — starting dev before
  prod used to hang on a message naming the web GUI, one layer below the real
  blocker.

**The cutover has been performed (2026-08-09).** Both environments run
simultaneously and were verified live: prod on 8100/8210-8215/8500 from
`D:\WebGUI Trading Prod`, dev on 9210-9215/9500 with all four suppressions
active, one shared Memurai, and dev holding **no proxy of its own**.

**Data flows one way.** `tools/snapshot_from_prod.py`, run **from dev**, copies
prod's SQLite stores (online-backup API — **prod keeps running**) and `DUMP`s db 0
into db 1. It hard-refuses unless `ENV_NAME == "dev"`, refuses when the two Redis
DBs resolve equal, and refuses while dev is up. It **excludes `cmd:*`** (a stream
is a queue dev would drain and EXECUTE) and **rewrites `cache:driver:control`
disabled**. **Promotion is explicit:** merge to `main` and push from dev, then run
`tools\promote.bat` in prod (dev-checkout guard, dirty-tree guard *before*
stopping anything, `git pull --ff-only`, reinstall only if `requirements.lock`
moved, restart).

**Known limits (not defects) are listed in the runbook** — chiefly that dev is
*quiet at rest, not incapable* (command handlers are ungated, so clicking Run scan
in dev still reaches Schwab through prod's proxy), that the legacy
`options-scanner/notifier.py` + `sentiment-dashboard/notifier.py` sit outside the
notification gate (dead from every service path — only their own tests import
them — but runnable by hand), and that `options_svc`'s `driver_paper_create`
handler is not env-guarded (its producer is, and the snapshot excludes `cmd:*`).

## Performance characteristics & known hotspots

Single-user, localhost Memurai — so most of these are *tolerable today* but are the
real levers if a page feels sluggish or a service churns CPU/network. Audited
2026-06-19; ranked by impact. Fix the High items first if optimizing.

**2026-07-18 re-audit — all Critical + High findings FIXED (TDD, per-layer tests):**
- **GEX collection was silently dropping ~37% of its 1-min slots** (measured in the
  live DB: 151 exactly-2-min gaps on 2026-07-17) — the ~24 per-symbol chain fetches
  ran SERIALLY (15–35 s of the 60 s budget) and the scheduler `await`-gathered ALL
  branches before sleeping, so a 30–90 s rescan also swallowed following slots. Now:
  `gex_collector.poll_once` fetches chains in a small pool (`POLL_FETCH_WORKERS=6`;
  engine compute + SQLite inserts stay on the calling thread — conn affinity +
  `engine._last_dte` mutation), and `scheduler.launch_branches` replaces
  `_gather_due`: due branches launch as KEYED background tasks with a
  still-running skip (a branch can only ever delay ITSELF), so the tick returns to
  its 30 s sleep immediately. This also fixes the flow-alert spike detector
  silently mixing 1-min and 2-min volume increments (a 2-min delta reads ~2×
  baseline).
- **`gamma_snapshot` re-decoded the WHOLE session's heatmap grids every minute**
  (4 views × ~440 rows × full-chain JSON by the close — tens of MB/min, the
  service's largest CPU burn). Now incremental: `compute._history_rows_incremental`
  memoizes decoded rows per `(symbol, view, session-date)` (lock-guarded,
  date-evicted) and appends only rows with `ts > last-seen` via
  `gex_history_db.load_date_with_grid(since_ts=…)` (sargable on the PK). Safe to
  share because `_crop_gamma_views` REBUILDS row tuples (never mutates memo rows).
- **The same 1-min tick fetched the viewed symbol's chain TWICE** (poll_once, then
  `refresh_gamma_current` seconds later). Now `poll_once(on_chain=…)` hands each
  fetched chain to the caller; `collect_gex_history` captures the currently-viewed
  symbol's chain (`_current_gamma_symbol`) into a CONSUME-ONCE stash
  (`_stash_tick_chain`/`_take_tick_chain`, 45 s TTL) that `gamma_snapshot` pops —
  only the same-tick refresh reuses it; every other caller still fetches fresh.
- **The webgui watcher regressed twice as the app grew:** `_freshness_facts` was
  full-deserializing 4 payload envelopes (incl. `options:scan` a SECOND time) every
  2 s tick per tab — `cache_set` now writes a tiny `{key}:ts` side key (same
  pipeline as the SET; skipped when `skip_unchanged` skips) and
  `bus_client.read_metas` probes `:ver`+`:ts` for all views in ONE pipelined
  round-trip (pre-upgrade keys fall back to the envelope once). And `_tick` called
  `_refresh_health` unconditionally (a proxy HTTP GET every 2 s per tab, bypassing
  the TTL memo) — it now re-warms via `cached_health`.
- **Sentiment was the biggest background Schwab-API burner:** the 120 s composite
  refresh fetched 11 NTM sector chains every cycle (~3,300 calls/day) for a
  slow-moving cumulative P/C — now TTL-cached 15 min in `live_composite`
  (`PCR_TTL_SEC`; an empty off-hours result is NOT cached so the first post-open
  refresh picks up volume). And `compute_30d_trend` refetched SPY 12-mo + 11
  sector histories on every 15-min trend recompute (~1,150 calls/day) for a
  ~daily-changing structural gauge — the self-fetching path is now cached hourly
  (`TREND_30D_TTL_SEC`; explicit-args calls bypass the cache).

**2026-07-19 — the Medium + Low tier from that audit was REMEDIATED (TDD per item):**
- **Flow alerts** now load only the trailing ~22 rows per symbol
  (`gex_history_db.load_flow_tail` + `handlers` `tail_limit = spike window + 2`) instead
  of the whole day's series every minute, normalize the series **once** per symbol
  (`flow_alerts._crossover_rows`/`_spike_rows` share one `_norm`), **exclude `$VIX`** (its
  option premium crossovers are noise), `skip_unchanged` the cooldown-map write, and
  mtime-cache the thresholds TOML (`load_thresholds` — was re-parsed every tick).
- **Storage:** the redundant **`idx_snap_today`** index (an exact duplicate of the PK
  autoindex) is dropped in `init_schema`, and **`gex_json` grids are zlib-compressed at
  insert** (`_encode_grid`/`_decode_grid`, ~5× smaller — the ~470 MB/day dominant cost;
  the reader is format-agnostic so legacy uncompressed rows still decode). `init_schema`
  runs **once per process** (a `_GEX_SCHEMA_READY` latch), not every 1-min collect.
- **Term structure** polls every **5 min** now, not every 1-min slot
  (`gex_collector.TERM_POLL_INTERVAL_MIN`; it's the widest SPX chain in the system).
- **Rescue advisories** read a **light GEX-only context** (`compute._light_gex_context` —
  single chain fetch + `calc_all_from_chain` + walls) instead of a full `gamma_snapshot`
  (which also builds the projection band / term grid / flow series / history decode, all
  discarded).
- **`reprice_captured`** now clears the repricer chain cache first (was pricing captured
  marks + the 3×/day action-alert reprice off up-to-5-min-stale chains — a freshness fix).
- **Proxy:** the stats counter uses **WAL + `synchronous=NORMAL`** (drops the per-call
  fsync on the ~60-70 calls/min hot path), `_rate_limit` holds a dedicated **`_rate_lock`**
  across its spacing (concurrent fan-outs no longer burst past 5 req/s → 429 risk), and the
  30 s reconcile logs INFO only on an actual change (else DEBUG).
- **market_svc:** the Claude summary runs as a **background task** (`asyncio.create_task`,
  no longer stalls the 2 s poll up to ~60 s), the deep-weekend poll throttles to 60 s
  (`WEEKEND_INTERVAL_SEC` — futures closed), and `read_sector_pcr` is **version-gated**
  (deserializes the composite only when it changes, not every 2 s).
- **sentiment_svc:** the state-transition phone push fires **outside `_TREND_LOCK`**
  (was holding it ~25 s on a flip day) and `sector_pc_delta` **closes its connection**
  (was leaking ~26 handles/day). **driver_svc** reads the composite **once per cycle**
  (shared by the market-state + magnitude readers).
- **webgui:** ticker / market / driver poll payloads now read **off the event loop**
  (`run.io_bound`), the driver poll **pipelines** its 5 version probes into one
  `read_versions`, the scanner builds its ~5,238 display rows **off the loop**
  (`_read_and_build` → `_apply_populate`), and page-build reads `options:scan` **once**
  per navigation (shared by `_recompute_badges` + `_acknowledge`, was 2-3×).

Consciously **not** done: the gamma-page `_render_view` figure build stays on the loop —
the server-side ±20-strike crop (C2/P2) already reduced it to ~11k entries (~10-30 ms once
per 120 s), and splitting the entangled async render isn't worth the regression risk; the
gamma page's now-redundant 120 s RTH refresh (with the server refreshing every minute) is
also left as a minor duplicate.

**Costing model (important, non-obvious):** the version is stored in a SEPARATE
tiny Redis key (`{key}:ver`, `INCR`'d by `cache_set`), so version-polls are now
**cheap probes** — `bus_client.read_version()` → `Bus.cache_version()` reads just
that int (no payload deserialize); `read_versions()`/`Bus.cache_versions()` batch
many in one pipelined round-trip (use it when a page polls several views — e.g.
Gamma). `read()`/`read_full()` still deserialize the full envelope (use only when
you actually need the payload). `Bus.cache_set(key, payload, event=…, skip_unchanged=…)`
(a) **skips the whole write + publish when the payload is byte-identical**
(`skip_unchanged=True` → no `INCR`/`SET`/publish, version unchanged → GUI poller
doesn't repaint), and (b) pipelines `SET`+`PUBLISH` into one round-trip when `event`
is given. The `SET` can't fold into the `INCR` (the envelope still embeds the version
for `cache_get`). options_svc header + gex_status use `skip_unchanged`; other periodic
republishers (sentiment 120 s, portfolio per-tick, driver perf) still bump
unconditionally — opt them in the same way if they prove chatty.

**HIGH — webgui event-loop pressure (runs on *every* page):** *(FIXED 2026-06-19)*
- The app-wide 2 s watcher now runs its blocking bus reads **off the event loop**
  (`main._tick` is async → `run.io_bound(_watcher_compute)`); `_watcher_compute`
  reads `options:scan` **once** and passes it to `_recompute_badges(scan)` (no double
  read), reads the driver badge via `bus_client.read_full` (payload+version in one
  read), and uses the **in-memory-cached** `app_settings.load()` (no per-tick disk
  read; invalidated on `set()`). Badge/chime UI work happens back on the UI thread
  after the await.
- `proxy.health()` is memoized for `_HEALTH_TTL_SEC` (`main.cached_health`) and
  re-warmed off-thread by the watcher tick, so a navigation no longer makes a blocking
  3 s-timeout HTTP call before first paint.
- `pages/options/gamma.py` coalesced its **four** 2 s version-polls into **one**
  `_poll` that reads all four versions in a single pipelined `read_versions(...)` call
  (cheap `:ver` counters) and dispatches only the changed views. (`status.py` remains
  the model citizen — blocking sweep via `nicegui.run.io_bound`.)

**HIGH — service-side serial proxy fan-out (biggest wall-clock wins):** *(FIXED
2026-06-19)* These I/O-bound proxy loops now fan out concurrently via
`services/_parallel.py:parallel_map` (services) / an inline `ThreadPoolExecutor`
(engine files). The proxy rate-limiter only *spaces* upstream calls ~0.2 s apart
(it does **not** hold a lock across the Schwab round-trip — see `schwab_proxy.py:
_rate_limit`), so concurrent calls genuinely overlap; pools are kept ≤8.
- Sentiment sector load — the 11 `get_daily_history` + 11 `/chains` loops in
  `sentiment_svc/compute.load_sector_perf` + `load_industries` (extracted to shared
  `_fetch_closes`/`_fetch_pcr` helpers), and the per-sector chains+history loops in
  `live_composite.compute_live`. *(The `_load_all_industries` outer per-sector loop
  stays serial — each iteration's inner fetches are now concurrent; flatten later if
  needed.)*
- Portfolio — `portfolio_svc/compute.compute_baselines` (per-symbol) and the
  `build_portfolio` holdings build (`portfolio-analyzer/src/portfolio.py`) fan out
  concurrently. **Plus:** baselines now recompute only when
  `compute.baseline_signature` (equity holdings + entries + day) changes — the
  periodic 10-min rebuild reuses cached baselines when nothing changed (`state.
  baseline_sig`), instead of re-fetching ~2N histories every cycle.
- Trade analyze — `_fetch_timeframes` (5 timeframes) and the independent
  SPY + sector-ETF + fundamentals fetches in `analyze` now run concurrently
  (after the early daily-sufficiency gate, so output is unchanged).

**HIGH — service-side per-tick churn (runs 24/7, no browser needed):** *(FIXED
2026-06-19)* `options_svc` `refresh_header` (a `get_quotes` proxy call + bridge read)
+ `publish_gex_status` (SQLite read) used to run on **every 30 s tick with no
market-hours gate** → ~2 proxy calls + 1 DB open + 2 cache writes every 30 s, all
day/all weekend. Now gated by `scheduler.periodic_refresh_due` — every tick during
market hours, throttled to once per `_OFFHOURS_INTERVAL_MIN` (5 min) off-hours/
weekends — **and** both use `cache_set(skip_unchanged=True)` so an unchanged view
writes/publishes nothing. The remaining per-tick proxy/DB churn outside market hours
is ~1/10th of before. *(Other services' serial fan-outs below are still open.)*

**MEDIUM — engine compute:** *(all FIXED 2026-06-19)*
- `shared/analysis_lib/technical.calculate_ema` is vectorized via `.ewm` (SMA seed +
  `ewm(alpha, adjust=False)` from the seed = the former loop's exact values, no Python
  loop). `macd_histogram_series` computes the histogram once; trade analyze reads
  `[-1]`/`[-2]` from it (was two `calculate_macd` calls = 4 EMA passes → 2).
  `volume_profile` buckets via `np.digitize`+`np.bincount` (was O(bars×bins) nested
  `iterrows`). Characterization tests pin numeric equivalence
  (`services/trade_svc/tests/test_technical.py`).
- `shared/bus.consume_commands` creates the consumer group **once** per (stream, group)
  per Bus (`self._groups`), not on every ~50 ms poll.
- Static workbooks: `sectors_ref.load_sectors_data` is now mtime-cached (`reset_cache()`
  for tests). (`watchlist.get_scan_symbols` was already mtime-cached — the `Top 20.xlsx`
  GEX-poll read only `stat()`s, never re-parsed.)
- `schwab_proxy.trader_request` routes through the pooled `token_mgr.session` (was bare
  `requests.*` → fresh TLS per `/accounts`/`/positions`/`/orders` call).
- `gex_history_db.load_today` / `load_today_with_grid` use a sargable
  `ts >= ? AND ts < ?` range (`_today_local_unix_range()`) so the `ts` index applies,
  instead of `DATE(ts,'unixepoch','localtime')=DATE('now')`. *(Not done: per-worker
  SQLite connection reuse — opening a read-only connection is sub-ms and sharing one
  across the service's executor threads would need `check_same_thread=False`+locking;
  deliberately left as a fresh connect per read.)*

**Already done right (don't "fix"):** all data pages version-gate repaints; Gamma and
Sentiment charts update Highcharts in place (`el.options=…; el.update()`, no flicker); `ui_guard`
suppresses dead-client callback noise; portfolio's SSE loop only republishes on a
`dirty` flag; the Redis connection and the marketdata Schwab session are pooled
singletons. **Hygiene:** stale `*.log.err` manual stderr captures are now
`.gitignore`-d (and the old ones removed).

## Tests

Each app's tests run from **inside that app folder** (entrypoints add the repo
root to `sys.path` at runtime):

```powershell
cd schwab-proxy        ; python -m pytest tests
cd options-scanner     ; python -m pytest tests -p no:randomly
cd sentiment-dashboard ; python -m pytest tests
cd trade-analyzer      ; python -m pytest .
cd portfolio-analyzer  ; python -m pytest tests
cd claude-driver       ; python -m pytest .
cd webgui              ; python -m pytest .   # 1336 green: transforms + shell smoke
```

> **In a worktree** (`.claude/worktrees/…`) there is no `.venv` — use the absolute
> `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`, and confine the `cd` to
> a **subshell** (`(cd webgui && …)`). A bare `cd` into a subdirectory leaves the
> shell there, and the hooks in `.claude/settings.json` are registered by RELATIVE
> path, so every subsequent Bash/PowerShell/Edit call then fails with a
> hook error and cannot be recovered from that session.

The 3-tier services run per folder from the repo root (NOT `pytest services` over
all of them — that puts multiple hyphenated app dirs on `sys.path` at once and
re-triggers the documented `config`/`scoring`/`notifier` module-name collisions).

> **⚠ The venv is at the REPO ROOT, not inside a git worktree.** When working in
> `.claude/worktrees/<name>/`, invoke it by absolute path:
> `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest …`

```powershell
# from the repo root, one service at a time
.venv\Scripts\python -m pytest services\sentiment_svc   # 279 passed / 1 documented-baseline fail
.venv\Scripts\python -m pytest services\options_svc     # 932 passed / 2 documented-baseline fail
.venv\Scripts\python -m pytest services\portfolio_svc   # 27
.venv\Scripts\python -m pytest services\trade_svc       # 56
.venv\Scripts\python -m pytest services\driver_svc      # 162
.venv\Scripts\python -m pytest shared\bus               # 15
.venv\Scripts\python -m pytest shared\contracts         # 37 (no app-dir imports — safe together)
```

**Known baseline failures — do NOT "fix" them as part of unrelated work, and do not
read them as a regression:**

- **options-scanner** — **11**, not the "~2" this line claimed until 2026-08-09.
  Re-measured that day at **11 failed / 1370 passed / 3 skipped**. The set, which
  is what you compare (never the count — see below):
  `test_gex_collector.py` ×5 (`test_next_boundary_*` ×4, `test_main_skips_before_market_open`),
  `test_gex_collector_lock.py::test_acquire_defers_when_fresh_other_owner`,
  `test_key_levels_doc.py` ×3, and
  `test_scanner_engine.py::TestEarningsAvoidance` ×2.
- **options_svc** — 2 date-relative `test_expected_move` failures (932 passed).
- **sentiment_svc** — `tests/test_compute_regime.py::test_daily_history_wins_over_session_latch`
  (the `$VIX1D` session latch beats the daily close: `assert 18.0 == 10.0`). Suite
  reads **279 passed / 1 failed** (2026-08-14; was 250/1). Reproduced at `7667920`,
  so it **predates** the dev/prod-environments branch; first documented 2026-08-08.

**Compare the failing SET, not the count.** A matching total is not evidence of a
clean run: this repo has a documented incident where two real regressions hid
behind two tests flipping to skipped while the total held steady. Run with `-rf`
and diff the node IDs name-by-name. It nearly bit again on 2026-08-09, when
options-scanner's passed/skipped drifted 1351/2 → 1370/3 across a change while the
failure count sat unmoved at 11 — that drift lives in the `test_gex_collector*`
group, which is timing-dependent.

The remaining per-service counts in the block above are indicative, not pinned.
Current, re-measured **2026-08-14** on the ring-graphics branch: **webgui 1336**
green and **sentiment_svc 279 passed / 1 failed** (the documented
`test_daily_history_wins_over_session_latch`). Re-measured **2026-08-09** on
`Using_Highcharts` at `b4ef24b`: **options_svc 932 passed / 2 failed**,
**options-scanner 1370 passed / 11 failed / 3 skipped**. portfolio_svc,
trade_svc, driver_svc, `shared/bus` and `shared/contracts` have **not** been
re-measured since they were first written down — treat those five as unverified
and measure your own baseline before trusting them.

## External processes (not in this repo)

The ML prediction servers (MES 8000 / MNQ 8001 / ES 8004 / NQ 8005) and the
options analytics service on 8200 are **separate, external processes**.
claude-driver addresses them over HTTP; this repo does not contain or start them.

## Design / plan docs

- [`docs/plans/2026-06-15-three-tier-architecture-design.md`](docs/plans/2026-06-15-three-tier-architecture-design.md) — **3-tier re-architecture** (GUI / per-domain services / Redis-Memurai backbone)
- [`docs/plans/2026-06-15-three-tier-architecture-plan.md`](docs/plans/2026-06-15-three-tier-architecture-plan.md) — bite-sized TDD implementation plan for the above
- [`docs/plans/2026-06-14-nicegui-webgui-design.md`](docs/plans/2026-06-14-nicegui-webgui-design.md)
- [`docs/plans/2026-06-14-nicegui-webgui-plan.md`](docs/plans/2026-06-14-nicegui-webgui-plan.md)
- [`docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md`](docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md) — **`/sentiment` Day/Week/Month ring graphics** (four gauges → two concentric SVG rings; the Week structural horizon; the Signals tile stack)
- [`docs/plans/2026-08-14-sentiment-trend-ring-graphics-plan.md`](docs/plans/2026-08-14-sentiment-trend-ring-graphics-plan.md) — bite-sized TDD implementation plan for the above
