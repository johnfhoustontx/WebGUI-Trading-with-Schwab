"""The Trade Plan — a verdict rendered as a falsifiable plan.

The verdict says WHAT. This says how you would hold it, and — the part that
earns the block its space — what would prove it wrong. Every field is derived
from something the analysis already computed; nothing here forecasts.

**The time stop is the point.** The artifact predicts 20 TRADING days. Past
that the read is unmodelled, and nothing in the app has ever said so: a
position taken on a 20-day edge and held for three months is no longer being
held for the reason it was opened. A round "30 days" or "one month" would be a
number we invented sitting on top of a number we measured, so the stop is the
model's own horizon, resolved to a real date.

**Clearance outranks everything.** A bottom-band read in a rising tape is a
RELATIVE expression, because that is what the model's label literally predicts
(20-day forward excess return vs SPY) — not a directional short the tape has
refused. See ``market_filter``.

Pure: the caller passes the analysis dict. Never raises.
"""
import datetime as dt
import sys

from repo_paths import TRADE_ANALYZER

# ``trade-analyzer`` exposes its engines as ``src.analysis.*`` and has no
# package init, so its directory goes on sys.path — the same bootstrap
# ``compute`` performs. Done here too so this module is importable on its own
# (a test that only wants the plan should not have to import the orchestrator).
if str(TRADE_ANALYZER) not in sys.path:
    sys.path.insert(0, str(TRADE_ANALYZER))

# 1.8x is the middle of the 1.5-2.0 band the plan doc set. It is a starting
# point, not a measured optimum, and is labelled as such on screen.
ATR_STOP_MULTIPLE = 1.8


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _add_trading_days(start, n):
    """``n`` trading days after ``start`` (weekends skipped).

    Holidays are NOT skipped: this is a review date, and being one session out
    twice a year costs nothing, while importing a market calendar here would
    buy precision the number does not have."""
    d, added = start, 0
    while added < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _side_from(swing):
    v = ((swing or {}).get("verdict") or "").strip().upper()
    if v == "BUY":
        return "long"
    if v == "SELL":
        return "short"
    return None


def build(analysis, today=None):
    """A trade plan for this analysis. Never raises."""
    from src.analysis import structure as _st

    today = today or dt.date.today()
    analysis = analysis if isinstance(analysis, dict) else {}
    swing = analysis.get("swing_model") or {}
    dealer = analysis.get("dealer_context") or {}
    clearance = analysis.get("direction_clearance") or {}

    spot = _num(analysis.get("price"))
    horizon = int(_num(swing.get("horizon_days")) or 20)
    side = _side_from(swing)

    plan = {
        "symbol": analysis.get("symbol"),
        "side": side,
        "action": "none",
        "structure": None,
        "rationale": "",
        "entry_zone": "",
        "stop": None,
        "stop_note": "",
        "target": "",
        "short_strike_guidance": "",
        "dte_min": None,
        "dte_max": None,
        "time_stop_trading_days": horizon,
        "time_stop_date": _add_trading_days(today, horizon).isoformat(),
        "time_stop_note": (
            f"Exit or re-underwrite at {horizon} trading days — past the "
            f"model's horizon the read is unmodelled."),
        "events": _events_line(analysis, horizon),
        "what_would_change_it": [],
    }

    if side is None:
        plan["what_would_change_it"] = [
            "The composite moving into the top or bottom band — today it sits "
            "in the middle, where the model has no edge to express."]
        plan["rationale"] = "No directional read to hold."
        return plan

    state = ((clearance.get(side) or {}).get("state")) or "cleared"
    iv_state = _st.iv_state_from_rank(_iv_rank(dealer))
    walls = _walls(dealer)
    chosen = _st.choose(side=side, iv_state=iv_state, clearance=state,
                        call_wall=walls["call"], put_wall=walls["put"],
                        spot=spot)

    plan.update({
        "action": chosen["action"],
        "structure": chosen["structure"],
        "rationale": chosen["rationale"],
        "short_strike_guidance": chosen["short_strike_guidance"],
        "dte_min": chosen["dte_min"], "dte_max": chosen["dte_max"],
        "entry_zone": _entry_zone(side, spot, dealer),
        "target": _target(swing, horizon),
    })
    stop, note = _stop(side, spot, analysis.get("momentum") or {}, walls)
    plan["stop"], plan["stop_note"] = stop, note

    if chosen["action"] == "none":
        plan["what_would_change_it"] = [
            r for r in ((clearance.get(side) or {}).get("reasons") or [])] or [
            "The tape clearing this side."]
    return plan


def _iv_rank(dealer):
    """IV rank if the analysis carries one. ``atm_iv`` is a LEVEL, not a rank,
    so it is deliberately not substituted — a 31% IV says nothing about whether
    31% is high for this name."""
    return dealer.get("iv_rank")


def _walls(dealer):
    """Walls only when the context says they are trustworthy. Off-hours they
    are withheld, and a withheld wall must not become a strike recommendation."""
    if not dealer.get("collected") or dealer.get("stale"):
        return {"call": None, "put": None}
    return {"call": _num(dealer.get("call_wall")),
            "put": _num(dealer.get("put_wall"))}


def _entry_zone(side, spot, dealer):
    flip = _num(dealer.get("flip")) if dealer.get("collected") else None
    if spot is None:
        return ""
    if side == "long":
        base = "pull back toward" + (f" the {flip:g} flip" if flip else " support")
        return f"{base}; avoid entering into the call wall"
    base = "rally into" + (f" the {flip:g} flip" if flip else " resistance")
    return f"{base}; avoid chasing a break lower"


def _stop(side, spot, momentum, walls):
    """``(level, note)``. Prefers a structural wall when it is TIGHTER than the
    ATR stop — the wall is where the cushion actually is — and returns None
    rather than inventing a level when neither input exists."""
    atr = _num(momentum.get("atr"))
    candidates = []
    if atr is not None and spot is not None:
        lvl = spot - ATR_STOP_MULTIPLE * atr if side == "long" \
            else spot + ATR_STOP_MULTIPLE * atr
        candidates.append((lvl, f"{ATR_STOP_MULTIPLE:g}x ATR"))
    wall = walls["put"] if side == "long" else walls["call"]
    if wall is not None:
        candidates.append((wall, "the %s wall" % ("put" if side == "long" else "call")))
    if not candidates:
        return None, ""
    # Tighter = nearer spot on the losing side.
    if spot is not None:
        candidates.sort(key=lambda c: abs(spot - c[0]))
    level, why = candidates[0]
    return level, f"{why} — whichever is tighter"


def _target(swing, horizon):
    exp = _num(swing.get("expected_fwd"))
    if exp is None:
        return ""
    return (f"{exp:+.1%} vs SPY over {horizon} trading days "
            f"(the band's calibrated mean, not a price objective)")


def _events_line(analysis, horizon):
    """The earnings line, including the case where we do not know.

    ``not_listed`` is the fail-open case: the vendor's coverage is patchy, so
    silence must not read as an all-clear."""
    coverage = analysis.get("earnings_coverage")
    days = _num((analysis.get("fundamentals") or {}).get("days_to_earnings"))
    if coverage == "not_listed":
        return ("Earnings: date unknown — this symbol is not in the calendar, "
                "so the earnings gate cannot speak for it")
    if coverage == "none_scheduled":
        return "Earnings: none scheduled in the calendar"
    if days is None:
        return ""
    d = int(days)
    inside = d <= horizon * 1.4          # trading-day horizon vs calendar days
    where = "INSIDE" if inside else "outside"
    return f"Earnings in {d} days — {where} the {horizon}-day horizon"
