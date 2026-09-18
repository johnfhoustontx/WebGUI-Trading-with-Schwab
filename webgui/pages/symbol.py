"""The Symbol Dossier (/symbol) — one screen per ticker, and a link out of every
band to the page that owns that fact.

Design: ``docs/plans/2026-09-17-symbol-dossier-design.md``.

Tier-1 reader. It polls ten shared cache views plus this symbol's own
``cache:options:dossier:<SYMBOL>`` on ONE batched ``read_versions`` every two
seconds (``VIEWS`` / ``REGION_VIEWS``, the Desk's shape), and repaints only the
bands whose views moved. Every number comes from ``pages/symbol_facts.py`` or a
builder the owning page already uses — this module holds the band assembly
(pure, tested in ``tests/test_symbol_page.py``) and the widgets.

**The fetch rule is the one that costs money.** A symbol the scanner covers is
answered entirely from cache. Anything else needs a ``dossier`` command on
``cmd:options`` — 4-5 Schwab calls against the proxy budget the 1-minute GEX
poll depends on. So a fetch is enqueued ONLY on navigation to a symbol and on
an explicit Refresh click, never by the poll timer (``should_enqueue`` says so,
and a source-level test pins that ``_poll`` cannot reach the enqueue at all).

No Highcharts, deliberately — the Desk's call: every chart is one click from
the page that owns it, and its absence sidesteps the hidden-mount and missing-
ResizeObserver traps outright.

Private only: it enqueues commands, which the public process refuses, so it is
NOT in ``live_screens.SCREENS``.
"""
import datetime as _dt
from urllib.parse import quote as _quote
from zoneinfo import ZoneInfo

import bus_client
import shell as _shell
from nicegui import run, ui

from pages import bullbear as _bb
from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import desk as _desk
from pages import symbol_facts as sf
from pages.fmt import num as _num  # the ONE copy (pages/fmt.py)
from pages.structure import structure_positions
from pages.options import handoff as _handoff
from pages.options import paper as _paper
from pages.options import persistence as _persistence
from pages.options import scanner as _scanner
from pages.options import svg as _svg
from pages.options.inputs import bind_symbol_load, select_all_on_focus
from pages.options.overlay import LOAD_TIMEOUT_SEC, build_loading_overlay
from pages.options.theme import (CON_ACCENT, CON_NEG, CON_POS, CON_TXT,
                                 CON_TXT_DIM, CON_TXT_MUTED, CON_WARN,
                                 CONSOLE_CARD, CONSOLE_DISPLAY,
                                 CONSOLE_FONT_HEAD_HTML, CONSOLE_PAGE,
                                 CONSOLE_RULE)
from pages.ui_guard import guard, guard_async
from shared.symbols import clean_symbol

ROUTE = "/symbol"
_CT = ZoneInfo("America/Chicago")
_DASH = "—"

# ── the fetch decision ──────────────────────────────────────────────────────
NAVIGATE, REFRESH, POLL = "navigate", "refresh", "poll"
_FETCH_TRIGGERS = (NAVIGATE, REFRESH)
NO_QUOTE, FETCH_FAILED = "no_quote", "fetch_failed"   # dossier.py's two words


def should_enqueue(coverage, trigger, *, have_dossier=False, feed_cold=False):
    """Whether this ``trigger`` may spend a ``dossier`` fetch on ``coverage``.

    * ``poll`` — and anything unrecognised — NEVER enqueues. A clock must not
      be what spends Schwab calls.
    * A **scanned** symbol never enqueues: the cache holds every fact, and the
      merge rule would prefer the cache over the fetch anyway.
    * A **cold options feed** enqueues nothing: the service that would answer
      the fetch is the one that has not published.
    * On **navigation**, a dossier the service still holds (``have_dossier`` —
      see ``reusable_dossier``) is reused; the 15-minute TTL exists so a repeat
      lookup spends nothing, and only the page can decide not to ask.
    * **Refresh** is the explicit re-fetch.
    """
    if trigger not in _FETCH_TRIGGERS or feed_cold:
        return False
    if not sf.needs_fetch(coverage):
        return False
    return not (trigger == NAVIGATE and have_dossier)


def reusable_dossier(dossier):
    """A cached dossier navigation should reuse rather than re-fetch.

    A ``no_quote`` result IS reusable — a typo will not start quoting in a
    minute, and must not re-spend on every visit. A ``fetch_failed`` one is not:
    it records an outage, and the next visit may well succeed."""
    return isinstance(dossier, dict) and dossier.get("error") != FETCH_FAILED


def fetch_command(raw):
    """The ``dossier`` command for ``raw``, or None when the allow-list refuses.

    ``shared.symbols.clean_symbol`` is the SAME allow-list the service applies:
    the symbol becomes part of a Redis key name there, so the page never builds
    a command for anything it would reject."""
    sym = clean_symbol(raw)
    return None if sym is None else {"type": "dossier", "args": {"symbol": sym}}


def dossier_view(raw):
    """``options:dossier:<SYMBOL>`` (the service's ``dossier_key`` without the
    ``cache:`` prefix bus_client adds), or None for a rejected symbol."""
    sym = clean_symbol(raw)
    return None if sym is None else f"options:dossier:{sym}"


# ── the poll ────────────────────────────────────────────────────────────────
# Every SHARED view, polled as ONE batch (the Desk's rule: a new view joins this
# tuple, never a poller of its own). The symbol's own dossier view is appended
# per page by ``poll_views``, since it is per-symbol.
VIEWS = ("options:matrix", "options:scan_funnel", "options:scan_day",
         "options:flow_alerts", "options:paper_account", "options:paper_trades",
         "options:driver_paper_account", "options:captured",
         "sentiment:regime", "sentiment:bullbear")

# Stands in for this page's ``options:dossier:<SYMBOL>`` in ``REGION_VIEWS``.
DOSSIER = "options:dossier"

# Big: the day union runs to megabytes by the close, so it is read through
# ``bus_client.read_gated`` rather than a plain read.
SCAN_DAY = "options:scan_day"

_BOOK_VIEWS = tuple(view for _tag, view, _key in sf.BOOK_VIEWS)

# Which views each band depends on — a repaint touches only the bands whose
# inputs moved. Coverage (scanned / collected / unknown) reads the matrix and
# the funnel, so every fact band depends on both.
REGION_VIEWS = {
    "header": ("options:matrix", "options:scan_funnel", DOSSIER),
    "structure": ("options:matrix", "options:scan_funnel", DOSSIER),
    "volatility": ("options:matrix", "options:scan_funnel", DOSSIER),
    "context": ("sentiment:regime", "sentiment:bullbear",
                "options:matrix", "options:scan_funnel", DOSSIER),
    "signals": (SCAN_DAY,),
    "flow": ("options:flow_alerts",),
    "positions": _BOOK_VIEWS,
}

POLL_SEC = 2.0


def poll_views(symbol):
    """The shared ``VIEWS`` plus this symbol's dossier view (none if rejected)."""
    view = dossier_view(symbol)
    return VIEWS if view is None else VIEWS + (view,)


def regions_for(changed, symbol):
    """The bands to repaint for the set of ``changed`` views.

    This symbol's dossier view maps to the ``DOSSIER`` token; any other
    symbol's dossier view is not this page's and repaints nothing."""
    own = dossier_view(symbol)
    tokens = set()
    for v in changed:
        if v == own:
            tokens.add(DOSSIER)
        elif not str(v).startswith(DOSSIER + ":"):
            tokens.add(v)
    return {region for region, deps in REGION_VIEWS.items()
            if tokens.intersection(deps)}


def today_ct():
    """Today in CENTRAL time — the basis the day envelope is stamped with."""
    return _scanner.today_ct()


# ── the header chip ─────────────────────────────────────────────────────────
# A finite set of tones, each a fixed console class (the Tailwind-first rule).
CHIP_TONES = {"pos": CON_POS, "accent": CON_ACCENT, "warn": CON_WARN,
              "neg": CON_NEG, "muted": CON_TXT_MUTED}


def _hhmm(stamp):
    """``HH:MM`` of an ISO timestamp, or None when there is no time in it."""
    try:
        return _dt.datetime.fromisoformat(str(stamp)).strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def coverage_chip(symbol, coverage, dossier, *, pending=False, raw=""):
    """``{"label", "tone", "message"}`` for the header.

    The three absences the design separates never share a sentence: a symbol
    Schwab did not quote (``no_quote`` — "check the symbol"), a fetch that could
    not reach Schwab at all (``fetch_failed`` — an outage, and the ticker may be
    fine, so it NEVER says "check the symbol"), and a ticker the allow-list
    refused before anything was asked.
    """
    tone = CHIP_TONES
    if symbol is None:
        text = str(raw or "").strip()
        if not text:
            return {"label": "", "tone": tone["muted"],
                    "message": "Type a ticker to open its dossier."}
        return {"label": "NOT FOUND", "tone": tone["neg"],
                "message": f"{text[:24]} is not a ticker symbol."}
    if coverage == sf.SCANNED:
        return {"label": "SCANNED", "tone": tone["pos"], "message": ""}
    d = dossier if isinstance(dossier, dict) else None
    error = d.get("error") if d else None
    if pending:
        return {"label": "FETCHING", "tone": tone["accent"],
                "message": f"Fetching {symbol}…"}
    if error == FETCH_FAILED:
        return {"label": "FETCH FAILED", "tone": tone["warn"],
                "message": (f"Couldn't fetch {symbol} — the quote feed didn't "
                            "answer. Try Refresh in a minute.")}
    if coverage == sf.COLLECTED:
        # A collected symbol HAS a quote (it has a matrix row), so a fetch's
        # no-quote is moot here rather than a verdict on the ticker.
        when = _hhmm(d.get("fetched_at")) if d and error is None else None
        label = f"COLLECTED · FETCHED {when}" if when else "COLLECTED"
        return {"label": label, "tone": tone["accent"], "message": ""}
    if error == NO_QUOTE:
        return {"label": "NOT FOUND", "tone": tone["neg"],
                "message": f"No quote for {symbol} — check the symbol"}
    if d is not None:
        when = _hhmm(d.get("fetched_at"))
        return {"label": f"FETCHED {when}" if when else "FETCHED",
                "tone": tone["accent"], "message": ""}
    return {"label": "NO DATA", "tone": tone["muted"],
            "message": f"No reading for {symbol} yet — try Refresh."}


def absence_message(symbol, chip, feed_cold, empty_line):
    """What a fact band says when it has nothing to draw.

    The chip's own sentence wins when it explains the gap (fetching / not found
    / fetch failed / a rejected ticker); then a cold options feed; and only then
    the band's own "nothing here" line — a dead feed and a quiet tape must never
    read alike."""
    if symbol is None or chip.get("label") in ("FETCHING", "NOT FOUND",
                                               "FETCH FAILED"):
        return chip.get("message") or _DASH
    if feed_cold:
        return _copy.WAITING_OPTIONS
    return empty_line


# ── the earnings line ───────────────────────────────────────────────────────

def earnings_line(date, status, today=None):
    """One line for the Context band, keeping the THREE-valued status.

    ``not_listed`` means the vendor does not cover the name, and renders "not
    covered" — never "none scheduled", which is a different fact (the name is
    covered and has no report). Conflating them is how an earnings gate fails
    open. A cached date without a status (the scan funnel carries the date but
    not the status) still prints the date; no date and no status claims nothing.
    """
    if status == "not_listed":
        return "Earnings: not covered by the calendar"
    if status == "none_scheduled":
        return "Earnings: none scheduled"
    try:
        when = _dt.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        when = None
    if when is None:
        if status == "upcoming":
            return "Earnings: upcoming, date not read"
        return "Earnings: no date read"
    try:
        base = _dt.date.fromisoformat(str(today or today_ct())[:10])
    except (TypeError, ValueError):
        base = None
    head = f"Earnings {when.strftime('%b')} {when.day}"
    if base is None:
        return head
    days = (when - base).days
    if days == 0:
        tail = "today"
    elif days == 1:
        tail = "tomorrow"
    elif days > 1:
        tail = f"in {days} days"
    else:
        tail = f"{-days} day{'s' if days != -1 else ''} ago"
    return f"{head} · {tail}"


# ── the fact bands ──────────────────────────────────────────────────────────

def structure_band(facts):
    """The Structure band: the wall/flip/spot bar, the flip side, net GEX.

    Walls go through the Desk's own ``_walls_trustworthy`` — a net GEX of
    exactly zero is the after-hours all-zero grid, whose walls are an argmax
    tie-break, not a level. A fetched symbol carries no ``gex_regime``, so its
    regime word comes from its own flip read (the same above/below question).
    """
    f = facts if isinstance(facts, dict) else {}
    spot, flip = _num(f.get("spot")), _num(f.get("flip"))
    net_gex = _num(f.get("net_gex"))
    raw_pw, raw_cw = _num(f.get("put_wall")), _num(f.get("call_wall"))
    walls_ok = _desk._walls_trustworthy(net_gex, False)
    pw, cw = (raw_pw, raw_cw) if walls_ok else (None, None)
    side, dist = _desk._flip_read(spot, flip)
    regime = f.get("gex_regime") or side
    return {"spot": spot, "flip": flip, "put_wall": pw, "call_wall": cw,
            "side": side, "distance": dist,
            "net_gex": net_gex, "net_gex_text": _desk.fmt_gex(net_gex),
            "regime_word": _desk.regime_word(regime),
            "setup": _desk.setup_word(f.get("dealer_regime")),
            "pos": structure_positions(spot, flip, pw, cw),
            "walls_withheld": (not walls_ok
                               and (raw_pw is not None or raw_cw is not None)),
            "has_any": any(v is not None for v in (spot, flip, pw, cw))}


# The IV-vs-HV band word -> a fixed console class.
_BAND_CLASS = {"high": CON_WARN, "low": CON_ACCENT, "mid": CON_TXT,
               "na": CON_TXT_MUTED}


def volatility_band(facts):
    """The Volatility band: Vol Rank, IV vs HV, ATM IV and the expected move.

    IV vs HV uses ``symbol_facts.iv_vs_hv`` — the scorer's own band words and
    boundaries. The expected move is ``symbol_facts.expected_move`` off the
    matrix's ATM IV, falling back to the fetched ``current_iv`` (the same
    percent basis) for an off-watchlist symbol that has no matrix row.
    """
    f = facts if isinstance(facts, dict) else {}
    iv, hv = _num(f.get("current_iv")), _num(f.get("hv_current"))
    ratio = sf.iv_vs_hv(iv, hv)
    if ratio["ratio"] is None:
        text = "IV vs HV —"
    else:
        text = (f"IV {iv:.1f} vs HV {hv:.1f} · {ratio['band']} "
                f"({ratio['ratio']:.2f}×)")
    atm = _num(f.get("atm_iv"))
    em = sf.expected_move(f.get("spot"), atm if atm is not None else iv)
    return {"iv_rank": _num(f.get("iv_rank")), "band": ratio["band"],
            "band_class": _BAND_CLASS.get(ratio["band"], CON_TXT_MUTED),
            "iv_hv_text": text, "atm_iv": atm, "iv_state": f.get("iv_state"),
            "em_day": em["day"], "em_week": em["week"]}


def _em_text(v):
    f = _num(v)
    return _DASH if f is None else f"±${f:,.2f}"


def context_band(ctx):
    """The Context band's words: regime, sector / industry, quadrant, rank."""
    c = ctx if isinstance(ctx, dict) else {}
    row = c.get("bullbear") if isinstance(c.get("bullbear"), dict) else None
    rank, prev = (_num(row.get("rank")), _num(row.get("rank_prev"))) \
        if row else (None, None)
    rank_text = ""
    if rank is not None:
        rank_text = f"#{int(rank)}"
        if prev is not None and int(prev) != int(rank):
            rank_text += f" (was {int(prev)})"
    reg = c.get("regime")
    place = ""
    if row:
        place = " / ".join(str(x) for x in (row.get("sector"),
                                           row.get("industry")) if x)
    return {"regime": reg, "place": place, "quadrant": c.get("quadrant"),
            "quadrant_label": c.get("quadrant_label"), "rank_text": rank_text,
            "on_map": row is not None}


# ── today: signals, flow ────────────────────────────────────────────────────
_LIST_LABELS = {"signals_0dte": "0-DTE", "signals_swing": "Swing",
                "signals_directional": "Directional"}


def _display_rows(key, signals, setups):
    """One list's rows through the Market Scanner's own builders + stampers."""
    if key == "signals_directional":
        rows = _scanner.directional_rows(signals)
    else:
        rows = _scanner.signal_rows(signals)
    _scanner.stamp_stale(rows, signals)
    _scanner.stamp_persistence(rows, signals, setups)
    by_id = {s.get("id"): s for s in signals if s.get("id")}
    out = []
    for r in rows:
        sig = by_id.get(r.get("id")) or {}
        entry = setups.get(sig.get("setup_key"))
        scores = entry.get("scores") if isinstance(entry, dict) else None
        out.append({
            "id": r.get("id"), "list": _LIST_LABELS.get(key, key),
            "what": r.get("type") or r.get("strategy_label") or _DASH,
            "strikes": r.get("strikes") or r.get("legs") or "",
            "expiration": r.get("expiration") or "", "dte": r.get("dte"),
            "score": r.get("composite_score"),
            "score_class": r.get("_score_class", ""),
            "seen_since": r.get("seen_since", _DASH),
            "score_trend": r.get("score_trend", _DASH),
            "trend_class": r.get("_trend_class", CON_TXT_MUTED),
            "stale": bool(r.get("_stale")),
            "stale_since": r.get("stale_since", ""),
            "spark": _persistence.score_sparkline(scores),
            "setup_key": sig.get("setup_key"),
        })
    return out


def signal_band(symbol, day_env, today=None):
    """``{"state", "message", "rows", "setups"}`` for the Signals column.

    Three absences, three sentences: a feed that has published nothing
    (``WAITING_OPTIONS``), an envelope from a previous session (the scanner's
    own ``day_note`` — never rendered as live), and a quiet tape for this name.

    ``setups`` holds one ``persistence_facts`` detail line per distinct setup
    among the rows ("Live since 09:15 · 1 gap"): the setup can outlive any one
    row, which is the whole reason the coarse key exists.
    """
    sym = clean_symbol(symbol)
    if not isinstance(day_env, dict) or not day_env:
        return {"state": "cold", "message": _copy.WAITING_OPTIONS,
                "rows": [], "setups": []}
    today = today or today_ct()
    if not _scanner.day_is_today(day_env, today):
        return {"state": "stale", "message": _scanner.day_note(day_env, today),
                "rows": [], "setups": []}
    signals = sf.signals_for(sym, day_env, today)
    if not signals:
        return {"state": "empty", "message": f"No signals for {sym} today.",
                "rows": [], "setups": []}
    setups = day_env.get("setups")
    setups = setups if isinstance(setups, dict) else {}
    rows = []
    for key in _scanner.DAY_LISTS:
        mine = [s for s in signals if s.get("list") == key]
        if mine:
            rows.extend(_display_rows(key, mine, setups))
    seen, details = set(), []
    for r in rows:
        k = r["setup_key"]
        if k is None or k in seen:
            continue
        seen.add(k)
        detail = _persistence.persistence_facts(setups.get(k))["detail"]
        if detail:
            details.append({"what": r["what"], "detail": detail})
    return {"state": "rows", "message": "", "rows": rows, "setups": details}


def flow_band(symbol, flow_env):
    """Today's flow alerts in the symbol, newest first, as Flow Alerts builds
    them — or the cold-feed / quiet-tape line."""
    sym = clean_symbol(symbol)
    if not isinstance(flow_env, dict):
        return {"message": _copy.WAITING_OPTIONS, "rows": []}
    rows = sf.alerts_for(sym, flow_env)
    if not rows:
        return {"message": f"No flow alerts for {sym} today.", "rows": []}
    return {"message": "", "rows": [
        {"time": r.get("time", ""), "kind": _desk.flow_kind_text(r),
         "detail": r.get("detail", ""), "tone": r.get("_tone_class", ""),
         "text": r.get("text", "")} for r in rows]}


# Which books the manage cycle's rescue overlay tags (the Desk's ``BOOKS``):
# the automated paper account and the driver. The ledger and the captured tape
# are never inspected, so their rows print an em-dash, not "OK".
_RESCUE_BOOKS = ("account", "driver")
_BOOK_LABEL = {"account": "PAPER ACCOUNT", "ledger": "LEDGER",
               "driver": "CLAUDE", "captured": "CAPTURED"}


def position_band(symbol, books):
    """Open rows in the symbol across all four books, or the absence line."""
    sym = clean_symbol(symbol)
    books = books if isinstance(books, dict) else {}
    if all(books.get(v) is None for v in _BOOK_VIEWS):
        return {"message": _copy.WAITING_OPTIONS, "rows": []}
    rows = sf.position_rows(sym, books)
    if not rows:
        return {"message": f"No open position in {sym}.", "rows": []}
    out = []
    for p in rows:
        tagged = p["book"] in _RESCUE_BOOKS
        dte = _paper._dte_from_expiration(p.get("expiration"))
        out.append({
            "book": p["book"], "book_label": _BOOK_LABEL.get(p["book"], p["book"]),
            "strategy": _desk.strategy_label(p.get("strategy") or p.get("type")),
            "strikes": _desk.strikes_text(p),
            "expiry": _desk.expiry_text({"expiration": p.get("expiration"),
                                         "dte": dte}),
            "quantity": _num(p.get("quantity")),
            "unrealized": _num(p.get("unrealized_pnl")),
            "rescue_state": p.get("rescue_state") if tagged else None,
            "heat": _num(p.get("heat")) if tagged else None,
            "flag": _desk.position_flag(p.get("rescue_state"),
                                        rescue_tagged=tagged),
        })
    return {"message": "", "rows": out}


# ── widgets ─────────────────────────────────────────────────────────────────
_TITLE = (f"{CONSOLE_DISPLAY} text-[15px] font-bold tracking-[.16em] "
          f"leading-none {CON_TXT}")
_LINK = (f"text-[12px] tracking-[.06em] cursor-pointer {CON_ACCENT} "
         f"hover:underline self-start")
_LINE = f"text-[13px] leading-snug {CON_TXT}"
_SUB = f"text-[11px] leading-snug {CON_TXT_DIM}"
_EMPTY = f"text-[12px] {CON_TXT_MUTED} py-2"
_CHIP = "text-[11px] tracking-[.14em] px-2 py-[2px] rounded-[2px] border"
_ROW = f"w-full items-center gap-x-3 gap-y-1 flex-wrap py-[6px] border-b {CONSOLE_RULE}"


def _band(title, links=()):
    """A console card with a title; returns the body column the painter clears.

    ``links`` is ``((label, handler, route), ...)``; a link is drawn only when
    this process can navigate to ``route``."""
    with ui.column().classes(f"{CONSOLE_CARD} w-full min-w-0 px-4 py-3 gap-2"):
        ui.label(title).classes(f"{_TITLE} border-b {CONSOLE_RULE} pb-2 w-full")
        body = ui.column().classes("w-full min-w-0 gap-1")
        if links:
            with ui.row().classes("gap-4 flex-wrap pt-1"):
                for label, handler, route in links:
                    if _shell.can_navigate(route):
                        ui.label(f"→ {label}").classes(_LINK).on(
                            "click", guard(handler))
    return body


def _go_route(route):
    return lambda *_: _shell.navigate_to(route)


def render(symbol=None):
    """Mount the dossier for ``symbol`` (the ``?symbol=`` query parameter).

    ⚠ ``symbol`` arrives from a URL anyone can type. It goes through
    ``shared.symbols.clean_symbol`` — the same allow-list the service applies —
    before it names a view, a command or a Redis key; anything refused renders
    the not-found state and enqueues nothing.
    """
    raw = "" if symbol is None else str(symbol)
    sym = clean_symbol(raw)
    views = poll_views(sym)
    own_view = dossier_view(sym)
    state = {"versions": {}, "data": {}, "pending": False,
             "scan_day_memo": {}}

    if CONSOLE_FONT_HEAD_HTML:
        ui.add_head_html(CONSOLE_FONT_HEAD_HTML)
    overlay = build_loading_overlay()

    with ui.column().classes(f"{CONSOLE_PAGE} w-full gap-4 p-4"):
        # ── header ───────────────────────────────────────────────────────────
        with ui.column().classes(f"{CONSOLE_CARD} w-full px-4 py-3 gap-2"):
            with ui.row().classes("w-full items-center gap-x-4 gap-y-2 flex-wrap"):
                inp = ui.input(placeholder="Ticker", value=sym or raw).props(
                    "dense outlined dark").classes("w-[140px]")
                select_all_on_focus(inp)
                sym_lbl = ui.label(sym or _DASH).classes(
                    f"{CONSOLE_DISPLAY} text-[26px] font-bold tracking-[.06em] "
                    f"leading-none {CON_TXT}")
                spot_lbl = ui.label(_DASH).classes(
                    f"text-[20px] tabular-nums leading-none {CON_TXT}")
                day_lbl = ui.label("").classes(
                    "text-[14px] tabular-nums leading-none")
                chip_lbl = ui.label("").classes(_CHIP)
                ui.element("div").classes("grow")
                refresh_btn = ui.button("Refresh", icon="refresh",
                                        color=None).props(
                    "no-caps dense flat").classes(
                    f"text-[12px] tracking-[.1em] px-3 {CON_ACCENT}")
            msg_lbl = ui.label("").classes(_SUB)

        # ── bands ────────────────────────────────────────────────────────────
        with ui.element("div").classes(
                "grid grid-cols-1 lg:grid-cols-2 gap-4 w-full"):
            struct_body = _band("STRUCTURE", (
                ("Dealer Positioning",
                 lambda *_: _handoff.send_to_gamma(sym), "/options/gamma"),))
            vol_body = _band("VOLATILITY", (
                ("Expected Move", _go_route("/options/expected-move"),
                 "/options/expected-move"),))
        ctx_body = _band("CONTEXT", (
            ("Bull / Bear Map", _go_route("/sentiment/bullbear"),
             "/sentiment/bullbear"),))
        with ui.element("div").classes(
                "grid grid-cols-1 lg:grid-cols-2 gap-4 w-full"):
            sig_body = _band("TODAY'S SIGNALS", (
                ("Market Scanner", _go_route("/options/scanner"),
                 "/options/scanner"),))
            flow_body = _band("FLOW ALERTS", (
                ("Flow Alerts", _go_route("/options/flow"), "/options/flow"),))
        pos_body = _band("YOUR POSITION", (
            ("Paper Ledger", _go_route("/options/paper"), "/options/paper"),
            ("Rescue", _go_route("/options/rescue"), "/options/rescue")))

    # ── state readers ────────────────────────────────────────────────────────
    def _d(view):
        return state["data"].get(view)

    def _coverage():
        return sf.symbol_coverage(sym, _d("options:matrix"),
                                  _d("options:scan_funnel"))

    def _feed_cold():
        return _d("options:matrix") is None

    def _merged():
        cached = sf.cached_facts(sym, _d("options:matrix"),
                                 _d("options:scan_funnel"))
        return sf.merge_facts(cached, _d(own_view) if own_view else None)

    def _chip():
        return coverage_chip(sym, _coverage(),
                             _d(own_view) if own_view else None,
                             pending=state["pending"], raw=raw)

    # ── painters ─────────────────────────────────────────────────────────────
    def _paint_header():
        facts = _merged()["facts"]
        chip = _chip()
        spot_lbl.text = _desk.fmt_price(facts["spot"])
        day_lbl.text = (_desk.fmt_signed_pct(facts["day_pct"])
                        if facts["day_pct"] is not None else "")
        day_lbl.classes(remove=f"{CON_POS} {CON_NEG} {CON_TXT_MUTED}",
                        add=_desk.signed_class(facts["day_pct"]))
        chip_lbl.text = chip["label"]
        chip_lbl.classes(remove=" ".join(set(CHIP_TONES.values())),
                         add=chip["tone"])
        chip_lbl.set_visibility(bool(chip["label"]))
        msg_lbl.text = chip["message"]
        msg_lbl.set_visibility(bool(chip["message"]))

    def _absent(body, empty_line):
        with body:
            ui.label(absence_message(sym, _chip(), _feed_cold(),
                                     empty_line)).classes(_EMPTY)

    def _paint_structure():
        struct_body.clear()
        s = structure_band(_merged()["facts"])
        if sym is None or not s["has_any"]:
            _absent(struct_body, f"No dealer structure read for {sym}.")
            return
        with struct_body:
            if s["pos"] is not None:
                _desk._structure_map(s["pos"])
                with ui.row().classes("w-full justify-between gap-2"):
                    ui.label(f"put wall {_desk.fmt_price(s['put_wall'])}").classes(
                        f"{_SUB} tabular-nums")
                    ui.label(f"call wall {_desk.fmt_price(s['call_wall'])}").classes(
                        f"{_SUB} tabular-nums")
            elif s["walls_withheld"]:
                ui.label("Walls withheld — net GEX reads exactly zero, the "
                         "after-hours signature of an empty grid.").classes(_SUB)
            side = s["side"]
            flip_line = f"flip {_desk.fmt_price(s['flip'])}"
            if side:
                flip_line = f"{side} flip ({s['distance']:.2f}%) · " + flip_line
            ui.label(flip_line).classes(
                f"{_LINE} {_desk.flip_side_class(side)}")
            ui.label(f"net GEX {s['net_gex_text']} · {s['regime_word']}"
                     + (f" · {s['setup']}" if s["setup"] else "")).classes(_SUB)

    def _paint_volatility():
        vol_body.clear()
        facts = _merged()["facts"]
        v = volatility_band(facts)
        if sym is None or all(x is None for x in (
                v["iv_rank"], v["atm_iv"], facts["current_iv"],
                facts["hv_current"])):
            _absent(vol_body, f"No volatility read for {sym}.")
            return
        with vol_body:
            with ui.row().classes("items-center gap-3 flex-wrap"):
                ui.label("Vol Rank").classes(_SUB)
                if v["iv_rank"] is not None:
                    ui.html(_svg.gradient_bar_svg(v["iv_rank"]))
                    ui.label(f"{v['iv_rank']:.0f}").classes(
                        f"{_LINE} tabular-nums")
                else:
                    ui.label(_DASH).classes(_LINE)
            ui.label(v["iv_hv_text"]).classes(f"{_LINE} {v['band_class']}")
            atm = v["atm_iv"]
            with ui.row().classes("items-baseline gap-2"):
                ui.label(f"ATM IV {_desk.fmt_iv(atm)}").classes(_LINE)
                if v["iv_state"] and v["iv_state"] != "na":
                    ui.label(str(v["iv_state"])).classes(
                        f"text-[12px] {_desk.iv_state_class(v['iv_state'])}")
            ui.label(f"Expected move {_em_text(v['em_day'])} day · "
                     f"{_em_text(v['em_week'])} week").classes(_SUB)

    def _paint_context():
        ctx_body.clear()
        merged = _merged()
        c = context_band(sf.context_facts(sym, _d("sentiment:regime"),
                                          _d("sentiment:bullbear")))
        facts = merged["facts"]
        with ctx_body:
            with ui.row().classes("w-full items-baseline gap-x-6 gap-y-1 flex-wrap"):
                reg = c["regime"]
                if reg:
                    ui.label(reg["word"]).classes(
                        f"{_LINE} font-semibold {_desk.regime_tone(reg)}"
                    ).tooltip(reg.get("tip") or "")
                else:
                    ui.label(_copy.WAITING_SENTIMENT).classes(_SUB)
                if sym is None:
                    pass                # the header already says why
                elif c["on_map"]:
                    if c["place"]:
                        ui.label(c["place"]).classes(_LINE)
                    ui.label(c["quadrant_label"] or _DASH).classes(
                        f"{_CHIP} {_bb.quadrant_class(c['quadrant'])}")
                    if c["rank_text"]:
                        ui.label(c["rank_text"]).classes(
                            f"{_LINE} tabular-nums")
                elif _d("sentiment:bullbear") is None:
                    ui.label(_copy.WAITING_SENTIMENT).classes(_SUB)
                else:
                    ui.label(f"{sym} is not on the Bull / Bear map.").classes(_SUB)
            if sym is not None:
                ui.label(earnings_line(facts["earnings_date"],
                                       facts["earnings_status"],
                                       today_ct())).classes(_LINE)

    def _paint_signals():
        sig_body.clear()
        if sym is None:
            _absent(sig_body, "")
            return
        band = signal_band(sym, _d(SCAN_DAY), today_ct())
        with sig_body:
            if not band["rows"]:
                ui.label(band["message"]).classes(_EMPTY)
                return
            for s in band["setups"]:
                ui.label(f"{s['what']} · {s['detail']}").classes(_SUB)
            for r in band["rows"]:
                row_cls = f"{_ROW} {_scanner.STALE_ROW_CLASS}" if r["stale"] else _ROW
                with ui.row().classes(row_cls):
                    ui.label(r["list"]).classes(f"{_CHIP} {CON_TXT_DIM}")
                    ui.label(f"{r['what']} {r['strikes']}".strip()).classes(_LINE)
                    ui.label(_desk.expiry_text(r)).classes(f"{_SUB} tabular-nums")
                    if r["score"] is not None:
                        ui.label(f"{r['score']:.0f}").classes(
                            f"text-[12px] px-2 rounded text-white tabular-nums "
                            f"{r['score_class']}")
                    ui.label(r["seen_since"]).classes(f"{_SUB} tabular-nums")
                    ui.label(r["score_trend"]).classes(
                        f"text-[12px] tabular-nums {r['trend_class']}")
                    if r["spark"]:
                        ui.html(r["spark"]).classes(CON_ACCENT)
                    if r["stale"]:
                        ui.label(f"dropped {r['stale_since']}".strip()).classes(_SUB)

    def _paint_flow():
        flow_body.clear()
        if sym is None:
            _absent(flow_body, "")
            return
        band = flow_band(sym, _d("options:flow_alerts"))
        with flow_body:
            if not band["rows"]:
                ui.label(band["message"]).classes(_EMPTY)
                return
            for r in band["rows"]:
                with ui.row().classes(_ROW):
                    ui.label(r["time"]).classes(f"{_SUB} tabular-nums")
                    ui.label(r["kind"]).classes(f"{_LINE} {r['tone']}".strip())
                    ui.label(r["detail"]).classes(_SUB)

    def _paint_positions():
        pos_body.clear()
        if sym is None:
            _absent(pos_body, "")
            return
        band = position_band(sym, {v: _d(v) for v in _BOOK_VIEWS})
        with pos_body:
            if not band["rows"]:
                ui.label(band["message"]).classes(_EMPTY)
                return
            for r in band["rows"]:
                with ui.row().classes(_ROW):
                    ui.label(r["book_label"]).classes(f"{_CHIP} {CON_TXT_DIM}")
                    ui.label(f"{r['strategy']} {r['strikes']}").classes(_LINE)
                    ui.label(r["expiry"]).classes(f"{_SUB} tabular-nums")
                    if r["quantity"] is not None:
                        ui.label(f"×{r['quantity']:g}").classes(
                            f"{_SUB} tabular-nums")
                    ui.label(_desk.fmt_money(r["unrealized"])).classes(
                        f"text-[13px] tabular-nums "
                        f"{_desk.signed_class(r['unrealized'])}")
                    ui.label(r["flag"]).classes(_desk.flag_chip_class(r["flag"]))

    painters = {"header": _paint_header, "structure": _paint_structure,
                "volatility": _paint_volatility, "context": _paint_context,
                "signals": _paint_signals, "flow": _paint_flow,
                "positions": _paint_positions}

    def _paint(payloads, regions=None):
        state["data"].update(payloads)
        if own_view in payloads and payloads[own_view] is not None \
                and state["pending"]:
            state["pending"] = False
            overlay.hide()
        todo = regions if regions is not None else regions_for(
            set(payloads), sym)
        for region in REGION_VIEWS:            # a stable paint order
            if region in todo:
                painters[region]()

    # ── reads ────────────────────────────────────────────────────────────────
    def _read_one(view):
        """``(payload, version)`` — the day union through ``read_gated``."""
        if view == SCAN_DAY:
            payload, _changed = bus_client.read_gated(view,
                                                      state["scan_day_memo"])
            memo = state["scan_day_memo"].get("state")
            return payload, (memo[0] if memo else None)
        return bus_client.read_full(view)

    def _read_all():
        return {v: _read_one(v) for v in views}

    def _seed(pairs):
        payloads = {}
        for v, (payload, version) in pairs.items():
            payloads[v] = payload
            state["versions"][v] = version
        _paint(payloads, regions=set(REGION_VIEWS))

    # ── the fetch — navigation and Refresh ONLY ──────────────────────────────
    def _enqueue_fetch(trigger):
        """Enqueue ONE dossier fetch if ``should_enqueue`` allows it.

        The only request site on the page. Its callers pass the trigger
        literally, and the poll is not one of them."""
        cmd = fetch_command(sym)
        if cmd is None or not _shell.may_enqueue():
            return False
        have = reusable_dossier(_d(own_view))
        if not should_enqueue(_coverage(), trigger, have_dossier=have,
                              feed_cold=_feed_cold()):
            return False
        bus_client.request("options", cmd)
        state["pending"] = True
        overlay.show(f"Fetching {sym}…")
        _paint_header()
        ui.timer(LOAD_TIMEOUT_SEC, _fetch_timeout, once=True)
        return True

    @guard
    def _fetch_timeout():
        # The backstop: the answer never came (service busy or down). Drop the
        # overlay and let the chip say there is no reading yet.
        if state["pending"]:
            state["pending"] = False
            overlay.hide()
            _paint({}, regions=set(REGION_VIEWS))

    # ── handlers ─────────────────────────────────────────────────────────────
    @guard
    def _open_typed():
        text = str(inp.value or "").strip()
        if text:
            _shell.navigate_to(f"{ROUTE}?symbol={_quote(text)}")

    bind_symbol_load(inp, _open_typed)

    @guard_async
    async def _on_refresh():
        _seed(await run.io_bound(_read_all))
        _enqueue_fetch("refresh")

    refresh_btn.on_click(_on_refresh)
    refresh_btn.set_enabled(sym is not None)

    @guard_async
    async def _poll():
        """ONE batched version probe; payloads read only for views that moved.

        Deliberately cannot fetch: a fetch spends Schwab calls, and only a
        navigation or a Refresh click may do that."""
        vers = await run.io_bound(bus_client.read_versions, list(views))
        changed = [v for v in views
                   if vers.get(v) is not None
                   and vers.get(v) != state["versions"].get(v)]
        if not changed:
            return
        payloads = {}
        for v in changed:
            payload, _version = await run.io_bound(_read_one, v)
            payloads[v] = payload
            state["versions"][v] = vers.get(v)
        _paint(payloads)

    # First paint: every band once, cold ones included, so each shows its OWN
    # placeholder. Then the one navigation fetch, if the cache cannot answer.
    _seed(_read_all())
    _enqueue_fetch("navigate")
    ui.timer(POLL_SEC, _poll)
