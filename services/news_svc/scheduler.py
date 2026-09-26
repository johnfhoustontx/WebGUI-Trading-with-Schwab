"""Poll the feeds on a calendar-aware cadence. No Schwab, no Claude.

The cadence comes from ``config/news.toml [collector]``: ``rth_poll_min``
during the regular session, ``offhours_poll_min`` on a trading day outside it,
``weekend_poll_min`` on a weekend or NYSE holiday (``shared.market_calendar``).

The loop wakes every ``TICK_S`` seconds rather than sleeping a whole interval,
and on each wake it beats the heartbeat, re-reads the config (``nc.load`` is
mtime-cached, so this is a stat) and polls when the interval since the last
poll has elapsed. So the first poll runs at start, an interval edit applies
without a restart, a session boundary (08:30 CT) is noticed within ``TICK_S``
rather than after up to an hour, and ``/health``'s tick age stays under
``TICK_S`` on a healthy loop even while the weekend cadence is 60 minutes.
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

# Seams for tests (a fake clock and sleep; never patch asyncio.sleep globally).
_sleep = asyncio.sleep
_monotonic = time.monotonic
_warned = set()


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _minutes(cfg, key) -> float:
    """``cfg["collector"][key]`` if it is a real, finite, positive number of
    minutes, else the built-in default. A bool, a string (even "5"), a NaN or
    a non-positive value is refused rather than coerced."""
    default = nc.DEFAULTS["collector"][key]
    section = cfg.get("collector") if isinstance(cfg, dict) else None
    value = section.get(key) if isinstance(section, dict) else None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        if value is not None and (key, repr(value)) not in _warned:
            _warned.add((key, repr(value)))   # read every pass: warn once, not every 30 s
            _log.warning("news.toml: [collector] %s = %r is not a positive number "
                         "of minutes - using %s", key, value, default)
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
            last_poll = _monotonic()
            try:
                result = await ev.run_in_executor(None, compute.poll_now, bus)
                if isinstance(result, dict) and result.get("skipped"):
                    _log.info("news poll skipped: %s", result["skipped"])
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad cycle kill the scheduler
                _log.exception("news poll cycle failed")
        remaining = interval - (_monotonic() - last_poll)
        await _sleep(max(1.0, min(TICK_S, remaining)))
