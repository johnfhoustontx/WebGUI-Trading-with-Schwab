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
VIEWBOX = 280                 # fixed internal coordinate space; ``size`` only
                              # sets width/height, so the SVG scales itself
RADII = (112.0, 90.0, 68.0)   # outer -> inner, in the fixed 280 viewBox
STROKE = 13.0
HALO_EXTRA = 9.0              # extra stroke-width for the translucent glow layer
HALO_OPACITY = 0.22
TICK_R = 132.0
CX = CY = 140.0
TRACK = "#1b2233"             # dim unfilled track (matches the page's chip bg)
TICK_FILL = "#7f8db0"
_TICKS = (0, 25, 50, 75, 100)

# --- text layout -----------------------------------------------------------
# Kept together because these are the knobs a human tunes BY EYE, and hunting
# them out of ring_svg's body is the slow part. Deliberately NOT pinned by
# tests: a coordinate someone is about to nudge should not turn the suite red.
# Tests that need to locate a text node select it by the size constant rather
# than a literal, so a nudge here cannot break them — so long as the two VALUE
# sizes stay distinct. ``_text_node`` matches the FIRST node of a given size, so
# setting CENTER_VALUE_SIZE == LEGEND_VALUE_SIZE would make the centre shadow the
# legend and redden a test for a non-correctness reason.
#
# The bottom 90 deg gap is the Week/Month legend. x=104/176 keeps both clear of
# the 0 and 100 ticks, which land at x~47 and x~233 on the r=132 rim.
_LEGEND_X = (104.0, 176.0)
TICK_SIZE = 11
CENTER_VALUE_Y, CENTER_VALUE_SIZE = 146.0, 52     # the OUTERMOST arc's reading
CENTER_CAPTION_Y, CENTER_CAPTION_SIZE = 170.0, 12
LEGEND_VALUE_Y, LEGEND_VALUE_SIZE = 250.0, 22     # Week + Month, in the gap
LEGEND_CAPTION_Y, LEGEND_CAPTION_SIZE = 267.0, 10


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


def _px(size):
    """``size`` as a bare pixel count. The only interpolated input that is not
    otherwise sanitized, so it gets the same treatment as ``uid``/``caption``
    for contract consistency; junk falls back to the viewBox edge (1:1)."""
    try:
        f = float(size)
    except (TypeError, ValueError):
        return str(VIEWBOX)
    # The bound also rejects NaN and inf, for which every comparison is False.
    return f"{f:.0f}" if 0.0 < f < 10000.0 else str(VIEWBOX)


def _id_token(uid):
    """``uid`` reduced to characters legal in a DOM id. Not an escaper (that is
    ``gauge._esc``) — an id has no business carrying quotes or markup at all."""
    return "".join(c for c in str(uid or "") if c.isalnum() or c in "_-")


# Vertical centring offset, as a fraction of the node's own font-size.
#
# NOT ``dominant-baseline="middle"``, which is the obvious spelling and which
# this used to emit: NiceGUI replaces ``Element.prototype.setHTML`` with
# ``DOMPurify.sanitize()`` (templates/index.html), and DOMPurify's SVG allowlist
# has ``alignment-baseline`` and ``baseline-shift`` but NOT
# ``dominant-baseline`` — it was silently stripped on the client, dropping every
# label to the default alphabetic baseline. The server-side string stayed
# correct, so no test could see it (``test_ring_svg_emits_nothing_dompurify_
# would_strip`` now guards the whole attribute surface). ``dy`` IS allowlisted,
# and a ``dy`` shift is the pre-``dominant-baseline`` idiom anyway — universally
# supported, and dependent on no allowlist detail that can change under us.
#
# ONE constant covers all FIVE text sizes on the dial (tick 11 / centre value 52
# / centre caption 12 / legend value 22 / legend caption 10) because ``em``
# resolves against each node's OWN font-size, so the shift scales with the
# glyph — a fixed pixel offset would not. 0.35 is half the cap height of the app
# font (IBM Plex Sans, capHeight 698/1000 -> 0.349em), so the emitted shift
# tracks half-cap at every size to within 0.05px, worst case at 52. That is the
# right centring for this dial's content: lining digits and all-caps captions.
# It is deliberately NOT a reproduction of ``middle``, which centres on the
# *x*-height (516/1000 -> 0.258em here) and so hung digits slightly low.
#
# The one glyph it does not centre exactly is the "no data" em-dash, which sits
# on the math axis (~0.28em) rather than mid-cap — about 1px low at 22px, 4px at
# 52px. Left alone: special-casing the placeholder is not worth a branch.
_BASELINE_DY = "0.35em"


def _text(x, y, body, size, fill, weight=None, spacing=None):
    extra = (f' font-weight="{weight}"' if weight else "") + \
            (f' letter-spacing="{spacing}"' if spacing else "")
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dy="{_BASELINE_DY}" font-size="{size}"{extra} '
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

    px = _px(size)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
        f'width="{px}" height="{px}" id="ring-{_id_token(uid)}">'
    ]

    # Tracks first, so every value arc paints over its own track.
    for r in RADII[:len(vals)]:
        d = _arc_path(CX, CY, r, START_DEG, START_DEG + SWEEP_DEG)
        parts.append(f'<path d="{d}" fill="none" stroke="{TRACK}" '
                     f'stroke-width="{STROKE}" stroke-linecap="round"/>')

    # Halo + value arc per filled arc. The glow is a wide translucent copy of
    # the same path drawn UNDER a normal-width bright one — deliberately not an
    # SVG <filter>, which ui.html's setHTML sanitizer may not pass through.
    #
    # A value of 0 sweeps nothing and so draws neither path; it stays
    # distinguishable from "no data" on TWO channels — the centre glyph ("0" vs
    # the em-dash) and its colour (ramp vs muted).
    #
    # Small-value flat spot: the round linecap adds STROKE/2 at each end, so the
    # drawn shape is a ~13px dot until the arc itself exceeds the cap diameter.
    # Arc length is r·(3π/2)·v/100, so the dot stops growing only past v≈2.46 on
    # the outer ring, 3.07 on Week and 4.06 on Month — WIDEST on the innermost
    # arc, which is the one fed by a mean and so likeliest to sit low. Left as
    # is: a blended 0-100 composite realistically never lands below ~4. Anyone
    # reusing ring_svg for a metric that CAN live down there should revisit it.
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
        parts.append(_text(x, y, t, TICK_SIZE, TICK_FILL))

    # Centre — the OUTERMOST arc only.
    if vals:
        parts.append(_text(CX, CENTER_VALUE_Y, _fmt(vals[0]),
                           CENTER_VALUE_SIZE, fills[0], weight=700))
        parts.append(_text(CX, CENTER_CAPTION_Y, caps[0],
                           CENTER_CAPTION_SIZE, TICK_FILL, spacing=3))

    # Week + Month live in the 90 deg bottom gap.
    for i, x in enumerate(_LEGEND_X, start=1):
        if i >= len(vals):
            break
        parts.append(_text(x, LEGEND_VALUE_Y, _fmt(vals[i]),
                           LEGEND_VALUE_SIZE, fills[i], weight=600))
        parts.append(_text(x, LEGEND_CAPTION_Y, caps[i],
                           LEGEND_CAPTION_SIZE, TICK_FILL, spacing=2))

    parts.append("</svg>")
    return "".join(parts)
