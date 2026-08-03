"""Server-side portfolio scheduler (Task #21).

Builds the portfolio model, then consumes the proxy SSE quote stream on a
background thread — replacing the legacy GUI-side ``ui.timer`` stream polling.
Each tick is applied to the shared raw model in memory; this async loop
throttle-publishes the re-formatted display payload (at most every
``PUBLISH_INTERVAL_SEC`` when there are pending ticks) and triggers a full
rebuild periodically — or immediately when the GUI "Refresh" command set the
``rebuild_requested`` flag — restarting the stream on the fresh holdings.

The blocking ``loop`` + ``_stream_worker`` own their sleep/SSE cadence and run
the BLOCKING handlers in the default executor so the event loop stays responsive
(the same shape as the driver loop). The pure decision helper ``rebuild_due`` and
the tick application ``apply_tick_to_state`` are unit-tested; the loop is wired
into the scaffold as ``make_app(scheduler=loop)``.
"""
import asyncio
import datetime as _dt
import logging
import threading
from datetime import time as _time
from zoneinfo import ZoneInfo

from services.portfolio_svc import compute, handlers
from services.portfolio_svc.state import STATE
from shared.market_calendar import HOLIDAYS as _HOLIDAYS  # noqa: F401  (kept for callers/tests)
from shared.market_calendar import is_trading_day as _cal_is_trading_day

_log = logging.getLogger("portfolio_svc.scheduler")

PUBLISH_INTERVAL_SEC = 2      # publish at most this often when ticks are pending
REBUILD_INTERVAL_SEC = 600    # full rebuild (refresh baselines/sectors) ~10 min
# Off-hours/weekend/holiday throttle for the PERIODIC full rebuild: the model
# barely changes when the market is closed, so drop the round-the-clock ~10-min
# rebuild (each = proxy trade-sync + sector fan-out + baseline stats) to once an
# hour off-hours. The live tick republish + an explicit "Refresh" command still
# rebuild immediately (they bypass this gate). Mirrors options_svc's
# periodic_refresh_due off-hours-throttle pattern.
OFFHOURS_REBUILD_INTERVAL_SEC = 3600   # full rebuild at most hourly off-hours
RECONNECT_WAIT_SEC = 3.0      # base pause before reconnecting a dropped stream
RECONNECT_WAIT_MAX_SEC = 60.0  # cap on the exponential reconnect backoff

# ── Market-hours gate (mirrors options_svc/scheduler.py) ───────────────────
_CT = ZoneInfo("America/Chicago")
_RTH_START = (8, 30)    # 08:30 CT (09:30 ET open)
_RTH_END = (15, 0)      # 15:00 CT (16:00 ET close)
# NYSE full-closure holidays come from shared/market_calendar.py (derived, not a
# literal — no yearly edit). ``_HOLIDAYS`` stays bound as the local alias.


def _is_rth(now):
    """Whether ``now`` is within Mon–Fri regular trading hours (holidays excl.)."""
    if not _cal_is_trading_day(now.date()):
        return False
    return _time(*_RTH_START) <= now.time() <= _time(*_RTH_END)


def _market_now():
    return _dt.datetime.now(_CT)


def rebuild_due(secs_since_rebuild, rebuild_requested, *,
                interval=REBUILD_INTERVAL_SEC,
                offhours_interval=OFFHOURS_REBUILD_INTERVAL_SEC, now=None):
    """Whether the loop should do a full PERIODIC rebuild this cycle (pure).

    An explicit ``rebuild_requested`` (a user "Refresh" command) always forces a
    rebuild, bypassing the market gate. Otherwise the periodic rebuild fires at
    the normal ``interval`` during regular trading hours, but throttles to the
    longer ``offhours_interval`` off-hours/weekends/holidays (the model barely
    changes when the market is closed). ``now`` defaults to the CT market clock.
    """
    if rebuild_requested:
        return True
    now = now or _market_now()
    threshold = interval if _is_rth(now) else offhours_interval
    return secs_since_rebuild >= threshold


def reconnect_delay(consecutive_failures, *, base=RECONNECT_WAIT_SEC,
                    cap=RECONNECT_WAIT_MAX_SEC):
    """Capped exponential backoff for stream reconnects (pure).

    ``consecutive_failures`` is the count of failed connect attempts so far (0 =
    the first reconnect). The delay doubles each failure — ``base·2**n`` — capped
    at ``cap`` so a permanently-broken stream (e.g. auth-broken) backs off to a
    slow poll instead of hammering the proxy every ``base`` seconds all day. A
    successful connection resets ``consecutive_failures`` to 0 at the call site.
    """
    n = max(0, int(consecutive_failures))
    return min(base * (2 ** n), cap)


def apply_tick_to_state(state, tick) -> None:
    """Apply one streamed quote tick to the shared raw model; mark dirty."""
    with state.lock:
        state.raw_model = compute.apply_tick(state.raw_model, tick)
        state.dirty = True


def _stream_worker(state, stop) -> None:
    """Blocking SSE consumer: stream current holdings, apply each tick.

    Reconnects until ``stop`` is set, with CAPPED EXPONENTIAL BACKOFF and a
    per-failure warning log — so a permanently-broken stream (e.g. auth-broken)
    backs off to a slow poll and is diagnosable, instead of silently retrying
    every few seconds all day. The symbol set is read from the current model once
    per (re)connect; a rebuild that changes holdings restarts this worker (the
    scheduler does that), so it always streams fresh symbols. Never raises out — a
    connection/parse error is logged, then it pauses and reconnects.
    """
    failures = 0  # consecutive failed connect attempts (for the backoff)
    while not stop.is_set():
        try:
            data = compute.make_data()
            symbols = compute.stream_symbols(state.raw_model)
        except Exception as e:  # noqa: BLE001
            symbols = []
            _log.warning("portfolio stream setup failed (attempt %d): %s",
                         failures + 1, e)
        if not symbols:
            state.streaming = False
            failures += 1
            stop.wait(reconnect_delay(failures - 1))
            continue
        state.streaming = True
        connected_ok = True
        try:
            data.stream_quotes(symbols,
                               lambda t: apply_tick_to_state(state, t),
                               stop.is_set)
        except Exception as e:  # noqa: BLE001 — never let the stream loop die.
            connected_ok = False
            _log.warning("portfolio stream disconnected (attempt %d): %s",
                         failures + 1, e)
        state.streaming = False
        if stop.is_set():
            break
        if connected_ok:
            failures = 0  # a clean stream session resets the backoff
            stop.wait(reconnect_delay(0))
        else:
            failures += 1
            stop.wait(reconnect_delay(failures - 1))


async def loop(bus) -> None:
    """Build once, stream quotes, throttle-publish on ticks, periodic rebuild."""
    state = STATE
    loop_ = asyncio.get_event_loop()

    def _start_stream():
        stop = threading.Event()
        thread = threading.Thread(target=_stream_worker, args=(state, stop),
                                  daemon=True, name="portfolio-stream")
        thread.start()
        return stop

    # Initial build (defensive — proxy-down publishes an empty model).
    try:
        await loop_.run_in_executor(None, handlers.rebuild, bus, state)
    except Exception:  # noqa: BLE001
        pass
    stop = _start_stream()
    secs_since_rebuild = 0
    try:
        while True:
            await asyncio.sleep(PUBLISH_INTERVAL_SEC)
            secs_since_rebuild += PUBLISH_INTERVAL_SEC
            try:
                if rebuild_due(secs_since_rebuild, state.rebuild_requested):
                    state.rebuild_requested = False
                    secs_since_rebuild = 0
                    stop.set()  # stop the stream on the old holdings
                    await loop_.run_in_executor(None, handlers.rebuild, bus, state)
                    stop = _start_stream()
                elif state.dirty:
                    with state.lock:
                        state.dirty = False
                    await loop_.run_in_executor(None, handlers.publish_current,
                                                bus, state)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — never let the scheduler die.
                pass
    finally:
        stop.set()
