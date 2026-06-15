"""Shared FastAPI scaffold for domain services.

Every domain service (sentiment, options, trade, ...) is just *engines + a
scheduler + a command-handler*; the boilerplate of standing up a FastAPI app,
a ``/health`` probe, and resilient background tasks lives here.

``make_app`` builds an app that, on startup, spawns up to two background
asyncio tasks:

* a **scheduler** coroutine (``async def scheduler(bus)``) — run once; it is
  expected to contain its own loop+sleep. If it raises, the exception is
  logged and swallowed and the task simply ends.
* a **command-consumer** loop — repeatedly drains the ``cmd:{domain}`` stream
  (consumer group ``{domain}-svc``) and dispatches each command to
  ``command_handler(bus, command)`` then acks it. A bad command can never kill
  the loop.

Both tasks are cancelled cleanly on shutdown. The blocking
``bus.consume_commands`` call runs in a thread-pool executor so it never
blocks the event loop; ``asyncio.CancelledError`` still breaks the loop, so a
pending executor call delays shutdown by at most ``poll_block_ms``.
"""
import asyncio
import inspect
import logging
import pathlib
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Repo root on sys.path so ``shared`` (PEP 420 namespace pkg) is importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.bus import Bus  # noqa: E402

log = logging.getLogger(__name__)


async def _run_scheduler(scheduler, bus) -> None:
    """Run the scheduler coroutine once; log+swallow any exception."""
    try:
        await scheduler(bus)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — must never crash the app.
        log.exception("scheduler task failed")


async def _consume_loop(domain, bus, command_handler, poll_block_ms) -> None:
    """Drain ``cmd:{domain}`` forever, dispatching each command to the handler.

    Each iteration is wrapped so a bad command (or a transient bus error) can
    never kill the loop. The blocking ``consume_commands`` runs in the default
    executor to keep the event loop responsive.
    """
    stream = f"cmd:{domain}"
    group = f"{domain}-svc"
    loop = asyncio.get_event_loop()
    while True:
        try:
            batch = await loop.run_in_executor(
                None,
                lambda: bus.consume_commands(
                    stream, group=group, consumer="c1", block_ms=poll_block_ms
                ),
            )
            for msg_id, command in batch:
                try:
                    result = command_handler(bus, command)
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — one bad command must not kill the loop.
                    log.exception("command handler failed for %s", msg_id)
                finally:
                    try:
                        bus.ack(stream, group, msg_id)
                    except Exception:  # noqa: BLE001
                        log.exception("ack failed for %s", msg_id)
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001 — keep looping on any transient error.
            log.exception("consume loop iteration failed")


def make_app(
    domain: str,
    *,
    scheduler=None,
    command_handler=None,
    bus=None,
    poll_block_ms: int = 1000,
) -> FastAPI:
    the_bus = bus  # resolved lazily in lifespan if None (honors pytest fake selection).

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        b = the_bus or Bus()
        app.state.bus = b
        tasks: list[asyncio.Task] = []
        if scheduler is not None:
            tasks.append(asyncio.create_task(_run_scheduler(scheduler, b)))
        if command_handler is not None:
            tasks.append(
                asyncio.create_task(
                    _consume_loop(domain, b, command_handler, poll_block_ms)
                )
            )
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"domain": domain, "up": True}

    return app
