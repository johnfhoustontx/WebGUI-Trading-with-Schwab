# The web app's shell, pages and theme

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## webgui structure (NiceGUI app)

`webgui/main.py` is the server + nav shell (**sub-menus are TABS** since
2026-07-11; the drawer became an **ICON RAIL** 2026-07-15; **reorganized
2026-07-27; **Strategy Tools group added 2026-07-28**; **system pages moved to
the drawer FOOT 2026-08-12**; **grouped into CAPTIONED SECTIONS 2026-08-16**):
the left drawer holds **17 items** — a top-pinned **Desk** and **Symbol** in a
**caption-less leading `NAV_SECTIONS` block** (Desk 2026-08-18, Symbol 2026-09-17:
the two entry points, *what is happening* and *tell me about X*), 11 in three captioned
sections, plus a bottom-pinned **`SYSTEM_RAIL`** block (**System Status**,
**Settings**, **Stop All Services**, **Sign out**) — and the active group's
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
`NAV_SECTIONS` is a list of `(caption, entries)` — a **caption-less leading block**
(Desk · Symbol) · **MARKETS** (Dealer
Positioning · Opportunity Board · Flow Alerts · Market News · Trend & Sentiment) · **STRATEGY**
(Strategy Tools · Options · Strategy Finder · Trade Analyzer) · **ACCOUNT**
(Portfolio · More) — where an entry is either a GROUP (`_nav_group_link`) or a
standalone rail page (`_nav_link`). **A caption of `None` means render NO header
at all** — not an empty one — and the drawer loop skips `_nav_section_header` for
it; that block is the rail's top-pinned mirror of `SYSTEM_RAIL`, and its pages get
a bare one-crumb breadcrumb since no section sits above them. ⚠ `first=(_i == 0)`
in that loop is consequently **never True**, which is deliberate: the first
*visible* caption keeps its `mt-4`, and that gap is what separates MARKETS from the
Desk and Symbol rows above it. Entries reference their group/page **by name**
via `_sec_group`/`_sec_page`, so `_NAV_GROUPS` / `OPTIONS_RAIL` / `FLAT_NAV` stay
the single source of every label + icon and **a typo raises at import** rather
than silently dropping a page out of the menu. `FLAT_NAV` no longer drives order
(it is now just the flat-route registry `_NAV_LABEL` iterates). Caption counts are
**derived** from `len(entries)` — never written down. The sentiment group renamed
**"Market Trend & Sentiment" → "Trend & Sentiment"** now the MARKETS caption
carries the word. **Every rail route is a shell page**, so `_nav_link` always
navigates in place and `_LANDING_ROUTES` in `test_shell.py` holds the two
caption-less routes, `/desk` and `/symbol`. (`EXTERNAL_RAIL_ROUTES` and `_nav_link`'s `new_tab=` existed solely for
the Live Mirror and went with it on 2026-09-02 — a rail row that opens elsewhere
must not claim the active wash, so anything reviving that shape needs both.) ⚠ The
Options group sits under STRATEGY while Dealer Positioning
/ Opportunity Board / Flow Alerts / Market News sit under MARKETS — deliberate: those four are
market-WIDE reads, the Options group is the per-signal find → analyze → track →
repair workflow. `test_nav_sections_partition_the_rail_with_nothing_lost_or_doubled`
is the guard that matters: a regrouping that drops or doubles an item is invisible
to every other test. **Stop All Services** is a **danger-outlined button**
(`_nav_danger_link`, `SYSTEM_DANGER_ROUTE`) — the one irreversible item in the
rail. ⚠ **Its position argument INVERTED on 2026-09-06 and the comment in
`main.py` records the current one** — it sat last so nothing could be overshot
INTO it, but the app is now used from a phone, where the bottom edge is the
easiest target, so the last slot is the worst place for it. **`Sign out` took
that slot** (`login_page.LOGOUT_ROUTE`, the one existing raw `@app.get` that
clears both cookies and 303s to `/login`): an overshoot now costs a re-login,
not the rest of the trading day. It is the one `SYSTEM_RAIL` route that is
**not a shell page**, which is exactly why `_nav_link` needs nothing special for
it — `active` is always a rendered page's route, so the wash is unreachable by
construction. The stop itself also demands a **fresh TOTP code** in its confirm
dialog (`pages/terminate.verify_stop_code`, persisting the accepted counter to
the SAME `auth_store` file the login form reads, so a code spent on one cannot
serve the other). ⚠ `test_nav_sections_partition_the_rail_with_nothing_lost_or_doubled`
does NOT cover `SYSTEM_RAIL` — it asserts those routes stay OUT of the sections
— so a footer row dropped or doubled is caught only by the drawer-icon
count/distinctness test and the two `test_shell.py` sign-out tests. A **live service-status card**
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
guards that the 17 drawer icons stay non-empty + mutually distinct). Labels/title
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
`shell.subtab_slot()` + `.compact-subtabs` (e.g. the Gamma
GEX/Charm/DEX/Vanna/Flow/Term picker, a `ui.tabs` since 2026-07-11 — same
value/on_value_change API as the old `ui.toggle`). Pages live in
`webgui/pages/`; each leaf exposes `render()` called inside the shell
`_layout`. `webgui/proxy.py` wraps `schwab-proxy/proxy_client.py` and adds
`health()`. Pure transforms / SVG builders are unit-tested (`webgui/tests/`);
heavy engine calls run off-thread via `nicegui.run.io_bound`.
**`webgui/voice.py`** is the Desk's spoken-alert clip cache — pure phrase
builders over an `edge-tts` synthesis layer, writing mp3s to `webgui/data/voice/`
(gitignored) which `main.py` mounts at **`/voice`** beside `/static`. ⚠ It is the
**one deliberate exception** to the Tier-1 import rule: `edge_tts` is neither an
engine nor a Schwab caller but a presentation concern, the audio equivalent of
the bundled WAVs in `webgui/static/sounds/`, and the import is **lazy** so the
module stays importable on a machine without the package (a test guards that).
Its public surface never raises — every failure (no network, no package,
unwritable cache) degrades to `None`, because the alternative to silence is a
traceback on the landing page.

**The app-wide alert/badge watcher, the Market Dashboard, the Market Summary Ticker and the
multi-strategy Swing Scanner each have their build notes in
[docs/CHANGELOG.md](../CHANGELOG.md) and a design/plan pair under `docs/plans/`; per-page
behaviour is in [docs/webgui-routes.md](../webgui-routes.md).**

Routes:

| Route | Page | Status |
|-------|------|--------|
| `/` | **Redirect to `/desk`** (2026-08-18; was `/market` from 2026-08-16, and the Market Scanner before that). A redirect, not a second render — the shell keys the active nav item and breadcrumb off the route, so a page at two URLs would highlight nothing. | built |
| `/desk` | **Desk — the HOME page.** Single-screen aggregate: regime + Day/Week/Month sentiment & trend rings · dealer positioning for `$SPX`/`SPY`/`QQQ`/`$NDX` (spot, flip, walls, net GEX, structure bar) · top-5 Opportunity · newest-5 Flow · newest-5 headlines · merged paper + captured Positions with rescue flags. Tier-1 reader of **11 views** on ONE batched 2 s `read_versions`. Read-only + click-through. **No Highcharts** (deliberate). [Design](../plans/2026-08-18-desk-home-dashboard-design.md) | built |
| `/symbol` | **Symbol Dossier** — one screen per ticker (`?symbol=` — linkable, and allow-listed through `shared.symbols.clean_symbol` before it names anything): structure · volatility (Vol Rank, IV vs HV, expected move) · context · today's signals with age + score trend · flow · in the news · open positions in every book, each band linking out to the page that owns it. Reads 11 shared views + its own `cache:options:dossier:<SYMBOL>` on ONE batched 2 s `read_versions`. **Cache wins**: the paid on-demand `dossier` command (4–5 Schwab calls, 15-min TTL, a 60-s service-side dedup) only FILLS gaps, and is enqueued on navigation or Refresh for a symbol the scanner does not cover — never by the poll. **Private only** (it enqueues, so it is not a public live screen). No Highcharts. [Detail](../webgui-routes.md) | built |
| `/options/scanner` | Options · Market Scanner — 0-DTE / Swing / Directional subtabs. Reads **`cache:options:scan_day`** (the day union), not `scan`, so dropped signals stay dimmed + frozen to EOD. ⚠ Each row's `setup_key` (`SYMBOL|TYPE|EXPIRATION`, strikes excluded) is a LOOKUP into the envelope's `setups` persistence map, **never a row key** — row identity stays `id` — and a setup whose start was not observed carries `age_unknown`, never a `first_seen` stamped `now`. [Detail](../webgui-routes.md) | built |
| `/options/matrix` | Opportunity Board — one sortable row per watchlist symbol, default-sorted by Hotness. Tier-1 reader of `cache:options:matrix`. The symbol cell opens its `/symbol` dossier — drawn only where `shell.can_navigate` says the route exists, so the public `/opportunity` copy stays plain text. **Rows gained `call_wall`/`put_wall`/`net_gex`/`atm_iv`/`iv_state`/`dealer_regime` on 2026-08-18** (for the Desk; additive, no contract change — `MatrixSnapshot` validates only `rows: list[dict]`). All degrade to `None`/`"na"`, **never `0`** — the off-hours case turns on that distinction. [Detail](../webgui-routes.md) | built |
| `/options/flow` | Flow Alerts — today's flow alerts (crossover · unusual activity · gamma flip · big_delta), newest first. Reader of `cache:options:flow_alerts`; resets overnight. [Detail](../webgui-routes.md) | built |
| `/news` | Market News — three regions: headlines (one line each, a High/Med/Low impact pill, times in CT), an **SEC / EDGAR** panel, and the economic **calendar** tiles. The SEC kinds are split out at the PRODUCER: `cache:news:feed` carries headlines only, `cache:news:sec` the Form 4s and offerings, `cache:news:calendar` the tiles (`news_svc`); Refresh enqueues `news_refresh` on `cmd:news`, which re-checks the calendar then polls the feeds. The Desk's headlines strip reads `news:feed`; the Symbol page's *In the news* band reads `news:feed` + `news:sec`. A public copy runs at live `/news` (`news_live`), reading only `feed_public`, `sec_public` and `calendar_public`. [Detail](../webgui-routes.md) | built |
| `/options/paper` | Paper Ledger — ledger table + shared detail panel; open trades repriced for live unrealized P&L on the manage tick. [Detail](../webgui-routes.md) | built |
| `/options/captured` | Captured Signals — newest capture first, with a day footer (opened/closed today · booked P&L · open P&L). [Detail](../webgui-routes.md) | built |
| `/options/portfolio` | Paper Account (the engine’s paper account) | built |
| `/options/calculator` | Calculator — the shared **entry panel** (ticker · strategy · expiry strip · chain grid beside the leg table) over collapsed pricing assumptions, six metric cards + the P&L matrix, in its own `[calc]` palette. **No action buttons**: a landed chain prices the legs and implies IV, and every edit re-prices after a 0.3 s debounce. ⚠ A grid click MOVES the leg on that side and type (adding one only when none matches) and prices at the MARK whichever side (Bid sells, Ask buys); each row's Bid / Mark / Ask dropdown re-prices it. **RATE MY TRADE** grades the legs with the Strategy Finder's scorer + checklist (`calc_rate` → `cache:options:calc_rating`) into BUY / CAUTION / PASS over the shared Trade detail panel. Persists UI state across navigation. A public copy runs at `/calculator` (`calc_live`; quotes hidden while the switch is off). [Detail](../webgui-routes.md) | built |
| `/options/swing` | Strategy Finder — single-symbol scan over seven build groups (directional · spreads · iron condors · straddles & strangles · butterflies & condors · calendars & diagonals · stock + options) ranked on one 0–100 Fit+Quality score, built on **every listed expiry** in the range — the whole chain by default (*All*, `dte_max: null`). ⚠ A range holding **more than 30 expirations asks first**: the service fetches no chain and answers with four `expiry_choice`s — Next 30 days · Next 90 days · Monthlies only (Schwab type `S`) · Everything — remembered per symbol while the page is open; a choice builds only its expirations, but the IV / expected move stay the whole chain's. The Income Window never asks. Sub-50 and Weak candidates are cut service-side, then only the **best 25 of each strategy type** are kept (the rest counted as `not_shown`) and the list pages 50 rows at a time server-side. A trade open through an earnings report is **flagged, not dropped**. ⚠ Paper only for the credit spreads, iron condors and `shared.structures.LEDGER_DEBIT`; straddles/strangles stay analysis only (D1). [Detail](../webgui-routes.md) | built |
| `/options/income` | Income Window — the 30–45 DTE premium board (put + call credit spreads, cash-secured puts, covered calls against held lots), jointly ranked across the whole watchlist. Tier-1 reader of `cache:options:income`, published **once daily** from `[slots.income]`. ⚠ Rows are **heterogeneous** (an adapted spread carries both the flat and the normalized shape, a `SHORT_PUT` only the normalized) — read a field both carry, and read the per-CONTRACT `net_credit`, never the per-share `credit`. [Detail](../webgui-routes.md) | built |
| `/options/shares` | Shares — the paper account's equity lots (put assignment converts a cash-secured put into stock at the strike). A second **reader** of `cache:options:paper_account`, not a second book. ⚠ No live equity mark exists anywhere in this app, so Mark/Unrealized are an em-dash on every row; a covering call is matched per **symbol**, not per lot. [Detail](../webgui-routes.md) | built |
| `/options/gamma` | Dealer Positioning — GEX/Charm/DEX/Vanna bars + intraday heatmap, flip/walls, the Flow and Net Prem console panels, Term structure, and the Claude briefing (Analyze). [Detail](../webgui-routes.md) | built |
| `/options/simulator` | Simulator — **Price & Time · Volatility · History** tabs (that order, since 2026-09-12) under the same entry panel, over ONE position shared with the Calculator (`shared_position`; no copy buttons). Its grid reads **`cache:options:sim_chain`**, published by `sim_fetch` from the SAME `/chains` call as the snapshot and written before `sim_meta`; leg strikes still come from `sim_meta`, since the engine prices only contracts in its snapshot. Persists UI state across navigation. A public copy runs at `/simulator` (`sim_live`; Price & Time only). [Detail](../webgui-routes.md) | built |
| `/options/expected-move` | Expected Move — 6-month candles + a forward ATM-IV expected-move cone to expiry, with leg strike lines. ⚠ its IV and move deliberately do **not** match ThinkorSwim. [Detail](../webgui-routes.md) | built |
| `/options/rescue` | Rescue — at-risk credit spreads → a ranked, commission-aware adjustment menu; execute cards apply behind a stale-price guard. Its ad-hoc form alone is also the public `/rescue` screen (see "The public live screens"). | built |
| `/sentiment` | Sentiment — the Market Regime Console (header · Sentiment/Trend/Signals cards · regime block · footer) over two concentric Day/Week/Month rings, plus the intraday graphs. [Detail](../webgui-routes.md) | built |
| `/sentiment/bullbear` | Bull / Bear Map — a lazily-expanding sector → GICS sub-industry → stock tree (a sub-industry has no ETF: it is an equal-weight basket of the GICS Map tab's listed stocks, keyed by its 8-digit code, and the Momentum page shares the same levels) showing absolute trend and relative strength vs SPY as **separate** marks (never blended) plus a live day-move, with participation as a third breadth axis beside the quadrant. ⚠ **Falling · Leading is the trap** a relative-only screen calls a buy. Headline is quadrant **counts**, never a regime verdict; `payload["regime"]` is never read. Reader of `cache:sentiment:bullbear`. [Detail](../webgui-routes.md) | built |
| `/sentiment/sectors` | Sector & Industry — a magnitude-forward **heat grid**: Day/Week/Month as three flush filled tiles, intensity normalised **per column** on that column's own p90, plus P/C and expandable industries. Sortable; RRG dropped. Reader of `cache:sentiment:sectors`. [Detail](../webgui-routes.md) | built |
| `/sentiment/rotation` | Sector Rotation — verdict strip (regime · **diverging spread gauge** on a −3…+3 scale with both ±threshold triggers · the spread and how far past its trigger it sits), a **weight-proportional flow band**, and four quadrant panels. Quadrant map table + rotating-from/into lists retired. Cached, manual Refresh only. [Detail](../webgui-routes.md) | built |
| `/sentiment/rrg` | RRG — **hand-drawn** relative-rotation plot (markers over an SVG trail layer, quadrant washes, fixed crosshair); **marker AREA = S&P weight**, trail = the **last 5 readings** resampled along a Catmull-Rom spline and labelled with the **sector name**. Domain is computed + symmetric about 100. Cached, manual Refresh only. [Detail](../webgui-routes.md) | built |
| `/sentiment/momentum` | Momentum — a **numbered argument** (regime trio + dispersion · three levels + alignment · quadrant counts · one decomposed example · rank over recent sessions), with the ranked leaderboard behind a **collapsed expander**. Scatter + ribbon dropped. Recomputed **once nightly** (16:20 CT), not on the tick. [Detail](../webgui-routes.md) | built |
| `/trade` · `/trade/evidence` · `/trade/board` · `/trade/plan` | Trade Analyzer — **four Signal Desk screens over ONE shared frame** (`pages/trade_shell.py`): **Overview** (the on-demand **Short Term** 1–8wk + **Long Term** months+ verdicts — Short Term runs the backtested IC-weighted factor model), **Evidence**, **Rank Board** (the universe-wide board, its own `trade:rank_board` view) and **Trade Plan**. Deep Dive and AI Query open separate reports. ⚠ The card names are **Short Term / Long Term**; "Position" and "Investor" survive only as ENGINE KEYS, and `test_trade_recommendation.py` fails on either as prose. ⚠ `pages/trade.py` is a **library, not a page** — its `render()` was deleted 2026-09-20 because no route ever reached it; the four screens above are what `main.py` routes. [Detail](../webgui-routes.md) | built |
| `/settings` | Settings — three sub-tabs. **General**: alert/ticker preferences, Schwab + Claude API call counts, and maintenance actions. **Appearance** (2026-09-19): every colour and font in eight groups that follow the design standard rather than the TOML's sections, over a live preview, saved as a `config/local/theme.toml` override. **Configuration** (2026-09-19): every `config/*.toml` setting by purpose, from the `webgui/config_schema.py` catalogue, saved as `config/local/` overrides, with a restart offer. [Detail](../webgui-routes.md) | built |
| `/portfolio` | Portfolio — Holdings / Sectors / Performance over the portfolio model, with live-streaming P&L via the service’s SSE consumer. | built |
| `/x` | **Post to X** (More tab, private only) — compose an ad-hoc marketing post (text, link, hashtags, optional image) with a live 280 count, confirm, and enqueue `x_post` on `cmd:options`; below it, the log of EVERY X post (reports, hourly trade ideas, ad-hoc) from `cache:options:x_log`, with why any was refused. The page never talks to X. [Design](../plans/2026-09-22-x-posting-design.md) | built |
| `/eod` · `/eod/detail` | EOD Report — Summary + Detailed aggregator over the `options:*` caches; Generate archives standalone HTML under `webgui/data/eod/<date>/`. ⚠ It **confirms, and refuses a cold cache** (2026-09-20): `write_archive` overwrites per DATE and every builder degrades to an empty note, so an unchecked click while the stack is stopped replaced the day's real report with a complete-looking empty one — `has_data` gates the button as it already gated `tools/generate_eod_report.py`. [Detail](../webgui-routes.md) | built |
| `/market` | Market Dashboard — live grid of ~48 macro tickers in framed category panels, coloured by semantic risk-on/off. Reader of `cache:market:dashboard`. [Detail](../webgui-routes.md) | built |
| `/status` | System Status — health board probing Redis / proxy / Schwab auth / the six services / webgui / **`webgui_live`** (a `peer` card: an HTTP liveness probe on the public screens, deliberately OUT of the 2 s health fan-out, so a dead public origin never badges the rail or chimes), plus cache freshness; per-component Restart via `systemctl --user`, **confirm-gated since 2026-09-20** — nine of the eleven cards carry one, including this web app and the proxy, and the dialog names what THAT restart costs. ⚠ The Redis card is READ-ONLY in every environment: it is a system unit a user-scoped systemctl cannot reach, and one server serves both environments. | built |
| `/terminate` | Stop All Services — confirm-gated `systemctl --user --no-block stop trading-<env>.target`. ⚠ Since 2026-09-07 that stops **both** web apps, so the public live screens go dark too. Redis survives structurally: it is a system unit the user target cannot reach. | built |

The `pages/options/` subpackage shares `detail.py` (collapsible Trade detail panel, reused by all signal
tables), **`flow_panels.py`** (PURE builders for the two Options Flow console
panels on the Gamma page's **Flow** + **Net Prem** subtabs — see the dedicated
section below), `svg.py` (gradient-bar / range-marker SVG — the composite-score
bar the Trade detail panel draws), `inputs.py`
(`select_all_on_focus` + `should_load` symbol-input helpers — `should_load` dedups
the symbol tab-out/Enter Load trigger), **`overlay.py`** (the shared full-screen
**wait overlay** — `build_loading_overlay()` → a handle with `.show(msg)`/`.hide()`,
plus a shared `LOAD_TIMEOUT_SEC` backstop; both the Calculator + Simulator show it
centered while a symbol Loads/Fetches), **`strategies.py`** (PURE shared
strategy/leg model — the normalized leg dict + `STRATEGY_TEMPLATES`/`STRATEGY_GROUPS`
+ `build_default_legs` + analytic-vs-numeric `summary_code` + `strategy_tags`/
`strategy_blurb`; imported by **both** the
Calculator and Simulator so templates never drift), **`leg_editor.py`** (the shared
**editable multi-leg widget** every leg-building page mounts — `state['legs']` is the
source of truth, each page injects its own `strikes_for`/`expiries_for` +
`show_premium`; `apply_expiry(expiry)` propagates the Calculator's top-level Expiry to
**all** legs. **Layouts over that one model:** `layout="table"` — the Calculator +
Simulator since 2026-09-12 — one row per leg (SIDE/TYPE toggles, a strike dropdown on
the real ladder stepped by ‹ ›, a Bid / Mark / Ask dropdown, and `price_for(leg,
source)` re-filling a leg's price when it becomes a different contract, never over a
typed price — the private `_manual_premium` and `_price_source` keys, which
`normalize_legs` strips); its GEOMETRY is shared while its palette enters as
`tokens` (Calculator `[calc]` near-black, Simulator app-navy); `delta_for` /
`show_premium` each COLLAPSE their track, `min_legs` floors the remove button at 1, and
the handle's `place_pick` (a grid click) MOVES the leg on the same side and type
and adds one only when none matches, editing in place rather than round-tripping the
other legs through `set_legs` (which would drop their private keys). ⚠ The strike
dropdown is deliberately NOT `with_input`: Quasar gives the filter box a 50px
min-width in a layer page CSS cannot beat, and it slid under the ‹ button.
`layout="row"`, the original single-line table, is mounted only by **Rescue**; the
two-line `card` layout was removed 2026-09-12), **`entry_panel.py`** (the shared entry panel: ticker,
spot, strategy, expiry strip, the chain grid, and the `legs_box` the page mounts its
editor into; the page decides what a grid pick means via `on_pick`. ⚠ The grid is the
COMPLETE chain as ONE `ui.html` block with a delegated click read from `data-*`
attributes — never a widget per cell, which an index chain would turn into thousands
of components; the DOMPurify allow-list and its `ALLOW_DATA_ATTR` default are pinned
by test. ⚠ The strip lists EVERY expiration (`expirations` from Schwab
`/expirationchain`) while the chain holds only the ones fetched so far — a lazy
`calc_load` / `sim_fetch` brings the nearest two plus any a leg needs, and
`calc_load_expiry` / `sim_fetch_expiry` merge one more per click. Never go back to one
fixed-window fetch: `$SPX`'s 60 days does not fit one proxy request, and neither does a
LARGE whole chain — SPY's timed out at the proxy's 30 s (NVDA's came back in 3.8 s) —
which is why every `swing_scan` fetches through `compute.fetch_scan_chain`, runs of ≤ 8
consecutive listed expiries 4 at a time, counting a failed run's EXPIRIES in
`expiries_failed` rather than hiding them (the Finder's scan counts its range on
Schwab's `daysToExpiration` — the chain keys' own DTE, a day off the host calendar
between 23:00 and 24:00 CT — and asks which expirations to load when more than 30 expirations are in range); with no expiration list it falls back to
one fetch, bounded to 120 days (`_FALLBACK_MAX_DTE`) when the scan has no DTE max), **`chain_grid.py`**
(PURE — the chain readers `extract_premium`/`extract_delta`/`leg_delta`/
`chain_expiries`/`chain_strikes`, moved out of `calculator.py` and re-exported there,
plus `chain_grid_rows`/`cell_text`/`parse_columns`), **`entry.py`** (PURE —
`step_strike`, `leg_from_pick`, `should_refill`, `Debounce`, `expiry_options`),
and **`handoff.py`** (cross-page
signal hand-off — Scanner/Swing "Send to Calculator" via a module-level `_pending`
stash + "Send to Paper trade" which enqueues a `paper_create` command on
`cmd:options`, plus the shared `add_row_actions` per-row action-button slot, **and the
Calculator legs stash** (`send_to_calculator_legs`/`take_pending_calculator_legs`, used
by multi-leg Send to Calculator — the Simulator↔Calculator copy stashes were removed
2026-09-12 when the two pages began sharing ONE position via
**`shared_position.py`**: each publishes from its `_capture`, seeds from it on render,
publishes `pending_legs` rather than a placeholder template while a chain loads, and
the Simulator carries share legs through untouched), **and the Flow-Alerts→Dealer-
Positioning symbol stash** (`send_to_gamma`/`take_pending_gamma`, one-shot — a symbol left
in the stash would silently re-hijack the gamma dropdown on the page's next build); engine-free),
**`strategy_menu.py`** (the shared cascading **Strategy picker** — a
`ui.select`-compatible button → nested family→variant Quasar submenu driven by
`strategies.STRATEGY_MENU`/`strategy_label`; both pages mount it so the picker
never drifts; `boxed=True` styles the trigger for the navy theme), and
**`theme.py`** (the shared dark-navy **"dashboard" theme** — now a vocabulary of
**Tailwind design-token constants** (`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/
`BTN_PRIMARY`/`STRATEGY_BTN`/`TXT_*`/`TILE_3D`) applied via `.classes(CARD)`, plus the
single **`APP_FIELD_CSS`** block — `build_quasar_css(THEME, scope=".ns-app")`, injected
**app-wide by both entrypoints** — for the Quasar-internal DOM no `.classes()` can
reach: filled navy input boxes, compact `.leg-*` cells, dark transparent tabs and the
teleported `strat-menu-navy` popup. ⚠ **`build_quasar_css`'s `scope` is a REQUIRED
argument** (2026-09-20): it used to default to `.calc-v2`, and with that class gone a
forgotten argument would emit a whole block scoped to an element the app never builds
— a failure with no symptom at all. The legacy `DASHBOARD_CSS` string was **deleted in
the Tailwind-first migration**, and the second copy of this block —
`QUASAR_INTERNAL_CSS` under `.calc-v2` — went with `pages/trade.py`'s unrouted
`render` on 2026-09-20 — see the "App theme — dark-navy" canonical section below), and
**`page_state.py`** (the shared PURE persistence helpers — `snapshot` /
`merge_restore` / `pick_seed` — both pages use to restore their full UI state across
navigation via a single-user module snapshot; see the route table). Options design + plan: [`docs/plans/2026-06-14-options-section-expansion-design.md`](../plans/2026-06-14-options-section-expansion-design.md)
/ [`-plan.md`](../plans/2026-06-14-options-section-expansion-plan.md).
Gamma/Simulator: [`docs/plans/2026-06-14-gamma-simulator-design.md`](../plans/2026-06-14-gamma-simulator-design.md) / [`-plan.md`](../plans/2026-06-14-gamma-simulator-plan.md).

**The four Trend & Sentiment screens rebuilt 2026-08-17 are ONE design family,
and their arithmetic lives in four PURE sibling modules** — `sector_heat.py`
(`/sentiment/sectors`), `rotation_view.py` (`/sentiment/rotation`), `rrg_view.py`
(`/sentiment/rrg`) and `momentum_view.py` (`/sentiment/momentum`), over the
shared `oklch.py` (oklch→sRGB, since all four designs were authored in oklch and
all four sit at the dark end where an sRGB interpolation bunches). **The page
modules hold widgets and wiring only**, which is why each screen's geometry —
heat levels, gauge positions, marker sizes, spline resampling, rank projection —
is unit-tested without a browser. **`rotation_view` is the palette root**: it
owns the four quadrant hues and the warm-neutral lightness ladder, and
`rrg_view` + `momentum_view` import them rather than restating, so the three
rotation-flavoured screens cannot drift. Designs: the four
`docs/plans/2026-08-17-*` docs. ⚠ Two of these pages ship a **hand-drawn SVG**
layer via `ui.html` (`rrg_view.tail_svg`, `momentum_view.rank_svg`) — read the
`vector-effect` gotcha above before touching either.

**App theme — dark-navy "dashboard" (Tailwind-first; the canonical reference).**
The shared dark-navy look (**app-wide since 2026-09-19** — both entrypoints put
`ns-app` on the content column and inject `SURFACE_CSS` + `APP_FIELD_CSS`, so a page
needs **no scope class of its own at all**) is now a set of **Tailwind design-token
constants** in **`webgui/pages/options/theme.py`** applied via `.classes(CARD)` etc.,
plus the one **`APP_FIELD_CSS`** block for the Quasar/Highcharts-internal DOM that
component `.classes()` can't reach (`q-field__control`, the `leg-*` cells, the `q-tab*`
chrome, the teleported `.strat-menu-navy` popup). ⚠ **There is no page-scoped copy of
that block any more.** `QUASAR_INTERNAL_CSS` was the same rules under `.calc-v2`,
injected a SECOND time by the three pages that wore that class — measured on 2026-09-20,
`APP_FIELD_CSS.replace(".calc-v2", ".ns-app")` matched it exactly — and both went with
`pages/trade.py`'s unrouted `render`, the last element in the app carrying the class.
**`DASHBOARD_CSS` is deleted** (Phase 4) and `theme.py` is now tokens +
`APP_FIELD_CSS` / `SURFACE_CSS` / `TYPOGRAPHY_CSS` / `NAV_THEME_CSS`. **This section +
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
  secondary, primary and danger buttons, the semantic
  positive/warning/negative/neutral set, the Sentiment/Rotation
  chart palette (`sentiment.py CLR_*`),
  plus **`[typography]`** (app-wide font family + text-category sizes:
  titles/.text-h6 · subtitles/.text-subtitle1 · sections/.text-subtitle2 · body ·
  small/.text-xs+EYEBROW → `build_typography_css`, injected app-wide by
  `main._layout`) and **`[menu]`** (the application menu: `accent` → `ui.colors(
  primary=…)`, which reaches the Quasar-colored controls — switches, sliders,
  `color=primary` buttons — **NOT** the header bar (decoupled via `header_bg`) and,
  since 2026-09-19, the active nav pill, tab-strip fill and active icon
  (`build_nav_css` re-emits them in the accent; the two washes are a `color-mix`, so
  ANY CSS colour works, not only a 6-digit hex); `drawer_bg`/`text`/
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
  rgba wash**, which stays the stock look; `build_nav_css` re-emits it in the
  accent as raw CSS, which the JIT limit does not bind. **`rgba(...)` was
  wrongly caught by that ban until 2026-08-14** — this line read "`var(...)`
  **or `rgba(...)`**", which is overstated and cost a real workaround: probed
  live while building the Signals tiles, `shadow-[0_0_18px_-6px_rgba(…)]`
  generates fine (the Refresh button's shadow is a live example), as do
  `bg-gradient-to-b from-[#hex] to-[#hex]`, `[text-shadow:0_0_12px_#hex]` and
  `drop-shadow-[…]`. The limitation is **`var(...)`**, not parenthesized
  functions generally. Note `rgba()` must be written with **no spaces** (a
  Tailwind arbitrary value cannot contain them, and underscores are the escape),
  and a `box-shadow` arbitrary needs the **rgba form, not a hex**.
- **Apply to a new page — use the kit (2026-09-19): `webgui/pages/ui_kit.py`.**
  ```python
  from pages import ui_kit as kit
  with kit.page():
      head = kit.header("Title", view="options:matrix")   # title · Updated stamp · actions
      with head.actions:
          kit.button("Refresh", kind="secondary", icon="refresh", on_click=_refresh)
      with kit.control_bar():
          sym = kit.symbol_field(value="SPY", on_load=_load)
          kit.button("Load", kind="primary", icon="search", on_click=_load)
      region = kit.region("Loading…")
      with region.content:
          table = kit.table(columns, rows, numeric=("pnl",))
  ```
  Fields and tabs are boxed app-wide: both entrypoints put `ns-app` on the content
  column and inject `theme.APP_FIELD_CSS` + `theme.SURFACE_CSS`, so a page adds no
  scope class. `tests/test_ui_kit_guard.py` fails when a page builds a raw
  `ui.button` / `ui.dialog` / `ui.notify` / `ui.table` or loads its own font; its
  `ALLOWED` ratchet is **exactly TEN written exceptions** since 2026-09-20 —
  **every page in the app is migrated**, so an entry there is a control that is not
  an action button (a segmented picker, a stepper, a selected-state toggle), never a
  page waiting its turn. The standard is
  [the design doc](../plans/2026-09-19-app-ui-consistency-design.md).
  Reactive (repainted-in-place) label colors swap via `.classes(remove=<finite set>, add=…)`
  so repeated repaints don't stack conflicting `text-[…]` classes.
- **Token vocabulary** (`.classes(<TOKEN>)`, all in `theme.py`): `PAGE` navy radial-gradient
  page wrap · `CARD` bordered navy panel · `EYEBROW` small muted label · `LABEL` / `MUTED`
  text · `BTN` / `BTN_PRIMARY` secondary / primary button · `STRATEGY_BTN` boxed Strategy
  trigger box (applied alongside the `strategy-menu-btn` scope hook via
  `strategy_menu.build_strategy_menu(..., boxed=True)`) · `TXT_POS/TXT_WARN/TXT_NEG/TXT_NEUTRAL`
  semantic state text colors (+ `STATE_TEXT_CLASSES` for the reactive `remove=`) · `BTN_QUIET`
  text-only button · `TILE_3D` metric tile (a 12px radius and a hairline — flat since
  Deep Slate, so the "3D" in the name is legacy). ⚠ `BTN_3D` / `BTN_3D_DANGER`, the
  aliases of `BTN_PRIMARY` / `BTN_DANGER` kept so the flattening needed no per-site
  edit, were **deleted 2026-09-20** once no page used them. **CSS-only hooks** (all in `APP_FIELD_CSS`, scoped under
  `.ns-app` except the popup — both entrypoints put that class on the content column, so a
  page adds no scope of its own): `.strat-menu-navy` the teleported Strategy-menu popup
  (**GLOBAL** — Quasar menus mount on `<body>`, outside the scope) · `.leg-head` /
  `.leg-row` / `.leg-strike` leg-table cells
  (`leg_editor.build_leg_editor(..., header=True)`).
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
The phase-by-phase (P0–P8) log is in [docs/CHANGELOG.md](../CHANGELOG.md); the rationale is in
[`docs/plans/2026-06-28-tailwind-first-ui-migration-design.md`](../plans/2026-06-28-tailwind-first-ui-migration-design.md).
The only inline styling that
remains is the **documented out-of-scope set**: Highcharts option dicts (chart config), raw
`ui.html()` HTML-string fragments + their CSS (`EOD_CSS` / the Gamma Analyze infographic /
the EOD export docs), and Quasar `color=` props. The escape hatch is **Quasar-internal**
`ui.add_css` — `theme.APP_FIELD_CSS` (field/tab/menu internals, injected ONCE app-wide by
both entrypoints, not per page), `_NAV_CSS`, and the handful of page blocks that style a
widget the page itself mounts (`SCAN_CSS`, `FINDER_CSS`, `MACRO_CSS`, `_TICKER_CSS`,
`_BULLBEAR_CSS`, `EOD_CSS` and the two keyframe blocks).
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
