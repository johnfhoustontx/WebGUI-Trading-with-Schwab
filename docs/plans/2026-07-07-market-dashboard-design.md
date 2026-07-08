# Market Dashboard (`/market`) — Design

**Date:** 2026-07-07
**Branch:** `Using_Highcharts`
**Status:** Design approved (brainstorming complete) — ready for implementation plan.

## 1. Goal

A new **More → Market Dashboard** page (`/market`) that displays a live, auto-updating
grid of the ~48 macro/market tickers from
`symbol_categories.csv`, grouped into a **framed panel per category**, laid out
side-by-side in a logical, easy-to-read arrangement. Each ticker is a **tile** whose
**background color reflects the market condition** (semantic risk-on / risk-off). Values
update continuously.

Source list (CSV): `Symbol, Description, Category` — 48 rows across 13 categories.

## 2. Key findings from data-availability probing (live proxy `:8100`)

Probed the real Schwab data before designing. Results shape the whole feature:

| Class | Symbols | Result |
|---|---|---|
| ETFs / equities | SPY, DIA, QQQ, IWM, RSP, QQEW, MTUM, SPMO, SMH, XSD, IGV, QTUM, XBI, XRT, XME, all XL*, TLT, HYG, LQD, GDLC, VCX | ✅ quote + `netPercentChange` |
| Indices (need `$`) | `SPX`→`$SPX`, `NDX`→`$NDX`, `VIX`→`$VIX`, `SKEW`→`$SKEW`, `VIX1D`→`$VIX1D`, `VIX3M`→`$VIX3M` | ✅ quote + `netPercentChange`, `assetMainType=INDEX` |
| Futures | `/ES[U26]`→`/ESU26`, `/NQ[U26]`→`/NQU26` | ✅ quote + `futurePercentChange`, `assetMainType=FUTURE` |
| NYSE internals | `$ADVN`, `$DECN`, `$TICK`, `$ADD`, `$ADSPD` | ⚠️ `lastPrice` only — `closePrice`/`netPercentChange` come back `0` (no day-change) |
| Computed spreads | `$ADVN-$DECN`, `HYG-LQD` | ⚠️ not a Schwab symbol — must be computed from legs |
| No Schwab data | `$DXY`, `$PCALL`, `$PCSP` | ❌ invalid symbols |

**Equivalents chosen for the unavailable symbols** (user decision — "suggest equivalents"):

- **`$DXY` → `UUP`** (Invesco DB US Dollar Bullish ETF) — standard quotable DXY tracker
  (verified: last 28.39, live `netPercentChange`). Keeps a live Currency frame.
- **`$PCALL` + `$PCSP` → one "Put/Call (cap-wt sectors)" tile.** No Schwab put/call index
  or ETF exists (`$CPC`/`$CPCE`/`$CPCI`/`$PCC` all invalid). The app **already computes a
  live put/call**: `sentiment_svc` publishes `live.sector_pcr` (cap-weighted sector Put/Call
  ratio) into `cache:sentiment:composite`. The Options Sentiment frame surfaces that single
  value instead of two dead tiles.

**Change fields differ by asset type** — a normalizer is required: equities/indices use
`netChange`/`netPercentChange`; futures use `netChange`/`futurePercentChange`; internals
have neither (value-only).

## 3. Streaming reality (why polling, not push)

Schwab's streamer (and the proxy's existing `/stream/quotes` SSE) covers
`LEVELONE_EQUITIES` only. Indices, NYSE internals, and VIX/SKEW have **no streaming
service at all** on Schwab — roughly half the symbols (including the most important macro
gauges) are **REST-only no matter what**. Futures *can* stream (`LEVELONE_FUTURES`) but the
proxy bridge doesn't subscribe to it today.

**Decision (user):** a single **fast uniform REST poll (~2 s during RTH)** of `/quotes` for
all symbols, publishing to Redis for the page to repaint. This is visually continuous, one
simple code path, and uniform across every category (the macro gauges refresh as often as
the ETFs). True push-streaming is rejected because it can only ever cover part of the set
and would require two update paths + new proxy machinery.

## 4. Coloring — semantic risk-on / risk-off (user decision)

Each symbol carries a **polarity**; the tile's `color_state` is derived from the signed
day-move (or, for value-only internals, the sign of the value) times the polarity:

- **Normal** (value up → **green / risk-on**): all equities/ETFs, `$SPX`/`$NDX`, `/ESU26`/
  `/NQU26`, HYG, LQD, GDLC, VCX, all sector SPDRs, thematics, factor ETFs, `$TICK`/`$ADD`/
  `$ADSPD` (positive value = green), `$ADVN-$DECN` net advancers.
- **Inverted** (value up → **red / risk-off**): `$VIX`/`$VIX1D`/`$VIX3M`, `$SKEW`, the
  Put/Call tile, `UUP` (dollar strength = tightening/risk-off), `TLT` (long-duration
  flight-to-safety). *(Per user: keep only VIX/SKEW/put-call/TLT/UUP inverted — defensive
  equity sectors XLP/XLU/XLV stay literal up=green.)*
- **Computed spreads:**
  - `$ADVN-$DECN` = `$ADVN.last − $DECN.last` → net advancers; positive → green.
  - `HYG-LQD` = `HYG.change_pct − LQD.change_pct` (HY vs IG relative day performance) →
    HY outperforming = spreads tightening = **green / risk-on**.
- **Value-only internals** (`$TICK`, `$ADD`, `$ADSPD`): no % change from Schwab → colored by
  the **sign of the value** vs 0. Raw `$ADVN`/`$DECN` counts are not signed; render them
  low-intensity informational (advancers light-green / decliners light-red) — the *net*
  spread tile carries the real breadth signal.
- **Flat / no-data** → neutral grey.

Color is bucketed into a small number of intensity levels (flat / mild / strong) mapped to
**fixed Tailwind palette classes** (per the repo's Tailwind-first standard — no `.style()`,
data-driven color mapped from a finite set).

## 5. Frame layout (user-approved order)

Responsive flex/grid **wrap** of framed category panels — frames side-by-side, wrapping on
narrow screens. Order flows macro → tape → rotation:

- **Row 1 — macro / risk gauges:** Volatility · Options Sentiment · Market Internals/Breadth · Currency
- **Row 2 — the tape:** Cash Index · Equity Index Futures · Broad-Market ETF · Custom Basket/Spread
- **Row 3 — rotation:** Sector SPDR · Thematic/Industry ETF · Factor/Momentum ETF · Fixed Income/Credit · Crypto/Alternatives

Each tile shows: **display symbol**, short **description** (muted), **last price**, and
**net change + % change** (omitted/blank for value-only internals), on the semantic
colored background.

## 6. Architecture & data flow (3-tier)

New Tier-2 **`market_svc`** (port **8215**, added to `config/ports.toml [services]` +
`repo_paths.SERVICE_PORTS`/`SERVICE_URLS`), following the existing per-domain service shape
(`make_app(...)` scaffold + a scheduler loop; no command handler needed — read-only).

```
market_svc scheduler loop (poll ~2s RTH / throttled off-hours)
  → proxy GET /quotes?symbols=<all real symbols, batched>
  → normalize change across INDEX/EQUITY/FUTURE
  → compute $ADVN-$DECN and HYG-LQD spreads
  → read cache:sentiment:composite → live.sector_pcr (Put/Call tile)
  → derive color_state per tile (polarity × sign)
  → publish cache:market:dashboard   (+ events:market:dashboard)

webgui /market page (Tier-1, engine-free)
  → version-poll cache:market:dashboard
  → repaint tiles in place (Tailwind palette-mapped bg classes)
```

**Symbol map** (baked from the CSV, single source of truth in the service): each entry =
`{csv_symbol, quote_symbol, display, description, category, polarity, kind}` where `kind` ∈
`{quote, spread, external}` (`external` = the sentiment put/call tile). This table encodes
all CSV→Schwab translations (`SPX`→`$SPX`, `VIX`→`$VIX`, `/ES[U26]`→`/ESU26`, `$DXY`→`UUP`,
…) and the polarity/kind so the poll + color logic stays pure and testable.

**Contract:** additive `shared/contracts/market.py` — `MarketDashboard` = ordered
categories, each with tiles `{category, csv_symbol, display, description, last, change,
change_pct, value_only, color_state, polarity, stale}`. Validated before caching, like the
other domain contracts.

**Cadence:** ~2 s during RTH (08:30–15:00 CT weekdays, holidays excluded — reuse the shared
`_HOLIDAYS`/`_is_rth` pattern); throttled to ~15 s off-hours/weekends (futures still move but
a glance-cadence is fine; indices/internals are stale anyway). Uses the proxy's batched
`/quotes` (one call), so it's one round-trip per tick regardless of symbol count.

## 7. Error handling

- Every symbol is defensive: a missing/invalid quote, a failed spread leg, or an absent
  sentiment composite → that tile renders **grey "no data"**; the frame and the rest of the
  dashboard are unaffected. The service never raises out of a tick (mirrors the other
  schedulers' `except: log` discipline).
- Off-hours zeros (internals reading 0) render as flat/grey, not misleading colors.
- Proxy down → the page shows the last cached snapshot + a stale indicator (version stops
  advancing); the standard "waiting for service" placeholder covers a cold start.

## 8. Testing

- **Pure helpers (TDD):** symbol-map integrity (every CSV row mapped or explicitly
  equivalized), the change-normalizer (INDEX vs EQUITY vs FUTURE), spread computation
  (`$ADVN-$DECN`, `HYG-LQD`), `color_state` bucketing (polarity × sign × intensity), and
  category grouping/ordering.
- **Service:** scaffold/handlers/scheduler tests like the other services (publish shape, the
  `MarketDashboard` contract gate, RTH-cadence gating), with a fake bus + fake proxy.
- **Page:** pure tile/frame builders unit-tested; `render()` smoke-verified in the browser
  preview (start `webgui`, screenshot the grid). End-to-end most reliably checked
  Redis-driven: publish a `cache:market:dashboard` snapshot and confirm the page paints it.
- Add `/market` to `webgui/tests/test_shell.py` and the `test_no_inline_style.py` guard.

## 9. Out of scope (YAGNI)

- Per-tile sparklines / mini-charts (a later enhancement if wanted).
- True push-streaming / a proxy `LEVELONE_FUTURES` bridge (rejected above).
- Click-through to detail pages, drag-reorder, user-configurable symbol lists.
- Alerting on tiles (the app already has the scanner-alert system).

## 10. Symbol map (complete)

| CSV symbol | Quote symbol | Category | Polarity | Notes |
|---|---|---|---|---|
| `$ADVN` | `$ADVN` | Market Internals / Breadth | normal (info) | value-only; low-intensity green |
| `$DECN` | `$DECN` | Market Internals / Breadth | inverted (info) | value-only; low-intensity red |
| `$ADVN-$DECN` | *(computed)* | Market Internals / Breadth | normal | `$ADVN.last − $DECN.last`; sign |
| `$ADD` | `$ADD` | Market Internals / Breadth | normal | value-only; sign of value |
| `$ADSPD` | `$ADSPD` | Market Internals / Breadth | normal | value-only; sign of value |
| `$TICK` | `$TICK` | Market Internals / Breadth | normal | value-only; sign of value |
| `$PCALL`+`$PCSP` | *(sentiment `live.sector_pcr`)* | Options Sentiment | inverted | one "Put/Call (cap-wt sectors)" tile |
| `VIX` | `$VIX` | Volatility | inverted | |
| `VIX1D` | `$VIX1D` | Volatility | inverted | |
| `VIX3M` | `$VIX3M` | Volatility | inverted | |
| `SKEW` | `$SKEW` | Volatility | inverted | tail-risk hedging |
| `SPX` | `$SPX` | Cash Index | normal | |
| `NDX` | `$NDX` | Cash Index | normal | |
| `/ES[U26]` | `/ESU26` | Equity Index Futures | normal | `futurePercentChange` |
| `/NQ[U26]` | `/NQU26` | Equity Index Futures | normal | `futurePercentChange` |
| `$DXY` | `UUP` | Currency | inverted | equivalent (dollar ETF) |
| `HYG-LQD` | *(computed)* | Custom Basket / Spread | normal | `HYG.chg% − LQD.chg%` |
| `SPY` `DIA` `QQQ` `IWM` `RSP` `QQEW` | same | Broad-Market ETF | normal | |
| `MTUM` `SPMO` | same | Factor / Momentum ETF | normal | |
| `SMH` `XSD` `IGV` `QTUM` `XBI` `XRT` `XME` | same | Thematic / Industry ETF | normal | |
| `XLC` `XLE` `XLF` `XLI` `XLK` `XLP` `XLRE` `XLU` `XLV` `XLY` | same | Sector SPDR | normal | |
| `TLT` | `TLT` | Fixed Income / Credit ETF | inverted | long-duration flight-to-safety |
| `HYG` | `HYG` | Fixed Income / Credit ETF | normal | |
| `LQD` | `LQD` | Fixed Income / Credit ETF | normal | |
| `GDLC` | `GDLC` | Crypto / Alternatives | normal | |
| `VCX` | `VCX` | Crypto / Alternatives | normal | private venture fund; may be stale |
