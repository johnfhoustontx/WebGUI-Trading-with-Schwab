"""Tests for the Trade detail panel's score bar.

It replaces the Highcharts speedometer (2026-08-25). The gauge was the only
chart on the four pages that mount the panel, so removing it also removes their
only Highcharts element — nothing is left needing the ESM anchor the old
`render()` docstring warned about.

Run from webgui:
    ..\\.venv\\Scripts\\python -m pytest tests\\test_score_bar.py -v
"""
import re

import pytest

from pages.options import svg


def _marker_x(markup):
    """The tick's x, or None. ⚠ `<line` is a PREFIX of `<linearGradient`, so a
    naive substring finds the gradient's x1="0" and reports a marker at the
    origin. Match the tag boundary."""
    m = re.search(r'<line\s[^>]*?x1="([\d.]+)"', markup)
    return float(m.group(1)) if m else None


def _fills(markup):
    """Every `width=` on a rect, in document order: track first, then fill."""
    return [float(w) for w in re.findall(r'<rect[^>]*?width="([\d.]+)"', markup)]


class TestGeometry:
    def test_the_fill_is_proportional_to_the_value(self):
        m = svg.score_bar_svg(59, bar_width=200)
        track, fill = _fills(m)[0], _fills(m)[1]
        assert track == 200
        assert fill == pytest.approx(118.0, abs=0.5)      # 59% of 200

    def test_zero_draws_a_track_and_no_fill(self):
        m = svg.score_bar_svg(0, bar_width=200)
        assert _fills(m)[1] == 0.0

    def test_one_hundred_fills_the_track(self):
        assert _fills(svg.score_bar_svg(100, bar_width=200))[1] == 200.0

    def test_an_out_of_range_value_is_clamped_not_overdrawn(self):
        assert _fills(svg.score_bar_svg(140, bar_width=200))[1] == 200.0
        assert _fills(svg.score_bar_svg(-20, bar_width=200))[1] == 0.0

    def test_the_marker_sits_at_the_end_of_the_fill(self):
        m = svg.score_bar_svg(59, bar_width=200)
        assert _marker_x(m) == pytest.approx(118.0, abs=0.5)


class TestAbsence:
    """`svg._clamp` returns 0.0 for a non-numeric, so a missing score would draw
    an EMPTY bar labelled 0 — the worst possible score, rendered confidently.
    That is the repo's most expensive bug class. Absence must look like absence."""

    @pytest.mark.parametrize("bad", [None, float("nan"), "", "abc", float("inf")])
    def test_a_missing_score_draws_no_fill_and_no_marker(self, bad):
        m = svg.score_bar_svg(bad)
        assert _marker_x(m) is None       # no marker
        assert len(_fills(m)) == 1        # the track only

    def test_a_missing_score_prints_a_dash_not_a_zero(self):
        m = svg.score_bar_svg(None)
        assert ">0<" not in m and "—" in m

    def test_a_real_zero_is_not_treated_as_missing(self):
        """0 is a legitimate score and must render as one."""
        m = svg.score_bar_svg(0)
        assert ">0<" in m and _marker_x(m) == 0.0


class TestColour:
    def test_the_gradient_ends_at_the_scores_own_colour(self):
        """The fill keeps the existing red->amber->green semantics rather than
        being a fixed green ramp, or a 20 would look as healthy as an 80."""
        for v in (10, 50, 90):
            assert svg.value_color(v).lower() in svg.score_bar_svg(v).lower()

    def test_a_low_score_is_not_green(self):
        low, high = svg.score_bar_svg(10), svg.score_bar_svg(90)
        assert svg.value_color(10) != svg.value_color(90)
        assert svg.value_color(90).lower() not in low.lower()

    def test_the_gradient_starts_darker_than_it_ends(self):
        """The supplied design ramps dark -> bright across the fill."""
        m = svg.score_bar_svg(59)
        stops = re.findall(r'stop-color="(#[0-9a-fA-F]{6})"', m)
        assert len(stops) == 2
        lum = lambda h: sum(int(h[i:i + 2], 16) for i in (1, 3, 5))
        assert lum(stops[0]) < lum(stops[1])


class TestItSurvivesTheBrowser:
    def test_the_gradient_id_is_unique_per_value_so_two_bars_cannot_collide(self):
        """Two bars on one page sharing a gradient id would both paint whichever
        definition the browser resolved last."""
        a, b = svg.score_bar_svg(20), svg.score_bar_svg(80)
        id_a = re.search(r'id="([^"]+)"', a).group(1)
        id_b = re.search(r'id="([^"]+)"', b).group(1)
        assert id_a != id_b

    def test_the_fill_actually_references_its_own_gradient(self):
        m = svg.score_bar_svg(59)
        gid = re.search(r'id="([^"]+)"', m).group(1)
        assert f"url(#{gid})" in m

    def test_it_emits_nothing_dompurify_would_strip(self):
        """`ui.html` sanitizes through the bundled DOMPurify — NiceGUI replaces
        setHTML with DOMPurify.sanitize. A stripped attribute is invisible
        server-side, which is how every label on the sentiment rings once
        silently mis-positioned. Reuses the rings guard's extraction."""
        from test_rings import _dompurify_allowlist
        allowed = _dompurify_allowlist()
        markup = svg.score_bar_svg(59)
        for tag in set(re.findall(r"<([a-zA-Z][a-zA-Z0-9-]*)", markup)):
            assert tag.lower() in allowed, f"<{tag}> would be stripped"
        for attr in set(re.findall(r'([a-zA-Z][a-zA-Z0-9-]*)=', markup)):
            assert attr.lower() in allowed, f"{attr}= would be stripped"
