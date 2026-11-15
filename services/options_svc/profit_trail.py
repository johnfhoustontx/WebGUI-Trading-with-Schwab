"""The profit trail's wiring into the three paper books.

The rule itself lives in ``signal_recommender`` (``_trail_level`` and the quiet
windows) and its settings in ``config/trade_mgmt.toml [protect]``. This module
holds what each book needs to hand the rule its inputs: the best profit a
position has reached, the mark's time, and, for captured signals, whether they
take the profit at the target or ride on past it.

Design and the replay behind its numbers:
docs/plans/2026-11-15-profit-trail-design.md. It imports nothing from
``compute`` (that file has a line ceiling).
"""
from shared import trade_mgmt as _trade_mgmt
from shared.numeric import finite as _num   # a real number or None; a bool is not a P&L


def best(*values):
    """The largest of the usable numbers given, or ``None`` when there are none.

    A peak is never invented: no stored best and no current mark is "no peak",
    which leaves the trail unarmed rather than armed at zero.
    """
    nums = [v for v in (_num(x) for x in values) if v is not None]
    return max(nums) if nums else None


def captured_rides() -> bool:
    """Does a captured signal ride on past its target (arm break-even) rather
    than take the profit there? ``[protect] captured_at_target``."""
    return _trade_mgmt.protect()["captured_at_target"] == "ride"


def captured_kwargs(rep, now, peak=None) -> dict:
    """The ``build_mark`` keywords for the trail on one captured signal.

    ``peak`` is the best STORED mark (``signal_db.peak_unrealized``); the mark
    being built is taken into it, so the trail arms on the mark that reaches its
    start rather than on the one after. The page's reprice passes no stored peak,
    so it shows the cycle's answer except where the trail has armed on an
    earlier mark.
    """
    import signal_recommender
    peak = best(peak, (rep or {}).get("unrealized_pnl"))
    return {"lifecycle": captured_rides(),
            "protect": signal_recommender.protect_inputs("captured", peak, now)}


# ── the Paper Ledger ─────────────────────────────────────────────────────────

def ledger_on() -> bool:
    """Does anything manage the operator's own Ledger CREDIT trades: the trail
    (``[protect] ledger``) or the take-profit (``ledger_take_profit``)?"""
    p = _trade_mgmt.protect()
    return bool(p["ledger"] or p["ledger_take_profit"])


def ledger_credit_rec(ctx) -> dict:
    """The exit decision for one of the operator's own CREDIT Ledger trades: the
    trail (when ``ctx`` carries its inputs) and, with ``ledger_take_profit``, the
    structure's target. No money, time or delta stop: those stay the operator's."""
    import signal_recommender
    return signal_recommender.protect_only(
        ctx, take_profit=_trade_mgmt.protect()["ledger_take_profit"])


def ledger_inputs(t, rep, now_ct) -> dict:
    """Record this mark as the trade's best when it is one, and return the ctx
    keys the trail needs (``{}`` when ``[protect] ledger`` is off).

    The best is written straight away, before any exit is decided: a trade
    closed on this very mark keeps the best it reached. ``t`` is updated IN PLACE
    too, because a close writes the whole row back from it
    (``paper_trader.update_trade``) and would otherwise put the old best back. A
    failed write costs the stored best one mark, never the exit, so it is a
    degrade and not a raise.
    """
    import signal_recommender
    import trades_db

    from services import _degrade
    if not ledger_on():
        return {}
    pnl = _num((rep or {}).get("unrealized_pnl"))
    if pnl is not None:
        try:
            conn = trades_db.connect()
            try:
                trades_db.raise_peak(conn, t["trade_id"], round(pnl, 2),
                                     now_ct.isoformat(timespec="seconds"))
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - see the docstring
            _degrade.degraded("options.ledger_peak", detail=t.get("trade_id"))
    peak = best(t.get("mfe_pnl"), pnl)
    if peak is not None and peak != _num(t.get("mfe_pnl")):
        t["mfe_pnl"] = round(peak, 2)
        t["mfe_at"] = now_ct.isoformat(timespec="seconds")
    return signal_recommender.protect_inputs("ledger", peak, now_ct)


def ledger_credit_ctx(t, rep) -> dict:
    """The rule ctx for one of the operator's own CREDIT Ledger trades, read by
    ``ledger_credit_rec``: the trail and the target, and no other exit."""
    return {"strategy": t.get("strategy"), "entry_credit": t.get("entry_credit"),
            "unrealized_pnl": (rep or {}).get("unrealized_pnl")}
