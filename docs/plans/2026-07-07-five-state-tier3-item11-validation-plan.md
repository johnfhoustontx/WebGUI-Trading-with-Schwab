# Five-State Validation (Item 11) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** An offline harness that reconstructs the five-state market classifier over ~5yr of SPY daily history and measures whether the states stratify forward returns (per-state mean/hit-rate + ordinal IC), producing a markdown report — the honest edge-check that gates Tier-3 items 9/10.

**Architecture:** New PURE `sentiment-dashboard/scoring/daily_direction.py` (a daily direction-score proxy + per-state forward-return stats + ordinal-IC helpers), reusing the REAL `scoring/effort.py` + `scoring/rejection_defense.py` + `scoring/aggression.py` + `scoring/market_state.py` + `scoring/trend_regime.py` so the study tests the actual grid. A NEW offline orchestration `sentiment-dashboard/validate_market_state.py` (run MANUALLY, never in a request path — mirrors `trade-analyzer/fit_swing_model.py`) fetches SPY + $VIX daily via the proxy, reconstructs the daily committed-state series with hysteresis, computes the metrics, and writes a report + JSON artifact (gitignored under `data/`).

**Tech Stack:** Python 3.11, pytest (TDD for the pure helpers), the schwab-proxy `/pricehistory`, the existing pure `scoring/` modules. Design doc: `docs/plans/2026-07-07-five-state-tier3-validation-integrations-design.md`.

**Conventions:**
- `scoring/` modules are PURE: no tk, no I/O, no pandas, no `shared.analysis_lib`; self-contained math; take a list of `{open,high,low,close,volume}` dicts (oldest first) — mirror `scoring/effort.py`. Confidence in `[0,1]`, defensive, JSON-safe floats.
- Tests run from inside `sentiment-dashboard/`: `cd "D:/WebGUI Trading with Schwab/sentiment-dashboard" && ../.venv/Scripts/python -m pytest tests/<file> -q`. Full suite carries 2 KNOWN pre-existing `test_apply_sector_perf.py` UI-import fails — ignore; introduce no new failures.
- The offline harness `validate_market_state.py` is NOT unit-tested (mirrors `fit_swing_model.py`); the PURE helpers carry the coverage. It's run manually.
- Commit after each green step; branch `Using_Highcharts` (commit directly, no PR).
- Honest framing is a REQUIREMENT: the report must state it validates the CORE two-axis concept on daily-reconstructable inputs (skew/sector-flow/session/order-flow excluded), not the full live classifier.

---

## Task 1: `daily_direction_score` — the daily direction proxy (PURE)

**Files:**
- Create: `sentiment-dashboard/scoring/daily_direction.py`
- Test: `sentiment-dashboard/tests/test_daily_direction.py`

**Step 1: Write the failing test.**
```python
from scoring.daily_direction import daily_direction_score

def _bars(closes, vols=None):
    vols = vols or [1_000_000] * len(closes)
    out = []
    for i, c in enumerate(closes):
        out.append({"open": c, "high": c * 1.005, "low": c * 0.995,
                    "close": c, "volume": vols[i]})
    return out

def test_strong_uptrend_scores_high():
    bars = _bars([100 + i for i in range(220)])          # steady rise
    assert daily_direction_score(bars) > 70

def test_strong_downtrend_scores_low():
    bars = _bars([320 - i for i in range(220)])
    assert daily_direction_score(bars) < 30

def test_flat_scores_near_50():
    bars = _bars([100 + (i % 2) for i in range(220)])    # chop
    assert 40 <= daily_direction_score(bars) <= 60

def test_insufficient_bars_neutral():
    assert daily_direction_score(_bars([100, 101, 102])) == 50.0
```

**Step 2: Run** `cd "D:/WebGUI Trading with Schwab/sentiment-dashboard" && ../.venv/Scripts/python -m pytest tests/test_daily_direction.py -q` → FAIL (module missing).

**Step 3: Implement** `daily_direction_score(daily_bars) -> float` in `scoring/daily_direction.py`:
- Self-contained (no pandas / no `shared.analysis_lib`); compute over the close list.
- Components → a 0–100 directional score (50 neutral, 100 max bull): (a) EMA stack — close vs EMA20 vs EMA50 vs EMA200 alignment (compute EMAs inline: SMA seed + `alpha=2/(n+1)` recurrence); (b) 20-bar slope of the close (or of EMA50), sign+magnitude; (c) a simple daily RSI(14) (Wilder or simple — document which). Blend into a signed direction in [-1,1], map `score = clamp(50 + 50*direction, 0, 100)`. Require ~200 bars for the EMA200 term; `< ~200` → return `50.0` (neutral, insufficient). Use the `intraday_trend._clamp` idiom (copy a local `_clamp`). Keep it a documented DAILY PROXY (docstring: it stands in for the live intraday direction blend in the historical study).

**Step 4: Run** → PASS.

**Step 5: Commit** `feat(sentiment): daily direction-score proxy for historical validation`.

---

## Task 2: `reconstruct_state_series` — daily committed state over history (PURE)

**Files:**
- Modify: `sentiment-dashboard/scoring/daily_direction.py`
- Test: `sentiment-dashboard/tests/test_daily_direction.py`

**Step 1: Write the failing test.** `reconstruct_state_series(daily_bars, direction_lookback=220, agg_lookback=30) -> list[str]` returns one committed state per day (aligned to `daily_bars`, `None` for days before enough lookback). Reuses the REAL modules.
```python
from scoring.daily_direction import reconstruct_state_series

def test_uptrend_reconstructs_bullish_late():
    bars = _bars([100 + i for i in range(260)], vols=[1_000_000 + (i % 2) * 800_000 for i in range(260)])
    states = reconstruct_state_series(bars)
    assert len(states) == len(bars)
    assert all(s is None for s in states[:200])           # warmup
    assert states[-1] in {"bullish", "lack_of_bullishness", "neutral",
                          "lack_of_bearishness", "bearish"}

def test_series_is_deterministic_and_committed():
    bars = _bars([200 + (i % 5) for i in range(260)])
    s1 = reconstruct_state_series(bars)
    s2 = reconstruct_state_series(bars)
    assert s1 == s2                                        # pure/deterministic
```

**Step 2: Run** → FAIL.

**Step 3: Implement** `reconstruct_state_series`:
- For each index `t` with `t >= direction_lookback`: slice the trailing `direction_lookback` bars → `daily_direction_score`; slice trailing `agg_lookback` bars → `effort.score_effort` + `rejection_defense.score_rejection_defense`; blend via `aggression.blend_aggression({"effort":e.score,"rejection":r.score}, {"effort":e.confidence,"rejection":r.confidence})` → `aggression`; `market_state.classify_market_state(direction, aggression).state` → raw state; thread through `trend_regime.commit_state(raw, history[-2:], prev_committed)` (maintain rolling `history` + `prev_committed` across the loop) → committed. Append committed. Days before warmup → `None`.
- Import the sibling `scoring` modules directly (same package). Defensive: a bad slice → `None` for that day, never raise.

**Step 4: Run** → PASS.

**Step 5: Commit** `feat(sentiment): reconstruct daily committed market-state series`.

---

## Task 3: forward-return + per-state stats + ordinal IC (PURE)

**Files:**
- Modify: `sentiment-dashboard/scoring/daily_direction.py`
- Test: `sentiment-dashboard/tests/test_daily_direction.py`

**Step 1: Write the failing tests.**
```python
from scoring.daily_direction import (
    forward_returns, per_state_stats, ordinal_ic, STATE_ORDINAL)

def test_forward_returns_causal():
    closes = [100, 110, 121]           # +10%, +10%
    fr = forward_returns(closes, horizon=1)
    assert fr[0] == 0.10 and abs(fr[1] - 0.10) < 1e-9 and fr[2] is None  # last has no fwd

def test_per_state_stats_groups_and_means():
    states = ["bullish", "bearish", "bullish", "bearish"]
    fwd =    [0.02,      -0.03,     0.04,      -0.01]
    st = per_state_stats(states, fwd)
    assert abs(st["bullish"]["mean"] - 0.03) < 1e-9 and st["bullish"]["n"] == 2
    assert st["bullish"]["hit_rate"] == 1.0 and st["bearish"]["hit_rate"] == 0.0

def test_ordinal_ic_positive_when_states_track_returns():
    # bullish precedes higher fwd, bearish lower -> positive rank IC
    states = ["bearish","lack_of_bullishness","neutral","lack_of_bearishness","bullish"]
    fwd =    [-0.02,    -0.01,                 0.0,      0.01,                  0.02]
    assert ordinal_ic(states, fwd) > 0.9
    assert STATE_ORDINAL == {"bearish":-2,"lack_of_bullishness":-1,"neutral":0,
                             "lack_of_bearishness":1,"bullish":2}

def test_stats_ignore_none_days():
    assert per_state_stats([None, "bullish"], [None, 0.01])["bullish"]["n"] == 1
```

**Step 2: Run** → FAIL.

**Step 3: Implement:**
- `forward_returns(closes, horizon) -> list[float|None]` — `(closes[t+horizon]/closes[t]) - 1`, `None` for the last `horizon` days / bad values.
- `STATE_ORDINAL` dict as above.
- `per_state_stats(states, fwd) -> {state: {"mean","hit_rate","n"}}` — group `fwd` by state over days where both are non-None; `hit_rate` = P(fwd>0); skip states with `n==0`.
- `ordinal_ic(states, fwd) -> float` — map states → `STATE_ORDINAL`, drop None pairs, return the Spearman rank correlation (reimplement a small pure `_spearman(a,b)` = Pearson of ranks; do NOT import `trade-analyzer` cross-app). `< 5` valid pairs → `0.0`.

**Step 4: Run** → PASS. Then full sentiment suite → only the 2 known fails.

**Step 5: Commit** `feat(sentiment): per-state forward-return stats + ordinal IC`.

---

## Task 4: the offline validation harness (orchestration; live-verified, NOT unit-tested)

**Files:**
- Create: `sentiment-dashboard/validate_market_state.py`
- Modify: `repo_paths.py` (add `MARKET_STATE_VALIDATION_REPORT` + `_JSON` paths under `sentiment-dashboard/data/`, gitignored)

**Step 1:** Add the paths to `repo_paths.py` (mirror `SWING_MODEL` / `SWING_MODEL_REPORT` — sibling under `SENTIMENT / "data"`, e.g. `market_state_validation.md` / `.json`).

**Step 2:** Write `validate_market_state.py` (mirror `trade-analyzer/fit_swing_model.py`'s structure + proxy fetch):
- Resolve a proxy client (as `fit_swing_model.py` / `live_composite.py` do) and fetch **SPY** ~5yr daily OHLCV via `/pricehistory` (`periodType=year, period=5, frequencyType=daily`) + **$VIX** daily (for the regime split). Parse to a list of `{open,high,low,close,volume}` dicts (oldest first) + a parallel date list.
- `states = reconstruct_state_series(spy_bars)`.
- For H in (5, 20): `fwd = forward_returns([b["close"] for b in spy_bars], H)`; `per_state_stats(states, fwd)`; `ordinal_ic(states, fwd)`.
- VIX-regime split: partition days by VIX above/below its median (or 20), recompute `ordinal_ic` + per-state means per regime.
- Write a **markdown report** (`MARKET_STATE_VALIDATION_REPORT`): a header with the honest caveat (core two-axis concept on daily-reconstructable inputs; skew/sector-flow/session/order-flow excluded; encouraging-not-conclusive), a per-state table (state · n · mean 5d · hit 5d · mean 20d · hit 20d), the ordinal IC at 5d/20d, the monotonicity read (are the means ordered Bearish→…→Bullish?), and the VIX-split. Also dump a JSON artifact with the same numbers. Both gitignored (they land under `sentiment-dashboard/data/`, already gitignored like the other DBs).
- Fully defensive (a fetch/parse failure prints a clear message + exits non-zero; a bad day is skipped) — mirror `fit_swing_model.py`.
- A `if __name__ == "__main__":` entry that runs it and prints the headline (ordinal IC 20d + top/bottom-state means).

**Step 3:** Smoke-import test only (no network): add `sentiment-dashboard/tests/test_validate_market_state_import.py` asserting `import validate_market_state` succeeds and its pure helpers are wired (e.g. it references `reconstruct_state_series`). Do NOT unit-test the network run.

**Step 4:** Commit `feat(sentiment): offline five-state validation harness (item 11)`.

---

## Task 5: run the harness against real history + present the result

**Step 1:** With the proxy running, run:
`cd "D:/WebGUI Trading with Schwab/sentiment-dashboard" && ../.venv/Scripts/python validate_market_state.py`

**Step 2:** Read the generated `market_state_validation.md`. Capture the headline numbers: the 20-day ordinal IC, the per-state mean-forward table, whether the means are monotonic Bearish→…→Bullish, and the VIX-split.

**Step 3:** Present the result HONESTLY (like the swing-model result): state the ordinal IC + per-state spread + the core-reconstruction caveat, and give a clear read — does the core two-axis concept stratify forward returns, and how much weight should items 9/10 therefore give the state (real / low / display-only)? This is the GATE decision; surface it to the user for the weight call.

**(No commit — this is a run + report step; the artifact is gitignored.)**

---

## Acceptance

- The four pure helpers are TDD'd green; the harness imports clean.
- `validate_market_state.py` runs end-to-end over real ~5yr SPY history and writes a report with per-state stratification + ordinal IC + the honest caveat.
- The result is presented honestly with a recommended weight for items 9/10.

## Then (separate, gated on the result)

Items 9 (Swing-Scanner family-fit tilt) + 10 (Driver decider context) per the design doc — built + weighted per the item-11 numbers.
