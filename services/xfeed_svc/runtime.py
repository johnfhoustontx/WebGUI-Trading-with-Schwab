"""The service's one Store / FxClient / Poller, built lazily on the default paths.

Handlers and the scheduler share it through ``get()``; tests swap it with
``set_for_tests``.
"""
import threading
from dataclasses import dataclass

from services.xfeed_svc import config
from services.xfeed_svc.fxapi import FxClient
from services.xfeed_svc.poller import Poller
from services.xfeed_svc.store import Store


@dataclass
class Runtime:
    store: Store
    fx: FxClient
    poller: Poller


_lock = threading.Lock()
_rt = None


def get() -> Runtime:
    global _rt
    with _lock:
        if _rt is None:
            store = Store()
            fx = FxClient(config.load()["fxembed"])
            _rt = Runtime(store, fx, Poller(store, fx))
        return _rt


def set_for_tests(rt) -> None:
    global _rt
    with _lock:
        _rt = rt
