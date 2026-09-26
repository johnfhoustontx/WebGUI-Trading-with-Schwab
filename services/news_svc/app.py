"""Runnable news service (port 8216).

Polls the public feeds in config/news.toml and publishes cache:news:feed /
feed_public / sec / sec_public / status; refreshes the economic calendar (and
watches for due releases) into cache:news:calendar / calendar_public /
calendar_status. Three scheduler branches - feeds, calendar, watch
(``scheduler.loop``). One command, news_refresh: the calendar, then the feeds,
each on its own per-source cadence, so a click or a replayed backlog is cheap.
Importable without side effects; starts uvicorn only under __main__.
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.news_svc import handlers, scheduler  # noqa: E402

app = make_app("news", scheduler=scheduler.loop,
               command_handler=handlers.handle_command)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["news"])
