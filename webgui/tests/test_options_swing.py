"""Tests for the Swing Scanner page (Tier-3 reader).

The scan pipeline (``screen_spreads`` + ``build_iron_condors`` +
``score_all_signals``, plus ``assign_ids``) moved to
``services/options_svc/compute.swing_scan`` — see that service's tests. The page
now only converts the credit-% input to a fraction (``pct_to_fraction``),
enqueues a ``swing_scan`` command, and reads the result from the Redis bus. So
the page must import NO engine / proxy / scoring code.
"""
import inspect

import bus_client
from pages.options import swing


def test_render_callable():
    assert callable(swing.render)


def test_pct_to_fraction():
    # screen_spreads expects a fraction (0.10), not a percent (10) — regression guard.
    assert swing.pct_to_fraction(10) == 0.10
    assert swing.pct_to_fraction(0) == 0.0


def test_page_imports_no_engine_or_proxy():
    """Regression: the Tier-3 page must not pull in engine / proxy / scoring code."""
    for attr in ("proxy", "scanner_engine", "run_iv_analysis", "scoring",
                 "engines", "OPTIONS_SCANNER", "sys", "assign_ids", "_swing_scan"):
        assert not hasattr(swing, attr), f"swing.py still references {attr}"
    # Also guard the literal import lines so the strings never creep back.
    src = inspect.getsource(swing)
    for forbidden in ("scanner_engine", "OPTIONS_SCANNER", "options_scoring",
                      "import proxy", "run_iv_analysis", "import scoring",
                      "from . import engines", "_swing_scan"):
        assert forbidden not in src, f"swing.py must not reference {forbidden!r}"


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path. Mirrors the header
    page test: rendering inside a slot context is enough to exercise the widget
    wiring + the initial fetch-free paint.
    """
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:swing") is None  # confirm empty
    with ui.card():
        swing.render()  # must not raise
