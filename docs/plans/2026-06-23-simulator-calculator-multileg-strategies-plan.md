# Simulator + Calculator Multi-Leg Strategies — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the Options Simulator and Calculator build, price, and analyze
multi-leg strategies (verticals incl. debit, condors incl. iron + all-same,
butterflies incl. iron, calendars + diagonals) with editable legs and a
copy-legs button both ways.

**Architecture:** A shared *pure* leg model (`strategies.py`) + one parameterized
leg-editor widget (`leg_editor.py`) that both Tier-1 pages mount. Tier-2 compute
gains per-leg expiry (calendars), ratio legs (butterflies), and a generic-numeric
summary for the new structures. The pricing engines already aggregate arbitrary
legs; only What-if's single-T and the engine's missing per-leg quantity need
fixing. See the paired design doc:
`docs/plans/2026-06-23-simulator-calculator-multileg-strategies-design.md`.

**Tech Stack:** Python 3.11, NiceGUI + Highcharts (Tier-1 webgui), FastAPI domain
service (Tier-2 `options_svc`), Redis/Memurai bus, pandas/numpy/scipy, pytest.

**Test commands (per folder — never `pytest services` over all of them):**
- Engine + calculator math: `cd "D:\WebGUI Trading with Schwab\options-scanner" ; python -m pytest tests/<file> -v`
- Options service: `cd "D:\WebGUI Trading with Schwab" ; .venv\Scripts\python -m pytest services\options_svc -q`
- Webgui: `cd "D:\WebGUI Trading with Schwab\webgui" ; ..\.venv\Scripts\python -m pytest -q`

**House rules (load-bearing):**
- DRY / YAGNI / TDD / frequent commits. One behavior per test.
- Conventional-commit prefixes (`feat:`/`fix:`/`refactor:`/`test:`/`docs:`).
- End every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Tier-1 webgui imports ONLY `nicegui` + `shared.bus`/`bus_client` + `shared.contracts`
  + sibling `pages.*` modules — **no engine imports**. `strategies.py` must stay
  pure (no `nicegui`).
- The existing `PCS`/`CCS`/`IC`/single analytic summary path must stay
  byte-identical (zero regressions) — new code is additive and routed by a flag.
- Each leg in the normalized model is:
  `{"option_type": "call"|"put", "side": "long"|"short", "strike": float|None,
    "expiry": "YYYY-MM-DD"|None, "qty": int, "premium": float|None}`.

---

## Task 1: Engine — ratio legs + generic Position builder

Butterflies are 1-2-1: the body leg trades at 2×. `Leg` only carries `sign`
(±1) today. Add a per-leg `ratio` and multiply Greeks by `sign * ratio`.

**Files:**
- Modify: `options-scanner/options_simulator/engine.py` (the `Leg` dataclass ~L22,
  `aggregate_position` ~L82, add a `Position.from_legs` classmethod)
- Test: `options-scanner/tests/test_simulator_engine_ratio.py` (create)

**Step 1: Write the failing test**

```python
# options-scanner/tests/test_simulator_engine_ratio.py
"""Ratio-leg aggregation: a 2x body must contribute 2x its Greeks."""
from datetime import date

import pandas as pd

from options_simulator.engine import Leg, Position, aggregate_position


def _row(name, val):
    return pd.DataFrame({"theo_price": [val], "delta": [val], "gamma": [val],
                         "theta": [val], "vega": [val], "rho": [val]})


def test_ratio_scales_greeks():
    # Two legs sharing a dummy contract: long ratio-2 minus short ratio-1.
    c = object()
    pos = Position(legs=[Leg(contract=c, sign=+1, ratio=2),
                         Leg(contract=c, sign=-1, ratio=1)],
                   label="ratio test")
    out = aggregate_position(pos, lambda _c: _row("x", 10.0))
    # 10*(+1*2) + 10*(-1*1) = 10
    assert out["theo_price"].iloc[0] == 10.0
    assert out["delta"].iloc[0] == 10.0


def test_ratio_defaults_to_one():
    c = object()
    pos = Position.from_legs([(c, +1, 1), (c, +1, 1)], label="two longs")
    out = aggregate_position(pos, lambda _c: _row("x", 3.0))
    assert out["theo_price"].iloc[0] == 6.0
```

**Step 2: Run test to verify it fails**

Run: `cd "D:\WebGUI Trading with Schwab\options-scanner" ; python -m pytest tests/test_simulator_engine_ratio.py -v`
Expected: FAIL — `Leg.__init__() got an unexpected keyword argument 'ratio'`.

**Step 3: Implement**

In `engine.py`, add `ratio` to `Leg`:

```python
@dataclass
class Leg:
    contract: ContractRow
    sign: int          # +1 = long (bought), -1 = short (sold)
    ratio: int = 1     # per-leg contract multiple (butterfly body = 2)
```

In `aggregate_position`, change the per-leg scale from `leg.sign` to
`leg.sign * leg.ratio` (both the seed-copy loop and the accumulation loop):

```python
        signed = df.copy()
        scale = leg.sign * leg.ratio
        for col in _GREEK_COLS:
            if col in signed.columns:
                signed[col] = signed[col] * scale
```

Add a generic builder to `Position`:

```python
    @classmethod
    def from_legs(cls, legs, label: str) -> "Position":
        """Build from an iterable of (contract, sign, ratio) tuples."""
        return cls(legs=[Leg(contract=c, sign=int(s), ratio=int(r))
                         for c, s, r in legs], label=label)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_simulator_engine_ratio.py -v`
Expected: PASS (2 passed).

**Step 5: Regression-check the engine's existing callers**

Run: `python -m pytest tests/ -k "simulator or engine" -q`
Expected: no new failures (the 2 pre-existing earnings-fixture fails are unrelated).

**Step 6: Commit**

```bash
git add options-scanner/options_simulator/engine.py options-scanner/tests/test_simulator_engine_ratio.py
git commit -m "feat(simulator-engine): per-leg ratio for butterfly bodies + Position.from_legs"
```

---

## Task 2: Shared strategy/leg model — `strategies.py` (pure, Tier-1)

The single source of truth for templates, default-leg construction, and the
analytic-vs-numeric summary routing. No `nicegui`, no engine imports.

**Files:**
- Create: `webgui/pages/options/strategies.py`
- Test: `webgui/tests/test_strategies.py` (create)

**Step 1: Write the failing tests**

```python
# webgui/tests/test_strategies.py
from pages.options import strategies as S


def test_templates_cover_all_families():
    names = set(S.STRATEGY_TEMPLATES)
    for n in ("LONG_CALL", "NAKED_PUT", "PCS", "CCS", "IC",
              "VERT_CALL_DEBIT", "CONDOR_CALL", "BUTTERFLY_CALL",
              "IRON_BUTTERFLY", "CALENDAR_CALL", "DIAGONAL_PUT"):
        assert n in names, n


def test_build_default_legs_butterfly_is_1_2_1():
    strikes = [90, 95, 100, 105, 110]
    legs = S.build_default_legs("BUTTERFLY_CALL", spot=100,
                                strikes=strikes, expiries=["2026-07-17"])
    assert [l["qty"] for l in legs] == [1, 2, 1]
    assert [l["side"] for l in legs] == ["long", "short", "long"]
    assert all(l["option_type"] == "call" for l in legs)
    # strikes are distinct + ATM-centered, all snapped to the available list
    ks = [l["strike"] for l in legs]
    assert ks == [95, 100, 105]
    assert all(k in strikes for k in ks)


def test_build_default_legs_calendar_uses_near_and_far_expiry():
    legs = S.build_default_legs("CALENDAR_CALL", spot=100,
                                strikes=[95, 100, 105],
                                expiries=["2026-07-17", "2026-08-21"])
    assert len(legs) == 2
    assert {l["expiry"] for l in legs} == {"2026-07-17", "2026-08-21"}
    # same strike on both legs (horizontal)
    assert legs[0]["strike"] == legs[1]["strike"] == 100
    # short the near, long the far
    near = next(l for l in legs if l["expiry"] == "2026-07-17")
    far = next(l for l in legs if l["expiry"] == "2026-08-21")
    assert near["side"] == "short" and far["side"] == "long"


def test_summary_code_canonical_vs_custom():
    strikes = [95, 100, 105]
    pcs = S.build_default_legs("PCS", 100, strikes, ["2026-07-17"])
    assert S.summary_code("PCS", pcs) == "PCS"
    # edit a leg away from the template -> CUSTOM
    pcs[0]["strike"] = 80
    assert S.summary_code("PCS", pcs) == "CUSTOM"
    # a brand-new family always numeric
    fly = S.build_default_legs("BUTTERFLY_CALL", 100, strikes + [90, 110], ["2026-07-17"])
    assert S.summary_code("BUTTERFLY_CALL", fly) == "CUSTOM"
```

**Step 2: Run to verify failure**

Run: `cd "D:\WebGUI Trading with Schwab\webgui" ; ..\.venv\Scripts\python -m pytest tests/test_strategies.py -v`
Expected: FAIL — `ModuleNotFoundError: pages.options.strategies`.

**Step 3: Implement `strategies.py`**

```python
"""Shared, PURE strategy/leg model for the Options Simulator + Calculator.

Tier-1: no nicegui, no engine imports. Defines the normalized leg dict, the
strategy template table, default-leg construction (ATM-centered, strike-snapped,
near/far expiries for calendars), and the analytic-vs-numeric summary routing.
Both pages import this so the templates + copy payload never drift.
"""

# Normalized leg dict keys: option_type, side, strike, expiry, qty, premium.

# Each template entry: list of leg specs. A spec is a dict with:
#   option_type, side, qty, strike_role, expiry_role
# strike_role drives default strike placement relative to ATM; expiry_role is
# "near" (front) or "far" (back) for calendars/diagonals (else "near").
def _leg(option_type, side, qty, strike_role, expiry_role="near"):
    return {"option_type": option_type, "side": side, "qty": qty,
            "strike_role": strike_role, "expiry_role": expiry_role}


STRATEGY_TEMPLATES = {
    # singles
    "LONG_CALL":  [_leg("call", "long", 1, "atm")],
    "LONG_PUT":   [_leg("put", "long", 1, "atm")],
    "NAKED_CALL": [_leg("call", "short", 1, "otm_up_1")],
    "NAKED_PUT":  [_leg("put", "short", 1, "otm_dn_1")],
    # verticals — credit (aliases PCS/CCS) + debit
    "PCS": [_leg("put", "short", 1, "otm_dn_1"), _leg("put", "long", 1, "otm_dn_2")],
    "CCS": [_leg("call", "short", 1, "otm_up_1"), _leg("call", "long", 1, "otm_up_2")],
    "VERT_PUT_DEBIT":  [_leg("put", "long", 1, "atm"), _leg("put", "short", 1, "otm_dn_1")],
    "VERT_CALL_DEBIT": [_leg("call", "long", 1, "atm"), _leg("call", "short", 1, "otm_up_1")],
    # condors
    "IC": [_leg("put", "short", 1, "otm_dn_1"), _leg("put", "long", 1, "otm_dn_2"),
           _leg("call", "short", 1, "otm_up_1"), _leg("call", "long", 1, "otm_up_2")],
    "CONDOR_CALL": [_leg("call", "long", 1, "atm"), _leg("call", "short", 1, "otm_up_1"),
                    _leg("call", "short", 1, "otm_up_2"), _leg("call", "long", 1, "otm_up_3")],
    "CONDOR_PUT": [_leg("put", "long", 1, "atm"), _leg("put", "short", 1, "otm_dn_1"),
                   _leg("put", "short", 1, "otm_dn_2"), _leg("put", "long", 1, "otm_dn_3")],
    # butterflies (1-2-1)
    "BUTTERFLY_CALL": [_leg("call", "long", 1, "otm_dn_1"), _leg("call", "short", 2, "atm"),
                       _leg("call", "long", 1, "otm_up_1")],
    "BUTTERFLY_PUT": [_leg("put", "long", 1, "otm_up_1"), _leg("put", "short", 2, "atm"),
                      _leg("put", "long", 1, "otm_dn_1")],
    "IRON_BUTTERFLY": [_leg("put", "short", 1, "atm"), _leg("put", "long", 1, "otm_dn_1"),
                       _leg("call", "short", 1, "atm"), _leg("call", "long", 1, "otm_up_1")],
    # calendars / diagonals (per-leg expiry)
    "CALENDAR_CALL": [_leg("call", "short", 1, "atm", "near"), _leg("call", "long", 1, "atm", "far")],
    "CALENDAR_PUT": [_leg("put", "short", 1, "atm", "near"), _leg("put", "long", 1, "atm", "far")],
    "DIAGONAL_CALL": [_leg("call", "short", 1, "otm_up_1", "near"), _leg("call", "long", 1, "atm", "far")],
    "DIAGONAL_PUT": [_leg("put", "short", 1, "otm_dn_1", "near"), _leg("put", "long", 1, "atm", "far")],
}

# Display groups for the UI dropdown (label -> code).
STRATEGY_GROUPS = [
    ("Singles", ["LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT"]),
    ("Verticals", ["PCS", "CCS", "VERT_CALL_DEBIT", "VERT_PUT_DEBIT"]),
    ("Condors", ["IC", "CONDOR_CALL", "CONDOR_PUT"]),
    ("Butterflies", ["BUTTERFLY_CALL", "BUTTERFLY_PUT", "IRON_BUTTERFLY"]),
    ("Calendars", ["CALENDAR_CALL", "CALENDAR_PUT", "DIAGONAL_CALL", "DIAGONAL_PUT"]),
]

# Strategy codes the calculator can summarize ANALYTICALLY (exact, legacy path).
_ANALYTIC_CODES = {"PCS", "CCS", "IC", "LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT"}


def _nearest(strikes, target):
    return min(strikes, key=lambda s: abs(s - target)) if strikes else target


def _role_strike(role, spot, strikes):
    """Map a strike_role -> a concrete strike snapped to ``strikes``.

    ATM is nearest-to-spot; otm_up_N / otm_dn_N step N positions out from ATM in
    the sorted strike ladder so wings stay distinct on any strike grid."""
    if not strikes:
        return None
    ladder = sorted(strikes)
    atm_idx = min(range(len(ladder)), key=lambda i: abs(ladder[i] - spot))
    if role == "atm":
        return ladder[atm_idx]
    direction = 1 if "up" in role else -1
    try:
        n = int(role.rsplit("_", 1)[-1])
    except ValueError:
        n = 1
    idx = max(0, min(len(ladder) - 1, atm_idx + direction * n))
    return ladder[idx]


def build_default_legs(template, spot, strikes, expiries):
    """Build normalized legs for ``template`` with ATM-centered, snapped strikes.

    ``expiries`` is a sorted list of ISO strings; near = expiries[0],
    far = the next distinct expiry (else near). Returns [] for an unknown
    template."""
    specs = STRATEGY_TEMPLATES.get(template)
    if not specs:
        return []
    exps = list(expiries or [])
    near = exps[0] if exps else None
    far = next((e for e in exps if e != near), near)
    legs = []
    for spec in specs:
        legs.append({
            "option_type": spec["option_type"],
            "side": spec["side"],
            "qty": spec["qty"],
            "strike": _role_strike(spec["strike_role"], spot, strikes),
            "expiry": far if spec["expiry_role"] == "far" else near,
            "premium": None,
        })
    return legs


def summary_code(template, legs):
    """Return the analytic code if ``legs`` still match the canonical template
    for an analytic-capable strategy, else ``"CUSTOM"`` (numeric summary).

    'Match' = same template-built leg roles (option_type/side/qty count) AND a
    single shared expiry (analytic formulas assume one expiry)."""
    if template not in _ANALYTIC_CODES:
        return "CUSTOM"
    if len({l.get("expiry") for l in legs}) > 1:
        return "CUSTOM"
    canon = STRATEGY_TEMPLATES[template]
    if len(legs) != len(canon):
        return "CUSTOM"
    canon_shape = sorted((s["option_type"], s["side"], s["qty"]) for s in canon)
    legs_shape = sorted((l["option_type"], l["side"], int(l.get("qty", 1))) for l in legs)
    return template if canon_shape == legs_shape else "CUSTOM"
```

> Note: `summary_code` keys analytic eligibility off leg *shape* (kind/side/qty)
> + single-expiry. Editing only a *strike* keeps the shape but the test
> `test_summary_code_canonical_vs_custom` expects a strike edit → CUSTOM. To make
> a strike edit fall to numeric (safer — analytic breakeven assumes template
> strikes), also compare strikes: append the snapped default-strike check. Adjust
> `summary_code` to also recompute `build_default_legs(template, spot, strikes,...)`
> and compare strikes; OR (simpler + what the test asserts) treat ANY strike
> different from the template defaults as CUSTOM. Implement the simpler rule:
> pass the spot+strikes used to build, and compare each leg's strike to the
> template default. Because the page always has spot+strikes when it builds legs,
> have the PAGE compute `summary_code` by checking "did the user edit anything
> since the template was applied?" — track a `dirty` flag in the editor (Task 9).
> **Decision:** keep `summary_code` shape+expiry based (as coded above) and let
> the page pass `strategy="CUSTOM"` whenever the leg-editor `dirty` flag is set.
> Update the test to match: a strike edit flips the editor's dirty flag (page
> concern), not `summary_code`. Rewrite `test_summary_code_canonical_vs_custom`
> to drop the strike-edit assertion and instead assert: canonical PCS→"PCS",
> a put→call swap on a leg→"CUSTOM", multi-expiry→"CUSTOM", butterfly→"CUSTOM".

**Step 4: Run to verify pass** (after the test edit noted above)

Run: `..\.venv\Scripts\python -m pytest tests/test_strategies.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/strategies.py webgui/tests/test_strategies.py
git commit -m "feat(options): shared pure strategy/leg model (templates + default legs + summary routing)"
```

---

## Task 3: Calculator P&L grid — per-leg expiry (`calc_spread_pnl`)

Make the grid price each leg at its own time-to-expiry per column, so calendars
work. Single-expiry behavior must stay byte-identical.

**Files:**
- Modify: `options-scanner/options_calculator.py` (`calc_spread_pnl` ~L732)
- Test: `options-scanner/tests/test_calc_multileg.py` (create)

**Step 1: Write the failing test**

```python
# options-scanner/tests/test_calc_multileg.py
from datetime import date

import options_calculator as oc


def test_per_leg_expiry_back_leg_retains_value_at_front_expiry():
    # Calendar: short near call, long far call, same strike. At the FRONT
    # expiry column (T_front=0) the near leg is worthless but the far leg still
    # has time value -> net position value > 0 somewhere near the strike.
    legs = [
        {"strike": 100, "option_type": "call", "side": "short", "premium": 2.0,
         "qty": 1, "expiry": "2026-07-17"},
        {"strike": 100, "option_type": "call", "side": "long", "premium": 5.0,
         "qty": 1, "expiry": "2026-08-21"},
    ]
    grid = oc.calc_spread_pnl(
        legs, spot=100, iv=0.30, r=0.04, eval_dates=None,
        price_range=(100, 100), expiry_date=date(2026, 7, 17),
        eval_times=[0.0],            # single column AT the front expiry
        per_leg_expiry=True)
    row = grid[0]                    # price == 100
    # net premium received at entry = (2 - 5)*100 = -300 debit; at front expiry
    # the far call (T>0, ATM) is worth well over 3.00, so PnL > -300 (recovers).
    assert row["pnl"][0] > -300


def test_single_expiry_unchanged_when_per_leg_off():
    legs = [{"strike": 95, "option_type": "put", "side": "short", "premium": 1.5, "qty": 1},
            {"strike": 90, "option_type": "put", "side": "long", "premium": 0.5, "qty": 1}]
    a = oc.calc_spread_pnl(legs, 100, 0.2, 0.04, None, (90, 110), date(2026, 7, 17),
                           eval_times=[0.02, 0.0])
    b = oc.calc_spread_pnl(legs, 100, 0.2, 0.04, None, (90, 110), date(2026, 7, 17),
                           eval_times=[0.02, 0.0], per_leg_expiry=True)
    assert a == b   # legs without 'expiry' fall back to the column T identically
```

**Step 2: Run to verify failure**

Run: `cd "D:\WebGUI Trading with Schwab\options-scanner" ; python -m pytest tests/test_calc_multileg.py -v`
Expected: FAIL — `calc_spread_pnl() got an unexpected keyword argument 'per_leg_expiry'`.

**Step 3: Implement**

Add params + per-leg T. In `calc_spread_pnl(...)` add `per_leg_expiry=False` to
the signature. Inside the price loop, replace the single-`T` leg pricing with a
per-leg time that subtracts the *same elapsed* time the column represents from
each leg's own expiry. Concretely, the column already encodes a time-to-expiry
`T` relative to `expiry_date`; the elapsed-from-now for that column is
`T0 - T` where `T0 = col_times[0]` (the "Now" column). Each leg's own T at that
column is `max(leg_T0 - (T0 - T), 0)` where `leg_T0` is the leg's current
time-to-expiry. When a leg has no `expiry` (or `per_leg_expiry` is False), use
the column `T` unchanged (back-compat).

```python
import datetime as _dt

def _leg_expiry_years(leg, expiry_date):
    """Leg's current (now) time-to-expiry in years from its own expiry, else None."""
    e = leg.get("expiry")
    if not e:
        return None
    try:
        d = _dt.date.fromisoformat(str(e))
    except (TypeError, ValueError):
        return None
    # 4pm-close convention, /365, never negative.
    close = _dt.datetime(d.year, d.month, d.day, 16)
    now = _dt.datetime.now()
    return max((close - now).total_seconds(), 0.0) / (365 * 86400)

# ...inside calc_spread_pnl, after col_times is built:
t0 = col_times[0] if col_times else 0.0
leg_t0 = [_leg_expiry_years(l, expiry_date) if per_leg_expiry else None for l in legs]

# ...inside the `for T in col_times:` loop, when pricing each leg:
for li, leg in enumerate(legs):
    ...
    base = leg_t0[li]
    t_leg = T if base is None else max(base - (t0 - T), 0.0)
    current_price = bs_price(price, strike, t_leg, r, adjusted_iv, opt_type)
    ...
```

Keep everything else identical. The `test_single_expiry_unchanged_when_per_leg_off`
test pins back-compat (legs without `expiry` → `base is None` → `t_leg = T`).

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_calc_multileg.py -v`
Expected: PASS (2 passed).

**Step 5: Regression-check existing calculator tests**

Run: `python -m pytest tests/ -k calculator -q`
Expected: no new failures.

**Step 6: Commit**

```bash
git add options-scanner/options_calculator.py options-scanner/tests/test_calc_multileg.py
git commit -m "feat(calculator): per-leg expiry in calc_spread_pnl (calendars)"
```

---

## Task 4: Calculator — generic numeric summary (`calc_summary_generic`)

Butterfly/condor/debit-vertical/calendar/diagonal have no closed form. Derive
max-profit / max-loss / breakevens numerically from the value-at-front-expiry
curve, PoP from the risk-neutral lognormal CDF.

**Files:**
- Modify: `options-scanner/options_calculator.py` (add `calc_summary_generic`)
- Test: `options-scanner/tests/test_calc_multileg.py` (extend)

**Step 1: Add failing tests**

```python
def test_generic_summary_long_call_butterfly():
    # 95/100/105 call fly, 1-2-1. Classic defined-risk: max loss = net debit,
    # max profit at the body, breakevens between wings.
    legs = [
        {"strike": 95, "option_type": "call", "side": "long", "premium": 6.0, "qty": 1},
        {"strike": 100, "option_type": "call", "side": "short", "premium": 3.0, "qty": 2},
        {"strike": 105, "option_type": "call", "side": "long", "premium": 1.5, "qty": 1},
    ]
    s = oc.calc_summary_generic(legs, spot=100, r=0.04, iv=0.25, T=0.05)
    # net debit per share = 6 - 2*3 + 1.5 = 1.5 -> max loss 150
    assert abs(s["max_loss"] - 150) < 25
    # max profit at body ~ (5 - 1.5)*100 = 350
    assert 300 < s["max_profit"] < 400
    assert len(s["breakevens"]) == 2
    assert 95 < s["breakevens"][0] < 100 < s["breakevens"][1] < 105
    assert 0 <= s["pop"] <= 100


def test_generic_summary_keys():
    legs = [{"strike": 100, "option_type": "call", "side": "long", "premium": 2, "qty": 1}]
    s = oc.calc_summary_generic(legs, 100, 0.04, 0.2, 0.05)
    assert set(s) >= {"entry_credit", "max_profit", "max_loss",
                      "breakevens", "return_on_risk", "pop"}
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_calc_multileg.py -k generic -v`
Expected: FAIL — `module 'options_calculator' has no attribute 'calc_summary_generic'`.

**Step 3: Implement**

```python
def calc_summary_generic(legs, spot, r=0.045, iv=0.20, T=None):
    """Numeric summary for ANY leg set (incl. butterfly/condor/calendar).

    Evaluates net position P&L across a dense price grid at the FRONT-leg
    expiry (calendars price the back leg via BS at its remaining T) and reads
    max-profit / max-loss / breakevens off that curve. PoP = risk-neutral
    lognormal probability mass over the profitable price region.
    """
    import math
    if T is None or T <= 0:
        T = 1.0 / 365.0

    # Entry net credit (short premium - long premium), scaled.
    entry_credit = 0.0
    for leg in legs:
        q = leg.get("qty", 1)
        amt = leg["premium"] * q * 100
        entry_credit += amt if leg["side"] == "short" else -amt

    # Front (nearest) expiry sets the evaluation horizon; each leg keeps its own
    # remaining T at that instant (0 for the front leg, >0 for a back leg).
    leg_t0, front_t0 = [], None
    for leg in legs:
        t = _leg_expiry_years(leg, None)   # None expiry -> None (same-expiry set)
        leg_t0.append(t)
        if t is not None:
            front_t0 = t if front_t0 is None else min(front_t0, t)
    # remaining T per leg at the front expiry
    def _leg_T(i):
        t = leg_t0[i]
        if t is None or front_t0 is None:
            return 0.0                      # same-expiry set -> expiry payoff
        return max(t - front_t0, 0.0)

    lo, hi = spot * 0.5, spot * 1.5
    n = 601
    xs = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    pnl = []
    for S in xs:
        val = 0.0
        for i, leg in enumerate(legs):
            q = leg.get("qty", 1)
            price = bs_price(S, leg["strike"], _leg_T(i), r, max(iv, 0.01),
                             leg["option_type"].lower())
            val += price * q * 100 * (1 if leg["side"] == "long" else -1)
        pnl.append(entry_credit + val)

    max_profit = max(pnl)
    max_loss_signed = min(pnl)            # most-negative P&L
    max_loss = abs(max_loss_signed) if max_loss_signed < 0 else 0.0

    # Breakevens: sign changes in the P&L curve (linear-interpolated crossings).
    bes = []
    for k in range(1, n):
        a, b = pnl[k - 1], pnl[k]
        if (a <= 0 < b) or (a >= 0 > b):
            x0, x1 = xs[k - 1], xs[k]
            bes.append(round(x0 + (x1 - x0) * (0 - a) / (b - a), 2))

    ror = (max_profit / max_loss * 100) if max_loss > 0 else 0.0

    # PoP: risk-neutral lognormal mass over the profitable region (P&L > 0).
    mu = math.log(spot) + (r - 0.5 * iv * iv) * T
    sd = iv * math.sqrt(T)

    def _cdf_S(x):
        return norm_cdf((math.log(x) - mu) / sd) if x > 0 and sd > 0 else 0.0

    pop = 0.0
    prev_prof = pnl[0] > 0
    seg_start = xs[0]
    for k in range(1, n):
        prof = pnl[k] > 0
        if prof != prev_prof:
            if prev_prof:
                pop += _cdf_S(xs[k]) - _cdf_S(seg_start)
            seg_start = xs[k]
            prev_prof = prof
    if prev_prof:
        pop += _cdf_S(xs[-1]) - _cdf_S(seg_start)

    return {
        "entry_credit": round(entry_credit, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": sorted(bes),
        "return_on_risk": round(ror, 2),
        "pop": round(max(0.0, min(100.0, pop * 100.0)), 2),
    }
```

**Step 4: Run to verify pass**

Run: `python -m pytest tests/test_calc_multileg.py -v`
Expected: PASS (all).

**Step 5: Commit**

```bash
git add options-scanner/options_calculator.py options-scanner/tests/test_calc_multileg.py
git commit -m "feat(calculator): generic numeric summary for multi-leg/calendar strategies"
```

---

## Task 5: Calculator compute wiring — `calc_compute` routes new strategies

Route `summary` to analytic vs generic, and pass `per_leg_expiry=True` to the
grid. Per-leg `expiry`/`qty` already ride on each leg dict.

**Files:**
- Modify: `services/options_svc/compute.py` (`calc_compute` ~L1163-1218)
- Test: `services/options_svc/tests/test_compute.py` (extend)

**Step 1: Add failing test**

```python
def test_calc_compute_butterfly_uses_generic_summary():
    import datetime as dt
    import services.options_svc.compute as compute
    exp = (dt.date.today() + dt.timedelta(days=20)).isoformat()
    legs = [
        {"strike": 95, "option_type": "call", "side": "long", "premium": 6.0, "qty": 1, "expiry": exp},
        {"strike": 100, "option_type": "call", "side": "short", "premium": 3.0, "qty": 2, "expiry": exp},
        {"strike": 105, "option_type": "call", "side": "long", "premium": 1.5, "qty": 1, "expiry": exp},
    ]
    out = compute.calc_compute(strategy="CUSTOM", spot=100, iv=0.25, rate=0.04,
                               ivadj=0.0, qty=1, expiry=exp, legs=legs,
                               range_min=0, range_max=0, range_pct=0.10)
    s = out["summary"]
    assert s["max_loss"] > 0 and s["max_profit"] > 0
    assert len(s["breakevens"]) == 2
```

**Step 2: Run to verify failure**

Run: `cd "D:\WebGUI Trading with Schwab" ; .venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k butterfly -q`
Expected: FAIL — analytic `calc_summary` raises/returns wrong shape for a 3-leg fly (IndexError or mis-summary).

**Step 3: Implement**

In `compute.calc_compute`, replace the single summary call with routing, and add
`per_leg_expiry=True` to the grid call:

```python
    from pages.options import strategies as _strat   # pure import is safe in-service
    code = strategy if strategy in _strat._ANALYTIC_CODES else "CUSTOM"
    if code == "CUSTOM":
        summary = oc.calc_summary_generic(legs, spot, r=rate, iv=iv, T=t_now)
    else:
        summary = oc.calc_summary(legs, code, spot, r=rate, iv=iv, T=t_now)
```

> If importing `pages.options.strategies` from the service is awkward on the
> service's `sys.path`, inline the analytic-code set as a module constant in
> `compute.py` (`_ANALYTIC_CODES = {"PCS","CCS","IC","LONG_CALL","LONG_PUT",
> "NAKED_CALL","NAKED_PUT"}`) and route on that — do NOT import a Tier-1 page
> module into Tier-2. **Prefer the inline constant.**

And the grid call:

```python
    pnl_data = oc.calc_spread_pnl(legs, spot, iv, rate, [None] * len(columns),
                                  price_range, expiry_date, iv_adjustment=ivadj,
                                  eval_times=eval_times, per_leg_expiry=True)
```

For calendars, also broaden the eval columns to run to the FRONT expiry — when
legs carry multiple expiries, set `expiry_date`/`settlement` to the nearest leg
expiry before building `columns` (so "Exp" = front expiry). Compute:

```python
    leg_exps = [dt.date.fromisoformat(str(l["expiry"])) for l in legs if l.get("expiry")]
    front = min(leg_exps) if leg_exps else expiry_date
    settlement = _expiry_settlement(front)
    t_now = time_to_expiry_years(now, front)
    expiry_date = front
```

**Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k "butterfly or calc" -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(calculator): route multi-leg/calendar to generic summary + per-leg-expiry grid"
```

---

## Task 6: Simulator compute — multi-leg `sim_run` + per-leg What-if Δt

`sim_run` takes a `legs` list, builds a multi-leg `Position`, and prices What-if
with per-leg elapsed-time decay (fixes calendars). IV-shock already per-legs T.

**Files:**
- Modify: `services/options_svc/compute.py` (`sim_run` ~L1296-1331)
- Test: `services/options_svc/tests/test_compute.py` (extend)

**Step 1: Add failing test**

```python
def test_sim_run_multileg_vertical_nets_legs(monkeypatch):
    import datetime as dt
    import services.options_svc.compute as compute
    from options_simulator.engine import ChainSnapshot, ContractRow
    import pandas as pd
    exp = dt.date.today() + dt.timedelta(days=10)
    snap = ChainSnapshot(spot=100, as_of=dt.datetime.now(), r=0.04, symbol="TEST",
                         contracts=[
                             ContractRow(95, "put", 1, 1.1, 1.05, 0.30, exp),
                             ContractRow(90, "put", 0.5, 0.6, 0.55, 0.32, exp)],
                         price_history=pd.Series(dtype=float))
    compute._SIM_SNAPSHOTS["TEST"] = snap
    legs = [{"kind": "put", "strike": 95, "expiry": exp.isoformat(), "side": "short", "qty": 1},
            {"kind": "put", "strike": 90, "expiry": exp.isoformat(), "side": "long", "qty": 1}]
    out = compute.sim_run("TEST", legs=legs, dt=0.0, mult=1.5)
    assert out["spot"] == 100
    assert out["whatif_rows"] and "S" in out["whatif_rows"][0]
    assert out["ivshock"] and "base" in out["ivshock"]
```

**Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k sim_run_multileg -q`
Expected: FAIL — `sim_run()` signature mismatch (`legs` unexpected).

**Step 3: Implement**

Replace `sim_run`'s single-contract signature with a `legs`-based one (keep a
back-compat shim so old single-arg callers still work, OR update the handler in
Task 8 to always pass `legs`). New body:

```python
def sim_run(symbol, legs=None, dt=5.0, mult=1.5, **_legacy) -> dict:
    """Compute What-if + IV-shock for a MULTI-LEG position → JSON-safe dict.

    ``legs`` is a list of {kind, strike, expiry, side, qty}. The What-if sweep
    advances each leg by ``dt`` ELAPSED days from now (per-leg decay → calendars
    work); IV-shock + the engine already price each leg at its own expiry.
    """
    from options_simulator import engine as seng
    import numpy as np
    import datetime as _dt

    snap = _SIM_SNAPSHOTS.get(symbol)
    if snap is None or not legs:
        return {}
    resolved = []      # (ContractRow, sign, ratio)
    for leg in legs:
        c = find_contract(snap, leg.get("expiry"), leg.get("kind"), leg.get("strike"))
        if c is None:
            return {}
        sign = +1 if leg.get("side", "long") == "long" else -1
        resolved.append((c, sign, int(leg.get("qty", 1))))
    pos = seng.Position.from_legs(resolved, label=f"{snap.symbol} {len(resolved)}-leg")

    # What-if: per-leg elapsed-time decay. forward_days(c) = leg_dte_now - dt.
    today = _dt.date.today()
    def _forward_days(c):
        dte_now = max((c.expiry - today).days, 0)
        return max(dte_now - float(dt), 0.01)

    s_range = np.linspace(snap.spot * 0.8, snap.spot * 1.2, 81)
    whatif_eng = seng.WhatIfEngine(snap)
    wdf = seng.aggregate_position(pos, lambda c: whatif_eng.sweep(c, s_range, _forward_days(c)))
    whatif_rows = _sim_records(wdf)

    shock_eng = seng.IVShockEngine(snap)
    sdf = seng.aggregate_position(pos, lambda c: shock_eng.sweep(c, [1.0, float(mult)]))
    rows = sdf.to_dict("records") if hasattr(sdf, "to_dict") else list(sdf or [])
    ivshock = {"base": rows[0], "shock": rows[1]} if len(rows) >= 2 else None
    return {"spot": snap.spot, "whatif_rows": whatif_rows, "ivshock": ivshock}
```

> `find_contract(snap, expiry, kind, strike)` already matches by
> `str(c.expiry) == str(expiry)` so ISO strings resolve fine. Note the What-if
> Δt is now ELAPSED days (was absolute DTE) — the page label changes in Task 12.

**Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k sim_run -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(simulator): multi-leg sim_run + per-leg elapsed What-if decay (calendars)"
```

---

## Task 7: Simulator compute — multi-leg `sim_replay`

**Files:**
- Modify: `services/options_svc/compute.py` (`sim_replay` ~L1371-1474)
- Test: `services/options_svc/tests/test_compute.py` (extend)

**Step 1: Add failing test** — build a snapshot with a small `price_history`
Series (2-3 timestamps), call `sim_replay("TEST", legs=[...])`, assert the trace
has `x`/`prices`/`greeks` keys and `greeks["delta"]` length == price history
length. (Mirror the Task 6 snapshot; add `price_history=pd.Series([100,101],
index=pd.to_datetime([...]))`.)

**Step 2: Run to verify failure** (signature mismatch).

**Step 3: Implement** — change the signature to
`sim_replay(symbol, legs=None, lookback="auto", **_legacy)`. Resolve legs →
`Position.from_legs` exactly like Task 6. Compute DTE from the **nearest** leg
expiry (`min(c.expiry for c,_,_ in resolved)`) for `replay_lookback_spec`.
Guard `if any(c.iv <= 0 for c, _, _ in resolved): return {"error": "IV
unavailable - cannot simulate"}`. The rest (history fetch, gap compression,
trace assembly) is unchanged — `aggregate_position(pos, lambda c:
ReplayEngine(snap_path).full_trace(c))` already per-legs T.

**Step 4: Run to verify pass.**

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(simulator): multi-leg sim_replay"
```

---

## Task 8: Handler dispatch — pass `legs` to sim_run / sim_replay

**Files:**
- Modify: `services/options_svc/handlers.py` (L542-555)
- Test: `services/options_svc/tests/test_handlers.py` (extend)

**Step 1: Add failing test** — enqueue a `sim_run` command whose args carry
`legs` + `dt`/`mult` through `handle_command` against a fakeredis `Bus` (mirror
the existing sim handler tests), assert `cache:options:sim_result` is written.

**Step 2: Run to verify failure.**

**Step 3: Implement**

```python
    elif command.type == "sim_run":
        a = command.args or {}
        result = compute.sim_run(a.get("symbol"), legs=a.get("legs"),
                                 dt=a.get("dt", 5.0), mult=a.get("mult", 1.5))
        version = bus.cache_set(CACHE_SIM_RESULT, result)
        bus.publish(EVENT_SIM_RESULT, {"version": version})
    elif command.type == "sim_replay":
        a = command.args or {}
        res = compute.sim_replay(a.get("symbol"), legs=a.get("legs"),
                                 lookback=a.get("lookback", "auto"))
        version = bus.cache_set(CACHE_SIM_REPLAY, res)
        bus.publish(EVENT_SIM_REPLAY, {"version": version})
```

**Step 4: Run to verify pass.** Then run the whole service suite:
`.venv\Scripts\python -m pytest services\options_svc -q` (expected: green).

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(simulator): handler passes multi-leg legs to sim_run/sim_replay"
```

---

## Task 9: Shared leg-editor widget — `leg_editor.py` (Tier-1)

A parameterized editable leg table both pages mount. Pure-logic (get/set,
template apply, dirty tracking) is unit-tested; the nicegui rendering is smoke-only.

**Files:**
- Create: `webgui/pages/options/leg_editor.py`
- Test: `webgui/tests/test_leg_editor.py` (create)

**Step 1: Write failing tests** (logic only — no nicegui)

```python
# webgui/tests/test_leg_editor.py
from pages.options import leg_editor as LE


def test_legs_to_payload_roundtrip():
    legs = [{"option_type": "call", "side": "long", "strike": 100.0,
             "expiry": "2026-07-17", "qty": 1, "premium": 2.5}]
    p = LE.legs_to_payload("SPY", legs)
    assert p["symbol"] == "SPY"
    assert p["legs"][0]["strike"] == 100.0
    # payload legs keep only the normalized keys
    assert set(p["legs"][0]) == {"option_type", "side", "strike", "expiry", "qty", "premium"}


def test_normalize_drops_premium_when_requested():
    legs = [{"option_type": "put", "side": "short", "strike": 95, "expiry": "2026-07-17",
             "qty": 2, "premium": 1.1}]
    out = LE.normalize_legs(legs, keep_premium=False)
    assert out[0]["premium"] is None
    assert out[0]["qty"] == 2
```

**Step 2: Run to verify failure.**

**Step 3: Implement** — the pure helpers + the widget builder. Pure helpers
(`normalize_legs`, `legs_to_payload`) first (they pass the tests); then
`build_leg_editor`:

```python
"""Shared editable leg-editor for the Simulator + Calculator (Tier-1).

Pure helpers (normalize/payload) are unit-tested; build_leg_editor renders one
row per leg with kind/side/strike/expiry/qty[/premium] + add/remove and a
Strategy dropdown that applies a template. Each page injects strikes_for /
expiries_for (its own data source) and show_premium.
"""
from nicegui import ui

from . import strategies as S

_KEYS = ("option_type", "side", "strike", "expiry", "qty", "premium")


def normalize_legs(legs, keep_premium=True):
    out = []
    for l in legs or []:
        out.append({
            "option_type": l.get("option_type"),
            "side": l.get("side"),
            "strike": l.get("strike"),
            "expiry": l.get("expiry"),
            "qty": int(l.get("qty", 1) or 1),
            "premium": (l.get("premium") if keep_premium else None),
        })
    return out


def legs_to_payload(symbol, legs, keep_premium=True):
    return {"symbol": (symbol or "").replace("$", "").upper(),
            "legs": normalize_legs(legs, keep_premium=keep_premium)}


def build_leg_editor(container, *, strikes_for, expiries_for, show_premium,
                     on_change, spot_getter=lambda: 0.0):
    """Mount the editor into ``container``. Returns a handle with get_legs() /
    set_legs(legs) / apply_template(name) / dirty flag."""
    state = {"rows": [], "dirty": False}

    def _emit():
        state["dirty"] = True
        on_change()

    def _row_legs():
        out = []
        for r in state["rows"]:
            out.append({"option_type": r["otype"].value, "side": r["side"].value,
                        "strike": r["strike"].value, "expiry": r["expiry"].value,
                        "qty": int(r["qty"].value or 1),
                        "premium": (r["premium"].value if show_premium else None)})
        return out

    def _render():
        container.clear()
        with container:
            for i, r in enumerate(list(state["rows"])):
                with ui.row().classes("items-end gap-2 no-wrap"):
                    r["otype"] = ui.select(["call", "put"], value=r["_d"]["option_type"],
                                           label="Type").classes("w-24")
                    r["side"] = ui.select(["long", "short"], value=r["_d"]["side"],
                                          label="Side").classes("w-24")
                    r["expiry"] = ui.select(expiries_for(), value=r["_d"]["expiry"],
                                            label="Expiry").classes("w-40")
                    r["strike"] = ui.select(strikes_for(r["_d"]["expiry"], r["_d"]["option_type"]),
                                            value=r["_d"]["strike"], label="Strike").classes("w-28")
                    r["qty"] = ui.number("Qty", value=r["_d"].get("qty", 1), min=1, max=100).classes("w-20")
                    if show_premium:
                        r["premium"] = ui.number("Premium", value=r["_d"].get("premium") or 0.0,
                                                 format="%.2f").classes("w-28")
                    ui.button(icon="close", on_click=lambda _e, idx=i: _remove(idx)).props("flat dense round")
                    for w in ("otype", "side", "expiry", "strike", "qty"):
                        r[w].on_value_change(lambda _e: (_sync_strikes(), _emit()))
                    if show_premium:
                        r["premium"].on_value_change(lambda _e: _emit())
            ui.button("Add leg", icon="add", on_click=_add).props("flat dense")

    def _sync_strikes():
        for r in state["rows"]:
            if "strike" in r and "otype" in r:
                opts = strikes_for(r["expiry"].value, r["otype"].value)
                r["strike"].options = opts
                if opts and r["strike"].value not in opts:
                    r["strike"].value = min(opts, key=lambda s: abs(s - spot_getter()))
                r["strike"].update()

    def _add():
        d = {"option_type": "call", "side": "long", "strike": None,
             "expiry": (expiries_for() or [None])[0], "qty": 1, "premium": None}
        state["rows"].append({"_d": d})
        _render(); _emit()

    def _remove(idx):
        if 0 <= idx < len(state["rows"]):
            state["rows"].pop(idx); _render(); _emit()

    def set_legs(legs):
        state["rows"] = [{"_d": dict(l)} for l in normalize_legs(legs)]
        state["dirty"] = False
        _render()

    def apply_template(name):
        legs = S.build_default_legs(name, spot_getter(), strikes_for(None, "call") or [],
                                    expiries_for())
        set_legs(legs)

    handle = type("LegEditor", (), {})()
    handle.get_legs = _row_legs
    handle.set_legs = set_legs
    handle.apply_template = apply_template
    handle.sync_strikes = _sync_strikes
    handle.is_dirty = lambda: state["dirty"]
    return handle
```

> `strikes_for(None, otype)` should return the union of strikes across expiries
> (the page implements it tolerantly). Keep the widget defensive — a missing
> strike list just yields an empty select.

**Step 4: Run to verify pass** (logic tests):
`..\.venv\Scripts\python -m pytest tests/test_leg_editor.py -v` → PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/leg_editor.py webgui/tests/test_leg_editor.py
git commit -m "feat(options): shared editable leg-editor widget"
```

---

## Task 10: Handoff — simulator stash + both-way copy

**Files:**
- Modify: `webgui/pages/options/handoff.py`
- Test: `webgui/tests/test_handoff_legs.py` (create)

**Step 1: Write failing tests**

```python
# webgui/tests/test_handoff_legs.py
from pages.options import handoff


def test_simulator_stash_is_one_shot():
    payload = {"symbol": "SPY", "legs": [{"option_type": "call", "side": "long",
               "strike": 100, "expiry": "2026-07-17", "qty": 1, "premium": None}]}
    handoff.set_pending_simulator(payload)
    assert handoff.take_pending_simulator() == payload
    assert handoff.take_pending_simulator() is None  # cleared after first take
```

**Step 2: Run to verify failure.**

**Step 3: Implement** — add to `handoff.py`:

```python
_pending = {"calculator": None, "expected_move": None, "simulator": None}

def set_pending_simulator(payload):
    _pending["simulator"] = payload

def take_pending_simulator():
    p = _pending.get("simulator")
    _pending["simulator"] = None
    return p

def send_to_simulator(payload):
    if not payload or not payload.get("symbol"):
        ui.notify("No legs to copy.", type="warning"); return
    set_pending_simulator(payload)
    ui.navigate.to("/options/simulator")
```

(And a matching `send_to_calculator_legs(payload)` that stashes a leg payload
into `_pending["calculator"]` — OR reuse the existing `calculator` stash with a
shape check on the receiving side. Prefer a separate `calculator_legs` stash key
to avoid colliding with the scanner-signal prefill: add
`_pending["calculator_legs"]` + `set/take_pending_calculator_legs`.)

**Step 4: Run to verify pass.**

**Step 5: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/tests/test_handoff_legs.py
git commit -m "feat(options): cross-page leg stash for Simulator<->Calculator copy"
```

---

## Task 11: Calculator page — mount shared editor + new strategies + Copy-to-Simulator

**Files:**
- Modify: `webgui/pages/options/calculator.py`
- Test: `webgui/tests/test_options_page.py` (extend a render smoke + builder test)

**Steps (TDD where pure, smoke + manual for UI):**

1. **Replace `LEG_SPECS`/`rebuild_legs`** with the shared editor. Build the
   strategy dropdown from `strategies.STRATEGY_GROUPS`. Mount
   `leg_editor.build_leg_editor(leg_box, strikes_for=_strikes_for,
   expiries_for=lambda: chain_expiries(state["chain"] or {}), show_premium=True,
   on_change=lambda: None, spot_getter=lambda: float(price_in.value or 0))`.
   `_strikes_for(expiry, otype)` → `chain_strikes(state["chain"], expiry or
   expiry_sel-default, otype)` (union across expiries when `expiry is None`).
2. **`do_calc`**: build `legs` from `editor.get_legs()` (each already carries
   `option_type/side/strike/expiry/qty/premium`); send `strategy =
   strategy_sel.value if not editor.is_dirty() else "CUSTOM"`. Keep the rest of
   the params dict. (The per-leg `expiry` now flows to the grid.)
3. **`fetch_premiums`**: iterate `editor.get_legs()`, fill each leg's premium via
   `extract_premium(chain, otype, strike, expiry=leg_expiry)`; call
   `editor.set_legs(updated)` to repaint.
4. **Copy to Simulator** button → `handoff.send_to_simulator(
   leg_editor.legs_to_payload(symbol, editor.get_legs(), keep_premium=False))`.
5. **Receive copied legs**: after `_apply_chain`, check
   `handoff.take_pending_calculator_legs()`; if present, set symbol + `editor.set_legs(p["legs"])`
   then auto-`fetch_premiums()` + `do_calc()`.
6. **Keep the scanner-signal `_prefill`** path working (it now calls
   `editor.set_legs(...)` built from the signal instead of the old `setleg`).

**Test:** extend `test_options_page.py` with a builder test that calls
`do_calc`'s param-assembly indirectly via a small pure helper if extracted, plus
a `render()` smoke (import + call within a NiceGUI test client, asserting no
exception) — mirror the existing calculator render smoke test.

**Manual verify (Preview tool, :8500):** Load SPY → pick **Butterfly (call)** →
Fetch Premiums → Calculate → summary tiles + heatmap render; switch to
**Calendar** → two expiries appear on the legs → Calculate shows a calendar
risk curve.

**Commit:**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_options_page.py
git commit -m "feat(calculator): editable multi-leg strategies + Copy to Simulator"
```

---

## Task 12: Simulator page — replace selector with editor + Δt elapsed + Copy-to-Calculator

**Files:**
- Modify: `webgui/pages/options/simulator.py`
- Test: `webgui/tests/test_options_scanner.py` or a new `webgui/tests/test_simulator_page.py`

**Steps:**

1. **Remove** the single-contract selector row (`expiry_sel`/`kind_tog`/
   `strike_sel`/`dir_tog`). Add a **Strategy** dropdown (from `STRATEGY_GROUPS`)
   + mount `leg_editor.build_leg_editor(legs_box, strikes_for=_strikes_for,
   expiries_for=lambda: (state["meta"] or {}).get("expiries") or [],
   show_premium=False, spot_getter=lambda: (state["meta"] or {}).get("spot") or 0)`,
   where `_strikes_for(expiry, otype)` reads `state["meta"]["strikes"][expiry][otype]`
   (union across expiries when `expiry is None`).
2. **`_current_params`/`_enqueue_run`**: send
   `{"symbol":…, "legs": editor.get_legs(), "dt": dt_slider.value,
   "mult": mult_slider.value}`. **`_enqueue_replay`**: send
   `{"symbol":…, "legs": editor.get_legs(), "lookback": lookback_sel.value}`.
   Trigger both on editor `on_change` (debounced for the dt/mult sliders as today).
3. **What-if Δt label** → "Δt {v}d elapsed" and the slider stays 0..30 (now
   elapsed days). Update the tooltip/help text.
4. **Copy to Calculator** button →
   `handoff.send_to_calculator_legs(leg_editor.legs_to_payload(symbol,
   editor.get_legs(), keep_premium=False))`.
5. **Receive copied legs**: after `_apply_meta`, check
   `handoff.take_pending_simulator()`; if present set symbol + auto-`_request_fetch()`,
   and once meta arrives `editor.set_legs(p["legs"])` then `_enqueue_run()/_enqueue_replay()`.
   (Stash the pending legs in `state["pending_legs"]` until meta lands.)
6. Keep the three charts persistent + reflow-on-tab-change exactly as today
   (ESM-import-map + inactive-tab-collapse gotchas).

**Test:** a render smoke (`render()` builds without exception) + a pure builder
test if any new pure helper is extracted (e.g., a `params_from_editor` function —
extract it so it's unit-testable, mirroring `_current_params`).

**Manual verify (Preview tool, :8500):** Fetch SPY → pick **Iron Butterfly** →
What-if curve is the tent payoff; **Replay** shows the netted 5-Greek stack;
pick **Calendar** → What-if at Δt>0 shows the back leg retaining value. Then
click **Copy to Calculator** → Calculator opens with the same legs + premiums
filled.

**Commit:**

```bash
git add webgui/pages/options/simulator.py webgui/tests/test_simulator_page.py
git commit -m "feat(simulator): editable multi-leg strategies + elapsed What-if + Copy to Calculator"
```

---

## Task 13: Live end-to-end verification (Redis-driven)

The most reliable 3-tier check bypasses the browser. With Memurai + the proxy +
`options_svc` running:

**Step 1:** Fetch a snapshot, then run a calendar sim:

```python
# scratch_verify.py (run from repo root with .venv)
from shared.bus import Bus
import time
b = Bus()
b.enqueue_command("cmd:options", {"type": "sim_fetch", "args": {"symbol": "SPY"}})
time.sleep(3)
meta = b.cache_get("cache:options:sim_meta")
exps = meta["expiries"][:2]            # near + far
k = min(meta["strikes"][exps[0]]["call"], key=lambda s: abs(s - meta["spot"]))
legs = [{"kind": "call", "strike": k, "expiry": exps[0], "side": "short", "qty": 1},
        {"kind": "call", "strike": k, "expiry": exps[1], "side": "long", "qty": 1}]
b.enqueue_command("cmd:options", {"type": "sim_run",
                  "args": {"symbol": "SPY", "legs": legs, "dt": 3, "mult": 1.5}})
time.sleep(3)
res = b.cache_get("cache:options:sim_result")
assert res["whatif_rows"], res
print("calendar What-if OK:", len(res["whatif_rows"]), "rows")
```

Expected: prints a row count; no `error` key.

**Step 2:** Repeat for `calc_compute` with an iron butterfly (4 legs, one
expiry) → assert `summary.max_loss > 0` and 2 breakevens.

**Step 3:** Browser smoke (Preview tool): both pages render, the copy buttons
round-trip a calendar between them.

**Step 4 (no commit of scratch):** delete `scratch_verify.py`. Then run ALL
suites once more:

```bash
cd "D:\WebGUI Trading with Schwab\options-scanner" ; python -m pytest tests -q
cd "D:\WebGUI Trading with Schwab" ; .venv\Scripts\python -m pytest services\options_svc -q
cd "D:\WebGUI Trading with Schwab\webgui" ; ..\.venv\Scripts\python -m pytest -q
```

Expected: green except the 2 known pre-existing options-scanner earnings-fixture
fails.

---

## Task 14: Update docs

**Files:**
- Modify: root `CLAUDE.md` (Simulator + Calculator route descriptions + a new
  "Multi-leg strategies" note + Last-updated banner)
- Modify: `options-scanner/CLAUDE.md` (Options Simulator overview — note multi-leg
  + ratio legs)

Mirror the existing house style (a dated "DONE" paragraph). Commit:

```bash
git add CLAUDE.md options-scanner/CLAUDE.md
git commit -m "docs: multi-leg strategies in Simulator + Calculator"
```

---

## Done criteria

- Both pages build/price/analyze verticals (credit+debit), condors (iron +
  all-same), butterflies (long+iron), calendars + diagonals, with editable legs.
- Copy-legs works both ways and round-trips a calendar.
- What-if decays each leg by its own clock (calendars correct); IV-shock + Replay
  net all legs.
- The Calculator summarizes new structures numerically; PCS/CCS/IC/singles keep
  their exact analytic summary (no regressions).
- All per-folder suites green (modulo the 2 known options-scanner fails).
```
