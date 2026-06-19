"""Tests for the pure SVG builders behind the Trade detail graphics."""
from pages.options import svg


def test_gradient_bar_clamps_and_renders():
    assert svg.gradient_bar_svg(150).strip().startswith("<svg")
    assert svg.gradient_bar_svg(-5).strip().startswith("<svg")
    assert "<rect" in svg.gradient_bar_svg(50)


def test_range_marker_positions_current_between_low_high():
    out = svg.range_marker_svg(10.0, 20.0, 15.0, width=100)
    assert out.strip().startswith("<svg")
    assert "</svg>" in out


def test_range_marker_handles_degenerate_range():
    out = svg.range_marker_svg(10.0, 10.0, 10.0)
    assert out.strip().startswith("<svg")
