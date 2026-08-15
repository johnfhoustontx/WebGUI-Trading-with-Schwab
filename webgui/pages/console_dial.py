"""The regime confidence dial for the Market Regime Console (``/sentiment``).

A pure SVG-string builder mounted with ``ui.html`` and updated via
``el.content`` — the idiom ``pages/rings.py`` established, and it reuses that
module's already-tested arc geometry rather than re-deriving it.

Two of the handoff's mechanics drop out as a result (both verified in the Phase 0
spike, recorded in docs/plans/2026-08-14-sentiment-console-redesign-plan.md):

  * it rotates the whole SVG ``-90deg`` so the arc starts at 12 o'clock —
    unnecessary, because ``rings._point`` already measures clockwise from 12;
  * it puts ``filter: drop-shadow(...)`` on the arc — which ``ui.html``'s
    sanitizer strips. The halo is instead a wide translucent copy of the same
    path drawn underneath a normal-width bright one, which is exactly how the
    rings glow.

Everything emitted must survive that sanitizer: no ``<style>``, no ``<filter>``,
no ``<use>``, and ``dy`` rather than ``dominant-baseline`` (see
``rings._BASELINE_DY`` — a stripped attribute changes nothing server-side, so
the page still renders, just wrong).
"""
from pages.gauge import _esc
from pages.options import theme
from pages.rings import _arc_path, _id_token

_C = theme.CONSOLE_COLORS

VIEWBOX = 244
CX = CY = 122.0
R_OUTER, R_TRACK, R_INNER = 104.0, 92.0, 74.0
STROKE = 16.0
HALO_EXTRA, HALO_OPACITY = 9.0, 0.22
BASELINE_DY = "0.35em"

# A 360 degree sweep's start and end points COINCIDE, and an SVG elliptical-arc
# command between identical points draws NOTHING — measured in the browser,
# getTotalLength() == 0. So a confidence of 1.0 would render an empty ring, the
# single most misleading thing this dial could do. Past this threshold the arc
# is drawn as a full <circle> instead, which has no such degeneracy.
FULL_CIRCLE_EPS = 0.999

# Text layout — tuned by eye, deliberately not pinned by tests (a coordinate
# someone is about to nudge should not turn the suite red).
NAME_Y, NAME_SIZE = 100.0, 40
VALUE_Y, VALUE_SIZE = 140.0, 46
CAPTION_Y, CAPTION_SIZE = 172.0, 9.5


def _safe_confidence(v):
    """Clamp to [0, 1]; junk/NaN/inf -> None (track-only, and an em-dash)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return max(0.0, min(1.0, f))


def _text(x, y, body, size, fill, weight=None, spacing=None, family=None):
    extra = (f' font-weight="{weight}"' if weight else "") + \
            (f' letter-spacing="{spacing}"' if spacing else "") + \
            (f' font-family="{family}"' if family else "")
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dy="{BASELINE_DY}" font-size="{size}"{extra} '
            f'fill="{fill}">{body}</text>')


def _ring(r, stroke, width, opacity=None):
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<circle cx="{CX}" cy="{CY}" r="{r}" fill="none" '
            f'stroke="{stroke}" stroke-width="{width}"{op}/>')


def dial_svg(confidence, name, uid="regime", accent=None, display_font=None):
    """The confidence dial as one inline SVG string. Never raises.

    ``confidence`` is 0..1; ``name`` is the committed regime's display word. A
    missing/junk confidence draws the track and an em-dash rather than a zero —
    "no reading" and "zero confidence" are different statements.
    """
    conf = _safe_confidence(confidence)
    accent = accent or _C["accent"]
    fam = display_font or (theme.THEME["console"].get("font_family") or "")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {VIEWBOX} {VIEWBOX}" width="100%" '
        f'id="regime-dial-{_id_token(uid)}">',
        _ring(R_OUTER, _hairline(0.14), 2),
        _ring(R_TRACK, _hairline(0.10), STROKE),
    ]
    if conf is not None and conf > 0:
        if conf >= FULL_CIRCLE_EPS:
            # Full ring — see FULL_CIRCLE_EPS. Halo then value, same order.
            parts.append(_ring(R_TRACK, accent, STROKE + HALO_EXTRA,
                               HALO_OPACITY))
            parts.append(_ring(R_TRACK, accent, STROKE))
        else:
            d = _arc_path(CX, CY, R_TRACK, 0.0, 360.0 * conf)
            if d:
                parts.append(f'<path d="{d}" fill="none" stroke="{accent}" '
                             f'stroke-width="{STROKE + HALO_EXTRA}" '
                             f'opacity="{HALO_OPACITY}"/>')
                parts.append(f'<path d="{d}" fill="none" stroke="{accent}" '
                             f'stroke-width="{STROKE}"/>')
    parts.append(_ring(R_INNER, _hairline(0.12), 1))
    parts.append(_text(CX, NAME_Y, _esc(str(name or "").upper()), NAME_SIZE,
                       _C["text"], weight=700, spacing=4,
                       family=fam or None))
    parts.append(_text(CX, VALUE_Y, "—" if conf is None else f"{conf * 100:.0f}%",
                       VALUE_SIZE, accent if conf is not None else _C["dim"],
                       weight=600))
    parts.append(_text(CX, CAPTION_Y, "CONFIDENCE", CAPTION_SIZE, _C["label"],
                       spacing=2))
    parts.append("</svg>")
    return "".join(parts)


def _hairline(alpha):
    """The console's one line colour at an alpha. An SVG attribute takes a real
    colour, not a Tailwind opacity modifier, so it is spelled out here."""
    return theme._alpha_hex(_C["line"], alpha)
