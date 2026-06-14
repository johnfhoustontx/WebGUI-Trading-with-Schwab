"""Sentiment page — composite gauge + components + 30d history + trend regime.

Thin NiceGUI layer over the copied ``history_backfill`` + ``scoring`` engines.
Pure transforms here are unit-tested; ``render()`` wires widgets + timers.
"""
import sys

from repo_paths import SENTIMENT

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

from scoring import WEIGHTS  # noqa: E402
from scoring import composite as scoring_composite  # noqa: E402
from scoring import trend_regime as trend_regime  # noqa: E402
from scoring import sector_perf as scoring_sector  # noqa: E402
from scoring import rotation as scoring_rotation    # noqa: E402

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_FLAT = "#9e9e9e"
CLR_CYAN = "#3fb6c7"
LINE_COLOR = "#42a5f5"

# (component_scores key, display name, weight or None if out of composite)
COMPONENTS = [
    ("vix_complex", "VIX Complex", WEIGHTS.get("vix_complex")),
    ("put_call",    "Put/Call",    WEIGHTS.get("put_call")),
    ("breadth",     "Breadth",     WEIGHTS.get("breadth")),
    ("rotation",    "Rotation",    WEIGHTS.get("rotation")),
    ("sector_perf", "Sector Perf", WEIGHTS.get("sector_perf")),
    ("credit_pulse", "Credit Pulse", WEIGHTS.get("credit_pulse")),
]


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def gauge_score(total):
    """0-10 composite -> 0-100 for the svg speedometer."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


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


def velocity_line(prior_scores, today_score):
    """(text, flag) from scoring.composite.velocity."""
    v = scoring_composite.velocity(list(prior_scores), _safe_float(today_score))
    roc3, roc5, z = v["roc_3d"], v["roc_5d"], v["z_20d"]
    parts = [
        f"3d ROC: {roc3:+.2f}" if roc3 is not None else "3d ROC: —",
        f"5d ROC: {roc5:+.2f}" if roc5 is not None else "5d ROC: —",
        f"20d Z: {z:+.2f}" if z is not None else "20d Z: —",
    ]
    flag = f"REGIME BREAK: {z:+.2f}σ from 20d mean" if v["regime_break"] else ""
    return " | ".join(parts), flag


def divergence_named(snapshot):
    """[(display_name, score)] for confident, scored components."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
    out = []
    for key, name, _w in COMPONENTS:
        s = _safe_float(scores.get(key))
        if s > 0 and _safe_float(confs.get(key)) > 0:
            out.append((name, s))
    return out


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
            "yaxis": {"range": [0, 10], "title": "Composite"},
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
        },
    }


def commit_trend_regime(spy_closes, lookback_days=trend_regime.HYSTERESIS_DAYS + 1):
    """Replay classify + commit_state over the last sessions for faithful
    hysteresis without persisted state. Returns (result, committed, days)."""
    closes = list(spy_closes)
    result = trend_regime.classify(closes)
    committed = None
    history = []
    n = len(closes)
    span = min(lookback_days, max(1, n - trend_regime.MIN_BARS_PARTIAL))
    for back in range(span - 1, -1, -1):
        sub = closes[: n - back] if back else closes
        raw = trend_regime.classify(sub).state
        committed, history = trend_regime.commit_state(raw, history, committed)
    days = 1
    return result, (committed or result.state), days


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
    Returns None when no chain or zero call volume. Ported from source
    sentiment_dashboard.py:2939-2953."""
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
    """%-change from n sessions ago to last close, or None. Mirrors source
    _pct_change_n (uses close[-(n+1)])."""
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


def sector_summary(sector_data, quotes):
    """'{pct_up}% green | Cap-wtd {wpct} | Score {score}/10' (mirrors source)."""
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
    wpct, _ = scoring_sector.weighted_sector_pct(sector_data, quotes)
    wpct_str = f"{wpct:+.2f}%" if wpct is not None else "—"
    score = scoring_sector.sectors_score(sector_data, quotes)
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


def sector_industry_etfs(sector_data, sector_name):
    """Industry ETF symbols under a sector (kind=='industry', valid etf)."""
    out = []
    for r in sector_data:
        if r.get("kind") != "industry" or r.get("sector") != sector_name:
            continue
        etf = r.get("etf")
        if etf and etf != "n/a" and len(str(etf)) <= 6:
            out.append(etf)
    return out


def industry_rows(sector_data, sector_name, ind_quotes, ind_trends):
    """Indented rows for a sector's industries: day/week/month % only
    (pcr/rrg blank — industry option volume is too thin)."""
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
            "pcr": None, "rrg": None,
            "is_industry": True,
        })
    return rows


def _load_snapshots(days=35):
    """Off-thread: full scoring path via the copied backfill engine.
    Returns (snapshots, spy_closes)."""
    import proxy
    import sectors_ref
    from history_backfill import backfill_history

    sector_data = sectors_ref.load_sectors_data()
    snaps, _stats = backfill_history(proxy.schwab_client, sector_data, [], days=days)
    spy_df = proxy.schwab_client.get_daily_history("SPY", months=12)
    spy_closes = (
        [float(c) for c in spy_df["close"].tolist()]
        if spy_df is not None else []
    )
    return snaps, spy_closes


def _load_sector_perf(spy_closes):
    """Off-thread: fetch sector quotes + history + P/C, compute rotation/RRG.
    Returns a dict the page renders. spy_closes reused from the composite load."""
    import proxy
    import sectors_ref
    from datetime import date, timedelta

    sd = sectors_ref.load_sectors_data()
    etfs = [r["etf"] for r in sd if r.get("kind") == "sector" and r.get("etf")]

    try:
        quotes = proxy.schwab_client.get_quotes(etfs) or {}
    except Exception:
        quotes = {}

    trends, closes = {}, {}
    for etf in etfs:
        try:
            df = proxy.schwab_client.get_daily_history(etf, months=3)
        except Exception:
            df = None
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        closes[etf] = cl
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}

    pcr = {}
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()
    for etf in etfs:
        try:
            chain = proxy.schwab_client._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        v = pcr_from_chain(chain)
        if v is not None:
            pcr[etf] = v

    try:
        irx_q = proxy.schwab_client.get_quote("$IRX") or {}
        irx = irx_q.get("last") if isinstance(irx_q, dict) else None
    except Exception:
        irx = None

    quads = scoring_rotation.compute_rrg_quadrants(closes, spy_closes or [],
                                                   rs_window=50, mom_window=20)
    sp_weights = {r["etf"]: r.get("sp_weight", 0.0)
                  for r in sd if r.get("kind") == "sector" and r.get("etf")}
    try:
        dual = scoring_rotation.compute_dual_momentum(closes, sp_weights, irx,
                                                      lookback_days=63)
    except Exception:
        dual = {}
    rot = scoring_rotation.compute_rotation(sd, trends, quotes)
    return {"sector_data": sd, "quotes": quotes, "trends": trends,
            "pcr": pcr, "quadrants": quads, "dual": dual, "rotation": rot}


def _signal_band(score):
    """(size_modifier, bias, signal) — mirrors source _update_position_modifier."""
    if score >= 9:
        return "1.25x", "Long", "Strong Bull"
    if score >= 7:
        return "1.10x", "Long", "Bullish"
    if score >= 5:
        return "1.00x", "Neutral", "Neutral"
    if score >= 3:
        return "0.85x", "Cautious", "Bearish"
    return "0.70x", "Short", "Strong Bear"


def component_table_rows(snapshot, rotation_value=None, sector_value=None):
    """Rows for the in-composite components: name/value/score/weight/conf/contrib.
    Scores/confs come from the snapshot so Contrib reconciles to the composite."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
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
    for key, name, w in COMPONENTS:
        if not w:                      # skip out-of-composite (credit_pulse)
            continue
        s = _safe_float(scores.get(key))
        c = _safe_float(confs.get(key))
        rows.append({
            "key": key, "name": name,
            "value": value_src.get(key) or "—",
            "score": int(s),
            "weight": f"{int(w * 100)}%",
            "conf": f"{int(c * 100)}%",
            "contrib": w * s * c,
        })
    return rows


def tiles(latest, prev_total):
    comp = latest.get("composite") or {}
    total = _safe_float(comp.get("total_score"))
    size, bias, signal = _signal_band(total)
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


def render():
    import nicegui.run as ng_run
    from nicegui import ui

    from pages.options.svg import speedometer_svg

    state = {"snaps": [], "spy": [], "sector": None}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Market Sentiment").classes("text-h6")
        date_lbl = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        spinner = ui.spinner(size="sm")
        spinner.visible = False
        ui.button(icon="refresh", on_click=lambda: load(with_sectors=True)).props("flat round")

    gauge_box = ui.html("").classes("q-mt-sm")
    bias_lbl = ui.label("").classes("text-h6")
    sub_lbl = ui.label("").classes("opacity-80")

    # Signal tiles
    tile_lbls = {}
    TILE_DEFS = [("modifier", "MODIFIER"), ("bias", "BIAS"), ("signal", "SIGNAL"),
                 ("yesterday", "YESTERDAY"), ("change", "CHANGE")]
    with ui.row().classes("w-full no-wrap gap-2 q-mt-sm"):
        for tkey, tlabel in TILE_DEFS:
            with ui.card().classes("q-pa-sm items-center").style("min-width:96px;flex:1"):
                ui.label(tlabel).classes("opacity-60 text-xs")
                tile_lbls[tkey] = ui.label("—").classes("text-bold text-subtitle1")

    comp_box = ui.column().classes("w-full q-gutter-xs q-mt-md")
    ui.separator().classes("q-my-md")
    ui.label("30-Day History").classes("text-subtitle1")
    hist_plot = ui.plotly(build_history_figure([])).classes("w-full")
    roll_lbl = ui.label("").classes("opacity-70 text-sm")
    vel_lbl = ui.label("").classes("opacity-80 text-sm")
    flag_lbl = ui.label("").classes("text-negative text-sm")
    div_lbl = ui.label("").classes("text-warning text-sm")
    ui.separator().classes("q-my-md")
    ui.label("Market Trend Regime").classes("text-subtitle1")
    regime_badge = ui.badge("").classes("text-subtitle2 q-pa-sm")
    regime_desc = ui.label("").classes("opacity-80 text-sm")
    regime_detail = ui.label("").classes("opacity-60 text-xs")

    # Sector & Industry Performance
    ui.separator().classes("q-my-md")
    ui.label("Sector & Industry Performance").classes("text-subtitle1")
    with ui.row().classes("items-center gap-3 w-full"):
        ui.button("Refresh", icon="refresh",
                  on_click=lambda: load_sectors()).props("flat dense")
        sec_spinner = ui.spinner(size="sm")
        sec_spinner.visible = False
        summary_lbl = ui.label("").classes("opacity-80 text-sm")
    rotation_lbl = ui.label("").classes("text-sm")
    sector_box = ui.column().classes("w-full q-gutter-xs q-mt-sm")

    SEC_COLS = [("sector", "Sector", 140), ("etf", "ETF", 50),
                ("desc", "Description", 200), ("day", "Day %", 70),
                ("week", "Week %", 70), ("month", "Month %", 70),
                ("pcr", "P/C", 56), ("rrg", "RRG", 90)]

    def _render_components(latest, rotation_value=None, sector_value=None):
        comp_box.clear()
        rows = component_table_rows(latest, rotation_value, sector_value)
        with comp_box:
            with ui.row().classes("items-center w-full no-wrap gap-3 opacity-60 text-xs"):
                ui.label("Component").style("width:110px")
                ui.label("Value").style("width:140px")
                ui.label("Score").style("width:50px")
                ui.label("Weight").style("width:60px")
                ui.label("Conf").style("width:50px")
                ui.label("Contrib").style("width:60px")
            for r in rows:
                sc = r["score"]
                sc_color = (CLR_GREEN if sc >= 7 else
                            (CLR_RED if sc < 4 else CLR_YELLOW))
                with ui.row().classes("items-center w-full no-wrap gap-3"):
                    ui.label(r["name"]).classes("text-sm").style("width:110px")
                    ui.label(str(r["value"])).classes("text-sm").style(
                        "width:140px;overflow:hidden;text-overflow:ellipsis;"
                        "white-space:nowrap")
                    ui.label(str(sc)).classes("text-sm text-bold").style(
                        f"width:50px;color:{sc_color}")
                    ui.label(r["weight"]).classes("text-sm").style("width:60px")
                    ui.label(r["conf"]).classes("text-sm").style("width:50px")
                    ui.label(f"{r['contrib']:.2f}").classes("text-sm").style("width:60px")

    def _comp_context():
        """(rotation_value, sector_value) from loaded sector data, or (None, None)."""
        sec = state["sector"]
        if not sec:
            return None, None
        rotation_value = (sec.get("dual") or {}).get("interp")
        wpct, _ = scoring_sector.weighted_sector_pct(sec["sector_data"], sec["quotes"])
        sector_value = f"{wpct:+.2f}%" if wpct is not None else None
        return rotation_value, sector_value

    def _apply():
        snaps = state["snaps"]
        if not snaps:
            bias_lbl.text = "No data"
            return
        latest = snaps[-1]
        comp = latest.get("composite") or {}
        total = _safe_float(comp.get("total_score"))
        date_lbl.text = f"as of {latest.get('date')} (last completed session)"
        gauge_box.content = speedometer_svg(gauge_score(total), comp.get("bias", ""),
                                            width=220, height=140)
        bias_lbl.text = f"{total:.2f} · {comp.get('bias', '')}"
        bias_lbl.style(f"color:{bias_color(comp.get('bias'))}")
        sub_lbl.text = (f"size {comp.get('size_modifier', '—')} · "
                        f"agg conf {_safe_float(comp.get('aggregate_confidence')):.0%}")
        prev_total = None
        if len(snaps) >= 2:
            prev_total = (snaps[-2].get("composite") or {}).get("total_score")
        t = tiles(latest, prev_total)
        for tkey, _tlabel in TILE_DEFS:
            tile_lbls[tkey].text = t[tkey]
        rotation_value, sector_value = _comp_context()
        _render_components(latest, rotation_value, sector_value)
        hist_plot.update_figure(build_history_figure(snaps))
        _dates, scores = composite_series(snaps[:-1])
        a5, a20, label = rolling_averages(scores)
        roll_lbl.text = f"5d: {a5:.2f}   20d: {a20:.2f}   {label}"
        line, flag = velocity_line(scores, total)
        vel_lbl.text = line
        flag_lbl.text = flag
        div_lbl.text = scoring_composite.divergence(divergence_named(latest)) or ""
        if state["spy"]:
            tr, committed, days = commit_trend_regime(state["spy"])
            green = {"bull_trend", "pullback_in_bull"}
            red = {"bear_rally", "bear_trend"}
            color = CLR_GREEN if committed in green else (
                CLR_RED if committed in red else CLR_YELLOW)
            regime_badge.text = trend_regime.STATE_LABELS[committed]
            regime_badge.style(f"background-color:{color};color:#111")
            regime_desc.text = trend_regime.STATE_DESCRIPTIONS[committed]
            regime_detail.text = (
                f"SPY {tr.spy_close:.2f} · 50d {tr.sma_50:.2f} · 200d {tr.sma_200:.2f} "
                f"· slope {tr.sma_200_slope_pct:+.2f}% · dd {tr.drawdown_pct:+.1f}% "
                f"· conf {tr.confidence:.0%}")

    def _apply_sectors():
        sec = state["sector"]
        if not sec:
            return
        sd, quotes = sec["sector_data"], sec["quotes"]
        summary_lbl.text = sector_summary(sd, quotes)
        regime, color, detail = rotation_banner(sec["rotation"])
        rotation_lbl.text = f"{regime} — {detail}"
        rotation_lbl.style(f"color:{color}")
        rows = sector_table_rows(sd, quotes, sec["trends"], sec["pcr"],
                                 sec["quadrants"])
        sector_box.clear()
        with sector_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                for _f, hdr, w in SEC_COLS:
                    ui.label(hdr).style(f"width:{w}px")
            for r in rows:
                with ui.row().classes("items-center w-full no-wrap gap-2 text-sm"):
                    ui.label(str(r["sector"] or "")).style("width:140px")
                    ui.label(str(r["etf"] or "")).style("width:50px")
                    ui.label(str(r["desc"] or "")).style(
                        "width:200px;overflow:hidden;text-overflow:ellipsis;"
                        "white-space:nowrap")
                    for fld in ("day", "week", "month"):
                        v = r[fld]
                        txt = f"{v:+.2f}%" if v is not None else "—"
                        ui.label(txt).style(f"width:70px;color:{pct_color(v)}")
                    pv = r["pcr"]
                    ui.label(f"{pv:.2f}" if pv is not None else "").style(
                        f"width:56px;color:{pcr_color(pv)}")
                    rv = r["rrg"]
                    ui.label(str(rv or "")).style(f"width:90px;color:{rrg_color(rv)}")
        # refill rotation/sector Value cells in the component table now loaded
        if state["snaps"]:
            rotation_value, sector_value = _comp_context()
            _render_components(state["snaps"][-1], rotation_value, sector_value)

    async def load_sectors():
        # Re-entrancy guard: the sector fetch (~24 proxy calls incl. /chains)
        # can outlast a refresh interval; never stack a second one.
        if state.get("loading_sectors"):
            return
        state["loading_sectors"] = True
        sec_spinner.visible = True
        try:
            state["sector"] = await ng_run.io_bound(_load_sector_perf, state["spy"])
            _apply_sectors()
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Sector load failed: {e}", type="negative")
        finally:
            sec_spinner.visible = False
            state["loading_sectors"] = False

    async def load(with_sectors=False):
        # Composite refresh. Sectors are loaded only on initial load and on
        # explicit Refresh — the 120s auto-timer is composite-only so the
        # heavy /chains fetch can't stack on a slow proxy.
        if state.get("loading"):
            return
        state["loading"] = True
        spinner.visible = True
        try:
            snaps, spy = await ng_run.io_bound(_load_snapshots)
            state["snaps"], state["spy"] = snaps, spy
            _apply()
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Sentiment load failed: {e}", type="negative")
        finally:
            spinner.visible = False
            state["loading"] = False
        if with_sectors:
            await load_sectors()

    ui.timer(0.1, lambda: load(with_sectors=True), once=True)
    ui.timer(120.0, load)
