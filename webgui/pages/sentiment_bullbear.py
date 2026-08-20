"""Bull / Bear Map (/sentiment/bullbear) — where the market is strong and weak.

Tier-1 reader over one view, ``cache:sentiment:bullbear`` — the nightly momentum
cascade merged with one batched quote call by
``services/sentiment_svc/compute.bullbear_view``. The arithmetic, ordering and
colours are pure in ``pages/bullbear.py``; this module holds widgets, wiring, and
the two things only a page knows: the unit the score axes display in, and which
element repaints when.

**Two clocks, two repaint paths, and they are the same distinction.** The scores
are last night's cascade, the day-moves seconds old, so a version change carrying
only new quotes reprices the day cells IN PLACE — rebuilding would collapse every
branch the reader had opened, twice a minute. Only ``scores_signature`` rebuilds.

**No regime verdict, anywhere.** ``/sentiment/sectors`` and ``/sentiment/rotation``
already print opposite risk-on/risk-off headlines off quantities that are not
commensurable (CLAUDE.md: +0.37 "Risk-on" beside −1.52 "Risk-off", 2026-08-17).
This page counts the rows on screen and stops; the payload carries ``regime`` and
this module never reads it.
See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
import datetime
from time import monotonic

import bus_client
from nicegui import ui
from pages import bullbear as B
from pages import busy as _busy
from pages.options.theme import ROTATION_FONT_HEAD_HTML, ROTATION_TOKENS as _T
from pages.rotation_view import NB, NE, NT, TONE
from pages.ui_guard import guard

VIEW = "sentiment:bullbear"

# The three level names, in the order ``compute.BULLBEAR_LEVELS`` writes them.
LEVELS = ("sector", "industry", "stock")

WAITING = ("Waiting for the sentiment service — no map has been published yet. "
           "The tree is built by the nightly cascade at 16:20 CT.")
NO_SCORES = "Scores not yet computed"
NO_QUOTES = ("Live quotes unavailable — the day-move column is empty. The "
             "scores, quadrants and breadth below are unaffected.")
NOTHING_CHANGED = "Refreshed — the map was already current."
NO_INDUSTRIES = "No scored industries in this sector"
NO_STOCKS = "No scored member stocks"
ORPHANS = "Not in a scored industry"

# How long a Refresh may still be in flight. It has to be a clock, because on a
# frozen tape there is no ack to wait for: ``handlers.publish_bullbear`` carries
# the STORED ``quoted_at`` forward when only the stamp moved, so
# ``cache_set(skip_unchanged=True)`` short-circuits and nothing on the bus moves
# — not the version, not ``{key}:ts``. Sized for the round trip (consume the
# command, one batched /quotes, publish) with room to spare, since dismissing
# before the data can arrive would say "finished" while the work is still running.
REFRESH_WAIT_SEC = 8.0


# ── formatting and repaint arithmetic (pure, so the tests need no browser) ───
def as_percent(fraction):
    """A cascade fraction -> a signed percent string, or the absent marker.

    ``raw.trend`` and ``raw.excess`` are FRACTIONS (in
    ``sentiment-dashboard/scoring/momentum``, ``exp(slope*252)-1`` scaled by R²
    and a difference of two ``_pct_return`` values) while ``day_pct`` is already
    a percent. Guarding None suffices: the only caller feeds ``B.row_axes``.
    """
    return B.NO_READING if fraction is None else B.signed_pct(fraction * 100.0)


def day_tone(day_pct):
    """The Today cell's tone, read off the digits ``signed_pct`` will print.

    Taking the raw float would paint a cell green while the number beside it
    reads "0.00%"; deriving it from the formatter stops the two disagreeing.
    """
    return {"+": "up", "-": "down"}.get(B.signed_pct(day_pct)[:1], "flat")


def headline_line(sector_rows):
    """The count headline, pluralised here because ``B.headline`` will not.

    It renders ``noun`` verbatim and would emit "1 of 1 sectors". Its empty
    string on an empty payload is deliberate too — ``_rebuild`` substitutes
    :data:`WAITING` rather than leave a blank strip.
    """
    counts = B.quadrant_counts(sector_rows)
    total = sum(counts.values())
    return B.headline(counts, "sector" if total == 1 else "sectors")


def distribution(counts):
    """The count strip: the four quadrants always, ``unknown`` only when real.

    The four stay at zero because an EMPTY trap bucket is itself a reading — drop
    it and "nothing is falling but leading" is indistinguishable from "that
    bucket was not counted". ``unknown`` is the absence of a quadrant, not a
    fifth, so it earns a chip only when something really went unscored.
    """
    out = [(q, counts.get(q, 0)) for q in B.QUADRANTS if q != "unknown"]
    if counts.get("unknown"):
        out.append(("unknown", counts["unknown"]))
    return out


def _clock_time(stamp):
    """The wall-clock time out of an ISO stamp, or the stamp verbatim.

    ``compute.bullbear_view`` stamps ``now().astimezone().isoformat()``, so the
    date is today's and only the time earns the space; an unparseable stamp
    renders raw, since showing what was published beats claiming nothing was.
    """
    try:
        return datetime.datetime.fromisoformat(str(stamp)).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(stamp)


def clocks(payload):
    """``(scores line, quotes line)`` — two clocks, dating two different things.

    They fail separately as well as tick separately: ``compute.bullbear_view``
    leaves ``quoted_at`` None when the quote call raised and ships the tree
    regardless, so a None there costs one column. Calling the page stale would
    send a reader away from scores that are perfectly good.
    """
    payload = payload or {}
    date = payload.get("session_date")
    stamp = payload.get("quoted_at")
    return (f"Scores as of {date}" if date else NO_SCORES,
            f"Quotes {_clock_time(stamp)}" if stamp else NO_QUOTES)


def day_map(levels):
    """``{(level, symbol): day_pct}`` — the live layer, flattened.

    Keyed by level AND symbol: an industry ETF is usually a scored stock too
    (``compute.bullbear_symbols`` dedups the quote call for that reason), so a
    symbol alone lets one row's move overwrite another's.
    """
    out = {}
    for level in LEVELS:
        for row in (levels or {}).get(level) or []:
            symbol = (row or {}).get("symbol")
            if symbol:
                out[(level, symbol)] = (row or {}).get("day_pct")
    return out


def scores_signature(payload):
    """What must move before the tree is rebuilt rather than repriced.

    The two score clocks plus the sector axes — the axes because a rescore
    landing on the same ``computed_at`` must still redraw, sector-only because
    all three levels come out of ONE cascade (``compute.bullbear_view`` copies
    ``momentum["levels"]`` wholesale).
    """
    payload = payload or {}
    rows = (payload.get("levels") or {}).get("sector") or []
    return (payload.get("session_date"), payload.get("computed_at"),
            tuple((r or {}).get("symbol") for r in rows),
            tuple(B.row_axes(r) for r in rows))


# ── the look ─────────────────────────────────────────────────────────────────
# One column template for all three levels — comparing a row against its parent
# is the reason the tree nests. Indent lives in the first cell, not the template.
GRID = ("grid grid-cols-[minmax(190px,2.4fr)_150px_92px_92px_88px"
        "_minmax(120px,1fr)] w-full items-center")
MIN_W = "min-w-[880px]"

_MONO = _T["RT_MONO"]
_NUM = f"{_MONO} tabular-nums text-right pr-4 leading-none whitespace-nowrap"
_CAPTION = (f"{_MONO} {NT['axis']} text-[9.5px] tracking-[.2em] uppercase "
            "leading-none whitespace-nowrap")
_CHIP = ("border rounded-full px-2.5 py-[3px] text-[10px] tracking-[.06em] "
         "leading-none whitespace-nowrap")
_BTN = (f"{_MONO} {NT['caption']} border {NE['btn_edge']} bg-transparent "
        "h-[30px] px-4 text-[10px] tracking-[.16em] leading-none")

# Day-move tone: three static classes, swapped with ``.classes(remove=…)`` so a
# reprice cannot stack two colours on one cell.
_DAY_TXT = {tone: TONE[tone]["txt"] for tone in ("up", "down", "flat")}
_DAY_TXT_ALL = " ".join(dict.fromkeys(_DAY_TXT.values()))

# Breadth fill: thin takes the risk-off hue — a row rising on a quarter of its
# constituents is the qualifier this bar exists to draw.
_BREADTH_FILL = {True: TONE["down"]["fill"], False: TONE["up"]["fill"]}

# The headline slot carries either the count or the reason there is none.
_HEADLINE = {True: f"{NT['body']} text-[26px] font-semibold leading-tight",
             False: f"{NT['caption']} text-[15px] leading-snug"}
_HEADLINE_ALL = " ".join(dict.fromkeys(_HEADLINE.values()))
_QUOTES_TXT = {True: NT["ghost"], False: TONE["down"]["txt"]}
_QUOTES_ALL = " ".join(dict.fromkeys(_QUOTES_TXT.values()))

_ROW_H = {"sector": "h-[52px]", "industry": "h-[38px]", "stock": "h-[32px]"}
_ROW_INDENT = {"sector": "pl-6", "industry": "pl-12", "stock": "pl-[76px]"}
_NAME_TXT = {"sector": f"{NT['body']} text-[14px] font-semibold",
             "industry": f"{NT['caption']} text-[12.5px]",
             "stock": f"{NT['rail']} text-[12px]"}

# The ONE escape hatch (CLAUDE.md): Quasar-internal DOM ``.classes()`` cannot
# reach. ``.q-item`` pads the expansion header 8/16px and
# ``.nicegui-expansion-content`` is a flex column with ``align-items:flex-start``
# — left alone, both push child rows out of the column grid.
_BULLBEAR_CSS = """
.bullbear .q-item { padding: 0; min-height: 0; }
.bullbear .nicegui-expansion-content { padding: 0; gap: 0; align-items: stretch; }
.bullbear .q-expansion-item__content { width: 100%; }
"""


def render():
    state = {"payload": None, "ver": None, "days": {}, "cells": [], "sig": None,
             "refresh_until": None}

    ui.add_head_html(ROTATION_FONT_HEAD_HTML)
    ui.add_css(_BULLBEAR_CSS)

    # ── chrome ──────────────────────────────────────────────────────────────
    with ui.column().classes(
            f"bullbear {_T['RT_SANS']} {_T['RT_VOID_BG']} w-full gap-0 pb-16 "
            "rounded-lg overflow-hidden"):
        with ui.column().classes("w-full gap-0 px-7 pt-6 pb-5"):
            ui.label("BULL / BEAR MAP").classes(_CAPTION)
            with ui.row().classes("items-center w-full no-wrap gap-4 mt-3"):
                ui.label("Where the market is strong and weak").classes(
                    f"{NT['body']} text-[29px] font-bold leading-tight "
                    "tracking-[-0.01em]")
                ui.space()
                ui.button("Refresh", color=None,
                          on_click=lambda: _request_refresh()) \
                    .props("flat no-caps dense").classes(_BTN)
            with ui.row().classes("items-center w-full no-wrap gap-4 mt-4"):
                scores_lbl = ui.label("").classes(
                    f"{_MONO} {NT['caption']} text-[11px] leading-none")
                quotes_lbl = ui.label("").classes(
                    f"{_MONO} text-[11px] leading-none {_QUOTES_TXT[True]}")

        with ui.column().classes("w-full gap-2.5 px-7 pb-6"):
            headline_lbl = ui.label("").classes(_HEADLINE[True])
            sub_lbl = ui.label(
                "Counted at sector level. Expand a sector for the industries "
                "inside it, an industry for its stocks — each level counts "
                "separately.").classes(f"{NT['ghost']} text-[12.5px] leading-snug")
            dist_box = ui.row().classes("flex-wrap gap-1.5 pt-1")

        # The scrim mounts here, not on ``rows_box``, which ``_rebuild`` clears
        # — taking the scrim with it. The floor height gives it somewhere to land
        # on a cold cache, which is when Refresh gets pressed.
        scroll_box = ui.element("div").classes(
            "w-full overflow-x-auto px-7 min-h-[96px]")
        with scroll_box:
            grid_wrap = ui.column().classes(f"{MIN_W} w-full gap-0")
            with grid_wrap:
                with ui.element("div").classes(
                        f"{GRID} h-[34px] border-b {NE['hair']}"):
                    for text, align in (
                            ("Sector · Industry · Stock", "pl-6"),
                            ("Quadrant", ""), ("Trend", "text-right pr-4"),
                            ("vs SPY", "text-right pr-4"),
                            ("Today", "text-right pr-4"), ("Breadth", "")):
                        ui.label(text).classes(f"{_CAPTION} {align}")
                rows_box = ui.column().classes("w-full gap-0")

        # Outside the scroller — a footnote sliding sideways is unreadable. The
        # window lengths (``scoring/momentum.TREND_WINDOW`` / ``RS_WINDOW``) are
        # deliberately unprinted: producer constants this page cannot verify.
        ui.label(
            "Trend is the annualised slope of a regression through months of "
            "closes, scaled by how well the line fits. vs SPY is the excess "
            "return over the benchmark. Both are scored once a night; only "
            "Today is live.").classes(
                f"{NT['ghost']} text-[11.5px] leading-relaxed px-7 pt-4 "
                "max-w-[820px]")

    map_busy = _busy.build_busy(scroll_box, "Refreshing the map…")

    # ── row builders (one shape, three levels) ──────────────────────────────
    def _set_day(cell, key):
        pct = state["days"].get(key)
        cell.text = B.signed_pct(pct)
        cell.classes(remove=_DAY_TXT_ALL, add=_DAY_TXT[day_tone(pct)])

    def _breadth(node):
        share = B.row_participation(node)
        width = B.breadth_width(share)
        if width is None:
            # No track AT ALL — a different drawing from an empty one. A stock
            # has no constituents, and a sector whose members were all unusable
            # has no reading; an empty groove would claim of both that nothing
            # confirms.
            ui.label(B.NO_READING).classes(f"{_MONO} {NT['ghost']} text-[11px]")
            return
        with ui.row().classes("items-center no-wrap gap-2.5 w-full pr-2"):
            with ui.element("div").classes(
                    f"h-1.5 flex-1 min-w-0 {NB['track']} rounded-full "
                    "overflow-hidden"):
                # The documented continuous-value exception: 0..100 is 101
                # classes, so this one is runtime-built. Set once (breadth is
                # nightly), so unlike the day tone there is nothing to
                # ``remove=``.
                ui.element("div").classes(
                    f"h-full rounded-full {_BREADTH_FILL[B.breadth_is_thin(share)]}"
                    f" w-[{width}%]")
            ui.label(f"{width}%").classes(
                f"{_MONO} {NT['ghost']} text-[10px] tabular-nums w-9 text-right")

    def _mark_row(node, level, leaf):
        """One grid row. Returns its chevron, or None for a leaf."""
        chevron = None
        trend, excess = B.row_axes(node)
        quad = B.quadrant(trend, excess)
        with ui.element("div").classes(
                f"{GRID} {_ROW_H[level]} {_ROW_INDENT[level]} pr-6 border-b "
                f"{NE['hair']} hover:bg-white/[0.02]"):
            with ui.row().classes("items-center no-wrap gap-2 min-w-0"):
                if leaf:
                    ui.element("div").classes("w-4 shrink-0")
                else:
                    chevron = ui.icon("chevron_right").classes(
                        f"{NT['rail']} text-[16px] shrink-0")
                ui.label(str(node.get("label") or node.get("symbol") or "")) \
                    .classes(f"{_NAME_TXT[level]} leading-none truncate")
                ui.label(str(node.get("symbol") or "")).classes(
                    f"{_MONO} {NT['ghost']} text-[9.5px] tracking-[.08em] shrink-0")
            with ui.row().classes("items-center"):
                ui.label(B.quadrant_label(quad)).classes(
                    f"{_CHIP} {B.quadrant_class(quad)}")
            ui.label(as_percent(trend)).classes(f"{_NUM} {NT['body']} text-[12px]")
            ui.label(as_percent(excess)).classes(f"{_NUM} {NT['caption']} text-[12px]")
            day = ui.label("").classes(f"{_NUM} text-[12px]")
            key = (level, node.get("symbol"))
            state["cells"].append((key, day))
            _set_day(day, key)
            _breadth(node)
        return chevron

    def _note(text, indent):
        ui.label(text).classes(
            f"{_MONO} {NT['ghost']} text-[10px] tracking-[.14em] uppercase "
            f"{indent} py-2.5 border-b {NE['hair']}")

    # ── lazy expansion ──────────────────────────────────────────────────────
    # Children build INSIDE the expand handler, so the default screen is eleven
    # sector rows and the 376-row tree never exists at once. A body with children
    # was filled before — that is the cache — and every branch adds at least one
    # (a note where there is nothing else) so an empty branch cannot misread.
    def _panel(node, level):
        panel = ui.expansion(str(node.get("label") or "")).classes("w-full")
        with panel.add_slot("header"):
            chevron = _mark_row(node, level, leaf=False)
        with panel:
            body = ui.column().classes("w-full gap-0")
        return panel, chevron, body

    def _open(event, chevron, body, fill):
        chevron.set_name("expand_more" if event.value else "chevron_right")
        if event.value and not body.default_slot.children:
            with body:
                fill()

    def _sector_panel(node):
        panel, chevron, body = _panel(node, "sector")

        @guard
        def _expand_sector(event):
            _open(event, chevron, body, lambda: _fill_sector(node))
        panel.on_value_change(_expand_sector)

    def _fill_sector(node):
        industries = node.get("industries") or []
        orphans = node.get("orphan_stocks") or []
        for industry in industries:
            _industry_panel(industry)
        if orphans:
            # ``build_tree`` files a stock here when its industry was never
            # scored — 10 of 296 on 2026-08-19. Dropping them would quietly
            # shrink the sector.
            _note(ORPHANS, _ROW_INDENT["industry"])
            for stock in orphans:
                _mark_row(stock, "stock", leaf=True)
        if not industries and not orphans:
            _note(NO_INDUSTRIES, _ROW_INDENT["industry"])

    def _industry_panel(node):
        panel, chevron, body = _panel(node, "industry")

        @guard
        def _expand_industry(event):
            _open(event, chevron, body, lambda: _fill_industry(node))
        panel.on_value_change(_expand_industry)

    def _fill_industry(node):
        stocks = node.get("stocks") or []
        for stock in stocks:
            _mark_row(stock, "stock", leaf=True)
        if not stocks:
            # Real: 3 of 69 industries held no admitted member stock on
            # 2026-08-19, so an empty panel is a state, not a bug.
            _note(NO_STOCKS, _ROW_INDENT["stock"])

    # ── painting ────────────────────────────────────────────────────────────
    def _rebuild():
        state["cells"] = []
        rows_box.clear()
        dist_box.clear()
        levels = (state["payload"] or {}).get("levels") or {}
        # Counts come from the BUILT tree, so the headline is a fact about the
        # rows on screen — the design's claim for it — not about rows it dropped.
        sectors = B.build_tree(levels)
        line = headline_line(sectors)
        headline_lbl.text = line or WAITING
        headline_lbl.classes(remove=_HEADLINE_ALL, add=_HEADLINE[bool(line)])
        sub_lbl.set_visibility(bool(line))
        grid_wrap.set_visibility(bool(sectors))
        with dist_box:
            for quad, count in distribution(B.quadrant_counts(sectors)):
                with ui.row().classes(
                        f"{_CHIP} items-center gap-2 {B.quadrant_class(quad)}"):
                    ui.label(B.quadrant_label(quad))
                    ui.label(str(count)).classes(f"{_MONO} font-semibold")
        with rows_box:
            for node in sectors:
                _sector_panel(node)

    def _paint():
        state["refresh_until"] = None
        map_busy.hide()
        scores, quotes = clocks(state["payload"])
        scores_lbl.text = scores
        quotes_lbl.text = quotes
        live = quotes != NO_QUOTES
        quotes_lbl.classes(remove=_QUOTES_ALL, add=_QUOTES_TXT[live])
        signature = scores_signature(state["payload"])
        if signature != state["sig"]:
            state["sig"] = signature
            _rebuild()
        for key, cell in state["cells"]:
            _set_day(cell, key)

    def _read():
        payload, version = bus_client.read_full(VIEW)
        state["payload"] = payload
        state["ver"] = version
        state["days"] = day_map((payload or {}).get("levels"))

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_bullbear"})
        ui.notify("Refresh requested")
        state["refresh_until"] = monotonic() + REFRESH_WAIT_SEC
        map_busy.show()

    def _expire_refresh():
        """Lower the scrim once a Refresh can no longer be in flight.

        Without this the only thing that lowers it is a repaint, so a refresh
        that legitimately changes nothing leaves the scrim up for the whole
        ``busy.BUSY_TIMEOUT_SEC`` backstop — 30 s of "working" on the frozen tape
        that is exactly when a reader presses Refresh to ask why nothing moves.
        """
        until = state["refresh_until"]
        if until is not None and monotonic() >= until:
            state["refresh_until"] = None
            map_busy.hide()
            ui.notify(NOTHING_CHANGED)

    @guard
    def _maybe_repaint():
        # The cheap ``:ver`` probe: an unchanged version costs one small GET and
        # no deserialize, per open tab, every 2 s.
        if bus_client.read_version(VIEW) == state["ver"]:
            _expire_refresh()
            return
        _read()
        _paint()

    _read()
    _paint()
    ui.timer(2.0, _maybe_repaint)
