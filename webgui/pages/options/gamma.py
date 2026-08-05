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
from pages.ui_guard import guard, guard_async
from .inputs import select_all_on_focus
from .theme import BTN, BTN_PRIMARY

POS_COLOR = "#66bb6a"
NEG_COLOR = "#ef5350"
SPOT_COLOR = "#ffd54f"
PRICE_LINE = "#f5f5f5"          # off-white — spot track overlaid on the dark heatmap
HEATMAP_SEP = "#4d4d4d"         # softer (lighter) cell-separator mesh on the heatmap
FLIP_COLOR = "#42a5f5"
WALL_COLOR = "#b39ddb"

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

# Dark diverging color-axis stops: the heatmap blends into the dark page (like the
# candlestick chart) — net ≈ 0 fades to TRANSPARENT (the background shows through),
# strong negative glows red, strong positive glows green. (rgba alpha is honored by
# the interpolated heatmap image.) Replaces the old light RdYlGn (yellow-at-zero).
HEAT_STOPS = [
    [0.00, "rgba(239,83,80,0.95)"],   # most-negative net → strong red
    [0.30, "rgba(239,83,80,0.45)"],
    [0.48, "rgba(239,83,80,0.0)"],
    [0.50, "rgba(0,0,0,0.0)"],         # zero → transparent (dark page shows through)
    [0.52, "rgba(102,187,106,0.0)"],
    [0.70, "rgba(102,187,106,0.45)"],
    [1.00, "rgba(102,187,106,0.95)"],  # most-positive net → strong green
]


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


def _view_label(view):
    """Display label for a view (GEX→GAMMA, DEX→DELTA; others unchanged)."""
    return _VIEW_LABELS.get(view, view)


def _darker(hexc, factor=0.55):
    """Return a darker shade of a ``#rrggbb`` color (for the beveled bar border)."""
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


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
        side = "Call wall" if (spot is None or w >= spot) else "Put wall"
        anns.append({"value": w, "text": f"{side} {w:g}", "color": WALL_COLOR})
    return anns


def _level_plot_line(value, text, color):
    """One horizontal reference line (+ right-aligned label) for the strike axis."""
    return {"value": value, "color": color, "width": 1, "dashStyle": "Dash",
            "zIndex": 4,
            "label": {"text": text, "align": "right", "x": -6, "y": -4,
                      "style": {"color": color, "fontSize": "10px"}}}


def _is_level(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def wall_plot_lines(spot, walls, flip=None):
    """Gamma-flip + Call/Put wall levels as yAxis plotLines — horizontal, so they
    run ACROSS the heatmap's full time axis.

    The bar chart already marks these levels on the shared strike axis; extending
    them over the heatmap shows where price sat relative to the flip and the walls
    at every point in the session, not just now. Naming/colors match
    ``line_annotations`` so the two panels read as one. Non-numeric levels are
    skipped rather than raising."""
    out = []
    if _is_level(flip):
        out.append(_level_plot_line(flip, f"Gamma flip {flip:g}", FLIP_COLOR))
    for w in (walls or []):
        if not _is_level(w):
            continue
        side = "Call wall" if (spot is None or w >= spot) else "Put wall"
        out.append(_level_plot_line(w, f"{side} {w:g}", WALL_COLOR))
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
        return {"strikes": [], "nets": [], "colors": [], "hovers": []}
    window = set(strikes_around(gex.keys(), spot, n_side))
    strikes, nets, colors, hovers = [], [], [], []
    for strike in sorted(gex):
        if strike not in window:
            continue
        cell = gex[strike] or {}
        net = cell.get("net", 0.0)
        strikes.append(strike)
        nets.append(net)
        colors.append(POS_COLOR if net >= 0 else NEG_COLOR)
        hovers.append(f"{strike:g}: net {net:,.0f} "
                      f"(C {cell.get('call', 0):,.0f} / P {cell.get('put', 0):,.0f})")
    return {"strikes": strikes, "nets": nets, "colors": colors, "hovers": hovers}


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
    """One-line '·'-separated status strip: last/next scan + next-refresh countdown +
    the per-view summary. The collector STATUS WORD is rendered separately (colored),
    so it's not included here. Defensive: missing fields → em-dashes."""
    st = gex_status or {}
    parts = [f"Last scan {st.get('last_scan') or '—'}",
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
    points = [{"x": s, "y": n, "color": c,
               "borderColor": _darker(c), "borderWidth": 1,
               "custom": {"hover": h}}
              for s, n, c, h in zip(b["strikes"], b["nets"], b["colors"], b["hovers"])]
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
        "plotOptions": {"bar": {"pointPadding": 0.04, "groupPadding": 0,
                                "borderRadius": 0}},
        "series": [{"type": "bar", "name": label, "data": points, "colorByPoint": False}],
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


def _coloraxis(zmax):
    """Diverging RdYlGn color axis, symmetric about zero (so net 0 = yellow)."""
    ca = {"stops": HEAT_STOPS, "labels": {"enabled": False}}
    if zmax:
        ca["min"], ca["max"] = -zmax, zmax
    return ca


UP_COLOR, DOWN_COLOR = "#7fd1a3", "#e79a9a"   # candle/OHLC up / down


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


def track_points(values):
    """[[time_index, level], …] for a level track, keeping None as a GAP.

    Nulls must be preserved rather than skipped: dropping them would shift every
    later point one column to the left, silently mis-dating the movement."""
    return [[i, v if isinstance(v, (int, float)) and not isinstance(v, bool) else None]
            for i, v in enumerate(values or [])]


def heatmap_figure(rows, view="GEX", height=680, yrange=None, projection=None,
                   walls=None, spot=None, flip=None, levels=None,
                   show_tracks=False, spot_style="line", spot_interval=5):
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
    # Heatmap points [time_index, strike_value, net]: x is the time category index,
    # y is the ACTUAL strike (linear axis) so the continuous spot line overlays.
    data = [[xi, strikes[yi], z[yi][xi]]
            for yi in vis for xi in range(len(times))
            if z[yi][xi] is not None]
    # Symmetric color clamp from the VISIBLE cells' 95th-percentile |net| (robust —
    # same as the Term heatmap) so a few extreme strikes don't wash the mid-range
    # colors to transparent on the flatter views (Charm / DEX / Vanna).
    zmax = _robust_zmax([z[yi] for yi in vis]) or None
    # Row height from the VISIBLE strikes' typical spacing (median gap) so cells
    # tile the window densely regardless of off-window strike spacing.
    rowsize = _strike_step([strikes[yi] for yi in vis])
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
        for strike, vals in pgrid.items():
            try:
                sk = float(strike)
            except (TypeError, ValueError):
                continue
            if yrange is not None and not (yrange[0] <= sk <= yrange[1]):
                continue
            proj_rows_for_zmax.append([v for v in vals if v is not None])
            for j, v in enumerate(vals):
                if v is not None:
                    heat_series["data"].append([base + j, sk, v])
        # Re-clamp the color axis over collected + projected visible cells (robust
        # 95th-pct so a few extreme 0-DTE ATM close cells don't wash the scale).
        zmax = _robust_zmax([z[yi] for yi in vis] + proj_rows_for_zmax) or None
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
    series.append(_line_series("Spot", spot_pts if spot_style == "line" else [],
                               PRICE_LINE, lineWidth=2, enableMouseTracking=True))
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
                             ("Call wall track", "call_wall", WALL_COLOR),
                             ("Put wall track", "put_wall", WALL_COLOR)):
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
             "plotLines": wall_plot_lines(spot, walls, flip)}
    if yrange is not None:
        yaxis["min"], yaxis["max"] = yrange[0], yrange[1]
    fig = _base_chart("heatmap", height)
    fig["chart"]["backgroundColor"] = "transparent"     # same as the candlestick graph
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
        "xAxis": {**_dark_axis(), "categories": times,
                  "labels": {"rotation": -45, "style": {"color": FONT}},
                  "plotLines": xaxis_plotlines},
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


# Flow-view colors (price / call premium / put premium).
FLOW_PRICE = "#e8d44d"   # yellow — underlying price
FLOW_CALL = "#26c6a4"    # green — call premium
FLOW_PUT = "#ef5f7a"     # pink — put premium


def _flow_num(v):
    return v if isinstance(v, (int, float)) else None


def flow_figure(rows, height=680):
    """Intraday options-flow chart for one symbol (dark, stacked panels).

    ``rows`` = the snapshot's ``flow`` list ({ts, spot, call_vol, put_vol,
    call_prem, put_prem}, one per 2-min snapshot). TOP panel: underlying **price**
    (left axis) + daily-cumulative **call/put premium** in $M (right axis). BOTTOM
    panel: **net premium (call − put)** in $M as a signed area (green call-lead /
    red put-lead). Premium is None on rows that predate Phase-1 collection — those
    points are skipped (the line just starts where premium began collecting)."""
    rows = rows or []
    times = [_fmt_ts(r.get("ts")) for r in rows]
    spot = [[i, _flow_num(r.get("spot"))] for i, r in enumerate(rows)
            if _flow_num(r.get("spot")) is not None]
    callp = [[i, _flow_num(r.get("call_prem")) / 1e6] for i, r in enumerate(rows)
             if _flow_num(r.get("call_prem")) is not None]
    putp = [[i, _flow_num(r.get("put_prem")) / 1e6] for i, r in enumerate(rows)
            if _flow_num(r.get("put_prem")) is not None]
    net = []
    for i, r in enumerate(rows):
        cp, pp = _flow_num(r.get("call_prem")), _flow_num(r.get("put_prem"))
        if cp is None and pp is None:
            continue
        net.append([i, ((cp or 0) - (pp or 0)) / 1e6])

    fig = _base_chart("line", height)
    fig["chart"]["marginBottom"] = 64
    fig["legend"] = {"enabled": True, "itemStyle": {"color": FONT},
                     "itemHoverStyle": {"color": "#ffffff"}}
    fig.update({
        "title": {"text": "Intraday options flow (price + call/put premium)",
                  "style": {"color": FONT}},
        "xAxis": {**_dark_axis("Time"), "categories": times,
                  "labels": {"rotation": -45, "style": {"color": FONT}}},
        "yAxis": [
            {**_dark_axis("Price"), "top": "0%", "height": "62%"},
            {**_dark_axis("Premium ($M)"), "top": "0%", "height": "62%", "opposite": True},
            {**_dark_axis("Net premium ($M)"), "top": "68%", "height": "32%",
             "offset": 0, "plotLines": [{"value": 0, "color": "#777777", "width": 1}]},
        ],
        "tooltip": {"shared": True, "backgroundColor": "#222222",
                    "borderColor": "#444444", "style": {"color": FONT, "fontSize": "11px"},
                    "valueDecimals": 2},
        "series": [
            {"type": "line", "name": "Price", "data": spot, "yAxis": 0,
             "color": FLOW_PRICE, "lineWidth": 2, "marker": {"enabled": False}},
            {"type": "line", "name": "Call premium", "data": callp, "yAxis": 1,
             "color": FLOW_CALL, "lineWidth": 2, "marker": {"enabled": False}},
            {"type": "line", "name": "Put premium", "data": putp, "yAxis": 1,
             "color": FLOW_PUT, "lineWidth": 2, "marker": {"enabled": False}},
            {"type": "area", "name": "Net premium (call − put)", "data": net, "yAxis": 2,
             "threshold": 0, "color": FLOW_CALL, "negativeColor": FLOW_PUT,
             "fillColor": "rgba(38,198,164,0.28)", "negativeFillColor": "rgba(239,95,122,0.28)",
             "lineWidth": 1, "marker": {"enabled": False}},
        ],
    })
    return fig


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
# The group/colour tables below DUPLICATE ``services/options_svc/net_premium.GROUPS``
# on purpose: Tier 1 may import only nicegui / bus_client / shared.contracts, never
# ``services.*``. Tests pin the membership so the two copies cannot drift silently.
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
NET_PREM_GROUPS = (
    {"key": "indices", "label": "Indices & Broad",
     "symbols": ("$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA")},
    {"key": "sectors", "label": "SPDR Sectors",
     "symbols": ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                 "XLP", "XLRE", "XLU", "XLV", "XLY")},
    {"key": "megacaps", "label": "Mega-caps",
     "symbols": ("NVDA", "AVGO", "AAPL", "META", "MSFT",
                 "TSLA", "PLTR", "AMZN", "GOOGL", "AMD")},
)

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
# The service's collection window (options_svc/scheduler _GEX_START/_GEX_STOP) and
# a staleness bound at 2× its 1-min publish cadence. Only INSIDE this window is a
# stale publish evidence of a failure — see net_prem_status_text.
_NP_WINDOW_OPEN, _NP_WINDOW_CLOSE = (8, 0), (15, 20)
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


def net_prem_figure(series, symbols, mode="dollars", height=680):
    """Intraday net-premium chart — one fixed-colour line per selected symbol.

    The x-axis is a SYNTHETIC category axis of ``_fmt_ts`` labels over the sorted
    UNION of timestamps across the selection, not a datetime axis — for two
    reasons. First, the same one ``flow_figure`` has: a real datetime axis
    stretches the session across the overnight/weekend gap. Second, and specific
    to this view: the symbols do not share a clock. A name that started
    collecting late has fewer rows, so plotting each series against its own row
    index would shear the lines apart — SPY's 09:15 point would sit above QQQ's
    08:30 one and the chart would silently lie about when a flow happened.
    Indexing every series into the shared union keeps them on one timeline, and a
    symbol that starts late simply begins further along the axis.
    """
    mode = mode if mode in NET_PREM_MODES else "dollars"
    picked = _np_selected(symbols)
    rows_by = {sym: _np_rows(series, sym) for sym in picked}
    times = sorted({ts for rows in rows_by.values() for ts, _ in rows})
    index = {ts: i for i, ts in enumerate(times)}

    plots = []
    for sym in picked:
        # A point with no value in this mode is SKIPPED, not emitted as None —
        # and that can never hide an interior gap. The stored premiums are
        # daily-CUMULATIVE (the service's build_series accumulates nothing
        # downstream), so call+put is monotonic non-decreasing: once it exceeds 0
        # it stays there for the rest of the session. An unreportable skew point
        # is therefore only ever possible in a LEADING run, before anything
        # traded — skipping trims a meaningless prefix and cannot connect the line
        # across a hole. A future "optimization" into a running total would break
        # that invariant, and with it this reasoning.
        data = [[index[ts], value] for ts, row in rows_by[sym]
                if (value := net_prem_value(row, mode)) is not None]
        if data:
            plots.append({"type": "line", "name": sym, "data": data,
                          "color": net_prem_color(sym), "lineWidth": 2,
                          "marker": {"enabled": False}})

    fig = _base_chart("line", height)
    fig["chart"]["marginBottom"] = 64
    fig["legend"] = {"enabled": True, "itemStyle": {"color": FONT},
                     "itemHoverStyle": {"color": "#ffffff"}}
    fig.update({
        "title": {"text": f"Intraday net premium (call $ − put $) · "
                          f"{NET_PREM_MODES[mode]}",
                  "style": {"color": FONT}},
        "xAxis": {**_dark_axis("Time"), "categories": [_fmt_ts(t) for t in times],
                  "labels": {"rotation": -45, "style": {"color": FONT}}},
        "yAxis": {**_dark_axis(_NET_PREM_AXIS[mode]),
                  "plotLines": [{"value": 0, "color": "#777777", "width": 1,
                                 "zIndex": 3}]},
        # Deliberately NOT shared: with up to 28 selectable series a shared
        # tooltip is a wall of rows taller than the chart. Hover reports the one
        # line the cursor is on.
        "tooltip": {"shared": False, "backgroundColor": "#222222",
                    "borderColor": "#444444",
                    "style": {"color": FONT, "fontSize": "11px"},
                    "valueDecimals": 2},
        "series": plots,
    })
    return fig


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
        return ("Net premium has never been published — is the options service "
                "running?")

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
    parts.append(f"{n} symbol{'' if n == 1 else 's'}")
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


def term_heatmap(term_grid):
    """Highcharts heatmap options for the Term view (net GEX by expiry × strike).

    Strikes with all-zero net across expirations are dropped. Both axes are
    categorical (no overlay), and the color scale is clamped symmetrically to a
    robust max so a few extreme strikes don't wash out the mid-range cells.
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
    # Blended + press-and-hold tooltip, same as the intraday heatmap. The Term view
    # is painted on chart_el (recreated on the bar↔Term kind switch), so the load
    # hook rides this figure and fires on that recreation.
    fig["chart"]["events"] = {":load": _HEAT_PRESS_TOOLTIP_JS}
    fig.update({
        "title": {"text": "Term structure (net GEX by expiry × strike)",
                  "style": {"color": FONT}},
        "xAxis": {**_dark_axis("Expiration"), "categories": exps},
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
    # No page title — the tab strip names the page (2026-07-11 dead-space cleanup).

    # state["snap"] is the cached snapshot from the bus (None until first read).
    # ``fetching`` is an in-flight guard so a slow off-loop big-payload read
    # (cache:options:gamma is ~14 MB) can't pile up across 2 s poll ticks.
    state: dict = {"snap": None, "countdown": 120, "fetching": False}
    # Last-seen bus cache versions for the fetch-free repaint/dialog timers.
    seen = {"gamma": None, "explain": None, "analyze": None, "status": None}

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
            for v in list(_VIEWS) + ["Flow", "Term"]:
                ui.tab(v, label=_view_label(v))
        return tabs

    if _slot is not None:
        with _slot:
            view_toggle = _build_view_tabs()
    else:
        view_toggle = _build_view_tabs()

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
    with ui.row().classes("w-[calc(100%+1rem)] no-wrap gap-0 items-start relative gamma-xhair-row"):
        chart_box = ui.column().classes(f"min-w-0 {_INIT_FLEX}")
        with chart_box:
            # chart_plot switches kind (bar <-> Term heatmap). Highcharts'
            # chart.update() leaks plotLines/colorAxis across a type switch, so the
            # element lives in its own container and is RECREATED on kind-change
            # (see _set_chart); same-kind repaints update in place (flicker-free).
            chart_plot_box = ui.column().classes("w-full q-gutter-none")
            with chart_plot_box:
                state["chart_el"] = ui.highchart(_empty_fig(), extras=["heatmap", "coloraxis"]).classes("w-full")
            state["chart_kind"] = "bar"
            chart_msg = ui.label("Fetch a symbol… (no snapshot yet).") \
                .classes("opacity-60 text-sm")
        heatmap_box = ui.column().classes(f"min-w-0 {_INIT_FLEX}")
        with heatmap_box:
            # Created with the heatmap init fig so the press-and-hold-tooltip load
            # hook is installed at creation (load fires once); updated in place after.
            heat_plot = ui.highchart(_heat_init_fig(), extras=["heatmap", "coloraxis"]).classes("w-full")
            heat_msg = ui.label("").classes("opacity-60 text-sm")

    # Tiny status strip BELOW the charts, right-aligned: the collector status WORD
    # (colored) + the neutral detail (last/next scan + refresh countdown + per-view
    # summary). Sits under the charts so it never collides with the time-axis labels.
    with ui.row().classes("w-full justify-end items-baseline gap-2 text-[10px] "
                          "leading-none opacity-90 -mt-1"):
        status_lbl = ui.label("").classes("font-medium")
        detail_lbl = ui.label("").classes("opacity-70")

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
        both panels to fill their containers. Null-safe (no-op if a chart isn't mounted
        yet); the next repaint reflows again."""
        @guard
        def _do():
            el = state.get("chart_el")
            ids = [i for i in (getattr(el, "id", None), heat_plot.id) if i]
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

    def _set_chart(fig):
        """Paint chart_plot: update in place when the chart KIND is unchanged
        (the common bar->bar repaint, flicker-free), but RECREATE the element when
        the kind changes (bar <-> Term heatmap) so stale plotLines/colorAxis from
        the previous type don't leak through Highcharts' merge-based update."""
        kind = fig["chart"]["type"]
        if state.get("chart_kind") != kind:
            chart_plot_box.clear()
            with chart_plot_box:
                state["chart_el"] = ui.highchart(fig, extras=["heatmap", "coloraxis"]).classes("w-full")
            state["chart_kind"] = kind
        else:
            _set_figure(state["chart_el"], fig)
        return state["chart_el"]

    def _render_view():
        """Paint the active view from the cached snapshot (no fetch, no teardown).

        The Highcharts elements persist across repaints and are updated in place
        (via _set_figure / _set_chart) so the charts don't flicker."""
        snap = state["snap"]
        if not snap:
            state["chart_el"].set_visibility(False)
            heat_plot.set_visibility(False)
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
            heat_msg.set_visibility(False)
            _apply_flex(0, term=True)
            _set_summary(summary_text({"spot": spot, "strike_count": None}, "Term"))
            return
        if view == "Flow":
            # Intraday options-flow: price + call/put premium + net panel, full width
            # (no heatmap). Same single chart element as Term (recreated on kind change).
            _set_chart(flow_figure(snap.get("flow") or []))
            state["chart_el"].set_visibility(True)
            heat_plot.set_visibility(False)
            heat_msg.set_visibility(False)
            _apply_flex(0, term=True)
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
                "gex": _refloat_keys(raw.get("gex"))}
        view_spot = data.get("spot") or spot
        if not isinstance(view_spot, (int, float)):
            # No usable underlying price (e.g. market closed / sparse off-hours
            # chain) — the near-spot bar/heatmap window can't be computed. Show a
            # message instead of crashing on the spot*pct band math.
            state["chart_el"].set_visibility(False)
            heat_plot.set_visibility(False)
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
        for r in (entry.get("history") or []):
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
                                                  spot_interval=spot_int_sel.value))
            heat_plot.set_visibility(True)
            heat_msg.set_visibility(False)
        else:
            heat_plot.set_visibility(False)
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
        ui.notify(f"Gamma refresh requested for {sym}")
        state["countdown"] = 120

    @guard
    def _auto_refresh():
        # Fetch-free on the page side: enqueue a refresh for the current symbol;
        # the service recomputes + republishes and the version-poll repaints.
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

    @guard_async
    async def _maybe_repaint(version):
        # Repaint only when the bus cache version changes (the service bumps it
        # when a requested gamma_refresh finishes). The version compare is done by
        # the caller off the cheap :ver probe; the actual snapshot payload
        # (cache:options:gamma is ~14 MB — a big blocking GET + JSON parse) is read
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
            *_SCHED_VIEWS.values()])
        await _maybe_repaint(v["options:gamma"])
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
    view_toggle.on_value_change(lambda e: _render_view())

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
        # Bar size is meaningless for a line — hide it rather than leave a control
        # that silently does nothing.
        spot_int_sel.set_visibility(spot_style_sel.value != "line")

    spot_style_sel.on_value_change(_on_spot_style)
    spot_int_sel.on_value_change(_on_spot_interval)
    _sync_spot_controls()

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
        # Sync the dropdown to the symbol actually in the cache so a page (re)build
        # doesn't show $SPX while another symbol's data is displayed (which a later
        # refresh would then revert to $SPX). Done BEFORE wiring on_value_change.
        _set_symbol((state["snap"] or {}).get("symbol"))
        symbol_in.on_value_change(lambda e: _on_symbol_change())
        _render_view()

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
