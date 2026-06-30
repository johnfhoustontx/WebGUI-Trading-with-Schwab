# Multi-Strategy Swing Scanner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand the single-symbol Swing Scanner from credit-spreads-only to a unified multi-strategy scanner (directional, verticals, neutral, diagonal) that infers a market view and ranks every candidate on one 0–100 Fit+Quality score.

**Architecture:** New pure engine module `options-scanner/strategy_scanner.py` builds normalized candidate signals (canonical `legs` list + payoff economics) for each family; new pure `options-scanner/strategy_scoring.py` infers bias from technicals/IV and scores each candidate (Thesis-Fit + Structural-Quality). `services/options_svc/compute.swing_scan` orchestrates fetch → infer → build → score → rank and returns `{signals, view}` under the existing `cache:options:swing` key. `webgui/pages/options/swing.py` gains a family multiselect, an inferred-view banner, strategy-agnostic columns, and legs-based Calculator/Expected-Move handoff.

**Tech Stack:** Python 3.11, pytest (TDD), NiceGUI/Quasar, Redis bus (`shared.bus`), Black-Scholes/intrinsic payoff math.

**Design doc:** [`docs/plans/2026-06-30-multi-strategy-swing-scanner-design.md`](2026-06-30-multi-strategy-swing-scanner-design.md)

**Conventions (read before starting):**
- Use @superpowers:test-driven-development for every task: write the failing test, watch it fail, minimal implementation, watch it pass, commit.
- Pure functions are TDD'd against synthetic dicts; `render()` stays thin (verified in the browser).
- Tests run **per folder** (entrypoints add the repo root to `sys.path`): `cd options-scanner; python -m pytest tests` and from the repo root `.venv\Scripts\python -m pytest services\options_svc` and `cd webgui; ..\.venv\Scripts\python -m pytest .`. Never `pytest services` over all services (module-name collisions).
- Branch: `Using_Highcharts` (the long-lived dev branch — do not create a new one).
- Conventional commits (`feat:`/`test:`/`refactor:`). End commit messages with the `Co-Authored-By: Claude Opus 4.8` trailer.

---

## Normalized signal shape (the contract every builder emits)

```python
{
  "id": str,                 # f"{symbol}_{type}_{exp}_{k1}_{k2}..." (unique)
  "symbol": str,
  "type": str,               # LONG_CALL|LONG_PUT|SHORT_CALL|SHORT_PUT|BULL_CALL|
                             # BEAR_PUT|PCS|CCS|IRON_CONDOR|CONDOR|IRON_FLY|
                             # CALL_FLY|PUT_FLY|CALL_DIAG|PUT_DIAG
  "family": str,             # DIRECTIONAL|VERTICAL|NEUTRAL|DIAGONAL
  "strategy_label": str,     # human label e.g. "Long Call", "Bull Call Spread"
  "bias": str,               # bullish|bearish|neutral (the structure's stance)
  "legs": [                  # canonical multi-leg list
     {"kind": "call"|"put", "side": "long"|"short", "strike": float,
      "expiration": "YYYY-MM-DD", "qty": int, "mark": float,
      "delta": float, "theta": float, "vega": float, "gamma": float, "iv": float},
     ...
  ],
  "expiration": str,         # front (nearest) expiration
  "dte": int,                # front dte
  "net_debit": float,        # >0 if you pay (None if net credit)
  "net_credit": float,       # >0 if you receive (None if net debit)
  "max_profit": float,       # None/inf flag for unbounded
  "max_loss": float,         # positive magnitude; None/inf flag for unbounded
  "capital": float,          # capital at risk used for efficiency factor
  "breakevens": [float, ...],
  "pop_pct": float,
  "rr": float | None,        # max_profit/max_loss where both bounded
  "net_delta": float, "net_theta": float, "net_vega": float, "net_gamma": float,
  "underlying_price": float,
  "timestamp": str,
  "unbounded": bool,         # True for naked short call / long (capped grid)
  # added by the scorer:
  "composite_score": float, "grade": str,
  "fit_score": float, "quality_score": float,
  "factor_scores": {"fit_dir": .., "fit_vol": .., "q_liq": .., "q_rr": ..,
                    "q_be": .., "q_pop": ..},
}
```

---

# PHASE 1 — Foundation + Directional + Verticals

## Task 1: Chain extraction helper

**Files:**
- Create: `options-scanner/strategy_scanner.py`
- Test: `options-scanner/tests/test_strategy_scanner.py`

**Step 1: Write the failing test.** Build a tiny synthetic chain (the Schwab shape: `callExpDateMap`/`putExpDateMap` → `{"2026-07-10:10": {"445.0": [contract], ...}}`) and assert extraction.

```python
import strategy_scanner as ss

def _contract(strike, delta, mark, **kw):
    base = {"delta": delta, "mark": mark, "bid": mark - 0.05, "ask": mark + 0.05,
            "theta": -0.02, "vega": 0.10, "gamma": 0.01, "volatility": 18.0,
            "totalVolume": 500, "openInterest": 1000}
    base.update(kw); return base

def _chain():
    return {
      "underlyingPrice": 450.0,
      "callExpDateMap": {"2026-07-10:10": {
          "450.0": [_contract(450.0, 0.50, 6.0)],
          "455.0": [_contract(455.0, 0.32, 3.5)],
          "460.0": [_contract(460.0, 0.18, 1.8)]}},
      "putExpDateMap": {"2026-07-10:10": {
          "450.0": [_contract(450.0, -0.50, 6.0)],
          "445.0": [_contract(445.0, -0.32, 3.5)],
          "440.0": [_contract(440.0, -0.18, 1.8)]}},
    }

def test_extract_options_groups_by_expiration():
    out = ss.extract_options(_chain(), "call", dte_min=5, dte_max=30)
    assert "2026-07-10" in out
    exp = out["2026-07-10"]
    assert exp["dte"] == 10
    assert set(exp["strikes"]) == {450.0, 455.0, 460.0}
    assert exp["strikes"][455.0]["delta"] == 0.32
    assert exp["strikes"][455.0]["mark"] == 3.5

def test_extract_options_filters_dte_window():
    chain = _chain()
    chain["callExpDateMap"]["2026-12-18:171"] = chain["callExpDateMap"]["2026-07-10:10"]
    out = ss.extract_options(chain, "call", dte_min=5, dte_max=30)
    assert list(out) == ["2026-07-10"]
```

**Step 2:** Run `cd options-scanner; python -m pytest tests/test_strategy_scanner.py -v` → FAIL (no module).

**Step 3: Implement.** Port the per-strike normalization from `scanner_engine.screen_spreads` (lines ~728-749): mark fallback to bid/ask mid then close.

```python
"""Multi-strategy candidate builders for the Swing Scanner (pure).

Given a Schwab option chain + spot, build NORMALIZED candidate signals across
families (directional, verticals, neutral, diagonal). Each candidate carries a
canonical ``legs`` list + payoff economics (max P/L, breakevens, PoP, capital).
Credit verticals (PCS/CCS) are produced by ``scanner_engine.screen_spreads`` and
adapted here; this module owns the new families.
"""
import datetime as _dt
import math

_GRID_LO, _GRID_HI, _GRID_N = 0.5, 1.5, 401   # ±50% of spot payoff grid


def _norm_mark(c):
    m = c.get("mark") or 0
    bid, ask = c.get("bid") or 0, c.get("ask") or 0
    if m <= 0 and bid > 0 and ask > 0:
        m = round((bid + ask) / 2, 4)
    if m <= 0:
        m = c.get("close") or c.get("theoreticalOptionValue") or 0
    return m


def extract_options(chain, kind, dte_min, dte_max):
    """{exp_str: {dte, strikes: {strike: leg_data}}} for one option kind."""
    key = "callExpDateMap" if kind == "call" else "putExpDateMap"
    out = {}
    for exp_key, strikes in (chain.get(key) or {}).items():
        exp_str, dte = exp_key.split(":")[0], int(float(exp_key.split(":")[1]))
        if not (dte_min <= dte <= dte_max):
            continue
        sd = {}
        for sk, contracts in strikes.items():
            if not contracts:
                continue
            c = contracts[0]
            if c.get("delta") is None:
                continue
            sd[float(sk)] = {
                "strike": float(sk), "delta": c.get("delta"),
                "mark": _norm_mark(c), "bid": c.get("bid") or 0, "ask": c.get("ask") or 0,
                "theta": c.get("theta") or 0, "vega": c.get("vega") or 0,
                "gamma": c.get("gamma") or 0, "iv": c.get("volatility") or 0,
                "volume": c.get("totalVolume") or 0, "oi": c.get("openInterest") or 0,
            }
        if sd:
            out[exp_str] = {"dte": dte, "strikes": sd}
    return out
```

**Step 4:** Run the tests → PASS.

**Step 5: Commit.** `git add options-scanner/strategy_scanner.py options-scanner/tests/test_strategy_scanner.py && git commit`.

---

## Task 2: Strike selection by delta target

**Files:** Modify `options-scanner/strategy_scanner.py`; Test same file.

**Step 1: Failing test.**

```python
def test_nearest_by_delta_picks_closest_abs_delta():
    strikes = ss.extract_options(_chain(), "call", 5, 30)["2026-07-10"]["strikes"]
    leg = ss.nearest_by_delta(strikes, 0.30)
    assert leg["strike"] == 455.0          # 0.32 is closest to 0.30

def test_nearest_by_delta_empty_returns_none():
    assert ss.nearest_by_delta({}, 0.30) is None
```

**Step 2:** Run → FAIL.

**Step 3: Implement.**

```python
def nearest_by_delta(strikes, target_abs_delta):
    """Leg whose |delta| is closest to target_abs_delta (None if empty)."""
    if not strikes:
        return None
    return min(strikes.values(), key=lambda v: abs(abs(v["delta"]) - target_abs_delta))
```

**Step 4:** PASS. **Step 5:** Commit.

---

## Task 3: Payoff economics (the heart)

**Files:** Modify `options-scanner/strategy_scanner.py`; Test same file.

This computes max P/L, breakevens, capital, R:R, and net greeks for ANY `legs` list. Sign convention: long leg pays `mark` (cost +), short receives (cost −). `entry_cost = Σ sign·mark·qty` (debit if >0). Terminal value `V(S) = Σ sign·intrinsic(leg,S)·qty`. P/L(S) = `V(S) − entry_cost`.

**Step 1: Failing tests** (cover a long call, a debit vertical, a defined-risk credit spread).

```python
def _leg(kind, side, strike, mark, qty=1, **kw):
    g = {"delta": 0.5, "theta": -0.02, "vega": 0.1, "gamma": 0.01, "iv": 18.0}
    g.update(kw)
    return {"kind": kind, "side": side, "strike": strike, "expiration": "2026-07-10",
            "qty": qty, "mark": mark, **g}

def test_payoff_long_call_unbounded_profit_capped_loss():
    legs = [_leg("call", "long", 450.0, 6.0)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert m["net_debit"] == 6.0 and m["net_credit"] is None
    assert m["max_loss"] == 6.0                 # premium paid
    assert m["unbounded"] is True               # profit uncapped (grid cap flags it)
    assert abs(m["breakevens"][0] - 456.0) < 0.5   # strike + premium

def test_payoff_bull_call_debit_spread_bounded():
    legs = [_leg("call", "long", 450.0, 6.0), _leg("call", "short", 455.0, 3.5)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert abs(m["net_debit"] - 2.5) < 1e-6
    assert abs(m["max_loss"] - 2.5) < 0.05      # debit paid
    assert abs(m["max_profit"] - 2.5) < 0.1     # width(5) - debit(2.5)
    assert m["unbounded"] is False
    assert abs(m["breakevens"][0] - 452.5) < 0.2

def test_payoff_put_credit_spread_max_loss_width_minus_credit():
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert abs(m["net_credit"] - 1.7) < 1e-6
    assert abs(m["max_profit"] - 1.7) < 0.05
    assert abs(m["max_loss"] - 3.3) < 0.1       # width 5 - credit 1.7
```

**Step 2:** Run → FAIL.

**Step 3: Implement.**

```python
def _intrinsic(leg, S):
    if leg["kind"] == "call":
        return max(0.0, S - leg["strike"])
    return max(0.0, leg["strike"] - S)

def _sign(leg):
    return 1.0 if leg["side"] == "long" else -1.0

def payoff_metrics(legs, spot):
    entry_cost = sum(_sign(l) * l["mark"] * l.get("qty", 1) for l in legs)   # +debit
    grid = [spot * (_GRID_LO + (_GRID_HI - _GRID_LO) * i / (_GRID_N - 1))
            for i in range(_GRID_N)]
    pls = []
    for S in grid:
        v = sum(_sign(l) * _intrinsic(l, S) * l.get("qty", 1) for l in legs)
        pls.append(v - entry_cost)
    max_p, min_p = max(pls), min(pls)
    # Unbounded if the payoff is still rising/falling at a grid edge (e.g. long call).
    edge = 0.5
    unbounded = (pls[-1] >= max_p - edge and pls[-1] > pls[-2] + 1e-6) or \
                (pls[0] <= min_p + edge and pls[0] < pls[1] - 1e-6)
    breakevens = []
    for i in range(1, len(grid)):
        if (pls[i - 1] <= 0 < pls[i]) or (pls[i - 1] >= 0 > pls[i]):
            # linear interp of the zero crossing
            t = pls[i - 1] / (pls[i - 1] - pls[i])
            breakevens.append(round(grid[i - 1] + t * (grid[i] - grid[i - 1]), 2))
    max_profit = None if (unbounded and pls[-1] >= max_p - edge) else round(max_p, 2)
    max_loss = abs(round(min_p, 2))
    net = round(entry_cost, 4)
    out = {
        "net_debit": net if net > 0 else None,
        "net_credit": round(-net, 4) if net < 0 else None,
        "max_profit": max_profit, "max_loss": max_loss,
        "breakevens": breakevens, "unbounded": unbounded,
        "capital": max_loss if not unbounded else round(abs(net) if net > 0 else spot * 0.20, 2),
        "rr": (round(max_profit / max_loss, 3) if (max_profit and max_loss) else None),
        "net_delta": round(sum(_sign(l) * l["delta"] * l.get("qty", 1) for l in legs), 4),
        "net_theta": round(sum(_sign(l) * l["theta"] * l.get("qty", 1) for l in legs), 4),
        "net_vega":  round(sum(_sign(l) * l["vega"]  * l.get("qty", 1) for l in legs), 4),
        "net_gamma": round(sum(_sign(l) * l["gamma"] * l.get("qty", 1) for l in legs), 4),
    }
    return out
```

**Step 4:** Run → PASS (tune `edge`/grid if a boundary assert is off). **Step 5:** Commit.

> Note: naked short call → `min_p` at the high grid edge is large-negative and `unbounded` True; `capital` falls back to the `spot*0.20` margin proxy. Naked short put → bounded (max loss = strike − credit). Both ranked normally (per the design's "no special treatment").

---

## Task 4: PoP from a normal terminal approximation

**Files:** Modify `strategy_scanner.py`; Test same file.

PoP = P(P/L(S_T) > 0) using a normal terminal price `S_T ~ N(spot, σ_T)` where `σ_T = spot · atm_iv · √(dte/365)`. Integrate the profit region from the breakevens + payoff sign.

**Step 1: Failing test.**

```python
def test_pop_long_call_is_low_side_probability():
    legs = [_leg("call", "long", 450.0, 6.0)]
    pop = ss.pop_from_payoff(legs, spot=450.0, atm_iv=0.18, dte=10)
    assert 5 < pop < 45      # must rally ~6 pts in 10d → modest odds

def test_pop_put_credit_spread_is_high():
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    pop = ss.pop_from_payoff(legs, spot=450.0, atm_iv=0.18, dte=10)
    assert pop > 55          # OTM short put, price above → wins most of the time
```

**Step 2:** FAIL. **Step 3: Implement** (normal CDF via `math.erf`; sample the payoff sign across a fine grid weighted by the normal pdf, or integrate profit mass between breakevens). Minimal robust version: numerically integrate the normal density over grid points where P/L>0.

```python
def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def pop_from_payoff(legs, spot, atm_iv, dte):
    sigma = spot * max(atm_iv, 1e-6) * math.sqrt(max(dte, 0.5) / 365.0)
    if sigma <= 0:
        return None
    entry_cost = sum(_sign(l) * l["mark"] * l.get("qty", 1) for l in legs)
    n = 801
    lo, hi = spot - 6 * sigma, spot + 6 * sigma
    prob = 0.0
    prev_S = lo
    prev_cdf = _norm_cdf((lo - spot) / sigma)
    for i in range(1, n):
        S = lo + (hi - lo) * i / (n - 1)
        cdf = _norm_cdf((S - spot) / sigma)
        mid = (S + prev_S) / 2
        v = sum(_sign(l) * _intrinsic(l, mid) * l.get("qty", 1) for l in legs)
        if v - entry_cost > 0:
            prob += (cdf - prev_cdf)
        prev_S, prev_cdf = S, cdf
    return round(prob * 100, 1)
```

**Step 4:** PASS. **Step 5:** Commit.

---

## Task 5: Build directional candidates (long + naked short)

**Files:** Modify `strategy_scanner.py`; Test same file.

**Step 1: Failing tests.**

```python
def test_build_directional_emits_long_and_naked_each_side():
    chain = _chain()
    sigs = ss.build_directional(chain, "SPY", spot=450.0, atm_iv=0.18,
                                dte_min=5, dte_max=30)
    types = {s["type"] for s in sigs}
    assert {"LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT"} <= types
    lc = next(s for s in sigs if s["type"] == "LONG_CALL")
    assert lc["family"] == "DIRECTIONAL" and lc["bias"] == "bullish"
    assert len(lc["legs"]) == 1 and lc["legs"][0]["side"] == "long"
    assert lc["max_loss"] > 0 and lc["pop_pct"] is not None
    assert lc["id"].startswith("SPY_LONG_CALL_")
```

**Step 2:** FAIL. **Step 3: Implement** a small spec table + a generic assembler `_make_signal(type, family, label, bias, legs, symbol, spot, atm_iv)` that calls `payoff_metrics` + `pop_from_payoff`, computes `id`, front exp/dte, timestamp, and merges everything into the normalized dict.

```python
_LONG_DELTA, _SHORT_DELTA = 0.55, 0.28   # target |delta| for long / naked-short legs

_DIRECTIONAL = [
    ("LONG_CALL",  "call", "long",  "bullish", "Long Call",  _LONG_DELTA),
    ("LONG_PUT",   "put",  "long",  "bearish", "Long Put",   _LONG_DELTA),
    ("SHORT_CALL", "call", "short", "bearish", "Short Call", _SHORT_DELTA),
    ("SHORT_PUT",  "put",  "short", "bullish", "Short Put",  _SHORT_DELTA),
]

def _front_exp(opts_by_exp):
    return min(opts_by_exp.items(), key=lambda kv: kv[1]["dte"]) if opts_by_exp else None

def _leg_from(leg_data, kind, side, exp):
    return {"kind": kind, "side": side, "strike": leg_data["strike"], "expiration": exp,
            "qty": 1, "mark": leg_data["mark"], "delta": leg_data["delta"],
            "theta": leg_data["theta"], "vega": leg_data["vega"],
            "gamma": leg_data["gamma"], "iv": leg_data["iv"]}

def _assemble(stype, family, label, bias, legs, symbol, spot, atm_iv):
    m = payoff_metrics(legs, spot)
    front = min(legs, key=lambda l: l["expiration"])
    dte = _dte_for(front["expiration"])
    pop = pop_from_payoff(legs, spot, atm_iv, dte)
    sk = "_".join(str(l["strike"]) for l in legs)
    return {"id": f"{symbol}_{stype}_{front['expiration']}_{sk}",
            "symbol": symbol, "type": stype, "family": family,
            "strategy_label": label, "bias": bias, "legs": legs,
            "expiration": front["expiration"], "dte": dte,
            "pop_pct": pop, "underlying_price": spot,
            "timestamp": _dt.datetime.now().isoformat(), **m}

def build_directional(chain, symbol, spot, atm_iv, dte_min, dte_max):
    out = []
    by_kind = {k: extract_options(chain, k, dte_min, dte_max) for k in ("call", "put")}
    for stype, kind, side, bias, label, target in _DIRECTIONAL:
        fe = _front_exp(by_kind[kind])
        if not fe:
            continue
        exp, data = fe
        leg_data = nearest_by_delta(data["strikes"], target)
        if not leg_data:
            continue
        legs = [_leg_from(leg_data, kind, side, exp)]
        out.append(_assemble(stype, "DIRECTIONAL", label, bias, legs, symbol, spot, atm_iv))
    return out
```

Add `_dte_for(exp_str)` = `(date.fromisoformat(exp) - date.today()).days` (clamped ≥0). **Step 4:** PASS. **Step 5:** Commit.

---

## Task 6: Build debit verticals (bull call / bear put)

**Files:** Modify `strategy_scanner.py`; Test same file.

**Step 1: Failing test.**

```python
def test_build_debit_verticals_bull_call_and_bear_put():
    sigs = ss.build_debit_verticals(_chain(), "SPY", 450.0, 0.18, 5, 30)
    bc = next(s for s in sigs if s["type"] == "BULL_CALL")
    assert bc["family"] == "VERTICAL" and bc["bias"] == "bullish"
    assert len(bc["legs"]) == 2
    assert bc["net_debit"] and bc["max_profit"] and not bc["unbounded"]
    assert any(s["type"] == "BEAR_PUT" for s in sigs)
```

**Step 2:** FAIL. **Step 3: Implement** — buy ~0.60Δ, sell ~0.30Δ (call side for bull, put side for bear); skip if the same strike or missing.

```python
_DEBIT_BUY, _DEBIT_SELL = 0.60, 0.30

def build_debit_verticals(chain, symbol, spot, atm_iv, dte_min, dte_max):
    out = []
    for stype, kind, bias, label in [("BULL_CALL", "call", "bullish", "Bull Call Spread"),
                                      ("BEAR_PUT", "put", "bearish", "Bear Put Spread")]:
        fe = _front_exp(extract_options(chain, kind, dte_min, dte_max))
        if not fe:
            continue
        exp, data = fe
        buy = nearest_by_delta(data["strikes"], _DEBIT_BUY)
        sell = nearest_by_delta(data["strikes"], _DEBIT_SELL)
        if not buy or not sell or buy["strike"] == sell["strike"]:
            continue
        legs = [_leg_from(buy, kind, "long", exp), _leg_from(sell, kind, "short", exp)]
        out.append(_assemble(stype, "VERTICAL", label, bias, legs, symbol, spot, atm_iv))
    return out
```

**Step 4:** PASS. **Step 5:** Commit.

---

## Task 7: Adapt existing credit-spread signals into the normalized shape

**Files:** Modify `strategy_scanner.py`; Test same file.

The `screen_spreads` PCS/CCS dicts already exist (compute calls them). Add `adapt_credit_spread(sig)` → fills `family="VERTICAL"`, `strategy_label`, `bias` (PCS→bullish, CCS→bearish), and a `legs` list reconstructed from `short_strike`/`long_strike`/marks, plus `net_credit`/`max_profit`/`max_loss` from the existing `credit`/`max_loss`. Also `adapt_iron_condor(sig)` (family NEUTRAL, bias neutral, 4 legs).

**Step 1: Failing test.**

```python
def test_adapt_credit_spread_pcs_to_normalized():
    pcs = {"id": "SPY_PCS_2026-07-10_445.0_440.0", "symbol": "SPY", "type": "PCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 445.0,
           "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
           "credit": 1.7, "max_loss": 3.3, "pop_pct": 68.0, "underlying_price": 450.0,
           "short_delta": -0.32, "net_theta": 0.04, "net_vega": -0.02}
    n = ss.adapt_credit_spread(pcs)
    assert n["family"] == "VERTICAL" and n["bias"] == "bullish"
    assert n["net_credit"] == 1.7 and n["max_loss"] == 3.3
    assert [l["side"] for l in n["legs"]] == ["short", "long"]
    assert n["legs"][0]["kind"] == "put"
```

**Step 2:** FAIL. **Step 3: Implement** the two adapters (preserve every existing field; add the new keys; build `legs` with the marks/greeks present). **Step 4:** PASS. **Step 5:** Commit.

---

## Task 8: `infer_market_view`

**Files:**
- Create: `options-scanner/strategy_scoring.py`
- Test: `options-scanner/tests/test_strategy_scoring.py`

**Inputs** are what `scanner_engine.calc_technicals(hist)` and `run_iv_analysis` already return. Inspect their real keys first (`calc_technicals` ~ `scanner_engine.py:108`; IV analysis via `compute.run_iv_analysis`) and write the test to those exact keys.

**Step 1: Failing tests.**

```python
import strategy_scoring as sc

def test_infer_view_bullish_trend_low_iv():
    tech = {"trend": "bullish", "ema_alignment": "bullish", "rsi": 62, "adx": 28}
    iv = {"iv_rank": 20, "iv_hv_ratio": 0.9}
    v = sc.infer_market_view(tech, iv)
    assert v["direction"] == "bullish" and v["conviction"] > 0.4
    assert v["vol_regime"] == "low"

def test_infer_view_neutral_when_no_trend_and_high_iv():
    v = sc.infer_market_view({"trend": "neutral", "rsi": 50, "adx": 12},
                             {"iv_rank": 75, "iv_hv_ratio": 1.4})
    assert v["direction"] == "neutral" and v["conviction"] < 0.4
    assert v["vol_regime"] == "high"
```

**Step 2:** FAIL. **Step 3: Implement** — map trend/EMA + RSI distance-from-50 + ADX strength → direction + conviction (0..1); IV rank bands (<35 low / >65 high / else mid). Defensive `.get` with neutral defaults. **Step 4:** PASS. **Step 5:** Commit.

---

## Task 9: Fit + Quality normalizers

**Files:** Modify `strategy_scoring.py`; Test same file.

**Step 1: Failing tests.**

```python
def test_fit_directional_bullish_structure_in_bull_view_high():
    v = {"direction": "bullish", "conviction": 0.8}
    hi = sc.fit_directional(net_delta=+40, view=v)   # bullish structure
    lo = sc.fit_directional(net_delta=-40, view=v)   # bearish structure
    assert hi > 70 and lo < 30

def test_fit_directional_neutral_structure_scores_high_only_low_conviction():
    flat = sc.fit_directional(net_delta=0.0, view={"direction": "neutral", "conviction": 0.1})
    assert flat > 60

def test_fit_vol_long_vega_fits_low_iv():
    assert sc.fit_vol(net_vega=+0.5, vol_regime="low") > 70
    assert sc.fit_vol(net_vega=+0.5, vol_regime="high") < 40
    assert sc.fit_vol(net_vega=-0.5, vol_regime="high") > 70
```

**Step 2:** FAIL. **Step 3: Implement** — `fit_directional`: align the sign of `net_delta` (scaled to ~[-1,1] via a soft clamp) with `direction × conviction`; neutral view rewards small |net_delta|. `fit_vol`: align sign of `net_vega` with regime (low→reward +vega, high→reward −vega, mid→neutral 50). Add quality normalizers: `q_rr`, `q_capital_eff`, `q_breakeven_vs_em(breakevens, spot, em_1sd, family)`, `q_pop(pop)`, and reuse `scoring.norm_liquidity` for `q_liq` (import lazily). **Step 4:** PASS. **Step 5:** Commit.

---

## Task 10: `score_strategy` — combine into 0–100 + grade

**Files:** Modify `strategy_scoring.py`; Test same file.

**Step 1: Failing tests.**

```python
def test_score_strategy_adds_composite_and_grade():
    sig = {"type": "LONG_CALL", "family": "DIRECTIONAL", "bias": "bullish",
           "net_delta": 45, "net_vega": 0.4, "pop_pct": 35, "rr": None,
           "max_profit": None, "max_loss": 6.0, "capital": 6.0, "breakevens": [456.0],
           "underlying_price": 450.0, "legs": [{"bid": 5.9, "ask": 6.1, "mark": 6.0}]}
    view = {"direction": "bullish", "conviction": 0.8, "vol_regime": "low"}
    out = sc.score_strategy(sig, view, atm_iv=0.18, em_1sd=8.0)
    assert 0 <= out["composite_score"] <= 100
    assert out["grade"] in ("Strong", "Good", "Marginal", "Weak")
    assert "fit_dir" in out["factor_scores"] and "q_liq" in out["factor_scores"]

def test_score_strategy_same_structure_better_in_aligned_view():
    sig = {... bullish long call ...}
    bull = sc.score_strategy(sig, {"direction":"bullish","conviction":0.8,"vol_regime":"low"}, 0.18, 8.0)
    bear = sc.score_strategy(sig, {"direction":"bearish","conviction":0.8,"vol_regime":"low"}, 0.18, 8.0)
    assert bull["composite_score"] > bear["composite_score"]
```

**Step 2:** FAIL. **Step 3: Implement** — `fit_score = 0.6·fit_dir + 0.4·fit_vol`; `quality_score = weighted(q_liq, q_rr/q_capital_eff, q_be, q_pop)`; `composite = round(0.5·fit + 0.5·quality, 1)`; grade thresholds (Strong ≥80 / Good ≥60 / Marginal ≥40 / Weak). Mutates+returns the signal. Add `score_all(signals, view, atm_iv, em_1sd)` that scores each (defensive per-signal try/except → skip) and sorts by composite desc. **Step 4:** PASS. **Step 5:** Commit.

---

## Task 11: Wire `compute.swing_scan` to the new pipeline

**Files:**
- Modify: `services/options_svc/compute.py:57-101`
- Test: `services/options_svc/tests/test_compute.py` (extend)

**Step 1: Failing test** (mirror the existing monkeypatch pattern at `test_compute.py:76`). Monkeypatch `compute.se.fetch_option_chain`/`fetch_price_history`/`calc_technicals`, `compute._proxy`, and assert the result has `signals` from MULTIPLE families + a `view`.

```python
def test_swing_scan_builds_all_families_and_view(monkeypatch):
    monkeypatch.setattr(compute.se, "fetch_option_chain", lambda *a, **k: _CHAIN)
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **k: _HIST)
    monkeypatch.setattr(compute.se, "calc_technicals", lambda h: {"trend": "bullish", "rsi": 60, "adx": 25})
    monkeypatch.setattr(compute, "run_iv_analysis", lambda *a, **k: {"iv_rank": 25, "atm_iv": 0.18,
                                                                      "expected_moves": {"daily": {"move_dollars": 4.0}}})
    monkeypatch.setattr(compute._proxy, "schwab_client", _FakeQuote(450.0))
    res = compute.swing_scan(symbol="SPY", dte_min=5, dte_max=30, families=None,
                             put_d_min=-0.2, put_d_max=-0.1, call_d_min=0.1,
                             call_d_max=0.2, min_cr_fraction=0.10)
    fams = {s["family"] for s in res["signals"]}
    assert "DIRECTIONAL" in fams and "VERTICAL" in fams
    assert res["view"]["direction"] == "bullish"
    assert all("composite_score" in s for s in res["signals"])
```

**Step 2:** FAIL. **Step 3: Implement** — change `swing_scan` to return a dict `{signals, view}`; add a `families` param (None = all Phase-1 families). After fetching chain/spot/tech/iv, lazily `import strategy_scanner as ssn, strategy_scoring as ssc`, compute `atm_iv`/`em_1sd` from `iv`, `view = ssc.infer_market_view(tech, iv)`, build directional + debit verticals + (existing `screen_spreads`→`adapt_credit_spread`) + (existing `build_iron_condors`→`adapt_iron_condor`) per `families`, then `ssc.score_all(signals, view, atm_iv, em_1sd)`, `assign_ids`, return `{signals, view}`. Keep the existing imports/fetch lines. **Step 4:** PASS (+ keep the old `test_swing_scan` test green by updating it to the new return shape). **Step 5:** Commit.

---

## Task 12: Handler passes `families` + caches `view`

**Files:**
- Modify: `services/options_svc/handlers.py:134-204`
- Test: `services/options_svc/tests/test_handlers.py`

**Step 1: Failing test** — enqueue `swing_scan` with `args={"families": ["DIRECTIONAL"], ...}`, monkeypatch `compute.swing_scan` to return `{"signals": [...], "view": {...}}`, assert the cached payload contains `signals`, `view`, `symbol`, `params`.

**Step 2:** FAIL. **Step 3: Implement** — add `"families": None` to `_SWING_DEFAULTS`; in `swing_scan`, `result = compute.swing_scan(**params)`; `payload = {"signals": result["signals"], "view": result.get("view"), "symbol": params["symbol"], "params": args}`. **Step 4:** PASS. **Step 5:** Commit.

---

## Task 13: Page — strategy-agnostic columns + rows

**Files:**
- Create: `webgui/pages/options/strategy_table.py` (pure column/row builders, keep `swing.py` thin)
- Test: `webgui/tests/test_strategy_table.py`

**Step 1: Failing tests** for `strategy_columns()` and `strategy_rows(signals)` — columns include Strategy/Bias/Legs/Debit-Credit/MaxP/MaxL/R:R/PoP/BE/Score/Grade; rows render a compact legs summary (`legs_summary(legs)` → e.g. "L 450C / S 455C"), `debit_credit_text` (−2.50 debit / +1.70 credit), sorted by score desc, robust to missing keys.

**Step 2:** FAIL. **Step 3: Implement** the pure builders (model on `scanner.signal_columns`/`signal_rows`; add `_score_class` via `scanner.score_zone_class`). **Step 4:** PASS. **Step 5:** Commit.

---

## Task 14: Page — view banner builder

**Files:** Modify `webgui/pages/options/strategy_table.py`; Test same file.

**Step 1: Failing test** for `view_banner_text(view)` → e.g. `"Inferred view: Bullish · conviction 0.6 · IV Rank low → favors long / debit"`; `None`/empty view → a neutral waiting string. **Step 2:** FAIL. **Step 3: Implement** (pure string builder + a small `_favors(view)` map). **Step 4:** PASS. **Step 5:** Commit.

---

## Task 15: Page — wire `swing.py` to the new model

**Files:**
- Modify: `webgui/pages/options/swing.py`
- Modify: `webgui/pages/options/handoff.py` (legs-based handoff for new types)

**Step 1:** Add a `ui.select(..., multiple=True)` **Strategy families** control (options DIRECTIONAL/VERTICAL/NEUTRAL/DIAGONAL; default all available), a **view banner** `ui.label`, move the Δ/credit gates into `ui.expansion("Advanced — credit spreads")`, swap the table to `strategy_table.strategy_columns()/strategy_rows()`, and read `payload["view"]` in `_populate` to update the banner. Pass `families` in `_request_scan`'s params.

**Step 2:** Handoff — add a helper `handoff.send_signal_to_calculator(sig)` that, when the signal has a canonical `legs` list, builds `{symbol, legs:[{option_type:kind, side, strike, expiry:expiration, qty, premium:mark}]}` and routes through `send_to_calculator_legs`; falls back to the old `send_to_calculator(sig)` for legacy spread dicts. Add `signal_to_em_payload` support for the new multi-leg types by reading `legs` directly when present (kind→option_type, side, strike). Wire `add_row_actions` to use the legs-aware calculator handoff. **Keep Paper-trade button only for PCS/CCS/IC** (guard in the actions slot / handler — Paper deferred for new types).

**Step 3:** Browser-verify (see Task 16). **Step 4:** Commit.

---

## Task 16: Live verification (Phase 1)

- Restart `options_svc` (the running one is stale): `.venv\Scripts\python services\options_svc\app.py`.
- Start the webgui preview (`:8500`), open `/options/swing`, Scan SPY.
- Confirm: the view banner shows an inferred bias; the table lists LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT/BULL_CALL/BEAR_PUT/PCS/CCS rows with scores; bullish structures rank higher in a bullish read; Send-to-Calculator opens the Calculator with the correct legs; Send-to-Expected-Move opens the EM tab; Paper button shown only on credit spreads.
- The most reliable non-browser check (per CLAUDE.md): `Bus().enqueue_command("cmd:options", {"type":"swing_scan","args":{"symbol":"SPY"}})` then `Bus().cache_get("cache:options:swing")` and inspect families/scores.
- Run all three suites green: `options-scanner`, `services\options_svc`, `webgui`.
- Commit any fixes; then update `CLAUDE.md` (route table + a "Multi-strategy Swing Scanner" section) and commit.

---

# PHASE 2 — Neutral (condor / butterfly / iron fly)

Same TDD rhythm; all builders added to `strategy_scanner.py` + scored by the existing `score_strategy` (neutral structures already handled by `fit_directional`'s neutral branch + `q_breakeven_vs_em` profit-zone logic).

- **Task 2.1 — `build_iron_condor_direct`** (own builder, not just adapting `build_iron_condors`): short ~0.20Δ put + call, long wings one or two strikes out, around the EM band. Type `IRON_CONDOR`, neutral.
- **Task 2.2 — `build_condor`** (all-call OR all-put 4-leg). Type `CONDOR`.
- **Task 2.3 — `build_iron_fly`** — ATM short straddle + protective wings. Type `IRON_FLY`.
- **Task 2.4 — `build_butterfly`** — long 1-2-1 call fly + put fly (body ATM/at EM center, `qty=2` on the body leg). Types `CALL_FLY`/`PUT_FLY`. Verify `payoff_metrics` handles the `qty=2` body correctly (add a butterfly payoff test: max loss = net debit, max profit = wing width − debit).
- **Task 2.5** — register the new families in `compute.swing_scan`'s family dispatch + the page's families multiselect; live-verify.

---

# PHASE 3 — Diagonals

The only family needing **two expirations** (long leg longer-dated, short leg nearer-dated), so it needs chain data beyond the single front expiration and per-leg expiry in `legs` (already supported by the normalized shape + the Calculator's per-leg expiry).

- **Task 3.1** — widen the chain fetch in `compute.swing_scan` to include a longer-dated expiration for the long leg (mirror `compute._term_chain`'s wider window).
- **Task 3.2 — `build_diagonals`** — `CALL_DIAG` (bullish: long deeper-ITM/longer call, short OTM/nearer call) + `PUT_DIAG` (bearish). Payoff at the FRONT (short) expiry uses the long leg's residual value — reuse the simulator/calculator per-leg-expiry valuation rather than pure intrinsic (the long leg still has time value at the short expiry). Add a focused test: the diagonal's near-expiry P/L curve peaks near the short strike.
- **Task 3.3** — page families multiselect + live-verify; final `CLAUDE.md` update.

---

## Definition of done (per phase)

- All new pure functions unit-tested; `options-scanner`, `services\options_svc`, and `webgui` suites green (no regressions beyond the documented pre-existing failures).
- The page renders + the scan works live (browser or Redis-driven check).
- `CLAUDE.md` updated (route table entry + a feature section + the design/plan links).
- Work committed on `Using_Highcharts` with conventional-commit messages.
