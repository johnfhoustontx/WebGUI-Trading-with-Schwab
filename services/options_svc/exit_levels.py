"""The Stop and Target columns of the three paper books.

Each OPEN row gets ``stop_level`` and ``target_level`` - POSITION P&L in dollars
(per contract x quantity), the same basis as the row's P&L column - plus
``stop_kind`` naming which stop is in force (``"money"``, ``"break-even"``,
``"trail"``, ``"debit"``, ``"idea"``). A level that does not apply is ``None``,
never 0: a Ledger credit trade with no trail armed has no stop at all.

The levels come from ``signal_recommender.exit_levels``, the rule engine's own
reading of the ctx each book's manage cycle builds, so a column can never show
a level the rules do not close at. Only P&L levels can be shown: the time stop,
the delta stop and the manage-at-DTE exit fire on time and delta.

Design: docs/plans/2026-11-15-profit-trail-design.md. Imports nothing from
``compute`` (that file has a line ceiling). Every function works in place and
never raises: a row it cannot read is left without the keys, and the page shows
a dash.
"""
import logging

from services import _degrade
from services.options_svc import commission, idea_ledger
from services.options_svc import profit_trail as _trail
from shared import trade_mgmt as _trade_mgmt
from shared.numeric import finite as _num

log = logging.getLogger(__name__)

KEYS = ("stop_level", "stop_kind", "target_level")


def _qty(row) -> int:
    try:
        q = int(row.get("quantity") or 1)
    except (TypeError, ValueError):
        return 1
    return q if q > 0 else 1


def _put(row, levels, qty) -> None:
    """Write ``levels`` (per contract) onto ``row`` as position dollars. A row
    with neither a stop nor a target gets no keys at all: it is published as
    built, and the page reads a missing level as a dash."""
    stop, target = (_num((levels or {}).get(k)) for k in ("stop", "target"))
    if stop is None and target is None:
        return
    row["stop_level"] = round(stop * qty, 2) if stop is not None else None
    row["target_level"] = round(target * qty, 2) if target is not None else None
    row["stop_kind"] = (levels or {}).get("stop_kind") if stop is not None else None


def _be_level(row) -> float:
    """Break-even plus the round-trip commissions for one contract, as the
    captured and Account cycles pass it (``compute._captured_be_level``)."""
    try:
        return commission.round_trip_commission(row.get("strategy") or row.get("type"),
                                                row.get("symbol"), 1)
    except Exception:  # noqa: BLE001 - a floor of 0 is the cycles' own fallback
        return 0.0


def _is_open(row) -> bool:
    return isinstance(row, dict) and (row.get("status") or "OPEN").upper() == "OPEN"


def _guarded(area, rows, one) -> None:
    for row in rows or ():
        if not _is_open(row):
            continue
        try:
            one(row)
        except Exception:  # noqa: BLE001 - one bad row must not blank the view
            _degrade.degraded(area, detail=row.get("trade_id") or row.get("signal_id")
                              or row.get("position_id"))


# ── the Paper Ledger ─────────────────────────────────────────────────────────

def _ledger_row(t) -> None:
    import signal_recommender
    qty = _qty(t)
    if idea_ledger.is_self_managed(t):
        _put(t, _idea_levels(t, qty), qty)
        return
    peak = t.get("mfe_pnl")
    if (t.get("direction") or "").upper() == "DEBIT":
        mp = _num(t.get("max_profit_total"))
        ctx = {"strategy": t.get("strategy"), "direction": "DEBIT",
               "entry_credit": t.get("entry_credit"), "entry_debit": t.get("entry_debit"),
               "max_profit": mp / qty if mp is not None else None,
               **signal_recommender.protect_inputs("ledger", peak, None)}
        _put(t, signal_recommender.exit_levels(ctx), qty)
        return
    ctx = {"strategy": t.get("strategy"), "entry_credit": t.get("entry_credit"),
           **signal_recommender.protect_inputs("ledger", peak, None)}
    _put(t, signal_recommender.exit_levels(
        ctx, protect_only_book=True,
        take_profit=_trade_mgmt.protect()["ledger_take_profit"]), qty)


def _idea_levels(t, qty) -> dict:
    """A self-managed row: its own stop and target lines (per contract,
    ``idea_ledger.levels``), and the trail above the stop once armed."""
    import signal_recommender
    stop = _num(t.get("stop_pnl"))
    mp = _num(t.get("max_profit_total"))
    ctx = {"strategy": t.get("strategy"), "direction": t.get("direction"),
           "entry_credit": t.get("entry_credit"), "entry_debit": t.get("entry_debit"),
           "max_profit": mp / qty if mp is not None else None,
           **signal_recommender.protect_inputs("ledger_self_managed", t.get("mfe_pnl"), None)}
    trail = signal_recommender.exit_levels(ctx, protect_only_book=True)["stop"]
    own = -stop if stop is not None else None
    if trail is not None and (own is None or trail > own):
        level, kind = trail, "trail"
    else:
        level, kind = own, "idea"
    return {"stop": level, "stop_kind": kind, "target": _num(t.get("target_pnl"))}


def ledger(trades) -> None:
    """Put the Stop and Target on each OPEN Ledger row, in place."""
    _guarded("options.ledger_levels", trades, _ledger_row)


# ── the paper Account ────────────────────────────────────────────────────────

def account(positions, lifecycle) -> None:
    """Put the Stop and Target on each OPEN Account position, in place.

    ``lifecycle`` is the Account's Settings switch, as the manage cycle reads it
    (``handlers.manual_paper_lifecycle_enabled``). The ctx is the one
    ``paper_engine.run_manage_cycle`` builds; ``mfe`` is position-level there
    too, so per contract it is divided by the quantity.
    """
    import signal_recommender

    def one(pos):
        qty = _qty(pos)
        mfe = _num(pos.get("mfe"))
        ctx = {"entry_credit": pos.get("entry_credit"), "strategy": pos.get("strategy")}
        if lifecycle:
            credit_total = (_num(pos.get("entry_credit")) or 0.0) * 100 * qty
            ctx.update(lifecycle=True, be_armed=bool(pos.get("be_armed")),
                       be_level=_be_level(pos),
                       trail_ladder=signal_recommender.ACTIVE_TRAIL_LADDER,
                       peak_pnl_frac=(mfe / credit_total
                                      if mfe is not None and credit_total > 0 else None))
        ctx.update(signal_recommender.protect_inputs(
            "account", mfe / qty if mfe is not None else None, None))
        _put(pos, signal_recommender.exit_levels(ctx), qty)

    _guarded("options.account_levels", positions, one)


# ── captured signals ─────────────────────────────────────────────────────────

def captured(signals) -> None:
    """Put the Stop and Target on each OPEN captured signal (one contract), in
    place. The best mark is the stored one (``signal_db.peak_unrealized``, one
    indexed aggregate a signal) taken with the row's latest mark."""
    import signal_db
    import signal_recommender

    rides = _trail.captured_rides()

    def one(r):
        peak = _trail.best(signal_db.peak_unrealized(r.get("signal_id")),
                           r.get("unrealized_pnl"))
        credit_total = (_num(r.get("entry_credit")) or 0.0) * 100
        ctx = {"entry_credit": r.get("entry_credit"),
               "strategy": r.get("strategy") or r.get("type"),
               "lifecycle": rides, "be_armed": bool(r.get("be_armed")),
               "be_level": _be_level(r),
               "trail_ladder": signal_recommender.ACTIVE_TRAIL_LADDER,
               "peak_pnl_frac": (peak / credit_total
                                 if peak is not None and credit_total > 0 else None),
               **signal_recommender.protect_inputs("captured", peak, None)}
        _put(r, signal_recommender.exit_levels(ctx), 1)

    _guarded("options.captured_levels", signals, one)
