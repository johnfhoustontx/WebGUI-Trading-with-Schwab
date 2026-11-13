"""A total-deadline watchdog for ``requests`` calls, shared by every service that
makes one (``news_svc.fetch``, ``xfeed_svc.fxapi``).

``requests``' ``timeout`` bounds each READ, not the request: a server sending one
byte every ``timeout - 1`` seconds never trips it. The fix is a WATCHDOG: the
caller arms a ``threading.Timer`` (``DEADLINE_FACTOR x timeout`` by convention)
whose expiry calls ``Watch.fire``, which shuts down every socket the request
opened. That wakes whatever read is blocked - status line, a header, the TLS
handshake or the body - and the caller sees ``Watch.expired``. Sockets are
captured as urllib3 opens them (``WatchedAdapter``) and reported to the
thread-local ``ACTIVE.watch``; the one phase the watchdog cannot cut is DNS
resolution and the TCP connect, which ``timeout`` already bounds.

Protocol: set ``ACTIVE.watch = watch`` before the request and clear it after;
open the request through ``get`` (a fresh session per request, so every socket
is one the watchdog saw opened).
"""
import socket
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3 import connection as _u3conn
from urllib3 import connectionpool as _u3pool

DEADLINE_FACTOR = 3


ACTIVE = threading.local()        # the fetching thread's Watch, while it fetches


class Watch:
    """The sockets one fetch opened, and whether its deadline has passed.

    Each socket is held as a DUPLICATE descriptor taken the moment urllib3
    creates it: the TLS wrap detaches the original object, and a bare fd number
    could be closed and reused by an unrelated socket before the timer fires.
    ``shutdown`` on the duplicate acts on the shared connection, so a read
    blocked in the fetching thread returns at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self._socks = []
        self.expired = False

    def register(self, sock):
        try:
            dup = socket.fromfd(sock.fileno(), sock.family, sock.type)
        except (OSError, ValueError, AttributeError):
            return
        with self._lock:
            self._socks.append(dup)
            if self.expired:                  # the connect itself ran past it
                cut(dup)

    def fire(self):
        with self._lock:
            self.expired = True
            for s in self._socks:
                cut(s)

    def close(self):
        with self._lock:
            socks, self._socks = self._socks, []
            for s in socks:
                try:
                    s.close()
                except OSError:
                    pass


def cut(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def register(sock):
    watch = getattr(ACTIVE, "watch", None)
    if watch is not None:
        watch.register(sock)
    return sock


class WatchedHTTPConnection(_u3conn.HTTPConnection):
    def _new_conn(self):
        return register(super()._new_conn())


class WatchedHTTPSConnection(_u3conn.HTTPSConnection):
    def _new_conn(self):
        return register(super()._new_conn())


class WatchedHTTPPool(_u3pool.HTTPConnectionPool):
    ConnectionCls = WatchedHTTPConnection


class WatchedHTTPSPool(_u3pool.HTTPSConnectionPool):
    ConnectionCls = WatchedHTTPSConnection


WATCHED_POOLS = {"http": WatchedHTTPPool, "https": WatchedHTTPSPool}


class WatchedAdapter(HTTPAdapter):
    """An adapter whose pools report every socket they open to ``ACTIVE``."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = dict(WATCHED_POOLS)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        if not proxy.lower().startswith("socks"):     # SOCKS brings its own pools
            manager.pool_classes_by_scheme = dict(WATCHED_POOLS)
        return manager


def get(url, **kwargs):
    """``requests.get`` through the watched adapter, on a fresh session."""
    with requests.Session() as session:
        adapter = WatchedAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session.get(url, **kwargs)
