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

POS_COLOR = "#66bb6a"
NEG_COLOR = "#ef5350"
SPOT_COLOR = "#ffd54f"
PRICE_LINE = "#0a1f44"          # dark navy — spot track overlaid on the heatmap
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


def _view_label(view):
    """Display label for a view (GEX→GAMMA, DEX→DELTA; others unchanged)."""
    return _VIEW_LABELS.get(view, view)


def _apply_dark(layout):
    """Inject the dark theme into a Plotly layout dict (in place); returns it.

    Sets dark paper/plot backgrounds + light font, and subtle grid/zero/line
    colors on both axes (existing axis keys like title/range are preserved)."""
    layout.setdefault("paper_bgcolor", DARK_BG)
    layout.setdefault("plot_bgcolor", DARK_BG)
    layout.setdefault("font", {"color": FONT})
    for ax in ("xaxis", "yaxis"):
        a = layout.setdefault(ax, {})
        a.setdefault("gridcolor", GRID)
        a.setdefault("zerolinecolor", "#555555")
        a.setdefault("linecolor", "#555555")
    return layout


def _darker(hexc, factor=0.55):
    """Return a darker shade of a ``#rrggbb`` color (for the beveled bar border)."""
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def line_annotations(spot, flip, walls):
    """Right-edge text labels for the reference lines (Spot / Gamma flip / walls).

    Walls are labeled by side: ``Call wall`` (strike ≥ spot, resistance) or
    ``Put wall`` (strike < spot, support). Returns a list of Plotly annotation
    dicts anchored to the right edge of the plot."""
    anns = []

    def _ann(y, text, color):
        return {"xref": "paper", "x": 1.0, "xanchor": "right",
                "yref": "y", "y": y, "yanchor": "bottom",
                "text": text, "showarrow": False,
                "font": {"color": color, "size": 10},
                "bgcolor": "rgba(0,0,0,0.45)"}

    if spot is not None:
        anns.append(_ann(spot, f"Spot {spot:g}", SPOT_COLOR))
    if flip is not None:
        anns.append(_ann(flip, f"Gamma flip {flip:g}", FLIP_COLOR))
    for w in (walls or []):
        side = "Call wall" if (spot is None or w >= spot) else "Put wall"
        anns.append(_ann(w, f"{side} {w:g}", WALL_COLOR))
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


def significant_strikes(bars, frac=0.03):
    """Strikes whose |net| ≥ frac·peak — drops near-zero edge strikes so the
    y-range crops to where the bars are actually visible (fixes GAMMA dead space).

    ``bars`` is a bars_from_gex(...) dict. Returns every strike when the peak is
    zero (nothing to crop)."""
    strikes, nets = bars.get("strikes") or [], bars.get("nets") or []
    peak = max((abs(n) for n in nets), default=0.0)
    if peak <= 0:
        return list(strikes)
    thr = peak * frac
    return [s for s, n in zip(strikes, nets) if abs(n) >= thr]


def bars_from_gex(data, spot, pct=0.02):
    """Per-strike net exposure within ±pct of spot, ascending by strike."""
    gex = (data or {}).get("gex") or {}
    lo, hi = spot * (1 - pct), spot * (1 + pct)
    strikes, nets, colors, hovers = [], [], [], []
    for strike in sorted(gex):
        if not (lo <= strike <= hi):
            continue
        cell = gex[strike] or {}
        net = cell.get("net", 0.0)
        strikes.append(strike)
        nets.append(net)
        colors.append(POS_COLOR if net >= 0 else NEG_COLOR)
        hovers.append(f"{strike:g}: net {net:,.0f} "
                      f"(C {cell.get('call', 0):,.0f} / P {cell.get('put', 0):,.0f})")
    return {"strikes": strikes, "nets": nets, "colors": colors, "hovers": hovers}


def _hline(value, color, dash=None):
    line = {"color": color, "width": 2}
    if dash:
        line["dash"] = dash
    return {"type": "line", "xref": "paper", "x0": 0, "x1": 1,
            "yref": "y", "y0": value, "y1": value, "line": line}


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
    if not strikes:
        return [spot * 0.98, spot * 1.02]
    lo, hi = min(strikes), max(strikes)
    span = hi - lo
    pad = span * pad_frac if span else max(spot * 0.002, 1.0)
    return [lo - pad, hi + pad]


def panel_flex(n_cols, full_cols=205, min_heat=0.28, max_heat=0.70):
    """(bar_weight, heat_weight) flex ratio from intraday snapshot count.

    full_cols ≈ two-minute slots in an 08:30–15:20 CT session. The heatmap
    fraction lerps min_heat→max_heat with session progress so the heatmap grows
    and the bars shrink as the day fills in; bars take the remainder."""
    p = 0.0 if full_cols <= 0 else max(0.0, min(1.0, n_cols / full_cols))
    heat = min_heat + (max_heat - min_heat) * p
    return round(1.0 - heat, 4), round(heat, 4)


def bar_figure(data, spot, view="GEX", walls=None, flip=None, pct=0.02, height=680,
               yrange=None):
    """Plotly horizontal-bar figure dict for one view (dark, beveled, labeled).

    ``yrange`` (when given) overrides the auto near-spot window — used to align
    the bar chart's strike axis with the intraday heatmap's."""
    b = bars_from_gex(data, spot, pct)
    label = _view_label(view)
    shapes = [_hline(spot, SPOT_COLOR)]
    if flip is not None:
        shapes.append(_hline(flip, FLIP_COLOR, dash="dash"))
    for w in (walls or []):
        shapes.append(_hline(w, WALL_COLOR, dash="dot"))
    layout = {
        "title": f"{label} by strike",
        "xaxis": {"title": label, "zeroline": True},
        "yaxis": {"title": "Strike",
                  "range": yrange if yrange is not None else bar_yrange(b["strikes"], spot),
                  "autorange": False},
        "shapes": shapes,
        "annotations": line_annotations(spot, flip, walls),
        "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
        "showlegend": False,
        "height": height,
        "autosize": True,
    }
    return {
        "data": [{
            "type": "bar", "orientation": "h",
            "x": b["nets"], "y": b["strikes"],
            # Beveled look: fill + a darker per-bar border.
            "marker": {"color": b["colors"],
                       "line": {"color": [_darker(c) for c in b["colors"]], "width": 1}},
            "hovertext": b["hovers"], "hoverinfo": "text",
        }],
        "layout": _apply_dark(layout),
    }


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


def heatmap_figure(rows, view="GEX", height=680, yrange=None):
    """Intraday strike×time heatmap (dark, cell separators, concise hover).

    ``yrange`` (when given) sets the Strike axis range so it aligns with the
    bar chart's near-spot window."""
    m = heatmap_matrix(rows)
    yaxis = {"title": "Strike"}
    if yrange is not None:
        yaxis["range"] = yrange
    data = [{
        "type": "heatmap", "x": m["x"], "y": m["y"], "z": m["z"],
        "colorscale": "RdYlGn", "zmid": 0,
        "xgap": 1, "ygap": 1,                       # faint cell separators
        "hovertemplate": "Strike %{y} · %{x}<br>net %{z:,.0f}<extra></extra>",
    }]
    spots = m.get("spots") or []
    if any(s is not None for s in spots):
        # Underlying price track over the session, on the shared Strike axis.
        data.append({
            "type": "scatter", "mode": "lines", "name": "Spot",
            "x": m["x"], "y": spots,
            "line": {"color": PRICE_LINE, "width": 2},
            "hovertemplate": "Spot %{y:,.2f} · %{x}<extra></extra>",
        })
    return {
        "data": data,
        "layout": _apply_dark({
            "title": f"{_view_label(view)} intraday (strike × time)",
            # automargin lets Plotly grow the bottom margin to fit the rotated,
            # dense time labels so they aren't clipped at the bottom edge.
            "xaxis": {"title": "Time", "automargin": True}, "yaxis": yaxis,
            # Lighter cell-separator mesh: the xgap/ygap reveal this colour, so a
            # mid-grey reads as a soft grid instead of the harsh near-black gaps.
            "plot_bgcolor": HEATMAP_SEP,
            "margin": {"l": 60, "r": 20, "t": 40, "b": 60},
            "height": height, "autosize": True,
            "showlegend": False,
            "hoverlabel": HOVER,
        }),
    }


def _empty_fig(height=680):
    """Minimal dark-themed empty figure for first paint / hidden state."""
    return {"data": [], "layout": _apply_dark({"height": height, "autosize": True})}


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
    """Plotly heatmap dict for the Term view (net GEX by expiry × strike).

    Strikes with all-zero net across expirations are dropped.
    """
    grid = term_grid or {}
    exps = grid.get("expirations") or []
    cells = grid.get("cells") or {}
    strikes = sorted({k for exp in exps for k, v in (cells.get(exp) or {}).items()
                      if (v or {}).get("net_gex_usd")})
    z = [[((cells.get(exp) or {}).get(s) or {}).get("net_gex_usd") for exp in exps]
         for s in strikes]
    trace = {"type": "heatmap", "x": exps, "y": strikes, "z": z,
             "colorscale": "RdYlGn", "zmid": 0,
             "xgap": 1, "ygap": 1,                      # faint cell separators
             "hovertemplate": "Strike %{y} · %{x}<br>net %{z:,.0f}<extra></extra>"}
    # Boost contrast: clamp the color scale symmetrically to a robust max so a few
    # extreme strikes don't wash out the mid-range cells.
    zmax = _robust_zmax(z)
    if zmax is not None:
        trace["zmin"], trace["zmax"] = -zmax, zmax
    return {
        "data": [trace],
        "layout": _apply_dark({
            "title": "Term structure (net GEX by expiry × strike)",
            "xaxis": {"title": "Expiration"}, "yaxis": {"title": "Strike"},
            "plot_bgcolor": HEATMAP_SEP,                 # softer cell-separator mesh
            "margin": {"l": 60, "r": 20, "t": 40, "b": 60},
            "height": 680, "autosize": True,
            "hoverlabel": HOVER,
        }),
    }


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
    # Persistent panels: the Plotly elements are created ONCE and updated in
    # place (update_figure) on every repaint — rebuilding them each time tore
    # down the canvas and caused the regeneration flicker. Message labels are
    # toggled via set_visibility. Column flex weights are set per-render from the
    # intraday snapshot count (panel_flex) so the heatmap grows / bars shrink
    # through the session.
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        chart_box = ui.column().classes("min-w-0").style("flex: 0.5 1 0%")
        with chart_box:
            chart_plot = ui.plotly(_empty_fig()).classes("w-full")
            chart_msg = ui.label("Fetch a symbol… (no snapshot yet).") \
                .classes("opacity-60 text-sm")
        heatmap_box = ui.column().classes("min-w-0").style("flex: 0.5 1 0%")
        with heatmap_box:
            heat_plot = ui.plotly(_empty_fig()).classes("w-full")
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

    def _render_view():
        """Paint the active view from the cached snapshot (no fetch, no teardown).

        The Plotly elements persist across repaints and are updated in place via
        update_figure (Plotly.react diff) so the charts don't flicker."""
        snap = state["snap"]
        pressure_box.clear()
        if not snap:
            chart_plot.set_visibility(False)
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
            chart_plot.update_figure(term_heatmap(snap.get("term") or {}))
            chart_plot.set_visibility(True)
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

        # One shared near-spot strike range so the bar chart and the intraday
        # heatmap line up vertically (axis alignment). Tight to the strikes that
        # actually have visible bars (drops near-zero edge strikes → no GAMMA
        # dead space), then widened to include the intraday spot path so the
        # heatmap's price line isn't clipped when price drifted out of that window.
        yr = bar_yrange(significant_strikes(bars_from_gex(data, view_spot)), view_spot)
        yr = union_range(yr, spot_path)
        chart_plot.update_figure(
            bar_figure(data, view_spot, view=view, walls=walls, flip=flip, yrange=yr))
        chart_plot.set_visibility(True)
        summary_lbl.text = summary_text(
            {**summary, "strike_count": data.get("strike_count")}, _view_label(view))

        if rows:
            heat_plot.update_figure(heatmap_figure(rows, view, yrange=yr))
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
                    def tile(label, val, color="#bdbdbd"):
                        with ui.card().classes("p-2"):
                            ui.label(label).classes("text-xs opacity-60")
                            ui.label(f"{val:,.0f}").classes("text-base font-bold").style(f"color:{color}")
                    tile("Net Δ now", hedge.get("net_delta_0dte") or 0)
                    tile("Projected close", hedge.get("projected_net_delta_close") or 0)
                    tile("Hedge pressure", hp, "#66bb6a" if hp >= 0 else "#ef5350")

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
        state["snap"] = bus_client.read("options:gamma") or None
        _render_view()

    def _paint_status(st):
        """Paint the collector status bar from a gex_status view dict (or None)."""
        st = st or {}
        label = st.get("status_label") or "Collector status unknown"
        color = st.get("status_color") or "#666666"
        status_lbl.text = label
        status_lbl.style(f"color:{color}")
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

    def _open_analyze_dialog(res):
        prompt = (res or {}).get("prompt") or "(no prompt)"
        with ui.dialog() as dlg, ui.card().classes("min-w-[640px]"):
            ui.label("GEX analysis prompt (SPX / SPY / QQQ)").classes("text-h6")
            ta = ui.textarea(value=prompt).props('readonly outlined input-style="min-height:55vh"').classes("w-full")
            with ui.row():
                ui.button("Copy", icon="content_copy",
                          on_click=lambda: ui.clipboard.write(ta.value)).props("flat")
                ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    @guard
    def _request_analyze():
        bus_client.request("options", {"type": "gamma_analyze"})
        ui.notify("Analyze requested…")

    @guard
    def _watch_analyze(version):
        if version is None or version == seen["analyze"]:
            return
        seen["analyze"] = version
        _open_analyze_dialog(bus_client.read("options:gamma_analyze") or {})

    @guard
    def _poll():
        # One coalesced 2s tick: read all four view versions in a single pipelined
        # round-trip (cheap :ver counters, no payload deserialize) and dispatch only
        # the views that changed — replaces four separate 2s version-poll timers.
        v = bus_client.read_versions([
            "options:gamma", "options:gex_status",
            "options:gamma_explain", "options:gamma_analyze"])
        _maybe_repaint(v["options:gamma"])
        _maybe_repaint_status(v["options:gex_status"])
        _watch_explain(v["options:gamma_explain"])
        _watch_analyze(v["options:gamma_analyze"])

    fetch_btn.on_click(_request_refresh)
    explain_btn.on_click(_request_explain)
    analyze_btn.on_click(_request_analyze)
    view_toggle.on_value_change(lambda e: _render_view())

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["gamma"] = bus_client.read_version("options:gamma")
    seen["explain"] = bus_client.read_version("options:gamma_explain")
    seen["analyze"] = bus_client.read_version("options:gamma_analyze")
    seen["status"] = bus_client.read_version("options:gex_status")
    state["snap"] = bus_client.read("options:gamma") or None
    _render_view()
    _paint_status(bus_client.read("options:gex_status"))

    ui.timer(1.0, _tick)                 # countdown display (no fetch)
    ui.timer(2.0, _poll)                 # one coalesced version-poll for all 4 views
    ui.timer(120.0, _auto_refresh)       # enqueue a refresh every 120s
