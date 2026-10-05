"""flow_stream: the worker that streams a flagged flow-alert contract after its
alert. The blocking loop is verified live; what it decides is tested here.

Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
import inspect
import logging
import threading

import pytest

from services.options_svc import flow_stream, flow_sides_tick

# Kept at IMPORT, before the suite's conftest swaps ``start`` for a no-op.
_REAL_START = flow_stream.start

A = "SPY   261009C00770000"
B = "QQQ   261009P00750000"


def test_stream_params_join_the_contract_symbols_unchanged():
    # A contract symbol is case- and space-sensitive: the padding is part of it.
    assert flow_stream.stream_params([A, B]) == {"symbols": f"{A},{B}"}


@pytest.mark.parametrize("current,wanted,want", [
    ([A], [A], False),
    ([A, B], [B, A], False),        # the same set in another order
    ([A], [A, B], True),            # a new alert
    ([A, B], [], True),             # a new session cleared the set
    ([], [], False),
])
def test_set_changed(current, wanted, want):
    assert flow_stream.set_changed(current, wanted) is want


def test_the_first_tick_of_a_contract_after_a_connect_is_unlabelled(monkeypatch):
    # Whatever printed while the stream was not connected cannot take the
    # label of the first quote seen after it.
    calls = []
    monkeypatch.setattr(flow_sides_tick, "stream_tick",
                        lambda tick, label=True: calls.append((tick["symbol"], label)))
    seen = set()
    for osi in (A, A, B, A):
        flow_stream.handle({"symbol": osi, "total_volume": 1.0}, seen)
    assert calls == [(A, False), (A, True), (B, False), (A, True)]


@pytest.mark.parametrize("tick", [None, 5, {}, {"symbol": ""}, {"symbol": None}])
def test_a_tick_with_no_contract_symbol_is_dropped(monkeypatch, tick):
    monkeypatch.setattr(flow_sides_tick, "stream_tick",
                        lambda *a, **k: pytest.fail("booked a tick with no symbol"))
    seen = set()
    flow_stream.handle(tick, seen)
    assert seen == set()


# --- one connection ----------------------------------------------------------

class _Resp:
    def __init__(self, lines, status_ok=True, after=None):
        self._lines, self._ok, self._after = lines, status_ok, after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("503 from the proxy")

    def iter_lines(self, decode_unicode=False):
        assert decode_unicode is True
        for line in self._lines:
            yield line
        if self._after:
            self._after()


class _Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.resp


def _line(osi, vol):
    return 'data: {"symbol": "%s", "total_volume": %s}' % (osi, vol)


def _record(monkeypatch):
    got = []
    monkeypatch.setattr(flow_sides_tick, "stream_tick",
                        lambda tick, label=True: got.append((tick, label)))
    return got


def test_consume_books_every_data_line_and_skips_the_rest(monkeypatch):
    got = _record(monkeypatch)
    monkeypatch.setattr(flow_sides_tick, "wanted_osis", lambda: [A])
    session = _Session(_Resp([_line(A, 10), ": keepalive", "", "data: {bad",
                              _line(A, 25)]))
    changed = flow_stream._consume(session, [A], threading.Event())
    assert changed is False                     # the stream simply ended
    assert [(t["total_volume"], label) for t, label in got] == [(10, False), (25, True)]
    (url, kw), = session.calls
    assert url.endswith("/stream/options")
    assert kw["params"] == {"symbols": A} and kw["stream"] is True
    # A read timeout: three missed keepalives is a dead proxy, not a quiet one.
    assert kw["timeout"] == (flow_stream.CONNECT_TIMEOUT_SEC,
                             flow_stream.READ_TIMEOUT_SEC)


def test_consume_returns_as_soon_as_the_wanted_set_changes(monkeypatch):
    got = _record(monkeypatch)
    monkeypatch.setattr(flow_stream, "RECHECK_SEC", 0.0)
    monkeypatch.setattr(flow_sides_tick, "wanted_osis", lambda: [A, B])
    session = _Session(_Resp([_line(A, 10), _line(A, 25)]))
    assert flow_stream._consume(session, [A], threading.Event()) is True
    assert got == []                            # it left before booking a line


def test_consume_stops_when_asked(monkeypatch):
    got = _record(monkeypatch)
    stop = threading.Event()
    stop.set()
    session = _Session(_Resp([_line(A, 10)]))
    assert flow_stream._consume(session, [A], stop) is False
    assert got == []


def test_consume_raises_on_an_error_status(monkeypatch):
    _record(monkeypatch)
    with pytest.raises(RuntimeError):
        flow_stream._consume(_Session(_Resp([], status_ok=False)), [A],
                             threading.Event())


# --- the loop ----------------------------------------------------------------

def test_worker_idles_while_nothing_is_flagged(monkeypatch):
    stop = threading.Event()
    asked = []

    def _wanted():
        asked.append(1)
        if len(asked) >= 3:
            stop.set()
        return []

    monkeypatch.setattr(flow_stream, "IDLE_POLL_SEC", 0.0)
    monkeypatch.setattr(flow_sides_tick, "wanted_osis", _wanted)
    monkeypatch.setattr(flow_stream, "_consume",
                        lambda *a: pytest.fail("connected with nothing to stream"))
    flow_stream._worker(stop)
    assert len(asked) == 3


def test_worker_reconnects_at_once_when_the_set_changed(monkeypatch):
    stop = threading.Event()
    sets = iter([[A], [A, B]])
    connects, waits = [], []

    def _consume(session, osis, stop_):
        connects.append(list(osis))
        if len(connects) == 2:
            stop.set()
        return True                             # "the set changed"

    monkeypatch.setattr(flow_sides_tick, "wanted_osis", lambda: next(sets))
    monkeypatch.setattr(flow_stream, "_consume", _consume)
    monkeypatch.setattr(flow_stream, "_pause", lambda stop_, sec: waits.append(sec))
    flow_stream._worker(stop)
    assert connects == [[A], [A, B]]
    assert waits == []                          # no pause between the two


def test_worker_backs_off_after_failures_and_never_raises(monkeypatch, caplog):
    stop = threading.Event()
    waits = []

    def _consume(session, osis, stop_):
        if len(waits) >= 3:
            stop.set()
            return False
        raise ConnectionError("proxy down")

    monkeypatch.setattr(flow_sides_tick, "wanted_osis", lambda: [A])
    monkeypatch.setattr(flow_stream, "_consume", _consume)
    monkeypatch.setattr(flow_stream, "_pause", lambda stop_, sec: waits.append(sec))
    with caplog.at_level(logging.WARNING, logger=flow_stream.log.name):
        flow_stream._worker(stop)
    assert waits == [3.0, 6.0, 12.0]
    assert caplog.text.count("flow stream disconnected") == 3


def test_a_clean_end_of_stream_resets_the_backoff(monkeypatch):
    stop = threading.Event()
    waits = []
    outcomes = iter([ConnectionError("x"), ConnectionError("x"), False,
                     ConnectionError("x")])

    def _consume(session, osis, stop_):
        out = next(outcomes, None)
        if out is None:
            stop.set()
            return False
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(flow_sides_tick, "wanted_osis", lambda: [A])
    monkeypatch.setattr(flow_stream, "_consume", _consume)
    monkeypatch.setattr(flow_stream, "_pause", lambda stop_, sec: waits.append(sec))
    flow_stream._worker(stop)
    # two failures (3, 6), a clean end (3), then a failure counted from zero (3)
    assert waits[:4] == [3.0, 6.0, 3.0, 3.0]


# --- wiring ------------------------------------------------------------------

def test_start_returns_a_stop_event_and_runs_a_daemon_thread(monkeypatch):
    started = threading.Event()

    def _worker(stop):
        started.set()
        stop.wait(5)

    monkeypatch.setattr(flow_stream, "_worker", _worker)
    before = set(threading.enumerate())
    stop = _REAL_START()
    try:
        assert started.wait(5)
        (t,) = [t for t in threading.enumerate() if t not in before]
        assert t.name == flow_stream.THREAD_NAME and t.daemon
    finally:
        stop.set()
    t.join(5)
    assert not t.is_alive()


def test_the_suite_does_not_start_the_real_worker():
    """The conftest replaces ``start``: a test that runs ``scheduler.loop``
    must not leave a polling thread behind."""
    assert flow_stream.start is not _REAL_START
    assert isinstance(flow_stream.start(), threading.Event)
    assert not [t for t in threading.enumerate()
                if t.name == flow_stream.THREAD_NAME]


def test_the_scheduler_starts_the_stream():
    """The loop only runs when schedulers are enabled, so starting the worker
    there is what keeps it out of the dev profile."""
    from services.options_svc import scheduler
    assert "flow_stream.start()" in inspect.getsource(scheduler.loop)
