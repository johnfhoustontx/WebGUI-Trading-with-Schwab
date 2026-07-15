"""Runnable market dashboard service (port 8215).

A scheduler polls the proxy for ~48 macro symbols and publishes
cache:market:dashboard, plus a periodic Claude verdict for the ticker. The
command consumer exists only for the webgui's ticker toggle
(enable_summary/disable_summary) — the page itself is a pure reader. Importable
without side effects; starts uvicorn only under __main__.
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.market_svc import handlers, scheduler  # noqa: E402

app = make_app("market", scheduler=scheduler.loop,
               command_handler=handlers.handle_command)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["market"])
