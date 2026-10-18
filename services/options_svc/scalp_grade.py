"""Grading the Opening Scalp's published calls once the day's bars are in.

The nightly ``scalp_bars`` branch runs ``top_up`` -> ``rebuild`` -> ``grade_pending``.
Grading answers one question the study cannot: how did what the operator was
actually SHOWN do? So it reads only ``scalp_store``'s ``calls`` - each call frozen
as published - and never the view, the study's rates or a recomputation:

* a symbol with no published roles that day has no grades, and a role is graded
  with its FROZEN structure, strikes, entry spot, entry time (the 08:35 boundary),
  ``highlight`` and ``avoid``;
* EVERY published role is graded, not only the highlighted one, flagged
  ``highlighted`` / ``avoided``, with the frozen ``p_base`` it was shown beside so
  the scorecard can average it;
* ``model_result`` is ``scalp_sim.simulate`` on the day's 5-minute bars at the
  morning's volatility reading x ``iv_scale()`` - the study's own method;
* ``chain_result`` is the same walk at the volatility IMPLIED by the saved 08:36
  chain's mid for the contract the role is long (a long option) or SHORT (a
  spread), read through ``scalp_call``'s own root-matching helpers. No slice, no
  quote, or no implied volatility: ``None``;
* a path with fewer bars than ``time_stop_min`` / 5 is not graded yet (nothing is
  written; the next nightly run tries again), and a short session is not graded
  at all, for the reason the study skips it (the model settles at 15:00 CT);
* grading is idempotent (``INSERT OR REPLACE`` per day, symbol and role).

The calibration is stored beside it: per day and symbol, the chain's
at-the-money volatility over the morning's volatility reading
(``iv_calibration``), the ratio ``iv_scale`` is meant to approximate. It lives in
its own table because ``rebuild`` deletes and re-inserts ``mornings``.

Imports nothing from ``compute``.
"""
import datetime as _dt
import logging

from services import _degrade
from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_call, scalp_fetch, scalp_sim, scalp_store, scalp_study

log = logging.getLogger("options_svc.scalp_grade")


def _date(day) -> _dt.date:
    return day if isinstance(day, _dt.date) else _dt.date.fromisoformat(str(day))


def _modelled_sigma(entry):
    """The morning's volatility reading x ``iv_scale()``, or None without one."""
    vol = _finite(entry.get("vol_at_entry"))
    return None if vol is None or vol <= 0 else vol * scalp_config.iv_scale()


def _chain_sigma(slice_, day, structure, strikes, spot, when):
    """The volatility implied by the slice's mid for the contract the role is
    long, or SHORT for a spread; None when the slice, the quote or the solver
    gives nothing."""
    legs = scalp_call.chain_legs(slice_, day, structure, strikes)
    if legs is None:
        return None
    short = legs[0]
    price = scalp_call._quote_mid(short)
    if price is None:
        return None
    oc = scalp_sim._calc()
    kind = "call" if structure in scalp_call._CALLS else "put"
    sigma = oc.implied_vol(price, spot, strikes[0],
                           oc.expiry_time_to_years(when, day), oc.RISK_FREE_RATE, kind)
    return _finite(sigma)


def _result(structure, spot, strikes, when, day, sigma, path):
    if sigma is None:
        return None
    return scalp_sim.simulate(structure, spot, strikes, when, day, sigma, path,
                              scalp_study.VERDICT_INTERVAL).result


def _calibration(day, symbol, entry):
    """``(day, symbol, atm_iv, vol_reading, ratio)`` from the frozen entry, or
    None when the chain's volatility or the morning's reading is missing."""
    chain = entry.get("chain")
    atm = _finite(chain.get("atm_iv")) if isinstance(chain, dict) else None
    vol = _finite(entry.get("vol_at_entry"))
    if atm is None or atm <= 0 or vol is None or vol <= 0:
        return None
    return (day.isoformat(), symbol, atm, vol, atm / vol)


def _grade_rows(day, symbol, entry, path, slice_) -> list:
    spot = _finite(entry.get("entry_spot"))
    stamp = _finite(entry.get("entry_ts"))
    when = scalp_fetch.ct(int(stamp))
    model_sigma = _modelled_sigma(entry)
    avoid = entry.get("avoid") or []
    rows = []
    for role, body in entry["roles"].items():
        structure = body.get("structure")
        raw = body.get("strikes")
        model = chain = None
        if structure and isinstance(raw, list) and raw:
            strikes = (float(raw[0]), float(raw[1]) if len(raw) > 1 else None)
            model = _result(structure, spot, strikes, when, day, model_sigma, path)
            chain = _result(structure, spot, strikes, when, day,
                            _chain_sigma(slice_, day, structure, strikes, spot, when),
                            path)
        rows.append((day.isoformat(), symbol, role, structure or "", model, chain,
                     int(entry.get("highlight") == role), int(role in avoid),
                     _finite(body.get("p_base"))))
    return rows


def grade_day(day, db_path=None) -> int:
    """Grade every published role of ``day`` (a date or ``YYYY-MM-DD``) whose
    five-minute path is complete; returns the grade rows written. A symbol whose
    path is short of bars is left for the next run, and one failing never stops
    the others."""
    day = _date(day)
    calls = {s: e for s, e in scalp_store.calls_on(day.isoformat(), db_path=db_path).items()
             if scalp_call._is_call(e)}
    if not calls or scalp_study._short(day, db_path):
        return 0
    grade_rows, calibration = [], []
    for symbol, entry in calls.items():
        try:
            spot, stamp = _finite(entry.get("entry_spot")), _finite(entry.get("entry_ts"))
            if spot is None or stamp is None:
                continue
            cal = _calibration(day, symbol, entry)
            if cal is not None:
                calibration.append(cal)
            path = scalp_study._path(symbol, scalp_study.VERDICT_INTERVAL, int(stamp),
                                     db_path)
            if path is None:
                continue
            grade_rows += _grade_rows(day, symbol, entry, path,
                                      scalp_store.chain_slice(day.isoformat(), symbol,
                                                              db_path=db_path))
        except Exception:
            _degrade.degraded("options.scalp_grade", detail=f"{day} {symbol}")
    conn = scalp_store.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO iv_calibration "
                "(day, symbol, atm_iv, vol_reading, ratio) VALUES (?, ?, ?, ?, ?)",
                calibration)
            conn.executemany(
                "INSERT OR REPLACE INTO grades (day, symbol, role, structure, "
                "model_result, chain_result, highlighted, avoided, p_base) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", grade_rows)
    finally:
        conn.close()
    return len(grade_rows)


def pending_days(db_path=None) -> list:
    """Published days (``YYYY-MM-DD``, oldest first) with a called role not yet
    graded."""
    conn = scalp_store.connect(db_path)
    try:
        days = [d for (d,) in conn.execute(
            "SELECT DISTINCT day FROM calls ORDER BY day").fetchall()]
        graded = {(d, s): n for d, s, n in conn.execute(
            "SELECT day, symbol, COUNT(*) FROM grades GROUP BY day, symbol").fetchall()}
    finally:
        conn.close()
    out = []
    for day in days:
        for symbol, entry in scalp_store.calls_on(day, db_path=db_path).items():
            if scalp_call._is_call(entry) and graded.get((day, symbol), 0) < len(entry["roles"]):
                out.append(day)
                break
    return out


def grade_pending(db_path=None) -> dict:
    """``grade_day`` for every published day not yet fully graded. Returns
    ``{"days": days tried, "rows": grade rows written}``; a day that raises is
    counted as a degrade and the rest still run."""
    tried = rows = 0
    for day in pending_days(db_path=db_path):
        tried += 1
        try:
            rows += grade_day(day, db_path=db_path)
        except Exception:
            _degrade.degraded("options.scalp_grade", detail=day)
    log.info("scalp grading: %d day(s) tried, %d grade row(s) written", tried, rows)
    return {"days": tried, "rows": rows}


__all__ = ["grade_day", "grade_pending", "pending_days"]
