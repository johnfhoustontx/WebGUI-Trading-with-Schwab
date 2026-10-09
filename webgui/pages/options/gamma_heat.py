"""The Dealer Positioning heatmap's transforms: which number a cell holds, how it
is scaled, and what the vertical axis measures.

PURE. No NiceGUI, no bus, and nothing imported from ``gamma`` (``gamma`` imports
this). Every function builds new lists: the history rows arrive through
``bus_client.read_shared`` and are shared by every open tab, so a transform that
wrote to its input would corrupt every other tab's chart.

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
import html
import re

from pages import fmt as _fmt

# value key -> the control's label. The order is the picker's order.
VALUES = {"net": "Net", "call": "Calls", "put": "Puts", "size": "Size"}

# The unit a view's cells are in, where it is a plain dollar figure
# (options-scanner/gamma_tool.py: GEX is gamma x open interest x 100 x spot^2 x
# 0.01, DEX is delta x open interest x 100 x spot). Charm and vanna are not a
# figure a reader can price, so their legend prints the bare number, as the
# bars' axis always has.
UNITS = {"GEX": "$ gamma per 1% move", "DEX": "$ delta"}


def cell_value(cell, mode="net"):
    """The number one stored cell draws for ``mode``, or None when it has none.

    ``size`` is ``abs(call) + abs(put)`` carrying the sign of ``net``: how much is
    at the strike, coloured by which way it leans. A strike whose calls and puts
    cancel is large here and zero in ``net``, which is the point of the mode.
    A cell with exactly zero net sits on the call side by convention.
    """
    if mode not in VALUES:
        raise ValueError(f"unknown heatmap value: {mode!r}")
    if not isinstance(cell, dict):
        return _fmt.num(cell) if mode == "net" else None
    if mode == "net":
        return _fmt.num(cell.get("net"))
    call, put = _fmt.num(cell.get("call")), _fmt.num(cell.get("put"))
    if mode == "call":
        return call
    if mode == "put":
        return put
    if call is None or put is None:
        return None
    net = _fmt.num(cell.get("net"))
    size = abs(call) + abs(put)
    return -size if (call + put if net is None else net) < 0 else size


def has_sides(grids):
    """Whether any cell in these grids carries BOTH a call and a put.

    Rows stored before the cells were split are bare numbers; with none that
    carry sides, Calls, Puts and Size have nothing to draw and are switched off
    rather than drawn as zero."""
    for grid in grids or ():
        if not isinstance(grid, dict):
            continue
        for cell in grid.values():
            if (isinstance(cell, dict) and _fmt.num(cell.get("call")) is not None
                    and _fmt.num(cell.get("put")) is not None):
                return True
    return False


def balanced_marks(grid, strikes, *, max_polarity, min_size_quantile, max_marks):
    """The strikes in ``strikes`` that hold a lot and lean little, largest first.

    A strike qualifies when ``abs(net) / size`` is at or under ``max_polarity``
    and its size is at or above the ``min_size_quantile`` rank of the strikes
    given. These are the strikes ``net`` draws as empty, and ``size`` draws in a
    hue that a small change of net can flip, so they are named on the axis.
    The three thresholds are ``config/gamma_heat.toml [balanced]``."""
    sized = []
    for strike in strikes or ():
        cell = (grid or {}).get(strike)
        size, net = cell_value(cell, "size"), cell_value(cell, "net")
        if not size or net is None:
            continue
        sized.append((abs(size), abs(net) / abs(size), strike))
    if not sized:
        return []
    ranked = sorted(s for s, _, _ in sized)
    floor = ranked[min(len(ranked) - 1, int(min_size_quantile * (len(ranked) - 1)))]
    hits = sorted(((s, k) for s, lean, k in sized
                   if s >= floor and lean <= max_polarity), reverse=True)
    return [k for _, k in hits[:max(0, int(max_marks))]]


# ── the colour scale ─────────────────────────────────────────────────────────
# scale key -> the control's label. The order is the picker's order.
#   locked    one colour is one amount for the whole session: the maximum is the
#             service's ``scale_lock`` for the view and value
#             (services/options_svc/gamma_window.py), set from the first hour
#   adaptive  the maximum is refitted to what is on screen on every paint
#   share     each cell as a percentage of its own column: shape, not level
SCALES = {"locked": "Locked", "adaptive": "Adaptive", "share": "Share of column"}


def robust_max(z, q=0.95):
    """Symmetric colour clamp for a z-grid: the ``q`` quantile of the absolute
    cell, so a few extreme strikes do not wash the mid-range to transparent.
    None when there is no non-zero cell."""
    vals = sorted(abs(v) for row in (z or []) for v in row if v)
    if not vals:
        return None
    idx = min(len(vals) - 1, int(q * (len(vals) - 1)))
    return vals[idx] or vals[-1]


def share_of_column(z):
    """Each cell as a percentage of its column's total absolute size: the shape
    of the positioning at each time, with the level taken out. A column that
    holds nothing is a gap. Builds a new grid."""
    if not z:
        return []
    totals = [sum(abs(row[c]) for row in z if row[c]) for c in range(len(z[0]))]
    return [[(100.0 * v / totals[c]) if v is not None and totals[c] else None
             for c, v in enumerate(row)] for row in z]


def _held(lock, mode):
    """The lock's figure for ``mode``, or None when it has none."""
    return _fmt.num((lock or {}).get(mode)) or None


def scale_max(z, scale, lock, mode):
    """The colour axis's maximum. ``locked`` uses the service's lock for this
    value, and falls back to the adaptive figure until one exists (the session's
    first hour, or a snapshot that predates the field)."""
    held = _held(lock, mode) if scale == "locked" else None
    return held if held else robust_max(z)


def bar_max(lock, mode):
    """The by-strike bars' locked extent for ``mode``, or None. Size draws a call
    bar and a put bar per strike, so its extent is the larger side's lock."""
    if mode == "size":
        sides = [v for v in (_held(lock, "call"), _held(lock, "put")) if v]
        return max(sides) if sides else None
    return _held(lock, mode)


def scale_caption(scale, lock, mode, lock_time):
    """What the legend says the scale is tied to. ``lock_time`` is the clock time
    the lock is (or will be) set, ``""`` when it cannot be told."""
    if scale == "share":
        return "share of each column"
    if scale == "locked":
        if _held(lock, mode):
            return f"held since {lock_time}" if lock_time else "held for the session"
        if lock_time:
            return f"settling until {lock_time}"
    return "adapts to what is visible"


# ── the legend strip ─────────────────────────────────────────────────────────
# One line of SVG beside the controls: [-max] [ramp] [+max]  unit · caption.
# It is NOT a Highcharts legend and not a column beside the chart: the heatmap
# runs flush to the window's right edge and must keep the hedge panel's width.
_RGBA = re.compile(r"rgba?\(([^)]+)\)")
_LEGEND_W, _LEGEND_H = 520, 16
_RAMP_X, _RAMP_W, _LABEL_W = 70, 120, 64
_BASELINE_DY = "0.35em"        # dominant-baseline is stripped by the sanitizer
_LEGEND_INK, _LEGEND_MUTED, _LEGEND_GROUND = "#a8adb8", "#6e7482", "#1c2238"


def _stop(colour):
    """``rgba(r,g,b,a)`` -> (``rgb(r,g,b)``, opacity): SVG takes them apart."""
    parts = [p.strip() for p in _RGBA.match(colour).group(1).split(",")]
    r, g, b = (int(float(p)) for p in parts[:3])
    return f"rgb({r},{g},{b})", (float(parts[3]) if len(parts) > 3 else 1.0)


def legend_svg(zmax, caption, stops, *, unit, uid="gheat"):
    """The heatmap's colour scale as one SVG strip: the two ends of the scale in
    numbers, the ramp between them, the unit and what the scale is tied to.

    ``stops`` is the colour axis's own list, so the strip cannot drift from the
    chart. Put side on the left, call side on the right. Empty when there is no
    scale to describe. A ``ui.html`` fragment: every tag and attribute here is
    checked against the shipped sanitizer's allow-list by a test."""
    top = _fmt.num(zmax)
    if not top:
        return ""
    if unit == "%":                  # a share scale: the ends ARE the unit
        lo, hi, unit = _fmt.pct(-top), _fmt.pct(top, signed=True), ""
    elif unit.startswith("$"):
        lo, hi = _fmt.money_short(-top), _fmt.money_short(top, signed=True)
    else:
        text, suffix = _fmt.scaled(top)
        lo, hi = f"-{text}{suffix}", f"+{text}{suffix}"
    grad = "".join(
        f'<stop offset="{pos:.2f}" stop-color="{rgb}" stop-opacity="{alpha:.2f}"></stop>'
        for pos, (rgb, alpha) in ((p, _stop(c)) for p, c in stops))
    mid, bar_h = _LEGEND_H / 2, _LEGEND_H - 6
    tail = html.escape(" · ".join(t for t in (unit, caption) if t))
    hi_x = _RAMP_X + _RAMP_W + 6
    return (
        f'<svg width="{_LEGEND_W}" height="{_LEGEND_H}" '
        f'viewBox="0 0 {_LEGEND_W} {_LEGEND_H}">'
        f'<defs><linearGradient id="{uid}-ramp" x1="0" y1="0" x2="1" y2="0">'
        f'{grad}</linearGradient></defs>'
        f'<text x="{_RAMP_X - 6}" y="{mid}" dy="{_BASELINE_DY}" text-anchor="end" '
        f'font-size="10" fill="{_LEGEND_INK}">{lo}</text>'
        f'<rect x="{_RAMP_X}" y="3" width="{_RAMP_W}" height="{bar_h}" '
        f'fill="{_LEGEND_GROUND}"></rect>'
        f'<rect x="{_RAMP_X}" y="3" width="{_RAMP_W}" height="{bar_h}" '
        f'fill="url(#{uid}-ramp)"></rect>'
        f'<text x="{hi_x}" y="{mid}" dy="{_BASELINE_DY}" '
        f'font-size="10" fill="{_LEGEND_INK}">{hi}</text>'
        f'<text x="{hi_x + _LABEL_W}" y="{mid}" dy="{_BASELINE_DY}" '
        f'font-size="10" fill="{_LEGEND_MUTED}">{tail}</text>'
        f'</svg>')
