"""Dealer Positioning's load timings (``pages/options/gamma_timing``)."""
import asyncio
import inspect
import logging

import pytest

from pages.options import gamma, gamma_timing as timing


class _Time:
    """A clock the test moves by hand, in seconds."""

    def __init__(self):
        self.t = 100.0
        self.wall = 1_700_000_000.0

    def step(self, ms):
        self.t += ms / 1000.0
        self.wall += ms / 1000.0


def _clock(t, enabled=True, who=lambda: ("$SPX", "GEX")):
    return timing.Clock(who, enabled=enabled, wait_sec=2.5,
                        clock=lambda: t.t, wall=lambda: t.wall)


def _browser(t, ms, nav=3190.0):
    """A run_javascript that takes ``ms`` to answer with ``nav``."""
    calls = []

    async def run_js(code, timeout):
        calls.append((code, timeout))
        t.step(ms)
        return nav
    run_js.calls = calls
    return run_js


def _report(clock, run_js, published=None):
    asyncio.run(clock.report(run_js, published))


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "webgui.gamma_timing"]


def test_an_open_logs_each_step_in_order(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()
    clock = _clock(t)
    t.step(380)
    clock.mark("built")
    t.step(860)
    read = clock.timed("snap", lambda: t.step(34) or {"symbol": "$SPX"})
    assert read() == {"symbol": "$SPX"}
    clock.timed("hist", lambda: t.step(51))()
    paint = clock.timed("figures", lambda: (clock.timed("bar", lambda: t.step(41))(),
                                           clock.timed("heat", lambda: t.step(702))()))
    paint()
    run_js = _browser(t, 310)
    _report(clock, run_js)
    assert _lines(caplog) == [
        "gamma open $SPX GEX: built=380 first_read=1240 snap=34 hist=51 "
        "figures=743 bar=41 heat=702 drawn=310 total=2378 nav=3190"]
    assert run_js.calls == [(timing.DRAWN_JS, 2.5)]


def test_a_wait_is_logged_once_and_a_new_one_rearms_it(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()
    clock = _clock(t)
    _report(clock, _browser(t, 10))
    _report(clock, _browser(t, 10))        # every repaint calls it: no line
    assert len(_lines(caplog)) == 1
    clock.start("tab")
    t.step(5)
    _report(clock, _browser(t, 10))
    assert len(_lines(caplog)) == 2
    assert _lines(caplog)[1] == "gamma tab $SPX GEX: drawn=10 total=15"


def test_a_symbol_wait_says_when_the_service_published_and_has_no_nav(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()
    clock = _clock(t)
    clock.pending = False
    clock.start("symbol")
    started = t.wall
    t.step(4200)                          # the service fetches and publishes
    clock.timed("snap", lambda: t.step(30))()
    stamp = timing._dt.datetime.fromtimestamp(started + 3.9, timing._dt.timezone.utc)
    _report(clock, _browser(t, 200), published=lambda: stamp.isoformat())
    assert _lines(caplog) == [
        "gamma symbol $SPX GEX: first_read=4200 published=3900 snap=30 "
        "drawn=200 total=4430"]


def test_a_browser_that_never_answers_still_leaves_a_line(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()
    clock = _clock(t)

    async def hidden(code, timeout):
        t.step(2500)
        raise TimeoutError

    _report(clock, hidden)
    assert _lines(caplog) == ["gamma open $SPX GEX: total=2500"]


def test_a_wait_started_while_one_is_drawn_does_not_change_its_line(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()
    clock = _clock(t)
    clock.timed("snap", lambda: t.step(20))()

    async def slow(code, timeout):
        clock.start("tab")                # a click lands mid-draw
        t.step(100)
        return 900.0

    _report(clock, slow)
    assert _lines(caplog) == [
        "gamma open $SPX GEX: first_read=0 snap=20 drawn=100 total=120 nav=900"]
    assert clock.pending and clock.kind == "tab"


def test_off_it_wraps_nothing_and_logs_nothing(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()
    clock = _clock(t, enabled=False)
    fn = lambda: 1                                       # noqa: E731
    assert clock.timed("snap", fn) is fn
    clock.start("tab")
    _report(clock, _browser(t, 10))
    assert _lines(caplog) == []


def test_a_label_that_fails_does_not_stop_the_line(caplog):
    caplog.set_level(logging.INFO, logger="webgui.gamma_timing")
    t = _Time()

    def broken():
        raise RuntimeError("no dropdown yet")

    _report(_clock(t, who=broken), _browser(t, 5))
    assert _lines(caplog) == ["gamma open - -: drawn=5 total=5 nav=3190"]


def test_the_page_clock_follows_the_config(monkeypatch):
    from shared import gamma_heat_config
    monkeypatch.setattr(gamma_heat_config, "timing",
                        lambda: {"enabled": False, "browser_wait_sec": 4.5})
    clock = timing.for_page(lambda: ("SPY", "GEX"))
    assert (clock.enabled, clock.wait_sec, clock.pending) == (False, 4.5, False)


def test_the_config_defaults_and_refuses_a_bad_value(monkeypatch):
    from shared import gamma_heat_config as cfg
    assert cfg.timing() == {"enabled": True, "browser_wait_sec": 3.0}
    monkeypatch.setattr(cfg, "load", lambda: {"timing": {
        "enabled": 1, "browser_wait_sec": 60}})
    assert cfg.timing() == {"enabled": True, "browser_wait_sec": 3.0}
    monkeypatch.setattr(cfg, "load", lambda: {"timing": {
        "enabled": False, "browser_wait_sec": 1.5}})
    assert cfg.timing() == {"enabled": False, "browser_wait_sec": 1.5}


# ── the wiring in gamma.render ───────────────────────────────────────────────

def test_render_times_every_read_paint_and_wait():
    """Each wait has a start and a report, each big read and each paint is
    timed, and the paint wrapper is installed before anything is wired to it."""
    src = inspect.getsource(gamma.render)
    for name in ("snap", "netprem", "hist"):
        assert f'clock.timed("{name}", bus_client.read_shared)' in src
    assert 'clock.timed("bar", bar_figure)(' in src
    assert 'clock.timed("heat", heatmap_figure)(' in src
    wrap = src.index('_render_view = clock.timed("figures", _render_view)')
    assert wrap < src.index("heat.on_change(_render_view")
    assert wrap < src.index('overlay_handler("gamma_level_tracks"')
    assert src.count("await clock.report(ui.run_javascript") == 3
    assert 'clock.start("symbol")' in src and 'clock.start("tab")' in src
    assert src.rstrip().endswith('clock.mark("built")')


# ── the briefing buttons, moved out of render ────────────────────────────────

class _Btn:
    def __init__(self):
        self.cls, self.tip, self.enabled = set(), None, None

    def classes(self, remove="", add=""):
        self.cls -= set(remove.split())
        self.cls |= set(add.split())

    def tooltip(self, text):
        self.tip = text

    def set_enabled(self, on):
        self.enabled = on


def test_a_briefing_button_lights_only_for_todays_data():
    today = timing._dt.datetime.now(gamma.ZoneInfo("America/Chicago")).date()
    btns = {"open": _Btn(), "close": _Btn()}
    views = {"open": "options:gamma_analyze_open", "close": "options:gamma_analyze_close"}
    state = {s: {"ver": None, "date": None, "applied": None} for s in btns}
    payloads = {views["open"]: {"generated_at": f"{today.isoformat()}T09:00"},
                views["close"]: {"generated_at": "2020-01-02T15:00"}}
    reads = []

    def read(view):
        reads.append(view)
        return payloads[view]

    titles = {"open": "Open", "close": "EOD recap"}
    gamma.sync_sched_buttons(btns, views, state, titles,
                             {views["open"]: 3, views["close"]: 1}, read)
    assert "bg-[#2563eb]" in btns["open"].cls and btns["open"].enabled
    assert btns["open"].tip == "Open today's Open briefing"
    assert "opacity-40" in btns["close"].cls
    assert btns["close"].tip == "EOD recap briefing — prior day (not today's)"
    # Same versions: no payload is read again.
    gamma.sync_sched_buttons(btns, views, state, titles,
                             {views["open"]: 3, views["close"]: 1}, read)
    assert len(reads) == 2
    gamma.sync_sched_buttons(btns, views, state, titles, {}, read)
    assert btns["open"].enabled is False
    assert btns["open"].tip == "Open briefing — not generated yet today"


@pytest.mark.parametrize("name", ["_SCHED_HL", "_SCHED_DIM"])
def test_the_briefing_classes_live_at_module_level(name):
    assert isinstance(getattr(gamma, name), str)
