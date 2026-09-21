"""The PUBLIC Simulator (pages/options/sim_live.py) - Price & Time only.

The ``test_calc_live.py`` harness: the page is built for real, the service's
side is played by writing answer and result keys straight to the test bus, and
the page's own timers are driven by hand.

⚠ Collecting the timers pokes NiceGUI internals. If an upgrade moves them this
SKIPS with the reason rather than failing.
"""
import ast
import asyncio
import datetime as dt
import inspect
import pathlib

import pytest
from nicegui import ui

import bus_client
from pages.options import calc_live, pub_chain_view, sim_live, simulator
from shared import public_tools as pt

_TODAY = dt.date.today()
NEAR = (_TODAY + dt.timedelta(days=5)).isoformat()
MID = (_TODAY + dt.timedelta(days=12)).isoformat()
FAR = (_TODAY + dt.timedelta(days=120)).isoformat()
LISTED = [NEAR, MID, FAR]
STRIKES = [490.0, 495.0, 500.0, 505.0, 510.0]
SRC = pathlib.Path(sim_live.__file__)


def _meta(loaded=(NEAR, MID), symbol="SPY"):
    return {"symbol": symbol, "spot": 502.0, "n_contracts": 100,
            "expiries": list(loaded),
            "strikes": {e: {"call": list(STRIKES), "put": list(STRIKES)}
                        for e in loaded},
            "expirations": list(LISTED)}


def _answer(key, outcome, **extra):
    bus_client.bus().cache_set(pt.cache_key(pt.answer_view(key)),
                               {"outcome": outcome, **extra,
                                "at": dt.datetime.now(dt.timezone.utc).isoformat()})


def _result(key, payload):
    bus_client.bus().cache_set(pt.cache_key(pt.result_view(key)), payload)


# ── harness ─────────────────────────────────────────────────────────────────

def _walk(root):
    yield root
    for slot in getattr(root, "slots", {}).values():
        for child in slot.children:
            yield from _walk(child)


def _joined(root):
    return " | ".join(el.text for el in _walk(root)
                      if isinstance(getattr(el, "text", None), str))


def _all(root, cls):
    return [el for el in _walk(root) if cls in getattr(el, "_classes", [])]


def _timer(root, name):
    timers = [el for el in _walk(root) if isinstance(el, ui.timer)]
    if not timers:
        pytest.skip("cannot collect any ui.timer from the page")
    by_name = {getattr(t.callback, "__name__", ""): t for t in timers}
    assert name in by_name, f"no {name} timer; found {sorted(by_name)}"
    return by_name[name]


def _run(root, name):
    with root:
        result = _timer(root, name).callback()
        if asyncio.iscoroutine(result):
            asyncio.new_event_loop().run_until_complete(result)


def _click(root, label):
    btn = [el for el in _walk(root)
           if isinstance(el, ui.button) and getattr(el, "text", "") == label]
    assert len(btn) == 1, f"expected one {label!r} button, found {len(btn)}"
    with root:
        for listener in list(getattr(btn[0], "_event_listeners", {}).values()):
            if listener.type == "click":
                arity = len(inspect.signature(listener.handler).parameters)
                listener.handler(*((None,) if arity else ()))


_HANDLES = {}


def _editor():
    return _HANDLES["editor"]


def _sent(stream):
    return [c for _id, c in bus_client.bus().consume_commands(
        stream, group="t", consumer="c", block_ms=50)]


def _tools():
    return _sent(pt.TOOLS_STREAM)


def _math(kind=None):
    return [c for c in _sent(pt.MATH_STREAM)
            if kind is None or c.args.get("kind") == kind]


def _key(cmd):
    return pt.request_key({"type": cmd.type, "args": cmd.args})


def _slider(root, cls):
    found = [el for el in _walk(root) if isinstance(el, ui.slider)
             and cls in getattr(el, "_classes", [])]
    assert len(found) == 1
    return found[0]


def _chart(root):
    found = [el for el in _walk(root) if "sim-whatif" in getattr(el, "_classes", [])]
    assert len(found) == 1
    return found[0]


_SEED = {"value": None}


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(sim_live, "SWEEP_DELAY_SEC", 0.0)

    async def _connected():
        return None
    monkeypatch.setattr(sim_live, "_client_connected", _connected)
    from pages.options import public_handoff
    _SEED["value"] = None
    monkeypatch.setattr(public_handoff, "read", lambda: _SEED["value"])
    writes = []
    monkeypatch.setattr(public_handoff, "write", lambda *a: writes.append(a))
    _HANDLES["writes"] = writes
    bus_client.reset()
    _HANDLES.pop("editor", None)
    sim_live.TOOLS._seen.clear()
    sim_live.MATH._seen.clear()
    from pages.options import leg_editor
    real = leg_editor.build_leg_editor

    def _capture(*a, **kw):
        handle = real(*a, **kw)
        _HANDLES["editor"] = handle
        _HANDLES["editor_kw"] = kw
        return handle

    monkeypatch.setattr(leg_editor, "build_leg_editor", _capture)

    def _build():
        with ui.card() as root:
            simulator.render(public=True)
        return root
    return _build


def _land_snapshot(root, cmd, meta=None):
    key = _key(cmd)
    _result(key, meta or _meta(symbol=cmd.args["symbol"]))
    _answer(key, "done")
    _run(root, "_poll")


def _loaded(build):
    """A built page with SPY loaded and its snapshot landed, queues drained."""
    root = build()
    _run(root, "_seed_from_handoff")
    _click(root, "Load")
    cmds = _tools()
    assert [c.args for c in cmds] == [{"kind": "sim_snapshot", "symbol": "SPY"}]
    _land_snapshot(root, cmds[0])
    return root


def _swept(root):
    """Run the debounce and return the one sweep it sent."""
    _run(root, "_sweep_tick")
    sweeps = _math("sweep")
    assert len(sweeps) == 1, sweeps
    return sweeps[0]


def _sweep_result(cmd, rows=None):
    return {"whatif_rows": rows or [{"S": 490.0, "theo_price": -400.0},
                                    {"S": 502.0, "theo_price": -100.0},
                                    {"S": 515.0, "theo_price": 0.0}],
            "whatif_baseline": -100.0, "spot": 502.0,
            "legs": cmd.args["legs"], "dt": cmd.args["dt"]}


def _land_sweep(root, cmd, rows=None):
    key = _key(cmd)
    _result(key, _sweep_result(cmd, rows))
    _answer(key, "done")
    _run(root, "_poll")


# ── pure ────────────────────────────────────────────────────────────────────

def test_the_snapshot_readers_take_the_meta_shape():
    meta = _meta()
    assert pub_chain_view.snapshot_loaded(meta) == [NEAR, MID]
    assert pub_chain_view.snapshot_listed(meta) == LISTED
    assert pub_chain_view.ladder_strikes(meta, NEAR, "put") == STRIKES
    eager = {k: v for k, v in meta.items() if k != "expirations"}
    assert pub_chain_view.snapshot_listed(eager) == [NEAR, MID]
    for bad in (None, {}, "x", {"expiries": "x"}, {"expiries": [NEAR], "strikes": []}):
        assert pub_chain_view.snapshot_loaded(bad) == []
        assert pub_chain_view.snapshot_listed(bad) == []
    junk = {"expiries": [3, NEAR, MID], "strikes": {NEAR: {}}}
    assert pub_chain_view.snapshot_loaded(junk) == [NEAR]


def test_share_legs_are_split_off_and_counted():
    legs = [{"option_type": "stock", "side": "long", "qty": 1, "strike": None,
             "expiry": None, "premium": None},
            {"option_type": "call", "side": "short", "qty": 1, "strike": 505.0,
             "expiry": NEAR, "premium": 1.0}]
    options, dropped = sim_live.split_seed(legs)
    assert dropped == 1 and [l["option_type"] for l in options] == ["call"]


def test_leg_expiries_are_distinct_nearest_first_and_at_most_two():
    legs = [{"option_type": "put", "expiry": e} for e in (FAR, NEAR, MID, NEAR)]
    assert sim_live.leg_expiries(legs) == [NEAR, MID]
    assert sim_live.leg_expiries([]) == []


def test_the_sweep_request_maps_kind_and_clamps_the_days():
    legs = [{"option_type": "put", "side": "short", "strike": 500.0, "expiry": NEAR,
             "qty": 1, "premium": None}]
    req = sim_live.sweep_request("SPY", legs, 40.0, 6.0)
    assert req["dt"] == 6.0
    assert req["legs"] == [{"kind": "put", "strike": 500.0, "expiry": NEAR,
                            "side": "short", "qty": 1}]
    assert pt.math_command(req) is not None
    assert sim_live.sweep_request("SPY", [{**legs[0], "strike": None}], 1, 6) is None


def test_an_answer_already_consumed_is_not_taken_again():
    now = dt.datetime.now(dt.timezone.utc)
    old = {"outcome": "load_first", "at": now.isoformat()}
    assert sim_live.answer_state(old, now)[0] is True
    assert sim_live.answer_state(old, now, after=now) == (False, None)
    newer = {"outcome": "load_first",
             "at": (now + dt.timedelta(seconds=1)).isoformat()}
    assert sim_live.answer_state(newer, now, after=now)[0] is True


def test_the_limiters_are_the_calculators():
    assert sim_live.TOOLS is calc_live.TOOLS and sim_live.MATH is calc_live.MATH


# ── a page load, and the seed ───────────────────────────────────────────────

def test_a_page_load_with_nothing_seeded_sends_nothing(page):
    root = page()
    _run(root, "_seed_from_handoff")
    _run(root, "_sweep_tick")
    _run(root, "_poll")
    assert _tools() == [] and _math() == []
    assert _editor().get_legs()                     # the default template


def test_a_seeded_tab_sends_one_snapshot_with_its_expirations(page):
    legs = [{"option_type": "put", "side": "short", "qty": 1, "strike": 500.0,
             "expiry": MID, "premium": 1.5},
            {"option_type": "put", "side": "long", "qty": 1, "strike": 495.0,
             "expiry": NEAR, "premium": 0.5},
            {"option_type": "call", "side": "long", "qty": 1, "strike": 510.0,
             "expiry": FAR, "premium": 0.5}]
    _SEED["value"] = ("QQQ", legs)
    root = page()
    _run(root, "_seed_from_handoff")
    cmds = _tools()
    assert [c.args for c in cmds] == [{"kind": "sim_snapshot", "symbol": "QQQ",
                                       "expiries": [NEAR, MID]}]
    shown = _editor().get_legs()
    assert [(l["option_type"], l["strike"], l["expiry"]) for l in shown] == \
        [(l["option_type"], l["strike"], l["expiry"]) for l in legs]
    assert _all(root, "entry-ticker")[0].value == "QQQ"
    # ...and the legs survive the snapshot landing
    _land_snapshot(root, cmds[0], _meta(loaded=(NEAR, MID, FAR), symbol="QQQ"))
    assert [(l["strike"], l["expiry"]) for l in _editor().get_legs()] == \
        [(l["strike"], l["expiry"]) for l in legs]


def test_arriving_with_a_position_counts_as_the_visitors_action(page):
    _SEED["value"] = ("SPY", [{"option_type": "put", "side": "short", "qty": 1,
                               "strike": 500.0, "expiry": NEAR, "premium": 1.0}])
    root = page()
    _run(root, "_seed_from_handoff")
    _land_snapshot(root, _tools()[0])
    cmd = _swept(root)
    assert cmd.args["legs"] == [{"kind": "put", "strike": 500.0, "expiry": NEAR,
                                 "side": "short", "qty": 1}]


def test_a_seeded_strike_the_snapshot_does_not_list_snaps_onto_its_ladder(page):
    _SEED["value"] = ("SPY", [{"option_type": "put", "side": "short", "qty": 1,
                               "strike": 497.0, "expiry": NEAR, "premium": 1.0}])
    root = page()
    _run(root, "_seed_from_handoff")
    assert _editor().get_legs()[0]["strike"] == 497.0      # shown as it came
    _land_snapshot(root, _tools()[0])
    assert _editor().get_legs()[0]["strike"] in STRIKES    # on the real ladder


def test_a_seeded_share_leg_is_dropped_and_said(page):
    legs = [{"option_type": "stock", "side": "long", "qty": 1, "strike": None,
             "expiry": None, "premium": 502.0},
            {"option_type": "call", "side": "short", "qty": 1, "strike": 505.0,
             "expiry": NEAR, "premium": 1.0}]
    _SEED["value"] = ("SPY", legs)
    root = page()
    note = _all(root, "sim-shares-note")[0]
    assert not note.visible
    _run(root, "_seed_from_handoff")
    assert [l["option_type"] for l in _editor().get_legs()] == ["call"]
    assert note.visible and note.text == sim_live.SHARES_DROPPED
    assert [c.args["expiries"] for c in _tools()] == [[NEAR]]


def test_the_page_never_writes_the_hand_off(page):
    _SEED["value"] = ("SPY", [{"option_type": "put", "side": "short", "qty": 1,
                               "strike": 500.0, "expiry": NEAR, "premium": 1.0}])
    root = page()
    _run(root, "_seed_from_handoff")
    _click(root, "Load")
    assert _HANDLES["writes"] == []


# ── loading ─────────────────────────────────────────────────────────────────

def test_load_sends_one_snapshot_on_the_tools_stream_only(page):
    root = page()
    _click(root, "Load")
    cmds = _tools()
    assert [(c.type, c.args) for c in cmds] == [
        (pt.TOOLS_TYPE, {"kind": "sim_snapshot", "symbol": "SPY"})]
    assert _math() == []
    assert bus_client.bus().consume_commands(
        "cmd:options", group="t", consumer="c", block_ms=50) == []


def test_a_bad_symbol_sends_nothing_and_says_so(page):
    root = page()
    field = _all(root, "entry-ticker")[0]
    with root:
        field.value = "NOT A SYMBOL!!"
    _click(root, "Load")
    assert _tools() == []
    assert field.error and "not a symbol this page can load" in field.error


def test_a_landed_snapshot_fills_the_strip_and_lays_the_template(page):
    root = _loaded(page)
    assert len(_all(root, "entry-expiry")) == len(LISTED)
    legs = _editor().get_legs()
    assert legs and all(l["strike"] in STRIKES and l["expiry"] == NEAR for l in legs)


def test_the_page_builds_no_grid_no_price_and_no_delta(page):
    _loaded(page)
    kw = _HANDLES["editor_kw"]
    assert kw["show_premium"] is False and kw["delta_for"] is None
    root = page()
    assert _all(root, "entry-gridbody") == []
    assert _all(root, "leg-price") == [] and _all(root, "leg-delta") == []


def test_a_refused_load_says_why(page):
    root = page()
    _click(root, "Load")
    _answer(_key(_tools()[0]), "closed")
    _run(root, "_poll")
    assert pt.OUTCOME_TEXT["closed"] in _joined(root)


def test_a_far_expiration_asks_for_sim_expiry(page):
    root = _loaded(page)
    _math()
    pills = _all(root, "entry-expiry")
    with root:
        for listener in list(pills[2]._event_listeners.values()):
            if listener.type == "click":
                listener.handler(None)
    cmds = _tools()
    assert [c.args for c in cmds] == [{"kind": "sim_expiry", "symbol": "SPY",
                                       "expiry": FAR}]
    _result(_key(cmds[0]), _meta(loaded=(NEAR, MID, FAR)))
    _answer(_key(cmds[0]), "done")
    _run(root, "_poll")
    assert all(l["expiry"] == FAR for l in _editor().get_legs())


# ── the sweep ───────────────────────────────────────────────────────────────

def test_a_landed_snapshot_sweeps_the_position_once(page):
    root = _loaded(page)
    cmd = _swept(root)
    assert cmd.args["symbol"] == "SPY" and len(cmd.args["legs"]) == 2
    assert all(set(l) == {"kind", "strike", "expiry", "side", "qty"}
               for l in cmd.args["legs"])


def test_a_days_move_sends_one_sweep_after_the_debounce(page, monkeypatch):
    monkeypatch.setattr(sim_live, "SWEEP_DELAY_SEC", 0.4)
    clock = [1000.0]
    monkeypatch.setattr(sim_live, "_clock", lambda: clock[0])
    root = _loaded(page)
    clock[0] += 1
    _swept(root)                                     # the landing's own sweep
    with root:
        _slider(root, "sim-days").value = 2
        _slider(root, "sim-days").value = 3          # a drag: two steps
    _run(root, "_sweep_tick")
    assert _math("sweep") == []                      # still inside the quiet time
    clock[0] += 0.5
    cmd = _swept(root)
    assert cmd.args["dt"] == 3.0


def test_the_price_offset_slider_sends_nothing(page, monkeypatch):
    root = _loaded(page)
    _land_sweep(root, _swept(root))
    # A spy on the builder as well as the streams: an identical sweep would be
    # skipped as "already on screen", so the streams alone could not tell a
    # slider that asks from one that does not.
    built = []
    real = sim_live.sweep_request
    monkeypatch.setattr(sim_live, "sweep_request",
                        lambda *a: (built.append(a), real(*a))[1])
    with root:
        _slider(root, "sim-price").value = 5
    _run(root, "_sweep_tick")
    _run(root, "_poll")
    assert built == []
    assert _math() == [] and _tools() == []
    lines = _chart(root).options["xAxis"]["plotLines"]
    assert any(pl["value"] == pytest.approx(502.0 * 1.05) for pl in lines)


def test_a_sweep_result_draws_the_chart_and_the_tiles(page):
    root = _loaded(page)
    _land_sweep(root, _swept(root))
    chart = _chart(root)
    assert chart.visible
    assert chart.options["series"][0]["data"] == [[490.0, -300.0], [502.0, 0.0],
                                                  [515.0, 100.0]]
    text = _joined(root)
    assert "Entry credit" in text
    assert _all(root, "sim-readout")[0].text.startswith("At 502.00")


def test_the_chart_exists_at_build_with_an_explicit_height(page):
    root = page()
    chart = _chart(root)
    assert isinstance(chart.options["chart"]["height"], int)
    assert chart.options["chart"]["height"] > 0


def test_a_late_sweep_answer_for_an_older_request_does_not_paint(page):
    root = _loaded(page)
    first = _swept(root)
    with root:
        _slider(root, "sim-days").value = 2
    second = _swept(root)
    assert _key(first) != _key(second)
    _land_sweep(root, second, rows=[{"S": 500.0, "theo_price": 50.0}])
    _land_sweep(root, first, rows=[{"S": 500.0, "theo_price": -999.0}])
    assert _chart(root).options["series"][0]["data"] == [[500.0, 150.0]]


def test_reverting_to_the_shown_position_never_paints_the_edit(page):
    root = _loaded(page)
    first = _swept(root)
    _land_sweep(root, first, rows=[{"S": 500.0, "theo_price": 50.0}])
    with root:
        _slider(root, "sim-days").value = 2
    second = _swept(root)
    with root:
        _slider(root, "sim-days").value = first.args["dt"]
    _run(root, "_sweep_tick")
    assert _math("sweep") == []                      # its result is on screen
    _land_sweep(root, second, rows=[{"S": 500.0, "theo_price": -999.0}])
    assert _chart(root).options["series"][0]["data"] == [[500.0, 150.0]]


def test_load_first_reloads_once_and_does_not_loop(page):
    root = _loaded(page)
    sweep = _swept(root)
    _answer(_key(sweep), "load_first")
    _run(root, "_poll")
    reload = _tools()
    assert [c.args for c in reload] == [{"kind": "sim_snapshot", "symbol": "SPY",
                                         "expiries": [NEAR]}]
    _land_snapshot(root, reload[0])
    again = _swept(root)
    assert _key(again) == _key(sweep)
    _answer(_key(again), "load_first")
    _run(root, "_poll")
    assert _tools() == []                            # no second automatic reload
    assert pt.OUTCOME_TEXT["load_first"] in _joined(root)
    # Load is still there, and a visitor action may reload again
    _click(root, "Load")
    assert len(_tools()) == 1


def test_a_refused_reload_says_why(page):
    root = _loaded(page)
    _answer(_key(_swept(root)), "load_first")
    _run(root, "_poll")
    _answer(_key(_tools()[0]), "budget")
    _run(root, "_poll")
    assert pt.OUTCOME_TEXT["budget"] in _joined(root)


@pytest.mark.parametrize("outcome", ["off_ladder", "throttled", "closed", "error"])
def test_other_refusals_show_their_text(page, outcome):
    root = _loaded(page)
    _answer(_key(_swept(root)), outcome)
    _run(root, "_poll")
    assert pt.OUTCOME_TEXT[outcome] in _joined(root)
    assert _tools() == []


def test_a_visitor_over_the_math_limit_sends_nothing(page, monkeypatch):
    root = _loaded(page)
    monkeypatch.setattr(sim_live.MATH, "allow", lambda key: False)
    _run(root, "_sweep_tick")
    assert _math() == []
    assert sim_live.limit_text("math") in _joined(root)


def test_a_visitor_over_the_tools_limit_sends_nothing(page, monkeypatch):
    monkeypatch.setattr(sim_live.TOOLS, "allow", lambda key: False)
    root = page()
    _click(root, "Load")
    assert _tools() == []
    assert sim_live.limit_text("tools") in _joined(root)


def test_the_now_snap_moves_the_days_slider_and_sweeps(page):
    root = _loaded(page)
    _swept(root)
    _click(root, "Now")
    assert _slider(root, "sim-days").value == 0
    assert _swept(root).args["dt"] == 0.0


def test_the_days_slider_fits_the_legs(page):
    root = _loaded(page)
    slider = _slider(root, "sim-days")
    from pages.options import sim_view
    r = sim_view.days_range(_editor().get_legs())
    assert slider._props["max"] == r["max"] and slider._props["step"] == r["step"]
    assert slider.value <= r["max"]


# ── source-level ────────────────────────────────────────────────────────────

def _tree():
    return ast.parse(SRC.read_text(encoding="utf-8"))


def test_the_source_reads_the_hand_off_and_never_writes_it():
    calls = {ast.unparse(n.func) for n in ast.walk(_tree()) if isinstance(n, ast.Call)}
    assert "public_handoff.read" in calls
    assert not any(c.endswith(".write") or c == "write" for c in calls)
    text = SRC.read_text(encoding="utf-8")
    assert "public_handoff.write" not in text and "_tab()" not in text


def test_the_source_has_no_replay_multiplier_or_other_tabs():
    tree = _tree()
    text = SRC.read_text(encoding="utf-8")
    for word in ("sim_replay", "TAB_VOLATILITY", "TAB_HISTORY", "mult_slider",
                 "ivshock_table", "replay_figure"):
        assert word not in text, word
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
              and isinstance(n.value, str)}
    assert "mult" not in consts
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
        {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {n for n in names if n == "mult" or n.startswith("mult_")}


def test_the_page_module_enqueues_only_through_the_public_requests():
    tree = _tree()
    writes = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            name = n.func.attr
            if name.startswith(("request", "enqueue")) or name in ("cache_set",
                                                                    "publish"):
                writes.add(ast.unparse(n.func))
    assert writes - {"pt.request_key"} == {"bus_client.request_public_tool",
                                          "bus_client.request_public_math"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and (n.module or "").split(".")[-1] == "bus_client"]


def test_the_page_module_names_no_owner_view():
    text = SRC.read_text(encoding="utf-8")
    for view in ("sim_meta", "sim_chain", "sim_result", "sim_replay",
                 "_SIM_SNAPSHOTS", "_LAST_SIM", "shared_position", "page_state",
                 "cmd:options"):
        assert view not in text, view


_ALLOWED_IMPORTS = {"nicegui", "bus_client", "visitor_limit", "shared.public_tools",
                    "shared.symbols", "shared.market_calendar", "shared"}
_STDLIB = {"__future__", "datetime", "logging", "math", "time", "zoneinfo"}


def test_the_page_imports_only_the_tier1_allow_list():
    for n in ast.walk(_tree()):
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                continue
            mods = [n.module or ""]
            if n.module == "shared":
                mods = [f"shared.{a.name}" for a in n.names]
        else:
            continue
        for mod in mods:
            ok = (mod in _ALLOWED_IMPORTS or mod.split(".")[0] in _STDLIB
                  or mod == "pages" or mod.startswith("pages."))
            assert ok, f"sim_live imports {mod}"
