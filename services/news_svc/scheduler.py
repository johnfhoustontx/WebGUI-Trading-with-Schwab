"""Three branches: the feeds on a calendar-aware cadence, the economic
calendar and its release watch every tick. No Schwab, no Claude.

Each pass launches the due branches as KEYED background tasks and returns to
its sleep (``launch``; ``options_svc.scheduler.launch_branches``' shape): a
branch still running from an earlier tick is skipped, never doubled, and a
slow branch delays only itself - a 60 s feed poll never holds the release
watch back. ``econ_calendar.refresh_now`` / ``watch_now`` decide per source
and per series whether anything is due, so most ticks fetch nothing.

The rest of this docstring is about the ``feeds`` branch.

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

The heartbeat is beaten once per pass, and a pass no longer waits on any
branch, so ``/health``'s tick age stays under about ``TICK_S`` even while a
poll runs; a hung branch shows instead as a WARNING once it has run past its
threshold (``launch``), and as that branch never relaunching.
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
# A branch still running this long is logged at WARNING (once per episode):
# the feed poll at twice its current interval, the calendar and watch here.
STUCK_FEEDS_FACTOR = 2
STUCK_CALENDAR_S = 600
MIN_INTERVAL_S = 60  # a bad or tiny config value never polls faster than this
MAX_INTERVAL_MIN = 10_080  # one week; anything larger is a bad value, not a cadence
# What the loop uses when working out the interval RAISES (a config bug, not a
# bad value - those fall back per key in ``_minutes``): the built-in RTH
# cadence, the shortest default, so a broken config degrades to polling a
# little too often rather than going quiet.
FALLBACK_INTERVAL_S = max(MIN_INTERVAL_S, int(nc.DEFAULTS["collector"]["rth_poll_min"]) * 60)

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


def _safe_repr(value) -> str:
    """``repr(value)``, or a placeholder when repr itself raises - an int over
    ``sys.get_int_max_str_digits()`` decimal digits (a TOML hex literal has no
    digit limit) makes repr raise ValueError."""
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__} too large to print>"


def _minutes(cfg, key) -> float:
    """``cfg["collector"][key]`` if it is a real, finite number of minutes in
    (0, one week], else the built-in default. A bool, a string (even "5"), a
    NaN, a non-positive or a huge value is refused rather than coerced."""
    default = nc.DEFAULTS["collector"][key]
    section = cfg.get("collector") if isinstance(cfg, dict) else None
    value = section.get(key) if isinstance(section, dict) else None
    if not _usable_minutes(value):
        shown = _safe_repr(value)
        if value is not None and (key, shown) not in _warned:
            _warned.add((key, shown))   # read every pass: warn once, not every 30 s
            _log.warning("news.toml: [collector] %s = %s is not a number of minutes "
                         "in (0, %s] - using %s", key, shown, MAX_INTERVAL_MIN, default)
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


async def _to_thread(fn, *args):
    """Run a blocking cycle on the default executor. A seam: tests swap in an
    inline call. Cancelling the awaiting task does not stop the thread - the
    executor finishes its call, so nothing is cut off mid-SQLite."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


async def _branch(name, fn, bus, on_end=None) -> None:
    """One branch's cycle. Never raises (but a cancellation): one branch's
    failure must not kill another, or the loop."""
    try:
        result = await _to_thread(fn, bus)
        if isinstance(result, dict) and result.get("skipped"):
            # The feed poll's skip is rare (a click raced it); the calendar
            # branches skip every tick while disabled, so theirs is debug.
            (_log.info if name == "poll" else _log.debug)(
                "news %s skipped: %s", name, result["skipped"])
    except asyncio.CancelledError:
        raise
    except Exception:  # never let one bad cycle kill the scheduler
        _log.exception("news %s cycle failed", name)
    finally:
        if on_end is not None:
            on_end()


def launch(running, key, make_coro, create_task, *, stuck_after_s=None,
           starts=None) -> bool:
    """Launch branch ``key`` as a background task unless its previous task is
    still running (``options_svc.scheduler.launch_branches``' shape): a slow
    branch can only ever delay ITSELF. The coroutine is built only when it will
    run, so a skip leaves nothing to close. Returns whether it launched.

    A skip is DEBUG - a branch outliving one tick is normal. With ``starts``
    (a dict this function keeps: key -> launch time + whether it warned) and
    ``stuck_after_s``, a skip of a task running that long or longer logs a
    WARNING instead, once per stuck episode: a hung branch never relaunches, and
    before this its only trace was a DEBUG line every 30 s."""
    prev = running.get(key)
    if prev is not None and not prev.done():
        entry = starts.get(key) if starts is not None else None
        if (entry is not None and stuck_after_s is not None and not entry["warned"]
                and _monotonic() - entry["t"] >= stuck_after_s):
            entry["warned"] = True
            _log.warning("news branch %r still running after %.0f s (threshold "
                         "%.0f s) - it is not relaunched until it ends",
                         key, _monotonic() - entry["t"], stuck_after_s)
        else:
            _log.debug("news branch %r still running; skipping this tick", key)
        return False
    running[key] = create_task(make_coro())
    if starts is not None:
        starts[key] = {"t": _monotonic(), "warned": False}
    return True


async def loop(bus) -> None:
    """Every ``TICK_S``: beat, then launch the due branches as keyed tasks and
    go straight back to sleep -

    * ``feeds``    - ``compute.poll_now`` on the v1 cadence (``poll_interval_s``),
      measured from when the last poll ENDED;
    * ``calendar`` - ``econ_calendar.refresh_now`` every tick; it fetches only
      the sources whose own ``refresh_min`` has passed, so most ticks read the
      store and publish an unchanged payload (``skip_unchanged``);
    * ``watch``    - ``econ_calendar.watch_now`` every tick; it fetches nothing
      unless a release has just passed and its value has not landed.

    A 60 s feed poll therefore never holds a release watch back a minute, and a
    branch still running from an earlier tick is skipped, never doubled (and
    WARNED about once it has run ``STUCK_FEEDS_FACTOR`` intervals, or
    ``STUCK_CALENDAR_S``). Cancelling the loop cancels and awaits every branch
    still running before the CancelledError propagates."""
    from services.news_svc import econ_calendar

    create_task = asyncio.get_running_loop().create_task
    running = {}
    starts = {}
    feeds = {"last_end": None}
    interval_failing = False

    def _feeds_ended():
        # Stamped when the poll ENDS, so a poll that overruns its interval is
        # still followed by a full interval of rest, never back-to-back.
        feeds["last_end"] = _monotonic()

    try:
        while True:
            _heartbeat.tick()
            try:
                interval = poll_interval_s(_utcnow(), nc.load())
                interval_failing = False
            except Exception:  # a config bug must never escape the loop
                if not interval_failing:   # once per failure run, not every 30 s
                    _log.exception("news poll interval could not be computed - using "
                                   "the built-in %s s", FALLBACK_INTERVAL_S)
                interval_failing = True
                interval = FALLBACK_INTERVAL_S
            last = feeds["last_end"]
            if last is None or _monotonic() - last >= interval:
                launch(running, "feeds",
                       lambda: _branch("poll", compute.poll_now, bus, _feeds_ended),
                       create_task, stuck_after_s=STUCK_FEEDS_FACTOR * interval,
                       starts=starts)
            launch(running, "calendar",
                   lambda: _branch("calendar", econ_calendar.refresh_now, bus),
                   create_task, stuck_after_s=STUCK_CALENDAR_S, starts=starts)
            launch(running, "watch",
                   lambda: _branch("watch", econ_calendar.watch_now, bus),
                   create_task, stuck_after_s=STUCK_CALENDAR_S, starts=starts)
            prev = running.get("feeds")
            last = feeds["last_end"]
            if last is None or (prev is not None and not prev.done()):
                remaining = TICK_S
            else:
                remaining = interval - (_monotonic() - last)
            await _sleep(max(1.0, min(TICK_S, remaining)))
    finally:
        # Shutdown: no branch task outlives the loop. Cancelling one that waits
        # on the executor returns at once - the thread finishes its call on its
        # own, so nothing is cut off mid-SQLite.
        tasks = [t for t in running.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
