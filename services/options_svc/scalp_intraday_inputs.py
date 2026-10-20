"""The intraday calls' price reading: the measured core of each 15-minute slot.

For $SPX, SPY or QQQ at one slot (08:51 ... 13:51 CT) this reads the stored
5-minute bars up to the slot's entry and answers two things: which way the last
15 minutes went (``direction``), and which of six inputs - the move since the
open, the side of VWAP, the big ten's breadth, the futures' last 15 minutes,
$VIX's last 15 minutes (inverted) and each sector fund's last 15 minutes - agree
with it (design 2026-10-20-scalp-intraday-calls, section 4). Pure reads of the
store: no proxy, no write, no import from ``compute``. It is the 08:36
``scalp_inputs`` machinery pointed at a different clock, and it reuses that
module's agreement and alignment rather than copying them.

Rules the study and the live call both depend on (Central time throughout):

* ``entry_ts`` is ``scalp_config.intraday_entry_ts``: the slot floored to the
  5-minute boundary (08:51 -> 08:50). A bar is stored by its START, so the last
  bar the reading may see starts at ``entry_ts - 5 minutes`` and **nothing at or
  after ``entry_ts`` is ever read** - not into a number, not even into the
  cache's view of it (the cache holds the day, and every read slices it).
* "The last 15 minutes" is the three bars starting at ``entry_ts - 15``,
  ``- 10`` and ``- 5 minutes``: the last bar's close over the first bar's open.
  The same definition serves the symbol (the direction), the futures, $VIX, each
  sector fund and each big-ten member. Any of the three missing, or one that
  starts before the 08:30 open, leaves that series' move ``None``.
* ``entry_spot`` is the close of the bar starting ``entry_ts - 5 minutes``.
* The direction dead zone is ``[intraday] dead_zone_pct``; every other input
  uses the 08:36 per-input dead zones (``[direction] dead_zone_pct`` for moves),
  through ``scalp_inputs``'s own ``_move_sense``. A slot before 08:45 has no
  direction, nor has a day whose symbol has no 08:30 bar.
* Since-open is ``entry_spot`` over the open of the bar starting 08:30. The VWAP
  side is ``entry_spot`` over the volume-weighted typical price ``(h+l+c)/3`` of
  every bar from 08:30 to the entry bar, in percent; an index prints no volume,
  so $SPX reads SPY's own spot against SPY's own VWAP. A hole in those bars, or
  no volume, leaves it ``None``.
* Breadth is the share of the big ten (``shared.symbols.big10``) moving UP over
  their last 15 minutes, among those with a usable move; a member inside the
  dead zone counts in the denominator and agrees with neither side. Fewer than
  ``[confirm] breadth_min_members`` usable members is ``None``. ``agree`` reads
  the share of members matching the DIRECTION: at or above ``breadth_agree`` is
  +1, at or below ``1 - breadth_agree`` is -1, between is 0.
* ``vol_at_entry`` is the close of the vol symbol's bar starting
  ``entry_ts - 5 minutes`` over 100, ``None`` unless a finite positive number;
  an unusable vol symbol ($VXN for QQQ) falls back to $VIX, and the symbol read
  is ``vol_source`` (kept off ``inputs`` so that dict stays numeric).
* Absent means absent: ``None`` in ``inputs`` and in ``agree``, never agreeing or
  disagreeing. Nothing raises for missing data.
* ``cache`` is an optional dict a caller reuses across the slots of ONE day in
  ONE store (the study reads ~21 slots x 3 symbols a day): it holds each
  symbol's whole day of bars, so a cached reading equals an uncached one.
"""
import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional

from shared import market_calendar as _mc
from shared import scalp_config
from shared import symbols as _symbols
from shared.numeric import finite as _finite

from services.options_svc import scalp_store
from services.options_svc.scalp_inputs import (
    _VIX, _agreement, _bar_ok, _move_sense, _pct, alignment as _alignment)

_FIVE = 300      # seconds in a 5-minute bar
_INTERVAL = 5    # the store's interval column is in minutes
_LAST = 3 * _FIVE   # "the last 15 minutes" is three bars
#: An index prints no volume, so its VWAP side is read off the fund that tracks it.
_VWAP_PROXY = {"$SPX": "SPY"}
#: Float slack when a breadth share is compared with its (decimal) threshold.
_EPS = 1e-9


@dataclass
class Reading:
    """One symbol at one slot. ``agree`` values: +1 agrees with ``direction``,
    -1 disagrees, 0 dead zone, ``None`` absent."""
    day: _dt.date
    slot: str
    symbol: str
    direction: Optional[int]
    entry_spot: Optional[float]
    entry_ts: int
    inputs: dict = field(default_factory=dict)
    agree: dict = field(default_factory=dict)
    vol_at_entry: Optional[float] = None
    vol_source: Optional[str] = None


def alignment(r: Reading) -> Optional[float]:
    """Agreeing inputs over agreeing plus disagreeing (the 08:36 semantics);
    ``None`` with no direction or no input taking a side."""
    return _alignment(r)  # type: ignore[arg-type]  # duck-typed on direction, agree


# ── bars ─────────────────────────────────────────────────────────────────────
def _open_ts(day) -> int:
    return int(_mc.regular_open_on(day).timestamp())


def _session(symbol, day, entry_ts, db_path, cache) -> dict:
    """``{ts: Bar}`` for the symbol's 5-minute bars from the open up to, and
    NOT including, ``entry_ts``. With a cache the whole day is read once and
    sliced here, so a later bar can never reach a number."""
    start = _open_ts(day)
    if cache is None:
        got = scalp_store.bars(symbol, _INTERVAL, start, entry_ts, db_path=db_path)
    else:
        key = (symbol, day)
        if key not in cache:
            cache[key] = scalp_store.bars(symbol, _INTERVAL, start,
                                          start + 24 * 3600, db_path=db_path)
        got = [b for b in cache[key] if b.ts < entry_ts]
    return {b.ts: b for b in got}


def _move15(bars, entry_ts, open_ts):
    """The last 15 minutes' move in percent: the close of the bar starting
    ``entry_ts - 5 min`` over the open of the one starting ``entry_ts - 15 min``.
    ``None`` unless all three bars are there, usable and inside the session."""
    if entry_ts - _LAST < open_ts:
        return None
    trio = [bars.get(entry_ts - _LAST + i * _FIVE) for i in range(3)]
    if not all(_bar_ok(b) for b in trio):
        return None
    return _pct(trio[2].close, trio[0].open)


def _spot(bars, entry_ts):
    bar = bars.get(entry_ts - _FIVE)
    return float(bar.close) if _bar_ok(bar) else None


def _vwap_side(bars, entry_ts, open_ts):
    """The entry spot over the session VWAP, in percent; ``None`` for a hole in
    the bars, no volume, or no spot."""
    spot = _spot(bars, entry_ts)
    if spot is None:
        return None
    total = weighted = 0.0
    for start in range(open_ts, entry_ts, _FIVE):
        bar = bars.get(start)
        if not _bar_ok(bar):
            return None
        volume = _finite(bar.volume) or 0.0
        weighted += (bar.high + bar.low + bar.close) / 3.0 * volume
        total += volume
    if total <= 0:
        return None
    return _pct(spot, weighted / total)


# ── the pieces ───────────────────────────────────────────────────────────────
def _direction(bars, entry_ts, open_ts) -> Optional[int]:
    if not _bar_ok(bars.get(open_ts)):
        return None
    move = _move15(bars, entry_ts, open_ts)
    if move is None or move == 0 or abs(move) < scalp_config.intraday_dead_zone_pct():
        return None
    return 1 if move > 0 else -1


def _breadth(day, entry_ts, open_ts, db_path, cache):
    """``(share of usable members moving up, share moving down)``, or
    ``(None, None)`` with too few usable members."""
    up = down = usable = 0
    for sym in _symbols.big10():
        move = _move15(_session(sym, day, entry_ts, db_path, cache), entry_ts, open_ts)
        if move is None:
            continue
        usable += 1
        sense = _move_sense(move)
        up += sense == 1
        down += sense == -1
    if usable < scalp_config.breadth_min_members():
        return None, None
    return up / usable, down / usable


def _breadth_agree(direction, up, down) -> Optional[int]:
    if direction is None or up is None:
        return None
    share = up if direction > 0 else down
    cut = scalp_config.breadth_agree()
    return 1 if share >= cut - _EPS else -1 if share <= 1.0 - cut + _EPS else 0


def _vol(symbol, day, entry_ts, db_path, cache):
    """``(close / 100, symbol read)`` from the vol symbol's last bar before the
    entry, falling back to $VIX; ``(None, None)`` when neither is usable."""
    wanted = scalp_config.inputs_for(symbol)["vol"]
    for sym in dict.fromkeys(s for s in (wanted, _VIX) if s):
        bar = _session(sym, day, entry_ts, db_path, cache).get(entry_ts - _FIVE)
        close = _finite(bar.close) if bar is not None else None
        if close is not None and close > 0 and math.isfinite(close):
            return close / 100.0, sym
    return None, None


# ── the reading ──────────────────────────────────────────────────────────────
def reading(symbol: str, day: _dt.date, slot: str, db_path=None,
            cache: Optional[dict] = None) -> Reading:
    """The measured reading for ``symbol`` at ``slot`` ("HH:MM" Central) on
    ``day``. ``cache`` is the per-day bar cache described in the module docstring."""
    entry_ts = scalp_config.intraday_entry_ts(day, slot)
    open_ts = _open_ts(day)
    wiring = scalp_config.inputs_for(symbol)

    def series(sym):
        return _session(sym, day, entry_ts, db_path, cache)

    own = series(symbol)
    direction = _direction(own, entry_ts, open_ts)

    open_bar = own.get(open_ts)
    spot = _spot(own, entry_ts)
    inputs = {"since_open": (_pct(spot, open_bar.open)
                             if spot is not None and _bar_ok(open_bar) else None)}
    # The VWAP side reads the symbol's own bars unless it prints no volume.
    proxy = _VWAP_PROXY.get(symbol, symbol)
    inputs["vwap_side"] = _vwap_side(series(proxy), entry_ts, open_ts)
    fut = wiring["futures"]
    inputs["fut_15m"] = _move15(series(fut), entry_ts, open_ts) if fut else None
    # "vol_change" is the 08:36 name for the one input that is inverted.
    inputs["vol_change"] = _move15(series(_VIX), entry_ts, open_ts)
    for etf in wiring["etfs"]:
        inputs[f"etf:{etf}"] = _move15(series(etf), entry_ts, open_ts)

    agree = _agreement(inputs, direction)
    up, down = _breadth(day, entry_ts, open_ts, db_path, cache)
    inputs["breadth"] = up
    agree["breadth"] = _breadth_agree(direction, up, down)

    vol_at_entry, vol_source = _vol(symbol, day, entry_ts, db_path, cache)
    return Reading(day=day, slot=slot, symbol=symbol, direction=direction,
                   entry_spot=spot, entry_ts=entry_ts, inputs=inputs, agree=agree,
                   vol_at_entry=vol_at_entry, vol_source=vol_source)
