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

**Persistent logging (R3a).** On app creation ``make_app`` bootstraps a
``RotatingFileHandler`` (5 MB × 5 backups) at ``services/<domain>_svc/logs/
<domain>.log`` on the ROOT logger, so every module's ``logging.getLogger(...)``
output survives the terminal tab closing. It is idempotent (guarded per-domain)
and gated OFF under pytest so the suite never litters log files.

**Scheduler supervision (R2).** The domain scheduler coroutine is *supervised*:
if it raises or returns unexpectedly it is restarted after a bounded backoff (up
to ``scheduler_max_restarts`` rapid restarts) so a transient failure (a Memurai
blip) self-heals. A small heartbeat (alive flag + last-start timestamp + restart
count) is tracked and surfaced on ``/health`` (``scheduler_alive`` /
``scheduler_restarts`` / ``scheduler_last_tick_age_s``) so the Status page (or an
external probe) can tell "process up" from "actively scheduling".
"""
import asyncio
import inspect
import logging
import os
import pathlib
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

# Repo root on sys.path so ``shared`` (PEP 420 namespace pkg) is importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import ENV_FLAGS  # noqa: E402
from shared.bus import Bus  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# R3a — persistent file logging (mirrors schwab-proxy's RotatingFileHandler)
# ---------------------------------------------------------------------------
_LOG_MAX_BYTES = 10 * 1024 * 1024  # ~10 MB per file
_LOG_BACKUP_COUNT = 5              # keep ~5 rotated files (≈50 MB ceiling)
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Domains whose file handler is already installed (idempotency guard); survives
# a second ``make_app`` call in-process without double-adding handlers.
_LOG_INSTALLED: set[str] = set()


def _under_pytest() -> bool:
    """True when running under pytest (so we don't spew log files in the repo)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def _install_file_logging(domain, log_root=None, *, force=False):
    """Attach a rotating file handler to the ROOT logger for ``domain``.

    Idempotent (per-domain) and a **no-op under pytest** unless ``force`` is set,
    so test runs never create ``logs/`` files. Returns the log file path when a
    handler was installed (or already present), else ``None``.

    ``log_root`` overrides the default ``services/<domain>_svc/logs`` directory
    (used by tests to write into a tmp dir). The root logger gains BOTH the file
    handler and — if none is present yet — a console ``StreamHandler``, so output
    goes to the file *and* the terminal.
    """
    if not force and _under_pytest():
        return None
    if domain in _LOG_INSTALLED:
        # Already wired — return the resolved path without re-adding a handler.
        if log_root is not None:
            return pathlib.Path(log_root) / f"{domain}.log"
        return _REPO_ROOT / "services" / f"{domain}_svc" / "logs" / f"{domain}.log"

    if log_root is not None:
        log_dir = pathlib.Path(log_root)
    else:
        log_dir = _REPO_ROOT / "services" / f"{domain}_svc" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{domain}.log"

        fmt = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)

        root = logging.getLogger()
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        root.addHandler(file_handler)
        # Keep console output: add a StreamHandler only if the root has none
        # (uvicorn usually configures one; don't duplicate it).
        if not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, RotatingFileHandler)
            for h in root.handlers
        ):
            console = logging.StreamHandler()
            console.setFormatter(fmt)
            root.addHandler(console)

        _LOG_INSTALLED.add(domain)
        log.info("file logging → %s", log_path)
        return log_path
    except Exception:  # noqa: BLE001 — logging setup must never crash startup.
        log.exception("failed to install file logging for %s", domain)
        return None


# ---------------------------------------------------------------------------
# R2 — scheduler supervision heartbeat
# ---------------------------------------------------------------------------
@dataclass
class _SchedulerHealth:
    """Heartbeat for the supervised scheduler task, surfaced on ``/health``."""

    has_scheduler: bool = False
    alive: bool = True          # False once the restart budget is exhausted
    restarts: int = 0           # number of times it was restarted after failure
    last_start: float | None = field(default=None)  # monotonic ts of last (re)start

    def last_tick_age_s(self):
        if self.last_start is None:
            return None
        return round(time.monotonic() - self.last_start, 3)


async def _run_scheduler(scheduler, bus) -> None:
    """Run the scheduler coroutine once; log+swallow any exception.

    Retained for back-compat / direct use; supervision lives in
    ``_supervise_scheduler``.
    """
    try:
        await scheduler(bus)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — must never crash the app.
        log.exception("scheduler task failed")


async def _supervise_scheduler(
    scheduler, bus, health: _SchedulerHealth, backoff_s: float, max_restarts: int
) -> None:
    """Run the scheduler, restarting it after a bounded backoff if it dies.

    A healthy scheduler contains its own infinite ``while True`` loop and never
    returns — so this wrapper simply awaits it. If it *raises* or *returns*
    (unexpected exit), we log, wait ``backoff_s``, and restart — up to
    ``max_restarts`` times, after which we give up and mark the heartbeat
    ``alive=False`` so ``/health`` can report the scheduler is dead. Cancellation
    (shutdown) always breaks out cleanly.
    """
    while True:
        health.last_start = time.monotonic()
        try:
            await scheduler(bus)
            # Returned without raising — an unexpected exit for a loop-scheduler.
            log.warning("scheduler coroutine returned; treating as an exit")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never crash the app.
            log.exception("scheduler task failed")

        if health.restarts >= max_restarts:
            health.alive = False
            log.error(
                "scheduler dead — exhausted %d restarts; NOT restarting again",
                max_restarts,
            )
            return
        health.restarts += 1
        log.warning(
            "restarting scheduler in %.2fs (restart %d/%d)",
            backoff_s,
            health.restarts,
            max_restarts,
        )
        try:
            await asyncio.sleep(backoff_s)
        except asyncio.CancelledError:
            raise


async def _consume_loop(domain, bus, command_handler, poll_block_ms) -> None:
    """Drain ``cmd:{domain}`` forever, dispatching each command to the handler.

    Each iteration is wrapped so a bad command (or a transient bus error) can
    never kill the loop. Both the blocking ``consume_commands`` AND each
    (synchronous, potentially multi-second — a ``sim_fetch`` is ~19 s) command
    handler run in the default thread-pool executor so a slow handler no longer
    stalls the event loop, ``/health``, or the scheduler task. Commands within a
    batch still run one-at-a-time (in read order) — no unbounded parallelism.

    A handler that raises → the command is dead-lettered (``cmd:{domain}:dead``)
    then ack'd, so it is neither silently lost nor blindly re-executed. On
    startup any entries stranded in the PEL by a previously-crashed consumer are
    drained to the same dead-letter list (for human review, NOT re-run).
    """
    stream = f"cmd:{domain}"
    group = f"{domain}-svc"
    loop = asyncio.get_event_loop()

    # Recover a prior crash's un-acked PEL (off the event loop; never raises).
    try:
        moved = await loop.run_in_executor(
            None, lambda: bus.drain_pending(stream, group, "c1")
        )
        if moved:
            log.warning(
                "drained %d stranded command(s) to %s on startup",
                moved,
                bus.dead_letter_key(stream),
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — startup drain must never crash the loop.
        log.exception("startup PEL drain failed for %s", stream)

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
                    result = await loop.run_in_executor(
                        None, command_handler, bus, command
                    )
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — one bad command must not kill the loop.
                    log.exception("command handler failed for %s", msg_id)
                    try:
                        bus.dead_letter(
                            stream, {"data": command.to_json()}, "handler raised"
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("dead-letter failed for %s", msg_id)
                finally:
                    try:
                        bus.ack(stream, group, msg_id)
                    except Exception:  # noqa: BLE001
                        log.exception("ack failed for %s", msg_id)
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001 — keep looping on any transient error.
            log.exception("consume loop iteration failed")


def _schedulers_enabled() -> bool:
    """Whether this process should run its scheduler.

    False in a suppressed environment (dev), where the stack works off a
    snapshot and must issue no Schwab calls at rest. ``TRADING_ENABLE_SCHEDULERS=1``
    is the escape hatch for the one dev case that genuinely needs collection:
    testing the collectors themselves.
    """
    if os.environ.get("TRADING_ENABLE_SCHEDULERS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    return bool(ENV_FLAGS.get("schedulers", True))


def make_app(
    domain: str,
    *,
    scheduler=None,
    command_handler=None,
    bus=None,
    poll_block_ms: int = 1000,
    scheduler_restart_backoff_s: float = 3.0,
    scheduler_max_restarts: int = 10,
) -> FastAPI:
    """Build the domain FastAPI app (see module docstring).

    New optional params (backward-compatible — positional signature unchanged):

    * ``scheduler_restart_backoff_s`` — pause before restarting a died scheduler.
    * ``scheduler_max_restarts`` — cap on rapid restarts before giving up (then
      ``/health`` reports ``scheduler_alive: false``).
    """
    the_bus = bus  # resolved lazily in lifespan if None (honors pytest fake selection).

    # R3a — persistent file logging (no-op under pytest, idempotent per-domain).
    _install_file_logging(domain)

    # R2 — scheduler heartbeat shared between the supervisor task and /health.
    # A suppressed environment reports "no scheduler", NOT "scheduler dead" —
    # otherwise /health and the Status page would show a healthy dev stack as
    # broken every time you looked at it.
    run_scheduler = scheduler is not None and _schedulers_enabled()
    health_state = _SchedulerHealth(has_scheduler=run_scheduler)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        b = the_bus or Bus()
        app.state.bus = b
        app.state.scheduler_health = health_state
        tasks: list[asyncio.Task] = []
        if run_scheduler:
            tasks.append(
                asyncio.create_task(
                    _supervise_scheduler(
                        scheduler,
                        b,
                        health_state,
                        scheduler_restart_backoff_s,
                        scheduler_max_restarts,
                    )
                )
            )
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
    app.state.scheduler_health = health_state

    @app.get("/health")
    def health():
        hs: _SchedulerHealth = app.state.scheduler_health
        # A service with no scheduler is trivially "alive" (nothing can die);
        # a supervised scheduler reports its real alive flag. Keys are ADDITIVE
        # — ``domain``/``up`` are unchanged for backward compatibility.
        return {
            "domain": domain,
            "up": True,
            "scheduler_alive": hs.alive if hs.has_scheduler else True,
            "scheduler_restarts": hs.restarts,
            "scheduler_last_tick_age_s": hs.last_tick_age_s(),
        }

    return app
