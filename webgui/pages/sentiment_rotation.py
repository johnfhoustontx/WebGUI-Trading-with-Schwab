"""Sector Rotation page — RRG-vs-SPY assessment (under Sentiment).

Thin NiceGUI layer over the copied ``sector_rotation_assessment`` engine.
Pure builders here are unit-tested; ``render()`` (Task 2) wires widgets.
Data is fairly static: cached module-level, manual Refresh only.
"""
import sys

from repo_paths import SENTIMENT

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

import sector_rotation_assessment as rotation_tool  # noqa: E402
from pages.ui_guard import guard_async  # noqa: E402

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_CYAN = "#3fb6c7"
CLR_FLAT = "#9e9e9e"

_QUAD_COLOR = {"Leading": CLR_GREEN, "Improving": CLR_CYAN,
               "Weakening": CLR_YELLOW, "Lagging": CLR_RED}


def quadrant_color(q):
    return _QUAD_COLOR.get(q, CLR_FLAT)


def _regime_color(regime):
    return {"Risk-ON": CLR_GREEN, "Risk-OFF": CLR_RED}.get(regime, CLR_YELLOW)


def headline_parts(a):
    """(regime, color, text, detail) from an assessment dict."""
    h = a.get("headline") or {}
    regime = h.get("regime", "—")
    text = h.get("text", "")
    spread = h.get("spread")
    if spread is not None:
        detail = (f"cyclical RS-Mom {h.get('cyclical_mom_mean', 0):.2f} vs "
                  f"defensive {h.get('defensive_mom_mean', 0):.2f} "
                  f"(spread {spread:+.1f}; threshold ±{rotation_tool.RISK_THRESHOLD})")
    else:
        detail = ""
    return regime, _regime_color(regime), text, detail


def side_rows(a, side_key, weights):
    """([{name, etf, quadrant, weight}], total_weight) for rotating_from/into."""
    rows = []
    total = 0.0
    for s in a.get(side_key) or []:
        w = float((weights or {}).get(s.get("etf"), 0.0) or 0.0)
        total += w
        rows.append({"name": s.get("name"), "etf": s.get("etf"),
                     "quadrant": s.get("quadrant"), "weight": w})
    return rows, total


def rotation_rows(a):
    """Quadrant-map rows (already rs_momentum-desc from the engine), + color."""
    out = []
    for s in a.get("sectors") or []:
        out.append({**s, "color": quadrant_color(s.get("quadrant"))})
    return out


def rrg_scatter_figure(a):
    """Plotly RRG scatter: x=RS-Ratio, y=RS-Momentum, dot per sector, 100/100 lines."""
    secs = a.get("sectors") or []
    xs = [s.get("rs_ratio") for s in secs]
    ys = [s.get("rs_momentum") for s in secs]
    colors = [quadrant_color(s.get("quadrant")) for s in secs]
    labels = [s.get("etf") for s in secs]
    line = {"color": "rgba(255,255,255,0.25)", "width": 1}
    return {
        "data": [{
            "type": "scatter", "mode": "markers+text",
            "x": xs, "y": ys, "text": labels, "textposition": "top center",
            "marker": {"size": 12, "color": colors},
            "hovertext": [f"{s.get('name')} — {s.get('quadrant')}" for s in secs],
            "hoverinfo": "text",
        }],
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 8, "b": 36}, "height": 360,
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
            "xaxis": {"title": "RS-Ratio", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "yaxis": {"title": "RS-Momentum", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "shapes": [
                {"type": "line", "xref": "x", "yref": "paper", "x0": 100, "x1": 100,
                 "y0": 0, "y1": 1, "line": line},
                {"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1,
                 "y0": 100, "y1": 100, "line": line},
            ],
        },
    }


import datetime as _dt

# Static-ish data: cache the assessment; recompute only on manual Refresh.
_ROTATION_CACHE = {"assessment": None, "at": None}


def _compute():
    """Off-thread: fetch aligned frame via the engine + build the assessment.
    Returns (assessment|None, error_str|None)."""
    symbols = [rotation_tool.BENCHMARK] + list(rotation_tool.SECTOR_ETFS)
    frame, missing = rotation_tool.build_aligned_frame(symbols)
    if frame is None:
        return None, "No data from proxy (is schwab-proxy running?)"
    a = rotation_tool.build_assessment(frame, _dt.date.today().isoformat())
    if a is None or not a.get("sectors"):
        return None, (f"Insufficient daily history (need {rotation_tool.MIN_BARS} "
                      f"aligned bars).")
    return a, None


def _sector_weights():
    import sectors_ref
    return {r["etf"]: r.get("sp_weight", 0.0)
            for r in sectors_ref.load_sectors_data()
            if r.get("kind") == "sector" and r.get("etf")}


def render():
    import nicegui.run as ng_run
    from nicegui import ui

    weights = _sector_weights()

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Sector Rotation").classes("text-h6")
        ui.label("RRG vs SPY").classes("opacity-60 text-sm")
        as_of = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        spinner = ui.spinner(size="sm"); spinner.visible = False
        ui.button("Refresh", icon="refresh", on_click=lambda: load(force=True)).props("flat dense")

    headline_lbl = ui.label("").classes("text-subtitle1 text-bold")
    detail_lbl = ui.label("").classes("opacity-70 text-sm")
    msg_lbl = ui.label("").classes("text-warning text-sm")
    cols_box = ui.row().classes("w-full no-wrap gap-6 q-mt-sm")
    # Quadrant map (left) + RRG scatter (right), side by side.
    with ui.row().classes("w-full no-wrap gap-6 items-start q-mt-md"):
        with ui.column().style("flex:1;min-width:0"):
            ui.label("Full Quadrant Map (sorted by RS-Momentum)").classes("text-subtitle2")
            table_box = ui.column().classes("w-full q-gutter-none")
        with ui.column().style("flex:1;min-width:0"):
            ui.label("RRG").classes("text-subtitle2")
            rrg_box = ui.column().classes("w-full")
    ui.label("Pairing is ordinal — strongest relative-selling vs strongest "
             "relative-buying pressure, not literal cash flow.").classes("opacity-50 text-xs q-mt-sm")

    QCOLS = [("name", "Sector", 150), ("etf", "ETF", 55), ("rs_ratio", "RS-Ratio", 90),
             ("rs_momentum", "RS-Mom", 90), ("quadrant", "Quadrant", 110),
             ("direction", "Dir", 60)]

    def _render(a):
        regime, color, text, detail = headline_parts(a)
        as_of.text = f"as of {a.get('date')}"
        headline_lbl.text = f"{regime} — {text}"
        headline_lbl.style(f"color:{color}")
        detail_lbl.text = detail
        msg_lbl.text = ""
        cols_box.clear()
        with cols_box:
            for side, title, tcolor in (("rotating_from", "ROTATING FROM", CLR_RED),
                                        ("rotating_into", "ROTATING INTO", CLR_GREEN)):
                rows, total = side_rows(a, side, weights)
                with ui.column().classes("items-start").style("flex:1"):
                    ui.label(f"{title}  ·  {total:.0f}% of S&P").style(f"color:{tcolor}").classes("text-bold text-sm")
                    for r in rows:
                        with ui.row().classes("items-center no-wrap gap-1 text-sm"):
                            ui.label(str(r["name"] or ""))
                            ui.label(f"({r['quadrant']})").style(
                                f"color:{quadrant_color(r['quadrant'])}")
                            ui.label(f"{r['weight']:.1f}%" if r['weight'] else "").classes("opacity-70")
        table_box.clear()
        with table_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                for _f, hdr, w in QCOLS:
                    ui.label(hdr).style(f"width:{w}px")
            for r in rotation_rows(a):
                with ui.row().classes("items-center w-full no-wrap gap-2 text-sm").style(f"color:{r['color']}"):
                    ui.label(str(r.get("name") or "")).style("width:150px")
                    ui.label(str(r.get("etf") or "")).style("width:55px")
                    ui.label(f"{r.get('rs_ratio'):.2f}").style("width:90px")
                    ui.label(f"{r.get('rs_momentum'):.2f}").style("width:90px")
                    ui.label(str(r.get("quadrant") or "")).style("width:110px")
                    ui.label(str(r.get("direction") or "")).style("width:60px")
        rrg_box.clear()
        with rrg_box:
            ui.plotly(rrg_scatter_figure(a)).classes("w-full")

    def _paint_cached():
        a = _ROTATION_CACHE["assessment"]
        if a:
            _render(a)
            return True
        return False

    @guard_async
    async def load(force=False):
        if not force and _paint_cached():
            return
        spinner.visible = True
        try:
            a, err = await ng_run.io_bound(_compute)
            if a:
                _ROTATION_CACHE["assessment"] = a
                _ROTATION_CACHE["at"] = _dt.datetime.now()
                _render(a)
            else:
                msg_lbl.text = err or "No rotation data."
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Rotation load failed: {e}", type="negative")
        finally:
            spinner.visible = False

    # Paint cache instantly if present; otherwise compute once (no auto-refresh).
    if not _paint_cached():
        ui.timer(0.1, lambda: load(force=True), once=True)
