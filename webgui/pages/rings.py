"""Pure SVG arc geometry shared by the hand-drawn dials (today the Market Regime
Console's confidence dial, ``pages/console_dial.py``). The concentric
Day/Week/Month ring dial this module was written for
(docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md) was replaced by
the console and its builder removed; the arc primitives and the DOMPurify
baseline note below outlived it.

Angles are measured **clockwise from 12 o'clock**. Pure functions, no NiceGUI
import.
"""
import math

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
# correct, so no test could see it (the DOMPurify allow-list tests built on
# ``tests/test_rings._dompurify_allowlist`` now guard every hand-drawn SVG
# builder's attribute surface). ``dy`` IS allowlisted,
# and a ``dy`` shift is the pre-``dominant-baseline`` idiom anyway — universally
# supported, and dependent on no allowlist detail that can change under us.
#
# ONE constant covers every text size on a dial (the ring dial had five, 10 to
# 52) because ``em``
# resolves against each node's OWN font-size, so the shift scales with the
# glyph — a fixed pixel offset would not. 0.35 is half the cap height of the app
# font (IBM Plex Sans, capHeight 698/1000 -> 0.349em), so the emitted shift
# tracks half-cap at every size to within 0.05px, worst case at 52. That is the
# right centring for this dial's content: lining digits and all-caps captions.
# It is deliberately NOT a reproduction of ``middle``, which centres on the
# *x*-height: that puts the alphabetic baseline at y + 0.258em, so a cap-height
# glyph's box centres at y - 0.091em — about 0.09em HIGH. This ``dy`` therefore
# pushes the text DOWN relative to the old rendering, not up.
#
# The one glyph it does not centre exactly is the "no data" em-dash, which sits
# on the math axis (~0.28em) rather than mid-cap — about 1px low at 22px, 4px at
# 52px. Left alone: special-casing the placeholder is not worth a branch.
_BASELINE_DY = "0.35em"

