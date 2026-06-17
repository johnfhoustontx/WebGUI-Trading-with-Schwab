"""Runnable driver domain service (Task #30).

Assembles the shared scaffold with this domain's command handler AND the 09:28-ET
morning scheduler. The GUI Driver page enqueues ``run``/``approve``/``skip``/
``perf`` commands on ``cmd:driver``; the scheduler also fires the morning
pipeline once per trading day and keeps the performance view warm. The scheduler
never executes orders — only an explicit ``approve`` command does.

Importable without side effects; only starts uvicorn under ``__main__`` on the
``driver`` service port (8214) from ``repo_paths.SERVICE_PORTS``.
"""
import pathlib
import sys

# Repo root on sys.path so ``repo_paths`` + the ``services``/``shared`` packages
# import whether run as a module or as a script (``python services/.../app.py``).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.driver_svc import handlers, scheduler  # noqa: E402

app = make_app(
    "driver",
    scheduler=scheduler.loop,
    command_handler=handlers.handle_command,
)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["driver"])
