"""The captured score's window and row shape (Daily / Weekly / MTD).

Pure: no bus, no database, no engine import. Moved out of ``compute`` on
2026-10-04; ``compute`` re-exports every name here.
"""
import datetime as _dt
import math


def _num(x):
    """``float(x)`` if finite, else None (never raises)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# The score's inception. Nothing before this date is part of it: signal_outcomes
# reaches back to 2026-06-15, from a period whose captures predate the
# regular-hours recorder gate. The period windows never reach that far on their
# own - this constant is what guarantees they cannot.
CAPTURED_SCORE_EPOCH = _dt.date(2026, 9, 1)


def captured_score_window(today):
    """``(lo, hi)`` INCLUSIVE dates the score must read to fill Daily/Weekly/MTD.

    The earlier of the WEEK start and the MONTH start - **not** month-to-date.

    Month-to-date looks like the right bound, because MTD is the widest row in
    the table. It is wrong: on Thursday 1 October the WTD row starts Monday
    28 September, before the month began. Bounding the read at the month start
    would return no rows for 28-30 September and the weekly row would silently
    under-count on the first days of every month, with nothing on screen to say
    it had.

    Floored at ``CAPTURED_SCORE_EPOCH``. The width is also what bounds the
    payload - at most about five weeks of closes.
    """
    month_start = today.replace(day=1)
    week_start = today - _dt.timedelta(days=today.weekday())      # Monday
    return max(min(month_start, week_start), CAPTURED_SCORE_EPOCH), today


def captured_perf_rows(raw):
    """Outcome rows -> the shape ``eod.normalize_trades(kind="captured")`` reads.

    ``entry_credit_total`` is ``entry_credit * 100``: ONE contract, matching
    ``close_signal_manually``'s ``(entry_credit - exit_value) * 100``. A captured
    signal is never sized, so one contract is the only basis either number has -
    and they must share it, or the two figures on one row describe different
    position sizes.

    Total over a malformed row. This feeds a nightly report, and one bad row must
    not cost the whole section.
    """
    out = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        credit = _num(r.get("entry_credit"))
        close_ts = r.get("close_ts") or r.get("close_date")
        out.append({
            "symbol": r.get("symbol"),
            "strategy": r.get("strategy"),
            "trade_type": r.get("scanner_type"),
            # DERIVED, never hardcoded: an OPEN signal is a legitimate row
            # here (see ``captured_performance``), and it is the one that makes
            # the report's "Opened" column right.
            "status": "CLOSED" if close_ts else "OPEN",
            "first_seen_ts": r.get("first_seen_ts"),
            "close_ts": close_ts,
            "realized_pnl": _num(r.get("realized_pnl")),
            "entry_credit_total": (round(credit * 100.0, 2)
                                   if credit is not None else None),
            "exit_reason": r.get("exit_reason"),
        })
    return out
