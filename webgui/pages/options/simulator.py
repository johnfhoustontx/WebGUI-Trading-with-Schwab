"""Simulator page — What-if price sweep + IV-shock.

Calls the pure ``options_simulator`` engines over a fetched ChainSnapshot and
renders Plotly figures. Figure/transform builders are pure (unit-tested);
``render()`` wires the fetch + contract selector + tabs (Task S2/S3). Replay is
a deferred follow-up.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

SPOT_COLOR = "#ffd54f"
TARGET_COLOR = "#42a5f5"
BASE_COLOR = "#42a5f5"
SHOCK_COLOR = "#ffa726"


def _records(df):
    """Normalize a DataFrame or list-of-dicts to a list of dict rows."""
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return list(df or [])


def _vline(x, color, dash=None):
    line = {"color": color, "width": 2}
    if dash:
        line["dash"] = dash
    return {"type": "line", "yref": "paper", "y0": 0, "y1": 1,
            "xref": "x", "x0": x, "x1": x, "line": line}


def whatif_figure(df, spot, target_s=None):
    """Plotly curve of underlying price (S) vs position theo price."""
    rows = _records(df)
    xs = [r["S"] for r in rows]
    ys = [r["theo_price"] for r in rows]
    shapes = [
        {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y",
         "y0": 0, "y1": 0, "line": {"color": "#888", "width": 1, "dash": "dash"}},
        _vline(spot, SPOT_COLOR),
    ]
    if target_s is not None:
        shapes.append(_vline(target_s, TARGET_COLOR, dash="dash"))
    return {
        "data": [{"type": "scatter", "mode": "lines", "x": xs, "y": ys,
                  "line": {"color": "#66bb6a"}, "name": "Theo"}],
        "layout": {"title": "What-if: price sweep",
                   "xaxis": {"title": "Underlying"}, "yaxis": {"title": "Theo price"},
                   "shapes": shapes, "showlegend": False,
                   "margin": {"l": 60, "r": 20, "t": 40, "b": 40}},
    }


def ivshock_figure(base, shock, mult=1.5):
    """Grouped base-vs-shock bars across the key metrics."""
    cats = ["Price", "Delta", "Gamma×100", "Theta", "Vega"]

    def vals(row):
        return [row.get("theo_price", 0), row.get("delta", 0),
                (row.get("gamma", 0) or 0) * 100, row.get("theta", 0), row.get("vega", 0)]

    return {
        "data": [
            {"type": "bar", "name": "base (×1.0)", "x": cats, "y": vals(base),
             "marker": {"color": BASE_COLOR}},
            {"type": "bar", "name": f"shock (×{mult:g})", "x": cats, "y": vals(shock),
             "marker": {"color": SHOCK_COLOR}},
        ],
        "layout": {"title": "IV shock", "barmode": "group",
                   "xaxis": {"title": "", "categoryarray": cats},
                   "yaxis": {"title": "Value"},
                   "margin": {"l": 60, "r": 20, "t": 40, "b": 40}},
    }


def expiries_of(snapshot):
    """Sorted unique expiries (as ISO strings) in the snapshot."""
    return sorted({str(c.expiry) for c in getattr(snapshot, "contracts", []) or []})


def strikes_of(snapshot, expiry, kind):
    """Sorted unique strikes for an expiry + kind (call/put)."""
    out = {c.strike for c in getattr(snapshot, "contracts", []) or []
           if str(c.expiry) == str(expiry) and c.kind == kind}
    return sorted(out)


def find_contract(snapshot, expiry, kind, strike):
    for c in getattr(snapshot, "contracts", []) or []:
        if str(c.expiry) == str(expiry) and c.kind == kind and c.strike == strike:
            return c
    return None


def render():
    import numpy as np

    from nicegui import run, ui

    import proxy
    from options_simulator import data as sdata
    from options_simulator import engine as seng

    ui.label("Simulator").classes("text-h5")

    state: dict = {"snap": None, "contract": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        symbol_in = ui.input("Symbol", value="SPY").classes("w-28")
        fetch_btn = ui.button("Fetch snapshot", icon="download")
        spinner = ui.spinner(size="lg")
        spinner.visible = False
        status = ui.label("").classes("opacity-70 text-sm")

    with ui.row().classes("items-end gap-3 flex-wrap"):
        expiry_sel = ui.select([], label="Expiry").classes("w-40")
        kind_tog = ui.toggle(["call", "put"], value="call")
        strike_sel = ui.select([], label="Strike").classes("w-32")
        dir_tog = ui.toggle(["buy", "sell"], value="buy")

    with ui.tabs() as tabs:
        tab_whatif = ui.tab("What-if")
        tab_ivshock = ui.tab("IV shock")
    with ui.tab_panels(tabs, value=tab_whatif).classes("w-full"):
        with ui.tab_panel(tab_whatif):
            with ui.row().classes("items-center gap-4 w-full"):
                ds_lbl = ui.label("ΔS 0%")
                ds_slider = ui.slider(min=-20, max=20, value=0).classes("w-48")
                dt_lbl = ui.label("Δt 5d")
                dt_slider = ui.slider(min=0, max=30, value=5).classes("w-48")
            whatif_box = ui.column().classes("w-full")
        with ui.tab_panel(tab_ivshock):
            with ui.row().classes("items-center gap-4 w-full"):
                mult_lbl = ui.label("IV ×1.5")
                mult_slider = ui.slider(min=0.5, max=3.0, step=0.1, value=1.5).classes("w-64")
            ivshock_box = ui.column().classes("w-full")

    def _sync_strikes():
        if not state["snap"]:
            return
        strikes = strikes_of(state["snap"], expiry_sel.value, kind_tog.value)
        strike_sel.options = strikes
        if strikes and strike_sel.value not in strikes:
            strike_sel.value = min(strikes, key=lambda s: abs(s - (state["snap"].spot or 0)))
        strike_sel.update()

    def _resolve_contract():
        snap = state["snap"]
        if not snap or strike_sel.value is None:
            state["contract"] = None
            return
        state["contract"] = find_contract(snap, expiry_sel.value, kind_tog.value, strike_sel.value)

    def _render_whatif():
        snap, contract = state["snap"], state["contract"]
        ds_lbl.text = f"ΔS {ds_slider.value:+g}%"
        dt_lbl.text = f"Δt {dt_slider.value:g}d"
        whatif_box.clear()
        if not (snap and contract):
            return
        spot = snap.spot
        target_s = spot * (1 + ds_slider.value / 100.0)
        s_range = np.linspace(spot * 0.8, spot * 1.2, 81)
        pos = seng.Position.single(contract, dir_tog.value, snap.symbol)
        eng = seng.WhatIfEngine(snap)
        df = seng.aggregate_position(pos, lambda c: eng.sweep(c, s_range, float(dt_slider.value) or 0.01))
        with whatif_box:
            ui.plotly(whatif_figure(df, spot, target_s)).classes("w-full")

    def _render_ivshock():
        snap, contract = state["snap"], state["contract"]
        mult = float(mult_slider.value)
        mult_lbl.text = f"IV ×{mult:g}"
        ivshock_box.clear()
        if not (snap and contract):
            return
        pos = seng.Position.single(contract, dir_tog.value, snap.symbol)
        eng = seng.IVShockEngine(snap)
        df = seng.aggregate_position(pos, lambda c: eng.sweep(c, [1.0, mult]))
        rows = df.to_dict("records")
        if len(rows) < 2:
            return
        with ivshock_box:
            ui.plotly(ivshock_figure(rows[0], rows[1], mult)).classes("w-full")

    def _on_selection():
        _resolve_contract()
        _render_whatif()
        _render_ivshock()

    async def do_fetch():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        fetch_btn.disable()
        spinner.visible = True
        status.text = "Fetching snapshot…"
        try:
            snap = await run.io_bound(sdata.fetch_snapshot, proxy.schwab_py_client, sym)
        except Exception as exc:
            ui.notify(f"Fetch failed: {exc}", type="negative")
            status.text = "Fetch failed."
            return
        finally:
            spinner.visible = False
            fetch_btn.enable()
        state["snap"] = snap
        exps = expiries_of(snap)
        expiry_sel.options = exps
        expiry_sel.value = exps[0] if exps else None
        expiry_sel.update()
        status.text = f"{snap.symbol} spot {snap.spot:,.2f} · {len(snap.contracts)} contracts"
        _sync_strikes()
        _on_selection()

    fetch_btn.on_click(do_fetch)
    expiry_sel.on_value_change(lambda e: (_sync_strikes(), _on_selection()))
    kind_tog.on_value_change(lambda e: (_sync_strikes(), _on_selection()))
    strike_sel.on_value_change(lambda e: _on_selection())
    dir_tog.on_value_change(lambda e: _on_selection())
    ds_slider.on_value_change(lambda e: _render_whatif())
    dt_slider.on_value_change(lambda e: _render_whatif())
    mult_slider.on_value_change(lambda e: _render_ivshock())
