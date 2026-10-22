"""Trend factors for the Opening Scalp's weighted chance: the slope of a moving
average and an RSI, each read on 5-minute, 15-minute and hourly bars (design
2026-10-22-scalp-weighted-probability, section 3).

The 15-minute and hourly bars are built here from the stored 5-minute bars of the
REGULAR session only, aligned to each day's open (15-minute: 08:30, 08:45 ...;
hourly: 08:30, 09:30 ... and the day's last, 14:30-15:00, is short). Only COMPLETE
bars count: every 5-minute part present, and the bar's END at or before the entry.
The EMA and RSI run on across previous sessions, so at 08:51 the hourly reading is
mostly yesterday's, which is what an hourly chart shows too.

Rules (Central time throughout; a bar is stored by its START):

* Nothing at or after ``entry_ts`` is ever read, into a number or into the cache.
  The store is asked for bars before the entry, and a bar whose end is after the
  entry is dropped by ``resample``.
* A gap is ``None``, never a neutral number made up here: the assembly (Task 4)
  turns an absent factor into 0 after scaling.
* Values are RAW and unsigned: slope in percent, RSI on 0..100.
* ``cache`` is an optional dict a caller reuses across the slots of a day. It holds
  only COMPLETED days' bars (each day before today), keyed by (store, symbol,
  day), so a cached reading equals an uncached one and a live slot never freezes
  the day it is still printing. Pure reads of the store: no proxy, no write, no
  import from ``compute``.
"""
import datetime as _dt
import math
from typing import Callable, Optional

from shared import market_calendar as _mc
from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_store

_FIVE = 300      # seconds in a 5-minute bar
_INTERVAL = 5    # the store's interval column is in minutes


# ── the arithmetic ───────────────────────────────────────────────────────────
def ema_slope_pct(closes, period: int, slope_bars: int) -> Optional[float]:
    """The change in percent of an EMA over its last ``slope_bars`` bars. The EMA
    is seeded with the SMA of the first ``period`` closes, then
    ``alpha = 2 / (period + 1)``. ``None`` with fewer than ``period + slope_bars``
    closes, or when the earlier EMA is not a positive finite number."""
    closes = list(closes)
    if period < 1 or slope_bars < 1 or len(closes) < period + slope_bars:
        return None
    alpha = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for c in closes[period:]:
        ema.append(alpha * c + (1.0 - alpha) * ema[-1])
    then, now = ema[-1 - slope_bars], ema[-1]
    if not (math.isfinite(then) and math.isfinite(now)) or then <= 0:
        return None
    return (now / then - 1.0) * 100.0


def rsi(closes, period: int) -> Optional[float]:
    """Wilder's RSI on 0..100: the first averages are the simple means of the
    first ``period`` changes, then ``(avg * (period - 1) + x) / period``. ``None``
    with fewer than ``period + 1`` closes; 100 with no losses, 50 with no change."""
    closes = list(closes)
    if period < 1 or len(closes) < period + 1:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(c, 0.0) for c in changes[:period]) / period
    loss = sum(max(-c, 0.0) for c in changes[:period]) / period
    for c in changes[period:]:
        gain = (gain * (period - 1) + max(c, 0.0)) / period
        loss = (loss * (period - 1) + max(-c, 0.0)) / period
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


# ── bars ─────────────────────────────────────────────────────────────────────
def _day_of(ts: int) -> _dt.date:
    return _dt.datetime.fromtimestamp(ts, _mc.CT).date()


def resample(bars5, minutes: int,
             open_ts_of_day: Callable[[_dt.date], int],
             close_ts_of_day: Callable[[_dt.date], int],
             before_ts: int) -> list:
    """``[(start_ts, close)]`` of the COMPLETE regular-session bars of ``minutes``,
    oldest first. Each day's bars start at its open and run ``minutes`` apart; the
    day's last may be short (it ends at the close). A bar counts when every one of
    its 5-minute parts is present with a finite close and its END is at or before
    ``before_ts``; a bar missing a part is dropped, never filled. A 5-minute part
    outside the regular session is ignored."""
    step = minutes * 60
    by_day: dict = {}
    for b in bars5:
        by_day.setdefault(_day_of(b.ts), {})[b.ts] = b
    out = []
    for day in sorted(by_day):
        parts = by_day[day]
        start, close_ts = open_ts_of_day(day), close_ts_of_day(day)
        while start < close_ts:
            end = min(start + step, close_ts)
            if end > before_ts:
                break
            wanted = range(start, end, _FIVE)
            got = [parts.get(t) for t in wanted]
            if got and all(p is not None and _finite(p.close) is not None for p in got):
                out.append((start, float(got[-1].close)))
            start += step
    return out


def _open_ts(day) -> int:
    return int(_mc.regular_open_on(day).timestamp())


def _close_ts(day) -> int:
    return int(_mc.regular_close_on(day).timestamp())


def _today() -> _dt.date:
    return _dt.datetime.now(_mc.CT).date()


def _day_bars(symbol, day, entry_ts, db_path, cache) -> list:
    """One session's 5-minute bars strictly before ``entry_ts``. A day before today
    is complete, so with a cache it is read whole once and sliced here."""
    start = _open_ts(day)
    completed = day < _today()
    if cache is None or not completed:
        return scalp_store.bars(symbol, _INTERVAL, start, entry_ts, db_path=db_path)
    key = (db_path, symbol, day)
    if key not in cache:
        cache[key] = scalp_store.bars(symbol, _INTERVAL, start, _close_ts(day),
                                      db_path=db_path)
    return [b for b in cache[key] if b.ts < entry_ts]


def _sessions(day, count) -> list:
    """``day`` and the ``count`` trading days before it, oldest first."""
    days = [day]
    for _ in range(count):
        days.append(_mc.prev_trading_day(days[-1]))
    return days[::-1]


# ── the factors ──────────────────────────────────────────────────────────────
def trend_factors(symbol: str, day: _dt.date, entry_ts: int, db_path=None,
                  cache: Optional[dict] = None) -> dict:
    """``{slope_<m>, rsi_<m>}`` for each bar size ``m`` in ``prob_timeframes()``,
    raw and unsigned (slope in percent, RSI 0..100), read from the regular
    sessions of the ``prob_lookback_sessions()`` days before ``day`` and ``day``'s
    own bars before ``entry_ts``. A reading that cannot be made is ``None``."""
    bars5 = []
    for d in _sessions(day, scalp_config.prob_lookback_sessions()):
        bars5.extend(_day_bars(symbol, d, entry_ts, db_path, cache))
    period = scalp_config.prob_ema_period()
    slope_bars = scalp_config.prob_slope_bars()
    rsi_period = scalp_config.prob_rsi_period()
    out: dict = {}
    for minutes in scalp_config.prob_timeframes():
        closes = [c for _ts, c in resample(bars5, minutes, _open_ts, _close_ts, entry_ts)]
        out[f"slope_{minutes}"] = ema_slope_pct(closes, period, slope_bars)
        out[f"rsi_{minutes}"] = rsi(closes, rsi_period)
    return out
