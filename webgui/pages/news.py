"""Market News — headlines, SEC filings and the economic calendar.

Tier-1: nicegui + bus_client + shell + app_settings + ``shared.news_config`` +
``pages.*`` only. Reads three views - ``cache:news:feed`` (``news_view.VIEW``,
the headline list), ``cache:news:sec`` (``VIEW_SEC``, the SEC filings card)
and ``cache:news:calendar`` (``VIEW_CAL``, the calendar hero and agenda) - each
on its own ``watch_view``. The ONE write is Refresh → ``news_refresh`` on
cmd:news, built only when ``shell.may_enqueue()`` says this process may.
``render(public=True)`` hands off to ``news_live`` before anything here builds.

Layout (the 2026-09-26 redesign): two columns at ``lg`` - two thirds and one
third - stacking below it in DOM order.

LEFT: a filter card (a search box over headlines and tickers, the All / High /
Med / Low band picker, one Sources chip per source in the feed with its count);
a line of MOST MENTIONED ticker chips with "N of M stories" at its right; then
the headline list - one line per story, no header row: time (Central, 24-hour),
the band word, the headline (a new-tab link), up to two ticker chips and "+N",
and the first source. A high story carries a red left border and a faint wash, a
med one an amber border.

RIGHT: the NEXT ON THE CALENDAR hero (the earliest timed event or data release
ahead, with a countdown); the SEC filings card (All / Insider buys / Offerings /
Registrations; a form badge, the symbol, the filer, a Form 4's dollar total and
the time); the Economic calendar card - an agenda grouped by day, every kind in
one list told apart by its badge (FOMC, SPEECH, TESTIMONY, EVENT, DATA,
DIVIDEND, IPO), a high-impact item with an amber border and a HIGH marker.

``build_board`` builds every region and its filters and returns the handles the
page feeds payloads into; the public screen (``news_live``) calls it too, so the
two origins cannot drift. It does NO bus read: each page reads its own views and
hands the payloads over. ``draw_rows`` / ``draw_sec_rows`` / ``draw_calendar`` /
``draw_next`` are the module-level painters it uses.

Every fact comes from ``pages/news_view.py`` (pure); this module holds widgets
and wiring. Colours are FIXED palette classes chosen from finite maps (a band, a
form family, a badge) - never a runtime value.

⚠ A title, teaser, detail line or calendar title is THIRD-PARTY TEXT. It reaches
the page only through ``ui.label`` / ``ui.link``, which escape — never
``ui.html``, and a source-level test pins that this module calls ``ui.html``
nowhere. A link's target is a feed's URL too, and ``ui.link`` does NOT escape
an href, so only what ``news_view.safe_href`` passes (http/https with a host)
becomes a link; anything else (a ``javascript:`` URL included) renders the
title as plain text. A filing links to https sec.gov alone (``sec_href``).

Built on the page kit (``pages/ui_kit.py``): the header line carries the title,
the Updated stamp and Refresh; one region per panel holds what a publish
replaces. The stamp is not ``stale``-aware: the collector's cadence moves with
the session (5 min in regular hours, 60 at the weekend), so an old stamp on a
Sunday is not a fault.
"""
from __future__ import annotations

import datetime as _dt
import math
from types import SimpleNamespace
from urllib.parse import quote

from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import news_view as nv
from pages.options import theme as _t

PAGE_SIZE = 60
# How often an open tab redraws what it already holds. A headline's stamp drops
# its time for a day at midnight Central, filings age, calendar events pass and
# the countdown moves while a tab sits open; the views only republish when their
# CONTENT changes. A redraw from the held payloads - never a bus read.
REPAINT_SEC = 60
# Tickers drawn inline on one headline row; the rest collapse into "+N" with
# the full list on hover (four chips squeezed the headline to nothing).
MAX_ROW_TICKERS = 2
# Chips on the MOST MENTIONED line.
TRENDING_CHIPS = 8
# How long Refresh may spin before the backstop gives the button back. One poll
# of every feed walks the Yahoo per-ticker feeds and SEC's paced requests, so it
# is minutes, not seconds; the release is ``news:status``, published at the end
# of every poll.
REFRESH_TIMEOUT_SEC = 240.0
SYMBOL_ROUTE = "/symbol"
SEEN_KEY = "news_seen_ts"          # app_settings; read by the rail badge later
DEFAULT_WINDOW_H = 6               # config/news.toml [trending] window_h

# The feed has published NOTHING: the ONE shared line (pages/copy.py), which the
# Desk strip and the Symbol band show too. It is worded apart from the no-match
# line below, because a cold collector and a filter that excludes everything
# must never read the same.
WAITING = _copy.WAITING_NEWS
NO_MATCH = "Nothing matches those filters. Clear one to see more."
# The feed is up and published an empty list: the ONE line for that, drawn by
# this page and by the public copy (pages/news_live.py) alike.
EMPTY_FEED = "The feed is up but carries no items right now."

SEARCH_HINT = "Filter by ticker or keyword"
# The band picker's options, in order; "all" is no filter. Selecting a band
# shows ONLY that band.
BAND_OPTIONS = {"all": "All", "high": "High", "med": "Med", "low": "Low"}
BAND_WORD = {"high": "HIGH", "med": "MED", "low": "LOW"}
SOURCES_LABEL = "SOURCES"
TRENDING_LABEL = "MOST MENTIONED"
WATCHLIST_CHIP = "Watchlist only"

SEC_TITLE = "SEC filings"
# What is true of the SEC view: the newest filings EDGAR published (the service
# keeps its own item window - not a time window, so no "last 24 h" here).
SEC_NOTE = "EDGAR · newest first"
SEC_PAGE_SIZE = 40
CAL_TITLE = "Economic calendar"
CAL_NOTE = "all times CT"
NEXT_TITLE = "NEXT ON THE CALENDAR"
HIGH_CHIP = "HIGH"
HIGH_HINT = "High-impact release"
# The SEC and calendar views have published NOTHING (never "nothing to report").
SEC_WAITING = "No filings yet — the SEC feed hasn't published this session."
SEC_EMPTY = "No SEC filings or insider buys right now."
SEC_NO_MATCH = "No filings of that kind right now."
CAL_WAITING = "No calendar yet — it hasn't been published this session."
CAL_EMPTY = "Nothing on the calendar ahead."
UNDATED_HEAD = "DATE NOT YET PUBLISHED"

# ── styling: fixed Tailwind classes, colours from finite maps ────────────────
# ⚠ ``max-sm:hidden``, never ``hidden sm:flex``: Quasar ships
# ``.hidden { display: none !important }``, which beats every ``sm:`` display
# utility - the source badges an earlier version drew were written that way and
# never showed at any width.
_SM_ONLY = "max-sm:hidden"
_CAPS = "text-[10.5px] font-semibold uppercase tracking-[.14em] text-[#6d76a0] whitespace-nowrap"
_GRID = "w-full grid grid-cols-1 lg:grid-cols-3 gap-4 items-start"
_LEFT = "lg:col-span-2 min-w-0 w-full flex flex-col gap-3 flex-nowrap"
_RIGHT = "min-w-0 w-full flex flex-col gap-4 flex-nowrap"
_FILTER_CARD = f"{_t.CARD} w-full min-w-0 gap-3 flex-nowrap"
_SIDE_CARD = f"{_t.CARD} w-full min-w-0 gap-2 flex-nowrap"
_CARD_HEAD = "w-full items-baseline gap-2 flex-nowrap"
_CARD_NOTE = "ml-auto text-[11.5px] text-[#6d76a0] whitespace-nowrap"

# The search box: a boxed app field (APP_FIELD_CSS) with a search icon; its
# placeholder muted so it never reads as a filter already in force.
_SEARCH = "flex-1 min-w-[220px]"
_SEARCH_PROPS = 'debounce=300 clearable input-class="placeholder:text-[#6d76a0]"'
# The segmented band picker (not a kit.button: a picker with a selected state).
_SEG = ("items-center gap-1 flex-nowrap rounded-[10px] border border-[#252c46] "
        "bg-[#141a30] p-1")
_SEG_OPT = ("items-center gap-1.5 flex-nowrap rounded-[7px] px-3 py-1 text-[13px] "
            "cursor-pointer select-none")
_SEG_ON = "bg-[#232b4d] text-[#eef1f6]"
_SEG_OFF = "text-[#8891ab] hover:text-[#eef1f6]"
_DOT = "w-1.5 h-1.5 rounded-[2px] shrink-0"
BAND_DOT = {"all": "bg-[#a9b6ff]", "high": "bg-rose-400", "med": "bg-amber-400",
            "low": "bg-[#6d76a0]", None: "bg-transparent"}
# A toggle chip (a source, the watchlist switch, an SEC kind).
_CHIP = ("items-center gap-1.5 flex-nowrap rounded-[8px] border px-2.5 py-1 "
         "cursor-pointer select-none text-[13px]")
_CHIP_OFF = "border-[#252c46] bg-[#141a30] text-[#c7cee6] hover:border-[#3a4570]"
_CHIP_ON = "border-[#6b86ff] bg-[#6b86ff]/15 text-[#eef1f6]"
_CHIP_N = "text-[11px] text-[#6d76a0] tabular-nums"
_PILL = ("items-center flex-nowrap rounded-full border px-3 py-0.5 cursor-pointer "
         "select-none text-[12.5px]")
# MOST MENTIONED: a boxed ticker and its count.
_TREND = ("items-center gap-1.5 flex-nowrap rounded-[6px] border px-2 py-0.5 "
          "cursor-pointer select-none")
_TREND_OFF = "border-[#2a3358] bg-[#141a30] hover:border-[#3a4570]"
_TREND_ON = "border-[#6b86ff] bg-[#6b86ff]/15"
_TREND_SYM = "text-[12px] font-bold text-[#eef1f6]"
_COUNT = "ml-auto text-[12px] tabular-nums text-[#6d76a0] whitespace-nowrap"

# ── the headline list ────────────────────────────────────────────────────────
_LIST_CARD = ("w-full gap-0 flex-nowrap rounded-[12px] border border-[#1c2340] "
              "bg-[#0f1428] overflow-hidden")
# One line per story: nothing wraps; the headline gives way with an ellipsis.
# overflow-hidden: on a phone a fixed-width cell would otherwise push the
# headline to 0px and the row off the side of the card.
_ROW = ("w-full items-center gap-3 flex-nowrap overflow-hidden pl-3.5 pr-4 py-2.5 "
        "border-l-2 border-b border-b-[#1c2340] last:border-b-0")
# A row's left accent (and a high row's wash) by band; low and unbanded keep a
# transparent border so every row's text starts at the same x.
ROW_ACCENT = {"high": "border-l-rose-500 bg-rose-500/[0.07]",
              "med": "border-l-amber-400", "low": "border-l-transparent",
              None: "border-l-transparent"}
# w-[4.5rem] holds a dated stamp ("Tue 16:22") on one line.
_STAMP = "w-[4.5rem] shrink-0 text-[13px] tabular-nums text-[#8891ab] whitespace-nowrap"
_BAND = "w-9 shrink-0 text-[10.5px] font-bold tracking-[.08em] whitespace-nowrap cursor-default"
BAND_TEXT = {"high": "text-rose-400", "med": "text-amber-400", "low": "text-[#6d76a0]",
             None: ""}
_HEADLINE = ("text-[14px] font-medium text-[#eef1f6] no-underline hover:underline "
             "truncate min-w-0 flex-1")
_TICKERS = f"shrink-0 flex items-center gap-1.5 flex-nowrap {_SM_ONLY}"
_TCHIP = ("text-[11px] font-semibold px-1.5 py-px rounded-[4px] border border-[#2a3358] "
          "bg-[#141a30] text-[#a9b6ff] shrink-0 whitespace-nowrap")
_TICKER_LINK = f"{_TCHIP} no-underline hover:underline"
_TICKER_CHIP = f"{_TCHIP} cursor-pointer hover:underline"
_MORE_TICKERS = ("text-[11px] font-semibold px-1.5 py-px rounded-[4px] border "
                 "border-[#2a3358] text-[#c7cee6] shrink-0 whitespace-nowrap cursor-default")
# The first source, accent blue, right-aligned in a fixed-width cell (kept
# empty for a row with none); every source on hover when there are several.
_SOURCE = (f"w-32 shrink-0 text-right text-[12.5px] text-[#7d93ff] truncate "
           f"cursor-default {_SM_ONLY}")

# ── the SEC card ─────────────────────────────────────────────────────────────
_SEC_ROW = ("w-full items-center gap-2.5 flex-nowrap overflow-hidden py-2 "
            "border-b border-[#1c2340] last:border-b-0")
_FORM_SLOT = "w-14 shrink-0 flex"
_FORM = ("text-[10px] font-bold tracking-[.04em] px-1.5 py-px rounded-[4px] "
         "whitespace-nowrap cursor-default")
# The form badge by family (news_view.sec_tone): Form 4 green, an offering rose,
# a new (IPO) registration amber, a shelf slate.
FORM_CLASSES = {"form4": "bg-emerald-500/15 text-emerald-300",
                "offering": "bg-rose-500/15 text-rose-300",
                "ipo": "bg-amber-500/15 text-amber-300",
                "shelf": "bg-slate-400/15 text-slate-300",
                "other": "bg-white/5 text-[#8891ab]"}
_SEC_SYM = f"w-14 shrink-0 flex {_SM_ONLY}"
_SEC_NONE = "text-[12px] text-[#6d76a0]"
_SEC_NAME = ("text-[13px] font-medium text-[#eef1f6] no-underline hover:underline "
             "truncate min-w-0 flex-1")
_SEC_VALUE = f"text-[12.5px] font-semibold tabular-nums {_t.TXT_POS} shrink-0 whitespace-nowrap"
_SEC_TIME = ("w-[4.5rem] shrink-0 text-right text-[12.5px] tabular-nums text-[#8891ab] "
             "whitespace-nowrap")

# ── the calendar hero and agenda ─────────────────────────────────────────────
_HERO = ("w-full gap-1.5 flex-nowrap rounded-[12px] border border-[#34407a] "
         "bg-gradient-to-br from-[#161e44] to-[#0f1428] px-4 py-3.5")
_HERO_HEAD = "w-full items-center gap-2 flex-nowrap"
_HERO_EYEBROW = ("text-[10.5px] font-semibold uppercase tracking-[.14em] "
                 "text-[#c7cee6] whitespace-nowrap")
_HERO_COUNT = "ml-auto text-[13px] font-semibold tabular-nums text-[#eef1f6] whitespace-nowrap"
_HERO_TITLE = "text-[16px] font-semibold leading-snug text-[#eef1f6] line-clamp-2 w-full"
_HERO_WHEN = "text-[12.5px] tabular-nums text-[#8891ab]"
_HERO_QUIET = "text-[13px] text-[#8891ab]"
_DAY = f"{_CAPS} pt-2"
_CAL_ROW = ("w-full items-center gap-3 flex-nowrap overflow-hidden py-2 pl-2 pr-1 "
            "border-l-2 border-b border-b-[#1c2340] last:border-b-0")
# A high-impact item: an amber left border and a faint amber wash.
CAL_ACCENT = {True: "border-l-amber-400 bg-amber-400/[0.07]", False: "border-l-transparent"}
_CAL_TIME = "w-11 shrink-0 text-[13px] tabular-nums text-[#c7cee6] whitespace-nowrap"
_BADGE_SLOT = "w-[4.75rem] shrink-0 flex"
_BADGE = ("text-[9.5px] font-bold tracking-[.06em] px-1.5 py-px rounded-[4px] "
          "whitespace-nowrap cursor-default")
BADGE_CLASSES = {"FOMC": "bg-rose-500/15 text-rose-300",
                 "SPEECH": "bg-[#6b86ff]/15 text-[#a9b6ff]",
                 "TESTIMONY": "bg-violet-500/15 text-violet-300",
                 "EVENT": "bg-white/5 text-[#8891ab]",
                 "DATA": "bg-amber-500/15 text-amber-300",
                 "DIVIDEND": "bg-emerald-500/15 text-emerald-300",
                 "IPO": "bg-sky-500/15 text-sky-300"}
_BADGE_FALLBACK = "bg-white/5 text-[#8891ab]"
_CAL_CELL = "min-w-0 flex-1 gap-0.5 flex-nowrap"
_CAL_LINE1 = "w-full items-baseline gap-2 flex-nowrap overflow-hidden"
_CAL_TITLE = "text-[13px] font-semibold text-[#eef1f6] truncate min-w-0 shrink-0 max-w-[78%]"
_CAL_SUB = f"text-[12px] text-[#6d76a0] truncate min-w-0 flex-1 {_SM_ONLY}"
_CAL_LINE = "text-[12px] text-[#8891ab] truncate w-full"
_CAL_CHIP = ("text-[9.5px] font-bold uppercase tracking-[.06em] text-amber-300 "
             "rounded-[4px] bg-amber-400/15 px-1 py-px shrink-0 cursor-default")
_CAL_NOTE = f"text-xs {_t.TXT_WARN}"
_CAL_EMPTY = f"text-xs {_t.MUTED}"


def _norm(text) -> str:
    return " ".join(text.split()).casefold() if isinstance(text, str) else ""


# A remainder after the headline shorter than this is a publisher tag, not a
# sentence (Google News: "<headline>  <publisher>").
_ECHO_TAIL_CHARS = 40
_TAIL_PUNCT = " -–—|:·•,"


def _teaser_echoes(teaser, row) -> bool:
    """True when ``teaser`` is only the headline again, or the headline plus a
    publisher's name - Google News writes its description that way, which on
    hover reads as the headline printed twice. A teaser that merely OPENS with
    the headline and carries on is a real first line and is kept.

    The headline must end on a word boundary in the teaser - whitespace,
    punctuation or the end - so "Fed" is not echoed by "Federal Reserve holds".
    (The rule the two-line v1 rows used, restored for the hover.)"""
    title, text = _norm(row.get("title")), _norm(teaser)
    if not title or not text.startswith(title):
        return False
    if len(text) > len(title) and text[len(title)].isalnum():
        return False
    tail = text[len(title):].strip(_TAIL_PUNCT)
    if not tail or len(tail) < _ECHO_TAIL_CHARS:
        return True
    names = [row.get("original_source"), row.get("source"),
             *(row.get("sources") if isinstance(row.get("sources"), list) else [])]
    return any(_norm(n) and _norm(n) == tail for n in names)


def teaser_text(row) -> str:
    """The row's teaser, or ``""`` when there is none or it only echoes the
    headline (see ``_teaser_echoes``)."""
    if not isinstance(row, dict):
        return ""
    teaser = row.get("teaser").strip() if isinstance(row.get("teaser"), str) else ""
    return "" if not teaser or _teaser_echoes(teaser, row) else teaser


def hover_text(row) -> str:
    """What a headline shows on hover: its teaser, else a filing's summary."""
    if not isinstance(row, dict):
        return ""
    return teaser_text(row) or nv.detail_line(row)


def band_hover(row) -> str:
    """The band word's hover: why the story scored as it did, in words."""
    return nv.reason_text(row.get("reasons") if isinstance(row, dict) else None)


def trending_window_h() -> float:
    """``[trending] window_h`` from config/news.toml, or the default when that
    is not a positive, finite number (a bool is not a number here)."""
    from shared import news_config as nc
    try:
        v = (nc.load().get("trending") or {}).get("window_h")
    except (AttributeError, TypeError):
        return DEFAULT_WINDOW_H
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return DEFAULT_WINDOW_H
    if not math.isfinite(v) or v <= 0:
        return DEFAULT_WINDOW_H
    return v


def _symbol_base(linked):
    """The dossier route this origin serves, or ``None`` (tickers then filter)."""
    if not linked:
        return None
    import shell as _shell
    # None means this origin serves no dossier: the private path would 404
    # here, so the tickers become filter chips rather than dead links.
    return _shell.route_for(SYMBOL_ROUTE)


def _ticker(t, href_base, on_ticker):
    """One ticker: a dossier link when ``href_base``, else a filter chip."""
    from nicegui import ui

    from pages.ui_guard import guard
    if href_base is not None:
        return ui.link(t, f"{href_base}?symbol={quote(t, safe='')}").classes(_TICKER_LINK)
    chip = ui.label(t).classes(_TICKER_CHIP)
    chip.on("click", guard(lambda _e=None, t=t: on_ticker(t)))
    return chip


def _headline(row, classes, *, href=nv.safe_href, hover=hover_text, text=None):
    """The headline as a new-tab link (only what ``href`` passes), else plain
    text; ``hover(row)`` on hover. Every string is escaped."""
    from nicegui import ui
    title = text or row.get("title") or "(untitled)"
    url = href(row.get("url"))
    el = (ui.link(title, url, new_tab=True) if url else ui.label(title)).classes(classes)
    extra = hover(row)
    if extra:
        with el:
            with ui.tooltip().classes("max-w-[420px]"):
                ui.label(extra)
    return el


def _band_word(row):
    """The band word (HIGH / MED / LOW, the reasons on hover), or an empty slot
    of the same width for an unscored story - never a guessed "LOW"."""
    from nicegui import ui
    band = row.get("band") if row.get("band") in BAND_WORD else None
    el = ui.label(BAND_WORD.get(band, "")).classes(f"{_BAND} {BAND_TEXT[band]}")
    words = band_hover(row) if band else ""
    if words:
        with el:
            ui.tooltip(words)
    return el


def _source(sources):
    """The Source cell: the first source; every source on hover when there are
    several. Empty (same width) for a row with none."""
    from nicegui import ui
    names = [s for s in sources if isinstance(s, str) and s] \
        if isinstance(sources, list) else []
    el = ui.label(names[0] if names else "").classes(_SOURCE)
    if len(names) > 1:
        with el:
            ui.tooltip(", ".join(names))
    return el


def draw_rows(container, rows, *, linked, on_ticker):
    """Clear ``container`` and, when there are rows, draw the headline card: ONE
    LINE per row - the stamp (``row["stamp"]``, Central, 24-hour), the band
    word, the headline (truncated with an ellipsis; a new-tab link), up to two
    ticker chips and "+N", and the first source. The row's left border and wash
    follow its band (``ROW_ACCENT``). Below ``sm`` the tickers and the source
    go, so a phone keeps the headline its room.

    A ticker links to its Symbol dossier when ``linked``; otherwise it is a chip
    that calls ``on_ticker(TICKER)`` - the public screen, which has no dossier,
    filters by it instead."""
    from nicegui import ui

    href_base = _symbol_base(linked)
    container.clear()
    if not rows:
        return
    with container:
        with ui.column().classes(_LIST_CARD):
            for r in rows:
                band = r.get("band") if r.get("band") in BAND_WORD else None
                with ui.row().classes(f"{_ROW} {ROW_ACCENT[band]}"):
                    ui.label(r.get("stamp") or r.get("when") or "").classes(_STAMP)
                    _band_word(r)
                    _headline(r, _HEADLINE)
                    tickers = r.get("tickers") or []
                    with ui.element("div").classes(_TICKERS):
                        for t in tickers[:MAX_ROW_TICKERS]:
                            _ticker(t, href_base, on_ticker)
                        rest = tickers[MAX_ROW_TICKERS:]
                        if rest:
                            with ui.label(f"+{len(rest)}").classes(_MORE_TICKERS):
                                ui.tooltip(", ".join(rest))
                    _source(r.get("sources"))


def _sec_hover(row):
    """A filing's hover: its full title when the row shows a shortened name,
    then its teaser - never the detail line printed inline."""
    parts = []
    title = row.get("title") or ""
    if title and title != row.get("name"):
        parts.append(title)
    teaser = teaser_text(row)
    if teaser:
        parts.append(teaser)
    return " — ".join(parts)


def draw_sec_rows(container, rows, *, linked, on_ticker):
    """Clear ``container`` and draw one line per ``news_view.sec_rows`` row: the
    band dot, the form badge (``FORM_CLASSES`` by ``news_view.sec_tone``), the
    symbol, the filer (``name``; the full title on hover), a Form 4's dollar
    total in green, and the time.

    A filing's name links only to an ``https`` sec.gov address
    (``news_view.sec_href``). Where this origin serves no Symbol dossier
    (``linked`` False, or the route is not published here) the symbol is a
    filter chip - and ``on_ticker`` filters the HEADLINE list only."""
    from nicegui import ui

    href_base = _symbol_base(linked)
    container.clear()
    with container:
        with ui.column().classes("w-full gap-0 flex-nowrap"):
            for r in rows or []:
                band = r.get("band") if r.get("band") in BAND_WORD else None
                with ui.row().classes(_SEC_ROW):
                    dot = ui.element("span").classes(f"{_DOT} {BAND_DOT[band]}")
                    if band:
                        with dot:
                            ui.tooltip(f"{BAND_OPTIONS[band]} impact")
                    with ui.element("div").classes(_FORM_SLOT):
                        if r.get("form"):
                            tone = FORM_CLASSES.get(nv.sec_tone(r), FORM_CLASSES["other"])
                            ui.label(r["form"]).classes(f"{_FORM} {tone}")
                    with ui.element("div").classes(_SEC_SYM):
                        if r.get("symbol"):
                            _ticker(r["symbol"], href_base, on_ticker)
                        else:
                            ui.label("—").classes(_SEC_NONE)
                    _headline(r, _SEC_NAME, href=nv.sec_href, hover=_sec_hover,
                              text=r.get("name") or None)
                    if r.get("value"):
                        ui.label(r["value"]).classes(_SEC_VALUE)
                    ui.label(r.get("stamp") or r.get("when") or "").classes(_SEC_TIME)


def draw_next(container, nxt):
    """Clear ``container`` and draw the NEXT ON THE CALENDAR hero from
    ``news_view.next_up``: the eyebrow and the countdown, the title (two lines
    at most) and its Central date and time - or a quiet line for ``None``."""
    from nicegui import ui

    container.clear()
    with container:
        with ui.row().classes(_HERO_HEAD):
            ui.label(NEXT_TITLE).classes(_HERO_EYEBROW)
            if nxt:
                ui.label(nxt.get("countdown") or "").classes(_HERO_COUNT)
        if not nxt:
            ui.label(nv.NOTHING_NEXT).classes(_HERO_QUIET)
            return
        ui.label(nxt.get("title") or "").classes(_HERO_TITLE)
        with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
            badge = nxt.get("badge") or ""
            if badge:
                ui.label(badge).classes(f"{_BADGE} {BADGE_CLASSES.get(badge, _BADGE_FALLBACK)}")
            ui.label(nxt.get("when") or "").classes(_HERO_WHEN)
            if nxt.get("high") is True:
                with ui.label(HIGH_CHIP).classes(_CAL_CHIP):
                    ui.tooltip(HIGH_HINT)


def _agenda_row(item):
    from nicegui import ui
    high = item.get("high") is True
    with ui.row().classes(f"{_CAL_ROW} {CAL_ACCENT[high]}"):
        ui.label(item.get("time") or "—").classes(_CAL_TIME)
        with ui.element("div").classes(_BADGE_SLOT):
            badge = item.get("badge") or ""
            ui.label(badge).classes(f"{_BADGE} {BADGE_CLASSES.get(badge, _BADGE_FALLBACK)}")
        with ui.column().classes(_CAL_CELL):
            with ui.row().classes(_CAL_LINE1) as line1:
                ui.label(item.get("title") or "").classes(_CAL_TITLE)
                if item.get("sub"):
                    ui.label(item["sub"]).classes(_CAL_SUB)
                full = " · ".join(x for x in (item.get("title"), item.get("sub")) if x)
                if full:
                    with line1:
                        ui.tooltip(full)
            for line in item.get("lines") or []:
                ui.label(line).classes(_CAL_LINE)
        if high:
            with ui.label(HIGH_CHIP).classes(_CAL_CHIP):
                ui.tooltip(HIGH_HINT)


def draw_calendar(container, agenda):
    """Clear ``container`` and draw ``news_view.agenda``: its stale-source
    notes, then each day's header and its rows (time, badge, title and a muted
    subtitle or one line per indicator), the undated tiles last, and the plain
    sentence of each kind with nothing to show. An item whose ``high`` is a real
    ``True`` gets an amber left border and wash and a HIGH marker."""
    from nicegui import ui

    agenda = agenda if isinstance(agenda, dict) else {}
    container.clear()
    with container:
        with ui.column().classes("w-full gap-0 flex-nowrap"):
            for note in agenda.get("notes") or []:
                ui.label(note).classes(_CAL_NOTE)
            days = agenda.get("days") or []
            undated = agenda.get("undated") or []
            for day in days:
                ui.label(day.get("head") or "").classes(_DAY)
                for item in day.get("items") or []:
                    _agenda_row(item)
            if undated:
                ui.label(UNDATED_HEAD).classes(_DAY)
                for item in undated:
                    _agenda_row(item)
            if not days and not undated:
                ui.label(CAL_EMPTY).classes(f"{_CAL_EMPTY} py-2")
            for line in agenda.get("empty") or []:
                ui.label(line).classes(f"{_CAL_EMPTY} pt-2")


def build_board(*, linked, symbol="", watchlist=None):
    """Build every region of the page inside the current context and return the
    handles a page feeds: ``take(feed)``, ``take_sec(sec)``, ``take_cal(cal)``
    (each a payload or ``None``), plus ``state`` and ``repaint``.

    It reads NO view - the caller does, so the public screen's reads stay in
    its own module where a test can see every one. ``symbol`` seeds the ticker
    filter; ``watchlist`` (a set of tickers) draws the "Watchlist only" chip -
    the private page's, and ``None`` on the public one. A slow timer
    (``REPAINT_SEC``) redraws what is held: stamps, filings, the countdown and
    the agenda age while a tab stays open."""
    from nicegui import ui

    from pages import ui_kit as kit
    from pages.ui_guard import guard

    st = {"payload": None, "rows": [], "sources": set(), "symbol": symbol or "",
          "query": "", "band": "all", "watchlist_only": False, "shown": PAGE_SIZE,
          "sec_payload": None, "sec_kind": "all", "sec_shown": SEC_PAGE_SIZE,
          "cal_payload": None}

    def _now():
        return _dt.datetime.now(_dt.timezone.utc)

    with ui.element("div").classes(_GRID):
        with ui.column().classes(_LEFT):
            with ui.column().classes(_FILTER_CARD):
                with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                    search = ui.input(placeholder=SEARCH_HINT) \
                        .props(f"{kit.FIELD_PROPS} {_SEARCH_PROPS}").classes(_SEARCH)
                    with search.add_slot("prepend"):
                        ui.icon("search").classes("text-[18px]")
                    band_box = ui.row().classes(_SEG)
                    wl_box = ui.row().classes("items-center")
                    wl_box.set_visibility(watchlist is not None)
                with ui.row().classes("w-full items-center gap-2 flex-wrap") as src_row:
                    ui.label(SOURCES_LABEL).classes(f"{_CAPS} mr-1")
                    src_box = ui.row().classes("items-center gap-2 flex-wrap")
            with ui.row().classes("w-full items-center gap-2 flex-wrap sm:flex-nowrap"):
                trend_box = ui.row().classes("items-center gap-2 flex-wrap min-w-0 flex-1")
                count = ui.label("").classes(_COUNT)
            region = kit.region("Loading the news…")
            more = kit.button("Show more", kind="secondary", icon="expand_more")
            more.set_visibility(False)
        with ui.column().classes(_RIGHT):
            hero = ui.column().classes(_HERO)
            hero.set_visibility(False)
            with ui.column().classes(_SIDE_CARD):
                with ui.row().classes(_CARD_HEAD):
                    kit.section_title(SEC_TITLE)
                    ui.label(SEC_NOTE).classes(_CARD_NOTE)
                sec_chips = ui.row().classes("w-full items-center gap-2 flex-wrap")
                sec_region = kit.region("Loading filings…")
                sec_more = kit.button("Show more", kind="secondary", icon="expand_more")
                sec_more.set_visibility(False)
            with ui.column().classes(_SIDE_CARD):
                with ui.row().classes(_CARD_HEAD):
                    kit.section_title(CAL_TITLE)
                    ui.label(CAL_NOTE).classes(_CARD_NOTE)
                cal_region = kit.region("Loading the calendar…")

    # ── the headline list and its filters ─────────────────────────────────
    def _filtered():
        return nv.filter_rows(st["rows"], sources=st["sources"] or None,
                              symbol=st["symbol"] or None,
                              watchlist=watchlist if st["watchlist_only"] else None,
                              band=st["band"], query=st["query"])

    def _reset_paging():
        st["shown"] = PAGE_SIZE

    @guard
    def _pick_ticker(t):
        # A second click on the ticker already in force clears it.
        st["symbol"] = "" if st["symbol"] == t else t
        _reset_paging()
        _paint()

    @guard
    def _pick_band(key):
        st["band"] = key if key in BAND_OPTIONS else "all"
        _reset_paging()
        _paint()

    @guard
    def _toggle_source(name):
        st["sources"] ^= {name}
        _reset_paging()
        _paint()

    @guard
    def _toggle_watchlist():
        st["watchlist_only"] = not st["watchlist_only"]
        _reset_paging()
        _paint()

    def _paint_band():
        band_box.clear()
        with band_box:
            for key, text in BAND_OPTIONS.items():
                on = st["band"] == key
                with ui.row().classes(f"{_SEG_OPT} {_SEG_ON if on else _SEG_OFF}") as opt:
                    ui.element("span").classes(f"{_DOT} {BAND_DOT[key]}")
                    ui.label(text)
                opt.on("click", guard(lambda _e=None, k=key: _pick_band(k)))

    def _paint_watchlist():
        wl_box.clear()
        if watchlist is None:
            return
        with wl_box:
            on = st["watchlist_only"]
            with ui.row().classes(f"{_CHIP} {_CHIP_ON if on else _CHIP_OFF}") as chip:
                ui.label(WATCHLIST_CHIP)
            chip.on("click", guard(lambda _e=None: _toggle_watchlist()))

    def _paint_sources():
        src_box.clear()
        pairs = nv.source_counts(st["rows"])
        src_row.set_visibility(bool(pairs))
        with src_box:
            for name, n in pairs:
                on = name in st["sources"]
                with ui.row().classes(f"{_CHIP} {_CHIP_ON if on else _CHIP_OFF}") as chip:
                    ui.label(name)
                    ui.label(str(n)).classes(_CHIP_N)
                chip.on("click", guard(lambda _e=None, s=name: _toggle_source(s)))

    def _paint_trending():
        trend_box.clear()
        if st["payload"] is None:
            return
        pairs = nv.trending(st["payload"], now=_now(),
                            window_h=trending_window_h())[:TRENDING_CHIPS]
        sym = st["symbol"]
        if sym and sym not in {t for t, _ in pairs}:
            # The ticker in force always has a chip, so it can be cleared.
            n = sum(1 for r in st["rows"] if sym in (r.get("tickers") or []))
            pairs = [(sym, n), *pairs]
        if not pairs:
            return
        with trend_box:
            ui.label(TRENDING_LABEL).classes(f"{_CAPS} mr-1")
            for t, n in pairs:
                on = sym == t
                with ui.row().classes(f"{_TREND} {_TREND_ON if on else _TREND_OFF}") as chip:
                    ui.label(t).classes(_TREND_SYM)
                    ui.label(str(n)).classes(_CHIP_N)
                chip.on("click", guard(lambda _e=None, t=t: _pick_ticker(t)))

    def _paint():
        region.busy.hide()
        _paint_band()
        _paint_watchlist()
        _paint_sources()
        _paint_trending()
        if st["payload"] is None:
            draw_rows(region.content, [], linked=linked, on_ticker=_pick_ticker)
            with region.content:
                kit.empty(WAITING)
            count.text = ""
            more.set_visibility(False)
            return
        matched = _filtered()
        shown = matched[:st["shown"]]
        draw_rows(region.content, shown, linked=linked, on_ticker=_pick_ticker)
        if not matched:
            with region.content:
                kit.empty(NO_MATCH if st["rows"] else EMPTY_FEED)
        count.text = nv.story_count(len(matched), len(st["rows"]))
        more.set_visibility(len(matched) > st["shown"])

    def take(payload):
        st["payload"] = payload if isinstance(payload, dict) else None
        st["rows"] = nv.rows(st["payload"], now=_now())
        # A source that stopped appearing would leave a filter with no visible
        # cause; drop it from the selection.
        st["sources"] &= {s for s, _ in nv.source_counts(st["rows"])}
        _paint()

    @guard
    def _on_search(e):
        st["query"] = e.value if isinstance(e.value, str) else ""
        _reset_paging()
        _paint()

    @guard
    def _more():
        st["shown"] += PAGE_SIZE
        _paint()

    search.on_value_change(_on_search)
    more.on_click(_more)

    # ── the SEC card ───────────────────────────────────────────────────────
    @guard
    def _pick_sec(key):
        st["sec_kind"] = key if key in nv.SEC_KINDS else "all"
        st["sec_shown"] = SEC_PAGE_SIZE
        _paint_sec()

    def _paint_sec_chips():
        sec_chips.clear()
        with sec_chips:
            for key, text in nv.SEC_KINDS.items():
                on = st["sec_kind"] == key
                with ui.row().classes(f"{_PILL} {_CHIP_ON if on else _CHIP_OFF}") as chip:
                    ui.label(text)
                chip.on("click", guard(lambda _e=None, k=key: _pick_sec(k)))

    def _paint_sec():
        sec_region.busy.hide()
        _paint_sec_chips()
        payload = st["sec_payload"]
        if payload is None:
            draw_sec_rows(sec_region.content, [], linked=linked, on_ticker=_pick_ticker)
            with sec_region.content:
                kit.empty(SEC_WAITING)
            sec_more.set_visibility(False)
            return
        srows = nv.sec_rows(payload, now=_now())
        picked = nv.filter_sec(srows, st["sec_kind"])
        draw_sec_rows(sec_region.content, picked[:st["sec_shown"]],
                      linked=linked, on_ticker=_pick_ticker)
        if not picked:
            with sec_region.content:
                kit.empty(SEC_NO_MATCH if srows else SEC_EMPTY)
        sec_more.set_visibility(len(picked) > st["sec_shown"])

    def take_sec(payload):
        st["sec_payload"] = payload if isinstance(payload, dict) else None
        _paint_sec()

    @guard
    def _sec_more():
        st["sec_shown"] += SEC_PAGE_SIZE
        _paint_sec()

    sec_more.on_click(_sec_more)

    # ── the calendar hero and agenda ───────────────────────────────────────
    def _paint_cal():
        cal_region.busy.hide()
        payload = st["cal_payload"]
        if payload is None:
            hero.set_visibility(False)
            cal_region.content.clear()
            with cal_region.content:
                kit.empty(CAL_WAITING)
            return
        now = _now()
        hero.set_visibility(True)
        draw_next(hero, nv.next_up(payload, now=now))
        draw_calendar(cal_region.content, nv.agenda(payload, now=now))

    def take_cal(payload):
        st["cal_payload"] = payload if isinstance(payload, dict) else None
        _paint_cal()

    @guard
    def repaint():
        # The slow clock: redraw what is already held. No bus read here.
        if st["payload"] is not None:
            st["rows"] = nv.rows(st["payload"], now=_now())
        _paint()
        _paint_sec()
        _paint_cal()

    ui.timer(REPAINT_SEC, repaint, immediate=False)
    return SimpleNamespace(take=take, take_sec=take_sec, take_cal=take_cal,
                           repaint=repaint, state=st, search=search,
                           pick_ticker=_pick_ticker, pick_band=_pick_band,
                           pick_sec=_pick_sec)


def render(public=False):
    """Build the Market News page (the private app)."""
    if public:
        from pages import news_live
        return news_live.render()
    import app_settings
    import bus_client
    import shell as _shell
    from nicegui import run

    from pages import ui_kit as kit
    from pages.ui_guard import guard, guard_async
    from pages.view_watch import watch_view
    from shared import news_config as nc

    _may_enqueue = _shell.may_enqueue()
    linked = _shell.can_navigate(SYMBOL_ROUTE)
    try:
        watchlist = set(nc.ticker_set())
    except Exception:  # noqa: BLE001 - a broken config must not blank the page
        watchlist = set()

    refresh_btn = None
    with kit.page():
        head = kit.header("Market News", view=nv.VIEW)
        if _may_enqueue:
            @guard
            def _refresh():
                # Both, not either: the button is not drawn where this process
                # may not enqueue, AND the handler declines (the total proof
                # tests/test_live_commands.py asks of a published module).
                if not _may_enqueue:
                    return
                bus_client.request("news", {"type": "news_refresh"})
                kit.set_busy(refresh_btn, timeout=REFRESH_TIMEOUT_SEC)
                kit.toast("info", "Checking every feed and the calendar now — "
                                  "new items appear here as the check "
                                  "finishes, usually within a few minutes.")
            with head.actions:
                refresh_btn = kit.button(
                    "Refresh", kind="secondary", icon="refresh", on_click=_refresh,
                    tooltip="Poll every news feed and re-check the calendar now "
                            "instead of waiting for the next scheduled check.")
        board = build_board(linked=linked, watchlist=watchlist)

    def _take(payload):
        board.take(payload)
        if isinstance(payload, dict):
            # Written on EVERY publish, visible tab or not; the rail badge that
            # will read it may want "seen while visible" instead.
            app_settings.set(SEEN_KEY, _dt.datetime.now(_dt.timezone.utc).isoformat())

    @guard_async
    async def _reread():
        # The bus read is blocking; keep it off the event loop (house pattern).
        _take(await run.io_bound(bus_client.read, nv.VIEW))

    _take(bus_client.read(nv.VIEW))
    watch_view(nv.VIEW, _reread)

    @guard_async
    async def _reread_sec():
        board.take_sec(await run.io_bound(bus_client.read, nv.VIEW_SEC))

    @guard_async
    async def _reread_cal():
        board.take_cal(await run.io_bound(bus_client.read, nv.VIEW_CAL))

    board.take_sec(bus_client.read(nv.VIEW_SEC))
    board.take_cal(bus_client.read(nv.VIEW_CAL))
    watch_view(nv.VIEW_SEC, _reread_sec)
    watch_view(nv.VIEW_CAL, _reread_cal)
    if refresh_btn is not None:
        # Every poll ends with a status publish - even one that found nothing
        # new, which leaves the feed view (skip_unchanged) untouched.
        watch_view(nv.VIEW_STATUS, guard(lambda: kit.set_busy(refresh_btn, False)))
