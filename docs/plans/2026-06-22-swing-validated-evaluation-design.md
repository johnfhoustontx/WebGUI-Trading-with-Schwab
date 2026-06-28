# Trade Analyzer rethink — validated swing (1–8 wk) evaluation

**Date:** 2026-06-22
**Status:** Design approved (brainstorming) — ready for implementation plan
**Scope:** `trade-analyzer/src/analysis` (factors + harness), `services/trade_svc`,
`shared/contracts`, `webgui/pages/trade.py`

## Problem

The Trade Analyzer's swing (1–8 wk) and investing (months+) verdicts are **static,
linear, hand-weighted factor scores** (`trade-analyzer/src/analysis/recommendation.py`):
each factor → `[−100, +100]`, × a fixed weight summing to 100, summed, cut at
±40 = BUY/SELL. The weights are **guesses — never validated against forward returns**.
Other structural gaps: the investing side is data-starved (Schwab `/instruments` has no
earnings surprises/guidance/FCF/sector-PE, so ~half its factors score ~0); horizon
mismatch (a 1–8 wk verdict leans on 5-minute RSI/VWAP/volume); factors are linear and
context-free (no regime conditioning, each symbol scored in isolation); and the existing
options/GEX/expected-move/sentiment engines don't feed the verdict.

## Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| Primary goal | **Predictive power, validated** — backtested against forward returns, weights learned from history |
| Data scope | **Swing first, investing later** — swing is fully backtestable on price history; the fundamental investing factors can't be backtested without a point-in-time fundamentals source (deferred) |
| Modeling direction | **Option A now, C-ready** — IC-weighted cross-sectional factor model + the shared backtest harness, structured so regime-conditional weights (C) drop in later; ML (B) is a later high-ceiling path contingent on universe expansion |

## Research grounding (multi-week horizon)

- Returns are **U-shaped in time**: reversal at very short horizons (1 wk–1 mo),
  continuation/momentum at intermediate (≈3–12 mo), reversal again multi-year. A
  multi-week model wants **intermediate momentum carried forward** + a **short-term-
  reversal entry timer**.
- The reversal↔momentum flip is **conditional** on turnover and price-to-52-wk-high
  (high turnover/PTH → continuation) — evidence weights should be condition-aware (→ C).
- **52-wk-high proximity** (George & Hwang anchoring) is a robust, geography-spanning
  momentum signal.
- Validation standard: **Spearman rank IC** + **ICIR** (mean/σ), **walk-forward**
  re-optimization, factors **lagged** to prevent leakage, scan multiple horizons
  (Alphalens methodology).

Sources: [Understanding momentum and reversal (JFE)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21000878),
[Short-term momentum/reversal, turnover & PTH](https://www.sciencedirect.com/science/article/abs/pii/S0927539824000902),
[George & Hwang 52-wk high](https://www.researchgate.net/publication/4992688_The_52-Week_High_and_Momentum_Investing),
[IC / Alphalens](https://www.pyquantnews.com/free-python-resources/real-factor-alpha-how-to-measure-it-with-information-coefficient-and-alphalens-in-python),
[Walk-forward validation](https://medium.com/@NFS303/walk-forward-analysis-a-production-ready-comparison-of-three-validation-approaches-69cd25fc9fc7),
[Short-term reversal (AlphaArchitect)](https://alphaarchitect.com/quantitative-momentum-research-short-term-return-reversal/).

## Section 1 — Architecture (offline fit → artifact → online score)

Fitting is **offline**, scoring is **online**, bridged by a versioned **artifact**.
Four pieces:

1. **Factor library** `trade-analyzer/src/analysis/factors.py` (new, pure). Each factor
   is `(daily_df) → pd.Series` (live value = last element; the same code feeds the
   backtest, so no drift). Reuses the `reconstruct_daily_composite` patterns.
2. **Backtest harness** — offline module/script in `trade-analyzer/`, run manually or
   scheduled, **never in the request path**. Pulls universe daily history via the proxy,
   computes factor Series, labels forward returns, computes IC/ICIR/spreads, walk-forward,
   fits IC-weights + calibration → writes the artifact + a Markdown research report.
3. **Live scorer** in `services/trade_svc/compute` — computes today's factors, z-scores
   cross-sectionally, applies artifact weights → composite → calibrated band. Replaces the
   swing `PositionVerdict` scoring; the rest of `analyze` stays.
4. **Artifact** `trade-analyzer/data/swing_model.json` (gitignored like other `data/`) —
   the bridge and the C-hook: weights keyed by regime (A writes a single `"all"` key; C
   adds `"trend"/"chop"/"highvol"` later — same loader, same scorer).

Data flow: `offline harness → swing_model.json → trade_svc.analyze reads it →
cache:trade:analysis → page (rank + calibrated outcome + per-factor IC)`. The Markov layer
rides the new validated composite; the Markov drift-tilt becomes an IC-tested factor.

## Section 2 — Factor library

Daily-OHLCV only (drops horizon-mismatched 5-min factors), **sign-corrected so higher =
more bullish**, winsorized. Cross-sectional z-scoring happens in the scorer.

**Core set (strong multi-week evidence):**

| Factor | Definition | Rationale |
|---|---|---|
| Intermediate momentum (12-1) | 12-mo return, skip last month | Intermediate continuation; skip-month avoids ST-reversal contamination |
| 6-1 momentum | 6-mo return skip last month | Shorter-memory momentum |
| 52-wk-high proximity (PTH) | price ÷ 252-day high | Anchoring momentum (George & Hwang) |
| Short-term reversal (STR) | −(5-day return) | 1-wk losers bounce; entry timer |
| Vol-adjusted momentum | 3-mo return ÷ trailing vol | "Sharpe momentum" |
| Trend quality | price vs 50/200-EMA stack + slope | MA signals predict the equity premium |
| Low volatility | −(60-day realized vol) | Low-vol anomaly |
| RS vs SPY | 63-day excess return vs SPY | Core cross-sectional momentum |
| RS vs sector | 63-day excess vs sector ETF | Idiosyncratic strength |

**Conditioning variable (weak alone, key for C/interactions):** turnover / relative volume.

**IC-tested candidates (kept only if they earn weight):** distance-from-52-wk-low, MACD
trend-change, acceleration (recent vs older slope), the existing **Markov drift-tilt**.

The final factor list is **decided by the harness's IC/ICIR**, not hand-chosen. Horizons
are scanned as multiple labels (≈10/20/40 trading days); the live verdict targets a
representative one (default ~20 days / 4 wks).

## Section 3 — Backtest harness (the validation)

Offline pipeline:

1. **Fit universe** — configurable liquid set (default ~S&P 100, ~100 names), 2–3 yr daily
   history via the proxy (parallelized). The **live** universe stays the watchlist; fitting
   on more names makes factor estimates trustworthy. SPY + sector ETFs for RS.
2. **Labeling** — per day T per symbol, label = **forward excess return vs SPY** over
   H ∈ {10, 20, 40} trading days (raw forward return also kept for calibration). Factors
   **lagged** (data ≤ T only).
3. **Per-factor evidence** — daily Spearman **rank IC** (factor vs forward excess return);
   **ICIR = mean/σ**; top-minus-bottom **quintile spread**.
4. **Walk-forward fit** — rolling train→test; train-window ICs set weights; measure the
   **composite's OOS IC, decile spread, hit-rate** on unseen test windows.
5. **Weighting** — default **ICIR-weighting** with non-negativity + shrinkage (drop
   weak/unstable factors); also compute a **ridge regression** alternative (handles factor
   correlation); keep whichever wins OOS.
6. **Calibration** — bucket historical composite scores into bands; per band record the
   forward-return distribution + hit-rate (P beat-SPY). Replaces the ±40 cuts; BUY/SELL
   thresholds set from OOS hit-rate.
7. **Artifact** (`swing_model.json`) — version/date, fit universe, per-factor {mean IC,
   ICIR, weight, norm mean/σ}, composite OOS IC + spread + hit-rate, score→band
   calibration, regime key `"all"` — plus a Markdown IC research report.

**Documented limitations:** survivorship bias (today's lists are survivors), regime
non-stationarity (a 2–3 yr fit may not hold), thin *live* cross-section (20 names).
Validation reduces self-deception; it does not guarantee forward performance.

## Section 4 — Live scoring, verdict, and meeting the current engine

**Live scorer** (`services/trade_svc/compute`, on-demand): load the cached
`swing_model.json` (lazy, versioned — same pattern as the Markov prior). Compute the
symbol's current factors, **z-score** each against a periodically-cached
**`cache:trade:universe_factors`** snapshot (the watchlist's current factor values,
refreshed daily; falls back to the artifact's historical norm if off-watchlist/stale),
apply artifact weights → **validated composite** → calibrated **band → expected forward
excess-return + hit-rate + BUY/HOLD/SELL** + a cross-sectional **percentile**. Per-factor
**contribution = weight × z-score**, carried with each factor's stored IC/ICIR.

**Meeting the current engine:**
- **Replaces** the swing `PositionVerdict` *scoring*; the **hard gates** (below-200-EMA,
  earnings window, sector downtrend) are **kept as advisory overlays** (filters, not return
  predictors; IC-testable later).
- **Markov preserved + upgraded**: runs on the *validated* composite (not the heuristic
  `composite_daily`); the drift-tilt is an IC-tested candidate factor.
- **Investor verdict untouched** (deferred).
- **Transition safety**: the legacy heuristic score is kept behind an expandable "legacy
  view" for the first release (old-vs-new sanity check), removed once trusted.

**Verdict UI** (Position card, still the 3-equal-frame row): the headline becomes the
**calibrated outcome** — e.g. *"BUY · top 12% setup · ≈ +3.1% / 4 wk · beat-SPY 63%"*. An
**evidence expander** lists each factor's z-score · weight · contribution · historical IC
(greyed if dropped), plus the **model's own track record** (version/date, OOS IC, hit-rate).
Engine-free (3-tier).

**Contract:** extend `TradeAnalysis` with an additive optional **`swing_model`** block
(validated score, percentile, expected-return, hit-rate, per-factor contributions+IC, model
version + OOS stats).

## Section 5 — Testing & phasing

**Testing (TDD by layer):**
- **Factor library** (pure): each factor on synthetic frames → known values;
  sign-correctness; historical-Series-last == live value; short-history/NaN handling;
  winsorization.
- **Harness**: forward-excess-return labeling; **no-leakage** (T uses ≤ T); a perfectly-
  predictive factor → IC ≈ 1, random → ≈ 0; walk-forward windows don't overlap;
  ICIR-weights drop weak/negative factors; calibration bands monotone; artifact round-trips.
- **Live scorer**: fixed artifact + synthetic snapshot → expected composite/band/verdict;
  off-watchlist → historical-norm fallback; **missing artifact → degrade to legacy, never
  crash**.
- **Contract / Page**: `swing_model` additive-optional; pure builders render the calibrated
  headline + evidence rows, no-op when absent.

**Acceptance gate:** the harness must show **positive OOS IC + a meaningful decile spread
on real data**, or the model is **not** promoted to primary (stay on legacy) and the factor
set is iterated first.

**Phasing (each shippable):**
1. Factor library + tests — pure, zero runtime risk.
2. Backtest harness + tests → run on real data → IC research report + artifact.
   **← decision gate** (review OOS IC before wiring live).
3. Live scorer + `swing_model` contract — lazy artifact + universe snapshot + calibrated
   verdict; head-less Redis verify.
4. Page — calibrated outcome headline + evidence expander + model track-record; legacy behind
   an expander; browser-verify.
5. Cutover — validated becomes the primary swing verdict; Markov rides it.
6. *(Later)* C: regime-conditional weights (same harness); B: ML if the universe is expanded.

**Out of scope (YAGNI):** Investor validation, ML/regime now, intraday factors, a
fundamentals-history source.
