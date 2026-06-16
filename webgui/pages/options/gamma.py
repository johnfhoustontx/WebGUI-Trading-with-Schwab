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

from .inputs import select_all_on_focus

POS_COLOR = "#66bb6a"
NEG_COLOR = "#ef5350"
SPOT_COLOR = "#ffd54f"
FLIP_COLOR = "#42a5f5"
WALL_COLOR = "#b39ddb"


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


def bar_figure(data, spot, view="GEX", walls=None, flip=None, pct=0.02, height=680):
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
            "yaxis": {"title": "Strike", "range": bar_yrange(b["strikes"], spot),
                      "autorange": False},
            "shapes": shapes,
            "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
            "showlegend": False,
            "height": height,
            "autosize": True,
        },
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
        return {"x": [], "y": [], "z": []}
    x = [_fmt_ts(r[0]) for r in rows]
    grids = [r[6] or {} for r in rows]
    strikes = sorted({s for g in grids for s, cell in g.items() if _cell_net(cell)})
    z = [[_cell_net(g.get(s) or {}) for g in grids] for s in strikes]
    return {"x": x, "y": strikes, "z": z}


def heatmap_figure(rows, view="GEX", height=680):
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
            "height": height, "autosize": True,
        },
    }


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
    return {
        "data": [{"type": "heatmap", "x": exps, "y": strikes, "z": z,
                  "colorscale": "RdYlGn", "zmid": 0}],
        "layout": {"title": "Term structure (net GEX by expiry × strike)",
                   "xaxis": {"title": "Expiration"}, "yaxis": {"title": "Strike"},
                   "margin": {"l": 60, "r": 20, "t": 40, "b": 60},
                   "height": 680, "autosize": True},
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
    import bus_client
    from nicegui import ui

    ui.add_css(EXPLAIN_CSS)  # scoped styles for the Explain dialog (ui.html strips <style>)
    ui.label("Gamma").classes("text-h5")

    # state["snap"] is the cached snapshot from the bus (None until first read).
    state: dict = {"snap": None, "countdown": 120}
    # Last-seen bus cache versions for the fetch-free repaint/dialog timers.
    seen = {"gamma": None, "explain": None, "analyze": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="$SPX").classes("w-28"))
        fetch_btn = ui.button("Refresh now", icon="refresh")
        view_toggle = ui.toggle(list(_VIEWS) + ["Term"], value="GEX")
        explain_btn = ui.button("Explain", icon="help").props("outline")
        analyze_btn = ui.button("Analyze", icon="psychology").props("outline")
        countdown_lbl = ui.label("").classes("opacity-60 text-sm")
    summary_lbl = ui.label("").classes("opacity-70 text-sm")
    pressure_box = ui.row().classes("gap-3 items-center")
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        chart_box = ui.column().classes("flex-grow min-w-0")
        heatmap_box = ui.column().classes("flex-grow min-w-0")

    def _current_symbol():
        return (symbol_in.value or "").strip().upper()

    def _render_view():
        """Paint the active view from the cached snapshot (no fetch)."""
        snap = state["snap"]
        chart_box.clear()
        heatmap_box.clear()
        pressure_box.clear()
        if not snap:
            with chart_box:
                ui.label("Fetch a symbol… (no snapshot yet).").classes("opacity-60 text-sm")
            summary_lbl.text = ""
            return

        view = view_toggle.value
        spot = snap.get("spot")
        if view == "Term":
            with chart_box:
                ui.plotly(term_heatmap(snap.get("term") or {})).classes("w-full")
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

        with chart_box:
            ui.plotly(bar_figure(data, view_spot, view=view, walls=walls, flip=flip)).classes("w-full")
        summary_lbl.text = summary_text(
            {**summary, "strike_count": data.get("strike_count")}, view)

        # History rows: index-6 grid dict needs its keys re-floated too.
        rows = []
        for r in (entry.get("history") or []):
            r = list(r)
            if len(r) > 6:
                r[6] = _refloat_keys(r[6])
            rows.append(tuple(r))
        with heatmap_box:
            if rows:
                ui.plotly(heatmap_figure(rows, view)).classes("w-full")
            else:
                ui.label("No intraday snapshots yet (history collector not running).") \
                    .classes("opacity-60 text-sm")

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
    def _maybe_repaint():
        # Fetch-free: re-read + repaint only when the bus cache version changes
        # (the service bumps it when a requested gamma_refresh finishes).
        version = bus_client.read_version("options:gamma")
        if version == seen["gamma"]:
            return
        seen["gamma"] = version
        state["snap"] = bus_client.read("options:gamma") or None
        _render_view()

    def _open_explain_dialog(res):
        symbol = (res or {}).get("symbol") or _current_symbol()
        body = (res or {}).get("body") or "<p>No explain data.</p>"
        fragment = wrap_explain(symbol, body, full=False)
        document = wrap_explain(symbol, body, full=True)
        with ui.dialog().props("maximized") as dlg, ui.card().classes("w-full h-full"):
            with ui.row().classes("justify-between w-full items-center"):
                ui.label(f"Explain — {symbol}").classes("text-h6")
                with ui.row():
                    ui.button("Download", icon="download",
                              on_click=lambda: ui.download.content(document, "explain.html")).props("flat")
                    ui.button("Close", on_click=dlg.close).props("flat")
            with ui.element("div").classes("w-full").style(
                    "background:#1b1b1b;max-height:85vh;overflow:auto;"
                    "padding:20px;border-radius:6px;"):
                ui.html(fragment)
        dlg.open()

    @guard
    def _request_explain():
        sym = _current_symbol()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        bus_client.request("options", {"type": "gamma_explain", "args": {"symbol": sym}})
        ui.notify("Explain requested…")

    @guard
    def _watch_explain():
        # The initial version was captured at render time, so any change here is a
        # fresh, user-requested result → open the dialog.
        version = bus_client.read_version("options:gamma_explain")
        if version is None or version == seen["explain"]:
            return
        seen["explain"] = version
        _open_explain_dialog(bus_client.read("options:gamma_explain") or {})

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
    def _watch_analyze():
        version = bus_client.read_version("options:gamma_analyze")
        if version is None or version == seen["analyze"]:
            return
        seen["analyze"] = version
        _open_analyze_dialog(bus_client.read("options:gamma_analyze") or {})

    fetch_btn.on_click(_request_refresh)
    explain_btn.on_click(_request_explain)
    analyze_btn.on_click(_request_analyze)
    view_toggle.on_value_change(lambda e: _render_view())

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["gamma"] = bus_client.read_version("options:gamma")
    seen["explain"] = bus_client.read_version("options:gamma_explain")
    seen["analyze"] = bus_client.read_version("options:gamma_analyze")
    state["snap"] = bus_client.read("options:gamma") or None
    _render_view()

    ui.timer(1.0, _tick)                 # countdown display (no fetch)
    ui.timer(2.0, _maybe_repaint)        # version-poll repaint from cache
    ui.timer(2.0, _watch_explain)        # open Explain dialog on new result
    ui.timer(2.0, _watch_analyze)        # open Analyze dialog on new result
    ui.timer(120.0, _auto_refresh)       # enqueue a refresh every 120s
