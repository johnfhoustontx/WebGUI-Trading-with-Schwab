"""The /ES and /NQ chain feed's watch: a phone alert when capture stops during the
CME session, one message when it resumes, and the feed's report on ``/health``.

The decision (``decide``) is pure and is tested with plain dictionaries shaped like
the status view ``futures_chain_feed`` keeps; ``tick`` is tested on the fake bus.
Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, section 5.9.
"""
import ast
import asyncio
import importlib
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services import _degrade
from services.options_svc import compute  # noqa: F401  puts options-scanner on sys.path
from services.options_svc import futures_chain_watch, handlers, scheduler
from services.options_svc.futures_chain_watch import decide, tick
from services.options_svc.tests.test_app import _BRANCH_HANDLERS
from shared import futures_chain
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.notify import system_alert

_CT = ZoneInfo("America/Chicago")
ES, NQ = "FOP.ES", "FOP.NQ"


def ct(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_CT)


# ── builders: the status view as futures_chain_feed keeps it ─────────────────

def entry(captured, repeats=0, reason=""):
    return {"captured_at": captured.timestamp(), "expiry": "2026-11-02", "listed": 100,
            "with_gamma": 90, "parity_gap": 0.5, "reason": reason, "repeats": repeats,
            "refused_at": None, "price": 7600.0, "activity": 1000.0,
            "refused_listed": None}


def status(captured, repeats=0, pc_reason="", reason="", symbol=ES):
    view = {symbol: entry(captured, repeats, reason)}
    if pc_reason:
        view["pc"] = {"reason": pc_reason, "at": captured.timestamp()}
    return view


def both(es, nq):
    return {ES: entry(es), NQ: entry(nq)}


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, title, lines, **kwargs):
        self.calls.append((title, list(lines)))
        return ["telegram"]


@pytest.fixture
def spy_send(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(system_alert, "send", spy)
    return spy


def raises(*args, **kwargs):
    raise RuntimeError("telegram is down")


def write_status(bus, **kwargs):
    bus.cache_set(futures_chain.STATUS_KEY, status(**kwargs))


def stored_state(bus):
    envelope = bus.cache_get(futures_chain.ALERT_STATE_KEY)
    return envelope.payload if envelope is not None else None


@pytest.fixture
def client(bus):
    from services.options_svc.app import app
    with TestClient(app) as test_client:
        yield test_client


# ── the decision ─────────────────────────────────────────────────────────────

def test_alerts_once_when_capture_stops_in_session():
    alerts, state = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), {})
    assert [a.kind for a in alerts] == ["stopped"]
    assert decide(ct(2026, 11, 2, 9, 25), status(captured=ct(2026, 11, 2, 9, 0)), state)[0] == []


def test_repeats_only_after_the_configured_time():
    _, state = decide(ct(2026, 11, 2, 0, 0), status(captured=ct(2026, 11, 1, 23, 0)), {})
    assert decide(ct(2026, 11, 2, 3, 59), status(captured=ct(2026, 11, 1, 23, 0)), state)[0] == []
    assert len(decide(ct(2026, 11, 2, 4, 1), status(captured=ct(2026, 11, 1, 23, 0)), state)[0]) == 1


@pytest.mark.parametrize("quiet", [(2026, 11, 2, 16, 0), (2026, 11, 7, 12, 0), (2026, 11, 8, 16, 30)])
def test_quiet_in_the_daily_gap_and_the_weekend(quiet):
    assert decide(ct(*quiet), status(captured=ct(2026, 11, 2, 9, 0)), {})[0] == []


def test_the_first_minutes_of_a_session_do_not_alert():
    assert decide(ct(2026, 11, 8, 17, 5), status(captured=ct(2026, 11, 6, 14, 55)), {})[0] == []


def test_the_first_minutes_do_not_alert_on_the_last_sessions_frozen_run():
    old = status(captured=ct(2026, 11, 6, 14, 55), repeats=5)
    assert decide(ct(2026, 11, 8, 17, 5), old, {})[0] == []


def test_a_session_that_never_gets_a_capture_alerts_once_the_quiet_time_has_passed():
    old = status(captured=ct(2026, 11, 6, 14, 55))
    assert decide(ct(2026, 11, 8, 17, 5), old, {})[0] == []
    assert [a.kind for a in decide(ct(2026, 11, 8, 17, 12), old, {})[0]] == ["stopped"]


def test_clears_with_one_message_when_it_resumes():
    _, state = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), {})
    alerts, state = decide(ct(2026, 11, 2, 9, 30), status(captured=ct(2026, 11, 2, 9, 29)), state)
    assert [a.kind for a in alerts] == ["resumed"]
    assert decide(ct(2026, 11, 2, 9, 35), status(captured=ct(2026, 11, 2, 9, 34)), state)[0] == []


def test_a_healthy_feed_that_never_stopped_says_nothing():
    alerts, state = decide(ct(2026, 11, 2, 9, 30), status(captured=ct(2026, 11, 2, 9, 29)), {})
    assert alerts == [] and state == {}


def test_nothing_is_said_about_a_feed_that_never_captured():
    assert decide(ct(2026, 11, 2, 9, 30), {}, {}) == ([], {})
    assert decide(ct(2026, 11, 2, 9, 30), {ES: {"captured_at": None, "repeats": 0}}, {})[0] == []
    # Only the PC has spoken: it has not tried a capture, so nothing is refused.
    assert decide(ct(2026, 11, 2, 9, 30), {"pc": {"reason": "ThinkorSwim logged out",
                                                  "at": 1.0}}, {}) == ([], {})


# ── an instrument whose every capture is refused (no capture ever accepted) ──

def refused_only(when, reason="Refused: the chain has no open interest.", symbol=ES):
    """The status entry the feed leaves when the PC sends and every capture is
    refused: a reason and a time, and no accepted capture."""
    return {symbol: {"captured_at": None, "expiry": None, "listed": None,
                     "with_gamma": None, "parity_gap": None, "reason": reason,
                     "repeats": 0, "refused_at": when.timestamp(), "price": None,
                     "activity": None, "refused_listed": None}}


def test_an_instrument_whose_every_capture_is_refused_alerts_once_with_the_reason():
    view = refused_only(ct(2026, 11, 2, 9, 19))
    alerts, state = decide(ct(2026, 11, 2, 9, 20), view, {})
    assert [(a.kind, a.symbol) for a in alerts] == [("stopped", ES)]
    assert "no open interest" in " ".join(alerts[0].lines)
    assert "/ES" in alerts[0].title and "FOP" not in " ".join(alerts[0].lines)
    assert decide(ct(2026, 11, 2, 9, 25), view, state)[0] == []


def test_a_refused_only_instrument_is_quiet_in_the_first_minutes_of_a_session():
    view = refused_only(ct(2026, 11, 8, 17, 4))
    assert decide(ct(2026, 11, 8, 17, 5), view, {})[0] == []
    assert [a.kind for a in decide(ct(2026, 11, 8, 17, 12), view, {})[0]] == ["stopped"]


def test_a_refused_only_instrument_is_quiet_outside_the_session():
    view = refused_only(ct(2026, 11, 2, 14, 0))
    for quiet in ((2026, 11, 2, 16, 0), (2026, 11, 7, 12, 0)):
        assert decide(ct(*quiet), view, {})[0] == []


def test_a_refused_only_instrument_repeats_only_after_the_configured_time():
    view = refused_only(ct(2026, 11, 2, 0, 0))
    _, state = decide(ct(2026, 11, 2, 0, 0), view, {})
    assert decide(ct(2026, 11, 2, 3, 59), view, state)[0] == []
    assert len(decide(ct(2026, 11, 2, 4, 1), view, state)[0]) == 1


def test_a_refused_only_instrument_resumes_when_a_capture_is_accepted():
    _, state = decide(ct(2026, 11, 2, 9, 20), refused_only(ct(2026, 11, 2, 9, 19)), {})
    alerts, state = decide(ct(2026, 11, 2, 9, 30), status(captured=ct(2026, 11, 2, 9, 29)), state)
    assert [a.kind for a in alerts] == ["resumed"] and state == {}


def test_a_refused_only_instrument_names_itself_beside_a_healthy_one():
    view = {**status(captured=ct(2026, 11, 2, 9, 19)),
            **refused_only(ct(2026, 11, 2, 9, 19), symbol=NQ)}
    alerts, _ = decide(ct(2026, 11, 2, 9, 20), view, {})
    assert [a.symbol for a in alerts] == [NQ]


def test_a_refused_only_instrument_is_told_through_tick(bus, spy_send):
    bus.cache_set(futures_chain.STATUS_KEY, refused_only(ct(2026, 11, 2, 9, 19)))
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    tick(bus, now=ct(2026, 11, 2, 9, 21))
    assert len(spy_send.calls) == 1 and "no open interest" in " ".join(spy_send.calls[0][1])


def test_the_gap_between_sessions_neither_alerts_nor_clears():
    _, state = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), {})
    alerts, kept = decide(ct(2026, 11, 2, 16, 0), status(captured=ct(2026, 11, 2, 9, 0)), state)
    assert alerts == [] and kept == state


def test_values_not_changing_alerts_too():
    assert [a.kind for a in decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 19), repeats=3), {})[0]] == ["stopped"]


def test_the_pcs_reason_goes_in_the_message():
    alerts, _ = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0), pc_reason="ThinkorSwim logged out"), {})
    assert "logged out" in " ".join(alerts[0].lines)


def test_the_services_refusal_goes_in_the_message():
    alerts, _ = decide(ct(2026, 11, 2, 9, 20),
                       status(captured=ct(2026, 11, 2, 9, 0), reason="Refused: half filled"), {})
    assert "half filled" in " ".join(alerts[0].lines)


def test_one_instrument_stopping_names_that_instrument():
    alerts, _ = decide(ct(2026, 11, 2, 9, 20), both(es=ct(2026, 11, 2, 9, 19), nq=ct(2026, 11, 2, 9, 0)), {})
    assert "/NQ" in alerts[0].title and "/ES" not in alerts[0].title


def test_each_instrument_has_its_own_clock():
    _, state = decide(ct(2026, 11, 2, 9, 20), both(es=ct(2026, 11, 2, 9, 19), nq=ct(2026, 11, 2, 9, 0)), {})
    alerts, _ = decide(ct(2026, 11, 2, 9, 25), both(es=ct(2026, 11, 2, 9, 0), nq=ct(2026, 11, 2, 9, 0)), state)
    assert [(a.kind, a.symbol) for a in alerts] == [("stopped", ES)]


def test_the_state_is_not_edited_in_place():
    given = {}
    decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), given)
    assert given == {}


def test_the_repeat_interval_is_the_configured_one(monkeypatch):
    from shared import futures_chain_config
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "alert_repeat_min": 10})
    _, state = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), {})
    assert decide(ct(2026, 11, 2, 9, 29), status(captured=ct(2026, 11, 2, 9, 0)), state)[0] == []
    assert len(decide(ct(2026, 11, 2, 9, 31), status(captured=ct(2026, 11, 2, 9, 0)), state)[0]) == 1


def test_a_garbled_status_view_decides_nothing_and_raises_nothing():
    for junk in ({ES: "no"}, {ES: {"captured_at": "yesterday"}}, {ES: {"captured_at": float("nan")}},
                 {ES: {"captured_at": True}}, {"pc": 4}):
        assert decide(ct(2026, 11, 2, 9, 20), junk, {})[0] == []


# ── tick: the bus, the send and the state ────────────────────────────────────

def test_tick_sends_one_alert_and_remembers_it(bus, spy_send):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    tick(bus, now=ct(2026, 11, 2, 9, 21))
    assert len(spy_send.calls) == 1
    assert "/ES" in spy_send.calls[0][0]


def test_tick_survives_a_failed_send_and_keeps_its_state(bus, monkeypatch):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    monkeypatch.setattr(system_alert, "send", raises)
    tick(bus, now=ct(2026, 11, 2, 9, 20))     # no exception
    assert stored_state(bus) in (None, {})    # nothing was marked as told


def test_a_failed_send_is_retried_on_the_next_tick(bus, monkeypatch):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    monkeypatch.setattr(system_alert, "send", raises)
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    spy = _Spy()
    monkeypatch.setattr(system_alert, "send", spy)
    tick(bus, now=ct(2026, 11, 2, 9, 21))
    assert len(spy.calls) == 1
    tick(bus, now=ct(2026, 11, 2, 9, 22))
    assert len(spy.calls) == 1


def test_a_send_that_reached_no_channel_is_retried(bus, monkeypatch):
    """``system_alert.send`` returns ``[]`` when notifications are off or
    unconfigured (the master switch, the ``system`` category, no destination): the
    alert is not marked as told. A channel's own network failure is swallowed
    inside ``send`` and still names the channel, so it is not this path."""
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    monkeypatch.setattr(system_alert, "send", lambda title, lines, **kw: [])
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    assert stored_state(bus) in (None, {})
    spy = _Spy()
    monkeypatch.setattr(system_alert, "send", spy)
    tick(bus, now=ct(2026, 11, 2, 9, 21))
    assert len(spy.calls) == 1


def test_one_failed_alert_does_not_unsend_the_other(bus, monkeypatch):
    bus.cache_set(futures_chain.STATUS_KEY, both(es=ct(2026, 11, 2, 9, 0), nq=ct(2026, 11, 2, 9, 0)))
    told = []

    def flaky(title, lines, **kwargs):
        if "/NQ" in title:
            raise RuntimeError("down")
        told.append(title)
        return ["telegram"]

    monkeypatch.setattr(system_alert, "send", flaky)
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    assert told and "/ES" in told[0]
    assert set(stored_state(bus)) == {ES}


def test_a_failed_send_leaves_a_trace(bus, monkeypatch):
    _degrade.reset()
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    monkeypatch.setattr(system_alert, "send", raises)
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    assert _degrade.counts().get(futures_chain_watch.AREA, 0) >= 1
    _degrade.reset()


def test_alert_state_lives_in_redis_so_a_restart_does_not_re_alert(bus, spy_send):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    importlib.reload(futures_chain_watch)       # a restart: no state in memory
    futures_chain_watch.tick(bus, now=ct(2026, 11, 2, 9, 21))
    assert len(spy_send.calls) == 1


def test_the_resumed_message_goes_out_once_through_tick(bus, spy_send):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    write_status(bus, captured=ct(2026, 11, 2, 9, 29))
    tick(bus, now=ct(2026, 11, 2, 9, 30))
    tick(bus, now=ct(2026, 11, 2, 9, 31))
    assert len(spy_send.calls) == 2
    assert "resumed" in spy_send.calls[1][0]


def test_alert_state_has_its_own_key_and_never_touches_the_status_view(bus, spy_send):
    assert futures_chain.ALERT_STATE_KEY != futures_chain.STATUS_KEY
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    before = bus.cache_get(futures_chain.STATUS_KEY).payload
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    assert bus.cache_get(futures_chain.STATUS_KEY).payload == before
    assert ES in stored_state(bus)


def test_a_quiet_tick_writes_nothing(bus, spy_send):
    write_status(bus, captured=ct(2026, 11, 2, 9, 29))
    tick(bus, now=ct(2026, 11, 2, 9, 30))
    assert bus.cache_get(futures_chain.ALERT_STATE_KEY) is None and spy_send.calls == []


def test_the_alert_key_reaches_no_page():
    """Nothing in the web tier names the key: a public screen cannot read it by
    way of a page (the Redis ACL is not a layer here, so no page may ask)."""
    webgui = pathlib.Path(__file__).resolve().parents[3] / "webgui"
    for path in webgui.rglob("*.py"):
        if "tests" in path.parts:
            continue
        assert "ALERT_STATE_KEY" not in path.read_text(encoding="utf-8"), path


def test_the_send_is_looked_up_by_attribute_not_imported_by_name():
    tree = ast.parse(pathlib.Path(futures_chain_watch.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "system_alert" in node.module:
            pytest.fail("import system_alert as a module and call system_alert.send")


def test_the_watch_imports_no_client_and_nothing_from_compute():
    tree = ast.parse(pathlib.Path(futures_chain_watch.__file__).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names += [f"{node.module}.{alias.name}" for alias in node.names]
    forbidden = ("requests", "urllib", "httpx", "schwab", "proxy", "anthropic")
    assert not [n for n in names if any(part in n.lower() for part in forbidden)], names
    assert not [n for n in names if n.endswith("compute")]


# ── the scheduler ────────────────────────────────────────────────────────────

def _run_one_tick(monkeypatch, bus, when):
    """Run ``scheduler.loop`` through its first tick at ``when`` with every other
    branch stubbed (the same stub list the loop test in test_app.py keeps)."""
    for name in _BRANCH_HANDLERS:
        monkeypatch.setattr(handlers, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(handlers, "autoclose_enabled", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(handlers, "rescan", lambda b: None)
    monkeypatch.setattr(scheduler, "_market_now", lambda: when)

    async def _stop(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", _stop)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(scheduler.loop(bus))
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


@pytest.mark.parametrize("when", [ct(2026, 11, 1, 23, 0),      # Sunday night: NYSE closed
                                  ct(2026, 11, 2, 3, 0),       # Monday, long before the open
                                  ct(2026, 11, 2, 16, 30)])    # after the NYSE close
def test_the_watch_runs_on_the_scheduler_outside_nyse_hours(monkeypatch, bus, when):
    seen = []
    monkeypatch.setattr(futures_chain_watch, "tick", lambda b, now=None: seen.append((b, now)))
    _run_one_tick(monkeypatch, bus, when)
    assert seen == [(bus, when)]


def test_a_watch_that_raises_does_not_stop_the_loop(monkeypatch, bus):
    def boom(b, now=None):
        raise RuntimeError("watch broke")
    monkeypatch.setattr(futures_chain_watch, "tick", boom)
    _run_one_tick(monkeypatch, bus, ct(2026, 11, 2, 9, 0))       # no exception but the stop


def test_the_watch_is_started_only_by_the_scheduler():
    """The scheduler is what ``_schedulers_enabled`` switches off, so a watch
    that is only reachable from it is suppressed with the rest."""
    from services.options_svc import app as options_app
    source = pathlib.Path(options_app.__file__).read_text(encoding="utf-8")
    assert "futures_chain_watch.tick(" not in source
    loop_source = pathlib.Path(scheduler.__file__).read_text(encoding="utf-8")
    assert "futures_chain_watch.tick" in loop_source


# ── /health ──────────────────────────────────────────────────────────────────

def test_health_reports_the_feed_and_never_the_verdict(client):
    body = client.get("/health").json()
    assert "futures_chain" in body and body["up"] is True


def test_health_names_each_instrument_by_its_page_label(bus, client):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0), reason="Refused: half filled")
    section = client.get("/health").json()["futures_chain"]
    assert section["instruments"]["/ES"]["captured_at"] == ct(2026, 11, 2, 9, 0).timestamp()
    assert section["instruments"]["/ES"]["reason"] == "Refused: half filled"


def test_health_with_no_status_view_is_empty_and_up(client):
    body = client.get("/health").json()
    assert body["futures_chain"]["instruments"] == {} and body["up"] is True


def test_health_does_no_heavy_work(bus, monkeypatch):
    """One read of the status view, and nothing else on the bus."""
    reads = []
    real = bus.cache_get
    monkeypatch.setattr(bus, "cache_get", lambda key, *a, **k: (reads.append(key), real(key, *a, **k))[1])
    futures_chain_watch.health(bus)
    assert reads == [futures_chain.STATUS_KEY]


def test_health_ages_a_capture_by_the_clock(bus, monkeypatch):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    monkeypatch.setattr(futures_chain_watch, "_now", lambda: ct(2026, 11, 2, 9, 20))
    section = futures_chain_watch.health(bus)
    assert section["instruments"]["/ES"]["age_s"] == 1200
    assert section["in_session"] is True


def test_health_reports_the_pcs_reason(bus):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0), pc_reason="ThinkorSwim logged out")
    assert futures_chain_watch.health(bus)["pc"]["reason"] == "ThinkorSwim logged out"


def test_health_survives_a_garbled_status_view(bus):
    bus.cache_set(futures_chain.STATUS_KEY, {ES: "no", "pc": 3})
    assert futures_chain_watch.health(bus)["instruments"] == {}


def test_health_lists_a_refused_only_instrument_with_its_reason_and_no_capture_time(bus, monkeypatch):
    bus.cache_set(futures_chain.STATUS_KEY, refused_only(ct(2026, 11, 2, 9, 19)))
    monkeypatch.setattr(futures_chain_watch, "_now", lambda: ct(2026, 11, 2, 9, 30))
    shown = futures_chain_watch.health(bus)["instruments"]["/ES"]
    assert shown["reason"] == "Refused: the chain has no open interest."
    assert "captured_at" in shown and shown["captured_at"] is None
    assert shown["age_s"] is None and shown["state"] == "stopped"
    assert shown["refused_at"] == ct(2026, 11, 2, 9, 19).timestamp()


def test_health_says_a_refused_only_instrument_has_no_accepted_capture_between_sessions(bus, monkeypatch):
    bus.cache_set(futures_chain.STATUS_KEY, refused_only(ct(2026, 11, 2, 14, 0)))
    monkeypatch.setattr(futures_chain_watch, "_now", lambda: ct(2026, 11, 2, 16, 0))
    shown = futures_chain_watch.health(bus)["instruments"]["/ES"]
    assert shown["state"] == "no capture accepted" and shown["captured_at"] is None


def test_health_leaves_out_an_instrument_the_pc_has_never_tried(bus):
    bus.cache_set(futures_chain.STATUS_KEY, {"pc": {"reason": "", "at": 1.0}})
    assert futures_chain_watch.health(bus)["instruments"] == {}


def test_the_feed_report_is_registered_on_the_options_service():
    from services.options_svc import app as options_app
    source = pathlib.Path(options_app.__file__).read_text(encoding="utf-8")
    assert "health_extra=" in source and "futures_chain_watch" in source
