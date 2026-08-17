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
Five homes, and the test for each is what a future session needs to *act*:

| Write it in | When |
|---|---|
| **CLAUDE.md** (here) | A durable invariant, convention, standard, or gotcha — something still true next month that changes how you write code |
| **[docs/CHANGELOG.md](docs/CHANGELOG.md)** | Dated shipping narrative: what shipped, the pieces, commit SHAs, test counts at the time, live-verification logs |
| **[docs/webgui-routes.md](docs/webgui-routes.md)** | Per-page behaviour detail — what a specific route renders, its cache keys, its own quirks |
| **`docs/plans/<date>-<feature>-{design,plan}.md`** | The reasoning and step plan for a feature, written as you build it |
| **[docs/manuals/](docs/manuals/README.md)** | Anything a **user** reads: the four built manuals. A user-visible behaviour change lands here too, not only in the CHANGELOG |

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
   The one exception is the **Tests** section's dated baselines, which exist precisely to be
   compared against — and even there, compare the failing *set*, never the count.

**History:** the *Last updated / Prior —* chain moved to the CHANGELOG on 2026-08-07. On
**2026-08-16** the file was audited again and cut **285 KB → ~100 KB**: per-route detail to
`docs/webgui-routes.md`, and ~60 accumulated feature narratives to the CHANGELOG. Nothing
was deleted — it was relocated. The rules above exist because the 2026-08-07 split fixed the
symptom and the file refilled in nine days.

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

## 3-tier architecture (approved 2026-06-15 — migration COMPLETE)

The monorepo was re-tiered (strangler-fig) into three **physically separate** tiers over a
**Redis (Memurai) backbone**. **All six domains are migrated** — sentiment, options,
portfolio, trade, driver, market — so the webgui imports only `nicegui` + `shared.bus` +
`shared.contracts`, and every page reads Redis. The shape:

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

**`start_all` order: Memurai → proxy → services → webgui.** Ports: `memurai=6379` plus one
per service (8210–8215). One shim survives by decision — `sentiment_bridge.json` is still
dual-written for `regime_filter`; retiring it (making `regime_filter` read Redis) is the last
open migration item. Full design:
[3-tier design doc](docs/plans/2026-06-15-three-tier-architecture-design.md).

## Folder map (what was copied in)

| Folder                 | Role                                                        | UI status        |
|------------------------|------------------------------------------------------------|------------------|
| `schwab-proxy/`        | Central Schwab API gateway / token manager. **Start FIRST.**| backend, :8100   |
| `options-scanner/`     | GEX/options scanner engines, scoring, paper engine, simulator. **`gex_history.db` stores FIVE view strings per symbol per minute** — `gex`/`charm`/`dex`/`vanna` plus **`prem`** (2026-08-15, per-strike traded premium from `flow_skew.premium_by_strike`, feeding the Premium Divergence strike ladder). `view` is free-form and a premium cell is `{call, put, net}` floats — exactly what the columnar float32 packer gates on — so the fifth view needed **no schema change**, and costs ~**+25%** on that DB. | engines only (Dash UI dropped) |
| `sentiment-dashboard/` | Market sentiment `scoring/` + `history_backfill` + `live_composite.py` (live intraday composite + bridge payload) + `publish_bridge.py` (headless bridge writer) + bridge + `sectors_ref.py`. **Its `market_calendar.py` was absorbed into `shared/market_calendar.py` and DELETED (2026-08-02)** — same module name and same three function names, but *inclusive* `prev/next_trading_day` vs the shared module's *exclusive*, an invisible one-day trap. | ported to NiceGUI `/sentiment` |
| `trade-analyzer/`      | `src/analysis` — fundamentals, recommendation, scoring, sector. | engines only (Tk UI dropped) |
| `portfolio-analyzer/`  | `src/` — sector breakdown, vs-sector perf, live streaming.  | engines only (Tk UI dropped) |
| `claude-driver/`       | Legacy morning/intraday orchestration. The order-approval queue was REMOVED 2026-07-08; only `RISK_LIMITS` in `config.py` is still read. | superseded by `driver_svc` |
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
the drawer FOOT 2026-08-12**; **grouped into CAPTIONED SECTIONS 2026-08-16**):
the left drawer holds **13 items** — 10 in three captioned sections plus a
bottom-pinned **`SYSTEM_RAIL`** block (**System Status**, **Settings**, **Stop
All Services**) — and the active group's
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
`SETTINGS_CHILDREN` survives as a More tab, a peer of EOD Report. **Market Dashboard is the FIRST tab of the Trend &
Sentiment group** (it was a flat item until 2026-07-27), and since
`_nav_group_link` navigates to `children[0]`, that group's rail item lands on
`/market`.

**The rail's ORDER is data, not the sequence of render calls (2026-08-16).**
`NAV_SECTIONS` is a list of `(caption, entries)` — **MARKETS** (Dealer
Positioning · Opportunity Board · Flow Alerts · Trend & Sentiment) · **STRATEGY**
(Strategy Tools · Options · Trade Analyzer · Claude Trades) · **ACCOUNT**
(Portfolio · More) — where an entry is either a GROUP (`_nav_group_link`) or a
standalone rail page (`_nav_link`). Entries reference their group/page **by name**
via `_sec_group`/`_sec_page`, so `_NAV_GROUPS` / `OPTIONS_RAIL` / `FLAT_NAV` stay
the single source of every label + icon and **a typo raises at import** rather
than silently dropping a page out of the menu. `FLAT_NAV` no longer drives order
(it is now just the flat-route registry `_NAV_LABEL` iterates). Caption counts are
**derived** from `len(entries)` — never written down. The sentiment group renamed
**"Market Trend & Sentiment" → "Trend & Sentiment"** now the MARKETS caption
carries the word. ⚠ The Options group sits under STRATEGY while Dealer Positioning
/ Opportunity Board / Flow Alerts sit under MARKETS — deliberate: those three are
market-WIDE reads, the Options group is the per-signal find → analyze → track →
repair workflow. `test_nav_sections_partition_the_rail_with_nothing_lost_or_doubled`
is the guard that matters: a regrouping that drops or doubles an item is invisible
to every other test. **Stop All Services** is now a **danger-outlined button**
(`_nav_danger_link`) sitting LAST in `SYSTEM_RAIL` (`SYSTEM_DANGER_ROUTE`) — the
one irreversible item in the rail, moved out from between System Status and
Settings so an overshoot can't land on it. A **live service-status card**
(`_status_card` / PURE `status_card_facts`) sits above that block: it reads the
throttled `/health` fan-out the watcher ALREADY runs (no new probe; latency is the
mean of services that ANSWERED — a timed-out probe would report the failure, not
the feed) and its warning count **IS** `len(alerts.unhealthy_keys(...))`, the same
computation behind the System Status badge, so the two cannot drift. No probe data
→ **"unknown"**, never a confident "Data feed live"; a bus outage resets it via
`_guarded_compute` rather than stranding the last good reading. The card is
**display:none** in the rail (not faded — it must surrender its height too).

**The drawer is a 68px ICON RAIL that expands to 264px on hover and OVERLAYS
the page.** It is LAID OUT at `NAV_WIDTH_RAIL=68` via Quasar's `width` prop
(`drawer_width(pinned)` → 68, or `NAV_WIDTH_OPEN=264` when pinned — `ui.left_drawer`
has **no `width` kwarg**, so it goes through `.props(f"width={...}")`), and
`_NAV_CSS` widens it on hover/`:focus-within` with
`.q-drawer:has(> .nav-drawer:not(.nav-pinned))` → `width: 264px !important`
(**interpolated from `NAV_WIDTH_OPEN`** since 2026-08-16 — that one rule is
appended to `_NAV_CSS` as its own f-string, since the main block is a plain
literal whose CSS braces would otherwise all need escaping).
**Quasar's LAYOUT still uses 68, so `.q-page-container`'s padding never changes —
the expanded menu OVERLAYS content rather than reflowing it.** That is deliberate:
this app's Highcharts have no ResizeObserver, so a reflow on every hover would
leave charts mis-sized. No Quasar mini-mode, no JS, no hover round-trips. Because
only the icon is visible when collapsed, **the icon is the affordance** (the
`icon` arg is live again — the earlier colored-dot indicator is retired; a test
guards that the 13 drawer icons stay non-empty + mutually distinct). Labels/title
clip and fade in via opacity; `.nav-drawer { overflow-x: hidden }` stops the
264px of content raising a scrollbar in the rail. **Section captions cross-fade to
HAIRLINES in the rail** (2026-08-16): `.nav-sep` is the exact INVERSE of the
`.nav-title` opacity rule — visible by default, hidden under the same three
"drawer is open" selectors — with both absolutely placed inside ONE fixed-height
`relative` box (`_nav_section_header`), so neither state reflows the other. The
**hamburger pins/unpins**
(`_toggle_pin`, persisted in `app_settings` `nav_pinned`, default False) rather
than show/hide: pinned lays out at 264 (the page genuinely reflows — correct for
an explicit choice) and the `.nav-pinned` class opts out of the hover rule. The
**active-icon accent** is `.nav-drawer .nav-active .nav-icon` — 3 classes +
`!important`, which it must be to out-specify `theme.build_nav_css`'s
`[menu].text` rule (see the gotchas). ⚠ **THREE more rail colours need the same
treatment, and all three shipped broken until a live browser caught them
(2026-08-16)** — a Tailwind `text-[#…]` is ONE class with no `!important`, so it
loses both to `build_nav_css`'s `.nav-drawer a{color:…!important}` and to
`_NAV_CSS`'s own 3-class `.nav-drawer .nav-active .nav-label`. Measured: the
danger button's LABEL rendered menu-grey `rgb(152,161,192)` (its icon was fine —
that one already had the rule), and the static **AI** pill rendered white
`rgb(238,241,246)` on the ACTIVE row, the only row it ever appears on. Hence
`.nav-drawer .nav-danger .nav-icon, .nav-drawer .nav-danger .nav-label` and
`.nav-drawer .nav-pill`, each `!important`. **The rule: any colour you set on a
rail element needs ≥3 classes + `!important`, or it is decorative only.** Per-page alert badges **float on the tabs**
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

**The app-wide alert/badge watcher, the Market Dashboard, the Market Summary Ticker and the
multi-strategy Swing Scanner each have their build notes in
[docs/CHANGELOG.md](docs/CHANGELOG.md) and a design/plan pair under `docs/plans/`; per-page
behaviour is in [docs/webgui-routes.md](docs/webgui-routes.md).**

Routes:

| Route | Page | Status |
|-------|------|--------|
| `/` | Options · Market Scanner — 0-DTE / Swing / Directional subtabs. Reads **`cache:options:scan_day`** (the day union), not `scan`, so dropped signals stay dimmed + frozen to EOD. [Detail](docs/webgui-routes.md) | built |
| `/options/matrix` | Opportunity Board — one sortable row per watchlist symbol, default-sorted by Hotness. Tier-1 reader of `cache:options:matrix`. [Detail](docs/webgui-routes.md) | built |
| `/options/flow` | Flow Alerts — today's flow alerts (crossover · unusual activity · gamma flip · big_delta), newest first. Reader of `cache:options:flow_alerts`; resets overnight. [Detail](docs/webgui-routes.md) | built |
| `/options/paper` | Paper Ledger — ledger table + shared detail panel; open trades repriced for live unrealized P&L on the manage tick. [Detail](docs/webgui-routes.md) | built |
| `/options/captured` | Captured Signals | built |
| `/options/portfolio` | Paper Account (the engine’s paper account) | built |
| `/options/calculator` | Calculator — summary tiles + P&L heatmap over real chain strikes, multi-leg builder, IV implied from the traded mark. Persists UI state across navigation. [Detail](docs/webgui-routes.md) | built |
| `/options/swing` | Strategy Finder — multi-strategy single-symbol scan (directional / spreads / neutral) ranked on one 0–100 Fit+Quality score; sub-50 and Weak candidates are cut service-side. [Detail](docs/webgui-routes.md) | built |
| `/options/gamma` | Dealer Positioning — GEX/Charm/DEX/Vanna bars + intraday heatmap, flip/walls, the Flow and Net Prem console panels, Term structure, and the Claude briefing (Analyze). [Detail](docs/webgui-routes.md) | built |
| `/options/simulator` | Simulator — Replay / What-if / IV-shock over the shared multi-leg builder; persists UI state across navigation. [Detail](docs/webgui-routes.md) | built |
| `/options/expected-move` | Expected Move — 6-month candles + a forward ATM-IV expected-move cone to expiry, with leg strike lines. ⚠ its IV and move deliberately do **not** match ThinkorSwim. [Detail](docs/webgui-routes.md) | built |
| `/options/rescue` | Rescue — at-risk credit spreads → a ranked, commission-aware adjustment menu; execute cards apply behind a stale-price guard. | built |
| `/sentiment` | Sentiment — the Market Regime Console (header · Sentiment/Trend/Signals cards · regime block · footer) over two concentric Day/Week/Month rings, plus the intraday graphs. [Detail](docs/webgui-routes.md) | built |
| `/sentiment/sectors` | Sector & Industry — a magnitude-forward **heat grid**: Day/Week/Month as three flush filled tiles, intensity normalised **per column** on that column's own p90, plus P/C and expandable industries. Sortable; RRG dropped. Reader of `cache:sentiment:sectors`. [Detail](docs/webgui-routes.md) | built |
| `/sentiment/rotation` | Sector Rotation — verdict strip (regime · **diverging spread gauge** on a −3…+3 scale with both ±threshold triggers · the spread and how far past its trigger it sits), a **weight-proportional flow band**, and four quadrant panels. Quadrant map table + rotating-from/into lists retired. Cached, manual Refresh only. [Detail](docs/webgui-routes.md) | built |
| `/sentiment/rrg` | RRG — full-width relative-rotation chart, one spline per sector with a faded tail. Cached, manual Refresh only. | built |
| `/sentiment/momentum` | Momentum — regime-conditioned momentum cascade over sectors / industry ETFs / stocks. Recomputed **once nightly** (16:20 CT), not on the tick. [Detail](docs/webgui-routes.md) | built |
| `/trade` | Trade Analyzer — on-demand Position (1–8wk) + Investor verdicts; Position runs the backtested IC-weighted factor model. Deep Dive and AI Query open separate reports. [Detail](docs/webgui-routes.md) | built |
| `/driver` | Claude Trades — monitor + override for the autonomous Claude decision layer, trading defined-risk spreads into its **own isolated paper book**. Paper only. [Detail](docs/webgui-routes.md) | built |
| `/settings` | Settings — alert/ticker preferences, the in-app theme editor, Schwab + Claude API call counts, and maintenance actions. [Detail](docs/webgui-routes.md) | built |
| `/portfolio` | Portfolio — Holdings / Sectors / Performance over the portfolio model, with live-streaming P&L via the service’s SSE consumer. | built |
| `/eod` · `/eod/detail` | EOD Report — Summary + Detailed aggregator over the `options:*` and `driver:*` caches; Generate archives standalone HTML under `webgui/data/eod/<date>/`. [Detail](docs/webgui-routes.md) | built |
| `/market` | Market Dashboard — live grid of ~48 macro tickers in framed category panels, coloured by semantic risk-on/off. Reader of `cache:market:dashboard`. [Detail](docs/webgui-routes.md) | built |
| `/status` | System Status — health board probing Memurai / proxy / Schwab auth / the six services / webgui, plus cache freshness; per-component windowless Restart. | built |
| `/terminate` | Stop All Services — confirm-gated stop of the whole local stack (Memurai is left running). | built |

The `pages/options/` subpackage shares `header.py` (compact quotes/VIX/sentiment
strip), `detail.py` (collapsible Trade detail panel, reused by all signal
tables), **`flow_panels.py`** (PURE builders for the two Options Flow console
panels on the Gamma page's **Flow** + **Net Prem** subtabs — see the dedicated
section below), `svg.py` (gradient-bar / range-marker SVG — the composite-score
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

**The migration is COMPLETE (2026-06-28) — the ENTIRE webgui is Tailwind-only**, with **zero
`.style()`/`:style=` anywhere in `webgui/pages`**, held there by the
`test_no_inline_style.py` guard, which covers every page. **Add any new page to that guard.**
The phase-by-phase (P0–P8) log is in [docs/CHANGELOG.md](docs/CHANGELOG.md); the rationale is in
[`docs/plans/2026-06-28-tailwind-first-ui-migration-design.md`](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md).
The only inline styling that
remains is the **documented out-of-scope set**: Highcharts option dicts (chart config), raw
`ui.html()` HTML-string fragments + their CSS (`EOD_CSS`/`EXPLAIN_CSS`/the Gamma Analyze infographic
/ the EOD export docs), and Quasar `color=` props. The ONE escape hatch is per-page **Quasar-internal**
`ui.add_css` (`QUASAR_INTERNAL_CSS` field/tab/menu internals; `_NAV_CSS`; the table-internal
`SCAN_CSS`/`PAPER_CSS`/`CAPTURED_CSS`/`_RESCUE_CSS`/`DRIVER_CSS` sticky-thead/`.q-table__middle`).
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
  Set `borderWidth:0`. **`states:{inactive:
  {enabled:False},hover:{enabled:False}}`** stops the hover-dim/fade.
  **`plotBackgroundColor` is NOT banned here — that instruction was narrower than it
  read, and it was corrected 2026-08-15.** This line said "drop `plotBackgroundColor`
  (the mesh)" because at the time (commit `e6ef342`) it held a FLAT grey
  (`HEATMAP_SEP`) that showed through the gaps between individually-bordered cells
  and read as a separator mesh. `interpolation: True` renders ONE continuous image
  with no cell gaps at all, so nothing can show BETWEEN cells any more — a fill here
  is a wash painted BEHIND the image. `gamma._wash_background()` uses exactly that
  for the plasma blue→magenta wash. What must not come back is a **flat opaque**
  plot background, which would defeat the `rgba(...,0)` zero stop by putting a solid
  colour where the page used to show through.
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

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q`. The dated baseline, the
standing warning about comparing the failing *set* rather than the count, and the
worktree/subshell caveat all live in the **Tests** section — don't duplicate a count here.
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

**Feature history lives in [docs/CHANGELOG.md](docs/CHANGELOG.md), not here.** The
per-feature build narratives that used to sit in this section — what shipped, the pieces,
the test counts at the time, the live-verification logs — were moved there on 2026-08-16.
Per-page detail moved to [docs/webgui-routes.md](docs/webgui-routes.md). Each feature also
has its own design/plan pair under `docs/plans/`. **Add new work to those, not to this file**
— see the maintenance banner at the top.

**⚠ Known open issue — the PRICE sub-score NaN exposure (not fixed).**
`sentiment_svc/compute._finite_pcts` guards only the SECTOR input. An all-NaN read of the
structural price inputs (`macd_hist`/`rsi`/`adx`, feeding `score_price` with a hardcoded
`vwap_pct=0.0`) scores **82.50 — near-maximum bullish — at UNCHANGED confidence**, where a
sane read scores 56.25; the same all-NaN read in `compute_intraday_trend` (the LIVE Day
gauge) scores **92.50**. The fix must cover both call sites with one shared filter.

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
See the "App theme — dark-navy 'dashboard'" section. **Five sections are page-scoped
languages, NOT the app-wide palette and NOT surfaced in Settings → Appearance:**
`[flow]` (the Options Flow console panels, the `/options/gamma` Flow + Net Prem
subtabs only — builder `flow_colors` + `FLOW_KEYFRAMES_CSS`),
`[console]` (Market Regime Console, `/sentiment` only), `[macro]` (the Macro Board
redesign, `/market` only), `[sectors]` (the Sector & Industry heat grid,
`/sentiment/sectors` only) and `[rotation]` (the Sector Rotation board,
`/sentiment/rotation` only). Each has matching `theme.py` builders
(`build_console_*` / `build_macro_*` / `build_sector_*` / `build_rotation_*`); the
first three are injected via that page's ONE `ui.add_css` escape-hatch block.
**`[sectors]` and `[rotation]` are the two that prove the rule holds** — a heat
grid and a diverging gauge are nothing but colour and measurement, and both need
**no `ui.add_css` at all**, only tokens and a font `<link>`. Their colour ramps
are deliberately NOT config-driven: both are data-driven cell maps (the category
excluded above, alongside the gauge face and the score/heat/P&L zone maps),
living in `webgui/pages/sector_heat.py` and `webgui/pages/rotation_view.py`.
`webgui/pages/oklch.py` holds the oklch→sRGB conversion both use — both supplied
designs were authored in oklch, and both sit at the dark end of the range where
an sRGB interpolation visibly bunches the low steps.

⚠ **The four RRG quadrant colours now exist in TWO palettes, on purpose.**
`sentiment_rotation.quadrant_color` (Leading `#66bb6a` / Improving `#3fb6c7` /
Weakening `#ffd54f` / Lagging `#ef5350`) still drives the **RRG scatter** and the
**Sector & Industry** quadrant text; the Sector Rotation board's supplied design
re-hues them (Leading 158 / Improving **232 blue** / Weakening **80 olive** /
Lagging 22) and `rotation_view.QUAD_CLASSES` implements that **for that page
only**. Unifying the two is a live open question, not an oversight.

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
cd webgui              ; python -m pytest .   # 1564 green: transforms + shell smoke
```

> **In a worktree** (`.claude/worktrees/…`) there is no `.venv` — use the absolute
> `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`. Confining the `cd` to a
> **subshell** (`(cd webgui && …)`) is still the tidier habit, but it is **no longer
> load-bearing**: the hooks in `.claude/settings.json` resolve their script from
> **`${CLAUDE_PROJECT_DIR:-.}`** (fixed 2026-08-16), so a persistent `cd` out of the
> repo root can no longer wedge the session. Before that fix the hook paths were
> relative, and a single bare `cd` made every subsequent Bash/PowerShell/Edit call
> fail with a hook error **that could not be recovered from** — the hook runs before
> the command, so the shell could not `cd` back. Two facts about the hook
> environment, both verified with a throwaway probe rather than assumed: it **does**
> export `CLAUDE_PROJECT_DIR` (absent from an ordinary tool shell, so it cannot be
> checked by echoing it), and hook commands run through a **POSIX shell, not
> cmd.exe** — `${VAR:-default}` expands, `%VAR%` does not.

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
  `test_scanner_engine.py::TestEarningsAvoidance` ×2. **Re-confirmed 2026-08-15**
  (1439 passed / 11 failed / 2 skipped) — same set, unchanged.
- **options_svc** — **none as of 2026-08-15** (1091 passed). The 2 date-relative
  `test_expected_move` failures previously listed here now pass — they depend on
  the run date, so expect them to return. ⚠ `test_flow_alert_window.py::
  test_gth_signal_still_fires_at_the_open` is **FLAKY**: observed failing once in
  a full run, then passing in isolation and in two subsequent full runs.
- **sentiment-dashboard** — **2**, both in `tests/test_apply_sector_perf.py`
  (`test_apply_sector_perf_merges_into_existing_quotes`,
  `test_apply_sector_perf_renders_from_merged_map`), failing with
  `ModuleNotFoundError: No module named 'sentiment_dashboard'`. They test the old
  **Tk UI entrypoint, which this repo deliberately never copied** (see the folder
  map), so they can never pass here. Suite reads **487 passed / 2 failed**;
  first measured 2026-08-14.
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
Current: **webgui 1356** green, re-measured **2026-08-15** on the plasma-palette
branch (1336 + 9 palette tests + 11 bevel/glow tests). **sentiment_svc 279 passed / 1 failed** (the
documented `test_daily_history_wins_over_session_latch`), last measured
**2026-08-14** on the ring-graphics branch. Re-measured **2026-08-09** on
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
- [`docs/plans/2026-08-15-gamma-plasma-palette-design.md`](docs/plans/2026-08-15-gamma-plasma-palette-design.md) — **Dealer Positioning plasma palette + wash** (GEX/Charm/DEX/Vanna + Term recoloured cyan/magenta; why the `plotBackgroundColor` ban was narrower than it read)
- [`docs/plans/2026-08-16-app-reference-guide-design.md`](docs/plans/2026-08-16-app-reference-guide-design.md) — **the Reference Guide** (a fourth manual; the per-page template, and why the audience level drives the plain-language + external-citation rules)

## User-facing manuals

**Four** manuals under [`docs/manuals/`](docs/manuals/README.md), each authored once
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

⚠ **`webgui/page_help.py` is the fifth manual and the most-read prose in the app** —
the per-page hover guides. It is the least likely thing to be touched when a feature
moves, so it rots first: the 2026-08-16 audit found it claiming a 5-minute paper
cycle that is hourly, a fixed $500 driver target that ratchets $250–$1,000, and
three flow-alert detectors where there are four. Treat it as documentation with a
test suite, not as code.
