"""Trade page (Tier-3 reader) — single-symbol MTF analysis + Buy/Hold/Sell verdicts.

This page holds **no engine call**: the full orchestration (fetch MTF data,
compute indicators, score the Position/Investor verdict engines) lives in
``services/trade_svc/compute`` (``analyze``). The page enqueues an ``analyze``
command and renders the cached ``TradeAnalysis`` result.

Interaction model:

* **Analyze** (button or Enter) → enqueue ``{"type":"analyze","args":{"symbol":…}}``
  on ``cmd:trade``; a version-poll on ``trade:analysis`` repaints from the cache.

The result persists across navigation (single-user, like the other pages): a
prior analysis paints instantly on revisit. The pure display builders
(``verdict_color``/``momentum_rows``/``breakdown_rows``/``alignment_rows``) are
unit-tested.

Fundamentals are not wired (MVP): the Investor verdict uses technicals/RS only
and degrades to "Insufficient fundamental data → HOLD" — a note flags this.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from .options.inputs import select_all_on_focus

BUY_COLOR = "#2e7d32"
HOLD_COLOR = "#f9a825"
SELL_COLOR = "#c62828"


def verdict_color(verdict):
    """Green BUY / amber HOLD / red SELL (HOLD is the default for anything else)."""
    v = (verdict or "").upper()
    if v == "BUY":
        return BUY_COLOR
    if v == "SELL":
        return SELL_COLOR
    return HOLD_COLOR


def bias_color(bias):
    """Color a BULLISH/BEARISH/NEUTRAL bias label."""
    b = (bias or "").upper()
    if b == "BULLISH":
        return BUY_COLOR
    if b == "BEARISH":
        return SELL_COLOR
    return HOLD_COLOR


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def fundamentals_rows(f):
    """(label, value) pairs for the fundamentals card; '—' for missing values.

    Growth/ROE are stored as fractions and shown as percents; margin trend and
    days-to-earnings render only when present.
    """
    if not f:
        return []
    rows = [
        ("P/E", _fmt(f.get("pe_ratio"), 1)),
        ("PEG", _fmt(f.get("peg_ratio"), 2)),
        ("Rev growth", _pct(f.get("rev_growth_ttm"))),
        ("EPS growth", _pct(f.get("eps_growth_ttm"))),
        ("ROE", _pct(f.get("roe"))),
    ]
    me = f.get("margin_expanding")
    rows.append(("Margins", "expanding" if me else "contracting" if me is False else "—"))
    dte = f.get("days_to_earnings")
    if dte is not None:
        rows.append(("Earnings in", f"{dte}d"))
    return rows


def momentum_rows(m):
    """(label, value) pairs for the momentum strip; '—' for missing values."""
    if not m:
        return []
    return [
        ("RSI", _fmt(m.get("rsi"))),
        ("ADX", _fmt(m.get("adx"))),
        ("MACD hist", _fmt(m.get("macd_hist"), 3)),
        ("VWAP", _fmt(m.get("vwap"), 2)),
        ("Rel Vol", _fmt(m.get("relative_volume"), 2)),
    ]


def breakdown_rows(verdict):
    """Factor-breakdown rows for a verdict's contribution table."""
    rows = []
    for b in (verdict or {}).get("breakdown", []):
        rows.append({
            "factor": b.get("factor", ""),
            "weight": b.get("weight", 0),
            "raw_score": b.get("raw_score", 0),
            "contribution": round(float(b.get("contribution", 0.0)), 1),
        })
    return rows


def alignment_rows(ema):
    """(timeframe, status) rows for the multi-timeframe EMA-alignment table."""
    rows = []
    for tf in (ema or {}).get("timeframes", []):
        rows.append({"timeframe": tf.get("timeframe", ""),
                     "status": tf.get("status", "")})
    return rows


_MK_BAND_COLORS = ["#c0392b", "#e67e22", "#7f8c8d", "#27ae60", "#1e8449"]
# stacked-area band fill colors (neutral grey lighter than the chip's grey)
_MK_AREA_COLORS = ["#c0392b", "#e67e22", "#bdc3c7", "#27ae60", "#1e8449"]
# Dark-navy chart styling (matches the dashboard theme the Calculator/Simulator share).
_MK_AXIS_STYLE = {"color": "#bdbdbd"}
_MK_GRID_COLOR = "rgba(255,255,255,0.08)"


def markov_band_chip(mk):
    """{'label','color'} for the current Markov band, or None when no block."""
    if not mk:
        return None
    i = mk.get("current_band", 2)
    labels = mk.get("band_labels") or ["?"] * 5
    color = _MK_BAND_COLORS[i] if 0 <= i < len(_MK_BAND_COLORS) else "#7f8c8d"
    label = labels[i] if 0 <= i < len(labels) else "?"
    return {"label": label, "color": color}


def markov_metric_rows(mk):
    """Per-horizon metric rows: horizon / P(buy) / P(sell) / E[score]."""
    if not mk:
        return []
    rows = []
    for h in mk.get("horizons", []):
        rows.append({
            "horizon": f"{h.get('n', '?')}d",
            "p_buy": f"{round(h.get('p_buy', 0) * 100)}%",
            "p_sell": f"{round(h.get('p_sell', 0) * 100)}%",
            "e_score": f"{h.get('e_score', 0):+.0f}",
        })
    return rows


def markov_drift_row(mk):
    """Formatted drift/tilt/confidence/adjusted-score strings, or None."""
    if not mk:
        return None
    return {
        "drift": f"{mk.get('drift', 0):+.0f}",
        "tilt": f"{mk.get('tilt', 0):+.0f}",
        "confidence": f"{round(mk.get('confidence', 0) * 100)}%",
        "persistence": f"{round(mk.get('persistence', 0) * 100)}%",
        "adjusted": f"{mk.get('markov_adjusted_score', 0):.0f}",
    }


def position_headline(pv, mk):
    """Headline score for the Position card: the Markov-adjusted score when a
    markov block is present (with the base score + tilt exposed for a subtitle),
    else the plain base score. Does NOT change the BUY/HOLD/SELL verdict label."""
    base = int(round((pv or {}).get("score", 0)))
    if not mk:
        return {"score": base, "base": base, "tilt": ""}
    adj = int(round(mk.get("markov_adjusted_score", base)))
    return {"score": adj, "base": base, "tilt": f"{mk.get('tilt', 0):+.0f}"}


def markov_forecast_figure(mk):
    """Highcharts stacked-area option dict: band probability over horizon.

    Plots the DENSE near-term trajectory (now/1/2/3/5/10/20d from ``mk['trajectory']``,
    falling back to the sparse ``mk['horizons']`` when absent) so the score-specific
    early path is visible — the 10d/20d tail converges to the prior stationary and is
    near-identical across symbols, which made the chart look the same for every stock.
    Dark-navy themed (transparent bg, light axes) so it sits on the dashboard navy.

    Tolerates None (returns an empty-but-valid figure with an explicit height so the
    persistent chart element renders at a stable size before data arrives)."""
    base = {
        "accessibility": {"enabled": False},
        "chart": {"type": "area", "height": 200, "backgroundColor": "transparent"},
        "title": {"text": None},
        "credits": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#cdd8ee"},
                   "itemHoverStyle": {"color": "#ffffff"}},
    }
    if not mk:
        return {**base, "series": []}
    labels = mk.get("band_labels") or ["?"] * 5
    points = mk.get("trajectory") or mk.get("horizons") or []
    cats = ["now"] + [f"{h['n']}d" for h in points]
    now = [0.0] * 5
    cb = mk.get("current_band", 2)
    if 0 <= cb < 5:
        now[cb] = 1.0
    dists = [now] + [h["dist"] for h in points]
    series = [{
        "name": labels[b] if b < len(labels) else "?",
        "color": _MK_AREA_COLORS[b],
        "data": [round(d[b] if b < len(d) else 0.0, 4) for d in dists],
    } for b in range(5)]
    return {
        **base,
        "xAxis": {"categories": cats, "labels": {"style": _MK_AXIS_STYLE},
                  "lineColor": _MK_GRID_COLOR, "tickColor": _MK_GRID_COLOR},
        "yAxis": {"min": 0, "max": 100, "title": {"text": "P(band)", "style": _MK_AXIS_STYLE},
                  "labels": {"format": "{value}%", "style": _MK_AXIS_STYLE},
                  "gridLineColor": _MK_GRID_COLOR},
        "plotOptions": {"area": {"stacking": "percent", "marker": {"enabled": False}}},
        "series": series,
    }


_BREAKDOWN_COLS = [
    {"name": "factor", "label": "Factor", "field": "factor", "align": "left"},
    {"name": "weight", "label": "Wt", "field": "weight"},
    {"name": "raw_score", "label": "Raw", "field": "raw_score"},
    {"name": "contribution", "label": "Contrib", "field": "contribution"},
]


def render():
    """Trade page: symbol input + Analyze button + verdict/MTF/momentum cards."""
    ui.label("Trade Analyzer").classes("text-h5")

    # Page state (local closure, not module globals — built per request).
    state = {"result": None, "ver": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="AAPL").classes("w-32"))
        analyze_btn = ui.button("Analyze", icon="analytics")
        status = ui.label("Enter a symbol and click Analyze.").classes("opacity-70 text-sm")

    # Layout: a top area (error banner + header), then a single verdict ROW of
    # three EQUAL-width cards (Position · Investor · Markov Forecast), then a
    # bottom area (MTF/momentum/sector). results_top/bottom are cleared+rebuilt on
    # repaint; the verdict row and its three cards are PERSISTENT — the Markov card
    # holds a Highcharts element that must exist at first render (a chart added
    # later on a chart-less page fails to resolve `nicegui-highcharts`) and must
    # not be destroyed by a clear(), so the verdict cards are refilled in place.
    results_top = ui.column().classes("w-full gap-3")
    verdict_row = ui.row().classes("w-full gap-3 items-stretch flex-wrap")
    with verdict_row:
        position_card = ui.card().classes("flex-1 min-w-[280px]")
        investor_card = ui.card().classes("flex-1 min-w-[280px]")
        markov_card = ui.card().classes("flex-1 min-w-[280px]")
        with markov_card:
            ui.label("Markov Forecast · composite-score regime").classes("text-subtitle2 opacity-70")
            markov_head = ui.row().classes("items-center gap-3 flex-wrap")
            markov_metrics = ui.row().classes("gap-4 flex-wrap")
            markov_chart = ui.highchart(markov_forecast_figure(None)).classes("w-full")
    verdict_row.set_visibility(False)
    markov_card.set_visibility(False)
    results_bottom = ui.column().classes("w-full gap-3")

    # ── card builders (widgets; pull from the pure transforms above) ──────────
    def _header(res):
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-4 flex-wrap"):
                ui.label(res.get("symbol", "")).classes("text-h5")
                desc = res.get("description")
                if desc and desc != res.get("symbol"):
                    ui.label(desc).classes("opacity-70")
                price = res.get("price")
                if price is not None:
                    ui.label(f"${price:,.2f}").classes("text-h6")
                bias = res.get("bias")
                if bias:
                    ui.label(bias).classes("text-weight-bold px-2 rounded") \
                        .style(f"color:{bias_color(bias)}")
                vol = res.get("volume")
                if vol:
                    ui.label(f"Vol {vol:,}").classes("opacity-60 text-sm")

    def _fill_verdict_card(card, title, verdict, mk=None):
        # Refill a PERSISTENT verdict card in place (clear+rebuild its contents) so
        # it can share the verdict row with the persistent Markov chart card.
        card.clear()
        verdict = verdict or {}
        # When a Markov block is present, headline the adjusted score (base + tilt
        # in a subtitle). The verdict WORD/color are unchanged — never re-derived.
        head = position_headline(verdict, mk) if mk else None
        with card:
            ui.label(title).classes("text-subtitle2 opacity-70")
            with ui.row().classes("items-baseline gap-3"):
                ui.label(verdict.get("verdict", "—")).classes("text-h4 text-weight-bold") \
                    .style(f"color:{verdict_color(verdict.get('verdict'))}")
                if head and isinstance(verdict.get("score"), int):
                    ui.label(f"score {head['score']:+d}").classes("opacity-70")
                else:
                    ui.label(f"score {verdict.get('score', 0):+d}"
                             if isinstance(verdict.get("score"), int)
                             else "").classes("opacity-70")
            if head and head["tilt"] and isinstance(verdict.get("score"), int):
                ui.label(f"base {head['base']:+d} · Markov {head['tilt']}") \
                    .classes("text-xs opacity-60")
            for r in verdict.get("top_reasons", []):
                ui.label(f"• {r}").classes("text-sm opacity-80")
            for g in verdict.get("gates_triggered", []):
                ui.label(f"⛔ {g}").classes("text-xs").style(f"color:{SELL_COLOR}")
            rows = breakdown_rows(verdict)
            if rows:
                with ui.expansion("Factor breakdown").classes("w-full"):
                    ui.table(columns=_BREAKDOWN_COLS, rows=rows,
                             row_key="factor").classes("w-full").props("dense")

    def _alignment_card(ema):
        ema = ema or {}
        with ui.card().classes("flex-1 min-w-[220px]"):
            ui.label("MTF EMA alignment").classes("text-subtitle2 opacity-70")
            pct = ema.get("alignment_percentage")
            ui.label(f"{pct:+.0f}%" if pct is not None else "—") \
                .classes("text-h6").style(f"color:{bias_color(ema.get('bias'))}")
            for r in alignment_rows(ema):
                with ui.row().classes("items-center gap-2 w-full justify-between"):
                    ui.label(r["timeframe"]).classes("text-sm opacity-80")
                    ui.label(r["status"]).classes("text-xs") \
                        .style(f"color:{bias_color(r['status'])}")

    def _momentum_card(m):
        with ui.card().classes("flex-1 min-w-[200px]"):
            ui.label("Momentum").classes("text-subtitle2 opacity-70")
            for label, value in momentum_rows(m):
                with ui.row().classes("items-center gap-2 w-full justify-between"):
                    ui.label(label).classes("text-sm opacity-80")
                    ui.label(value).classes("text-sm text-weight-medium")

    def _fundamentals_card(fundamentals):
        with ui.card().classes("flex-1 min-w-[200px]"):
            ui.label("Fundamentals").classes("text-subtitle2 opacity-70")
            for label, value in fundamentals_rows(fundamentals):
                with ui.row().classes("items-center gap-2 w-full justify-between"):
                    ui.label(label).classes("text-sm opacity-80")
                    ui.label(value).classes("text-sm text-weight-medium")

    def _sector_card(sector):
        sector = sector or {}
        strength = sector.get("strength") or {}
        with ui.card().classes("flex-1 min-w-[200px]"):
            ui.label("Sector").classes("text-subtitle2 opacity-70")
            name = sector.get("name") or "Unknown"
            etf = sector.get("etf")
            ui.label(f"{name}" + (f" ({etf})" if etf else "")).classes("text-sm")
            sc = strength.get("score")
            if sc is not None:
                ui.label(f"Strength {sc:+d}").classes("text-h6") \
                    .style(f"color:{BUY_COLOR if sc > 0 else SELL_COLOR if sc < 0 else HOLD_COLOR}")
            if strength.get("in_confirmed_downtrend"):
                ui.label("Confirmed downtrend").classes("text-xs").style(f"color:{SELL_COLOR}")

    def _render_results():
        res = state["result"]
        results_top.clear()
        results_bottom.clear()
        if not res:
            verdict_row.set_visibility(False)
            with results_top:
                ui.label("No analysis yet — enter a symbol and click Analyze.") \
                    .classes("opacity-70")
            return
        with results_top:
            if res.get("errors"):
                with ui.row().classes("w-full bg-red-2 text-red-10 rounded p-3 "
                                      "items-center gap-2"):
                    ui.icon("warning")
                    ui.label("; ".join(res["errors"]))
            _header(res)
        has_verdict = bool(res.get("position_verdict") or res.get("investor_verdict"))
        verdict_row.set_visibility(has_verdict)
        if has_verdict:
            # Three EQUAL-width cards in one row: Position · Investor · Markov.
            # (The Markov card's own visibility is managed by _update_markov.)
            _fill_verdict_card(position_card, "Position · 1–8 wk",
                               res.get("position_verdict"), res.get("markov"))
            _fill_verdict_card(investor_card, "Investor · months+",
                               res.get("investor_verdict"))
            with results_bottom:
                with ui.row().classes("w-full gap-3 items-stretch flex-wrap"):
                    _alignment_card(res.get("ema_alignment"))
                    _momentum_card(res.get("momentum"))
                    _sector_card(res.get("sector"))
                    if res.get("fundamentals_available"):
                        _fundamentals_card(res.get("fundamentals"))
                if not res.get("fundamentals_available"):
                    ui.label("Fundamentals unavailable for this symbol — the "
                             "Investor verdict degrades to HOLD on insufficient "
                             "data.").classes("text-xs opacity-60")

    @guard
    def _update_markov(res):
        mk = (res or {}).get("markov")
        if not mk:
            markov_card.set_visibility(False)
            return
        markov_card.set_visibility(True)
        markov_head.clear()
        with markov_head:
            chip = markov_band_chip(mk)
            if chip:
                ui.label(chip["label"]).classes("text-weight-bold px-2 py-1 rounded text-white") \
                    .style(f"background:{chip['color']}")
            dr = markov_drift_row(mk)
            if dr:
                ui.label(f"drift {dr['drift']} · tilt {dr['tilt']} · conf "
                         f"{dr['confidence']} · persist {dr['persistence']}") \
                    .classes("text-sm opacity-80")
                ui.label(f"adj score {dr['adjusted']}").classes("text-sm text-weight-medium")
            pv = mk.get("prior_version")
            if pv:
                ui.label(f"prior {pv}").classes("text-xs opacity-50")
        markov_metrics.clear()
        with markov_metrics:
            for r in markov_metric_rows(mk):
                with ui.column().classes("items-center gap-0"):
                    ui.label(r["horizon"]).classes("text-xs opacity-60")
                    ui.label(f"E {r['e_score']}").classes("text-h6")
                    ui.label(f"↑{r['p_buy']} ↓{r['p_sell']}").classes("text-xs opacity-80")
        markov_chart.options = markov_forecast_figure(mk)
        markov_chart.update()
        # reflow after the (possibly previously-hidden) chart is visible so it
        # doesn't render collapsed (no ResizeObserver in nicegui-highcharts).
        ui.timer(0.05, lambda: ui.run_javascript(
            f"getElement({markov_chart.id})?.chart?.reflow()"), once=True)

    def _status_for(res):
        if not res:
            return "Enter a symbol and click Analyze."
        sym = res.get("symbol", "")
        if res.get("errors"):
            return f"{sym}: {res['errors'][0]}"
        price = res.get("price")
        pv = (res.get("position_verdict") or {}).get("verdict", "—")
        iv = (res.get("investor_verdict") or {}).get("verdict", "—")
        px = f" · ${price:,.2f}" if price is not None else ""
        return f"{sym}{px} · Position {pv} / Investor {iv}"

    # ── command enqueue ───────────────────────────────────────────────────────
    @guard
    def _request_analyze():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        bus_client.request("trade", {"type": "analyze", "args": {"symbol": sym}})
        status.text = f"Analyzing {sym}…"

    analyze_btn.on_click(_request_analyze)
    symbol_in.on("keydown.enter", lambda e: _request_analyze())

    # ── version-poll repaint (fetch-free) ─────────────────────────────────────
    @guard
    def _poll():
        version = bus_client.read_version("trade:analysis")
        if version == state["ver"]:
            return
        state["ver"] = version
        state["result"] = bus_client.read("trade:analysis") or None
        _render_results()
        _update_markov(state["result"])
        status.text = _status_for(state["result"])

    # Initial paint (graceful-empty when the service is cold / no prior analysis).
    state["ver"] = bus_client.read_version("trade:analysis")
    state["result"] = bus_client.read("trade:analysis") or None
    _render_results()
    _update_markov(state["result"])
    status.text = _status_for(state["result"])
    ui.timer(2.0, _poll)
