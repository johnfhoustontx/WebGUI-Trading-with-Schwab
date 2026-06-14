"""Pure SVG builders for the Trade detail graphics.

These mirror the hand-drawn Tk canvas widgets (speedometer gauge, factor
gradient bars, IV/EM range markers) as inline SVG strings so NiceGUI can render
them via ``ui.html``. Pure functions — unit-tested, no NiceGUI dependency.
"""
import math

RED = (239, 83, 80)      # #ef5350
AMBER = (255, 167, 38)   # #ffa726
BLUE = (66, 165, 245)    # #42a5f5
GREEN = (102, 187, 106)  # #66bb6a


def _clamp(v, lo=0.0, hi=100.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def value_color(value):
    """Red→amber→green for a 0-100 value (matches the Tk gradient bar)."""
    v = _clamp(value)
    if v <= 50:
        return _hex(_lerp_color(RED, AMBER, v / 50.0))
    return _hex(_lerp_color(AMBER, GREEN, (v - 50.0) / 50.0))


def _zone_color(v):
    if v < 40:
        return _hex(RED)
    if v < 55:
        return _hex(AMBER)
    if v < 75:
        return _hex(BLUE)
    return _hex(GREEN)


def _arc_point(cx, cy, r, value):
    """Point on the upper semicircle for a 0-100 value (0=left, 100=right)."""
    theta = math.radians(180 - _clamp(value) / 100.0 * 180.0)
    return cx + r * math.cos(theta), cy - r * math.sin(theta)


def speedometer_svg(score, grade, width=160, height=104):
    """Semicircular gauge with colored zones and a needle at ``score``."""
    s = _clamp(score)
    cx, cy, r = width / 2.0, height - 14.0, width / 2.0 - 14.0
    segments = []
    zones = [(0, 40, RED), (40, 55, AMBER), (55, 75, BLUE), (75, 100, GREEN)]
    for a, b, rgb in zones:
        x0, y0 = _arc_point(cx, cy, r, a)
        x1, y1 = _arc_point(cx, cy, r, b)
        segments.append(
            f'<path d="M {x0:.1f} {y0:.1f} A {r:.1f} {r:.1f} 0 0 1 {x1:.1f} {y1:.1f}" '
            f'fill="none" stroke="{_hex(rgb)}" stroke-width="12" stroke-linecap="butt"/>'
        )
    nx, ny = _arc_point(cx, cy, r - 8, s)
    needle = (f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{nx:.1f}" y2="{ny:.1f}" '
              f'stroke="#e0e0e0" stroke-width="3"/>'
              f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="#e0e0e0"/>')
    text = (f'<text x="{cx:.1f}" y="{cy - 2:.1f}" text-anchor="middle" '
            f'font-size="20" font-weight="bold" fill="#fff">{int(s)}</text>'
            f'<text x="{cx:.1f}" y="{cy + 12:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#bdbdbd">{grade}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(segments)}{needle}{text}</svg>')


def gradient_bar_svg(value, width=150, height=12):
    """Horizontal bar filled to ``value`` (0-100), colored red→amber→green."""
    v = _clamp(value)
    fill_w = v / 100.0 * width
    color = value_color(v)
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<rect x="0" y="0" width="{width}" height="{height}" rx="3" fill="#2a2a2a"/>'
            f'<rect x="0" y="0" width="{fill_w:.1f}" height="{height}" rx="3" fill="{color}"/>'
            f'</svg>')


def range_marker_svg(low, high, current, width=160, height=14):
    """Horizontal range line with a triangle marker at ``current``."""
    try:
        low, high, current = float(low), float(high), float(current)
    except (TypeError, ValueError):
        low, high, current = 0.0, 1.0, 0.5
    span = high - low
    frac = 0.5 if span == 0 else (current - low) / span
    frac = max(0.0, min(1.0, frac))
    x = frac * width
    mid = height / 2.0
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<line x1="2" y1="{mid:.1f}" x2="{width - 2}" y2="{mid:.1f}" '
            f'stroke="#666" stroke-width="2"/>'
            f'<line x1="2" y1="{mid - 4:.1f}" x2="2" y2="{mid + 4:.1f}" stroke="#666" stroke-width="2"/>'
            f'<line x1="{width - 2}" y1="{mid - 4:.1f}" x2="{width - 2}" y2="{mid + 4:.1f}" '
            f'stroke="#666" stroke-width="2"/>'
            f'<polygon points="{x:.1f},{mid - 5:.1f} {x - 4:.1f},{mid + 4:.1f} {x + 4:.1f},{mid + 4:.1f}" '
            f'fill="#ffd54f"/>'
            f'</svg>')
