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


# ── score bar (replaced the Highcharts speedometer, 2026-08-25) ───────────────

_BAR_TRACK = "#161d2e"       # dark navy well, matching the panel's CARD
_BAR_MARKER = "#f2f6ff"      # the tick at the fill's leading edge
_BAR_MISSING = "#7f8db0"     # MUTED — the dash when there is no score


def _finite_score(value):
    """A real 0-100 reading, or None. ``_clamp`` alone will NOT do here.

    ``_clamp`` returns its LOW bound for anything non-numeric, so a missing
    score would render as a confident 0 — the worst possible score, drawn as
    though it were measured. That is this repo's most expensive bug class, and
    an empty bar labelled 0 is exactly how it looks on a chart.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):    # NaN / inf
        return None
    return _clamp(v)


def score_bar_svg(value, bar_width=190, height=16, label_width=34):
    """The panel's score bar: dark track, gradient fill, tick, value.

    The gradient runs DARK -> the value's own ``value_color``, so the existing
    red/amber/green semantics survive the change of shape: a 20 cannot look as
    healthy as an 80. A missing score draws the bare track and an em dash —
    never a filled-to-zero bar, which would read as a measured worst score.

    Rendered through ``ui.html``, so every tag and attribute here is checked
    against the bundled DOMPurify allowlist by ``test_score_bar.py``.
    """
    v = _finite_score(value)
    total_w = bar_width + label_width
    mid = height / 2.0
    parts = [
        f'<svg viewBox="0 0 {total_w} {height}" width="{total_w}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="0" y="0" width="{bar_width}" height="{height}" rx="3" '
        f'fill="{_BAR_TRACK}"/>',
    ]

    if v is None:
        parts.append(
            f'<text x="{bar_width + label_width / 2:.1f}" y="{mid:.1f}" dy="0.35em" '
            f'text-anchor="middle" font-size="11" fill="{_BAR_MISSING}">—</text>')
        parts.append("</svg>")
        return "".join(parts)

    end = value_color(v)
    # A per-value id: two bars on one page sharing one gradient id would both
    # paint whichever definition the browser resolved last.
    gid = f"sb{int(round(v * 10)):04d}"
    fill_w = v / 100.0 * bar_width
    parts += [
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{_darken(end, 0.32)}"/>'
        f'<stop offset="1" stop-color="{end}"/>'
        f'</linearGradient></defs>',
        f'<rect x="0" y="0" width="{fill_w:.1f}" height="{height}" rx="3" '
        f'fill="url(#{gid})"/>',
        f'<line x1="{fill_w:.1f}" y1="1" x2="{fill_w:.1f}" y2="{height - 1}" '
        f'stroke="{_BAR_MARKER}" stroke-width="1.5"/>',
        f'<text x="{bar_width + label_width / 2:.1f}" y="{mid:.1f}" dy="0.35em" '
        f'text-anchor="middle" font-size="11" fill="{end}">{v:.0f}</text>',
        "</svg>",
    ]
    return "".join(parts)


def _darken(hex_color, factor):
    """``hex_color`` scaled toward black — the gradient's dark end."""
    h = hex_color.lstrip("#")
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{int(round(c * factor)):02x}" for c in rgb)
