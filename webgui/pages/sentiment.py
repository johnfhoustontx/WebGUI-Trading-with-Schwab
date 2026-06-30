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
from pages.gauge import gauge_figure  # noqa: F401  (re-export; used by render)
from pages.options.theme import BTN_3D, TILE_3D
from pages.ui_guard import guard, guard_async

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_FLAT = "#9e9e9e"
CLR_CYAN = "#3fb6c7"
LINE_COLOR = "#42a5f5"

# LOCAL Tailwind class maps (Phase 5). These mirror the page's OWN 5-color palette
# EXACTLY — the yellow/cyan have no theme TXT_* token and the flat differs from the
# theme neutral, so they are intentionally NOT the shared theme tokens (this page
# keeps its own look). The `*_color` hex helpers above are retained for the
# Highcharts figures + non-`.classes()` callers; the `*_class` helpers below return
# the Tailwind class string for `.classes()`.
TXT_G = "text-[#66bb6a]"
TXT_R = "text-[#ef5350]"
TXT_Y = "text-[#ffd54f]"
TXT_FLAT = "text-[#9e9e9e]"
TXT_CY = "text-[#3fb6c7]"
BG_G = "bg-[#66bb6a]"
BG_R = "bg-[#ef5350]"
BG_Y = "bg-[#ffd54f]"
# Remove-sets for reactive in-place recolors (these MUST cover every class the
# element can apply, or colors stack across the page's ~2s auto-refresh).
SENT_TEXT_CLASSES = " ".join([TXT_G, TXT_R, TXT_Y, TXT_FLAT, TXT_CY])
TRAFFIC_BG_CLASSES = " ".join([BG_G, BG_R, BG_Y])

# Map a local hex -> its Tailwind text class (for helpers that already return a hex).
_HEX_TO_TXT = {CLR_GREEN: TXT_G, CLR_RED: TXT_R, CLR_YELLOW: TXT_Y,
               CLR_FLAT: TXT_FLAT, CLR_CYAN: TXT_CY}
# Map a local hex -> its Tailwind bg class (only the three traffic bands have a bg).
_HEX_TO_TXT_BG = {CLR_GREEN: BG_G, CLR_RED: BG_R, CLR_YELLOW: BG_Y}
# Per-cell right divider for the sector/industry table rows (last cell omits it).
BORDER_R = "border-r border-white/[0.04]"

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


def traffic_bg_class(total):
    """Composite traffic-light band as a Tailwind bg class (tile backgrounds)."""
    return _HEX_TO_TXT_BG[traffic_color(total)]


def gauge_score(total):
    """0-10 composite -> 0-100 for the speedometer gauge."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# Short dial captions (the full label shows beneath the gauge).
_TREND_SHORT = {"bull_trend": "BULL", "pullback_in_bull": "PULLBACK",
                "range": "RANGE", "bear_rally": "BEAR RALLY", "bear_trend": "BEAR"}

# Market Trend sub-score display metadata (name + weight). Mirrors the service's
# TREND_WEIGHTS — kept local so the page imports no engine (3-tier rule).
_TREND_SUB_META = [("price", "Price / MTF", "45%"), ("breadth", "Breadth", "25%"),
                   ("sector", "Sector", "20%"), ("vix", "VIX", "10%")]


def trend_gauge_value(trend):
    """0-100 needle = the intraday trend score directly (smoothed if present)."""
    t = trend or {}
    v = t.get("smoothed_score", t.get("score"))
    if v is None:
        return 50.0
    return _clamp(_safe_float(v, 50.0), 0.0, 100.0)


def trend_subscore_rows(trend):
    """Display rows [{name, score, weight, conf}] for the Market Trend sub-score
    popup; skips sub-scores absent from the payload (e.g. the 30-day variant has
    only price+sector)."""
    t = trend or {}
    scores = t.get("sub_scores") or {}
    confs = t.get("sub_confidence") or {}
    rows = []
    for key, name, weight in _TREND_SUB_META:
        if key not in scores:
            continue
        rows.append({"name": name, "score": f"{_safe_float(scores.get(key)):.1f}",
                     "weight": weight, "conf": f"{_safe_float(confs.get(key)):.2f}"})
    return rows


def bias_color(bias):
    b = (bias or "").lower()
    if "bull" in b:
        return CLR_GREEN
    if "bear" in b:
        return CLR_RED
    return CLR_YELLOW


def bias_text_class(bias):
    return _HEX_TO_TXT[bias_color(bias)]


def trend_text_class(committed):
    """Tailwind text class for a committed trend state (mirrors _apply's mapping)."""
    if committed in {"bull_trend", "pullback_in_bull"}:
        return TXT_G
    if committed in {"bear_rally", "bear_trend"}:
        return TXT_R
    return TXT_Y


def rotation_text_class(color):
    """Map a rotation_banner() hex color -> Tailwind text class."""
    return _HEX_TO_TXT.get(color, TXT_FLAT)


def sc_text_class(score):
    """Component score color: >=7 green, <4 red, else amber (mirrors _render_components)."""
    sc = _safe_float(score)
    if sc >= 7:
        return TXT_G
    if sc < 4:
        return TXT_R
    return TXT_Y


def composite_series(snapshots):
    """(dates, scores) for snapshots with a positive composite total."""
    dates, scores = [], []
    for s in snapshots:
        v = _safe_float((s.get("composite") or {}).get("total_score"))
        if v > 0:
            dates.append(s.get("date"))
            scores.append(v)
    return dates, scores


def sentiment_30d_avg(snaps):
    """Mean composite over the backfill history (0.0 if none). Pure."""
    scores = composite_series(snaps or [])[1]
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def build_history_figure(snapshots):
    """Highcharts options dict: composite over time (line)."""
    dates, scores = composite_series(snapshots)
    axis_label = {"style": {"color": "#bdbdbd"}}
    return {
        "chart": {"type": "line", "backgroundColor": "transparent",
                  "height": 220, "spacing": [8, 12, 8, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {
            "categories": dates,
            "tickAmount": 6,
            "lineColor": "rgba(255,255,255,0.15)",
            "gridLineColor": "rgba(255,255,255,0.06)",
            "labels": axis_label,
        },
        "yAxis": {
            "min": 0, "max": 10,
            "title": {"text": "Composite", "style": {"color": "#bdbdbd"}},
            "gridLineColor": "rgba(255,255,255,0.06)",
            "labels": axis_label,
        },
        "tooltip": {"pointFormat": "Composite: <b>{point.y:.2f}</b>"},
        "series": [{
            "name": "Composite", "type": "line", "data": scores,
            "color": LINE_COLOR, "lineWidth": 2,
            "marker": {"enabled": True, "radius": 3},
        }],
    }


def _intraday_figure(points, *, value_key, y_max, y_title, zones):
    """Shared Highcharts options for a 2-min intraday value series, colorized by
    value via series.zones. Ordinal x-axis collapses overnight session gaps; the
    date lives in the tooltip header (datetime-crosshair-label epoch-ms gotcha)."""
    pts = points or []
    data = [[int(p["ts"]) * 1000, _safe_float(p.get(value_key))] for p in pts]
    axis_label = {"style": {"color": "#bdbdbd"}}
    return {
        "chart": {"type": "line", "backgroundColor": "transparent",
                  "height": 200, "spacing": [8, 12, 8, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {"type": "datetime", "ordinal": True,
                  "lineColor": "rgba(255,255,255,0.15)",
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label,
                  "crosshair": {"label": {"enabled": False}}},
        "yAxis": {"min": 0, "max": y_max,
                  "title": {"text": y_title, "style": {"color": "#bdbdbd"}},
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label},
        "tooltip": {"xDateFormat": "%b %e, %H:%M",
                    "pointFormat": y_title + ": <b>{point.y:.2f}</b>"},
        "series": [{
            "name": y_title, "type": "line", "data": data,
            "lineWidth": 2, "zoneAxis": "y", "zones": zones,
            "marker": {"enabled": False},
        }],
    }


def build_sentiment_intraday_figure(points):
    """Daily Market Sentiment (0-10), colorized by traffic bands."""
    zones = [{"value": 4.5, "color": CLR_RED},
             {"value": 6.5, "color": CLR_YELLOW},
             {"color": CLR_GREEN}]
    return _intraday_figure(points, value_key="sentiment", y_max=10,
                            y_title="Sentiment", zones=zones)


def build_trend_intraday_figure(points):
    """Daily Market Trend (0-100), colorized by the 30/70 range boundaries."""
    zones = [{"value": 30, "color": CLR_RED},
             {"value": 70, "color": CLR_YELLOW},
             {"color": CLR_GREEN}]
    return _intraday_figure(points, value_key="trend", y_max=100,
                            y_title="Trend", zones=zones)


def pct_color(pct):
    """Green up / red down / gray flat (|pct| < 0.05)."""
    if pct is None or abs(float(pct)) < 0.05:
        return CLR_FLAT
    return CLR_GREEN if float(pct) > 0 else CLR_RED


def pct_text_class(pct):
    return _HEX_TO_TXT[pct_color(pct)]


def pcr_color(pcr):
    """<0.95 call-dominated green, >1.05 put-dominated red, else flat."""
    if pcr is None or float(pcr) <= 0:
        return CLR_FLAT
    if float(pcr) < 0.95:
        return CLR_GREEN
    if float(pcr) > 1.05:
        return CLR_RED
    return CLR_FLAT


def pcr_text_class(pcr):
    return _HEX_TO_TXT[pcr_color(pcr)]


def rrg_color(quadrant):
    return {
        "Leading": CLR_GREEN, "Improving": CLR_CYAN,
        "Weakening": CLR_YELLOW, "Lagging": CLR_RED,
    }.get(quadrant, CLR_FLAT)


def rrg_text_class(quadrant):
    return _HEX_TO_TXT[rrg_color(quadrant)]


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
        ui.button(icon="refresh", color=None, on_click=lambda: _request_refresh()) \
            .props("no-caps").classes(f"q-ml-sm {BTN_3D}")

    tile_lbls, tile_cards = {}, {}
    # 2x2 signal matrix (Modifier dropped per design).
    TILE_DEFS = [("bias", "BIAS"), ("signal", "SIGNAL"),
                 ("yesterday", "YESTERDAY"), ("change", "CHANGE")]
    # Three evenly-distributed, top-aligned columns with matching h6 headers.
    with ui.row().classes("w-full items-start justify-around gap-6 flex-wrap"):
        # ① Market Sentiment — composite speedometer + press-and-hold Components popup
        with ui.column().classes("items-center min-w-[210px]"):
            ui.label("Market Sentiment").classes("text-h6")
            with ui.row().classes("items-end justify-center gap-4 no-wrap"):
                with ui.column().classes("items-center"):
                    ui.label("Today").classes("opacity-60 text-xs")
                    gauge_box = ui.highchart(gauge_figure(50.0, "—")) \
                        .classes("q-mt-xs w-[170px] h-[120px]")
                with ui.column().classes("items-center"):
                    ui.label("30-Day Avg").classes("opacity-60 text-xs")
                    gauge_avg_box = ui.highchart(gauge_figure(50.0, "—")) \
                        .classes("q-mt-xs w-[170px] h-[120px]")
            bias_lbl = ui.label("").classes("text-h6")
            sub_lbl = ui.label("").classes("opacity-80 text-sm")
            with ui.button("Components", icon="table_view").props("flat dense") as comp_btn:
                with ui.menu().props("no-parent-event") as comp_menu:
                    comp_box = ui.column().classes("q-pa-md min-w-[520px]")
            # Press-and-hold: shown while the mouse button is down, closed on release.
            comp_btn.on("mousedown", lambda: comp_menu.open())
            comp_btn.on("mouseup", lambda: comp_menu.close())
            comp_btn.on("mouseleave", lambda: comp_menu.close())
        # ② Market Trend — speedometer (hybrid needle) + label/desc + detail popup
        with ui.column().classes("items-center min-w-[210px]"):
            ui.label("Market Trend").classes("text-h6")
            with ui.row().classes("items-end justify-center gap-4 no-wrap"):
                with ui.column().classes("items-center"):
                    ui.label("Today").classes("opacity-60 text-xs")
                    trend_gauge_box = ui.highchart(gauge_figure(50.0, "—")) \
                        .classes("q-mt-xs w-[170px] h-[120px]")
                with ui.column().classes("items-center"):
                    ui.label("30-Day").classes("opacity-60 text-xs")
                    trend_gauge_30_box = ui.highchart(gauge_figure(50.0, "—")) \
                        .classes("q-mt-xs w-[170px] h-[120px]")
            regime_badge = ui.label("").classes("text-subtitle1 text-bold")
            regime_desc = ui.label("").classes("opacity-80 text-sm text-center")
            with ui.button("Trend Detail", icon="insights").props("flat dense") as trend_btn:
                with ui.menu().props("no-parent-event") as trend_menu:
                    trend_detail_box = ui.column().classes("q-pa-md text-sm min-w-[240px]")
            trend_btn.on("mousedown", lambda: trend_menu.open())
            trend_btn.on("mouseup", lambda: trend_menu.close())
            trend_btn.on("mouseleave", lambda: trend_menu.close())
        # ③ Signals — 2x2 matrix
        with ui.column().classes("items-center min-w-[210px]"):
            ui.label("Signals").classes("text-h6")
            with ui.grid(columns=2).classes("gap-2 q-mt-sm"):
                for tkey, tlabel in TILE_DEFS:
                    c = ui.card().classes(f"q-pa-sm items-center min-w-[96px] {TILE_3D}")
                    with c:
                        ui.label(tlabel).classes("text-xs text-[#111]")
                        tile_lbls[tkey] = ui.label("—").classes("text-bold text-[#111]")
                    tile_cards[tkey] = c

    ui.separator().classes("q-my-md")
    # 30-Day History — collapsible, collapsed by default.
    with ui.expansion("30-Day History", icon="show_chart", value=False).classes("w-full"):
        hist_plot = ui.highchart(build_history_figure([])).classes("w-full")
        roll_lbl = ui.label("").classes("opacity-70 text-sm")
        vel_lbl = ui.label("").classes("opacity-80 text-sm")
        flag_lbl = ui.label("").classes("text-negative text-sm")
        div_lbl = ui.label("").classes("text-warning text-sm")

    # Sector & Industry Performance
    ui.separator().classes("q-my-md")
    ui.label("Sector & Industry Performance").classes("text-subtitle1")
    with ui.row().classes("items-center gap-3 w-full"):
        ui.button("Refresh", icon="refresh", color=None,
                  on_click=lambda: _request_refresh()).props("no-caps").classes(BTN_3D)
        ui.button("Expand All", color=None, on_click=lambda: _expand_all()).props("no-caps").classes(BTN_3D)
        ui.button("Collapse All", color=None, on_click=lambda: _collapse_all()).props("no-caps").classes(BTN_3D)
        summary_lbl = ui.label("").classes("opacity-80 text-sm")
    rotation_lbl = ui.label("").classes("text-sm")
    sector_box = ui.column().classes("w-full q-gutter-none q-mt-sm")

    SEC_COLS = [("sector", "Sector", 140), ("etf", "ETF", 50),
                ("desc", "Description", 200), ("day", "Day %", 70),
                ("week", "Week %", 70), ("month", "Month %", 70),
                ("pcr", "P/C", 56), ("rrg", "RRG", 90)]
    # Single source of truth for column widths so the header (driven by SEC_COLS)
    # and the data-row cells never drift. ``desc`` is the flex column (its tuple
    # width is unused — both header and rows render it ``flex-1 min-w-[160px]``).
    SEC_W = {field: w for field, _label, w in SEC_COLS}

    def _render_components(latest, rotation_value=None, sector_value=None):
        comp_box.clear()
        weights = (state.get("derived") or {}).get("weights")
        rows = component_table_rows(latest, weights, rotation_value, sector_value)
        with comp_box:
            with ui.row().classes("items-center w-full no-wrap gap-3 opacity-60 text-xs"):
                ui.label("Component").classes("w-[110px]")
                ui.label("Value").classes("w-[140px]")
                ui.label("Score").classes("w-[50px]")
                ui.label("Weight").classes("w-[60px]")
                ui.label("Conf").classes("w-[50px]")
            for r in rows:
                sc = r["score"]
                with ui.row().classes("items-center w-full no-wrap gap-3"):
                    ui.label(r["name"]).classes("text-sm w-[110px]")
                    ui.label(str(r["value"])).classes(
                        "text-sm w-[140px] overflow-hidden text-ellipsis whitespace-nowrap")
                    ui.label(f"{sc:.2f}").classes(
                        "text-sm text-bold w-[50px] " + sc_text_class(sc))
                    ui.label(r["weight"]).classes("text-sm w-[60px]")
                    ui.label(r["conf"]).classes("text-sm w-[50px]")

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
        gauge_box.options = gauge_figure(gauge_score(total), comp.get("bias", ""))
        gauge_box.update()
        avg = sentiment_30d_avg(state["snaps"])
        gauge_avg_box.options = gauge_figure(gauge_score(avg), f"{avg:.2f}")
        gauge_avg_box.update()
        bias_lbl.text = f"{total:.2f} · {comp.get('bias', '')}"
        bias_lbl.classes(remove=SENT_TEXT_CLASSES, add=bias_text_class(comp.get('bias')))
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
        band_bg = traffic_bg_class(total)
        for tkey, _tlabel in TILE_DEFS:
            tile_lbls[tkey].text = t[tkey]
            tile_cards[tkey].classes(remove=TRAFFIC_BG_CLASSES, add=band_bg)
        rotation_value, sector_value = _comp_context()
        _render_components(latest, rotation_value, sector_value)
        hist_plot.options = build_history_figure(snaps)
        hist_plot.update()
        a5, a20, label = rolling_averages(prior_scores)
        roll_lbl.text = f"5d: {a5:.2f}   20d: {a20:.2f}   {label}"
        vel = derived.get("velocity") or {}
        vel_lbl.text = vel.get("text", "")
        flag_lbl.text = vel.get("flag", "")
        div_lbl.text = derived.get("divergence", "") or ""
        trend = derived.get("trend")
        if trend:
            committed = trend.get("state")
            trend_gauge_box.options = gauge_figure(
                trend_gauge_value(trend), _TREND_SHORT.get(committed, "—"))
            trend_gauge_box.update()
            regime_badge.text = trend.get("label", "")
            regime_badge.classes(remove=SENT_TEXT_CLASSES, add=trend_text_class(committed))
            regime_desc.text = trend.get("description", "")
            trend_detail_box.clear()
            with trend_detail_box:
                ui.label(
                    f"Trend score {trend_gauge_value(trend):.0f} · "
                    f"conf {_safe_float(trend.get('confidence')):.0%}"
                ).classes("text-bold")
                for r in trend_subscore_rows(trend):
                    ui.label(f"{r['name']} ({r['weight']}): {r['score']}  "
                             f"conf {r['conf']}").classes("text-sm")
        else:
            trend_gauge_box.options = gauge_figure(50.0, "—")
            trend_gauge_box.update()
            regime_badge.text = ""
            regime_desc.text = ""
            trend_detail_box.clear()
            with trend_detail_box:
                ui.label("—").classes("text-sm")
        t30 = (state.get("derived") or {}).get("trend_30d_ago")
        if t30:
            trend_gauge_30_box.options = gauge_figure(
                trend_gauge_value(t30), _TREND_SHORT.get(t30.get("state"), "—"))
            trend_gauge_30_box.update()
        else:
            trend_gauge_30_box.options = gauge_figure(50.0, "—")
            trend_gauge_30_box.update()

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
                ui.label("").classes("w-[24px]")
                for _f, hdr, w in SEC_COLS:
                    if _f == "desc":
                        ui.label(hdr).classes("flex-1 min-w-[160px]")
                    else:
                        ui.label(hdr).classes(f"w-[{w}px]")
            for r in rows:
                sector_name = r["sector"]
                expanded = sector_name in state["expanded"]
                dc = pct_text_class(r["day"])  # name/etf/desc share the Day % color
                with ui.row().classes(
                        "items-center w-full no-wrap gap-2 text-sm "
                        "border-b border-white/5 hover:bg-white/[0.04]"):
                    ui.icon("keyboard_arrow_down" if expanded else "keyboard_arrow_right") \
                        .classes(f"cursor-pointer w-[24px] {BORDER_R}") \
                        .on("click", lambda _e, s=sector_name: _toggle_sector(s))
                    ui.label(str(sector_name or "")).classes(f"w-[{SEC_W['sector']}px] {BORDER_R} {dc}")
                    ui.label(str(r["etf"] or "")).classes(f"w-[{SEC_W['etf']}px] {BORDER_R} {dc}")
                    ui.label(str(r["desc"] or "")).classes(
                        f"flex-1 min-w-[160px] {BORDER_R} "
                        f"overflow-hidden text-ellipsis whitespace-nowrap {dc}")
                    for fld in ("day", "week", "month"):
                        v = r[fld]
                        ui.label(f"{v:+.2f}%" if v is not None else "—") \
                            .classes(f"w-[{SEC_W[fld]}px] {BORDER_R} {pct_text_class(v)}")
                    pv = r["pcr"]
                    ui.label(f"{pv:.2f}" if pv is not None else "").classes(
                        f"w-[{SEC_W['pcr']}px] {BORDER_R} {pcr_text_class(pv)}")
                    rv = r["rrg"]
                    ui.label(str(rv or "")).classes(f"w-[{SEC_W['rrg']}px] {rrg_text_class(rv)}")
                if expanded:
                    # Industries come PRECOMPUTED in the sectors cache view
                    # ({"quotes","trends","pcr","quadrants"} per sector name) —
                    # no proxy call here.
                    ind = (state["industries"] or {}).get(sector_name)
                    if not ind:
                        with ui.row().classes("items-center w-full no-wrap gap-2 text-xs opacity-60"):
                            ui.label("").classes("w-[24px]")
                            ui.label("no industry data").classes("w-[200px]")
                    else:
                        for ir in industry_rows(sd, sector_name, ind.get("quotes"), ind.get("trends"), ind.get("pcr"), ind.get("quadrants")):
                            idc = pct_text_class(ir["day"])  # industry name/etf/desc share its Day % color
                            with ui.row().classes(
                                    "items-center w-full no-wrap gap-2 text-xs "
                                    "border-b border-white/5 hover:bg-white/[0.04] bg-white/[0.02]"):
                                ui.label("").classes(f"w-[24px] {BORDER_R}")
                                ui.label(str(ir["label"] or "")).classes(
                                    f"w-[{SEC_W['sector']}px] pl-[14px] {BORDER_R} opacity-85 {idc}")
                                ui.label(str(ir["etf"] or "")).classes(f"w-[{SEC_W['etf']}px] {BORDER_R} {idc}")
                                ui.label(str(ir["desc"] or "")).classes(
                                    f"flex-1 min-w-[160px] {BORDER_R} "
                                    f"overflow-hidden text-ellipsis whitespace-nowrap opacity-80 {idc}")
                                for fld in ("day", "week", "month"):
                                    v = ir[fld]
                                    ui.label(f"{v:+.2f}%" if v is not None else "—") \
                                        .classes(f"w-[{SEC_W[fld]}px] {BORDER_R} {pct_text_class(v)}")
                                pv = ir["pcr"]
                                ui.label(f"{pv:.2f}" if pv is not None else "").classes(
                                    f"w-[{SEC_W['pcr']}px] {BORDER_R} {pcr_text_class(pv)}")
                                rv = ir["rrg"]
                                ui.label(str(rv or "")).classes(f"w-[{SEC_W['rrg']}px] {rrg_text_class(rv)}")

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
        rotation_lbl.classes(remove=SENT_TEXT_CLASSES, add=rotation_text_class(color))
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
