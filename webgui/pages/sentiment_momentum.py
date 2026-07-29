"""Momentum page — regime banner · quadrant scatter · rank ribbon + leaderboard.

Tier-1 reader: reads ``cache:sentiment:momentum`` only. No proxy calls, no
engine imports, no compute — the nightly cascade in ``services/sentiment_svc``
puts everything the page needs in that one view.

Tailwind-first (no inline styles); the charts are PLAIN Highcharts charts, never
stockChart — an in-place ``chart.update()`` throws in the stock module and
silently freezes an open page.
"""
import bus_client
from pages.options.theme import BTN_3D
from pages.ui_guard import guard

VIEW = "sentiment:momentum"

# In `suppressed` the banner is the loud element on the page and the
# leaderboard renders muted beneath it.
BANNER_CLASSES = {
    "favorable": "text-[#66bb6a] text-subtitle1 text-bold",
    "neutral": "text-[#ffd54f] text-subtitle1 text-bold",
    "suppressed": "text-[#ef5350] text-h6 text-bold",
}

# Deliberately the SAME four names the RRG page uses (pages.sentiment_rotation
# _QUAD_COLOR). Both charts are 2x2 strength-vs-rate-of-change scatters sitting on
# adjacent tabs in one nav group, so two vocabularies that agree on half the
# corners read as a bug rather than a distinction. The axes still differ — RRG is
# purely relative to SPY, this x is a five-component blend — and that nuance lives
# in the tooltip and page help, not in a second set of names.
QUADRANTS = {
    "leading": "Leading",       # strong and still accelerating
    "improving": "Improving",   # weak but turning up — the early screen
    "weakening": "Weakening",   # strong but decelerating — late, do not chase
    "lagging": "Lagging",
}

LEVEL_OPTIONS = {"industry": "Industries", "stock": "Stocks"}

# Sector colours reused across the scatter series (finite palette, no runtime
# hex — the Tailwind-first rule applies to classes; chart config is exempt).
_SERIES_COLORS = [
    "#42a5f5", "#66bb6a", "#ffa726", "#ab47bc", "#26c6da", "#ef5350",
    "#8d6e63", "#d4e157", "#5c6bc0", "#ec407a", "#78909c",
]

_ALIGN_FILLED = "▮"
_ALIGN_HOLLOW = "▯"

# The zero lines are this chart's frame of reference — which side of each you
# are on IS the quadrant — so they read louder than the gridlines behind them.
ZERO_LINE_COLOR = "rgba(255,255,255,0.55)"
ZERO_LINE_WIDTH = 2
_GRID_COLOR = "rgba(255,255,255,0.06)"

# Matches the RRG page's corner-label treatment (same vocabulary, same look).
_QUAD_LABEL_STYLE = {"color": "rgba(255,255,255,0.34)", "fontSize": "11px",
                     "fontWeight": "bold", "letterSpacing": "2px",
                     "textTransform": "uppercase"}


def _zero_line():
    return {"value": 0, "width": ZERO_LINE_WIDTH, "color": ZERO_LINE_COLOR,
            "zIndex": 4}


def quadrant_label_bands():
    """Four invisible x-bands whose only job is to carry a corner label.

    Split at 0, not the RRG's 100 — these axes are z-scores centred on zero.
    The band gives left/right; the label's verticalAlign gives top/bottom.
    """
    def band(right, top, text):
        return {
            "from": 0 if right else -1e9,
            "to": 1e9 if right else 0,
            "color": "rgba(0,0,0,0)",          # invisible band — label only
            "zIndex": 0,
            "label": {"text": text,
                      "align": "right" if right else "left",
                      "textAlign": "right" if right else "left",
                      "verticalAlign": "top" if top else "bottom",
                      "x": -10 if right else 10,
                      "y": 18 if top else -12,
                      "style": dict(_QUAD_LABEL_STYLE)},
        }
    # Strong is to the right, accelerating is up.
    return [band(True, True, QUADRANTS["leading"]),
            band(True, False, QUADRANTS["weakening"]),
            band(False, True, QUADRANTS["improving"]),
            band(False, False, QUADRANTS["lagging"])]


def _num(value, digits=2, dash="—"):
    if value is None:
        return dash
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return dash


def rows_for(payload, level):
    """The scored rows for one level, defensively."""
    if not payload:
        return []
    return (payload.get("levels") or {}).get(level) or []


def rank_history_for(payload, level):
    """{symbol: [(date, rank)]} for one level — the ribbon's input."""
    if not payload:
        return {}
    return (payload.get("rank_history") or {}).get(level) or {}


def status_text(payload):
    if not payload or not payload.get("session_date"):
        return "Waiting for sentiment service…"
    return f"Session {payload['session_date']}"


# --- regime banner ----------------------------------------------------------

def banner_parts(regime):
    """(headline, tailwind classes) for the regime banner.

    The current regime and the lookback it implies are what make the rest of
    the screen interpretable at a glance, so they lead the page.
    """
    regime = regime or {}
    state = regime.get("state") or "neutral"
    label = regime.get("label") or state.title()
    lookback = regime.get("lookback") or "—"
    text = f"Momentum {label} — weighting the {lookback} lookback"
    if regime.get("crash_risk"):
        text += " · momentum-crash risk"
    return text, BANNER_CLASSES.get(state, BANNER_CLASSES["neutral"])


def banner_reasons(regime):
    return list((regime or {}).get("reasons") or [])


def leaderboard_muted(regime):
    """True when the leaderboard should render muted beneath the banner."""
    return (regime or {}).get("state") == "suppressed"


# --- quadrant scatter -------------------------------------------------------

def quadrant_for(score, accel):
    """Which corner a row sits in — the vocabulary the chart labels use."""
    if score is None or accel is None:
        return ""
    if score >= 0:
        return QUADRANTS["leading"] if accel >= 0 else QUADRANTS["weakening"]
    return QUADRANTS["improving"] if accel >= 0 else QUADRANTS["lagging"]


def _accel(row):
    return (row.get("components") or {}).get("accel")


def quadrant_figure(rows, title="Momentum vs acceleration"):
    """Score on x, acceleration on y, bubble by rank, one series per sector.

    This chart replaces about six tables: where a name sits relative to the
    axes says whether the move is starting, running, or finished.
    """
    groups = {}
    for row in rows or []:
        if row.get("score") is None or _accel(row) is None:
            continue
        groups.setdefault(row.get("sector") or "All", []).append({
            "x": float(row["score"]),
            "y": float(_accel(row)),
            "name": row.get("label") or row.get("symbol"),
            "symbol_code": row.get("symbol"),
            "quadrant": quadrant_for(row["score"], _accel(row)),
        })

    series = [{"name": name, "data": data,
               "color": _SERIES_COLORS[i % len(_SERIES_COLORS)],
               "marker": {"radius": 5, "symbol": "circle"}}
              for i, (name, data) in enumerate(sorted(groups.items()))]

    return {
        "accessibility": {"enabled": False},
        "chart": {"type": "scatter", "backgroundColor": "transparent",
                  "height": 420, "zoomType": "xy"},
        "title": {"text": title, "style": {"color": "#cdd8ee"}},
        "credits": {"enabled": False},
        "legend": {"itemStyle": {"color": "#8794b4"}},
        "xAxis": {
            "title": {"text": "Momentum score (z)", "style": {"color": "#8794b4"}},
            "labels": {"style": {"color": "#8794b4"}},
            "gridLineColor": _GRID_COLOR, "lineColor": "rgba(255,255,255,0.15)",
            "plotLines": [_zero_line()],
            "plotBands": quadrant_label_bands(),
        },
        "yAxis": {
            "title": {"text": "Acceleration (z)", "style": {"color": "#8794b4"}},
            "labels": {"style": {"color": "#8794b4"}},
            "gridLineColor": _GRID_COLOR,
            "plotLines": [_zero_line()],
        },
        "tooltip": {
            "pointFormat": ("<b>{point.name}</b><br/>score {point.x:.2f} · "
                            "accel {point.y:.2f}<br/>{point.quadrant}"),
        },
        "plotOptions": {"series": {"states": {"inactive": {"enabled": False}}}},
        "series": series,
    }


# --- rank ribbon ------------------------------------------------------------

def ribbon_figure(rank_history, title="Rank over the last sessions"):
    """Bump chart of rank over time. Rank 1 sits at the top via a reversed axis.

    Reversing the axis (rather than negating the data) keeps the tooltip
    showing the real rank number.
    """
    series, categories = [], []
    for symbol, points in sorted((rank_history or {}).items()):
        if not points:
            continue
        for date, _rank in points:
            if date not in categories:
                categories.append(date)
        series.append({"name": symbol,
                       "data": [rank for _d, rank in points],
                       "marker": {"enabled": False}})
    categories.sort()
    return {
        "accessibility": {"enabled": False},
        "chart": {"type": "line", "backgroundColor": "transparent", "height": 380},
        "title": {"text": title, "style": {"color": "#cdd8ee"}},
        "credits": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {"categories": categories,
                  "labels": {"style": {"color": "#8794b4"}},
                  "gridLineColor": "#213152"},
        "yAxis": {"reversed": True, "title": {"text": "Rank",
                                              "style": {"color": "#8794b4"}},
                  "labels": {"style": {"color": "#8794b4"}},
                  "gridLineColor": "#213152"},
        "plotOptions": {"series": {"states": {"inactive": {"enabled": False}}}},
        "series": series,
    }


# --- leaderboard ------------------------------------------------------------

def rank_delta(row):
    """Movement since the previous stored session, or '' when there is none."""
    rank, prev = row.get("rank"), row.get("rank_prev")
    if rank is None or prev is None:
        return ""
    if prev == rank:
        return "–"
    return f"▲{prev - rank}" if prev > rank else f"▼{rank - prev}"


def _alignment_blocks(alignment):
    if not alignment:
        return ""
    return "".join(_ALIGN_FILLED if bool(b) else _ALIGN_HOLLOW for b in alignment)


def _display_row(row):
    comp = row.get("components") or {}
    return {
        "symbol": row.get("symbol"),
        "label": row.get("label") or row.get("symbol"),
        "sector": row.get("sector") or "",
        "industry": row.get("industry") or "",
        "rank": row.get("rank"),
        "move": rank_delta(row),
        "score": _num(row.get("score")),
        "percentile": _num(row.get("percentile"), 0),
        # A score nobody can decompose is a score nobody trusts at 9:31.
        "trend": _num(comp.get("trend")),
        "rs": _num(comp.get("rs")),
        "accel": _num(comp.get("accel")),
        "path": _num(comp.get("path")),
        "participation": _num(row.get("participation")),
        "alignment": _alignment_blocks(row.get("alignment")),
        "quadrant": quadrant_for(row.get("score"), comp.get("accel")),
    }


def leaderboard_rows(rows, n=15):
    """(top n, bottom n) display rows — never overlapping on a short list."""
    rows = list(rows or [])
    top = rows[:n]
    bottom = rows[len(top):][-n:] if len(rows) > len(top) else []
    return [_display_row(r) for r in top], [_display_row(r) for r in bottom]


LEADERBOARD_COLUMNS = [
    {"name": "rank", "label": "#", "field": "rank", "align": "right"},
    {"name": "move", "label": "Δ", "field": "move", "align": "left"},
    {"name": "label", "label": "Name", "field": "label", "align": "left"},
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "score", "label": "Score", "field": "score", "align": "right"},
    {"name": "percentile", "label": "Pctl", "field": "percentile", "align": "right"},
    {"name": "trend", "label": "Trend", "field": "trend", "align": "right"},
    {"name": "rs", "label": "RS", "field": "rs", "align": "right"},
    {"name": "accel", "label": "Accel", "field": "accel", "align": "right"},
    {"name": "path", "label": "Path", "field": "path", "align": "right"},
    {"name": "participation", "label": "Partic.", "field": "participation",
     "align": "right"},
    {"name": "alignment", "label": "Align", "field": "alignment", "align": "left"},
    {"name": "quadrant", "label": "Quadrant", "field": "quadrant", "align": "left"},
]

# Columns that only mean anything at one level. Rendering a permanently empty
# column reads as a broken page — which is exactly how the blank Align column
# looked on the industry view.
_LEVEL_ONLY = {"alignment": "stock", "participation": "industry"}


def normalise_level(level):
    """Coerce an untrusted level (e.g. a query param) to a known one."""
    return level if level in LEVEL_OPTIONS else "industry"


def section_heading(title, level):
    """'Leaders — Stocks' — so which view you are on is never ambiguous."""
    return f"{title} — {LEVEL_OPTIONS[normalise_level(level)]}"


def leaderboard_columns(level):
    """The column set for one level, minus the columns undefined there."""
    level = normalise_level(level)
    return [c for c in LEADERBOARD_COLUMNS
            if _LEVEL_ONLY.get(c["field"], level) == level]


# --- excluded footer --------------------------------------------------------

def excluded_text(excluded):
    """How many symbols were dropped — the delisted/renamed-ticker tell."""
    n = len(excluded or [])
    return "" if not n else f"{n} symbols excluded"


def excluded_tooltip(excluded):
    return " · ".join(f"{e.get('symbol')}: {e.get('reason')}"
                      for e in (excluded or []))


# --- page -------------------------------------------------------------------

def render(level="industry"):
    from nicegui import ui

    state = {"ver": None, "level": normalise_level(level), "payload": None}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Momentum").classes("text-h6")
        ui.label("Regime-conditioned, across sector · industry · stock") \
            .classes("opacity-60 text-sm")
        status = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        level_sel = ui.select(LEVEL_OPTIONS, value=state["level"]).props("dense outlined")
        ui.button("Refresh", icon="refresh", color=None,
                  on_click=lambda: _request_refresh()).props("no-caps").classes(BTN_3D)

    banner = ui.label("").classes(BANNER_CLASSES["neutral"])
    reasons = ui.label("").classes("opacity-70 text-sm")

    chart_box = ui.column().classes("w-full q-mt-sm")
    with chart_box:
        quadrant = ui.highchart(quadrant_figure([])).classes("w-full")
        ribbon = ui.highchart(ribbon_figure({})).classes("w-full q-mt-sm")

    board = ui.column().classes("w-full q-mt-sm gap-2")
    with board:
        top_head = ui.label("").classes("text-subtitle2 opacity-80")
        top_table = ui.table(columns=leaderboard_columns(state["level"]), rows=[],
                             row_key="symbol").classes("w-full")
        bottom_head = ui.label("").classes("text-subtitle2 opacity-80")
        bottom_table = ui.table(columns=leaderboard_columns(state["level"]), rows=[],
                                row_key="symbol").classes("w-full")

    footer = ui.label("").classes("opacity-60 text-xs")

    def _paint(payload):
        regime = (payload or {}).get("regime") or {}
        text, cls = banner_parts(regime)
        banner.text = text
        banner.classes(remove=" ".join(BANNER_CLASSES.values()), add=cls)
        reasons.text = " · ".join(banner_reasons(regime))
        status.text = status_text(payload)

        rows = rows_for(payload, state["level"])
        quadrant.options = quadrant_figure(
            rows, f"{LEVEL_OPTIONS[state['level']]} — momentum vs acceleration")
        quadrant.update()
        ribbon.options = ribbon_figure(
            rank_history_for(payload, state["level"]),
            f"{LEVEL_OPTIONS[state['level']]} — rank over recent sessions")
        ribbon.update()

        top, bottom = leaderboard_rows(rows)
        cols = leaderboard_columns(state["level"])
        top_head.text = section_heading("Leaders", state["level"])
        bottom_head.text = section_heading("Laggards", state["level"])
        for table, data in ((top_table, top), (bottom_table, bottom)):
            table.columns = cols
            table.rows = data
            table.update()
        # Muted beneath a suppressed banner — a leaderboard nobody should
        # trade must not read as the headline.
        board.classes(remove="opacity-40", add="opacity-40"
                      if leaderboard_muted(regime) else "")

        excluded = (payload or {}).get("excluded") or []
        footer.text = excluded_text(excluded)
        footer.tooltip(excluded_tooltip(excluded))

    @guard
    def _apply():
        state["payload"] = bus_client.read(VIEW) or {}
        _paint(state["payload"])

    @guard
    def _on_level(event):
        state["level"] = event.value or "industry"
        _paint(state["payload"])

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_momentum"})
        ui.notify("Momentum refresh requested")

    @guard
    def _maybe_repaint():
        ver = bus_client.read_version(VIEW)
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _apply()

    level_sel.on_value_change(_on_level)
    state["ver"] = bus_client.read_version(VIEW)
    _apply()
    ui.timer(2.0, _maybe_repaint)
