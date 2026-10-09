"""Writing one minute's hedging-flow rows.

Moved out of ``compute`` whole (``compute._write_hiro_rows`` is this function).
It imports nothing from ``compute``.
"""
import logging

from services import _degrade

log = logging.getLogger(__name__)


def write_rows(gh, conn, ts_min, rows) -> None:
    """Persist one minute's HIRO rows ``{symbol: row}``. Never raises.

    ONE batch (one commit) normally. ``insert_hiro_rows`` validates every row
    BEFORE writing any, so one bad row fails the whole batch — then fall back to
    one insert per symbol, so that row alone is lost, not the minute for every
    symbol.

    A DB-level failure part-way through ``executemany`` leaves the batch's
    earlier rows in an OPEN transaction, and the inserts ACCUMULATE: without a
    rollback the fallback would add those rows a second time and commit a
    double count. So the failed batch is rolled back before the fallback."""
    if not rows:
        return
    try:
        gh.insert_hiro_rows(conn, [(s, ts_min, r) for s, r in rows.items()])
        return
    except Exception:
        log.debug("hiro batch insert failed; falling back per symbol", exc_info=True)
    try:
        conn.rollback()
    except Exception:
        log.debug("hiro batch rollback failed", exc_info=True)
    for s, r in rows.items():
        try:
            gh.insert_hiro_row(conn, s, ts_min, r)
        except Exception:
            _degrade.degraded("options.hiro_insert", detail=s)
