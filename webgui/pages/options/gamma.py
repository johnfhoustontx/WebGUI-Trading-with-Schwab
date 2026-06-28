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
from pages.ui_guard import guard, guard_async
from .theme import TXT_POS, TXT_NEG, TXT_NEUTRAL

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


def flex_class(grow, grow2=1, basis="0%"):
    """Runtime arbitrary-value Tailwind class for a continuous flex ratio.

    The bar/heatmap split is a genuinely continuous value (~82 distinct ratios over a
    session via panel_flex) with no finite palette, so it uses a JIT-generated
    arbitrary class (`_` = space). Reset per repaint via .classes(remove=prev, add=new)."""
    return f"flex-[{grow}_{grow2}_{basis}]"


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
    fig.update({
        "title": {"text": f"{label} by strike", "style": {"color": FONT}},
        # A Highcharts bar chart reverses its xAxis by default (low strike at top);
        # reversed=False restores high strikes at the TOP, matching the heatmap's
        # linear strike axis so the two panels line up.
        "xAxis": {**_dark_axis("Strike"), "min": yr[0], "max": yr[1],
                  "reversed": False, "plotLines": plotlines},
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


def heatmap_figure(rows, view="GEX", height=680, yrange=None):
    """Intraday strike×time Highcharts heatmap (dark, cell separators, concise
    hover) with the underlying spot-price line overlaid on the same (linear)
    strike axis. ``yrange`` (when given) sets the Strike axis range so it aligns
    with the bar chart's near-spot window."""
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
    zmax = max((abs(z[yi][xi]) for yi in vis for xi in range(len(times))
                if z[yi][xi] is not None), default=0) or None
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
    if any(s is not None for s in spots):
        # Underlying price track over the session, on the shared Strike axis. A
        # line series ignores the colorAxis, so it isn't recolored by net value.
        spot_pts = [[xi, sp] for xi, sp in enumerate(spots) if isinstance(sp, (int, float))]
        series.append({"type": "line", "name": "Spot", "data": spot_pts,
                       "color": PRICE_LINE, "lineWidth": 2, "marker": {"enabled": False},
                       "colorAxis": False, "enableMouseTracking": True, "states": no_fade,
                       "tooltip": {"headerFormat": "", "pointFormat": "Spot {point.y:,.2f}"}})
    yaxis = {**_dark_axis("Strike")}
    if yrange is not None:
        yaxis["min"], yaxis["max"] = yrange[0], yrange[1]
    fig = _base_chart("heatmap", height)
    fig["chart"]["backgroundColor"] = "transparent"     # same as the candlestick graph
    fig["chart"]["marginBottom"] = 64                   # room for rotated time labels
    # Press-and-hold tooltip hook must be on whatever options the element MOUNTS
    # with — _render_view overwrites the init fig's options before the client
    # mounts, so carry the load hook here too (load fires once at mount).
    fig["chart"]["events"] = {":load": _HEAT_PRESS_TOOLTIP_JS}
    fig.update({
        "title": {"text": f"{_view_label(view)} intraday (strike × time)",
                  "style": {"color": FONT}},
        "xAxis": {**_dark_axis("Time"), "categories": times,
                  "labels": {"rotation": -45, "style": {"color": FONT}}},
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


def _heat_init_fig(height=680):
    """Initial (empty) heatmap element options + the press-and-hold-tooltip load hook.

    The heatmap element is persistent (created once, updated in place), and
    ``chart.events.load`` only fires at creation — so the hook must be present on
    the element's FIRST figure, not added later by heatmap_figure."""
    fig = _base_chart("heatmap", height)
    fig["title"] = {"text": None}
    fig["series"] = []
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
    s = summary or {}
    parts = [f"{view}"]
    if s.get("spot") is not None:
        parts.append(f"spot {s['spot']:,.2f}")
    if s.get("strike_count") is not None:
        parts.append(f"{s['strike_count']} strikes")
    if s.get("net_total") is not None:
        parts.append(f"net {s['net_total']:,.0f}")
    if s.get("flip") is not None:
        parts.append(f"flip {s['flip']:.1f}")
    return "  ·  ".join(parts)


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


def render():
    import bus_client
    from nicegui import ui

    ui.add_css(EXPLAIN_CSS)  # scoped styles for the Explain dialog (ui.html strips <style>)
    ui.label("Gamma").classes("text-h5")

    # state["snap"] is the cached snapshot from the bus (None until first read).
    state: dict = {"snap": None, "countdown": 120}
    # Last-seen bus cache versions for the fetch-free repaint/dialog timers.
    seen = {"gamma": None, "explain": None, "analyze": None, "status": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        _sym_opts = symbol_options(bus_client.read("options:gamma_symbols"))
        symbol_in = ui.select(_sym_opts, value=_DEFAULT_SYMBOL,
                              with_input=True, label="Symbol").classes("w-40")
        fetch_btn = ui.button("Refresh now", icon="refresh")
        view_toggle = ui.toggle({v: _view_label(v) for v in list(_VIEWS) + ["Term"]},
                                 value="GEX")
        explain_btn = ui.button("Explain", icon="help").props("outline")
        analyze_btn = ui.button("Analyze", icon="psychology").props("outline")
        countdown_lbl = ui.label("").classes("opacity-60 text-sm")
    # Auto briefings (today): the $SPX/SPY/QQQ Analyze runs the options service
    # auto-generates at premarket / ~18 min after open / midday / close. Each button
    # opens that slot's latest briefing in a new tab (the slot key is separate from
    # the ad-hoc Analyze key, so these never auto-open). Enabled once a slot has been
    # generated (version present); the generated date/time shows in the doc subtitle.
    sched_btns = {}
    with ui.row().classes("items-center gap-2 flex-wrap"):
        ui.label("Auto briefings:").classes("opacity-60 text-sm")
        for _slot, _title in (("premarket", "Premarket"), ("open", "Open"),
                              ("midday", "Midday"), ("close", "Close")):
            _b = ui.button(_title, icon="schedule").props("flat dense")
            _b.on_click(lambda s=_slot: ui.navigate.to(
                f"/options/analyze?slot={s}", new_tab=True))
            _b.disable()
            _b.tooltip(f"{_title} $SPX/SPY/QQQ briefing — not generated yet today")
            sched_btns[_slot] = _b
    # Collector status bar: status dot/text (colored) + last/next scan times.
    # Read-only view published by the options service (cache:options:gex_status);
    # version-polled like gamma/explain/analyze below. Sits alongside (does NOT
    # replace) the "Next refresh" countdown above.
    with ui.row().classes("items-center gap-4 flex-wrap"):
        status_lbl = ui.label("").classes("text-sm font-medium")
        last_scan_lbl = ui.label("Last scan —").classes("opacity-60 text-sm")
        next_scan_lbl = ui.label("Next scan —").classes("opacity-60 text-sm")
    summary_lbl = ui.label("").classes("opacity-70 text-sm")
    pressure_box = ui.row().classes("gap-3 items-center")
    # Persistent panels: the Highcharts elements are created ONCE and updated in
    # place on every repaint (Highcharts diffs the new options) — rebuilding them
    # each time would flash. Message labels are toggled via set_visibility. Column
    # flex weights are set per-render from the intraday snapshot count (panel_flex)
    # so the heatmap grows / bars shrink through the session.
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        chart_box = ui.column().classes("min-w-0").style("flex: 0.5 1 0%")
        with chart_box:
            # chart_plot switches kind (bar <-> Term heatmap). Highcharts'
            # chart.update() leaks plotLines/colorAxis across a type switch, so the
            # element lives in its own container and is RECREATED on kind-change
            # (see _set_chart); same-kind repaints update in place (flicker-free).
            chart_plot_box = ui.column().classes("w-full q-gutter-none")
            with chart_plot_box:
                state["chart_el"] = ui.highchart(_empty_fig(), extras=["heatmap"]).classes("w-full")
            state["chart_kind"] = "bar"
            chart_msg = ui.label("Fetch a symbol… (no snapshot yet).") \
                .classes("opacity-60 text-sm")
        heatmap_box = ui.column().classes("min-w-0").style("flex: 0.5 1 0%")
        with heatmap_box:
            # Created with the heatmap init fig so the press-and-hold-tooltip load
            # hook is installed at creation (load fires once); updated in place after.
            heat_plot = ui.highchart(_heat_init_fig(), extras=["heatmap"]).classes("w-full")
            heat_msg = ui.label("").classes("opacity-60 text-sm")

    def _current_symbol():
        return (symbol_in.value or "").strip().upper()

    def _apply_flex(n_cols, term=False):
        """Set the bar/heatmap column widths. Term → bars full width (no heatmap);
        otherwise proportional to the intraday snapshot count (panel_flex)."""
        if term:
            chart_box.style("flex: 1 1 0%")
            heatmap_box.style("flex: 0 0 0px")
            heatmap_box.set_visibility(False)
            return
        heatmap_box.set_visibility(True)
        bar_w, heat_w = panel_flex(n_cols)
        chart_box.style(f"flex: {bar_w} 1 0%")
        heatmap_box.style(f"flex: {heat_w} 1 0%")

    def _set_chart(fig):
        """Paint chart_plot: update in place when the chart KIND is unchanged
        (the common bar->bar repaint, flicker-free), but RECREATE the element when
        the kind changes (bar <-> Term heatmap) so stale plotLines/colorAxis from
        the previous type don't leak through Highcharts' merge-based update."""
        kind = fig["chart"]["type"]
        if state.get("chart_kind") != kind:
            chart_plot_box.clear()
            with chart_plot_box:
                state["chart_el"] = ui.highchart(fig, extras=["heatmap"]).classes("w-full")
            state["chart_kind"] = kind
        else:
            _set_figure(state["chart_el"], fig)
        return state["chart_el"]

    def _render_view():
        """Paint the active view from the cached snapshot (no fetch, no teardown).

        The Highcharts elements persist across repaints and are updated in place
        (via _set_figure / _set_chart) so the charts don't flicker."""
        snap = state["snap"]
        pressure_box.clear()
        if not snap:
            state["chart_el"].set_visibility(False)
            heat_plot.set_visibility(False)
            heat_msg.set_visibility(False)
            chart_msg.text = "Fetch a symbol… (no snapshot yet)."
            chart_msg.set_visibility(True)
            summary_lbl.text = ""
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
            summary_lbl.text = summary_text({"spot": spot, "strike_count": None}, "Term")
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
            summary_lbl.text = ""
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
        summary_lbl.text = summary_text(
            {**summary, "strike_count": data.get("strike_count")}, _view_label(view))

        if rows:
            _set_figure(heat_plot, heatmap_figure(rows, view, yrange=yr))
            heat_plot.set_visibility(True)
            heat_msg.set_visibility(False)
        else:
            heat_plot.set_visibility(False)
            heat_msg.text = "No intraday snapshots yet (history collector not running)."
            heat_msg.set_visibility(True)
        _apply_flex(len(rows))

        if view == "DEX":
            hedge = entry.get("hedge") or {}
            with pressure_box:
                hp = hedge.get("hedge_pressure")
                if hp is None:
                    ui.label("0-DTE hedge pressure: n/a (nearest expiry is not 0-DTE)").classes("opacity-60 text-sm")
                else:
                    def tile(label, val, cls=TXT_NEUTRAL):
                        with ui.card().classes("p-2"):
                            ui.label(label).classes("text-xs opacity-60")
                            ui.label(f"{val:,.0f}").classes(f"text-base font-bold {cls}")
                    tile("Net Δ now", hedge.get("net_delta_0dte") or 0)
                    tile("Projected close", hedge.get("projected_net_delta_close") or 0)
                    tile("Hedge pressure", hp, TXT_POS if hp >= 0 else TXT_NEG)

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
        countdown_lbl.text = f"Next refresh: {state['countdown'] // 60}:{state['countdown'] % 60:02d}"

    @guard
    def _maybe_repaint(version):
        # Fetch-free: re-read + repaint only when the bus cache version changes
        # (the service bumps it when a requested gamma_refresh finishes).
        if version == seen["gamma"]:
            return
        seen["gamma"] = version
        snap = bus_client.read("options:gamma") or None
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
        last_scan_lbl.text = f"Last scan {st.get('last_scan') or '—'}"
        next_scan_lbl.text = f"Next scan {st.get('next_scan') or '—'}"

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

    _SCHED_VIEWS = {s: f"options:gamma_analyze_{s}" for s in sched_btns}

    def _sync_sched_btns(versions):
        # Enable a briefing button once its slot has been generated (version present);
        # the doc subtitle carries the generated date/time so staleness is visible.
        for s, b in sched_btns.items():
            if versions.get(_SCHED_VIEWS[s]):
                b.enable()
                b.tooltip(f"Open the latest auto-generated {b.text} briefing")
            else:
                b.disable()

    @guard
    def _poll():
        # One coalesced 2s tick: read all view versions in a single pipelined
        # round-trip (cheap :ver counters, no payload deserialize) and dispatch only
        # the views that changed — replaces separate per-view version-poll timers.
        v = bus_client.read_versions([
            "options:gamma", "options:gex_status",
            "options:gamma_explain", "options:gamma_analyze",
            *_SCHED_VIEWS.values()])
        _maybe_repaint(v["options:gamma"])
        _maybe_repaint_status(v["options:gex_status"])
        _watch_explain(v["options:gamma_explain"])
        _watch_analyze(v["options:gamma_analyze"])
        _sync_sched_btns(v)

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

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["gamma"] = bus_client.read_version("options:gamma")
    seen["explain"] = bus_client.read_version("options:gamma_explain")
    seen["analyze"] = bus_client.read_version("options:gamma_analyze")
    seen["status"] = bus_client.read_version("options:gex_status")
    _sync_sched_btns(bus_client.read_versions(list(_SCHED_VIEWS.values())))
    state["snap"] = bus_client.read("options:gamma") or None
    # Sync the dropdown to the symbol actually in the cache so a page (re)build
    # doesn't show $SPX while another symbol's data is displayed (which a later
    # refresh would then revert to $SPX). Done BEFORE wiring on_value_change.
    _set_symbol((state["snap"] or {}).get("symbol"))
    symbol_in.on_value_change(lambda e: _on_symbol_change())
    _render_view()
    _paint_status(bus_client.read("options:gex_status"))

    ui.timer(1.0, _tick)                 # countdown display (no fetch)
    ui.timer(2.0, _poll)                 # one coalesced version-poll for all 4 views
    ui.timer(120.0, _auto_refresh)       # enqueue a refresh every 120s
