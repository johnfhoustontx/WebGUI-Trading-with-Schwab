"""Pure-transform + Tier-3-reader tests for the Sector Rotation page."""
import inspect

from pages import sentiment_rotation as R


def test_neither_rotation_page_round_trips_hover_to_the_server():
    """The durable half of the old plotly-migration guard.

    Originally this also pinned ``ui.highchart`` on the RRG page. That stopped
    being true on 2026-08-17, when the RRG was rebuilt from a supplied design as
    a hand-drawn plot — absolutely-positioned markers over an SVG trail layer —
    so neither page renders a chart element at all now. What must never come
    back is the per-hover client→server round-trip the plotly version used."""
    from pages import sentiment_rrg
    for src in (inspect.getsource(sentiment_rrg.render),
                inspect.getsource(R.render)):
        assert "plotly_hover" not in src and "plotly_unhover" not in src
        assert "run_plot_method" not in src


def test_page_has_no_engine_glue():
    """Regression: the migrated page must not re-introduce the sentiment-engine
    imports / sys.path glue (the source of the cross-app ``scoring`` collision)."""
    src = inspect.getsource(R)
    assert "sector_rotation_assessment" not in src
    assert "sectors_ref" not in src
    assert "rotation_tool" not in src
    assert "from repo_paths import SENTIMENT" not in src
    assert "import sys" not in src
    assert "sys.path" not in src
    # The page reads the bus instead of holding a compute path.
    assert "_compute" not in src
    assert "_sector_weights" not in src
    assert "_ROTATION_CACHE" not in src


def test_render_graceful_empty():
    """render() must paint a waiting placeholder without crashing when the bus
    cache is cold (service not running). Mirrors test_sentiment.py: render inside
    a slot context (a card) to exercise the widget wiring + initial paint."""
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("sentiment:rotation") is None  # confirm empty
    with ui.card():
        R.render()  # must not raise
