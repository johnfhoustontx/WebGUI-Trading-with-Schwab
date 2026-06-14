"""Gamma page — GEX / Charm / DEX / Vanna exposure + intraday heatmap.

Calls ``gamma_tool.GammaEngine`` (pure compute over a live option chain) and
renders horizontal bar charts + an intraday strike×time heatmap with NiceGUI
``ui.plotly``. Figure/transform builders are pure (unit-tested); ``render()``
wires the controls (Task G2/G3).
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

POS_COLOR = "#66bb6a"
NEG_COLOR = "#ef5350"
SPOT_COLOR = "#ffd54f"
FLIP_COLOR = "#42a5f5"
WALL_COLOR = "#b39ddb"


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


def bar_figure(data, spot, view="GEX", walls=None, flip=None, pct=0.02):
    """Plotly horizontal-bar figure dict for one view."""
    b = bars_from_gex(data, spot, pct)
    shapes = [_hline(spot, SPOT_COLOR)]
    if flip is not None:
        shapes.append(_hline(flip, FLIP_COLOR, dash="dash"))
    for w in (walls or []):
        shapes.append(_hline(w, WALL_COLOR, dash="dot"))
    return {
        "data": [{
            "type": "bar", "orientation": "h",
            "x": b["nets"], "y": b["strikes"],
            "marker": {"color": b["colors"]},
            "hovertext": b["hovers"], "hoverinfo": "text",
        }],
        "layout": {
            "title": f"{view} by strike",
            "xaxis": {"title": view, "zeroline": True},
            "yaxis": {"title": "Strike"},
            "shapes": shapes,
            "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
            "showlegend": False,
        },
    }


def _fmt_ts(value):
    """Format an epoch-seconds timestamp as HH:MM; pass strings through."""
    if isinstance(value, (int, float)):
        import datetime as dt
        return dt.datetime.fromtimestamp(value).strftime("%H:%M")
    return str(value)


def heatmap_matrix(rows):
    """(x=times, y=strikes, z=[y][x]) from gex_history rows.

    Each row is (ts, spot, flip, top_pos, top_neg, net_total, grid_dict) where
    grid_dict maps strike->value.
    """
    if not rows:
        return {"x": [], "y": [], "z": []}
    x = [_fmt_ts(r[0]) for r in rows]
    grids = [r[6] or {} for r in rows]
    strikes = sorted({s for g in grids for s in g})
    z = [[g.get(s) for g in grids] for s in strikes]
    return {"x": x, "y": strikes, "z": z}


def heatmap_figure(rows, view="GEX"):
    m = heatmap_matrix(rows)
    return {
        "data": [{
            "type": "heatmap", "x": m["x"], "y": m["y"], "z": m["z"],
            "colorscale": "RdYlGn", "zmid": 0,
        }],
        "layout": {
            "title": f"{view} intraday (strike × time)",
            "xaxis": {"title": "Time"}, "yaxis": {"title": "Strike"},
            "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
        },
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


def render():
    import datetime as dt

    from nicegui import run, ui

    import proxy
    import gamma_tool as gt

    ui.label("Gamma").classes("text-h5")

    state: dict = {"results": None, "spot": None, "symbol": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        symbol_in = ui.input("Symbol", value="$SPX").classes("w-28")
        fetch_btn = ui.button("Refresh now", icon="refresh")
        view_toggle = ui.toggle(list(_VIEWS), value="GEX")
        spinner = ui.spinner(size="lg")
        spinner.visible = False
        countdown_lbl = ui.label("").classes("opacity-60 text-sm")
    summary_lbl = ui.label("").classes("opacity-70 text-sm")
    pressure_box = ui.row().classes("gap-3 items-center")
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        chart_box = ui.column().classes("flex-grow min-w-0")
        heatmap_box = ui.column().classes("flex-grow min-w-0")

    def _load_history(symbol, vstr):
        import gex_history_db as gh
        try:
            conn = gh.connect(read_only=True)
            return gh.load_today_with_grid(conn, symbol, vstr)
        except Exception:
            return []

    def _walls(view, data):
        if view == "GEX":
            return gt.get_gex_walls(data, top_n=5)
        if view == "DEX":
            return gt.get_dex_walls(data, top_n=5)
        return []

    def _render_view():
        results = state["results"]
        if not results:
            return
        view = view_toggle.value
        idx, vstr = _VIEWS[view]
        data = results[idx]
        spot = data.get("spot") or state["spot"]
        summary = gt.GammaEngine().snapshot_summary(data, vstr)
        flip = summary.get("flip")
        walls = _walls(view, data)
        chart_box.clear()
        with chart_box:
            ui.plotly(bar_figure(data, spot, view=view, walls=walls, flip=flip)).classes("w-full")
        summary_lbl.text = summary_text({**summary, "strike_count": data.get("strike_count")}, view)

        rows = _load_history(state["symbol"], vstr)
        heatmap_box.clear()
        with heatmap_box:
            if rows:
                ui.plotly(heatmap_figure(rows, view)).classes("w-full")
            else:
                ui.label("No intraday snapshots yet (history collector not running).") \
                    .classes("opacity-60 text-sm")

        pressure_box.clear()
        if view == "DEX":
            with pressure_box:
                hp = data.get("hedge_pressure")
                if hp is None:
                    ui.label("0-DTE hedge pressure: n/a (nearest expiry is not 0-DTE)").classes("opacity-60 text-sm")
                else:
                    def tile(label, val, color="#bdbdbd"):
                        with ui.card().classes("p-2"):
                            ui.label(label).classes("text-xs opacity-60")
                            ui.label(f"{val:,.0f}").classes("text-base font-bold").style(f"color:{color}")
                    tile("Net Δ now", data.get("net_delta_0dte") or 0)
                    tile("Projected close", data.get("projected_net_delta_close") or 0)
                    tile("Hedge pressure", hp, "#66bb6a" if hp >= 0 else "#ef5350")

    async def do_fetch():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        fetch_btn.disable()
        spinner.visible = True
        try:
            def _f():
                resp = proxy.schwab_py_client.get_option_chain(
                    sym, contract_type="ALL", from_date=dt.date.today(),
                    to_date=dt.date.today() + dt.timedelta(days=7))
                chain = resp.json() if getattr(resp, "status_code", None) == 200 else None
                return gt.GammaEngine().calc_all_from_chain(chain) if chain else None
            results = await run.io_bound(_f)
        except Exception as exc:
            ui.notify(f"Fetch failed: {exc}", type="negative")
            return
        finally:
            spinner.visible = False
            fetch_btn.enable()
        if not results:
            ui.notify(f"No chain data for {sym}.", type="warning")
            return
        state["results"] = results
        state["spot"] = results[0].get("spot")
        state["symbol"] = sym
        state["countdown"] = 120
        _render_view()

    def _tick():
        state["countdown"] = state.get("countdown", 120) - 1
        if state["countdown"] < 0:
            state["countdown"] = 120
        countdown_lbl.text = f"Next refresh: {state['countdown'] // 60}:{state['countdown'] % 60:02d}"

    fetch_btn.on_click(do_fetch)
    view_toggle.on_value_change(lambda e: _render_view())

    state["countdown"] = 120
    ui.timer(1.0, _tick)              # countdown display
    ui.timer(120.0, do_fetch)         # auto-refresh every 120s
    ui.timer(0.1, do_fetch, once=True)  # autoload on open
