"""The Trade Plan screen on the page kit (Phase 5, Task 3).

The frame is the shell's (Task 1), so what is this screen's own is the pair of
actions at the foot of the plan card — and the refusal one of them used to make
AFTER the click.

⚠ Both stay INSIDE the plan card rather than moving to ``head.actions``.
``plan_card.set_visibility(actionable)`` hides the card when there is no plan,
and a header action is a promise that the page always offers it; a Find strikes
sitting in the header over a screen that says "No trade" is a different claim
from one that disappears with the plan it belongs to.
"""
import inspect
import pathlib

from nicegui import ui

from pages import trade_plan_screen as tps
from pages import trade_terminal as tt
from pages import ui_kit as kit
from pages.options import theme as _t

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"

# An actionable plan. `plan_rows` returns [] unless `action` is set to
# something other than "none", and `_build` needs BOTH rows and an action
# before it shows the card at all.
_PLAN = {"action": "credit", "structure": "put credit spread",
         "dte_min": 30, "dte_max": 45, "entry_zone": "1.20–1.45 credit",
         "time_stop_trading_days": 20, "time_stop_date": "2026-10-17",
         "rationale": "Top-band composite with the tape cleared."}

_ANALYSIS = {"symbol": "MU", "price": 178.4, "trade_plan": _PLAN,
             "direction_clearance": {"long": {"state": "cleared", "reasons": []},
                                     "short": {"state": "relative_only",
                                               "reasons": ["SPY above a rising 200-DMA"]}},
             "swing_model": {"percentile": 91}}

# The same plan with no structure: actionable (it has rows and an action) but
# with nothing the Calculator can model. This is the case that used to be a
# toast AFTER the click.
_NO_STRUCTURE = dict(_ANALYSIS,
                     trade_plan={k: v for k, v in _PLAN.items() if k != "structure"})


def _src():
    return (PAGES / "trade_plan_screen.py").read_text(encoding="utf-8")


def _render(analysis, monkeypatch):
    """Render the screen over ``analysis`` and return ONLY its own elements.

    ``ui.context.client.elements`` is the auto-index client the whole module
    shares, so a plain ``elements.values()`` also hands back widgets another
    test's render left behind, and an assertion about "the buttons on this
    page" then passes off someone else's page."""
    import bus_client
    monkeypatch.setattr(bus_client, "read",
                        lambda v: analysis if v == "trade:analysis" else {})
    before = set(ui.context.client.elements)
    with ui.card():
        tps.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _buttons(els):
    return {e.text: e for e in els if isinstance(e, ui.button)}


def _descends_from(child, ancestor):
    node = child
    while node is not None:
        if node is ancestor:
            return True
        slot = getattr(node, "parent_slot", None)
        node = slot.parent if slot is not None else None
    return False


class TestTheTwoActionsAreTheKits:
    def test_both_actions_go_through_the_kit(self, monkeypatch):
        els = _render(_ANALYSIS, monkeypatch)
        btns = _buttons(els)
        assert "Find strikes" in btns and "Open in calculator" in btns
        assert _t.BTN_PRIMARY in " ".join(btns["Find strikes"].classes), \
            "Find strikes is the plan's one main action"
        assert _t.BTN in " ".join(btns["Open in calculator"].classes), \
            "Open in calculator is a secondary action"

    def test_the_screen_builds_no_raw_button_dialog_or_toast(self):
        src = _src()
        for raw in ("ui.button(", "ui.dialog(", "ui.notify(", "ui.table("):
            assert raw not in src, f"{raw} must go through pages/ui_kit.py"

    def test_the_actions_stay_inside_the_plan_card(self, monkeypatch):
        """Not ``head.actions``. The card hides itself when there is no plan,
        and an action in the header is a promise the page always offers it."""
        els = _render(_ANALYSIS, monkeypatch)
        btns = _buttons(els)
        header_actions = btns["Deep Dive"].parent_slot.parent
        for label in ("Find strikes", "Open in calculator"):
            assert not _descends_from(btns[label], header_actions), \
                f"{label} belongs to the plan card, not the page header"


class TestTheRefusalIsShownBeforeTheClickNotAfter:
    """The page KNOWS a plan with no structure cannot be modelled — that is
    exactly what ``calculator_handoff`` returns None for — so making the reader
    click to be told is a refusal disguised as an action.

    ⚠ ``kit.gate`` does not reach this: it reads ``field.validation`` and a
    button is not a field (measured in Phase 2). The button is disabled in
    ``_paint`` instead, and carries a tooltip — a dead control with no
    explanation is worse than the toast it replaces."""

    def test_a_plan_with_no_structure_cannot_be_modelled(self):
        """Non-vacuity: the fixture really is the refusing case."""
        assert tt.calculator_handoff(_NO_STRUCTURE) is None
        assert tt.calculator_handoff(_ANALYSIS) is not None

    def test_the_calculator_button_is_dead_when_there_is_nothing_to_model(
            self, monkeypatch):
        els = _render(_NO_STRUCTURE, monkeypatch)
        btn = _buttons(els)["Open in calculator"]
        assert not btn.enabled, \
            "the page knows this before the click; it must not invite one"

    def test_a_disabled_button_still_says_why(self, monkeypatch):
        els = _render(_NO_STRUCTURE, monkeypatch)
        btn = _buttons(els)["Open in calculator"]
        tips = [e for e in els if isinstance(e, ui.tooltip)
                and _descends_from(e, btn)]
        assert tips, "a dead control with no explanation is worse than a toast"
        assert any("structure" in (t.text or "") for t in tips)

    def test_it_is_live_when_the_plan_names_a_structure(self, monkeypatch):
        els = _render(_ANALYSIS, monkeypatch)
        assert _buttons(els)["Open in calculator"].enabled

    def test_find_strikes_is_never_gated_on_the_structure(self, monkeypatch):
        """It hands the SYMBOL to the Strategy Finder, which builds every
        structure itself — so a plan that names none is exactly when it is
        most useful."""
        els = _render(_NO_STRUCTURE, monkeypatch)
        assert _buttons(els)["Find strikes"].enabled

    def test_the_paint_decides_it_rather_than_the_build(self):
        """The plan changes under the page on every analysis, so the gate has
        to be re-decided with it."""
        src = inspect.getsource(tps._build)
        assert "calculator_handoff(" in src and "set_enabled(" in src
