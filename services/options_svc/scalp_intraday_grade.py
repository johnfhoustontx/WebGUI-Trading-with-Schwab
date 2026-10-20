"""Grading the intraday scalp calls once the day's bars are in.

The nightly ``scalp_bars`` branch runs ``scalp_grade.grade_pending`` (the 08:36
calls) and then this module's ``grade_pending``. It is ``scalp_grade`` pointed at
the 15-minute slot calls (design 2026-10-20-scalp-intraday-calls, section 8), and
it REUSES that module's helpers - ``call_rules``, the modelled and chain-implied
volatility, the walk - rather than copying them. It reads only
``intraday_calls_on``: each call frozen as published, never the view, the study's
rates or a recomputation.

* every role of every published slot call is graded with its FROZEN structure,
  strikes, entry spot, entry time (``entry_ts``, the slot's 5-minute boundary) and
  rules (``entry["rules"]``, never the evening's config), flagged ``highlighted`` /
  ``avoided``, with the role's frozen ``p_base`` and the slot's ``block``;
* the call's confirmation WORD (Confirms / Mixed / Contradicts) is stored beside
  the grade, so the scorecard can show whether calls that confirmed did better;
* ``model_result`` walks the day's 5-minute bars at the call's frozen
  ``vol_at_entry`` x ``iv_scale``; ``chain_result`` at the volatility IMPLIED by
  THAT slot's own saved chain slice at its own ``underlyingPrice`` (the contract
  the role is long, or the short leg of a spread); no slice, no quote or no
  implied volatility: ``None``;
* a path with fewer bars than the call's ``time_stop_min`` / 5 is not graded yet
  (nothing is written; the next nightly run tries again), and a short session is
  never graded (the model settles at 15:00 CT), so it is never counted either;
* grading is WRITE-ONCE per (day, slot, symbol, role): a role already graded is
  never recomputed and ``INSERT OR IGNORE`` backs that up.

An entry with no roles (a flat slot, a missed one) is not a call and is skipped.
Imports nothing from ``compute``.
"""
import datetime as _dt
import logging

from services import _degrade
from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_call, scalp_grade, scalp_store, scalp_study

log = logging.getLogger("options_svc.scalp_intraday_grade")


def _date(day) -> _dt.date:
    return day if isinstance(day, _dt.date) else _dt.date.fromisoformat(str(day))


def _calls(day, db_path) -> dict:
    """``{(slot, symbol): entry}`` of the real (frozen, role-bearing) calls."""
    return {k: e for k, e in scalp_store.intraday_calls_on(day.isoformat(),
                                                           db_path=db_path).items()
            if scalp_call._is_call(e)}


def _graded_roles(day, db_path) -> dict:
    """``{(slot, symbol): {role, ...}}`` already graded for ``day``."""
    conn = scalp_store.connect(db_path)
    try:
        found = conn.execute("SELECT slot, symbol, role FROM intraday_grades "
                             "WHERE day = ?", (day.isoformat(),)).fetchall()
    finally:
        conn.close()
    out = {}
    for slot, symbol, role in found:
        out.setdefault((slot, symbol), set()).add(role)
    return out


def _word(entry):
    conf = entry.get("confirmation")
    word = conf.get("word") if isinstance(conf, dict) else None
    return word if isinstance(word, str) and word else None


def _rows(day, slot, symbol, entry, path, slice_, done) -> list:
    """The grade rows of one slot call: ``scalp_grade``'s per-role rows, each
    widened with the slot, the confirmation word and the block."""
    block = entry.get("block") or scalp_config.intraday_block(slot) or ""
    word = _word(entry)
    out = []
    for (d, sym, role, structure, model, chain, high, avoid,
         p_base) in scalp_grade._grade_rows(day, symbol, entry, path, slice_, done):
        out.append((d, slot, sym, role, structure, model, chain, high, avoid, word,
                    p_base, block))
    return out


def grade_day(day, db_path=None) -> int:
    """Grade every published role of every intraday slot call of ``day`` (a date or
    ``YYYY-MM-DD``) whose five-minute path is complete; returns the grade rows
    written. A call short of bars is left for the next run, and one failing never
    stops the others. Grades are WRITE-ONCE: a role already graded is skipped."""
    day = _date(day)
    calls = _calls(day, db_path)
    if not calls or scalp_study._short(day, db_path):
        return 0
    graded = _graded_roles(day, db_path)
    rows = []
    for (slot, symbol), entry in sorted(calls.items()):
        try:
            done = graded.get((slot, symbol), set())
            if set(entry["roles"]) <= done:
                continue
            spot, stamp = _finite(entry.get("entry_spot")), _finite(entry.get("entry_ts"))
            if spot is None or stamp is None:
                continue
            path = scalp_study._path(symbol, scalp_study.VERDICT_INTERVAL, int(stamp),
                                     db_path,
                                     time_stop_min=scalp_grade.call_rules(entry)["time_stop_min"])
            if path is None:
                continue
            rows += _rows(day, slot, symbol, entry, path,
                          scalp_store.intraday_slice(day.isoformat(), slot, symbol,
                                                     db_path=db_path), done)
        except Exception:
            _degrade.degraded("options.scalp_intraday_grade",
                              detail=f"{day} {slot} {symbol}")
    conn = scalp_store.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO intraday_grades (day, slot, symbol, role, "
                "structure, model_result, chain_result, highlighted, avoided, "
                "confirm_word, p_base, block) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows)
    finally:
        conn.close()
    return len(rows)


def pending_days(db_path=None) -> list:
    """Days (``YYYY-MM-DD``, oldest first) with an intraday call role not yet
    graded."""
    conn = scalp_store.connect(db_path)
    try:
        days = [d for (d,) in conn.execute(
            "SELECT DISTINCT day FROM intraday_calls ORDER BY day").fetchall()]
        graded = {(d, sl, s): n for d, sl, s, n in conn.execute(
            "SELECT day, slot, symbol, COUNT(*) FROM intraday_grades "
            "GROUP BY day, slot, symbol").fetchall()}
    finally:
        conn.close()
    out = []
    for day in days:
        for (slot, symbol), entry in scalp_store.intraday_calls_on(day, db_path=db_path).items():
            if scalp_call._is_call(entry) and graded.get((day, slot, symbol), 0) < len(entry["roles"]):
                out.append(day)
                break
    return out


def grade_pending(db_path=None) -> dict:
    """``grade_day`` for every day with an intraday call not yet fully graded.
    Returns ``{"days": days tried, "rows": grade rows written}``; a day that raises
    is counted as a degrade and the rest still run."""
    tried = rows = 0
    for day in pending_days(db_path=db_path):
        tried += 1
        try:
            rows += grade_day(day, db_path=db_path)
        except Exception:
            _degrade.degraded("options.scalp_intraday_grade", detail=day)
    log.info("scalp intraday grading: %d day(s) tried, %d grade row(s) written", tried, rows)
    return {"days": tried, "rows": rows}


__all__ = ["grade_day", "grade_pending", "pending_days"]
