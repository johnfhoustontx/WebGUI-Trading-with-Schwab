"""The Dealer Positioning heatmap's transforms: which number a cell holds, how it
is scaled, and what the vertical axis measures.

PURE. No NiceGUI, no bus, and nothing imported from ``gamma`` (``gamma`` imports
this). Every function builds new lists: the history rows arrive through
``bus_client.read_shared`` and are shared by every open tab, so a transform that
wrote to its input would corrupt every other tab's chart.

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
import html
import math
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


# ── level or change ──────────────────────────────────────────────────────────
# show key -> the control's label ({n} is [show] change_window_min).
#   level   the value itself
#   open    the value less the same strike's value in the session's first column
#   window  the value less the same strike's value {n} minutes earlier
# ⚠ What a change MEANS depends on the value. The four Greek values are weighted
# by open interest, which updates once a day, so their change is the same
# positions repricing as price, time and volatility move. It is not new trades.
SHOWS = {"level": "Level", "open": "Change since open", "window": "Change over {n} min"}

# The Show picker's tooltip. A named constant because it carries the one thing a
# reader must not get wrong about this view, and a test holds it to that.
SHOW_HELP = ("The level, or how each strike has changed. Open interest updates "
             "once a day, so a change here is the same positions repricing as "
             "price, time and volatility move. It is not new trades.")


def show_labels(window_min):
    """The Show picker's options, with the window's minutes filled in."""
    return {key: label.format(n=window_min) for key, label in SHOWS.items()}


def show_suffix(show, window_min):
    """What a chart title adds for ``show``: nothing for the level."""
    return "" if show == "level" else show_labels(window_min)[show].lower()


def delta(z, ts, window_min=None):
    """Each cell less the same strike's cell at a basis column. Builds new lists.

    ``window_min`` None: the basis is the session's first column. Otherwise it is
    the latest column at least that many minutes older, and a column with none
    that old behind it is a gap. A strike with no reading at the basis is a gap
    too, never a zero: zero would claim the exposure had not moved.

    ``ts`` are the columns' epoch seconds. A window needs them; rows labelled
    with clock text have no age and come out as gaps."""
    n = len(ts)
    if window_min is None:
        basis = [0] * n
    else:
        basis, j = [], 0
        for i in range(n):
            now = _fmt.num(ts[i])
            if now is None:
                basis.append(None)
                continue
            want = now - window_min * 60
            while j + 1 < i:                # advance to the latest column by then
                nxt = _fmt.num(ts[j + 1])
                if nxt is None or nxt > want:
                    break
                j += 1
            then = _fmt.num(ts[j])
            basis.append(j if j < i and then is not None and then <= want else None)
    return [[(row[i] - row[b]) if b is not None and row[i] is not None
             and row[b] is not None else None
             for i, b in enumerate(basis)] for row in z]


def basis_grid(rows, show, window_min):
    """The grid the by-strike bars subtract in a change view, or None.

    The bars draw NOW, so their basis is the row the heatmap's LAST column is
    measured from: the session's first row, or the latest row at least
    ``window_min`` minutes older than the last. None for the level, and when no
    row is old enough yet (no basis means no bars, never bars against nothing)."""
    rows = [r for r in rows or () if len(r) > 6 and isinstance(r[6], dict)]
    if show == "level" or not rows:
        return None
    if show == "open":
        return rows[0][6]
    last = _fmt.num(rows[-1][0])
    if last is None:
        return None
    want, best = last - window_min * 60, None
    for row in rows:
        at = _fmt.num(row[0])
        if at is not None and at <= want:
            best = row[6]
    return best


# ── the projected afternoon ──────────────────────────────────────────────────
# options_svc prices each view's open interest at marks a few minutes apart, from
# now to a minute before the close, with spot, open interest and volatility held
# (services/options_svc/forward_grid.py). The page fills one row per MINUTE so
# the heat map is one column a minute across the whole session, and the rows are
# shaped like the history's so every transform above runs over them unchanged.
# Design: docs/plans/2026-10-23-heatmap-full-session-forward-design.md


def _between(values, j, w):
    """The value ``w`` of the way from ``values[j]`` to ``values[j + 1]``, or
    None when either is not a number."""
    try:
        a = _fmt.num(values[j])
        b = _fmt.num(values[min(j + 1, len(values) - 1)])
    except (TypeError, IndexError, KeyError):
        return None
    if a is None or b is None:
        return None
    return a + (b - a) * w


def forward_series(forward, after_ts):
    """``{"rows", "up", "down"}`` for a view's band: one history-shaped row a
    minute, ``(ts, spot, None, None, None, None, {strike: {call, put, net}})``,
    from the minute after ``after_ts`` (the last collected row's epoch seconds)
    to the band's last mark, and the expected-move cone at each of those
    minutes (None where it has none).

    Each side is interpolated in time between the two marks around the minute;
    a minute before the first mark takes the first. ``net`` is calls plus puts.
    A strike whose side is not a number at a mark is a gap there. ``after_ts``
    that is not a number starts the rows at the first whole minute of the band.
    Bad input is an empty band. Builds new lists."""
    empty = {"rows": [], "up": [], "down": []}
    if not isinstance(forward, dict):
        return empty
    ts = [_fmt.num(t) for t in (forward.get("ts") or [])]
    calls, puts = forward.get("call"), forward.get("put")
    if (not ts or None in ts or not isinstance(calls, dict)
            or not isinstance(puts, dict)):
        return empty
    strikes = []
    for key in set(calls) | set(puts):
        k = _fmt.num(key)
        if k is not None:
            strikes.append((k, calls.get(key) or [], puts.get(key) or []))
    after = _fmt.num(after_ts)
    start = (math.floor(after / 60) + 1) * 60 if after is not None \
        else math.ceil(ts[0] / 60) * 60
    spot = _fmt.num(forward.get("spot"))
    cone = forward.get("cone") if isinstance(forward.get("cone"), dict) else {}
    out = {"rows": [], "up": [], "down": []}
    j = 0
    for minute in range(int(start), int(ts[-1]) + 1, 60):
        while j + 1 < len(ts) - 1 and ts[j + 1] <= minute:
            j += 1
        span = ts[min(j + 1, len(ts) - 1)] - ts[j]
        w = min(1.0, max(0.0, (minute - ts[j]) / span)) if span > 0 else 0.0
        grid = {}
        for k, c_row, p_row in strikes:
            c, p = _between(c_row, j, w), _between(p_row, j, w)
            if c is not None and p is not None:
                grid[k] = {"call": c, "put": p, "net": c + p}
        out["rows"].append((minute, spot, None, None, None, None, grid))
        for side in ("up", "down"):
            out[side].append(_between(cone.get(side) or [], j, w))
    return out


# ── the vertical frame ───────────────────────────────────────────────────────
# frame key -> the control's label.
#   strike  the vertical axis is strike: price moves, the levels stand still
#   spot    the vertical axis is distance from spot: price is a flat line through
#           the middle and the flip and walls move. A wall price is approaching
#           slides toward the centre, and the slope is the closing speed.
FRAMES = {"strike": "Strike", "spot": "From spot"}

# Two real strikes further apart than this many ladder steps are a hole in the
# data, not neighbours to interpolate between.
_MAX_BRIDGE_STEPS = 2.5


def to_spot_frame(strikes, z, spots, *, step, half):
    """Resample ``z[strike][time]`` onto offsets from each column's OWN spot.

    Returns ``(offsets, z2)``: ``offsets`` is the uniform ladder
    ``-half*step … +half*step`` and ``z2[offset][time]`` the value there, the
    linear interpolation between the two strikes around ``spot + offset`` that
    have a reading in that column. It is a gap when that point lies outside the
    column's strikes, or when those two strikes are a hole apart
    (``_MAX_BRIDGE_STEPS``): nothing is extrapolated and no hole is bridged.
    A column with no spot is all gaps. Builds new lists.

    The history the service publishes keeps ``[window] spot_side`` strikes each
    side of the session's low and high for exactly this, so ``half`` should be
    that number: a taller frame shows gaps on a trending day."""
    offsets = [i * step for i in range(-half, half + 1)]
    out = [[None] * len(spots) for _ in offsets]
    for c, raw in enumerate(spots):
        spot = _fmt.num(raw)
        if spot is None:
            continue
        pts = [(k, z[r][c]) for r, k in enumerate(strikes) if z[r][c] is not None]
        if not pts:
            continue
        j = 0
        for oi, d in enumerate(offsets):
            target = spot + d
            if target < pts[0][0] or target > pts[-1][0]:
                continue
            while j + 2 < len(pts) and pts[j + 1][0] < target:
                j += 1
            (k0, v0), (k1, v1) = pts[j], pts[min(j + 1, len(pts) - 1)]
            if k1 == k0:
                out[oi][c] = v0 if target == k0 else None
            elif k1 - k0 <= _MAX_BRIDGE_STEPS * step:
                out[oi][c] = v0 + (v1 - v0) * (target - k0) / (k1 - k0)
    return offsets, out


# ── where the picture lands ──────────────────────────────────────────────────
# ``interpolation: True`` does not draw cells: Highcharts paints ONE pixel per
# point onto a canvas of round(D / rowsize) + 1 rows (D = max - min of the strike
# axis), putting a point in row floor(C - C / D * (y - min)), and stretches that
# canvas over the plot. The axis pads itself half a row each end for the same
# rowsize, so the picture's rows are centred on max, max - rowsize, … — and a
# strike is drawn on its own place on the axis ONLY when max and min sit on the
# strike ladder. Off it (the bars' 4% pad, the spot path's 1%) the canvas rows
# fall between strikes and every colour is drawn up to a strike HIGHER than its
# strike; and a product such as 0.2 * 15 = 3.0000000000000004 floors two strikes
# into one row, so one of them is not drawn at all. Measured in the browser
# 2026-10-14: an $SPX strike at 5970 vanished, 5900 drew 0.83 of a strike high,
# while the contour lines, drawn on the axis, sat where the values are.
#
# On the ladder that floor lands exactly ON a whole number, where rounding can
# tip it either way. So the top of the range is nudged up by this share of a
# row: every strike's row is then a whole number plus a sliver, and floors the
# same in any arithmetic. The picture moves by the same sliver, nothing visible.
_RASTER_NUDGE = 1e-6


def raster_range(lo, hi, anchor, step):
    """``[lo, hi]`` widened to the strike ladder ``anchor + k * step`` (``hi``
    then nudged up by ``_RASTER_NUDGE`` of a row), so the heatmap's picture puts
    every strike on its own place on the axis. Both panels take the result: the
    bars must share the heatmap's axis exactly. Unchanged when there is no
    ladder to snap to."""
    nums = [_fmt.num(v) for v in (lo, hi, anchor, step)]
    if None in nums or nums[3] <= 0 or nums[0] > nums[1]:
        return [lo, hi]
    lo, hi, anchor, step = nums
    below = math.floor((lo - anchor) / step + 1e-9)
    above = max(math.ceil((hi - anchor) / step - 1e-9), below + 1)
    return [anchor + below * step, anchor + above * step + step * _RASTER_NUDGE]


def spot_frame_range(step, half):
    """The spot frame's vertical range for BOTH panels: the offsets' own span.
    The axis adds the half row each end that holds the outer cells whole (it
    pads itself for the heatmap's rowsize), and on the offsets' ladder the
    picture lands on the axis (``raster_range``)."""
    edge = half * step
    return raster_range(-edge, edge, 0.0, step)


# ── smoothing ────────────────────────────────────────────────────────────────
# The heat map's picture is blended by the browser as it is stretched to the
# panel, but that only softens the edges between cells: a strike that reads 10,
# 0, 10, 0 from one minute to the next still draws as stripes. This averages the
# VALUES over neighbouring minutes and strikes before they are coloured.
#
# smooth key -> the control's label. The order is the picker's order.
SMOOTHS = {"off": "Off", "light": "Light", "medium": "Medium", "strong": "Strong"}
# Each strength as a share of the configured width (``[smooth]`` is Medium's).
SMOOTH_STRENGTH = {"light": 0.5, "medium": 1.0, "strong": 2.0}
SMOOTH_HELP = ("Average each cell with the minutes and strikes around it before "
               "it is coloured, so noise from one minute to the next does not "
               "draw as stripes. A smoothed peak is lower and wider than the "
               "real one, and the newest minutes lag a little. The bars are "
               "never smoothed.")


def smooth_args(level, widths):
    """``smooth``'s keywords for one strength, or None for no smoothing.
    ``widths`` is ``gamma_heat_config.smooth()``, Medium's own."""
    share = SMOOTH_STRENGTH.get(level)
    if not share:
        return None
    out = {key: (_fmt.num(widths.get(key)) or 0.0) * share
           for key in ("minutes", "strikes")}
    return out if any(v > 0 for v in out.values()) else None


def _weights(sigma):
    """A bell curve's weights for offsets ``-r … r``, out to three widths."""
    radius = max(1, math.ceil(3 * sigma))
    return [math.exp(-(d * d) / (2.0 * sigma * sigma))
            for d in range(-radius, radius + 1)]


def _averaged(line, weights):
    """One row or column, each cell replaced by the weighted average of the
    cells around it. The weights are shared out over the cells that EXIST, so a
    gap is neither a zero nor a value, and an edge is not dimmed. A gap stays a
    gap."""
    n, radius = len(line), len(weights) // 2
    out = [None] * n
    for i in range(n):
        if line[i] is None:
            continue
        total = weight = 0.0
        for j in range(max(0, i - radius), min(n, i + radius + 1)):
            value = line[j]
            if value is not None:
                w = weights[j - i + radius]
                total += w * value
                weight += w
        out[i] = total / weight
    return out


def smooth(z, *, minutes, strikes):
    """The grid ``z[yi][xi]`` with each cell averaged over its neighbours: a bell
    curve ``minutes`` columns wide along time and ``strikes`` rows wide along the
    strike axis (each a standard deviation; 0 leaves that direction alone).

    A cell that is not a number is a gap, before and after. Nothing is written
    to ``z``; with both widths at 0 it is handed back as it came."""
    if not z or (minutes <= 0 and strikes <= 0):
        return z
    grid = [[v if _fmt.num(v) is not None else None for v in row] for row in z]
    if minutes > 0:
        weights = _weights(minutes)
        grid = [_averaged(row, weights) for row in grid]
    if strikes > 0:
        weights = _weights(strikes)
        columns = [_averaged(list(col), weights) for col in zip(*grid)]
        grid = [list(row) for row in zip(*columns)]
    return grid


# ── the colour ramp ──────────────────────────────────────────────────────────
# The colour axis's stops, built from the eight colours an operator can choose
# (``theme.gamma_colors``). WHERE each grade sits and how see-through it is are
# the design's and stay here: the axis is symmetric about zero, the bright
# stops sit far out so most of the field stays deep, and opacity runs from
# nothing at zero to nearly solid at the ends, which is what blends the field
# into the wash behind it. (position, colour key or None for clear, opacity)
RAMP = (
    (0.00, "put_peak", 0.98), (0.12, "put", 0.92),
    (0.28, "put_mid", 0.62), (0.48, "put_low", 0.5),
    (0.50, None, 0.0),
    (0.52, "call_low", 0.5), (0.72, "call_mid", 0.62),
    (0.88, "call", 0.92), (1.00, "call_peak", 0.98),
)


def _rgb(colour):
    """``#rrggbb`` -> (r, g, b)."""
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def rgba(colour, alpha):
    """``#rrggbb`` at ``alpha`` as ``rgba(r,g,b,a)``, the form a chart takes."""
    r, g, b = _rgb(colour)
    return f"rgba({r},{g},{b},{alpha})"


def mix(colour, other, amount):
    """``colour`` moved ``amount`` (0 … 1) of the way to ``other``, as ``#rrggbb``."""
    return "#" + "".join(f"{round(a + (b - a) * amount):02x}"
                         for a, b in zip(_rgb(colour), _rgb(other)))


def tint(colour, amount):
    """``colour`` moved ``amount`` (0 … 1) of the way to white."""
    return mix(colour, "#ffffff", amount)


def shade(colour, keep):
    """``colour`` at ``keep`` (0 … 1) of its brightness; the rest is black."""
    return mix(colour, "#000000", 1.0 - keep)


def ramp_stops(colours):
    """The colour axis's stops for ``colours`` (``theme.gamma_colors``): each
    grade's colour at its place in ``RAMP``, and a clear stop at zero."""
    return [[pos, rgba(colours[key], alpha) if key else "rgba(0,0,0,0.0)"]
            for pos, key, alpha in RAMP]


def ramp_svg(stops, *, uid, width=340, height=22):
    """The ramp alone as an SVG strip, over the ground the legend uses: what
    the Appearance preview draws from the unsaved colours. A ``ui.html``
    fragment, so every tag here is one the sanitizer keeps (pinned by a test)."""
    grad = "".join(
        f'<stop offset="{pos:.2f}" stop-color="{rgb}" stop-opacity="{alpha:.2f}"></stop>'
        for pos, (rgb, alpha) in ((p, _stop(c)) for p, c in stops))
    return (
        f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none">'
        f'<defs><linearGradient id="{uid}-ramp" x1="0" y1="0" x2="1" y2="0">'
        f'{grad}</linearGradient></defs>'
        f'<rect x="0" y="0" width="{width}" height="{height}" '
        f'fill="{_LEGEND_GROUND}"></rect>'
        f'<rect x="0" y="0" width="{width}" height="{height}" '
        f'fill="url(#{uid}-ramp)"></rect>'
        f'</svg>')


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


# ── contour lines ────────────────────────────────────────────────────────────
# Isolines of the drawn grid, as plain polylines the chart can overlay. Marching
# squares over the cells between neighbouring strikes and neighbouring minutes;
# a cell with a gap (None) in any corner is skipped, so no line is ever
# interpolated through a value that was not measured.

# A cell's four edges, and which pairs of them a line joins for each pattern of
# corners at or above the level. Corner bits: lower-left 1, lower-right 2,
# upper-right 4, upper-left 8 ("lower" is the lower strike).
_BOTTOM, _RIGHT, _TOP, _LEFT = range(4)
_CELL_LINES = {
    1: ((_LEFT, _BOTTOM),), 2: ((_BOTTOM, _RIGHT),), 3: ((_LEFT, _RIGHT),),
    4: ((_RIGHT, _TOP),), 5: ((_LEFT, _TOP), (_BOTTOM, _RIGHT)),
    6: ((_BOTTOM, _TOP),), 7: ((_LEFT, _TOP),), 8: ((_LEFT, _TOP),),
    9: ((_BOTTOM, _TOP),), 10: ((_LEFT, _BOTTOM), (_RIGHT, _TOP)),
    11: ((_RIGHT, _TOP),), 12: ((_LEFT, _RIGHT),), 13: ((_BOTTOM, _RIGHT),),
    14: ((_LEFT, _BOTTOM),)}
# A point on a straight run is dropped when it sits within this share of one
# strike step of the run. Small enough that a line is never visibly bent.
_CONTOUR_TOLERANCE = 0.03
# The longest run one simplifying step looks back over (it re-checks every
# point in the run, so this bounds the work on a long flat line).
_CONTOUR_RUN = 64


def contour_levels(zmax, steps):
    """The levels drawn each side of zero: the top of the colour scale and
    ``steps - 1`` halvings below it, ascending. ``[]`` with no usable scale."""
    if (isinstance(zmax, bool) or not isinstance(zmax, (int, float))
            or zmax != zmax or zmax <= 0 or steps < 1):
        return []
    return [zmax / 2 ** k for k in range(int(steps) - 1, -1, -1)]


def _level_lines(ys, z, levels):
    """``{level: ([(edge, edge), …], {edge: (x, y)})}`` for every level at once.

    An edge is named by its lower-left node, so the two cells that share it
    name it alike and the pieces join exactly, with no comparing of floats."""
    width = min(len(row) for row in z)
    found = {level: ([], {}) for level in levels}
    for yi in range(len(ys) - 1):
        low, high = z[yi], z[yi + 1]
        y0, dy = ys[yi], ys[yi + 1] - ys[yi]
        for xi in range(width - 1):
            a, b, c, d = low[xi], low[xi + 1], high[xi + 1], high[xi]
            if a is None or b is None or c is None or d is None:
                continue
            least, most = min(a, b, c, d), max(a, b, c, d)
            if least == most:
                continue
            for level in levels:
                if not least < level <= most:
                    continue
                case = ((a >= level) | (b >= level) << 1
                        | (c >= level) << 2 | (d >= level) << 3)
                segments, points = found[level]
                named = {}
                for edge in {e for pair in _CELL_LINES[case] for e in pair}:
                    if edge == _BOTTOM:
                        key, at = ("h", xi, yi), (xi + (level - a) / (b - a), y0)
                    elif edge == _RIGHT:
                        key = ("v", xi + 1, yi)
                        at = (xi + 1, y0 + dy * (level - b) / (c - b))
                    elif edge == _TOP:
                        key = ("h", xi, yi + 1)
                        at = (xi + (level - d) / (c - d), y0 + dy)
                    else:
                        key, at = ("v", xi, yi), (xi, y0 + dy * (level - a) / (d - a))
                    points[key] = at
                    named[edge] = key
                segments.extend((named[e1], named[e2]) for e1, e2 in _CELL_LINES[case])
    return found


def _chains(segments):
    """Join two-edge pieces into runs of edges: open lines first, then loops (a
    loop ends on the edge it began on)."""
    touching = {}
    for i, (e1, e2) in enumerate(segments):
        touching.setdefault(e1, []).append(i)
        touching.setdefault(e2, []).append(i)
    used = [False] * len(segments)

    def walk(edge):
        chain = [edge]
        while True:
            step = next((i for i in touching[edge] if not used[i]), None)
            if step is None:
                return chain
            used[step] = True
            e1, e2 = segments[step]
            edge = e2 if e1 == edge else e1
            chain.append(edge)

    out = [walk(edge) for edge, pieces in touching.items()
           if len(pieces) == 1 and not used[pieces[0]]]
    out += [walk(e1) for i, (e1, _e2) in enumerate(segments) if not used[i]]
    return out


def _straightened(points, tolerance):
    """``points`` without those that sit on a straight run between their
    neighbours (within ``tolerance`` in strike units). Ends are always kept."""
    if len(points) < 3:
        return list(points)
    out, anchor, end = [points[0]], 0, 2
    while end < len(points):
        (ax, ay), (bx, by) = points[anchor], points[end]
        straight = bx != ax and end - anchor < _CONTOUR_RUN
        if straight:
            for px, py in points[anchor + 1:end]:
                if (not min(ax, bx) <= px <= max(ax, bx)
                        or abs(py - (ay + (by - ay) * (px - ax) / (bx - ax))) > tolerance):
                    straight = False
                    break
        if straight:
            end += 1
        else:
            anchor = end - 1
            out.append(points[anchor])
            end = anchor + 2
    out.append(points[-1])
    return out


def _span(line):
    xs = [p[0] for p in line]
    return max(xs) - min(xs)


def _flat(lines, max_points):
    """Polylines as ONE series: longest first, a ``[x, None]`` break after each,
    stopping before the point budget is passed."""
    out, used = [], 0
    for line in sorted(lines, key=lambda ln: -_span(ln)):
        if used + len(line) > max_points:
            break
        used += len(line)
        out.extend([round(x, 3), round(y, 3)] for x, y in line)
        out.append([round(line[-1][0], 3), None])
    return out


def contours(ys, z, zmax, *, steps, min_columns, max_points):
    """Contour lines of the drawn grid ``z[yi][xi]`` (rows are ``ys``, ascending;
    columns are the minutes), as ``{"pos": points, "neg": points}``.

    The levels are ``contour_levels(zmax, steps)`` above zero and the same below
    it, so a line means the same share of the colour scale in every view. Each
    list is one chart series: ``[x, y]`` points with an ``[x, None]`` break
    between lines, ``x`` in column units (a crossing between two minutes sits
    between their columns). A line spanning fewer than ``min_columns`` columns
    is a speck and is left out; past ``max_points`` the shortest are dropped.
    Nothing is written to ``z``."""
    out = {"pos": [], "neg": []}
    levels = contour_levels(zmax, steps)
    if not levels or len(ys) < 2 or len(z) < 2 or min(len(row) for row in z) < 2:
        return out
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b > a]
    tolerance = _CONTOUR_TOLERANCE * min(gaps) if gaps else 0.0
    signed = {level: "pos" for level in levels}
    signed.update({-level: "neg" for level in levels})
    lines = {"pos": [], "neg": []}
    for level, (segments, points) in _level_lines(ys, z, list(signed)).items():
        for chain in _chains(segments):
            line = _straightened([points[edge] for edge in chain], tolerance)
            if line[0] > line[-1]:
                line.reverse()               # one direction, so the output is stable
            if _span(line) >= min_columns:
                lines[signed[level]].append(line)
    for sign in out:
        out[sign] = _flat(lines[sign], max_points)
    return out
