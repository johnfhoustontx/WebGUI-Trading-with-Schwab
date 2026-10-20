"""The Opening Scalp's morning inputs: the operator's premarket routine, measured.

For one symbol and one trading day this reads the stored bars (``scalp_store``)
and answers two things: which way the opening five minutes went (``direction``),
and which of the morning's inputs - overnight futures (Asia, Europe, the gap, the
position in the overnight range), the sector ETFs' premarket, $VIX, and how the
open bar closed within its own range - agree with that direction. Pure reads of
the store: no proxy, no write, no import from ``compute``.

Rules the study depends on (Central time throughout, ``ct`` from the fetcher):

* ``interval`` selects ONLY the open bar: the 5-minute bar starting 08:30, or the
  five 1-minute bars 08:30-08:34 aggregated (first open, highest high, lowest
  low, last close, summed volume; any of the five missing -> no open bar).
  Every premarket, overnight and prior-close input ALWAYS reads 5-minute bars, so
  the two intervals see identical inputs. ``entry_ts`` is the 08:35 boundary (the
  open bar's start + 5 minutes) and ``entry_spot`` the close of the last bar
  inside 08:30-08:35 - never a wall-clock time.
* "Prior close" is the close of the 5-minute bar starting 14:55 on the PRIOR
  TRADING DAY; "premarket" is 03:00 <= start < 08:30; "overnight" runs from the
  first bar after the prior trading day's 16:00 to 08:30, so on a Monday Asia
  begins with the Sunday-evening bar, not at Friday's close.
* Absent means absent: a missing input is ``None`` in ``inputs`` and ``None`` in
  ``agree``; it never counts as agreeing or disagreeing. ``alignment`` also
  ignores the dead-zone ``0``. A missing open bar leaves ``direction`` ``None``
  (and ``agree`` all ``None``) but the inputs are still filled where possible.
  Nothing raises for missing data.
* ``vol_at_entry`` is the close of the vol symbol's 5-minute bar starting 08:30
  over 100, ``None`` unless that is a finite positive number. A vol symbol that is
  not usable ($VXN for QQQ) falls back to $VIX; the symbol actually read is the
  Morning's ``vol_source`` (kept off ``inputs`` so that dict stays numeric).
* ``open_range_ratio`` (open bar range / mean open-bar range over the prior 20
  trading days that have one; fewer than five such days -> ``None``) is recorded
  in ``inputs`` and is never an agreement input: it has no direction.
"""
import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo

from shared import market_calendar as _mc
from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_store
from services.options_svc.scalp_store import Bar

_CT = ZoneInfo("America/Chicago")
_FIVE = 300      # seconds in a 5-minute bar
_INTERVAL = 5    # the store's interval column is in minutes
#: The volatility index every morning may fall back to.
_VIX = "$VIX"
#: Trading days back the open-range ratio's mean reads, and the fewest it accepts.
_RATIO_DAYS = 20
_RATIO_MIN_DAYS = 5


@dataclass
class Morning:
    """One symbol's morning. ``agree`` values: +1 agrees with ``direction``, -1
    disagrees, 0 dead zone, ``None`` absent."""
    day: _dt.date
    symbol: str
    direction: Optional[int]
    entry_spot: Optional[float]
    entry_ts: Optional[int]
    open_bar: Optional[Bar]
    inputs: dict = field(default_factory=dict)
    agree: dict = field(default_factory=dict)
    vol_at_entry: Optional[float] = None
    vol_source: Optional[str] = None


# ── time helpers ─────────────────────────────────────────────────────────────
def _at(day, hour, minute=0) -> int:
    """Epoch seconds of ``hour:minute`` Central on ``day`` (zoneinfo, so the
    daylight-saving change cannot move it)."""
    return int(_dt.datetime(day.year, day.month, day.day, hour, minute,
                            tzinfo=_CT).timestamp())


def _open_ts(day) -> int:
    return int(_mc.regular_open_on(day).timestamp())


def _five(symbol, start_ts, end_ts, db_path) -> list:
    return scalp_store.bars(symbol, _INTERVAL, start_ts, end_ts, db_path=db_path)


# ── numbers ──────────────────────────────────────────────────────────────────
def _pct(now, then):
    """``now / then - 1`` in percent; ``None`` unless both are usable and ``then``
    is positive."""
    now, then = _finite(now), _finite(then)
    if now is None or then is None or then <= 0:
        return None
    return (now / then - 1.0) * 100.0


def _prior_close(symbol, day, db_path):
    """The close of the 5-minute bar starting 14:55 on the prior trading day."""
    prev = _mc.prev_trading_day(day)
    got = _five(symbol, _at(prev, 14, 55), _at(prev, 14, 55) + 1, db_path)
    return _finite(got[0].close) if got else None


def _premarket(symbol, day, db_path) -> list:
    return _five(symbol, _at(day, 3), _open_ts(day), db_path)


# ── the open bar ─────────────────────────────────────────────────────────────
def _open_bar(symbol, day, interval, db_path) -> Optional[Bar]:
    """The bar covering 08:30-08:35 CT, or ``None`` when any of it is missing."""
    start = _open_ts(day)
    if interval == 5:
        got = scalp_store.bars(symbol, 5, start, start + 1, db_path=db_path)
        return got[0] if got else None
    got = scalp_store.bars(symbol, 1, start, start + _FIVE, db_path=db_path)
    if [b.ts for b in got] != [start + 60 * i for i in range(5)]:
        return None
    return Bar(start, got[0].open, max(b.high for b in got), min(b.low for b in got),
               got[-1].close, sum(b.volume or 0.0 for b in got))


def _bar_ok(bar) -> bool:
    return bar is not None and None not in (
        _finite(bar.open), _finite(bar.high), _finite(bar.low), _finite(bar.close))


def _open_range_ratio(symbol, day, interval, open_bar, db_path):
    """The open bar's range over the mean open-bar range of the prior 20 trading
    days that have one (fewer than five -> ``None``)."""
    if not _bar_ok(open_bar):
        return None
    ranges = []
    d = day
    for _ in range(_RATIO_DAYS):
        d = _mc.prev_trading_day(d)
        b = _open_bar(symbol, d, interval, db_path)
        if _bar_ok(b):
            ranges.append(b.high - b.low)
    if len(ranges) < _RATIO_MIN_DAYS:
        return None
    mean = sum(ranges) / len(ranges)
    return (open_bar.high - open_bar.low) / mean if mean > 0 else None


# ── the futures ──────────────────────────────────────────────────────────────
def _futures_inputs(fut, day, db_path):
    """Asia, Europe, the gap and the overnight range position, plus the premarket
    bars the $SPX open-break test reads. Each is ``None`` when its bars are not
    all there."""
    out = {"fut_asia": None, "fut_europe": None, "fut_gap": None,
           "fut_range_pos": None}
    if not fut:
        return out
    prev = _mc.prev_trading_day(day)
    night = _five(fut, _at(prev, 16), _open_ts(day), db_path)
    if not night:
        return out
    last = _finite(night[-1].close)
    asia = [b for b in night if b.ts < _at(day, 2)]
    asia_close = _finite(asia[-1].close) if asia else None
    out["fut_asia"] = _pct(asia_close, night[0].open)
    out["fut_europe"] = _pct(last, asia_close)
    out["fut_gap"] = _pct(last, _prior_close(fut, day, db_path))
    highs = [_finite(b.high) for b in night]
    lows = [_finite(b.low) for b in night]
    if last is not None and None not in highs and None not in lows:
        hi, lo = max(highs), min(lows)
        out["fut_range_pos"] = (last - lo) / (hi - lo) if hi > lo else None
    return out


def _broke(bar, pm_bars):
    """+1 when the bar's high is above the premarket high, -1 when its low is
    below the premarket low, 0 for neither (or for both: it names no side).
    ``None`` when either side is missing."""
    if not _bar_ok(bar) or not pm_bars:
        return None
    hi = max(b.high for b in pm_bars)
    lo = min(b.low for b in pm_bars)
    return float((1 if bar.high > hi else 0) - (1 if bar.low < lo else 0))


# ── agreement ────────────────────────────────────────────────────────────────
#: Recorded in ``inputs`` but never read as a percent move: the open-range ratio
#: has no direction, and the intraday breadth is a 0..1 share of names whose
#: verdict the intraday reading gives itself.
_NOT_A_MOVE = ("open_range_ratio", "breadth_up_share")


def _move_sense(pct, inverted=False):
    """+1 up, -1 down, 0 inside the dead zone; ``None`` for no reading."""
    if pct is None:
        return None
    band = scalp_config.dead_zone_pct()
    sense = 1 if pct > band else -1 if pct < -band else 0
    return -sense if inverted else sense


def _position_sense(pos):
    """+1 above the middle's dead zone, -1 below it, 0 inside."""
    if pos is None:
        return None
    band = scalp_config.dead_zone_frac()
    return 1 if pos > 0.5 + band else -1 if pos < 0.5 - band else 0


def _agreement(inputs, direction) -> dict:
    """Each input's sense multiplied by the direction: +1 agrees, -1 disagrees."""
    senses = {}
    for key, value in inputs.items():
        if key in _NOT_A_MOVE:
            continue
        if key in ("fut_range_pos", "open_close_loc"):
            senses[key] = _position_sense(value)
        elif key == "open_broke_pm":
            senses[key] = None if value is None else int(value)
        else:
            senses[key] = _move_sense(value, inverted=(key == "vol_change"))
    return {k: (None if s is None or direction is None else s * direction)
            for k, s in senses.items()}


def alignment(m: Morning) -> Optional[float]:
    """Agreeing inputs over agreeing plus disagreeing; ``None`` when the morning
    has no direction or no input took a side. Absent and dead-zone inputs are
    ignored."""
    if m.direction is None:
        return None
    votes = [v for v in m.agree.values() if v]
    if not votes:
        return None
    return sum(1 for v in votes if v > 0) / len(votes)


# ── the vol bar ──────────────────────────────────────────────────────────────
def _vol(symbol, day, db_path):
    """``(close / 100, symbol read)`` from the vol symbol's 08:30 bar, falling
    back to $VIX; ``(None, None)`` when neither gives a finite positive close."""
    start = _open_ts(day)
    wanted = scalp_config.inputs_for(symbol)["vol"]
    for sym in dict.fromkeys(s for s in (wanted, _VIX) if s):
        got = _five(sym, start, start + 1, db_path)
        close = _finite(got[0].close) if got else None
        if close is not None and close > 0 and math.isfinite(close):
            return close / 100.0, sym
    return None, None


# ── the morning ──────────────────────────────────────────────────────────────
def morning(symbol: str, day: _dt.date, interval: int = 5, db_path=None) -> Morning:
    """The measured morning for ``symbol`` on ``day``. ``interval`` (1 or 5)
    picks how the open bar is read; every other input reads 5-minute bars."""
    if interval not in (1, 5):
        raise ValueError(f"interval must be 1 or 5, not {interval!r}")
    wiring = scalp_config.inputs_for(symbol)
    fut = wiring["futures"]
    open_bar = _open_bar(symbol, day, interval, db_path)
    if not _bar_ok(open_bar):
        open_bar = None

    direction = None
    if open_bar is not None and open_bar.open > 0:
        move = (open_bar.close - open_bar.open) / open_bar.open * 100.0
        if abs(move) >= scalp_config.open_dead_zone_pct() and move != 0:
            direction = 1 if move > 0 else -1

    inputs = _futures_inputs(fut, day, db_path)
    for etf in wiring["etfs"]:
        last = _premarket(etf, day, db_path)
        inputs[f"etf:{etf}"] = (_pct(last[-1].close, _prior_close(etf, day, db_path))
                                if last else None)
    vix_pm = _premarket(_VIX, day, db_path)
    inputs["vol_change"] = (_pct(vix_pm[-1].close, _prior_close(_VIX, day, db_path))
                            if vix_pm else None)

    if open_bar is not None and open_bar.high > open_bar.low:
        inputs["open_close_loc"] = ((open_bar.close - open_bar.low)
                                    / (open_bar.high - open_bar.low))
    else:
        inputs["open_close_loc"] = None
    if symbol.startswith("$") and fut:
        # An index has no premarket: the futures' own open bar and premarket stand in.
        fut_open = _five(fut, _open_ts(day), _open_ts(day) + 1, db_path)
        inputs["open_broke_pm"] = _broke(fut_open[0] if fut_open else None,
                                         _premarket(fut, day, db_path))
    else:
        inputs["open_broke_pm"] = _broke(open_bar, _premarket(symbol, day, db_path))
    inputs["open_range_ratio"] = _open_range_ratio(symbol, day, interval,
                                                   open_bar, db_path)

    vol_at_entry, vol_source = _vol(symbol, day, db_path)
    return Morning(
        day=day, symbol=symbol, direction=direction,
        entry_spot=None if open_bar is None else float(open_bar.close),
        entry_ts=None if open_bar is None else open_bar.ts + _FIVE,
        open_bar=open_bar, inputs=inputs, agree=_agreement(inputs, direction),
        vol_at_entry=vol_at_entry, vol_source=vol_source)
