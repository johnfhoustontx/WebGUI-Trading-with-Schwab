"""The harness must import without starting a server (tests import it)."""
import contextlib
import importlib.util
import logging
import pathlib
import socket

import pytest


def _harness():
    path = pathlib.Path(__file__).resolve().parents[1] / "ui_harness.py"
    spec = importlib.util.spec_from_file_location("ui_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)            # __name__ != "__main__": no ui.run
    return mod


def test_harness_imports_without_starting_a_server():
    assert callable(_harness().main)


def test_render_kwargs_default_to_nothing():
    """A page with no arguments is the common case and must stay argument-free."""
    assert _harness().parse_args(["settings"]).render_kwargs == {}


def test_render_kwargs_reach_the_page_that_needs_them():
    """``symbol.render(symbol=...)`` and ``sentiment_momentum.render(level=...)``
    take their subject as an argument, not from the cache - without this the
    harness can only ever draw their empty state."""
    args = _harness().parse_args(
        ["symbol", "--kwargs", '{"symbol": "SPY"}'])
    assert args.render_kwargs == {"symbol": "SPY"}


def test_a_kwargs_value_that_is_not_an_object_is_refused_at_the_command_line():
    """A bare string or list would fail later as ``render(**"SPY")`` - deep in a
    page build, where the traceback says nothing about the command line."""
    h = _harness()
    for bad in ('"SPY"', "[1,2]", "null", "not json"):
        with pytest.raises(SystemExit):
            h.parse_args(["symbol", "--kwargs", bad])



# ── the port preflight ─────────────────────────────────────────────────────
# A harness started on a port something already answers on does NOT fail
# loudly: NiceGUI's bind fails, the OLD server keeps serving, and whoever opens
# the page reads THAT server's page believing it is theirs. Measured 2026-09-20,
# when four leftover harnesses held 9593-9596 for two hours serving code from
# before four later commits, and an agent measured one of them as its own.

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _listening(host):
    """Something answering on ``host``, on a port the OS picks."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    s.listen()
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


def test_a_free_port_is_not_taken():
    assert _harness().port_taken(_free_port()) is False


def test_a_WILDCARD_listener_is_taken():
    """The case that matters, and the one a bind-probe gets WRONG.

    Every harness before this fix bound ``0.0.0.0`` (NiceGUI's non-native
    default). Measured on Windows: binding ``127.0.0.1`` on a port a wildcard
    listener holds SUCCEEDS, so a probe that asks "can I bind?" reports a
    leftover's port as free - the silent-bind trap, reproduced inside the fix
    meant to kill it. Asking "does anything answer?" cannot be fooled that way.
    """
    with _listening("0.0.0.0") as port:
        assert _harness().port_taken(port) is True


def test_a_loopback_listener_is_taken():
    with _listening("127.0.0.1") as port:
        assert _harness().port_taken(port) is True


def test_a_taken_port_is_refused_before_anything_starts():
    """Loud, and it names the port - the whole point is that the alternative
    was silent."""
    with _listening("127.0.0.1") as port:
        with pytest.raises(SystemExit) as exc:
            _harness().preflight_port(port)
    assert str(port) in str(exc.value)


def test_the_harness_serves_loopback_only():
    """Never 0.0.0.0 (CLAUDE.md). The harness passed no host, and NiceGUI
    resolves that to every interface outside native mode - so each leftover was
    listening on the LAN, not just this machine."""
    kw = _harness().run_kwargs(_harness().parse_args(["settings", "--port", "9700"]))
    assert kw["host"] == "127.0.0.1"
    assert kw["port"] == 9700


# ── the log filter ─────────────────────────────────────────────────────────

def test_main_installs_the_deleted_slot_filter_before_serving(monkeypatch):
    """``main.py`` drops NiceGUI's benign "parent slot of the element has been
    deleted" record (a timer racing a disconnect - CLAUDE.md, ``ui_guard``).
    The harness is a separate entrypoint and never installed it, so it printed
    a traceback the running app deliberately swallows: a phantom for the next
    session to chase.

    The filter is removed first, so a copy some other import left behind cannot
    make this pass."""
    h = _harness()
    from nicegui import ui
    from pages import ui_guard

    log = logging.getLogger("nicegui")
    saved = list(log.filters)
    for f in saved:
        if isinstance(f, ui_guard._DeletedSlotFilter):
            log.removeFilter(f)
    served = {}
    monkeypatch.setattr(h, "port_taken", lambda *a, **k: False)
    monkeypatch.setattr(ui, "run", lambda **kw: served.update(kw))
    try:
        h.main(["settings", "--port", "9701"])
        assert any(isinstance(f, ui_guard._DeletedSlotFilter) for f in log.filters)
        assert served["host"] == "127.0.0.1"
    finally:
        log.filters[:] = saved
