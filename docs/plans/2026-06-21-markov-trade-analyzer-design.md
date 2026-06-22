# Markov 2.0 — Trade Analyzer probabilistic forecast + verdict tilt

**Date:** 2026-06-21
**Status:** Design approved (brainstorming) — ready for implementation plan
**Scope:** `trade-analyzer/src/analysis`, `services/trade_svc`, `shared/contracts`,
`webgui/pages/trade.py`

## Problem

Today's Trade Analyzer produces a **single-snapshot, point-estimate verdict**:
`PositionVerdict` (1–8 wk) and `InvestorVerdict` (months+) are static linear
weighted-factor scores in [-100, +100] → BUY/HOLD/SELL with hard gates
(`trade-analyzer/src/analysis/recommendation.py`). There is no notion of *where
the score is heading* — no dynamics, no probability of crossing into BUY/SELL
territory, no forward distribution.

"Markov 2.0" adds a **probabilistic, forward-looking layer**: model the stock as
moving between discrete score-states with transition probabilities, surface the
forecast to the user, and gently tilt the verdict by the expected forward move.

## Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| Core role | Probabilistic **overlay panel** + an **injected factor** (combination), conflicts addressed |
| State definition | **Composite-score bands** (discretize the existing PositionVerdict score) |
| Transition matrix | **Hybrid** — per-symbol counts, Bayesian-shrunk toward a pooled/global prior |
| Engine scope | **PositionVerdict only** (Investor lacks daily-reconstructable history) |
| Factor strength | **Tilt modifier** — bounded ±~12 pt drift on top of the base 100-pt score |

## Section A — The method

### A1. States = composite-score bands, anchored at the decision boundaries

5 bands whose edges are the existing verdict cuts (±40), so the forecast directly
answers "P(cross into BUY/SELL)":

| Band | Score range | Verdict zone |
|------|-------------|--------------|
| S1 Strong-Bear | [-100, -40) | SELL |
| S2 Weak-Bear | [-40, -15) | HOLD |
| S3 Neutral | [-15, +15) | HOLD |
| S4 Weak-Bull | [+15, +40) | HOLD |
| S5 Strong-Bull | [+40, +100] | BUY |

### A2. Historical score reconstruction — the central wrinkle

A transition matrix needs the score *as a time series*, but the live verdict uses
factors that are **intraday-only and not reconstructable for past bars** (intraday
VWAP, intraday relative-volume, multi-timeframe EMA alignment off 1/5/15/60-min
candles).

Solution — a parallel **"Markov base score" (`composite_daily`)**: the same factor
set but every factor computed on **daily** bars (EMA alignment from the daily EMA
stack; RSI/ADX/MACD on daily; dist-52wk/RS/sector unchanged; intraday-only factors
dropped, weights renormalized to 100). Computed *identically* for every historical
bar **and** the live bar.

- The **displayed verdict** still uses the full intraday-enriched `composite_full`
  (unchanged).
- The **Markov chain runs entirely on `composite_daily`** — a self-consistent
  daily series.
- This **dissolves the feedback-loop conflict**: the chain is built from
  `composite_daily`, which never contains the Markov tilt, so injecting the tilt
  into `composite_full` cannot feed back into the matrix.

### A3. Transition matrix — hybrid (per-symbol + Bayesian shrinkage)

Over ~1–2 yr of `composite_daily`, count day-to-day band transitions → 5×5
`C_sym`. Estimate each row via Dirichlet-multinomial shrinkage to a pooled prior:

```
P[i,j] = (C_sym[i,j] + α · Prior[i,j]) / (Σ_j C_sym[i,j] + α)
```

`Prior` = normalized pooled counts across the watchlist (Top 20 + indices);
`α` ≈ 30 pseudo-counts. Data-rich rows dominated by the symbol's own behavior;
thin/empty rows lean on the prior. Pooled prior is cached (slow-moving).

### A4. Forecast outputs (visible overlay)

From current band + shrunk `P`:
- Next-day transition probabilities (current band's row).
- Multi-horizon projection via `P^n` for n = **5/10/20 trading days**.
- Derived: **P(BUY band)**, **P(SELL band)**, **E[score]** (Σ midpoint·prob),
  **persistence** P(stay), **stationary distribution** (long-run context).

### A5. Injected factor — bounded tilt modifier (resolves weighting conflict)

```
markov_drift = E[composite_daily @ 10 trading days] − composite_daily_now
tilt         = clip(markov_drift × k, −MAX, +MAX)   # MAX ≈ 12 pts
tilt        *= confidence                            # eff. sample size of current row
markov_adjusted_score = composite_full + tilt
```

- 11 base factors stay at 100 (no re-tuning; base score stays interpretable).
- Tilt is a **separate forward-looking line** in the breakdown;
  `markov_adjusted_score` shown alongside the base score.
- Overlay and factor are the **same model** — the factor is the expected value of
  the distribution the panel shows, so they can never contradict. Confidence
  weighting folds in the low-conviction gate.

## Section B — Integration (3-tier)

### B1. Pure engine — `trade-analyzer/src/analysis/markov.py`

Pure, unit-testable (score series + pooled counts in; no proxy):
`BANDS`/`BAND_EDGES`/`BAND_MIDPOINTS`, `classify_band`, `count_matrix`,
`shrink(C_sym, prior, alpha)`, `project(P, n)`, `forecast(P, current_band,
horizons)`, `drift_tilt(forecast, composite_daily_now, k, max_pts, confidence)`.

### B2. Score reconstruction — `services/trade_svc/compute.py`

`reconstruct_daily_composite(daily, spy, sector_hist) -> pd.Series` walks the daily
history computing the daily-only factor variants + renormalized `composite_daily`
(reuses `scoring.py` + `technical` daily indicators). `analyze()` then builds the
per-symbol count matrix, pulls the cached pooled prior, `shrink → forecast →
drift_tilt`, attaches a `markov` block + `markov_adjusted_score`. Fully defensive:
any failure → `markov: None`, verdict unchanged.

### B3. Pooled prior — cached, lazily refreshed

`compute_markov_prior(universe)` fans out (`parallel_map`) over watchlist + indices,
reconstructs each `composite_daily`, aggregates → normalized prior. Cached at
**`cache:trade:markov_prior`** with an age check (recompute if older than ~1 trading
day). `trade_svc` is on-demand (no scheduler) → **lazy**: `analyze()` reads the
cache; missing/stale → recompute off-thread + cache; first-ever call falls back to
a uniform/identity-leaning prior so it never blocks. (Daily scheduler tick is a
later option.)

### B4. Contract — `shared/contracts/trade.py`

Extend `TradeAnalysis` with an **additive, optional** `markov` envelope:
`current_band`, `band_labels`, `transition_row`, `horizons:[{n,dist,p_buy,p_sell,
e_score}]`, `drift`, `tilt`, `markov_adjusted_score`, `confidence`, `prior_version`.
Additive/optional → existing payloads stay valid.

### B5. Page — `webgui/pages/trade.py`

New **"Markov Forecast"** card under the Position verdict: current-band chip +
band-probability-over-horizon Highcharts chart (built once at render, updated in
place per ESM-import-map/`el.options=…;el.update()` gotchas); metric strip
(P(BUY)/P(SELL)/E[score] at 5/10/20d, persistence); `markov_drift` row +
`markov_adjusted_score` next to the base score in the Position breakdown. Pure
builders (`markov_band_chip`, `markov_forecast_figure`, `markov_metric_rows`,
`markov_drift_row`) unit-tested; `@guard` on handlers; degrades when `markov`
absent.

### B6. Data flow

```
trade.py  --enqueue 'analyze'-->  cmd:trade
trade_svc.compute.analyze(symbol):
    [existing] quote + MTF + verdicts                  → composite_full / verdict
    [new] reconstruct_daily_composite                  → composite_daily series
          markov.count_matrix → shrink(prior) → forecast
          drift_tilt                                    → markov_adjusted_score
    → TradeAnalysis(...).validate → cache:trade:analysis + publish
trade.py  version-polls + repaints (verdict + Markov card)
```

## Section C — Testing & phasing

### Testing (TDD by layer)

- **Pure `markov.py`**: band boundaries (±40/±15/clamp); `count_matrix` known
  counts; `shrink` thin→prior, rich→empirical, α monotonicity; `project` row-sums,
  stationary convergence, degenerate matrices; `drift_tilt` clamp, flat→0,
  confidence scaling.
- **Service `compute`**: `reconstruct_daily_composite` shape/range/spot-check;
  `analyze()` attaches valid `markov`, degrades to `None` on failure; prior cache
  stale→recompute, fresh→reuse, missing→uniform fallback.
- **Contract**: `TradeAnalysis` accepts payloads with and without `markov`.
- **Page**: pure builders render with sample `markov`, no-op when absent.

### Phasing (each independently shippable)

1. Pure core (`markov.py` + tests) — zero runtime risk.
2. Score reconstruction (`reconstruct_daily_composite` + tests).
3. Service wiring (pooled prior + `markov` block + contract) — verify head-less via
   Redis enqueue/cache-read.
4. Page (Markov Forecast card + drift row + adjusted score) — verify in preview.
5. Tilt activation (`markov_adjusted_score` as headline, base still visible) — last,
   isolated.

### Out of scope (YAGNI)

Investor-engine Markov, HMM/latent states, intraday-step chains, a dedicated prior
scheduler. The `markov.py` abstraction won't block these later.
