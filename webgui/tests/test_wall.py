"""The public wall-display document (``webgui/wall.py``).

Everything under test is the EMITTED STRING — no browser, no Redis, the same
shape ``test_desk_stream.py`` takes. That is not a compromise: the page's whole
job is to be a static shell around three iframes, so its correctness IS what the
document says.

One test below carries more weight than the rest. ``/wall`` rotates by
``opacity``, never ``display:none``, because a hidden iframe has zero layout size
and this app's Highcharts have no ResizeObserver — a chart that mounts at 0x0
renders collapsed and never recovers. Nothing on the three pages draws a chart
today, so that trap is latent rather than live, which is exactly the kind that
gets re-introduced by a reasonable-looking edit.
"""
import re

import wall


def test_pages_are_the_three_dashboards_in_rotation_order():
    assert [p["path"] for p in wall.PAGES] == [
        "/desk", "/market", "/sentiment/momentum"]
    # Every panel needs a human label for the overlay -- a viewer landing
    # mid-rotation has no other way to know what they are looking at.
    assert all(p["label"] for p in wall.PAGES)


def test_route_is_a_constant_not_a_literal():
    assert wall.PAGE_ROUTE == "/wall"


def test_document_is_a_complete_standalone_html_page():
    doc = wall.document()
    assert doc.startswith("<!DOCTYPE html>")
    assert "</html>" in doc
    # It carries its OWN style block: it is a raw HTMLResponse, the documented
    # out-of-scope case, not a NiceGUI page.
    assert "<style>" in doc


def test_document_embeds_all_three_pages_as_iframes():
    doc = wall.document()
    for page in wall.PAGES:
        assert f'src="{page["path"]}"' in doc
    assert doc.count("<iframe") == 3


# ── rotation: opacity, never display ─────────────────────────────────────────
def test_panels_are_never_hidden_with_display_none_or_visibility():
    """An iframe hidden with display:none has ZERO layout size, and this app's
    Highcharts have no ResizeObserver -- a chart that mounts at 0x0 renders
    collapsed forever. None of these three pages draws a chart TODAY, but the
    day one does (or a chart-heavy page like /options/gamma is swapped in) the
    panel would silently break. Rotation is opacity + z-index, and this test is
    what keeps it that way."""
    doc = wall.document()
    panel_css = re.search(r"\.panel\s*\{[^}]*\}", doc, re.S).group(0)
    squashed = panel_css.replace(" ", "")
    assert "display:none" not in squashed
    assert "visibility" not in squashed
    assert "opacity" in squashed


def test_every_panel_is_laid_out_at_full_size():
    doc = wall.document()
    panel_css = re.search(r"\.panel\s*\{[^}]*\}", doc, re.S).group(0)
    squashed = panel_css.replace(" ", "")
    assert "position:absolute" in squashed
    assert "width:100%" in squashed and "height:100%" in squashed


def test_rotation_timings_come_from_the_constants():
    doc = wall.document()
    assert str(wall.DWELL_MS) in doc
    assert str(wall.FADE_MS) in doc


def test_fade_is_shorter_than_the_dwell():
    # A fade longer than the dwell would mean never settling on a page.
    assert wall.FADE_MS < wall.DWELL_MS
