"""Pure display language for the RRG plot (/sentiment/rrg).

The plot is hand-drawn — absolutely-positioned markers over an SVG tail layer —
rather than a chart library, so all of its geometry is ordinary arithmetic and
lives here, pinned by ``tests/test_rrg_view.py``. ``pages/sentiment_rrg.py``
holds only widgets.

**Why hand-drawn.** The supplied design is a plain scatter with quadrant washes,
a fixed crosshair and per-marker sizing; Highcharts brings a scale model, a
legend, a tooltip engine and a reflow lifecycle that this needs none of, and the
app already carries a documented list of Highcharts traps (the stock module vs
in-place updates, charts collapsing inside inactive tab panels). Eleven markers
and forty-odd line segments do not justify any of that.

**The palette is shared with the Sector Rotation board** — same four quadrant
hues, same warm-neutral ladder — imported from ``pages/rotation_view.py`` rather
than restated, so the two screens in that nav group cannot drift apart.

Three departures from the supplied design, each forced by real data:

1. **The domain is computed, not fixed.** The design hard-codes RS-Ratio
   98.9…101.1. Real five-reading tails reach **97.28**, which that window would
   clip clean off the plot. The domain here is derived from the data and kept
   symmetric about 100 (see :func:`domain`).
2. **The tails are real.** The design generates a plausible spiral with
   ``sampleTail()`` and says so in its own footer. These are the engine's last
   five ``tail`` readings.
3. **No ``vector-effect``.** The design scales a 0–100 viewBox with
   ``preserveAspectRatio="none"`` and relies on
   ``vector-effect:non-scaling-stroke`` to stop the stroke stretching with it.
   That attribute is **not** in DOMPurify's allowlist, so ``ui.html`` strips it
   and every tail renders thick horizontally and hairline vertically. Percentage
   coordinates need neither the viewBox nor the rescue.
"""
import math

from pages.oklch import oklch_hex
from pages.rotation_view import (  # noqa: F401 — the shared palette, deliberately
    NB, NE, NT, QUAD_CHROMA, QUAD_HUE, TONE, fmt_mom, fmt_spread,
    regime_display, regime_sentence,
)

# ── the tail ─────────────────────────────────────────────────────────────────
# How many readings a trail shows. The engine keeps twelve; five is what the
# design's four segments want, and it is the horizon over which a rotation is
# still legible as a direction rather than a scribble.
TAIL_POINTS = 5


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def tail_points(sector, n=TAIL_POINTS):
    """The sector's last ``n`` readings as ``[(rs_ratio, rs_momentum), …]``.

    The engine writes the current position as the FINAL tail sample, so the
    trail ends exactly on the marker rather than near it. A sector with no tail
    falls back to its head alone, which draws a marker and no trail."""
    pts = []
    for p in (sector.get("tail") or []):
        x, y = _num(p.get("rs_ratio")), _num(p.get("rs_momentum"))
        if x is not None and y is not None:
            pts.append((x, y))
    if not pts:
        x, y = _num(sector.get("rs_ratio")), _num(sector.get("rs_momentum"))
        return [(x, y)] if x is not None and y is not None else []
    return pts[-n:]


# ── the domain ───────────────────────────────────────────────────────────────
# The design's window, kept as a FLOOR so a becalmed session does not zoom into
# noise and render a flat tape as violent rotation.
MIN_HALF_X = 1.1
MIN_HALF_Y = 3.4
DOMAIN_PAD = 1.08     # headroom so a marker at the extreme is not cut by the edge
CENTRE = 100.0        # RS-Ratio and RS-Momentum are both indexed to 100


def domain(sectors, n=TAIL_POINTS):
    """The plot window: symmetric about 100, containing every plotted point.

    **Symmetry is load-bearing.** The quadrant washes and the crosshair are drawn
    at exactly 50%/50%, so an asymmetric window would put the axes somewhere
    other than RS-Ratio 100 / RS-Momentum 100 and silently reassign every
    sector's quadrant on screen — the plot would disagree with the panel that
    names the quadrant."""
    xs, ys = [], []
    for s in sectors or []:
        for x, y in tail_points(s, n):
            xs.append(abs(x - CENTRE))
            ys.append(abs(y - CENTRE))
    hx = max(MIN_HALF_X, (max(xs) * DOMAIN_PAD) if xs else 0.0)
    hy = max(MIN_HALF_Y, (max(ys) * DOMAIN_PAD) if ys else 0.0)
    return {"x_lo": CENTRE - hx, "x_hi": CENTRE + hx,
            "y_lo": CENTRE - hy, "y_hi": CENTRE + hy}


def px(value, lo, hi):
    """RS-Ratio → percentage across the plot, left to right."""
    span = hi - lo
    return 50.0 if span <= 0 else (value - lo) / span * 100.0


def py(value, lo, hi):
    """RS-Momentum → percentage down the plot: **inverted**, so stronger
    momentum sits higher, which is the only way an RRG reads."""
    span = hi - lo
    return 50.0 if span <= 0 else 100.0 - (value - lo) / span * 100.0


# ── ticks ────────────────────────────────────────────────────────────────────
_STEPS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 25.0)


def ticks(lo, hi, target=7):
    """Evenly spaced ticks inside ``[lo, hi]``, always including 100.

    Anchored on the centre rather than on ``lo`` so the crosshair always carries
    a label — the one tick a reader actually navigates by."""
    span = hi - lo
    if span <= 0:
        return [CENTRE]
    raw = span / max(1, target)
    step = next((s for s in _STEPS if s >= raw), _STEPS[-1])
    out, k = [], 0
    while True:
        v = CENTRE + k * step
        if v > hi + 1e-9:
            break
        out.append(round(v, 6))
        k += 1
    k = 1
    while True:
        v = CENTRE - k * step
        if v < lo - 1e-9:
            break
        out.append(round(v, 6))
        k += 1
    return sorted(out)


# ── markers ──────────────────────────────────────────────────────────────────
MARKER_BASE = 10.0     # diameter floor: a 0%-weight sector must still be visible
MARKER_SCALE = 1.9


def marker_px(weight):
    """Marker diameter in px for an S&P weight.

    Diameter goes as ``sqrt(weight)``, so the marker's AREA is proportional to
    the weight — the only encoding that reads honestly as "share of the index".
    Scaling the diameter linearly would make Technology look ten times Utilities
    rather than the ~16× in area it actually is."""
    w = _num(weight) or 0.0
    return MARKER_BASE + math.sqrt(max(0.0, w)) * MARKER_SCALE


# ── the quadrant palette ─────────────────────────────────────────────────────
QUADRANT_CORNERS = {"Improving": "top-left", "Leading": "top-right",
                    "Lagging": "bottom-left", "Weakening": "bottom-right"}
# Per-quadrant wash chroma + corner-label lightness, from the design. They vary
# by a hair because equal numbers do NOT read as equal weight across these hues.
_WASH_CHROMA = {"Improving": 0.018, "Leading": 0.020,
                "Lagging": 0.022, "Weakening": 0.020}
_CORNER_L = {"Improving": 0.60, "Leading": 0.62,
             "Lagging": 0.60, "Weakening": 0.64}
_CORNER_C = {"Improving": 0.09, "Leading": 0.10,
             "Lagging": 0.11, "Weakening": 0.10}

QUAD_WASH = {q: f"bg-[{oklch_hex(0.155, _WASH_CHROMA[q], QUAD_HUE[q])}]"
             for q in QUADRANT_CORNERS}
QUAD_CORNER_TXT = {q: f"text-[{oklch_hex(_CORNER_L[q], _CORNER_C[q], QUAD_HUE[q])}]"
                   for q in QUADRANT_CORNERS}
PANEL_HEX = "#0B0C0E"      # the plot fill, and so the halo's inner cut


def _rgba(hexstr):
    """``#rrggbb`` → ``rgba(r,g,b,1)`` with NO spaces.

    A ``shadow-[…]`` arbitrary does not generate from a hex (documented
    Tailwind-JIT gotcha), and a Tailwind arbitrary value cannot contain a
    space."""
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},1)"


# Marker vocabulary: fill, the halo that lifts it off a same-hue wash, and the
# ETF label. Built once from the closed quadrant set, per the Tailwind-first
# rule on data-driven colour. The halo is ONE box-shadow with two stops — a 2px
# cut in the panel colour, then a 1px ring — which is what stops a marker
# dissolving into the quadrant wash behind it, since both share a hue.
QUAD_MARKER = {
    q: {
        "dot": f"bg-[{oklch_hex(0.68, QUAD_CHROMA[q], QUAD_HUE[q])}]",
        "halo": ("shadow-[0_0_0_2px_" + _rgba(PANEL_HEX) + ",0_0_0_3px_"
                 + _rgba(oklch_hex(0.42, QUAD_CHROMA[q] * 0.6, QUAD_HUE[q]))
                 + "]"),
        "label": f"text-[{oklch_hex(0.88, QUAD_CHROMA[q] * 0.7, QUAD_HUE[q])}]",
        "tail": oklch_hex(0.66, QUAD_CHROMA[q], QUAD_HUE[q]),
    }
    for q in QUADRANT_CORNERS
}
FALLBACK_QUADRANT = "Improving"


def marker_classes(quadrant):
    return QUAD_MARKER.get(quadrant, QUAD_MARKER[FALLBACK_QUADRANT])


# ── points + label decluttering ──────────────────────────────────────────────
# Labels sit beside their marker and carry the SECTOR NAME, not the ETF code —
# so the plot is readable without knowing that XLRE is Real Estate. Names are
# ~4x wider than a ticker, which is why the flip rule below measures the label
# instead of using a fixed threshold: "Communication" placed to the right of a
# marker at 70% would hang off the plot entirely.
LABEL_GAP_PX = 8.0
# Two labels closer than this vertically overprint into an unreadable smear.
LABEL_MIN_GAP_PX = 15.0
# Nominal plot size, used only to turn percentage positions into the pixel space
# the gap and the width estimate are expressed in. These are label nudges, not
# layout, so approximate values are fine — the plot's real height is fixed at
# 600 and its width is fluid around this figure.
PLOT_H_PX = 600.0
PLOT_W_PX = 620.0
EDGE_PAD_PX = 6.0
# Mean advance of Instrument Sans at 11.5px, measured across the eleven sector
# names. Only ever used to decide which SIDE a label goes on.
LABEL_CHAR_PX = 6.35


def label_width_px(text):
    """Rough rendered width of a label, for the side decision."""
    return len(str(text or "")) * LABEL_CHAR_PX


def plot_points(sectors, weights, dom):
    """One entry per plottable sector: position, size, palette and label offset.

    Sectors missing either coordinate are DROPPED rather than drawn at the
    origin, which would silently plant a marker on the crosshair and read as a
    real, perfectly neutral sector."""
    out = []
    for s in sectors or []:
        x, y = _num(s.get("rs_ratio")), _num(s.get("rs_momentum"))
        if x is None or y is None:
            continue
        q = s.get("quadrant")
        xp = px(x, dom["x_lo"], dom["x_hi"])
        size = marker_px((weights or {}).get(s.get("etf")))
        label = s.get("name") or s.get("etf") or ""
        # Place the label to the marker's right unless the whole thing would
        # then hang off the plot — measured, because a sector name is wide.
        gap = size / 2 + LABEL_GAP_PX
        fits_right = (xp / 100.0 * PLOT_W_PX + gap + label_width_px(label)
                      <= PLOT_W_PX - EDGE_PAD_PX)
        out.append({
            "etf": s.get("etf"), "name": s.get("name"), "label": label,
            "quadrant": q,
            "x_pct": xp, "y_pct": py(y, dom["y_lo"], dom["y_hi"]),
            "size_px": size, "classes": marker_classes(q),
            "anchor": "left" if fits_right else "right",
            "dx": gap * (1 if fits_right else -1),
            "dy": 0.0,
        })
    return _declutter(out)


def _declutter(points):
    """Push apart labels that would overprint, per side, top to bottom.

    Per side because a label on the left of the plot cannot collide with one on
    the right, and treating them as one column would scatter labels that were
    already perfectly readable."""
    for anchor in ("left", "right"):
        col = sorted((p for p in points if p["anchor"] == anchor),
                     key=lambda p: p["y_pct"])
        last = None
        for p in col:
            y = p["y_pct"] / 100.0 * PLOT_H_PX
            if last is not None and y - last < LABEL_MIN_GAP_PX:
                p["dy"] = (last + LABEL_MIN_GAP_PX) - y
                last = last + LABEL_MIN_GAP_PX
            else:
                p["dy"] = 0.0
                last = y
    return points


# ── the tail layer ───────────────────────────────────────────────────────────
TAIL_W = (1.30, 2.14)      # stroke width: oldest end → newest end
TAIL_O = (0.16, 0.67)      # opacity: oldest end → newest end
# Sub-samples per span of the smoothing spline. Six is where the corners stop
# being visible at this plot size; more only inflates the emitted SVG.
SMOOTH_SAMPLES = 6
# Catmull-Rom tangent scale. 0.5 is the standard uniform form. Lower values pull
# the curve toward the straight polyline, which is the direction of safety here:
# this is a data plot, and a spline that bulges is claiming the sector visited a
# position it never held.
TAIL_TENSION = 0.5


def _catmull_rom(p0, p1, p2, p3, t, tension=TAIL_TENSION):
    """One point on the Catmull-Rom span between ``p1`` and ``p2``."""
    t2, t3 = t * t, t * t * t
    out = []
    for a, b, c, d in zip(p0, p1, p2, p3):
        m1 = tension * (c - a)
        m2 = tension * (d - b)
        out.append((2 * b - 2 * c + m1 + m2) * t3
                   + (-3 * b + 3 * c - 2 * m1 - m2) * t2
                   + m1 * t + b)
    return tuple(out)


def smooth_tail(points, samples=SMOOTH_SAMPLES):
    """A polyline through ``points``, resampled along a Catmull-Rom spline.

    The curve passes through every real reading — the smoothing only decides the
    route *between* them, and the end tangents are clamped (the first and last
    points are duplicated) so a trail never flares off past its own endpoints."""
    pts = list(points or [])
    if len(pts) < 3:
        return pts
    ext = [pts[0]] + pts + [pts[-1]]
    out = [pts[0]]
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for k in range(1, samples + 1):
            out.append(_catmull_rom(p0, p1, p2, p3, k / samples))
    return out


def tail_segments(sectors, dom):
    """Every trail sub-segment, oldest first, fading and thinning toward the past.

    Age is encoded twice on purpose — width *and* opacity. Either alone is
    ambiguous against eleven overlapping trails; together they read as direction
    without needing an arrowhead. Because the trail is resampled along a spline,
    both taper CONTINUOUSLY rather than stepping once per reading."""
    segs = []
    for s in sectors or []:
        pts = smooth_tail(tail_points(s))
        if len(pts) < 2:
            continue
        stroke = marker_classes(s.get("quadrant"))["tail"]
        n = len(pts) - 1
        for i in range(n):
            a, b = pts[i], pts[i + 1]
            u = (i + 1) / n if n else 1.0        # 0 at the oldest end, 1 at now
            segs.append({
                "x1": px(a[0], dom["x_lo"], dom["x_hi"]),
                "y1": py(a[1], dom["y_lo"], dom["y_hi"]),
                "x2": px(b[0], dom["x_lo"], dom["x_hi"]),
                "y2": py(b[1], dom["y_lo"], dom["y_hi"]),
                "stroke": stroke,
                "width": TAIL_W[0] + u * (TAIL_W[1] - TAIL_W[0]),
                "opacity": TAIL_O[0] + u * (TAIL_O[1] - TAIL_O[0]),
            })
    return segs


def tail_svg(sectors, dom):
    """The whole trail layer as one SVG string for ``ui.html``.

    Percentage coordinates, no viewBox: the plot is a fluid width by a fixed
    height, so any viewBox would need non-uniform scaling, and the attribute
    that rescues stroke width under it (``vector-effect``) is stripped by
    DOMPurify. Percentages resolve straight against the viewport and leave
    ``stroke-width`` in real pixels."""
    lines = [
        f'<line x1="{s["x1"]:.2f}%" y1="{s["y1"]:.2f}%" '
        f'x2="{s["x2"]:.2f}%" y2="{s["y2"]:.2f}%" stroke="{s["stroke"]}" '
        f'stroke-width="{s["width"]:.2f}" stroke-linecap="round" '
        f'opacity="{s["opacity"]:.2f}"/>'
        for s in tail_segments(sectors, dom)
    ]
    return ('<svg width="100%" height="100%">' + "".join(lines) + "</svg>")


# ── the alert strip ──────────────────────────────────────────────────────────
# The strip is TINTED BY THE VERDICT rather than left neutral: it is the only
# element above the fold that states a conclusion, and a reader who takes
# nothing else from the page should still leave knowing which way the rotation
# is pointing. Hue follows the same up/down language as the rest of the group.
_STRIP_HUE = {"down": 22.0, "up": 158.0, "flat": 90.0}
_STRIP_CHROMA = {"down": (0.030, 0.070, 0.130),
                 "up": (0.028, 0.060, 0.110),
                 "flat": (0.006, 0.006, 0.010)}

STRIP_TINT = {
    tone: {
        "bg": f"bg-[{oklch_hex(0.16, c[0], _STRIP_HUE[tone])}]",
        "edge": f"border-[{oklch_hex(0.30, c[1], _STRIP_HUE[tone])}]",
        "txt": f"text-[{oklch_hex(0.80, c[2], _STRIP_HUE[tone])}]",
    }
    for tone, c in _STRIP_CHROMA.items()
}
# Full vocabularies for the reactive `.classes(remove=…)` — a partial remove set
# leaves two tints fighting after the second repaint.
STRIP_BG_CLASSES = " ".join(dict.fromkeys(t["bg"] for t in STRIP_TINT.values()))
STRIP_EDGE_CLASSES = " ".join(dict.fromkeys(t["edge"] for t in STRIP_TINT.values()))
STRIP_TXT_CLASSES = " ".join(dict.fromkeys(t["txt"] for t in STRIP_TINT.values()))
STRIP_DOT_CLASSES = " ".join(dict.fromkeys(t["dot"] for t in TONE.values()))


def alert_bar(headline, threshold):
    """The tinted strip under the title: verdict, sentence and the arithmetic."""
    h = headline if isinstance(headline, dict) else {}
    word, tone = regime_display(h.get("regime"))
    t = _num(threshold)
    stats = (f"cyclical {fmt_mom(h.get('cyclical_mom_mean'))} · "
             f"defensive {fmt_mom(h.get('defensive_mom_mean'))} · "
             f"spread {fmt_spread(h.get('spread'))} vs "
             f"±{t:.2f}" if t is not None else
             f"cyclical {fmt_mom(h.get('cyclical_mom_mean'))} · "
             f"defensive {fmt_mom(h.get('defensive_mom_mean'))} · "
             f"spread {fmt_spread(h.get('spread'))}")
    return {
        "word": f"{word} rotation" if tone in ("up", "down") else word,
        "sentence": regime_sentence(h.get("regime"), "")
                    .replace("Money is rotating", "Money is moving"),
        "stats": stats,
        "tone": tone,
    }
