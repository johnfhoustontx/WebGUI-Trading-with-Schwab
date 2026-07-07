# Five-State Classifier — Tier 3 (Validation + Integrations) — Design

**Date:** 2026-07-07
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorm complete) — implementation plan (item 11 first) to follow.
**Depends on:** the shipped five-state classifier (Phases 0–5,
[design](2026-07-07-five-state-market-classifier-design.md) /
[plan](2026-07-07-five-state-market-classifier-plan.md)).

## Motivation

The five-state market classifier (Bullish / Lack of Bullishness / Neutral / Lack of Bearishness /
Bearish) is live and drives `regime_filter`'s PCS/CCS gating. Tier 3 was deferred as a separate
design: **item 9** (state → Swing-Scanner strategy-family bias), **item 10** (state → Driver decision
packet), **item 11** (formal IC/backtest validation that the five states stratify forward returns).

The house discipline — set by the validated-swing-model (honest OOS IC ≈ +0.037, 5/13 folds negative,
labeled not overtrusted) — is **validate before trust**. Items 9 and 10 wire the states into strategy
selection and the autonomous money-path; item 11 measures whether the states actually predict forward
returns. So **item 11 is the gate for 9/10** and is built first.

## Key decisions (from brainstorming)

1. **Sequencing:** validate first (item 11), gate 9/10 on the result.
2. **Reconstruction for item 11:** daily-OHLCV **core** reconstruction over ~5yr SPY history (the
   live recording has ~1 day; most inputs — skew/sector-flow/session/order-flow — have no long
   historical record). Honest caveat: validates the **core two-axis concept** on daily-reconstructable
   inputs, not the full live signal set.
3. **Result-gated weighting:** the item-11 numbers set how much weight 9/10 give the state (real
   weight / low-weight tilt / display-only). No magnitude is pre-committed.
4. **Item 10 is decider-context-only:** `regime_filter` already hard-gates the scanner menu the driver
   reads, so no redundant hard guardrail — `guardrails.py`'s code-authoritative core is untouched.

## Item 11 — offline validation harness (the concrete build for this pass)

**New offline script** `sentiment-dashboard/validate_market_state.py` — run **manually**, NEVER in a
request path (mirrors `trade-analyzer/fit_swing_model.py`). Pulls ~5yr SPY daily OHLCV via the proxy
(+ `$VIX` daily for a regime split).

**Reconstruct the daily committed state** per historical day, reusing the REAL pure modules so the
study tests the actual grid (not a lookalike):
- **Direction axis** — a NEW pure `daily_direction_score(daily_bars) → 0–100` (EMA20/50/200 stack +
  20-day slope + daily RSI, ADX-scaled toward 50 in chop). Explicitly a **daily proxy** for the live
  intraday direction blend — the core-reconstruction limitation.
- **Aggression axis** — the ACTUAL `scoring/effort.py:score_effort` (daily OHLCV) +
  `scoring/rejection_defense.py:score_rejection_defense` (daily candles), combined via the ACTUAL
  `scoring/aggression.py:blend_aggression` (effort+rejection subset; skew/flow/session/profile/
  order_flow absent → drop out of the confidence-weighted blend).
- **State** — the ACTUAL `scoring/market_state.py:classify_market_state(direction, aggression)`,
  threaded through the ACTUAL `scoring/trend_regime.py:commit_state` 2-day hysteresis → one committed
  state per day.

**Forward returns** — per day, SPY forward **5-day and 20-day** returns (raw + a realized-vol-adjusted
variant). Causal: the state at close of day *t* predicts the *t → t+H* return.

**Metrics** (the honest edge check):
- Per-state **mean forward return + hit-rate P(fwd>0) + count**, at 5d and 20d.
- **Ordinal IC** — states → expected-direction rank (Bullish +2 · Lack-of-Bearishness +1 · Neutral 0 ·
  Lack-of-Bullishness −1 · Bearish −2), rank-correlated with forward returns (reuse
  `trade-analyzer/backtest.py` primitives where they fit).
- **Monotonicity** — do the per-state means increase Bearish→…→Bullish? (the load-bearing question).
- **VIX-regime split** — does it hold in calm vs stressed tapes?

**Output** — a markdown research report + a small JSON artifact, both gitignored under `data/` (like
`swing_model.json`). The report states up front it validates the **core two-axis concept on
daily-reconstructable inputs**, not the full live signal set — a positive result is *encouraging, not
conclusive*.

**Acceptance:** the harness runs end-to-end over real 5yr history and emits per-state stratification +
ordinal IC + the honest caveat. (There is no pass/fail threshold — the NUMBERS are the deliverable and
the gate for 9/10.)

## The gate → items 9/10 (designed here; built + tuned after item 11's result)

After running item 11 I present the numbers, and they set 9/10's weight:
- **Positive ordinal IC + extreme states stratify** → build 9/10 with real (bounded) weight.
- **Thin/near-zero** → build 9/10 as low-weight tilts / context-only.
- **Negative / no stratification** → scope 9/10 to display-only (show the state; don't bias
  scoring/decisions); label informational.

### Item 9 — Swing-Scanner strategy-family bias
The market-wide committed state (read from `cache:sentiment:composite` `derived.trend` / the bridge)
overlays the swing scanner's per-symbol `view` as an **additive, bounded fit tilt** in
`options-scanner/strategy_scoring.py:score_strategy` (which already scores `fit_directional` +
`fit_vol` off `infer_market_view → {direction, conviction, vol_regime}`):
- Neutral → iron-condor / premium-selling boost.
- Lack-of-Bearishness → PCS boost (cheap, undefended puts — the framework's insight).
- Lack-of-Bullishness → CCS boost + long-call penalty (exhaustion at highs).
- Bearish → debit-put favor + credit stand-down.
- Bullish → long / PCS boost.
Tilt **magnitude scales with the measured edge** (small if thin). Defensive: no state → no tilt. The
scanner stays single-symbol (the state is market context). Additive to existing scoring; the FLAT
scanner's tuned composite + the driver's sizing are untouched.

### Item 10 — Driver decision-packet context
Add the committed state (label + evidence + ordinal lean) to `services/driver_svc/compute.py:build_packet`'s
`market` dict (currently `{vix, …}` from `fetch_market_context`) so the **Claude decider sees it as
reasoning context**. **No redundant hard guardrail** — `regime_filter` already hard-gates the scanner
menu the driver reads (`cache:options:scan`), so `guardrails.py`'s code-authoritative core is left
untouched. Optionally a soft advisory line in the packet prompt. Low-risk, additive.

## Architecture / data flow

```
validate_market_state.py (offline, manual)
   ├─ proxy: 5yr SPY daily OHLCV + $VIX daily
   ├─ per day: daily_direction_score  ─┐
   │           effort + rejection ─ blend_aggression ─┤
   │                                                  ▼
   │           classify_market_state → commit_state → committed state (per day)
   ├─ forward 5d/20d SPY returns (causal)
   └─ per-state mean/hit-rate/count + ordinal IC + monotonicity + VIX split
        → data/market_state_validation_report.md + .json  (gitignored)

[after result] item 9: state → strategy_scoring.score_strategy fit tilt (bounded, edge-scaled)
              item 10: state → driver build_packet market context (decider sees it; no new guardrail)
```

## Error handling / testing

- The harness is offline + defensive (a bad symbol/day is skipped, never aborts the run) — like
  `fit_swing_model.py`. The reconstruction reuses the already-tested pure modules; the NEW pieces
  (`daily_direction_score`, the forward-return + per-state stat functions, the ordinal-IC mapping) are
  PURE and unit-tested (TDD). The proxy-fetch orchestration is not unit-tested (mirrors
  `fit_swing_model.py`).
- Items 9/10 (later): item 9's tilt is a PURE function of (state, family) unit-tested + folded
  defensively into `score_strategy`; item 10 is an additive dict field + a packet line, unit-tested.
  Both degrade to no-op when the state is absent.

## Files (this pass — item 11)

**New:**
- `sentiment-dashboard/validate_market_state.py` — the offline harness (orchestration).
- `sentiment-dashboard/scoring/daily_direction.py` — PURE `daily_direction_score` (+ the per-state
  forward-return stat + ordinal-IC helpers, or reuse `trade-analyzer/backtest.py`).
- `repo_paths` — `MARKET_STATE_VALIDATION_REPORT` / artifact path (gitignored `data/`).
- Tests: `sentiment-dashboard/tests/test_daily_direction.py` + the stat/IC helpers.

**Later (9/10):** `options-scanner/strategy_scoring.py` (fit tilt) + its tests;
`services/driver_svc/compute.py` `build_packet`/`fetch_market_context` (market-state context) + tests.

## Deferred / out of scope
- The full-fidelity live-state validation (accumulate `market_state_history_db` over months, then
  validate the EXACT live states incl. streaming inputs) — a future re-run once data exists.
- Regime-conditional five-state weighting — same harness, later.

## Related
- Validated-swing-model precedent (`docs/plans/2026-06-22-swing-validated-evaluation-*`) — the
  offline-fit / honest-IC discipline this mirrors.
- `trade-analyzer/backtest.py` — IC / quantile-spread primitives to reuse.
- The recording (`services/sentiment_svc/market_state_history_db.py`) — for the future live-state re-run.
