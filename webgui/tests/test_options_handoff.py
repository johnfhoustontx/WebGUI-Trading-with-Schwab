"""Tests for the cross-page signal handoff (Send to Calculator / Paper trade)."""
import inspect

from pages.options import handoff


def test_pending_calculator_set_and_take():
    handoff.set_pending_calculator({"symbol": "MU", "type": "PCS"})
    sig = handoff.take_pending_calculator()
    assert sig == {"symbol": "MU", "type": "PCS"}
    # consumed once
    assert handoff.take_pending_calculator() is None


def test_take_pending_calculator_empty():
    handoff.take_pending_calculator()  # clear any
    assert handoff.take_pending_calculator() is None


def _paper_button(slot):
    """The ``<q-btn …>`` block for the Send-to-Paper action in a row-actions slot."""
    return next(b for b in slot.split("<q-btn") if "to_paper" in b)


def test_actions_slot_gates_the_paper_button_on_allow_paper():
    """A row the page has marked un-tradeable must not offer Paper. The Scanner's
    day union keeps DROPPED signals on screen frozen at an hours-old price, and
    ``paper_create`` records ``signal['credit']`` VERBATIM — so an ungated button
    books a fictional entry. Guards the ``:class``-style binding, which no row test
    can reach."""
    assert "_allow_paper" in _paper_button(handoff._ACTIONS_SLOT)


def test_strategy_actions_slot_gates_the_paper_button_on_allow_paper():
    assert "_allow_paper" in _paper_button(handoff._STRATEGY_ACTIONS_SLOT)


def test_only_the_paper_button_is_gated():
    """Calculator + Expected Move stay available on a stale row — reviewing a
    dropped signal is the point of the day union; only BOOKING it is the hazard."""
    for slot in (handoff._ACTIONS_SLOT, handoff._STRATEGY_ACTIONS_SLOT):
        assert slot.count("_allow_paper") == 1


def test_handoff_is_engine_free():
    """Regression: the last options-engine import is gone. The module must not
    import ``paper_trader`` or do any ``OPTIONS_SCANNER``/``sys.path`` glue —
    paper-trade creation goes through the bus instead."""
    src = inspect.getsource(handoff)
    assert "paper_trader" not in src
    assert "OPTIONS_SCANNER" not in src
    assert "import sys" not in src
    assert "sys.path" not in src
    # It now drives paper-trade creation through the bus command.
    assert "import bus_client" in src
    assert "paper_create" in src
