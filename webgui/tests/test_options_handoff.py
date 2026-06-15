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
