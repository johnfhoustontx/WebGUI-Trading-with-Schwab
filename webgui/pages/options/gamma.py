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
            "yaxis": {"title": "Strike", "range": [spot * 0.95, spot * 1.05]},
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
                   "margin": {"l": 60, "r": 20, "t": 40, "b": 60}},
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
    import html_render

    try:
        from regime_filter import evaluate_regime
    except Exception:  # pragma: no cover
        evaluate_regime = lambda: {"active": False}  # noqa: E731

    ui.label("Gamma").classes("text-h5")

    state: dict = {"results": None, "spot": None, "symbol": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        symbol_in = ui.input("Symbol", value="$SPX").classes("w-28")
        fetch_btn = ui.button("Refresh now", icon="refresh")
        view_toggle = ui.toggle(list(_VIEWS) + ["Term"], value="GEX")
        explain_btn = ui.button("Explain", icon="help").props("outline")
        analyze_btn = ui.button("Analyze", icon="psychology").props("outline")
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
        if view == "Term":
            pressure_box.clear()
            heatmap_box.clear()
            chart_box.clear()
            tg = gt.GammaEngine().compute_term_grid(state.get("chain")) if state.get("chain") else {}
            with chart_box:
                ui.plotly(term_heatmap(tg)).classes("w-full")
            summary_lbl.text = summary_text(
                {"spot": state.get("spot"), "strike_count": None}, "Term")
            return
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
                if not chain:
                    return None
                eng = gt.GammaEngine()
                res = eng.calc_all_from_chain(chain)
                return chain, res, eng._last_dte
            fetched = await run.io_bound(_f)
            results = fetched[1] if fetched else None
            state["chain"] = fetched[0] if fetched else None
            state["dte"] = fetched[2] if fetched else None
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

    def _explain_ctx():
        gex, charm, dex, vanna = state["results"]
        try:
            regime = evaluate_regime() or {"active": False}
        except Exception:
            regime = {"active": False}
        return {
            "symbol": state["symbol"], "spot": state["spot"], "dte": state.get("dte"),
            "gex_summary": gt.GammaEngine.snapshot_summary(gex, "gex"),
            "charm_summary": gt.GammaEngine.snapshot_summary(charm, "charm"),
            "dex_summary": gt.GammaEngine.snapshot_summary(dex, "dex"),
            "sentiment": regime,
        }

    def do_explain():
        if not state.get("results"):
            ui.notify("Fetch first.", type="warning")
            return
        try:
            txt = gt.build_explain_html_text(_explain_ctx())
            html = html_render.render_explain_html(txt, None, state["symbol"])
        except Exception as exc:
            ui.notify(f"Explain failed: {exc}", type="negative")
            return
        with ui.dialog().props("maximized") as dlg, ui.card().classes("w-full h-full"):
            with ui.row().classes("justify-between w-full items-center"):
                ui.label(f"Explain — {state['symbol']}").classes("text-h6")
                with ui.row():
                    ui.button("Download", icon="download",
                              on_click=lambda: ui.download.content(html, "explain.html")).props("flat")
                    ui.button("Close", on_click=dlg.close).props("flat")
            # Render the explain document inside a white scroll panel (NiceGUI strips
            # <iframe>, so inject the HTML directly).
            with ui.element("div").classes("w-full").style(
                    "background:#fff;color:#111;max-height:85vh;overflow:auto;"
                    "padding:16px;border-radius:6px;"):
                ui.html(html)
        dlg.open()

    def _blocks_for(symbol, chain):
        eng = gt.GammaEngine()
        res = eng.calc_all_from_chain(chain)
        if not res:
            return None
        gex, charm, dex, vanna = res
        try:
            em = eng.calc_expected_move_from_chain(chain)
        except Exception:
            em = None
        dte = eng._last_dte

        def bd(snap, view):
            if not snap:
                return None
            return gt.build_analysis_dict(snap, view, symbol, dte,
                                          expected_move=em, grouping=1, chain=chain)
        return {"gex": bd(gex, "gex"), "charm": bd(charm, "charm"),
                "dex": bd(dex, "dex"), "vanna": bd(vanna, "vanna")}

    def _analyze_prompt():
        blocks = {}
        for key, sym in (("spx", "$SPX"), ("spy", "SPY"), ("qqq", "QQQ")):
            try:
                resp = proxy.schwab_py_client.get_option_chain(
                    sym, contract_type="ALL", from_date=dt.date.today(),
                    to_date=dt.date.today() + dt.timedelta(days=7))
                chain = resp.json() if getattr(resp, "status_code", None) == 200 else None
                blocks[key] = _blocks_for(sym, chain) if chain else None
            except Exception:
                blocks[key] = None
        return gt.build_summary_prompt_bundled(blocks["spx"], blocks["spy"], blocks["qqq"])

    async def do_analyze():
        analyze_btn.disable()
        spinner.visible = True
        try:
            prompt = await run.io_bound(_analyze_prompt)
        except Exception as exc:
            ui.notify(f"Analyze failed: {exc}", type="negative")
            return
        finally:
            spinner.visible = False
            analyze_btn.enable()
        with ui.dialog() as dlg, ui.card().classes("min-w-[640px]"):
            ui.label("GEX analysis prompt (SPX / SPY / QQQ)").classes("text-h6")
            ta = ui.textarea(value=prompt).props('readonly outlined input-style="min-height:55vh"').classes("w-full")
            with ui.row():
                ui.button("Copy", icon="content_copy",
                          on_click=lambda: ui.clipboard.write(ta.value)).props("flat")
                ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    fetch_btn.on_click(do_fetch)
    explain_btn.on_click(do_explain)
    analyze_btn.on_click(do_analyze)
    view_toggle.on_value_change(lambda e: _render_view())

    state["countdown"] = 120
    ui.timer(1.0, _tick)              # countdown display
    ui.timer(120.0, do_fetch)         # auto-refresh every 120s
    ui.timer(0.1, do_fetch, once=True)  # autoload on open
