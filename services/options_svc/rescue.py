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


#############################################
# CANDIDATE BUILDERS (pure)
#############################################
#
# Each builder is build_<action>(position, mark, price_leg, ctx) -> dict | None.
# Returns a uniform candidate dict (shaped like RescueCandidate) or None when the
# action is not applicable / cannot be priced. Never raises — returns None on
# missing data / None prices; the orchestrator wraps calls in try/except.
#
# price_leg(symbol, expiry, right, strike) -> per-contract mid for ONE option leg
# (right is "PUT"|"CALL"), or None if unpriceable.

from services.options_svc.commission import commission_for, futures_commission  # noqa: E402


def _spread_max_loss_dollars(width, qty, total_credit_dollars) -> float:
    """Defined-risk spread max loss in dollars: width*100*qty - total net credit."""
    return round(max(0.0, width * 100 * qty - total_credit_dollars), 2)


def _add_days(expiry, n) -> str:
    try:
        d = _dt.date.fromisoformat(str(expiry)[:10])
        return (d + _dt.timedelta(days=n)).isoformat()
    except Exception:
        return expiry


def _leg(side, right, strike, expiry, qty, price):
    # est_fill_legs prices are display-only (the economics use ``cv`` / the
    # guarded ns/nl/etc., not these per-leg values). Coalesce a missing
    # (unpriceable, illiquid) leg price to 0.0 so the RescueLeg contract — whose
    # ``price`` is a non-Optional float — can always be constructed. A None here
    # otherwise raises a pydantic ValidationError that sinks the WHOLE advisory.
    return {"side": side, "right": right, "strike": strike, "expiry": expiry,
            "qty": qty, "price": float(price) if price is not None else 0.0}


def build_close(position, mark, price_leg, ctx) -> dict | None:
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    cv = mark.get("current_value")
    if cv is None:
        return None
    gross = -round(cv * 100 * qty, 2)
    commission = commission_for(2, sym, qty)
    return {
        "action": "close",
        "label": "Close now (systematic stop)",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": 0.0,
        "new_short_delta": 0.0,
        "new_width": None,
        "new_expiry": None,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [],
        "rationale": ["Locks the current loss, removes all further risk."],
        "warnings": [],
    }


def build_partial_close(position, mark, price_leg, ctx) -> dict | None:
    qty = position.get("quantity") or 1
    if qty <= 1:
        return None
    sym = position["symbol"]
    cv = mark.get("current_value")
    if cv is None:
        return None
    cur_delta = mark.get("current_short_delta")
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    old_ml = position.get("max_loss_total") or w * 100 * qty
    close_qty = qty // 2
    remaining = qty - close_qty
    gross = -round(cv * 100 * close_qty, 2)
    commission = commission_for(2, sym, close_qty)
    new_max_loss = round(old_ml * remaining / qty, 2)
    return {
        "action": "partial_close",
        "label": f"Close {close_qty} of {qty} contracts (scale down)",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": cur_delta,
        "new_width": None,
        "new_expiry": None,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [],
        "rationale": [f"Sheds {close_qty}/{qty} of the position; "
                      f"max loss falls to ${new_max_loss:.0f}."],
        "warnings": [],
    }


def build_narrow(position, mark, price_leg, ctx) -> dict | None:
    strategy = position.get("strategy")
    if strategy not in ("PCS", "CCS"):
        return None
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    expiry = position["expiration"]
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    if w <= 1:
        return None
    cur_delta = mark.get("current_short_delta")
    entry_credit_d = (position.get("entry_credit") or 0) * 100 * qty
    old_long = position["long_strike"]
    if strategy == "PCS":
        right = "PUT"
        new_long = position["short_strike"] - round(w / 2)
    else:  # CCS
        right = "CALL"
        new_long = position["short_strike"] + round(w / 2)
    p_old = price_leg(sym, expiry, right, old_long)
    p_new = price_leg(sym, expiry, right, new_long)
    if p_old is None or p_new is None:
        return None
    gross = round((p_old - p_new) * 100 * qty, 2)   # sell old long(+), buy closer long(-)
    commission = commission_for(2, sym, qty)
    new_width = abs(position["short_strike"] - new_long)
    total_credit = entry_credit_d + gross
    new_max_loss = _spread_max_loss_dollars(new_width, qty, total_credit)
    return {
        "action": "narrow",
        "label": "Narrow the spread",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": cur_delta,
        "new_width": new_width,
        "new_expiry": expiry,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [
            _leg("SELL", right, old_long, expiry, qty, p_old),
            _leg("BUY", right, new_long, expiry, qty, p_new),
        ],
        "rationale": [f"Width {w:g}->{new_width:g}; locks a small loss but caps "
                      f"catastrophic loss at ${new_max_loss:.0f}."],
        "warnings": [],
    }


def build_convert_ic(position, mark, price_leg, ctx) -> dict | None:
    strategy = position.get("strategy")
    if strategy not in ("PCS", "CCS"):
        return None
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    expiry = position["expiration"]
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    und = mark.get("current_underlying")
    old_ml = position.get("max_loss_total") or w * 100 * qty
    cur_delta = mark.get("current_short_delta")
    base = und if und is not None else position["short_strike"]
    if strategy == "PCS":   # add call spread above
        dist = max(abs(base - position["short_strike"]), base * 0.01)
        call_short = round(base + dist)
        call_long = call_short + w
        right, s1, l1 = "CALL", call_short, call_long
    else:  # CCS — add put spread below
        dist = max(abs(position["short_strike"] - base), base * 0.01)
        put_short = round(base - dist)
        put_long = put_short - w
        right, s1, l1 = "PUT", put_short, put_long
    ps = price_leg(sym, expiry, right, s1)
    pl = price_leg(sym, expiry, right, l1)
    if ps is None or pl is None:
        return None
    credit_pc = max(0.0, ps - pl)
    gross = round(credit_pc * 100 * qty, 2)
    commission = commission_for(2, sym, qty)
    new_max_loss = round(max(0.0, old_ml - gross), 2)
    return {
        "action": "convert_ic",
        "label": "Convert to Iron Condor",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": cur_delta,
        "new_width": w,
        "new_expiry": expiry,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [
            _leg("SELL", right, s1, expiry, qty, ps),
            _leg("BUY", right, l1, expiry, qty, pl),
        ],
        "rationale": [f"Collects ${gross:.0f} credit on the untested side, cuts max "
                      f"loss to ${new_max_loss:.0f}, flattens delta."],
        "warnings": ["Caps the upside / adds opposite-side risk on a sharp reversal."],
    }


def build_convert_butterfly(position, mark, price_leg, ctx) -> dict | None:
    strategy = position.get("strategy")
    if strategy not in ("PCS", "CCS"):
        return None
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    expiry = position["expiration"]
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    old_ml = position.get("max_loss_total") or w * 100 * qty
    cur_delta = mark.get("current_short_delta")
    short = position["short_strike"]
    if strategy == "PCS":
        right, s1, l1 = "CALL", short, short + w
    else:  # CCS
        right, s1, l1 = "PUT", short, short - w
    ps = price_leg(sym, expiry, right, s1)
    pl = price_leg(sym, expiry, right, l1)
    if ps is None or pl is None:
        return None
    credit_pc = max(0.0, ps - pl)
    gross = round(credit_pc * 100 * qty, 2)
    commission = commission_for(2, sym, qty)
    new_max_loss = round(max(0.0, old_ml - gross), 2)
    return {
        "action": "convert_butterfly",
        "label": "Convert to Iron Butterfly",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": cur_delta,
        "new_width": w,
        "new_expiry": expiry,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [
            _leg("SELL", right, s1, expiry, qty, ps),
            _leg("BUY", right, l1, expiry, qty, pl),
        ],
        "rationale": [f"Richer ATM credit (${gross:.0f}); tighter profit zone "
                      f"centered near {s1:g}."],
        "warnings": [f"Profits only if price pins near {s1:g}."],
    }


def build_roll_down(position, mark, price_leg, ctx) -> dict | None:
    strategy = position.get("strategy")
    if strategy not in ("PCS", "CCS"):
        return None
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    expiry = position["expiration"]
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    cv = mark.get("current_value")
    if cv is None:
        return None
    entry_credit_d = (position.get("entry_credit") or 0) * 100 * qty
    if strategy == "PCS":
        right = "PUT"
        new_short = position["short_strike"] - round(w)
        new_long = new_short - w
    else:  # CCS
        right = "CALL"
        new_short = position["short_strike"] + round(w)
        new_long = new_short + w
    ns = price_leg(sym, expiry, right, new_short)
    nl = price_leg(sym, expiry, right, new_long)
    if ns is None or nl is None:
        return None
    credit_new_pc = max(0.0, ns - nl)
    gross = round((-cv + credit_new_pc) * 100 * qty, 2)
    commission = commission_for(4, sym, qty)
    total_credit = entry_credit_d + gross
    new_max_loss = _spread_max_loss_dollars(w, qty, total_credit)
    return {
        "action": "roll_down",
        "label": "Roll down (same expiry)",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": None,
        "new_width": w,
        "new_expiry": expiry,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [
            _leg("BUY", right, position["short_strike"], expiry, qty,
                 price_leg(sym, expiry, right, position["short_strike"])),
            _leg("SELL", right, position["long_strike"], expiry, qty,
                 price_leg(sym, expiry, right, position["long_strike"])),
            _leg("SELL", right, new_short, expiry, qty, ns),
            _leg("BUY", right, new_long, expiry, qty, nl),
        ],
        "rationale": [f"Closes the tested spread (-${cv * 100 * qty:.0f}) and reopens "
                      f"at {new_short:g}/{new_long:g} for ${credit_new_pc * 100 * qty:.0f}; "
                      f"net ${gross:.0f}."],
        "warnings": [],
    }


def build_roll_out(position, mark, price_leg, ctx) -> dict | None:
    strategy = position.get("strategy")
    if strategy not in ("PCS", "CCS", "IC"):
        return None
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    expiry = position["expiration"]
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    cv = mark.get("current_value")
    if cv is None:
        return None
    cur_delta = mark.get("current_short_delta")
    is_put = strategy in ("PCS", "IC")
    entry_credit_d = (position.get("entry_credit") or 0) * 100 * qty
    new_expiry = _add_days(expiry, 30)
    right = "PUT" if is_put else "CALL"
    ns = price_leg(sym, new_expiry, right, position["short_strike"])
    nl = price_leg(sym, new_expiry, right, position["long_strike"])
    if ns is None or nl is None:
        return None
    credit_new_pc = max(0.0, ns - nl)
    gross = round((-cv + credit_new_pc) * 100 * qty, 2)
    commission = commission_for(4, sym, qty)
    total_credit = entry_credit_d + gross
    new_max_loss = _spread_max_loss_dollars(w, qty, total_credit)
    return {
        "action": "roll_out",
        "label": "Roll out (more time)",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": cur_delta,
        "new_width": w,
        "new_expiry": new_expiry,
        "dte_after": (mark.get("dte") or 0) + 30,
        "est_fill_legs": [
            _leg("SELL", right, position["short_strike"], new_expiry, qty, ns),
            _leg("BUY", right, position["long_strike"], new_expiry, qty, nl),
        ],
        "rationale": [f"Same strikes, +30 days for ${gross:.0f} net; gives the trade "
                      f"time to recover."],
        "warnings": (["IC rolled as a put-side estimate."] if strategy == "IC" else []),
    }


def build_roll_down_out(position, mark, price_leg, ctx) -> dict | None:
    strategy = position.get("strategy")
    if strategy not in ("PCS", "CCS"):
        return None
    qty = position.get("quantity") or 1
    sym = position["symbol"]
    expiry = position["expiration"]
    w = position.get("width") or abs(position["short_strike"] - position["long_strike"])
    cv = mark.get("current_value")
    if cv is None:
        return None
    entry_credit_d = (position.get("entry_credit") or 0) * 100 * qty
    new_expiry = _add_days(expiry, 30)
    if strategy == "PCS":
        right = "PUT"
        new_short = position["short_strike"] - round(w)
        new_long = new_short - w
    else:  # CCS
        right = "CALL"
        new_short = position["short_strike"] + round(w)
        new_long = new_short + w
    ns = price_leg(sym, new_expiry, right, new_short)
    nl = price_leg(sym, new_expiry, right, new_long)
    if ns is None or nl is None:
        return None
    credit_new_pc = max(0.0, ns - nl)
    gross = round((-cv + credit_new_pc) * 100 * qty, 2)
    commission = commission_for(4, sym, qty)
    total_credit = entry_credit_d + gross
    new_max_loss = _spread_max_loss_dollars(w, qty, total_credit)
    return {
        "action": "roll_down_out",
        "label": "Roll down & out",
        "apply_kind": "execute",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": new_max_loss,
        "new_short_delta": None,
        "new_width": w,
        "new_expiry": new_expiry,
        "dte_after": (mark.get("dte") or 0) + 30,
        "est_fill_legs": [
            _leg("SELL", right, new_short, new_expiry, qty, ns),
            _leg("BUY", right, new_long, new_expiry, qty, nl),
        ],
        "rationale": [f"Reopens at {new_short:g}/{new_long:g}, +30 days, for "
                      f"${gross:.0f} net; widens breakeven and buys time."],
        "warnings": [],
    }


def build_inverted(position, mark, price_leg, ctx) -> dict | None:
    if position.get("strategy") != "IC":
        return None
    return {
        "action": "inverted",
        "label": "Go inverted (manual)",
        "apply_kind": "advisory",
        "gross_cash": 0.0,
        "commission": 0.0,
        "net_cash": 0.0,
        "new_max_loss": None,
        "new_short_delta": None,
        "new_width": None,
        "new_expiry": None,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [],
        "rationale": ["Roll the untested call side DOWN past the put short to collect "
                      "width-beating credit. Discretionary strike selection — shown as "
                      "guidance; place manually."],
        "warnings": ["Sharp reversal puts the inverted short calls at risk."],
    }


def build_broken_wing(position, mark, price_leg, ctx) -> dict | None:
    if position.get("strategy") not in ("PCS", "CCS"):
        return None
    return {
        "action": "broken_wing",
        "label": "Broken-wing butterfly (manual)",
        "apply_kind": "advisory",
        "gross_cash": 0.0,
        "commission": 0.0,
        "net_cash": 0.0,
        "new_max_loss": None,
        "new_short_delta": None,
        "new_width": None,
        "new_expiry": None,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [],
        "rationale": ["Add an unequal-wing butterfly to vacuum premium and cut "
                      "downside, often for a credit. Optimal wings are discretionary — "
                      "shown as guidance; place manually."],
        "warnings": [],
    }


def build_futures_hedge(position, mark, price_leg, ctx) -> dict | None:
    if ctx.get("kind") not in ("index", "futures"):
        return None
    qty = position.get("quantity") or 1
    cur_delta = mark.get("current_short_delta")
    net_delta_shares = round((cur_delta or 0.0) * 100 * qty)
    n = max(1, round(abs(net_delta_shares) / 5))
    commission = futures_commission(n)
    return {
        "action": "futures_hedge",
        "label": "Delta hedge with futures",
        "apply_kind": "advisory",
        "gross_cash": 0.0,
        "commission": commission,
        "net_cash": round(-commission, 2),
        "new_max_loss": None,
        "new_short_delta": None,
        "new_width": None,
        "new_expiry": None,
        "dte_after": mark.get("dte"),
        "est_fill_legs": [],
        "rationale": [f"Net position delta ~= {net_delta_shares} share-equivalents; "
                      f"trade ~{n} micro futures (e.g. /MES, $5/pt) to flatten without "
                      f"touching the spread."],
        "warnings": [],
    }


#############################################
# SINGLE-OPTION RESCUE (pure) — Phase 1
#############################################
#
# Coverage for the "Single" strategy family: LONG_CALL / LONG_PUT (defined risk =
# the debit paid) and NAKED_CALL / NAKED_PUT (undefined risk = the credit
# received). Advisory-only (ad-hoc has no Apply). See
# docs/plans/2026-06-23-rescue-singles-design.md.
#
# ``position`` carries ``strategy``, ``short_strike`` (the single strike),
# ``entry_credit`` (SIGNED per-share: + premium received for a naked short, −
# premium paid for a long), ``quantity``. ``mark`` carries ``current_underlying``,
# ``current_value`` (option mark per-share), ``unrealized_pnl`` (dollars),
# ``current_short_delta``, ``dte``.

_SINGLE_LONG = ("LONG_CALL", "LONG_PUT")
_SINGLE_NAKED = ("NAKED_CALL", "NAKED_PUT")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def assess_single_risk(position, mark, gex=None, regime=None) -> dict:
    """Classify a SINGLE-option position into ok/watch/tested/critical + 0-100
    heat. Pure, fully defensive (missing underlying/None skips that term)."""
    strategy = (position.get("strategy") or "").upper()
    strike = _num(position.get("short_strike"))
    qty = int(position.get("quantity") or 1)
    entry_credit = _num(position.get("entry_credit")) or 0.0
    und = _num(mark.get("current_underlying"))
    pnl = mark.get("unrealized_pnl")
    dte = mark.get("dte")
    d = abs(_num(mark.get("current_short_delta")) or 0.0)

    if strategy in _SINGLE_LONG:
        # defined risk = the debit paid = abs(entry_credit)
        debit_dollars = abs(entry_credit) * 100 * max(1, qty)
        loss = max(0.0, -(pnl if pnl is not None else 0.0))
        loss_frac = loss / max(1.0, debit_dollars)
        otm_depth = 0.0
        if strike and und:
            if strategy == "LONG_CALL" and und < strike:
                otm_depth = (strike - und) / strike
            elif strategy == "LONG_PUT" and und > strike:
                otm_depth = (und - strike) / strike
        heat = min(50.0, loss_frac * 60) + min(25.0, otm_depth * 300)
        if dte is not None and dte <= 5 and otm_depth > 0:
            heat += 15
        heat = max(0.0, min(100.0, heat))
        state = ("critical" if heat >= 75 else "tested" if heat >= 50
                 else "watch" if heat >= 25 else "ok")
        return {"state": state, "heat": round(heat, 1)}

    # NAKED_CALL / NAKED_PUT — undefined risk; mirror the credit-short-leg logic.
    state = "ok"
    heat = 8.0    # undefined-risk base bump
    if strike and und:
        # naked put danger = underlying falling toward/through the strike;
        # naked call = underlying rising toward/through the strike.
        gap = ((und - strike) / strike if strategy == "NAKED_PUT"
               else (strike - und) / strike)
        if gap <= 0:                       # through the short strike
            state = _max(state, "critical"); heat += 45
        elif gap <= 0.01:
            state = _max(state, "tested"); heat += 32
        elif gap <= 0.03:
            state = _max(state, "watch"); heat += 18
    if d >= 0.45:
        state = _max(state, "critical"); heat += 25
    elif d >= 0.30:
        state = _max(state, "tested"); heat += 15
    credit_dollars = entry_credit * 100 * max(1, qty)
    if credit_dollars > 0 and pnl is not None and pnl < 0:
        mult = abs(pnl) / credit_dollars
        if mult >= 2:
            heat += 14
        elif mult >= 1:
            heat += 8
    heat = max(0.0, min(100.0, heat))
    return {"state": state, "heat": round(heat, 1)}


def _single_candidate(action, label, *, gross, commission, rationale,
                      new_max_loss=None, new_expiry=None, dte_after=None,
                      new_width=None, est_fill_legs=None, context=None,
                      warnings=None, score=50.0) -> dict:
    """Assemble a uniform (RescueCandidate-shaped) single-option candidate dict.
    All single candidates are advisory-only."""
    gross = round(gross, 2)
    commission = round(commission, 2)
    return {
        "action": action,
        "label": label,
        "apply_kind": "advisory",
        "gross_cash": gross,
        "commission": commission,
        "net_cash": round(gross - commission, 2),
        "new_max_loss": (round(new_max_loss, 2) if new_max_loss is not None else None),
        "new_short_delta": None,
        "new_width": new_width,
        "new_expiry": new_expiry,
        "dte_after": dte_after,
        "est_fill_legs": est_fill_legs or [],
        "rationale": list(rationale),
        "context": list(context or []),
        "warnings": list(warnings or []),
        "score": score,
    }


def single_candidates(position, mark, price_leg, gex=None, regime=None) -> list[dict]:
    """Build advisory rescue candidates for a single-option position. Pure; a
    candidate needing an unpriceable leg is skipped. Order: close first (the safe
    floor), then the repairs."""
    strategy = (position.get("strategy") or "").upper()
    sym = position.get("symbol")
    qty = int(position.get("quantity") or 1)
    strike = _num(position.get("short_strike"))
    expiry = position.get("expiration")
    entry_credit = _num(position.get("entry_credit")) or 0.0
    cv = mark.get("current_value")
    dte = mark.get("dte")
    right = "CALL" if strategy in ("LONG_CALL", "NAKED_CALL") else "PUT"
    rword = "call" if right == "CALL" else "put"
    try:
        w = max(1, round(strike * 0.05)) if strike else 1
    except (TypeError, ValueError):
        w = 1

    out: list[dict] = []
    if strike is None:
        return out

    if strategy in _SINGLE_LONG:
        debit_dollars = abs(entry_credit) * 100 * qty
        # 1. close — sell to close, recover remaining premium (credit).
        if cv is not None:
            out.append(_single_candidate(
                "close", "Sell to close",
                gross=cv * 100 * qty, commission=commission_for(1, sym, qty),
                new_max_loss=0.0, dte_after=dte,
                rationale=["Sell to close — recover the remaining premium, "
                           "cut the loss."],
                score=60.0))
        # 2. roll_out — same strike, +30 DTE (debit; buys time).
        new_expiry = _add_days(expiry, 30)
        nl = price_leg(sym, new_expiry, right, strike) if cv is not None else None
        if cv is not None and nl is not None:
            out.append(_single_candidate(
                "roll_out", "Roll out ~30 days",
                gross=(cv - nl) * 100 * qty, commission=commission_for(2, sym, qty),
                new_expiry=new_expiry, dte_after=(dte or 0) + 30,
                est_fill_legs=[_leg("SELL", right, strike, expiry, qty, cv),
                               _leg("BUY", right, strike, new_expiry, qty, nl)],
                rationale=["Roll out ~30 days for more time (costs a debit)."],
                score=45.0))
        # 3. convert_to_vertical — sell a further-OTM same-type option → debit spread.
        far = strike + w if right == "CALL" else strike - w
        sp = price_leg(sym, expiry, right, far)
        if sp is not None:
            gross = sp * 100 * qty
            out.append(_single_candidate(
                "convert_to_vertical", f"Convert to {rword} debit spread",
                gross=gross, commission=commission_for(1, sym, qty),
                new_max_loss=max(0.0, debit_dollars - round(gross, 2)),
                new_width=w, dte_after=dte,
                est_fill_legs=[_leg("SELL", right, far, expiry, qty, sp)],
                rationale=[f"Sell a further-OTM {rword} against it → debit spread; "
                           f"recovers premium and caps the position."],
                score=52.0))
        return out

    # NAKED_CALL / NAKED_PUT — defend undefined risk.
    undef = "This position is undefined-risk."
    # 1. close — buy to close (debit).
    if cv is not None:
        out.append(_single_candidate(
            "close", "Buy to close",
            gross=-(cv * 100 * qty), commission=commission_for(1, sym, qty),
            new_max_loss=0.0, dte_after=dte,
            rationale=["Buy to close — removes the undefined risk."],
            context=[undef], score=60.0))
    # 2. roll — buy to close + sell a new option away & out for a credit.
    new_expiry = _add_days(expiry, 30)
    roll_strike = strike - w if strategy == "NAKED_PUT" else strike + w
    ns = price_leg(sym, new_expiry, right, roll_strike) if cv is not None else None
    if cv is not None and ns is not None:
        out.append(_single_candidate(
            "roll", f"Roll {'down' if strategy == 'NAKED_PUT' else 'up'} & out",
            gross=(-cv + ns) * 100 * qty, commission=commission_for(2, sym, qty),
            new_expiry=new_expiry, dte_after=(dte or 0) + 30,
            est_fill_legs=[_leg("BUY", right, strike, expiry, qty, cv),
                           _leg("SELL", right, roll_strike, new_expiry, qty, ns)],
            rationale=["Roll away and out for a credit — buys room; still "
                       "undefined risk."],
            context=[undef], warnings=["Position remains undefined-risk."],
            score=45.0))
    # 3. buy_protection — buy a further-OTM same-type option → DEFINES the risk.
    prot_strike = strike - w if strategy == "NAKED_PUT" else strike + w
    pp = price_leg(sym, expiry, right, prot_strike)
    if pp is not None:
        gross = -(pp * 100 * qty)
        credit_dollars = entry_credit * 100 * qty
        out.append(_single_candidate(
            "buy_protection", f"Buy a further-OTM {rword} (define risk)",
            gross=gross, commission=commission_for(1, sym, qty),
            new_max_loss=max(0.0, w * 100 * qty - (credit_dollars + round(gross, 2))),
            new_width=w, dte_after=dte,
            est_fill_legs=[_leg("BUY", right, prot_strike, expiry, qty, pp)],
            rationale=[f"Buy a further-OTM {rword} → converts to a credit spread "
                       f"that DEFINES your max loss."],
            score=52.0))
    return out


#############################################
# DEBIT VERTICALS (pure) — Phase 1b
#############################################
# Coverage for defined-risk DEBIT verticals: VERT_CALL_DEBIT (bull call = long
# lower call + short higher call) and VERT_PUT_DEBIT (bear put = long higher put
# + short lower put). Defined max loss = the debit paid; directional. Advisory-
# only. See docs/plans/2026-06-24-rescue-debit-verticals-design.md.
#
# ``position`` carries ``strategy``, ``long_strike`` (the LONG/directional leg),
# ``short_strike`` (the SHORT leg), ``entry_credit`` (SIGNED per-share: NEGATIVE =
# debit paid), ``quantity``. ``mark`` carries ``current_underlying``,
# ``current_value`` (spread value/share = long-leg mid − short-leg mid),
# ``unrealized_pnl`` (dollars), ``current_short_delta``, ``dte``.

_DEBIT_STRATEGIES = ("VERT_CALL_DEBIT", "VERT_PUT_DEBIT")


def assess_debit_risk(position, mark, gex=None, regime=None) -> dict:
    """Classify a DEBIT-vertical position into ok/watch/tested/critical + 0-100
    heat. A debit vertical is directional; "at-risk" = the underlying moved
    against it (toward max loss). Reuses the LONG-single heat model keyed on the
    LONG leg + a loss-fraction term. Pure, fully defensive (missing underlying/
    None skips the otm term; never raises)."""
    strategy = (position.get("strategy") or "").upper()
    long_strike = _num(position.get("long_strike"))
    qty = int(position.get("quantity") or 1)
    entry_credit = _num(position.get("entry_credit")) or 0.0
    und = _num(mark.get("current_underlying"))
    pnl = mark.get("unrealized_pnl")
    dte = mark.get("dte")

    debit_dollars = abs(entry_credit) * 100 * max(1, qty)
    loss = max(0.0, -(pnl if pnl is not None else 0.0))
    loss_frac = loss / max(1.0, debit_dollars)
    otm_depth = 0.0
    if long_strike and und:
        if strategy == "VERT_CALL_DEBIT" and und < long_strike:
            otm_depth = (long_strike - und) / long_strike
        elif strategy == "VERT_PUT_DEBIT" and und > long_strike:
            otm_depth = (und - long_strike) / long_strike
    heat = min(50.0, loss_frac * 60) + min(25.0, otm_depth * 300)
    if dte is not None and dte <= 5 and otm_depth > 0:
        heat += 15
    heat = max(0.0, min(100.0, heat))
    state = ("critical" if heat >= 75 else "tested" if heat >= 50
             else "watch" if heat >= 25 else "ok")
    return {"state": state, "heat": round(heat, 1)}


def debit_candidates(position, mark, price_leg, gex=None, regime=None) -> list[dict]:
    """Build advisory rescue candidates for a DEBIT-vertical position. Pure; a
    candidate needing an unpriceable leg is skipped. Order: close first (the safe
    floor), then the repairs. All ``apply_kind="advisory"``."""
    strategy = (position.get("strategy") or "").upper()
    sym = position.get("symbol")
    qty = int(position.get("quantity") or 1)
    L = _num(position.get("long_strike"))
    S = _num(position.get("short_strike"))
    expiry = position.get("expiration")
    entry_credit = _num(position.get("entry_credit")) or 0.0
    cv = mark.get("current_value")
    dte = mark.get("dte")
    right = "CALL" if strategy == "VERT_CALL_DEBIT" else "PUT"
    rword = "call" if right == "CALL" else "put"

    out: list[dict] = []
    if L is None or S is None:
        return out
    w = abs(S - L)
    debit_dollars = abs(entry_credit) * 100 * qty

    # 1. close — sell the spread to close, recover the remaining value (credit).
    if cv is not None:
        out.append(_single_candidate(
            "close", "Sell to close",
            gross=cv * 100 * qty, commission=commission_for(2, sym, qty),
            new_max_loss=0.0, dte_after=dte,
            est_fill_legs=[_leg("SELL", right, L, expiry, qty,
                                price_leg(sym, expiry, right, L)),
                           _leg("BUY", right, S, expiry, qty,
                                price_leg(sym, expiry, right, S))],
            rationale=["Sell to close — recover the remaining value, cut the loss."],
            score=60.0))

    # 2. roll_out — sell current + buy same-strikes spread at +30d (debit; time).
    new_expiry = _add_days(expiry, 30)
    nl = price_leg(sym, new_expiry, right, L) if cv is not None else None
    ns = price_leg(sym, new_expiry, right, S) if cv is not None else None
    if cv is not None and nl is not None and ns is not None:
        new_spread_val = nl - ns
        gross = (cv - new_spread_val) * 100 * qty      # debit (later spread richer)
        out.append(_single_candidate(
            "roll_out", "Roll out ~30 days",
            gross=gross, commission=commission_for(4, sym, qty),
            new_expiry=new_expiry, dte_after=(dte or 0) + 30, new_width=w,
            est_fill_legs=[
                _leg("SELL", right, L, expiry, qty, price_leg(sym, expiry, right, L)),
                _leg("BUY", right, S, expiry, qty, price_leg(sym, expiry, right, S)),
                _leg("BUY", right, L, new_expiry, qty, nl),
                _leg("SELL", right, S, new_expiry, qty, ns)],
            rationale=["Roll out ~30 days for more time (costs a debit)."],
            score=45.0))

    # 3. convert_to_butterfly — sell an equal-width spread beyond the short strike
    #    → credit that reduces the net debit and caps the position.
    far = S + w if right == "CALL" else S - w
    sp_s = price_leg(sym, expiry, right, S)
    sp_f = price_leg(sym, expiry, right, far)
    if sp_s is not None and sp_f is not None:
        credit = max(0.0, sp_s - sp_f)
        gross = credit * 100 * qty
        out.append(_single_candidate(
            "convert_to_butterfly", f"Convert to {rword} butterfly",
            gross=gross, commission=commission_for(2, sym, qty),
            new_max_loss=round(max(0.0, debit_dollars - round(gross, 2)), 2),
            new_width=w, dte_after=dte,
            est_fill_legs=[_leg("SELL", right, S, expiry, qty, sp_s),
                           _leg("BUY", right, far, expiry, qty, sp_f)],
            rationale=[f"Sell a further {rword} spread → butterfly; recovers "
                       f"premium and reduces the net debit."],
            score=52.0))
    return out


#############################################
# SINGLE-TYPE RANGE STRUCTURES (pure) — Phase 1c
#############################################
# Coverage for defined-risk DEBIT range structures made of ONE option type:
# CONDOR_CALL / CONDOR_PUT (long condor: long K1, short K2, short K3, long K4) and
# BUTTERFLY_CALL / BUTTERFLY_PUT (long 1-2-1 fly: long K1, short 2×K2, long K3).
# Defined max loss = the debit paid; neutral/range (profit in the body / between the
# inner shorts). Advisory-only. See
# docs/plans/2026-07-14-rescue-condor-butterfly-design.md.
#
# ``position`` carries ``strategy``, ``legs`` (PER-UNIT: [{right, side, strike, qty}]),
# ``entry_credit`` (SIGNED per-share: NEGATIVE = debit paid), ``quantity`` (units).
# ``mark`` carries ``current_underlying``, ``current_value`` (structure value/share =
# Σ sign·mid·qty over the legs), ``unrealized_pnl`` (dollars), ``dte``.

_RANGE_STRATEGIES = ("CONDOR_CALL", "CONDOR_PUT", "BUTTERFLY_CALL", "BUTTERFLY_PUT")


def _range_center_halfwidth(legs) -> tuple:
    """Profit-zone center + half-width for a range structure, from its legs.

    center = midpoint of the SHORT strikes (the fly body / the two condor inner
    shorts); half_width = center → the nearest LONG strike (wing). Returns
    ``(None, None)`` when the strikes are degenerate/missing. Pure."""
    shorts = [_num(l.get("strike")) for l in (legs or []) if l.get("side") == "short"]
    longs = [_num(l.get("strike")) for l in (legs or []) if l.get("side") == "long"]
    shorts = [s for s in shorts if s is not None]
    longs = [w for w in longs if w is not None]
    if not shorts or not longs:
        return (None, None)
    center = sum(shorts) / len(shorts)
    half_width = min(abs(w - center) for w in longs)
    if half_width <= 0:
        return (center, None)
    return (center, half_width)


def assess_range_risk(position, mark, gex=None, regime=None) -> dict:
    """Classify a single-type RANGE structure (condor/butterfly) into
    ok/watch/tested/critical + 0-100 heat. A long range structure loses when the
    underlying leaves the profit zone toward a wing. Pure, fully defensive (missing
    underlying / degenerate strikes skip the range term; never raises)."""
    legs = position.get("legs") or []
    qty = int(position.get("quantity") or 1)
    entry_credit = _num(position.get("entry_credit")) or 0.0
    und = _num(mark.get("current_underlying"))
    pnl = mark.get("unrealized_pnl")
    dte = mark.get("dte")

    debit_dollars = abs(entry_credit) * 100 * max(1, qty)
    loss = max(0.0, -(pnl if pnl is not None else 0.0))
    loss_frac = loss / max(1.0, debit_dollars)

    center, half_width = _range_center_halfwidth(legs)
    range_frac = 0.0
    if und is not None and center is not None and half_width:
        range_frac = abs(und - center) / half_width

    heat = min(50.0, loss_frac * 60) + min(35.0, range_frac * 35)
    if dte is not None and dte <= 5 and range_frac > 0.8:
        heat += 15
    heat = max(0.0, min(100.0, heat))
    state = ("critical" if heat >= 75 else "tested" if heat >= 50
             else "watch" if heat >= 25 else "ok")
    return {"state": state, "heat": round(heat, 1)}


def range_candidates(position, mark, price_leg, gex=None, regime=None) -> list[dict]:
    """Build advisory rescue candidates for a single-type RANGE structure. Pure; a
    candidate needing an unpriceable leg is skipped. Order: close first (the safe
    floor), then roll_out. All ``apply_kind="advisory"``."""
    legs = position.get("legs") or []
    sym = position.get("symbol")
    qty = int(position.get("quantity") or 1)
    expiry = position.get("expiration")
    cv = mark.get("current_value")
    dte = mark.get("dte")
    strategy = (position.get("strategy") or "").upper()
    word = "condor" if "CONDOR" in strategy else "butterfly"

    out: list[dict] = []
    if not legs:
        return out
    n = len(legs)

    def _side_word(leg):
        return "SELL" if leg.get("side") == "long" else "BUY"

    # 1. close — sell/close the whole structure, recover the remaining value (credit).
    if cv is not None:
        out.append(_single_candidate(
            "close", "Close the structure",
            gross=cv * 100 * qty, commission=commission_for(n, sym, qty),
            new_max_loss=0.0, dte_after=dte,
            est_fill_legs=[
                _leg(_side_word(l), l.get("right"), l.get("strike"), expiry,
                     qty * int(l.get("qty") or 1),
                     price_leg(sym, expiry, l.get("right"), l.get("strike")))
                for l in legs],
            rationale=[f"Close the {word} — recover the remaining value, cut the loss."],
            score=60.0))

    # 2. roll_out — close the structure + reopen the SAME strikes at +30d (debit; time).
    new_expiry = _add_days(expiry, 30)
    new_mids = ([price_leg(sym, new_expiry, l.get("right"), l.get("strike"))
                 for l in legs] if cv is not None else [None])
    if cv is not None and all(m is not None for m in new_mids):
        # structure value = +long −short (matches ``cv`` from compute).
        new_cv = sum((1.0 if l.get("side") == "long" else -1.0)
                     * m * int(l.get("qty") or 1)
                     for l, m in zip(legs, new_mids))
        gross = (cv - new_cv) * 100 * qty          # debit (later structure richer)
        roll_legs = []
        for l in legs:
            roll_legs.append(_leg(_side_word(l), l.get("right"), l.get("strike"),
                                  expiry, qty * int(l.get("qty") or 1),
                                  price_leg(sym, expiry, l.get("right"), l.get("strike"))))
        for l, m in zip(legs, new_mids):
            # reopen: original side at the later expiry.
            open_side = "BUY" if l.get("side") == "long" else "SELL"
            roll_legs.append(_leg(open_side, l.get("right"), l.get("strike"),
                                  new_expiry, qty * int(l.get("qty") or 1), m))
        out.append(_single_candidate(
            "roll_out", "Roll out ~30 days",
            gross=gross, commission=commission_for(2 * n, sym, qty),
            new_expiry=new_expiry, dte_after=(dte or 0) + 30,
            est_fill_legs=roll_legs,
            rationale=[f"Roll the {word} out ~30 days for more time in the range "
                       f"(costs a debit)."],
            score=45.0))
    return out


#############################################
# ORCHESTRATOR (pure)
#############################################

_BUILDERS = [build_close, build_partial_close, build_narrow, build_convert_ic,
             build_convert_butterfly, build_broken_wing, build_roll_down,
             build_roll_out, build_roll_down_out, build_inverted, build_futures_hedge]


def rescue_candidates(position, mark, price_leg, gex=None, regime=None,
                      underlying=None) -> list[dict]:
    """Build, annotate, score, and rank every applicable rescue action. Pure;
    never raises (a failing builder is dropped)."""
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
