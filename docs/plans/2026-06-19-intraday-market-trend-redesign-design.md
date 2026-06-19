# Intraday Market Trend redesign — design

**Date:** 2026-06-19
**Status:** Approved (brainstorming) — implementation plan next
**Scope:** Sentiment tab "Market Trend" panel + the sentiment bridge `trend_regime` block

## Problem

The current Market Trend reading (`sentiment-dashboard/scoring/trend_regime.py`)
is a **5-state daily classifier** over SPY *daily closes*: close vs 50/200-DMA,
the 20-bar slope of the 200-DMA, and 252-day drawdown, with a 2-day hysteresis.
It is structurally slow by construction — the 200-DMA and 252-day drawdown barely
move day to day, so the "Today" needle is effectively frozen intraday. Recomputing
it every 15 minutes would show no change.

We want a **more accurate and more responsive** Market Trend that is recomputed
**every 15 minutes** and genuinely reflects intraday action, while staying
consistent with the existing intraday composite pipeline (`live_composite.py`).

## Decisions (from brainstorming)

1. **Replace the model entirely** with a purpose-built intraday trend indicator
   (not a needle-only overlay on the old daily regime).
2. **Inputs:** all four families — SPY/$SPX multi-timeframe price, market breadth
   internals, VIX/volatility, and sector participation/rotation.
3. **Bridge:** drive the GUI with the new indicator **and** publish it to the
   bridge, mapping the new 0–100 score back onto the existing 5-state vocabulary
   so Options Scanner's `regime_filter` keeps working unchanged.
4. **Second gauge** = the prevailing trend over the **last ~30 sessions** (a
   rolling structural backdrop), not a "30-days-ago" snapshot. Mirrors how the
   Market *Sentiment* panel pairs "Today" with "30-Day Avg".
5. **Approach A** — a weighted composite of normalized directional sub-scores,
   built in the same idiom as `live_composite.py` (pure `scoring/` module →
   `ScoreResult` → confidence-weighted `blend`).

## The Market Trend Score (the math)

A single **directional** 0–100 score (50 = neutral, 100 = max bull, 0 = max
bear). Distinct from the existing 1–10 *contrarian* composite, so it gets its own
directional sub-scores, but reuses the same `ScoreResult` + confidence-weighted
blend idiom.

| Sub-score | Weight | Computed every 15 min |
|---|---|---|
| **Price / MTF** | **45%** | SPY intraday 5-min + 15-min bars + daily closes → EMA alignment across timeframes (`shared/analysis_lib/technical.calculate_ema_alignment`) + price-vs-VWAP + MACD/RSI. **ADX scales how far the score departs from 50** — strong trend → extremes, chop → stays near 50. Core accuracy lever. |
| **Breadth** | **25%** | $ADVN/$DECN ratio + net, $SPXA50R (% > 50DMA), NYHGH/NYLOW → 0–100. Live intraday. |
| **Sector participation** | **20%** | # of 11 GICS sectors green + cyclical-vs-defensive leadership (reuses the dual-momentum already in `compute_live`). |
| **VIX / vol** | **10%** | $VIX level + intraday change + $VIX1D term. Also a **confidence dampener** — spiking vol lowers trend confidence. |

**Blend:** `trend = Σ(wᵢ·sᵢ·cᵢ) / Σ(wᵢ·cᵢ)` — identical confidence-weighted formula
as `scoring/composite.blend`; a low-confidence input cannot dominate.

**Responsiveness vs. whipsaw:** the published needle is EMA-smoothed over ~2–3
fifteen-minute reads (≈30–45 min span). It moves through the session but isn't
jumpy; ADX-scaling + confidence-weighting keep it from over-committing in chop.

## The two gauges

- **Live gauge ("Today"):** the EMA-smoothed 0–100 score, recomputed every 15 min.
  The score *is* the needle (no anchor+nudge). A press-and-hold popup (same pattern
  as the component table) shows the four sub-scores + confidences.
- **30-Day gauge:** the prevailing trend over the last ~30 sessions — the slow
  structural backdrop. Computed from **daily** bars using the same Price + Sector
  sub-score functions on daily timeframes (daily EMA stack 8/21/50/200 + sector
  daily performance), taken as the mean reading across the last 30 sessions.

**Data-availability nuance (accepted):** intraday internals (live A/D breadth,
VIX1D term) aren't stored historically per past session, so the 30-day gauge leans
on what is reconstructable from daily history (price structure + sector performance
+ daily VIX). It is a faithful *structural* trend, not a perfect day-by-day replay
of the live model. The live gauge uses all four inputs; the 30-day uses the
daily-reconstructable subset.

## 5-state bridge mapping

The live 0–100 score maps to the existing state vocabulary so `regime_filter`
keeps working unchanged:

| Score band | State |
|---|---|
| 80–100 | `bull_trend` |
| 70–80 | `pullback_in_bull` |
| 30–70 | `range` |
| 20–30 | `bear_rally` |
| 0–20 | `bear_trend` |

`range` is deliberately the dominant middle band (30–70) — the needle must clearly
commit before flipping to a directional regime, reinforcing the accuracy bias.

**Hysteresis on the discrete state only:** a ±5 buffer around thresholds + require
2 consecutive 15-min reads to flip the *published* state, so Options Scanner's scan
recipes don't thrash every 15 min, even while the GUI needle moves continuously.
The mapping is a monotonic approximation of the original (non-1-D) state semantics,
chosen to preserve consumer compatibility; `raw_state` may still carry nuance.

## Architecture & wiring

Honors the 3-tier rule (webgui imports only nicegui + shared.bus + shared.contracts;
all engine work in the service).

**New pure module — `sentiment-dashboard/scoring/intraday_trend.py`** (beside the
other `scoring/` modules so `scoring` resolves in the service):
- `score_price(mtf_inputs) -> ScoreResult`, `score_breadth_dir(...)`,
  `score_sector_participation(...)`, `score_vix_context(...)` — directional 0–100,
  pure, no tk.
- `blend_trend(scores, confs) -> (score, conf)` with `TREND_WEIGHTS` (45/25/20/10)
  the single source of truth.
- `score_to_state(score)` (band table) + `commit_trend_state(raw, history, prev)`
  hysteresis (adapted from the existing `commit_state`).

**Proxy client — `proxy_client.py`:** add
`get_intraday_history(symbol, minutes=15, days=1)` (calls `/pricehistory` with
`frequencyType=minute`), mirroring `get_daily_history`.

**Service — `services/sentiment_svc/compute.py`:**
- `compute_intraday_trend(schwab, sector_data, prior_state_history)` — fetches SPY
  intraday bars + breadth + VIX, reuses the sector data `compute_live` already
  pulls, calls the scoring module, returns
  `{score, smoothed_score, state, label, description, sub_scores, confidence, …}`.
- `compute_30d_trend(spy_daily, sector_daily)` — the daily-reconstructable
  structural reading for the second gauge.
- Both wired into `derive_composite_extras`, **replacing**
  `build_trend_dict`/`trend_30d_ago` while keeping the `trend` / `trend_30d_ago`
  keys so the page contract holds; new fields added.

**Cadence — `services/sentiment_svc/scheduler.py`:** add a `trend_due(now, last)`
15-min gate (mirrors options_svc `gex_due`); the heavier intraday-trend fetch runs
only every 15 min, while the existing 120 s composite loop and bridge publish pick
up the latest cached trend. EMA-smoothing + hysteresis history persist in service
state across ticks.

**Bridge — `live_composite.build_bridge_payload`:** feed it the mapped state; keep
`sma_*`/`drawdown` (from daily) for `regime_filter` back-compat; add `trend_score`
+ `sub_scores` additively.

**webgui — `pages/sentiment.py`:** `trend_gauge_value` returns the score directly;
second gauge reads the 30-day score; add the sub-score press-and-hold popup. Pure
builders unit-tested.

## Testing

- Scoring module: boundary case per sub-score + missing-data (conf 0); blend;
  `score_to_state` band edges; `commit_trend_state` hysteresis.
- Service compute: shape + defensive degradation (no intraday data → safe default).
- webgui builders: needle == score; popup sub-score rows.
- Bridge: score→state mapping + back-compat fields present.
- Drift-guard on the 15-min cadence constants if referenced in two places.

## Out of scope

- Changing the contrarian sentiment composite (1–10) — untouched.
- Updating `regime_filter` itself — the 5-state mapping keeps it working as-is.
- Portfolio/Trade/Driver pages.
