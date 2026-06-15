"""Runnable sentiment domain service (Task 1.3).

Assembles the shared scaffold with this domain's scheduler + command handler:

* scheduler ``scheduler.loop`` — full refresh once, then composite-only / 120s.
* command handler ``handlers.handle_command`` — ``refresh`` → full refresh.

Importable without side effects; only starts uvicorn under ``__main__`` on the
``sentiment`` service port (8210) from ``repo_paths.SERVICE_PORTS``.
"""
import pathlib
import sys

# Repo root on sys.path so ``repo_paths`` + the ``services``/``shared`` packages
# import whether run as a module or as a script (``python services/.../app.py``).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.sentiment_svc import handlers, scheduler  # noqa: E402

app = make_app(
    "sentiment",
    scheduler=scheduler.loop,
    command_handler=handlers.handle_command,
)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["sentiment"])
