"""Sector Rotation page — RRG-vs-SPY assessment (under Sentiment).

Tier-3 reader: this page holds **no engine calls and no app ``scoring`` import**.
The assessment, S&P weights and risk threshold are computed in
``services/sentiment_svc`` (the only process where ``import scoring`` resolves to
sentiment's package rather than the options-scanner ``scoring.py`` — the
documented cross-app collision). The service caches them; this page only
**formats** them. Cache view read:

* ``sentiment:rotation`` → ``{"assessment", "weights", "risk_threshold", "error"}``
  (see ``services/sentiment_svc/handlers.refresh_rotation``).

Pure builders (``quadrant_color``, ``headline_parts``, ``side_rows``,
``rotation_rows``, ``rrg_scatter_figure``) are unit-tested. ``render()`` wires
widgets, a Refresh button that enqueues a ``cmd:sentiment`` ``refresh_rotation``
command, and a fetch-free version-poll ``ui.timer`` that repaints when the bus
cache version changes.
"""
import bus_client
from pages.ui_guard import guard, guard_async  # noqa: F401

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_CYAN = "#3fb6c7"
CLR_FLAT = "#9e9e9e"

# Fallback when the service-supplied risk threshold is absent (cold cache).
DEFAULT_RISK_THRESHOLD = 1.5

_QUAD_COLOR = {"Leading": CLR_GREEN, "Improving": CLR_CYAN,
               "Weakening": CLR_YELLOW, "Lagging": CLR_RED}


def quadrant_color(q):
    return _QUAD_COLOR.get(q, CLR_FLAT)


def _regime_color(regime):
    return {"Risk-ON": CLR_GREEN, "Risk-OFF": CLR_RED}.get(regime, CLR_YELLOW)


def headline_parts(a, risk_threshold=DEFAULT_RISK_THRESHOLD):
    """(regime, color, text, detail) from an assessment dict.

    ``risk_threshold`` arrives from the service-cached value (the engine's
    ``RISK_THRESHOLD``); it falls back to ``DEFAULT_RISK_THRESHOLD`` so the page
    never needs to import the engine."""
    h = a.get("headline") or {}
    regime = h.get("regime", "—")
    text = h.get("text", "")
    spread = h.get("spread")
    rt = risk_threshold if risk_threshold is not None else DEFAULT_RISK_THRESHOLD
    if spread is not None:
        detail = (f"cyclical RS-Mom {h.get('cyclical_mom_mean', 0):.2f} vs "
                  f"defensive {h.get('defensive_mom_mean', 0):.2f} "
                  f"(spread {spread:+.1f}; threshold ±{rt})")
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


def _hex_to_rgba(hex_color, alpha):
    """'#66bb6a' + 0.28 -> 'rgba(102, 187, 106, 0.28)'."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _sector_trace(sec):
    """One RRG trace for a sector: a faded trail line + a single bright head dot
    (the current point), labeled with the ETF. Built from the sampled tail; falls
    back to a single head point when the sector has no tail."""
    tail = sec.get("tail") or []
    color = quadrant_color(sec.get("quadrant"))
    if tail:
        xs = [p["rs_ratio"] for p in tail]
        ys = [p["rs_momentum"] for p in tail]
    else:
        r, m = sec.get("rs_ratio"), sec.get("rs_momentum")
        if r is None or m is None:
            return None
        xs, ys = [r], [m]
    n = len(xs)
    return {
        "type": "scatter", "mode": "lines+markers+text",
        "x": xs, "y": ys,
        "line": {"color": _hex_to_rgba(color, 0.4), "width": 1.6, "shape": "spline"},
        "marker": {"color": color,
                   "size": [0.0] * (n - 1) + [13],
                   "opacity": [0.0] * (n - 1) + [1.0]},
        "text": [""] * (n - 1) + [sec.get("etf") or ""],
        "textposition": "top center", "textfont": {"size": 10},
        "hovertemplate": (f"{sec.get('name')} ({sec.get('etf')}) — "
                          f"{sec.get('quadrant')}<br>RS-Ratio %{{x:.2f}} · "
                          f"RS-Mom %{{y:.2f}}<extra></extra>"),
        "showlegend": False,
    }


def _focus_opacities(n, focus, dim=0.12):
    """Trace-opacity list: 1.0 for the ``focus`` trace, ``dim`` for the rest.
    Returns all-1.0 when ``focus`` is None / out of range (restore on unhover)."""
    if focus is None or not (0 <= focus < n):
        return [1.0] * n
    return [1.0 if i == focus else dim for i in range(n)]


def rrg_scatter_figure(a):
    """Plotly RRG scatter: one trace per sector (faded trail line + a single head
    dot), 100/100 crosshair lines, hovermode closest so each sector is hoverable.
    Trace order matches the sectors order so ``curveNumber == sector index``."""
    secs = a.get("sectors") or []
    traces = [t for t in (_sector_trace(s) for s in secs) if t is not None]
    line = {"color": "rgba(255,255,255,0.25)", "width": 1}
    return {
        "data": traces,
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 8, "b": 36}, "height": 560,
            "template": "plotly_dark", "hovermode": "closest",
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


def render():
    from nicegui import ui

    state = {"ver": None}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Sector Rotation").classes("text-h6")
        ui.label("RRG vs SPY").classes("opacity-60 text-sm")
        as_of = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        ui.button("Refresh", icon="refresh",
                  on_click=lambda: _request_refresh()).props("flat dense")

    headline_lbl = ui.label("").classes("text-subtitle1 text-bold")
    detail_lbl = ui.label("").classes("opacity-70 text-sm")
    msg_lbl = ui.label("").classes("text-warning text-sm")
    cols_box = ui.row().classes("no-wrap items-start gap-8 q-mt-sm")
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

    def _render(a, weights, risk_threshold):
        regime, color, text, detail = headline_parts(a, risk_threshold)
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
                with ui.column().classes("items-start"):
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

    @guard
    def _apply():
        rot = bus_client.read("sentiment:rotation") or {}
        a = rot.get("assessment")
        weights = rot.get("weights") or {}
        rt = rot.get("risk_threshold")
        err = rot.get("error")
        if a:
            _render(a, weights, rt)
        elif err:
            as_of.text = ""
            headline_lbl.text = ""
            detail_lbl.text = ""
            msg_lbl.text = err
        else:
            as_of.text = ""
            headline_lbl.text = "Waiting for sentiment service…"
            detail_lbl.text = ""
            msg_lbl.text = ""

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_rotation"})
        ui.notify("Refresh requested")

    @guard
    def _maybe_repaint():
        # Fetch-free: repaint only when the bus cache version changes.
        ver = bus_client.read_version("sentiment:rotation")
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _apply()

    state["ver"] = bus_client.read_version("sentiment:rotation")
    _apply()
    ui.timer(2.0, _maybe_repaint)
