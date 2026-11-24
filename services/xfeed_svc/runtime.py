"""The service's one Store / FxClient / Poller / Discord sender, built lazily on the default paths.

Handlers and the scheduler share it through ``get()``; tests swap it with
``set_for_tests``.
"""
import threading
from dataclasses import dataclass

from services.xfeed_svc import config
from services.xfeed_svc.discord_feed import Sender
from services.xfeed_svc.fxapi import FxClient
from services.xfeed_svc.poller import Poller
from services.xfeed_svc.store import Store


@dataclass
class Runtime:
    store: Store
    fx: FxClient
    poller: Poller
    # The Discord feed's sender (discord_feed.py). None in a test that builds
    # a Runtime without one: the poll then hands new posts to no one.
    discord: Sender | None = None


_lock = threading.Lock()
_rt = None


def get() -> Runtime:
    global _rt
    with _lock:
        if _rt is None:
            store = Store()
            fx = FxClient(config.load()["fxembed"])
            _rt = Runtime(store, fx, Poller(store, fx), Sender(store))
        return _rt


def set_for_tests(rt) -> None:
    global _rt
    with _lock:
        _rt = rt
