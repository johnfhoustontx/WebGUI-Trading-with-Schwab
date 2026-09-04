"""Gamma page — GEX / Charm / DEX / Vanna exposure + intraday heatmap.

Tier-3 reader: this page holds **no engine call**. The live option-chain fetch +
GammaEngine compute (GEX/Charm/DEX/Vanna) + per-view summary/walls/history grids +
term grid + the Explain document + the Analyze prompt all live in the options
service compute module and are published to the Redis bus by the service
(``cache:options:gamma`` + ``cache:options:gamma_explain`` + ``cache:options:gamma_analyze``).
This page **reads** those cached snapshots and renders them, and drives refresh/
explain/analyze by **enqueuing commands** (``gamma_refresh``/``gamma_explain``/
``gamma_analyze``). The figure/transform builders below stay pure + unit-tested.

JSON-key gotcha (load-bearing): the per-strike ``data`` dicts (gex/charm/dex/vanna,
keyed by ``strike_float``) and the history rows' grid dict (tuple index 6) have
FLOAT keys. Redis stores the snapshot as JSON, so those float keys round-trip to
STRINGS. The pure builders (``bars_from_gex`` sorts + numeric-compares strikes;
``heatmap_matrix`` sorts strikes) require float keys — so the page re-floats them
via ``_refloat_keys`` BEFORE feeding the builders. The builders stay unchanged.
"""
import math
from zoneinfo import ZoneInfo

import app_settings
import page_help as _page_help
from pages import busy as _busy
from pages.ui_guard import guard, guard_async
from shared import market_calendar as _mc
from shared import symbols as _symbols
from . import flow_panels as _fx
from .inputs import select_all_on_focus
from .theme import BTN, BTN_PRIMARY, FLOW_KEYFRAMES_CSS, MUTED

# "Plasma" palette (see docs/plans/2026-08-15-gamma-plasma-palette-design.md): the
# exposure field runs CYAN for call-heavy (positive net) and MAGENTA for put-heavy
# (negative). These two are the ramp's "hot" stops, and they do double duty — the
# by-strike bars and the sided wall lines use them, so a bar, a wall and the cells
# around them all read as the same instrument.
POS_COLOR = "#35c8ff"
NEG_COLOR = "#ff4d8d"
CALL_WALL_COLOR = POS_COLOR
PUT_WALL_COLOR = NEG_COLOR
SPOT_COLOR = "#ffd54f"
PRICE_LINE = "#f5f5f5"          # off-white — spot track overlaid on the dark heatmap
# Level lines that are NOT call/put-sided need colors from OUTSIDE the ramp, or they
# read as data. Lavender (which the walls vacated when they went sided) and amber
# are the two the plasma ramp never reaches — pinned by a test.
FLIP_COLOR = "#b39ddb"
PROJ_FLIP_COLOR = "#ffb74d"   # projected EOD delta-flip (0-DTE charm drift)
PANEL_BORDER = "rgba(120,140,160,0.16)"   # hairline framing the washed plot area

# Dark theme for all charts (matches the app's dark shell).
DARK_BG = "#1b1b1b"
GRID = "#333333"
FONT = "#e6e6e6"
HOVER = {"bgcolor": "#222222", "bordercolor": "#444444", "font": {"size": 11, "color": FONT}}

# Friendlier toggle/title labels: GEX→GAMMA, DEX→DELTA (internal view keys + the
# engine/cache strings stay "GEX"/"DEX" — only the display label changes).
_VIEW_LABELS = {"GEX": "GAMMA", "DEX": "DELTA"}

# Shared plot-area geometry for the "by strike" bars + the intraday heatmap. Both
# panels use the SAME chart height + top/bottom margins so their Strike axes occupy
# the identical vertical pixel band — a given strike lines up across both panels (and
# the shared-strike crosshair lands on the right row in each). The bottom margin fits
# the heatmap's rotated time labels; the bars just leave that space empty.
_PLOT_HEIGHT = 680
_PLOT_MARGIN_TOP = 48
_PLOT_MARGIN_BOTTOM = 64

# Diverging PLASMA color-axis stops. The axis is symmetric about zero (0.0 = most
# negative … 0.50 = zero … 1.0 = most positive) while the reference design's ramps
# are one-sided intensity ramps, so each ramp is MIRRORED outward from the centre:
# put-heavy runs aubergine → magenta → pale pink, call-heavy runs deep blue → cyan
# → ice. Net ≈ 0 stays TRANSPARENT so the wash behind shows through (rgba alpha is
# honored by the interpolated heatmap image) — that is what lets the panel read as
# depth rather than a box.
#
# Two properties here are deliberate, not incidental:
#  • the ALPHA ramp (0 at the centre → ~1 at the extremes) is what blends the field
#    into the page, and the design does the same (`0.10 + a^1.05 * 0.90`);
#  • the BRIGHT stops sit out at 0.88/0.12 rather than spread evenly, so most of the
#    field stays deep blue/aubergine and only the cores reach cyan and ice. That
#    reproduces the design's `pow(a, 1.7)` colour-position shaping without needing a
#    non-linear axis.
# Shared by all four intraday views AND Term via ``_coloraxis``.
HEAT_STOPS = [
    [0.00, "rgba(255,186,220,0.98)"],  # most-negative net → pale pink
    [0.12, "rgba(255,77,141,0.92)"],   # put "hot" (magenta)
    [0.28, "rgba(150,36,122,0.62)"],
    [0.48, "rgba(122,44,92,0.5)"],    # put ramp base, near-zero (lifted)
    [0.50, "rgba(0,0,0,0.0)"],         # zero → transparent (the wash shows through)
    [0.52, "rgba(42,95,155,0.5)"],    # call ramp base, near-zero (lifted)
    [0.72, "rgba(42,118,224,0.62)"],
    [0.88, "rgba(53,200,255,0.92)"],   # call "hot" (cyan)
    [1.00, "rgba(190,248,255,0.98)"],  # most-positive net → ice
]

# The washed plot background, blue at the top fading to magenta at the bottom.
#
# This sets ``plotBackgroundColor``, which commit e6ef342 removed — read that
# before "fixing" it back. There it held a FLAT grey (the old ``HEATMAP_SEP``)
# which showed through the gaps between individually-bordered cells and read as a
# separator mesh. The same commit turned on ``interpolation: True``, which renders
# the heatmap as ONE continuous image with no cell gaps at all — so nothing can
# show BETWEEN cells any more, and a gradient here is a wash painted behind that
# image, not a mesh. Its job is to keep quiet strikes reading as dim colour instead
# of empty space.
def _wash_background():
    return {
        "linearGradient": {"x1": 0, "y1": 0, "x2": 0, "y2": 1},
        "stops": [[0.00, "#15263e"], [0.46, "#16223a"],
                  [0.62, "#241628"], [1.00, "#281422"]],
    }


def _apply_wash(fig):
    """Paint the plasma wash behind a heatmap's plot area and frame it."""
    fig["chart"]["plotBackgroundColor"] = _wash_background()
    fig["chart"]["plotBorderWidth"] = 1
    fig["chart"]["plotBorderColor"] = PANEL_BORDER
    return fig


def _dark_axis(title=None):
    """Shared dark-theme axis options (grid/line/label colors + optional title)."""
    ax = {"gridLineColor": GRID, "lineColor": "#555555",
          "labels": {"style": {"color": FONT}}}
    if title is not None:
        ax["title"] = {"text": title, "style": {"color": FONT}}
    return ax


def _base_chart(chart_type, height):
    """Common Highcharts scaffolding (dark bg, no credits/a11y/legend)."""
    return {
        "chart": {"type": chart_type, "backgroundColor": DARK_BG, "height": height,
                  "spacingTop": 8, "style": {"fontFamily": "inherit"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
    }


def _strike_plotline(value, color, dash, text):
    """A reference plotLine across the strike axis, labeled at its right edge."""
    return {"value": value, "color": color, "width": 2, "dashStyle": dash, "zIndex": 5,
            "label": {"text": text, "align": "right", "x": -4,
                      "style": {"color": color, "fontSize": "10px"}}}


def _strike_step(strikes):
    """Row height for a linear strike axis = the MEDIAN positive gap between
    consecutive strikes (so heatmap cells tile to FILL the panel).

    Using the median, not the minimum: a chain that mixes spacings — e.g. 1.0
    strikes near the money among 2.5 strikes elsewhere (QCOM, SPCX, …) — would,
    under the minimum, get 1.0-tall rows separated by ~1.5 of dead space (thin,
    uneven rows). The median row height tiles the majority spacing densely (like
    $SPX's uniform grid); the few finer strikes just overlap slightly. Falls back
    to 1.0 when there are no gaps."""
    import statistics
    diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return statistics.median(diffs) if diffs else 1.0


# A resampled ladder past this many rows is refused — the raster gains nothing
# beyond the panel's own pixel height (~570px), and a pathological chain (one
# stray half-strike among round ones) would otherwise explode the row count.
_MAX_UNIFORM_ROWS = 240


def uniform_strike_grid(strikes, z):
    """Resample ``(strikes, z)`` onto an EVENLY spaced strike ladder.

    ``interpolation: True`` rasterizes the heatmap onto a canvas laid out on ONE
    uniform row height (``_strike_step``, the median gap). That is fine for a chain
    whose strikes are evenly spaced — every symbol here except **$NDX**, which
    quotes 5-wide near the money among 10-wide elsewhere (measured live: 28 gaps of
    5 among 56 of 10). Under a 10-row canvas the 5-wide strikes collide two-into-one
    and the cells between them are never written, so the upscaled image reads as a
    comb of vertical stripes instead of a smooth field.

    Filling the ladder to the FINEST gap makes the data grid match the canvas grid.
    The inserted rows are linearly interpolated between their bracketing real
    strikes — which invents no information the chart wasn't already implying, since
    an interpolated heatmap shades between samples regardless; it just does the
    shading on a grid the rasterizer can represent. A column is left ``None``
    wherever either bracketing strike is ``None``, so genuine holes stay holes.

    Returns ``(strikes, z)`` UNCHANGED when the ladder is already uniform (the
    common case — no cost), when there is nothing to resample, or when the result
    would exceed ``_MAX_UNIFORM_ROWS``."""
    if len(strikes) < 3:
        return strikes, z
    gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    if not gaps:
        return strikes, z
    step = min(gaps)
    # Uniform already? (fp tolerance — strikes arrive as floats off JSON.)
    if max(gaps) - step <= step * 1e-6:
        return strikes, z
    span = strikes[-1] - strikes[0]
    n = int(round(span / step)) + 1
    if n > _MAX_UNIFORM_ROWS or n <= len(strikes):
        return strikes, z
    ncols = len(z[0]) if z else 0
    ladder = [strikes[0] + i * step for i in range(n)]
    tol = step * 1e-6
    out = []
    j = 0                        # index of the real strike at/below the ladder row
    for y in ladder:
        while j + 1 < len(strikes) and strikes[j + 1] <= y + tol:
            j += 1
        if abs(strikes[j] - y) <= tol:          # a real strike — take its row as-is
            out.append(list(z[j]))
            continue
        if j + 1 >= len(strikes):               # past the last real strike
            out.append([None] * ncols)
            continue
        lo, hi = strikes[j], strikes[j + 1]
        w = (y - lo) / (hi - lo) if hi > lo else 0.0
        a_row, b_row = z[j], z[j + 1]
        out.append([None if (a is None or b is None) else a + (b - a) * w
                    for a, b in zip(a_row, b_row)])
    return ladder, out


def _view_label(view):
    """Display label for a view (GEX→GAMMA, DEX→DELTA; others unchanged)."""
    return _VIEW_LABELS.get(view, view)


def _darker(hexc, factor=0.55):
    """Return a darker shade of a ``#rrggbb`` color (for the beveled bar border)."""
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def _composite(hexc, overlay, alpha):
    """Flatten an ``rgba(overlay, overlay, overlay, alpha)`` layer over an opaque base.

    ``overlay`` is a single channel value (255 = white, 0 = black), since the
    design's bevel is a pure white→black wash. Returns an opaque ``#rrggbb``."""
    h = hexc.lstrip("#")
    base = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(c * (1 - alpha) + overlay * alpha))) for c in base)


# The reference design's glass bevel, as (position, overlay-channel, alpha):
#   linear-gradient(180deg, rgba(255,255,255,.85) 0%, rgba(255,255,255,.28) 22%,
#                   rgba(255,255,255,.04) 46%, rgba(0,0,0,.30) 74%, rgba(0,0,0,.55) 100%)
# laid over the bar's own colour — a bright specular top rolling to a dark
# underside, which is the whole of the "3D" read.
BEVEL_STOPS = [(0.00, 255, 0.85), (0.22, 255, 0.28), (0.46, 255, 0.04),
               (0.74, 0, 0.30), (1.00, 0, 0.55)]

# Design: box-shadow 0 0 12px <bar colour>. Highcharts' shadow `width` is the blur.
GLOW_WIDTH = 12


def bevel_fill(hexc, mirrored=False):
    """The design's glass bevel as a Highcharts gradient fill over ``hexc``.

    An SVG fill takes ONE paint, so the design's CSS overlay-on-top-of-a-colour is
    BAKED into the stops via ``_composite`` rather than layered. Highcharts leaves
    ``gradientUnits`` unset for 0–1 coordinates, so the gradient is
    ``objectBoundingBox`` — each bar gets the bevel scaled to its own box, which is
    what makes a short bar look like a short cylinder rather than a slice of a long
    one (verified live).

    The gradient runs across the bar's THICKNESS, which is the **x** direction for
    BOTH panels — the non-obvious half of this, and measured rather than assumed.
    A Highcharts ``bar`` is a ``column`` whose SERIES GROUP is rotated (live:
    ``transform="translate(72,48) rotate(90 117 685) scale(-1 1)"``), so its points
    are authored in the un-rotated frame exactly like a column's: local x = the
    bar's thickness, local y = its length, and the rotation is what lays it
    horizontally on screen. An objectBoundingBox gradient is resolved in that LOCAL
    frame, so ``x1:0→x2:1`` shades across the thickness in both cases. Using y
    here instead shades along the bar's LENGTH, which reads as a fade rather than
    a bevel.

    ``mirrored`` is the second half of that transform and is easy to miss: the bar
    group is ``rotate(90 …) scale(-1 1)``, and the **scale(-1 1) MIRRORS local x**,
    so an un-mirrored gradient puts the design's white specular stop at the bar's
    BOTTOM edge on screen and the dark stop on top — a bevel lit from underneath.
    Pass ``mirrored=True`` for the rotated ``bar`` panel; the hedge ``column``
    group carries no such flip, so it stays False and is lit from its left edge."""
    x1, x2 = (1, 0) if mirrored else (0, 1)
    return {"linearGradient": {"x1": x1, "y1": 0, "x2": x2, "y2": 0},
            "stops": [[pos, _composite(hexc, ov, a)] for pos, ov, a in BEVEL_STOPS]}


def glow(hexc, width=GLOW_WIDTH):
    """A centred (zero-offset) shadow in the bar's OWN colour — i.e. a glow.

    Highcharts honours ``shadow`` per SERIES only; a per-POINT shadow is silently
    dropped (probed live — the point carries no ``filter`` attribute at all). That
    is why the bar and hedge panels split their data into one series per sign
    instead of colouring points within a single series."""
    return {"color": hexc, "width": width, "offsetX": 0, "offsetY": 0, "opacity": 1}


def line_annotations(spot, flip, walls):
    """Reference-line labels (Spot / Gamma flip / walls) as ``{value, text, color}``.

    Walls are labeled by side: ``Call wall`` (strike ≥ spot, resistance) or
    ``Put wall`` (strike < spot, support). Consumed by ``bar_figure`` to build the
    strike-axis plotLine labels."""
    anns = []
    if spot is not None:
        anns.append({"value": spot, "text": f"Spot {spot:g}", "color": SPOT_COLOR})
    if flip is not None:
        anns.append({"value": flip, "text": f"Gamma flip {flip:g}", "color": FLIP_COLOR})
    for w in (walls or []):
        call = spot is None or w >= spot
        anns.append({"value": w, "text": f"{'Call' if call else 'Put'} wall {w:g}",
                     "color": CALL_WALL_COLOR if call else PUT_WALL_COLOR})
    return anns


def _level_plot_line(value, text, color):
    """One horizontal reference line (+ right-aligned label) for the strike axis."""
    return {"value": value, "color": color, "width": 1, "dashStyle": "Dash",
            "zIndex": 4,
            "label": {"text": text, "align": "right", "x": -6, "y": -4,
                      "style": {"color": color, "fontSize": "10px"}}}


def _is_level(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def wall_plot_lines(spot, walls, flip=None, projected_flip=None):
    """Gamma-flip + Call/Put wall (+ projected EOD flip) levels as yAxis plotLines —
    horizontal, so they run ACROSS the heatmap's full time axis.

    The bar chart already marks these levels on the shared strike axis; extending
    them over the heatmap shows where price sat relative to the flip and the walls
    at every point in the session, not just now. Naming/colors match
    ``line_annotations`` so the two panels read as one.

    ``projected_flip`` is where the DEX curve crosses zero once the 0-DTE book's
    deltas are advanced to the 15:00 CT close by CHARM at flat spot (engine
    ``compute_projected_flip``). It is a 0-DTE DELTA concept, not this view's own
    metric, so it is drawn on EVERY view as a shared reference and labeled
    "Proj. flip" in its own color — the gap between it and the actual flip IS the
    hedging drift, expressed in price. ``None`` on any symbol whose nearest expiry
    isn't today (most of them). Non-numeric levels are skipped rather than raising."""
    out = []
    if _is_level(flip):
        out.append(_level_plot_line(flip, f"Gamma flip {flip:g}", FLIP_COLOR))
    if _is_level(projected_flip):
        out.append(_level_plot_line(
            projected_flip, f"Proj. flip {projected_flip:g}", PROJ_FLIP_COLOR))
    for w in (walls or []):
        if not _is_level(w):
            continue
        call = spot is None or w >= spot
        out.append(_level_plot_line(w, f"{'Call' if call else 'Put'} wall {w:g}",
                                    CALL_WALL_COLOR if call else PUT_WALL_COLOR))
    return out


def _robust_zmax(z, q=0.95):
    """Symmetric color clamp for a heatmap z-grid: the ``q`` percentile of |net|.

    Using a high percentile (not the raw max) keeps a few extreme strikes from
    washing out the mid-range colors. Returns None when there's no non-zero data."""
    vals = sorted(abs(v) for row in (z or []) for v in row if v)
    if not vals:
        return None
    idx = min(len(vals) - 1, int(q * (len(vals) - 1)))
    return vals[idx] or vals[-1]


def _refloat_keys(d):
    """Cast a dict's keys back to float (JSON round-trips float keys → strings).

    The service's per-strike dicts (``{strike_float: {call,put,net}}``) and the
    history rows' grid dicts are stored in Redis as JSON, which stringifies dict
    keys. The pure builders (``bars_from_gex``/``heatmap_matrix``) sort + numeric-
    compare strikes and so REQUIRE float keys. This re-floats them, tolerating
    already-float keys (idempotent) and non-castable keys (passed through). Values
    are untouched. Non-dict input → ``{}``."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        try:
            out[float(k)] = v
        except (TypeError, ValueError):
            out[k] = v
    return out


N_SIDE = 20  # strikes shown on each side of spot (bars + heatmap window)


def strikes_around(strikes, spot, n_side=N_SIDE):
    """The nearest ``n_side`` strikes at/below spot + ``n_side`` strictly above.

    A FIXED COUNT (not a ±% band) so the bar/heatmap window holds a consistent
    number of strikes through the day — the candles/cells stay the same size as
    spot drifts. Lower-priced names with fewer listed strikes naturally get a
    smaller window (slicing just returns what exists). Returns sorted floats; an
    unusable spot returns all numeric strikes sorted."""
    s = sorted(set(x for x in (strikes or []) if isinstance(x, (int, float))))
    if not isinstance(spot, (int, float)):
        return s
    below = [x for x in s if x < spot][-n_side:]   # n nearest strictly below spot
    above = [x for x in s if x > spot][:n_side]     # n nearest strictly above spot
    at = [x for x in s if x == spot]                # the at-spot strike, if listed
    return below + at + above


def bars_from_gex(data, spot, n_side=N_SIDE):
    """Per-strike net exposure for the ``n_side``-each-side window around spot.

    A fixed strike COUNT (see ``strikes_around``) — not a ±% band — so the bar
    count (hence candle width) is consistent through the session. Returns empty
    bars when ``spot`` is missing (e.g. a weekend/off-hours snapshot with no
    underlying price).
    """
    gex = (data or {}).get("gex") or {}
    if not isinstance(spot, (int, float)):
        return {"strikes": [], "nets": [], "colors": [], "hovers": [], "projected": []}
    # Each strike's OWN 0-DTE charm drift (the engine's per-strike drift map).
    # Present only while the nearest expiry is today, and only on the strikes that
    # actually hold 0-DTE interest — so `projected` is None elsewhere and the chart
    # skips drawing an outline that would just sit on top of the solid bar.
    drift = (data or {}).get("hedge_drift_by_strike") or {}
    window = set(strikes_around(gex.keys(), spot, n_side))
    strikes, nets, colors, hovers, projected = [], [], [], [], []
    for strike in sorted(gex):
        if strike not in window:
            continue
        cell = gex[strike] or {}
        net = cell.get("net", 0.0)
        strikes.append(strike)
        nets.append(net)
        colors.append(POS_COLOR if net >= 0 else NEG_COLOR)
        d = drift.get(strike)
        projected.append(net + d if isinstance(d, (int, float))
                         and not isinstance(d, bool) else None)
        hovers.append(f"{strike:g}: net {net:,.0f} "
                      f"(C {cell.get('call', 0):,.0f} / P {cell.get('put', 0):,.0f})")
    return {"strikes": strikes, "nets": nets, "colors": colors, "hovers": hovers,
            "projected": projected}


def union_range(yrange, values, pad_frac=0.01):
    """Expand a ``[lo, hi]`` y-range to include all numeric ``values`` (e.g. the
    intraday spot path), with a small pad so an extreme point isn't flush to the
    edge. Returns ``yrange`` unchanged when there are no numeric values.

    Used so the heatmap's overlaid spot-price line is never clipped when the
    underlying drifted outside the near-spot strike window the bars/heatmap share."""
    nums = [v for v in (values or []) if isinstance(v, (int, float))]
    if not nums:
        return yrange
    lo, hi = yrange
    lo2, hi2 = min(lo, min(nums)), max(hi, max(nums))
    span = hi2 - lo2
    pad = span * pad_frac if span else 0.0
    return [lo2 - pad, hi2 + pad]


def bar_yrange(strikes, spot, pad_frac=0.04):
    """Y-axis [lo, hi] tight to the strikes that actually have bars.

    Pads the strike span by ``pad_frac`` so the outermost bars aren't clipped.
    Falls back to a narrow band around spot when there are no bars.
    """
    spot_ok = isinstance(spot, (int, float))
    if not strikes:
        return [spot * 0.98, spot * 1.02] if spot_ok else [0.0, 1.0]
    lo, hi = min(strikes), max(strikes)
    span = hi - lo
    pad = span * pad_frac if span else (max(spot * 0.002, 1.0) if spot_ok else 1.0)
    return [lo - pad, hi + pad]


def panel_flex(n_cols, full_cols=205, min_heat=0.28, max_heat=0.70):
    """(bar_weight, heat_weight) flex ratio from intraday snapshot count.

    full_cols ≈ two-minute slots in an 08:30–15:20 CT session. The heatmap
    fraction lerps min_heat→max_heat with session progress so the heatmap grows
    and the bars shrink as the day fills in; bars take the remainder."""
    p = 0.0 if full_cols <= 0 else max(0.0, min(1.0, n_cols / full_cols))
    heat = min_heat + (max_heat - min_heat) * p
    return round(1.0 - heat, 4), round(heat, 4)


# Fixed strike/heatmap flex split now that the full day + forward band are shown.
# (strike, heat). Flip to (0.70, 0.30) if the day gets hard to read.
_STRIKE_HEAT_SPLIT = (0.40, 0.60)


# Collector status-bar color → Tailwind arbitrary-value class. The finite color set
# comes from gex_status.classify_collector_status ({green, red, gray, #c48b00}); the
# compute default/fallback is #666666. Exact values preserved as text-[…] classes.
_STATUS_CLASS = {
    "green": "text-[green]",
    "red": "text-[red]",
    "gray": "text-[gray]",
    "#c48b00": "text-[#c48b00]",
}
_STATUS_FALLBACK = "text-[#666666]"
# The full set the reactive status label may carry — removed before each repaint so
# colors don't accumulate across version-poll repaints.
_ALL_STATUS = " ".join(sorted(set(_STATUS_CLASS.values()) | {_STATUS_FALLBACK}))


def status_color_class(color):
    """Map a gex_status collector color to its static Tailwind class (fallback gray)."""
    return _STATUS_CLASS.get(color, _STATUS_FALLBACK)


def status_strip_text(gex_status, summary, countdown):
    """One-line '·'-separated status strip: the named market session + last/next
    scan + next-refresh countdown + the per-view summary. The collector STATUS
    WORD is rendered separately (colored), so it's not included here.

    The session leads with Cboe's own vocabulary (GTH / Regular / Curb / Closed).
    It is the context for everything after it: during GTH only the ~7
    ETH-eligible symbols are collected, so "Last scan" can legitimately point at
    yesterday. Defensive: missing scan fields → em-dashes, and an absent or
    empty session is OMITTED rather than rendered as a bogus 'Session —' (a view
    cached before the field existed, or a degraded one, reports no session).
    """
    st = gex_status or {}
    parts = []
    if st.get("session"):
        parts.append(f"Session {st['session']}")
    parts += [f"Last scan {st.get('last_scan') or '—'}",
              f"Next scan {st.get('next_scan') or '—'}"]
    if isinstance(countdown, int):
        parts.append(f"Next refresh {countdown // 60}:{countdown % 60:02d}")
    if summary:
        parts.append(summary)
    return "  ·  ".join(parts)


def flex_class(grow, grow2=1, basis="0%"):
    """Runtime arbitrary-value Tailwind class for a continuous flex ratio.

    The bar/heatmap split is a genuinely continuous value (~82 distinct ratios over a
    session via panel_flex) with no finite palette, so it uses a JIT-generated
    arbitrary class (`_` = space). Reset per repaint via .classes(remove=prev, add=new)."""
    return f"flex-[{grow}_{grow2}_{basis}]"


# Initial bar/heatmap split (50/50). Single source of truth so the box-creation
# inits and the flex_cur seed in render() can't drift apart.
_INIT_FLEX = flex_class(0.5)


def bar_figure(data, spot, view="GEX", walls=None, flip=None, n_side=N_SIDE, height=680,
               yrange=None):
    """Highcharts horizontal-bar options for one view (dark, beveled, labeled).

    In a Highcharts ``bar`` chart the category axis (``xAxis``) is vertical, so the
    STRIKE axis is ``xAxis`` (linear, with the spot/flip/wall reference plotLines)
    and the exposure axis is ``yAxis``. ``yrange`` (when given) overrides the auto
    near-spot window — used to align the strike axis with the intraday heatmap's."""
    b = bars_from_gex(data, spot, n_side)
    label = _view_label(view)
    yr = yrange if yrange is not None else bar_yrange(b["strikes"], spot)
    # Split by SIGN into two series. Highcharts applies `shadow` (the design's glow)
    # per SERIES — a per-point shadow is silently dropped — so one glow colour per
    # series is the only way each side can glow its own colour. Each point still
    # carries its own bevelled gradient fill.
    pos_pts, neg_pts = [], []
    for s, n, c, h in zip(b["strikes"], b["nets"], b["colors"], b["hovers"]):
        (pos_pts if n >= 0 else neg_pts).append(
            {"x": s, "y": n, "color": bevel_fill(c, mirrored=True),
             "borderColor": _darker(c), "borderWidth": 1,
             "custom": {"hover": h}})
    plotlines = [_strike_plotline(a["value"],
                                  a["color"],
                                  "Solid" if a["text"].startswith("Spot") else
                                  ("Dash" if "flip" in a["text"] else "Dot"),
                                  a["text"])
                 for a in line_annotations(spot, flip, walls)]
    fig = _base_chart("bar", height)
    # Transparent so the page background shows through — matches the heatmap panel
    # (and the candlestick graph) instead of the lighter DARK_BG box.
    fig["chart"]["backgroundColor"] = "transparent"
    # Shared plot geometry so the Strike axis aligns pixel-for-pixel with the heatmap.
    fig["chart"]["marginTop"] = _PLOT_MARGIN_TOP
    fig["chart"]["marginBottom"] = _PLOT_MARGIN_BOTTOM
    fig.update({
        "title": {"text": f"{label} by strike", "style": {"color": FONT}},
        # A Highcharts bar chart reverses its xAxis by default (low strike at top);
        # reversed=False restores high strikes at the TOP, matching the heatmap's
        # linear strike axis so the two panels line up. startOnTick/endOnTick False
        # pins the axis to EXACTLY [yr0, yr1] (no tick-snapping) so the vertical band
        # matches the heatmap's yAxis exactly.
        "xAxis": {**_dark_axis("Strike"), "min": yr[0], "max": yr[1],
                  "reversed": False, "startOnTick": False, "endOnTick": False,
                  "plotLines": plotlines},
        "yAxis": {**_dark_axis(label),
                  "plotLines": [{"value": 0, "color": "#777777", "width": 1, "zIndex": 3}]},
        "tooltip": {"backgroundColor": "#222222", "borderColor": "#444444",
                    "style": {"color": FONT, "fontSize": "11px"},
                    "headerFormat": "", "pointFormat": "{point.custom.hover}"},
        # grouping False so the projected outline OVERLAYS its bar instead of being
        # drawn beside it (which would halve the bar width and break the alignment
        # with the heatmap).
        # crisp False: with crisping ON, a bar whose value is exactly 0 gets its
        # 1px border subtracted from a 0-height rect, so Highcharts emits
        # height="-1" and the browser logs 'A negative value is not valid'. Purely
        # a console warning (nothing renders wrong), but a zero-net strike is
        # routine here, so silence it at the source rather than live with the noise.
        "plotOptions": {"bar": {"pointPadding": 0.04, "groupPadding": 0,
                                "borderRadius": 0, "grouping": False,
                                "crisp": False}},
        "series": [
            {"type": "bar", "name": "Call gamma", "data": pos_pts,
             "colorByPoint": False, "shadow": glow(POS_COLOR)},
            {"type": "bar", "name": "Put gamma", "data": neg_pts,
             "colorByPoint": False, "shadow": glow(NEG_COLOR)},
        ],
    })
    # Projected close: each strike's net after ITS OWN 0-DTE charm drift. Drawn as an
    # outline ON TOP of the solid bar (transparent fill) so it reads whether the
    # projection EXTENDS past the current bar or pulls back INSIDE it — a filled bar
    # behind would be invisible in the pull-back case. Amber, matching the projected
    # flip line. Omitted entirely when the symbol has no 0-DTE book.
    proj_pts = [{"x": s_, "y": pv,
                 "custom": {"hover": f"{s_:g}: projected close {pv:,.0f}"}}
                for s_, pv in zip(b["strikes"], b.get("projected") or [])
                if isinstance(pv, (int, float)) and not isinstance(pv, bool)]
    # ALWAYS emitted, empty when the symbol has no 0-DTE book. This element is
    # updated IN PLACE and Highcharts REPLACES rather than updates series when the
    # count changes — leaving stray paths and shifted colorIndexes, the same trap
    # heatmap_figure documents. Appending this conditionally made the count swing
    # 1↔2 between symbols; with the sign split it is now a fixed 3.
    fig["series"].append({
        "type": "bar", "name": "Projected close", "data": proj_pts,
        "color": "transparent", "borderColor": PROJ_FLIP_COLOR, "borderWidth": 1,
        "enableMouseTracking": True,
        "states": {"inactive": {"enabled": False}, "hover": {"enabled": False}},
    })
    return fig


def _fmt_ts(value):
    """Format an epoch-seconds timestamp as HH:MM; pass strings through."""
    if isinstance(value, (int, float)):
        import datetime as dt
        return dt.datetime.fromtimestamp(value).strftime("%H:%M")
    return str(value)


def _cell_net(cell):
    """Net value from a grid cell — handles both {call,put,net} dicts and bare numbers."""
    if isinstance(cell, dict):
        return cell.get("net")
    return cell


def heatmap_matrix(rows):
    """(x=times, y=strikes, z=[y][x]) of net exposure from gex_history rows.

    Each row is (ts, spot, flip, top_pos, top_neg, net_total, grid_dict) where
    grid_dict maps strike -> {call, put, net} (or a bare net number). Strikes
    whose net is zero across every snapshot are dropped (keeps the heatmap on the
    active strikes near spot instead of the full 3000–9800 chain).
    """
    if not rows:
        return {"x": [], "y": [], "z": [], "spots": []}
    x = [_fmt_ts(r[0]) for r in rows]
    spots = [r[1] if isinstance(r[1], (int, float)) else None for r in rows]
    grids = [r[6] or {} for r in rows]
    strikes = sorted({s for g in grids for s, cell in g.items() if _cell_net(cell)})
    z = [[_cell_net(g.get(s) or {}) for g in grids] for s in strikes]
    return {"x": x, "y": strikes, "z": z, "spots": spots}


def heatmap_categories(rows, projection=None):
    """The heatmap's FULL x-axis categories: collected times + the forward band's.

    The hedge panel below the heatmap is a SEPARATE element whose only job is to
    read vertically against it, so it must be built on THIS list, not on the
    collected times alone — a GEX projection widens the heatmap's axis by up to
    26 forward marks, and a hedge panel spanning only the session would stretch
    those same minutes across the projection's width too. The guard condition
    mirrors ``heatmap_figure``'s projection block exactly (a test pins the two
    together)."""
    times = list(heatmap_matrix(rows)["x"])
    if projection and projection.get("times") and projection.get("grid"):
        times += list(projection["times"])
    return times


def _pin_time_axis(axis, categories):
    """Pin a category time axis to EXACTLY ``len(categories)`` column slots.

    Highcharts derives axis extremes from the series DATA, so two panels sharing a
    category list still scale independently: the hedge series starts at the first
    minute with a 0-DTE book and stops at the last collected column, while the
    heatmap runs the whole session plus its projection band. Unpinned, those bars
    stretch across the full width and land nowhere near the cells above them.
    Both panels pin the same -0.5 … n-0.5 band instead (xAxis start/endOnTick
    default False, so the values are honoured exactly). No categories → no pin: an
    empty range renders as a broken plot band."""
    if categories:
        axis["min"], axis["max"] = -0.5, len(categories) - 0.5
    return axis


def _coloraxis(zmax):
    """Diverging RdYlGn color axis, symmetric about zero (so net 0 = yellow)."""
    ca = {"stops": HEAT_STOPS, "labels": {"enabled": False}}
    if zmax:
        ca["min"], ca["max"] = -zmax, zmax
    return ca


# Candle/OHLC up / down. Deliberately NOT the plasma pair: these encode PRICE
# direction, where green/red is the universal convention and cyan/magenta would
# collide with the call/put meaning carried by every other mark on the panel.
UP_COLOR, DOWN_COLOR = "#7fd1a3", "#e79a9a"

# 0-DTE hedge pressure: dealers must BUY into the close / must SELL. Kept as its
# own pair rather than reusing UP/DOWN_COLOR — that pair also serves the candles,
# and the hedge panel moved to the plasma scheme while the candles did not.
HEDGE_BUY_COLOR = POS_COLOR
HEDGE_SELL_COLOR = NEG_COLOR


def ohlc_bars(spots, interval):
    """``[[x, open, high, low, close], …]`` from the per-snapshot spot samples.

    Spot is stored as a 1-min POINT SAMPLE, not a bar, so bars are derived the way
    any charting tool builds them from a sampled series: ``open`` is the PREVIOUS
    bar's close (carried forward, so bars are contiguous and even a 1-min bar has a
    body — that minute's move — instead of a degenerate O==H==L==C dash), and
    high/low span that open plus this bucket's samples. ``x`` is the bucket's
    CENTRE column so the bar sits over the heatmap cells it summarizes.

    HONEST LIMIT: highs/lows are sampled once a minute, so wicks understate the
    true intra-minute extremes — these are bars over the same series the spot line
    draws, not exchange bars. Buckets with no usable sample emit no bar; a None
    sample is skipped rather than read as 0 (which would spike the low). Never
    raises."""
    try:
        step = int(interval)
    except (TypeError, ValueError):
        return []
    if step <= 0:
        return []
    out, prev_close = [], None
    spots = list(spots or [])
    for start in range(0, len(spots), step):
        chunk = spots[start:start + step]
        vals = [v for v in chunk
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            continue
        o = prev_close if prev_close is not None else vals[0]
        c = vals[-1]
        out.append([start + (len(chunk) - 1) // 2, o, max([o] + vals),
                    min([o] + vals), c])
        prev_close = c
    return out


def candle_points(bars):
    """(body, wick) point lists for the candle/OHLC overlay.

    Candlesticks are a Highcharts STOCK series, and loading the stock module
    breaks this chart outright — it patches ``Chart.update``, which this heatmap
    depends on for its flicker-free in-place refresh, leaving the chart with zero
    series (live-verified). So the bars are drawn from two core series instead:
    a ``columnrange`` body (open→close) and an ``errorbar`` wick (low→high), each
    point carrying its OWN color so up and down bars are distinguishable within
    one series."""
    body, wick = [], []
    for b in (bars or []):
        try:
            x, o, hi, lo, c = b[0], b[1], b[2], b[3], b[4]
        except (TypeError, IndexError):
            continue
        color = UP_COLOR if c >= o else DOWN_COLOR
        body.append({"x": x, "low": min(o, c), "high": max(o, c), "color": color})
        wick.append({"x": x, "low": lo, "high": hi, "color": color,
                     "stemColor": color, "whiskerColor": color})
    return body, wick


def hedge_points(hedge_rows, col_ts=None):
    """``[[x, $B, color], …]`` for the hedge-pressure panel.

    x is the heatmap COLUMN carrying that row's timestamp — ``col_ts`` is the
    heatmap rows' ts in column order, and a row whose ts is not a column is
    DROPPED. Row position is not a column index: ``load_hedge_series`` skips
    minutes with a NULL pressure (the column is only written while the nearest
    expiry is today), and the two series are RTH-filtered independently, so one
    missing minute would slide every later bar a column left — under the wrong
    cells, which is exactly what the panel exists to be compared against. With no
    ``col_ts`` it falls back to the row index (callers with no heatmap to align
    to).

    Dollars are converted to BILLIONS (raw values run to 1e9+ and are unreadable),
    and each point is colored by SIGN — positive means dealers must BUY into the
    close, negative SELL — so a flip from one to the other is visible at a glance.
    The colours are the plasma pair (``HEDGE_*_COLOR``), which ``hedge_figure`` also
    partitions its two series on."""
    # First column wins a duplicated ts; the collector writes one row per view per
    # boundary, so a repeat means a re-run, and the earlier column is the one the
    # heatmap drew.
    index = None
    if col_ts is not None:
        index = {}
        for i, t in enumerate(col_ts):
            index.setdefault(t, i)
    out = []
    for i, r in enumerate(hedge_rows or []):
        v = (r or {}).get("hedge_pressure")
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if index is None:
            x = i
        else:
            x = index.get((r or {}).get("ts"))
            if x is None:
                continue
        out.append([x, round(v / 1e9, 4),
                    HEDGE_BUY_COLOR if v >= 0 else HEDGE_SELL_COLOR])
    return out


def hedge_summary_text(hedge_rows):
    """One-line read of the CURRENT hedge pressure ('' when there is none)."""
    pts = [r.get("hedge_pressure") for r in (hedge_rows or [])
           if isinstance((r or {}).get("hedge_pressure"), (int, float))]
    if not pts:
        return ""
    v = pts[-1]
    side = "buy" if v >= 0 else "sell"
    return (f"0-DTE hedge pressure {'+' if v >= 0 else '-'}${abs(v)/1e9:.2f}B — "
            f"dealers must {side} into the close if spot holds")


def hedge_figure(hedge_rows, times, height=150, col_ts=None):
    """Compact signed-column panel of 0-DTE hedge pressure over the session.

    Its OWN element, below the heatmap: pressure is in DOLLARS while the heatmap's
    y-axis is STRIKE (and is pixel-aligned to the bar chart), so it cannot share
    that axis. Horizontal alignment is therefore something this figure has to
    reproduce rather than inherit, and it takes BOTH halves of it: ``times`` is
    the heatmap's full category list (``heatmap_categories`` — projection band
    included, so a column is the same width in both panels) and ``col_ts`` maps
    each pressure reading onto the column holding its timestamp. Both panels also
    run marginLeft/marginRight 0, so equal category counts mean equal plot bands."""
    pts = hedge_points(hedge_rows, col_ts)
    fig = _base_chart("column", height)
    fig["chart"]["backgroundColor"] = "transparent"
    fig["chart"]["marginLeft"] = 0
    fig["chart"]["marginRight"] = 0
    fig.update({
        "title": {"text": None},
        "legend": {"enabled": False},
        "xAxis": _pin_time_axis(
            {**_dark_axis(), "categories": list(times or []),
             "labels": {"enabled": False}}, times),
        "yAxis": {**_dark_axis(), "title": {"text": None},
                  "labels": {"enabled": False},
                  "plotLines": [{"value": 0, "color": "#42506b", "width": 1,
                                 "zIndex": 3}]},
        # Split by SIGN for the same reason as the bar panel: the glow is a
        # per-SERIES shadow, so buy-side and sell-side need their own series to
        # glow their own colour. Both are ALWAYS emitted (empty when that side has
        # no pressure) so the count stays fixed at 2 across in-place updates.
        "series": [
            {"type": "column", "name": name,
             "data": [{"x": x, "y": y, "color": bevel_fill(colour)}
                      for x, y, c in pts if (c == colour)],
             "borderWidth": 0, "groupPadding": 0.02, "pointPadding": 0.0,
             "shadow": glow(colour),
             # Same zero-height crisping warning as the bar chart — and pressure
             # legitimately decays to exactly 0 at the close, so it WILL happen daily.
             "crisp": False,
             "states": {"inactive": {"enabled": False}, "hover": {"enabled": False}},
             "enableMouseTracking": True,
             "tooltip": {"headerFormat": "",
                         "pointFormat": "Hedge {point.y:+,.2f}B"}}
            for name, colour in (("Hedge buy", HEDGE_BUY_COLOR),
                                 ("Hedge sell", HEDGE_SELL_COLOR))
        ],
    })
    return fig


def track_points(values):
    """[[time_index, level], …] for a level track, keeping None as a GAP.

    Nulls must be preserved rather than skipped: dropping them would shift every
    later point one column to the left, silently mis-dating the movement."""
    return [[i, v if isinstance(v, (int, float)) and not isinstance(v, bool) else None]
            for i, v in enumerate(values or [])]


def heatmap_figure(rows, view="GEX", height=680, yrange=None, projection=None,
                   walls=None, spot=None, flip=None, levels=None,
                   show_tracks=False, spot_style="line", spot_interval=5,
                   projected_flip=None):
    """Intraday strike×time Highcharts heatmap (dark, cell separators, concise
    hover) with the underlying spot-price line overlaid on the same (linear)
    strike axis. ``yrange`` (when given) sets the Strike axis range so it aligns
    with the bar chart's near-spot window.

    ``projection`` (GEX only) appends a forward band: extra time columns of
    projected net-per-mark cells on the SAME heatmap series/colorAxis, a 'now'
    divider between the collected and future columns, the Spot line continued flat
    along the cone midline, and faint EM up/down cone overlays."""
    m = heatmap_matrix(rows)
    times, strikes, z = m["x"], m["y"], m["z"]
    # Only build cells for strikes within the visible ``yrange`` window. GEX net is
    # ~0 away from spot (heatmap_matrix already drops those), but Charm/DEX/Vanna
    # are non-zero across the WHOLE chain (~250 strikes) — emitting all of them is
    # ~45k points to serialize + render every repaint when only the near-spot
    # window shows. Cropping here keeps every view as light as GEX.
    if yrange is not None:
        lo, hi = yrange
        vis = [yi for yi in range(len(strikes)) if lo <= strikes[yi] <= hi]
    else:
        vis = list(range(len(strikes)))
    # Fill an unevenly spaced ladder ($NDX quotes 5-wide near the money among
    # 10-wide) so the data grid matches the uniform grid `interpolation: True`
    # rasterizes onto — otherwise the finer strikes collide two-into-one row and
    # the unwritten cells between them read as vertical stripes. No-op (same lists
    # back) for every evenly spaced chain. MUST run on the VISIBLE strikes only:
    # the full chain spans ~3000-9800 with wide wing gaps, which would resample to
    # thousands of rows and be refused by the row cap.
    vstrikes, vz = uniform_strike_grid([strikes[yi] for yi in vis],
                                       [z[yi] for yi in vis])
    # Heatmap points [time_index, strike_value, net]: x is the time category index,
    # y is the ACTUAL strike (linear axis) so the continuous spot line overlays.
    data = [[xi, vstrikes[yi], vz[yi][xi]]
            for yi in range(len(vstrikes)) for xi in range(len(times))
            if vz[yi][xi] is not None]
    # Symmetric color clamp from the VISIBLE cells' 95th-percentile |net| (robust —
    # same as the Term heatmap) so a few extreme strikes don't wash the mid-range
    # colors to transparent on the flatter views (Charm / DEX / Vanna).
    zmax = _robust_zmax(vz) or None
    # Row height = the (now uniform) ladder's spacing, so cells tile the window
    # densely and every canvas row has a strike to fill it.
    rowsize = _strike_step(vstrikes)
    # Blended look: interpolation renders one smooth image (no per-cell borders or
    # separator mesh); states.inactive disabled so nothing fades on hover/click.
    no_fade = {"inactive": {"enabled": False}, "hover": {"enabled": False}}
    series = [{"type": "heatmap", "name": "net", "data": data,
               "colsize": 1, "rowsize": rowsize,
               "interpolation": True, "borderWidth": 0, "states": no_fade,
               "tooltip": {"headerFormat": "",
                           "pointFormat": "Strike {point.y} · net {point.value:,.0f}"}}]
    spots = m.get("spots") or []
    # Underlying price track over the session (on the shared Strike axis; a line series
    # ignores the colorAxis so it isn't recolored by net value). Built here, appended
    # (with the two EM-cone series) AFTER the projection block below.
    spot_pts = [[xi, sp] for xi, sp in enumerate(spots) if isinstance(sp, (int, float))]

    # Forward projection band (GEX only): extend the figure with future columns,
    # a 'now' seam, the spot line continued along the cone midline, and EM cones.
    xaxis_plotlines = []
    em_up_pts, em_down_pts = [], []
    if projection and projection.get("times") and projection.get("grid"):
        ptimes = list(projection["times"])
        base = len(times)
        pgrid = projection["grid"]
        cone = projection.get("cone") or {}
        # Future heatmap cells on the SAME heatmap series/colorAxis, cropped to yrange.
        proj_rows_for_zmax = []
        heat_series = series[0]      # the heatmap series
        pairs = []
        for strike, vals in pgrid.items():
            try:
                sk = float(strike)
            except (TypeError, ValueError):
                continue
            if yrange is not None and not (yrange[0] <= sk <= yrange[1]):
                continue
            pairs.append((sk, list(vals)))
        pairs.sort()                 # uniform_strike_grid needs an ordered ladder
        pstrikes = [sk for sk, _ in pairs]
        prows = [vals for _, vals in pairs]
        # Same ladder fill as the collected cells — the projection sits on the
        # chain's own strikes, so on a mixed ladder it would speckle the band with
        # unwritten rows while the session behind it renders smooth.
        pstrikes, prows = uniform_strike_grid(pstrikes, prows)
        for sk, vals in zip(pstrikes, prows):
            proj_rows_for_zmax.append([v for v in vals if v is not None])
            for j, v in enumerate(vals):
                if v is not None:
                    heat_series["data"].append([base + j, sk, v])
        # Re-clamp the color axis over collected + projected visible cells (robust
        # 95th-pct so a few extreme 0-DTE ATM close cells don't wash the scale).
        zmax = _robust_zmax(vz + proj_rows_for_zmax) or None
        # 'now' divider between the last collected and first future column.
        xaxis_plotlines.append({"value": base - 0.5, "color": "#8a93a3", "width": 1,
                                "dashStyle": "Dash", "zIndex": 4,
                                "className": "gamma-now-divider",
                                "label": {"text": "now", "style": {"color": FONT},
                                          "rotation": 0, "y": 12}})
        # Continue the Spot line flat along cone.mid into the future.
        for j, mid in enumerate(cone.get("mid") or []):
            if isinstance(mid, (int, float)):
                spot_pts.append([base + j, mid])
        em_up_pts = [[base + j, lvl] for j, lvl in enumerate(cone.get("up") or [])
                     if isinstance(lvl, (int, float))]
        em_down_pts = [[base + j, lvl] for j, lvl in enumerate(cone.get("down") or [])
                       if isinstance(lvl, (int, float))]
        times = times + ptimes

    # Append the three line series in a FIXED order (Spot, EM up, EM down) so EVERY
    # view emits exactly 4 series (heatmap + these 3). A VARYING series count breaks
    # the in-place chart.update() — Highcharts replaces (not updates) series, shifting
    # colorIndex + leaving stray line paths — which rendered the heatmap as a mess of
    # thin lines when toggling GEX<->Charm/DELTA/Vanna. Empty data → an inert series.
    def _line_series(name, pts, color, **extra):
        s = {"type": "line", "name": name, "data": pts, "color": color,
             "marker": {"enabled": False}, "colorAxis": False, "states": no_fade,
             "tooltip": {"headerFormat": "", "pointFormat": name + " {point.y:,.2f}"}}
        s.update(extra)
        return s
    # Spot overlay: line, candles, or OHLC. All three series ALWAYS exist (empty
    # when not selected) so the series count stays fixed — see the note above. The
    # bar styles share one geometry: a columnrange BODY + an errorbar WICK. "OHLC"
    # is the same bars drawn thin, so it reads as a bar rather than a filled candle
    # (true left/right open-close ticks need the stock module, which breaks this
    # chart's in-place update — see candle_points).
    _bars = (ohlc_bars(spots, spot_interval)
             if spot_style in ("candle", "ohlc") else [])
    _body, _wick = candle_points(_bars)
    # The white track crosses bright cyan cores, so it carries the design's dark
    # halo — a series ``shadow``, NOT a second underlaid line series, because the
    # series COUNT is load-bearing for the in-place chart.update() (see above).
    series.append(_line_series("Spot", spot_pts if spot_style == "line" else [],
                               PRICE_LINE, lineWidth=2, enableMouseTracking=True,
                               shadow={"color": "rgba(0,0,0,0.85)", "width": 5,
                                       "offsetX": 0, "offsetY": 0, "opacity": 1}))
    series.append({
        "type": "columnrange", "name": "Spot candles", "data": _body,
        "colorAxis": False, "states": no_fade, "borderWidth": 0,
        "grouping": False, "enableMouseTracking": False,
        **({"pointWidth": 2} if spot_style == "ohlc" else {}),
    })
    series.append({
        "type": "errorbar", "name": "Spot wicks", "data": _wick,
        "colorAxis": False, "states": no_fade, "grouping": False,
        "whiskerLength": ("60%" if spot_style == "ohlc" else 0),
        "enableMouseTracking": False,
    })
    series.append(_line_series("EM up", em_up_pts, "#7fd1a3", lineWidth=1,
                               dashStyle="ShortDash", enableMouseTracking=False))
    series.append(_line_series("EM down", em_down_pts, "#e79a9a", lineWidth=1,
                               dashStyle="ShortDash", enableMouseTracking=False))
    # Level-movement tracks (toggleable): where the flip + walls sat at each
    # snapshot. SOLID, against the dashed static plotLines of the same color — so
    # the pair reads as "the level now" (dashed) and "how it got there" (solid).
    # Stepped, because a wall holds a strike then JUMPS to the next one; a smooth
    # line would imply levels that never existed. Emitted even when toggled off
    # (empty data) to keep the series count fixed — see the note above.
    lv = levels or {}
    for name, key, color in (("Flip track", "flip", FLIP_COLOR),
                             ("Call wall track", "call_wall", CALL_WALL_COLOR),
                             ("Put wall track", "put_wall", PUT_WALL_COLOR)):
        pts = track_points(lv.get(key)) if show_tracks else []
        series.append(_line_series(name, pts, color, lineWidth=1, step="left",
                                   enableMouseTracking=False))
    # The bar chart already labels the Strike axis and the heatmap shares its EXACT
    # y-range, so hide the heatmap's (duplicate) strike labels + title and drop its
    # left-axis gutter — the cells butt directly against the bars.
    yaxis = {**_dark_axis(), "startOnTick": False, "endOnTick": False,
             "title": {"text": None}, "labels": {"enabled": False},
             # ALWAYS emit the key (empty when there are no levels): in-place
             # chart.update() MERGES options, so omitting it would leave the
             # previous view's flip/wall lines painted over the new view.
             "plotLines": wall_plot_lines(spot, walls, flip, projected_flip)}
    if yrange is not None:
        yaxis["min"], yaxis["max"] = yrange[0], yrange[1]
    fig = _base_chart("heatmap", height)
    fig["chart"]["backgroundColor"] = "transparent"     # same as the candlestick graph
    _apply_wash(fig)     # blue→magenta wash behind the cells; quiet strikes stay lit
    # Shared plot geometry so the Strike axis aligns pixel-for-pixel with the bars.
    fig["chart"]["marginTop"] = _PLOT_MARGIN_TOP
    fig["chart"]["marginBottom"] = _PLOT_MARGIN_BOTTOM   # room for rotated time labels
    # No strike-label gutter (bars own that axis) → cells butt against the bar panel;
    # zero right margin → the heatmap runs to the panel's right edge.
    fig["chart"]["marginLeft"] = 0
    fig["chart"]["marginRight"] = 0
    # Press-and-hold tooltip hook must be on whatever options the element MOUNTS
    # with — _render_view overwrites the init fig's options before the client
    # mounts, so carry the load hook here too (load fires once at mount).
    fig["chart"]["events"] = {":load": _HEAT_PRESS_TOOLTIP_JS}
    fig.update({
        "title": {"text": f"{_view_label(view)} intraday (strike × time)",
                  "style": {"color": FONT}},
        # No "Time" axis title — the HH:MM labels make it obvious, and the title was
        # getting clipped at the bottom edge under the rotated labels.
        "xAxis": _pin_time_axis(
            {**_dark_axis(), "categories": times,
             "labels": {"rotation": -45, "style": {"color": FONT}},
             "plotLines": xaxis_plotlines}, times),
        "yAxis": yaxis,
        "colorAxis": _coloraxis(zmax),
        "series": series,
    })
    return fig


# Make the heatmap + spot-line tooltip PRESS-AND-HOLD (no hover text): a chart
# .events.load hook gates Highcharts' tooltip.refresh so it only paints while the
# left mouse button is held. On mousedown we flip the gate on and show the point
# under the cursor; while held, Highcharts' own mousemove → runPointActions →
# refresh keeps the popup following the cursor (works for the interpolated heatmap
# AND the line); on mouseup (anywhere) we hide it and gate off again. Plain hover
# shows nothing. Shipped as a NiceGUI ``:``-dynamic-property → ``new Function``;
# installed ONCE at element creation (load fires once) and survives in-place option
# updates (heatmap_figure never re-sets the global tooltip or chart.events). The
# held-guard makes the document mouseup listener a no-op for a destroyed chart.
_HEAT_PRESS_TOOLTIP_JS = (
    "function(){var c=this;if(!c.tooltip)return;"
    "var orig=c.tooltip.refresh.bind(c.tooltip),held=false;"
    "c.tooltip.refresh=function(p){if(held)orig(p);};"
    "c.container.addEventListener('mousedown',function(ev){"
    "if(ev.button!==0)return;held=true;"
    "c.pointer.runPointActions(c.pointer.normalize(ev));});"
    "document.addEventListener('mouseup',function(){"
    "if(held){held=false;if(c.tooltip)c.tooltip.hide(0);}});}"
)


# Shared crosshair spanning BOTH panels. Installed once on the chart row (matched by
# the .gamma-xhair-row hook class); it appends two absolutely-positioned overlay
# lines and, on mousemove over either chart's plot area, draws a HORIZONTAL strike
# line across the WHOLE row (so the same strike is marked on the bars AND the heatmap
# — the alignment payoff) plus a VERTICAL cursor line at the pointer within the
# hovered panel's plot band. pointer-events:none so it never blocks the charts'
# own tooltips (incl. the heatmap press-and-hold). MT/MB match _PLOT_MARGIN_* so the
# plot band is located correctly; querying .highcharts-container live on each move
# survives chart recreation (bar↔Term). Idempotent via the row._xhair latch.
_CROSSHAIR_JS = (
    "(()=>{const row=document.querySelector('.gamma-xhair-row');"
    "if(!row||row._xhair)return;row._xhair=true;"
    "const MT=%d,MB=%d;"
    "const mk=(css)=>{const d=document.createElement('div');"
    "d.style.cssText='position:absolute;pointer-events:none;display:none;z-index:6;'+css;"
    "row.appendChild(d);return d;};"
    "const h=mk('left:0;right:0;height:0;border-top:1px dashed rgba(245,245,245,0.6);');"
    "const v=mk('width:0;border-left:1px dashed rgba(245,245,245,0.6);');"
    "const hide=()=>{h.style.display='none';v.style.display='none';};"
    "row.addEventListener('mousemove',(e)=>{"
    "const rr=row.getBoundingClientRect();let hit=null;"
    "row.querySelectorAll('.highcharts-container').forEach((c)=>{const r=c.getBoundingClientRect();"
    "if(e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom)hit=r;});"
    "if(!hit){hide();return;}const top=hit.top+MT,bot=hit.bottom-MB;"
    "if(e.clientY<top||e.clientY>bot){hide();return;}"
    "h.style.top=(e.clientY-rr.top)+'px';h.style.display='block';"
    "v.style.left=(e.clientX-rr.left)+'px';v.style.top=(top-rr.top)+'px';"
    "v.style.height=(bot-top)+'px';v.style.display='block';});"
    "row.addEventListener('mouseleave',hide);})();"
) % (_PLOT_MARGIN_TOP, _PLOT_MARGIN_BOTTOM)


def _heat_init_fig(height=680):
    """Initial (empty) heatmap element options + the press-and-hold-tooltip load hook.

    The heatmap element is persistent (created once, updated in place), and
    ``chart.events.load`` only fires at creation — so the hook must be present on
    the element's FIRST figure, not added later by heatmap_figure."""
    fig = _base_chart("heatmap", height)
    fig["title"] = {"text": None}
    fig["series"] = []
    # A colorAxis MUST be present when the heatmap element is CREATED: Highcharts 12.4
    # won't bind a colorAxis added later via chart.update(), so a heatmap series pushed
    # into a colorAxis-less chart renders nothing (no image / no cells). The persistent
    # heat element is created from this fig and updated in place, so seed the colorAxis
    # here (min/max come later from heatmap_figure's data).
    fig["colorAxis"] = _coloraxis(None)
    fig["tooltip"] = {"enabled": True}      # needed so chart.tooltip exists for press
    fig["chart"]["events"] = {":load": _HEAT_PRESS_TOOLTIP_JS}
    return fig


def _empty_fig(height=680):
    """Minimal dark-themed empty Highcharts options for first paint / hidden state."""
    fig = _base_chart("bar", height)
    fig["title"] = {"text": None}
    fig["series"] = []
    return fig


def _set_figure(element, fig):
    """Update a persistent ``ui.highchart`` in place (Highcharts diffs the new
    options internally → no canvas teardown, no flicker), mirroring the old
    Plotly ``update_figure`` contract."""
    element.options = fig
    element.update()


# Scoped explain CSS (rules only). Injected via ui.add_css for the in-app dialog
# (NiceGUI strips <style> from ui.html) and inlined into the downloadable doc.
EXPLAIN_CSS = """
.gx-explain{font-family:'Segoe UI',system-ui,sans-serif;color:#e6e6e6;line-height:1.55;max-width:920px;}
.gx-explain .gx-title{font-size:1.55rem;font-weight:700;color:#ffffff;margin:.1em 0 .05em;}
.gx-explain .gx-sub{opacity:.7;font-size:.9rem;margin:0 0 1.1em;}
.gx-explain h2{font-size:1.15rem;color:#90caf9;margin:1.4em 0 .35em;
  border-bottom:1px solid #3a3a3a;padding-bottom:5px;letter-spacing:.3px;}
.gx-explain h3{font-size:1rem;color:#ffd54f;margin:1em 0 .25em;}
.gx-explain p{font-size:.92rem;margin:.3em 0;}
.gx-explain ul{margin:.3em 0 .7em 1.3em;padding:0;}
.gx-explain li{font-size:.92rem;margin:.2em 0;}
.gx-explain hr{border:0;border-top:1px solid #333;margin:1.1em 0;}
.gx-explain .footer{opacity:.65;font-size:.82rem;font-style:italic;}
"""


def wrap_explain(symbol, body_html, full=False):
    """Wrap explain body HTML in the scoped ``gx-explain`` container.

    full=False -> fragment for inline injection (page provides CSS via add_css).
    full=True  -> standalone HTML document with the CSS inlined (for download).
    """
    inner = (f'<div class="gx-explain">'
             f'<div class="gx-title">Gamma Tool Explain — {symbol}</div>'
             f'<div class="gx-sub">Dealer-positioning read across GEX, Charm, DEX and Vanna.</div>'
             f"{body_html}</div>")
    if not full:
        return inner
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f"<title>Gamma Tool Explain — {symbol}</title>"
            f"<style>body{{background:#1b1b1b;margin:0;padding:24px;}}{EXPLAIN_CSS}</style>"
            f"</head><body>{inner}</body></html>")


# The Flow view is drawn by ``flow_panels.divergence_panel`` (an SVG console
# panel), not by a Highcharts figure — see that module's docstring for why. The
# old ``flow_figure`` and its FLOW_PRICE/FLOW_CALL/FLOW_PUT palette are gone with
# it; the summary line below survives because it feeds the shared status strip.


def _flow_num(v):
    return v if isinstance(v, (int, float)) else None


def flow_summary_text(rows):
    """One-line status for the Flow view header."""
    rows = rows or []
    if not rows:
        return "No flow data yet for this session (collected going forward)."
    last = rows[-1]
    cp, pp = _flow_num(last.get("call_prem")), _flow_num(last.get("put_prem"))
    if cp is None or pp is None:
        return ("Premium not collected yet for this session — populates going "
                "forward (price + volume shown).")
    cv, pv = int(last.get("call_vol") or 0), int(last.get("put_vol") or 0)
    return (f"Today: call ${cp / 1e6:,.1f}M · put ${pp / 1e6:,.1f}M premium · "
            f"net ${(cp - pp) / 1e6:+,.1f}M · {cv:,} call / {pv:,} put contracts")


# ── Net Prem view ───────────────────────────────────────────────────────────
# Intraday net premium (cumulative call $ − cumulative put $) for any combination
# of ~28 symbols, from ``cache:options:net_premium``.
#
# The group table comes from ``config/symbols.toml`` via ``shared.symbols`` - the
# SAME file ``services/options_svc/net_premium.GROUPS`` reads. It used to be a
# deliberate byte-copy of that constant, because Tier 1 may not import
# ``services.*`` and tests were the only thing keeping the two in step. Reading a
# config FILE is not a services import, and ``theme.toml`` is the standing
# precedent for Tier 1 doing exactly this, so the duplication is gone rather than
# merely policed. The COLOUR table below stays here: it is presentation, and
# nothing outside this page needs it.
#
# Everything here is TOTAL over the payload. ``NetPremiumSnapshot.series`` is typed
# as a bare ``dict``, so ``{'SPY': 'notalist'}``, ``{'SPY': [[1]]}``, ``{'SPY': [None]}``
# and ``{'SPY': [[1, 'x', 'y']]}`` all pass validation and can really arrive here.
# Positional row access is PARTIAL — unlike the ``.get()`` reads the matrix page uses,
# a bare ``row[1]`` raises IndexError and 500s the WHOLE Dealer Positioning page. So a
# malformed row skips that point, a malformed symbol skips that series, and the rest
# of the chart still renders.
#
# Shape note: JSON round-trip is not identity. Tuples arrive as LISTS and dict keys
# as STRINGS, so the page always sees ``list`` rows keyed by ``str`` — which is what
# makes positional access viable at all. Nothing below may assume tuples.
NET_PREM_GROUPS = _symbols.netprem_groups()

# One fixed colour per symbol — keyed by SYMBOL, never by position in the
# selection, so a line keeps its colour whether you plot 2 names or 20 (a
# palette-by-index would recolour every line each time you tick a checkbox).
# Mutually distinct + covering every group member; both pinned by tests.
NET_PREM_COLORS = {
    "$SPX": "#f5f5f5", "$NDX": "#8ab4ff", "BIG10": "#ffd166", "SPY": "#4dd0e1",
    "QQQ": "#b388ff", "IWM": "#ff8a65", "DIA": "#9ccc65",
    "XLB": "#a1887f", "XLC": "#4fc3f7", "XLE": "#ffb74d", "XLF": "#66bb6a",
    "XLI": "#90a4ae", "XLK": "#7986cb", "XLP": "#f06292", "XLRE": "#26a69a",
    "XLU": "#dce775", "XLV": "#ce93d8", "XLY": "#ff8a80",
    "NVDA": "#76ff03", "AVGO": "#ff5252", "AAPL": "#eeeeee", "META": "#448aff",
    "MSFT": "#00e5ff", "TSLA": "#ff4081", "PLTR": "#ffab40", "AMZN": "#ffd54f",
    "GOOGL": "#69f0ae", "AMD": "#e57373",
}
NET_PREM_FALLBACK = "#9e9e9e"
NET_PREM_MODES = {"dollars": "Dollars ($M)", "skew": "Skew %"}
_NET_PREM_AXIS = {"dollars": "Net premium ($M)", "skew": "Net premium (%)"}

_NP_CT = ZoneInfo("America/Chicago")
# The service's GEX collection window + a staleness bound at 2× its 1-min publish
# cadence. Only INSIDE this window is a stale publish evidence of a failure — see
# net_prem_status_text.
#
# Read from ``shared.market_calendar`` (backed by ``config/sessions.toml``), which
# is what options_svc's scheduler now gates on too — so the two CANNOT drift. This
# used to be a hand-copied ``(8, 0), (15, 20)`` literal pinned by an AST test that
# re-parsed the service file; the start had already moved once (08:30 → 08:00).
# Importing ``shared.*`` is allowed under the 3-tier rule (only ``services.*`` is
# off limits to the webgui).
_np_open_t, _np_close_t = _mc.window_bounds("collection")
_NP_WINDOW_OPEN = (_np_open_t.hour, _np_open_t.minute)
_NP_WINDOW_CLOSE = (_np_close_t.hour, _np_close_t.minute)
_NP_STALE_SEC = 120


def net_prem_symbols():
    """Every symbol the Net Prem view can plot, in group order (a flat list)."""
    out = []
    for group in NET_PREM_GROUPS:
        for sym in group["symbols"]:
            if sym not in out:
                out.append(sym)
    return out


# Membership set for the selection guard — see _np_selected.
_NET_PREM_KNOWN = frozenset(net_prem_symbols())


def net_prem_group_symbols(group_key):
    """The symbols belonging to one group, or ``[]`` for an unknown key.

    Backs the "Only this group" button, which is the single place the group tab
    is allowed to change what is PLOTTED rather than merely what is visible.
    Total over junk so a persisted group key that no longer exists degrades to
    "no group" instead of raising."""
    for group in NET_PREM_GROUPS:
        if group["key"] == group_key:
            return list(group["symbols"])
    return []


def net_prem_only_group(selected, group_key):
    """``selected`` reduced to the symbols of ``group_key``, order preserved.

    Backs the "Only this group" button. It KEEPS that group's existing ticks
    rather than selecting the whole group, so the button is a narrowing — the
    chart can only lose lines by pressing it, never gain ones you didn't ask
    for. Unknown group → ``[]`` (see net_prem_group_symbols)."""
    active = set(net_prem_group_symbols(group_key))
    return [s for s in _np_selected(selected) if s in active]


def net_prem_with_group(selected, group_key):
    """``selected`` plus every symbol of ``group_key``, in group order.

    Backs "Select all", which is scoped to the ACTIVE group rather than all 28:
    the tick-boxes on screen ARE that group, so ticking a hidden 28 would put
    lines on the chart whose source the reader cannot see. Other groups' ticks
    survive — pair it with "Only this group" to get exactly one whole group."""
    keep = set(_np_selected(selected)) | set(net_prem_group_symbols(group_key))
    return [s for s in net_prem_symbols() if s in keep]


def net_prem_color(symbol):
    """The fixed line colour for a symbol; grey for anything unrecognised."""
    return NET_PREM_COLORS.get(symbol, NET_PREM_FALLBACK)


def _np_num(value):
    """A finite float, or None.

    Rejects bools (``True`` is an ``int`` in Python, so an unguarded flag would
    read as a premium of 1.0) and nan/inf, which Highcharts renders as a hole in
    the line rather than an obvious error.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _np_pair(row):
    """``(call, put)`` from a ``[ts, call_prem, put_prem]`` row, or None.

    Indexing is wrapped rather than type-gated so anything that indexes like the
    published row works, while ``None``, truncated rows, mappings, strings and
    bare numbers all degrade to None instead of raising.
    """
    try:
        call, put = _np_num(row[1]), _np_num(row[2])
    except (TypeError, IndexError, KeyError):
        return None
    if call is None or put is None:
        return None
    return call, put


def net_prem_value(row, mode="dollars"):
    """The plotted value for one ``[ts, call_prem, put_prem]`` row.

    ``dollars`` → net premium in $M (call − put). ``skew`` → the signed share of
    the session's total traded premium that the net represents, as a percent —
    which is what makes a $30M index line comparable with a $2M sector one.

    Skew returns None when nothing traded either side: there is no ratio to
    report (and no divide-by-zero). A genuine ``(0, 0)`` therefore plots as 0.0
    in dollars but is simply absent in skew — the same distinction the service's
    ``_project`` preserves upstream. An unknown mode degrades to dollars.
    """
    pair = _np_pair(row)
    if pair is None:
        return None
    call, put = pair
    if mode == "skew":
        total = call + put
        if total <= 0:
            return None
        return (call - put) / total * 100.0
    return (call - put) / 1e6


def _np_rows(series, symbol):
    """``[(ts, row), …]`` ts-ascending for one symbol; ``[]`` when it has nothing.

    A symbol whose payload is not a readable list of rows yields ``[]`` — one bad
    symbol must never take the rest of the chart down with it.
    """
    if not isinstance(series, dict):
        return []
    rows = series.get(symbol)
    if not isinstance(rows, (list, tuple)):
        return []
    out = []
    for row in rows:
        try:
            ts = _np_num(row[0])
        except (TypeError, IndexError, KeyError):
            continue
        if ts is None or _np_pair(row) is None:
            continue
        out.append((ts, row))
    out.sort(key=lambda pair: pair[0])   # Highcharts line data must be x-ascending
    return out


def _np_selected(symbols):
    """The selection deduped, in the caller's order, restricted to KNOWN symbols.

    The two filters are both load-bearing, because the selection is **persisted**
    to ``webgui/data/settings.json`` — a tracked, hand-editable file with no type
    validation on read — so it is untrusted input, not something the checkbox UI
    fully controls:

    - **non-``str`` entries are dropped**, or a hand-edited ``[123]`` reaches
      ``", ".join(missing)`` and raises TypeError, 500-ing the whole page;
    - **unknown symbols are dropped**, so a stale saved ticker (one since removed
      from ``NET_PREM_GROUPS``) cannot raise ValueError in the caller's
      ``key=order.index`` sort. It is also unplottable by definition — the groups
      ARE this view's universe.

    Guarding here rather than at each use site means every downstream builder can
    assume the selection is a clean subset of ``net_prem_symbols()``.
    """
    out = []
    for sym in symbols or ():
        if isinstance(sym, str) and sym in _NET_PREM_KNOWN and sym not in out:
            out.append(sym)
    return out


def _np_latest(series, symbols, mode):
    """``{symbol: last reportable value}`` over an ALREADY-selected symbol list.

    The single definition of "this symbol has something to show in this mode",
    shared by ``net_prem_missing`` and ``net_prem_summary_text`` so the header
    cannot contradict the chart.
    """
    out = {}
    for sym in symbols:
        for _ts, row in reversed(_np_rows(series, sym)):
            value = net_prem_value(row, mode)
            if value is not None:
                out[sym] = value
                break
    return out


def net_prem_missing(series, symbols, mode="dollars"):
    """The selected symbols with nothing to plot in ``mode``, in selection order.

    The service OMITS a symbol entirely when it collected nothing, so naming them
    is the only way the UI can say "no data yet" instead of leaving the reader to
    wonder about an absent line. A present-but-malformed payload counts as
    missing too: same user-visible outcome — nothing to plot.

    **Mode-aware on purpose.** A symbol whose only rows are ``(0, 0)`` has
    perfectly parseable data yet draws NO line in skew mode (there is no ratio to
    report), so a mode-blind answer would call it present while the chart showed
    nothing. "Missing" here means exactly what the chart does.
    """
    mode = mode if mode in NET_PREM_MODES else "dollars"
    picked = _np_selected(symbols)
    latest = _np_latest(series, picked, mode)
    return [sym for sym in picked if sym not in latest]


# The Net Prem view is drawn by ``flow_panels.field_panel`` (an SVG console
# panel), not by a Highcharts figure. ``net_prem_figure`` is gone with it; the
# readers it stood on (``_np_selected`` / ``_np_rows`` / ``net_prem_value``) are
# unchanged and now feed the panel directly, so the data path is the same one
# the old chart used and its tests still cover it.


def _np_fmt(value, mode):
    """A reading in its mode: ``+$4.0M`` / ``-80%`` (sign leads, so it reads as
    a direction rather than as ``$-4.0M``)."""
    sign = "+" if value >= 0 else "-"
    if mode == "skew":
        return f"{sign}{abs(value):,.0f}%"
    return f"{sign}${abs(value):,.1f}M"


def _np_lead_label(value, high):
    """The adjective for an extreme reading, never contradicting its own sign.

    "most call-led" is nonsense on a NEGATIVE net: when the whole selection is
    put-led, the top of the range is merely the *least* put-led of them. Reading
    "most call-led SPY -$4.0M" beside "most put-led QQQ -$8.0M" would look to a
    trader like the header disagreeing with itself.
    """
    if high:
        return "most call-led" if value >= 0 else "least put-led"
    return "most put-led" if value < 0 else "least call-led"


def net_prem_summary_text(series, symbols, mode="dollars"):
    """One-line header for the Net Prem view: how many symbols are plotted, the
    current extreme on each side, and any selected names with no data yet.

    Each symbol's reading is its LAST reportable point, so the line answers
    "where does the money sit right now" rather than describing the whole day.
    Shares ``net_prem_missing``'s definition of missing, so the header can never
    name a symbol the chart is actually drawing (or vice versa).
    """
    mode = mode if mode in NET_PREM_MODES else "dollars"
    picked = _np_selected(symbols)
    if not picked:
        return "Select symbols to plot intraday net premium (call $ − put $)."

    latest = _np_latest(series, picked, mode)
    missing = net_prem_missing(series, picked, mode)
    tail = f" · no data yet: {', '.join(missing)}" if missing else ""

    n = len(latest)
    if not n:
        return (f"0 of {len(picked)} selected symbols plotted{tail} "
                f"(collected going forward).")
    parts = [f"{n} symbol{'' if n == 1 else 's'} plotted"]
    top = max(latest, key=lambda s: latest[s])
    bottom = min(latest, key=lambda s: latest[s])
    if top == bottom:                       # one symbol — no two extremes to name
        parts.append(f"{top} {_np_fmt(latest[top], mode)}")
    else:
        parts.append(f"{_np_lead_label(latest[top], True)} {top} "
                     f"{_np_fmt(latest[top], mode)}")
        parts.append(f"{_np_lead_label(latest[bottom], False)} {bottom} "
                     f"{_np_fmt(latest[bottom], mode)}")
    return " · ".join(parts) + tail


def _np_parse_ts(iso):
    """A UTC-aware datetime from the payload's ISO ``ts``; None if unparseable."""
    import datetime as dt
    if not isinstance(iso, str) or not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _np_in_window(now):
    """True when ``now`` falls inside the service's 08:00–15:20 CT collection
    window on a trading day — the only time a stale publish means a failure.

    Weekend + NYSE-holiday gated (reusing ``alerts``' calendar, which the whole
    app already shares) so a quiet Saturday or a Thanksgiving cannot be reported
    as a broken publisher. Imported inside the function: ``alerts`` imports
    ``pages.options.scanner``, so keeping it off this module's import line avoids
    coupling the Gamma page's import order to it.
    """
    import alerts
    ct = now.astimezone(_NP_CT)
    if ct.weekday() >= 5 or alerts.is_market_holiday(ct.date()):
        return False
    return _NP_WINDOW_OPEN <= (ct.hour, ct.minute) < _NP_WINDOW_CLOSE


def net_prem_status_text(payload, now=None):
    """Status line for the Net Prem view, separating the three service outcomes
    the page otherwise cannot tell apart.

    ``publish_net_premium`` ends three ways: a valid publish; a contract-validation
    failure; an outer failure. The latter two are LOGGED, not cached — so from
    here they look exactly like "collection has not started yet": a stale or absent
    key. Eleven sector lines will legitimately be empty for a while after ship, so
    "the service is broken" must not be indistinguishable from "nothing collected".
    The payload's own ``ts`` (already in the contract, already validated) separates
    them:

    - absent payload  → never published (service down, or first boot)
    - fresh ts, empty series → published fine, nothing collected yet
    - ts older than ~2 min while INSIDE the 08:00–15:20 CT collection window on a
      trading day → the publisher is failing

    A stale ``ts`` OUTSIDE that window is deliberately NOT flagged: off-hours the
    key legitimately holds the last tick of the session, which is correct
    persistence for this view and matches how the heatmap and Flow views behave.
    Flagging it would cry wolf every evening and all weekend.

    ``now`` is a parameter rather than read from the clock so this stays PURE and
    testable; the page supplies it.
    """
    import datetime as dt
    # isinstance, not `payload or {}`: a non-dict payload would otherwise survive
    # the guard and blow up on the first .get() — total intent, carried through.
    p = payload if isinstance(payload, dict) else {}
    if not p:
        # Neutral about the cause: an absent key means EITHER the options service
        # never published OR Memurai is down, and this branch cannot tell which.
        return ("Net premium has never been published — check the options "
                "service and Memurai on /status")

    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:                       # never crash on a naive caller
        now = now.replace(tzinfo=dt.timezone.utc)
    raw = p.get("series")
    n = len(raw) if isinstance(raw, dict) else 0
    when = _np_parse_ts(p.get("ts"))
    age = (now - when).total_seconds() if when is not None else None

    parts = []
    if p.get("session_date"):
        parts.append(f"session {p['session_date']}")
    # "collected", not a bare count: the summary line beside this one says
    # "N symbols plotted" (the SELECTION), so an unqualified "27 symbols" here
    # reads as a contradiction rather than as the published universe.
    parts.append(f"{n} collected")
    if when is not None:
        parts.append("updated "
                     + when.astimezone(_NP_CT).strftime("%I:%M %p").lstrip("0"))
    text = " · ".join(parts)

    if age is not None and age > _NP_STALE_SEC and _np_in_window(now):
        text += (f" · STALE — nothing published for {int(age // 60)} min inside "
                 "the collection window; check the options service")
    elif n == 0 and not p.get("error"):
        text += " · not collected yet (fills from the next 1-min poll)"
    if p.get("error"):
        # Rendered VERBATIM, never matched on — the house pattern
        # (matrix.status_text): these strings are user-facing UI copy.
        text += f" · {p['error']}"
    return text


# Hairline between adjacent expiry columns on the Term heatmap. Width 1 is the
# thinnest a crisp SVG stroke goes; the low alpha is what makes it read as a hair
# rather than a rule, so it separates the columns without competing with the data.
EXPIRY_SEP_COLOR, EXPIRY_SEP_WIDTH = "rgba(255,255,255,0.22)", 1


def expiry_separators(expirations):
    """xAxis plotLines splitting the Term heatmap between each expiration column.

    ``interpolation: True`` blends the Term view into one continuous field exactly
    as it does intraday — but here the x axis is DAYS, not minutes, so the blending
    is misleading: it smears one expiration's exposure into the next when nothing
    actually varies continuously between them. A hairline on each category boundary
    (at ``i + 0.5``, the midpoint between category centres) restores the reading
    that each column is its own expiry. Empty for 0 or 1 expirations — there is no
    boundary to draw."""
    exps = expirations or []
    return [{"value": i + 0.5, "color": EXPIRY_SEP_COLOR, "width": EXPIRY_SEP_WIDTH,
             "zIndex": 5, "className": "gamma-expiry-sep"}
            for i in range(len(exps) - 1)]


def term_heatmap(term_grid):
    """Highcharts heatmap options for the Term view (net GEX by expiry × strike).

    Strikes with all-zero net across expirations are dropped. Both axes are
    categorical (no overlay), and the color scale is clamped symmetrically to a
    robust max so a few extreme strikes don't wash out the mid-range cells.

    Note the axes being CATEGORICAL is also why this view never showed the mixed
    strike-ladder striping the intraday heatmap did (see ``uniform_strike_grid``):
    every strike is its own row by index, so an uneven ladder cannot collide rows
    here. The trade-off is that the y axis is ordinal, not proportional to price —
    a 5-wide and a 10-wide gap occupy the same height.
    """
    grid = term_grid or {}
    exps = grid.get("expirations") or []
    raw_cells = grid.get("cells") or {}
    # Strike keys round-trip to STRINGS through Redis JSON; re-float per expiry so the
    # numeric sort + ``{s:g}`` labels below work (idempotent for already-float keys).
    cells = {exp: _refloat_keys(raw_cells.get(exp) or {}) for exp in exps}
    strikes = sorted({k for exp in exps for k, v in (cells.get(exp) or {}).items()
                      if isinstance(k, (int, float)) and (v or {}).get("net_gex_usd")})
    z = [[((cells.get(exp) or {}).get(s) or {}).get("net_gex_usd") for exp in exps]
         for s in strikes]
    data = [[xi, yi, z[yi][xi]]
            for yi in range(len(strikes)) for xi in range(len(exps))
            if z[yi][xi] is not None]
    no_fade = {"inactive": {"enabled": False}, "hover": {"enabled": False}}
    fig = _base_chart("heatmap", 680)
    fig["chart"]["backgroundColor"] = "transparent"     # same as the candlestick graph
    _apply_wash(fig)                                    # same wash as intraday
    # Blended + press-and-hold tooltip, same as the intraday heatmap. The Term view
    # is painted on chart_el (recreated on the bar↔Term kind switch), so the load
    # hook rides this figure and fires on that recreation.
    fig["chart"]["events"] = {":load": _HEAT_PRESS_TOOLTIP_JS}
    fig.update({
        "title": {"text": "Term structure (net GEX by expiry × strike)",
                  "style": {"color": FONT}},
        "xAxis": {**_dark_axis("Expiration"), "categories": exps,
                  "plotLines": expiry_separators(exps)},
        "yAxis": {**_dark_axis("Strike"), "categories": [f"{s:g}" for s in strikes]},
        "colorAxis": _coloraxis(_robust_zmax(z)),
        "series": [{"type": "heatmap", "name": "net", "data": data,
                    "interpolation": True, "borderWidth": 0, "states": no_fade,
                    "tooltip": {"headerFormat": "",
                                "pointFormat": "net {point.value:,.0f}"}}],
    })
    return fig


def summary_text(summary, view):
    # Spot (already the on-chart spot line) and strike-count (a data-loaded indicator
    # only) were dropped as low-value; net + flip are the actual reads.
    s = summary or {}
    parts = [f"{view}"]
    if s.get("net_total") is not None:
        parts.append(f"net {s['net_total']:,.0f}")
    if s.get("flip") is not None:
        parts.append(f"flip {s['flip']:.1f}")
    return "  ·  ".join(parts)


def dex_hedge_suffix(hedge):
    """0-DTE hedge-pressure summary suffix for the Delta view, folded into the
    bottom-right status strip so DEX has no separate tiles row. Returns the n/a note
    when the nearest expiry isn't 0-DTE."""
    h = hedge or {}
    hp = h.get("hedge_pressure")
    if hp is None:
        return "hedge n/a (nearest expiry not 0-DTE)"
    return (f"Net Δ {h.get('net_delta_0dte') or 0:,.0f}  ·  "
            f"proj close {h.get('projected_net_delta_close') or 0:,.0f}  ·  "
            f"hedge {hp:,.0f}")


# view name -> (tuple index from calc_all_from_chain, engine view string)
_VIEWS = {"GEX": (0, "gex"), "Charm": (1, "charm"), "DEX": (2, "dex"), "Vanna": (3, "vanna")}

# Subtab order. Net Prem sits beside Flow — they are the two options-FLOW views
# (Flow is one symbol's call/put premium over the session; Net Prem is the net of
# that across many symbols at once), so a reader comparing them doesn't cross Term.
_VIEW_ORDER = list(_VIEWS) + ["Flow", "Net Prem", "Term"]


def chart_kind(fig):
    """Identity of a figure for the single full-width chart element.

    ``_set_chart`` updates the element IN PLACE when the kind is unchanged (the
    common flicker-free repaint) and RECREATES it when the kind changes, because
    Highcharts' ``update()`` MERGES options and leaks the previous figure's
    config through.

    Keying on ``chart.type`` alone is not enough. Flow and Net Prem are BOTH
    ``"line"`` charts, but Flow declares a LIST of three banded yAxes
    (``top``/``height`` of "0%"/"62%" and "68%"/"32%") while Net Prem declares a
    single unbanded dict. A merge drops that dict onto axis 0 and leaves axes 1
    and 2 alive, so Net Prem renders squeezed into the top 62% with two orphaned
    "Premium ($M)" / "Net premium ($M)" axes still painted. The axis TOPOLOGY is
    precisely the merge surface, so it belongs in the identity — and deriving the
    kind from the figure itself (rather than threading the view name in from the
    caller) means a future view can't regress this by forgetting an argument.

    The count is deliberately NOT part of the identity beyond list-vs-single: a
    Net Prem repaint changes its SERIES count as symbols are ticked, and tearing
    the element down on every checkbox would flash. Total over junk — it runs on
    every repaint, so a malformed figure must not 500 the page.
    """
    fig = fig if isinstance(fig, dict) else {}
    chart = fig.get("chart")
    ctype = chart.get("type") if isinstance(chart, dict) else None
    axes = fig.get("yAxis")
    # 0 = a single axis dict (or none); N = a list of N banded axes.
    return (ctype, len(axes) if isinstance(axes, list) else 0)


_DEFAULT_SYMBOL = "$SPX"
_FALLBACK_SYMBOLS = ["$SPX", "SPY", "QQQ"]


def symbol_options(cached):
    """Dropdown option list from the cached gamma_symbols view.

    ``cached`` is ``{"symbols":[...]}`` (or None when the bus is cold). Returns a
    list with ``$SPX`` guaranteed present and FIRST (so it's the default), order
    otherwise preserved. Cold/empty → the index-trio fallback."""
    syms = (cached or {}).get("symbols") if isinstance(cached, dict) else None
    if not syms:
        return list(_FALLBACK_SYMBOLS)
    ordered = [_DEFAULT_SYMBOL] + [s for s in syms if s != _DEFAULT_SYMBOL]
    out, seen = [], set()
    for s in ordered:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def history_key(view) -> str:
    """Cache view holding one Gamma view's intraday history rows.

    Each view's history is its OWN key rather than a field of the gamma snapshot:
    measured in prod the four blobs were ~1.1 MB EACH against ~400 KB for the rest
    of the payload, and this page draws one view at a time (2026-08-20).
    """
    return f"options:gamma_hist_{str(view).lower()}"


def history_rows(payload, symbol):
    """Rows out of a history payload, but ONLY if they belong to ``symbol``.

    The snapshot and the history keys are separate writes, so a reader can pair a
    new snapshot with an older history. Inside one symbol that is harmless -- the
    rows are append-only for the session -- but across symbols it would draw one
    symbol's heatmap beneath another's bars, so the stamp is checked and a
    mismatch reads as "no history yet". ``symbol=None`` (before the first snapshot
    lands) accepts whatever is there. Junk-tolerant; never raises.
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    if symbol:
        stamped = payload.get("symbol")
        if not stamped or str(stamped).upper() != str(symbol).upper():
            return []
    return rows


def history_dates(cached):
    """Distinct briefing dates (newest first) from the cached gamma_briefings index.

    ``cached`` is ``{"briefings":[{date, slot, …}, …]}`` (metadata, already newest
    first) or None. Used to populate the history-picker date dropdown."""
    briefings = (cached or {}).get("briefings") if isinstance(cached, dict) else None
    out, seen = [], set()
    for b in (briefings or []):
        d = (b or {}).get("date")
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def render():
    import bus_client
    from nicegui import ui, run

    ui.add_css(EXPLAIN_CSS)  # scoped styles for the Explain dialog (ui.html strips <style>)
    # The Flow/Net Prem console panels' ONE escape-hatch: a keyframes animation
    # cannot be an inline style, and those panels are raw ui.html fragments.
    ui.add_css(FLOW_KEYFRAMES_CSS)
    # No page title — the tab strip names the page (2026-07-11 dead-space cleanup).

    # state["snap"] is the cached snapshot from the bus (None until first read).
    # ``fetching`` is an in-flight guard so a slow off-loop big-payload read
    # (cache:options:gamma, ~0.4 MB since the 2026-08-20 history split, plus the
    # visible view's ~1.1 MB history key) can't pile up across 2 s poll ticks.
    # state["netprem"] is the (separate, ~500 KB) cache:options:net_premium payload
    # with its own in-flight guard — it is symbol-independent, so it is NOT part of
    # the gamma snapshot and must not share the big snapshot's ``fetching`` latch.
    # state["hist"] caches per-view history rows {view: rows}, each fetched from its
    # OWN key on demand and CLEARED whenever the gamma version moves. The page draws
    # one view at a time, so only the views actually looked at are ever paid for.
    state: dict = {"snap": None, "countdown": 120, "fetching": False,
                   "netprem": None, "np_fetching": False, "np_bulk": False,
                   "hist": {}, "hist_fetching": False}
    # Last-seen bus cache versions for the fetch-free repaint/dialog timers.
    seen = {"gamma": None, "explain": None, "analyze": None, "status": None,
            "netprem": None}

    # View picker as SUBTABS directly under the main tab strip (2026-07-11 — was a
    # ui.toggle button group in the header row): a second tab level, styled by the
    # shared .compact-subtabs rule. Renders into main.subtab_slot() (the slot the
    # shell mounts beneath the strip); falls back inline if the slot is absent.
    # Same value/on_value_change API as the old toggle, so the wiring is unchanged.
    import main as _shell
    _slot = _shell.subtab_slot()

    def _build_view_tabs():
        tabs = ui.tabs(value="GEX").classes("compact-subtabs").props(
            "dense no-caps inline-label align=left")
        with tabs:
            for v in _VIEW_ORDER:
                tab = ui.tab(v, label=_view_label(v))
                _h = _page_help.subtab_help("/options/gamma", v)
                if _h:
                    with tab:
                        ui.tooltip(_h).props("delay=350 max-width=340px")
        return tabs

    if _slot is not None:
        with _slot:
            view_toggle = _build_view_tabs()
    else:
        view_toggle = _build_view_tabs()
    # "… › Dealer Positioning › Gamma". Needs a labeller: the tab VALUES are the
    # engine keys (GEX/DEX), while the strip shows GAMMA/DELTA — the header should
    # read what the tab reads, in sentence case rather than the strip's caps.
    _shell.bind_breadcrumb_leaf(
        view_toggle, lambda v: _view_label(_shell._view_name(v)).title())

    with ui.row().classes("items-center gap-3 flex-wrap w-full"):
        _sym_opts = symbol_options(bus_client.read("options:gamma_symbols"))
        symbol_in = select_all_on_focus(
            ui.select(_sym_opts, value=_DEFAULT_SYMBOL,
                      with_input=True, label="Symbol").classes("w-40"))
        fetch_btn = ui.button("Refresh now", icon="refresh", color=None).props("no-caps").classes(BTN_PRIMARY)
        # Overlay the intraday movement of the flip + walls on the heatmap. Off by
        # default (it adds three lines to an already-dense chart); the choice is
        # persisted so it survives navigation and restarts.
        tracks_sw = ui.switch("Level movement",
                              value=bool(app_settings.get("gamma_level_tracks")))
        tracks_sw.props("dense").classes("text-xs")
        tracks_sw.tooltip("Show where the gamma flip and the call/put walls sat "
                          "through the session, not just now")
        # Spot overlay style. Candles/OHLC are BUCKETED from the same 1-min spot
        # samples the line draws (see ohlc_bars); the bar-size picker is hidden for
        # the line, where it would mean nothing.
        spot_style_sel = ui.select(
            {"line": "Line", "candle": "Candles", "ohlc": "OHLC"},
            value=app_settings.get("gamma_spot_style") or "line",
            label="Spot").props("dense options-dense").classes("w-28")
        spot_style_sel.tooltip("How to draw the spot price over the heatmap")
        spot_int_sel = ui.select(
            {1: "1 min", 5: "5 min", 15: "15 min"},
            value=app_settings.get("gamma_spot_interval") or 5,
            label="Bar").props("dense options-dense").classes("w-24")
        spot_int_sel.tooltip("Bar size for candles / OHLC. Highs and lows are "
                             "sampled once a minute, so wicks understate the true "
                             "intra-minute range.")
        # Explain / Analyze / Briefings push to the RIGHT of the frame (2026-07-11).
        ui.space()
        explain_btn = ui.button("Explain", icon="help", color=None).props("no-caps").classes(BTN)
        analyze_btn = ui.button("Analyze", icon="psychology", color=None).props("no-caps").classes(BTN)
        # Auto briefings: the $SPX/SPY/QQQ Analyze the options service auto-generates at
        # premarket / ~18 min after open / midday / close, folded into a single dropdown
        # to save a header row. Each item opens that slot's briefing in a new tab (the
        # slot key is separate from the ad-hoc Analyze key, so these never auto-open). An
        # item is **highlighted** only when its slot's data is from TODAY (CT); prior-day
        # data (e.g. over the weekend) stays dim — see _sync_sched_btns. Clickable
        # whenever data exists; disabled when a slot has never run.
        _SCHED_HL = "bg-[#2563eb] text-white opacity-100"  # today's briefing is ready
        _SCHED_DIM = "opacity-40"                           # prior-day data, or none yet
        sched_btns = {}
        _sched_titles = {}
        briefings_btn = ui.button("Briefings", icon="schedule", color=None).props("no-caps").classes(BTN)
        with briefings_btn:
            _briefings_menu = ui.menu()
            with _briefings_menu:
                for _slot, _title in (("premarket", "Premarket"), ("open", "Open"),
                                      ("midday", "Midday"), ("close", "EOD recap")):
                    _mi = ui.menu_item(_title, on_click=lambda s=_slot: ui.navigate.to(
                        f"/options/analyze?slot={s}", new_tab=True))
                    _mi.classes(f"text-[#cdd8ee] {_SCHED_DIM}")
                    _mi.set_enabled(False)
                    _mi.tooltip(f"{_title} $SPX/SPY/QQQ briefing — not generated yet today")
                    sched_btns[_slot] = _mi
                    _sched_titles[_slot] = _title

    # --- Net Prem controls (this view only) ---------------------------------
    # Shown/hidden as one block by _sync_np_controls, the same way the Bar-size
    # picker hides for the line spot style: a control that does nothing on the
    # active view is worse than no control.
    #
    # The persisted selection only SEEDS the checkboxes. Everything downstream
    # derives the plotted list from the checkbox map (_np_current), so a
    # hand-edited settings.json naming a retired ticker simply matches no
    # checkbox and self-heals — there is no `list.index()` to raise, which
    # @guard would re-raise into a 500.
    _np_seed = app_settings.get("gamma_netprem_symbols")
    _np_seed = {s for s in (_np_seed if isinstance(_np_seed, (list, tuple)) else [])
                if isinstance(s, str)}
    _np_group_labels = {g["key"]: g["label"] for g in NET_PREM_GROUPS}
    _np_group0 = app_settings.get("gamma_netprem_group")
    if _np_group0 not in _np_group_labels:
        _np_group0 = NET_PREM_GROUPS[0]["key"]
    _np_mode0 = app_settings.get("gamma_netprem_mode")
    if _np_mode0 not in NET_PREM_MODES:
        _np_mode0 = "dollars"

    np_boxes: dict = {}          # symbol -> ui.checkbox (built once, all groups)
    with ui.column().classes("w-full gap-1") as np_box:
        with ui.row().classes("items-center gap-3 flex-wrap w-full"):
            np_group_tabs = ui.tabs(value=_np_group0).classes("compact-subtabs").props(
                "dense no-caps inline-label align=left")
            with np_group_tabs:
                for _g in NET_PREM_GROUPS:
                    _gtab = ui.tab(_g["key"], label=_g["label"])
                    _gh = _page_help.subtab_help("/options/gamma", _g["key"])
                    if _gh:
                        with _gtab:
                            ui.tooltip(_gh).props("delay=350 max-width=340px")
            # The scale control now lives IN the Flow Field panel (its DOLLARS /
            # SKEW % toggle). This select is kept, hidden, as the state HOLDER:
            # every reader already goes through np_mode_sel.value and the
            # persist-and-repaint path hangs off its on_value_change, so the
            # toggle sets this and one code path still owns the change. Two
            # VISIBLE controls for one setting would be the real problem.
            np_mode_sel = ui.select(dict(NET_PREM_MODES), value=_np_mode0,
                                    label="Scale").props(
                "dense options-dense").classes("w-36")
            np_mode_sel.set_visibility(False)
            ui.space()
            np_count_lbl = ui.label("").classes(f"text-xs {MUTED}")
            np_all_btn = ui.button("Select all", color=None).props(
                "no-caps dense flat").classes(BTN)
            np_all_btn.tooltip(
                "Tick every symbol in the group you are on. Other groups' "
                "selections are left alone — use \"Only this group\" to drop "
                "them.")
            np_only_btn = ui.button("Only this group", color=None).props(
                "no-caps dense flat").classes(BTN)
            np_only_btn.tooltip(
                "Unplot everything outside the group tab you are on. The tab "
                "filters the tick-boxes, not the chart — so symbols ticked in "
                "another group keep plotting until you drop them here.")
            np_clear_btn = ui.button("Clear all", color=None).props(
                "no-caps dense flat").classes(BTN)
        # One checkbox per symbol, all built up front and toggled by VISIBILITY
        # per group — so the group tab filters what you SEE without touching what
        # is plotted (tick $SPX on Indices, switch to Sectors, tick XLK: both plot).
        with ui.row().classes("items-center gap-x-3 gap-y-0 flex-wrap w-full"):
            for _g in NET_PREM_GROUPS:
                for _sym in _g["symbols"]:
                    _cb = ui.checkbox(_sym, value=_sym in _np_seed)
                    # One fixed hex per symbol from a 28-entry map = a finite
                    # palette, so an arbitrary-value class is Tailwind-first legal.
                    _cb.props("dense").classes(
                        f"text-xs text-[{net_prem_color(_sym)}]")
                    np_boxes[_sym] = _cb
        # The publisher-health line lives HERE rather than in the shared bottom
        # strip: it is about the SERVICE, not the chart, it is view-specific, and
        # the strip already merges three sources into one tiny overlay.
        np_status_lbl = ui.label("").classes(f"text-xs {MUTED}")

    state["netprem_sel"] = [s for s in net_prem_symbols() if s in _np_seed]

    # The collector status + detail strip is rendered as a TINY overlay pinned to the
    # bottom-right of the heatmap panel (created inside the chart row below), so it no
    # longer takes a full row above the charts. status_lbl / detail_lbl are created
    # there; the repaint helpers here reference them (resolved at call time).

    # Three independent sources feed the detail strip (collector status, the per-view
    # summary, the refresh countdown); unify them behind one state dict + repaint fn.
    strip_state = {"status": None, "summary": "", "countdown": state.get("countdown", 120)}

    def _repaint_strip():
        detail_lbl.text = status_strip_text(strip_state["status"], strip_state["summary"],
                                            strip_state["countdown"])

    def _set_summary(text):
        strip_state["summary"] = text or ""
        _repaint_strip()
    # Persistent panels: the Highcharts elements are created ONCE and updated in
    # place on every repaint (Highcharts diffs the new options) — rebuilding them
    # each time would flash. Message labels are toggled via set_visibility. Column
    # flex weights are set per-render from the intraday snapshot count (panel_flex)
    # so the heatmap grows / bars shrink through the session.
    # gap-0: bars + heatmap sit flush (no inter-panel gap). w-[calc(100%+1rem)] makes
    # the row 1rem wider than the content box so it extends INTO (and fills) the content
    # column's p-4 right padding — the heatmap's right edge then reaches the window edge.
    # (A negative right margin does NOT widen a full-width flex item, so calc-width is
    # used instead; the 1rem lands inside the parent's padding → never a horizontal scroll.)
    chart_row = ui.row().classes(
        "w-[calc(100%+1rem)] no-wrap gap-0 items-start relative gamma-xhair-row")
    with chart_row:
        chart_box = ui.column().classes(f"min-w-0 {_INIT_FLEX}")
        with chart_box:
            # chart_plot switches kind (bar <-> Term heatmap). Highcharts'
            # chart.update() leaks plotLines/colorAxis across a type switch, so the
            # element lives in its own container and is RECREATED on kind-change
            # (see _set_chart); same-kind repaints update in place (flicker-free).
            chart_plot_box = ui.column().classes("w-full q-gutter-none")
            with chart_plot_box:
                state["chart_el"] = ui.highchart(_empty_fig(), extras=["heatmap", "coloraxis"]).classes("w-full")
            # Seeded from the SAME figure the element was created with, so the
            # first real paint can't spuriously recreate it.
            state["chart_kind"] = chart_kind(_empty_fig())
            # The Flow + Net Prem console panels. ONE persistent ui.html whose
            # .content is swapped per repaint — the rings.py / regime_mix.py
            # idiom. It lives inside chart_box because both views already run
            # full width with the heatmap hidden (_apply_flex(term=True)).
            panel_el = ui.html("").classes("w-full")
            panel_el.set_visibility(False)
            chart_msg = ui.label("Fetch a symbol… (no snapshot yet).") \
                .classes("opacity-60 text-sm")
        heatmap_box = ui.column().classes(f"min-w-0 {_INIT_FLEX}")
        with heatmap_box:
            # Created with the heatmap init fig so the press-and-hold-tooltip load
            # hook is installed at creation (load fires once); updated in place after.
            heat_plot = ui.highchart(_heat_init_fig(), extras=["heatmap", "coloraxis"]).classes("w-full")
            # 0-DTE hedge-pressure track, directly UNDER the heatmap and sharing its
            # time categories. Its own element because pressure is in DOLLARS while
            # the heatmap's y-axis is STRIKE (and is pixel-aligned to the bar chart),
            # so it cannot share that axis. Hidden unless the symbol has a 0-DTE book.
            hedge_plot = ui.highchart(hedge_figure([], [])).classes("w-full")
            hedge_plot.set_visibility(False)
            hedge_lbl = ui.label("").classes("opacity-70 text-[10px] text-right w-full")
            hedge_lbl.set_visibility(False)
            heat_msg = ui.label("").classes("opacity-60 text-sm")

    # Tiny status strip BELOW the charts, right-aligned: the collector status WORD
    # (colored) + the neutral detail (last/next scan + refresh countdown + per-view
    # summary). Sits under the charts so it never collides with the time-axis labels.
    with ui.row().classes("w-full justify-end items-baseline gap-2 text-[10px] "
                          "leading-none opacity-90 -mt-1"):
        status_lbl = ui.label("").classes("font-medium")
        detail_lbl = ui.label("").classes("opacity-70")

    # Long-form guide to the three 0-DTE projection overlays (outline bars, the
    # Proj. flip line, the hedge-pressure panel) — collapsed, so it costs nothing
    # until asked for. It lives ON the page rather than only in the nav hover
    # tooltip because that tooltip is pointer-events:none and clips to the space
    # under its nav item, which cuts a guide this long off mid-sentence with no way
    # to scroll. Same text as the hover guide (one constant in page_help).
    with ui.expansion("How to read the 0-DTE close projection") \
            .classes("w-full text-xs opacity-80").props("dense"):
        ui.markdown(_page_help.PROJECTION_HELP_MD).classes("text-xs text-left")

    # History picker (BELOW the charts): browse past stored briefings. Pick a date
    # (+ optional slot) and Open regenerates the report from the stored analysis (via
    # the gamma_history command) and opens it in a new tab. Dates come from
    # cache:options:gamma_briefings.
    with ui.row().classes("items-center gap-2 flex-wrap"):
        ui.label("History:").classes("opacity-60 text-sm")
        hist_date = ui.select([], label="Date").props("dense options-dense").classes("w-40")
        hist_slot = ui.select(
            {"": "All slots", "premarket": "Premarket", "open": "Open",
             "midday": "Midday", "close": "EOD recap"}, value="") \
            .props("dense options-dense").classes("w-32")
        hist_open = ui.button("Open", icon="history").props("flat dense")
        hist_hint = ui.label("").classes("opacity-50 text-xs")

    def _current_symbol():
        return (symbol_in.value or "").strip().upper()

    # Track the current flex class per box so each reset removes the previous
    # arbitrary class (else two flex-[…] classes stack). Seeded with _INIT_FLEX —
    # the same constant the boxes above were created with (can't drift).
    flex_cur = {"chart": _INIT_FLEX, "heat": _INIT_FLEX}

    def _set_flex_class(box, key, cls):
        box.classes(remove=flex_cur[key], add=cls)
        flex_cur[key] = cls

    def _reflow_charts():
        """Resize the Highcharts panels to their (flex-sized) containers.

        The nicegui-highcharts element measures its width ONCE at mount and has no
        ResizeObserver, and ``chart.update()`` does NOT resize — so after a flex/width
        change the SVG stays frozen at its first-mount width (dead space to the right
        of the panel). Defer one tick so the new flex widths are laid out, then reflow
        all three panels to fill their containers. Null-safe (no-op if a chart isn't
        mounted yet); the next repaint reflows again.

        The HEDGE panel needs this even though its width never changes: it is created
        hidden (a symbol with no 0-DTE book never shows it), so it mounts having
        measured a zero-width container and stays collapsed once shown — measured at
        8px inside a 689px column. Callers must therefore run this AFTER the repaint's
        set_visibility, or the reflow just re-measures zero."""
        @guard
        def _do():
            el = state.get("chart_el")
            ids = [i for i in (getattr(el, "id", None), heat_plot.id, hedge_plot.id)
                   if i]
            if ids:
                ui.run_javascript(";".join(f"getElement({i})?.chart?.reflow()" for i in ids))
        ui.timer(0.05, _do, once=True)

    def _apply_flex(n_cols, term=False):
        """Set the bar/heatmap column widths (fixed _STRIKE_HEAT_SPLIT split), then
        reflow so the panels fill their containers. Term → bars full width (no heatmap)."""
        if term:
            _set_flex_class(chart_box, "chart", flex_class(1))
            _set_flex_class(heatmap_box, "heat", flex_class(0, grow2=0, basis="0px"))
            heatmap_box.set_visibility(False)
            _reflow_charts()
            return
        heatmap_box.set_visibility(True)
        bar_w, heat_w = _STRIKE_HEAT_SPLIT
        _set_flex_class(chart_box, "chart", flex_class(bar_w))
        _set_flex_class(heatmap_box, "heat", flex_class(heat_w))
        _reflow_charts()

    # Inline wait for the chart region: a symbol change refetches a ~1.5 MB
    # snapshot, and until it lands the panels still show the PREVIOUS symbol,
    # which is worse than showing nothing — it looks like live data for a
    # symbol you are no longer on.
    chart_busy = _busy.build_busy(chart_row, "Loading…")

    def _set_chart(fig):
        """Paint chart_plot: update in place when the chart KIND is unchanged
        (the common bar->bar repaint, flicker-free), but RECREATE the element when
        the kind changes (bar <-> Term heatmap, Flow <-> Net Prem) so stale
        plotLines/colorAxis/yAxis config from the previous figure doesn't leak
        through Highcharts' merge-based update. See ``chart_kind`` for why the
        identity is NOT just ``chart.type``."""
        kind = chart_kind(fig)
        if state.get("chart_kind") != kind:
            chart_plot_box.clear()
            with chart_plot_box:
                state["chart_el"] = ui.highchart(fig, extras=["heatmap", "coloraxis"]).classes("w-full")
            state["chart_kind"] = kind
        else:
            _set_figure(state["chart_el"], fig)
        return state["chart_el"]

    def _np_current():
        """The plotted symbols, in group order, derived from the checkboxes.

        Total by construction — it filters a KNOWN symbol list rather than
        ordering a caller-supplied one, so nothing here can raise on an
        unrecognised name."""
        return [s for s in net_prem_symbols() if np_boxes[s].value]

    def _paint_np_status():
        """Recompute the publisher-health line from the cached payload.

        Driven by the 1 s ``_tick`` as well as by repaints, because staleness is
        a function of the CLOCK, not of any cache version — and the one failure
        this line exists to report is exactly the one that stops every version
        bump. If it were only recomputed on a repaint, a whole-service outage
        would freeze it at "updated 5:20 PM" forever and the reader would never
        see "check the options service". Pure over state the page already holds:
        no bus read, no I/O."""
        import datetime as _dt
        payload = state.get("netprem")
        payload = payload if isinstance(payload, dict) else None
        # net_prem_status_text renders the payload's own error verbatim.
        np_status_lbl.text = net_prem_status_text(
            payload, _dt.datetime.now(_dt.timezone.utc))

    def _show_panel(html, kind, payload, uid):
        """Swap the console panel's markup in and (re)bind its client-side scrub.

        The fragment is replaced wholesale, so the listeners bound by the script
        go with the old DOM — there is nothing to unbind. The script is deferred
        one tick because ``el.content`` is applied on the client asynchronously:
        run immediately, it would bind to the PREVIOUS fragment's nodes (or to
        none at all on the first paint) and the panel would sit inert.
        """
        panel_el.content = html
        panel_el.set_visibility(True)
        state["chart_el"].set_visibility(False)
        heat_plot.set_visibility(False)
        hedge_plot.set_visibility(False)
        hedge_lbl.set_visibility(False)
        heat_msg.set_visibility(False)
        chart_msg.set_visibility(False)
        _apply_flex(0, term=True)          # full width, no heatmap panel
        # Both scripts are deferred to the SAME tick: el.content is applied on
        # the client asynchronously, so binding immediately would attach to the
        # previous fragment's nodes (or to none at all on the first paint).
        # toggle_js runs even without a scrub payload — the scale toggle exists
        # in the empty state too, and must stay clickable there.
        scripts = [s for s in (_fx.scrub_js(uid, kind, payload),
                               _fx.toggle_js(uid) if kind == "field" else "")
                   if s]
        if scripts:
            @guard
            def _bind():
                for script in scripts:
                    ui.run_javascript(script)
            ui.timer(0.05, _bind, once=True)

    def _hide_panel():
        """Return the row to the Highcharts elements (every non-panel view)."""
        panel_el.set_visibility(False)

    def _render_net_prem():
        """Paint the Net Prem view as the Flow Field console panel."""
        payload = state.get("netprem")
        payload = payload if isinstance(payload, dict) else {}
        series = payload.get("series")
        series = series if isinstance(series, dict) else {}
        sel = state["netprem_sel"]
        mode = np_mode_sel.value
        mode = mode if mode in NET_PREM_MODES else "dollars"

        # Reuse the existing, well-tested readers: _np_selected guards the
        # persisted selection, _np_rows drops unreadable rows per symbol, and
        # net_prem_value applies the mode. The panel only changes how this is
        # DRAWN — the data path underneath is untouched.
        picked = _np_selected(sel)
        rows_by = {}
        for sym in picked:
            # A point with no value in this mode is SKIPPED, and that can never
            # hide an interior gap. The stored premiums are daily-CUMULATIVE
            # (the service's build_series accumulates nothing downstream), so
            # call+put is monotonic non-decreasing: once it exceeds 0 it stays
            # there for the rest of the session. An unreportable skew point is
            # therefore only ever possible in a LEADING run, before anything
            # traded — skipping trims a meaningless prefix and cannot connect a
            # line across a hole. A future "optimization" into a running total
            # would break that invariant, and with it this reasoning.
            pairs = [(ts, value) for ts, row in _np_rows(series, sym)
                     if (value := net_prem_value(row, mode)) is not None]
            if pairs:
                rows_by[sym] = pairs
        html, scrub = _fx.field_panel(rows_by, picked, NET_PREM_COLORS,
                                      mode, "fxfield")
        _show_panel(html, "field", scrub, "fxfield")
        # net_prem_summary_text already folds in the mode-aware "no data yet"
        # names (it shares net_prem_missing's definition), so the header can
        # never disagree with what the chart draws.
        _set_summary(net_prem_summary_text(series, sel, mode))
        _paint_np_status()
        np_count_lbl.text = f"Selected: {len(sel)}"

    def _render_view():
        """Paint the active view from the cached snapshot (no fetch, no teardown).

        The Highcharts elements persist across repaints and are updated in place
        (via _set_figure / _set_chart) so the charts don't flicker."""
        if view_toggle.value == "Net Prem":
            # Handled BEFORE the no-snapshot early return: this view is
            # symbol-INDEPENDENT (it reads its own cache key), so it must paint
            # even when no gamma snapshot has been cached for the current symbol.
            _render_net_prem()
            return
        _hide_panel()
        snap = state["snap"]
        if not snap:
            state["chart_el"].set_visibility(False)
            heat_plot.set_visibility(False)
            hedge_plot.set_visibility(False)
            hedge_lbl.set_visibility(False)
            heat_msg.set_visibility(False)
            chart_msg.text = "Fetch a symbol… (no snapshot yet)."
            chart_msg.set_visibility(True)
            _set_summary("")
            return
        chart_msg.set_visibility(False)

        view = view_toggle.value
        spot = snap.get("spot")
        if view == "Term":
            _set_chart(term_heatmap(snap.get("term") or {}))
            state["chart_el"].set_visibility(True)
            heat_plot.set_visibility(False)
            hedge_plot.set_visibility(False)
            hedge_lbl.set_visibility(False)
            heat_msg.set_visibility(False)
            _apply_flex(0, term=True)
            _set_summary(summary_text({"spot": spot, "strike_count": None}, "Term"))
            return
        if view == "Flow":
            # Premium Divergence console panel — the call/put ribbon, the strike
            # ladder and the readout rail, full width (no heatmap). A raw ui.html
            # fragment rather than a Highcharts figure; see flow_panels.
            # ``session_dte`` is the tenor the SERIES was collected against;
            # ``dte`` is the live chain's and describes right now. They agree
            # during market hours and diverge the moment the page outlives its
            # session — over a weekend the panel shows Friday's 0DTE-dominated
            # series while the live read says 2DTE. Falls back to ``dte`` for a
            # snapshot published before options_svc carried the new field.
            _sdte = snap.get("session_dte")
            html, scrub = _fx.divergence_panel(
                snap.get("flow") or [], snap.get("prem_ladder") or [],
                snap.get("symbol") or _current_symbol(),
                _fx.dte_label(snap.get("dte") if _sdte is None else _sdte),
                "fxdiv")
            _show_panel(html, "div", scrub, "fxdiv")
            _set_summary(flow_summary_text(snap.get("flow")))
            return

        entry = (snap.get("views") or {}).get(view) or {}
        # Every view's result dict keys its per-strike map under "gex" (GammaEngine
        # uses "gex" for charm/dex/vanna too). The figure builders read
        # ``data["gex"]``, whose keys JSON-stringified in Redis — re-float them
        # before the builders sort + numeric-compare strikes.
        raw = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        data = {"spot": raw.get("spot"),
                "strike_count": raw.get("strike_count"),
                "gex": _refloat_keys(raw.get("gex")),
                # Same float-key round-trip as the grid (Redis JSON stringifies them).
                "hedge_drift_by_strike": _refloat_keys(
                    raw.get("hedge_drift_by_strike"))}
        view_spot = data.get("spot") or spot
        if not isinstance(view_spot, (int, float)):
            # No usable underlying price (e.g. market closed / sparse off-hours
            # chain) — the near-spot bar/heatmap window can't be computed. Show a
            # message instead of crashing on the spot*pct band math.
            state["chart_el"].set_visibility(False)
            heat_plot.set_visibility(False)
            hedge_plot.set_visibility(False)
            hedge_lbl.set_visibility(False)
            heat_msg.set_visibility(False)
            sym = snap.get("symbol") or _current_symbol()
            chart_msg.text = (f"No spot price for {sym} yet "
                              "(market closed or sparse data) — try Refresh during market hours.")
            chart_msg.set_visibility(True)
            _set_summary("")
            return
        summary = entry.get("summary") or {}
        flip = entry.get("flip")
        walls = entry.get("walls") or []

        # History rows first (index-6 grid dict needs its keys re-floated too): the
        # intraday spot path (index 1) feeds the shared y-range below.
        rows = []
        for r in (state.get("hist") or {}).get(view) or []:
            r = list(r)
            if len(r) > 6:
                r[6] = _refloat_keys(r[6])
            rows.append(tuple(r))
        spot_path = [r[1] for r in rows if len(r) > 1 and isinstance(r[1], (int, float))]

        # One shared strike range so the bar chart and the intraday heatmap line up
        # vertically (axis alignment). Spans the FIXED ±N_SIDE-strike window around
        # spot (consistent bar/cell count + size through the day), then widened to
        # include the intraday spot path so the heatmap's price line isn't clipped
        # when price drifted out of that window.
        yr = bar_yrange(bars_from_gex(data, view_spot)["strikes"], view_spot)
        yr = union_range(yr, spot_path)
        _set_chart(bar_figure(data, view_spot, view=view, walls=walls, flip=flip, yrange=yr))
        state["chart_el"].set_visibility(True)
        _set_summary(summary_text(
            {**summary, "strike_count": data.get("strike_count")}, _view_label(view)))

        if rows:
            projection = None
            if view == "GEX":
                proj = entry.get("projection") or {}
                if proj.get("times") and proj.get("grid"):
                    projection = {"times": proj["times"],
                                  "grid": _refloat_keys(proj["grid"]),
                                  "cone": proj.get("cone") or {},
                                  "spot": proj.get("spot")}
            _set_figure(heat_plot, heatmap_figure(rows, view, yrange=yr,
                                                  projection=projection,
                                                  walls=walls, spot=view_spot,
                                                  flip=flip,
                                                  levels=entry.get("levels"),
                                                  show_tracks=bool(tracks_sw.value),
                                                  spot_style=spot_style_sel.value,
                                                  spot_interval=spot_int_sel.value,
                                                  projected_flip=snap.get("projected_flip")))
            # The hedge panel is its own element under the heatmap, so their
            # horizontal alignment is entirely this wiring's job: SAME categories
            # (the projection band widens the GEX axis) and the heatmap rows' ts
            # (a minute with no 0-DTE book is absent from hedge_history, and by
            # row position every later bar would sit a column early).
            _hedge = snap.get("hedge_history") or []
            _cats = heatmap_categories(rows, projection)
            _col_ts = [r[0] for r in rows]
            _has_hedge = bool(hedge_points(_hedge, _col_ts))
            if _has_hedge:
                _set_figure(hedge_plot,
                            hedge_figure(_hedge, _cats, col_ts=_col_ts))
                hedge_lbl.set_text(hedge_summary_text(_hedge))
            hedge_plot.set_visibility(_has_hedge)
            hedge_lbl.set_visibility(_has_hedge)
            heat_plot.set_visibility(True)
            heat_msg.set_visibility(False)
        else:
            heat_plot.set_visibility(False)
            hedge_plot.set_visibility(False)
            hedge_lbl.set_visibility(False)
            heat_msg.text = "No intraday snapshots yet (history collector not running)."
            heat_msg.set_visibility(True)
        _apply_flex(len(rows))

        if view == "DEX":
            # Fold the 0-DTE hedge-pressure into the bottom-right status strip (no
            # separate tiles row) so Delta has no extra line.
            _set_summary(strip_state["summary"] + "  ·  "
                         + dex_hedge_suffix(entry.get("hedge")))

    @guard
    def _request_refresh():
        sym = _current_symbol()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        bus_client.request("options", {"type": "gamma_refresh", "args": {"symbol": sym}})
        ui.notify(f"Refreshing {sym} — the panels update when the "
                  f"new read lands.")
        chart_busy.show(f"Loading {sym}…")
        state["countdown"] = 120

    @guard
    def _auto_refresh():
        # Fetch-free on the page side: enqueue a refresh for the current symbol;
        # the service recomputes + republishes and the version-poll repaints.
        # SKIPPED on Net Prem, which reads its own cache key and never touches
        # the gamma snapshot — enqueueing there costs the options service a full
        # option-chain fetch + GammaEngine compute for a result this view
        # discards. (Safe only because _tick now owns the status refresh; this
        # enqueue used to be its accidental driver.)
        if view_toggle.value == "Net Prem":
            state["countdown"] = 120
            return
        sym = _current_symbol()
        if sym:
            bus_client.request("options", {"type": "gamma_refresh", "args": {"symbol": sym}})
        state["countdown"] = 120

    @guard
    def _tick():
        state["countdown"] = state.get("countdown", 120) - 1
        if state["countdown"] < 0:
            state["countdown"] = 120
        strip_state["countdown"] = state["countdown"]
        _repaint_strip()
        if view_toggle.value == "Net Prem":
            # Staleness is a clock function — see _paint_np_status.
            _paint_np_status()

    @guard_async
    async def _maybe_repaint(version):
        # Repaint only when the bus cache version changes (the service bumps it
        # when a requested gamma_refresh finishes). The version compare is done by
        # the caller off the cheap :ver probe; the actual snapshot payload
        # (cache:options:gamma is ~0.4 MB since the history split — still a blocking
        # GET + JSON parse, and the visible view's history follows it) is read
        # OFF the event loop via run.io_bound so it never blocks other clients.
        if version == seen["gamma"] or state.get("fetching"):
            # Unchanged, or a prior big read is still in flight — don't stack them.
            return
        seen["gamma"] = version
        state["fetching"] = True
        try:
            snap = await run.io_bound(bus_client.read, "options:gamma") or None
        finally:
            state["fetching"] = False
        # Only adopt a snapshot for the symbol currently selected — a foreign
        # publish (e.g. the service's one-shot $SPX startup refresh) must NOT
        # revert the displayed symbol out from under the user.
        want = _current_symbol()
        if snap and want and (snap.get("symbol") or "").upper() != want:
            return
        state["snap"] = snap
        # A new snapshot means new history rows; drop the per-view cache and pull
        # back only the view actually on screen.
        state["hist"] = {}
        await _load_history(view_toggle.value)
        _render_view()
        chart_busy.hide()

    async def _load_history(view):
        """Fetch ``view``'s history rows off-loop, once per gamma version.

        Mirrors _maybe_repaint_netprem: separate key, own in-flight guard, big read
        via run.io_bound. Cached in state["hist"] so flipping back to a view the
        user has already seen costs nothing."""
        if not view or view in (state.get("hist") or {}) or state.get("hist_fetching"):
            return
        state["hist_fetching"] = True
        try:
            payload = await run.io_bound(bus_client.read, history_key(view))
        finally:
            state["hist_fetching"] = False
        state.setdefault("hist", {})[view] = history_rows(
            payload, (state.get("snap") or {}).get("symbol"))

    @guard_async
    async def _maybe_repaint_netprem(version):
        # Same shape as _maybe_repaint: version-gated, with its OWN in-flight
        # guard, and the payload (~500 KB) read OFF the event loop. Repaint only
        # when this view is showing — the other views don't read it, and the
        # cached payload is already up to date for when the user switches over.
        if version == seen["netprem"] or state.get("np_fetching"):
            return
        seen["netprem"] = version
        state["np_fetching"] = True
        try:
            state["netprem"] = await run.io_bound(bus_client.read, "options:net_premium")
        finally:
            state["np_fetching"] = False
        if view_toggle.value == "Net Prem":
            _render_view()

    def _paint_status(st):
        """Paint the collector status bar from a gex_status view dict (or None)."""
        st = st or {}
        label = st.get("status_label") or "Collector status unknown"
        color = st.get("status_color") or "#666666"
        status_lbl.text = label
        # Reactive: the status bar repaints on every version-poll, so remove the full
        # status-class set before adding the new one (prevents color accumulation).
        status_lbl.classes(remove=_ALL_STATUS, add=status_color_class(color))
        strip_state["status"] = st
        _repaint_strip()

    @guard
    def _maybe_repaint_status(version):
        # Fetch-free: re-read + repaint the status bar only when the bus cache
        # version changes (the service republishes it every scheduler tick).
        if version == seen["status"]:
            return
        seen["status"] = version
        _paint_status(bus_client.read("options:gex_status"))

    @guard
    def _request_explain():
        sym = _current_symbol()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        bus_client.request("options", {"type": "gamma_explain", "args": {"symbol": sym}})
        ui.notify("Building Explain infographic… opening in a new tab.")

    @guard
    def _watch_explain(version):
        # The initial version was captured at render time, so any change here is a
        # fresh, user-requested infographic → open it in a new browser tab. The
        # /options/explain route serves the cached standalone HTML (raw, so its own
        # CSS/fonts apply). ?v= busts the browser cache so each click shows the latest.
        if version is None or version == seen["explain"]:
            return
        seen["explain"] = version
        ui.navigate.to(f"/options/explain?v={version}", new_tab=True)

    @guard
    def _request_analyze():
        bus_client.request("options", {"type": "gamma_analyze"})
        ui.notify("Analyzing $SPX / SPY / QQQ… opens in a new tab (a few seconds).")

    @guard
    def _watch_analyze(version):
        # Mirrors _watch_explain: the service ran gamma_analyze (called Claude +
        # rendered the HTML) and bumped the version → open the result in a new browser
        # tab. /options/analyze serves the cached standalone HTML raw (so its own CSS
        # applies). ?v= busts the browser cache so each click shows the latest.
        if version is None or version == seen["analyze"]:
            return
        seen["analyze"] = version
        ui.navigate.to(f"/options/analyze?v={version}", new_tab=True)

    def _refresh_history_dates(payload):
        dates = history_dates(payload)
        hist_date.options = dates
        if dates and hist_date.value not in dates:
            hist_date.value = dates[0]        # default to the newest
        hist_date.update()
        hist_hint.text = f"{len(dates)} day(s) stored" if dates else "no history yet"

    @guard
    def _open_history():
        d = hist_date.value
        if not d:
            ui.notify("No stored briefings to view yet.", type="warning")
            return
        bus_client.request("options", {"type": "gamma_history",
                                       "args": {"date": d, "slot": hist_slot.value or None}})
        ui.notify("Building history report… opens in a new tab.")

    @guard
    def _watch_history(version):
        # options_svc regenerated the history report → open it in a new tab (mirrors
        # _watch_analyze). /options/gamma-history serves the cached HTML raw.
        if version is None or version == seen.get("history"):
            return
        seen["history"] = version
        ui.navigate.to(f"/options/gamma-history?v={version}", new_tab=True)

    hist_open.on_click(_open_history)

    _SCHED_VIEWS = {s: f"options:gamma_analyze_{s}" for s in sched_btns}
    _sched_state = {s: {"ver": None, "date": None, "applied": None} for s in sched_btns}

    def _sync_sched_btns(versions):
        # Highlight a briefing button ONLY when its slot's data is from TODAY (CT);
        # prior-day data (e.g. over the weekend) stays dim — and the un-highlighted
        # buttons are dimmed further. Clickable whenever data exists; disabled when a
        # slot has never run. The payload (for generated_at) is read only when a slot's
        # version changes; the today-vs-prior compare runs every tick, so a highlight
        # also drops at midnight on a page left open across the rollover.
        import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        today = _dt.datetime.now(_ZI("America/Chicago")).date().isoformat()
        for s, b in sched_btns.items():
            ver = versions.get(_SCHED_VIEWS[s])
            st = _sched_state[s]
            if ver != st["ver"]:
                st["ver"] = ver
                ga = ((bus_client.read(_SCHED_VIEWS[s]) or {}).get("generated_at")
                      if ver else "") or ""
                st["date"] = ga[:10] if len(ga) >= 10 else None
            is_today = bool(ver) and st["date"] == today
            key = (bool(ver), is_today)
            if key == st["applied"]:
                continue  # no state change → don't re-push classes every tick
            st["applied"] = key
            _t = _sched_titles[s]
            if is_today:
                b.classes(remove=_SCHED_DIM, add=_SCHED_HL)
                b.tooltip(f"Open today's {_t} briefing")
            else:
                b.classes(remove=_SCHED_HL, add=_SCHED_DIM)
                b.tooltip(f"{_t} briefing — "
                          + ("prior day (not today's)" if ver else "not generated yet today"))
            b.set_enabled(bool(ver))

    @guard_async
    async def _poll():
        # One coalesced 2s tick: read all view versions in a single pipelined
        # round-trip (cheap :ver counters, no payload deserialize — these stay ON
        # the event loop) and dispatch only the views that changed. Only the big
        # gamma snapshot fetch (~14 MB) is moved off-loop, inside _maybe_repaint;
        # the small status/explain/analyze/sched payloads stay inline.
        v = bus_client.read_versions([
            "options:gamma", "options:gex_status",
            "options:gamma_explain", "options:gamma_analyze",
            "options:gamma_briefings", "options:gamma_history",
            "options:net_premium",
            *_SCHED_VIEWS.values()])
        await _maybe_repaint(v["options:gamma"])
        await _maybe_repaint_netprem(v["options:net_premium"])
        _maybe_repaint_status(v["options:gex_status"])
        _watch_explain(v["options:gamma_explain"])
        _watch_analyze(v["options:gamma_analyze"])
        _sync_sched_btns(v)
        if v["options:gamma_briefings"] != seen.get("briefings"):
            seen["briefings"] = v["options:gamma_briefings"]
            _refresh_history_dates(bus_client.read("options:gamma_briefings"))
        _watch_history(v["options:gamma_history"])

    def _set_symbol(sym):
        """Point the dropdown at ``sym`` (adding it to the options if the universe
        doesn't list it). Caller wires on_value_change AFTER the initial set so
        this programmatic sync doesn't enqueue a spurious refresh."""
        if not sym:
            return
        if sym not in symbol_in.options:
            symbol_in.options = list(symbol_in.options) + [sym]
        symbol_in.value = sym
        symbol_in.update()

    @guard
    def _on_symbol_change():
        # Selecting a symbol switches to it immediately (no need to click Refresh
        # now) and keeps the cache in lockstep with the dropdown.
        _request_refresh()

    fetch_btn.on_click(_request_refresh)
    explain_btn.on_click(_request_explain)
    analyze_btn.on_click(_request_analyze)

    @guard_async
    async def _on_view_change(e):
        # _sync_np_controls FIRST so the Net Prem block is shown/hidden before the
        # repaint (the checkbox visibility feeds nothing but the eye, but showing a
        # stale block for a frame reads as a glitch).
        _sync_np_controls()
        # ...and the symbol-scoped cluster hides/shows with the same switch.
        _sync_spot_controls()
        # This view's history is its own key — fetch it the first time the user
        # lands on the view (no-op once cached for this gamma version).
        await _load_history(view_toggle.value)
        _render_view()

    view_toggle.on_value_change(_on_view_change)

    @guard
    def _on_tracks_toggle(e):
        # Persist the choice, then repaint from the cached snapshot — the tracks
        # ride the snapshot the page already holds, so no refetch is needed.
        app_settings.set("gamma_level_tracks", bool(e.value))
        _render_view()

    tracks_sw.on_value_change(_on_tracks_toggle)

    @guard
    def _on_spot_style(e):
        app_settings.set("gamma_spot_style", e.value)
        _sync_spot_controls()
        _render_view()

    @guard
    def _on_spot_interval(e):
        app_settings.set("gamma_spot_interval", e.value)
        _render_view()

    def _sync_spot_controls():
        # Symbol / Refresh now / Level movement / Spot / Bar all drive the
        # SYMBOL-SCOPED views (the by-strike bars, the heatmap, Flow, Term). Net
        # Prem is symbol-INDEPENDENT — it plots a fixed 28-symbol universe from
        # its own cache key and has no spot overlay — so every one of them is a
        # dead knob there. Hide the cluster rather than leave controls that
        # silently do nothing, the same reasoning that hides Bar for a line spot.
        # Explain / Analyze / Briefings go too: all three report on the SYMBOL in
        # the (now hidden) dropdown, so on Net Prem they would act on a symbol
        # the reader can no longer see or change — worse than a dead knob.
        symbol_scoped = view_toggle.value != "Net Prem"
        for el in (symbol_in, fetch_btn, tracks_sw, spot_style_sel,
                   explain_btn, analyze_btn, briefings_btn):
            el.set_visibility(symbol_scoped)
        # Bar size is meaningless for a line — hide it rather than leave a control
        # that silently does nothing.
        spot_int_sel.set_visibility(
            symbol_scoped and spot_style_sel.value != "line")

    spot_style_sel.on_value_change(_on_spot_style)
    spot_int_sel.on_value_change(_on_spot_interval)
    _sync_spot_controls()

    def _sync_np_controls():
        """Show the Net Prem block only on that view, and only the active group's
        checkboxes within it (the selection itself is untouched — see np_boxes)."""
        on = view_toggle.value == "Net Prem"
        np_box.set_visibility(on)
        active = np_group_tabs.value
        for g in NET_PREM_GROUPS:
            visible = on and g["key"] == active
            for sym in g["symbols"]:
                np_boxes[sym].set_visibility(visible)

    @guard
    def _on_np_group(e):
        # Filters which checkboxes are SHOWN; the plotted set is unchanged, so
        # there is nothing to repaint.
        app_settings.set("gamma_netprem_group", e.value)
        _sync_np_controls()

    def _np_commit():
        """Adopt the checkbox state as the plotted selection, persist, repaint."""
        state["netprem_sel"] = _np_current()
        app_settings.set("gamma_netprem_symbols", state["netprem_sel"])
        _render_view()

    @guard
    def _on_np_symbol():
        if state.get("np_bulk"):
            return          # a bulk set (Clear all) commits once at the end
        _np_commit()

    @guard
    def _on_np_mode(e):
        app_settings.set("gamma_netprem_mode", e.value)
        _render_view()

    @guard
    def _on_panel_mode(e):
        """The Flow Field panel's scale toggle, arriving via ``emitEvent``.

        Writes through the hidden select so the persist + repaint path stays
        single-source (``_on_np_mode`` fires on the value change).

        ``e.args`` is BROWSER input — it is persisted to settings.json and read
        back on the next page build, so it is normalized against the known keys
        before it reaches ``app_settings``. It also arrives as a bare string or
        as a one-element list depending on how emitEvent was called, so both
        shapes are unwrapped rather than assumed.
        """
        raw = e.args
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        mode = _fx.normalize_mode(raw if isinstance(raw, str) else None)
        if mode != np_mode_sel.value:
            np_mode_sel.value = mode          # -> _on_np_mode -> persist+repaint

    ui.on(_fx.MODE_EVENT, _on_panel_mode)

    def _np_bulk_set(keep):
        """Set every checkbox to ``keep(symbol)``, then commit ONCE.

        Latched so 28 programmatic value sets don't fire 28 repaints. ``keep`` is
        called for a symbol before that symbol is written, so it may read the
        current value of the box it is deciding about."""
        state["np_bulk"] = True
        try:
            for sym, cb in np_boxes.items():
                cb.value = bool(keep(sym))
        finally:
            state["np_bulk"] = False
        _np_commit()

    @guard
    def _np_clear():
        _np_bulk_set(lambda sym: False)

    @guard
    def _np_select_all():
        keep = set(net_prem_with_group(_np_current(), np_group_tabs.value))
        _np_bulk_set(lambda sym: sym in keep)

    @guard
    def _np_only_group():
        """Drop everything outside the active group; leave its own ticks alone.

        The group tab filters which checkboxes are VISIBLE, not what is plotted —
        deliberately, so a cross-group selection ($SPX beside XLK) is possible at
        all. The cost of that model is that "just show me this group" would
        otherwise be a two-step (Clear all, then re-tick). This is that step in
        one click, and it is the only place the tab affects the chart."""
        keep = set(net_prem_only_group(_np_current(), np_group_tabs.value))
        _np_bulk_set(lambda sym: sym in keep)

    np_group_tabs.on_value_change(_on_np_group)
    np_mode_sel.on_value_change(_on_np_mode)
    np_all_btn.on_click(_np_select_all)
    np_only_btn.on_click(_np_only_group)
    np_clear_btn.on_click(_np_clear)
    for _cb in np_boxes.values():
        _cb.on_value_change(lambda e: _on_np_symbol())
    _sync_np_controls()

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    # The cheap :ver probes + the small gex_status/sched reads stay inline; the big
    # gamma snapshot (~14 MB) is read OFF the event loop in _initial_load so the
    # first page build doesn't block the loop for every connected client.
    seen["gamma"] = bus_client.read_version("options:gamma")
    seen["explain"] = bus_client.read_version("options:gamma_explain")
    seen["analyze"] = bus_client.read_version("options:gamma_analyze")
    seen["status"] = bus_client.read_version("options:gex_status")
    seen["briefings"] = bus_client.read_version("options:gamma_briefings")
    seen["history"] = bus_client.read_version("options:gamma_history")
    seen["netprem"] = bus_client.read_version("options:net_premium")
    _sync_sched_btns(bus_client.read_versions(list(_SCHED_VIEWS.values())))
    _paint_status(bus_client.read("options:gex_status"))
    _refresh_history_dates(bus_client.read("options:gamma_briefings"))

    @guard_async
    async def _initial_load():
        # Big-payload initial read, off-loop. Guarded against the 2 s _poll racing
        # it (both go through state["fetching"]); the poll skips while this runs and
        # this skips if the poll already fetched. Symbol-sync + first paint happen
        # here, and on_value_change is wired AFTER the sync so the programmatic
        # symbol set doesn't enqueue a spurious refresh.
        if not state.get("fetching"):
            state["fetching"] = True
            try:
                state["snap"] = await run.io_bound(bus_client.read, "options:gamma") or None
            finally:
                state["fetching"] = False
        # The Net Prem payload rides the same off-loop initial read (its own key,
        # its own guard) so switching to that view paints immediately.
        if not state.get("np_fetching"):
            state["np_fetching"] = True
            try:
                state["netprem"] = await run.io_bound(
                    bus_client.read, "options:net_premium")
            finally:
                state["np_fetching"] = False
        # Sync the dropdown to the symbol actually in the cache so a page (re)build
        # doesn't show $SPX while another symbol's data is displayed (which a later
        # refresh would then revert to $SPX). Done BEFORE wiring on_value_change.
        # A symbol handed over from the Flow Alerts tape WINS over the cached one —
        # it is an explicit request — and the refresh below moves the cache to it.
        await _load_history(view_toggle.value)
        from .handoff import take_pending_gamma
        handoff_sym = take_pending_gamma()
        _set_symbol(handoff_sym or (state["snap"] or {}).get("symbol"))
        symbol_in.on_value_change(lambda e: _on_symbol_change())
        _render_view()
        if handoff_sym:
            _request_refresh()

    _render_view()                       # instant empty/placeholder paint
    ui.timer(0.05, _initial_load, once=True)  # big snapshot read off-loop

    ui.timer(1.0, _tick)                 # countdown display (no fetch)
    ui.timer(2.0, _poll)                 # one coalesced version-poll for all 4 views
    ui.timer(120.0, _auto_refresh)       # enqueue a refresh every 120s

    @guard
    def _install_crosshair():
        # Wire the shared bar↔heatmap crosshair once (after the charts mount). The
        # installer latches on the row so repeated calls are no-ops.
        ui.run_javascript(_CROSSHAIR_JS)
    ui.timer(0.4, _install_crosshair, once=True)
