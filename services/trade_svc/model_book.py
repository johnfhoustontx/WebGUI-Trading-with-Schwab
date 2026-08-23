"""The model's own paper book — what following the board would actually have done.

PURE lifecycle: candidates, marks, close rules, summary. The store and the
scheduler live in ``model_book_store`` / the handler; nothing here does I/O.

The journal answers "did the ranking correlate with what happened?". This
answers the question a person actually asks: "what would it have made?" — and
because it applies the Trade Plan's own stop, target and time stop, the answer
is about the strategy as described rather than an idealized version of it.

⚠ **It trades the UNDERLYING, not the P3 options structure — a deliberate
deviation from the phase plan.** The model predicts a 20-day excess return on
the STOCK. Wrapping that in a spread adds theta and vega P&L that has nothing to
do with whether the ranking works, so a book that lost money on correct calls
would be indistinguishable from one whose calls were wrong. The Trade Plan still
tells a human which structure to express it with; this measures the signal
underneath. Adding structure-level P&L is a follow-on, not a substitute.

Two rules keep the result from flattering itself:

**The market filter is honoured.** When the tape has not cleared a directional
short, the model's actual prediction is relative underperformance — so the book
opens a PAIR against SPY and stores the SPY entry. Without that leg, a
relative-only short measures the market's direction rather than the model's.

**Gated names are not traded.** The board shows them with their reasons and a
human would skip them; a book that took them would measure a strategy nobody
would run.
"""
import datetime as dt

# Anything the board could not evaluate stays out of the book: the point is to
# follow what the board actually says.
_TRADEABLE_STATUS = "ok"


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _add_trading_days(start, n):
    d, added = start, 0
    while added < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def wanted_symbols(board):
    """The ungated pool symbols this board would trade — WITHOUT needing prices.

    Exists because the tick has a chicken-and-egg problem: it cannot build
    candidates until it has quotes, and it should not quote the whole universe
    to find out which two names it needs. Splitting the selection from the
    pricing is what lets it quote exactly the symbols involved."""
    board = board or {}
    if board.get("status") != _TRADEABLE_STATUS or board.get("thin_cross_section"):
        return []
    out = []
    for row in (board.get("rows") or []):
        side = row.get("pool")
        if side not in ("long", "short"):
            continue
        if row.get("gated_long" if side == "long" else "gated_short"):
            continue
        if row.get("symbol"):
            out.append(row["symbol"])
    return out


def candidates(board, prices, today=None, horizon=None):
    """Positions to open from today's board. Never raises."""
    board = board or {}
    prices = prices or {}
    today = today or dt.date.today()
    if board.get("status") != _TRADEABLE_STATUS or board.get("thin_cross_section"):
        return []
    spy = _num(prices.get("SPY"))
    horizon = int(horizon or board.get("horizon_days") or 20)
    relative = board.get("short_expression") != "directional"
    stop_on = _add_trading_days(today, horizon).isoformat()

    out = []
    for row in (board.get("rows") or []):
        side = row.get("pool")
        if side not in ("long", "short"):
            continue
        if row.get("gated_long" if side == "long" else "gated_short"):
            continue
        entry = _num(prices.get(row.get("symbol")))
        if entry is None or entry <= 0:
            continue          # never open at a price we do not have
        out.append({
            "symbol": row.get("symbol"),
            "side": side,
            "entry": entry,
            "spy_entry": spy,
            # A long is judged on excess return too, but the tape has cleared
            # it, so it is expressed outright; only the SHORT side changes
            # expression with the market filter.
            "expression": "relative" if (side == "short" and relative)
                          else "directional",
            "composite": _num(row.get("composite")),
            "decile": row.get("decile"),
            "opened_on": today.isoformat(),
            "time_stop_on": stop_on,
            "status": "open",
        })
    return out


def mark(position, price, spy_price):
    """``position`` with ``pnl_pct`` filled in. None when unpriceable."""
    p = dict(position or {})
    entry, last = _num(p.get("entry")), _num(price)
    if entry is None or last is None or entry <= 0:
        p["pnl_pct"] = None
        return p
    raw = (last / entry - 1.0) * (1.0 if p.get("side") == "long" else -1.0)
    if p.get("expression") == "relative":
        spy_entry, spy_last = _num(p.get("spy_entry")), _num(spy_price)
        if spy_entry is None or spy_last is None or spy_entry <= 0:
            p["pnl_pct"] = None
            return p
        # The market leg is the OPPOSITE side of the pair, so its contribution
        # carries the same sign convention as the symbol leg.
        mkt = (spy_last / spy_entry - 1.0) * (1.0 if p.get("side") == "long" else -1.0)
        raw -= mkt
    p["pnl_pct"] = raw
    p["last"] = last
    return p


def close_reason(position, price, spy_price, today=None):
    """``"stop"`` / ``"target"`` / ``"time"``, or None to hold.

    The time stop is checked LAST but binds regardless of the level: the model
    predicts 20 trading days, and holding past that turns a measured edge into a
    hope the model never expressed."""
    p = position or {}
    today = today or dt.date.today()
    last = _num(price)
    side = p.get("side")
    if last is not None:
        stop, target = _num(p.get("stop")), _num(p.get("target"))
        if side == "long":
            if stop is not None and last <= stop:
                return "stop"
            if target is not None and last >= target:
                return "target"
        else:
            if stop is not None and last >= stop:
                return "stop"
            if target is not None and last <= target:
                return "target"
    on = p.get("time_stop_on")
    if on:
        try:
            if today >= dt.date.fromisoformat(str(on)[:10]):
                return "time"
        except ValueError:
            pass
    return None


def _side_stats(rows):
    pnls = [_num(r.get("pnl_pct")) for r in rows]
    pnls = [x for x in pnls if x is not None]
    return {
        "n": len(rows),
        "mean_pnl": (sum(pnls) / len(pnls)) if pnls else None,
        "hit_rate": (sum(1 for x in pnls if x > 0) / len(pnls)) if pnls else None,
        "total_pnl": sum(pnls) if pnls else None,
    }


def summary(positions):
    """Realized stats, split by side, plus the open count.

    Long and short are separate because a book carried entirely by its longs is
    a different product from one that works on both sides — and this model's
    short side is the half the market filter usually expresses relatively."""
    rows = [r for r in (positions or []) if isinstance(r, dict)]
    closed = [r for r in rows if r.get("status") == "closed"]
    return {
        "long": _side_stats([r for r in closed if r.get("side") == "long"]),
        "short": _side_stats([r for r in closed if r.get("side") == "short"]),
        "open": sum(1 for r in rows if r.get("status") == "open"),
        "closed": len(closed),
    }
