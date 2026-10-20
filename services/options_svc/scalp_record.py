"""Records the intraday confirmation reading's raw inputs, one row per scalp
symbol per slot.

Every 15 minutes from 08:51 to 13:51 CT (``scalp_config.intraday_slots``) and
once at the 08:36 call, ``record`` reads the four views through
``scalp_confirm.sources`` (ONCE, shared by the symbols' rows) and saves, per
scalp symbol, the confirmation reading's inputs into ``scalp_store``'s
``intraday_readings``: the walls, flip and net GEX of the symbol and the big ten,
the net premium level, the windowed flow counts and the sentiment state.

It ships before any intraday call exists because the walls and net premium
history in ``gex_history.db`` is kept five sessions: what is not saved now is
gone, and the later study needs a morning's worth of every slot.

A row is written with or without a direction. ``directions`` / ``win_points``
(``{symbol: +1 | -1}`` / ``{symbol: points}``) are the 08:36 call's own; every
other slot passes neither, so ``components`` keeps its raw values and scores
nothing (an absent verdict is never a neutral one). A second record of the same
(day, slot) replaces the row: the scheduler's latch means it normally runs once.

Design: docs/plans/2026-10-20-scalp-intraday-calls-design.md section 6.
Imports nothing from ``compute``. Never raises.
"""
import datetime as _dt
from zoneinfo import ZoneInfo

from services import _degrade
from services.options_svc import scalp_confirm, scalp_store
from shared import scalp_config
from shared import symbols as _symbols

_CT = ZoneInfo("America/Chicago")


def _now(now):
    if not isinstance(now, _dt.datetime):
        return _dt.datetime.now(_CT)
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def _day(day) -> _dt.date:
    return day if isinstance(day, _dt.date) else _dt.date.fromisoformat(str(day))


def record(bus, day, slot, now=None, db_path=None, directions=None, win_points=None,
           entry_ts=None) -> int:
    """Save one ``intraday_readings`` row per scalp symbol for ``day``'s ``slot``;
    returns the rows written. ``entry_ts`` (epoch seconds) defaults to the slot's
    own 5-minute bar (``scalp_config.intraday_entry_ts``). One symbol failing
    never stops the others, and nothing here raises (a failure is counted as
    ``options.scalp_record``)."""
    try:
        day = _day(day)
        now = _now(now)
        if entry_ts is None:
            entry_ts = scalp_config.intraday_entry_ts(day, slot)
        symbols = list(scalp_config.symbols())
        everyone = list(dict.fromkeys(symbols + list(_symbols.big10())))
        src = scalp_confirm.sources(bus, now)
    except Exception:
        _degrade.degraded("options.scalp_record", detail="setup")
        return 0
    directions = directions if isinstance(directions, dict) else {}
    win_points = win_points if isinstance(win_points, dict) else {}
    written = 0
    for symbol in symbols:
        try:
            body = {"slot": slot, "entry_ts": int(entry_ts),
                    "recorded_at": now.isoformat(),
                    "direction": directions.get(symbol),
                    "components": scalp_confirm.components(
                        src, symbol, directions.get(symbol), win_points.get(symbol),
                        entry_ts),
                    "snapshot": scalp_confirm.snapshot(
                        src, everyone, entry_ts=entry_ts, symbol=symbol)}
            scalp_store.save_reading(day.isoformat(), slot, symbol, body,
                                     db_path=db_path)
            written += 1
        except Exception:
            _degrade.degraded("options.scalp_record", detail=f"{slot} {symbol}")
    return written


__all__ = ["record"]
