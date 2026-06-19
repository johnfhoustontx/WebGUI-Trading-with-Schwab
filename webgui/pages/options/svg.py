"""Pure SVG builders for the Trade detail graphics.

These mirror the hand-drawn Tk canvas widgets (factor gradient bars, IV/EM range
markers) as inline SVG strings so NiceGUI can render them via ``ui.html``. Pure
functions — unit-tested, no NiceGUI dependency. (The composite-score speedometer
is now the shared Highcharts solid-gauge in ``pages/gauge.py``.)
"""
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
