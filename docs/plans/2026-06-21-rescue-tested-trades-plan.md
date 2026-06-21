# Rescue Tested Trades Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Detect tested credit spreads (PCS/CCS/IC) and present a ranked, commission-aware menu of rescue adjustments the user can approve to apply to paper positions.

**Architecture:** Approach C (hybrid). Cheap at-risk detection piggybacks the existing 5-min `run_manage_cycle` (writes a `rescue_state`+`heat` overlay onto the paper-account view + a `rescue_summary` for the nav badge). Expensive candidate ranking runs on-demand via a `rescue` command → `cache:options:rescue:<id>`. Apply executes via new paper-engine adjustment primitives behind a stale-price re-check. Tier-2 logic lives in `services/options_svc` + `options-scanner`; Tier-1 page reads Redis only.

**Tech Stack:** Python 3.11, pytest (+ fakeredis), NiceGUI (`ui.highchart`), Redis/Memurai via `shared.bus`, `shared.contracts` (pydantic `_Base`), SQLite (`paper_account_db`), Black-Scholes repricing via `signal_repricer`.

**Design doc:** [2026-06-21-rescue-tested-trades-design.md](2026-06-21-rescue-tested-trades-design.md)

**Read before starting:**
- `services/options_svc/{compute,handlers,scheduler,app}.py` (3-tier service shape, command dispatch at `handlers.py:252` `handle_command`)
- `options-scanner/{paper_engine,paper_account_db,signal_repricer,signal_recommender}.py` (stop constants at `signal_recommender.py:13-17`; `_close` at `paper_engine.py:257`; positions schema at `paper_account_db.py:53-81`)
- `shared/contracts/options.py` + `shared/contracts/envelope.py` (`_Base`)
- `webgui/pages/options/{simulator,calculator}.py` (on-demand command → version-poll page pattern); `webgui/main.py` NAV + `_NAV_BADGES` + watcher

**Test commands (run from repo root unless noted):**
- `.venv\Scripts\python -m pytest services\options_svc -q`
- `.venv\Scripts\python -m pytest shared\contracts -q`
- `cd webgui && ..\.venv\Scripts\python -m pytest -q`
- `cd options-scanner && ..\.venv\Scripts\python -m pytest tests -q` (baseline: 667 passed, 2 known fails)

**Conventions:** small commits (`feat:`/`test:`/`docs:`), end commit messages with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. TDD: write the failing test, see it fail, minimal impl, see it pass, commit. Never hard-code rates/paths — use `config/commissions.toml`.

---

## Phase 0 — Commission model (pure, no deps)

### Task 0.1: Commission rate config

**Files:**
- Create: `config/commissions.toml`

**Step 1:** Create the file (Schwab standard, 2026 Pricing Guide):

```toml
# Schwab standard commissions. Per the 2026 Pricing Guide.
# Passthrough exchange/regulatory fees default to 0 — set per product when known.
[options]        # per contract, applied PER LEG, on open AND close
equity = 0.65
index = 0.65
index_exchange_fee = 0.00   # Cboe proprietary index-option fee (SPX/VIX/OEX)

[futures]        # per contract, PER SIDE
standard = 2.25
exchange_fee = 0.00
```

**Step 2:** Commit.

```bash
git add config/commissions.toml
git commit -m "feat(rescue): Schwab-standard commission rate config"
```

### Task 0.2: `commission.py` loader + `commission_for`

**Files:**
- Create: `services/options_svc/commission.py`
- Test: `services/options_svc/tests/test_commission.py`

**Step 1: Write failing tests.**

```python
import pytest
from services.options_svc import commission as c


def test_index_symbols_detected():
    assert c.is_index_symbol("$SPX") is True
    assert c.is_index_symbol("SPX") is True
    assert c.is_index_symbol("$VIX") is True
    assert c.is_index_symbol("SPY") is False
    assert c.is_index_symbol("AAPL") is False


def test_equity_option_per_leg():
    # 2 legs closed, 1 contract -> 2 * 0.65
    assert c.commission_for(legs=2, symbol="SPY", qty=1) == pytest.approx(1.30)


def test_equity_option_scales_with_qty():
    # roll = 4 legs, 3 contracts -> 4 * 3 * 0.65
    assert c.commission_for(legs=4, symbol="SPY", qty=3) == pytest.approx(7.80)


def test_index_uses_index_rate_plus_exchange_fee(monkeypatch):
    monkeypatch.setattr(c, "_RATES", {
        "options": {"equity": 0.65, "index": 0.65, "index_exchange_fee": 0.49},
        "futures": {"standard": 2.25, "exchange_fee": 0.0},
    })
    # 2 legs, 1 contract, index -> 2 * (0.65 + 0.49)
    assert c.commission_for(legs=2, symbol="$SPX", qty=1) == pytest.approx(2.28)


def test_zero_legs_is_free():
    # let-expire / assignment legs cost nothing
    assert c.commission_for(legs=0, symbol="SPY", qty=5) == 0.0


def test_futures_round_turn_per_side():
    # futures hedge: qty * standard * 2 sides + exchange_fee
    assert c.futures_commission(qty=2) == pytest.approx(2 * 2.25 * 2)
```

**Step 2:** Run, expect ImportError/fail.

**Step 3: Implement.**

```python
"""
Options service — Schwab commission model.
Version: 1.0.0

Pure helpers. Rates load once from config/commissions.toml (single source of
truth; never hard-code rates in callers). See the rescue design doc.
"""
from __future__ import annotations
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
from repo_paths import REPO_ROOT  # noqa: E402

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

#############################################
# RATES
#############################################

def _load_rates() -> dict:
    path = pathlib.Path(REPO_ROOT) / "config" / "commissions.toml"
    with open(path, "rb") as fh:
        return tomllib.load(fh)

_RATES = _load_rates()

# Index roots that carry the index option rate (+ exchange passthrough).
_INDEX_ROOTS = {"SPX", "VIX", "OEX", "NDX", "RUT", "XSP", "DJX"}


def is_index_symbol(symbol: str) -> bool:
    if not symbol:
        return False
    return symbol.lstrip("$").upper() in _INDEX_ROOTS


def _option_rate(symbol: str) -> float:
    opt = _RATES["options"]
    if is_index_symbol(symbol):
        return float(opt["index"]) + float(opt.get("index_exchange_fee", 0.0))
    return float(opt["equity"])


def commission_for(legs: int, symbol: str, qty: int) -> float:
    """Total option commission for ``legs`` option legs of ``qty`` contracts each.
    A leg is a single buy/sell of one option series. Closing a 2-leg spread = 2
    legs; a roll (close 2 + open 2) = 4 legs. Let-expire/assignment legs = pass 0.
    """
    if legs <= 0 or qty <= 0:
        return 0.0
    return round(legs * qty * _option_rate(symbol), 4)


def futures_commission(qty: int) -> float:
    """Round-turn futures commission (per side x 2) + exchange passthrough."""
    fut = _RATES["futures"]
    if qty <= 0:
        return 0.0
    return round(qty * float(fut["standard"]) * 2 + float(fut.get("exchange_fee", 0.0)), 4)
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit `feat(rescue): commission model (commission_for/futures_commission)`.

---

## Phase 1 — Contracts

### Task 1.1: `RescueCandidate` + `RescueAdvisory`

**Files:**
- Modify: `shared/contracts/options.py`
- Test: `shared/contracts/tests/test_options_rescue.py`

**Step 1: Write failing tests.** (Mirror the existing envelope tests in `shared/contracts/tests/`.)

```python
from shared.contracts.options import RescueAdvisory, RescueCandidate


def test_candidate_defaults():
    cand = RescueCandidate(action="close", label="Close now")
    assert cand.apply_kind == "execute"
    assert cand.warnings == []
    assert cand.net_cash == 0.0


def test_advisory_validates_envelope():
    adv = RescueAdvisory(
        position_id=7, symbol="SPY", strategy="PCS",
        state="tested", heat=72.0,
        candidates=[{"action": "close", "label": "Close now"}],
    )
    assert adv.candidates[0].action == "close"
    assert adv.state == "tested"


def test_advisory_roundtrips_model_dump():
    adv = RescueAdvisory(position_id=1, symbol="SPY", strategy="PCS",
                         state="ok", heat=0.0)
    dumped = adv.model_dump()
    assert RescueAdvisory(**dumped).symbol == "SPY"
```

**Step 2:** Run `.venv\Scripts\python -m pytest shared\contracts -q`, expect fail.

**Step 3: Implement — append to `shared/contracts/options.py`.**

```python
class RescueLeg(_Base):
    side: str = ""        # "BUY" | "SELL"
    right: str = ""       # "PUT" | "CALL"
    strike: float = 0.0
    expiry: str | None = None
    qty: int = 0
    price: float = 0.0


class RescueCandidate(_Base):
    """One ranked rescue action with full economics (commission-inclusive)."""
    action: str                     # close | partial_close | narrow | convert_ic |
                                    # convert_butterfly | broken_wing | roll_down |
                                    # roll_out | roll_down_out | inverted | futures_hedge
    label: str
    applies: bool = True
    apply_kind: str = "execute"     # "execute" | "advisory"
    gross_cash: float = 0.0         # credit (+) / debit (-) before fees
    commission: float = 0.0
    net_cash: float = 0.0           # gross_cash - commission
    new_max_loss: float | None = None
    new_breakeven: float | None = None
    new_short_delta: float | None = None
    new_width: float | None = None
    new_expiry: str | None = None
    dte_after: int | None = None
    est_fill_legs: list[RescueLeg] = []
    rationale: list[str] = []
    context: list[str] = []
    warnings: list[str] = []
    score: float = 0.0


class RescueMark(_Base):
    underlying: float | None = None
    current_value: float | None = None
    unrealized_pnl: float | None = None
    short_delta: float | None = None
    dte: int | None = None


class RescueAdvisory(_Base):
    """cache:options:rescue:<position_id> — ranked rescue menu for one position."""
    position_id: int
    symbol: str
    strategy: str
    state: str = "ok"               # ok | watch | tested | critical
    heat: float = 0.0
    mark: RescueMark = RescueMark()
    context: list[str] = []
    candidates: list[RescueCandidate] = []
    priced_from_version: int | None = None
    ts: str | None = None
    error: str | None = None
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit `feat(rescue): RescueAdvisory/RescueCandidate contracts`.

---

## Phase 2 — At-risk detection (pure)

### Task 2.1: `RESCUE_THRESHOLDS` + `assess_position_risk`

**Files:**
- Create: `services/options_svc/rescue.py` (all pure rescue logic lives here; `compute.py` imports it)
- Test: `services/options_svc/tests/test_rescue_detect.py`

**Step 1: Write failing tests.**

```python
from services.options_svc import rescue


def _pos(**kw):
    base = dict(position_id=1, symbol="SPY", strategy="PCS",
                short_strike=500.0, long_strike=495.0, width=5.0,
                expiration="2026-07-31", entry_credit=1.00,
                current_short_delta=0.18, quantity=1)
    base.update(kw); return base


def _mark(**kw):
    base = dict(current_underlying=520.0, unrealized_pnl=20.0,
                current_short_delta=0.18, current_value=0.80, dte=40)
    base.update(kw); return base


def test_far_otm_is_ok():
    r = rescue.assess_position_risk(_pos(), _mark(), gex=None, regime=None)
    assert r["state"] == "ok"
    assert r["heat"] < 25


def test_underlying_through_short_strike_is_critical():
    r = rescue.assess_position_risk(
        _pos(), _mark(current_underlying=498.0, current_short_delta=0.55,
                      unrealized_pnl=-250.0, dte=10),
        gex=None, regime=None)
    assert r["state"] == "critical"
    assert r["heat"] >= 75


def test_money_stop_breach_marks_tested():
    # loss >= 2x credit (credit 1.00*100=100 -> -200)
    r = rescue.assess_position_risk(
        _pos(), _mark(unrealized_pnl=-200.0, current_short_delta=0.30),
        gex=None, regime=None)
    assert r["state"] in ("tested", "critical")


def test_gex_below_flip_raises_heat():
    base = rescue.assess_position_risk(
        _pos(), _mark(current_underlying=501.0, current_short_delta=0.28),
        gex=None, regime=None)
    hot = rescue.assess_position_risk(
        _pos(), _mark(current_underlying=501.0, current_short_delta=0.28),
        gex={"flip": 505.0, "put_wall": 490.0}, regime=None)
    assert hot["heat"] > base["heat"]   # short below flip = negative gamma danger
```

**Step 2:** Run, expect fail.

**Step 3: Implement `rescue.py` (detection portion).** Reuse the existing stop constants so detection ≈ auto-close.

```python
"""
Options service — rescue advisory engine (pure).
Version: 1.0.0

Detection (assess_position_risk), strategic context, candidate generation, and
ranking for tested credit spreads. No I/O — callers pass marks/gex/regime in.
See docs/plans/2026-06-21-rescue-tested-trades-design.md.
"""
from __future__ import annotations
import datetime as _dt

# Mirror signal_recommender stop constants so detection stays consistent with
# the auto-close manage cycle.
RESCUE_THRESHOLDS = {
    "delta_warn": 0.30,
    "delta_critical": 0.45,
    "delta_drift": 0.12,
    "money_warn_mult": 1.0,     # x entry credit (loss)
    "money_tested_mult": 2.0,
    "money_critical_mult": 3.0,
    "dte_manage": 21,
    "dte_urgent": 2,
    "proximity_watch_pct": 0.03,    # underlying within 3% of short strike
    "proximity_tested_pct": 0.01,
}

_STATES = ["ok", "watch", "tested", "critical"]


def _max(*states: str) -> str:
    return _STATES[max(_STATES.index(s) for s in states)]


def _dte(expiration: str, today: _dt.date | None = None) -> int:
    try:
        exp = _dt.date.fromisoformat(str(expiration)[:10])
    except Exception:
        return 999
    today = today or _dt.date.today()
    return (exp - today).days


def assess_position_risk(position, mark, gex=None, regime=None, today=None) -> dict:
    """Classify a single open position into ok/watch/tested/critical + 0-100 heat.

    position: paper position dict (short_strike, long_strike, entry_credit,
        quantity, strategy, symbol, expiration). mark: latest reprice
        (current_underlying, current_short_delta, unrealized_pnl). gex/regime are
        optional heat *modifiers* (never standalone triggers).
    """
    th = RESCUE_THRESHOLDS
    state = "ok"
    heat = 0.0

    short = position.get("short_strike")
    und = mark.get("current_underlying")
    is_put_side = position.get("strategy") in ("PCS", "IC")
    dte = mark.get("dte")
    if dte is None:
        dte = _dte(position.get("expiration"), today)

    # 1. proximity to short strike
    if short and und:
        # for a put spread, danger is underlying falling toward/below short
        gap = (und - short) / short if is_put_side else (short - und) / short
        if gap <= 0:                       # through the short strike
            state = _max(state, "critical"); heat += 45
        elif gap <= th["proximity_tested_pct"]:
            state = _max(state, "tested"); heat += 32
        elif gap <= th["proximity_watch_pct"]:
            state = _max(state, "watch"); heat += 18

    # 2. short delta
    d = abs(mark.get("current_short_delta") or 0.0)
    if d >= th["delta_critical"]:
        state = _max(state, "critical"); heat += 25
    elif d >= th["delta_warn"]:
        state = _max(state, "tested"); heat += 15

    # 3. P&L vs credit
    credit_dollars = (position.get("entry_credit") or 0.0) * 100 * (position.get("quantity") or 1)
    pnl = mark.get("unrealized_pnl")
    if credit_dollars > 0 and pnl is not None and pnl < 0:
        mult = abs(pnl) / credit_dollars
        if mult >= th["money_critical_mult"]:
            state = _max(state, "critical"); heat += 20
        elif mult >= th["money_tested_mult"]:
            state = _max(state, "tested"); heat += 14
        elif mult >= th["money_warn_mult"]:
            state = _max(state, "watch"); heat += 8

    # 4. time
    if dte <= th["dte_urgent"] and state != "ok":
        state = _max(state, "critical"); heat += 10
    elif dte <= th["dte_manage"] and state in ("tested", "critical"):
        heat += 6

    # 5. GEX modifier — short strike on the wrong side of the gamma flip
    if gex and short and und:
        flip = gex.get("flip")
        if flip and is_put_side and und < flip:
            heat += 8            # negative-gamma, vol-expansion danger
        wall = gex.get("put_wall") if is_put_side else gex.get("call_wall")
        if wall and short and abs(short - wall) / short <= 0.005:
            heat -= 5            # resting on a wall -> bounce more likely

    # 6. regime modifier — strategy fighting the tape
    if regime:
        ts = (regime.get("trend_state") or "").lower()
        if is_put_side and "bear" in ts:
            heat += 6
        if (not is_put_side) and "bull" in ts:
            heat += 6

    heat = max(0.0, min(100.0, heat))
    return {"state": state, "heat": round(heat, 1), "dte": dte}
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit `feat(rescue): assess_position_risk detection + thresholds`.

---

## Phase 3 — Strategic context (pure)

### Task 3.1: `strategic_context`

**Files:**
- Modify: `services/options_svc/rescue.py`
- Test: `services/options_svc/tests/test_rescue_context.py`

**Step 1: Write failing tests.**

```python
from services.options_svc import rescue


def _pos(**kw):
    base = dict(symbol="$SPX", strategy="PCS", short_strike=5980.0)
    base.update(kw); return base


def test_index_is_cash_settled_note():
    ctx = rescue.strategic_context(_pos(), gex=None, regime=None, underlying=5990.0)
    assert any("cash-settled" in s.lower() or "european" in s.lower() for s in ctx["notes"])
    assert ctx["assignment_risk"] is False


def test_futures_flag_assignment_risk():
    ctx = rescue.strategic_context(
        _pos(symbol="/ES", short_strike=5980.0),
        gex=None, regime=None, underlying=5950.0)  # deep below short
    assert ctx["assignment_risk"] is True
    assert any("assignment" in s.lower() for s in ctx["notes"])


def test_below_flip_flags_negative_gamma():
    ctx = rescue.strategic_context(
        _pos(), gex={"flip": 5995.0, "put_wall": 5950.0},
        regime=None, underlying=5985.0)
    assert ctx["negative_gamma"] is True
    assert any("flip" in s.lower() for s in ctx["notes"])
```

**Step 2:** Run, fail.

**Step 3: Implement — append to `rescue.py`.**

```python
_FUTURES_PREFIXES = ("/ES", "/NQ", "/MES", "/MNQ", "/RTY", "/YM")


def _instrument_kind(symbol: str) -> str:
    s = (symbol or "").upper()
    if s.startswith("/"):
        return "futures"
    from services.options_svc.commission import is_index_symbol
    return "index" if is_index_symbol(s) else "equity"


def strategic_context(position, gex=None, regime=None, underlying=None) -> dict:
    """Market-structure annotation: dealer gamma, regime, settlement mechanics.
    Returns notes[] + boolean flags used as ranking modifiers (never hard gates)."""
    notes: list[str] = []
    kind = _instrument_kind(position.get("symbol", ""))
    short = position.get("short_strike")
    is_put = position.get("strategy") in ("PCS", "IC")

    negative_gamma = False
    near_wall = False
    if gex:
        flip = gex.get("flip")
        if flip and underlying is not None:
            if (is_put and underlying < flip) or ((not is_put) and underlying > flip):
                negative_gamma = True
                notes.append(f"Short side is past the gamma flip ({flip:g}) — "
                             f"negative gamma, vol likely to expand; rolling here is risky.")
        wall = gex.get("put_wall") if is_put else gex.get("call_wall")
        if wall and short and abs(short - wall) / short <= 0.01:
            near_wall = True
            notes.append(f"Short strike rests near a {'put' if is_put else 'call'} "
                         f"wall ({wall:g}) — a bounce is statistically more likely.")

    assignment_risk = False
    if kind == "index":
        notes.append("Index option (European, cash-settled): no early-assignment risk; "
                     "holding to expiration is structurally safe from assignment.")
    elif kind == "futures":
        deep_itm = short and underlying is not None and (
            (is_put and underlying < short) or ((not is_put) and underlying > short))
        assignment_risk = bool(deep_itm)
        if deep_itm:
            notes.append("Futures option (American): short is ITM — early assignment / "
                         "futures-contract delivery is possible.")
        else:
            notes.append("Futures option (American): assignment possible if the short goes ITM.")
    else:
        assignment_risk = True
        notes.append("Equity/ETF option (American): early assignment possible near "
                     "ex-dividend or when deep ITM.")

    if regime:
        ts = (regime.get("trend_state") or "")
        if ts:
            notes.append(f"Regime: {ts} (confidence {regime.get('trend_confidence', 0):.0%}).")

    return {"notes": notes, "negative_gamma": negative_gamma,
            "near_wall": near_wall, "assignment_risk": assignment_risk, "kind": kind}
```

**Step 4:** Run, PASS. **Step 5:** Commit `feat(rescue): strategic_context (gamma/regime/settlement)`.

---

## Phase 4 — Candidate generation & ranking

> Candidate **pricing** needs the option chain. Reuse the chain fetch + mid-mark
> helpers `signal_repricer` already uses. To keep tasks pure/testable, the leg
> pricer is injected as a callable `price_leg(symbol, expiry, right, strike) ->
> float (mid)`; the real one wraps `signal_repricer`'s chain cache, a fake one is
> passed in tests. Build per-action functions first, then the orchestrator+ranker.

### Task 4.1: `score_candidate` (ranking, pure)

**Files:** Modify `services/options_svc/rescue.py`; Test `services/options_svc/tests/test_rescue_score.py`

**Step 1: Write failing tests.**

```python
from services.options_svc import rescue


def _c(**kw):
    base = dict(action="roll_down_out", net_cash=10.0, new_max_loss=300.0,
                new_short_delta=0.15, new_breakeven=495.0, commission=2.6)
    base.update(kw); return base


def test_max_loss_reduction_outranks_small_credit():
    big_cut = rescue.score_candidate(_c(new_max_loss=100.0), old_max_loss=400.0,
                                     old_short_delta=0.40, ctx={})
    small_cut = rescue.score_candidate(_c(new_max_loss=380.0), old_max_loss=400.0,
                                       old_short_delta=0.40, ctx={})
    assert big_cut > small_cut


def test_debit_is_penalized():
    credit = rescue.score_candidate(_c(net_cash=15.0), old_max_loss=400.0,
                                    old_short_delta=0.40, ctx={})
    debit = rescue.score_candidate(_c(net_cash=-15.0), old_max_loss=400.0,
                                   old_short_delta=0.40, ctx={})
    assert credit > debit


def test_roll_penalized_in_negative_gamma():
    normal = rescue.score_candidate(_c(action="roll_down_out"), old_max_loss=400.0,
                                    old_short_delta=0.40, ctx={"negative_gamma": False})
    risky = rescue.score_candidate(_c(action="roll_down_out"), old_max_loss=400.0,
                                   old_short_delta=0.40, ctx={"negative_gamma": True})
    assert risky < normal
```

**Step 2:** fail. **Step 3: Implement — append to `rescue.py`.**

```python
_ROLL_ACTIONS = {"roll_down", "roll_out", "roll_down_out", "inverted"}


def score_candidate(candidate, old_max_loss, old_short_delta, ctx) -> float:
    """0-100 desirability. Priority: max-loss reduction per net $ spent, delta
    flattening, credit-vs-debit, GEX/regime fit, breakeven. Pure."""
    c = candidate if isinstance(candidate, dict) else candidate.model_dump()
    score = 50.0

    # 1. max-loss reduction (per net dollar spent when it costs money)
    new_ml = c.get("new_max_loss")
    if new_ml is not None and old_max_loss:
        reduction = (old_max_loss - new_ml) / old_max_loss   # fraction
        score += 30 * max(-1.0, min(1.0, reduction))
        spent = max(0.0, -c.get("net_cash", 0.0)) + abs(c.get("commission", 0.0))
        if spent > 0 and old_max_loss - new_ml > 0:
            efficiency = (old_max_loss - new_ml) / spent
            score += min(10.0, efficiency / 5.0)

    # 2. delta flattening
    nd = c.get("new_short_delta")
    if nd is not None and old_short_delta:
        score += 15 * max(-1.0, min(1.0, (abs(old_short_delta) - abs(nd)) / abs(old_short_delta)))

    # 3. credit vs debit (net of commission)
    net = c.get("net_cash", 0.0)
    score += 8 if net >= 0 else -12   # never roll for a debit just to save it

    # 4. GEX/regime fit
    if c.get("action") in _ROLL_ACTIONS and ctx.get("negative_gamma"):
        score -= 12
    if c.get("action") in _ROLL_ACTIONS and ctx.get("near_wall"):
        score += 6

    # 5. breakeven improvement handled via max_loss/delta; small tilt for closes
    if c.get("action") == "close":
        score += 2   # capital preservation is always available

    return round(max(0.0, min(100.0, score)), 1)
```

**Step 4:** PASS. **Step 5:** Commit `feat(rescue): score_candidate ranking`.

### Task 4.2: Candidate builders (pure, injected pricer)

**Files:** Modify `services/options_svc/rescue.py`; Test `services/options_svc/tests/test_rescue_candidates.py`

Build one function per action that returns a `dict` shaped like `RescueCandidate`
(unscored), or `None` if unpriceable/not applicable. Each takes `(position, mark,
price_leg, ctx)`. Use `commission_for` for fees. Helpers compute new_max_loss /
new_breakeven from strikes + width.

**Step 1: Write failing tests** (representative — write one per builder):

```python
from services.options_svc import rescue


def _pos(**kw):
    base = dict(position_id=1, symbol="SPY", strategy="PCS",
                short_strike=500.0, long_strike=495.0, width=5.0,
                expiration="2026-07-31", entry_credit=1.00, quantity=2,
                max_loss_total=800.0)
    base.update(kw); return base


def _mark(**kw):
    base = dict(current_underlying=501.0, current_value=2.50,
                unrealized_pnl=-300.0, current_short_delta=0.35, dte=30)
    base.update(kw); return base


def _flat_pricer(*a, **k):
    return 1.00   # every leg mids at 1.00


def test_close_candidate_uses_current_value_and_2_legs_commission():
    c = rescue.build_close(_pos(), _mark(), _flat_pricer, ctx={})
    assert c["action"] == "close"
    # debit to close = current_value * 100 * qty
    assert c["gross_cash"] == -2.50 * 100 * 2
    # 2 legs * 2 contracts * 0.65
    assert c["commission"] == 2 * 2 * 0.65
    assert c["new_max_loss"] == 0.0


def test_convert_ic_adds_call_credit_and_cuts_max_loss():
    c = rescue.build_convert_ic(_pos(), _mark(), _flat_pricer, ctx={})
    assert c["action"] == "convert_ic"
    assert c["gross_cash"] > 0           # collecting call-side credit
    assert c["new_max_loss"] < _pos()["max_loss_total"]


def test_narrow_returns_debit_and_smaller_width():
    c = rescue.build_narrow(_pos(), _mark(), _flat_pricer, ctx={})
    assert c["action"] == "narrow"
    assert c["new_width"] < 5.0


def test_unpriceable_leg_returns_none():
    def dead_pricer(*a, **k):
        return None
    assert rescue.build_convert_ic(_pos(), _mark(), dead_pricer, ctx={}) is None
```

**Step 2:** fail. **Step 3: Implement each builder** in `rescue.py`. Sketch for the two
hardest; the rest follow the same shape (compute legs → price → gross_cash →
commission via `commission_for(legs, symbol, qty)` → net_cash → new_max_loss/
breakeven/width/delta → rationale/warnings). Build:
`build_close`, `build_partial_close`, `build_narrow`, `build_convert_ic`,
`build_convert_butterfly`, `build_broken_wing`, `build_roll_down`, `build_roll_out`,
`build_roll_down_out`, `build_inverted`, `build_futures_hedge` (apply_kind="advisory").

```python
from services.options_svc.commission import commission_for, futures_commission


def _credit_dollars(per_contract_credit, qty):
    return round(per_contract_credit * 100 * qty, 2)


def build_close(position, mark, price_leg, ctx) -> dict | None:
    qty = position.get("quantity") or 1
    cv = mark.get("current_value")
    if cv is None:
        return None
    gross = -round(cv * 100 * qty, 2)                  # debit to close
    comm = commission_for(legs=2, symbol=position["symbol"], qty=qty)
    return {
        "action": "close", "label": "Close now (systematic stop)",
        "apply_kind": "execute",
        "gross_cash": gross, "commission": comm, "net_cash": round(gross - comm, 2),
        "new_max_loss": 0.0, "new_short_delta": 0.0,
        "dte_after": mark.get("dte"),
        "rationale": ["Locks the current loss, removes all further risk."],
        "warnings": [] if gross >= -abs(gross) else [],
    }


def build_convert_ic(position, mark, price_leg, ctx) -> dict | None:
    """Sell a call credit spread of the SAME width above spot -> PCS becomes IC.
    Max loss falls by the call-side credit collected; BP usually unchanged
    (broker margins the larger side only)."""
    if position.get("strategy") != "PCS":
        return None
    sym, qty, width = position["symbol"], position.get("quantity") or 1, position["width"]
    und = mark.get("current_underlying")
    expiry = position["expiration"]
    if not und:
        return None
    # pick call short ~ symmetric distance above spot as the put short is below
    dist = und - position["short_strike"]
    call_short = round(und + max(dist, und * 0.01))
    call_long = call_short + width
    cs = price_leg(sym, expiry, "CALL", call_short)
    cl = price_leg(sym, expiry, "CALL", call_long)
    if cs is None or cl is None:
        return None
    credit_pc = max(0.0, cs - cl)
    gross = _credit_dollars(credit_pc, qty)
    comm = commission_for(legs=2, symbol=sym, qty=qty)
    old_ml = position.get("max_loss_total") or width * 100 * qty
    new_ml = round(max(0.0, old_ml - gross), 2)
    return {
        "action": "convert_ic", "label": "Convert to Iron Condor",
        "apply_kind": "execute",
        "gross_cash": gross, "commission": comm, "net_cash": round(gross - comm, 2),
        "new_max_loss": new_ml, "new_width": width, "new_expiry": expiry,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [
            {"side": "SELL", "right": "CALL", "strike": call_short, "expiry": expiry, "qty": qty, "price": cs},
            {"side": "BUY", "right": "CALL", "strike": call_long, "expiry": expiry, "qty": qty, "price": cl},
        ],
        "rationale": [f"Collects ${gross:.0f} call-side credit, cuts max loss to "
                      f"${new_ml:.0f}, flattens delta. Caps upside on a sharp reversal."],
        "warnings": ["Short calls now at risk if the market whipsaws up."],
    }
```

(Implement remaining builders analogously. `build_futures_hedge` returns
`apply_kind="advisory"`, uses `futures_commission`, and only when
`_instrument_kind in ("index","futures")`; it sizes contracts to neutralize net
position delta and sets no `new_max_loss`.)

**Step 4:** PASS each. **Step 5:** Commit `feat(rescue): per-action candidate builders`.

### Task 4.3: `rescue_candidates` orchestrator

**Files:** Modify `services/options_svc/rescue.py`; Test `services/options_svc/tests/test_rescue_orchestrate.py`

**Step 1: Failing test** — given a PCS position + flat pricer + ctx, returns a
ranked list (close present, scored descending), drops `None`s, never raises.

```python
def test_orchestrator_ranks_and_filters():
    cands = rescue.rescue_candidates(_pos(), _mark(), _flat_pricer,
                                     gex=None, regime=None)
    assert cands and cands[0]["score"] >= cands[-1]["score"]
    assert any(c["action"] == "close" for c in cands)
    assert all(c is not None for c in cands)
```

**Step 3: Implement.**

```python
_BUILDERS = [build_close, build_partial_close, build_narrow, build_convert_ic,
             build_convert_butterfly, build_broken_wing, build_roll_down,
             build_roll_out, build_roll_down_out, build_inverted, build_futures_hedge]


def rescue_candidates(position, mark, price_leg, gex=None, regime=None,
                      underlying=None) -> list[dict]:
    ctx = strategic_context(position, gex, regime,
                            underlying or mark.get("current_underlying"))
    out = []
    for fn in _BUILDERS:
        try:
            c = fn(position, mark, price_leg, ctx)
        except Exception:
            c = None
        if not c:
            continue
        c.setdefault("context", list(ctx["notes"]))
        if ctx.get("assignment_risk") and "assignment" not in " ".join(c.get("warnings", [])).lower():
            c.setdefault("warnings", []).append("Assignment risk on this instrument.")
        if c.get("net_cash", 0.0) < 0 and "debit" not in " ".join(c.get("warnings", [])).lower():
            c.setdefault("warnings", []).append("Net debit — costs money to apply.")
        c["score"] = score_candidate(c, position.get("max_loss_total"),
                                     mark.get("current_short_delta"), ctx)
        out.append(c)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out
```

**Step 4:** PASS. **Step 5:** Commit `feat(rescue): rescue_candidates orchestrator+ranking`.

---

## Phase 5 — Apply mechanics

### Task 5.1: `position_adjustments` table

**Files:** Modify `options-scanner/paper_account_db.py` (add to `SCHEMA` + an
`insert_adjustment`/`list_adjustments`); Test `options-scanner/tests/test_paper_adjustments.py`

**Step 1: Failing test** — insert an adjustment row, read it back by position_id.

**Step 3:** Add to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS position_adjustments (
    adjustment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    parent_position_id INTEGER,
    action TEXT,
    legs TEXT,            -- JSON
    gross_cash REAL,
    commission REAL,
    net_cash REAL,
    reason TEXT,
    ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_adj_position ON position_adjustments(position_id);
```

Add `insert_adjustment(db_path, **fields)` + `list_adjustments(db_path, position_id)`
(JSON-encode/decode `legs`). Also add a `parent_position_id` column to
`paper_positions` (idempotent `ALTER` guard in `init_db`, or add to the `CREATE` for
fresh DBs + a try/except `ALTER` for existing ones).

**Step 4/5:** PASS; commit `feat(rescue): position_adjustments table + accessors`.

### Task 5.2: `paper_adjust.py` primitives

**Files:** Create `options-scanner/paper_adjust.py`; Test `options-scanner/tests/test_paper_adjust.py`

Each primitive takes `(db_path, position, candidate, broker=None)`, mutates the DB
inside one transaction, writes a `position_adjustments` row, realizes commission +
any cash into the account, and returns a result dict `{ok, action, position_id,
new_position_id?, realized?, error?}`. Reuse `paper_engine._close` for the close legs
of a roll. Implement: `apply_close`, `apply_partial_close`, `apply_narrow`,
`apply_convert_ic`, `apply_convert_butterfly`, `apply_broken_wing`, `apply_roll`
(handles roll_down/out/down_out → close current + open linked new position with
`parent_position_id`), `apply_inverted`. `futures_hedge` has **no** apply (advisory).

**Step 1: Failing tests** (per primitive) against a temp DB seeded with one OPEN
position; assert DB state after (e.g. convert_ic → `strategy='IC'`, `call_short`/
`call_long` set, cash increased by net credit; roll → old row `status='CLOSED'` with
realized P&L + a new OPEN row linked by `parent_position_id`).

**Step 3:** Implement primitives. **Step 4/5:** PASS; commit
`feat(rescue): paper-engine adjustment primitives`.

### Task 5.3: Dispatcher `apply_adjustment` + stale-price guard

**Files:** Modify `options-scanner/paper_adjust.py`; Test same file's tests.

`apply_adjustment(db_path, position, candidate, price_leg, broker=None,
tolerance=0.15)`: re-price the candidate's `est_fill_legs` via `price_leg`, recompute
`net_cash`; if it drifted > `tolerance` fraction from `candidate["net_cash"]` (or the
position is no longer OPEN), return `{"ok": False, "stale": True, ...}` without
mutating. Else dispatch to the matching `apply_*` by `candidate["action"]`.

**Step 1: Failing tests:** (a) drift beyond tolerance → `stale`, no DB change;
(b) within tolerance → executes. **Step 3:** implement. **Step 4/5:** PASS; commit
`feat(rescue): apply_adjustment dispatcher with stale-price abort`.

---

## Phase 6 — Service wiring

### Task 6.1: Real leg pricer + `compute` entry points

**Files:** Modify `services/options_svc/compute.py`; Test `services/options_svc/tests/test_compute_rescue.py`

Add:
- `_make_leg_pricer(symbol)` → returns a `price_leg(symbol, expiry, right, strike)`
  closure backed by `signal_repricer`'s chain fetch/cache (mid of bid/ask). Defensive
  → returns `None` on miss.
- `compute_rescue(position_id) -> dict`: load the position from `paper_account_db`
  (or captured signal), reprice it (`signal_repricer.reprice_swing`) to build `mark`,
  fetch `gamma_snapshot(symbol)` for gex, read regime from the bridge/sentiment cache,
  call `rescue.rescue_candidates(...)`, assemble a `RescueAdvisory`-shaped dict
  (incl. `assess_position_risk` state/heat + `priced_from_version` + `ts`). Fully
  defensive → `{"error": ...}`.
- `assess_open_positions() -> dict`: cheap pass over all OPEN positions using their
  already-stored marks → `{position_id: {state, heat}}` + a summary
  `{n_tested, n_critical, position_ids}`. No chain fetches.

**Step 1:** Failing tests with a fake proxy/chain + fakeredis (mirror existing
`test_compute_*` in the suite). **Step 3:** implement. **Step 4/5:** PASS; commit
`feat(rescue): compute_rescue + assess_open_positions + leg pricer`.

### Task 6.2: Manage-cycle overlay + `rescue_summary`

**Files:** Modify `services/options_svc/handlers.py` (extend `run_manage_and_refresh`
at `handlers.py:162`), add cache-key constants; Test `services/options_svc/tests/test_handlers_rescue.py`

After the manage cycle reprices/closes, call `compute.assess_open_positions()` and:
- merge `{state, heat}` into each row of the paper-account view before
  `cache_set(CACHE_PAPER, ...)` (so the overlay rides the existing view — no new fetch
  for the GUI), and
- `cache_set(CACHE_RESCUE_SUMMARY, summary, event=EVENT_RESCUE_SUMMARY,
  skip_unchanged=True)`.

Add constants `CACHE_RESCUE = "cache:options:rescue"` (per-id: `f"{CACHE_RESCUE}:{id}"`),
`CACHE_RESCUE_SUMMARY = "cache:options:rescue_summary"`, matching `EVENT_*`.

**Step 1:** failing test: run handler against fakeredis-seeded positions → assert the
paper view rows carry `rescue_state`/`heat` and `rescue_summary` is published.
**Step 3/4/5:** implement, PASS, commit `feat(rescue): manage-cycle risk overlay + summary`.

### Task 6.3: `rescue` + `rescue_apply` commands

**Files:** Modify `services/options_svc/handlers.py` (`handle_command` at
`handlers.py:252`); Test `services/options_svc/tests/test_handlers_rescue.py`

- `rescue` (args `{position_id}`) → `compute_rescue` → validate `RescueAdvisory` →
  `cache_set(f"{CACHE_RESCUE}:{id}")` + publish.
- `rescue_apply` (args `{position_id, candidate}`) → load position → build leg pricer →
  `paper_adjust.apply_adjustment(...)`; on `stale` re-cache the advisory with a
  `stale`-flagged note; on success refresh the paper view + re-run `compute_rescue`
  for that id; publish.

Add two `elif command.type == ...` branches mirroring the existing `sim_run`/
`calc_compute` branches.

**Step 1:** failing tests: enqueue `rescue` → advisory cached; enqueue `rescue_apply`
with a within-tolerance candidate → position adjusted + advisory refreshed; with a
stale candidate → no mutation + stale flag. **Step 3/4/5:** implement, PASS, commit
`feat(rescue): rescue + rescue_apply command handlers`.

> No scheduler change — detection rides the existing `manage_due` tick.

---

## Phase 7 — GUI

### Task 7.1: Pure page builders

**Files:** Create `webgui/pages/options/rescue.py` (builders first); Test
`webgui/tests/test_rescue.py`

Pure functions: `heat_color(heat)` (green→amber→red zones, reuse the
`score_zone_color` idiom), `at_risk_rows(paper_view, captured_view)` (filter to
state in tested/critical, sort by heat desc, shape table rows),
`candidate_card_rows(advisory)` (one display dict per candidate: title, the
`gross / commission / net` line, metrics line, legs sub-rows, chips, warnings),
`summary_line(advisory)`, `cash_text(value)` (credit `+$x` green / debit `-$x` red).

**Step 1:** failing unit tests for each. **Step 3/4/5:** implement, PASS, commit
`feat(rescue): rescue page pure builders + tests`.

### Task 7.2: `render()` page

**Files:** Modify `webgui/pages/options/rescue.py`; (no new test — smoke via shell test in 7.3)

Follow the `simulator.py`/`calculator.py` pattern: build one persistent
`ui.highchart` at render (ESM import-map gotcha) for the optional payoff diagram;
`@guard` every handler. Top: at-risk `ui.table` from the cached paper+captured views
(version-poll, no fetch). Row-select → enqueue `rescue` command (`Bus().enqueue_command
("cmd:options", {"type": "rescue", "position_id": id})`), version-poll
`cache:options:rescue:<id>`, render candidate cards. `execute` cards get an **Apply**
button → confirm dialog → enqueue `rescue_apply`; toast result; surface `stale`.
Adjustment-history strip from a small read. Keep page state in a local closure dict.

**Step 5:** commit `feat(rescue): rescue page render()`.

### Task 7.3: Nav item, route, badge, row highlights

**Files:** Modify `webgui/main.py`; Modify `webgui/tests/test_shell.py`; Modify
`webgui/pages/options/paper.py` + `captured.py` (heat row coloring)

- Add `("/options/rescue", "Rescue", "healing")` to the Options NAV group; add
  `@ui.page("/options/rescue")` → `rescue.render()`.
- Add `/options/rescue` to the expected route set in `test_shell.py` (**Step 1:** this
  test fails first).
- Wire a red badge: in the watcher (`main._watcher_compute`), read
  `cache:options:rescue_summary` and set `_NAV_BADGES["Rescue"] = n_tested + n_critical`
  (cleared when the page opens, like the other badges).
- In `paper.py`/`captured.py`, color rows by the `rescue_state`/`heat` now present on
  the cached rows (a `body-cell` slot, reuse `heat_color`).

**Step 2:** `test_shell.py` fails → **Step 3:** wire route → **Step 4:** passes.
**Step 5:** commit `feat(rescue): nav item + route + badge + at-risk row highlights`.

---

## Phase 8 — End-to-end verification & docs

### Task 8.1: Redis-driven E2E check

**Files:** none (manual verification per CLAUDE.md "Verify in the browser" →
Redis-driven section).

Steps (services + Memurai up):
1. Seed a tested paper position (or use a live one).
2. `Bus().enqueue_command("cmd:options", {"type": "rescue", "position_id": <id>})`;
   `Bus().cache_get("cache:options:rescue:<id>")` → assert ranked candidates with
   commission/net populated.
3. Pick a candidate; `enqueue_command(..., {"type": "rescue_apply", "position_id": id,
   "candidate": <chosen>})`; re-read the paper view → assert the adjustment applied
   (or `stale` if prices moved).
4. Screenshot `/options/rescue` (light single-position page — screenshot tool is fine;
   avoid heavy multi-chart pages per the gotcha).

### Task 8.2: Full suites + docs

**Step 1:** Run all affected suites green:
- `.venv\Scripts\python -m pytest services\options_svc -q`
- `.venv\Scripts\python -m pytest shared\contracts -q`
- `cd options-scanner && ..\.venv\Scripts\python -m pytest tests -q` (allow the 2 known fails)
- `cd webgui && ..\.venv\Scripts\python -m pytest -q`

**Step 2:** Update the root `CLAUDE.md` (new section "Rescue tested trades page",
`/options/rescue` route row, `config/commissions.toml` mention, refreshed banner) and
`options-scanner/CLAUDE.md` (`paper_adjust.py`, `position_adjustments` table). Use the
claude-md-management:revise-claude-md skill.

**Step 3:** Commit `docs(rescue): document rescue feature in CLAUDE.md` and
`test(rescue): full-suite green`.

---

## Risks / notes

- **Pricer accuracy:** candidate economics are only as good as the mid-mark; the
  stale-price guard (5.3) is the safety net for apply. Keep `tolerance` configurable.
- **Captured signals are advisory-only** — `rescue_apply` must reject (clear message)
  any non-paper position id.
- **BP/margin on IC conversion:** broker margins the larger side; mirror the engine's
  existing `max_loss`/BP logic — don't invent a new BP rule.
- **Module-collision rule:** all engine imports stay inside `options_svc`/
  `options-scanner` (own processes). Webgui imports only nicegui + shared.* .
- **Per-folder test runs only** (never `pytest services` across all services).
