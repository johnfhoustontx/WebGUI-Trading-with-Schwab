# CLAUDE.md — WebGUI Trading with Schwab

Guidance for Claude Code sessions working in this repository. Read this first,
then the per-app `CLAUDE.md` for the folder you are editing.

> **Maintenance:** This document is the living architecture/tech record for the
> project and is **updated regularly** as the build progresses (an explicit
> standing requirement). After any structural change — new page, new dependency,
> port change, copied/removed module — update the relevant section here.

**Last updated:** 2026-07-20 (**Options Matrix Display tab** (`/options/matrix`, a **main-menu (left-rail)
item directly under the Options group** — its OWN standalone page via `main.OPTIONS_RAIL` [rendered by
`_nav_link` right after the Options group entry], NOT an Options tab-strip entry; moved out of
`OPTIONS_CHILDREN` per the user's request so it's a top-level menu item, not a subtab) — an at-a-glance
**sortable grid of EVERY watchlist stock** (the
~45-symbol `gex_collector.collection_symbols()` universe minus `$VIX`), one row per name, to **spot
opportunities** fast. Columns: Ticker · Spot · Day % · **Intraday trend** (▲/▬/▼ from the day's spot slope)
· **Call/Put flow acceleration** (recent-slope-vs-day-average of cumulative call/put premium — "is
call/put-buying heating up") · P/C ratio · Net premium ($M) · **GEX regime** (spot vs stored gamma flip) ·
**# Signals** · **# Flow alerts** (today) · a **Buy/Neutral/Sell** options-flow composite · a **Hotness**
sort key (default sort, hottest float up). **Architecture — a new aggregator in `options_svc`, page is a
pure Tier-1 reader.** Pure derivation `services/options_svc/matrix.py` (`intraday_trend`/`flow_acceleration`/
`composite_signal`/`pc_ratio`/`net_premium_m`/`gex_regime`/`hotness`/`build_rows` — no I/O, all thresholds
named constants, per-symbol-guarded so one null-premium symbol can't zero the grid). `compute.build_matrix`
(DB-only orchestration: per symbol reads the intraday flow series via `gex_history_db.load_flow_series` +
the latest gamma flip via a NEW cheap **`gex_history_db.latest_flip`** [selects ONLY the `flip` column — no
whole-session grid decode, avoiding the documented hotspot], counts signals from `cache:options:scan_day` +
flow-alerts from the UNCAPPED `cache:options:flow_alert_cooldowns` seen-map [each cid `{SYM}|...` is one
distinct daily event — the true per-symbol count, NOT the 50-capped `cache:options:flow_alerts` rolling list
that undercounts once the day fires >50 alerts; caught by the user + fixed 2026-07-21] grouped by `symbol`)
→ publishes **`cache:options:matrix`**
(`MatrixSnapshot` contract) from **`handlers.collect_gex_history`** on the existing **1-min GEX branch** (no
`scheduler.py`/`app.py` edit), plus a **~30 s live spot/day% overlay** (`compute.apply_live_spots` +
`handlers.refresh_matrix_spots`, one batched `get_quotes`) on the `refresh_header` tick so Spot/Day% feel
live. The webgui **`webgui/pages/options/matrix.py`** page is engine-free — version-polls `cache:options:matrix`
every ~2 s and repaints a sortable `ui.table` in place (Tailwind-first, colored Buy/Sell + trend + regime
cells; default order = server-sorted hotness-desc). **Counts + flow gate on `session_date`, not `today`**, so
off-hours the grid shows the last session's flow with ITS own counts. **A date-type bug was caught by LIVE
verification and fixed**: the count gate compared the string `scan_day["date"]` against the **`datetime.date`**
returned by `active_session_date()` → all-zero counts on every row; fixed by normalizing `session_date` to an
isoformat string for the gate + payload field while keeping the date object for the DB reads (a regression
test now passes a real `datetime.date`). **Live-verified end-to-end** against the running stack (45 rows,
counts populated — $NDX 135 sig → hotness 275, AMD 7 flow alerts — sorted hotness-desc, colored badges, the
live-spot overlay updating spots off-window; no console errors). **Restart `options_svc` + the webgui.** TDD
per layer (implementer + spec + code-quality review each): contracts 42, matrix pure **21**, options_svc
**677**/2 baseline, gex_history_db **40**, webgui **861**; ruff clean. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-20-options-matrix-display-design.md) /
[plan](docs/plans/2026-07-20-options-matrix-display-plan.md). Prior — 2026-07-20 (**X/Twitter public-post notification channel + Swing Scanner liquidity
fixes** — two pieces. **(1) Twitter channel.** A fourth notification channel
(`services/options_svc/push_notify.py`) posts new SCANNER (0-DTE + swing) signals to a public
X/Twitter account, riding the SAME `notify_signals` fan-out as Telegram/Discord/Fi-SMS.
`twitter_signal_text` builds a **≤280-char** tweet — compact signal body + a CONFIG-DRIVEN static
footer (hashtags · Discord invite link · extra promo text · disclaimer); budget-defended so the
footer always survives and only the body truncates. `send_twitter` is a **tweepy v2 / OAuth 1.0a**
sender (lazy import; best-effort — a duplicate-content **187**, rate-limit **429**, or network error
is caught, never raised into the scan/publish path). `notify_twitter` is a **scanner-only** fan-out
with its OWN gates, independent of the private channels: a PUBLIC `min_score` (only your stronger
signals go public while weaker ones still push privately) + a **persisted per-day `daily_cap`**
(quota guard vs. the free-tier monthly write cap + spam-flagging), `dry_run` = format+log without
posting. Wired into `notify_signals` (kind=="scanner" only), guarded so a Twitter failure can NEVER
break the Telegram/Discord/SMS sends. **The signal GRADE** (Strong/Good/Marginal/Weak from the flat
scanner's composite ≥80/≥60/≥40/else) is now shown on the tweet AND on `telegram_signal_text` +
`discord_signal_embed` (new Grade field) — all three tolerant of a missing grade. Config: a `twitter`
block in `shared/notifications.json` (+ `.example.json`) — `enabled`/`dry_run`/`min_score`/`daily_cap`
+ 4 OAuth keys + `hashtags`/`discord_url`/`extra_text`/`disclaimer` — with `TWITTER_*` env overrides.
**Ships OFF** (`enabled:false` + `dry_run:true`) → inert until real keys are added AND both flags
flipped; **nothing publishes by default** (X account creation + the go-live flip are the user's — a
public-publish action). New dep **`tweepy>=4.14`** (installed 4.17.0, pinned). TDD; push_notify **73**
+ shared/notify **14** green, options_svc 629/2 (the documented pre-existing `test_expected_move`
baseline), ruff clean; verified end-to-end in dry-run (strong signals formatted w/ grade+footer, weak
one gated out of the public feed, nothing posted). **(2) Swing Scanner liquidity fixes**
(`options-scanner/strategy_scoring.py`) — the Swing Scanner (`/options/swing`) graded EVERY candidate
on non-index symbols (AAPL/MSFT/IWM) **Weak** with "Fails: liquidity" + `q_liq=0.0`. Root cause:
`q_liq` delegated to the FLAT scanner's `scoring.norm_liquidity` — a percent-of-mark band (hard 0 at
≥5%) calibrated on index options (`$SPX`/SPY/QQQ, penny-wide on high marks) and DESIGNED as a soft
5/100 ranking factor, but the Swing Scanner promoted it to a HARD gate (a 0 caps composite at 39 →
Weak). Fixed with a LOCAL, tick-aware `norm_liquidity_ticks` scoring the spread on percent-of-mark
**or** quoting-ticks, whichever is more forgiving (mirrors the flat scanner's own
`passes_liquidity_gate` hybrid). A SECOND fix: `q_liq` averaged a neutral-50 placeholder for unquoted
legs into the real measurement, compressing every credit structure into [25,75] and making Strong
unreachable for the CREDIT/NEUTRAL families — now unquoted legs are SKIPPED. **`scoring.norm_liquidity`
is deliberately UNTOUCHED** (it feeds the flat scanner's `calc_composite_score`, which the driver
sizes paper trades from — keeping the recalibration local confines the blast radius). Live-verified
(IWM/AAPL/MSFT all-Weak → Good/Marginal; SPY/QQQ unchanged; genuinely-wide ZM still Weak). Green:
strategy_scoring/scanner/scoring **125**, options_svc **629/2** baseline, ruff clean. Commits
`be94c7a` (tick-aware) / `80617aa` (leg-dilution) / `800adf7` (Twitter). Branch `Using_Highcharts`.
Prior — 2026-07-18 (**Flow alerts → contract-level Unusual Options Activity (strike/cost/
expiry/premium)** — a follow-up to the 2026-07-17 flow alerts: the **"unusual activity"** alert is now a
per-contract **vol/OI** detector that NAMES the specific option and carries the fields the user asked for.
The aggregate per-minute volume-spike (`detect_spike`, rolling baseline) is **RETIRED**; the new pure
**`flow_alerts.detect_uoa(symbol, chain, cfg)`** walks the live chain (all strikes/expiries, calls+puts),
qualifies a contract when **volume/openInterest ≥ K** (default 3×) **AND** volume ≥ `vol_floor` (500)
**AND** premium (`mark·vol·100`) ≥ `premium_floor` ($250k — real money only), **skips `oi ≤ 0`**, and
returns the **top-N by premium per symbol** (default 3; 0-DTE stays in — the premium floor + cap tame it).
Each alert reads e.g. **`SPY 07/18 450C — UNUSUAL: 8,200 vol vs 1,300 OI (6.3×) · $1.85 · $1.52M
premium`** (Strike + C/P · Expiry [MM/DD, `0DTE` tag] · Cost [mark] · Premium [$, humanized] · vol/OI).
**No re-fetch:** UOA is computed inside the 1-min GEX poll's existing **`on_chain`** hook
(`compute.collect_gex_snapshots` → `stash_uoa` → consume-once `_UOA_STASH`), so it reuses the chain the
poll already fetched; `handlers.run_flow_alerts` drains the stash and emits contract alerts **once per
contract per day** (the cid `{sym}|uoa|{side}|{strike}|{expiry}` doubles as a date-scoped seen-set — vol/OI
is monotonic, so a contract crosses K once and pings once). UOA shares the **crossover's `$VIX`-excluded
universe** and is gated by the same `enabled` kill-switch (skipped in `on_chain` when off). The
**crossover** alert is unchanged but now shows the explicit premiums (`$SPX — call premium overtook puts:
$2.10M calls vs $1.95M puts (bullish flip)`). Delivery (Discord/Telegram push + webgui toast+chime) is
unchanged — the richer `text` flows through both; `push_notify._flow_is_bullish` + the webgui `_tick`
bullish check were repointed `spike`→`uoa` so a UOA **call** renders GREEN. **`config/flow_alerts.toml`**
`[spike]`→`[uoa]` (`k`/`vol_floor`/`premium_floor`/`top_n`). **The Windows desktop-notification "not
working" report was diagnosed as NOT a code bug** — the flow branch calls the same `notify_desktop` as the
working scanner alerts; it needs `desktop_notifications` ON + browser permission granted + Windows allowing
browser notifications + an alert actually firing (08:00–15:20 CT). **Restart `options_svc`.** TDD per layer
(spec + quality review each); options_svc flow suites green. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-18-flow-uoa-contract-detail-design.md) /
[plan](docs/plans/2026-07-18-flow-uoa-contract-detail-plan.md). Prior — 2026-07-19 (**efficiency re-audit → Medium + Low tier remediated**:
the follow-up batch to the 2026-07-18 Critical/High fixes — TDD per item, all suites green
(webgui **854**, options_svc **596** [2 pre-existing date-relative `test_expected_move`],
sentiment_svc **144**, driver_svc **211**, market_svc **49**, proxy **98**, bus **24**).
Flow alerts read only the trailing ~22 rows + normalize once + drop `$VIX` + mtime-cache the
TOML + `skip_unchanged` the cooldown; `gex_json` grids **zlib-compressed at insert** (~5×) +
redundant `idx_snap_today` dropped + `init_schema` once/process; **term chain polls 5-min**;
rescue advisories use a **light GEX-only context** (no full `gamma_snapshot`);
`reprice_captured` clears the chain cache first (freshness); proxy stats counter **WAL** +
`_rate_limit` locked + reconcile log demoted; market_svc summary → **background task** +
weekend throttle + version-gated pcr; sentiment push **outside `_TREND_LOCK`** +
`sector_pc_delta` conn closed; driver reads the composite **once/cycle**; webgui ticker/market/
driver reads moved **off the event loop** + driver version-probes pipelined + scanner rows built
off-loop + page-build `options:scan` read once. Gamma `_render_view` left on-loop (crop already
tamed it — poor risk/reward). See "Performance characteristics & known hotspots". Branch
`Using_Highcharts`. Prior — 2026-07-18 (**efficiency re-audit → all Critical + High fixes**: a
four-agent audit of the grown app found — and this session fixed, TDD per layer —
(1) **~37% of 1-min GEX slots silently dropped** (serial 24-chain fetch + the
scheduler gathering all branches before sleeping) → `poll_once` now fetches chains
in a pool + `scheduler.launch_branches` fires keyed non-blocking background tasks
with a still-running skip (also un-distorts the flow-alert spike baselines);
(2) `gamma_snapshot`'s **whole-session grid re-decode every minute** → incremental
per-(symbol,view,date) memo + `load_date_with_grid(since_ts=…)`; (3) the same
tick's **double chain fetch** for the viewed symbol → `poll_once(on_chain=…)` +
a consume-once tick-chain stash; (4) webgui watcher regressions — `read_metas`
(pipelined `:ver`+`:ts` probes; `cache_set` now writes a `{key}:ts` side key) +
the TTL-bypassing health re-warm; (5) sentiment's **~4,400 Schwab calls/day** —
sector P/C TTL-cached 15 min + `compute_30d_trend` self-fetch cached hourly. See
"Performance characteristics & known hotspots" for the full record + the still-open
Medium items. Branch `Using_Highcharts`. Prior — 2026-07-17 (**Options-flow alerts — put/call premium crossover + unusual activity**:
new **in-app popup (toast + chime) + Discord/Telegram** alerts on two events, detected server-side in
`options_svc` riding the **1-min GEX poll** over the **whole collected universe** (~24 symbols). **(1)
Crossover** — a symbol's daily-cumulative call **premium ($)** crosses its put premium (money-weighted
sentiment flip; `detect_crossover` fires on a net-sign flip that clears a hysteresis **band** [2% of the
larger side] AND a **`min_premium`** floor [$10k] so tiny open-session premiums don't chatter). **(2)
Unusual activity** — a per-minute **volume** increment (this snapshot − last) spikes to **≥ K× the
symbol's own trailing average** (`detect_spike`, K=4 over a 20-min window) AND clears an absolute
**`floor`** (500 contracts); the relative test ALWAYS applies via `k × max(baseline, min_baseline)` so a
dead-quiet name can't fire on the floor alone, plus a **warm-up** (`min_points`) for the first minutes.
Data is **unsigned/cumulative** (Schwab has no tape) so alerts say "unusual activity", never "buying".
**Architecture** (mirrors the existing signal/action pushes): pure detectors in
`services/options_svc/flow_alerts.py`; a `handlers.run_flow_alerts(bus)` (wired into `collect_gex_history`
after `publish_flow_skew`, best-effort/guarded — a flow-alert failure can NEVER break GEX collection)
iterates the universe on **one reused read-only `gex_history_db` connection**, reads each symbol's day
flow series (`load_flow_series`), detects with a **date-scoped Redis cooldown map**
(`cache:options:flow_alert_cooldowns`, keys `{sym}|crossover` / `{sym}|spike|{side}`, 30/20-min cooldowns
so a fired signal pings ONCE), pushes each fresh alert via `push_notify.send_flow_alert` (Telegram HTML +
Discord embed, **green = bullish** [calls overtook / call surge] / **red = bearish**), and appends
(deduped by `id`, capped 50, date-scoped) to **`cache:options:flow_alerts`**. The webgui's existing 2-s
watcher (`main.py` `_watcher_compute`/`_tick`) reads that key, diffs new alert `id`s vs
`_ALERT_STATE["flow_acked"]` (seeded on the first tick so a page load doesn't replay the day's backlog),
and fires `play_alert` + a colored `ui.notify` toast (+ optional desktop notification) — reusing the
alert-sound/volume/desktop settings, gated by a new **Settings → "Flow alerts"** toggle
(`app_settings.flow_alerts_enabled`). **Three independent gates:** `flow_alerts.toml enabled`
(whole-feature server kill-switch → nothing published → webgui silent), the notifications-config `enabled`
(phone push), and the webgui toggle (popup only). **Thresholds live in `config/flow_alerts.toml`** (K /
band / floor / min_baseline / min_premium / window / cooldowns — edit + restart to tune). Runs only in the
08:00–15:20 CT poll window (automatic). **Restart `options_svc` + the webgui.** Built
subagent-by-subagent, TDD per layer (spec + quality review each); options_svc **579** (+2 pre-existing
date-relative `test_expected_move` fails) + webgui **844** green. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-17-flow-crossover-unusual-activity-alerts-design.md) /
[plan](docs/plans/2026-07-17-flow-crossover-unusual-activity-alerts-plan.md). Prior — 2026-07-16 (**Scanner: directional trades + day-persistent signals**: the Options Scanner (`/`)
gained a third sub-tab — **Directional** — and its tables now hold **the whole day's signals**, not just the last
scan's. **(1) Directional.** `scanner_engine.run_full_scan` emits a NEW **`signals_directional`** list (single-leg
`LONG_CALL`/`LONG_PUT`/`SHORT_CALL`/`SHORT_PUT`), built per symbol per DTE window by **reusing**
`strategy_scanner.build_directional` + `strategy_scoring` (already proven on `/options/swing`) against chains the
scan already fetched. **Own tab + own scorer, deliberately:** options-scanner's `scoring.py` is a premium-seller's
model that *structurally cannot score a long call* (rewards positive theta, penalizes long vega, needs a short
strike), so directional is scored on **Fit+Quality** and its score **never sits beside a premium composite**.
`em_1sd` is computed **per window** (a single 1-day EM would under-score the swing side by ~17.5 composite points).
Naked shorts render **`Max L = ∞`** + an **undefined-risk** badge and **cannot be paper-traded**. The list is
invisible to the **autonomous driver by construction** (`build_packet` merges only `signals_0dte + signals_swing`;
the `{PCS,CCS,IC}` allowlist is the second layer) — **pinned by a synthetic probe** (an allowlist-PASSING PCS parked
in `signals_directional`), because the two defenses are REDUNDANT and a realistic test can't tell them apart.
**(2) Day persistence.** `rescan` now publishes a **SECOND key `cache:options:scan_day`** — a date-scoped union
`{date, signals_*, truncated?}` (pure `compute.merge_day_signals`, id-keyed): a still-qualifying signal takes the
**fresh** numbers, a dropped-out one is **carried forward frozen** (`live=False` + `stale_since`) and renders
**dimmed + "Dropped HH:MM"**. **`cache:options:scan` keeps live-only semantics verbatim** — the driver reads it and
must never be offered a signal that no longer qualifies. Date is **CT-pinned via `_today_ct()`** — NOT
`active_session_date()`, which flips at 08:30 while scans start at 08:00 and would wipe each morning's first two
scans. **Capped at 2000/list** (evict oldest-stale-first, **never evict a live signal**, log + a `truncated` block
the page surfaces): measured worst case was **~17 MB**, at the 16 MB `cache:options:gamma` payload that forced the
P2 crop. **The page gates its render on `payload["date"] == today_ct`** — the merge is best-effort and fails
**stale, not absent**, so yesterday's envelope still carries `live=True` rows. **(3) "New" reworked + a bug fixed.**
Now means **unseen since you last viewed the page** (acknowledged **only on `render()`'s initial paint**, never on a
version-poll repaint — otherwise a repaint while you're away acknowledges signals you never saw). **The old marker
was broken:** `_sig_key` rebuilt a key from `short_strike`/`long_strike` but was fed DISPLAY rows where
`signal_rows` merges both into one `strikes` cell, so every key collapsed to `SPY|PCS|None|None|07/17` and a new
signal at different strikes went unmarked. **Now keyed on the engine's unique `id`.** **(4) A live bug fixed on the
way in** (`/options/swing` had it too): `payoff_metrics` set `unbounded=True` for **both** an unbounded PROFIT (long
call) and an unbounded LOSS (naked short), so a short call rendered **`Max P = ∞`** while its genuinely unlimited
loss showed as a finite margin proxy — exactly inverted. Now emits `unbounded_profit`/`unbounded_loss`.
**Persistence created one new hazard, closed:** a dropped signal is frozen at an hours-old price and `paper_create`
records `signal['credit']` **verbatim, no re-pricing** — so the **Paper button is gated off on stale rows** (all
three tabs; Calculator/Expected-Move stay open — reviewing a dropped signal is the point, booking it is the
hazard). Table reads moved **off the event loop** (`run.io_bound`) + `rowsPerPage: 100` (was unbounded → up to
~6,000 DOM rows). **⚠ KNOWN — the Directional tab's ranking is dominated by a pre-existing scoring artifact
(`strategy_scoring`, affects `/options/swing` equally):** a long put's max profit is *bounded* at S=0 (underlying →
$0) so it always gets a finite R:R (measured up to **1404:1**), while a long call's is honestly **unbounded** →
`rr=None` → a PoP proxy. Live result: **LONG_PUT avg score 59.2 / best rank #1; LONG_CALL avg 45.2 / best rank
#14 — all top 12 were LONG_PUT**, i.e. *being honest about unlimited upside is penalized ~14 points*. The #1 signal
was an ATM IWM 295 put **19 minutes from expiry** graded "Good — passes all quality gates" on an **884:1** R:R that
only pays if IWM hits $0 today. **Not fixed** — it's a scoring-model decision, not a bug, and it changes Swing too.
**Restart `options_svc` + the webgui.** options-scanner **1269** / options_svc **564** (2 pre-existing
date-relative `test_expected_move` fails) / driver_svc **209** / contracts **40** / webgui **840** green; ruff
clean. **Live-verified end-to-end during RTH**: all 4 types built, day key accumulated across two scans with **21
0-DTE signals frozen at 14:42** as they stopped qualifying, driver menu provably free of directional. **Test
baselines in `options-scanner/CLAUDE.md` were badly stale (667/2 vs a real 1260/15) and were corrected.** Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-16-scanner-directional-day-persistence-design.md) /
[plan](docs/plans/2026-07-16-scanner-directional-day-persistence-plan.md). Prior — 2026-07-15 (**Nav drawer → icon rail that expands on hover**: the webgui's left drawer
(`webgui/main.py`) is now a **64px icon rail** that widens to **248px on hover** and **OVERLAYS** the page
instead of reflowing it. **Mechanism** (the part worth keeping): the drawer is LAID OUT at
`NAV_WIDTH_RAIL=64` via Quasar's `width` prop (`drawer_width(pinned)` → 64, or `NAV_WIDTH_OPEN=248` when
pinned); `_NAV_CSS` widens it on hover with `.q-drawer:has(> .nav-drawer:not(.nav-pinned)):hover, :focus-within
{ width: 248px !important }`. Because Quasar's LAYOUT still uses 64, `.q-page-container`'s padding never
changes → the expanded menu overlays content — **deliberate**: this app's Highcharts have no ResizeObserver,
so reflowing on every hover would leave charts mis-sized. No Quasar mini-mode, no JS, no hover round-trips.
The **hamburger pins/unpins** (persisted via `app_settings` `nav_pinned`, default False); pinned lays out at
248 (the page genuinely reflows — correct for an explicit choice) and `.nav-pinned` disables the hover rule.
**Icons render again** (a prior redesign had replaced each item's icon with a colored dot, leaving the `icon`
arg dead): the dot is retired and the icon is the affordance — it's the only thing visible when collapsed.
Two were re-curated — Market Trend & Sentiment `insights`→**`speed`** (the page is four speedometer gauges;
`insights` collided with Trade Analyzer's) and Trade Analyzer `analytics`→**`query_stats`** (the job is
"analyze one symbol", not "charts"); the other five kept (`candlestick_chart`/`dashboard`/`account_balance`/
`smart_toy`/`more_horiz`), guarded by a test asserting the 7 drawer icons stay non-empty + mutually distinct.
**Badges** moved from `ml-auto` onto each icon's top-right corner (Quasar `floating` on a `relative` wrapper)
so a collapsed rail still reports "3 new signals"; `_count_badge`/`_set_badge` DRY the drawer + tab-strip
construction and the 2s watcher's updates. **The two traps this exposed are recorded in the "NiceGUI gotchas"
section — read them before touching drawer CSS or measuring anything in the automation browser**:
(1) `.nav-drawer` is NOT the `<aside>` (the width lives on the parent `.q-drawer`; reach it via
`:has(> .nav-drawer)`), and (2) CSS transitions freeze at their START value in the backgrounded automation
browser, so `getComputedStyle` LIES until you kill transitions. Also: the active-icon accent must be
`.nav-drawer .nav-active .nav-icon` (0,3,0) `!important` to out-specify `theme.build_nav_css`'s
`[menu].text` rule — a `.nav-icon-active` class was tried and REMOVED (at (0,2,0) it tied and lost on source
order). webgui **772** green; ruff clean. Branch `Using_Highcharts`. Prior — 2026-07-14 (**Rescue coverage — Phase 1c: single-type condors & butterflies (ad-hoc)**: the
`/options/rescue` **ad-hoc** tab now builds advisory rescues for the **single-type range structures** —
`CONDOR_CALL`/`CONDOR_PUT` (long condor: long K1 / short K2 / short K3 / long K4) and `BUTTERFLY_CALL`/
`BUTTERFLY_PUT` (long 1-2-1 fly: long K1 / short 2×K2 / long K3). All defined-risk **DEBIT**, neutral/range
(`IC` + `IRON_BUTTERFLY` were already covered — the mapper folds an iron fly into `IC`; this is the all-call OR
all-put sibling family). **Advisory-only** (ad-hoc has no Apply). Now **12 of 19** structures; the rest still pop
"not available yet". Engine (`services/options_svc/rescue.py`, PURE): `assess_range_risk` (heat =
`min(50, loss_frac·60) + min(35, range_frac·35) + 15 if dte≤5 & range_frac>0.8`, where `range_frac =
|underlying − center| / half_width`, center = midpoint of the SHORT strikes, half_width = center → nearest LONG
wing) + `range_candidates` — commission-aware, `apply_kind="advisory"`: **close** (sell the structure → +cv·100·qty
credit, `new_max_loss=0`) + **roll_out** (+30d same strikes → a debit; skipped if any rolled leg is unpriceable).
The structure carries per-unit `legs`; **structure value `cv = +long −short`** (a long condor/fly you own is
POSITIVE, ~ the debit paid — same convention as the debit-vertical path; an early `+short −long` sign slip was
caught in review + pinned by a non-degenerate compute test). Compute (`compute.py`): `_advisory_from_range`/
`_adhoc_range` mirror the debit path — `_RANGE_STRATEGIES` route in `compute_rescue_adhoc`; every leg priced via
`_make_leg_pricer`, cv falls back to the entered debit off-hours (only `close` survives, as with singles/debit).
Page mapper (`webgui/pages/options/rescue.py`): `_range_spec_from_parsed` recognizes an all-one-type LONG range
structure by aggregating signed net qty per strike (4 strikes `[+q,−q,−q,+q]` → condor; 3 strikes `[+q,−2q,+q]` →
butterfly; a split 2× body folds in; short/credit structures fall through), emitting per-unit `legs` + `quantity=q`
+ a NEGATIVE `entry_credit`; the four codes added to `RESCUE_ADHOC_SUPPORTED`. TDD per layer; green: options_svc
**537** (incl. `test_rescue_range` **15** + `test_compute_rescue` range **+6**) + webgui rescue/shell **52**; ruff
clean. Live-verified end-to-end (SPY call condor + put butterfly → advisory-only, correct close economics + heat).
**Follow-up (same day) — rescue close cards show the LOCKED-IN P&L, not a bare $0.** The card `Max loss` field is
`new_max_loss` = the max loss of the position that REMAINS after the action; a full **close** leaves you flat → $0,
which read as "this trade has no loss." Fixed across ALL close paths: the `RescueCandidate` contract gained
`realized_pnl` (the P&L locked in), set on every close/partial builder (`build_close`/`build_partial_close`/the
single/debit/range close candidates) from `mark.unrealized_pnl` (partial = ×closed-fraction); the page renders a
colored **Realized P&L** cell alongside Gross/Comm/Net and **suppresses the trivial "Max loss after: $0" on a full
close** (partial keeps it — the residual is real). The residual field was also relabeled **"Max loss" → "Max loss
after"**. Live-verified (MU bear-put debit close → Realized P&L −$650 shown, $0 suppressed). Branch `Using_Highcharts`. Design:
[design](docs/plans/2026-07-14-rescue-condor-butterfly-design.md). Prior — 2026-07-14 (**Rescue coverage — Phase 1b: debit verticals (ad-hoc)**: the `/options/rescue`
**ad-hoc** tab now builds advisory rescues for **defined-risk DEBIT verticals** — `VERT_CALL_DEBIT` (bull call =
long lower call + short higher call) and `VERT_PUT_DEBIT` (bear put = long higher put + short lower put) — the
next family after singles (credit spreads · IC/fly · singles · debit verticals = **8 of 19** structures; the rest
still pop "not available yet"). **Advisory-only** (ad-hoc has no Apply). Engine (`services/options_svc/rescue.py`,
PURE): `assess_debit_risk` (directional — "at-risk" = underlying moved against the LONG leg; heat =
`min(50, loss_frac·60) + min(25, otm_depth·300) + 15 if dte≤5 & OTM`, otm_depth from `long_strike`) +
`debit_candidates` — all commission-aware, `apply_kind="advisory"`: **close** (sell to close → +cv·100·qty credit,
`new_max_loss=0`), **roll_out** (sell current + buy +30d same-strikes → debit since the later spread is richer),
**convert_to_butterfly** (SELL short-strike + BUY beyond it → the L/S/(S±w) butterfly, credit that reduces the net
debit). Compute routing (`compute.py`): `_advisory_from_debit`/`_adhoc_debit` mirror the singles path —
`_DEBIT_STRATEGIES` route in `compute_rescue_adhoc`; the two legs are priced directly via `_make_leg_pricer`
(cv = long mid − short mid, falling back to the entered debit when a leg is unpriceable — the off-hours case, where
only `close` survives), underlying from the gamma-snapshot spot, `unrealized_pnl = (cv − |entry_credit|)·100·qty`.
Page mapper (`webgui/pages/options/rescue.py`): `adhoc_spec_from_legs` recognizes a 2-leg debit vertical (BEFORE the
generic net-credit guard, which applies only to credit structures) → `long_strike`/`short_strike` + a **negative**
`entry_credit`; both codes added to `RESCUE_ADHOC_SUPPORTED`. TDD per layer; green: options_svc **516** (incl.
`test_rescue_debit` **11** + `test_compute_rescue` debit **+5**) + webgui rescue/shell **47**; ruff clean.
Live-verified end-to-end (SPY bull-call + bear-put → advisory-only, correct close economics + directional heat).
Branch `Using_Highcharts`. Design:
[design](docs/plans/2026-06-24-rescue-debit-verticals-design.md). Prior — 2026-07-13 (**Paper-trade non-credit structures (long options + debit verticals)**: the
multi-strategy Swing Scanner's **Send to Paper** button now works for **defined-risk DEBIT** structures —
**LONG_CALL / LONG_PUT / BULL_CALL / BEAR_PUT** — not just credit spreads (naked shorts stay excluded:
undefined risk). The ledger (`trades.db`, `paper_trader`) grows a **legs-based DEBIT trade**: `create_paper_trade`
routes those `type`s to `_create_debit_trade`, storing the normalized `legs` + `entry_debit` (the scanner's
per-contract `net_debit`) + `max_loss` (= debit) + `direction="DEBIT"` (a DEBIT reads as a NEGATIVE per-share
`entry_credit` so the existing Paper Trades columns render unchanged). Repricing + settlement are GENERIC over
the legs: new `signal_repricer.reprice_legs` (values each leg at its current mid, long +/short −, → per-contract
unrealized = `value×100 − entry_debit`) + `legs_intrinsic_value`/`position_intrinsic` for expiration
(`paper_trader._expire_debit_trade`); `compute._reprice_open_pnl` routes `direction=="DEBIT"` → `reprice_legs`,
credit spreads keep the tested short/long-strike `reprice_swing` path (zero regression). The Paper Trades page
renders debit legs as `L 450C` / `L 100C / S 105C` (`paper._legs_text`), and `strategy_table._PAPER_TYPES` +
`_allow_paper` now gate the button open for the four debit types. **Units validated end-to-end against the REAL
`strategy_scanner.payoff_metrics`** (net_debit/max_loss are per-CONTRACT ×100; leg `mark` per-share): a $2.50
LONG_CALL ×3 → entry_debit $250, max_loss $753.90 (incl. commission), ITM@112 → realized ($1,200−$250)×3=$2,850.
TDD per layer; green: options-scanner signal_repricer **24** + paper_trader debit **6** + options_svc **466** +
webgui **739**; ruff clean. See [[paper-debit-trade-representation]]. **Restart `options_svc`** to pick it up.
Branch `Using_Highcharts`. Prior — 2026-07-13 (**Manual-book analytics parity + swing-model staleness warning + process
watchdog + gex VACUUM tool**: the follow-on "remaining items" batch. **(1) Manual-book analytics parity.**
The `perf_analytics` engine + `perf_charts` chart builders (equity curve + MAE/MFE) are now shared and
wired to the MANUAL paper account too (`compute.manual_analytics` → `_book_analytics(None)` →
`cache:options:paper_analytics`, published by `refresh_paper_account`; surfaced as an **Analytics** section
on `/options/portfolio`). Because the manual book auto-trades EVERY captured signal while the driver book
trades Claude's SELECTION, the two equity curves are the **scanner-baseline-vs-decider benchmark** — the
answer to "does Claude's selection add edge?" (live: manual −$430 realized vs driver −$2,648, caveat:
different risk caps). The driver page's `equity_curve_figure`/`excursion_text` were extracted to the shared
`pages/options/perf_charts.py` (re-exported in `driver.py`, so both pages render identically). **(2)
Swing-model staleness warning.** `/trade` now shows an amber "⚠ Model is N days old — re-run
fit_swing_model.py" nudge when the `swing_model.json` fit date is >60 days old (`trade.model_staleness`),
so the validated factor model's regime-dependent edge doesn't silently decay (the refit itself stays a
manual offline run). **(3) Process watchdog (opt-in) — `tools/watchdog.py`.** Probes every tier (Memurai
PING / proxy + 6 services `/health` / webgui TCP) and restarts a DEAD process via the same windowless
`tools/restart_one.bat` the Status page uses, **storm-capped** (≤3 restarts / 10 min → then left down +
logged). NOT started by `start_all` — run it yourself for an unattended stack (`python tools/watchdog.py`,
`--dry-run`/`--once`). Closes the deferred R4a gap (a dead PROCESS, vs the in-process scheduler restart).
Live-verified (dry-run sweep: all 9 components healthy). **(4) gex_history.db VACUUM — `tools/vacuum_gex.py`** (since 2026-07-13 also a confirm-gated **Settings → Maintenance → Vacuum GEX history DB** button — runs the tool as an off-thread subprocess and prints before→after; first run reclaimed 1.72→1.46 GB)**.**
Offline maintenance that runs `PRAGMA auto_vacuum=INCREMENTAL; VACUUM;` to SHRINK the DB on disk (the daily
`purge_keep_sessions` frees pages but doesn't shrink the file). **Refuses to run during market hours / while
the collector lock is fresh** (VACUUM locks the DB for minutes) unless `--force`; `--purge` runs retention
first; reports before→after size. (DB currently 1.72 GB.) TDD; green: webgui **738** + options_svc (manual
analytics/handlers) + tools/watchdog **6**; ruff clean. **Restart `options_svc`** for the manual analytics
view. Also — **the five-state order-flow streamers were LIVE-RTH-VERIFIED** this session (below). Branch
`Using_Highcharts`. Prior — 2026-07-13 (**Driver performance analytics + MAE/MFE + proxy hardening + CI/ruff +
order-flow live-verified**: a batch of "know-thyself" analytics, security hardening, and hygiene. **(1)
Driver performance analytics (`/driver` → new "Analytics" section).** A new PURE
`services/options_svc/perf_analytics.py` builds three views over the driver book's positions —
**equity_curve** (daily realized P&L + cumulative-realized equity, bucketed by exit date), **posture
post-mortem** (`posture_stance` groups CLOSED positions by whether they were opened WITH vs AGAINST the
directional posture recorded at entry → win-rate/avg-P&L per stance + a with-vs-against edge, answering
*does the decider win more trading with the tape?*), and **excursion_stats** (MAE/MFE aggregates + MFE-
capture). `compute.driver_analytics()` reads the driver DB → published to a new
`cache:options:driver_paper_analytics` view by `refresh_driver_paper` (every 5-min manage tick). The page
renders a Highcharts equity curve (equity line + daily-P&L columns, built once + updated in place) + a
posture-stance table + an MAE/MFE line (`driver.equity_curve_figure`/`postmortem_rows`/
`postmortem_headline`/`excursion_text`). Live-verified: the equity curve populates from the real book (9
daily points, −$2,648 cumulative realized). **(2) MAE/MFE tracking (#2).** `paper_account_db` gained
nullable `mae`/`mfe`/`entry_context` columns (idempotent ALTER migration); `paper_engine.run_manage_cycle`
rolls each open position's max-adverse/max-favorable excursion on every reprice (`excursion_update`, PURE).
Forward-only (existing positions have NULL until repriced). **(3) Entry-context stamping (#7).** The driver
handler stamps each opened position's `entry_context` (posture + market_read summary + shadow would_block)
— threaded `driver_paper_create` args → `open_driver_position(context=)` → stored as JSON — so the
post-mortem can attribute the ENTRY regime to the realized outcome (forward-only; pre-existing positions
have no context → not attributed). **(4) Proxy hardening (#8) — all backward-compatible/opt-in
(`docs/SECURITY.md`).** `schwab_proxy` CORS now defaults to a **local webgui/proxy allowlist** instead of
`*` (closes the browser-reachable-proxy hole; override via `PROXY_CORS_ORIGINS`); an **optional shared
secret** guards the trading endpoints (`/accounts`/`/orders`/`/positions`/`/transactions` require
`X-Proxy-Secret` — enforced ONLY when `PROXY_SHARED_SECRET`/`shared/proxy_secret.txt` is set, timing-safe;
`proxy_client` auto-attaches it); the **Bus** supports an optional `MEMURAI_PASSWORD` (unset → no AUTH,
unchanged). Defaults preserve today's behavior exactly. **(5) CI + ruff + lockfile (#10).** A GitHub
Actions per-folder test matrix + `ruff check` (lenient `pyproject.toml` config, passes clean) + pinned
security-sensitive deps + `requirements.lock` + `docs/CI.md` (some of this pre-existed from the 2026-07-02
pass and was consolidated/fixed to green). **(6) Five-state order-flow streamers — LIVE-RTH-VERIFIED.**
Probed the live caches during market hours (12:09 CT): `cache:sentiment:order_flow` populates with fresh
Lee-Ready-classified equity CVD + option pressure, and the aggression axis consumes both (visible as
`order-flow`/`option-flow` in `derived.trend.evidence`) — Phases 4-5's pending live check now passes. See
[[streamer-order-flow-deferred]]. TDD per layer; green: driver_svc **203** + options_svc **463** + webgui
**732** + schwab-proxy **91** + shared/bus **20** + options-scanner paper **75**; ruff clean. **Restart
`options_svc` + `driver_svc`** (the analytics view + entry-context stamping go live; the /driver Analytics
section fills once options_svc republishes). Restart the proxy only if you set a secret / CORS override.
Branch `Using_Highcharts`. Prior — 2026-07-13 (**Directional-gate shadow mode + automated EOD close-out push**: two
independent enhancements. **(1) Directional-gate shadow mode (driver_svc).** The wrong-side directional
gate (`guardrails._side_blocked` / `WRONG_SIDE_REGIME`) is still shipped INERT
(`settings.DIRECTIONAL_GATE_ENABLED=False`) because its offline backtest only covered 7/22 trades — so
instead of waiting, `compute.run_cycle` now runs it in **log-only shadow mode**: it computes the decisive
price-truth posture (`_directional_posture`) EVERY cycle and, via the new PURE
`guardrails.shadow_gate(executable, posture)`, records which trades that FIRED a live gate WOULD have
blocked — **without blocking anything** while the flag is off (byte-identical execution to before). The
finding rides an additive `shadow_gate` block (`{posture, would_block:[{id,symbol,structure}], n,
enabled}`) on the run_cycle return → onto each `cache:driver:autonomous` decision-log row (loose dict, no
contract change) → surfaced on `/driver` as an amber "👁 Gate shadow: would block N …" line
(`driver.shadow_gate_line`, shown only while the gate is inert AND it would have blocked ≥1 fired trade).
Every live trading day now accrues real would-have-blocked evidence, so `DIRECTIONAL_GATE_ENABLED` can be
flipped on data instead of the thin replay. When the flag is ON the wrong-side trades are already in
`rejected`, so the shadow is naturally empty. **(2) Automated EOD close-out push (options_svc).** A new
once-daily scheduled slot at **~15:10 CT** (`scheduler.eod_summary_due`, mirrors `action_alert_due`,
trading-day/holiday-gated, 30-min grace) → `handlers.run_eod_summary` → `compute.collect_eod_summary`
(PURE `_eod_book_summary` per book) assembles the day's result for BOTH engine paper books — the MANUAL
account + the isolated DRIVER account — (day P&L = `session_pnl`, equity, open count, halt flag, today's
closed W-L + realized from `exit_ts`-dated closed positions) and pushes a compact digest via the existing
`shared/notify` channels (`push_notify.send_eod_summary` — Telegram/Discord/SMS; sends whenever ≥1 book is
seeded, no empty-content skip since the day's P&L IS the point). Cached at `cache:options:eod_summary` for
inspection. Book state is read AS-IS at 15:10 (no manage cycle is forced first), so a 0-DTE that expired
but hasn't settled still contributes its unrealized. Closes the daily-accountability loop — the day's
RESULT now pushes to the phone alongside the already-24/7 signal + action-alert pushes, no browser needed.
TDD per layer: driver_svc **202** + options_svc (+new push_notify/scheduler/compute/handler tests) +
webgui **726** green; live-verified `collect_eod_summary` end-to-end against the real books (manual/driver
day P&L assembled, digest rendered, handler cached). **Restart `driver_svc` + `options_svc`** to pick both
up. Branch `Using_Highcharts`. Prior — 2026-07-12 (**Market Trend & Sentiment split into 4 tabs**: the monolithic
`/sentiment` page was broken up — the **Sector & Industry Performance** table moved to a NEW
`/sentiment/sectors` tab (`pages.sentiment_sectors`, inserted between Sentiment and Sector Rotation)
and the **RRG chart** moved out of `/sentiment/rotation` into a NEW `/sentiment/rrg` tab
(`pages.sentiment_rrg`, last, after Sector Rotation). Final `SENTIMENT_CHILDREN` tab order: Sentiment
· Sector & Industry · Sector Rotation · RRG. The new pages are thin Tier-3 readers that **reuse the
PURE builders** from `pages.sentiment` / `pages.sentiment_rotation` (so the display transforms + their
tests stay single-source); `/sentiment` still reads `cache:sentiment:sectors` only to fill the
Components popup's Rotation/Sector-Value cells. The **"Daily Sentiment & Trend"** intraday graphs are
now **expanded by default**. Per-page hover/help text (`page_help.py`) updated for the new structure.
webgui **723** green; live-verified all 4 tabs (Sentiment: gauges + expanded graphs, no sector table;
Sector & Industry: full table; Sector Rotation: quadrant map, no chart; RRG: 11-series chart). Branch
`Using_Highcharts`. Prior — 2026-07-12 (**Tabbed navigation + page-chrome overhaul + config-driven theming**:
the webgui nav was REDESIGNED — flat drawer main menu + the active group's child pages as a
**folder-style TAB STRIP across the top** with a **subtab slot** beneath it (`main._NAV_GROUPS` /
`_group_children` / `subtab_slot()`; badges float on tabs, drawer group items carry summed badges;
see "webgui structure"). **In-page view pickers moved into the subtab slot**: Gamma
GEX/Charm/DEX/Vanna/Flow/Term, Scanner 0-DTE/Swing (Run scan right-aligned with the table), and
Simulator Replay/What-if/IV-shock (its Controls+Strategy cards also merged side-by-side into one
card). **Menu renames**: Sentiment→Market Trend & Sentiment, Trade→Trade Analyzer, Driver→Claude
Trades. **Page-header cleanup** across Scanner/Paper/Captured/Paper-Portfolio/Swing/Gamma/Simulator/
Expected-Move/Rescue/Market-Dashboard: redundant titles removed (the tab strip names the page),
action buttons right-justified, row counts bottom-right small; Rescue also dropped its wrapper
cards + went dense. **Config-driven styling**: `config/theme.toml` (palette / semantic / 3D buttons
/ gauge / charts / **typography** [px sizes] / **menu**) + a **Settings → Appearance** in-app editor
(section tabs + clickable swatch tiles + Save / Save-&-restart / Reset — comment-preserving
`theme.save_theme_values`). **Fixes shipped alongside**: sentiment intraday graphs frozen-update bug
(Highstock `chart.update()` throws → plain synthetic-index charts; days packed, no dead space) +
test-fixture rows leaking into the live intraday DB (pytest now isolates it — see
tests/conftest + `intraday_history_db.connect`); hourly RTH sector-P/C recompute
(`sentiment_svc scheduler.sectors_due` — premarket starts no longer blank the P/C column); per-view
staleness thresholds (`alerts.stale_after` — no more false "scanner stale" toasts between 15-min
scans); driver scheduler holiday gate (`driver_svc _HOLIDAYS` — no Claude calls on market holidays).
webgui **723** / options_svc **432** / sentiment_svc **140** / driver_svc **162** green. Branch
`Using_Highcharts`. Prior — 2026-07-11 (**Gamma forward projection on the GEX heatmap + 1-min collection +
condensed header**: the `/options/gamma` GEX heatmap now draws a **forward projection band** out to
the 4pm-ET close — future **15-min** columns re-price today's **standing open interest at flat spot**
(the deterministic charm/time-decay morph: walls sharpen, gamma concentrates ATM into the close), each
contract's current GEX contribution scaled by a **BS gamma time-decay ratio** anchored 1.0 at the
collected "now" column so the seam is continuous (`compute.project_gex_grid`, pure, reuses the engine's
exact GEX formula). An **expected-move cone** (`project_em_cone`, √-time fan) is overlaid so the spot
uncertainty is shown honestly rather than baked into the colored grid. **GEX-only, sticky-strike IV,
hidden off-hours** (no session left → collected-only). The projection rides the EXISTING
`cache:options:gamma` GEX view — `gamma_snapshot` attaches a `projection` block (`{times, grid, cone,
spot}`) computed off the live chain and **cropped to the display window** (`_crop_gamma_views`); the page
appends the future columns right of a dashed **"now" divider** with the cone as faint dashed overlays
(`heatmap_figure(projection=…)`, same interpolated image / colorAxis). The strike/heatmap split is now a
**fixed 40/60** (`_STRIKE_HEAT_SPLIT`, one-line flip to 70/30) — the full day + forward band make the
heatmap the star. **GEX collection cadence dropped 2 min → 1 min** (`gex_collector.POLL_INTERVAL_MIN` /
`scheduler._GEX_INTERVAL_MIN` / `gex_status.STALE_AFTER_SEC=120` in lockstep) and the collection
window now **starts 08:00 CT** (was 08:30 — 30 min pre-open; `_GEX_START` / `gex_collector.START_HOUR`
in lockstep). **The Gamma charts now show PRE- and POST-market**: the overnight blank
(`scheduler.gamma_cleared`) was REMOVED, so the display shows the most-recent-available session 24/7 —
the by-strike bars from the live chain, the heatmap from `active_session_date`, which returns the PRIOR
session premarket and flips to today once collection starts. **NOTE (measured):** premarket adds little
DATA — OI is fixed overnight and `$SPX` (an index) doesn't tick pre-open, so pre-open rows are largely a
re-pricing of static OI; the 08:00 premarket Claude briefing is UNAFFECTED (it reads the live chain, never
the collector DB). **Explain + Analyze + the
4×/day scheduled auto-briefings** now carry a reader-first **"into the close"** forward read
(`_projection_brief` = projected flip / call+put walls / EM band at the close → an optional
`close_outlook` field on the Analyze `submit_analysis` schema + infographic card, and an "Into the close"
block on the Explain infographic). The Gamma **header was condensed 4 rows → 2** (a **Briefings**
dropdown replaces the four auto-briefing buttons; one `·`-separated status strip merges collector status
+ last/next scan + refresh countdown + summary via `status_strip_text`). **Restart `options_svc` + the
webgui.** Built subagent-by-subagent, TDD per layer (2-stage spec+quality review); options_svc **431** +
webgui **698** green. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-11-gamma-forward-projection-design.md) /
[plan](docs/plans/2026-07-11-gamma-forward-projection-plan.md). Prior — 2026-07-10 (**Manual Paper Portfolio → hourly entry+manage cadence**: the
MANUAL paper account's auto-run moved from the every-5-min `manage_due` slot to a NEW
**top-of-the-hour** schedule — entry (open new paper trades from current captured signals) +
manage (reprice + auto-close hits) **once at 09:00–14:00 CT** (last run 2pm; **NO 15:00 run** at
the regular-session close). New PURE gate `scheduler.paper_cycle_due(now, ran_slots)` (trading-day
only, once-per-hour within a 20-min grace, mirrors `analyze_slot_due`) + handler
`handlers.run_paper_entry_and_manage` (entry guarded on an existing account in its own try/except
so an entry failure can't skip manage → `run_manage_and_refresh`); the scheduler `loop` gates it on
`paper_cycle_due` (hour latched in `paper_ran` before the blocking cycle). The isolated **DRIVER**
paper account is UNCHANGED — it stays on the 5-min `manage_due` slot (`run_driver_manage_and_refresh`
now runs alone there). **Trade-off:** the manual book's live P&L + target/stop auto-close now update
hourly, not every 5 min. **Restart `options_svc`** to pick it up. options_svc **419** green; TDD per
layer (8 gate + 3 handler tests + updated loop source-inspection tests). Branch `Using_Highcharts`.
See the "Paper auto-manage" box below. Prior — 2026-07-09 (**Intraday options premium/volume flow — new Gamma `Flow`
view**: a per-symbol intraday chart (a new view inserted **before Term**) of the underlying
**price** + daily-cumulative **call/put premium ($M)** with a **net-premium (call−put)** signed
bottom panel. **Phase 1 (backend):** the 2-min GEX poll now also computes
**`flow_skew.index_call_put_premium(chain)`** = `Σ mark × totalVolume × 100` per call/put
(mid-based, **UNSIGNED daily-cumulative** — Schwab has no time-&-sales tape, so no buy/sell split)
for EVERY collected symbol (index base + `Top 20.xlsx`), stored as additive `call_prem`/`put_prem`
REAL columns in `gex_history_db` (idempotent ALTER migration), read via
**`gex_history_db.load_flow_series(conn, symbol, d)`** → `(ts, spot, call_vol, put_vol, call_prem,
put_prem)` per snapshot. **Phase 2 (frontend):** `compute.gamma_snapshot` embeds a **`flow`** series
(reusing the ONE read-only history connection), and the page's PURE `flow_figure`/`flow_summary_text`
render it as a Highcharts stacked-panel chart (price left-axis + call/put premium right-axis + net
panel) under the new **`Flow`** toggle. Premium is **forward-only** (NULL on pre-Phase-1 rows → the
line starts where collection began); no signed buy/sell is possible from stored data. **Restart
`options_svc`** (its `collect_gex_snapshots` runs `init_schema` [adds the columns] then `poll_once`
[populates them]) **+ the webgui**. options-scanner flow_skew/gex_history **+7**, options_svc compute,
webgui **692** green; TDD per layer. Branch `Using_Highcharts`. Prior — 2026-07-09 (**Driver directional gate + cumulative MTD target** — two fixes
motivated by a forensic review ("C") of the driver's REAL closed book: 22 closed trades,
**−$908 realized / 27% win / PF 0.23**, drawdown to $22,768 (−8.9%), a −$1,946 halt day. Root
cause = **wrong-side selection** — 10 of 11 DELTA_STOPs were **call credit spreads run over by a
rising tape** (CCS bucket −$706 @ 21% win); the stops fire at ~0.35 short delta (sensible, median
~1-day hold) so they're fine — **the entry side is the problem**. The app's own sentiment read was
**bearish (3.92) while price melted up**, i.e. its directional opinions were INVERTED, so the fix
keys on **price truth**, not sentiment. **(A) Directional gate** (`guardrails._side_blocked` +
`WRONG_SIDE_REGIME` + a `posture` kwarg on `apply_guardrails`): hard-block a **CCS in an `up` tape /
a PCS in a `down` tape** (IC exempt), where `compute._directional_posture(market_read)` derives
up/down/neutral from **broad-index change_pct + `$ADVN-$DECN` breadth agreement** (NOT sentiment/bias,
NOT the gamma flip — a volatility regime); `_market_read` now carries per-index `change_pct` from the
dashboard. The gate is **code-authoritative, IC-exempt, and degrade-safe** (posture `neutral` when
data is missing → inert), placed BEFORE the capacity check so a block never eats a slot. It ships
**behind `settings.DIRECTIONAL_GATE_ENABLED` (default False = INERT)** and `run_cycle` forces
`neutral` until the flag is flipped. **Backtest first** (`validate_directional_gate.py`, offline):
replaying the 22 real trades vs SPX spot-trend from `gex_history` — at a 24h lookback it blocks the
**two catastrophic $SPX CCS losses (−$561, clear up-trends) → net +$613 / 66% of the CCS bucket**,
but only **7/22 trades are covered** by history and it's **lookback-sensitive** (30h → net −$49 / 0%);
concept validated on the worst day, but **too thin to auto-enable → flag stays OFF** pending more
coverage / the user's call. **(B) Cumulative MTD target** (LIVE now): the flat $500/day banking
target becomes `effective_target = clamp(N×500 − MTD_realized_before_today, TARGET_FLOOR 250,
TARGET_CAP 1000)` (`compute.effective_target` + `mtd_realized_before_today` + `_mtd_trading_days`),
computed in the handler from the driver book's closed-position MTD realized P&L + a holiday-aware
trading-day count, threaded into `build_packet` + `halt_state` (and the published monitor `target`).
Behind pace → ratchet to $1,000; ahead → ease to $250; **the −$1,500 loss halt + per-trade caps are
UNCHANGED** (only the bank/stop threshold moves — bounded, no chasing via oversizing); fails safe to
$500. Built directly, TDD, per-task commits: driver_svc **196** + contracts **38** green. **Restart
`driver_svc`** — the cumulative target is live immediately; the gate is inert until you enable it.
PAPER ONLY. Design/plan:
[design](docs/plans/2026-07-09-driver-directional-gate-cumulative-target-design.md) /
[plan](docs/plans/2026-07-09-driver-directional-gate-cumulative-target-plan.md). Prior — 2026-07-08 (**Driver market-context block — the decider now reads gamma /
breadth / sentiment (context only)**: the autonomous Driver's Claude decider was blind to market
structure — it saw only `vix` + the five-state label string, yet it trades $SPX/SPY/QQQ credit
spreads whose safety is defined by exactly the gamma flip/walls it couldn't see. It now gets an
additive **`market_read`** in its decision packet: per-index **gamma flip / call+put walls / max-pain /
expected-move / what-if** from the **freshest TODAY `gamma_analyze` briefing** (the 4×/day Claude
briefing — one Claude writes the gamma thesis, the Driver's Claude now reads it) paired with a **live
per-index spot** (`fetch_market_context` gained `SPY,QQQ` → a fresh spot-vs-flip **posture**; the
briefing spot is the fallback), the **market-dashboard breadth (`$ADVN-$DECN`) + risk-on/off**
(`cache:market:dashboard`), and the **sentiment 0-10 score + bias** (`cache:sentiment:composite`
`live.composite`). **CONTEXT ONLY — no new hard rule**: `_market_read` is appended in `build_packet`
exactly like the existing `market_state` line (never filters the menu; absent sources → no key →
byte-identical to today), and **`guardrails.py` is UNTOUCHED** (the wall-aware rejection + breadth
halt are a **deferred** ③-gate follow-up that must be backtested first). Pure, defensive helpers in
`driver_svc/compute.py` (`_dashboard_risk_read` / `_pick_latest_briefing` [drops prior-session
briefings — stale walls mislead] / `_market_read` / `_posture` / `_as_of` / `_market_read_summary`);
the handler reads the caches into the `market` dict (`_read_briefing` across the 4 scheduled slot keys
+ `_read_sentiment_magnitude`); the decider `_SYSTEM` gained a paragraph on **how to weigh** the read
(prefer put-credit below the put wall / call-credit above the call wall; below-flip / risk-off →
be **selective, NOT stand down** — keeps the Very Aggressive $500/day mandate); and a one-line
`market_read` **summary is surfaced on each `/driver` decision-log row**. Driver stays **Redis + one
proxy quote** (3-tier clean — no engine/DB imports). Built directly, TDD, per-task commits: driver_svc
**170** + contracts **38** green. **Restart `driver_svc`** (benefits from `options_svc` + `market_svc` +
`sentiment_svc` up so the briefing / dashboard / composite caches populate). PAPER ONLY. Design/plan:
[design](docs/plans/2026-07-08-driver-market-context-block-design.md) /
[plan](docs/plans/2026-07-08-driver-market-context-block-plan.md). Prior — 2026-07-08 (**Removed the legacy morning-agent / order-approval queue (full purge)**
— per the user's directive, the entire legacy morning-agent + approval-queue subsystem was deleted in
three reviewed units. (1) **`services/driver_svc`** — the `run_morning`/`execute`/`build_perf_report`
compute, the `run`/`approve`/`skip`/`perf` handlers + `cache:driver:approvals`/`cache:driver:performance`,
the `morning_due` scheduler branch, and the `ApprovalState`/`PerfReport` contracts. The AUTONOMOUS path is
UNTOUCHED — its `fetch_market_context` was made **self-contained** (a direct `$VIX,$SPX,$VIX1D` proxy
fetch, defensive → `{}`) so it no longer imports `morning_agent`. (2) **`webgui/pages/driver.py`** — the
"Legacy approval queue" UI, "Run morning agent" button, approval cards/dialog + dead builders removed; the
page is now purely the **autonomous monitor + the closed-trade Performance view**. Stray refs cleaned in
`main.py` (nav badge), `status.py` (freshness row repointed to `driver:autonomous`), `eod.py` (driver tiles
repointed to `driver_paper_perf`), `page_help.py`. (3) **`claude-driver/`** — DELETED `morning_agent.py`,
`order_executor.py`, `trade_selector.py`, `perf_report.py`, `approval_server.py`, `order_preview.py`,
`intraday_monitor.py`, `start_all.bat` + their 8 tests; KEPT `config.py` (autonomous still reads
`RISK_LIMITS`) + `feature_engineer.py` (shared ML-feature builder, used by non-morning-agent ML scripts);
`tools/check_env.py` dropped the :8300 approval-server health check. Green: driver_svc **144** + contracts
**37** + webgui **679**; claude-driver introduced no new import errors. Branch `Using_Highcharts`.). Prior — 2026-07-08 (**Driver Performance view — real closed-trade P&L (was always $0)**
— the `/driver` "Performance" table read the dead legacy morning-agent `trade_log.json` ledger,
whose old "polled" equity/futures rows never close, so it showed **all-open, $0, ~2-week-stale**
data. Repointed it to the driver's **isolated paper account's `closed_positions`**
(`cache:options:driver_paper_account`) — the REAL closed options credit spreads with actual
realized P&L, updated every 5-min manage cycle + the page's 2s version-poll (timely). New PURE
builders in `webgui/pages/driver.py` — `closed_summary_text` (Closed N · W–L · win% · realized $)
+ `closed_trade_rows` (newest-first) + `_CLOSED_COLS` — with reader-friendly columns
(Closed/Symbol/Strategy/Qty/**humanized** Exit-reason [`TARGET_HIT`→"Target hit"]/colored Realized
P&L), dropping the useless legacy Bucket/Source/Status columns; a **Refresh** button forces an
immediate `driver_paper_manage` reprice. The legacy `cache:driver:performance` page read is gone.
webgui driver **31** green (compile-verified; browser check skipped — the running webgui holds
:8500). **Restart the webgui** to see it. Branch `Using_Highcharts`.). Prior — 2026-07-08 (**Driver "Very Aggressive" risk profile** — loosened the
autonomous driver's risk envelope toward the $500/day goal (user directive); all knobs now live
in `driver_svc/settings.py`: `PER_TRADE_MAX_RISK 1500→3000`, `DAILY_RISK_BUDGET 4500→12000`,
`MAX_CONCURRENT 6→10`, `MAX_TRADES_PER_CYCLE 3→5`, `VIX_MAX 25→35`, `MENU_TOP_N 12→15`, plus a NEW
`DAILY_LOSS_HALT=1500` — the biggest brake, replacing the legacy $250 halt that ended the day
after ONE losing $SPX (`compute._daily_max_loss` reads settings first, legacy `config.RISK_LIMITS`
as fallback). The paper OPEN path's `options_svc.compute._DRIVER_MAX_RISK_PER_TRADE`→`3000` (the
MANUAL account stays `config_paper.MAX_RISK_PER_TRADE=250`); the `decider._SYSTEM` prompt was
rewritten from "standing down is encouraged" to an AGGRESSIVE mandate (take reasonably-scored
trades to build toward the target; stand down only on genuinely poor edge / hostile conditions).
Net posture: ~half the $25k paper book deployable, ~12%/trade, a $1,500 daily-loss stop (3× the
target) — deliberately aggressive (user choice; dial back in `settings.py`). driver_svc **168** +
options_svc driver-account **15** green. **Restart driver_svc + options_svc** to pick it up.
Branch `Using_Highcharts`.). Prior — 2026-07-08 (**Gamma briefing history — store + CLI utility +
in-app viewer**: every Gamma Analyze briefing (the 4×/day Auto briefings + ad-hoc/
manual runs) is now **persisted** to a new SQLite store
`options-scanner/gamma_briefing_history_db.py` (`repo_paths.GAMMA_BRIEFING_DB`, one
row per `(date, slot)`) as its **STRUCTURED analysis payload** — the report HTML is
**regenerated on demand** (pure `compute.analyze_history_doc`), never frozen, so old
briefings re-render in the current infographic design and the data stays queryable
(bias/headline pulled out as columns). `handlers._persist_briefing` records each
successful run (wired into `run_scheduled_gamma_analyze` + the ad-hoc `gamma_analyze`
command; degraded no-chains/no-key pages have no `analysis` and are skipped);
`publish_gamma_briefing_index` publishes the metadata index
**`cache:options:gamma_briefings`** for the picker (startup + after each persist); the
**`gamma_history`** command regenerates a date's (or a single slot's) report →
**`cache:options:gamma_history`** → served raw at **`/options/gamma-history`**. A
**CLI utility** `services/options_svc/gamma_briefing_report.py` (run manually) does
`--list` / `--date [--slot]` / `--range START END` / `--generate` (fresh run → store
→ report; needs the proxy + key), writing HTML under
`options-scanner/data/gamma_reports/`. The `/options/gamma` page gains a **History
picker** (a date + slot dropdown from the index + an **Open** button that enqueues
`gamma_history` and opens the regenerated report in a new tab, mirroring
`_watch_analyze`; `history_dates` is the pure date-list helper). **Restart
`options_svc`** so persistence + the index publish go live. gamma_briefing_history_db
**7** + options_svc handlers/scheduler/compute + webgui **689** green. Built with
per-layer TDD. Branch `Using_Highcharts`.). Prior — 2026-07-07 (**Five-state market classifier (direction × aggression) —
Phases 0–5 + Tier 3 shipped (Phases 0–3 LIVE on REST data; Phases 4–5 streamer login+subscriptions
verified live, RTH order-flow population pending; Tier 3 = validation harness + LOW-weight swing/driver
integrations)**: the app's one-axis intraday
trend state (`scoring/intraday_trend.py:score_to_state` → `bull_trend`/`pullback_in_bull`/`range`/
`bear_rally`/`bear_trend`) is **replaced — for the regime-driving intraday state** — by a
**two-axis direction × aggression classifier** emitting five trader states: **Bullish / Lack of
Bullishness / Neutral / Lack of Bearishness / Bearish**. The two middle states capture the
effort-vs-result asymmetry a single directional axis can't express (price up but hollow → *Lack of
Bullishness*; price down but no follow-through → *Lack of Bearishness*). **Architecture:** the
existing 0–100 intraday trend score is the DIRECTION axis (unchanged), crossed with a NEW signed
**AGGRESSION** axis via a 9-cell grid (`sentiment-dashboard/scoring/market_state.py:classify_market_state`,
PURE; bands `≥60` bullish / `≤40` bearish, aggression `≥0.2`/`≤−0.2`). Aggression inputs
(confidence-weighted-blended via the PURE signed `scoring/aggression.py:blend_aggression`, graceful-
degrading): **(1) volume-effort** (`scoring/effort.py` — up/down-day volume ratio + volume-on-
rallies-vs-pullbacks + close-location-value over SPY daily); **(2) 25-delta risk-reversal skew Δ**
(`options-scanner/flow_skew.py`, computed in the options_svc **2-min GEX poll** from the ALREADY-
fetched $SPX/SPY/QQQ chains — no extra fetch — stored per snapshot in `gex_history_db`, published
as **`cache:options:flow_skew`** with `rr_delta` vs the prior snapshot; a shared-front-expiration
guard keeps the RR tenor-consistent); **(3) cross-sector cap-weighted P/C 5-trading-day Δ**
(`live_composite.cap_weighted_pcr` + a NEW daily store `services/sentiment_svc/sector_pcr_history_db.py`).
Wired in `sentiment_svc.compute_intraday_trend` (reads `cache:options:flow_skew` + `compute.sector_pc_delta()`,
signs+normalizes — **rising put demand → NEGATIVE aggression**, SCALE tunables `SKEW_DELTA_SCALE=5.0`
IV-pts / `PC_DELTA_SCALE=0.3` P/C — blends, classifies), threaded through the EXISTING
`trend_regime.commit_state` 2-day hysteresis with a **migration guard** (an old-vocab persisted state
is treated as cold-start so no stale string is published). Published under the **SAME** bridge
`trend_regime.state` key, so **`regime_filter` was rekeyed via its one `_TREND_STATE_VOTE` dict** to
the new vocab (`bullish`→bull/block-CCS · `bearish`→bear/block-PCS · `neutral`→None ·
`lack_of_bearishness`→lean_bull [resilient, puts undefended → favor PCS] · `lack_of_bullishness`→
lean_bear [exhaustion at highs → favor CCS]) — **`evaluate_regime`'s AND-of-agreement logic is
UNCHANGED** (the two middle states land exactly on the old soft-lean slots). `compute._bridge_trend`
always emits new-vocab (neutral at cold start) so the gate is NEVER fed an unrecognized string. The
daily committed state is **recorded** (`services/sentiment_svc/market_state_history_db.py`, 90-day
window) for a later backtest-validation task. `/sentiment` shows the five-state label + description +
a **"Why" evidence** popup (direction/effort/skew/flow/aggression lines) on the **Today** trend gauge;
the **30-Day structural gauge KEEPS the old band vocabulary** (a structural direction-only read has no
aggression axis — `score_to_state` is **retained, deliberately NOT deleted**), so the page carries
both vocabularies (`_TREND_SHORT`/`trend_text_class` cover all 10 keys). **Phase 0** lifted the
Telegram/Discord/Fi-SMS channel senders + `shared/notifications.json` config out of
`options_svc/push_notify.py` into a shared **`shared/notify/`** helper (for the coming state-transition
alerts). **Phase 3 (SHIPPED)** added three intraday structure signals — **session-structure**
(`scoring/session_structure.py`, VWAP-hold + opening-range break → blended into the DIRECTION
price sub-score, `SESSION_BLEND=0.20`), **rejection/defense** (`scoring/rejection_defense.py`,
upper-wick exhaustion at highs vs defended-dip resilience → a new `rejection` AGGRESSION component,
`AGG_WEIGHTS["rejection"]=0.20`, no sign flip), and **volume-profile-shape**
(`scoring/profile_shape.py`, balanced single-HVN session → damps aggression toward Neutral,
`PROFILE_DAMP=0.5`) — all folded into `compute_intraday_trend` (each defensive/degrading) — plus a
**state-transition phone push** (`services/sentiment_svc/state_alert.py`: on a committed-state FLIP,
fire Telegram/Discord/Fi-SMS via the `shared/notify/` helper, gated enabled + valid-new-vocab + differ
+ market-hours; the cold-start old→new-vocab first cycle and same-state are skipped; best-effort, can't
abort the recompute). **Phases 4–5 (SHIPPED — streamer equity + option aggressor flow; code-complete,
pending a LIVE RTH verification):** the aggression axis now has real order-flow. **Proxy (additive,
proven-safe):** `_normalize_level1_equity` widened with bid/ask/bid_size/ask_size/last_size/total_volume
(+ RTH `REGULAR_MARKET_*` fallbacks for last/last_size — resolves the old `TODO(live)`); a NEW
`_normalize_level1_option` (last/last_size/bid/ask) + a `/stream/options` SSE fan-out with a refcounted
OSI union on the EXISTING shared stream worker — **provably isolated from paper-trade tracking**: the
reconcile subscribes `_registry.legs_union() ∪ flow_osis` (replace-semantics, read fresh on the stream
loop) and the trade-untrack orphan guard spares `_option_refcount`, so a tracked leg can NEVER lose its
subscription; the trade-detector block in `_on_option_message` is byte-identical (fan-out appended after).
**Consumers (`services/sentiment_svc/order_flow_consumer.py`, mirror the portfolio SSE-worker pattern):**
an EQUITY worker streams `/stream/quotes?symbols=SPY,QQQ`, classifies each trade via the PURE
`scoring/order_flow.py` (Lee-Ready quote rule + tick test → aggressor ratio / CVD), rolls a 5-min window;
an OPTION worker refreshes near-ATM SPY/QQQ OSIs every 5 min, streams `/stream/options`, classifies
put/call trades at bid/ask (per-OSI prev_last) → a signed put/call-pressure `signal` (put-buying →
NEGATIVE → bearish); both publish into **`cache:sentiment:order_flow`** (`{SPY,QQQ, options}`). The
classifier folds SPY equity CVD as the **`order_flow`** component (weight 0.15) and option pressure as a
distinct **`option_flow`** component (weight 0.10) — both NO sign flip (positive = net buying = bullish =
aligned), both defensive/degrading (no stream → drop out). Honest caveat: level-one CONFLATES rapid
ticks, so this is a **sampled** read (reliable over minute windows, not tick-perfect); Schwab has no
time-&-sales, SPY proxies $SPX (no index tape). **Still needs a LIVE RTH check** (restart proxy +
sentiment_svc, watch `cache:sentiment:order_flow` populate + the aggression axis move) — the blocking SSE
workers are live-verified, not unit-tested (the pure classifier/window/aggregate helpers carry the
coverage, mirroring the portfolio precedent). **Tier 3 (SHIPPED — validate-first): item 11** built an
OFFLINE validation harness (`sentiment-dashboard/validate_market_state.py` — run manually, NEVER in a
request path) that reconstructs the daily committed state over ~5yr SPY history (a daily-OHLCV CORE
reconstruction: a NEW `scoring/daily_direction.py:daily_direction_score` proxy × the REAL
`effort`+`rejection_defense` aggression, through the REAL `market_state` grid + `commit_state`
hysteresis) and measures forward-return stratification (per-state mean/hit-rate + **ordinal IC**). **Honest
result:** 20d ordinal IC **+0.087** (5d +0.055) — a modest, **regime-dependent** edge (calm IC +0.086 /
stressed +0.024) CONCENTRATED IN THE TWO MIDDLE STATES (Lack-of-Bullishness +0.99% vs Lack-of-Bearishness
+2.16% mean-20d — the framework's effort-vs-result innovation); the extremes are **inconclusive** here
(Bullish +0.65% underperformed via exhaustion; **Bearish NEVER fired in 5yr** — the inputs that most drive
it, skew spikes + put-flow, are exactly the ones EXCLUDED from the daily reconstruction). So — like the
validated swing model — a thin, label-don't-overtrust edge. **Items 9 & 10 were therefore built at LOW
weight (user decision):** **item 9** = a SMALL bounded family-fit tilt (`strategy_scoring.state_family_tilt`,
`STATE_TILT_MAX=6`, leaning on the two middle states — Lack-of-Bearishness→PCS+, Lack-of-Bullishness→CCS+/
long-call−) applied to `score_strategy`'s composite **AFTER the hard-gate grade is decided (a ranking nudge
that can NEVER flip a gated grade)**, fed by the live state read from `cache:sentiment:composite` in the
`swing` handler; **item 10** = the committed state (label+evidence) surfaced to the **Driver's Claude
decider as CONTEXT ONLY** in `build_packet` (read in the driver handler) — **`guardrails.py` is UNTOUCHED**
(`regime_filter` already hard-gates the driver's menu; the state is context, not a second gate, proven
context-only by test). Both additive/defensive (no state → no tilt / no context line). Everything is
ADDITIVE except the ONE coordinated `trend_regime.state` vocabulary change (`regime_filter` rekeyed in
lockstep). Green: sentiment_svc **136**, options_svc **400**, driver_svc **168**, webgui **681**,
schwab-proxy **82** (equity + option stream fan-out), options-scanner flow_skew **+18** / strategy_scoring
**56** / gex_history migration, sentiment-dashboard scoring modules
(effort/aggression/market_state/session/rejection/profile/order_flow/daily_direction) **+101**,
shared/notify **14**.
Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). **Restart `options_svc` +
`sentiment_svc`** to pick this up. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-07-five-state-market-classifier-design.md) /
[plan](docs/plans/2026-07-07-five-state-market-classifier-plan.md).). Prior — 2026-07-06 (**Paper expiration auto-close (both books) + thrice-daily
"trades needing action" push** — two features. **(1) Expiration auto-close.** Validated that
paper trades did NOT reliably auto-close on expiration and fixed both stores. The **account**
engine (`paper_engine.run_manage_cycle`, `paper_account.db`) settles at intrinsic, but had two
bugs: it settled 0-DTE **intraday at the open** (the 5-min auto-manage tick made this fire at
08:30) and it **could never settle a past expiration** because
`signal_repricer.reprice_swing` returns `current_underlying=None` for `exp < today` (it skips the
doomed chain fetch). Now gated by the pure `paper_engine.should_settle(exp, today, now_ct)` —
settle at/after **15:00 CT** on the expiry day (4pm ET close) or any later day — with a direct
`paper_engine.underlying_last(client, symbol)` quote fallback when the repricer supplies none
(`run_manage_cycle` gained a `now_ct=None` param for deterministic tests). The **ledger**
(`paper_trader.py`/`trades.db`, the Paper Trades tab) **never auto-closed at all** —
`expire_paper_trade` had ZERO callers — so new `compute.expire_ledger_trades(now_ct=None)`
settles OPEN ledger trades on the SAME `should_settle` gate, wired into
`handlers.run_manage_and_refresh` (the 5-min manage tick + the manual "Run manage cycle" button;
the pre-existing piggyback `refresh_paper_trades` republishes the settled rows). See
[[paper-two-systems-expiration]]. **(2) Action alerts.** A thrice-daily push (Telegram + Discord;
SMS if configured) at **10:00 / 13:00 / 15:00 CT** on trading days summarizing **trades needing
action** — `scheduler.action_alert_due` (once per slot within a 20-min grace, mirrors
`analyze_slot_due`) → `handlers.run_action_alert(bus, slot)` → `compute.collect_action_items`
(four categories: captured signals recommending **CUT/TAKE_PROFIT** via a fresh `reprice_captured`,
**expiring-today** ledger+account trades, **at-risk** rescue tested/critical, account **near
stop/target** [40–50% of max profit, or 150–200% of credit loss]) → `push_notify.send_action_digest`
(new `action_digest_text`/`action_digest_embed`/`action_total`/`action_slot_label`; skips an empty
digest — no "all clear" spam). Cached at `cache:options:action_alert` for inspection. All defensive
+ per-category guarded. Restart `options_svc` to pick both up. options_svc **389** + options-scanner
paper/eod/repricer **71** green; verified live (digest built against real data: 17 captured actions +
1 at-risk). Branch `Using_Highcharts`.). Prior — 2026-07-02 (**Driver risk-sizing fix (RISK_TOO_HIGH) + Sonnet 5 + prompt
caching** — a debugging session on "driver trades logged **Executed** but never showed up."
Root cause: the `/driver` decision-log "Executed N: SYM×q" line is only the **enqueue** of a
`driver_paper_create` command; the real open in `options_svc.compute.open_driver_position` was
**silently rejecting** every $SPX/MU pick with `RISK_TOO_HIGH` (the truth is the account view's
rolling **`last_open_results`**, and the driver DB had NEVER held an $SPX or MU position). The
cause was a **100× units mismatch**: `guardrails.clamp_quantity` (driver_svc) sized affordability
off the scanner's **per-SHARE** `max_loss` (~$7) while `paper_sizing.size_contracts`
(options-scanner) correctly used **per-CONTRACT** dollars (`(width−credit)×100`, ~$705), so the
driver kept approving $SPX/MU whose real per-contract risk ($409–$1,833) blew past the paper
sizer's `config_paper.MAX_RISK_PER_TRADE=$250` → sized to 0. **Fixed:** (1) the guardrail now
evaluates **per-contract dollars** (`guardrails.CONTRACT_MULTIPLIER=100` + `_max_loss_dollars`)
in `clamp_quantity` + the daily-budget accounting; (2) the driver's caps raised
`PER_TRADE_MAX_RISK 300→1500` / `DAILY_RISK_BUDGET 900→4500` (user opted to let $SPX/MU trade);
(3) the paper OPEN path got its own `options_svc.compute._DRIVER_MAX_RISK_PER_TRADE=1500` (passed
explicitly to `size_contracts`) so the user's MANUAL paper account stays at $250. A $SPX
regression test (rejected at $250, opens 2 contracts at $1500) + updated guardrail/e2e/packet
tests pin it; driver_svc **160** + options_svc **334** green. **NOTE:** the legacy daily-loss
halt is still **$250** (`config.RISK_LIMITS`), so with $1,500 trades one losing $SPX can trip the
day's halt (raise it if undesired); the widest $SPX (~$1,833/contract) stays excluded at $1,500.
**Restart options_svc + driver_svc** to pick this up. See
[[driver-executed-but-rejected-risk-too-high]]. **Also this session:** both Claude API call sites
upgraded **Sonnet 4.6 → Sonnet 5** (`claude-sonnet-5`) — the driver decider (via the gitignored
`shared/driver_model.txt` override) + the Gamma Analyze `_ANALYZE_MODEL`; live-probed first
(Sonnet 5 **accepts** `thinking:{"type":"disabled"}`, unlike Fable 5, so no param rework), build
default stays **Opus 4.8**. **Prompt caching** enabled on the driver decision call
(`decider._cached_system` cache-marks the tools+system prefix, 1h TTL to match the 30-min
checkpoint cadence) — currently **inert** (the ~800-token prefix is below Sonnet's 2048-token
cache floor, so nothing is cached or billed extra; engages automatically if the static prefix
grows). Branch `Using_Highcharts`.). Prior — 2026-07-01 (**Reliability remediation** — the technical audit's
[Reliability & Error Handling](docs/audits/2026-07-01-technical-audit.md) pillar (the lowest-scored,
5/10) addressed; theme = *keep the "never raises" defensiveness, add the evidence*. All suites green:
options_svc **333**, driver_svc **157**, sentiment_svc **61**, portfolio_svc **32**, trade_svc **68**,
shared/contracts **42**, scaffold **20**, proxy **63**, webgui **676**, options-scanner paper/scoring
modules **90** (full options-scanner blocked only by the pre-existing intermittent tkinter dashboard
crash; the OPTS agent's clean run was 1186/10-baseline). **R1 (flagship — retires the known days-long
silent-KeyError incident):** `options_svc/handlers.py` now **captures** `open_driver_position`'s result,
logs opened/rejected/error, and surfaces a rolling `last_open_results` (cap 25) on
`cache:options:driver_paper_account` so the /driver log shows per-trade OUTCOMES, not just "enqueued".
**R2 (dead scheduler was invisible):** `_scaffold._supervise_scheduler` restarts a dead scheduler
coroutine (3 s backoff, `max_restarts=10` storm cap → then `alive=False`) and `/health` gains
`scheduler_alive`/`scheduler_restarts`/`scheduler_last_tick_age_s` (the age is "since last (re)start" —
`scheduler_alive` is the load-bearing signal; a service with `scheduler=None` reports alive). **R3
(no persistent logs + silent excepts):** `make_app` installs a per-service `RotatingFileHandler`
(`services/<domain>_svc/logs/<domain>.log`, 10 MB × 5, root logger, **off under pytest**, idempotent);
**19 scheduler + several handler/compute `except Exception: pass`** → `log.exception`. **R4b/R8 (failures
invisible until /status):** the app-wide watcher (`webgui/main.py`, on every page) now alerts (chime +
`⚠` Status nav badge + optional desktop notification, same settings/market-hours gate) on **STALE
scheduled views + down service `/health`** — pure transition-deduped logic in `alerts.py`
(`new_health_alerts`), service-health probe throttled to 30 s (not every 2 s). **R5 (stale trade
commands):** additive **`Command.ts`** (`shared/contracts`) + a 3-min staleness gate rejects stale
`driver_paper_create`/`paper_create` (missing ts → treated fresh, back-compat). **R6 (non-atomic open
→ BP drift):** `paper_account_db.reconcile_buying_power` recomputes reserved BP = Σ open positions'
max_loss (keeps `cash+reserved` invariant), run at options_svc scheduler startup for BOTH the manual +
driver books. **R7 (stand-down reason opaque):** `decider` classifies `no_key`/`api_error`/
`parse_error`/`model` (fail-safe behavior byte-identical — additive), carried through
`_publish_autonomous` onto the decision-log row → the /driver UI shows a red incident chip so a broken
API key looks like an ops incident, not model caution. **R9 (Low):** proxy stops retrying deterministic
4xx (401-refresh + order-POST-no-retry intact) + rotates its INFO log; portfolio SSE gets capped
backoff + logging; the app-wide `_tick` is `guard_async`-wrapped + logs once on a bus outage (not a
traceback every 2 s); EOD archives write via temp-file + `os.replace`. **DEFERRED (flagged):** **R4a**
— a cross-process auto-restart **supervisor daemon** (R2 in-process restart + R4b alerting already cover
visibility + in-process self-heal; a standalone watchdog that auto-restarts dead PROCESSES is new
always-on machinery, offered as an optional follow-up). Remaining audit pillars: **Security** (proxy
wildcard CORS + no-auth order path, Memurai password, dep pinning) + **Code Quality** (god-modules,
`render()` closures, sys.path/collision debt). Prior — 2026-07-01 (**Performance + Architecture remediation** — the technical audit's
[Performance & Speed](docs/audits/2026-07-01-technical-audit.md) + [Scalability & Architecture]
pillars addressed; all suites green: options-scanner **1181** [+11 pre-existing baseline], options_svc
**322**, driver_svc **143**, sentiment_svc **61**, portfolio_svc **29**, shared/bus **20**, scaffold
**8**, webgui **658**). **P3 (command handlers off the event loop):** `services/_scaffold.py`'s
consume loop now dispatches each command via `run_in_executor` (one-at-a-time, read order) so a slow
handler (a ~19 s `sim_fetch`) no longer stalls `/health`, the scheduler, or the queue. **A2 (command-
stream hygiene, `shared/bus/client.py`):** `enqueue_command` XADD is bounded (`_XADD_MAXLEN=1000`,
approximate); a **dead-letter** convention `cmd:{domain}:dead` (Redis list, `Bus.dead_letter`/
`dead_letter_key`) + per-entry decode: a handler that raises → dead-letter + ack (was: logged &
discarded); an undecodable/poison entry → dead-letter + ack + **batch continues** (was: whole batch
failed un-acked into the PEL forever); `Bus.drain_pending` (`XAUTOCLAIM`, min-idle 0) drains a crashed
consumer's stranded PEL to the dead-letter list at startup — **surfaced for review, never silently
lost NOR blindly re-executed** (a stranded `driver_paper_create`/`rescue_apply` re-run could double-
open). **P1/A1 (GEX retention):** `gex_history_db.purge_keep_sessions(keep=5)` now deletes old rows
from BOTH `gex_snapshots` AND `gex_term_snapshots` (the term table previously had NO purge), called
from `compute.collect_gex_snapshots` **at most once per local date** (`_LAST_PURGE_DATE` latch, not
every 2-min tick) — bounds the ~3 GB DB growth while keeping the last 5 sessions so the off-hours
persistence still works. DELETE reuses free pages but doesn't shrink the file: a **one-time manual
`VACUUM`** (`PRAGMA auto_vacuum=INCREMENTAL; VACUUM;`, run offline) is documented to reclaim the 3 GB
— deliberately NOT auto-run (locks the live DB for minutes). **P2 (slim `cache:options:gamma`):**
`compute.gamma_snapshot` now crops every view's per-strike history grid to the ±20-strike display
window (`GAMMA_N_SIDE`, widened for the intraday spot path) BEFORE caching — **flip/walls are still
computed on the FULL grid first** (crop-invariant; verified a far $SPX wall at 3000 survives). Same
key/structure, so the page is unchanged; measured **16.3 MB → 3.07 MB ($SPX), 9.8 MB → 2.97 MB (SPY)**
on a trending day (~1 MB calm). **P5/P6 (webgui off-loop reads):** the big gamma + calc-chain payload
reads now run via `nicegui.run.io_bound` under `guard_async` + an in-flight guard (the cheap `:ver`
version probes stay on-loop; version-gating preserved so the 14 MB isn't fetched every 2 s). **A4
(scheduler concurrency):** `options_svc/scheduler.py` runs the due slot branches concurrently so a slow
15-min rescan can't delay the 2-min GEX collect / 5-min manage (per-branch isolation preserved). **P4
(sentiment cost):** the 120 s refresh is off-hours-gated (`refresh_due`) and the 35-day backfill is now
computed **at most once per session-day** (`_load_snapshots_cached`) with `skip_unchanged` on the
history publish — ~95%+ fewer off-hours proxy calls, RTH cadence unchanged. **P8/P9 (GEX):** sargable
`last_snapshot_age`/`first_snapshot_today` (`ts >= ? AND ts < ?` range) + one reused read-only
connection across the 4 gamma views. **P10 (portfolio):** the 10-min full rebuild is off-hours-gated
(explicit refresh still immediate). **DEFERRED by decision** (user-confirmed): **A5** (per-tab request-id
result keying — a single-user-multi-tab edge case) and **A6** (retire the `sentiment_bridge.json`
dual-write → regime_filter reads Redis; + the ':8100 proxy may be the source repo's binary' version-skew)
— the bridge retirement is a live-scanner-gating migration and the proxy-skew is an ops concern; both
left for a dedicated pass. Reliability + Security + Code-Quality pillars are the remaining audit
follow-ups. Prior — 2026-07-01 (**Calculation-accuracy audit + remediation**: a five-domain
quant audit of the app's math [full reports under [`docs/audits/`](docs/audits/):
[technical audit](docs/audits/2026-07-01-technical-audit.md) +
[calculation-accuracy audit](docs/audits/2026-07-01-calculation-accuracy-audit.md)] found the
money-bearing math (BSM pricing/Greeks/IV solver, expected move, defined-risk trade economics,
buying-power/margin, GEX regime signals, the look-ahead-free factor model) **textbook-correct**,
but flagged a set of standard-conformance + consistency defects, **now FIXED** (all suites green:
options-scanner **1166** [+10 pre-existing baseline fails], options_svc **314**, trade_svc **68**,
sentiment_svc **52**, portfolio-analyzer **198**, portfolio_svc **27**). **Behavior changes to know:**
(1) **RSI + ADX now use Wilder's RMA smoothing** (`shared/analysis_lib/technical.py` +
`trade_svc/compute.py`), not simple rolling means — values now match TOS/TradingView (RSI-14
validated against the StockCharts worked example 70.53/66.32); this shifts the Trade-page momentum
strip + the sentiment intraday-trend needle (correctly). (2) **VWAP is now session-anchored**
(resets each session), not a multi-day cumulative. (3) **Volume-profile value area** now grows
**contiguously from the POC** (standard Market-Profile), not by sorting disjoint high-volume bins.
(4) **Relative Strength** (`technical.calculate_relative_strength` + `analysis_lib/sector_analysis.py`
Holdings "vs Sector (RS)") switched from an unstable return-ratio [sign-inverted in down markets] to
a **parity ratio `100·(1+stock)/(1+bench)`**. (5) **Swing-scanner economics are now
commission-aware**: a new PURE `options-scanner/commissions.py` (reads `config/commissions.toml`,
no `services/` import) folds **round-trip commission** ($0.65/leg × legs × 2) into
`strategy_scanner.payoff_metrics`' `max_profit`/`max_loss`/`capital` [never off an unbounded profit],
so R:R + capital-efficiency + the quality **grade** are net-of-fees — a borderline IC can now flip
Good→Weak. **Driver-facing gap CLOSED:** the live autonomous **driver sizes from the FLAT scanner**
(`cache:options:scan`), not the swing scanner — so rather than mutate the flat scanner's tuned
composite score / sort / paper-BP sizing (all consume the gross `credit`/`max_loss`/`rr_pct`),
`scanner_engine._attach_net_economics` adds **additive** `commission`/`net_credit`/`net_max_loss`/
`net_rr_pct` to every PCS/CCS/IC signal, and the driver's model menu (`driver_svc.compute._menu_item`
+ the decider system prompt) now shows the model the **net** credit/max_loss + commission, so the
driver's perceived edge is net-of-fees while scoring/ranking/sizing + the webgui display stay
untouched (guardrail BP sizing still keys off the raw gross `max_loss` — structural margin, not
commission — by design). Additionally, the **paper engine now debits commission into realized
P&L at close** (`paper_engine.net_realized_pnl` → both close sites; round-trip on a managed
BUY_TO_CLOSE, opening-only on an OTM expiry), reducing both the stored `realized_pnl` and account
cash from the one value in `_close` — so the **driver performance scorecard AND the manual paper
account are net-of-fees** (the rescue-apply close path already did this). (6) **Swing payoff units
normalized to per-CONTRACT dollars (×100)** across all families (`payoff_metrics` native builders
were per-share while credit adapters were ×100 — now consistent; `_normalize_credit` `capital` bug
fixed: `capital = max_loss` for defined-risk credit). (7) **Single risk-free-rate source**
`options_calculator.RISK_FREE_RATE = 0.045` (was 0.045 in the calculator vs **0.04** in the
simulator); **`q = 0` dividend assumption documented** in the BSM docstrings. (8) **Simulator
expiry settlement fixed** from a timezone-naive `hour=15` to **16:00 US/Eastern tz-aware**,
matching the calculator (`options_calculator.expiry_time_to_years`) — the 0DTE bug where 15:30
collapsed an option to intrinsic-only ($0.012 vs the correct $0.090). (9) **Term-structure GEX
×0.01 unit fix** (`gamma_tool.compute_term_grid`) — term cells were **100× the intraday scale**
(per-$1² not per-1%); GEX magnitudes documented as **nearest-expiry-relative** (not a full-surface
SpotGamma replica — sign/flip/walls ARE standard). (10) **Portfolio annualized return** switched to
a **252 trading-day basis** (was calendar 365 while vol used √252 → ~1.45× Sharpe-scale error;
`evaluation.py` now `busday_count`-based). (11) **Factor-model live scorer z-basis** now matches
the fit's **2/98 cross-sectional winsorization** (`swing_model.py`, was a ±3 hard clip → mild tail
miscalibration; ±3 kept only on the thin-snapshot norm fallback). (12) **Two PoP conventions
documented** (calculator = risk-neutral lognormal r-drift; swing = zero-drift normal) — labeled,
not unified. **DEFERRED (require a manual `fit_swing_model.py` refit against live 5-yr proxy data —
documented in-code in `swing_model.py` + the audit doc):** covariance-aware factor weighting [the
univariate signed-IC weighter double-counts the correlated momentum cluster] and regime-gating
`low_vol`'s regime-overfit inverted sign. Reliability/security/perf/architecture findings from the
same audit pass (silent-degradation logging, 3 GB unbounded `gex_history.db`, at-most-once command
streams, proxy wildcard-CORS) are catalogued in the technical-audit doc as OPEN follow-ups. Branch
`Using_Highcharts`. Prior — 2026-06-30 (**Swing Scanner — quality-gated grading**: the multi-strategy
Swing Scanner's **grade now reflects trade QUALITY, not view-fit**. The `score_strategy`
composite is **quality-dominant** (`0.7·quality + 0.3·fit` — fit demoted to a ranking
tiebreaker), and the **grade is capped by per-family HARD GATES** (liquidity / R:R-or-
capital-efficiency / PoP): a trade failing any minimum bar → **Weak** + a **`grade_reason`**
naming the failed dims ("Fails: liquidity, PoP"); pass mins → Good/Marginal; clear the
**excellent** bars on every gated dim + composite ≥78 → **Strong** (genuinely rare). Per-family
bars (credit = high-PoP/low-R:R; long = low-PoP/high-R:R with unbounded-profit auto-passing
reward; naked = capital-efficiency → below Strong by design). Making the **liquidity gate real**
required carrying `bid`/`ask`/`volume`/`oi` onto the normalized legs (`strategy_scanner` +
`scanner_engine.build_iron_condors` now forwards both IC shorts' liquidity). The page shows a
**color-coded Grade** (Strong/Good→green, Marginal→amber, Weak→red) with the reason in a tooltip.
strategy_scanner **26** + strategy_scoring **57** + options_svc + webgui **653** green;
live-verified (SPY/NVDA/IWM: Weak trades carry a liquidity/R:R reason, Strong rare, quality
dominates so a counter-view but structurally-sound trade can still grade Good). Design/plan:
[design](docs/plans/2026-06-30-swing-quality-gated-grading-design.md) /
[plan](docs/plans/2026-06-30-swing-quality-gated-grading.md). Prior — 2026-06-30
(**Multi-strategy Swing Scanner — Phase 1**: the `/options/swing`
page was expanded from a credit-spread-only premium scanner to a **unified single-symbol
multi-strategy scanner** — it builds + ranks candidates across **Directional** (long/naked
call+put), **Spreads** (debit bull-call/bear-put + credit PCS/CCS), and **Neutral** (iron
condor) families on ONE comparable **0–100 Fit+Quality score**. The scanner **infers a market
view** (direction/conviction + IV vol-regime) from the symbol's own technicals + IV and scores
each structure by FIT-to-that-view + STRUCTURAL-QUALITY (because the legacy `scoring.py` is a
premium-seller's model that would rank long calls/debit spreads near zero). Two new PURE engine
modules — `options-scanner/strategy_scanner.py` (normalized-signal builders + a structure-driven
`payoff_metrics`: analytic `unbounded` flag from the call-tail coefficient, breakpoint extrema,
PoP) + `strategy_scoring.py` (`infer_market_view` + `fit_directional`/`fit_vol` + quality
normalizers + `score_strategy`/`score_all`) — feed `compute.swing_scan` (now returns
`{signals, view}` + a `families` arg; derives `atm_iv` decimal from the engine's dollar daily EM;
adapts the existing `screen_spreads`/`build_iron_condors` output into the normalized shape). The
page gains a families multiselect + an inferred-view banner + strategy-agnostic colored columns
(`strategy_table.py`), with legs-based Calculator/Expected-Move handoff for all types (Paper-trade
gated to credit structures). strategy_scanner **18** + strategy_scoring **35** + options_svc **313**
+ webgui **650** green; live-verified end-to-end against the proxy (SPY/NVDA → inferred bearish
view, BEAR_PUT/LONG_PUT correctly ranked top). Phases 2 (condor/butterfly/iron-fly) + 3 (diagonals)
planned. Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-30-multi-strategy-swing-scanner-design.md) /
[plan](docs/plans/2026-06-30-multi-strategy-swing-scanner.md). Prior — 2026-06-30
(**Sentiment "Daily Sentiment & Trend" intraday graphs**: the
`/sentiment` page's collapsed **30-Day History** section — the daily composite-history line
+ 5d/20d rolling-average + velocity/divergence text — is **replaced** by a collapsed
**"Daily Sentiment & Trend"** expander holding **two stacked, value-colorized (green/yellow/red)
2-min intraday graphs**: Daily Market Sentiment (0–10) + Daily Market Trend (0–100). Each is a
Highcharts line colorized by value via `series.zones`/`zoneAxis:"y"` over an **ordinal datetime
x-axis** (collapses overnight session gaps), rolling the **last 5 trading days**. The series is
**recorded going forward** (no backfill) — `sentiment_svc`'s 120 s `refresh()` records one
`(ts, sentiment 0–10, trend 0–100)` point, **RTH-gated** (Mon–Fri 08:30–15:00 CT), into a new
SQLite store (`services/sentiment_svc/intraday_history_db.py`,
`repo_paths.SENTIMENT_INTRADAY_DB = sentiment-dashboard/data/sentiment_intraday.db`; rolling
window = last 5 distinct local dates; one shared connection `check_same_thread=False` serialized
by `handlers._INTRADAY_LOCK` across the multi-worker executor), prunes to 5 days, and publishes
**`cache:sentiment:intraday_history`** (`{"points":[{ts,sentiment,trend},…]}`; additive
`shared/contracts/sentiment.py:IntradayHistory`). The page (`webgui/pages/sentiment.py`) reads
that view in `_read_cache` (it rides the composite version bump — same refresh cycle), paints
both charts in `_apply` via the PURE builders `build_sentiment_intraday_figure` /
`build_trend_intraday_figure` (sentiment bands ≤4.5/≤6.5, trend bands ≤30/≤70, matching the
gauge/`score_to_state` semantics), and **reflows on expand** (a `@guard`-wrapped worker — a chart
built inside a collapsed expander measures 0×0, the documented Simulator-hidden-tab fix).
sentiment_svc **51** + shared/contracts **39** + webgui **617** green; live-verified end-to-end
(restarted service created the DB + recorded a real RTH point → page rendered both colorized
charts, session gap collapsed, no console errors). Built subagent-by-subagent (TDD, two-stage
spec+quality review per task). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-30-sentiment-daily-intraday-graphs-design.md) /
[plan](docs/plans/2026-06-30-sentiment-daily-intraday-graphs.md). Prior — 2026-06-28
(**✅ Tailwind-first UI migration COMPLETE (Phases 0–8) — the
ENTIRE webgui is Tailwind-only**: all NiceGUI component styling now uses **Tailwind utility
classes via `.classes()`** — **zero `.style()`/`:style=` remain anywhere in `webgui/pages`**
(verified by grep + the `test_no_inline_style.py` guard over every page); **607 webgui tests
green; every page live-verified** in the browser preview. The dark-navy theme is a vocabulary of
**Python Tailwind-class-string token constants** in `pages/options/theme.py`
(`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/`BTN_PRIMARY`/`STRATEGY_BTN`/`TXT_*`/`BTN_3D*`)
applied with `.classes(CARD)`; the legacy `DASHBOARD_CSS` was **DELETED** (P4) — `theme.py` is now
**tokens + the one `QUASAR_INTERNAL_CSS` escape hatch** (field/tab/menu internals). Dynamic
data-driven colors are **palette-mapped** to fixed Tailwind classes (per-page local maps where the
palette is page-specific); genuinely-continuous values (e.g. a panel-flex ratio) use a runtime
arbitrary `flex-[…]` class. **Out of scope** (left as-is, by rule): Highcharts option dicts, raw
`ui.html()` HTML-string fragments + their CSS (EOD/Gamma Explain/Analyze), and Quasar `color=`
props. The ONE escape hatch is per-page **Quasar-internal** `ui.add_css` (table/field/tab/menu
internals). Scope **pragmatic**, intent **convert + light polish** (every page kept its existing
look). Built phase-by-phase (menu → each screen by logical group), each phase spec+quality-reviewed
by subagents, browser-gated, and tests-green — see the "UI styling standard — Tailwind-first" +
"App theme — dark-navy" sections below + the
[design doc](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md) /
[plan](docs/plans/2026-06-28-tailwind-first-ui-migration-plan.md) /
[phase2](docs/plans/2026-06-28-tailwind-first-ui-migration-phase2-plan.md) /
[phase3a](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3a-plan.md) /
[phase3b](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3b-plan.md) /
[phase3c](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3c-plan.md) /
[phase4](docs/plans/2026-06-28-tailwind-first-ui-migration-phase4-plan.md) /
[phase5](docs/plans/2026-06-28-tailwind-first-ui-migration-phase5-plan.md). **Phase 0
(token vocabulary) + Phase 1 (nav shell) + Phase 2 (shared `pages/options/*` helpers —
`.style()`-free, dynamic colors palette-mapped) + Phase 3a (the six signal-table screens
— Scanner/Swing/Captured/Paper/Paper-Portfolio/Rescue) + Phase 3b (Calculator + Simulator
on tokens) + Phase 3c (Gamma + Expected-Move; panel flex via a continuous-value runtime
arbitrary class) + Phase 4 (Trade + the LEGACY CLEANUP: `DASHBOARD_CSS` DELETED — `theme.py`
= tokens + `QUASAR_INTERNAL_CSS` only) + Phase 5 (Sentiment + Sector Rotation — the heaviest
~58 `.style()`; local color-class maps, sector-table CSS → Tailwind, auto-refresh recolors
via `remove/add`) DONE — OPTIONS + TRADE + SENTIMENT TAILWIND-ONLY**
— webgui 599 green + live-verified (no class-stacking under auto-refresh; gauges/charts intact). **Phase 3a** removes every `.style()` AND every Vue `:style=` slot binding
from those pages: dynamic Quasar table-cell colors now map to a stamped Tailwind **`:class`**
field from a finite palette (`score_zone_class`/`rec_class`/`pnl_class`/`verdict_class`/
`heat_bg_class`/`cash_class` — exact hexes preserved as `bg-[#..]`/`text-[#..]` arbitrary
classes; the shared `heat_border_class` in `rescue.py` is imported by captured/paper/portfolio
for the at-risk left-border tint, DRY); the 3D gradient buttons (Scanner "Run scan" + Paper
actions) move to shared **`theme.BTN_3D` / `theme.BTN_3D_DANGER`** tokens (Tailwind arbitrary
`bg-[linear-gradient(…)]` + multi-layer `shadow-[…,…]` + `hover:`/`active:` variants, applied
with `color=None`); each page's `ui.add_css` block is slimmed to Quasar-table-internal rules
only (cell `td/th` padding, sticky `thead tr th`, `.q-table__middle` max-height, the scanner
`.q-tab`/`.q-tab__indicator`/`.q-tab--active` chrome). A `test_no_inline_style.py` guard
asserts all six files are `.style(`/`:style=`-free. Live-verified (computed styles): the
gradient/multi-layer-shadow buttons render exactly, tab accents + active underline, score/
heat/rec badge bg colors, cash/P&L text colors, and the 3:2 rescue column split — no console
errors. Next is Phase 3b. Prior — **Validated swing (1–8 wk) evaluation —
Trade page**:
the `/trade` **Position** verdict's hand-weighted swing scoring is **replaced by a
backtested, IC-weighted cross-sectional factor model** (investing/months deferred —
needs point-in-time fundamentals). A new PURE factor library
(`trade-analyzer/src/analysis/factors.py`: 10 causal, sign-corrected, daily-OHLCV
factors + a registry — `mom_12_1`/`mom_6_1`/`pth`/`str_5d`/`vol_adj_mom`/
`trend_quality`/`low_vol`/`rs_spy`/`rs_sector`/`turnover`; no look-ahead — winsorized
**cross-sectionally at scoring**, not per-factor) feeds an OFFLINE harness
(`trade-analyzer/backtest.py` IC/ICIR/quantile-spread/`zscore_by_date`/**signed
IC-weighting**/`walk_forward`/`calibrate` + the orchestrator `fit_swing_model.py`,
**run manually — NEVER in the request path**) that pulls ~78 liquid symbols' 5-yr
daily history, builds a (date,symbol) panel with **20-day forward EXCESS-return-vs-SPY**
labels, and writes the versioned **artifact `trade-analyzer/data/swing_model.json`**
(signed weights + per-factor IC + cross-sectional norm + score→outcome calibration +
walk-forward OOS IC) + a markdown research report (both gitignored under `data/`). A
LIVE scorer (`services/trade_svc/swing_model.py`, on-demand in `analyze()`, defensive →
falls back to legacy/None) z-scores the symbol's current factors **CROSS-SECTIONALLY
against the current universe snapshot** (re-centered to today's regime — the
calibration-consistent basis; the artifact's time-averaged norm is only a thin-snapshot
fallback), **clips z to ±3**, composites with the signed weights, and reads
**BUY/SELL/HOLD off the calibration band** + a percentile
+ expected forward return + beat-SPY hit-rate. Additive optional **`swing_model`** block
on `TradeAnalysis`; the daily `cache:trade:universe_factors` snapshot — built over the
artifact's **`fit_universe`** (~78-name fit cross-section) — is the scoring basis (the
time-averaged norm is the thin-snapshot fallback). The `/trade` Position card shows the validated verdict as the headline +
a calibrated outcome line + a **"Why — validated factors"** evidence expander, with the
**legacy heuristic** verdict tucked into a collapsed expander (Investor + Markov cards
unchanged — the **Markov card still forecasts the legacy technical-momentum score**, a
separate lens, a documented coexistence). **Validated result (current fit):** composite
**OOS IC ≈ +0.037** (5 of 13 walk-forward folds negative — the edge is thin +
regime-dependent); top quintile ≈ **+1.35% / 4 wk at 52% beat-SPY**, bottom ≈
**−0.80% / 43%**; signed weights low_vol **−0.34** (reclaimed with a NEGATIVE weight —
high-vol outperformed in this large-cap bull period), mom_12_1 **+0.21**, mom_6_1
**+0.17**, trend_quality **+0.12**, rs_sector **+0.08**, turnover **+0.07**. **Honest
caveats:** survivorship + non-stationarity; the edge leans on low_vol's inverted sign
reflecting this 5-yr bull-ish regime; **re-run `fit_swing_model.py` periodically**;
regime-conditional weighting (Option C) is the planned next step. See the **"Validated
swing evaluation (Trade page) — DONE (2026-06-28)"** section below + the manuals
(rebuilt). Design/plan:
[design](docs/plans/2026-06-22-swing-validated-evaluation-design.md) /
[plan](docs/plans/2026-06-22-swing-validated-evaluation.md). **Prior — 2026-06-27**
(**Gamma Analyze → live Claude API + infographic +
4×/day auto-run**: the `/options/gamma` **Analyze** button no longer copies a prompt
to a dialog — it now **calls Claude (Sonnet 5, thinking disabled, ~1.5k max-tokens)
via a forced `submit_analysis` tool-use call** and renders the structured reply as a
self-contained dark **infographic** served in a new browser tab (mirrors Explain's
`/options/<view>` raw-`HTMLResponse` route pattern): a **regime banner + bias meter**,
a **per-index card** ($SPX/SPY/QQQ) with a **price-level ladder** (spot vs gamma flip /
call+put walls / expected-move band, with label de-collision) + **metric tiles** +
note + a **per-symbol what-if** (▲ rally / ▼ sell-off / ▬ chop), and a **"Why is this
happening"** section at the bottom. The **Exp. move** tile is a **code-authoritative
1-day EM** (`spot·ATM_IV·√(1/365)` via `compute._session_expected_move`) that overrides
the model's copy — the engine's `calc_expected_move_from_chain` is a 0-DTE
remaining-hours EM that collapses to ~0 off-hours / at the close (the bug that surfaced
SPX EM ≈ 3). It also **auto-runs on a schedule** (`scheduler.analyze_slot_due`: premarket
09:00 ET / ~18 min after open 09:48 ET / midday 12:30 ET / close 15:58 ET, once per
trading day within a 20-min grace) → `handlers.run_scheduled_gamma_analyze` caches each
under its **own slot key** (`cache:options:gamma_analyze_{premarket,open,midday,close}`,
separate from the ad-hoc `cache:options:gamma_analyze` so a scheduled run never
auto-opens a tab); the Gamma page's **Auto briefings** buttons open each via
`/options/analyze?slot=…`. Every failure degrades to a readable HTML page (no chains /
no key / API error / no tool reply); output carries **no disclaimers**. Anthropic key
resolved locally in `compute` (env `ANTHROPIC_API_KEY` → gitignored
`shared/anthropic_key.txt`; options_svc does NOT import driver_svc). See the **"Gamma
Analyze — Claude infographic + auto-run (DONE 2026-06-27)"** section below. **Earlier
this session — EoD report redesign + Scanner/Paper/Driver UX batch**:
the **`/eod` report** was rebuilt around **Daily / Weekly(WTD) / MTD performance per
book** (manual ledger + Driver, separately) + **trade-type breakdowns** (strategy /
0-DTE-Swing / status) + **TOC + collapsible `<details>` nav** (no JS — works in-app and
in the exported files); needs the new additive `compute.driver_account_view()`
**`closed_positions`** field. See the "EOD Report redesign — DONE (2026-06-27)" section
below + [design](docs/plans/2026-06-27-eod-report-redesign-design.md) /
[plan](docs/plans/2026-06-27-eod-report-redesign-plan.md). **Also shipped this session**
(Scanner / Paper Trades / Driver UX batch + denser nav, commit `36bcf40`): Scanner
Calculator-transfer fix (legs were wiped pre-chain-load — stash `pending_legs` +
`load_symbol`) + tab counts/colors + in-app new-signal toast + 3D Run-scan button;
Paper Trades **live unrealized P&L** (reprice open ledger trades via `signal_repricer`,
market-hours gated) + colored/decimal P&L + renamed headers + newest-first sort +
**red Delete buttons** (needed `color=None` so `.pt-danger` beats `bg-primary`) +
descriptive **Analyze popup** + speedometer PoP-fallback + "Current price" label;
Driver **today-only decision log** + colored perf P&L + Bucket/Instrument labels +
sticky headers + the **root-cause fix that the driver never opened a position**
(`open_driver_position` KeyError'd on `'signal_id'` because the driver feeds RAW scanner
signals `type`/`credit`/`id`, not `strategy`/`entry_credit`/`signal_id` — see
[[driver-feeds-raw-scanner-signal-shape]]); nav inter-item spacing halved + all groups
expanded by default. Prior — 2026-06-25 (**Driver isolated paper account + performance
scorecard**: the autonomous Driver now trades into — and measures itself against —
its **own dedicated paper book** (`options-scanner/data/paper_account_driver.db`, new
`repo_paths.DRIVER_PAPER_DB`, $25k start), fully isolated from the user's manual paper
account. This fixes a latent **write/read split** found while investigating "where do
the driver's trades show up?": the Driver **wrote** `paper_create` into System A (the
flat LEDGER `trades.db` — no repricing/auto-manage/account, so its trades were inert
rows and the `source="driver"` tag was silently dropped) but **read** its day-P&L /
$500-target / halt from System B (the user's `paper_account.db` ENGINE account) — so it
measured the **wrong book** and its trades never repriced. Now a new
**`driver_paper_create`** command (`services/options_svc`) opens each guardrail
survivor into `DRIVER_PAPER_DB` via the new `compute.open_driver_position(signal, qty)`
(extracted from `paper_engine.run_entry_cycle`'s per-signal block — simulated fill →
re-size on the ACTUAL fill credit → reserve BP → `insert_position`; the guardrail qty
is a **CEILING**, `min(clamped, sized-on-fill)`; never raises), and the **5-min manage
tick** reprices + auto-exits the driver account on the existing `manage_due` slot in its
**OWN guarded branch** (`compute.run_driver_manage_cycle`) so a driver failure can't
skip the manual refresh. options_svc publishes two new views:
**`cache:options:driver_paper_account`** (snapshot + open positions — **NO rescue
overlay**, that reads the manual book) and **`cache:options:driver_paper_perf`** (a PURE
`driver_perf.build_scorecard`: # trades, open/closed, **win rate**, **profit factor**
[None when no losses yet], avg win/loss, realized/unrealized/total P&L, best/worst,
**P&L by symbol & by strategy**). `driver_svc` rewired: `run_autonomous_cycle` enqueues
`driver_paper_create` (not `paper_create`), reads day-P&L + positions from the **DRIVER**
account (`CACHE_OPT_DRIVER_PAPER`), and attaches the scorecard to the published
**`AutonomousState.perf`** (new additive field); `build_packet`'s open-position
attribution is correct-by-construction (the whole driver DB is the driver's; the dead
`source=="driver"` filter falls back to the full account). The `/driver` **monitor
re-points** its Day-P&L bar / summary / open positions to
`cache:options:driver_paper_account` (was the manual `paper_account`) and gains a
**Performance scorecard card** (headline + quality chips, best/worst, by-symbol /
by-strategy tables) reading `cache:options:driver_paper_perf` (live — refreshes on the
5-min tick, not just the 30-min cycle). **driver_svc must NOT import `paper_engine`**
(it transitively pulls `scoring`/`signal_repricer` → the documented cross-app module
collision) — all engine calls stay in options_svc; driver_svc only enqueues + reads
cache. **Also this session** (supporting fixes, shipped): a **`DRIVER_MODEL`** override
(env → gitignored `shared/driver_model.txt` → default `claude-opus-4-8`) so the decider
runs e.g. `claude-sonnet-5` per-deployment; decision-log timestamps in **CST**
(`to_central`); the Enable/Disable toggle hardened (optimistic state + timeout warning —
the real "switch keeps turning off" cause was driver_svc being DOWN, i.e. no consumer
for the enable command). New: `DRIVER_PAPER_DB`; `open_driver_position` /
`run_driver_manage_cycle` / `driver_account_view` / `driver_account_perf` /
`driver_perf.build_scorecard` (options_svc); `driver_paper_create` / `driver_paper_manage`
/ `driver_paper_reset` commands + the two cache views; `AutonomousState.perf`. **PAPER
ONLY** — `config.PAPER_TRADE` stays True; the driver never flips it. options_svc **285**
+ driver_svc **138** + contracts **35** + webgui **510** green (incl. a Redis-driven e2e
proving a `driver_paper_create` lands ONLY in the driver DB — manual account untouched —
and both views + the scorecard reflect it). Built subagent-by-subagent (TDD, two-stage
spec+quality review per unit). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-25-driver-isolated-paper-account-design.md) /
[plan](docs/plans/2026-06-25-driver-isolated-paper-account-plan.md).)
Prior — 2026-06-24 (**Autonomous Driver — strategy-agnostic Claude
decision layer (level B, paper)**: the `/driver` morning agent's hardcoded
`trade_selector` rule tree (three fixed buckets — the reason only equity trades
ever appeared: the SPX-options + MES-futures branches hit hardcoded gates and the
tree fell through to the one branch that always passed) is **replaced by a
Claude-driven decision layer** that auto-selects + sizes **defined-risk option
credit spreads (PCS/CCS/IC) from the scanner menu** (`cache:options:scan`),
targeting **net $500/day**, in **autonomous paper** mode (NO human approval gate;
`config.PAPER_TRADE` stays True — this service never flips it). Pipeline (all new
in `services/driver_svc`): **`compute.build_packet`** projects the top-N
composite-scored scanner signals into a compact model-facing menu + day-P&L
gap-to-target, keeping a `menu_by_id`→RAW-signal map (real scanner field names:
structure in **`type`**, **`expiration`**, **`pop_pct`** — the plan's guesses were
wrong, caught during the build) → **`decider.decide`** (Claude **Opus 4.8** via the
new `anthropic` dep; a forced `submit_decision` tool-use call; ANY failure — no key
/ API error / malformed output — degrades to **stand-down**, never trades blind;
`import anthropic` is lazy) → **`guardrails.apply_guardrails`** (the **PURE
code-authoritative safety core**: a defined-risk allowlist, a per-trade + daily-
budget **quantity clamp**, and a **halt** at banked-$500 / daily-loss-cap / VIX>25
— the model PROPOSES, code DECIDES; the model never sizes its own risk; hardened
vs NaN/inf) → enqueue the EXISTING `cmd:options` **`paper_create`** per survivor (a
`source="driver"` COPY of the signal + the CLAMPED qty; the enqueue loop is
isolated so a mid-loop failure can't skip the halt-latch/publish). A
**`cache:driver:control`** key is the **master switch + STOP kill-switch** (default
**OFF** — the user explicitly enables); the scheduler fires a cycle at **09:28 +
every 30 min during RTH** (`checkpoint_due`) with a **next-day halt re-arm**
(`should_rearm`), each on the executor + per-branch guarded. The `/driver` page
flips to a **monitor + override** — Enable/Disable, a confirm-gated **STOP**, **Run
now**, a day-P&L-vs-$500 progress bar, open-driver-positions, and a newest-first
**decision-log** audit — reading **`cache:driver:autonomous`** (`AutonomousState`);
the **legacy approval queue + Performance** UI is preserved (gated off while
autonomy is enabled). The decider only PICKS from the scored scanner menu (never
invents strikes), and the legacy `trade_selector` is retained as a degrade path.
**Real `/ES` `/MES` futures options (FOP) were investigated and shelved** — Schwab's
API serves no FOP chains and places only EQUITY/OPTION orders (see
[[schwab-api-instrument-limits]] / the design doc); SPX/NDX index options are the
cash-settled 1256 equivalent if revisited. New contracts `DriverControl` /
`AutonomousState`; new `anthropic` dep + `ANTHROPIC_API_KEY` (env / gitignored
`shared/anthropic_key.txt`). driver_svc **130** + shared/contracts **34** + webgui
**483** green (incl. a Redis-driven e2e proving the model's requested qty=3 **clamps
to 1** through the REAL pipeline, and a banked-$600 cycle latches the kill-switch).
Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-driver-autonomous-claude-decider-design.md) /
[plan](docs/plans/2026-06-24-driver-autonomous-claude-decider-plan.md).)
Prior — 2026-06-24 (**Calculator/Simulator UX batch — symbol tab/Enter
Load, wait overlay, Expiry→all legs, compact leg cells**: four Tier-1 UI changes to
`/options/calculator` + `/options/simulator` (no service/contract changes).
**(1)** The **Symbol** field now fires **Load** (Calculator) / **Fetch snapshot**
(Simulator) on **tab-out (`focusout`) + Enter (`keydown.enter`)** — `focusout` not
`blur` (NiceGUI binds the q-input ROOT where `blur` doesn't bubble) — deduped via the
new PURE `inputs.should_load(current, last_loaded)` so an unchanged symbol doesn't
re-fetch; the **Load/Fetch BUTTON still force-reloads** (bypasses the dedup), and a
`state["loading"]` re-entrancy guard collapses the focusout-then-button-click double
fire. **(2)** A **centered full-screen wait overlay** — new shared
**`pages/options/overlay.py`** `build_loading_overlay()` → a handle with
`.show(msg)`/`.hide()` (a `position:fixed` dimmed backdrop + `ui.spinner`, built once
per render) — shows on **user-initiated** loads (`show_wait=True`), hides on
chain/meta arrival (`_apply_chain`/`_apply_meta`), with a **safety timeout**
(`overlay.LOAD_TIMEOUT_SEC=30s`, shared) that also resets the dedup. The timeout was
**raised 15s→30s after live-measuring the Simulator's `sim_fetch` at ~19s for SPY**
(6870 contracts) — 15s fired before a real snapshot landed, hiding the spinner
prematurely; the overlay's PRIMARY dismissal is data-arrival, so the backstop must
exceed the slowest legitimate fetch. Mount-time auto-loads (persisted-state restore /
cross-page handoff) pass `show_wait=False` (no overlay flash on every navigation).
**(3)** The Calculator's **top-level Expiry propagates to ALL legs** (literal, incl.
calendars — the user's choice) via `leg_editor.apply_expiry` / PURE
`set_legs_expiry(legs, expiry)`, which re-syncs each leg's strike select to the new
expiry; wired on `expiry_sel.on_value_change` → `_on_expiry_change`, **guarded by
`state["applying"]`** so the programmatic expiry sets in `_apply_chain`/`_prefill`
don't fire it, and the editor **`dirty` flag is preserved** so an untouched
single-expiry template still routes through the analytic summary. The **Simulator has
no global expiry** (per-leg only), so this is Calculator-only. **(4)** The shared
**`leg_editor`** leg-table cells are **compact** (a `leg-row` class on each row +
`theme.py` `.leg-row` CSS: `min-height:32px` + trimmed top/bottom AND side padding)
and the **Type** column widened (`w-20`→`w-24`) so **`call`/`put` no longer clip**
(verified: "call" renders 20px in a 58px cell), and the **"Actions" header is
dropped** (an empty `w-10` spacer keeps the trashcan column aligned) — **both pages**
(shared editor). New PURE helpers (`should_load`, `set_legs_expiry`) + the overlay
handle are unit-tested; webgui **460 green**; **verified live** (Calculator: AAPL
tab-out + MSFT Enter load with overlay show/dismiss; Simulator: SPY tab-out → overlay
→ ~19s snapshot → legs populate at 732/731 near spot 733.24, status "SPY spot 733.24 ·
6870 contracts"; compact cells + full "call"/"put" + no "Actions" header on both).
Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-calculator-simulator-ux-changes-design.md) /
[plan](docs/plans/2026-06-24-calculator-simulator-ux-changes-plan.md).)
Prior — 2026-06-24 (**Gamma page overhaul — persistence, fixed strike
window, blended heatmaps**: a batch of `/options/gamma` fixes. **(1) Symbol no
longer reverts to `$SPX` on refresh** — the page reads one shared
`cache:options:gamma`, so the **dropdown syncs to the cached snapshot's symbol on
build**, a repaint **ignores any snapshot whose symbol ≠ the selected one** (so the
service's one-shot `$SPX` startup publish can't clobber it), and **selecting a symbol
auto-refreshes** it (`gamma._set_symbol`/`_on_symbol_change` + the `_maybe_repaint`
guard). **(2) Fixed ±N strike window** (`gamma.strikes_around`, N_SIDE=20) for the
bars **and** heatmap instead of a ±% band, so the candle/cell count — hence size —
stays consistent through the day; the heatmap `rowsize` is the **median** visible-
strike gap (not the min) so mixed-spacing names (QCOM/SPCX: 1.0 strikes among 2.5)
tile densely like `$SPX` (`_strike_step`). **(3) Heatmap cropped to the visible
near-spot window** before building cells — Charm/DEX/Vanna are non-zero across the
whole chain, so this cut ~45k→~2.4k points (~19×). **(4) Off-hours persistence** —
the candles + heatmap stay on the **last session's** data until the **next trading
day's midnight CT**, then clear (Fri persists through the weekend / holidays until
the pre-session midnight): `scheduler.active_session_date()`/`gamma_cleared()` +
**[SUPERSEDED 2026-07-11 — the overnight clear was REMOVED so the charts show pre/post-market; `gamma_cleared` is gone]** +
`gex_history_db.load_date_with_grid(date)`; `compute.gamma_snapshot` returns empty
in the overnight cleared window and loads the **active session date** for the
heatmap (service-side; the DB retains prior rows). **(5) Blended heatmaps** — both
the intraday **and Term** heatmaps render as a smooth **interpolated** image (no
cell borders / separator mesh), a **dark diverging colorscale** (`HEAT_STOPS`: net
≈ 0 fades to **transparent** so the dark page shows through, like the candlestick
chart; strong −/+ glow red/green), a **transparent** chart background, an
**off-white** (`#f5f5f5`) spot line, **no fade** on hover (`states.inactive`/`hover`
disabled), and a **press-and-hold tooltip** — a `chart.events.load` hook
(`_HEAT_PRESS_TOOLTIP_JS`) gates Highcharts' `tooltip.refresh` so the popup shows
**only while the left button is held** (mousedown → show + follow the cursor;
mouseup → hide); plain hover shows nothing. **(6) Term view bugfixes** — re-floats
JSON-stringified strike keys + widens the chain fetch to the **next 5 expirations
regardless of cadence** (`compute._term_chain`/`_count_expirations`, so weekly/
monthly-only names show 5 columns, not 1). **(7)** an off-hours `spot=None` snapshot
no longer 500s the page. webgui 455 + options_svc 256 green; verified live. Branch
`Using_Highcharts`.)
Prior — 2026-06-24 (**Calc "Number of strikes" + Calc/Sim state persistence**:
two changes. **(1)** The Calculator's **Range min/max/%** controls are replaced by a single
**Number of strikes** input (default 24): the P&L grid now draws **±N real chain strikes
around spot** (strictly — a far-OTM leg can fall off; raise N to see it). New pure
`calculator.strikes_window(strikes, spot, n)` (the n strikes ≤spot + n >spot from the
front-expiry call∪put ladder) feeds an explicit **`price_rows`** list into
`compute.calc_compute` → engine `calc_spread_pnl(price_rows=…)` (additive — used verbatim
as the grid rows, else the even-step ±N heuristic fallback). `calc_compute`'s
`range_min/max/pct` params + `symmetric_price_range` are **removed**; the `calc_compute`
handler is `**args`-generic so it needed no change. **(2)** Both `/options/calculator` and
`/options/simulator` now **persist full UI state across navigation** and **auto-refresh on
return** — a single-user module-level snapshot (`_LAST_CALC`/`_LAST_SIM`) captures every
input (symbol/strategy/legs/fields/sliders[/active tab]) on change and restores it on
`render()` under a `restoring` guard (so wiring fires no stray commands); restored legs
ride each page's existing `pending_legs` hook so the post-fetch re-run uses them (an
explicit **Copy-to-Calculator/Simulator handoff still wins** over the snapshot — see
`page_state.pick_seed`). Survives navigation + browser reload; resets on a webgui restart
(like every persisting page). New PURE `webgui/pages/options/page_state.py`
(`snapshot`/`merge_restore`/`pick_seed`). options-scanner 17 + options_svc 249 + webgui
450 green; verified live (Number-of-strikes: AAPL grid = 24 rows = ±12 real strikes
265→322.5; persistence: AAPL+12 / MSFT restored across nav + price auto-refreshed;
service contract via Redis: N=5 → exact ±5 strikes). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-calculator-simulator-state-persistence-design.md) /
[plan](docs/plans/2026-06-24-calculator-simulator-state-persistence.md).)
Prior — 2026-06-24 (**Simulator What-if P/L fix**: the `/options/simulator`
**What-if** payoff had two bugs vs the Calculator for the *same* trade. (1) **Missing
×100 contract multiplier** — the simulator engine prices in **per-share × qty** units,
so a 10-lot spread's curve read **100× too small** (a real ~$14.5k max loss showed as
**−$200**). `compute.sim_run` now scales each `whatif_rows` `theo_price` by
`_CONTRACT_MULT=100` → a **dollar** position value (the Calculator scales by the same
literal 100). (2) **Wrong P/L baseline** — `whatif_pnl` subtracted the position's value
at spot at the **forward** time ("zero at spot"), so the profit side capped at **0** and
the whole curve was off by the credit. It now measures **from entry**: `sim_run` returns
**`whatif_baseline`** = the position's $ value at **spot, NOW** (the entry mark, Δt=0
full DTE), and `whatif_pnl(df, spot, baseline)` / `whatif_figure(..., baseline)` plot
`value(S,t) − baseline` — identical to the Calculator's `entry_credit + value(S,t)`
([options_calculator.py](options-scanner/options_calculator.py) `val += price*q*100;
pnl=entry_credit+val`), so **profit caps at the net credit, loss floors at width−credit**,
and theta now shows as the **Δt** slider moves (the old framing pinned spot to 0,
hiding it). No-baseline `whatif_pnl` keeps the legacy nearest-spot fallback (back-compat
/ pre-restart cached results); the IV-shock + Replay tabs are unchanged. Verified on the
**real** engine (not just the fakes): a 20-wide 10-lot SNDK call credit spread yields
`|max-profit| + |max-loss| = $20,000` (= width×100×qty) with profit=credit and
loss=−(20000−credit). webgui 438 + options_svc 253 green. **Restart `options_svc` +
reload the page** to see it live (the running service/page are stale). Branch
`Using_Highcharts`.)
Prior — 2026-06-24 (**Trade Analyzer theme + Markov near-term fix**: the
`/trade` page now wears the shared dark-navy **"dashboard" theme** (`ui.add_css(
DASHBOARD_CSS)` + `.calc-v2` wrap from `webgui/pages/options/theme.py`; header +
verdict + secondary cards are `calc-card`s, the Analyze button is `cv2-btn-primary`).
**Dead space removed**: the verdict row switched `items-stretch` → **`items-start`**
so the short Position/Investor cards size to content instead of stretching to the tall
Markov card (verified live: 308/276px vs the Markov card's 453px — was ~150-180px of
empty bottom each). **Markov chart fix** for "looks the same for every symbol": the
5/10/20d forecast **converges to the bull-leaning pooled-prior stationary within
~10 days** (only the near term is score-specific), so `trade_svc.compute.build_markov_block`
now emits an **additive** dense **`trajectory`** (`_MK_TRAJECTORY_HORIZONS=[1,2,3,5,10,20]`,
reusing the tested `forecast()`) and `trade.markov_forecast_figure` plots it
(`now→1d→2d→3d→5d→10d→20d`, falling back to `horizons` for back-compat) — the
score-specific early path is now visible (verified live: XOM Strong-Bear opens
red-dominated, INTC Strong-Bull green-dominated, NVDA between). The chart is
dark-themed (transparent bg, light axes, fixed `{value}%` y-axis). **`horizons`
(5/10/20d cards), `drift`, `tilt`, `markov_adjusted_score` are unchanged** — the
trajectory is chart-only, the verdict label/score math untouched. **Tab-out =
Analyze**: a **`focusout`** handler (NOT `blur` — NiceGUI binds to the q-input ROOT
and `blur` doesn't bubble there, same reason `select_all_on_focus` uses `focusin`)
fires Analyze, deduped via `should_request` (collapses the blur-then-click double
fire). **Last analyzed symbol persists** across navigation: the input seeds from the
cached `trade:analysis` result's `symbol`. webgui 435 + trade_svc 41 green; verified
live end-to-end (themed render, dense chart, tab-out analyze, symbol persistence).
Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-trade-analyzer-theme-layout-markov-design.md) /
[plan](docs/plans/2026-06-24-trade-analyzer-theme-layout-markov-plan.md).)
Prior — 2026-06-24 (**App theme rollout + What-if payoff**: the dark-navy
**"dashboard" theme** is now **shared, not Calculator-only** — extracted to
**`webgui/pages/options/theme.py`** (`DASHBOARD_CSS`, scoped under `.calc-v2`) and
injected by **both** the Calculator **and** the Simulator (`ui.add_css(DASHBOARD_CSS)`
+ wrap in `.calc-v2`), so the look never drifts. The **Simulator** gains the navy
gradient, bordered `calc-card`s, the **boxed** Strategy picker, **header-table** legs,
`cv2-btn`/`cv2-btn-primary` buttons, and **dark transparent Quasar tabs** so its
already-dark-transparent Highcharts panels sit on the navy. See the new **"App theme
— dark-navy 'dashboard'"** reference section below (palette + class vocabulary +
how-to-apply) — the single place to look up the theme. The Simulator **What-if** tab
is restyled as a **green/red profit-loss payoff**: `simulator.whatif_figure` plots
`P/L = theo_price(S) − theo_price(spot)` (`whatif_pnl`, **zero at spot**) as a
Highcharts **area** with `threshold:0` + `color`/`negativeColor` + `fillColor`/
`negativeFillColor` (green profit fill+line above the breakeven, red loss below; an
explicit base `color` stops a default-blue base path leaking under the split) + faint
full-height Profit/Loss `plotBands` with labels; `theo_price` is already
sign-weighted by leg side (`aggregate_position` scale `sign*ratio`), so the
subtraction is the holder's P/L and the direction is correct (verified live: a
24-DTE SPY bull put spread loses on the downside, profits on the upside). webgui 430
green. Branch `Using_Highcharts`.)
Prior — 2026-06-23 (**Multi-leg Simulator + Calculator DONE**: the
`/options/simulator` and `/options/calculator` pages now build, price, and analyze
**multi-leg strategies** — verticals (credit *and* debit), condors (iron +
all-same), butterflies (long 1-2-1 + iron), and **calendars/diagonals** (per-leg
expiry) — with **editable legs** and a **copy-legs button both ways**
(Simulator ↔ Calculator); existing singles + PCS/CCS/IC stay. New shared **pure**
`webgui/pages/options/strategies.py` (normalized leg dict + `STRATEGY_TEMPLATES`/
`STRATEGY_GROUPS` + `build_default_legs` + analytic-vs-numeric `summary_code`) and
`webgui/pages/options/leg_editor.py` (one parameterized editable leg-table widget
both pages mount — `state['legs']` is the source of truth so re-renders never lose
edits; each page injects its own `strikes_for`/`expiries_for` + `show_premium`).
Engine `options_simulator/engine.py` gains `Leg.ratio` (+ `Position.from_legs`) so
`aggregate_position` scales each leg by `sign*ratio` (butterfly body = 2×). Calc
engine: `calc_spread_pnl(per_leg_expiry=True)` prices each leg at its own
time-to-expiry (calendars) + new `calc_summary_generic` (numeric max-P/L /
breakevens / PoP off the value-at-front-expiry curve) for butterfly/condor/
calendar/`CUSTOM`; `compute.calc_compute` routes analytic (PCS/CCS/IC/singles) vs
generic and runs the grid to the **front (nearest) leg expiry**. `compute.sim_run`
(per-leg **elapsed** What-if Δt — a deliberate change from absolute-DTE, fixes
calendars) + `compute.sim_replay` are now **multi-leg** (back-compat with the old
single-contract args); `handlers` forward a `legs` arg; `handoff.py` adds the
`simulator`/`calculator_legs` stashes + `send_to_simulator`/`send_to_calculator_legs`.
options-scanner engine+calc + options_svc 252 + webgui 419 green; verified live
(SPY calendar `sim_run`/`sim_replay` 234-bar trace + an iron butterfly with exact
`max_loss=2300`/`breakevens=[727,741]`). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-23-simulator-calculator-multileg-strategies-design.md) /
[plan](docs/plans/2026-06-23-simulator-calculator-multileg-strategies-plan.md).)
Prior — 2026-06-22 (**Markov 2.0 (Trade Analyzer) DONE**: the `/trade`
**Position** verdict gains a probabilistic forward layer — the composite score is
discretized into **5 bands** (edges = the ±40 BUY/SELL cuts + ±15 neutral); a
per-symbol day-to-day transition matrix is **Bayesian-shrunk** toward a pooled
(17-symbol) prior; `P^n` projects **5/10/20-day** band distributions →
P(BUY)/P(SELL)/E[score]. New PURE engine `trade-analyzer/src/analysis/markov.py`
(classify/count/shrink/project/forecast/`drift_tilt`); `trade_svc.compute`
`reconstruct_daily_composite` (a daily-only **"Markov base score"** so history is
reconstructable) + `build_pooled_prior`/`get_prior` (cached
`cache:trade:markov_prior`, lazy daily) + `build_markov_block` wired **defensively**
into `analyze()`; an additive optional `markov` block on the `TradeAnalysis`
contract; a **Markov Forecast card** — the third **equal-width frame in the verdict row**
alongside Position/Investor — (stacked-area band-probability chart + per-horizon
metrics + a bounded **±12pt confidence-weighted drift tilt** surfaced as a
`markov_adjusted_score` Position headline — **verdict label unchanged**). No
feedback by construction (chain on `composite_daily`, tilt on `composite_full`).
trade-analyzer 215 + trade_svc 40 + contracts 26 + webgui 385 green; verified live
(AAPL). Branch `Using_Highcharts`. See "Markov 2.0 (Trade page)" below.)
Prior — 2026-06-21 (**Rescue Tested Trades DONE**: new `/options/rescue`
page + an advisory/one-click-apply rescue feature for tested credit spreads (PCS/CCS/
IC). Hybrid arch ("Approach C"): cheap at-risk detection rides the existing 5-min
manage cycle (tags paper-account rows with `rescue_state`/`heat` + publishes
`cache:options:rescue_summary` for a nav badge); the ranked candidate menu is computed
on-demand via a `rescue` command → `cache:options:rescue:<position_id>`; apply executes
via new paper-engine primitives behind a stale-price guard. New PURE engine
`services/options_svc/rescue.py` (11 candidate builders + risk/context/scoring),
`compute.compute_rescue`, `handlers.rescue`/`rescue_apply` + summary overlay,
`options-scanner/paper_adjust.py` (apply primitives + dispatcher), `paper_account_db`
`position_adjustments` table + `parent_position_id` col, `config/commissions.toml`
(commission source of truth), `RescueAdvisory`/`RescueCandidate` contracts.
shared/contracts 24 + options_svc 226 + webgui 372 green; options-scanner 1056 (12
pre-existing fails). Verified live (real INTC paper positions). Branch
`Using_Highcharts`. See "Rescue tested trades" below.)
Prior — 2026-06-20 (**Replay + Expected Move look-back, DTE-aware**: the
Simulator Replay path and the Expected Move trailing history now size to the
selected contract's **DTE** (Replay tiers 1-min/1d → daily/~½×DTE; EM ≈ **3× DTE**)
with an **auto + manual-override** look-back dropdown on each tab. EM also
**collapses non-trading-day gaps** — renders via `ui.highchart(type="stockChart")`
→ ordinal x-axis + a trading-day-only `em_cone` (reuses `scheduler._HOLIDAYS`) — and
the Replay hover tooltip is capped at 2dp. 356 webgui + 145 options_svc tests green;
verified live. Branch `Using_Highcharts`. See "Simulator Replay tab" + "Expected
Move page".)
Prior — 2026-06-20 (**Simulator Replay tab migrated**: the third legacy
Tk simulator tab (`Replay`) now lives in the 3-tier webgui alongside What-if /
IV-shock — new `compute.sim_replay` + `sim_replay` command +
`cache:options:sim_replay` + a stacked price+5-Greek Highcharts panel with a
client-side scrub cursor. Verified live (SPY 62-bar trace). See "Simulator Replay
tab" below.)
Prior — 2026-06-19 (**charting migrated Plotly/SVG → Highcharts**: every
webgui chart + gauge now renders via `nicegui-highcharts` (`ui.highchart`), not
`ui.plotly`/inline-SVG. Pure builders return Highcharts option dicts; in-place updates
are `el.options = fig; el.update()` (replaces `update_figure`). The Sentiment / Trend /
Trade-detail speedometers are the shared **`webgui/pages/gauge.py`** angular gauge
(painted red→yellow→green rainbow face + needle). Key gotchas now in the "NiceGUI
gotchas" section: the `gauge` type needs NO `extras` (auto-loads via `loadMore`;
`extras=["highcharts-more"]` throws), `solid-gauge`/`heatmap` ARE valid extras, a
dynamically-added chart needs a chart already present at first render (ESM import map),
`bar` axis is reversed by default, and `chart.update()` leaks config across a
series-type switch (recreate on kind-change). `plotly` was never a Python dependency.
322 webgui tests green; verified live. Branch `Using_Highcharts`.
Prior — 2026-06-19 (**intraday Market Trend redesign**: the Sentiment tab's
Market Trend gauge is now a responsive **directional 0–100 score** recomputed every
15 min — Price/MTF 45% + Breadth 25% + Sector 20% + VIX 10%, confidence-weighted,
EMA-smoothed needle, 5-state mapped (range widened to 30–70) onto the bridge with
2-read hysteresis so `regime_filter` is unchanged. New pure
`sentiment-dashboard/scoring/intraday_trend.py` + `services/sentiment_svc/compute.py`
`compute_intraday_trend`/`compute_30d_trend` + 15-min gated/persisted refresh +
additive bridge fields (`trend_score`/`sub_scores`, daily `sma_*` kept). Second gauge
is now the **30-Day structural** trend. See "Intraday Market Trend model" below.
Prior — 2026-06-19 (**perf-fix batch 2**: implemented the remaining High +
all Medium audit items — webgui health-cache + off-thread/de-duped alert watcher +
in-memory `app_settings` cache, Gamma's four polls coalesced into one cheap pipelined
`read_versions`, `Bus` cheap `:ver` version reads + `consume_commands` group-create
once, `technical` EMA/MACD/volume-profile vectorized, `sectors_ref` mtime-cache,
trader-path pooled session, sargable `gex_history_db` today-query. All suites green
(2 pre-existing sentiment-dashboard UI-import fails aside). Prior same-day: **perf-fix
batch 1** + **end-to-end audit pass**: corrected doc drift —
full `config/ports.toml` (memurai + ml_servers + services 8210–8214), the
`pages/options/handoff.py` + `pages/ui_guard.py` shared helpers, the Sentiment
3-column / 2×2-tile layout, the proxy `/pricehistory` 404-flood fix, and paper
auto-manage (no longer "manual-only"); added a **"Performance characteristics &
known hotspots"** section from an efficiency audit. Prior same-day: **System
Status page DONE**: new `/status` pure-webgui
page probes every tier — Memurai PING, schwab-proxy `/health`, the five domain
services' `/health` (:8210–8214), and webgui itself — into an overall up/down
banner + per-component cards, plus a **published-data-freshness** table (each
domain's latest cache version + age, flagging scheduled views gone stale). 304
webgui tests green. See "System Status page (`/status`) — DONE" below.
Prior — 2026-06-18 (**EOD Report page DONE**: new `/eod` + `/eod/detail`
pure-webgui pages aggregate the collected `options:*` + `driver:*` caches into a
**Summary** rollup + **Detailed** report, with a **Generate** button that snapshots
the caches into standalone `summary.html`/`detail.html` archived under
`webgui/data/eod/<date>/` (in-app view + dated archive + `/eod/file` raw serving).
See "EOD Report page (`/eod` + `/eod/detail`) — DONE" below.
Prior: **3-tier migration — all five domains migrated** (Sentiment, Options,
Portfolio, Trade, Driver) — every page reads Redis and the webgui imports only
`nicegui` + `shared.bus` + `shared.contracts`. Remaining: Phase 6 retire-shims
(`regime_filter` reads Redis; drop the bridge dual-write).)

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
| `sentiment-dashboard/` | Market sentiment `scoring/` + `history_backfill` + `live_composite.py` (live intraday composite + bridge payload) + `publish_bridge.py` (headless bridge writer) + bridge + `sectors_ref.py`. | ported to NiceGUI `/sentiment` |
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
2026-07-11; the drawer became an **ICON RAIL** 2026-07-15): the left drawer is a
**FLAT main menu** — one item per group (**Options**, **Market Trend &
Sentiment**, **More**) plus the flat Market Dashboard / Trade / Portfolio /
Driver items — and the active group's **child pages render as a compact TAB
STRIP across the top of the page** (`_NAV_GROUPS` + `_group_children(active)`;
a `ui.tabs` under the header with `.compact-tabs` small padding — q-tab
min-height 30px — clicking a tab navigates; More's strip includes the Settings
children, e.g. User Manuals).

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
(tracked; missing keys regenerate from `DEFAULTS`). The **Settings** page (`/settings`,
`pages/settings.py`) binds the alert toggles/sound/volume/market-hours/min-score +
desktop-notification controls. The drawer is restyled (`.nav-drawer` CSS: active
pill, hover, right-aligned badges, title block). Browsers block autoplay until a
user gesture — clicking any nav link or **Test sound** unlocks it. Design/plan:
[design](docs/plans/2026-06-17-scanner-alerts-settings-badges-design.md) /
[plan](docs/plans/2026-06-17-scanner-alerts-settings-badges-plan.md).

Routes:

| Route | Page | Status |
|-------|------|--------|
| `/` | Options · Scanner (0-4 / 5-15 DTE, two-pane + detail panel; **THREE folder-style SUBTABS since 2026-07-16 — 0-DTE / Swing / Directional**. **Directional** renders the engine's `signals_directional` (single-leg LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT) via the SHARED `strategy_table` builders, scored on **Fit+Quality** (never beside a premium composite — see the Last-updated entry); naked shorts show `Max L = ∞` + an undefined-risk badge and no Paper button. **The tables read `cache:options:scan_day`** (the day union) not `cache:options:scan`, so the day's signals persist to EOD with dropped-out ones **dimmed + frozen + "Dropped HH:MM"** and **no Paper button** (frozen price + verbatim `entry_credit` = a fictional entry); the render is **gated on the envelope's CT date** and surfaces a `truncated` notice. The status bar still reads the LIVE key (the day envelope carries no timestamp/errors) and says "N live signals" so it can't be read as the day count. **"New" = unseen since you last VIEWED the page** (acknowledged only on initial paint), keyed on the engine's unique `id` — this fixed a real bug where the key collapsed to `SPY|PCS|None|None|07/17`; **a webgui restart re-marks everything New** (page-side state, deliberate). ⚠ the nav badge/chime still count credit spreads ONLY — a Fit+Quality score isn't commensurable with the premium composite the min-score alert threshold gates on;  under the main tab strip** (2026-07-11, `main.subtab_slot()` + `.compact-subtabs`; amber/blue tab text kept) with **live signal counts** (`tab_label`); **Run scan is right-aligned flush with the table** (`.scan-panels` drops the q-tab-panel padding); a new qualifying signal pops an **in-app toast** (`fiber_new`, blue-8 — matching the row "new" badge) alongside the chime/desktop notification; **Run scan** is the app's solid 3D button (`color=None` + `.scan-btn`); the per-row **Send to Calculator** now transfers correctly — `_prefill` stashes `pending_legs` + `load_symbol()` so legs apply AFTER the chain loads, instead of being wiped by strike-coercion against an empty chain (see [[calculator-leg-transfer-needs-chain-first]])) | built |
| `/options/matrix` | Matrix (**NEW 2026-07-20** — a **main-menu (left-rail) item directly under the Options group** (`main.OPTIONS_RAIL`, standalone page, NOT an Options tab-strip entry): at-a-glance **sortable grid of every watchlist stock** (~45 symbols = `collection_symbols()` minus `$VIX`), one row/symbol — Ticker/Spot/Day %/**Intraday trend**/**Call+Put flow acceleration**/P/C ratio/Net premium $M/**GEX regime**/# Signals/# Flow alerts/**Buy-Neutral-Sell** flow composite/**Hotness** (default sort, hottest first). Pure Tier-1 reader of **`cache:options:matrix`** (`webgui/pages/options/matrix.py`, version-polls ~2 s, in-place sortable `ui.table`, Tailwind-first colored cells) published by a new `options_svc` aggregator — pure `services/options_svc/matrix.py` (trend/accel/composite/hotness) + `compute.build_matrix` over `gex_history.db` (`load_flow_series` + cheap `latest_flip`) + per-symbol counts from `scan_day` (signals) + the **uncapped** `flow_alert_cooldowns` seen-map (flow alerts — not the 50-capped `flow_alerts` list); built on the 1-min GEX branch + a ~30 s live spot/day% overlay on the header tick. Counts gate on `session_date`. See the 2026-07-20 "Last updated" entry) | built |
| `/options/paper` | Paper Trades (ledger table + shared detail panel. **Live unrealized P&L** — `compute.paper_trades_view(reprice=True)` reprices OPEN ledger trades via `signal_repricer` (per-spread × qty), **market-hours gated**, on reload + the 5-min manage tick; the **P&L** column shows realized (closed) or live unrealized (open), **2-decimals + green/red colored**; **Credit/Risk** show 2-decimals; headers are **Credit / Risk / P&L** (no `$`); **newest-first** default sort; **Delete / Delete-all-closed buttons are red** (needed `color=None` so `.pt-danger` beats Quasar's `bg-primary`); the **Analyze** button pops a **descriptive dialog** (verdict + rationale + unrealized P&L / % / current price / DTE / target / breakeven + close X) — `compute.analyze_paper` enriched with `rationale` + `metrics`; row-click analyses update the detail panel silently. Detail panel: the **speedometer falls back to PoP** for paper trades (was stuck at 0 — no stored composite score) and the "Underlying" label is now **"Current price"**) | built |
| `/options/captured` | Captured Signals | built |
| `/options/portfolio` | Paper Portfolio (paper account) | built |
| `/options/calculator` | Calculator (summary tiles + P&L heatmap — grid rows = **±N real chain strikes around spot** via the **Number of strikes** input (default 24, strictly around spot; `strikes_window`→`price_rows`); **intraday time-to-expiry** — the grid's first column is **"Now"** (current mark-to-market value, priced at calendar hours-to-4pm-ET /365) and the last is **"Exp"** (expiration payoff), fixing 0DTE which previously showed only the payoff everywhere; summary tiles + PoP also use the intraday "Now" T (was an `or 1/365` clamp that over-priced 0DTE ~20×); the **IV** button **implies IV from the traded contract's mark** ThinkorSwim-style via a `calc_iv` command → `cache:options:calc_iv` (`compute.calc_iv` → engine `implied_vol` bisection), falling back to ATM chain `volatility` pre-strike-pick; **multi-leg strategy builder** — a Strategy dropdown (singles + verticals credit/debit + condors iron/all-same + butterflies long/iron + calendars/diagonals) over the shared **editable leg-editor** (`leg_editor.py`: per-leg kind/side/strike/expiry/qty + Add/Remove), per-leg expiry so **calendars** price each leg at its own T, a **generic-numeric summary** for non-PCS/CCS/IC structures, and a **Copy to Simulator** button; **persists full UI state across navigation** (symbol/strategy/legs/fields/Number-of-strikes) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **Loads on tab-out (`focusout`) / Enter** (deduped via `inputs.should_load`; the Load button still force-reloads) with a **centered full-screen wait overlay** (`overlay.py`, `LOAD_TIMEOUT_SEC=30s` backstop) until the chain lands; the **top-level Expiry propagates to all legs** (`leg_editor.apply_expiry`, re-syncs strikes); **compact leg cells** (`leg-row`) + the **"Actions" header dropped**; **Send-to-Calculator from the Scanner now lands correctly** — `_prefill` stashes `pending_legs` + `load_symbol()` so the legs apply once the chain is loaded (applying them first wiped every strike via the leg-editor's strike-coercion — see [[calculator-leg-transfer-needs-chain-first]])) | built |
| `/options/swing` | Swing Scanner (**multi-strategy**, single-symbol: builds + ranks candidates across **Directional** (long/naked call+put), **Spreads** (debit bull-call/bear-put + credit PCS/CCS), and **Neutral** (iron condor) families on ONE unified **0–100 Fit+Quality** score; **Diagonals** are a later phase. The scanner **infers a market view** (direction/conviction + IV vol-regime) from the symbol's technicals + IV and ranks each structure by FIT to that view + STRUCTURAL QUALITY — so a long call and a put-credit-spread are comparable. A **Strategy-families multiselect** (default all; empty ⇒ all) + an inferred-**view banner** + strategy-agnostic columns (Strategy/Bias/Legs/Debit-Credit/Max P/Max L/R:R/PoP/BE/Score/Grade, colored by score+bias; the **Grade is quality-gated** — color-coded green/amber/red with a `grade_reason` tooltip, driven by structural quality + per-family hard gates, NOT view-fit). Per-row **Send to Calculator / Expected Move** work for ALL types via the canonical `legs`; **Send to Paper** works for credit structures (PCS/CCS/IC) **AND defined-risk debit structures (LONG_CALL/LONG_PUT/BULL_CALL/BEAR_PUT)** as of 2026-07-13 (naked shorts excluded — undefined risk; see the "Last updated" entry). See the "Multi-strategy Swing Scanner" section below) | built |
| `/options/gamma` | Gamma (GEX/Charm/DEX/Vanna bars + flip/**single Call+Put walls** + intraday heatmap; **fixed ±20-strike window** around spot for bars+heatmap (`strikes_around`, consistent candle/cell size all day; heatmap `rowsize`=median gap; heatmap cropped to the window); **blended interpolated heatmaps** (intraday **and Term**) — smooth image, no lines, dark `HEAT_STOPS` colorscale (zero→transparent like the candlestick chart), transparent bg, off-white spot line, no fade, **press-and-hold tooltip** (`_HEAT_PRESS_TOOLTIP_JS`); bar/heatmap **width split grows with session** snapshot count; **flicker-free** in-place Highcharts updates; **symbol is a dropdown** — default `$SPX`, populated from the collected universe (watchlist minus `$VIX`) via `cache:options:gamma_symbols`, **syncs to the cached symbol on build + selecting auto-refreshes + repaints ignore foreign-symbol snapshots** (no revert to `$SPX`); Term shows the **next 5 expirations regardless of cadence** (`_term_chain`); **pre/post-market persistence (2026-07-11)** — the charts show the most-recent-available session 24/7 with NO overnight blanking: the by-strike bars come from the live chain (which returns data off-hours) and the heatmap from `active_session_date` (the PRIOR session premarket, flipping to today once the 08:00 CT collection starts) + `load_date_with_grid`; off-hours `spot=None` degrades gracefully; a **Flow** view (inserted before Term) charts the symbol's intraday **price** + daily-cumulative **call/put premium ($M)** + a **net-premium (call−put)** signed panel from the snapshot's `flow` series (`flow_figure`/`flow_summary_text`; premium is mid-based, unsigned, forward-only); Explain works per-selected-symbol; **Analyze** calls Claude (forced `submit_analysis` tool) and opens an **infographic** tab — regime + bias gauge, per-index price-level ladder + tiles + **what-if** (rally/sell-off/chop), bottom **"Why is this happening"**; **code-authoritative 1-day Exp. move**; also **auto-runs 4×/day** (premarket / ~18 min after open / midday / close) into per-slot keys with **Auto briefings** buttons + a **History picker** (date + slot dropdown → a report regenerated from the persisted briefing history at `/options/gamma-history`) — see the "Gamma Analyze" section below) | built |
| `/options/simulator` | Simulator (**Replay / What-if / IV-shock as SUBTABS under the main strip** + **Controls+Strategy merged side-by-side in one card** (2026-07-11); **multi-leg strategy builder** — a Strategy dropdown over the shared **editable leg-editor** (`leg_editor.py`) replaces the old single-contract selector — driving all three legacy tabs: **Replay** (re-prices the **netted** position along the underlying's recent path → stacked price + 5-Greek panels over a gap-compressed integer x-axis w/ a client-side scrub cursor) + What-if (a **dollar profit/loss payoff from entry**: P/L = position value (×100 contract multiplier) minus the **entry mark** (`whatif_baseline` = value at spot *now*) — so profit caps at the net credit, loss floors at width−credit, **matching the Calculator** — with a green profit fill above / red loss fill below breakeven (area `threshold:0` + `color`/`negativeColor`) + faint Profit/Loss washes + labels; Δt is **elapsed** days from now, per-leg decay → **calendars** correct, theta visible as Δt slides) + IV-shock; **Copy to Calculator** button; **dark-navy dashboard theme** via shared `theme.py`; **persists full UI state across navigation** (symbol/strategy/legs/sliders/active tab) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **Fetches the snapshot on tab-out (`focusout`) / Enter** (deduped) with the same **centered wait overlay** (`overlay.py`) until the meta lands; **compact leg cells** + no "Actions" header (shared `leg_editor`)) | built |
| `/options/expected-move` | Expected Move (candlestick price history (6-mo daily) + forward **ATM-IV expected-move cone** to the option's expiration (green/red dashed, √-time fan) + leg **strike lines** (short solid / long dashed, put/call colored) + axis **crosshair** w/ Date(X)+Price(Y) label boxes; opened in a **new browser tab** via stash-handoff from Scanner/Paper/Captured/Calculator, or standalone w/ symbol+expiry input) | built |
| `/options/rescue` | Rescue (last tab of the Options strip; bare dense table, no wrapper cards since 2026-07-12; at-risk credit spreads (PCS/CCS/IC) → **at-risk table** (paper+captured, heat-colored) → select a position → ranked **commission-aware adjustment menu**: close / partial-close / narrow / convert-IC / butterfly / roll-down/out/down-out / broken-wing / inverted / futures-hedge; each card shows gross/commission/net + metrics + legs + rationale + strategic context + warnings + score; execute cards have **Apply → confirm → `rescue_apply`** behind a stale-price guard, advisory cards show "manual"; nav badge from `cache:options:rescue_summary`) | built |
| `/sentiment` | Sentiment — nav group **Market Trend & Sentiment** since 2026-07-11 (two-column top: **dual** Sentiment gauges (Today + 30-Day Avg) + **dual** Market Trend gauges (Today live-intraday + 30-Day structural — directional 0–100 score, 15-min cadence). **The Today trend gauge's state label + regime badge now show the FIVE-STATE (direction × aggression) vocabulary** — short labels **Bull / Weak Bull / Neutral / Resilient / Bear**, badge label+description e.g. "Lack of Bearishness — Refuses to drop, puts cheap/undefended — favor PCS" — and the press-and-hold **TREND DETAIL popup gained a "Why" evidence section** (direction/effort/skew/flow/session/rejection/profile/order-flow/option-flow/aggression lines). The **0–100 needle is unchanged** (still the direction score); the **30-Day structural gauge deliberately KEEPS the old band vocabulary** (structural read = no aggression axis), so the panel carries both. See the root five-state entry above. / component table; traffic-light tiles; collapsed **"Daily Sentiment & Trend"** expander = two value-colorized (green/yellow/red) **2-min intraday graphs** (Daily Market Sentiment 0–10 + Daily Market Trend 0–100), rolling **last 5 trading days**, session gaps collapsed, **recorded going forward** by `sentiment_svc` (RTH-gated) into `SENTIMENT_INTRADAY_DB` → `cache:sentiment:intraday_history` (replaced the old 30-day-history line + rolling-avg/velocity/divergence text) — **expanded by default since 2026-07-12**; bottom status bar; **persists across navigation**; **server-side 120s auto-refresh + bridge publish, tab-independent**. **Since 2026-07-12** the Sector & Industry table, Sector Rotation, and the RRG chart are SEPARATE tabs (below) — this page still reads `cache:sentiment:sectors` only to fill the Components popup's Rotation/Sector-Value cells) | built |
| `/sentiment/sectors` | Sector & Industry (NEW tab 2026-07-12, `pages.sentiment_sectors`, inserted between Sentiment and Sector Rotation): the **Sector & Industry Performance** table lifted out of `/sentiment` — Day/Week/Month %, P/C, RRG quadrant, rotation banner, cap-weighted summary line, **expandable industries w/ P/C+RRG**; Refresh / Expand All / Collapse All. Tier-3 reader of `cache:sentiment:sectors`; **reuses the PURE builders from `pages.sentiment`** (`sector_table_rows`/`sector_summary`/`rotation_banner`/`industry_rows` + color helpers) so the display logic + its tests stay single-source) | built |
| `/sentiment/rotation` | Sector Rotation (RRG-vs-SPY assessment: Risk-ON/OFF headline + spread; **top row** = quadrant-map table (left) + tight ROTATING FROM/INTO w/ S&P weights (right). **Since 2026-07-12 the RRG CHART moved to its own `/sentiment/rrg` tab** — this page is now the headline + quadrant map + rotating-from/into only; reuses `sector_rotation_assessment`; cached, **manual Refresh only**) | built |
| `/sentiment/rrg` | RRG (NEW tab 2026-07-12, `pages.sentiment_rrg`, last tab after Sector Rotation): the **full-width RRG** chart lifted out of `/sentiment/rotation` — Risk-ON/OFF headline for context + per-sector "meteor tails" (engine `assess_sector` retains a `tail` of `TAIL_LENGTH=12` RS-Ratio/RS-Mom points sampled every `TAIL_STRIDE=2` days; **one spline series per sector** = faded trail line + single bright head dot) with native Highcharts hover-isolation (`plotOptions.series.states.inactive`). Tier-3 reader of `cache:sentiment:rotation`; **reuses `rrg_scatter_figure`/`headline_parts` from `pages.sentiment_rotation`**; cached, **manual Refresh only**) | built |
| `/trade` | Trade Analyzer (nav label since 2026-07-11; on-demand single-symbol analysis: **Position (1–8wk)** + **Investor (months+)** Buy/Hold/Sell verdicts w/ score + top reasons + hard gates + expandable factor breakdown. The **Position** verdict is now a **backtested, IC-weighted cross-sectional factor model** (`swing_model.json` artifact → live `swing_model.py` scorer): the headline is the **validated** BUY/SELL/HOLD off a **calibration band** + an outcome line (percentile · expected fwd return / horizon · beat-SPY hit-rate) + a **"Why — validated factors"** evidence expander (per-factor z/weight/contribution/IC + model version & OOS IC), with the **legacy heuristic** verdict tucked into a collapsed expander (Investor unchanged); **MTF EMA alignment** (per-timeframe); momentum strip (RSI/ADX/MACD/VWAP/RelVol); sector strength; **Fundamentals card** (P/E/PEG/growth/ROE/margins via proxy `/instruments`); **Markov Forecast card** (third **equal-width frame in the verdict row**, alongside Position + Investor: 5-band composite-score Markov chain → stacked-area band-probability forecast + P(BUY)/P(SELL)/E[score] at 5/10/20d + a bounded confidence-weighted drift-tilt `markov_adjusted_score` headline, verdict label unchanged; **chart plots the dense near-term `trajectory` now/1/2/3/5/10/20d** so it differs by score — the 5/10/20d tail converges to the bull-leaning prior stationary; chart is dark-navy themed); **dark-navy "dashboard" theme** (`.calc-v2` via shared `theme.py`, `items-start` compact cards); **tab-out (`focusout`) = Analyze** (deduped); **persists last analyzed symbol** + analysis across nav) | built |
| `/driver` | Claude Trades (nav label since 2026-07-11; **autonomous monitor + override** [level B]: a **Claude decision layer** (Opus 4.8 default; `DRIVER_MODEL` env / `shared/driver_model.txt` override → e.g. Sonnet 5) auto-selects/sizes **defined-risk option spreads (PCS/CCS/IC) from the scanner** (`cache:options:scan`) toward **net $500/day** in **paper**, gated by a **`cache:driver:control`** master switch + confirm-gated **STOP** kill-switch; the page shows day-P&L-vs-$500 progress, open-driver-positions, a newest-first **decision-log** audit (`cache:driver:autonomous`, times in **CST**), and a **Performance scorecard** (win-rate / profit-factor / avg win-loss / P&L by symbol & strategy — `cache:options:driver_paper_perf`), all reading the Driver's **own isolated paper book** (`cache:options:driver_paper_account`, separate from the manual account), with **Enable/Disable** + **Run now**; 09:28-ET morning + 30-min autonomous **entry-window** checkpoints (**09:45–15:30 ET** — the open's first ~15 min skipped so the post-open structure is readable, and **no NEW entries in the last 30 min before the close**; management/exits are unaffected, on options_svc's separate 5-min manage cycle) run `build_packet`→`decider.decide`→**`guardrails.apply_guardrails`** (PURE code clamps size + halts at banked-$500/loss-cap/VIX — the model never sizes its own risk)→`cmd:options` **`driver_paper_create`** (opens into the dedicated `paper_account_driver.db`, repriced + auto-exited on the 5-min manage tick — fully separate from the user's manual paper trades). A **Performance** view shows the driver's **closed trades + realized P&L** from its isolated paper account (`cache:options:driver_paper_account['closed_positions']` — reader-friendly columns Closed/Symbol/Strategy/Qty/Exit-reason/Realized-P&L, colored, newest-first, updated every 5-min manage cycle + the 2s version-poll; a **Refresh** button forces a `driver_paper_manage` reprice). **The legacy morning-agent order-approval queue + its `claude-driver` engine were REMOVED (2026-07-08)** — the page is now purely the autonomous monitor + this Performance view. Orders simulated (`PAPER_TRADE=True`). **Root-cause fix (2026-06-27): the driver had NEVER opened a position** — `compute.open_driver_position` read `signal_id`/`strategy`/`entry_credit` but the driver feeds RAW scanner signals keyed `id`/`type`/`credit`, so every open `KeyError`'d on `'signal_id'` and the defensive `try/except` swallowed it to `status=error`; the decision log showed "executed" (only the ENQUEUE) while the account stayed empty. Fixed by normalizing the signal shape — open positions now appear + the scorecard P&L populates. See [[driver-feeds-raw-scanner-signal-shape]]. **Second root-cause fix (2026-07-02): $SPX/MU logged "Executed" but never opened** — a **100× units mismatch**: `guardrails.clamp_quantity` sized affordability off the scanner's **PER-SHARE** `max_loss` (~$7) while the paper account's `size_contracts` correctly used **per-CONTRACT** dollars (`(width−credit)×100`, ~$705), so the driver kept proposing $SPX/MU whose real per-contract risk ($409–$1,833) exceeded the paper sizer's $250 cap → `RISK_TOO_HIGH` → **silently rejected** (the "Executed" in the log is only the ENQUEUE; the true outcome is in the account view's `last_open_results`, cap 25). Fixed: the guardrail evaluates **per-contract dollars** (`CONTRACT_MULTIPLIER`); the driver's caps raised to **$1,500/$4,500** and the paper open path given its own **`_DRIVER_MAX_RISK_PER_TRADE=$1,500`** (manual account unchanged at $250) — $SPX/MU now open. See [[driver-executed-but-rejected-risk-too-high]]. **Market-context block (2026-07-08):** the decider's
packet now carries an additive **`market_read`** — per-index gamma **flip/walls/what-if** from the
freshest `gamma_analyze` briefing + a **live spot** (spot-vs-flip **posture**), dashboard **breadth +
risk-on/off**, and the **sentiment 0-10 score** — as **reasoning context only** (never filters the
menu; `guardrails.py` untouched — the wall-aware gate is deferred). Its one-line summary shows on each
decision-log row) | built |
| `/settings` | Settings (GUI prefs via `app_settings`: scanner **audio alert** on/off + sound + volume, only-during-market-hours, min-score-to-alert; desktop-notification toggle + permission grant + Test sound; ticker toggle/speed; **Appearance** — edits every `config/theme.toml` knob in-app (7 sections: palette / semantic / 3D buttons / gauge / charts / typography / menu; color pickers + text inputs, `theme.knob_label` humanized labels) with **Save** (comment-preserving `theme.save_theme_values`), **Save & restart web GUI** (reuses the Status page's windowless self-restart), and a confirm-gated **Reset to defaults**; **API usage** (2026-07-13) — outbound Schwab API-call counts Today / last 7 / last 30 days, read off-thread from the proxy's `GET /stats/api_calls`, **plus Claude (Anthropic) call counts** from the cross-tier `shared/anthropic_counter.py` store (`shared/data/anthropic_call_counts.db`, WAL — recorded immediately before every `messages.create` at the three call sites: driver decider / Gamma Analyze / market-ticker summary; services need a restart to start counting) (counted per actual HTTP request at the marketdata rate-limit chokepoint + the trader loop → per-day rows in `schwab-proxy/data/api_call_counts.db`, forward-only; requires a proxy restart to start counting); **Maintenance** (2026-07-13) — a confirm-gated **Vacuum GEX history DB** button (optional purge-first switch) that runs `tools/vacuum_gex.py` as a subprocess off-thread and prints the before→after size — the tool still refuses while the collector is active) | built |
| `/portfolio` | Portfolio (3-tier, `services/portfolio_svc` :8212: **Holdings / Sectors / Performance** tabs over the portfolio model — sector breakdown, vs-sector RS, since-purchase excess, benchmark over/under-weight, tailwind; **Performance** scorecard (return/capital/risk/entry grades + composite + ann. return + drawdown) with a per-position **advisory suggestions** detail pane; **live-streaming P&L** via the service's proxy SSE consumer republishing each tick; proxy/stream status bar; persists across nav) | built |
| `/eod` · `/eod/detail` | EOD Report (pure-webgui aggregator over `options:*` + `driver:*` caches. **Summary** = headline tiles + a **verbose Daily / Weekly(WTD) / MTD performance** block **per book** — the manual paper **ledger** (`options:paper_trades`) and the **Driver** account (`options:driver_paper_account`, incl. its new `closed_positions`) shown separately (realized P&L bucketed by **exit** date; opened/credit by **entry** date; a per-book now-line = equity/session-P&L/open-unrealized/open-count). **Detailed** = the same performance + **trade-type breakdowns** (by **strategy** PCS/CCS/IC, by **0-DTE/Swing**, by **status** Open/Closed/Expired) for each book + full trade/scanner/captured/driver tables. **Navigation**: a jump-link **TOC** + every section in a native **`<details>`** (collapsible, **no JS** — works in-app AND in the exported files). **Generate** snapshots the caches → standalone `summary.html` + `detail.html` archived under `webgui/data/eod/<date>/`; `/eod/file` serves them raw. Pure builders (`normalize_trades`/`period_buckets`/`breakdown_rows`/`performance_table_html`/`breakdown_table_html`/`toc`/`details_section`) unit-tested. Realized reads `$0`/`—` until trades close — by design, not a bug) | built |
| `/market` | Market Dashboard (3-tier, `services/market_svc` :8215: a live grid of ~48 macro tickers from `symbol_categories.csv`, grouped into a **framed panel per category** laid out macro→tape→rotation (Volatility/Options-Sentiment/Internals/Currency · Cash-Index/Futures/Broad-ETF/**Magnificent-7** · Sector/Thematic/Factor/Fixed-Income/Crypto/Countries). Each **tile** shows symbol + description (hover tooltip) + last + net/%-change on a **semantic risk-on/off colored background** (green risk-on / red risk-off / grey no-data, intensity by magnitude) — **polarity-aware** (VIX/SKEW/put-call/TLT/UUP shade RED on up-moves). The **Magnificent 7** frame leads with a **composite `MAG7` tile** = the equal-weighted avg day %-move of NVDA/MSFT/GOOGL/AMZN/META/AAPL/TSLA + a breadth subline (e.g. "3/7 up"), colored by the avg (a new `kind="basket"` tile whose members are also its 7 constituent tiles). `market_svc` polls the proxy's raw `/quotes` on a **~2 s RTH cadence** (5 s off-hours — futures trade ~24h so off-hours stays snappy), normalizes change across INDEX/EQUITY/FUTURE, computes the `$ADVN-$DECN` breadth spread + the `MAG7` basket, and reads the app's own cap-weighted put/call from `cache:sentiment:composite` → publishes `cache:market:dashboard`; the page version-polls + **updates tiles in place** (no per-tick rebuild). **CSV→Schwab symbol map** handles the translations (`SPX`→`$SPX`, `VIX`→`$VIX`, `/ES[U26]`→`/ESU26`) + **equivalents for symbols Schwab can't quote** (`$DXY`→`UUP`; `$PCALL`/`$PCSP`→the sentiment cap-weighted P/C tile). See the "Market Dashboard" section below) | built |
| `/status` | System Status (pure-webgui health board: overall up/down banner + per-component cards probing **Memurai** PING, **schwab-proxy** `/health`, **Schwab Authorization** (OAuth token state, with an **Authorize** button → proxy `/auth`), the **six domain services** `/health` (incl. `market_svc` :8215), and **webgui** itself; plus a **published-data-freshness** table — each domain's cache version + age (incl. `market:dashboard`), flagging stale scheduled views; a **Restart button on every component card** (proxy + the six services + Memurai + the webgui itself, shown up or down) — proxy/services/webgui relaunch **windowlessly** via `tools\restart_one.bat` (`CREATE_NO_WINDOW` → hidden `pythonw`, logs to `logs\`), Memurai via `Restart-Service`; the auth card shows **Authorize** instead; off-thread sweep, auto-refresh 15 s + manual) | built |
| `/terminate` | Terminate (guarded "stop the whole local stack" page: red **Stop all services** button behind a confirm dialog → spawns `stop_all.bat` detached via `cmd /c start`, which kills the proxy + 6 services + this web app by listening port; **Memurai is left running**; the page goes unresponsive after confirm, by design) | built |

**Market Dashboard (`/market`) — DONE (2026-07-07).** A new **More → Market Dashboard**
page streaming a live grid of ~48 macro tickers (from `symbol_categories.csv`), grouped
into a **framed panel per category** and colored by **semantic risk-on/off market
condition**. Sixth Tier-2 service. Pieces:
- **New service `services/market_svc` (:8215, read-only).** A scheduler polls the proxy's
  **raw `/quotes`** endpoint (not `SchwabProxyClient.get_quotes`, which discards
  `assetMainType`/`futurePercentChange`) for all real symbols in ONE batched call on a
  **~2 s RTH cadence** (`scheduler.poll_interval`, 5 s off-hours/weekends/holidays — NOT
  throttled hard because the equity-index futures trade ~24h and are the main off-hours
  mover; the shared `_HOLIDAYS` gate drives it), normalizes change across INDEX/EQUITY/FUTURE,
  computes the `$ADVN-$DECN` breadth spread, reads the app's own cap-weighted put/call, derives a per-tile
  `color_state`, and publishes **`cache:market:dashboard`** (`skip_unchanged=True`, so no
  repaint on byte-identical ticks). No command handler — the page only reads.
- **PURE modules.** `symbols.py` = the **CSV→Schwab symbol map** (single source of truth):
  63 tiles with per-symbol **polarity** (`normal` up=risk-on / `inverted` up=risk-off) +
  `kind` (`quote`/`spread`/`external`), encoding the translations (`SPX`→`$SPX`, `VIX`→
  `$VIX`, `SKEW`→`$SKEW`, `/ES[U26]`→`/ESU26`) and the **equivalents for symbols Schwab
  can't quote** (`$DXY`→**`UUP`**; `$PCALL`+`$PCSP`→one **"Put/Call"** tile
  fed from `cache:sentiment:composite` → `live.sector_pcr`). `classify.py` = pure
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
  composite ≥`STRONG_MIN`78 → **Strong** (genuinely rare). Bars are per-family (credit = high
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
+ `take_pending_simulator`/`take_pending_calculator_legs`); engine-free),
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
  documents. **JIT gotcha (2026-07-09):** the bundled Tailwind browser JIT does
  NOT reliably generate arbitrary classes containing `var(...)` **or `rgba(...)`**
  (plain-hex arbitraries are fine) — the nav pill's old `bg-[var(--q-primary)]`
  silently produced no rule; it is now a plain `.nav-active` rule in `_NAV_CSS`
  with a **hardcoded rgba wash**, so it does **not** follow the `accent` knob (nor
  do the tab-strip fills or the active icon accent). Changing `accent` moves the
  Quasar controls only; to move the nav accents, edit `main._NAV_CSS` as well.
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
  trading_days_only=True)`). **stockChart + in-place updates DON'T MIX (2026-07-06,
  cost: the frozen sentiment intraday graphs):** Highstock's `chart.update(fullOptions)`
  throws in the stock module (`Cannot read properties of undefined (reading 'enabled')`),
  so a `type="stockChart"` element updated via `el.options = ...; el.update()` silently
  FREEZES at whatever it first rendered — use a PLAIN chart for any live-updating element
  (pack time gaps with a synthetic category axis instead; `xAxis.breaks` is no substitute
  — it renders zero ticks). A `ui.highchart` added DYNAMICALLY on a page
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

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (772 green as of
this writing). TDD pure functions; smoke-verify `render()` with a screenshot.
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
**Layout:** a two-column top region — left: **two** Market Sentiment speedometers
(**Today** `gauge_score(total)` + **30-Day Avg** `gauge_score(sentiment_30d_avg(snaps))`,
the avg = page-side mean of the history composites) + bias + size/conf; right: **two**
**Market Trend** speedometers (**Today** + **30-Day**, both via `trend_gauge_value`,
which now returns the directional 0–100 trend **score directly** — no anchor/nudge).
Both values come from `derived["trend"]` / `derived["trend_30d_ago"]` **published by
`sentiment_svc`** via the new **intraday Market Trend model** (see the dedicated
section below) — with the regime badge/desc beneath and a **TREND DETAIL**
press-and-hold popup showing the four sub-scores (Price/Breadth/Sector/VIX) +
confidences (`trend_subscore_rows`). The top region is now
**three columns** (Market Sentiment / Market Trend / Signals); the component
**table** (Value/Score[2dp]/Weight/Conf — Contrib computed for reconciliation but
not shown; credit_pulse excluded per v4.3 `WEIGHTS`) and the Trend detail are
**press-and-hold popups** (`ui.menu().props("no-parent-event")`), not always-visible
columns. The Signals column is a **2×2 four-tile matrix** (`TILE_DEFS` =
**Bias/Signal/Yesterday/Change** — Modifier dropped per design) with a
**traffic-light background** (`traffic_color(total)`). Below that, an **expanded-by-default**
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
  _VIX_SYMS` and standalone `technical`) and `compute_30d_trend` (daily structural analog
  for the **second gauge**, price + sector only). Both defensive → neutral on any failure.
- **15-min cadence + persisted state** in `handlers.refresh` via the module-level
  `_TREND` holder (lock-guarded; `scheduler.trend_due`/`TREND_INTERVAL_SEC=900`): the
  EMA-smoothing + hysteresis state thread across reads; the held trend rides inside the
  existing `cache:sentiment:composite` `derived.trend`/`derived.trend_30d_ago` (no new
  Redis key).
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
`premium_floor`/`top_n`; the `enabled` server kill-switch), loaded by
`services/options_svc/flow_alerts.py:load_thresholds()` (defaults if the file is missing) —
edit + restart `options_svc` to tune. See the 2026-07-18 "Last updated" entry.

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
python services\sentiment_svc\app.py      # :8210  (composite + rotation)
python services\options_svc\app.py        # :8211  (scan/swing/header/gamma/paper/captured/calculator
                                          #          + 1-min intraday GEX history collection, 08:00–15:20 CT)
python services\portfolio_svc\app.py      # :8212  (sector breakdown + vs-sector perf + live-streaming P&L)
python services\trade_svc\app.py          # :8213  (on-demand symbol analysis: MTF + Position/Investor verdicts)
python services\market_svc\app.py         # :8215  (live macro-ticker Market Dashboard: ~2s RTH poll of /quotes → cache:market:dashboard)
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
cd options-scanner     ; python -m pytest tests
cd sentiment-dashboard ; python -m pytest tests
cd trade-analyzer      ; python -m pytest .
cd portfolio-analyzer  ; python -m pytest tests
cd claude-driver       ; python -m pytest .
cd webgui              ; python -m pytest .   # 772 tests: transforms + shell smoke
```

The 3-tier services run per folder from the repo root (NOT `pytest services` over
all of them — that puts multiple hyphenated app dirs on `sys.path` at once and
re-triggers the documented `config`/`scoring`/`notifier` module-name collisions):

```powershell
# from the repo root, one service at a time
.venv\Scripts\python -m pytest services\sentiment_svc   # 140
.venv\Scripts\python -m pytest services\options_svc     # 432
.venv\Scripts\python -m pytest services\portfolio_svc   # 27
.venv\Scripts\python -m pytest services\trade_svc       # 56
.venv\Scripts\python -m pytest services\driver_svc      # 162
.venv\Scripts\python -m pytest shared\bus               # 15
.venv\Scripts\python -m pytest shared\contracts         # 37 (no app-dir imports — safe together)
```

- **options-scanner** has ~2 known date-relative failing tests carried over from
  the source repo — do not "fix" them as part of unrelated work.

## External processes (not in this repo)

The ML prediction servers (MES 8000 / MNQ 8001 / ES 8004 / NQ 8005) and the
options analytics service on 8200 are **separate, external processes**.
claude-driver addresses them over HTTP; this repo does not contain or start them.

## Design / plan docs

- [`docs/plans/2026-06-15-three-tier-architecture-design.md`](docs/plans/2026-06-15-three-tier-architecture-design.md) — **3-tier re-architecture** (GUI / per-domain services / Redis-Memurai backbone)
- [`docs/plans/2026-06-15-three-tier-architecture-plan.md`](docs/plans/2026-06-15-three-tier-architecture-plan.md) — bite-sized TDD implementation plan for the above
- [`docs/plans/2026-06-14-nicegui-webgui-design.md`](docs/plans/2026-06-14-nicegui-webgui-design.md)
- [`docs/plans/2026-06-14-nicegui-webgui-plan.md`](docs/plans/2026-06-14-nicegui-webgui-plan.md)
