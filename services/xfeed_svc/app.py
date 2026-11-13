"""Runnable X-feed service (port 8219).

The scheduler runs the poll cycle; ``cmd:xfeed`` goes to the handlers.
Importable without side effects; starts uvicorn only under __main__.
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.xfeed_svc import handlers, scheduler  # noqa: E402

app = make_app("xfeed", scheduler=scheduler.loop, command_handler=handlers.handle_command,
               slow_commands=handlers.SLOW, on_dropped=handlers.on_dropped)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["xfeed"])
