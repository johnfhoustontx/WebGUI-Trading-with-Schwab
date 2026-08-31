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


# ── the overlay ──────────────────────────────────────────────────────────────
def test_overlay_uses_the_apps_own_brand_not_a_copy():
    """A second hand-written wordmark would drift from config/theme.toml.
    The stream must render whatever the app header renders."""
    import main
    from pages.options import theme
    doc = wall.document()
    assert theme.BRAND_NAME_A in doc and theme.BRAND_NAME_B in doc
    assert main.brand_lockup_html(mark=False) in doc


def test_overlay_carries_a_clock_and_a_page_label():
    doc = wall.document()
    assert 'id="clock"' in doc
    assert 'id="page-label"' in doc


def test_disclaimer_slot_exists_and_is_empty_by_decision():
    """Left empty by operator decision. Kept as a single constant so turning it
    on is a one-line change, not a redesign."""
    assert wall.DISCLAIMER == ""
    assert 'id="disclaimer"' in wall.document()


def test_clock_is_central_time():
    # The trading clock. A stream stamped in UTC is one nobody can use.
    assert "America/Chicago" in wall.document()


def test_the_outgoing_panel_holds_opacity_until_the_incoming_covers_it():
    """Fading both panels at once composites the body colour through the middle
    of every handover -- a dark blink, ~2,160 times a session, on camera.

    Stacked back to front (body, outgoing, incoming) the background's weight is
    ``(1 - a_in) * (1 - a_out)``; with both panels sharing one easing curve that
    is ``(1 - e) * e``, non-zero for the WHOLE fade and 0.25 at its midpoint.
    The outgoing panel therefore holds at full opacity for FADE_MS and only then
    snaps to 0, by which point the incoming panel fully occludes it.
    """
    doc = wall.document()
    off = re.search(r"\.panel\s*\{[^}]*\}", doc, re.S).group(0).replace(" ", "")
    on = re.search(r"\.panel\.on\s*\{[^}]*\}", doc,
                   re.S).group(0).replace(" ", "")
    # The off state changes opacity in zero time, after a full fade's delay.
    assert f"0mslinear{wall.FADE_MS}ms" in off
    # The on state is the only one that actually animates.
    assert f"{wall.FADE_MS}msease-in-out0ms" in on


def test_the_incoming_panel_stacks_above_the_one_it_is_covering():
    """Load-bearing once the outgoing panel holds opaque: if the incoming panel
    were painted UNDER it, a fully opaque outgoing panel would hide the fade
    entirely and the crossfade would become a hard cut at FADE_MS."""
    doc = wall.document()
    on = re.search(r"\.panel\.on\s*\{[^}]*\}", doc,
                   re.S).group(0).replace(" ", "")
    assert "z-index:1" in on


# ── staying alive for nine hours ─────────────────────────────────────────────
# These four assert on the emitted JS as a STRING, which is a weak form of test
# and worth naming as such: they pin the shape of the guard, not its behaviour.
# They are still the discriminating ones available without a browser, and each
# fails against the naive implementation it exists to rule out.
def test_panels_reload_on_a_long_interval():
    doc = wall.document()
    assert str(wall.RELOAD_MS) in doc
    assert wall.RELOAD_MS >= 10 * 60 * 1000


def test_a_panel_only_reloads_while_it_is_off_camera():
    """A reload blanks an iframe for a beat. Doing that to the VISIBLE panel
    would put the blank on the stream -- the failure this exists to prevent,
    not cause. Fails against the naive "reload them all" implementation."""
    doc = wall.document()
    assert "if (i === idx) return;" in doc


def test_the_reload_check_waits_out_the_fade():
    """At the instant of rotation the OUTGOING panel is still fully opaque
    underneath -- that is what makes the crossfade work. Reloading it before
    FADE_MS elapses would blank it on camera. Fails against a reloadStale()
    called inline in the rotation."""
    doc = wall.document()
    assert f"setTimeout(reloadStale, {wall.FADE_MS})" in doc


def test_staleness_is_tracked_per_panel_not_globally():
    """One global 'reload everything now' tick would skip whichever panel was
    showing and never come back to it -- so the panel that had been up longest
    would be the only one that never refreshed. Fails against a single shared
    last-reload timestamp."""
    doc = wall.document()
    assert "LOADED[i]" in doc


# ── the route registration ────────────────────────────────────────────────────
def test_route_is_registered_as_a_raw_html_response():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert wall.PAGE_ROUTE in paths


def test_wall_is_not_a_nav_page():
    """A display target, not a destination. It must not appear in the rail, the
    tab strips or the breadcrumb registry -- and specifically must not become a
    third entry in test_shell's _LANDING_ROUTES exemption."""
    import main
    assert wall.PAGE_ROUTE not in main._NAV_LABEL
    assert wall.PAGE_ROUTE not in main.EXTERNAL_RAIL_ROUTES
