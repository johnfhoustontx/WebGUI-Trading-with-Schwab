"""Runnable options domain service (Task 2.4).

Assembles the shared scaffold with this domain's scheduler + command handler:

* scheduler ``scheduler.loop`` — 15-min auto-scan within 08:00–15:15 CT on
  trading days (checks the slot every 30 s).
* command handler ``handlers.handle_command`` — ``rescan`` → full rescan.

Importable without side effects; only starts uvicorn under ``__main__`` on the
``options`` service port (8211) from ``repo_paths.SERVICE_PORTS``.
"""
import pathlib
import sys

# Repo root on sys.path so ``repo_paths`` + the ``services``/``shared`` packages
# import whether run as a module or as a script (``python services/.../app.py``).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.options_svc import handlers, scheduler  # noqa: E402

app = make_app(
    "options",
    scheduler=scheduler.loop,
    command_handler=handlers.handle_command,
)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["options"])
