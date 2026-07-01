# Swing Quality-Gated Grading — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the swing-scanner grade reflect trade QUALITY — driven by structural quality and capped by per-family hard gates (liquidity, R:R/capital-eff, PoP) — with a `grade_reason` the user can trust.

**Architecture:** Pure changes to `options-scanner/strategy_scoring.py` (gate config + `evaluate_gates` + a quality-dominant, gated grade) supported by carrying `bid/ask/volume/oi` onto the normalized legs in `strategy_scanner.py` (so the liquidity gate is real), then surfacing `grade_reason` + a colored Grade cell in the webgui `strategy_table.py`/`swing.py`. No service/contract change.

**Design:** [`2026-06-30-swing-quality-gated-grading-design.md`](2026-06-30-swing-quality-gated-grading-design.md)

**Conventions:** @superpowers:test-driven-development for every task. Tests per folder: `cd options-scanner; python -m pytest tests -q`; `cd webgui; ..\.venv\Scripts\python -m pytest -q`. Branch `Using_Highcharts`. Conventional commits + `Co-Authored-By: Claude Opus 4.8` trailer. Use the project venv python.

---

# UNIT E1 — Engine: real liquidity legs + gated grading

## Task 1: carry bid/ask/volume/oi onto normalized legs

**Files:** `options-scanner/strategy_scanner.py`, `options-scanner/tests/test_strategy_scanner.py`

**Why:** `_leg_from` drops bid/ask/volume/oi, so `q_liq` returns a neutral 50 for every directional/debit signal and the liquidity gate can't judge fillability. `extract_options` already captures them in `leg_data`.

**Step 1 — failing test:** assert a leg built by `build_directional` carries `bid`, `ask`, `volume`, `oi` from the chain (the `_contract` fixture already sets bid/ask via `mark±0.05`, `totalVolume`, `openInterest`).
```python
def test_build_directional_legs_carry_liquidity_fields():
    lc = next(s for s in ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30)
              if s["type"] == "LONG_CALL")
    leg = lc["legs"][0]
    assert "bid" in leg and "ask" in leg and leg["ask"] > leg["bid"]
    assert "volume" in leg and "oi" in leg
```
**Step 2:** run → FAIL. **Step 3:** add `bid`/`ask`/`volume`/`oi` to the dict `_leg_from` returns (pull from `leg_data`). **Step 4:** PASS. **Step 5:** also extend `adapt_credit_spread`/`adapt_iron_condor`'s `_credit_leg` to set `bid`/`ask`/`volume` on the SHORT leg from the source dict's `bid`/`ask`/`volume` when present (long leg / missing → leave absent so `norm_liquidity` degrades to 50). Add a test that an adapted PCS with source `bid`/`ask`/`volume` carries them on the short leg. **Step 6:** run full `test_strategy_scanner.py` green. **Commit.**

## Task 2: gate config + `gate_profile(signal)`

**Files:** `options-scanner/strategy_scoring.py`, `options-scanner/tests/test_strategy_scoring.py`

**Step 1 — failing tests:**
```python
def test_gate_profile_maps_types():
    assert sc.gate_profile({"type": "LONG_CALL"}) == "LONG"
    assert sc.gate_profile({"type": "SHORT_PUT"}) == "NAKED"
    assert sc.gate_profile({"type": "BULL_CALL"}) == "DEBIT"
    assert sc.gate_profile({"type": "PCS"}) == "CREDIT"
    assert sc.gate_profile({"type": "IRON_CONDOR"}) == "NEUTRAL"
    assert sc.gate_profile({"type": "???"}) == "DEBIT"   # safe default
```
**Step 2:** FAIL. **Step 3:** add a `GATE_BARS` dict (per §1 of the design — `min`+`excellent` for liq/reward/pop per profile) + `_TYPE_PROFILE` map + `gate_profile`. **Step 4:** PASS. **Commit.**

## Task 3: `evaluate_gates(signal, em_1sd=None)`

Returns `{"passed_min": bool, "passed_excellent": bool, "reasons": [..]}`. Dimensions: **liquidity** (`q_liq(signal) >= bar` AND OI/volume floors when present), **reward** (per profile: LONG uses `rr` with unbounded-profit auto-pass; NAKED uses `q_capital_eff`-style `max_profit/capital`; others use `rr`), **pop** (`pop_pct >= bar`).

**Step 1 — failing tests** (representative):
```python
def _credit(rr=0.3, pop=70, liq_legs=True):
    legs = [{"bid":1.0,"ask":1.05,"mark":1.02,"volume":500,"oi":1000}] if liq_legs else []
    return {"type":"PCS","family":"VERTICAL","rr":rr,"pop_pct":pop,
            "max_profit":1.7,"capital":3.3,"legs":legs,"underlying_price":450}

def test_gates_credit_passes_min_with_good_pop_and_rr():
    g = sc.evaluate_gates(_credit(rr=0.3, pop=70))
    assert g["passed_min"] and not g["reasons"]

def test_gates_credit_fails_on_low_pop():
    g = sc.evaluate_gates(_credit(rr=0.3, pop=40))
    assert not g["passed_min"] and "PoP" in " ".join(g["reasons"])

def test_gates_long_call_unbounded_profit_passes_reward():
    lc = {"type":"LONG_CALL","rr":None,"net_debit":6.0,"pop_pct":35,
          "max_profit":None,"capital":6.0,
          "legs":[{"bid":5.9,"ask":6.0,"mark":5.95,"volume":800,"oi":2000}]}
    g = sc.evaluate_gates(lc)
    assert g["passed_min"]           # unbounded profit clears the R:R bar

def test_gates_naked_uses_capital_efficiency():
    nk = {"type":"SHORT_CALL","rr":None,"net_credit":3.5,"pop_pct":70,
          "max_profit":3.5,"capital":90.0,   # cap-eff ~0.04 -> below 0.10 min
          "legs":[{"bid":3.4,"ask":3.6,"mark":3.5,"volume":300,"oi":800}]}
    g = sc.evaluate_gates(nk)
    assert not g["passed_min"] and "R:R" in " ".join(g["reasons"]) or "capital" in " ".join(g["reasons"]).lower()

def test_gates_fail_on_illiquid_legs():
    c = _credit(); c["legs"] = [{"bid":1.0,"ask":1.9,"mark":1.4,"volume":1,"oi":5}]  # 64% spread
    g = sc.evaluate_gates(c)
    assert not g["passed_min"] and "liquid" in " ".join(g["reasons"]).lower()
```
**Step 2:** FAIL. **Step 3:** implement. Use `q_liq`/`q_capital_eff` from this module; OI/VOL floors are lenient module constants (`OI_FLOOR`, `VOL_FLOOR`) and are **skipped when the leg lacks the field** (no false-fail on missing data). Reason strings are human words ("liquidity", "R:R", "PoP"). `passed_excellent` = every gated dimension clears the profile's `excellent` bar. **Step 4:** PASS. **Commit.**

## Task 4: quality-dominant, gated grade in `score_strategy`

**Step 1 — failing tests:**
```python
def test_score_strategy_gate_fail_is_weak_with_reason():
    sig = _credit(pop=40)  # fails PoP
    out = sc.score_strategy(sig, {"direction":"bullish","conviction":0.8,"vol_regime":"low"}, 0.18, 8.0)
    assert out["grade"] == "Weak" and "PoP" in out["grade_reason"]
    assert out["composite_score"] <= 39            # sinks in the ranking

def test_score_strategy_passes_gates_good():
    sig = _credit(rr=0.3, pop=72)
    out = sc.score_strategy(sig, {"direction":"bullish","conviction":0.8,"vol_regime":"high"}, 0.18, 8.0)
    assert out["grade"] in ("Good","Strong","Marginal")
    assert out["grade"] != "Weak"
    assert "grade_reason" in out

def test_score_strategy_composite_is_quality_dominant():
    # a structurally strong but view-MISALIGNED trade should not be dragged to Weak by fit
    sig = _credit(rr=0.35, pop=75)  # a bullish PCS
    bear_view = {"direction":"bearish","conviction":0.9,"vol_regime":"high"}
    out = sc.score_strategy(sig, bear_view, 0.18, 8.0)
    assert out["grade"] != "Weak"   # gates pass -> fit misalignment can't force Weak
```
**Step 2:** FAIL. **Step 3:** in `score_strategy`: keep computing `fit_score` + `quality_score`; set `composite_score = round(0.7*quality_score + 0.3*fit_score, 1)`; call `evaluate_gates`; apply the grade logic from §2 of the design (Weak+cap on gate-fail; Strong needs `passed_excellent` + composite≥`STRONG_MIN`; Good≥`GOOD_MIN`; else Marginal); set `grade`, `grade_reason`. Keep `factor_scores` (add `passed_min`/reasons is optional). Add module constants `GATE_FAIL_CAP=39`, `STRONG_MIN=78`, `GOOD_MIN=58`. Keep per-signal defensiveness. **Step 4:** PASS + keep every existing `test_strategy_scoring.py` test green (update any that asserted the old 50/50 composite or old grade thresholds to the new reality). **Step 5:** run full `options-scanner` engine suites (`test_strategy_scanner.py test_strategy_scoring.py test_scoring.py`) green. **Commit.**

---

# UNIT E2 — UI: grade reason + colored Grade cell

## Task 5: `strategy_rows` carries `grade_reason` + `_grade_class`

**Files:** `webgui/pages/options/strategy_table.py`, `webgui/tests/test_strategy_table.py`

**Step 1 — failing tests:** `strategy_rows([sig])[0]` includes `grade_reason` (from the signal) and `_grade_class` mapping grade→a fixed Tailwind text class (Strong/Good→green tokens, Marginal→amber, Weak→red) via a small `grade_class(grade)` pure fn with a neutral fallback.
```python
def test_strategy_rows_carry_grade_reason_and_class():
    row = st.strategy_rows([{"id":"x","type":"PCS","grade":"Weak",
                             "grade_reason":"Fails: PoP","composite_score":30}])[0]
    assert row["grade_reason"] == "Fails: PoP"
    assert row["_grade_class"] == st.grade_class("Weak")
    assert "rose" in st.grade_class("Weak") or "red" in st.grade_class("Weak")
```
**Step 2:** FAIL. **Step 3:** add `grade_class` (finite map, reuse `theme.TXT_*` tokens where they fit) + include `grade_reason`/`_grade_class` in each row. **Step 4:** PASS. **Commit.**

## Task 6: colored Grade cell + reason tooltip in `swing.py`

**Files:** `webgui/pages/options/swing.py`

**Step 1:** add a `body-cell-grade` slot (mirroring the existing `composite_score`/`bias` slots) rendering the grade with `:class="props.row._grade_class"` and a `<q-tooltip>{{ props.row.grade_reason }}</q-tooltip>`. Tailwind-first (no `.style()`). **Step 2:** run `cd webgui; ..\.venv\Scripts\python -m pytest tests/test_no_inline_style.py tests/test_shell.py tests/test_strategy_table.py -q` green + confirm `swing.py` imports. **Commit.**

---

# Task 7: Live verification + docs

- Restart `options_svc` + `webgui` (they're stale) via `tools\restart_one.bat` (kill 8211 wait 8100 → app; kill 8500 wait 8211 → main). Or run the in-process `compute.swing_scan` check.
- Enqueue a scan for a few symbols; confirm grades now spread sensibly: illiquid / poor-R:R / low-PoP trades grade **Weak** with a reason; **Strong** is rare; most solid trades are Marginal/Good. Confirm the Grade cell is colored + the tooltip shows the reason in the browser.
- Update `CLAUDE.md` (the swing route row + the "Multi-strategy Swing Scanner" section) with the quality-gated grading + the design/plan links. Commit.

## Definition of done
- All new pure fns unit-tested; `options-scanner` + `webgui` suites green (no regressions beyond the documented pre-existing failures); the no-inline-style guard passes.
- Live-verified: grades reflect quality + gate reasons; Strong is rare.
- `CLAUDE.md` updated; work committed on `Using_Highcharts`.
