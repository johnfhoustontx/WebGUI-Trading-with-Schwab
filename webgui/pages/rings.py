"""Pure SVG builders for the concentric Day/Week/Month ring graphics on
``/sentiment`` (design: docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md).

Angles are measured **clockwise from 12 o'clock**. The scale starts at 225°
(lower-left = 0) and sweeps 270° to 495° ≡ 135° (lower-right = 100), leaving a
90° gap at the bottom for the Week/Month legend. Pure functions, no NiceGUI
import — mounted by the page via ``ui.html`` and updated with ``el.content``.
"""
import math

from pages.gauge import _esc, _ramp_color

START_DEG = 225.0       # 0 on the scale — lower-left
SWEEP_DEG = 270.0       # to 495 deg == 135 deg — lower-right
_MIN_SWEEP_DEG = 0.5    # below this an arc is under ~1px — draw nothing


def _point(cx, cy, r, deg):
    """(x, y) at ``deg`` clockwise from 12 o'clock on the circle (cx, cy, r)."""
    rad = math.radians(deg - 90.0)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _value_angle(value):
    """Scale value -> absolute sweep angle (225 .. 495).

    ``value`` MUST already be clamped to 0-100 by the caller — that is a
    precondition, not a description. Past 133 the sweep exceeds 360 deg and
    wraps silently into a *short* arc, which reads as a LOW value.
    """
    return START_DEG + SWEEP_DEG * (value / 100.0)


def _arc_path(cx, cy, r, start_deg, end_deg):
    """SVG ``d`` for a clockwise arc; "" when the sweep is non-positive,
    reversed, or under ~1px of arc."""
    sweep = end_deg - start_deg
    if sweep < _MIN_SWEEP_DEG:
        return ""
    x0, y0 = _point(cx, cy, r, start_deg)
    x1, y1 = _point(cx, cy, r, end_deg)
    large = 1 if sweep > 180.0 else 0
    return (f"M {x0:.2f} {y0:.2f} "
            f"A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}")


# --------------------------------------------------------------------- dial
RADII = (112.0, 90.0, 68.0)   # outer -> inner, in the fixed 280 viewBox
STROKE = 13.0
HALO_EXTRA = 9.0              # extra stroke-width for the translucent glow layer
HALO_OPACITY = 0.22
TICK_R = 132.0
CX = CY = 140.0
TRACK = "#1b2233"             # dim unfilled track (matches the page's chip bg)
TICK_FILL = "#7f8db0"
_TICKS = (0, 25, 50, 75, 100)
# The bottom 90 deg gap is the Week/Month legend. x=104/176 keeps both clear of
# the 0 and 100 ticks, which land at x~47 and x~233 on the r=132 rim.
_LEGEND_X = (104.0, 176.0)


def _safe_value(v):
    """Clamp to [0, 100]; None / junk / NaN -> None (renders track-only).

    Deliberately NOT ``gauge._safe_float``: that coerces junk to a 0.0 default,
    which would paint a not-yet-published horizon as a hard zero. None is how
    the ring says "no data".
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                       # NaN
        return None
    if f in (float("inf"), float("-inf")):
        return None
    return max(0.0, min(100.0, f))


def _fmt(v):
    return "—" if v is None else f"{v:.0f}"


def _fill(v):
    """Text/stroke colour for a value — the ramp for a real reading, the muted
    tick grey for a missing one. A missing horizon must NOT borrow the ramp's
    zero colour: a red em-dash reads as a bearish reading nothing supplied."""
    return TICK_FILL if v is None else _ramp_color(v / 100.0)


def _id_token(uid):
    """``uid`` reduced to characters legal in a DOM id. Not an escaper (that is
    ``gauge._esc``) — an id has no business carrying quotes or markup at all."""
    return "".join(c for c in str(uid or "") if c.isalnum() or c in "_-")


def _text(x, y, body, size, fill, weight=None, spacing=None):
    extra = (f' font-weight="{weight}"' if weight else "") + \
            (f' letter-spacing="{spacing}"' if spacing else "")
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dominant-baseline="middle" font-size="{size}"{extra} '
            f'fill="{fill}">{body}</text>')


def ring_svg(arcs, uid, size=280):
    """Concentric Day/Week/Month dial as one inline SVG string.

    ``arcs`` is outermost-first: ``[{"value": 0-100 or None, "caption": str}, ...]``
    (1-3 entries; extras are dropped). ``uid`` namespaces the root DOM id —
    REQUIRED, because two rings share the /sentiment page and a duplicate id
    would make them collide.

    The centre carries the OUTERMOST arc only (there is no room for three rows
    inside r=68); the other two sit in the 90 deg gap at the bottom. Never
    raises — a junk arc degrades to track-only.
    """
    arcs = [a if isinstance(a, dict) else {} for a in (arcs or [])][:len(RADII)]
    vals = [_safe_value(a.get("value")) for a in arcs]
    caps = [_esc(a.get("caption")) for a in arcs]
    fills = [_fill(v) for v in vals]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 280" '
        f'width="{size}" height="{size}" id="ring-{_id_token(uid)}">'
    ]

    # Tracks first, so every value arc paints over its own track.
    for r in RADII[:len(vals)]:
        d = _arc_path(CX, CY, r, START_DEG, START_DEG + SWEEP_DEG)
        parts.append(f'<path d="{d}" fill="none" stroke="{TRACK}" '
                     f'stroke-width="{STROKE}" stroke-linecap="round"/>')

    # Halo + value arc per filled arc. The glow is a wide translucent copy of
    # the same path drawn UNDER a normal-width bright one — deliberately not an
    # SVG <filter>, which ui.html's setHTML sanitizer may not pass through.
    # A value of 0 sweeps nothing and so draws neither; it stays distinguishable
    # from "no data" by its centre text ("0" vs the em-dash).
    for i, v in enumerate(vals):
        if v is None:
            continue
        d = _arc_path(CX, CY, RADII[i], START_DEG, _value_angle(v))
        if not d:
            continue
        parts.append(f'<path d="{d}" fill="none" stroke="{fills[i]}" '
                     f'stroke-width="{STROKE + HALO_EXTRA}" stroke-linecap="round" '
                     f'opacity="{HALO_OPACITY}"/>')
        parts.append(f'<path d="{d}" fill="none" stroke="{fills[i]}" '
                     f'stroke-width="{STROKE}" stroke-linecap="round"/>')

    # Scale ticks around the outer rim.
    for t in _TICKS:
        x, y = _point(CX, CY, TICK_R, _value_angle(t))
        parts.append(_text(x, y, t, 11, TICK_FILL))

    # Centre — the OUTERMOST arc only.
    if vals:
        parts.append(_text(CX, 146, _fmt(vals[0]), 52, fills[0], weight=700))
        parts.append(_text(CX, 170, caps[0], 12, TICK_FILL, spacing=3))

    # Week + Month live in the 90 deg bottom gap.
    for i, x in enumerate(_LEGEND_X, start=1):
        if i >= len(vals):
            break
        parts.append(_text(x, 250, _fmt(vals[i]), 22, fills[i], weight=600))
        parts.append(_text(x, 267, caps[i], 10, TICK_FILL, spacing=2))

    parts.append("</svg>")
    return "".join(parts)
