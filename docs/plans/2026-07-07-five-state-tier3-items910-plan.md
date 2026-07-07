# Five-State Tier 3 — Items 9 & 10 (low-weight tilt + decider context) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the five-state market state into the Swing Scanner (item 9, a SMALL bounded family-fit tilt) and the Driver decision packet (item 10, decider context only), at LOW weight — reflecting the validation result (modest +0.087 IC, concentrated in the two MIDDLE states; extremes unreliable; regime-dependent).

**Architecture:** Item 9 = a pure `state_family_tilt(state, structure_type) → small bounded points` folded additively into `options-scanner/strategy_scoring.py:score_strategy`, with `services/options_svc/compute.py:swing_scan` reading the committed state from `cache:sentiment:composite` and passing it through. Item 10 = the committed state (label + evidence) added to `services/driver_svc/compute.py:fetch_market_context` → `build_packet`'s `market` dict → the decider prompt; **no guardrail change** (regime_filter already hard-gates the driver's menu). Both additive + defensive (no state → no-op). Design doc: `docs/plans/2026-07-07-five-state-tier3-validation-integrations-design.md`.

**Tech Stack:** Python 3.11, pytest (TDD), `shared.bus` (cross-service cache read), the pure `options-scanner/strategy_scoring.py`, `services/driver_svc`.

**Conventions:**
- LOW weight is a hard requirement: the tilt must be a small bounded adjustment (max ±`STATE_TILT_MAX` on the 0-100 composite — set `STATE_TILT_MAX = 6.0`), never able to flip a Weak trade to Strong on its own.
- `strategy_scoring.py` is a PURE engine module (options-scanner) — no `services/` import; lazy-imported by the service (the documented cross-app `scoring`-collision discipline).
- Run options-scanner engine tests: `cd "D:/WebGUI Trading with Schwab/options-scanner" && ../.venv/Scripts/python -m pytest tests/test_strategy_scoring.py -q` (2 known `TestEarningsAvoidance` fails elsewhere — ignore). options_svc: `cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest services/options_svc -q`. driver_svc: `.venv/Scripts/python -m pytest services/driver_svc -q`.
- Commit after each green step; branch `Using_Highcharts`.

---

## Task 1: `state_family_tilt` + fold into `score_strategy` (item 9, PURE)

**Files:**
- Modify: `options-scanner/strategy_scoring.py`
- Test: `options-scanner/tests/test_strategy_scoring.py`

**Step 1: Write the failing test.**
```python
from strategy_scoring import state_family_tilt, STATE_TILT_MAX

def test_tilt_bounded():
    for state in ("bullish","lack_of_bullishness","neutral","lack_of_bearishness","bearish"):
        for t in ("PCS","CCS","IC","LONG_CALL","LONG_PUT","BULL_CALL","BEAR_PUT"):
            assert -STATE_TILT_MAX <= state_family_tilt(state, t) <= STATE_TILT_MAX

def test_middle_states_lean_correctly():
    # Lack of Bearishness (resilient, puts undefended) favors PCS, disfavors nothing hard
    assert state_family_tilt("lack_of_bearishness", "PCS") > 0
    # Lack of Bullishness (exhaustion at highs) favors CCS, penalizes long calls
    assert state_family_tilt("lack_of_bullishness", "CCS") > 0
    assert state_family_tilt("lack_of_bullishness", "LONG_CALL") < 0
    # Neutral favors premium/IC
    assert state_family_tilt("neutral", "IC") > 0

def test_unknown_or_missing_state_no_tilt():
    assert state_family_tilt(None, "PCS") == 0.0
    assert state_family_tilt("garbage", "PCS") == 0.0
    assert state_family_tilt("neutral", "UNKNOWN_TYPE") == 0.0
```

**Step 2: Run** `cd "D:/WebGUI Trading with Schwab/options-scanner" && ../.venv/Scripts/python -m pytest tests/test_strategy_scoring.py -q` → FAIL.

**Step 3: Implement** in `strategy_scoring.py`:
- `STATE_TILT_MAX = 6.0`.
- A `_STATE_TILT` table: `{state: {family_key: points}}` — SMALL values (±2 to ±6), leaning on the MIDDLE states (the validated signal), modest on the extremes (Bullish exhaustion-prone → small; Bearish rarely fires → small/defensive). Map structure `type` strings to family keys (credit: PCS/CCS/IC; debit: BULL_CALL/BEAR_PUT; directional: LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT). Suggested leans (tune within ±6):
  - `neutral`: IC/PCS/CCS (premium) +3; long options −2.
  - `lack_of_bearishness`: PCS +5, BULL_CALL +2, LONG_PUT −3, BEAR_PUT −2 (resilient, puts undefended).
  - `lack_of_bullishness`: CCS +5, BEAR_PUT +2, LONG_CALL −4, BULL_CALL −3 (exhaustion at highs).
  - `bullish`: LONG_CALL/BULL_CALL/PCS +2 (small — exhaustion caveat); CCS −2.
  - `bearish`: LONG_PUT/BEAR_PUT +3, PCS −3, CCS +1 (defensive; state rarely fires).
- `state_family_tilt(state, structure_type) -> float`: look up, clamp to ±STATE_TILT_MAX, return 0.0 on unknown state/type/None.

**Step 4: Run** → PASS.

**Step 5: Fold into `score_strategy`.** Add a `market_state=None` kwarg to `score_strategy(signal, view, atm_iv, em_1sd, market_state=None)`. After the composite is computed, add `tilt = state_family_tilt(market_state, signal.get("type"))`; `signal["composite_score"] = _clamp(signal["composite_score"] + tilt, 0, 100)` (reuse the module's clamp); store `signal["state_tilt"] = tilt` for transparency. The grade/grade_reason are recomputed from the tilted composite ONLY IF the existing code derives grade from composite AFTER this point — otherwise leave grade as-is (the tilt is a ranking nudge; do NOT let it flip the hard gates). Thread `market_state` through `score_all(signals, view, atm_iv, em_1sd, market_state=None)`. Add a test that a `lack_of_bearishness` state raises a PCS signal's composite vs `market_state=None`, bounded by STATE_TILT_MAX, and does NOT change a Weak-gated grade.

**Step 6: Run** the strategy_scoring suite → green.

**Step 7: Commit** `feat(swing): low-weight market-state family tilt in strategy scoring`.

---

## Task 2: `swing_scan` reads + passes the market state (item 9 wiring)

**Files:**
- Modify: `services/options_svc/compute.py` (`swing_scan`)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Write the failing test.** `swing_scan` reads the committed state from `cache:sentiment:composite` (`derived.trend.state`) via the bus and passes it into `score_all(..., market_state=<state>)`. Test with a fake bus returning a composite whose `derived.trend.state == "lack_of_bearishness"`, assert the scored signals carry a non-zero `state_tilt` on a PCS (or that `score_all` received the state). Missing/absent composite → `market_state=None` → no tilt (graceful).

**Step 2–4:** Red → implement (read `bus.cache_get("cache:sentiment:composite")` defensively → `payload["derived"]["trend"]["state"]`; pass to `score_all`; lazy-import discipline preserved) → green.

**Step 5: Commit** `feat(options): swing scanner reads market state for the family tilt`.

---

## Task 3: market state into the Driver decision packet (item 10, context only)

**Files:**
- Modify: `services/driver_svc/compute.py` (`fetch_market_context` + `build_packet`)
- Test: `services/driver_svc/tests/test_compute_packet.py`

**Step 1: Write the failing test.** `fetch_market_context()` adds a `market_state` block (`{state, label, evidence}`) read from `cache:sentiment:composite` (`derived.trend`), defensive → omitted on failure. `build_packet` includes the `market_state` in the packet it builds (so the decider prompt/`market` context carries it). Assert the packet's market context contains the state label + evidence when present, and is unaffected when absent. **No change to `guardrails.apply_guardrails`** (assert the guardrail path is untouched — regime_filter already hard-gates the menu).

**Step 2–4:** Red → implement (read the composite via the bus defensively in `fetch_market_context`; thread `market_state` into `build_packet`'s returned `market`/packet dict + the decider-facing text; do NOT touch guardrails) → green.

**Step 5: Commit** `feat(driver): surface market state to the decider as context (no guardrail change)`.

---

## Acceptance
- Item 9: a `lack_of_bearishness`/`lack_of_bullishness` state nudges the matching family's composite by a SMALL bounded amount (≤ ±6), never flipping a gated grade; `swing_scan` feeds the live state; absent state → no-op.
- Item 10: the driver decider sees the market state as context in its packet; `guardrails.py` is untouched.
- All touched suites green (strategy_scoring, options_svc, driver_svc), run per-folder.

## Honest note (carry into CLAUDE.md)
The tilt weights are intentionally SMALL and lean on the two MIDDLE states (the validated signal); the extremes are modest (Bullish exhaustion-prone, Bearish rarely fires in the reconstruction). This reflects item 11's thin, regime-dependent, core-reconstruction edge — the state is a nudge + decider context, not a driver of selection.
