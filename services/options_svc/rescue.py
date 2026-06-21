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
