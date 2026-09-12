"""The shared chain readers + the entry panel's chain-grid builders (pure)."""
import inspect

from pages.options import calculator as calc
from pages.options import chain_grid as cg

_READERS = ("extract_atm_iv", "_find_contract", "_finite", "extract_premium",
            "extract_delta", "leg_delta", "position_delta", "chain_expiries",
            "chain_strikes")


def test_calculator_reexports_the_readers():
    # Moved so the Simulator reads the same chain without importing another PAGE.
    for name in _READERS:
        assert getattr(calc, name) is getattr(cg, name), name


def test_chain_grid_holds_no_engine_or_ui_imports():
    src = inspect.getsource(cg)
    for forbidden in ("scanner_engine", "options_calculator", "import proxy",
                      "from nicegui", "import nicegui"):
        assert forbidden not in src, forbidden
