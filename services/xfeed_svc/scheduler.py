"""The xfeed loop: publish the views once, then run the poller's cycle on its interval.

Every ``poller.tick_s`` seconds the loop beats the heartbeat and decides whether to
start a cycle. A cycle is ``Poller.run_cycle`` (synchronous: it sleeps between
accounts) on the default executor as a background task, so the loop keeps beating
while one runs. A running cycle is never doubled, the interval is measured from when
the last cycle ENDED, and the interval is re-read from config every tick, so an edit
applies without a restart. Cancelling the loop cancels and awaits the cycle task.

A fault in the first publish, a cycle or the config read is counted
(``_degrade.degraded``) and never escapes the loop.
"""
import asyncio
import logging
import time

from services import _degrade, _heartbeat
from services.xfeed_svc import _trace, config, handlers, runtime

log = logging.getLogger("xfeed_svc.scheduler")

AREA = "xfeed.scheduler"

# Seams for tests (a fake clock and sleep; never patch asyncio.sleep globally).
_sleep = asyncio.sleep
_monotonic = time.monotonic


async def _to_thread(fn, *args):
    """Run a blocking call on the default executor. A seam: tests swap in an inline call."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def _note(doing, exc) -> None:
    _degrade.degraded(AREA, detail=_trace.where(doing, exc), exc_info=False)


async def loop(bus) -> None:
    state = {"last_end": None}
    cycle = None

    def on_change(rt):
        try:
            handlers.publish_all(bus, rt)
        except Exception as exc:
            _note("publishing after a poll change", exc)

    async def run_cycle(rt):
        try:
            await _to_thread(rt.poller.run_cycle, lambda: on_change(rt))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _note("running a poll cycle", exc)
        finally:
            state["last_end"] = _monotonic()

    try:
        try:
            await _to_thread(handlers.publish_all, bus, runtime.get())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _note("publishing the views at start", exc)
        while True:
            _heartbeat.tick()
            tick_s = 5
            try:
                tick_s = config.load()["poller"]["tick_s"]
                running = cycle is not None and not cycle.done()
                if not running:
                    rt = runtime.get()
                    last = state["last_end"]
                    if last is None or _monotonic() - last >= rt.poller.interval_s():
                        cycle = asyncio.get_running_loop().create_task(run_cycle(rt))
            except Exception as exc:
                _note("deciding whether to poll", exc)
            await _sleep(tick_s)
    finally:
        if cycle is not None and not cycle.done():
            cycle.cancel()
            await asyncio.gather(cycle, return_exceptions=True)
