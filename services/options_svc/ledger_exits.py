"""The exit decision for the operator's own Paper Ledger trades.

Two passes ask it: the hourly manage pass (``compute.manage_ledger_trades``) on
a chain it fetched, and the live pass (``ledger_live``) on streamed quotes. Both
hand it the same mark shape (``signal_repricer.reprice_legs`` /
``reprice_swing``'s keys) and get the same answer for the same mark, which is
the point of having one module. It imports nothing from ``compute`` (that file
has a line ceiling). Design: docs/plans/2026-11-24-ledger-live-marks-design.md.

What a row gets:

* a DEBIT row (``direction == "DEBIT"``): ``signal_recommender.recommend``'s
  debit rules (dispatched there, before any credit rule) and the profit trail;
* a CREDIT row: the profit trail and the +50% target only, each behind its
  ``[protect]`` switch (``profit_trail``). No money, time or delta stop: those
  stay the operator's.

A self-managed row (an idea, the daily condor, a shadow idea) is never managed
here: ``idea_ledger.mark_cycle`` owns those.
"""
import datetime as _dt

from services.options_svc import idea_ledger as _ideas
from services.options_svc import profit_trail as _trail

#: The ledger structures this exit pass manages with the full debit rules. The
#: CREDIT rows get the trail and the target only (``profit_trail``).
DEBIT = "DEBIT"


def is_debit(trade) -> bool:
    return ((trade or {}).get("direction") or "").upper() == DEBIT


def managed(trade) -> bool:
    """Is ``trade`` one this pass may close? OPEN, the operator's own, and either
    a debit row or a credit row while the trail or the take-profit is on."""
    t = trade or {}
    if (t.get("status") or "").upper() != "OPEN" or _ideas.is_self_managed(t):
        return False
    return is_debit(t) or _trail.ledger_on()


def reprice(trade, client):
    """The hourly mark of ``trade``: its stored legs for a DEBIT row, its strikes
    for a CREDIT row (``reprice_swing``, as the page's own reprice does).

    ⚠ Until 2026-11-24 the hourly pass priced EVERY row with ``reprice_legs``. A
    credit row has no ``legs``, so it was marked at 0.00 with a P&L of 0.00 and
    neither the trail nor the target could ever fire on it."""
    import signal_repricer
    if is_debit(trade):
        return signal_repricer.reprice_legs(trade, client)
    return signal_repricer.reprice_swing(trade, client)


def exit_ctx(trade, rep, today):
    """The ``signal_recommender`` ctx for one DEBIT ledger row, or ``None``.

    ⚠ **Every dollar figure here is PER CONTRACT**, because that is what
    ``reprice_legs`` returns as ``unrealized_pnl`` and what
    ``signal_recommender._debit_target_base`` compares against. The row stores
    ``max_profit_total`` already multiplied by quantity, so a 3-lot handed
    straight through would have a target three times too far away.
    """
    qty = trade.get("quantity") or 1
    mp_total = trade.get("max_profit_total")
    try:
        max_profit = (float(mp_total) / int(qty)) if mp_total else None
    except (TypeError, ValueError, ZeroDivisionError):
        max_profit = None
    try:
        exp = _dt.date.fromisoformat(str(trade["expiration"]))
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "strategy": trade.get("strategy"),
        "direction": DEBIT,
        "entry_credit": trade.get("entry_credit"),
        "entry_debit": trade.get("entry_debit"),
        "max_profit": max_profit,
        "unrealized_pnl": rep.get("unrealized_pnl"),
        "dte_remaining": (exp - today).days,
        "dte_at_entry": trade.get("dte_at_entry"),
        "current_short_delta": None,
    }


def decide(trade, rep, today, protect):
    """The rule's answer for ``trade`` at mark ``rep``: a recommendation dict
    (``action``, ``code``, ``reason``) when it says close, else ``None``.

    ``protect`` is the trail's ctx keys (``profit_trail.ledger_inputs`` on the
    hourly pass, the same keys from a held best on the live one). A mark with no
    P&L or no value is ``None``: a zero would satisfy a zero-threshold rule and
    record a fabricated close price."""
    import signal_recommender
    rep = rep or {}
    if rep.get("error") or rep.get("unrealized_pnl") is None:
        return None
    if rep.get("current_value") is None:
        return None
    debit = is_debit(trade)
    ctx = exit_ctx(trade, rep, today) if debit else _trail.ledger_credit_ctx(trade, rep)
    if ctx is None:
        return None
    ctx.update(protect or {})
    rec = (signal_recommender.recommend(ctx) if debit
           else _trail.ledger_credit_rec(ctx))
    return None if rec.get("action") == "HOLD" else rec
