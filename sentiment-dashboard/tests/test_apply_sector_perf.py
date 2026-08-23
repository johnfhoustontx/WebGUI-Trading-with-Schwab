"""Regression: industry Day% quotes must survive periodic sector refreshes.

The original bug: _apply_sector_perf did `self._last_sector_quotes = quotes`,
which replaced (rather than merged into) the cumulative quote map.
Industries fetched lazily during expand were wiped on every subsequent
fetch, causing the UI to render them as "—" until the user re-expanded
the sector.

This fork replaced the Tk UI with the NiceGUI webgui and deliberately never
copied ``sentiment_dashboard.py`` (root CLAUDE.md, folder map), so these two
tests can never pass HERE — they are green in the source repo. Skipped at module
level rather than deleted: the regression they pin is real, and the day the
module reappears they should run again.
"""
from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "sentiment_dashboard",
    reason="Tk UI entrypoint deliberately not copied into this fork",
)


def _make_app():
    # Import inside the test so the module is loaded with the conftest
    # sys.path already configured.
    from sentiment_dashboard import SentimentDashboard
    app = SentimentDashboard.__new__(SentimentDashboard)
    app._sector_data = [
        {'kind': 'sector', 'sector': 'Energy', 'etf': 'XLE',
         'label': 'Energy', 'name': '', 'notes': '', 'sp_weight': 0.0},
        {'kind': 'industry', 'sector': 'Energy', 'etf': 'XOP',
         'label': 'Oil & Gas E&P', 'name': '', 'notes': '',
         'sp_weight': 0.0},
        {'kind': 'industry', 'sector': 'Energy', 'etf': 'OIH',
         'label': 'Oil Svcs', 'name': '', 'notes': '', 'sp_weight': 0.0},
    ]
    app._industries_fetched = set()
    app.sector_perf_summary = MagicMock()
    app.schwab = None
    app._weighted_sector_pct = lambda: (None, 0.0)
    app._sectors_score = lambda: 5.0
    app._populate_sector_tree = MagicMock()
    app._update_rotation_banner = MagicMock()
    app._refresh_sector_trends = MagicMock()
    return app


def test_apply_sector_perf_merges_into_existing_quotes():
    app = _make_app()
    # Industries were previously lazy-fetched and merged in.
    app._last_sector_quotes = {
        'XLE': {'change_pct': 1.0},
        'XOP': {'change_pct': 2.0},
        'OIH': {'change_pct': 1.5},
    }

    # Periodic refresh delivers fresh sector quotes only.
    app._apply_sector_perf({'XLE': {'change_pct': 1.8}})

    # Industries must still be present.
    assert app._last_sector_quotes['XOP']['change_pct'] == 2.0
    assert app._last_sector_quotes['OIH']['change_pct'] == 1.5
    # New sector value wins.
    assert app._last_sector_quotes['XLE']['change_pct'] == 1.8


def test_apply_sector_perf_renders_from_merged_map():
    """_populate_sector_tree must receive the merged map, not just the
    sector-only quotes from this refresh — otherwise industries render
    as "—" until the next lazy expand."""
    app = _make_app()
    app._last_sector_quotes = {'XOP': {'change_pct': 2.0}}

    app._apply_sector_perf({'XLE': {'change_pct': 1.8}})

    app._populate_sector_tree.assert_called_once()
    rendered_with = app._populate_sector_tree.call_args.args[0]
    assert 'XOP' in rendered_with
    assert 'XLE' in rendered_with
