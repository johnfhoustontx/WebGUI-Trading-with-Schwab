"""trade_svc's scheduler: ONE branch, the daily watchlist dividend pull.

trade_svc was on-demand only until news v2 (design, "trade_svc gains a
scheduler"). The pull (``services/trade_svc/dividends.refresh``) runs once a
TRADING day (``shared.market_calendar``) at or after ``[calendar.dividends]
refresh_at`` in ``config/news.toml`` — 06:40 CT as shipped, outside the session
and off the quarter hours the options autoscan's chain bursts own.

The loop wakes every ``TICK_S`` seconds, beats the heartbeat, re-reads the
config (``news_config.load`` is mtime-cached, so a stat) and runs the pull when
:func:`dividends_due` says so. "Already ran today" is kept IN MEMORY once a
pull succeeds (or the store says the day already ran), so the ticks after the
run until midnight never open the store; with nothing in memory - a restart -
it is read from the STORE (``shared.dividends.last_run_day``), in the executor,
so a restart does not refetch. ``refresh`` checks the same day again itself,
under a lock that keeps a command pull from overlapping this one. The pull is
blocking I/O (~80 proxy calls), so it runs in the default executor. A failure
is logged and retried after ``RETRY_AFTER_FAIL_S``, never every tick.

``make_app``'s ``schedulers`` flag gates the whole loop, so dev stays quiet.
"""
import asyncio
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from services import _heartbeat
from shared import market_calendar as mc

_log = logging.getLogger("trade_svc.scheduler")
_CT = ZoneInfo("America/Chicago")

TICK_S = 60               # how often the loop wakes (heartbeat, config re-read)
RETRY_AFTER_FAIL_S = 900  # a failed pull is retried after this, not every tick

# Seams for tests (a fake clock and sleep; never patch asyncio.sleep globally).
_sleep = asyncio.sleep
_monotonic = time.monotonic
_warned = set()


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _warn_once(key, msg, *args):
    if key not in _warned:
        _warned.add(key)
        _log.warning(msg, *args)


def dividends_config() -> dict:
    """The validated ``[calendar.dividends]`` table (``enabled``, ``refresh_at``,
    ``lookback_days``, ...) from ``shared.news_config.dividends_config``.

    ⚠ Not ``calendar_config()["dividends"]``: that accessor returns the
    ``[calendar]`` SCALARS only, so the key is absent. A seam for tests."""
    from shared import news_config as nc
    return nc.dividends_config()


def _default_refresh_at() -> str:
    from shared import news_config as nc
    return nc.DEFAULTS["calendar"]["dividends"]["refresh_at"]


def _parse_hhmm(raw) -> dt.time:
    return dt.datetime.strptime(raw, "%H:%M").time()


def refresh_at_time(cfg) -> dt.time:
    """``cfg["refresh_at"]`` as a CT wall-clock time.

    The value comes from :func:`shared.news_config.dividends_config`, which
    already validated it as ``"HH:MM"`` (zero-padded), so it is parsed as is.
    Anything else is the shipped default from ``shared.news_config.DEFAULTS``
    (not a second literal here), with one WARNING per distinct bad value."""
    raw = cfg.get("refresh_at") if isinstance(cfg, dict) else None
    try:
        return _parse_hhmm(raw)
    except (TypeError, ValueError):
        default = _default_refresh_at()
        _warn_once(("refresh_at", repr(raw)),
                   "news.toml: [calendar.dividends] refresh_at = %r is not HH:MM - "
                   "using %s", raw, default)
        return _parse_hhmm(default)


def _local(now):
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def dividends_due(now, last_run, cfg) -> bool:
    """True when the pull should run at ``now`` (naive is CT): enabled, a
    trading day, at or after ``refresh_at``, and not yet run today."""
    # ``is not True``: an ``enabled`` that is not a real bool fails closed here
    # too, whatever the accessor did with it.
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return False
    local = _local(now)
    if not mc.is_trading_day(local.date()):
        return False
    if local.time() < refresh_at_time(cfg):
        return False
    return last_run != local.date().isoformat()


def _read_last_run():
    """The store's ``last_run_day`` (None when the store has never run)."""
    from shared import dividends as store
    conn = store.init_db()
    try:
        return store.last_run_day(conn)
    finally:
        store.close_db(conn)


def _refresh():
    from services.trade_svc import dividends
    return dividends.refresh()


async def loop(bus) -> None:
    ev = asyncio.get_running_loop()
    failed_at = None
    check_failing = False
    ran_day = None   # the CT day a pull last succeeded (or the store said ran)
    while True:
        _heartbeat.tick()
        try:
            cfg = dividends_config()
            now = _utcnow()
            today = _local(now).date().isoformat()
            # The store is opened only once the clock says a run could be due
            # AND memory does not already say today ran - off the event loop.
            due = dividends_due(now, ran_day, cfg)
            if due:
                last = await ev.run_in_executor(None, _read_last_run)
                if last == today:
                    ran_day = today
                due = dividends_due(now, last, cfg)
            check_failing = False
        except asyncio.CancelledError:
            raise
        except Exception:  # a config or store bug must never escape the loop
            if not check_failing:   # once per failure run, not every tick
                _log.exception("dividend schedule check failed")
            check_failing = True
            due = False
        if due and (failed_at is None or _monotonic() - failed_at >= RETRY_AFTER_FAIL_S):
            try:
                n = await ev.run_in_executor(None, _refresh)
                _log.info("dividend pull: %s symbols fetched", n)
                failed_at = None
                ran_day = today
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad pull kill the scheduler
                _log.exception("dividend pull failed - retrying in %s s",
                               RETRY_AFTER_FAIL_S)
                failed_at = _monotonic()
        await _sleep(TICK_S)
