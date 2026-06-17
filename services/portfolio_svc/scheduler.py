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
import threading

from services.portfolio_svc import compute, handlers
from services.portfolio_svc.state import STATE

PUBLISH_INTERVAL_SEC = 2      # publish at most this often when ticks are pending
REBUILD_INTERVAL_SEC = 600    # full rebuild (refresh baselines/sectors) ~10 min
RECONNECT_WAIT_SEC = 3.0      # pause before reconnecting a dropped stream


def rebuild_due(secs_since_rebuild, rebuild_requested, *,
                interval=REBUILD_INTERVAL_SEC):
    """Whether the loop should do a full rebuild this cycle (pure)."""
    return bool(rebuild_requested) or secs_since_rebuild >= interval


def apply_tick_to_state(state, tick) -> None:
    """Apply one streamed quote tick to the shared raw model; mark dirty."""
    with state.lock:
        state.raw_model = compute.apply_tick(state.raw_model, tick)
        state.dirty = True


def _stream_worker(state, stop) -> None:
    """Blocking SSE consumer: stream current holdings, apply each tick.

    Reconnects until ``stop`` is set. The symbol set is read from the current
    model once per (re)connect; a rebuild that changes holdings restarts this
    worker (the scheduler does that), so it always streams fresh symbols.
    Never raises out — a connection/parse error just pauses and reconnects.
    """
    while not stop.is_set():
        try:
            data = compute.make_data()
            symbols = compute.stream_symbols(state.raw_model)
        except Exception:  # noqa: BLE001
            symbols = []
        if not symbols:
            state.streaming = False
            stop.wait(RECONNECT_WAIT_SEC)
            continue
        state.streaming = True
        try:
            data.stream_quotes(symbols,
                               lambda t: apply_tick_to_state(state, t),
                               stop.is_set)
        except Exception:  # noqa: BLE001 — never let the stream loop die.
            pass
        state.streaming = False
        if stop.is_set():
            break
        stop.wait(RECONNECT_WAIT_SEC)


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
