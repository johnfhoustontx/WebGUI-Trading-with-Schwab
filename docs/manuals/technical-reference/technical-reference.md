[TOC]

# About this document

This is the **calculation and architecture reference** for the WebGUI Trading
with Schwab stack. It documents *how every number is derived* — the formulas,
weights, thresholds, lookback windows, and cadences used across the sentiment,
options, trade, and portfolio engines.

It is an **offline maintainer reference**, not an end-user guide. For task-oriented
usage see the *User Guide*; for the inter-service integration surface see the
*API / Developer Reference*.

> **Source-of-truth note.** Constants in this document were read from the code
> (file paths are given throughout). Where a value lives in a single canonical
> place — e.g. `sentiment-dashboard/scoring/__init__.py:WEIGHTS` — that file
> governs; if you change it there, update this document.

---

# Architecture Overview

## The three tiers

The stack is split into three physically separate tiers communicating over a local
Redis (Memurai) backbone. No two Tier-2 services talk to each other directly.

```
TIER 1  GUI         webgui/ NiceGUI app (:8500). Renders pages, reads Redis cache,
                    subscribes to events, enqueues commands. No engine imports.
   ▲ cache read / subscribe                │ commands
TIER 3  STORE+COMM  Memurai (:6379): cache:{domain}:{view}, events:{domain}:{view}
                    pub/sub, cmd:{domain} command streams. shared/contracts (typed
                    payloads) + shared/bus (redis wrapper).
   ▲ publish                               │ consume
TIER 2  SERVICES    services/{domain}_svc FastAPI (sentiment/options/portfolio/
                    trade/driver). Each imports only its engines, owns its
                    scheduler + command consumer, validates + caches + publishes.
                    Calls schwab-proxy (:8100) for market data.
```

## Process map and ports

| Process | Port | Role |
|---------|------|------|
| schwab-proxy | 8100 | Schwab auth/token manager + market-data gateway. Start first. |
| Memurai (Redis) | 6379 | Cache, pub/sub, command streams. |
| sentiment_svc | 8210 | Sentiment composite, trend, rotation. |
| options_svc | 8211 | Scans, paper trading, gamma, calculator, simulator, expected move. |
| portfolio_svc | 8212 | Holdings, sectors, performance, live P&L stream. |
| trade_svc | 8213 | On-demand single-symbol analysis. |
| driver_svc | 8214 | Morning order-approval pipeline. |
| webgui | 8500 | The web UI. |

Ports come from `config/ports.toml` via `repo_paths.py` — never hard-coded.

## Data flow (one request)

1. The GUI enqueues a command on `cmd:{domain}` (a Redis Stream).
2. The owning service's consumer loop drains the stream and dispatches to a handler.
3. The handler calls its **compute** layer (the engines), which fetches market data
   from the proxy and computes a result.
4. The result is validated against a **contract**, written to `cache:{domain}:{view}`
   (which increments a version counter), and an event is published on
   `events:{domain}:{view}`.
5. The GUI's version-poll timer sees the new version, reads the cache, and repaints.

## Folder map

| Folder | Contents |
|--------|----------|
| `schwab-proxy/` | Schwab gateway / token manager. |
| `options-scanner/` | GEX/options engines, scoring, paper engine, simulator, IV analysis. |
| `sentiment-dashboard/` | Sentiment `scoring/` package + live composite + bridge. |
| `trade-analyzer/` | `src/analysis` — recommendation, scoring, fundamentals, sector. |
| `portfolio-analyzer/` | `src/` — sector breakdown, comparisons, evaluation. |
| `claude-driver/` | Morning orchestration + order approval logic. |
| `shared/` | `analysis_lib/` (technical, market data), `contracts/`, `bus/`. |
| `services/` | The five Tier-2 domain services. |
| `webgui/` | The NiceGUI front end. |

## Scoring conventions (shared idioms)

- **Sentiment scores** are integers **1–10, contrarian** (10 = max fear /
  opportunity, 1 = max greed / risk). `0` means "input undefined".
- **Confidence** is a float in `[0.0, 1.0]`. Missing data → `0.0`; partial →
  fractional (often `sqrt(fields_present / fields_possible)`).
- **Composites are confidence-weighted**, never a plain weighted average, so a
  low-confidence component cannot dominate.
- Piecewise mappings use **narrow neutral bands** (≈0.95–1.05 of normal) so small
  moves through a breakpoint produce a visible change.

---

# Sentiment Calculations

All sentiment scoring lives in `sentiment-dashboard/scoring/` (pure functions, no
I/O). The single source of truth for component weights is
`scoring/__init__.py:WEIGHTS`.

## Composite blend

**File:** `scoring/composite.py` · `blend(scores, confs, weights)`

The master composite is a confidence-weighted blend of five components:

```
composite             = Σ(wᵢ · scoreᵢ · confᵢ) / Σ(wᵢ · confᵢ)
aggregate_confidence  = Σ(wᵢ · confᵢ)
```

**Component weights** (`scoring/__init__.py:WEIGHTS`, sum = 1.0):

| Component | Weight | Module |
|-----------|--------|--------|
| VIX Complex | 0.20 | `vix.py` |
| Put/Call (cap-weighted sectors) | 0.20 | `put_call.py` |
| Breadth | 0.20 | `breadth.py` |
| Rotation | 0.15 | `rotation.py` |
| Sector Performance | 0.25 | `sector_perf.py` |

> **Credit Pulse was removed from the composite (v4.3)** and its 5% reallocated to
> Put/Call. `credit_pulse.py` still *computes* a score for display, but it is not
> in `WEIGHTS` and does not enter the blend.

## VIX Complex

**File:** `scoring/vix.py` · `score_complex(...)`

The 20% VIX slot is itself a blend of three sub-scores
(`VIX_SUB_WEIGHTS` in `scoring/__init__.py`):

| Sub-score | Sub-weight | Input |
|-----------|-----------|-------|
| Term structure | 0.50 | VIX vs its 10-day MA |
| VIX1D | 0.33 | VIX1D vs VIX |
| Term slope | 0.17 | VIX9D vs VIX |

Each sub-score is a **piecewise mapping of a ratio to a 1–10 score**, with a narrow
neutral band around 1.0.

**Term structure** — `score_term(vix, vix_ma)` on `ratio = vix / vix_ma`:

```
ratio < 0.85          -> 10.0   (deep contango, calm)
0.85 <= ratio < 0.95  -> 7.0 + (ratio-0.85)/0.10 * 2.0
0.95 <= ratio < 1.05  -> 5.0 + (ratio-0.95)/0.10 * 1.0   (neutral band)
1.05 <= ratio < 1.15  -> 4.0 - (ratio-1.05)/0.10 * 1.0
1.15 <= ratio < 1.30  -> 2.0 - (ratio-1.15)/0.15 * 1.0
ratio >= 1.30         -> 1.0    (backwardation, stress)
```

**VIX1D** — `score_vix1d(vix1d, vix)` on `ratio = vix1d / vix`: same shape with
breakpoints 0.80 / 0.88 / 0.98 / 1.05 / 1.15.

**Term slope** — `score_term_slope(vix9d, vix)` on `slope = vix9d / vix`:
breakpoints 0.85 / 0.92 / 1.00 / 1.05.

## Put/Call

**File:** `scoring/put_call.py`

The composite uses **cap-weighted per-sector** put/call ratios
(`score_sector_weighted`):

```
blended_pcr = Σ(pcr_etf · weight_etf) / Σ(weight_etf)        # S&P cap weights
score       = interpolate(PC_THRESHOLDS, blended_pcr)        # clamp 1..10
confidence  = sqrt(sectors_used / sectors_possible)
```

`PC_THRESHOLDS` (ratio, score) — interpolated linearly between points; higher P/C
(more fear) scores higher:

```
[(1.3, 1), (1.1, 2), (0.9, 5), (0.7, 8), (0.0, 10)]
```

The single-market variant `score(pc_equity, pc_ma, skew)` adds ±1 adjustments for
P/C spikes/leans (current vs 10-day MA crossing ±15%) and for an Elevated/Inverted
skew categorical.

## Breadth

**File:** `scoring/breadth.py` · `score(...)`

Blends an advance/decline reading with the percent of stocks above their 50-day MA.

`BREADTH_THRESHOLDS` (% above 50DMA, score): `[(75,10),(65,8),(55,6),(45,5),(35,3),(0,1)]`.

**A/D ratio** (`advance / decline`) maps piecewise:

```
>=4.0 -> 10   >=3.0 -> 9   >=2.0 -> 8   >=1.5 -> 7   >=1.15 -> 6
>=0.87 -> 5   >=0.67 -> 4  >=0.50 -> 3  >=0.33 -> 2  <0.33 -> 1
```

**Blend:** when both are present, `score = 0.7 · ad_score + 0.3 · dma_score`; a new
highs/lows ratio nudges ±1. Confidence ≈ `sqrt(fields_present / 4)`.

## Rotation

**File:** `scoring/rotation.py`

Two paths exist:

- **Legacy / display** (`score_fallback`, and the tk app's day/3d/week blend at
  40/40/20) — base 5 with categorical adjustments.
- **Live composite** uses **dual momentum with a crash filter**
  (`compute_dual_momentum`), the more robust path used by `live_composite.py`.

**Dual momentum** (`lookback_days = 63`):

```
return_etf  = close[-1] / close[-63] - 1
cash_return = (1 + irx_yield_pct/100) ^ (63/252) - 1     # 13-week T-bill, $IRX
```

- **Crash filter:** if the *top* sector's trailing return is below the cash return,
  the regime is risk-off and `score = 1`.
- **Otherwise:** rank sectors by trailing return; compute the average rank of
  cyclical vs defensive ETFs; `rank_spread = def_avg_rank − cyc_avg_rank`
  (positive = defensives leading = risk-off); `score = clip(5.0 + rank_spread, 1, 10)`.
- **Confidence:** `sqrt((returns_available / possible) · irx_present)` where
  `irx_present` is 1.0 with a live $IRX yield, else 0.5.

**RRG quadrants** (`compute_rrg_quadrants`, `rs_window=50`, `mom_window=20`) — used
by the Sector Rotation page:

```
RS              = sector_close / benchmark_close          # per bar
RS_strength     = 100 · RS_today / mean(RS, last 50)
RS_momentum     = 100 · RS_today / RS_(20 bars ago)
```

Quadrants: **Leading** (strength≥100, mom≥100), **Weakening** (≥100, <100),
**Lagging** (<100, <100), **Improving** (<100, ≥100).

## Sector Performance

**File:** `scoring/sector_perf.py`

Cap-weighted daily move across the 11 GICS sectors, mapped to a 1–10 score:

```
cap_wtd_return = Σ(sector_daily_return · sector_cap_weight)
```

Mapping (return → score): `+2.0% → 10`, `+1.5 → 9`, `+1.0 → 8`, `+0.5 → 7`,
`0..+0.5 → 6`, `-0.5..0 → 5`, `-1.0..-0.5 → 4`, `-1.0 → 3`, `-1.5 → 2`, `≤-2.0 → 1`.
Cap weights live in `sectors_ref.SP500_SECTOR_WEIGHTS`.

## Velocity and divergence

**File:** `scoring/composite.py`

**Velocity** (`velocity(history_scores, today)`):

```
roc_3d = today - scores[-3]
roc_5d = today - scores[-5]
z_20d  = (today - mean(scores[-20:])) / std(scores[-20:])
regime_break = abs(z_20d) > 1.5
```

**Divergence** (`divergence(named_scores)`): of the components with score > 0, if
`max_score − min_score ≥ 4`, flag a low-conviction divergence between the highest
and lowest components.

## Intraday Market Trend (directional 0–100)

**File:** `scoring/intraday_trend.py`

A directional 0–100 trend score (50 = neutral, 100 = max bull) recomputed every
15 minutes, blended from four sub-scores by `TREND_WEIGHTS`:

```
TREND_WEIGHTS = {price: 0.45, breadth: 0.25, sector: 0.20, vix: 0.10}
```

Each sub-score returns `TrendSub(score, confidence)`; `blend_trend` combines them
confidence-weighted (same idiom as the composite), and `score_to_state` maps the
result to the five-state vocabulary used by the regime bridge.

**Price** — `score_price(alignment_pct, price_vs_vwap_pct, macd_hist, rsi, adx, n_timeframes)`:

```
align = alignment_pct / 100
vwap  = clamp(price_vs_vwap_pct / 0.5, -1, 1)
macd  = sign(macd_hist)
rsi   = (rsi - 50) / 20
direction = 0.50·align + 0.20·vwap + 0.15·macd + 0.15·rsi
adx_factor = clip(adx / 40, 0.3, 1.0)               # chop hugs 50
score = clamp(50 + 50·direction·adx_factor, 0, 100)
confidence = clip(n_timeframes / 3, 0, 1)
```

**Breadth** — `score_breadth_dir(net_ad, pct_above_50, new_highs, new_lows)`:
weighted blend (0.4 / 0.4 / 0.2) of normalized net A/D, `(pct−50)/50`, and
`(highs−lows)/(highs+lows)`; `score = 50 + 50·direction`; confidence = sum of the
weights actually used.

**Sector** — `score_sector_participation(n_green, n_total, cyc_def_spread)`:
`participation = (n_green/n_total − 0.5)·2`; `direction = 0.6·participation +
0.4·cyc_def_spread`; confidence = `clip(n_total/11, 0, 1)`.

**VIX** — `score_vix_context(vix, vix_change_pct, vix1d, vix9d)`:
`lvl = clip((20−vix)/10, −1, 1)`, `chg = clip(−vix_change_pct/5, −1, 1)`,
`term = clip((vix−vix1d)/2, −1, 1)`; `direction = 0.4·lvl + 0.4·chg + 0.2·term`;
confidence = 1.0.

**Volatility damper** — `vol_confidence_factor(vix_change_pct)`: a sharp VIX spike
lowers aggregate confidence: `clip(1 − 0.04·vix_change_pct, 0.4, 1.0)` for positive
changes, else 1.0.

## Daily trend-regime state machine

**File:** `scoring/trend_regime.py`

A SPY-based five-state classifier (independent of the composite) used to fill the
back-compat `sma_*`/`drawdown` bridge fields. Constants:

```
BULL_DD_MAX       = -5.0     # max drawdown from 252d peak for "bull"
PULLBACK_DD_MAX   = -12.0
BEAR_RALLY_DD_MIN = -10.0
SLOPE_BULL_MIN    =  0.05    # 200-DMA slope %, bull
SLOPE_BEAR_MAX    = -0.05
SLOPE_WINDOW      = 20
HYSTERESIS_DAYS   = 2
```

Inputs are SPY daily closes. It computes `sma50`, `sma200`, the 20-bar slope of the
200-DMA (as %), and the 252-day drawdown, then classifies:

```
close > sma50 > sma200  and slope > 0.05  and dd > -5   -> bull_trend
close <= sma50, sma50 > sma200, slope > 0, dd > -12     -> pullback_in_bull
close < sma50 < sma200  and slope < -0.05               -> bear_trend
close > sma50, slope < 0, dd < -10                      -> bear_rally
otherwise                                               -> range
```

`commit_state` requires the raw state to repeat for `HYSTERESIS_DAYS` before
flipping. Confidence: 0.0 below 50 bars, 0.5 from 50–200, 1.0 at ≥200 bars.

---

# Options Scoring

**File:** `options-scanner/scoring.py`

## Composite 0–100 score

Each signal gets a 0–100 quality score: a weighted sum of eleven normalized factors
(`DEFAULT_WEIGHTS`, sum = 100):

| Factor | Weight | Group | Normalizer |
|--------|--------|-------|-----------|
| Risk/Reward | 15 | Value | `norm_rr` |
| Probability of Profit | 10 | Value | `norm_pop` |
| Theta efficiency | 10 | Value | `norm_theta` |
| IV Rank | 12 | Context | `norm_iv_rank` |
| IV/HV ratio | 10 | Context | `norm_iv_hv_ratio` |
| Vega risk | 8 | Context | `norm_vega_risk` |
| Expected-move buffer | 12 | Context | `norm_em_buffer` |
| Liquidity | 5 | Execution | `norm_liquidity` |
| Trend alignment | 10 | Execution | `score_trend` |
| GEX wall proximity | 4 | Execution | `norm_gex_proximity` |
| DEX wall proximity | 4 | Execution | `norm_dex_proximity` |

```
score = Σ(weightᵢ · normalized_factorᵢ) / 100
```

## Factor normalizers

**Risk/Reward** — `norm_rr(rr_pct)`: `min(100, rr_pct / 50 · 100)` (50%+ R:R → 100).

**Probability of Profit** — `norm_pop(pop_pct)`: `min(100, (pop_pct − 50) / 45 · 100)`
(PoP 50% → 0, 95%+ → 100).

**Theta efficiency** — `norm_theta(net_theta, max_loss, all_theta_efficiencies)`:
`efficiency = |net_theta| / max_loss · 100` (daily decay as % of risk). Normalized
by **percentile rank** among all candidates when peer data exists, else linearly:
`min(100, efficiency / 0.5 · 100)`.

**IV Rank** — `norm_iv_rank(iv_rank)`: pass-through clamped to `[0, 100]`.

**IV/HV ratio** — `norm_iv_hv_ratio(iv_hv)`: `(iv_hv − 0.5) / 1.0 · 100`, clamped
(ratio 0.5 → 0, 1.0 → 50, 1.5+ → 100). Rewards IV richer than realized vol.

**Vega risk** — `norm_vega_risk(net_vega, max_loss, iv_rank)`: penalizes vega
exposure in low-IV regimes:

```
vega_exposure = |net_vega| / max_loss            # typically 0.001..0.05
vega_score    = max(0, min(100, (1 - vega_exposure/0.05)·100))
score         = 0.6·vega_score + 0.4·(iv_rank/100·100)
```

**Expected-move buffer** — `norm_em_buffer(short_strike, underlying, em_1sd, spread_type)`:
how far the short strike sits outside the ±1σ expected move.

```
distance  = underlying - short_strike   (PCS) | short_strike - underlying (CCS)
em_ratio  = distance / em_1sd
em_ratio <= 0  -> 0                              (short strike ITM/ATM)
0 < em_ratio<1 -> em_ratio · 50                  (inside EM, penalized)
em_ratio >= 1  -> min(100, 50 + (em_ratio-1)·50) (outside EM, rewarded)
```

**Liquidity** — `norm_liquidity(bid, ask, mark)`: `spread_pct = (ask−bid)/mark·100`;
`max(0, min(100, (1 − spread_pct/5)·100))` (≤1% spread → 100, ≥5% → 0).

**Trend alignment** — `score_trend(...)`: market regime maps to ±10, normalized to
`[0, 100]`.

**GEX / DEX wall proximity** — `norm_gex_proximity` / `norm_dex_proximity`: distance
of the short strike to the nearest wall as % of spot; `min(100, min_dist_pct / 1.0 ·
100)` (≥1% away → 100, at the wall → 0).

## Expected move and IV analysis

**File:** `options-scanner/iv_analysis.py`

**Expected move** — `calc_expected_move(price, iv_pct, dte)`:

```
em_1sd = price · (iv_pct/100) · sqrt(max(dte, 0.25) / 365)
```

Returns the 1σ dollar/percent move plus ±1σ and ±2σ bands.
`calc_expected_moves(price, iv_pct)` returns daily/weekly/monthly variants.

**IV Rank & percentile** — `calc_iv_rank_percentile(current_iv, hv_series, lookback_days=252)`:
compares current ATM IV to the trailing 252-day **HV-30** distribution (a realized-
vol proxy):

```
vol_rank       = (current_iv - hv_low_52w) / (hv_high_52w - hv_low_52w) · 100
vol_percentile = (# days with hv < current_iv) / total_days · 100
```

Returned under both `iv_*` (legacy) and `hv_*` (honest) keys.

**Historical volatility** — `calc_historical_vol_series(candles, window=30)`:
rolling 30-day std of daily log returns, annualized: `std · sqrt(252) · 100`.

---

# Technical Indicators

**File:** `shared/analysis_lib/technical.py`. All operate on OHLCV DataFrames.

**EMA** — `calculate_ema(df, period)`:

```
multiplier = 2 / (period + 1)
EMA[t]     = close[t]·multiplier + EMA[t-1]·(1 - multiplier)
```

Seeded with the SMA of the first `period` bars; implemented vectorized via
`ewm(alpha=multiplier, adjust=False)`.

**RSI** — `calculate_rsi(df, period=14)`:

```
avg_gain = EMA(gains, 14);  avg_loss = EMA(losses, 14)
RSI = 100 - 100 / (1 + avg_gain/avg_loss)
```

Defaults to 50.0 on insufficient data. Reference bands: oversold 30, overbought 70.

**ADX** — `calculate_adx(df, period=14)`:

```
+DI = 100 · EMA(+DM, 14) / EMA(TR, 14)
-DI = 100 · EMA(-DM, 14) / EMA(TR, 14)
DX  = 100 · |+DI - -DI| / (+DI + -DI)
ADX = EMA(DX, 14)
```

(TR = true range; +DM/−DM = directional movement.) >25 ≈ strong trend.

**MACD** — `calculate_macd(df, fast=12, slow=26, signal=9)`:

```
macd_line   = EMA(close,12) - EMA(close,26)
signal_line = EMA(macd_line, 9)
histogram   = macd_line - signal_line
```

`macd_histogram_series(df)` returns the full histogram series (so callers can read
the prior bar for acceleration).

**VWAP** — `calculate_vwap(df)`:

```
typical = (high + low + close) / 3
vwap    = cumsum(typical · volume) / cumsum(volume)
```

**Relative volume** — `calculate_relative_volume(df, period=20)`:
`today_volume / mean(prior period volume)`, returned with today's raw volume.

**Volume profile** — `calculate_volume_profile(df, num_bins=20)`: buckets closes
into 20 price bins by volume (vectorized via `digitize`/`bincount`), returning the
**POC** (highest-volume bin), and the **VAH/VAL** bounding the 70% value area.

---

# Trade Analyzer

**File:** `trade-analyzer/src/analysis/scoring.py`. Each primitive returns an
integer in `[−100, +100]`; verdict engines blend them into Position and Investor
Buy/Hold/Sell verdicts.

## Technical primitives

| Function | Mapping (abridged) |
|----------|--------------------|
| `score_rsi(rsi)` | <30 → −90, 30–40 → −60, 40–50 → −20, 50–60 → 60, 60–70 → 30, 70+ → −20 |
| `score_adx_directional(adx, ema_slope)` | sign = EMA slope; ≥25 → 100·sign, ≥20 → 60·sign, ≥15 → 30·sign, else 0 |
| `score_macd(hist, hist_prev)` | up&rising → 80, up&falling → 30, down&rising → −20, down&falling → −80 |
| `score_relative_volume(rv, ema_slope)` | >1.5 → 60·sign, ≥1.0 → 20·sign, <0.7 → −30 |
| `score_vwap(price, vwap)` | far above → 60/30, far below → −80/−40 by % distance |
| `score_volume_profile_location(price, vp)` | at POC → 0, in value area above POC → 50, below VAL → −60 |
| `score_relative_strength_percentile(p)` | `round((p − 0.5)·200)`, clamped ±100 |
| `score_distance_from_52wk_high(d)` | ≤5% → 60, ≤15% → 20, ≤30% → −20, >30% → −60 |

## Fundamental primitives

| Function | Mapping |
|----------|---------|
| `score_pe_vs_sector(pe, sector_median)` | ratio ≤0.7 → 60, ≤1.0 → 30, ≤1.3 → −10, >1.3 → −50 |
| `score_peg(peg)` | <1 → 40, ≤2 → 0, >2 → −40 |
| `score_growth_metric(g)` | >0.15 → 80, ≥0.05 → 30, ≥0 → 0, ≥−0.05 → −30, else −80 |
| `score_roe(roe)` | >0.15 → 60, ≥0.05 → 20, else −40 |
| `score_earnings_surprise_streak(s)` | ≥4 beats >5% → 80, last miss <0% → −60, else 0 |
| `score_guidance_direction(d)` | RAISED → 40, LOWERED/CUT → −60, else 0 |

## Fundamentals parsing

**File:** `trade-analyzer/src/analysis/fundamentals.py`. The Schwab
`/instruments?projection=fundamental` payload is parsed as a **superset**: the real
Schwab fields are primary (`revChangeTTM`/`epsChangePercentTTM` as percent →
fraction; `returnOnEquity` as percent via a `>2` magnitude heuristic;
`operatingMarginTTM` vs `MRQ` for the margin trend), with legacy speculative names
as fallback. Fields the payload does not carry (next-earnings date, EPS surprises,
guidance, FCF) degrade to `None`, so those gates simply never fire.

## Markov 2.0 forecast (Position)

**Files:** `trade-analyzer/src/analysis/markov.py` (pure math) +
`services/trade_svc/compute.py` (reconstruction, prior, wiring). A probabilistic
forward layer on the **Position** composite score, rendered as the third card in the
verdict row.

**States — 5 score bands** anchored at the verdict's decision boundaries
(`classify_band`):

| Band | Composite-score range | Verdict zone |
|------|-----------------------|--------------|
| 0 Strong-Bear | [−100, −40) | SELL |
| 1 Weak-Bear | [−40, −15) | HOLD |
| 2 Neutral | [−15, +15) | HOLD |
| 3 Weak-Bull | [+15, +40) | HOLD |
| 4 Strong-Bull | [+40, +100] | BUY |

**Markov base score (`composite_daily`).** The live verdict mixes intraday-only
factors (intraday VWAP, intraday relative volume, multi-timeframe EMA alignment) that
cannot be reconstructed for past bars, so the chain instead runs on a parallel
**daily-only** composite computed identically for every historical bar and for "now"
(`reconstruct_daily_composite`): EMA-alignment (price vs the daily 12/21/50/200 EMA
stack), ADX (directional), RSI, MACD, daily relative volume, distance-from-252-day-
high, RS vs SPY (63d/126d), and sector-ETF-vs-SPY RS — the daily-reconstructable
subset of the Position factors, weights renormalized to a 100-point scale. A bar with
a missing close is excluded (it is not an observation).

**Transition matrix — hybrid (per-symbol + pooled prior).** Day-to-day band
transitions over ~1 yr of `composite_daily` form a 5×5 count matrix `C_sym`
(`count_matrix`). Each row is Bayesian-shrunk toward a pooled prior via a
Dirichlet-multinomial blend (`shrink`, α = 30):

```
P[i,j] = (C_sym[i,j] + α · Prior[i,j]) / (Σ_j C_sym[i,j] + α)
```

The **pooled prior** (`build_pooled_prior` / `get_prior`) aggregates band transitions
across a curated 17-symbol universe, row-normalized (`pooled_prior`); it is cached at
`cache:trade:markov_prior` and rebuilt lazily once per day (uniform fallback on
failure).

**Forecast** (`forecast`). From the current band, the n-step distribution is
`dist₀ · Pⁿ` (`project`) for n = 5 / 10 / 20 trading days, yielding **P(BUY)** =
P(band 4), **P(SELL)** = P(band 0), and **E[score]** = Σ midpoint·prob over band
midpoints `[−70, −27.5, 0, +27.5, +70]`. The row's self-transition probability is the
**persistence**; the **stationary** (long-run) distribution is found by power
iteration (robust to reducible chains).

**Drift tilt** (`drift_tilt`, `row_confidence`). The expected forward move drives a
bounded, confidence-weighted adjustment to the displayed score:

```
drift      = E[score @ 10d] − composite_daily_now
confidence = n / (n + 40)            # n = observed transitions out of the current band
tilt       = clip(0.5 · drift, −12, +12) · confidence
markov_adjusted_score = clip(composite_full + tilt, −100, +100)
```

`composite_full` is the live (intraday-enriched) Position score. **No feedback by
construction:** the chain is built only from `composite_daily`, so the tilt added to
`composite_full` can never feed back into the matrix. The tilt moves the **score**
only — the Buy/Hold/Sell **label** is never re-derived from it. Every step is
defensive: any failure yields no Markov block and the verdict is unchanged.

---

# Portfolio Analytics

**File:** `portfolio-analyzer/src/`.

**Position classification** (`sectors.classify_positions`): each position is tagged
with a sector and sector ETF (futures are bucketed as "Futures" and excluded from
normal classification).

**Sector weights** (`sectors.sector_weights`): market value by sector over the
**absolute-value** total, so shorts don't distort weights:

```
weight_sector = Σ(market_value in sector) / Σ|market_value|
```

**vs Benchmark** (`weights_vs_benchmark`): `my_weight[sector] − benchmark[sector]`
(positive = overweight).

**Holding vs sector RS** (`holding_vs_sector`): the stock's trailing return vs its
sector ETF over several lookbacks (100 = parity), via
`shared/analysis_lib/sector_analysis.calculate_stock_vs_sector_rs`.

**Since-purchase excess** (`since_purchase_vs_sector`): `stock_return −
sector_return` since entry.

**RRG quadrants** (`compute_rrg_quadrants`, `rs_window=50`, `mom_window=20`): same
quadrant rule as the sentiment RRG.

**Evaluation** (`evaluation.py`): a split-speed scorecard — slow **baselines**
(relative-strength percentile, technical and fundamental scores) computed at load
and refreshed on a cadence, plus a fast **live** P&L/tailwind update per tick —
producing per-position letter grades (Return / Capital / Risk / Entry), a composite,
annualized return, and drawdown, with advisory suggestions.

---

# Black-Scholes & the Simulator

**File:** `options-scanner/options_calculator.py`.

## Pricing and Greeks

```
d1 = [ln(S/K) + (r + σ²/2)·T] / (σ·√T)
d2 = d1 - σ·√T
```

| Greek / price | Call | Put |
|---------------|------|-----|
| Price | `S·N(d1) − K·e^(−rT)·N(d2)` | `K·e^(−rT)·N(−d2) − S·N(−d1)` |
| Delta | `N(d1)` | `N(d1) − 1` |
| Gamma | `n(d1) / (S·σ·√T)` | same |
| Theta (per day) | `[−S·n(d1)·σ/(2√T) − r·K·e^(−rT)·N(d2)] / 365` | put sign on the second term |
| Charm | `−n(d1)·[2rT − d2·σ√T] / (2T·σ√T)` | call value `+ r·e^(−rT)` |
| Vanna | `−n(d1)·d2/σ / 100` (per 1 vol point) | same |

(`N` = standard-normal CDF, `n` = its PDF.) At/after expiration (T ≤ 0), price is
intrinsic value and second-order Greeks are 0.

## Spread metrics and P&L grid

`calc_summary(legs, strategy, spot, r, iv, T)` returns credit/debit, max
profit/loss, break-even(s), strike width, and probability of profit.

`calc_spread_pnl(legs, spot, iv, rate, eval_dates, price_range, expiry_date, iv_adjustment=0)`
re-prices every leg via Black-Scholes at each (price point × evaluation date) and
sums P&L into the grid that drives the Calculator heat map. An `iv_adjustment`
shifts IV at every point for shock scenarios.

## Simulator engines

**File:** `options-scanner/options_simulator/`.

- **What-if** (`WhatIfEngine`) — freezes time `days_elapsed` forward and sweeps an
  81-point ±20% price range, decomposing P&L by Delta/Gamma/Theta/Vega.
- **IV shock** (`IVShockEngine`) — compares the contract at base IV vs `IV × mult`.
- **Replay** (`ReplayEngine.full_trace`) — steps the contract bar-by-bar along the
  underlying's recent path, re-pricing and computing all Greeks at each bar. The
  service (`compute.sim_replay`) wraps this and compresses overnight/weekend gaps
  onto a consecutive integer x-axis for the six-panel chart.

---

# GEX / Gamma

**File:** `options-scanner/gamma_tool.py`.

## Per-strike gamma exposure (GEX)

The standard GEX contribution per strike (calls positive, puts negative):

```
GEX_strike = gamma · open_interest · 100 · spot²
```

In code (`gamma_tool.py`) the per-strike value is computed as
`gamma · OI · 100 · spot · spot · 0.01` (the `· 0.01` scales the result for
display). A **Volume** variant substitutes total volume for open interest. The
engine returns, per strike, the call, put, and net values.

## Dealer delta exposure (DEX) and projection

The DEX/hedge panel sums `OI · delta · contract_multiplier · spot` across strikes
for the current and a projected delta, surfacing net-delta-now vs projected-close
hedging pressure.

## Directional walls

`get_directional_walls(gex_dict, spot)` returns one **put wall** (the strike below
spot with the largest put GEX) and one **call wall** (the strike above spot with the
largest call GEX) — the single-wall pair the Gamma page draws.

## Intraday collection

The options service collects GEX snapshots every 2 minutes within 08:30–15:20 CT on
trading days (reusing the standalone collector's `poll_once`) into `gex_history.db`,
which feeds the strike × time heat map. The universe is the index base
(`$SPX`/`$VIX`/`SPY`/`QQQ`) plus the watchlist.

---

# Rescue Tested Trades

**Files:** `services/options_svc/rescue.py` (pure engine), `commission.py`,
`compute.compute_rescue` / `compute.assess_open_positions`, and the apply primitives
in `options-scanner/paper_adjust.py`.

The Rescue feature detects credit spreads (PCS/CCS/IC) that have moved against the
position and proposes a ranked, commission-aware menu of adjustments. The
architecture is **hybrid ("Approach C")** — cheap detection rides an existing loop,
while the expensive ranked menu and the apply are on-demand:

| Phase | Where it runs | Output |
|-------|---------------|--------|
| **Detection** (state + heat) | The 5-min paper manage cycle | Tags `cache:options:paper_account` rows with `rescue_state` / `heat`; publishes `cache:options:rescue_summary` (counts) for the nav badge. |
| **Ranked menu** | On demand (`rescue` command) | `cache:options:rescue:<position_id>` — the per-position advisory. |
| **Apply** | On demand (`rescue_apply` command) | Mutates the paper account behind a stale-price guard; writes an audit row. |

## Detection model

**File:** `rescue.py` · `assess_position_risk(...)`.

Each open spread is graded on a four-state ladder and assigned a **0–100 heat**:

```
ok  →  watch  →  tested  →  critical
```

The thresholds mirror the manage-cycle stops. Heat is driven by:

- **Short-strike proximity** — how close the underlying is to the short strike
  (the dominant input; ITM short = high heat).
- **Short delta** — the short leg's delta as an assignment-probability proxy.
- **P&L vs credit** — current loss as a fraction of the credit originally taken in.
- **DTE** — less time to recover raises heat.
- **GEX / regime modifiers** — sitting below the dealer **gamma flip** or pinned at a
  **put wall** adjusts heat; the market regime (from the sentiment bridge) nudges it.

`assess_open_positions()` is the **cheap** pass used for the badge: it reuses each
position's stored marks (no fresh chain fetch) to compute state/heat for every open
paper position, and `compute_rescue` does the **expensive** per-position pass (live
reprice + full candidate construction).

## Strategic context

**File:** `rescue.py` · `strategic_context(...)`. Independent of any single
candidate, this annotates the advisory with three reads (notes + boolean flags):

- **Dealer gamma** — rolling *below* the gamma flip is flagged risky (dealers sell
  into weakness, accelerating moves); resting *on a put wall* favors a bounce.
- **Regime fit** — whether the spread's direction aligns with the current trend
  regime.
- **Settlement mechanics** — **index** options are **European, cash-settled** (no
  early assignment); **equity / futures** options are **American** and carry
  assignment risk when in-the-money. (`commission.is_index_symbol` classifies.)

## Candidate builders and economics

`rescue_candidates(...)` orchestrates eleven candidate builders, constructing each
**independently** so one bad candidate can't sink the whole advisory:

| Builder | Apply kind | Idea |
|---------|-----------|------|
| `close` | execute | Buy back the whole spread now. |
| `partial_close` | execute | Close part of the size. |
| `narrow` | execute | Roll the long leg in toward the short (cuts width and max loss). |
| `convert_ic` | execute | Add the opposite-side spread → Iron Condor. |
| `convert_butterfly` | execute | Tighten to an Iron Butterfly. |
| `roll_down` | execute | Roll the spread down (same expiry). |
| `roll_out` | execute | Roll to a later expiry for more time. |
| `roll_down_out` | execute | Roll both down and out. |
| `broken_wing` | advisory | Asymmetric-width repair (place manually). |
| `inverted` | advisory | Invert the strikes (place manually). |
| `futures_hedge` | advisory | Offsetting futures position (place manually). |

**Commission-aware economics.** Commissions come from `config/commissions.toml` via
`commission.py` (`commission_for` / `futures_commission` / `is_index_symbol`) — never
hard-coded. Schwab standard rates: listed equity/ETF options **$0.65/contract per
leg**, index options $0.65 + a Cboe exchange-fee passthrough, futures **$2.25/contract
per side**, letting a leg expire **$0**. Each candidate reports `gross_cash` (before
fees), `commission`, and `net_cash` (after).

The **max loss** of a credit spread (used for both the post-adjustment metric and BP
reconciliation) follows the standard idiom:

```
max_loss = width · 100 · qty − net_credit          # net_credit = credit taken in net of commission
```

(`_spread_max_loss` in `rescue.py`.) The candidate's `new_max_loss` is recomputed for
the adjusted legs.

## Scoring and ranking

**File:** `rescue.py` · `score_candidate(...)`. Candidates are ranked by, in priority
order:

1. **Max-loss reduction per net dollar** — the dominant term; how much risk each net
   dollar removes (or, for credit actions, adds while reducing risk).
2. **Delta** — preferring adjustments that flatten directional exposure.
3. **Debit penalty** — debit actions are penalized, encoding *"never roll for a debit
   just to save it."*
4. **GEX / regime modifiers** — the same gamma-flip / put-wall / regime reads that
   feed heat nudge the score.

## Apply and the stale-price guard

**File:** `options-scanner/paper_adjust.py`. The `rescue_apply` handler dispatches to
`apply_adjustment`, which routes to the per-action primitive (`apply_close`,
`apply_partial_close`, `apply_narrow`, `apply_convert_ic`, `apply_convert_butterfly`,
`apply_roll`, `apply_inverted`).

Before mutating anything, `apply_adjustment` **re-prices the candidate's legs live**
and:

- **Aborts without mutation** if the position is no longer `OPEN`, or if the
  re-priced economics have **drifted past tolerance** from what the candidate
  promised (the GUI surfaces *"prices moved — re-review"*).
- Otherwise mutates the paper DB inside the existing cash / buying-power mechanism,
  **reconciling reserved BP** to the position's new max loss.

**Rolls** close the old position and open a new one linked via the
`parent_position_id` column. Every applied adjustment writes an audit row to the
`position_adjustments` table (`paper_account_db.insert_adjustment` /
`list_adjustments`). `rescue_apply` refuses non-paper ids — **captured signals are
advisory-only** (no paper position to mutate).

---

# Constants Appendix

A consolidated table of the load-bearing constants. The cited file governs.

| Constant / set | Value | Where |
|----------------|-------|-------|
| Composite weights | vix 0.20, put_call 0.20, breadth 0.20, rotation 0.15, sector_perf 0.25 | `scoring/__init__.py:WEIGHTS` |
| VIX sub-weights | term 0.50, vix1d 0.33, slope 0.17 | `scoring/__init__.py:VIX_SUB_WEIGHTS` |
| Trend weights | price 0.45, breadth 0.25, sector 0.20, vix 0.10 | `intraday_trend.py:TREND_WEIGHTS` |
| Put/Call thresholds | (1.3,1)(1.1,2)(0.9,5)(0.7,8)(0.0,10) | `put_call.py:PC_THRESHOLDS` |
| Breadth %>50DMA thresholds | (75,10)(65,8)(55,6)(45,5)(35,3)(0,1) | `breadth.py:BREADTH_THRESHOLDS` |
| Rotation lookback | 63 trading days; cash via $IRX | `rotation.py` |
| RRG windows | rs_window 50, mom_window 20 | `rotation.py` / `portfolio/sectors.py` |
| Trend-regime constants | bull dd −5, pullback dd −12, bear-rally dd −10, slope ±0.05, hysteresis 2 | `trend_regime.py` |
| Options score weights | rr15 pop10 theta10 iv12 iv_hv10 vega8 em12 liq5 trend10 gex4 dex4 (=100) | `options-scanner/scoring.py:DEFAULT_WEIGHTS` |
| Expected move | `price·(iv/100)·sqrt(max(dte,0.25)/365)` | `iv_analysis.py:calc_expected_move` |
| IV rank lookback | 252 trading days (HV-30 distribution) | `iv_analysis.py` |
| HV window | 30-day rolling, ann. ×sqrt(252) | `iv_analysis.py` |
| EMA / RSI / ADX / MACD | 14 / 14 / 14 / (12,26,9) | `shared/analysis_lib/technical.py` |
| Relative volume / vol profile | period 20 / 20 bins, 70% value area | `technical.py` |
| GEX per strike | `gamma·OI·100·spot²` (calls +, puts −) | `gamma_tool.py` |
| Trade primitives range | integer −100..+100 | `trade-analyzer/src/analysis/scoring.py` |

## Service cadences

| Service | Cadence |
|---------|---------|
| sentiment_svc | Composite refresh every 120 s; trend recompute gated to 15 min; rotation at startup / on demand. |
| options_svc | Auto-scan 15-min slots (08:00–15:15 CT); GEX collect 2-min slots (08:30–15:20 CT); paper auto-manage every 5 min in market hours; header tick each 30 s (skip-unchanged). |
| portfolio_svc | Live SSE ticks; throttled publish ≤ every 2 s; full rebuild every 10 min or on demand. |
| trade_svc | On-demand only (no scheduler). |
| driver_svc | Morning run once/day at 09:28 ET; perf refresh ≈ every 5 min. |

## Commissions

Source of truth: `config/commissions.toml`, loaded by
`services/options_svc/commission.py` — never hard-coded. Used by the Rescue
candidate menu's net-cash and ranking.

| Instrument | Rate |
|------------|------|
| Listed equity / ETF options | $0.65 / contract per leg |
| Index options | $0.65 / contract per leg + Cboe exchange-fee passthrough |
| Futures | $2.25 / contract per side |
| Let a leg expire | $0 |
