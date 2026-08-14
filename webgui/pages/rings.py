"""Pure SVG builders for the concentric Day/Week/Month ring graphics on
``/sentiment`` (design: docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md).

Angles are measured **clockwise from 12 o'clock**. The scale starts at 225°
(lower-left = 0) and sweeps 270° to 495° ≡ 135° (lower-right = 100), leaving a
90° gap at the bottom for the Week/Month legend. Pure functions, no NiceGUI
import — mounted by the page via ``ui.html`` and updated with ``el.content``.
"""
import math

START_DEG = 225.0   # 0 on the scale — lower-left
SWEEP_DEG = 270.0   # to 495 deg == 135 deg — lower-right


def _point(cx, cy, r, deg):
    """(x, y) at ``deg`` clockwise from 12 o'clock on the circle (cx, cy, r)."""
    rad = math.radians(deg - 90.0)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _value_angle(value):
    """0-100 -> absolute sweep angle (225 .. 495)."""
    return START_DEG + SWEEP_DEG * (value / 100.0)


def _arc_path(cx, cy, r, start_deg, end_deg):
    """SVG ``d`` for a clockwise arc; "" for a degenerate (sub-pixel) sweep."""
    sweep = end_deg - start_deg
    if sweep < 0.5:
        return ""
    x0, y0 = _point(cx, cy, r, start_deg)
    x1, y1 = _point(cx, cy, r, end_deg)
    large = 1 if sweep > 180.0 else 0
    return (f"M {x0:.2f} {y0:.2f} "
            f"A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}")
