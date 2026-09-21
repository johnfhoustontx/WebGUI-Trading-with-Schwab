"""The public Calculator and Simulator keep every visitor's position to itself.

Both pages run in ONE process that serves every anonymous visitor, so the one
failure that matters is a position crossing between them - visitor A's legs
seeding visitor B's Simulator, or the OWNER's private Calculator state showing
on the public page. The private pages keep their position in single-user
module stores (``calculator._LAST_CALC``, ``simulator._LAST_SIM``,
``shared_position``); the public pages hand off through NiceGUI TAB storage
(``public_handoff``), keyed per browser tab.

The seam is ``public_handoff._tab`` - the one function that returns the tab's
store. Each test gives each simulated visitor a dict of its own there and
drives the REAL ``write`` / ``read``, so a hand-off that reached for a shared
store instead would put one visitor's position in front of another.

The harness is ``test_calc_live.py``'s: the page is built for real and its own
timers are driven by hand. ⚠ Collecting timers pokes NiceGUI internals; an
upgrade that moves them SKIPS these tests with the reason rather than failing.
"""
import asyncio
import datetime as dt
import inspect

import pytest
from nicegui import ui

import bus_client
from pages.options import (calc_live, calculator, entry_panel, leg_editor,
                           public_handoff, shared_position, sim_live, simulator)
from shared import public_tools as pt

_TODAY = dt.date.today()
NEAR = (_TODAY + dt.timedelta(days=5)).isoformat()
MID = (_TODAY + dt.timedelta(days=12)).isoformat()

# Two visitors' positions, deliberately different in every field.
A_SYMBOL, A_LEGS = "AAPL", [
    {"option_type": "put", "side": "short", "strike": 215.0, "expiry": NEAR,
     "qty": 1, "premium": 1.5},
    {"option_type": "put", "side": "long", "strike": 210.0, "expiry": NEAR,
     "qty": 1, "premium": 0.5},
]
B_SYMBOL, B_LEGS = "MSFT", [
    {"option_type": "call", "side": "long", "strike": 455.0, "expiry": MID,
     "qty": 2, "premium": 3.25},
]

# The owner's private position: a ticker and strike no public default uses.
SENTINEL_SYMBOL = "ZQXV"
SENTINEL_STRIKE = 777.0
SENTINEL_LEGS = [{"option_type": "call", "side": "short", "strike": SENTINEL_STRIKE,
                  "expiry": NEAR, "qty": 3, "premium": 4.2}]


# ── harness ─────────────────────────────────────────────────────────────────

def _walk(root):
    yield root
    for slot in getattr(root, "slots", {}).values():
        for child in slot.children:
            yield from _walk(child)


def _everything_shown(root):
    """Every label text AND every widget value on the page, as one string - a
    symbol typed into the ticker box is a value, not a text."""
    parts = []
    for el in _walk(root):
        for attr in ("text", "value"):
            v = getattr(el, attr, None)
            if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                parts.append(str(v))
    return " | ".join(parts)


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


def _tools():
    return [c for _id, c in bus_client.bus().consume_commands(
        pt.TOOLS_STREAM, group="t", consumer="c", block_ms=50)]


def _math():
    return [c for _id, c in bus_client.bus().consume_commands(
        pt.MATH_STREAM, group="t", consumer="c", block_ms=50)]


def _strikes(legs):
    return sorted(leg.get("strike") for leg in legs if leg.get("strike") is not None)


# The tab store each simulated visitor sees; switched before acting on a root.
_ACTIVE = {"store": None}
# The newest leg editor / entry panel the page built.
_HANDLES: dict = {}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(ui.navigate, "to", lambda *a, **k: None)
    monkeypatch.setattr(calc_live, "RECALC_DELAY_SEC", 0.0)
    monkeypatch.setattr(sim_live, "SWEEP_DELAY_SEC", 0.0)

    async def _connected():
        return None
    monkeypatch.setattr(sim_live, "_client_connected", _connected)
    # THE seam: each visitor's tab store. The real write/read run on top of it.
    _ACTIVE["store"] = None
    monkeypatch.setattr(public_handoff, "_tab", lambda: _ACTIVE["store"])
    bus_client.reset()
    for mod in (calc_live, sim_live):
        mod.TOOLS._seen.clear()
        mod.MATH._seen.clear()

    handles = _HANDLES
    real_editor = leg_editor.build_leg_editor
    real_panel = entry_panel.build_entry_panel

    def _editor(*a, **kw):
        handles["editor"] = real_editor(*a, **kw)
        return handles["editor"]

    def _panel(*a, **kw):
        handles["panel"] = real_panel(*a, **kw)
        return handles["panel"]

    monkeypatch.setattr(leg_editor, "build_leg_editor", _editor)
    monkeypatch.setattr(entry_panel, "build_entry_panel", _panel)

    def build(module, store):
        """One visitor's page over its own tab store: ``(root, editor, panel)``."""
        _ACTIVE["store"] = store
        handles.clear()
        with ui.card() as root:
            module.render(public=True)
        return root, handles["editor"], handles["panel"]

    yield build
    shared_position.reset()
    calculator._LAST_CALC.clear()
    simulator._LAST_SIM.clear()


def _chain(symbol, legs):
    """The published public chain for ``symbol``, listing the legs' strikes."""
    exps = sorted({leg["expiry"] for leg in legs} | {NEAR, MID})
    ladder = sorted({leg["strike"] for leg in legs}
                    | {leg["strike"] + d for leg in legs for d in (-5.0, 5.0)})
    return {"symbol": symbol, "api": symbol, "spot": ladder[len(ladder) // 2],
            "expirations": exps,
            "strikes": {e: {"call": list(ladder), "put": list(ladder)} for e in exps},
            "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def _calc_with(build, store, symbol, legs):
    """A public Calculator in ``store`` that loaded ``symbol``'s chain and shows
    ``legs``, handed off through the page's own Open in Simulator path."""
    root, _editor, panel = build(calculator, store)
    with root:
        panel.symbol_in.value = symbol
    _ACTIVE["store"] = store
    _click(root, "Load")
    cmd = [c for c in _tools() if c.args.get("kind") == "chain"]
    assert [c.args["symbol"] for c in cmd] == [symbol]
    bus = bus_client.bus()
    bus.cache_set(pt.cache_key(pt.chain_view(symbol)), _chain(symbol, legs))
    bus.cache_set(pt.cache_key(pt.answer_view(
        pt.request_key({"type": cmd[0].type, "args": cmd[0].args}))),
        {"outcome": "done", "at": dt.datetime.now(dt.timezone.utc).isoformat()})
    _run(root, "_poll")
    editor = _HANDLES["editor"]             # a quotes-mode change rebuilds it
    with root:
        editor.set_legs([dict(leg) for leg in legs])
    assert _strikes(editor.get_legs()) == _strikes(legs)
    _ACTIVE["store"] = store
    _click(root, "Open in Simulator")
    return root


def _sim_seeded(build, store):
    """A public Simulator in ``store``, its seed timer run, and what it sent."""
    root, editor, panel = build(simulator, store)
    _tools()                                    # nothing from the build itself
    _ACTIVE["store"] = store
    _run(root, "_seed_from_handoff")
    return root, editor, panel, _tools()


# ── two visitors ────────────────────────────────────────────────────────────

def test_two_visitors_never_see_each_others_position(harness):
    store_a, store_b = {}, {}
    _calc_with(harness, store_a, A_SYMBOL, A_LEGS)
    _calc_with(harness, store_b, B_SYMBOL, B_LEGS)

    # Each tab holds its own visitor's position, and only that.
    assert public_handoff.KEY in store_a and public_handoff.KEY in store_b
    assert store_a[public_handoff.KEY]["symbol"] == A_SYMBOL
    assert store_b[public_handoff.KEY]["symbol"] == B_SYMBOL

    # Each visitor's Simulator seeds from its OWN tab: symbol, strikes, and the
    # one snapshot request it sends.
    for store, symbol, legs in ((store_a, A_SYMBOL, A_LEGS),
                                (store_b, B_SYMBOL, B_LEGS)):
        root, editor, panel, sent = _sim_seeded(harness, store)
        assert panel.symbol_in.value == symbol
        assert _strikes(editor.get_legs()) == _strikes(legs)
        assert [c.args["symbol"] for c in sent] == [symbol]
        other = B_SYMBOL if symbol == A_SYMBOL else A_SYMBOL
        assert other not in _everything_shown(root)


def test_a_new_tab_seeds_nothing_and_sends_nothing(harness):
    # Another visitor has a position in THEIR tab...
    _calc_with(harness, {}, A_SYMBOL, A_LEGS)
    _tools()
    _math()
    # ...and a new tab, with an empty store, builds a Simulator.
    root, editor, panel, sent = _sim_seeded(harness, {})
    assert sent == []
    assert _math() == []
    assert panel.symbol_in.value != A_SYMBOL
    assert _strikes(editor.get_legs()) != _strikes(A_LEGS)
    assert A_SYMBOL not in _everything_shown(root)


# ── the owner's private state ───────────────────────────────────────────────

def _plant_private_sentinel():
    shared_position.publish(SENTINEL_SYMBOL, "BEAR_CALL", SENTINEL_LEGS, NEAR)
    calculator._LAST_CALC.update({"symbol": SENTINEL_SYMBOL, "strategy": "BEAR_CALL",
                                  "legs": [dict(leg) for leg in SENTINEL_LEGS],
                                  "expiry": NEAR})
    simulator._LAST_SIM.update({"symbol": SENTINEL_SYMBOL, "strategy": "BEAR_CALL",
                                "legs": [dict(leg) for leg in SENTINEL_LEGS]})
    assert shared_position.current()["symbol"] == SENTINEL_SYMBOL


def _assert_no_sentinel(root, editor, store):
    shown = _everything_shown(root)
    assert SENTINEL_SYMBOL not in shown
    assert "777" not in shown
    assert SENTINEL_STRIKE not in _strikes(editor.get_legs())
    stored = store.get(public_handoff.KEY) or {}
    assert stored.get("symbol") != SENTINEL_SYMBOL
    assert SENTINEL_STRIKE not in _strikes(stored.get("legs") or [])


def test_the_public_calculator_never_shows_the_private_pages_position(harness):
    _plant_private_sentinel()
    store = {}
    root, editor, _panel = harness(calculator, store)
    _run(root, "_load_default")
    _run(root, "_recalc_tick")
    _assert_no_sentinel(root, editor, store)
    # Nothing it sends names the owner's position either.
    for cmd in _tools() + _math():
        assert SENTINEL_SYMBOL not in repr(cmd.args)


def test_the_public_simulator_never_shows_the_private_pages_position(harness):
    _plant_private_sentinel()
    store = {}
    root, editor, _panel, sent = _sim_seeded(harness, store)
    _run(root, "_sweep_tick")
    _assert_no_sentinel(root, editor, store)
    assert sent == []
    for cmd in _math():
        assert SENTINEL_SYMBOL not in repr(cmd.args)
