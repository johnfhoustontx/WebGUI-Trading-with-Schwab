"""Runnable driver domain service (Task #30).

Assembles the shared scaffold with this domain's command handler and scheduler.
Two coexisting modes on ``cmd:driver``:

* **Legacy approval queue** — ``run``/``approve``/``skip``/``perf``; the 09:28-ET
  scheduler fires the morning pipeline once per trading day and keeps the
  performance view warm. This path only *proposes* orders for a human APPROVE —
  it never executes on its own.
* **Autonomous decision layer (level B, paper)** — ``cycle``/``enable``/
  ``disable``/``stop`` + a 30-min RTH checkpoint clock. When the
  ``cache:driver:control`` master switch is enabled (default OFF), each checkpoint
  runs the Claude decision layer and AUTO-EXECUTES the guardrail-clamped survivors
  as PAPER trades via ``cmd:options`` ``paper_create`` (``config.PAPER_TRADE``
  stays True — this service never flips it).

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
