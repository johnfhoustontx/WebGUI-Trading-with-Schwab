# Market Regime — blended (soft-membership) structural classifier — design (2026-07-23)

A third classification axis for the app: **market STRUCTURE** — *how* price is moving
(Mean Reversion / Trending / Breakout / Choppy / Crisis), complementing the existing
direction × aggression five-state classifier (*which way* and *how hard*). Built
**soft-first**: the primary output is a **membership vector** (each regime a continuous
0–1 weight) so regime *transitions* are gradual, first-class, and readable — the hard
label is derived for display only.

## Purpose

Strategy-family selection is the app's weakest decision: the driver's worst realized
losses were call credit spreads sold into trend days — a *structure* error, not a
direction error. This classifier answers "what kind of tape is this?" and, critically,
"what is it *becoming*?":

| Regime | Strategy focus (later phases) |
|---|---|
| Mean Reversion | range structures (IC / condor / fly) score up; directional down |
| Trending | credit spreads WITH the trend; directional with trend |
| Breakout | long options / debit verticals (momentum entries) |
| Choppy | size down / stand down |
| Crisis/Stress | halt + capital preservation |

**Phase 1 is context-only** (Sentiment page + driver `market_read` + recorded history).
No gate, no tilt, no sizing change until the offline validation (Phase 2) shows the
memberships stratify forward strategy-family P&L — the same discipline as the five-state
rollout and the driver's shadow-mode directional gate.

## Why soft memberships (the design's core decision)

Every input is already continuous (ADX, EMA slope, BB-width percentile, ATR percentile,
VIX spike, flip distance). A hard classifier would threshold these and argmax; blending
just **doesn't throw the vector away**:

- **No threshold-flapping.** ADX drifting 24→26 moves `trending` 0.55→0.65 instead of
  flipping a label every read. Small input changes → small output changes.
- **Transitions become the signal.** With the vector sampled every 5 min and smoothed by
  two EMAs, "Mean Reversion → Trending, ~60% complete" is a direct readout
  (fast-EMA vs slow-EMA divergence), not an inference from a label flip.
- **Consumers blend instead of gate** (Phase 3): strategy tilt = Σ membershipᵣ·tiltᵣ
  (the `state_family_tilt` shape, weighted over five regimes); driver size scalar =
  Σ membershipᵣ·size_factorᵣ — gradual de-risking as choppy/crisis membership builds,
  no cliff.
- **More validatable.** Continuous memberships vs forward returns give rank correlations
  over every observation instead of five thin label buckets.

## Architecture

```
sentiment-dashboard/scoring/market_regime.py     PURE — evidence ramps, memberships,
                                                 smoothing, transition, commit (TDD'd)
sentiment-dashboard/scoring/volatility.py        PURE — ATR + Bollinger-width helpers (new)
services/sentiment_svc/compute.py                compute_market_regime() — fetch + assemble
services/sentiment_svc/scheduler.py              REGIME_INTERVAL_SEC = 300 slot (new)
services/sentiment_svc/handlers.py               _REGIME holder (lock-guarded, mirrors _TREND)
services/sentiment_svc/intraday_history_db.py    new regime_intraday table (same DB file)
shared/contracts/sentiment.py                    new additive RegimeState contract
cache:sentiment:regime                           the published view (skip_unchanged)
webgui/pages/sentiment.py                        regime mix panel (Phase 1 UI)
```

- **Owner: sentiment_svc** — it already fetches SPY intraday bars, breadth, and VIX on a
  schedule, holds the `_TREND` persisted-state idiom, and owns the sibling classifiers.
- **Own 5-min slot**, NOT a speedup of the 15-min trend recompute: the trend gauge and
  its 30-day structural work stay at their cadence. `regime_due(now, last)` mirrors
  `trend_due` (RTH every 5 min; off-hours the regime is not recomputed — the last
  session's committed state persists with an `as_of` stamp).
- **Cross-domain reads (Redis only, defensive):** dealer-gamma inputs come from
  **`cache:options:matrix`** (per-symbol spot + `gex_regime` above/below flip, 1-min
  fresh) — the same read-a-sibling-cache pattern as `flow_skew` in
  `compute_intraday_trend`. Missing/stale → that evidence drops out (see Degradation).
- **Fetch sharing:** the 5-min slot fetches SPY 5-min bars + daily via the proxy; the
  fetch is memoized with a ~4-min TTL so the 15-min trend recompute reuses it (net new
  Schwab calls ≈ the 5-min slot's own — roughly +150/day, noise vs the dashboard poll).

## The membership model

`market_regime.score_regimes(evidence) -> RegimeScores`:

- **Raw intensities** `raw[r] ∈ [0,1]` per regime — each an average of that regime's
  evidence ramps (below), with per-input confidence weights (the `blend_aggression`
  idiom). Raw values are NOT forced to compete.
- **Normalized memberships** `memberships[r] = raw[r] / Σraw` — the display/consumer
  vector — computed ONLY when `Σraw` clears a floor.
- **Confidence** `= max(raw)`. When `max(raw) < UNCLEAR_FLOOR` (0.25), the state is
  **"Unclear"**: memberships published as the raw-proportional vector but flagged
  `unclear=True`, headline label "Unclear", consumers treat as neutral. Normalization
  must never manufacture certainty out of five weak scores.

All ramps are `ramp(x, lo, hi)` = clamp((x−lo)/(hi−lo), 0, 1) — pure, testable, no
step functions. All thresholds are named module constants (promote to a TOML later only
if live tuning demands it, per the `flow_alerts.toml` precedent).

## Per-regime evidence (computed on SPY 5-min bars unless noted)

**Shared inputs per sample:** ADX-14 (Wilder's, `technical.calculate_adx`) on 5-min
bars; EMA20 slope in ATR units; Bollinger width (20, 2σ) + its percentile vs the
trailing ~5 sessions; ATR-14 + percentile; session VWAP side/hold
(`session_structure`); opening-range break state (`session_structure`); wick
exhaustion/defense (`rejection_defense`); profile shape (`profile_shape`); relative
volume; VIX / VIX1D / VIX3M quotes; $SPX & SPY `gex_regime` + flip distance from
`cache:options:matrix`.

- **Mean Reversion** = avg( ramp(20−ADX, 0, 8), flat-EMA (1−ramp(|slope|, 0.05, 0.25)
  ATR/bar), BB width in mid-percentile band, balanced profile (`profile_shape`),
  **above-flip bonus** (positive dealer gamma structurally dampens moves) ).
- **Trending** = avg( ramp(ADX, 18, 30) × rising-ADX factor, EMA stack slope,
  band-hugging (fraction of last 12 closes in the outer BB quartile), VWAP held one
  side ≥ 80% of session, OR break held ).
- **Breakout** = squeeze-release: ramp(20th_pctile − width_pctile_prior, …) ×
  ramp(width_expansion_rate, …) × ramp(rel_vol, 1.2, 2.0) × OR-break-fresh. Decays
  fast by construction (the "prior squeeze" term ages out) — breakout hands off to
  Trending naturally.
- **Choppy** = avg( both-sided wick score (`rejection_defense` firing in both
  directions), failed-OR-break count (break then re-cross), EMA20 whipsaw count ramp,
  high-ATR-with-low-ADX (effort without direction) ).
- **Crisis/Stress** = max( ramp(VIX1D day-over-day spike, +10%, +35%),
  term-inversion depth (VIX1D>VIX, VIX>VIX3M), ramp(ATR percentile, 0.85, 0.97),
  unfilled-gap > 1%, **below-flip × deep-negative-GEX** ). `max`, not avg — any single
  crisis tell is sufficient.

## Temporal blending & transitions

- Two EMAs over the membership vector, **defined in wall-clock half-lives** so cadence
  and smoothing are independent knobs: `FAST_HALF_LIFE_MIN = 15`,
  `SLOW_HALF_LIFE_MIN = 60` (per-sample α derived from Δt).
- **Transition object** published every sample:
  `{from, to, progress, direction}` where `to` = regime with the largest positive
  (fast − slow) divergence above a floor, `from` = largest negative, `progress` =
  scaled divergence. Reads as "Mean Reversion → Trending, 60%". No divergence above
  floor → `transition: null` (stable regime).
- **Headline label** (display only): dominant fast-EMA membership must exceed the
  runner-up by `COMMIT_MARGIN = 0.10` for `COMMIT_READS = 2` consecutive samples to
  flip — a generalization of `trend_regime.commit_state`. Consumers read the vector;
  the label is allowed to lag.
- **Crisis asymmetry — the one exception to smoothness.** A raw crisis intensity
  ≥ `CRISIS_ATTACK = 0.7` bypasses the EMAs (fast attack: crisis membership jumps to
  the raw value immediately, label commit rule waived); decay follows the slow EMA.
  Additionally the crisis inputs alone (VIX quotes already fetched + a
  `cache:options:matrix` read) are re-evaluated inside the existing **120 s composite
  refresh**, so crisis onset latency is ≤ 2 min rather than ≤ 5 — at zero extra fetches.

## Contract, cache, recording

- **`shared/contracts/sentiment.py: RegimeState`** (additive):
  `{ts, as_of, memberships{5}, raw{5}, confidence, unclear, label, committed_label,
  transition{from,to,progress}|null, evidence[str], version_info}`.
- **`cache:sentiment:regime`** — published by the 5-min slot (and by a crisis-attack
  re-publish), `skip_unchanged=True`.
- **Recording from day one:** new `regime_intraday` table in the existing
  `sentiment_intraday.db` (`intraday_history_db` — same connect/prune idiom, same
  `_INTRADAY_LOCK`, pytest → `:memory:` per the house isolation rule): one row per
  sample `(ts, mr, tr, bo, ch, cr, confidence, label)`, rolling ~30 sessions (more than
  the 5-day sentiment window — this is tuning + validation data). A
  **`cache:sentiment:regime_history`** view (today's points) feeds the UI chart.

## Phase 1 UI (Sentiment page)

- A **"Market Regime" panel** on `/sentiment`: the committed label + confidence chip,
  the transition line ("Mean Reversion → Trending · 60%"), and a **stacked-area chart
  of today's membership vector** (the Markov band-probability chart pattern — plain
  chart, synthetic category axis per the stockChart gotcha; regime colors from
  `config/theme.toml [charts]`).
- One line added to the driver's `market_read` context block (label + top-two
  memberships + transition) — context only, `guardrails.py` untouched.
- Unclear/degraded states render honestly ("Unclear — evidence weak").

## Degradation (defensive, house rule)

Per-input confidence weights: a missing input (matrix cache stale > 5 min, VIX quote
failed, too few bars premarket) zeroes that input's weight rather than defaulting its
value; a regime whose every input is missing gets `raw = 0`. Everything degrades toward
"Unclear", never raises, never blocks the composite refresh (`run_regime` wrapped
best-effort like `run_flow_alerts`).

## Phases

1. **Classify + surface (this design):** pure modules TDD'd → service wiring →
   contract/cache → recording → Sentiment panel + driver context line. Context-only.
2. **Offline validation:** extend the `validate_market_state.py` harness — reconstruct
   daily-resolution memberships over ~5 yr SPY history (bar-derivable inputs only;
   document that VIX1D/gamma inputs are excluded, as the five-state validation did) and
   measure forward strategy-family P&L stratification by membership (rank IC per
   family). Publish the honest result in the report before ANY consumer wiring.
3. **Consumers (each gated on Phase 2 evidence, separately reviewable):**
   scanner blended tilt (bounded, post-grade like `state_family_tilt`); driver size
   scalar (guardrails change — shadow-mode first, like the directional gate);
   Matrix-page regime column.
4. **(Optional, later)** learn the regime transition matrix from the recorded history
   via the existing `markov.py` machinery → expected-path annotations ("breakouts here
   typically resolve into Trending within ~45 min").

## Cadence & cost summary

| What | Cadence | New Schwab calls |
|---|---|---|
| Regime recompute (5-min bars, full evidence) | every 5 min RTH | ~2/run (SPY intraday + daily, TTL-shared with the trend recompute) ≈ +150/day |
| Crisis-input re-check | inside the existing 120 s refresh | 0 (reuses fetched VIX + a Redis read) |
| Publish / record | per sample, `skip_unchanged` | 0 |
| Claude calls | none | 0 |

## Rejected alternatives

- **Hard 5-way label with hysteresis** — loses the transition information, inherits
  threshold-flapping, and forces every consumer into cliffs; the memberships are
  computed anyway, so hard-only discards value for zero savings.
- **HMM / learned regime model** — a fitted hidden-Markov or clustering model is the
  academic standard but is unexplainable, needs a training pipeline, and the app's
  validation culture (thin-edge honesty) favors transparent ramps whose every number
  can be read off the chart. Revisit only if Phase 2 shows the ramps have no edge.
- **Computing in options_svc (it has the 1-min chains)** — the evidence is mostly
  SPY-bar + VIX + breadth shaped, which sentiment_svc already owns; options_svc only
  contributes the flip, which travels fine over `cache:options:matrix`.
- **Sub-5-min base cadence** — 5-min bars only finalize every 5 min; faster sampling
  re-reads unfinished bars. The crisis fast path covers the one place lag is expensive.

## Key tunables (initial values, all named constants in `market_regime.py`)

`REGIME_INTERVAL_SEC=300 · FAST_HALF_LIFE_MIN=15 · SLOW_HALF_LIFE_MIN=60 ·
UNCLEAR_FLOOR=0.25 · COMMIT_MARGIN=0.10 · COMMIT_READS=2 · CRISIS_ATTACK=0.7 ·
ADX ramps 18→30 (trend) / 20−8 (mean-rev) · BB squeeze pctile <20th ·
ATR crisis pctile 0.85→0.97 · VIX1D spike +10%→+35% · rel-vol 1.2→2.0`

Initial values are informed guesses; the recorded history exists precisely so they can
be tuned against real sessions before any consumer depends on them.
