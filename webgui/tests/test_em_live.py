"""The PUBLIC Expected Move chart (pages/options/em_live.py), 2026-10-24.

The page is built for real and the service's side is played by writing the
answer and result keys straight to the test bus - ``test_calc_live.py``'s
harness, whose helpers this module borrows.
"""
import ast
import asyncio
import datetime as dt
import pathlib

import pytest
from nicegui import ui
from nicegui.events import GenericEventArguments

import bus_client
import live_screens
import shell
from pages.options import calc_live, em_live, expected_move, strategy_strip
from shared import public_tools as pt
from test_calc_live import (FAR, LISTED, MID, NEAR, STRIKES, _answer, _chain,
                            _joined, _publish_chain, _result, _run, _tools, _walk)

SRC = pathlib.Path(em_live.__file__)


def _key(cmd):
    return pt.request_key({"type": cmd.type, "args": cmd.args})


def _field(root, cls, label):
    """A control by the label ``kit.field`` puts above it."""
    for el in _walk(root):
        kids = getattr(getattr(el, "default_slot", None), "children", [])
        if any(isinstance(k, ui.label) and k.text == label for k in kids):
            for k in kids:
                if isinstance(k, cls):
                    return k
    raise AssertionError(f"no {cls.__name__} under {label!r}")


def _enter(inp):
    (listener,) = [l for l in inp._event_listeners.values() if l.type == "keydown.enter"]
    listener.handler(GenericEventArguments(sender=inp, client=inp.client, args=None))


def _chart(root):
    (chart,) = [el for el in _walk(root) if isinstance(el, ui.highchart)]
    return chart


def _cone(symbol="SPY", expiry=NEAR):
    return {"symbol": symbol, "expiry": expiry, "spot": 502.0, "atm_iv": 0.2,
            "dte": 5, "candles": [[1, 500.0, 504.0, 497.0, 501.0]],
            "em_upper": [[1, 502.0], [2, 512.5]], "em_lower": [[1, 502.0], [2, 491.5]],
            "lookback": {"label": "daily · 20d", "key": "auto"},
            "public": True, "quotes": False,
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat()}


@pytest.fixture
def seed(monkeypatch):
    """What this browser tab's hand-off holds (None: nothing)."""
    holder = {"value": None}

    async def _connected():
        return None

    monkeypatch.setattr(em_live, "_client_connected", _connected)
    monkeypatch.setattr(em_live.public_handoff, "read", lambda: holder["value"])
    return holder


@pytest.fixture
def page(monkeypatch, seed):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    calc_live.TOOLS._seen.clear()
    with ui.card() as root:
        expected_move.render(public=True)
    return root


def _open(root):
    _run(root, "_open")


def _loaded(root, loaded=(NEAR, MID)):
    """The visitor loads SPY and its chain lands."""
    with root:
        _enter(_field(root, ui.input, "Symbol"))
    (cmd,) = _tools()
    assert cmd.args == {"kind": "chain", "symbol": "SPY"}
    _publish_chain(_chain(loaded=loaded))
    _answer(_key(cmd), "done")
    _run(root, "_poll")


def _pick(root, expiry):
    with root:
        _field(root, ui.select, "Expiry").value = expiry


def _drawn(root, expiry=NEAR):
    """Pick ``expiry`` and land its cone; returns the em command."""
    _pick(root, expiry)
    (cmd,) = [c for c in _tools() if c.args.get("kind") == "em"]
    _result(_key(cmd), _cone(expiry=expiry))
    _answer(_key(cmd), "done")
    _run(root, "_poll")
    return cmd


# ── pure ────────────────────────────────────────────────────────────────────

def test_seed_lines_are_the_option_legs_strikes():
    legs = [{"option_type": "stock", "side": "long", "strike": None, "expiry": None},
            {"option_type": "put", "side": "short", "strike": 495.0, "expiry": NEAR},
            {"option_type": "put", "side": "long", "strike": 490.0, "expiry": NEAR}]
    assert em_live.seed_lines(legs) == (NEAR, [
        {"strike": 495.0, "option_type": "put", "side": "short"},
        {"strike": 490.0, "option_type": "put", "side": "long"}])
    assert em_live.seed_lines([]) == (None, [])


def test_an_em_request_names_a_known_window_or_nothing():
    assert em_live.em_request("SPY", NEAR, "3mo") == {
        "kind": "em", "symbol": "SPY", "expiry": NEAR, "lookback": "3mo"}
    assert em_live.em_request("SPY", NEAR, "10y") is None


# ── the page ────────────────────────────────────────────────────────────────

def test_a_page_load_sends_nothing(page):
    _open(page)
    assert _tools() == []


def test_a_page_load_shows_a_chain_someone_else_loaded_and_still_sends_nothing(
        monkeypatch, seed):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    bus_client.reset()
    _publish_chain(_chain())
    with ui.card() as root:
        expected_move.render(public=True)
    _open(root)
    assert list(_field(root, ui.select, "Expiry").options) == LISTED
    assert _tools() == []


def test_loading_then_picking_an_expiration_draws_its_cone(page):
    _loaded(page)
    cmd = _drawn(page)
    assert cmd.args == {"kind": "em", "symbol": "SPY", "expiry": NEAR,
                        "lookback": "auto"}
    series = _chart(page).options["series"]
    assert [s["name"] for s in series] == ["SPY", "Upper EM", "Lower EM"]
    assert "Expected move ±10.50" in _joined(page)
    assert "Look-back: daily · 20d" in _joined(page)


def test_an_expiration_whose_strikes_are_not_loaded_is_fetched_first(page):
    _loaded(page)
    _pick(page, FAR)
    (fetch,) = _tools()
    assert fetch.args == {"kind": "expiry", "symbol": "SPY", "expiry": FAR}
    _publish_chain(_chain(loaded=(NEAR, MID, FAR)))
    _answer(_key(fetch), "done")
    _run(page, "_poll")
    (em,) = _tools()
    assert em.args["kind"] == "em" and em.args["expiry"] == FAR


def test_a_strike_line_is_drawn_here_and_asks_for_nothing(page):
    _loaded(page)
    _drawn(page)
    with page:
        _field(page, ui.select, "Strike (optional)").value = 500.0
    assert _tools() == []
    lines = _chart(page).options["yAxis"]["plotLines"]
    assert [(l["value"], l["label"]["text"]) for l in lines] == [
        (500.0, "short put 500.00")]


def test_a_new_look_back_asks_for_a_new_cone(page):
    _loaded(page)
    _drawn(page)
    with page:
        _field(page, ui.select, "Look-back").value = "1y"
    (cmd,) = _tools()
    assert cmd.args["lookback"] == "1y"


def test_a_forgotten_chain_is_reloaded_once_then_the_cone_asked_again(page):
    _loaded(page)
    _pick(page, NEAR)
    (em,) = _tools()
    _answer(_key(em), "load_first")
    _run(page, "_poll")
    (reload,) = _tools()
    assert reload.args == {"kind": "chain", "symbol": "SPY"}
    _answer(_key(reload), "done", at=dt.datetime.now(dt.timezone.utc).isoformat())
    _run(page, "_poll")
    (again,) = _tools()
    assert again.args == em.args
    # A second load_first is shown, not chased round again.
    _answer(_key(again), "load_first",
            at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)).isoformat())
    _run(page, "_poll")
    assert _tools() == []
    assert pt.OUTCOME_TEXT["load_first"] in _joined(page)


def test_a_refusal_shows_the_workers_own_sentence(page):
    _loaded(page)
    _pick(page, NEAR)
    (em,) = _tools()
    _answer(_key(em), "error", error_text="No expected move could be drawn.")
    _run(page, "_poll")
    assert "No expected move could be drawn." in _joined(page)


def test_the_hourly_limit_is_the_calculators(page, monkeypatch):
    assert "_calc_live.TOOLS.allow(visitor)" in SRC.read_text(encoding="utf-8")
    monkeypatch.setattr(calc_live.TOOLS, "allow", lambda _visitor: False)
    with page:
        _enter(_field(page, ui.input, "Symbol"))
    assert _tools() == []
    assert calc_live.limit_text("tools") in _joined(page)


def test_a_calculator_position_handed_across_loads_and_draws_its_strikes(page, seed):
    seed["value"] = ("SPY", [
        {"option_type": "put", "side": "short", "strike": 495.0, "expiry": MID,
         "qty": 1, "premium": None},
        {"option_type": "put", "side": "long", "strike": 490.0, "expiry": MID,
         "qty": 1, "premium": None}])
    _open(page)
    (load,) = _tools()
    assert load.args == {"kind": "chain", "symbol": "SPY"}
    _publish_chain(_chain())
    _answer(_key(load), "done")
    _run(page, "_poll")
    assert _field(page, ui.select, "Expiry").value == MID
    (em,) = _tools()
    _result(_key(em), _cone(expiry=MID))
    _answer(_key(em), "done")
    _run(page, "_poll")
    lines = _chart(page).options["yAxis"]["plotLines"]
    assert [l["value"] for l in lines] == [495.0, 490.0]


# ── the boundary ────────────────────────────────────────────────────────────

def test_the_only_write_is_the_public_tools_request():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    writes = {c for c in calls if c.startswith("bus_client.") and "read" not in c}
    assert writes == {"bus_client.request_public_tool"}


def test_the_page_never_touches_the_owners_single_user_stores():
    src = SRC.read_text(encoding="utf-8")
    for owner in ("shared_position", "page_state", "app_settings",
                  "options:expected_move", "options:em_chain"):
        assert owner not in src, owner


def test_render_hands_off_to_the_public_page_before_building_anything():
    tree = ast.parse(pathlib.Path(expected_move.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "render")
    assert [a.arg for a in fn.args.args] == ["public"]
    body = [s for s in fn.body if not (isinstance(s, ast.Expr)
                                       and isinstance(s.value, ast.Constant))]
    assert ast.unparse(body[0]) == ("if public:\n    from . import em_live\n"
                                    "    return em_live.render()")


def test_the_expected_move_screen_is_published_untiled():
    (screen,) = [s for s in live_screens.SCREENS if s.slug == "expected-move"]
    assert (screen.route, screen.module, screen.private_route, screen.kwargs,
            screen.tile) == ("/expected-move", "options.expected_move",
                             "/options/expected-move", {"public": True}, False)


# ── the Strategy Tools strip ────────────────────────────────────────────────

def test_the_strips_tools_are_the_private_groups():
    import main
    assert [(r, label) for r, label, _i in main.STRATEGY_TOOLS_CHILDREN] == list(
        strategy_strip.TOOLS)


def test_the_strip_is_drawn_only_on_the_public_origin():
    with ui.card():
        assert strategy_strip.render("/options/expected-move") is None
    shell.publish(live_screens.PUBLIC_ROUTES)
    try:
        with ui.card():
            strip = strategy_strip.render("/options/expected-move")
        assert strip is not None and strip.value == "/options/expected-move"
        tabs = [el for el in _walk(strip) if isinstance(el, ui.tab)]
        assert [t._props["name"] for t in tabs] == [r for r, _l in strategy_strip.TOOLS]
    finally:
        shell.unpublish()


def test_the_strip_leaves_out_a_tool_this_origin_does_not_serve():
    shell.publish({"/options/calculator": "/calculator",
                   "/options/expected-move": "/expected-move"})
    try:
        assert [r for r, _l in strategy_strip.tabs()] == [
            "/options/calculator", "/options/expected-move"]
    finally:
        shell.unpublish()


def test_every_public_strategy_tool_draws_the_strip():
    from pages.options import sim_live
    for mod, route in ((calc_live, "/options/calculator"),
                       (sim_live, "/options/simulator"),
                       (em_live, "/options/expected-move")):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "strategy_strip.render(" in src, mod.__name__
        assert route in src, mod.__name__
