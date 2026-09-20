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


# ── the RRG on the page kit (Phase 3, Task 1) ────────────────────────────────
def _rrg_render(monkeypatch=None):
    """Render the RRG page in a slot context, as test_render_graceful_empty does."""
    from nicegui import ui
    from pages import sentiment_rrg
    with ui.card():
        sentiment_rrg.render()
    return list(ui.context.client.elements.values())


def test_the_scrim_survives_the_repaint_that_used_to_delete_it():
    """The bug this migration fixes. ``build_busy`` mounted the scrim INSIDE
    ``plot``, and ``_paint_plot`` opens with ``plot.clear()`` - so the first
    ``_apply()`` on build deleted it, and every later Refresh raised a scrim
    that no longer existed. ``kit.region`` keeps the spinner on ``outer`` and
    clears only ``content``, so it is still here after the build repaint."""
    from nicegui import ui
    els = _rrg_render()
    assert any(isinstance(e, ui.spinner) for e in els), \
        "the region's spinner was deleted by the build-time repaint"


def test_the_rrg_frame_is_the_kit_and_carries_no_surface_of_its_own():
    import inspect
    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    assert "kit.page()" in src
    assert 'kit.header("RRG", view=VIEW, stale=False)' in src
    # The page-scoped ground, face and ladder are gone from the frame.
    for token in ("RT_VOID_BG", "RT_SANS", "ROTATION_FONT_HEAD_HTML"):
        assert token not in src, f"{token} is a page-scoped surface value"


def test_the_refresh_toast_is_gone_because_the_spinner_says_it():
    import inspect
    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    assert "ui.notify" not in src and "Refreshing — the page updates" not in src


def test_the_quadrant_hues_and_the_tone_dots_are_untouched():
    """Charts keep their data colours: this is the half that must NOT change."""
    import inspect
    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    for name in ("QUAD_WASH", "QUAD_CORNER_TXT", "STRIP_TINT", "TONE"):
        assert name in src, f"{name} is a data colour and must survive"
