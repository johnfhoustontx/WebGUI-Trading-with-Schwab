"""
SchwabProxy - Trade lifecycle event detector
Version: 1.0.0
Last Updated: 2026-05-30

Version 1.0.0 Changes:
- Initial implementation

Pure first-crossing detector. No I/O. evaluate() returns at most one event dict
(or None) for a single tick given the current spread legs.
"""
MULTIPLIER = 100


def _mid(leg):
    b, a = leg.get("bid"), leg.get("ask")
    if b is None or a is None:
        return None
    return (b + a) / 2.0


def _spread_mid(strategy, legs):
    ps, pl = _mid(legs.get("put_short", {})), _mid(legs.get("put_long", {}))
    if strategy in ("PCS", "CCS"):
        if ps is None or pl is None:
            return None
        return ps - pl
    if strategy == "IC":
        cs, cl = _mid(legs.get("call_short", {})), _mid(legs.get("call_long", {}))
        if None in (ps, pl, cs, cl):
            return None
        return (ps - pl) + (cs - cl)
    return None


def evaluate(state, legs, underlying, ts):
    """Return one event dict {event_type, terminal, ts, underlying, mid,
    unrealized_pnl, pnl_pct, note} to fire, or None. Respects state['fired']."""
    fired = state["fired"]
    mid = _spread_mid(state["strategy"], legs)
    if mid is None:
        return None
    qty = state.get("quantity", 1)
    credit = state["entry_credit"]
    pnl = (credit - mid) * MULTIPLIER * qty
    pnl_pct = pnl / (credit * MULTIPLIER * qty) if credit else 0.0

    def mk(et, terminal, note):
        # trade_id stamped here so the event is self-contained — perf_events
        # requires a non-null trade_id, and the caller records the event as-is.
        return {"trade_id": state.get("trade_id"),
                "event_type": et, "terminal": terminal, "ts": ts,
                "underlying": underlying, "mid": round(mid, 4),
                "unrealized_pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 4),
                "note": note}

    # Precedence: target, then stop, then strike test.
    if "target_hit" not in fired and mid <= state["target_mid"]:
        return mk("target_hit", True, ">=50% credit captured")
    if "stop_hit" not in fired and mid >= state["stop_mid"]:
        return mk("stop_hit", True, "2x credit stop")
    if "strike_test" not in fired and underlying is not None:
        sk = state["short_strike"]
        strat = state["strategy"]
        tested = (underlying <= sk) if strat in ("PCS", "IC") else (underlying >= sk)
        # For IC use the put short as the tested side here; call side test is v2.
        if tested:
            return mk("strike_test", False, "short strike tested")
    return None
