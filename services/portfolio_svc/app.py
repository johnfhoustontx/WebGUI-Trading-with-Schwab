"""Runnable portfolio domain service (Task #21).

Assembles the shared scaffold with this domain's command handler AND the
streaming scheduler. The scheduler builds the portfolio model, consumes the proxy
SSE quote stream, and throttle-publishes ``cache:portfolio:positions`` on each
tick; the GUI Portfolio page reads the cache + enqueues a ``refresh`` command on
``cmd:portfolio``.

Importable without side effects; only starts uvicorn under ``__main__`` on the
``portfolio`` service port (8212) from ``repo_paths.SERVICE_PORTS``.
"""
import pathlib
import sys

# Repo root on sys.path so ``repo_paths`` + the ``services``/``shared`` packages
# import whether run as a module or as a script (``python services/.../app.py``).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.portfolio_svc import handlers, scheduler  # noqa: E402

app = make_app(
    "portfolio",
    scheduler=scheduler.loop,
    command_handler=handlers.handle_command,
)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["portfolio"])
