"""Momentum page — a numbered argument, not a dashboard.

Tier-1 reader: reads ``cache:sentiment:momentum`` only. No proxy calls, no
engine imports, no compute — the nightly cascade in ``services/sentiment_svc``
puts everything the page needs in that one view.

**Rebuilt 2026-08-17** from a supplied design, the fourth screen in that family.
The page now walks one argument: (1) is momentum worth trading today, with all
three regimes shown side by side and the dispersion reading behind them; (2) how
much of each level is in its own top quartile, and how many stocks have industry
*and* sector behind them; (3) where the names sit, as quadrant counts; (4) one
worked example, decomposed into its five z-scores; (5) rank over recent
sessions. The **ranked leaderboard survives beneath all of it behind a collapsed
expander** — the orientation read is the default and the screening surface is
one click away.

The Highcharts quadrant scatter and rank ribbon are **gone**; section 3 replaces
the scatter with counts and section 5 draws the ranks as a hand-built SVG. Their
builders are kept below only because their tests still pin them.

All the new arithmetic is pure in ``pages/momentum_view.py``; the leaderboard's
own transforms stay here.
"""
import bus_client
from pages import busy as _busy
from pages import momentum_view as V
from pages.oklch import oklch_hex as _ok
from pages.options.theme import (
    ROTATION_FONT_HEAD_HTML, ROTATION_TOKENS as _T,
)
from pages.rotation_view import NB, NE, NT
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

# A bump chart stops being readable somewhere around a dozen lines; drawing all
# 68 industries produced solid spaghetti that conveyed nothing.
RIBBON_MAX_SERIES = 12


def _latest_rank(points):
    return points[-1][1] if points and points[-1][1] is not None else 10**9


def ribbon_subset(rank_history, n=RIBBON_MAX_SERIES):
    """The n best CURRENTLY ranked symbols — chosen on the latest session.

    Ranking on the latest session (not the first) is what lets a climber into
    the chart; that movement is the whole point of a bump chart.
    """
    have = [(s, p) for s, p in (rank_history or {}).items() if p]
    have.sort(key=lambda sp: (_latest_rank(sp[1]), sp[0]))
    return have[:n]


def ribbon_figure(rank_history, title="Rank over recent sessions",
                  n=RIBBON_MAX_SERIES):
    """Bump chart of rank over time for the current leaders.

    Rank 1 sits at the top via a reversed axis rather than negated data, so the
    tooltip still shows the real rank number.
    """
    chosen = ribbon_subset(rank_history, n)
    total = len([p for p in (rank_history or {}).values() if p])

    categories = sorted({d for _s, pts in chosen for d, _r in pts})
    series = [{"name": symbol,
               "data": [rank for _d, rank in points],
               "marker": {"enabled": True, "radius": 3}}
              for symbol, points in chosen]

    sessions = len(categories)
    if not chosen:
        note = "No ranked history yet — the first nightly run seeds it."
    elif sessions < 2:
        note = (f"Only {sessions} session stored — movement appears once a "
                "second nightly run lands.")
    elif total > len(chosen):
        note = f"Top {len(chosen)} of {total} by current rank · {sessions} sessions"
    else:
        note = f"{total} tracked · {sessions} sessions"

    return {
        "accessibility": {"enabled": False},
        "chart": {"type": "line", "backgroundColor": "transparent", "height": 380},
        "title": {"text": title, "style": {"color": "#cdd8ee"}},
        "subtitle": {"text": note, "style": {"color": "#8794b4",
                                             "fontSize": "11px"}},
        "credits": {"enabled": False},
        # A line you cannot name is a line you cannot use.
        "legend": {"enabled": True, "itemStyle": {"color": "#8794b4",
                                                  "fontWeight": "normal"}},
        "xAxis": {"categories": categories,
                  "labels": {"style": {"color": "#8794b4"}},
                  "gridLineColor": _GRID_COLOR},
        "yAxis": {"reversed": True, "title": {"text": "Rank",
                                              "style": {"color": "#8794b4"}},
                  "labels": {"style": {"color": "#8794b4"}},
                  "gridLineColor": _GRID_COLOR},
        "tooltip": {"shared": False,
                    "pointFormat": "<b>{series.name}</b> — rank {point.y}"},
        # Hover one line, dim the rest — the only way to follow a path in a
        # chart with a dozen overlapping series.
        "plotOptions": {"series": {"states": {"inactive": {"opacity": 0.12}}}},
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


# ── the redesigned page's shared style constants ────────────────────────────
# The numbered step captions above each section. They carry the argument, so
# they are one style and never restated inline.
_MONO = _T["RT_MONO"]
_STEP = (f"{_MONO} {NT['rail']} text-[10px] tracking-[.18em] uppercase "
         "leading-none mb-3")
_ALIGN_PANEL = f"bg-[{_ok(0.17, 0.035, 158)}]"
_ALIGN_EDGE = f"border-[{_ok(0.34, 0.07, 158)}]"
_ALIGN_TITLE = f"text-[{_ok(0.78, 0.11, 158)}]"
_ALIGN_BODY = f"text-[{_ok(0.82, 0.02, 158)}]"
_DISP_TXT = f"text-[{_ok(0.80, 0.13, 80)}]"
_DISP_FILL = f"bg-[{_ok(0.52, 0.10, 80)}]"
_DISP_MARK = f"bg-[{_ok(0.92, 0.08, 80)}]"
_LIMIT_BG = f"bg-[{_ok(0.115, 0.006, 90)}]"
_LIMIT_TAG = f"text-[{_ok(0.62, 0.09, 80)}]"


def render(level="industry"):
    """The Momentum page as a numbered argument.

    Sections 1–5 are the design; the ranked leaderboard survives beneath them
    behind a collapsed expander, so the orientation read is the default and the
    screening surface is one click away.
    """
    from nicegui import ui

    state = {"ver": None, "level": normalise_level(level), "payload": None}

    ui.add_head_html(ROTATION_FONT_HEAD_HTML)

    wrap = ui.column().classes(
        f"{_T['RT_SANS']} {_T['RT_VOID_BG']} {NT['txt']} w-full gap-0 "
        "px-7 pt-9 pb-14 rounded-lg overflow-hidden")

    with wrap:
        # ── header ──────────────────────────────────────────────────────────
        with ui.row().classes("items-end w-full no-wrap gap-7 mb-6"):
            with ui.column().classes("gap-2 min-w-0"):
                eyebrow_lbl = ui.label("").classes(
                    f"{_MONO} {NT['eyebrow']} text-[10.5px] tracking-[.16em] "
                    "uppercase leading-none")
                ui.label("Momentum").classes(
                    "text-[33px] font-semibold leading-none "
                    "tracking-[-0.025em] whitespace-nowrap")
            ui.space()
            with ui.row().classes("items-center no-wrap gap-2"):
                level_sel = ui.select(
                    LEVEL_OPTIONS, value=state["level"],
                    on_change=lambda e: _set_level(e.value)) \
                    .props("outlined dense options-dense borderless") \
                    .classes(f"{_MONO} momentum-level min-w-[132px]")
                ui.button("Refresh", color=None,
                          on_click=lambda: _request_refresh()) \
                    .props("flat no-caps dense").classes(
                        f"{_MONO} {NT['txt']} text-[11px] tracking-[.1em] "
                        f"uppercase bg-transparent border {NE['btn_edge']} "
                        f"px-4 h-[38px] leading-none hover:{NB['btn_hover']}")

        # ── 1 · is momentum worth trading today? ────────────────────────────
        ui.label("1 · Is momentum worth trading today?").classes(_STEP)
        regime_box = ui.row().classes("w-full flex-wrap gap-0.5 mb-0.5")
        disp_box = ui.row().classes(
            "items-center w-full flex-wrap gap-[26px] px-[22px] py-[18px] "
            f"mb-9 {V.LEVEL_GROOVE} border {NE['hair']}")

        # ── 2 · three levels ────────────────────────────────────────────────
        ui.label("2 · Three levels, and where they agree").classes(_STEP)
        with ui.row().classes("w-full flex-wrap gap-0.5 mb-9 items-stretch"):
            with ui.column().classes(
                    f"flex-[1_1_520px] min-w-[300px] px-6 pt-6 pb-[26px] gap-4 "
                    f"{V.LEVEL_GROOVE} border {NE['hair']}"):
                levels_box = ui.column().classes("w-full gap-4")
                ui.label("Bright segment = names in the top quartile of their "
                         "level · track width scales with universe size (√)") \
                    .classes(f"{_MONO} {NT['axis']} text-[9.5px] "
                             "tracking-[.12em] uppercase leading-[1.7] pt-0.5")
            with ui.column().classes(
                    f"flex-[1_1_300px] min-w-[280px] p-6 gap-3.5 "
                    f"{_ALIGN_PANEL} border {_ALIGN_EDGE}"):
                ui.label("Align · all three agree").classes(
                    f"{_MONO} {_ALIGN_TITLE} text-[11px] tracking-[.18em] "
                    "uppercase leading-none")
                with ui.row().classes("items-center no-wrap gap-4"):
                    with ui.row().classes("no-wrap gap-[3px]"):
                        for _ in range(3):
                            ui.element("div").classes(
                                f"w-4 h-[26px] {V.ALIGN_ON}")
                    align_lbl = ui.label("0").classes(
                        f"{_MONO} {NT['bright']} text-[34px] font-medium "
                        "leading-none tracking-[-0.03em]")
                    ui.label("stocks whose industry and sector both confirm") \
                        .classes(f"text-[13px] leading-[1.35] {_ALIGN_BODY}")
                ui.label("The highest-conviction rows on the page — these are "
                         "the ones to take to Trade Analyzer.").classes(
                    f"text-[13.5px] leading-[1.5] {_ALIGN_BODY}")

        # ── 3 · quadrants ───────────────────────────────────────────────────
        quad_step = ui.label("").classes(_STEP)
        quad_box = ui.element("div").classes(
            "grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-0.5 "
            "w-full mb-9")

        # ── 4 · what a score is made of ─────────────────────────────────────
        ui.label("4 · What a score is made of").classes(_STEP)
        with ui.row().classes("w-full flex-wrap gap-0.5 mb-9 items-stretch"):
            example_box = ui.column().classes(
                f"flex-[0_1_300px] min-w-[260px] p-6 gap-3 "
                f"{V.LEVEL_GROOVE} border {NE['hair']}")
            with ui.column().classes(
                    f"flex-[1_1_460px] min-w-[320px] px-[26px] pt-6 pb-[26px] "
                    f"gap-3.5 {V.LEVEL_GROOVE} border {NE['hair']}"):
                comp_box = ui.column().classes("w-full gap-3.5")
                ui.label("Z-scores · centre line is the universe average") \
                    .classes(f"{_MONO} {NT['axis']} text-[9.5px] "
                             "tracking-[.12em] uppercase leading-[1.7]")

        # ── 5 · rank over recent sessions ───────────────────────────────────
        with ui.row().classes(
                "items-baseline justify-between w-full flex-wrap gap-5 mb-3"):
            rank_step = ui.label("").classes(_STEP + " mb-0")
            ui.label("Steady climbers beat yesterday's jumpers").classes(
                f"{_MONO} {NT['ghost']} text-[10px] tracking-[.12em] "
                "uppercase leading-none")
        with ui.column().classes(
                f"w-full px-[26px] pt-[26px] pb-5 mb-9 gap-0 "
                f"{V.LEVEL_GROOVE} border {NE['hair']}"):
            with ui.row().classes("w-full no-wrap gap-3"):
                rtick_box = ui.element("div").classes(
                    "w-[34px] shrink-0 relative h-[250px]")
                with ui.column().classes("flex-1 min-w-0 gap-0 pr-[52px]"):
                    rank_plot = ui.element("div").classes(
                        "relative h-[250px] w-full")
                    rdate_box = ui.row().classes(
                        "justify-between w-full no-wrap pt-2.5")
            story_lbl = ui.label("").classes(
                f"text-[13px] leading-[1.5] pt-3.5 {NT['body']}")

        # ── the leaderboard, behind a toggle ────────────────────────────────
        # Collapsed by default: the sections above are the orientation read, and
        # a ranked table opens as the answer to a question you have already
        # decided to ask.
        board_exp = ui.expansion("Full leaderboard").classes(
            f"w-full mb-9 {_MONO} {NT['caption']} text-[10px] "
            f"tracking-[.18em] uppercase {V.LEVEL_GROOVE} border {NE['hair']} "
            "momentum-board")
        with board_exp:
            # normal-case: the expander HEADER is uppercased, and text-transform is
            # inherited — without this every table cell shouts.
            board_box = ui.column().classes(
                f"w-full gap-6 p-1 normal-case {_T['RT_SANS']}")

        # ── limits + footnote ───────────────────────────────────────────────
        with ui.element("div").classes(
                "grid grid-cols-[repeat(auto-fit,minmax(230px,1fr))] gap-0.5 "
                "w-full"):
            for tag, text in V.LIMITS:
                with ui.column().classes(
                        f"px-5 pt-[18px] pb-5 gap-2 {_LIMIT_BG} "
                        f"border {NE['note_rule']}"):
                    ui.label(tag).classes(
                        f"{_MONO} {_LIMIT_TAG} text-[9.5px] tracking-[.16em] "
                        "uppercase leading-none")
                    ui.label(text).classes(
                        f"text-[13px] leading-[1.45] {NT['rail']}")
        foot_lbl = ui.label("").classes(
            f"{_MONO} {NT['ghost']} text-[9.5px] tracking-[.1em] uppercase "
            "leading-[1.8] pt-5 w-full")

    mom_busy = _busy.build_busy(quad_box, "Recomputing momentum…")

    # ── painters ────────────────────────────────────────────────────────────
    def _paint_regime(regime):
        regime_box.clear()
        with regime_box:
            for card in V.regime_cards(regime):
                cls = V.REGIME_CLASSES[card["state"]]
                on = card["active"]
                with ui.column().classes(
                        ("flex-[1.35_1_300px] gap-2.5 border-2 "
                         f"{cls['panel']} {cls['edge']}" if on else
                         f"flex-[1_1_260px] gap-2 {V.LEVEL_GROOVE} border "
                         f"{NE['hair']}")
                        + " px-[22px] pt-5 pb-[22px] min-w-0"):
                    with ui.row().classes("items-center no-wrap gap-2.5"):
                        ui.element("div").classes(
                            ("w-2.5 h-2.5 " + cls["dot"]) if on else
                            ("w-2 h-2 " + cls["dim_dot"])
                            + " rounded-full shrink-0")
                        ui.label(card["title"]).classes(
                            f"{_MONO} tracking-[.18em] uppercase leading-none "
                            + (f"text-[13px] font-medium {cls['title']}" if on
                               else f"text-[12px] {cls['dim_title']}"))
                    ui.label(card["blurb"]).classes(
                        f"text-[16px] font-medium leading-[1.35] "
                        f"{NT['bright']}" if on else
                        f"text-[13px] leading-[1.45] {NT['blurb']}")
                    ui.label(card["action"]).classes(
                        f"{_MONO} tracking-[.1em] uppercase leading-none "
                        + (f"text-[10.5px] {cls['action']}" if on
                           else f"text-[10px] {NT['ghost']}"))

    def _paint_dispersion(regime):
        disp_box.clear()
        d = V.dispersion(regime)
        with disp_box:
            if not d:
                ui.label("No dispersion reading published.").classes(
                    f"text-[13px] {NT['blurb']}")
                return
            with ui.column().classes("gap-[5px] shrink-0"):
                ui.label("Dispersion").classes(
                    f"{_MONO} {NT['label']} text-[10px] tracking-[.16em] "
                    "uppercase leading-none")
                ui.label(d["ordinal"]).classes(
                    f"{_MONO} {_DISP_TXT} text-[26px] font-medium "
                    "leading-none tracking-[-0.03em]")
            with ui.column().classes(
                    "flex-[1_1_300px] min-w-[220px] gap-[7px]"):
                with ui.element("div").classes(
                        f"relative h-3 w-full {NB['track']}"):
                    ui.element("div").classes(
                        f"absolute left-0 top-0 h-3 {_DISP_FILL} "
                        f"w-[{d['pct']:.1f}%]")
                    ui.element("div").classes(
                        f"absolute -top-[3px] w-0.5 h-[18px] {_DISP_MARK} "
                        f"left-[{d['pct']:.1f}%]")
                with ui.row().classes(
                        "justify-between w-full no-wrap "
                        f"{_MONO} {NT['axis']} text-[9.5px] tracking-[.12em] "
                        "uppercase"):
                    ui.label("0 · everything moves together")
                    ui.label("100 · wide spread")
            if d["sentence"]:
                ui.label(d["sentence"]).classes(
                    f"flex-[1_1_260px] text-[13.5px] leading-[1.45] "
                    f"{NT['body']}")

    def _paint_levels(levels):
        levels_box.clear()
        align_lbl.text = str(V.alignment_count(levels))
        with levels_box:
            for b in V.level_bars(levels):
                with ui.element("div").classes(
                        "grid grid-cols-[96px_minmax(0,1fr)_130px] "
                        "items-center gap-[18px] w-full"):
                    ui.label(b["name"]).classes(
                        f"{_MONO} {NT['rail']} text-[11px] tracking-[.14em] "
                        "uppercase")
                    with ui.element("div").classes(
                            f"relative block h-5 w-full {V.LEVEL_GROOVE}"):
                        ui.element("div").classes(
                            f"absolute left-0 top-0 h-5 {V.LEVEL_TRACK} "
                            f"w-[{b['track_pct']:.1f}%]")
                        ui.element("div").classes(
                            f"absolute left-0 top-0 h-5 {V.LEVEL_FILL} "
                            f"w-[{b['fill_pct']:.1f}%]")
                    with ui.row().classes(
                            "items-baseline justify-end no-wrap gap-[7px]"):
                        ui.label(str(b["top"])).classes(
                            f"{_MONO} {NT['bright']} text-[17px] font-medium")
                        ui.label(f"of {b['total']}").classes(
                            f"{_MONO} {NT['rail']} text-[11px]")

    def _paint_quadrants(rows):
        quad_box.clear()
        with quad_box:
            for q in V.quadrant_panels(rows):
                cls = V.QUAD_CLASSES[q["name"]]
                with ui.column().classes(
                        f"p-6 gap-0 min-w-0 {cls['panel']} border {cls['edge']}"):
                    with ui.row().classes(
                            "items-baseline justify-between w-full no-wrap "
                            "gap-3 mb-4"):
                        with ui.row().classes("items-center no-wrap gap-2.5"):
                            ui.element("div").classes(
                                f"w-[9px] h-[9px] rounded-full shrink-0 "
                                f"{cls['dot']}")
                            ui.label(q["name"]).classes(
                                f"{_MONO} {cls['title']} text-[12px] "
                                "font-medium tracking-[.18em] uppercase "
                                "leading-none")
                        with ui.row().classes("items-baseline no-wrap gap-[7px]"):
                            ui.label(str(q["count"])).classes(
                                f"{_MONO} {NT['bright']} text-[24px] "
                                "font-medium tracking-[-0.03em] leading-none")
                            ui.label(q["share"]).classes(
                                f"{_MONO} {NT['of_index']} text-[10px] "
                                "tracking-[.1em] uppercase leading-none")
                    with ui.element("div").classes(
                            f"relative h-1 w-full mb-4 {NB['hair']}"):
                        ui.element("div").classes(
                            f"absolute left-0 top-0 h-1 {cls['bar']} "
                            f"w-[{q['bar_pct']:.1f}%]")
                    ui.label(q["blurb"]).classes(
                        f"text-[12.5px] leading-[1.4] mb-3.5 {NT['blurb']}")
                    ui.label("Strongest by score").classes(
                        f"{_MONO} {NT['axis']} text-[9.5px] tracking-[.14em] "
                        "uppercase mb-2.5")
                    with ui.row().classes("flex-wrap gap-[5px] w-full"):
                        for name in q["names"]:
                            ui.label(name).classes(
                                f"{_MONO} {cls['chip_txt']} {cls['chip']} "
                                "text-[10.5px] tracking-[.06em] px-2.5 py-[5px] "
                                "truncate max-w-full")
                        if q["more"]:
                            ui.label(f"+{q['more']} more").classes(
                                f"{_MONO} {NT['caption']} border "
                                f"{NE['btn_edge']} text-[10.5px] "
                                "tracking-[.06em] px-2.5 py-[5px]")

    def _paint_example(rows):
        example_box.clear()
        comp_box.clear()
        ex = V.example_row(rows)
        with example_box:
            if not ex:
                ui.label("No rows for this level.").classes(
                    f"text-[13px] {NT['blurb']}")
                return
            ui.label(f"Example row · {ex['sector'] or 'top ranked'}").classes(
                f"{_MONO} {NT['caption']} text-[10px] tracking-[.16em] "
                "uppercase leading-none")
            ui.label(ex["label"]).classes(
                "text-[22px] font-semibold leading-[1.1] tracking-[-0.02em]")
            with ui.row().classes("items-baseline flex-wrap gap-[18px]"):
                for cap, val, tone in (("Score", ex["score"], None),
                                       ("Pctl", ex["percentile"], None),
                                       ("Δ rank", ex["delta"],
                                        V.POS_TXT if ex["delta_positive"]
                                        else None)):
                    with ui.column().classes("gap-[3px]"):
                        ui.label(cap).classes(
                            f"{_MONO} {NT['of_index']} text-[9.5px] "
                            "tracking-[.14em] uppercase leading-none")
                        ui.label(val).classes(
                            f"{_MONO} text-[24px] font-medium leading-none "
                            f"tracking-[-0.03em] {tone or NT['bright']}")
            with ui.row().classes("items-center no-wrap gap-2.5 pt-1 flex-wrap"):
                if ex["quadrant"]:
                    qc = V.QUAD_CLASSES[ex["quadrant"]]
                    ui.label(ex["quadrant"]).classes(
                        f"{_MONO} {qc['chip_txt']} {qc['chip']} text-[10px] "
                        "tracking-[.14em] uppercase px-2.5 py-[5px]")
                if ex["align_blocks"]:
                    with ui.row().classes("items-center no-wrap gap-[3px]"):
                        for on in ex["align_blocks"]:
                            ui.element("div").classes(
                                f"w-[11px] h-[18px] "
                                + (V.ALIGN_ON if on else
                                   f"{V.ALIGN_OFF} border {NE['btn_edge']}"))
                    ui.label(ex["align_text"]).classes(
                        f"{_MONO} {NT['of_index']} text-[9.5px] "
                        "tracking-[.1em] uppercase")
        with comp_box:
            for c in V.component_bars(ex["components"]):
                with ui.element("div").classes(
                        "grid grid-cols-[74px_minmax(0,1fr)_46px] "
                        "items-center gap-4 w-full"):
                    ui.label(c["label"]).classes(
                        f"{_MONO} {NT['value']} text-[10.5px] "
                        "tracking-[.12em] uppercase")
                    with ui.column().classes("gap-[5px] min-w-0 w-full"):
                        ui.label(c["meaning"]).classes(
                            f"text-[12.5px] leading-[1.3] {NT['note']}")
                        with ui.element("div").classes(
                                f"relative block h-[7px] w-full {NB['track']}"):
                            ui.element("div").classes(
                                f"absolute left-1/2 -top-0.5 w-px h-[11px] "
                                f"{NB['btn_edge']}")
                            if c["width_pct"]:
                                ui.element("div").classes(
                                    "absolute top-0 h-[7px] "
                                    + (V.POS_BAR if c["positive"]
                                       else V.NEG_BAR)
                                    + f" left-[{c['left_pct']:.1f}%] "
                                    f"w-[{c['width_pct']:.1f}%]")
                    ui.label(c["text"]).classes(
                        f"{_MONO} text-[12.5px] text-right tabular-nums "
                        + (V.POS_TXT if c["positive"] else V.NEG_TXT))

    def _paint_ranks(history):
        rank_plot.clear()
        rtick_box.clear()
        rdate_box.clear()
        ch = V.rank_chart(history)
        story_lbl.text = V.rank_story(ch)
        with rtick_box:
            for t in V.rank_ticks(ch):
                ui.label(str(t["rank"])).classes(
                    f"{_MONO} {NT['of_index']} text-[10px] absolute right-1.5 "
                    f"-translate-y-1/2 top-[{t['y_pct']:.2f}%]")
        with rank_plot:
            svg = V.rank_svg(ch)
            if svg:
                ui.html(svg).classes(
                    "absolute inset-0 w-full h-full pointer-events-none")
            for s in ch["series"]:
                ui.label(s["symbol"]).classes(
                    f"{_MONO} absolute left-full whitespace-nowrap "
                    "translate-x-2 -translate-y-1/2 text-[10.5px] "
                    "tracking-[.06em] "
                    + (V.HILITE_TXT if s["highlight"] else NT["caption"])
                    + f" top-[{s['points'][-1][1]:.2f}%]")
        dates = ch["dates"]
        with rdate_box:
            if dates:
                marks = [dates[0]] + ([dates[len(dates) // 2]]
                                      if len(dates) > 2 else []) + [dates[-1]]
                for d in marks:
                    ui.label(d).classes(
                        f"{_MONO} {NT['axis']} text-[9.5px] tracking-[.1em]")

    def _paint_board(rows, level, muted):
        board_box.clear()
        top, bottom = leaderboard_rows(rows)
        cols = leaderboard_columns(level)
        with board_box:
            for title, data in (("Leaders", top), ("Laggards", bottom)):
                if not data:
                    continue
                ui.label(section_heading(title, level)).classes(
                    f"{_MONO} {NT['caption']} text-[10px] tracking-[.18em] "
                    "uppercase")
                ui.table(columns=cols, rows=data, row_key="symbol").classes(
                    "w-full momentum-table" + (" opacity-50" if muted else ""))

    # ── apply ───────────────────────────────────────────────────────────────
    def _apply():
        mom_busy.hide()
        p = state["payload"] or {}
        lvl = state["level"]
        rows = rows_for(p, lvl)
        regime = p.get("regime") or {}
        name = LEVEL_OPTIONS[lvl].lower()
        session = p.get("session_date")
        eyebrow_lbl.text = (
            f"Markets → Trend & Sentiment → Momentum · "
            f"session {session} · nightly 16:20 CT" if session else
            "Markets → Trend & Sentiment → Momentum · awaiting data")
        quad_step.text = f"3 · Where the {len(rows)} {name} sit"
        rank_step.text = (
            f"5 · Rank over the last "
            f"{len(V.rank_chart(rank_history_for(p, lvl))['dates'])} sessions")
        _paint_regime(regime)
        _paint_dispersion(regime)
        _paint_levels(p.get("levels") or {})
        _paint_quadrants(rows)
        _paint_example(rows)
        _paint_ranks(rank_history_for(p, lvl))
        _paint_board(rows, lvl, leaderboard_muted(regime))
        excl = excluded_text(p.get("excluded"))
        foot_lbl.text = (
            "Suppressed state follows Daniel & Moskowitz, “Momentum Crashes”, "
            "JFE 2016 · not a component of the sentiment composite"
            + (f" · {excl}" if excl else ""))
        if p.get("excluded"):
            foot_lbl.tooltip(excluded_tooltip(p["excluded"]))

    def _read():
        state["payload"] = bus_client.read(VIEW) or {}

    @guard
    def _set_level(value):
        state["level"] = normalise_level(value)
        _apply()

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_momentum"})
        ui.notify("Recompute requested")
        mom_busy.show()

    @guard
    def _maybe_repaint():
        ver = bus_client.read_version(VIEW)
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _read()
        _apply()

    state["ver"] = bus_client.read_version(VIEW)
    _read()
    _apply()
    ui.timer(2.0, _maybe_repaint)
