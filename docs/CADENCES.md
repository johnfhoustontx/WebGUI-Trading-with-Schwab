# Cadence reference — every scheduled API call, poll, and publish

Single reference for "what runs, how often, and what it costs" across the stack.
Cadence constants were read from the scheduler source on 2026-07-16 — when you tune
one, update this table (the **Source** column says where the knob lives).

Times are **CT** unless marked ET. "RTH" = regular trading hours on a trading day
(weekends + NYSE holidays gated out).

## 1. Schwab API calls (all via schwab-proxy :8100)

The proxy spaces upstream Schwab calls ~0.2 s apart (rate limiter) and counts every
outbound call per day (`Settings → API usage`, `GET /stats/api_calls`).

| What | Service | Cadence | Window / gating | Calls per tick (approx) | Source |
|---|---|---|---|---|---|
| Market Dashboard quote poll | market_svc | every **2 s** RTH / **5 s** off-hours (24/7 — futures) | always on | 1 batched `/quotes` (~48 symbols) | `market_svc/scheduler.py` `RTH_INTERVAL_SEC`/`OFFHOURS_INTERVAL_SEC` |
| GEX snapshot collection | options_svc | every **1 min** | 08:30–15:20 CT, trading days | ~24 `/chains` (index base + Top 20 watchlist) + flow-skew/premium computed from same chains | `options_svc/scheduler.py` `_GEX_INTERVAL_MIN`, `gex_collector.POLL_INTERVAL_MIN` |
| Header strip refresh (quotes + VIX) | options_svc | every **30 s** RTH / once per **5 min** off-hours | `periodic_refresh_due`; `skip_unchanged` write | 1 `/quotes` | `options_svc/scheduler.py` `POLL_INTERVAL_SEC`, `_OFFHOURS_INTERVAL_MIN` |
| Scanner autoscan (0-DTE + swing + directional signals) | options_svc | every **15 min** | RTH | chains/quotes across the watchlist — **no extra calls for directional** (single-leg candidates are built from the chains already fetched) | `options_svc` rescan slot |
| Driver paper book manage (reprice + auto-exit) | options_svc | every **5 min** | RTH | chains for symbols with open driver positions | `options_svc/scheduler.py` `_MANAGE_INTERVAL_MIN` |
| Manual paper book entry + manage | options_svc | **hourly**, top of hour **09:00–14:00 CT** (no 15:00 run; 20-min grace) | trading days | reprice of open ledger/account trades + entry from captured signals | `options_svc/scheduler.py` `paper_cycle_due`, `_PAPER_GRACE_MIN` |
| Action-alert digest repricing | options_svc | **3×/day**: 10:00 / 13:00 / 15:00 CT (20-min grace) | trading days | fresh `reprice_captured` chain fetches | `options_svc/scheduler.py` `action_alert_due` |
| Sentiment composite refresh | sentiment_svc | every **120 s** RTH / once per **15 min** off-hours | `refresh_due` | breadth/VIX quotes + sector fetches (live composite) | `sentiment_svc/scheduler.py` `REFRESH_INTERVAL_SEC`, `_OFFHOURS_INTERVAL_MIN` |
| Intraday Market Trend recompute | sentiment_svc | every **15 min** (inside the 120 s refresh) | RTH-weighted | SPY 5/15-min + daily history, breadth/sector/VIX quotes | `sentiment_svc/scheduler.py` `TREND_INTERVAL_SEC` |
| Sector & Industry table (P/C) rebuild | sentiment_svc | **once per RTH hour** | `sectors_due` | ~24 calls incl. 11 `/chains` for sector P/C | `sentiment_svc/scheduler.py` `sectors_due` |
| Order-flow streams (equity SPY/QQQ + near-ATM options) | sentiment_svc | **continuous SSE**; near-ATM OSI set re-derived every **5 min**; cache publish every **30 s** | RTH-meaningful (streams run when connected) | 2 persistent `/stream/*` connections + a chain fetch per OSI refresh | `order_flow_consumer.py` `OPTION_OSI_REFRESH_SEC`, `ORDER_FLOW_PUBLISH_SEC` |
| Portfolio live P&L stream | portfolio_svc | **continuous SSE**; republish every **2 s** when ticks pending | always on | 1 persistent `/stream/quotes` | `portfolio_svc/scheduler.py` `PUBLISH_INTERVAL_SEC` |
| Portfolio full rebuild (baselines/sectors) | portfolio_svc | every **10 min** RTH / **hourly** off-hours (or on manual Refresh) | `rebuild_due`; baselines reused when signature unchanged | per-holding history fetches (cached) | `portfolio_svc/scheduler.py` `REBUILD_INTERVAL_SEC`, `OFFHOURS_REBUILD_INTERVAL_SEC` |
| Trade Analyzer analysis | trade_svc | **on-demand only** (Analyze / tab-out) | — | 5 timeframes + SPY + sector ETF + fundamentals | `trade_svc/compute.analyze` |
| Driver market context | driver_svc | per autonomous cycle (see Claude table) | entry window | 1 `/quotes` ($VIX/$SPX/$VIX1D/SPY/QQQ) | `driver_svc/compute.fetch_market_context` |
| EOD summary assembly | options_svc | **1×/day ~15:10 CT** (30-min grace) | trading days | none extra — reads book state as-is | `options_svc/scheduler.py` `eod_summary_due` |
| Calculator / Simulator / Gamma page / Expected Move | options_svc | **on-demand** (page commands) | — | chain/quote/history per request | respective handlers |
| webgui proxy health probe | webgui | memoized **4 s** TTL, rewarmed by the 2 s watcher | every open page | 1 `/health` | `webgui/main.py` `_HEALTH_TTL_SEC` |

Rough daily volume for the two big hitters: the dashboard poll ≈ **~24k quote
calls/day** (2 s RTH + 5 s off-hours, 24/7) and GEX collection ≈ **~10k chain
calls/day** (24 symbols × 410 RTH minutes). Everything else is comparatively small.

## 2. Claude (Anthropic) API calls

All three call sites record into `shared/anthropic_counter.py` immediately before
`messages.create` (visible in `Settings → API usage`; services must have been
restarted after 2026-07-14 to count).

| What | Service | Cadence | Gating | Model | Source |
|---|---|---|---|---|---|
| Ticker market summary (verdict) | market_svc | every **40 min** RTH / **60 min** off-hours | `summary_due` + the Settings **ticker toggle** (off → no call) | Sonnet 5 | `market_svc/scheduler.py` `SUMMARY_RTH_SEC`/`SUMMARY_OFFHOURS_SEC` |
| Gamma Analyze auto-briefings | options_svc | **4×/day**: 08:00 / 08:48 / 11:30 / 14:58 CT (20-min grace) | trading days | Sonnet 5 | `options_svc/scheduler.py` `analyze_slot_due` |
| Gamma Analyze (ad-hoc button) | options_svc | on-demand | — | Sonnet 5 | `compute.gamma_analyze` |
| Driver autonomous decider | driver_svc | **every 30 min** in the entry window **09:45–15:30 ET** (+ Run now) | master switch ON + trading day + not halted (~12/day max) | `DRIVER_MODEL` override, else Opus 4.8 | `driver_svc/settings.py` `CHECKPOINT_MIN`; `scheduler.checkpoint_due` |

Steady state with the ticker on and the driver off: **~22 calls/day** (≈18 ticker +
4 briefings). Driver enabled adds up to ~12.

## 3. Internal schedules & publishes (no external API)

| What | Where | Cadence | Notes |
|---|---|---|---|
| options_svc scheduler tick (slot-gate check) | options_svc | 30 s | `POLL_INTERVAL_SEC`; due branches run concurrently |
| GEX collector status publish | options_svc | 30 s RTH / 5 min off-hours | SQLite age read; `skip_unchanged` |
| GEX history purge (keep 5 sessions) | options_svc | once per local date (first collect tick) | frees pages; `tools/vacuum_gex.py` / Settings → Vacuum shrinks the file |
| Driver scheduler tick (run gate) | driver_svc | 30 s | `POLL_INTERVAL_SEC`; halt re-arm next day |
| Order-flow window prune | sentiment_svc | ≤ every 30 s inside the stream loop | 5-min rolling windows |
| Gamma briefing index publish | options_svc | startup + after each persisted briefing | `cache:options:gamma_briefings` |
| Five-state transition push | sentiment_svc | event-driven (committed-state flip) | market-hours gated |
| New-signal push (Telegram/Discord/Fi-SMS) | options_svc | event-driven at scan/captured publish | date-scoped seen-set; silent seed on restart. **Credit spreads only** — directional is excluded (its Fit+Quality score isn't commensurable with the premium composite the `min_score` gate uses) |
| Scanner day-union merge + publish | options_svc | event-driven, every rescan | pure `merge_day_signals` → `cache:options:scan_day` (date-scoped, CT-pinned, capped 2000/list). No API calls; `cache:options:scan` stays live-only for the driver |
| EOD digest push | options_svc | 1×/day ~15:10 CT | Telegram/Discord/SMS |
| Watchdog (opt-in, not in start_all) | tools/watchdog.py | continuous probe loop | restarts dead processes, ≤3 per 10 min |

## 4. Browser-side polling (webgui — Redis reads only)

Version polls read the tiny `{key}:ver` counter (no payload) until it changes.

| What | Cadence | Notes |
|---|---|---|
| App-wide watcher (badges, alert chime, staleness) | 2 s | off-loop; service `/health` probes throttled to 30 s |
| Page version polls (Gamma, tables, driver, …) | 2 s | Gamma coalesces all views into one pipelined `read_versions` |
| Ticker content poll | 4 s | version-gated; DOM rebuilt only when the content signature changes |
| Status page sweep | 15 s (+ manual) | off-thread, 2.5 s per-probe timeout |
| Sentiment page repaint | 120 s (fetch-free) | tracks the service cache |
| Stale-view alert thresholds | default 600 s; `options:scan` 20 min | `alerts.STALE_OVERRIDES` |
