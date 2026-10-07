"""TRACKED structures: the manage loop and the view.

The Market Scanner records structures that are not credit spreads - debit
spreads, straddles and strangles, butterflies and condors, calendars,
backspreads, the Directional tab's single legs - so each one gets a mark series
and an outcome. They are MEASURED, never traded: the paper Account refuses them
(``paper_engine._NO_AUTO_ENTRY_TYPES`` and the structure allow-list), no page
offers one to a paper book, and every other reader of ``signals.db`` excludes
them (``signal_db.TRACKED_TYPES``).

This module is the only thing that reads them back:

* ``manage_cycle`` settles what has expired, marks what is open, applies the
  exit rules to the mark and closes what they close. It writes outcomes to
  ``signals.db`` and never a broker order.
* ``view`` is the payload of ``cache:options:tracked``.

A sibling of ``compute`` that imports nothing from it (that file has a size
ceiling); the engines do the arithmetic (``structure_marks``).
"""
import datetime as _dt
import logging
import math
import sys
from zoneinfo import ZoneInfo

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

from services import _degrade, _proxy  # noqa: E402
from shared import market_calendar as _mc  # noqa: E402
from shared import trade_mgmt as _trade_mgmt  # noqa: E402

log = logging.getLogger("options_svc.tracked")

CT = ZoneInfo("America/Chicago")
MULTIPLIER = 100.0


def _real(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# ── the manage loop ─────────────────────────────────────────────────────────

def manage_cycle(now_ct=None, client=None, db_path=None) -> dict:
    """Settle, mark and exit the OPEN tracked structures.

    For each one, in this order:

    1. **Its front expiry has reached settlement** (``paper_engine.should_settle``:
       at or after 15:00 CT on the day, or any later day). A structure whose legs
       all expire together settles at intrinsic against
       ``paper_engine.settlement_underlying`` - the one rule the three paper books
       share - with reason ``EXPIRED``; no usable price DEFERS it to the next
       cycle. A calendar or diagonal cannot be valued that way (its back month
       still has time value), so one that reaches here unclosed is closed
       ``UNMARKABLE``, with no outcome.
    2. **Otherwise, inside the regular session**, it is marked from its legs. A
       failed mark is skipped: nothing is ever closed on a missing quote.
    3. The mark is stored, and when the exit rules say so the row is closed on it
       (``structure_marks.CLOSE_CODES``).

    One row's failure costs that row this cycle and nothing else. Returns
    ``{"closed": [...], "marked": n, "deferred": n}``.
    """
    import paper_engine
    import signal_db
    import signal_repricer
    import structure_marks as sm

    now = now_ct or _dt.datetime.now(CT)
    today = now.date().isoformat()
    client = client or _proxy.schwab_py_client
    db = {} if db_path is None else {"db_path": db_path}
    out = {"closed": [], "marked": 0, "deferred": 0}
    try:
        rows = signal_db.get_open_signals_with_latest_mark(tracked=True, **db)
    except Exception:  # noqa: BLE001
        _degrade.degraded("options.tracked_read")
        return out
    if not rows:
        return out
    try:
        signal_repricer.clear_chain_cache()
    except Exception:  # noqa: BLE001
        log.exception("clear_chain_cache before tracked manage degraded")
    in_session = _mc.is_regular_hours(now)
    close_from = _trade_mgmt.tracked()["front_expiry_close"]
    marks = []

    def _closed(row, reason, exit_val):
        out["closed"].append({"signal_id": row.get("signal_id"),
                              "symbol": row.get("symbol"),
                              "strategy": row.get("strategy"),
                              "reason": reason, "exit_val": exit_val})

    for row in rows:
        sid = row.get("signal_id")
        try:
            legs = sm.legs_of(row)
            if legs is None:
                continue                 # unreadable legs: left open, never guessed
            front = sm.expirations(legs)[0]
            if paper_engine.should_settle(front, today, now):
                if sm.is_two_expiry(legs):
                    signal_db.close_unmarkable(sid, close_ts=now, **db)
                    _closed(row, "UNMARKABLE", None)
                    continue
                spot = paper_engine.settlement_underlying(
                    client, row.get("symbol"), front, today)
                exit_val = None if spot is None else sm.expiry_value(row, spot)
                if exit_val is None:
                    out["deferred"] += 1
                    log.warning("tracked EXPIRY DEFERRED %s %s %s: no settlement "
                                "price", sid, row.get("symbol"), front)
                    continue
                signal_db.close_signal_manually(sid, exit_val, "EXPIRED", close_ts=now,
                                                settlement_underlying=spot, **db)
                _closed(row, "EXPIRED", exit_val)
                continue
            if not in_session:
                continue
            rep = sm.reprice(row, client, today=now.date())
            mark = sm.build_mark(row, rep, now, front_expiry_day=sm.front_expiry_due(
                row, now, close_from))
            if mark is None:
                continue
            marks.append(mark)
            code = mark.get("recommendation_code")
            if code in sm.CLOSE_CODES:
                signal_db.close_signal_manually(sid, rep["current_value"], code,
                                                close_ts=now, **db)
                _closed(row, code, rep["current_value"])
        except Exception:  # noqa: BLE001
            _degrade.degraded("options.tracked_manage", detail=str(sid))
    if marks:
        try:
            out["marked"] = signal_db.insert_marks(marks, **db)
        except Exception:  # noqa: BLE001
            _degrade.degraded("options.tracked_marks_write")
    return out


# ── the view ────────────────────────────────────────────────────────────────

_OPEN_KEYS = ("signal_id", "symbol", "strategy", "family", "scanner_type",
              "expiration", "dte_at_entry", "entry_credit", "entry_max_loss",
              "entry_max_profit", "entry_score", "entry_grade", "entry_underlying",
              "unbounded", "entry_spans_earnings", "first_seen_ts", "current_value",
              "unrealized_pnl", "current_underlying", "recommendation",
              "recommendation_reason", "last_mark_ts")


def r_multiple(realized_pnl, entry_max_loss):
    """P&L over the dollars at risk, or None. ``entry_max_loss`` is per share."""
    pnl, risk = _real(realized_pnl), _real(entry_max_loss)
    if pnl is None or risk is None or risk <= 0:
        return None
    return pnl / (risk * MULTIPLIER)


def stats(outcomes):
    """One row per structure over the closed tracked rows, most trades first.

    A row closed ``UNMARKABLE`` has no P&L and is counted only as that - never
    as a scratch. ``avg_r`` is the mean P&L over risk; for a structure whose loss
    is unbounded the risk is a margin estimate, which ``unbounded`` flags, and
    such a figure is not comparable with a defined-risk one.
    """
    groups = {}
    for o in outcomes or []:
        if not isinstance(o, dict) or not o.get("strategy"):
            continue
        g = groups.setdefault(o["strategy"], {
            "strategy": o["strategy"], "family": o.get("family"), "n": 0,
            "wins": 0, "total_pnl": 0.0, "_r": [], "unmarkable": 0,
            "unbounded": False, "through_earnings": 0})
        pnl = _real(o.get("realized_pnl"))
        if pnl is None:
            g["unmarkable"] += 1
            continue
        g["n"] += 1
        g["wins"] += pnl > 0
        g["total_pnl"] += pnl
        g["unbounded"] = g["unbounded"] or bool(o.get("unbounded"))
        g["through_earnings"] += bool(o.get("entry_spans_earnings"))
        r = r_multiple(pnl, o.get("entry_max_loss"))
        if r is not None:
            g["_r"].append(r)
    out = []
    for g in groups.values():
        rs = g.pop("_r")
        g["total_pnl"] = round(g["total_pnl"], 2)
        g["win_pct"] = round(100.0 * g["wins"] / g["n"], 1) if g["n"] else None
        g["avg_r"] = round(sum(rs) / len(rs), 3) if rs else None
        out.append(g)
    out.sort(key=lambda g: (-g["n"], g["strategy"]))
    return out


def view(now_ct=None, db_path=None) -> dict:
    """The ``cache:options:tracked`` payload: the open rows with their last mark
    and parsed legs, today's closes, and the results by structure. Defensive:
    a failed read yields empty lists, never a raise."""
    import signal_db
    import structure_marks as sm

    now = now_ct or _dt.datetime.now(CT)
    db = {} if db_path is None else {"db_path": db_path}
    try:
        open_rows = signal_db.get_open_signals_with_latest_mark(tracked=True, **db)
        outcomes = signal_db.get_tracked_outcomes(**db)
    except Exception:  # noqa: BLE001
        _degrade.degraded("options.tracked_view")
        open_rows, outcomes = [], []
    rows = []
    for r in open_rows:
        row = {k: r.get(k) for k in _OPEN_KEYS}
        row["legs"] = sm.legs_of(r) or []
        rows.append(row)
    today = now.date().isoformat()
    closed_today = [o for o in outcomes if o.get("close_date") == today]
    return {"date": today, "open": rows, "closed_today": closed_today,
            "stats": stats(outcomes),
            "counts": {"open": len(rows), "closed": len(outcomes),
                       "closed_today": len(closed_today)}}
