# Strategy Finder — Every Structure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Teach the Strategy Finder (`/options/swing`) to build, price and score 13
more option structures (straddles, strangles, butterflies, iron butterfly, condors,
calendars, diagonals, covered call, protective put, collar), with Send to Paper
only for the debit structures the Paper Ledger already handles correctly.

**Architecture:** New pure builders in `options-scanner/strategy_scanner.py` emit
the existing normalized candidate shape. `payoff_metrics` / `pop_from_payoff` gain
a second valuation path — front-expiry Black-Scholes for later-expiring legs, spot
for share legs — taken ONLY when a leg set holds one, so the existing 9 structures'
numbers are byte-identical. `strategy_scoring._TYPE_PROFILE` maps each new type to
an existing gate profile; `compute.swing_scan` gains four build groups; the page
gains four checkboxes. Design: `docs/plans/2026-09-13-strategy-finder-all-structures-design.md`.

**Tech Stack:** Python 3.11, pytest, NiceGUI (Tier 1), Redis bus (fakeredis under
pytest). No new dependencies.

---

## Ground rules for whoever executes this

- **Run each app's tests from inside that app folder** (see CLAUDE.md "Tests").
  On this Windows workstation the venv is `D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe`
  — call it `$PY` below. From a worktree you must use that absolute path.
- **Compare the failing SET, never the count** (`-rf` is on by default).
- **Never weaken an existing assertion to make a new change pass.** If an existing
  test fails, the change is wrong or the design needs revisiting — stop and say so.
- **Dates in new tests are relative to today** (`date.today() + timedelta(...)`),
  never absolute: `strategy_scanner._dte_for` measures from today, and absolute
  fixture dates have already rotted a suite once.
- Commit after every task. End each message with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

### Two vocabularies that must not be confused

| name | what it is | values |
|---|---|---|
| **build group** | what the page's checkbox sends as `families`; decides which builders run | `DIRECTIONAL`, `VERTICAL`, `NEUTRAL` (existing), **`STRADDLE`, `BUTTERFLY`, `CALENDAR`, `STOCK`** (new) |
| **signal `family`** | a field ON each candidate; read by `strategy_scoring.q_breakeven_vs_em` | `DIRECTIONAL`, `VERTICAL`, `NEUTRAL` only |

For the three existing groups the two coincide. The new groups do not: every
straddle/strangle/butterfly/condor/calendar carries `family="NEUTRAL"` (wide profit
zone is what is good), and diagonals + share structures carry `family="DIRECTIONAL"`.

### The share leg (D4 convention)

```python
{"kind": "stock", "side": "long", "strike": None, "expiration": None, "qty": 1,
 "mark": spot, "delta": 1.0, "theta": 0.0, "vega": 0.0, "gamma": 0.0, "iv": 0.0}
```

`qty` counts **100-share lots**, exactly as an option leg's `qty` counts contracts,
so every existing `× 100` stays correct. No `bid`/`ask`/`oi`/`volume` keys: the
liquidity scorer skips a leg without a quote, which is right (the stock is not the
illiquid part).

---

### Task 1: Commission counts CONTRACTS, not leg dicts

A butterfly is 4 contracts (1 + 2 + 1) held in 3 leg dicts, because its body is
one dict at `qty: 2`. `commissions.round_trip_commission` is handed `len(legs)`, so
it would bill 3 contracts, one short. And a share leg must not be billed at all. Every existing leg set is all-options
with `qty 1`, where contracts == `len(legs)`, so the existing numbers do not move.

**Files:**
- Modify: `options-scanner/strategy_scanner.py` (`payoff_metrics`, the `comm = ...` line ~112)
- Test: `options-scanner/tests/test_strategy_scanner.py`

**Step 1: Write the failing tests** (append to the test file)

```python
# ---- Strategy Finder: every structure (2026-09-13) ----
import datetime as _dt


def _exp(days):
    return (_dt.date.today() + _dt.timedelta(days=days)).isoformat()


def _stock(spot, qty=1):
    return {"kind": "stock", "side": "long", "strike": None, "expiration": None,
            "qty": qty, "mark": spot, "delta": 1.0, "theta": 0.0, "vega": 0.0,
            "gamma": 0.0, "iv": 0.0}


def test_option_contracts_counts_qty_and_ignores_shares():
    fly = [_leg("call", "long", 445.0, 8.0), _leg("call", "short", 450.0, 6.0, qty=2),
           _leg("call", "long", 455.0, 3.5)]
    assert ss._option_contracts(fly) == 4
    assert ss._option_contracts([_stock(450.0), _leg("call", "short", 455.0, 3.5)]) == 1


def test_butterfly_commission_charges_four_contracts():
    fly = [_leg("call", "long", 445.0, 8.0), _leg("call", "short", 450.0, 6.0, qty=2),
           _leg("call", "long", 455.0, 3.5)]
    assert ss.payoff_metrics(fly, spot=450.0)["commission"] == round(4 * 0.65 * 2, 4)


def test_existing_qty_one_commission_is_unchanged():
    legs = [_leg("call", "long", 450.0, 6.0), _leg("call", "short", 455.0, 3.5)]
    assert ss.payoff_metrics(legs, spot=450.0)["commission"] == 2.60
```

**Step 2: Run to verify they fail**

Run: `cd options-scanner && $PY -m pytest tests/test_strategy_scanner.py -k "contracts or butterfly_commission or qty_one" -p no:randomly`
Expected: FAIL — `AttributeError: module 'strategy_scanner' has no attribute '_option_contracts'`, and the butterfly asserting 5.2 against 3.9.

**Step 3: Implement**

Add above `payoff_metrics`:

```python
def _is_stock(leg):
    """A 100-share-lot leg (the Calculator's D4 convention), not an option."""
    return leg.get("kind") == "stock"


def _option_contracts(legs):
    """Option CONTRACTS in a leg set: each option leg's qty, share legs excluded.

    ``commissions.round_trip_commission`` bills per contract but was handed
    ``len(legs)``, which is right only while every leg is one contract. A
    butterfly's body is one dict at qty 2, and Schwab charges nothing for stock.
    For every all-options qty-1 leg set this equals ``len(legs)``, so the existing
    nine structures are billed exactly as before.
    """
    return sum(int(l.get("qty", 1) or 1) for l in legs if not _is_stock(l))
```

In `payoff_metrics`, replace

```python
    comm = _cm.round_trip_commission(legs, symbol, 1)
```

with

```python
    comm = _cm.round_trip_commission(_option_contracts(legs), symbol, 1)
```

**Step 4: Run the whole file**

Run: `cd options-scanner && $PY -m pytest tests/test_strategy_scanner.py -p no:randomly`
Expected: all PASS (existing + 3 new).

**Step 5: Commit**

```bash
git add options-scanner/strategy_scanner.py options-scanner/tests/test_strategy_scanner.py
git commit -m "fix(finder): bill commission per contract, not per leg dict"
```

---

### Task 2: Payoff at the front expiry for calendars and share legs

**Files:**
- Modify: `options-scanner/strategy_scanner.py` (`_pl_at`, `payoff_metrics`, `pop_from_payoff`, `_assemble`)
- Test: `options-scanner/tests/test_strategy_scanner.py`

**Step 1: Write the failing tests**

```python
def test_single_expiry_options_never_take_the_front_valuation_path(monkeypatch):
    """The existing nine structures must be byte-identical: prove the new path is
    not even entered for an all-options single-expiry set."""
    def _boom(*a, **k):
        raise AssertionError("front-expiry valuation used on a single-expiry set")
    monkeypatch.setattr(ss, "_front_value", _boom)
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    ss.payoff_metrics(legs, spot=450.0)
    ss.pop_from_payoff(legs, 450.0, 0.18, 10)


def _cal_legs(front_days=14, back_days=42, K=100.0):
    import options_calculator as oc
    f, b = front_days / 365, back_days / 365
    short = _leg("call", "short", K, oc.bs_price(100.0, K, f, oc.RISK_FREE_RATE, 0.28, "call"),
                 iv=28.0)
    long_ = _leg("call", "long", K, oc.bs_price(100.0, K, b, oc.RISK_FREE_RATE, 0.26, "call"),
                 iv=26.0)
    short["expiration"], long_["expiration"] = _exp(front_days), _exp(back_days)
    return short, long_


def test_calendar_max_loss_is_its_debit_plus_commission():
    short, long_ = _cal_legs()
    m = ss.payoff_metrics([short, long_], spot=100.0)
    debit = (long_["mark"] - short["mark"]) * 100
    assert m["net_debit"] == round(debit, 2)
    assert m["unbounded"] is False
    assert abs(m["max_loss"] - (debit + 2.60)) < 1.0
    assert m["max_profit"] > 0
    assert len(m["breakevens"]) == 2


def test_calendar_back_leg_is_not_valued_at_intrinsic():
    """At the strike the front call is worthless and the back call still has time
    value - intrinsic-only math would call the peak a loss of the whole debit."""
    short, long_ = _cal_legs()
    m = ss.payoff_metrics([short, long_], spot=100.0)
    assert m["max_profit"] > 100.0


def test_covered_call_max_loss_reaches_a_stock_price_of_zero():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    m = ss.payoff_metrics([_stock(100.0), call], spot=100.0)
    # entry = 100 - 1 = 99/share; worst case the stock goes to zero.
    assert abs(m["max_loss"] - (99.0 * 100 + 1.30)) < 0.01
    assert abs(m["max_profit"] - (6.0 * 100 - 1.30)) < 0.01
    assert m["unbounded_profit"] is False and m["unbounded_loss"] is False
    assert m["commission"] == 1.30          # the share leg is not billed


def test_protective_put_is_unbounded_upside_not_capped_at_the_grid():
    put = _leg("put", "long", 95.0, 1.2)
    put["expiration"] = _exp(30)
    m = ss.payoff_metrics([_stock(100.0), put], spot=100.0)
    assert m["unbounded_profit"] is True and m["max_profit"] is None
    assert abs(m["max_loss"] - ((100.0 + 1.2 - 95.0) * 100 + 1.30)) < 0.01


def test_assemble_takes_dte_from_the_front_OPTION_leg_not_a_share_leg():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    s = ss._assemble("COVERED_CALL", "DIRECTIONAL", "Covered Call", "bullish",
                     [_stock(100.0), call], "XYZ", 100.0, 0.28)
    assert s["expiration"] == _exp(30) and s["dte"] == 30
    assert s["pop_pct"] is not None
```

**Step 2: Run to verify they fail**

Run: `cd options-scanner && $PY -m pytest tests/test_strategy_scanner.py -k "front_valuation or calendar or covered_call_max or protective_put or front_OPTION" -p no:randomly`
Expected: FAIL (`_front_value` missing; `_intrinsic` KeyError on `strike: None`; `min(... expiration)` TypeError on `None`).

**Step 3: Implement** (in `strategy_scanner.py`)

Add near the top imports:

```python
import options_calculator as _oc
```

Add after `_sign`:

```python
def _option_legs(legs):
    return [l for l in legs if not _is_stock(l)]


def _front_expiration(legs):
    """Earliest expiration among the OPTION legs (a share leg never expires)."""
    exps = [l["expiration"] for l in _option_legs(legs) if l.get("expiration")]
    return min(exps) if exps else None


def _needs_front_valuation(legs):
    """True when intrinsic-at-one-expiry is WRONG for this leg set: it holds a
    share leg, or its option legs span more than one expiration.

    Everything else - every structure the Finder built before 2026-09-13 - takes
    the untouched intrinsic path, which is what keeps those numbers byte-identical.
    """
    if any(_is_stock(l) for l in legs):
        return True
    return len({l.get("expiration") for l in legs}) > 1


def _front_value(leg, S, front):
    """Per-share value of one leg at the FRONT expiration with the underlying at S.

    A share is worth S. A leg expiring at the front is worth its intrinsic. A
    later leg keeps time value: Black-Scholes at its OWN IV (the chain's
    ``volatility`` is a percent) over the calendar days between the two
    expirations. Both settle at 16:00 ET, so whole days / 365 is exact here and is
    not the inline time-to-expiry CLAUDE.md forbids (that rule is about a
    wall-clock ``now``, which does not enter this calculation).
    """
    if _is_stock(leg):
        return float(S)
    exp = leg.get("expiration")
    if not front or not exp or exp == front:
        return _intrinsic(leg, S)
    days = (_dt.date.fromisoformat(exp) - _dt.date.fromisoformat(front)).days
    if days <= 0:
        return _intrinsic(leg, S)
    iv = leg.get("iv") or 0
    sigma = iv / 100.0 if iv > 1.5 else (iv or 0.20)
    return _oc.bs_price(S, leg["strike"], days / 365.0, _oc.RISK_FREE_RATE,
                        max(sigma, 0.01), leg["kind"])
```

Replace `_pl_at` with:

```python
def _pl_at(legs, entry_cost, S, front=None):
    if front is None:
        v = sum(_sign(l) * _intrinsic(l, S) * l.get("qty", 1) for l in legs)
    else:
        v = sum(_sign(l) * _front_value(l, S, front) * l.get("qty", 1) for l in legs)
    return v - entry_cost
```

In `payoff_metrics`, directly after `net = round(entry_cost, 4)` add:

```python
    # None on every single-expiry options set -> the unchanged intrinsic path.
    front = _front_expiration(legs) if _needs_front_valuation(legs) else None
```

Replace the `call_coeff` line with (a long share lot slopes like a long call):

```python
    call_coeff = sum(_sign(l) * l.get("qty", 1) for l in legs
                     if l["kind"] == "call" or _is_stock(l))
```

Replace the breakpoint block

```python
    strikes = [l["strike"] for l in legs]
    far_high = 2.0 * max(strikes) if strikes else spot * 2.0
    points = sorted({0.0, far_high} | set(strikes))
    pls = [_pl_at(legs, entry_cost, S) for S in points]
```

with

```python
    strikes = [l["strike"] for l in legs if l.get("strike") is not None]
    far_high = 2.0 * max(strikes) if strikes else spot * 2.0
    points = {0.0, far_high} | set(strikes)
    if front is not None:
        # A Black-Scholes-valued curve peaks BETWEEN breakpoints, so sample it.
        points |= {far_high * i / 800 for i in range(801)}
    pls = [_pl_at(legs, entry_cost, S, front) for S in sorted(points)]
```

In the breakeven grid replace `gpls = [_pl_at(legs, entry_cost, S) for S in grid]`
with `gpls = [_pl_at(legs, entry_cost, S, front) for S in grid]`.

In `pop_from_payoff`, add `front = _front_expiration(legs) if _needs_front_valuation(legs) else None`
after `entry_cost = ...`, and replace the `v = sum(...)` line with
`v = _pl_at(legs, 0.0, mid, front)`.

In `_assemble`, replace

```python
    front = min(legs, key=lambda l: l["expiration"])
    dte = _dte_for(front["expiration"])
    pop = pop_from_payoff(legs, spot, atm_iv, dte)
    sk = "_".join(str(l["strike"]) for l in legs)
    return {"id": f"{symbol}_{stype}_{front['expiration']}_{sk}",
```

with

```python
    front_exp = _front_expiration(legs)
    dte = _dte_for(front_exp)
    pop = pop_from_payoff(legs, spot, atm_iv, dte)
    sk = "_".join("SH" if _is_stock(l) else str(l["strike"]) for l in legs)
    return {"id": f"{symbol}_{stype}_{front_exp}_{sk}",
```

and `"expiration": front["expiration"]` with `"expiration": front_exp`.

**Step 4: Run the whole file, then the scoring file** (scoring reads these shapes)

Run: `cd options-scanner && $PY -m pytest tests/test_strategy_scanner.py tests/test_strategy_scoring.py -p no:randomly`
Expected: all PASS. If a pre-existing test fails, the byte-identical promise is broken — stop.

**Step 5: Commit**

```bash
git add options-scanner/strategy_scanner.py options-scanner/tests/test_strategy_scanner.py
git commit -m "feat(finder): value later-expiring and share legs at the front expiry"
```

---

### Task 3: Shared strike helpers

**Files:**
- Modify: `options-scanner/strategy_scanner.py`
- Test: `options-scanner/tests/test_strategy_scanner.py`

**Step 1: Failing tests**

```python
def test_atm_strike_is_nearest_to_spot():
    assert ss._atm_strike({95.0: {}, 100.0: {}, 105.0: {}}, 101.0) == 100.0
    assert ss._atm_strike({}, 101.0) is None


def test_symmetric_wing_picks_the_common_distance_nearest_the_target():
    strikes = {90.0, 95.0, 100.0, 105.0, 110.0, 112.5}
    assert ss._symmetric_wing(strikes, 100.0, 4.0) == 5.0
    assert ss._symmetric_wing(strikes, 100.0, 9.0) == 10.0
    # 12.5 exists above but 87.5 does not below -> never asymmetric
    assert ss._symmetric_wing(strikes, 100.0, 12.4) == 10.0
    assert ss._symmetric_wing({100.0}, 100.0, 5.0) is None


def test_half_expected_move():
    import math
    assert abs(ss._half_em(100.0, 0.28, 30) - 100 * 0.28 * math.sqrt(30 / 365) / 2) < 1e-9
    assert ss._half_em(100.0, 0.28, 0) == ss._half_em(100.0, 0.28, 1)
```

**Step 2: Run** — Expected: FAIL (helpers missing).

**Step 3: Implement** (after `_front_exp`)

```python
def _atm_strike(strikes, spot):
    """The listed strike nearest spot, or None for an empty ladder."""
    return min(strikes, key=lambda k: abs(k - spot)) if strikes else None


def _half_em(spot, atm_iv, dte):
    """Half the 1-sigma expected move to ``dte`` - the wing target (design doc)."""
    return spot * max(atm_iv or 0.0, 0.0) * math.sqrt(max(dte, 1) / 365.0) / 2.0


def _symmetric_wing(strikes, center, target):
    """A wing DISTANCE listed on BOTH sides of ``center``, nearest ``target``.

    Butterflies and condors are built symmetric on purpose: a broken wing is a
    different risk profile, and a ladder that happens to lack the mirror strike
    must not quietly produce one. None when no distance exists on both sides.
    """
    have = {round(k, 4) for k in strikes}
    c = round(center, 4)
    dists = [round(k - c, 4) for k in have if k > c and round(2 * c - k, 4) in have]
    return min(dists, key=lambda d: abs(d - target)) if dists else None
```

**Step 4: Run** `tests/test_strategy_scanner.py` — Expected: PASS.

**Step 5: Commit** — `git commit -m "feat(finder): ATM, half-EM and symmetric-wing strike helpers"`

---

### Task 4: Straddles and strangles

**Files:** Modify `options-scanner/strategy_scanner.py`; test in `options-scanner/tests/test_strategy_scanner.py`.

**Step 1: Failing tests** — a multi-strike chain helper first (also used by Tasks 5-7):

```python
def _ladder_chain(spot=100.0, days=(30,), step=5.0, n=6, iv=28.0):
    """A symmetric chain: strikes spot +/- n*step on every expiry in ``days``,
    Black-Scholes marks and deltas so strike selection behaves like a real chain."""
    import options_calculator as oc
    chain = {"underlyingPrice": spot, "callExpDateMap": {}, "putExpDateMap": {}}
    for d in days:
        key = f"{_exp(d)}:{d}"
        chain["callExpDateMap"][key], chain["putExpDateMap"][key] = {}, {}
        for i in range(-n, n + 1):
            K, T = spot + i * step, d / 365
            for kind, m in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
                mark = oc.bs_price(spot, K, T, oc.RISK_FREE_RATE, iv / 100, kind)
                delta = oc.bs_delta(spot, K, T, oc.RISK_FREE_RATE, iv / 100, kind)
                chain[m][key][f"{K:.1f}"] = [_contract(K, delta, round(max(mark, 0.01), 2),
                                                      volatility=iv)]
    return chain


def _by_type(sigs):
    return {s["type"]: s for s in sigs}


def test_straddles_sit_at_the_money_on_the_front_expiry():
    out = _by_type(ss.build_straddles_strangles(_ladder_chain(days=(30, 60)), "XYZ",
                                                100.0, 0.28, 5, 90))
    for t, side in (("LONG_STRADDLE", "long"), ("SHORT_STRADDLE", "short")):
        legs = out[t]["legs"]
        assert {(l["kind"], l["side"], l["strike"]) for l in legs} == {
            ("call", side, 100.0), ("put", side, 100.0)}
        assert out[t]["expiration"] == _exp(30) and out[t]["family"] == "NEUTRAL"


def test_short_strangle_aims_at_the_band_midpoint_and_long_mirrors_it():
    out = _by_type(ss.build_straddles_strangles(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    short = {l["kind"]: l for l in out["SHORT_STRANGLE"]["legs"]}
    long_ = {l["kind"]: l for l in out["LONG_STRANGLE"]["legs"]}
    assert short["call"]["strike"] > 100.0 and short["put"]["strike"] < 100.0
    assert abs(abs(short["call"]["delta"]) - 0.15) < 0.08
    assert {k: l["strike"] for k, l in short.items()} == {k: l["strike"] for k, l in long_.items()}


def test_the_band_ceiling_drops_a_short_strangle_but_never_a_straddle():
    rich = _ladder_chain(step=20.0, n=2)   # nothing between ATM (0.5) and far OTM
    out = _by_type(ss.build_straddles_strangles(
        rich, "XYZ", 100.0, 0.28, 5, 90, put_band=(-0.20, -0.30), call_band=(0.20, 0.30)))
    assert "SHORT_STRADDLE" in out
    for l in out.get("SHORT_STRANGLE", {"legs": []})["legs"]:
        assert abs(l["delta"]) <= 0.30
```

**Step 2: Run** — Expected: FAIL (`build_straddles_strangles` missing).

**Step 3: Implement** (after `build_debit_verticals`)

```python
def _short_target(band):
    return (band[0] + band[1]) / 2.0 if band else _SHORT_DELTA


def _front_pair(chain, dte_min, dte_max):
    """(exp, call_strikes, put_strikes) for the nearest expiry both maps list."""
    calls = extract_options(chain, "call", dte_min, dte_max)
    puts = extract_options(chain, "put", dte_min, dte_max)
    common = sorted(set(calls) & set(puts), key=lambda e: calls[e]["dte"])
    if not common:
        return None
    e = common[0]
    return e, calls[e]["strikes"], puts[e]["strikes"]


def build_straddles_strangles(chain, symbol, spot, atm_iv, dte_min, dte_max,
                              put_band=None, call_band=None):
    """Long/short straddle (ATM) and long/short strangle (band-midpoint shorts).

    The short-delta band's CEILING binds the short STRANGLE only - a straddle's
    shorts are ~0.50 delta by definition and applying it would delete the
    structure every time. The long strangle buys the same strikes the short
    strangle sells, so the two rows compare like for like.
    """
    fp = _front_pair(chain, dte_min, dte_max)
    if not fp:
        return []
    exp, cs, ps = fp
    out = []
    k = _atm_strike(set(cs) & set(ps), spot)
    if k is not None:
        for stype, side, label in (("LONG_STRADDLE", "long", "Long Straddle"),
                                   ("SHORT_STRADDLE", "short", "Short Straddle")):
            legs = [_leg_from(cs[k], "call", side, exp), _leg_from(ps[k], "put", side, exp)]
            out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))
    cb, pb = _band_abs(call_band), _band_abs(put_band)
    c = nearest_by_delta({s: v for s, v in cs.items() if s > spot}, _short_target(cb))
    p = nearest_by_delta({s: v for s, v in ps.items() if s < spot}, _short_target(pb))
    if c and p:
        for stype, side, label in (("LONG_STRANGLE", "long", "Long Strangle"),
                                   ("SHORT_STRANGLE", "short", "Short Strangle")):
            if side == "short" and ((cb and abs(c["delta"]) > cb[1])
                                    or (pb and abs(p["delta"]) > pb[1])):
                continue
            legs = [_leg_from(c, "call", side, exp), _leg_from(p, "put", side, exp)]
            out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))
    return out
```

**Step 4: Run** `tests/test_strategy_scanner.py` — Expected: PASS.

**Step 5: Commit** — `git commit -m "feat(finder): build straddles and strangles"`

---

### Task 5: Butterflies, iron butterfly, condors

**Step 1: Failing tests**

```python
def test_call_butterfly_is_symmetric_with_a_two_lot_body():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    legs = sorted(out["BUTTERFLY_CALL"]["legs"], key=lambda l: l["strike"])
    assert [(l["side"], l["qty"]) for l in legs] == [("long", 1), ("short", 2), ("long", 1)]
    assert legs[1]["strike"] == 100.0
    assert legs[1]["strike"] - legs[0]["strike"] == legs[2]["strike"] - legs[1]["strike"]
    assert out["BUTTERFLY_CALL"]["family"] == "NEUTRAL"
    assert out["BUTTERFLY_CALL"]["net_debit"] is not None


def test_iron_butterfly_shorts_both_sides_at_the_money():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    legs = {(l["kind"], l["side"]): l["strike"] for l in out["IRON_BUTTERFLY"]["legs"]}
    assert legs[("call", "short")] == legs[("put", "short")] == 100.0
    assert legs[("call", "long")] - 100.0 == 100.0 - legs[("put", "long")]
    assert out["IRON_BUTTERFLY"]["net_credit"] is not None


def test_condor_has_four_symmetric_strikes_long_outside():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    for t in ("CONDOR_CALL", "CONDOR_PUT"):
        legs = sorted(out[t]["legs"], key=lambda l: l["strike"])
        assert [l["side"] for l in legs] == ["long", "short", "short", "long"]
        ks = [l["strike"] for l in legs]
        assert ks[1] - ks[0] == ks[3] - ks[2] and ks[1] < 100.0 < ks[2]


def test_no_wing_on_a_ladder_with_one_strike():
    one = _ladder_chain(n=0)
    assert ss.build_butterflies_condors(one, "XYZ", 100.0, 0.28, 5, 90) == []
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement**

```python
def build_butterflies_condors(chain, symbol, spot, atm_iv, dte_min, dte_max):
    """Call/put butterfly, iron butterfly (ATM body) and call/put condor.

    Wings sit at the listed SYMMETRIC distance nearest half the 1-sigma expected
    move to the front expiry; a condor's shorts sit one wing either side of ATM and
    its longs two. All carry ``family="NEUTRAL"`` so ``q_breakeven_vs_em`` rewards a
    wide profit zone rather than a breakeven near spot.
    """
    fp = _front_pair(chain, dte_min, dte_max)
    if not fp:
        return []
    exp, cs, ps = fp
    both = set(cs) & set(ps)
    k = _atm_strike(both, spot)
    if k is None:
        return []
    dte = _dte_for(exp)
    d = _symmetric_wing(both, k, _half_em(spot, atm_iv, dte))
    if not d:
        return []
    lo, hi = k - d, k + d
    out = []

    def body(leg, qty):
        leg["qty"] = qty
        return leg

    for stype, kind, m, label in (("BUTTERFLY_CALL", "call", cs, "Call Butterfly"),
                                  ("BUTTERFLY_PUT", "put", ps, "Put Butterfly")):
        legs = [_leg_from(m[lo], kind, "long", exp), body(_leg_from(m[k], kind, "short", exp), 2),
                _leg_from(m[hi], kind, "long", exp)]
        out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))

    legs = [_leg_from(ps[lo], "put", "long", exp), _leg_from(ps[k], "put", "short", exp),
            _leg_from(cs[k], "call", "short", exp), _leg_from(cs[hi], "call", "long", exp)]
    out.append(_assemble("IRON_BUTTERFLY", "NEUTRAL", "Iron Butterfly", "neutral", legs,
                         symbol, spot, atm_iv))

    if (k - 2 * d) in both and (k + 2 * d) in both:
        for stype, kind, m, label in (("CONDOR_CALL", "call", cs, "Call Condor"),
                                      ("CONDOR_PUT", "put", ps, "Put Condor")):
            legs = [_leg_from(m[k - 2 * d], kind, "long", exp),
                    _leg_from(m[lo], kind, "short", exp),
                    _leg_from(m[hi], kind, "short", exp),
                    _leg_from(m[k + 2 * d], kind, "long", exp)]
            out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))
    return out
```

⚠ `_leg_from` returns a fresh dict each call, so setting `qty` on the body cannot
leak between the call and put butterflies.

**Step 4: Run** — Expected: PASS. **Step 5: Commit** — `git commit -m "feat(finder): build butterflies, iron butterfly and condors"`

---

### Task 6: Calendars and diagonals

**Step 1: Failing tests**

```python
def test_calendar_sells_the_front_and_buys_the_expiry_nearest_plus_28():
    chain = _ladder_chain(days=(7, 21, 35, 49))
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    legs = {l["side"]: l for l in out["CALENDAR_CALL"]["legs"]}
    assert legs["short"]["expiration"] == _exp(7) and legs["long"]["expiration"] == _exp(35)
    assert legs["short"]["strike"] == legs["long"]["strike"] == 100.0
    assert out["CALENDAR_CALL"]["expiration"] == _exp(7)
    assert out["CALENDAR_PUT"]["family"] == "NEUTRAL"


def test_diagonal_back_leg_is_one_strike_further_IN_the_money_and_a_debit():
    """The standard diagonal: sell the front at the money, buy the back month one
    strike deeper in the money. That is a DEBIT whose loss is about what you paid;
    the out-of-the-money version first planned came out as a credit."""
    out = _by_type(ss.build_calendars(_ladder_chain(days=(7, 35)), "XYZ", 100.0, 0.28, 5, 60))
    c = {l["side"]: l["strike"] for l in out["DIAGONAL_CALL"]["legs"]}
    p = {l["side"]: l["strike"] for l in out["DIAGONAL_PUT"]["legs"]}
    assert c == {"short": 100.0, "long": 95.0}
    assert p == {"short": 100.0, "long": 105.0}
    for t in ("DIAGONAL_CALL", "DIAGONAL_PUT"):
        assert out[t]["net_debit"] is not None


def test_a_back_leg_with_the_schwab_sentinel_iv_is_never_chosen():
    chain = _ladder_chain(days=(7, 35))
    back = [k for k in chain["callExpDateMap"] if k.endswith(":35")][0]
    chain["callExpDateMap"][back]["100.0"][0]["volatility"] = -999.0
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out        # no crash, and no 100C back leg


def test_no_calendar_without_two_expiries_seven_days_apart():
    assert ss.build_calendars(_ladder_chain(days=(7, 10)), "XYZ", 100.0, 0.28, 5, 60) == []
    assert ss.build_calendars(_ladder_chain(days=(30,)), "XYZ", 100.0, 0.28, 5, 60) == []
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement**

```python
_CAL_BACK_OFFSET, _CAL_MIN_GAP = 28, 7


def _usable_iv(iv):
    """A chain IV (percent) that _front_value can price: finite and positive.
    Schwab's -999 sentinel, NaN and 0 are not."""
    try:
        v = float(iv)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def build_calendars(chain, symbol, spot, atm_iv, dte_min, dte_max):
    """Call/put calendar (same ATM strike) and call/put diagonal (back leg one
    listed strike further IN the money, so it is a debit), inside the scan's own
    DTE window.

    Front = nearest expiry; back = the expiry whose DTE is nearest front + 28 with
    at least 7 days between them. No second chain fetch: a window without two such
    expiries builds nothing, and the user widens DTE max for longer calendars.
    """
    out = []
    for kind, label, direction in (("call", "Call", 1), ("put", "Put", -1)):
        # A back leg without a usable IV cannot be priced at the front expiry
        # (_front_value raises on it), so it is never a candidate.
        by_exp = {e: {**v, "strikes": {k: leg for k, leg in v["strikes"].items()
                                       if _usable_iv(leg.get("iv"))}}
                  for e, v in extract_options(chain, kind, dte_min, dte_max).items()}
        if len(by_exp) < 2:
            continue
        ordered = sorted(by_exp.items(), key=lambda kv: kv[1]["dte"])
        f_exp, f = ordered[0]
        backs = [(e, v) for e, v in ordered[1:] if v["dte"] - f["dte"] >= _CAL_MIN_GAP]
        if not backs:
            continue
        b_exp, b = min(backs, key=lambda kv: abs(kv[1]["dte"] - (f["dte"] + _CAL_BACK_OFFSET)))
        k = _atm_strike(set(f["strikes"]) & set(b["strikes"]), spot)
        if k is None:
            continue
        legs = [_leg_from(f["strikes"][k], kind, "short", f_exp),
                _leg_from(b["strikes"][k], kind, "long", b_exp)]
        out.append(_assemble(f"CALENDAR_{kind.upper()}", "NEUTRAL", f"{label} Calendar",
                             "neutral", legs, symbol, spot, atm_iv))
        # One strike deeper IN the money: below ATM for a call, above for a put.
        deeper = sorted(s for s in b["strikes"] if (k - s) * direction > 0)
        if deeper:
            kb = deeper[-1] if direction > 0 else deeper[0]
            legs = [_leg_from(f["strikes"][k], kind, "short", f_exp),
                    _leg_from(b["strikes"][kb], kind, "long", b_exp)]
            out.append(_assemble(f"DIAGONAL_{kind.upper()}", "DIRECTIONAL",
                                 f"{label} Diagonal",
                                 "bullish" if direction > 0 else "bearish",
                                 legs, symbol, spot, atm_iv))
    return out
```

**Step 4: Run** — Expected: PASS. **Step 5: Commit** — `git commit -m "feat(finder): build calendars and diagonals inside the DTE window"`

---

### Task 7: Covered call, protective put, collar

**Step 1: Failing tests**

```python
def test_share_structures_hold_one_lot_and_band_midpoint_options():
    out = _by_type(ss.build_stock_structures(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    for t in ("COVERED_CALL", "PROTECTIVE_PUT", "COLLAR"):
        stock = [l for l in out[t]["legs"] if l["kind"] == "stock"]
        assert len(stock) == 1 and stock[0]["qty"] == 1 and stock[0]["mark"] == 100.0
        assert out[t]["family"] == "DIRECTIONAL"
    cc = {l["kind"]: l for l in out["COVERED_CALL"]["legs"]}
    assert cc["call"]["side"] == "short" and cc["call"]["strike"] > 100.0
    pp = {l["kind"]: l for l in out["PROTECTIVE_PUT"]["legs"]}
    assert pp["put"]["side"] == "long" and pp["put"]["strike"] < 100.0
    col = {l["kind"]: l for l in out["COLLAR"]["legs"]}
    assert col["call"]["strike"] == cc["call"]["strike"]
    assert col["put"]["strike"] == pp["put"]["strike"]
    assert out["PROTECTIVE_PUT"]["unbounded_profit"] is True


def test_covered_call_respects_the_call_band_ceiling():
    rich = _ladder_chain(step=20.0, n=2)
    out = _by_type(ss.build_stock_structures(rich, "XYZ", 100.0, 0.28, 5, 90,
                                             call_band=(0.05, 0.10)))
    assert "COVERED_CALL" not in out and "COLLAR" not in out
    assert "PROTECTIVE_PUT" in out
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement**

```python
def _stock_leg(spot):
    """One 100-share lot at spot (the Calculator's D4 share-leg convention)."""
    return {"kind": "stock", "side": "long", "strike": None, "expiration": None,
            "qty": 1, "mark": float(spot), "delta": 1.0, "theta": 0.0, "vega": 0.0,
            "gamma": 0.0, "iv": 0.0}


def build_stock_structures(chain, symbol, spot, atm_iv, dte_min, dte_max,
                           put_band=None, call_band=None):
    """Covered call, protective put and collar, each on one 100-share lot bought at
    spot. The short call is at the call band's midpoint and obeys its CEILING; the
    long put is at the put band's midpoint and does not (a band governs where you
    SELL premium). ⚠ ``COVERED_CALL`` here is the WHOLE position, as in the
    Calculator - the paper account's ``COVERED_CALL`` is the option leg alone, so
    this row gets no Paper button.
    """
    fp = _front_pair(chain, dte_min, dte_max)
    if not fp:
        return []
    exp, cs, ps = fp
    cb, pb = _band_abs(call_band), _band_abs(put_band)
    c = nearest_by_delta({s: v for s, v in cs.items() if s > spot}, _short_target(cb))
    if c and cb and abs(c["delta"]) > cb[1]:
        c = None
    p = nearest_by_delta({s: v for s, v in ps.items() if s < spot}, _short_target(pb))
    out = []
    if c:
        legs = [_stock_leg(spot), _leg_from(c, "call", "short", exp)]
        out.append(_assemble("COVERED_CALL", "DIRECTIONAL", "Covered Call", "bullish",
                             legs, symbol, spot, atm_iv))
    if p:
        legs = [_stock_leg(spot), _leg_from(p, "put", "long", exp)]
        out.append(_assemble("PROTECTIVE_PUT", "DIRECTIONAL", "Protective Put", "bullish",
                             legs, symbol, spot, atm_iv))
    if c and p:
        legs = [_stock_leg(spot), _leg_from(c, "call", "short", exp),
                _leg_from(p, "put", "long", exp)]
        out.append(_assemble("COLLAR", "DIRECTIONAL", "Collar", "bullish",
                             legs, symbol, spot, atm_iv))
    return out
```

**Step 4: Run** the full options-scanner suite (Tasks 1-7 touch a shared module):

Run: `cd options-scanner && $PY -m pytest tests -p no:randomly`
Expected: the pre-existing failing SET unchanged (CLAUDE.md records none today).

**Step 5: Commit** — `git commit -m "feat(finder): build covered call, protective put and collar"`

---

### Task 8: Gate profiles for the new types + the re-runnable sweep

**Files:**
- Modify: `options-scanner/strategy_scoring.py` (`_TYPE_PROFILE` ~169)
- Test: `options-scanner/tests/test_strategy_scoring.py`
- Create: `tools/sweep_strategy_gates.py`, `tools/tests/test_sweep_strategy_gates.py`

**Step 1: Failing test** (next to `test_gate_profile_maps_types`; do NOT edit that test)

```python
NEW_STRUCTURE_PROFILES = {
    "LONG_STRADDLE": "LONG", "LONG_STRANGLE": "LONG", "PROTECTIVE_PUT": "LONG",
    "SHORT_STRADDLE": "NAKED", "SHORT_STRANGLE": "NAKED", "COVERED_CALL": "NAKED",
    "BUTTERFLY_CALL": "DEBIT", "BUTTERFLY_PUT": "DEBIT", "IRON_BUTTERFLY": "DEBIT",
    "CONDOR_CALL": "DEBIT", "CONDOR_PUT": "DEBIT",
    "CALENDAR_CALL": "DEBIT", "CALENDAR_PUT": "DEBIT",
    "DIAGONAL_CALL": "DEBIT", "DIAGONAL_PUT": "DEBIT", "COLLAR": "DEBIT",
}


def test_every_new_finder_structure_has_an_EXPLICIT_profile():
    """An unmapped type silently falls to DEBIT - which gives an unbounded long an
    unjudgeable reward and cuts it. Explicit entries make that choice visible.
    IRON_BUTTERFLY is DEBIT despite its credit: put-call parity makes it the long
    butterfly's payoff (see the 2026-09-13 design doc)."""
    for t, prof in NEW_STRUCTURE_PROFILES.items():
        assert sc._TYPE_PROFILE.get(t) == prof, t
```

**Step 2: Run** — `cd options-scanner && $PY -m pytest tests/test_strategy_scoring.py -k EXPLICIT` → FAIL.

**Step 3: Implement** — extend `_TYPE_PROFILE`:

```python
_TYPE_PROFILE = {
    "LONG_CALL": "LONG", "LONG_PUT": "LONG",
    "SHORT_CALL": "NAKED", "SHORT_PUT": "NAKED",
    "BULL_CALL": "DEBIT", "BEAR_PUT": "DEBIT",
    "PCS": "CREDIT", "CCS": "CREDIT",
    "IRON_CONDOR": "NEUTRAL", "IC": "NEUTRAL",
    # Strategy Finder, every structure (2026-09-13). Measured by
    # tools/sweep_strategy_gates.py - quote its figures WITH their parameters.
    "LONG_STRADDLE": "LONG", "LONG_STRANGLE": "LONG", "PROTECTIVE_PUT": "LONG",
    # SHORT_STRADDLE and COVERED_CALL clear NO profile's PoP bar (~57 and ~54 vs
    # NAKED's 65) and are counted in "below the quality bar" rather than shown.
    # Operator decision: no bar is invented without outcome data.
    "SHORT_STRADDLE": "NAKED", "SHORT_STRANGLE": "NAKED", "COVERED_CALL": "NAKED",
    # IRON_BUTTERFLY is the long butterfly's payoff by put-call parity; judging it
    # under NEUTRAL's 55 PoP bar would cut both every time.
    "BUTTERFLY_CALL": "DEBIT", "BUTTERFLY_PUT": "DEBIT", "IRON_BUTTERFLY": "DEBIT",
    "CONDOR_CALL": "DEBIT", "CONDOR_PUT": "DEBIT",
    "CALENDAR_CALL": "DEBIT", "CALENDAR_PUT": "DEBIT",
    "DIAGONAL_CALL": "DEBIT", "DIAGONAL_PUT": "DEBIT", "COLLAR": "DEBIT",
}
```

**Step 4: Create `tools/sweep_strategy_gates.py`** — model the header on
`tools/sweep_naked_capeff.py` (same `_REPO` / `OPTIONS_SCANNER` bootstrap). Body:

```python
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from repo_paths import OPTIONS_SCANNER  # noqa: E402

sys.path.insert(0, str(OPTIONS_SCANNER))
import options_calculator as oc   # noqa: E402
import strategy_scanner as ss     # noqa: E402
import strategy_scoring as sc     # noqa: E402

BUILDERS = ("build_straddles_strangles", "build_butterflies_condors",
            "build_calendars", "build_stock_structures")


def chain(spot, iv, days, step):
    import datetime as dt
    out = {"underlyingPrice": spot, "callExpDateMap": {}, "putExpDateMap": {}}
    for d in days:
        key = f"{(dt.date.today() + dt.timedelta(days=d)).isoformat()}:{d}"
        for m in ("callExpDateMap", "putExpDateMap"):
            out[m][key] = {}
        n = int(spot * 0.4 / step)
        for i in range(-n, n + 1):
            K, T = round(spot + i * step, 2), d / 365
            for kind, m in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
                mark = max(oc.bs_price(spot, K, T, oc.RISK_FREE_RATE, iv, kind), 0.01)
                out[m][key][f"{K:.1f}"] = [{
                    "delta": oc.bs_delta(spot, K, T, oc.RISK_FREE_RATE, iv, kind),
                    "mark": round(mark, 2), "bid": round(mark * 0.98, 2),
                    "ask": round(mark * 1.02, 2), "theta": -0.02, "vega": 0.1,
                    "gamma": 0.01, "volatility": iv * 100, "totalVolume": 500,
                    "openInterest": 2000}]
    return out


def rows(spot, iv, front_days, step):
    em = spot * iv * math.sqrt(front_days / 365)
    c = chain(spot, iv, (front_days, front_days + 28), step)
    view = {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"}
    for name in BUILDERS:
        kw = {}
        if name in ("build_straddles_strangles", "build_stock_structures"):
            kw = {"put_band": (-0.20, -0.10), "call_band": (0.10, 0.20)}
        for s in getattr(ss, name)(c, "XYZ", spot, iv, 0, front_days + 30, **kw):
            scored = sc.score_strategy(dict(s), view, iv, em, market_state=None)
            yield {"type": s["type"], "dte": s["dte"], "pop": s["pop_pct"], "rr": s["rr"],
                   "profile": sc.gate_profile(s), "score": scored.get("composite_score"),
                   "grade": scored.get("grade")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spot", type=float, default=100.0)
    ap.add_argument("--iv", type=float, default=0.28)
    ap.add_argument("--days", default="14,30,45")
    ap.add_argument("--step", type=float, default=2.5)
    a = ap.parse_args(argv)
    print(f"spot={a.spot} iv={a.iv} step={a.step}")
    for d in (int(x) for x in a.days.split(",")):
        for r in rows(a.spot, a.iv, d, a.step):
            print(f"{r['type']:16s} dte={r['dte']:3d} pop={r['pop']!s:6s} rr={r['rr']!s:7s} "
                  f"profile={r['profile']:7s} score={r['score']!s:6s} grade={r['grade']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Docstring must say: PURE (in-memory Black-Scholes chain → the REAL builders and
scorers; no Schwab, no DB, no network), what each profile row means, and that the
figures move with `--step` and wing width.

⚠ Check `score_strategy`'s return shape before relying on `composite_score` /
`grade` keys — read `options-scanner/strategy_scoring.py:769`. Adjust the two
`.get`s to whatever it actually returns; do not guess.

**Step 5: Tool test** (`tools/tests/test_sweep_strategy_gates.py`):

```python
import importlib.util
from pathlib import Path


def _tool():
    p = Path(__file__).resolve().parents[1] / "sweep_strategy_gates.py"
    spec = importlib.util.spec_from_file_location("sweep_strategy_gates", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sweep_reaches_every_new_structure_and_reproduces_the_two_cuts():
    rows = list(_tool().rows(100.0, 0.28, 30, 2.5))
    types = {r["type"] for r in rows}
    assert {"LONG_STRADDLE", "SHORT_STRADDLE", "BUTTERFLY_CALL", "IRON_BUTTERFLY",
            "CONDOR_PUT", "CALENDAR_CALL", "DIAGONAL_PUT", "COVERED_CALL",
            "PROTECTIVE_PUT", "COLLAR"} <= types
    by = {r["type"]: r for r in rows}
    assert by["SHORT_STRADDLE"]["grade"] == "Weak"
    assert by["COVERED_CALL"]["grade"] == "Weak"
```

If either `Weak` assertion fails, the design's central measurement did not
reproduce through the real builders — **stop and report the actual grades**; do
not edit the assertion.

Run: `$PY -m pytest tools/tests/test_sweep_strategy_gates.py` and
`cd options-scanner && $PY -m pytest tests/test_strategy_scoring.py -p no:randomly` → PASS.

**Step 6: Commit** — `git commit -m "feat(finder): gate profiles for every new structure, with the sweep behind them"`

---

### Task 9: `swing_scan` builds the four new groups; earnings reads the LATEST expiry

**Files:**
- Modify: `services/options_svc/compute.py` (`_SWING_FAMILIES` ~240, `swing_scan` ~379-434)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Failing tests** (append; reuse `_swing_chain`'s monkeypatch block by
extracting it — add this helper and use it only in the new tests):

```python
def _patch_swing_inputs(monkeypatch, chain):
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: chain)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": 540.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda client, symbol: {"h": 1})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda hist: {"trend": "NEUTRAL", "rsi14": 50,
                                      "price": 540.0, "sma20": 540.0})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None:
                        {"iv_rank": 50.0, "expected_moves": {"daily": {"move_dollars": 5.0}}})
    monkeypatch.setattr(compute.se, "screen_spreads", lambda *a, **k: [])
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [])


def _two_expiry_chain():
    import datetime as dt
    a = _swing_chain((dt.date.today() + dt.timedelta(days=10)).isoformat(), 10)
    b = _swing_chain((dt.date.today() + dt.timedelta(days=38)).isoformat(), 38)
    for m in ("callExpDateMap", "putExpDateMap"):
        a[m].update(b[m])
    return a


def test_default_swing_scan_builds_all_seven_groups(monkeypatch, unfiltered_swing):
    _patch_swing_inputs(monkeypatch, _two_expiry_chain())
    types = {s["type"] for s in compute.swing_scan(
        "SPY", 5, 60, -0.20, -0.10, 0.10, 0.20, 0.10)["signals"]}
    assert {"LONG_STRADDLE", "BUTTERFLY_CALL", "CALENDAR_CALL", "COVERED_CALL"} <= types


def test_an_unchecked_group_is_not_built(monkeypatch, unfiltered_swing):
    _patch_swing_inputs(monkeypatch, _two_expiry_chain())
    types = {s["type"] for s in compute.swing_scan(
        "SPY", 5, 60, -0.20, -0.10, 0.10, 0.20, 0.10,
        families=["DIRECTIONAL"])["signals"]}
    assert not types & {"LONG_STRADDLE", "BUTTERFLY_CALL", "CALENDAR_CALL", "COVERED_CALL"}


def test_a_calendar_is_earnings_gated_on_its_BACK_month(monkeypatch, unfiltered_swing):
    import datetime as dt
    _patch_swing_inputs(monkeypatch, _two_expiry_chain())
    report = dt.date.today() + dt.timedelta(days=20)   # after the front, before the back
    out = compute.swing_scan("SPY", 5, 60, -0.20, -0.10, 0.10, 0.20, 0.10,
                             families=["CALENDAR"], earnings_date=report.isoformat())
    assert not [s for s in out["signals"] if s["type"].startswith("CALENDAR")]
```

⚠ Before writing the third test, read `scanner_engine.check_earnings_conflict`
and `earnings_gate_applies` to confirm the date type it takes (str vs `date`) and
that `SWING` at DTE 10 is gated. Match the real signature.

**Step 2: Run** — `$PY -m pytest services/options_svc/tests/test_compute.py -k "seven_groups or unchecked_group or BACK_month"` → FAIL.

**Step 3: Implement**

```python
# Candidate build groups. ``families=None`` builds all of these. The first three
# are ALSO the ``family`` value their candidates carry; the four added 2026-09-13
# are build groups only - their candidates carry NEUTRAL or DIRECTIONAL, the one
# vocabulary strategy_scoring reads (see the design doc).
_SWING_FAMILIES = ("DIRECTIONAL", "VERTICAL", "NEUTRAL",
                   "STRADDLE", "BUTTERFLY", "CALENDAR", "STOCK")
```

After the `if "NEUTRAL" in fams:` block, add:

```python
    bands = {"put_band": (put_d_min, put_d_max), "call_band": (call_d_min, call_d_max)}
    if "STRADDLE" in fams:
        signals += ssn.build_straddles_strangles(chain, symbol, spot, atm_iv,
                                                 dte_min, dte_max, **bands)
    if "BUTTERFLY" in fams:
        signals += ssn.build_butterflies_condors(chain, symbol, spot, atm_iv, dte_min, dte_max)
    if "CALENDAR" in fams:
        signals += ssn.build_calendars(chain, symbol, spot, atm_iv, dte_min, dte_max)
    if "STOCK" in fams:
        signals += ssn.build_stock_structures(chain, symbol, spot, atm_iv,
                                              dte_min, dte_max, **bands)
```

Add a module-level helper beside `assign_ids`:

```python
def _latest_expiration(sig):
    """The LAST expiration a candidate is exposed to. A calendar's back month can
    span a report its front leg expires ahead of, so the earnings gate must read
    this, not ``sig["expiration"]`` (the front). Single-expiry rows are unchanged."""
    exps = [l.get("expiration") for l in sig.get("legs") or [] if l.get("expiration")]
    return max(exps) if exps else sig.get("expiration")
```

In the earnings filter replace `s.get("expiration")` with `_latest_expiration(s)`.

⚠ `income_scan` passes `families=("VERTICAL", "DIRECTIONAL")` explicitly, so the
Income board builds none of the new groups — confirm by running
`services/options_svc/tests/test_income_scan.py` unchanged.

**Step 4: Run the service suite**

Run: `$PY -m pytest services/options_svc -rf` (from the repo root — one service at a time)
Expected: no new failures; `test_income_scan.py` and `test_handlers.py` untouched.

**Step 5: Commit** — `git commit -m "feat(finder): scan straddle, butterfly, calendar and stock groups"`

---

### Task 10: Page checkboxes and the legs cell

**Files:**
- Modify: `webgui/pages/options/swing.py` (`_FAMILY_OPTIONS` ~72)
- Modify: `webgui/pages/options/strategy_table.py` (`legs_summary` ~56)
- Test: `webgui/tests/test_strategy_table.py`, plus the swing page test if one pins `_FAMILY_OPTIONS` (grep `_FAMILY_OPTIONS` in `webgui/tests`).

**Step 1: Failing tests**

```python
def test_legs_summary_prints_a_share_lot():
    legs = [{"kind": "stock", "side": "long", "strike": None, "qty": 1},
            {"kind": "call", "side": "short", "strike": 105.0, "expiration": "2026-10-16"}]
    assert st.legs_summary(legs) == "L 100 SH / S 105C"


def test_legs_summary_dates_only_a_leg_on_a_LATER_expiry():
    legs = [{"kind": "call", "side": "short", "strike": 100.0, "expiration": "2026-10-16"},
            {"kind": "call", "side": "long", "strike": 100.0, "expiration": "2026-11-13"}]
    assert st.legs_summary(legs) == "S 100C / L 100C 11/13"


def test_legs_summary_single_expiry_is_unchanged():
    legs = [{"kind": "put", "side": "short", "strike": 445.0, "expiration": "2026-10-16"},
            {"kind": "put", "side": "long", "strike": 440.0, "expiration": "2026-10-16"}]
    assert st.legs_summary(legs) == "S 445P / L 440P"


def test_finder_offers_the_calculators_seven_groups():
    from pages.options import swing
    assert list(swing._FAMILY_OPTIONS) == [
        "DIRECTIONAL", "VERTICAL", "NEUTRAL", "STRADDLE", "BUTTERFLY", "CALENDAR", "STOCK"]
    assert swing._FAMILY_OPTIONS["STRADDLE"] == "Straddles & strangles"
    assert swing._FAMILY_OPTIONS["STOCK"] == "Stock + options"
```

(Use whatever alias the test file already imports `strategy_table` as.)

**Step 2: Run** — `cd webgui && $PY -m pytest tests/test_strategy_table.py` → FAIL.

**Step 3: Implement**

```python
# Build groups, labelled with the Calculator's STRATEGY_GROUPS names so a structure
# is called the same thing on both pages.
_FAMILY_OPTIONS = {"DIRECTIONAL": "Directional", "VERTICAL": "Spreads",
                   "NEUTRAL": "Neutral", "STRADDLE": "Straddles & strangles",
                   "BUTTERFLY": "Butterflies & condors", "CALENDAR": "Calendars",
                   "STOCK": "Stock + options"}
```

Delete the "Diagonal is a later phase — omitted" comment. ⚠ Seven checkboxes no
longer fit `no-wrap` on one row: change that row's classes from
`"items-center gap-3 no-wrap"` to `"items-center gap-3 flex-wrap"`.

```python
def legs_summary(legs):
    """Compact one-line summary of the legs, e.g. ``"L 450C / S 455C"``.

    ``L`` = long, ``S`` = short; strike + ``C``/``P``. A share lot prints
    ``L 100 SH``. A leg on a LATER expiration than the earliest carries its
    ``MM/DD`` so a calendar reads without the detail panel. Empty/None -> '—'.
    """
    if not legs:
        return "—"
    exps = [l.get("expiration") for l in legs if l.get("expiration")]
    front = min(exps) if exps else None
    parts = []
    for leg in legs:
        side = "L" if (leg.get("side") == "long") else "S"
        if leg.get("kind") == "stock":
            parts.append(f"{side} {100 * int(leg.get('qty') or 1)} SH")
            continue
        kind = "C" if (leg.get("kind") == "call") else "P"
        text = f"{side} {_fmt_strike(leg.get('strike'))}{kind}"
        exp = leg.get("expiration")
        if exp and front and exp != front:
            text += f" {_short_exp(exp)}"
        parts.append(text)
    return " / ".join(parts) if parts else "—"
```

⚠ `legs_summary` is also used by the Market Scanner's Directional tab, whose legs
are all single-expiry options — the third test above is its guard.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_strategy_table.py tests/test_no_inline_style.py` → PASS.

**Step 5: Commit** — `git commit -m "feat(finder): seven strategy groups and a legs cell that reads calendars and shares"`

---

### Task 11: Send to Paper for the six debit structures

**Files:**
- Modify: `shared/structures.py` — add `LEDGER_DEBIT`
- Modify: `options-scanner/paper_trader.py:43` — import it
- Modify: `webgui/pages/options/strategy_table.py:24` — extend `_PAPER_TYPES`
- Modify: `config/trade_mgmt.toml` — six `[structures.*]` tables
- Test: `shared/tests/test_structures.py`, `shared/tests/test_cross_tier_mirrors.py`,
  `options-scanner/tests/` (a new `test_paper_debit_multileg.py`), `webgui/tests/test_strategy_table.py`

**Step 1: The PRECONDITION test first** — the ledger must record, reprice and
settle a two-lot body. Create `options-scanner/tests/test_paper_debit_multileg.py`:

```python
import datetime as dt

import paper_trader
import signal_repricer
import strategy_scanner as ss


def _exp(d):
    return (dt.date.today() + dt.timedelta(days=d)).isoformat()


def _leg(kind, side, strike, mark, qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": _exp(30),
            "qty": qty, "mark": mark, "delta": 0.5, "theta": 0, "vega": 0, "gamma": 0,
            "iv": 28.0}


def _fly_signal():
    legs = [_leg("call", "long", 95.0, 7.0), _leg("call", "short", 100.0, 4.0, qty=2),
            _leg("call", "long", 105.0, 2.0)]
    return ss._assemble("BUTTERFLY_CALL", "NEUTRAL", "Call Butterfly", "neutral",
                        legs, "XYZ", 100.0, 0.28)


def test_a_butterfly_settles_at_its_body_with_the_two_lot_counted():
    trade = paper_trader._create_debit_trade(_fly_signal(), 1, "SWING",
                                             dt.datetime.now(paper_trader.TZ))
    assert [l["qty"] for l in trade["legs"]] == [1, 2, 1]
    per_share, pnl = signal_repricer.legs_intrinsic_value(trade, 100.0)
    assert per_share == 5.0                         # 5 - 0 + 0, not 5 - 0 (qty 1)
    assert pnl == round(500.0 - trade["entry_debit"], 2)
    per_share, _ = signal_repricer.legs_intrinsic_value(trade, 110.0)
    assert per_share == 0.0                         # 15 - 2*10 + 5


def test_a_butterfly_reprices_with_the_two_lot_counted():
    trade = paper_trader._create_debit_trade(_fly_signal(), 1, "SWING",
                                             dt.datetime.now(paper_trader.TZ))
    key = f"{_exp(30)}:30"

    def q(bid, ask):
        return [{"bid": bid, "ask": ask, "delta": 0.5}]

    chain = {"underlyingPrice": 100.0,
             "callExpDateMap": {key: {"95.0": q(6.9, 7.1), "100.0": q(3.9, 4.1),
                                      "105.0": q(1.9, 2.1)}},
             "putExpDateMap": {}}

    class _Client:
        pass

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(signal_repricer, "_fetch_chain", lambda client, sym, exp: chain)
    try:
        rep = signal_repricer.reprice_legs(trade, _Client())
    finally:
        mp.undo()
    assert rep["current_value"] == 1.0              # 7 - 2*4 + 2
```

Run: `cd options-scanner && $PY -m pytest tests/test_paper_debit_multileg.py -p no:randomly`

- **PASS** → continue with Step 2.
- **FAIL** → the ledger does not handle a two-lot body. Per the design, **drop
  `BUTTERFLY_CALL` / `BUTTERFLY_PUT` from every list below**, keep the four others,
  mark the two tests `xfail(strict=True, reason=...)` naming the defect, and record
  it in the CHANGELOG entry. Do not change the ledger in this plan.

⚠ Confirm `_create_debit_trade`'s real signature (`signal, quantity, mode, now`)
and `reprice_legs`'s `_fetch_chain` call before running; adjust the calls, not the
assertions.

**Step 2: Failing taxonomy + mirror tests**

In `shared/tests/test_structures.py`:

```python
def test_ledger_debit_is_the_four_originals_plus_the_six_finder_structures():
    from shared import structures as s
    assert set(s.LEDGER_DEBIT) == {
        "LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT",
        "LONG_STRADDLE", "LONG_STRANGLE", "BUTTERFLY_CALL", "BUTTERFLY_PUT",
        "CONDOR_CALL", "CONDOR_PUT"}
```

In `shared/tests/test_cross_tier_mirrors.py` (Tier 1 cannot import `shared.structures`):

```python
def test_the_pages_paper_button_covers_exactly_the_ledgers_debit_structures():
    """The Paper button (webgui) and the ledger's debit path (options-scanner) are
    two lists in tiers that cannot share one. A type with the button but not the
    debit path falls into the CREDIT branch of create_paper_trade and KeyErrors on
    short_strike; the converse is a structure the ledger supports that nobody can
    send."""
    taxonomy = set(_const("shared/structures.py", "LEDGER_DEBIT"))
    page = set(_const("webgui/pages/options/strategy_table.py", "_PAPER_TYPES"))
    credit = {"PCS", "CCS", "IC", "IRON_CONDOR"}
    assert page == taxonomy | credit
```

And a rule-table pin in `options-scanner/tests/` (or wherever
`test_debit_exit_rules` lives — grep `exit_dte`):

```python
def test_every_ledger_debit_structure_has_a_time_exit():
    from shared import structures as s, trade_mgmt as tm
    for name in s.LEDGER_DEBIT:
        assert tm.structure_rules(name).get("exit_dte") == 21, name
```

Run all three → FAIL.

**Step 3: Implement**

`shared/structures.py`, after `_DEFAULT_LEGS`:

```python
# Debit structures the Paper LEDGER records, reprices and settles generically by
# their legs (paper_trader._create_debit_trade / signal_repricer.reprice_legs /
# legs_intrinsic_value). Anything NOT here that is not a credit spread falls into
# create_paper_trade's credit branch and KeyErrors on ``short_strike`` - which is
# why the page's Paper button is pinned to this list by test_cross_tier_mirrors.
LEDGER_DEBIT = ("LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT",
                "LONG_STRADDLE", "LONG_STRANGLE",
                "BUTTERFLY_CALL", "BUTTERFLY_PUT", "CONDOR_CALL", "CONDOR_PUT")
```

`options-scanner/paper_trader.py` — replace line 43 with the repo-root bootstrap
`paper_adjust.py` uses, then:

```python
import pathlib as _pathlib
import sys as _sys

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import structures as _structures  # noqa: E402

# See shared.structures.LEDGER_DEBIT. Kept as a set under its old name: three
# call sites test membership against it.
PAPER_DEBIT_TYPES = set(_structures.LEDGER_DEBIT)
```

(grep `PAPER_DEBIT_TYPES` first to confirm nothing mutates it.)

`webgui/pages/options/strategy_table.py`:

```python
# Structures the Paper-trade button is allowed for: the credit spreads (``IC`` is
# the engine's iron-condor key, ``IRON_CONDOR`` the normalized one) PLUS
# shared.structures.LEDGER_DEBIT, which Tier 1 cannot import - the two are pinned
# equal by shared/tests/test_cross_tier_mirrors.py. Naked shorts, short
# straddles/strangles, the iron butterfly, calendars and every share structure are
# excluded: the ledger's credit path only understands two-strike spreads and iron
# condors, it settles at intrinsic (wrong for a back month), and it holds no shares.
_PAPER_TYPES = {"PCS", "CCS", "IC", "IRON_CONDOR",
                "LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT",
                "LONG_STRADDLE", "LONG_STRANGLE", "BUTTERFLY_CALL", "BUTTERFLY_PUT",
                "CONDOR_CALL", "CONDOR_PUT"}
```

`config/trade_mgmt.toml`, after `[structures.BEAR_PUT]`:

```toml
# Strategy Finder debit structures (2026-09-13) — the same time exit as the four
# above. The profit target needs no table: _debit_target_base already takes a
# fraction of MAX PROFIT for a bounded butterfly/condor and of the DEBIT for an
# unbounded straddle/strangle.
[structures.LONG_STRADDLE]
exit_dte = 21

[structures.LONG_STRANGLE]
exit_dte = 21

[structures.BUTTERFLY_CALL]
exit_dte = 21

[structures.BUTTERFLY_PUT]
exit_dte = 21

[structures.CONDOR_CALL]
exit_dte = 21

[structures.CONDOR_PUT]
exit_dte = 21
```

Extend the page test:

```python
def test_paper_button_on_long_straddle_but_not_calendar_or_covered_call():
    rows = {r["id"]: r for r in st.strategy_rows([
        {"id": "a", "type": "LONG_STRADDLE"}, {"id": "b", "type": "CALENDAR_CALL"},
        {"id": "c", "type": "COVERED_CALL"}, {"id": "d", "type": "IRON_BUTTERFLY"}])}
    assert rows["a"]["_allow_paper"] is True
    assert not any(rows[k]["_allow_paper"] for k in "bcd")
```

**Step 4: Run**

```
$PY -m pytest shared/tests
cd options-scanner && $PY -m pytest tests -p no:randomly
cd webgui && $PY -m pytest tests/test_strategy_table.py
$PY -m pytest services/options_svc -rf
```

Expected: PASS; failing sets unchanged.

**Step 5: Commit** — `git commit -m "feat(finder): paper-trade the six debit structures the ledger settles"`

---

### Task 12: Send to Calculator carries a calendar and a covered call intact

No production change is expected — this pins it.

**Files:** Test `webgui/tests/test_handoff.py` (grep for the existing handoff test file name first).

```python
def test_calendar_reaches_the_calculator_with_both_expiries():
    from pages.options import handoff
    sig = {"symbol": "SPY", "legs": [
        {"kind": "call", "side": "short", "strike": 500.0, "expiration": "2026-10-16",
         "qty": 1, "mark": 3.0},
        {"kind": "call", "side": "long", "strike": 500.0, "expiration": "2026-11-13",
         "qty": 1, "mark": 6.0}]}
    legs = handoff._signal_legs_payload(sig)["legs"]
    assert [l["expiry"] for l in legs] == ["2026-10-16", "2026-11-13"]


def test_covered_call_reaches_the_calculator_as_a_stock_leg():
    from pages.options import handoff, strategies
    sig = {"symbol": "SPY", "legs": [
        {"kind": "stock", "side": "long", "strike": None, "expiration": None,
         "qty": 1, "mark": 500.0},
        {"kind": "call", "side": "short", "strike": 510.0, "expiration": "2026-10-16",
         "qty": 1, "mark": 3.0}]}
    stock = handoff._signal_legs_payload(sig)["legs"][0]
    assert stock["option_type"] == strategies.STOCK
    assert stock["strike"] is None and stock["expiry"] is None


def test_expected_move_drops_the_share_leg():
    from pages.options import handoff
    sig = {"symbol": "SPY", "expiration": "2026-10-16", "legs": [
        {"kind": "stock", "side": "long", "strike": None},
        {"kind": "call", "side": "short", "strike": 510.0}]}
    assert [l["strike"] for l in handoff.signal_to_em_payload(sig)["legs"]] == [510.0]
```

Run `cd webgui && $PY -m pytest tests/<that file>`. If the stock test FAILS
(`"stock"` vs `strategies.STOCK` spelling), fix `_signal_legs_payload` to map
`kind == "stock"` to `strategies.STOCK` — that is the one allowed production edit
here. Commit: `git commit -m "test(finder): calendar and covered call hand off to the Calculator intact"`

---

### Task 13: Documentation

**Files:**
- `webgui/page_help.py` — the `/options/swing` guide: name the four new groups;
  say short straddles and covered calls are built but almost never clear the bar
  (they are counted in "below the quality bar"); say the Paper button appears for
  long straddles/strangles, butterflies and condors only; say calendars come from
  inside the DTE range, so widen DTE max for longer ones.
- `docs/manuals/user-guide/user-guide.md` and
  `docs/manuals/reference-guide/reference-guide.md` — the `## Strategy Finder`
  sections: the same four facts, plus the legs-cell notation (`L 100 SH`, a dated
  back leg).
- `docs/manuals/technical-reference/technical-reference.md` — the Finder scoring
  section: the profile table from the design doc WITH its parameters, the
  front-expiry valuation, the per-contract commission, and
  `tools/sweep_strategy_gates.py` as the way to re-measure.
- `docs/webgui-routes.md` — the `/options/swing` entry's structure list.
- `docs/CHANGELOG.md` — a new top entry (move the current "Last updated" to "Prior —").
- `CLAUDE.md` — ONE durable addition, under "A STOCK leg is 100-share LOTS":
  `COVERED_CALL` now names the whole position in the **Finder** as well as the
  Calculator, so the paper account's option-leg-only `COVERED_CALL` is the odd one
  out, and the Finder row must never gain a Paper button. Also update the
  `/options/swing` row of the route table.

Then rebuild the manuals and keep only the ones whose Markdown changed:

```bash
cd docs/manuals && $PY build_docs.py
git status --short docs/manuals   # revert *.html/*.docx for manuals whose .md did not change
```

Run: `cd webgui && $PY -m pytest tests/test_page_help.py tests/test_docs_cover_the_ui.py`
Commit: `git commit -m "docs(finder): every structure - guide, manuals, changelog"`

---

### Task 14: Full verification

```
cd options-scanner && $PY -m pytest tests -p no:randomly
cd webgui && $PY -m pytest .
$PY -m pytest services/options_svc
$PY -m pytest shared/tests
$PY -m pytest tools/tests
```

Compare each failing set against the pre-change baseline. Then use
superpowers:requesting-code-review on the branch diff before anything leaves the
worktree.

**Live check (after promote, operator's call on timing — see memory
"promote-timing-on-a-trading-day"):** the Finder has no dev environment, so
verification is Redis-driven on prod, which is read-only for this command:

```bash
ssh vps2-ts 'cd /home/administrator/dev && .venv/bin/python -c "
from shared.bus import Bus; b = Bus()
b.enqueue_command(\"cmd:options\", {\"type\": \"swing_scan\", \"args\": {\"symbol\": \"SPY\", \"dte_min\": 0, \"dte_max\": 60}})"'
```

then read `cache:options:swing` and report, per new type: built or not, grade,
score, and `filtered_out`. ⚠ Off-hours chains are stale (see memory
"chain-underlying-price-stale-off-hours") — run it during RTH for a real read.
