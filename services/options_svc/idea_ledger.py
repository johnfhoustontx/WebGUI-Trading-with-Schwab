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

``mark_cycle`` is the idea book's own 5-minute pass (design section 3): settle an
expired row at its legs' intrinsic value, otherwise mark it from real option quotes
(``structure_marks.reprice``, the per-leg pricer the tracked structures use), keep
its worst point, and close it at its stop or target AT THE MARK'S VALUE. The user's
own Ledger passes never see an idea row, so nothing else ever closes one.

Imports nothing from ``compute`` (the file has a line-count ceiling).
"""
import logging
import sys
import uuid

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import paper_engine  # noqa: E402
import paper_lock as _paper_lock  # noqa: E402  (the one lock for both paper books)
import paper_trader  # noqa: E402
import signal_repricer  # noqa: E402
import structure_marks  # noqa: E402

from services import _degrade, _proxy  # noqa: E402
from services.options_svc import site_ideas  # noqa: E402
from shared import paper_limits  # noqa: E402
from shared.numeric import parsed_finite as _finite  # noqa: E402

log = logging.getLogger(__name__)

SOURCE = "trade_idea"
_MULT = 100.0                  # one contract = 100 shares; entry_credit is per share


def is_idea(trade) -> bool:
    """A Ledger row that came from a posted idea. The source column, and nothing else."""
    return bool(isinstance(trade, dict) and trade.get("source") == SOURCE)


def show_mark(trade) -> None:
    """Put an OPEN idea row's recorded mark on it as ``unrealized_pnl`` (the mark is
    per contract). No chain is read, so it holds outside the session too; no mark
    recorded yet leaves it blank."""
    mark = trade.get("last_mark_pnl")
    if is_idea(trade) and (trade.get("status") or "").upper() == "OPEN" and mark is not None:
        trade["unrealized_pnl"] = round(mark * int(trade.get("quantity") or 1), 2)


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


# ── the 5-minute cycle ──────────────────────────────────────────────────────
# Every figure below is PER CONTRACT (``structure_marks.pnl_of``'s basis), the same
# as ``target_pnl`` / ``stop_pnl``; only ``realized_pnl`` is scaled by quantity.

def _stamp(when) -> str:
    return when.isoformat(timespec="seconds")


def _lower_mae(row, pnl, stamp) -> None:
    """Move the worst point down to ``pnl`` when it is worse. It only ever falls, and
    stays at the booked 0.0 while no mark has gone negative."""
    worst = _finite(row.get("mae_pnl"))
    if pnl < (0.0 if worst is None else worst):
        row["mae_pnl"], row["mae_at"] = round(pnl, 2), stamp


def apply_mark(t, pnl, when):
    """``(row, "STOP" | "TARGET" | None)``: ``t`` with this mark recorded. PURE.

    The stop is checked FIRST, so a mark that satisfies both lines is a stop. Each
    line is inclusive, and a line that is not set (None) never fires."""
    row, stamp = dict(t), _stamp(when)
    row["last_mark_pnl"], row["last_mark_at"] = round(pnl, 2), stamp
    _lower_mae(row, pnl, stamp)
    stop, target = _finite(row.get("stop_pnl")), _finite(row.get("target_pnl"))
    if stop is not None and pnl <= -stop:
        return row, "STOP"
    if target is not None and pnl >= target:
        return row, "TARGET"
    return row, None


def close_row(t, value, reason, when) -> dict:
    """``t`` closed at ``value`` (the cost to close a share, ``structure_marks``'
    convention: NEGATIVE for a long position worth money). PURE.

    EXPIRED for ``reason == "EXPIRED"``, CLOSED otherwise. No closing commission is
    taken: the idea's entry cash already carries the round trip, open and close
    (``test_exit_commission_basis``)."""
    qty = int(t.get("quantity") or 1)
    pnl = structure_marks.pnl_of(t, value)
    if pnl is None:
        raise ValueError(f"no P&L for {t.get('trade_id')} at {value!r}")
    row = dict(t)
    row.update({
        "status": "EXPIRED" if reason == "EXPIRED" else "CLOSED",
        "exit_time": _stamp(when), "exit_reason": reason,
        "exit_debit": value, "exit_debit_total": round(value * _MULT * qty, 2),
        "realized_pnl": round(pnl * qty, 2),
    })
    return row


def _settle(t, client, today, now_ct) -> str:
    """Settle an expired row at its legs' intrinsic value on the shared settlement
    price. No price (or a two-expiry row, which has no intrinsic close) defers it."""
    sp = paper_engine.settlement_underlying(client, t.get("symbol"), t.get("expiration"),
                                            today)
    value = None if sp is None else structure_marks.expiry_value(
        {**t, "legs_json": t.get("legs")}, sp)
    pnl = None if value is None else structure_marks.pnl_of(t, value)
    if pnl is None:
        log.warning("idea EXPIRY DEFERRED %s %s %s: no settlement value",
                    t.get("trade_id"), t.get("symbol"), t.get("expiration"))
        return "skipped"
    row = dict(t)
    _lower_mae(row, pnl, _stamp(now_ct))
    paper_trader.update_trade(t["trade_id"], close_row(row, value, "EXPIRED", now_ct))
    return "settled"


def _mark(t, client, now_ct):
    """Mark one row and close it on its stop or target. ``"skipped"`` (the row left
    exactly as it was) when there is no mark: a mark that cannot be made is never a
    zero. Returns the counts it adds to."""
    rep = structure_marks.reprice({**t, "legs_json": t.get("legs")}, client, now_ct.date())
    value = (rep or {}).get("current_value")
    pnl = None if (rep or {}).get("error") else structure_marks.pnl_of(t, value)
    if pnl is None:
        return ("skipped",)
    row, hit = apply_mark(t, pnl, now_ct)
    if hit:
        row = close_row(row, value, hit, now_ct)
    paper_trader.update_trade(t["trade_id"], row)
    return ("marked", "closed") if hit else ("marked",)


@_paper_lock.serialized
def mark_cycle(now_ct, client=None, *, settle_only=False) -> dict:
    """One pass over the OPEN idea rows: ``{"marked", "closed", "settled", "skipped"}``.

    Per row, in order: settle it when ``paper_engine.should_settle`` says it is due;
    otherwise mark it (``settle_only`` stops here: the 15:05 CT settle slot calls it
    that way, after the close, when a quote is not a market). A row that raises is
    a degrade and counts as skipped; the pass goes on. Off (``[idea_trades]
    enabled = false``) the idea book is left exactly as it is."""
    counts = {"marked": 0, "closed": 0, "settled": 0, "skipped": 0}
    if not paper_limits.idea_trades()["enabled"]:
        return counts
    client = client if client is not None else _proxy.schwab_py_client
    today = now_ct.date().isoformat()
    rows = [t for t in paper_trader.get_all_trades()
            if is_idea(t) and (t.get("status") or "").upper() == "OPEN"]
    if rows and not settle_only:
        signal_repricer.clear_chain_cache()     # structure_marks fetches through it
    for t in rows:
        try:
            if paper_engine.should_settle(t.get("expiration"), today, now_ct):
                done = (_settle(t, client, today, now_ct),)
            elif settle_only:
                continue
            else:
                done = _mark(t, client, now_ct)
        except Exception:  # noqa: BLE001 - one bad row never costs the others
            _degrade.degraded("options.idea_ledger.mark_cycle", detail=t.get("trade_id"))
            done = ("skipped",)
        for key in done:
            counts[key] += 1
    if any(counts.values()):
        log.info("idea marks: %s", counts)
    return counts
