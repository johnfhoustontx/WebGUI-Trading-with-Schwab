"""Each posted hourly trade idea, booked into the Paper Ledger (trades.db).

The idea is the card exactly as posted: ``build_row`` turns the ``trade_idea.normalize``
dict into a Ledger row from the idea's own numbers (its entry cash with commission in,
its max loss and profit, its legs) and nothing is re-priced or re-derived from a chain.
The row is marked ``source = "trade_idea"`` - the one thing that identifies it.

``book`` is deliberately NOT ``compute.create_paper_trade``: it takes no risk-cap rung
and refuses no structure by name. The Ledger's caps and its tradeable-structure list
govern what a person sends to Paper; an idea is a posted call being measured, and a
call the caps would have refused is exactly the one the weekly review must still see.
It does hold ``BOOK_LOCK`` like every other writer of the book.

Imports nothing from ``compute`` (the file has a line-count ceiling).
"""
import logging
import sys
import uuid

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import paper_lock as _paper_lock  # noqa: E402  (the one lock for both paper books)
import paper_trader  # noqa: E402

from services.options_svc import site_ideas  # noqa: E402
from shared import paper_limits  # noqa: E402
from shared.numeric import parsed_finite as _finite  # noqa: E402

log = logging.getLogger(__name__)

SOURCE = "trade_idea"
_MULT = 100.0                  # one contract = 100 shares; entry_credit is per share


def is_idea(trade) -> bool:
    """A Ledger row that came from a posted idea. The source column, and nothing else."""
    return bool(isinstance(trade, dict) and trade.get("source") == SOURCE)


def levels(idea, cfg):
    """``(target_pnl, stop_pnl)`` in dollars per contract, positive; either may be None.

    The same numbers the public results use (``site_ideas.exit_levels``). A debit
    structure the rules give no stop gets ``cfg["debit_stop_frac"]`` of its debit, so
    every idea that cost money has a loss line to be measured against."""
    facts = site_ideas.entry_facts(idea, posted=None)
    target, stop = site_ideas.exit_levels(facts) if facts else (None, None)
    cash = _finite(idea.get("entry_cash"))
    if stop is None and cash is not None and cash < 0:
        stop = round(cfg["debit_stop_frac"] * -cash, 2)
    return target, stop


def _strikes(legs, strategy) -> dict:
    """``short_strike`` / ``long_strike`` / ``width`` (and the call side of an iron
    condor) read off the idea's legs. A credit spread's two legs; the condor's four."""
    def pick(side, kind):
        return next((lg["strike"] for lg in legs
                     if lg["side"] == side and lg["kind"] == kind), None)

    if strategy == "PCS":
        short_k, long_k = pick("short", "put"), pick("long", "put")
    elif strategy == "CCS":
        short_k, long_k = pick("short", "call"), pick("long", "call")
    elif strategy == "IC":
        short_k, long_k = pick("short", "put"), pick("long", "put")
        return {"short_strike": short_k, "long_strike": long_k,
                "width": None if short_k is None or long_k is None
                else round(abs(short_k - long_k), 2),
                "call_short": pick("short", "call"), "call_long": pick("long", "call")}
    else:
        return {}
    return {"short_strike": short_k, "long_strike": long_k,
            "width": None if short_k is None or long_k is None
            else round(abs(short_k - long_k), 2)}


def build_row(idea, now, cfg) -> dict:
    """The Ledger row for ``idea`` posted at ``now``. Pure: no store, no clock."""
    qty = int(cfg["quantity"])
    cash = float(idea["entry_cash"])                  # per contract, signed, commission in
    max_loss, max_profit = idea.get("max_loss"), idea.get("max_profit")
    stamp = now.isoformat(timespec="seconds")
    target, stop = levels(idea, cfg)
    debit = cash < 0
    row = {
        "trade_id": str(uuid.uuid4())[:8], "mode": "PAPER", "status": "OPEN",
        "symbol": idea["symbol"], "strategy": idea["type"],
        "trade_type": idea.get("trade_type") or "",
        "expiration": idea["expiration"], "dte_at_entry": idea.get("dte"),
        "quantity": qty, "legs": idea["legs"],
        "entry_credit": round(cash / _MULT, 4),
        "entry_credit_total": round(cash * qty, 2),
        "direction": "DEBIT" if debit else None,
        "entry_debit": round(-cash, 2) if debit else None,
        "entry_debit_total": round(-cash * qty, 2) if debit else None,
        "max_loss_per": None if max_loss is None else round(max_loss / _MULT, 4),
        "max_loss_total": None if max_loss is None else round(max_loss * qty, 2),
        "max_profit_total": None if max_profit is None else round(max_profit * qty, 2),
        "unbounded": max_profit is None,
        "breakeven": " / ".join(str(b) for b in (idea.get("breakevens") or [])),
        "underlying_at_entry": idea.get("spot"),
        "entry_time": stamp,
        "notes": "Trade idea",
        "source": SOURCE, "idea_id": idea.get("id"), "idea_posted": stamp,
        "idea_grade": idea.get("grade"), "idea_score": idea.get("score"),
        "idea_label": idea.get("label"),
        "target_pnl": target, "stop_pnl": stop, "mae_pnl": 0.0,
        "entry_source": "card",
        # paper_trader's event log and trade_tracker_client._payload subscript these
        # on every row; a debit has none (as in paper_trader._create_debit_trade)
        "short_strike": None, "long_strike": None, "width": None,
    }
    row.update(_strikes(idea["legs"], idea["type"]))
    return row


@_paper_lock.serialized
def book(idea, now) -> dict:
    """Book ``idea`` as an OPEN Ledger row, once per post.

    ``{"status": "booked" | "duplicate" | "disabled", "trade_id": str | None}``. The
    post is identified by ``(idea id, post time)`` across every row, open or closed,
    so a retry of the same post books nothing, while the same idea posted on a later
    day is a second trade. No cap is evaluated and no structure refused."""
    cfg = paper_limits.idea_trades()
    if not cfg["enabled"]:
        return {"status": "disabled", "trade_id": None}
    row = build_row(idea, now, cfg)
    for t in paper_trader.get_all_trades():
        if t.get("idea_id") == row["idea_id"] and t.get("idea_posted") == row["idea_posted"]:
            return {"status": "duplicate", "trade_id": None}
    paper_trader.add_trade(row)
    return {"status": "booked", "trade_id": row["trade_id"]}
