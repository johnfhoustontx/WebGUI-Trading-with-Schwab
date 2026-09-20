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
def _rrg_render():
    """Render the RRG page in a slot context and return ONLY the elements this
    render built.

    The auto-index client is shared by every test in the module, so a plain
    ``elements.values()`` also hands back the spinner some OTHER page's kit
    region left behind - and the scrim test below then passes whatever this
    page did. Measured: with the pre-migration page restored, that spelling
    still went green off ``test_render_graceful_empty``'s rotation render."""
    from nicegui import ui
    from pages import sentiment_rrg
    before = set(ui.context.client.elements)
    with ui.card():
        sentiment_rrg.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


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


# ── Sector Rotation on the page kit (Phase 3, Task 2) ────────────────────────
def _rotation_render():
    """Render the Sector Rotation page in a slot context, as
    ``test_render_graceful_empty`` does, and return ONLY the elements this
    render built.

    The auto-index client is shared by every test in the module, so a plain
    ``elements.values()`` would also hand back the RRG page's kit spinner from
    the test above and pass whatever this page did."""
    from nicegui import ui
    before = set(ui.context.client.elements)
    with ui.card():
        R.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def test_the_rotation_scrim_survives_the_repaint_that_used_to_delete_it():
    """The bug this migration fixes, the RRG's one screen over. ``build_busy``
    mounted the scrim INSIDE ``quad_box``, and ``_paint_quadrants`` opens with
    ``quad_box.clear()`` - so the first ``_apply()`` on build deleted it, and
    every later Refresh raised a scrim that no longer existed. ``kit.region``
    keeps the spinner on ``outer`` and clears only ``content``, so it is still
    here after the build repaint."""
    from nicegui import ui
    els = _rotation_render()
    assert any(isinstance(e, ui.spinner) for e in els), \
        "the region's spinner was deleted by the build-time repaint"


def test_the_rotation_frame_is_the_kit_and_carries_no_surface_of_its_own():
    src = inspect.getsource(R.render)
    assert "kit.page()" in src
    assert 'kit.header("Sector Rotation", view=VIEW, stale=False)' in src
    # The page-scoped ground, face, panel fill and ladder are gone.
    for token in ("RT_VOID_BG", "RT_SANS", "RT_MONO", "RT_PANEL_BG",
                  "ROTATION_FONT_HEAD_HTML"):
        assert token not in src, f"{token} is a page-scoped surface value"
    for token in ("V.NT[", "V.NB[", "V.NE["):
        assert token not in src, f"{token} is the retiring neutral ladder"


def test_the_view_name_is_one_constant_the_header_read_and_watch_all_share():
    """Three sites used to spell ``sentiment:rotation`` independently - the
    header stamp could then poll a view the page does not read."""
    src = inspect.getsource(R.render)
    assert R.VIEW == "sentiment:rotation"
    assert "bus_client.read(VIEW)" in src
    assert "watch_view(VIEW" in src
    assert '"sentiment:rotation"' not in src, \
        "the view name is inline again; use the VIEW constant"


def test_the_rotation_refresh_toast_is_gone_because_the_spinner_says_it():
    src = inspect.getsource(R.render)
    assert "ui.notify" not in src and "Refreshing — the page updates" not in src


def test_the_rotation_tone_dots_and_quadrant_classes_are_untouched():
    """Charts keep their data colours: this is the half that must NOT change.

    Passes before and after the migration by design - it is the guard on the
    half of the page the kit may not reach, not a red-then-green test."""
    from pages import rotation_view as V
    # The reactive `remove=` set is still every tone dot, byte for byte.
    assert R._DOT_CLASSES == " ".join(
        dict.fromkeys(t["dot"] for t in V.TONE.values()))
    src = inspect.getsource(R.render)
    # The flow band and the quadrant panels still read their quadrant hues.
    assert "V.quad_classes(" in src
    for key in ("'seg'", "'seg_top'", "'ticker'", "'dot'", "'title'",
                "'mom'", "'chip'", "'bar'"):
        assert f"qc[{key}]" in src, f"quad_classes[{key}] is a data colour"
    # The gauge and the flow footers still read their tones.
    for expr in ("V.TONE['flat']['dot']", "V.TONE['flat']['txt']",
                 "V.TONE['down']['tick']", "V.TONE['up']['tick']",
                 "V.TONE['down']['txt']", "tone['fill']", "tone['mark']",
                 "tone['foot_edge']", "tone['foot_pct']", "tone['foot_lbl']"):
        assert expr in src, f"{expr} is a data colour and must survive"
