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


def render():
    import nicegui.run as ng_run
    from nicegui import ui

    from pages.options.svg import speedometer_svg, gradient_bar_svg

    state = {"snaps": [], "spy": []}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Market Sentiment").classes("text-h6")
        date_lbl = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        spinner = ui.spinner(size="sm")
        spinner.visible = False
        ui.button(icon="refresh", on_click=lambda: load()).props("flat round")

    gauge_box = ui.html("").classes("q-mt-sm")
    bias_lbl = ui.label("").classes("text-h6")
    sub_lbl = ui.label("").classes("opacity-80")
    comp_box = ui.column().classes("w-full q-gutter-xs q-mt-md")
    ui.separator().classes("q-my-md")
    ui.label("30-Day History").classes("text-subtitle1")
    hist_plot = ui.plotly(build_history_figure([])).classes("w-full")
    vel_lbl = ui.label("").classes("opacity-80 text-sm")
    flag_lbl = ui.label("").classes("text-negative text-sm")
    div_lbl = ui.label("").classes("text-warning text-sm")
    ui.separator().classes("q-my-md")
    ui.label("Market Trend Regime").classes("text-subtitle1")
    regime_badge = ui.badge("").classes("text-subtitle2 q-pa-sm")
    regime_desc = ui.label("").classes("opacity-80 text-sm")
    regime_detail = ui.label("").classes("opacity-60 text-xs")

    def _render_components(latest):
        comp_box.clear()
        scores = latest.get("component_scores") or {}
        confs = latest.get("component_confidence") or {}
        with comp_box:
            for key, name, w in COMPONENTS:
                s = _safe_float(scores.get(key))
                c = _safe_float(confs.get(key))
                with ui.row().classes("items-center w-full no-wrap gap-3"):
                    ui.label(name).classes("text-sm").style("width:110px")
                    ui.label(f"{s:.1f}").classes("text-sm text-bold").style("width:34px")
                    ui.html(gradient_bar_svg(s * 10.0))
                    tag = (f"w {w*100:.0f}%" if w else "out of composite")
                    ui.label(tag).classes("opacity-60 text-xs").style("width:120px")
                    ui.label(f"conf {c:.0%}").classes("opacity-60 text-xs")

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
        _render_components(latest)
        hist_plot.update_figure(build_history_figure(snaps))
        _dates, scores = composite_series(snaps[:-1])
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

    async def load():
        spinner.visible = True
        try:
            snaps, spy = await ng_run.io_bound(_load_snapshots)
            state["snaps"], state["spy"] = snaps, spy
            _apply()
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Sentiment load failed: {e}", type="negative")
        finally:
            spinner.visible = False

    ui.timer(0.1, load, once=True)
    ui.timer(120.0, load)
