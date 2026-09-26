"""Runnable trade domain service (Task #26).

Assembles the shared scaffold with this domain's command handler and its
scheduler. Trade analysis is **on-demand only**: the GUI Trade page enqueues an
``analyze`` command with a symbol and the consumer runs
``handlers.handle_command`` → ``analyze`` → compute → cache + publish. The
scheduler has ONE branch, the daily watchlist dividend pull (``scheduler.py``,
news v2), gated like every loop by the environment's ``schedulers`` flag.

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
    scheduler=scheduler.loop,
)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["trade"])
