"""Runnable trade domain service (Task #26).

Assembles the shared scaffold with this domain's command handler. Trade
**analysis** is on-demand: the GUI Trade page enqueues an ``analyze`` command
with a symbol and the consumer runs ``handlers.handle_command`` → ``analyze`` →
compute → cache + publish. Nothing here polls for a verdict.

The scheduler is not a second path to that — it is the one thing this service
owes the stack on a clock: the nightly Alpha Vantage earnings-calendar pull
(``scheduler.loop``, 20:00 CT per ``config/sessions.toml`` ``[slots.earnings]``).
That store was previously refreshed only as a side effect of somebody running an
analyze, and it now also gates ``options_svc``'s 30-45 DTE income window. See
``scheduler.py`` for why it is a slot rather than a systemd timer.

Importable without side effects; only starts uvicorn under ``__main__`` on the
``trade`` service port (8213) from ``repo_paths.SERVICE_PORTS``.
"""
import pathlib
import sys

# Repo root on sys.path so ``repo_paths`` + the ``services``/``shared`` packages
# import whether run as a module or as a script (``python services/.../app.py``).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.trade_svc import handlers, scheduler  # noqa: E402

app = make_app(
    "trade",
    command_handler=handlers.handle_command,
    # Suppressed environments never reach the vendor: the scaffold runs this
    # only when `_schedulers_enabled()` agrees.
    scheduler=scheduler.loop,
)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["trade"])
