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

The Highcharts quadrant scatter and rank ribbon are **gone** — section 3
replaces the scatter with counts and section 5 draws the ranks as a hand-built
SVG — and their builders were deleted with them.

All the new arithmetic is pure in ``pages/momentum_view.py``; the leaderboard's
own transforms stay here.

**On the page kit since 2026-09-19** (the consistency standard,
``docs/plans/2026-09-19-app-ui-consistency-design.md``): the header line carries
the name and the Updated stamp, the level picker sits in a control bar, the
eyebrow is the status line, and ONE ``kit.region`` covers the whole argument —
every one of the five sections and the leaderboard is replaced together by a
refresh or a level change. The page's own ground, its two faces and the
rotation family's warm-neutral ladder are gone; it wears the app surface like
every other screen. This is the LAST of the four rotation-family screens, so
the shared ladder (``rotation_view.NEUTRAL`` / ``NT`` / ``NB`` / ``NE``) and the
``[rotation]`` theme section retired with it.

⚠ **Two things the kit did NOT take.** ``render(level=…)``'s signature is
load-bearing — ``live_screens.py`` pins ``kwargs={"level": "industry"}`` for the
published screen and the gallery capture drives ``?level=``. And ``_name_chip``
stays a raw ``ui.button``: it is ONE call site producing on the order of the
whole level's universe per repaint, and it is a selectable name chip — 10.5px,
ring-on-select, ``max-w-full`` — not a page action, so it is recorded in the
ui-kit guard's ``ALLOWED`` with that reason rather than forced through
``kit.button``.

⚠ **This page's wait scrim used to die on the build paint.** ``build_busy``
mounted it inside ``quad_box`` and ``_paint_quadrants`` opens with
``quad_box.clear()``, so the first ``_apply()`` deleted it and every later
Refresh raised a scrim that no longer existed. ``kit.region`` keeps the spinner
on ``outer`` and clears only ``content``.
"""
import bus_client
from pages import momentum_view as V
from pages import ui_kit as kit
from pages.oklch import oklch_hex as _ok
from pages.options import theme
from pages.view_watch import watch_view
from pages.ui_guard import guard

VIEW = "sentiment:momentum"


# Deliberately the SAME four names the rotation screens use (see
# ``pages/rotation_view.py``, from which ``momentum_view`` takes the hues). Both
# are 2x2 strength-vs-rate-of-change reads sitting in one nav group, so two
# vocabularies agreeing on half the corners would read as a bug rather than a
# distinction. The axes still differ — RRG is purely relative to SPY, this score
# is a five-component blend — and that nuance lives in the page help.
QUADRANTS = {
    "leading": "Leading",       # strong and still accelerating
    "improving": "Improving",   # weak but turning up — the early screen
    "weakening": "Weakening",   # strong but decelerating — late, do not chase
    "lagging": "Lagging",
}

LEVEL_OPTIONS = {"industry": "Industries", "stock": "Stocks"}


_ALIGN_FILLED = "▮"
_ALIGN_HOLLOW = "▯"


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


def leaderboard_muted(regime):
    """True when the leaderboard should render muted beneath the banner."""
    return (regime or {}).get("state") == "suppressed"


def quadrant_for(score, accel):
    """Which corner a row sits in — the vocabulary the chart labels use."""
    if score is None or accel is None:
        return ""
    if score >= 0:
        return QUADRANTS["leading"] if accel >= 0 else QUADRANTS["weakening"]
    return QUADRANTS["improving"] if accel >= 0 else QUADRANTS["lagging"]


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
_P = theme.THEME["palette"]
# The faint end of the app's text ladder — the colour ``kit.EYEBROW`` wears, and
# what replaced the rotation family's dimmest ladder rungs when it retired.
# ``tabular-nums`` is what aligns figures down a column, and it is a numeric
# variant of whatever face is in use, so the page-scoped mono face is not
# missed where a figure has to line up.
_FAINT = f"text-[{_P['icon']}]"
# Chart FURNITURE, not data: a panel hairline, the limits card's rule, the
# dispersion groove and a quadrant bar's trough all draw a frame, so they take
# the app's card border. Two things sit one step brighter, on the BUTTON
# border, for the reason Task 1 of this migration gave the RRG's crosshair —
# each marks a fixed reference rather than a scale: the component bar's centre
# line (the universe average), and the edge of a control-like box (the "+N
# more" expander, an alignment block that is OFF).
_FRAME_EDGE = f"border-[{_P['card_border']}]"
_TRACK = f"bg-[{_P['card_border']}]"
_BTN_EDGE = f"border-[{_P['btn_border']}]"
_ZERO_TICK = f"bg-[{_P['btn_border']}]"

# The numbered step captions above each section. They carry the argument, so
# they are one style and never restated inline.
_STEP = (f"{theme.MUTED} text-[10px] tracking-[.18em] uppercase "
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
# The ring on the currently-selected name chip. The app's SELECTION accent —
# the colour ``kit``'s own selected table row wears (``build_surface_css`` and
# its ``.kit-row-selected`` rule) — rather than a quadrant hue: it says "this
# one" and nothing about the datum, and it has to read as that against four
# different tints.
_SEL_RING = f"ring-[{_P['focus']}]"


def render(level="industry"):
    """The Momentum page as a numbered argument.

    Sections 1–5 are the design; the ranked leaderboard survives beneath them
    behind a collapsed expander, so the orientation read is the default and the
    screening surface is one click away.
    """
    from nicegui import ui

    # Whether this render may command the sentiment service at all — resolved
    # ONCE, so the button and its handler cannot disagree. False on the public
    # live origin, where a Refresh is eleven sector chains plus their histories
    # per click against the owner's Schwab budget. See ``shell.may_enqueue``.
    import shell as _shell
    _may_enqueue = _shell.may_enqueue()

    # ``selected`` is the symbol driving section 4. None = the level's leader.
    state = {"ver": None, "level": normalise_level(level), "payload": None,
             "selected": None}

    with kit.page():
        # No description line: what this argument is and how to read it is the
        # opening of page_help.HELP_MD["/sentiment/momentum"].
        #
        # stale=False although the view is on a SCHEDULE: ``[slots.momentum]
        # at = "16:20"`` recomputes it ONCE A NIGHT, so ``alerts.stale_after``
        # would paint the stamp amber every single day. A view that is not due
        # to publish now has an age that says nothing.
        head = kit.header("Momentum", view=VIEW, stale=False)
        # Not drawn on the public live origin — see shell.may_enqueue: a
        # refresh is eleven sector chains plus their histories per click,
        # against the owner's Schwab budget.
        if _may_enqueue:
            with head.actions:
                kit.button("Refresh", kind="secondary", icon="refresh",
                           on_click=lambda: _request_refresh())
        # The level picker COMMANDS NOTHING — it re-reads rows the service has
        # already published — so it belongs in the control bar rather than
        # beside Refresh, and it stays drawn on the public live origin where
        # Refresh does not.
        with kit.control_bar():
            level_sel = kit.select_field(
                "Level", LEVEL_OPTIONS, value=state["level"],
                width="w-[132px]", on_change=lambda e: _set_level(e.value))
        # Keeps its runtime text: that is the READING's session date and the
        # cadence that produced it, not this page's own freshness, which the
        # header stamp answers.
        eyebrow_lbl = kit.status_line()

        # ONE region over the whole argument. A refresh — and a level change —
        # replaces all five sections AND the leaderboard, and the spinner now
        # lives on the region's OUTER element, so none of the `.clear()` calls
        # below can delete it. That was this page's bug from the 2026-08-17
        # rebuild until now: `build_busy` mounted the scrim inside `quad_box`,
        # and the build-time `_apply()` → `_paint_quadrants` → `quad_box.clear()`
        # removed it, so Refresh never showed a spinner.
        body = kit.region("Recomputing momentum…")
        # The section rhythm below is the MARGINS', exactly as it was when this
        # body was its own column, so the region's own row gap comes off rather
        # than stacking on top of it.
        body.content.classes(remove="gap-3", add="gap-0")
        with body.content:
            # ── 1 · is momentum worth trading today? ─────────────────────────
            ui.label("1 · Is momentum worth trading today?").classes(_STEP)
            regime_box = ui.row().classes("w-full flex-wrap gap-0.5 mb-0.5")
            disp_box = ui.row().classes(
                "items-center w-full flex-wrap gap-[26px] px-[22px] py-[18px] "
                f"mb-9 {V.LEVEL_GROOVE} border {_FRAME_EDGE}")

            # ── 2 · three levels ─────────────────────────────────────────────
            ui.label("2 · Three levels, and where they agree").classes(_STEP)
            with ui.row().classes("w-full flex-wrap gap-0.5 mb-9 items-stretch"):
                with ui.column().classes(
                        f"flex-[1_1_520px] min-w-[300px] px-6 pt-6 pb-[26px] gap-4 "
                        f"{V.LEVEL_GROOVE} border {_FRAME_EDGE}"):
                    levels_box = ui.column().classes("w-full gap-4")
                    ui.label("Bright segment = names in the top quartile of their "
                             "level · track width scales with universe size (√)") \
                        .classes(f"{_FAINT} text-[9.5px] "
                                 "tracking-[.12em] uppercase leading-[1.7] pt-0.5")
                with ui.column().classes(
                        f"flex-[1_1_300px] min-w-[280px] p-6 gap-3.5 "
                        f"{_ALIGN_PANEL} border {_ALIGN_EDGE}"):
                    ui.label("Align · all three agree").classes(
                        f"{_ALIGN_TITLE} text-[11px] tracking-[.18em] "
                        "uppercase leading-none")
                    with ui.row().classes("items-center no-wrap gap-4"):
                        with ui.row().classes("no-wrap gap-[3px]"):
                            for _ in range(3):
                                ui.element("div").classes(
                                    f"w-4 h-[26px] {V.ALIGN_ON}")
                        align_lbl = ui.label("0").classes(
                            f"{theme.LABEL} tabular-nums text-[34px] font-medium "
                            "leading-none tracking-[-0.03em]")
                        ui.label("stocks whose industry and sector both confirm") \
                            .classes(f"text-[13px] leading-[1.35] {_ALIGN_BODY}")
                    ui.label("The highest-conviction rows on the page — these are "
                             "the ones to take to Trade Analyzer.").classes(
                        f"text-[13.5px] leading-[1.5] {_ALIGN_BODY}")
                    # The names themselves, not just the count. Clicking one sends
                    # it to section 4 — and to the stock level, since that is the
                    # only level these rows exist on.
                    ui.label("By rank · click to decompose").classes(
                        f"{_FAINT} text-[9.5px] tracking-[.14em] uppercase")
                    aligned_box = ui.row().classes("flex-wrap gap-[5px] w-full")

            # ── 3 · quadrants ────────────────────────────────────────────────
            quad_step = ui.label("").classes(_STEP)
            quad_box = ui.element("div").classes(
                "grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-0.5 "
                "w-full mb-9")

            # ── 4 · what a score is made of ──────────────────────────────────
            ui.label("4 · What a score is made of").classes(_STEP)
            with ui.row().classes("w-full flex-wrap gap-0.5 mb-9 items-stretch"):
                example_box = ui.column().classes(
                    f"flex-[0_1_300px] min-w-[260px] p-6 gap-3 "
                    f"{V.LEVEL_GROOVE} border {_FRAME_EDGE}")
                with ui.column().classes(
                        f"flex-[1_1_460px] min-w-[320px] px-[26px] pt-6 pb-[26px] "
                        f"gap-3.5 {V.LEVEL_GROOVE} border {_FRAME_EDGE}"):
                    comp_box = ui.column().classes("w-full gap-3.5")
                    ui.label("Z-scores · centre line is the universe average") \
                        .classes(f"{_FAINT} text-[9.5px] "
                                 "tracking-[.12em] uppercase leading-[1.7]")

            # ── 5 · rank over recent sessions ────────────────────────────────
            with ui.row().classes(
                    "items-baseline justify-between w-full flex-wrap gap-5 mb-3"):
                rank_step = ui.label("").classes(_STEP + " mb-0")
                ui.label("Steady climbers beat yesterday's jumpers").classes(
                    f"{_FAINT} text-[10px] tracking-[.12em] "
                    "uppercase leading-none")
            with ui.column().classes(
                    f"w-full px-[26px] pt-[26px] pb-5 mb-9 gap-0 "
                    f"{V.LEVEL_GROOVE} border {_FRAME_EDGE}"):
                with ui.row().classes("w-full no-wrap gap-3"):
                    rtick_box = ui.element("div").classes(
                        "w-[34px] shrink-0 relative h-[250px]")
                    with ui.column().classes("flex-1 min-w-0 gap-0 pr-[52px]"):
                        rank_plot = ui.element("div").classes(
                            "relative h-[250px] w-full")
                        rdate_box = ui.row().classes(
                            "justify-between w-full no-wrap pt-2.5")
                story_lbl = ui.label("").classes(
                    f"text-[13px] leading-[1.5] pt-3.5 {theme.LABEL}")

            # ── the leaderboard, behind a toggle ─────────────────────────────
            # Collapsed by default: the sections above are the orientation read, and
            # a ranked table opens as the answer to a question you have already
            # decided to ask.
            board_exp = ui.expansion("Full leaderboard").classes(
                f"w-full mb-9 {theme.MUTED} text-[10px] tracking-[.18em] "
                f"uppercase {V.LEVEL_GROOVE} border {_FRAME_EDGE}")
            with board_exp:
                # normal-case: the expander HEADER is uppercased, and text-transform is
                # inherited — without this every table cell shouts.
                board_box = ui.column().classes(
                    "w-full gap-6 p-1 normal-case")

            # ── limits + footnote ────────────────────────────────────────────
            with ui.element("div").classes(
                    "grid grid-cols-[repeat(auto-fit,minmax(230px,1fr))] gap-0.5 "
                    "w-full"):
                for tag, text in V.LIMITS:
                    with ui.column().classes(
                            f"px-5 pt-[18px] pb-5 gap-2 {_LIMIT_BG} "
                            f"border {_FRAME_EDGE}"):
                        ui.label(tag).classes(
                            f"{_LIMIT_TAG} text-[9.5px] tracking-[.16em] "
                            "uppercase leading-none")
                        ui.label(text).classes(
                            f"text-[13px] leading-[1.45] {theme.MUTED}")
            foot_lbl = ui.label("").classes(
                f"{_FAINT} text-[9.5px] tracking-[.1em] uppercase "
                "leading-[1.8] pt-5 w-full")

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
                         f"{_FRAME_EDGE}")
                        + " px-[22px] pt-5 pb-[22px] min-w-0"):
                    with ui.row().classes("items-center no-wrap gap-2.5"):
                        ui.element("div").classes(
                            ("w-2.5 h-2.5 " + cls["dot"]) if on else
                            ("w-2 h-2 " + cls["dim_dot"])
                            + " rounded-full shrink-0")
                        ui.label(card["title"]).classes(
                            "tracking-[.18em] uppercase leading-none "
                            + (f"text-[13px] font-medium {cls['title']}" if on
                               else f"text-[12px] {cls['dim_title']}"))
                    ui.label(card["blurb"]).classes(
                        f"text-[16px] font-medium leading-[1.35] "
                        f"{theme.LABEL}" if on else
                        f"text-[13px] leading-[1.45] {theme.MUTED}")
                    ui.label(card["action"]).classes(
                        "tracking-[.1em] uppercase leading-none "
                        + (f"text-[10.5px] {cls['action']}" if on
                           else f"text-[10px] {_FAINT}"))

    def _paint_dispersion(regime):
        disp_box.clear()
        d = V.dispersion(regime)
        with disp_box:
            if not d:
                ui.label("No dispersion reading published.").classes(
                    f"text-[13px] {theme.MUTED}")
                return
            with ui.column().classes("gap-[5px] shrink-0"):
                ui.label("Dispersion").classes(
                    f"{theme.MUTED} text-[10px] tracking-[.16em] "
                    "uppercase leading-none")
                ui.label(d["ordinal"]).classes(
                    f"{_DISP_TXT} tabular-nums text-[26px] font-medium "
                    "leading-none tracking-[-0.03em]")
            with ui.column().classes(
                    "flex-[1_1_300px] min-w-[220px] gap-[7px]"):
                with ui.element("div").classes(
                        f"relative h-3 w-full {_TRACK}"):
                    ui.element("div").classes(
                        f"absolute left-0 top-0 h-3 {_DISP_FILL} "
                        f"w-[{d['pct']:.1f}%]")
                    ui.element("div").classes(
                        f"absolute -top-[3px] w-0.5 h-[18px] {_DISP_MARK} "
                        f"left-[{d['pct']:.1f}%]")
                with ui.row().classes(
                        "justify-between w-full no-wrap "
                        f"{_FAINT} text-[9.5px] tracking-[.12em] "
                        "uppercase"):
                    ui.label("0 · everything moves together")
                    ui.label("100 · wide spread")
            if d["sentence"]:
                ui.label(d["sentence"]).classes(
                    f"flex-[1_1_260px] text-[13.5px] leading-[1.45] "
                    f"{theme.LABEL}")

    def _paint_aligned(levels):
        aligned_box.clear()
        a = V.aligned_names(levels)
        align_lbl.text = str(a["count"])
        with aligned_box:
            if not a["members"]:
                ui.label("No stock has both its industry and its sector "
                         "behind it today.").classes(
                    f"text-[12.5px] leading-[1.4] {theme.MUTED}")
                return
            for m in a["members"]:
                _name_chip(m, V.ALIGN_CLASSES, pick=_select_aligned)

    def _paint_levels(levels):
        levels_box.clear()
        _paint_aligned(levels)
        with levels_box:
            for b in V.level_bars(levels):
                with ui.element("div").classes(
                        "grid grid-cols-[96px_minmax(0,1fr)_130px] "
                        "items-center gap-[18px] w-full"):
                    ui.label(b["name"]).classes(
                        f"{theme.MUTED} text-[11px] tracking-[.14em] "
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
                            f"{theme.LABEL} tabular-nums text-[17px] "
                            "font-medium")
                        ui.label(f"of {b['total']}").classes(
                            f"{theme.MUTED} tabular-nums text-[11px]")

    def _name_chip(member, cls, pick=None):
        """One clickable name. Clicking drives section 4 rather than navigating
        — the whole point is to decompose it without losing your place."""
        sel = member["symbol"] == state["selected"]
        act = pick or _select
        # ⚠ A RAW ``ui.button``, with its reason recorded in the ui-kit
        # guard's ALLOWED: this is ONE call site producing on the order of the
        # whole level's universe per repaint, and it is a selectable name chip
        # — not a page action. Through ``kit.button`` it would drop a
        # full-size action button into a quadrant panel hundreds of times. Its
        # fill and text stay the QUADRANT's (data); only the selection ring is
        # an app token.
        btn = ui.button(member["label"], color=None,
                        on_click=lambda _e=None, sym=member["symbol"]: act(sym)) \
            .props("flat no-caps dense") \
            .classes(f"{cls['chip_txt']} {cls['chip']} text-[10.5px] "
                     "tracking-[.06em] px-2.5 py-[5px] max-w-full normal-case "
                     "leading-none min-h-0"
                     + (f" ring-1 {_SEL_RING}" if sel else ""))
        # A ticker alone does not say what it is; the row already carries both.
        where = " · ".join(x for x in (member.get("sector"),
                                       member.get("industry")) if x)
        if where:
            btn.tooltip(where)

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
                                f"{cls['title']} text-[12px] "
                                "font-medium tracking-[.18em] uppercase "
                                "leading-none")
                        with ui.row().classes("items-baseline no-wrap gap-[7px]"):
                            ui.label(str(q["count"])).classes(
                                f"{theme.LABEL} tabular-nums text-[24px] "
                                "font-medium tracking-[-0.03em] leading-none")
                            ui.label(q["share"]).classes(
                                f"{_FAINT} text-[10px] "
                                "tracking-[.1em] uppercase leading-none")
                    with ui.element("div").classes(
                            f"relative h-1 w-full mb-4 {_TRACK}"):
                        ui.element("div").classes(
                            f"absolute left-0 top-0 h-1 {cls['bar']} "
                            f"w-[{q['bar_pct']:.1f}%]")
                    ui.label(q["blurb"]).classes(
                        f"text-[12.5px] leading-[1.4] mb-3.5 {theme.MUTED}")
                    ui.label("Strongest by score").classes(
                        f"{_FAINT} text-[9.5px] tracking-[.14em] "
                        "uppercase mb-2.5")
                    with ui.row().classes("flex-wrap gap-[5px] w-full"):
                        for m in q["names"]:
                            _name_chip(m, cls)
                    if q["more"]:
                        # The tail, not a teaser: "which names are Leading?"
                        # is what this section is for, so the rest of the
                        # membership is one click away rather than absent.
                        with ui.expansion(f"+{q['more']} more").classes(
                                f"w-full mt-2 {theme.MUTED} "
                                "text-[10px] tracking-[.1em] uppercase "
                                f"border {_BTN_EDGE}"):
                            with ui.row().classes(
                                    "flex-wrap gap-[5px] w-full p-1 normal-case"):
                                for m in q["members"][len(q["names"]):]:
                                    _name_chip(m, cls)

    def _paint_example(rows):
        example_box.clear()
        comp_box.clear()
        ex = V.example_row(rows, state["selected"])
        with example_box:
            if not ex:
                ui.label("No rows for this level.").classes(
                    f"text-[13px] {theme.MUTED}")
                return
            with ui.row().classes(
                    "items-baseline justify-between w-full no-wrap gap-3"):
                ui.label(
                    ("Top ranked · " if ex["is_default"] else "Selected · ")
                    + (ex["sector"] or "—")).classes(
                    f"{theme.MUTED} text-[10px] tracking-[.16em] "
                    "uppercase leading-none truncate")
                if not ex["is_default"]:
                    # Link-like: it resets the page's own selection and
                    # commands nothing.
                    kit.button("Top ranked", kind="quiet",
                               on_click=lambda: _select(None)) \
                        .classes("shrink-0")
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
                            f"{_FAINT} text-[9.5px] "
                            "tracking-[.14em] uppercase leading-none")
                        ui.label(val).classes(
                            "tabular-nums text-[24px] font-medium leading-none"
                            f" tracking-[-0.03em] {tone or theme.LABEL}")
            with ui.row().classes("items-center no-wrap gap-2.5 pt-1 flex-wrap"):
                if ex["quadrant"]:
                    qc = V.QUAD_CLASSES[ex["quadrant"]]
                    ui.label(ex["quadrant"]).classes(
                        f"{qc['chip_txt']} {qc['chip']} text-[10px] "
                        "tracking-[.14em] uppercase px-2.5 py-[5px]")
                if ex["align_blocks"]:
                    with ui.row().classes("items-center no-wrap gap-[3px]"):
                        for on in ex["align_blocks"]:
                            ui.element("div").classes(
                                f"w-[11px] h-[18px] "
                                + (V.ALIGN_ON if on else
                                   f"{V.ALIGN_OFF} border {_BTN_EDGE}"))
                    ui.label(ex["align_text"]).classes(
                        f"{_FAINT} text-[9.5px] tracking-[.1em] uppercase")
        with comp_box:
            for c in V.component_bars(ex["components"]):
                with ui.element("div").classes(
                        "grid grid-cols-[74px_minmax(0,1fr)_46px] "
                        "items-center gap-4 w-full"):
                    ui.label(c["label"]).classes(
                        f"{theme.LABEL} text-[10.5px] "
                        "tracking-[.12em] uppercase")
                    with ui.column().classes("gap-[5px] min-w-0 w-full"):
                        ui.label(c["meaning"]).classes(
                            f"text-[12.5px] leading-[1.3] {theme.MUTED}")
                        with ui.element("div").classes(
                                f"relative block h-[7px] w-full {_TRACK}"):
                            ui.element("div").classes(
                                f"absolute left-1/2 -top-0.5 w-px h-[11px] "
                                f"{_ZERO_TICK}")
                            if c["width_pct"]:
                                ui.element("div").classes(
                                    "absolute top-0 h-[7px] "
                                    + (V.POS_BAR if c["positive"]
                                       else V.NEG_BAR)
                                    + f" left-[{c['left_pct']:.1f}%] "
                                    f"w-[{c['width_pct']:.1f}%]")
                    ui.label(c["text"]).classes(
                        "text-[12.5px] text-right tabular-nums "
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
                    f"{_FAINT} tabular-nums text-[10px] absolute right-1.5 "
                    f"-translate-y-1/2 top-[{t['y_pct']:.2f}%]")
        with rank_plot:
            svg = V.rank_svg(ch)
            if svg:
                ui.html(svg).classes(
                    "absolute inset-0 w-full h-full pointer-events-none")
            for s in ch["series"]:
                ui.label(s["symbol"]).classes(
                    "absolute left-full whitespace-nowrap "
                    "translate-x-2 -translate-y-1/2 text-[10.5px] "
                    "tracking-[.06em] "
                    + (V.HILITE_TXT if s["highlight"] else theme.MUTED)
                    + f" top-[{s['points'][-1][1]:.2f}%]")
        dates = ch["dates"]
        with rdate_box:
            if dates:
                marks = [dates[0]] + ([dates[len(dates) // 2]]
                                      if len(dates) > 2 else []) + [dates[-1]]
                for d in marks:
                    ui.label(d).classes(
                        f"{_FAINT} text-[9.5px] tracking-[.1em]")

    def _paint_board(rows, level, muted):
        board_box.clear()
        top, bottom = leaderboard_rows(rows)
        cols = leaderboard_columns(level)
        with board_box:
            for title, data in (("Leaders", top), ("Laggards", bottom)):
                if not data:
                    continue
                ui.label(section_heading(title, level)).classes(
                    f"{theme.MUTED} text-[10px] tracking-[.18em] "
                    "uppercase")
                # ⚠ Through the kit the leaderboard SORTS for the first
                # time: ``table_columns`` defaults every data column
                # ``sortable``. ``numeric=()`` is deliberate — each column
                # already declares its own ``align``, which the kit keeps for
                # anything it is not told is numeric.
                tbl = kit.table(cols, rows=data, row_key="symbol", numeric=(),
                                classes="w-full cursor-pointer"
                                + (" opacity-50" if muted else ""))
                # rowClick carries the clicked row in args[1]; `table.selected`
                # is a different thing entirely and would lag a click behind.
                tbl.on("rowClick",
                       lambda e: _select((e.args[1] or {}).get("symbol")))

    # ── apply ───────────────────────────────────────────────────────────────
    def _apply():
        body.busy.hide()
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
    def _select(symbol):
        """Point section 4 at one row. ``None`` returns to the leader."""
        state["selected"] = symbol or None
        p = state["payload"] or {}
        rows = rows_for(p, state["level"])
        _paint_example(rows)
        _paint_quadrants(rows)      # so the selected chip shows its ring
        _paint_aligned(p.get("levels") or {})

    @guard
    def _select_aligned(symbol):
        """Pick an aligned name. These are stocks, and section 4 can only
        decompose a row on the level it is showing — so this switches the level
        too rather than falling back to the leader with no explanation."""
        if state["level"] != "stock":
            state["level"] = "stock"
            level_sel.value = "stock"      # fires _set_level, which clears
        state["selected"] = symbol or None  # ...so the pick is set after it
        _apply()

    @guard
    def _set_level(value):
        state["level"] = normalise_level(value)
        # A pick from one level does not exist on another, and silently
        # carrying it would strand the card on a fallback with no explanation.
        state["selected"] = None
        _apply()

    @guard
    def _request_refresh():
        if not _may_enqueue:
            return          # no button either — see shell.may_enqueue
        bus_client.request("sentiment", {"type": "refresh_momentum"})
        # No toast: the region's spinner already says the page is waiting, and
        # the standard keeps a toast for the OUTCOME of an action.
        body.busy.show()

    _read()
    _apply()
    # Version-gated repaint + seed, in one line (pages/view_watch.py).
    # This idiom -- probe :ver, compare, re-read, repaint, hang it on a
    # 2 s timer -- was written out longhand on 22 pages.
    watch_view(VIEW, lambda: (_read(), _apply()))
