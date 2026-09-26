"""Poll the feeds on a calendar-aware cadence. No Schwab, no Claude.

The cadence comes from ``config/news.toml [collector]``: ``rth_poll_min``
during the regular session, ``offhours_poll_min`` on a trading day outside it,
``weekend_poll_min`` on a weekend or NYSE holiday (``shared.market_calendar``).

The loop wakes every ``TICK_S`` seconds rather than sleeping a whole interval,
and on each wake it beats the heartbeat, re-reads the config (``nc.load`` is
mtime-cached, so this is a stat) and polls when the interval since the last
poll ENDED has elapsed. So the first poll runs at start, an interval edit
applies without a restart, a session boundary (08:30 CT) is noticed within
``TICK_S`` rather than after up to an hour, and a poll that overruns its
interval is still followed by a full interval of rest.

The heartbeat is beaten once per pass, not from inside a poll, so ``/health``'s
tick age stays under about ``TICK_S`` BETWEEN polls (even on the 60-minute
weekend cadence) but grows for as long as a poll runs, and a slow poll reads
as an older tick.
"""
import asyncio
import datetime as dt
import logging
import math
import time
from zoneinfo import ZoneInfo

from services import _heartbeat
from services.news_svc import compute
from shared import market_calendar as mc
from shared import news_config as nc

_log = logging.getLogger("news_svc.scheduler")
_CT = ZoneInfo("America/Chicago")

TICK_S = 30          # how often the loop wakes (heartbeat, config re-read)
MIN_INTERVAL_S = 60  # a bad or tiny config value never polls faster than this
MAX_INTERVAL_MIN = 10_080  # one week; anything larger is a bad value, not a cadence

# Seams for tests (a fake clock and sleep; never patch asyncio.sleep globally).
_sleep = asyncio.sleep
_monotonic = time.monotonic
_warned = set()


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _usable_minutes(value) -> bool:
    """True for a real, finite number in (0, ``MAX_INTERVAL_MIN``]. Anything
    that RAISES while being checked (a 400-digit int makes ``math.isfinite``
    raise OverflowError) is unusable, never an exception out of the loop."""
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and 0 < value <= MAX_INTERVAL_MIN)
    except Exception:
        return False


def _minutes(cfg, key) -> float:
    """``cfg["collector"][key]`` if it is a real, finite number of minutes in
    (0, one week], else the built-in default. A bool, a string (even "5"), a
    NaN, a non-positive or a huge value is refused rather than coerced."""
    default = nc.DEFAULTS["collector"][key]
    section = cfg.get("collector") if isinstance(cfg, dict) else None
    value = section.get(key) if isinstance(section, dict) else None
    if not _usable_minutes(value):
        if value is not None and (key, repr(value)) not in _warned:
            _warned.add((key, repr(value)))   # read every pass: warn once, not every 30 s
            _log.warning("news.toml: [collector] %s = %r is not a number of minutes "
                         "in (0, %s] - using %s", key, value, MAX_INTERVAL_MIN, default)
        return float(default)
    return float(value)


def poll_interval_s(now, cfg) -> int:
    """Seconds between polls at ``now`` (naive is treated as CT). Never below
    ``MIN_INTERVAL_S``."""
    local = now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)
    if not mc.is_trading_day(local.date()):
        key = "weekend_poll_min"
    elif mc.is_regular_hours(local):
        key = "rth_poll_min"
    else:
        key = "offhours_poll_min"
    return max(MIN_INTERVAL_S, int(_minutes(cfg, key) * 60))


async def loop(bus) -> None:
    ev = asyncio.get_running_loop()
    last_poll = None
    while True:
        _heartbeat.tick()
        interval = poll_interval_s(_utcnow(), nc.load())
        if last_poll is None or _monotonic() - last_poll >= interval:
            try:
                result = await ev.run_in_executor(None, compute.poll_now, bus)
                if isinstance(result, dict) and result.get("skipped"):
                    _log.info("news poll skipped: %s", result["skipped"])
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad cycle kill the scheduler
                _log.exception("news poll cycle failed")
            # Stamped when the poll ENDS, so a poll that overruns its interval
            # is still followed by a full interval of rest, never back-to-back.
            last_poll = _monotonic()
        remaining = interval - (_monotonic() - last_poll)
        await _sleep(max(1.0, min(TICK_S, remaining)))
