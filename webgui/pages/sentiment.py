"""Sentiment page — composite gauge + components + 30d history + trend regime.

Tier-3 reader: this page holds **no engine calls, no refresh loop, and no app
``scoring``/``live_composite`` imports**. All scoring-derived values (component
weights, size/bias/signal band, velocity, divergence, trend regime, cap-weighted
sector pct/score) are computed in ``services/sentiment_svc`` — the only process
where ``import scoring`` resolves to sentiment's package rather than the
options-scanner ``scoring.py`` (the documented cross-app collision). The service
places them in the cache; this page only **formats** them. Cache views read:

* ``sentiment:composite`` → ``{"live", "composite_at", "proxy_up", "derived"}``
  where ``derived`` = ``{"weights", "size", "bias", "signal", "velocity",
  "divergence", "trend"}`` (see ``compute.derive_composite_extras``).
* ``sentiment:history``   → ``{"snaps", "spy"}``
* ``sentiment:sectors``   → ``{"sector", "industries", "sector_at", "summary"}``
  where ``summary`` = ``{"wpct", "score"}`` (see ``compute.derive_sector_summary``).

The pure display transforms (``traffic_color``, ``composite_series`` for the
history figure, table/figure builders, …) are unit-tested. ``render()`` wires
widgets, a Refresh button that enqueues a ``cmd:sentiment`` command, and a
fetch-free version-poll ``ui.timer`` that repaints when the bus cache version
changes.
"""
import bus_client
from pages.ui_guard import guard, guard_async

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_FLAT = "#9e9e9e"
CLR_CYAN = "#3fb6c7"
LINE_COLOR = "#42a5f5"

# (component_scores key, display name). Weights are NO LONGER baked from app
# ``scoring`` at import — they arrive at render time via the cached
# ``derived["weights"]`` (computed in the service). A component with no weight
# in that dict (or weight 0) is treated as out-of-composite (e.g. credit_pulse).
COMPONENTS = [
    ("vix_complex", "VIX Complex"),
    ("put_call",    "Put/Call (sectors)"),
    ("breadth",     "Market Breadth"),
    ("rotation",    "Rotation"),
    ("sector_perf", "Sector Performance"),
    ("credit_pulse", "Credit Pulse"),
]


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def traffic_color(total):
    """Composite traffic-light band for tile backgrounds.
    >=6.5 green, <=4.5 red, else amber. Mirrors source _update_metric_card_colors."""
    v = _safe_float(total, 5.0)
    if v >= 6.5:
        return CLR_GREEN
    if v <= 4.5:
        return CLR_RED
    return CLR_YELLOW


def gauge_score(total):
    """0-10 composite -> 0-100 for the svg speedometer."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# Market Trend regime -> speedometer anchor (0-100 "bullishness").
_TREND_ANCHORS = {"bull_trend": 85.0, "pullback_in_bull": 65.0, "range": 50.0,
                  "bear_rally": 35.0, "bear_trend": 15.0}

# Short dial captions (the full label shows beneath the gauge).
_TREND_SHORT = {"bull_trend": "BULL", "pullback_in_bull": "PULLBACK",
                "range": "RANGE", "bear_rally": "BEAR RALLY", "bear_trend": "BEAR"}


def trend_gauge_value(trend):
    """0-100 needle for the Market Trend speedometer (hybrid).

    Anchored by regime so the needle stays in the matching color zone
    (bear=red … bull=green), then nudged within the band by the 200d slope and
    drawdown so it reflects strength: ``anchor + clamp(slope%*50, ±8) +
    clamp(dd%*0.3, ±5)``, clamped to [0,100]. Unknown/missing trend -> 50."""
    t = trend or {}
    anchor = _TREND_ANCHORS.get(t.get("state"), 50.0)
    nudge = (_clamp(_safe_float(t.get("sma_200_slope_pct")) * 50.0, -8.0, 8.0)
             + _clamp(_safe_float(t.get("drawdown_pct")) * 0.3, -5.0, 5.0))
    return _clamp(anchor + nudge, 0.0, 100.0)


def bias_color(bias):
    b = (bias or "").lower()
    if "bull" in b:
        return CLR_GREEN
    if "bear" in b:
        return CLR_RED
    return CLR_YELLOW


def composite_series(snapshots):
    """(dates, scores) for snapshots with a positive composite total."""
    dates, scores = [], []
    for s in snapshots:
        v = _safe_float((s.get("composite") or {}).get("total_score"))
        if v > 0:
            dates.append(s.get("date"))
            scores.append(v)
    return dates, scores


def build_history_figure(snapshots):
    """Plotly fig dict: composite over time."""
    dates, scores = composite_series(snapshots)
    return {
        "data": [{
            "type": "scatter", "mode": "lines+markers",
            "x": dates, "y": scores,
            "line": {"color": LINE_COLOR, "width": 2},
            "name": "Composite",
        }],
        "layout": {
            "margin": {"l": 36, "r": 12, "t": 8, "b": 28},
            "height": 220,
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "xaxis": {"gridcolor": "rgba(255,255,255,0.06)", "zeroline": False,
                      "linecolor": "rgba(255,255,255,0.15)", "nticks": 6},
            "yaxis": {"range": [0, 10], "title": "Composite",
                      "gridcolor": "rgba(255,255,255,0.06)", "zeroline": False,
                      "linecolor": "rgba(255,255,255,0.15)"},
        },
    }


def pct_color(pct):
    """Green up / red down / gray flat (|pct| < 0.05)."""
    if pct is None or abs(float(pct)) < 0.05:
        return CLR_FLAT
    return CLR_GREEN if float(pct) > 0 else CLR_RED


def pcr_color(pcr):
    """<0.95 call-dominated green, >1.05 put-dominated red, else flat."""
    if pcr is None or float(pcr) <= 0:
        return CLR_FLAT
    if float(pcr) < 0.95:
        return CLR_GREEN
    if float(pcr) > 1.05:
        return CLR_RED
    return CLR_FLAT


def rrg_color(quadrant):
    return {
        "Leading": CLR_GREEN, "Improving": CLR_CYAN,
        "Weakening": CLR_YELLOW, "Lagging": CLR_RED,
    }.get(quadrant, CLR_FLAT)


def pcr_from_chain(chain):
    """Sum put vs call totalVolume from a Schwab /chains payload -> ratio.
    Returns None when no chain or zero call volume. Pure transform retained
    for display/test parity (chain fetching itself lives in the service)."""
    if not chain:
        return None
    pv = cv = 0
    for strikes in (chain.get("putExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    pv += v
    for strikes in (chain.get("callExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    cv += v
    return round(pv / cv, 3) if cv > 0 else None


def _pct_change_n(closes, n):
    """%-change from n sessions ago to last close, or None."""
    if not closes or len(closes) < n + 1:
        return None
    prev = float(closes[-(n + 1)])
    last = float(closes[-1])
    if prev == 0:
        return None
    return (last - prev) / prev * 100.0


def week_month_from_closes(closes):
    """(day3_pct, week_pct, month_pct) from a daily-close list (n=3/5/21)."""
    return (_pct_change_n(closes, 3),
            _pct_change_n(closes, 5),
            _pct_change_n(closes, 21))


def sector_table_rows(sector_data, quotes, trends, pcr, quadrants):
    """Build display rows for the sectors, sorted by Day % desc (None last)."""
    rows = []
    for r in sector_data:
        if r.get("kind") != "sector":
            continue
        etf = r.get("etf")
        q = (quotes or {}).get(etf) or {}
        t = (trends or {}).get(etf) or {}
        rows.append({
            "sector": r.get("sector") or r.get("label"),
            "etf": etf,
            "desc": r.get("name") or "",
            "day": q.get("change_pct"),
            "week": t.get("week_pct"),
            "month": t.get("month_pct"),
            "pcr": (pcr or {}).get(etf),
            "rrg": (quadrants or {}).get(etf),
        })
    rows.sort(key=lambda r: (r["day"] is None, -(r["day"] or 0.0)))
    return rows


def sector_summary(sector_data, quotes, summary=None):
    """'{pct_up}% green | Cap-wtd {wpct} | Score {score}/10' (mirrors source).

    ``pct_up`` is computed here from quotes (pure). The cap-weighted pct and
    sector score come from the service-computed ``summary`` dict
    (``{"wpct", "score"}``); when absent (cold cache) they render as '—'/0.0."""
    pcts = []
    for r in sector_data:
        if r.get("kind") != "sector":
            continue
        q = (quotes or {}).get(r.get("etf")) or {}
        p = q.get("change_pct")
        if p is not None:
            pcts.append(p)
    if not pcts:
        return "No sector data returned"
    pct_up = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    summary = summary or {}
    wpct = summary.get("wpct")
    wpct_str = f"{wpct:+.2f}%" if wpct is not None else "—"
    score = _safe_float(summary.get("score"))
    return f"{pct_up:.0f}% green | Cap-wtd {wpct_str} | Score {score:.1f}/10"


def rotation_banner(rot):
    """(regime, color, detail) from a compute_rotation() dict (or None).
    Mirrors source _update_rotation_banner: day -> 3d -> week fallback."""
    if not rot:
        return "—", CLR_FLAT, "Refresh sector data to compute rotation"
    if rot.get("day_spread") is not None:
        tf, spread = "day", rot["day_spread"]
    elif rot.get("3d_spread") is not None:
        tf, spread = "3d", rot["3d_spread"]
    elif rot.get("week_spread") is not None:
        tf, spread = "week", rot["week_spread"]
    else:
        return "—", CLR_FLAT, "Refresh sector data to compute rotation"
    if spread >= 1.0:
        regime, color = "STRONG RISK-ON", CLR_GREEN
    elif spread >= 0.3:
        regime, color = "RISK-ON", CLR_GREEN
    elif spread <= -1.0:
        regime, color = "STRONG RISK-OFF", CLR_RED
    elif spread <= -0.3:
        regime, color = "RISK-OFF", CLR_RED
    else:
        regime, color = "MIXED", CLR_YELLOW
    cyc, dfn = rot.get(f"{tf}_cyc"), rot.get(f"{tf}_def")
    top = rot.get(f"{tf}_top3") or []
    bot = rot.get(f"{tf}_bot3") or []
    cyc_s = f"{cyc:+.2f}%" if cyc is not None else "—"
    def_s = f"{dfn:+.2f}%" if dfn is not None else "—"
    detail = (f"{tf.upper()}: Cyc {cyc_s} vs Def {def_s} (spread {spread:+.2f}%)"
              f"  ▲ {', '.join(top[:2]) or '—'}  ▼ {', '.join(bot[-2:]) or '—'}")
    return regime, color, detail


def industry_rows(sector_data, sector_name, ind_quotes, ind_trends, ind_pcr=None, ind_quadrants=None):
    """Indented rows for a sector's industries: day/week/month % + pcr/rrg."""
    rows = []
    for r in sector_data:
        if r.get("kind") != "industry" or r.get("sector") != sector_name:
            continue
        etf = r.get("etf")
        if not (etf and etf != "n/a" and len(str(etf)) <= 6):
            continue
        q = (ind_quotes or {}).get(etf) or {}
        t = (ind_trends or {}).get(etf) or {}
        rows.append({
            "label": r.get("label") or etf,
            "etf": etf,
            "desc": r.get("name") or "",
            "day": q.get("change_pct"),
            "week": t.get("week_pct"),
            "month": t.get("month_pct"),
            "pcr": (ind_pcr or {}).get(etf),
            "rrg": (ind_quadrants or {}).get(etf),
            "is_industry": True,
        })
    return rows


def is_rth(now):
    """True if `now` (a tz-aware America/Chicago datetime) is within regular
    trading hours (Mon–Fri 08:30–15:00 CT)."""
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return (8, 30) <= hm < (15, 0)


def component_table_rows(snapshot, weights=None, rotation_value=None, sector_value=None):
    """Rows for the in-composite components: name/value/score/weight/conf/contrib.
    Scores/confs come from the snapshot so Contrib reconciles to the composite.

    ``weights`` is the service-computed ``derived["weights"]`` dict (component
    key -> weight). A component absent from it (or weight 0/None) is treated as
    out-of-composite and skipped (e.g. credit_pulse). When ``weights`` is None
    (cold cache) no rows are produced."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
    weights = weights or {}
    value_src = {
        "vix_complex": (snapshot.get("volatility") or {}).get("interpretation"),
        "put_call": (snapshot.get("options") or {}).get("pc_equity"),
        "breadth": (snapshot.get("breadth") or {}).get("interpretation"),
        # Rotation Value is the dual-momentum "Cyc rank …" string once the
        # sector load completes; before that show "—" rather than the
        # snapshot's raw-float interp ("Day 5.3064999… · …").
        "rotation": rotation_value or "—",
        "sector_perf": sector_value,
    }
    rows = []
    for key, name in COMPONENTS:
        w = weights.get(key)
        if not w:                      # skip out-of-composite (credit_pulse)
            continue
        s = _safe_float(scores.get(key))
        c = _safe_float(confs.get(key))
        rows.append({
            "key": key, "name": name,
            "value": value_src.get(key) or "—",
            "score": s,
            "weight": f"{int(w * 100)}%",
            "conf": f"{int(c * 100)}%",
            "contrib": w * s * c,
        })
    return rows


def tiles(latest, prev_total, band=None):
    """Signal tiles. ``band`` = service-computed ``(size, bias, signal)`` from
    ``derived`` (size_modifier/bias/signal); when absent, those three show '—'."""
    comp = latest.get("composite") or {}
    total = _safe_float(comp.get("total_score"))
    if band:
        size, bias, signal = band
    else:
        size, bias, signal = "—", "—", "—"
    if prev_total is None:
        yest, change = "—", "—"
    else:
        yest = f"{_safe_float(prev_total):.2f}"
        change = f"{total - _safe_float(prev_total):+.2f}"
    return {"modifier": size, "bias": bias, "signal": signal,
            "yesterday": yest, "change": change}


def rolling_averages(prior_scores):
    """(a5, a20, label) — Rising/Falling/Stable from 5d vs 20d means."""
    s = [x for x in prior_scores if x and x > 0]
    if not s:
        return 0.0, 0.0, "Stable"
    a5 = sum(s[-5:]) / len(s[-5:])
    a20 = sum(s[-20:]) / len(s[-20:])
    label = "Rising" if a5 > a20 + 0.3 else ("Falling" if a5 < a20 - 0.3 else "Stable")
    return round(a5, 2), round(a20, 2), label


def _parse_iso(value):
    """Parse an ISO timestamp string -> datetime, or None. Tolerant of a
    trailing 'Z' and of already-datetime inputs (returns them unchanged)."""
    if value is None:
        return None
    from datetime import datetime
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _fmt_time(value):
    """ISO timestamp (or datetime) -> local 'HH:MM:SS', or '' on failure."""
    dt = _parse_iso(value)
    if dt is None:
        return ""
    try:
        from datetime import timezone
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt.strftime("%H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


def render():
    from nicegui import ui

    ui.add_css('''
    .sent-sectors .secrow { border-bottom: 1px solid rgba(255,255,255,0.05); }
    .sent-sectors .secrow:hover { background: rgba(255,255,255,0.04); }
    .sent-sectors .secrow > div { border-right: 1px solid rgba(255,255,255,0.04); }
    .sent-sectors .secrow > div:last-child { border-right: none; }
    .sent-sectors .indrow { background: rgba(255,255,255,0.02); }
    ''')

    from pages.options.svg import speedometer_svg

    def _read_cache():
        """Pull the three sentiment cache views off the bus into ``state``.
        Graceful-empty: any missing view yields empty data (page renders a
        waiting placeholder rather than crashing)."""
        composite = bus_client.read("sentiment:composite") or {}
        history = bus_client.read("sentiment:history") or {}
        sectors = bus_client.read("sentiment:sectors") or {}
        state["live"] = composite.get("live")
        state["composite_at"] = composite.get("composite_at")
        state["proxy_up"] = composite.get("proxy_up")
        # Scoring-derived values (weights/size/bias/signal/velocity/divergence/
        # trend) computed in the service; the page only formats them.
        state["derived"] = composite.get("derived") or {}
        state["snaps"] = history.get("snaps") or []
        state["spy"] = history.get("spy") or []
        state["sector"] = sectors.get("sector")
        state["industries"] = sectors.get("industries") or {}
        state["sector_at"] = sectors.get("sector_at")
        # Cap-weighted sector pct/score, computed in the service.
        state["sector_summary"] = sectors.get("summary") or {}

    # Page-local UI state (per render closure; expanded set is page-local, not
    # module-global, per webgui conventions).
    state = {
        "snaps": [], "spy": [], "sector": None, "live": None,
        "industries": {}, "expanded": set(), "derived": {}, "sector_summary": {},
        "composite_at": None, "sector_at": None, "proxy_up": None,
        # last-seen bus cache versions for the fetch-free repaint timer
        "comp_ver": None, "sec_ver": None,
    }
    _read_cache()
    state["comp_ver"] = bus_client.read_version("sentiment:composite")
    state["sec_ver"] = bus_client.read_version("sentiment:sectors")

    # Top bar: "as of …" date + a small 3D refresh button, right-aligned. The
    # section titles now live per-column (all the same h6 size) below.
    with ui.row().classes("items-center w-full"):
        ui.space()
        date_lbl = ui.label("").classes("opacity-70 text-sm")
        ui.button(icon="refresh", on_click=lambda: _request_refresh()) \
            .props("round dense push color=primary size=sm").classes("q-ml-sm")

    tile_lbls, tile_cards = {}, {}
    # 2x2 signal matrix (Modifier dropped per design).
    TILE_DEFS = [("bias", "BIAS"), ("signal", "SIGNAL"),
                 ("yesterday", "YESTERDAY"), ("change", "CHANGE")]
    # Three evenly-distributed, top-aligned columns with matching h6 headers.
    with ui.row().classes("w-full items-start justify-around gap-6 flex-wrap"):
        # ① Market Sentiment — composite speedometer + press-and-hold Components popup
        with ui.column().classes("items-center").style("min-width:210px"):
            ui.label("Market Sentiment").classes("text-h6")
            gauge_box = ui.html("").classes("q-mt-sm")
            bias_lbl = ui.label("").classes("text-h6")
            sub_lbl = ui.label("").classes("opacity-80 text-sm")
            with ui.button("Components", icon="table_view").props("flat dense") as comp_btn:
                with ui.menu().props("no-parent-event") as comp_menu:
                    comp_box = ui.column().classes("q-pa-md").style("min-width:520px")
            # Press-and-hold: shown while the mouse button is down, closed on release.
            comp_btn.on("mousedown", lambda: comp_menu.open())
            comp_btn.on("mouseup", lambda: comp_menu.close())
            comp_btn.on("mouseleave", lambda: comp_menu.close())
        # ② Market Trend — speedometer (hybrid needle) + label/desc + detail popup
        with ui.column().classes("items-center").style("min-width:210px"):
            ui.label("Market Trend").classes("text-h6")
            trend_gauge_box = ui.html("").classes("q-mt-sm")
            regime_badge = ui.label("").classes("text-subtitle1 text-bold")
            regime_desc = ui.label("").classes("opacity-80 text-sm text-center")
            with ui.button("Trend Detail", icon="insights").props("flat dense") as trend_btn:
                with ui.menu().props("no-parent-event") as trend_menu:
                    regime_detail = ui.label("").classes("q-pa-md text-sm") \
                        .style("max-width:360px")
            trend_btn.on("mousedown", lambda: trend_menu.open())
            trend_btn.on("mouseup", lambda: trend_menu.close())
            trend_btn.on("mouseleave", lambda: trend_menu.close())
        # ③ Signals — 2x2 matrix
        with ui.column().classes("items-center").style("min-width:210px"):
            ui.label("Signals").classes("text-h6")
            with ui.grid(columns=2).classes("gap-2 q-mt-sm"):
                for tkey, tlabel in TILE_DEFS:
                    c = ui.card().classes("q-pa-sm items-center").style("min-width:96px")
                    with c:
                        ui.label(tlabel).classes("text-xs").style("color:#111")
                        tile_lbls[tkey] = ui.label("—").classes("text-bold").style("color:#111")
                    tile_cards[tkey] = c

    ui.separator().classes("q-my-md")
    # 30-Day History — collapsible, collapsed by default.
    with ui.expansion("30-Day History", icon="show_chart", value=False).classes("w-full"):
        hist_plot = ui.plotly(build_history_figure([])).classes("w-full")
        roll_lbl = ui.label("").classes("opacity-70 text-sm")
        vel_lbl = ui.label("").classes("opacity-80 text-sm")
        flag_lbl = ui.label("").classes("text-negative text-sm")
        div_lbl = ui.label("").classes("text-warning text-sm")

    # Sector & Industry Performance
    ui.separator().classes("q-my-md")
    ui.label("Sector & Industry Performance").classes("text-subtitle1")
    with ui.row().classes("items-center gap-3 w-full"):
        ui.button("Refresh", icon="refresh",
                  on_click=lambda: _request_refresh()).props("flat dense")
        ui.button("Expand All", on_click=lambda: _expand_all()).props("flat dense")
        ui.button("Collapse All", on_click=lambda: _collapse_all()).props("flat dense")
        summary_lbl = ui.label("").classes("opacity-80 text-sm")
    rotation_lbl = ui.label("").classes("text-sm")
    sector_box = ui.column().classes("w-full q-gutter-none q-mt-sm sent-sectors")

    SEC_COLS = [("sector", "Sector", 140), ("etf", "ETF", 50),
                ("desc", "Description", 200), ("day", "Day %", 70),
                ("week", "Week %", 70), ("month", "Month %", 70),
                ("pcr", "P/C", 56), ("rrg", "RRG", 90)]

    def _render_components(latest, rotation_value=None, sector_value=None):
        comp_box.clear()
        weights = (state.get("derived") or {}).get("weights")
        rows = component_table_rows(latest, weights, rotation_value, sector_value)
        with comp_box:
            with ui.row().classes("items-center w-full no-wrap gap-3 opacity-60 text-xs"):
                ui.label("Component").style("width:110px")
                ui.label("Value").style("width:140px")
                ui.label("Score").style("width:50px")
                ui.label("Weight").style("width:60px")
                ui.label("Conf").style("width:50px")
            for r in rows:
                sc = r["score"]
                sc_color = (CLR_GREEN if sc >= 7 else
                            (CLR_RED if sc < 4 else CLR_YELLOW))
                with ui.row().classes("items-center w-full no-wrap gap-3"):
                    ui.label(r["name"]).classes("text-sm").style("width:110px")
                    ui.label(str(r["value"])).classes("text-sm").style(
                        "width:140px;overflow:hidden;text-overflow:ellipsis;"
                        "white-space:nowrap")
                    ui.label(f"{sc:.2f}").classes("text-sm text-bold").style(
                        f"width:50px;color:{sc_color}")
                    ui.label(r["weight"]).classes("text-sm").style("width:60px")
                    ui.label(r["conf"]).classes("text-sm").style("width:50px")

    def _comp_context():
        """(rotation_value, sector_value) from loaded sector data, or (None, None).
        ``sector_value`` (cap-weighted pct) comes from the service-computed
        sector summary; ``rotation_value`` is the dual-momentum interp string."""
        sec = state["sector"]
        if not sec:
            return None, None
        rotation_value = (sec.get("dual") or {}).get("interp")
        wpct = (state.get("sector_summary") or {}).get("wpct")
        sector_value = f"{wpct:+.2f}%" if wpct is not None else None
        return rotation_value, sector_value

    def _apply():
        live = state.get("live")
        snaps = state["snaps"]
        if not live and not snaps:
            bias_lbl.text = "Waiting for sentiment service…"
            date_lbl.text = ""
            return
        latest = live or snaps[-1]
        comp = latest.get("composite") or {}
        total = _safe_float(comp.get("total_score"))
        if live:
            from datetime import datetime as _dt2
            from zoneinfo import ZoneInfo as _ZI
            _rth = is_rth(_dt2.now(_ZI("America/Chicago")))
            date_lbl.text = (f"as of {latest.get('date')} (live intraday)" if _rth
                             else f"as of {latest.get('date')} (latest — market closed)")
        else:
            date_lbl.text = f"as of {latest.get('date')} (last completed session)"
        gauge_box.content = speedometer_svg(gauge_score(total), comp.get("bias", ""),
                                            width=200, height=130)
        bias_lbl.text = f"{total:.2f} · {comp.get('bias', '')}"
        bias_lbl.style(f"color:{bias_color(comp.get('bias'))}")
        sub_lbl.text = f"Confidence {_safe_float(comp.get('aggregate_confidence')):.0%}"
        # Prior series: when showing live, today=live and the prior series is
        # the full backfill (all completed sessions); when showing backfill,
        # exclude the last (it's "today").
        prior_scores = (composite_series(snaps)[1] if live
                        else composite_series(snaps[:-1])[1])
        prev_total = prior_scores[-1] if prior_scores else None
        derived = state.get("derived") or {}
        band_labels = None
        if derived.get("size") is not None:
            band_labels = (derived.get("size", "—"), derived.get("bias", "—"),
                           derived.get("signal", "—"))
        t = tiles(latest, prev_total, band_labels)
        band = traffic_color(total)
        for tkey, _tlabel in TILE_DEFS:
            tile_lbls[tkey].text = t[tkey]
            tile_cards[tkey].style(f"background-color:{band}")
        rotation_value, sector_value = _comp_context()
        _render_components(latest, rotation_value, sector_value)
        hist_plot.update_figure(build_history_figure(snaps))
        a5, a20, label = rolling_averages(prior_scores)
        roll_lbl.text = f"5d: {a5:.2f}   20d: {a20:.2f}   {label}"
        vel = derived.get("velocity") or {}
        vel_lbl.text = vel.get("text", "")
        flag_lbl.text = vel.get("flag", "")
        div_lbl.text = derived.get("divergence", "") or ""
        trend = derived.get("trend")
        if trend:
            committed = trend.get("state")
            green = {"bull_trend", "pullback_in_bull"}
            red = {"bear_rally", "bear_trend"}
            color = CLR_GREEN if committed in green else (
                CLR_RED if committed in red else CLR_YELLOW)
            trend_gauge_box.content = speedometer_svg(
                trend_gauge_value(trend), _TREND_SHORT.get(committed, "—"),
                width=200, height=130)
            regime_badge.text = trend.get("label", "")
            regime_badge.style(f"color:{color}")
            regime_desc.text = trend.get("description", "")
            regime_detail.text = (
                f"SPY {_safe_float(trend.get('spy_close')):.2f} · "
                f"50d {_safe_float(trend.get('sma_50')):.2f} · "
                f"200d {_safe_float(trend.get('sma_200')):.2f} "
                f"· slope {_safe_float(trend.get('sma_200_slope_pct')):+.2f}% "
                f"· dd {_safe_float(trend.get('drawdown_pct')):+.1f}% "
                f"· conf {_safe_float(trend.get('confidence')):.0%}")
        else:
            trend_gauge_box.content = speedometer_svg(50.0, "—", width=200, height=130)
            regime_badge.text = ""
            regime_desc.text = ""
            regime_detail.text = ""

    def _render_sector_table():
        sec = state["sector"]
        sector_box.clear()
        if not sec:
            with sector_box:
                ui.label("Waiting for sentiment service…").classes("opacity-60 text-sm")
            return
        sd = sec["sector_data"]
        rows = sector_table_rows(sd, sec["quotes"], sec["trends"], sec["pcr"], sec["quadrants"])
        with sector_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                ui.label("").style("width:24px")
                for _f, hdr, w in SEC_COLS:
                    if _f == "desc":
                        ui.label(hdr).style("flex:1;min-width:160px")
                    else:
                        ui.label(hdr).style(f"width:{w}px")
            for r in rows:
                sector_name = r["sector"]
                expanded = sector_name in state["expanded"]
                dc = pct_color(r["day"])  # name/etf/desc share the Day % color
                with ui.row().classes("items-center w-full no-wrap gap-2 text-sm secrow"):
                    ui.icon("keyboard_arrow_down" if expanded else "keyboard_arrow_right") \
                        .classes("cursor-pointer").style("width:24px") \
                        .on("click", lambda _e, s=sector_name: _toggle_sector(s))
                    ui.label(str(sector_name or "")).style(f"width:140px;color:{dc}")
                    ui.label(str(r["etf"] or "")).style(f"width:50px;color:{dc}")
                    ui.label(str(r["desc"] or "")).style(
                        f"flex:1;min-width:160px;color:{dc};overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                    for fld in ("day", "week", "month"):
                        v = r[fld]
                        ui.label(f"{v:+.2f}%" if v is not None else "—") \
                            .style(f"width:70px;color:{pct_color(v)}")
                    pv = r["pcr"]
                    ui.label(f"{pv:.2f}" if pv is not None else "").style(
                        f"width:56px;color:{pcr_color(pv)}")
                    rv = r["rrg"]
                    ui.label(str(rv or "")).style(f"width:90px;color:{rrg_color(rv)}")
                if expanded:
                    # Industries come PRECOMPUTED in the sectors cache view
                    # ({"quotes","trends","pcr","quadrants"} per sector name) —
                    # no proxy call here.
                    ind = (state["industries"] or {}).get(sector_name)
                    if not ind:
                        with ui.row().classes("items-center w-full no-wrap gap-2 text-xs opacity-60"):
                            ui.label("").style("width:24px")
                            ui.label("no industry data").style("width:200px")
                    else:
                        for ir in industry_rows(sd, sector_name, ind.get("quotes"), ind.get("trends"), ind.get("pcr"), ind.get("quadrants")):
                            idc = pct_color(ir["day"])  # industry name/etf/desc share its Day % color
                            with ui.row().classes("items-center w-full no-wrap gap-2 text-xs secrow indrow"):
                                ui.label("").style("width:24px")
                                ui.label(str(ir["label"] or "")).style(
                                    f"width:140px;padding-left:14px;color:{idc};opacity:0.85")
                                ui.label(str(ir["etf"] or "")).style(f"width:50px;color:{idc}")
                                ui.label(str(ir["desc"] or "")).style(
                                    f"flex:1;min-width:160px;color:{idc};overflow:hidden;text-overflow:ellipsis;"
                                    "white-space:nowrap;opacity:0.8")
                                for fld in ("day", "week", "month"):
                                    v = ir[fld]
                                    ui.label(f"{v:+.2f}%" if v is not None else "—") \
                                        .style(f"width:70px;color:{pct_color(v)}")
                                pv = ir["pcr"]
                                ui.label(f"{pv:.2f}" if pv is not None else "").style(
                                    f"width:56px;color:{pcr_color(pv)}")
                                rv = ir["rrg"]
                                ui.label(str(rv or "")).style(f"width:90px;color:{rrg_color(rv)}")

    @guard
    def _toggle_sector(sector_name):
        if sector_name in state["expanded"]:
            state["expanded"].discard(sector_name)
        else:
            state["expanded"].add(sector_name)
        _render_sector_table()

    @guard
    def _expand_all():
        if not state["sector"]:
            return
        for r in sector_table_rows(state["sector"]["sector_data"], state["sector"]["quotes"],
                                   state["sector"]["trends"], state["sector"]["pcr"],
                                   state["sector"]["quadrants"]):
            state["expanded"].add(r["sector"])
        _render_sector_table()

    @guard
    def _collapse_all():
        state["expanded"].clear()
        _render_sector_table()

    def _apply_sectors():
        sec = state["sector"]
        if not sec:
            summary_lbl.text = ""
            rotation_lbl.text = ""
            _render_sector_table()
            return
        sd, quotes = sec["sector_data"], sec["quotes"]
        summary_lbl.text = sector_summary(sd, quotes, state.get("sector_summary"))
        regime, color, detail = rotation_banner(sec["rotation"])
        rotation_lbl.text = f"{regime} — {detail}"
        rotation_lbl.style(f"color:{color}")
        _render_sector_table()
        # refill rotation/sector Value cells in the component table now loaded
        if state["snaps"] or state.get("live"):
            rotation_value, sector_value = _comp_context()
            latest = state.get("live") or state["snaps"][-1]
            _render_components(latest, rotation_value, sector_value)

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh"})
        ui.notify("Refresh requested")

    from datetime import timedelta
    @guard
    def _render_status():
        parts = []
        ca = _parse_iso(state.get("composite_at"))
        if ca:
            parts.append(f"Updated {_fmt_time(ca)}")
            parts.append(f"Next ~{_fmt_time(ca + timedelta(seconds=120))[:5]}")
        sa = state.get("sector_at")
        sa_str = _fmt_time(sa)
        if sa_str:
            parts.append(f"Sectors {sa_str}")
        up = state.get("proxy_up")
        parts.append(f"Proxy: {'connected' if up else ('—' if up is None else 'down')}")
        status_lbl.text = "   ·   ".join(parts) if parts else "Waiting for sentiment service…"

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache versions to the last-painted ones and
        # only re-read + repaint on change. Mirrors the previous version-poll
        # pattern but tracks the Redis bus version instead of an in-process cache.
        comp_ver = bus_client.read_version("sentiment:composite")
        sec_ver = bus_client.read_version("sentiment:sectors")
        if comp_ver == state["comp_ver"] and sec_ver == state["sec_ver"]:
            return
        state["comp_ver"] = comp_ver
        state["sec_ver"] = sec_ver
        _read_cache()
        _apply()
        _apply_sectors()
        _render_status()

    ui.separator().classes("q-my-sm")
    status_lbl = ui.label("Waiting for sentiment service…").classes("opacity-60 text-xs w-full")

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    _apply()
    _apply_sectors()
    _render_status()
    # Fetch-free version-poll repaint: tracks the service's cache writes without
    # any engine call. The page never fetches; the Refresh button enqueues a
    # command for the service to recompute.
    ui.timer(2.0, _maybe_repaint)
