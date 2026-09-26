"""PURE facts for the news page, the Desk strip and the Symbol band.

Imports nothing from nicegui or bus_client; stdlib and ``shared.symbols`` only
(the Tier-1 allow-list - pinned by ``tests/test_news_view.py``).

Everything here tolerates junk: a payload that is not a dict, ``items`` that is
not a list, an item that is not a dict, or a ``tickers`` / ``sources`` field that
is not a list of strings is skipped or cleaned, never raised on. The published
feed payloads are ``{"items": [...]}``; nothing here reads any other key of
them. The calendar payload (``news:calendar``) is read only by
``calendar_groups`` / ``indicator_state``, and its settings
(``release_watch_min``, ``actual_fresh_h``) come from the payload's own
``settings`` key - Tier 1 never reads the calendar config.

An item's ``impact`` is ``{"band", "score", "reasons"}``; a missing or junk one
is NO band (``None``), never "low" - a row that was not scored must not read as
scored and unimportant.

⚠ A row's ``title`` and ``teaser`` are PLAIN TEXT from third-party feeds. The
page renders them through labels and links, which escape; a caller must never
hand them to ``ui.html`` unescaped.

Times display in CENTRAL time, and ``today`` is the Central date - the app's
convention (the header stamp, the Flow page and the Desk all read CT). A naive
``published_at`` / ``first_seen`` is read as UTC (the adapters emit UTC); an
unparseable one - or one too extreme to convert, like year 1 or 9999 - gives an
empty time, no age and ``today`` False. A naive ``now`` follows the project
convention instead: it is CENTRAL wall-clock time.
"""
import datetime as dt
import math
from collections import Counter
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from shared.symbols import clean_symbol

_CT = ZoneInfo("America/Chicago")   # display zone, and a naive ``now`` (CLAUDE.md)
VIEW = "news:feed"
VIEW_PUBLIC = "news:feed_public"
VIEW_STATUS = "news:status"
VIEW_SEC = "news:sec"
VIEW_SEC_PUBLIC = "news:sec_public"
VIEW_CAL = "news:calendar"
VIEW_CAL_PUBLIC = "news:calendar_public"
VIEW_CAL_STATUS = "news:calendar_status"      # private: carries error text

# Impact pill: a FIXED finite map (the Tailwind-first rule - no runtime colour).
BAND_CLASSES = {
    "high": "bg-rose-500/20 text-rose-300",
    "med": "bg-amber-500/15 text-amber-300",
    "low": "bg-white/5 text-[#7f8db0]",
    None: "",
}
BAND_LETTER = {"high": "H", "med": "M", "low": "L"}
_BAND_RANK = {"high": 3, "med": 2, "low": 1}

DESK_LIMIT = 5     # rows on the Desk's news strip
SYMBOL_LIMIT = 8   # rows on the Symbol dossier's news band


def _dt(s):
    """An aware datetime (naive read as UTC), or ``None``.

    A stamp that parses but cannot be carried into UTC and CT (year 1 with a
    positive offset, 9999-12-31 late with a negative one, year 1 naive) is
    ``None`` too: every consumer converts, and one such item must not take
    down the whole page."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        when = dt.datetime.fromisoformat(s.strip())
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        when.astimezone(dt.timezone.utc)
        when.astimezone(_CT)
    except (ValueError, OverflowError):
        return None
    return when


def _aware(now):
    """``now`` as an aware datetime; a naive one is Central wall-clock time."""
    return now if now.tzinfo else now.replace(tzinfo=_CT)


def _items(payload):
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def _str(v):
    return v if isinstance(v, str) else ""


def _tickers(raw):
    """Cleaned, de-duplicated tickers in order; a non-list is none."""
    out = []
    if isinstance(raw, list):
        for t in raw:
            sym = clean_symbol(t) if isinstance(t, str) else None
            if sym and sym not in out:
                out.append(sym)
    return out


def _sources(it):
    raw = it.get("sources")
    if isinstance(raw, list):
        out = []
        for s in raw:
            s = s.strip() if isinstance(s, str) else ""
            if s and s not in out:
                out.append(s)
        if out:
            return out
    one = _str(it.get("source")).strip()
    return [one] if one else []


def _time(ct):
    return ct.strftime("%I:%M %p").lstrip("0") if ct else ""


def _day(ct):
    """``"Sep 5"`` - month and an UNPADDED day."""
    return f"{ct.strftime('%b')} {ct.day}" if ct else ""


def _impact(it):
    """``(band, reasons)``; junk is ``(None, [])`` - never "low"."""
    imp = it.get("impact")
    if not isinstance(imp, dict):
        return None, []
    band = imp.get("band")
    band = band if isinstance(band, str) and band in _BAND_RANK else None
    raw = imp.get("reasons")
    reasons = [x for x in raw if isinstance(x, str)] if isinstance(raw, list) else []
    return band, reasons


def rows(payload, *, now) -> list:
    """One display dict per usable item, in payload order."""
    now = _aware(now)
    try:
        today_ct = now.astimezone(_CT).date()
    except (ValueError, OverflowError):
        today_ct = None
    out = []
    for it in _items(payload):
        published = it.get("published_at")
        when = _dt(published)
        ct = when.astimezone(_CT) if when else None
        today = ct is not None and ct.date() == today_ct
        time_, day = _time(ct), _day(ct)
        topics = it.get("topics")
        detail = it.get("detail")
        band, reasons = _impact(it)
        out.append({
            "id": it.get("id"), "title": _str(it.get("title")), "teaser": _str(it.get("teaser")),
            "url": _str(it.get("url")), "tickers": _tickers(it.get("tickers")),
            "sources": _sources(it), "original_source": _str(it.get("original_source")),
            "kind": _str(it.get("kind")),
            "topics": [t for t in topics if isinstance(t, str)] if isinstance(topics, list) else [],
            "detail": detail if isinstance(detail, dict) else {},
            "time": time_, "day": day, "today": today,
            "when": (time_ if today else f"{day} {time_}") if ct else "",
            "published_at": published, "first_seen": it.get("first_seen"),
            "age_min": ((now - when).total_seconds() / 60) if when else None,
            "band": band, "reasons": reasons,
        })
    return out


# A Yahoo per-ticker item is tagged with the ticker it was FETCHED for, not one
# its headline named, so it would make every polled name trend on its own.
_NOT_TRENDING_KINDS = frozenset({"yahoo_ticker"})


def trending(payload, *, now, window_h) -> list:
    """``[(TICKER, n_items)]`` over items published within ``window_h`` hours,
    most-mentioned first, ties alphabetical. An undated item never trends, and
    neither does a Yahoo per-ticker item: Trending counts tickers NAMED in the
    general feeds' headlines, not the ones the collector asked about."""
    counts = Counter()
    for r in rows(payload, now=now):
        if r["kind"] in _NOT_TRENDING_KINDS:
            continue
        if r["age_min"] is not None and r["age_min"] <= window_h * 60:
            counts.update(r["tickers"])
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def sources_present(rows_) -> list:
    return sorted({s for r in rows_ for s in (r.get("sources") or []) if isinstance(s, str) and s})


def _row_symbols(r):
    raw = r.get("tickers") if isinstance(r, dict) else None
    return set(_tickers(raw))


def filter_rows(rows_, *, sources, symbol, watchlist=None, min_band=None) -> list:
    """Rows matching every given filter. ``symbol`` is cleaned on both sides;
    a symbol that does not clean matches NOTHING (not everything).

    ``min_band`` (``"high"`` / ``"med"`` / ``"low"``) keeps rows at that band or
    above; an unbanded row never passes a band filter. ``None`` (or an unknown
    word) filters nothing."""
    floor = _BAND_RANK.get(min_band) if isinstance(min_band, str) else None
    sym = None
    if symbol:
        sym = clean_symbol(symbol) if isinstance(symbol, str) else None
        if not sym:
            return []
    watch = None
    if watchlist is not None:
        watch = {clean_symbol(w) for w in watchlist if isinstance(w, str)} - {None}
    if isinstance(sources, str):
        sources = [sources]          # a bare string is ONE source name, not its letters
    want_sources = set(sources) if sources else None
    out = []
    for r in rows_ or []:
        if not isinstance(r, dict):
            continue
        tickers = _row_symbols(r)
        if want_sources and not (set(r.get("sources") or []) & want_sources):
            continue
        if sym and sym not in tickers:
            continue
        if watch is not None and not (tickers & watch):
            continue
        if floor is not None and _BAND_RANK.get(r.get("band"), 0) < floor:
            continue
        out.append(r)
    return out


def unseen(payload, *, since) -> int:
    """How many items were first seen strictly AFTER ``since`` - compared as
    instants, never as strings. An unparseable stamp on either side never counts."""
    cutoff = _dt(since)
    if cutoff is None:
        return 0
    n = 0
    for it in _items(payload):
        seen = _dt(it.get("first_seen"))
        if seen is not None and seen > cutoff:
            n += 1
    return n


def for_symbol(payload, symbol, *, now, limit=SYMBOL_LIMIT) -> list:
    return filter_rows(rows(payload, now=now), sources=None, symbol=symbol)[:limit]


def safe_href(url):
    """``url`` (stripped) if a browser may follow it as a link, else ``None``.

    ``ui.link`` escapes its TEXT, not its href: a ``javascript:`` or ``data:``
    URL in a third-party feed would run on click. Only an absolute ``http`` /
    ``https`` address with a host qualifies; a caller draws the headline as
    plain text for anything else."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return url


def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


_MONEY_UNITS = ((1e9, "B", 1), (1e6, "M", 1), (1e3, "K", 0), (1.0, "", 0))


def _money(v):
    """``$136.4M``-style, matching the service's Form 4 titles.

    A figure that ROUNDS to 1000 of its unit steps up one unit, so 999,600
    reads ``$1.0M``, never ``$1000K``."""
    sign, a = ("-" if v < 0 else ""), abs(v)
    i = next((k for k, (scale, _, _) in enumerate(_MONEY_UNITS) if a >= scale),
             len(_MONEY_UNITS) - 1)
    while True:
        scale, suffix, places = _MONEY_UNITS[i]
        text = f"{a / scale:.{places}f}"
        if i == 0 or float(text) < 1000:
            return f"{sign}${text}{suffix}"
        i -= 1


def detail_line(row) -> str:
    """A one-line summary for a Form 4 or filing row; ``""`` for anything else.

    Form 4: ``"9 purchases · $136.4M · 2026-09-23"`` (each missing part omitted).
    Filing: ``"Form S-3"``."""
    if not isinstance(row, dict):
        return ""
    d = row.get("detail")
    if not isinstance(d, dict) or not d:
        return ""
    kind = row.get("kind")
    if kind == "edgar_form4":
        parts = []
        groups = d.get("groups")
        if isinstance(groups, list) and groups:
            n = len(groups)
            parts.append(f"{n} purchase{'s' if n != 1 else ''}")
        total = _finite(d.get("total_value"))
        if total is not None and total != 0:
            parts.append(_money(total))
        date = _str(d.get("transaction_date")).strip()
        if date:
            parts.append(date)
        return " · ".join(parts)
    if kind == "edgar_filings":
        form = _str(d.get("form")).strip()
        return f"Form {form}" if form else ""
    return ""



# ---- the SEC panel ----------------------------------------------------------


def sec_rows(payload, *, now) -> list:
    """``rows()`` plus ``symbol`` (the first tagged ticker, else the filer's
    cleaned symbol, else ``""``) and ``details`` (``detail_line``)."""
    out = []
    for r in rows(payload, now=now):
        sym = r["tickers"][0] if r["tickers"] else None
        if not sym:
            raw = r["detail"].get("symbol")
            sym = clean_symbol(raw) if isinstance(raw, str) else None
        out.append({**r, "symbol": sym or "", "details": detail_line(r)})
    return out


# ---- the economic calendar ----------------------------------------------------

DASH = "\u2014"
NO_NEXT = "Next date not yet published"
_DEFAULT_WATCH_MIN = 60.0
_DEFAULT_FRESH_H = 24.0

# (title, the sources that feed it, what an empty group says). The source names
# are the producer's ``sources`` keys; a group greys only when EVERY one of its
# sources the payload reports (``off`` aside) is ``stale`` or ``never``.
_GROUPS = (
    ("Economic news/Calendar", ("fed", "bls", "bea"), "No scheduled events ahead"),
    ("Dividend / IPO", ("dividends", "nasdaq_ipo"), "No dividends or IPOs ahead"),
    ("Economic data (CPI, PPI etc)", ("bls", "bea", "fred_calendar", "fred_api", "fredgraph"),
     "No indicators to show"),
)
_NOTE_STALE = "Source unavailable \u2014 showing the last good reading"
_NOTE_NEVER = "Not published yet"


# The largest setting a timedelta is ever built from: a week of watch window,
# a year of freshness. Anything past these (or not finite and positive) is junk
# in the payload and reads as the default - never an OverflowError.
_MAX_WATCH_MIN = 10080.0
_MAX_FRESH_H = 8760.0


def _setting(cfg, key, default, hi):
    v = _finite(cfg.get(key)) if isinstance(cfg, dict) else None
    return v if v is not None and 0 < v <= hi else default


def _explicitly_not_bootstrap(flag):
    """Only an explicit ``False`` (or a plain int ``0``) says an observation is
    NOT a first fill. A missing key, ``"true"``, ``None`` or anything else is
    treated as bootstrap: an unclear flag must not promote a first-fill value
    to a fresh Actual."""
    if flag is False:
        return True
    return type(flag) is int and flag == 0


def _weekday_day(d):
    return f"{d.strftime('%a %b')} {d.day}"


def _date(s):
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return dt.date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def _when_text(at, date):
    """``"Wed Oct 28 \u00b7 1:00 PM CT"`` for an instant, ``"Mon Oct 5"`` for a date
    only, ``""`` for neither."""
    when = _dt(at)
    if when is not None:
        ct = when.astimezone(_CT)
        return f"{_weekday_day(ct)} \u00b7 {_time(ct)} CT"
    d = _date(date)
    return _weekday_day(d) if d else ""


def fmt_indicator(value, unit) -> str:
    """The display figure for one derived indicator value; ``"\u2014"`` for no
    reading (``None``, NaN, infinity, a bool or a string) - never ``0.0%``."""
    v = _finite(value)
    if v is None:
        return DASH
    places = 0 if unit in ("change_k", "level_k") else 1
    text = f"{v:.{places}f}"
    if float(text) == 0:
        text = text.lstrip("-")                 # never "-0.0"
    if unit in ("pct_mom", "change_k") and float(text) > 0:
        text = "+" + text                       # a change carries its sign
    suffix = {"pct_mom": "% m/m", "change_k": "K", "level_pct": "%",
              "level_k": "K", "pct_saar": "% SAAR"}.get(unit, "")
    return text + suffix


def _obs(ind, key):
    o = ind.get(key)
    return o if isinstance(o, dict) else {}


def indicator_state(ind, now, cfg) -> dict:
    """Actual / Prior / state / next-release text for one indicator.

    Decided HERE, from facts the payload carries, because it is a function of
    ``now``: the latest observation is the last release's ACTUAL only if it was
    first seen at or after that release - and a ``bootstrap`` observation (the
    series' first fill, stamped with the fill time) only once the watch window
    has passed. Within ``actual_fresh_h`` of the release the tile reads
    ``released`` (value landed) or ``awaiting`` (not yet); otherwise it is
    ``upcoming``: Actual \u2014, Prior = the latest observation.

    A ``next_release_at`` the clock has already passed (a payload older than
    the release) is treated as the last release, and the next is unknown."""
    ind = ind if isinstance(ind, dict) else {}
    now = _aware(now)
    watch = dt.timedelta(minutes=_setting(cfg, "release_watch_min", _DEFAULT_WATCH_MIN,
                                          _MAX_WATCH_MIN))
    fresh = dt.timedelta(hours=_setting(cfg, "actual_fresh_h", _DEFAULT_FRESH_H, _MAX_FRESH_H))
    unit = _str(ind.get("unit"))
    latest, prior = _obs(ind, "latest"), _obs(ind, "prior")
    latest_txt = fmt_indicator(latest.get("value"), unit)
    prior_txt = fmt_indicator(prior.get("value"), unit)

    last = _dt(ind.get("last_release_at"))
    if last is not None and last > now:
        last = None
    nxt, next_date = _dt(ind.get("next_release_at")), ind.get("next_date")
    if nxt is not None and nxt <= now:
        last = nxt if last is None or nxt > last else last
        nxt, next_date = None, None

    state, actual, shown_prior = "upcoming", DASH, latest_txt
    if last is not None and now - last <= fresh:
        seen = _dt(latest.get("first_seen"))
        boot = not _explicitly_not_bootstrap(latest.get("bootstrap"))
        after = seen is not None and seen >= last
        if after and (not boot or now - last > watch):
            state, actual, shown_prior = "released", latest_txt, prior_txt
        else:
            state = "awaiting"
            # a bootstrap value first seen after the release may BE the new
            # figure: it cannot be called the prior either
            shown_prior = DASH if (after and boot) else latest_txt

    if nxt is not None:
        next_txt = _when_text(ind.get("next_release_at"), None)
    else:
        d = _date(next_date)
        try:
            today = now.astimezone(_CT).date()
        except (ValueError, OverflowError):
            today = None
        next_txt = _weekday_day(d) if d and today and d >= today else NO_NEXT
    return {"key": _str(ind.get("key")), "label": _str(ind.get("label")),
            "actual": actual, "prior": shown_prior, "state": state, "next": next_txt}


def _list(payload, key):
    v = payload.get(key) if isinstance(payload, dict) else None
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _today_ct(now):
    try:
        return _aware(now).astimezone(_CT).date()
    except (ValueError, OverflowError, AttributeError, TypeError):
        return None


def _event_tile(ev, now, today):
    """``None`` for an event with no title, or one already past: by its
    instant when it has one, else by its Central date."""
    title = _str(ev.get("title")).strip()
    if not title:
        return None
    when = _dt(ev.get("at"))
    if when is not None:
        if when < _aware(now):
            return None
    else:
        d = _date(ev.get("date"))
        if d is not None and today is not None and d < today:
            return None
    return {"title": title, "when": _when_text(ev.get("at"), ev.get("date")), "lines": []}


def _dividend_tile(d, today):
    """``None`` without a symbol or an ex-date, or once the ex-date is past."""
    sym = clean_symbol(d["symbol"]) if isinstance(d.get("symbol"), str) else None
    ex = _date(d.get("ex_date"))
    if not sym or ex is None or (today is not None and ex < today):
        return None
    pay = _date(d.get("pay_date"))
    amount = _finite(d.get("amount"))
    lines = []
    if amount is not None and amount > 0:
        lines.append(f"${amount:.2f} a share")
    if pay:
        lines.append(f"Pays {_weekday_day(pay)}")
    return {"title": f"{sym} dividend" if sym else "Dividend",
            "when": f"Ex-div {_weekday_day(ex)}" if ex else "", "lines": lines}


def _ipo_tile(i, today):
    """``None`` with neither a symbol nor a company, with no date, or once the
    date is past."""
    sym = clean_symbol(i["symbol"]) if isinstance(i.get("symbol"), str) else None
    company = _str(i.get("company")).strip()
    day = _date(i.get("date"))
    if not (sym or company) or day is None or (today is not None and day < today):
        return None
    name = " \u00b7 ".join(x for x in (sym, company) if x)
    lines = []
    price = _finite(i.get("price"))
    rng = _str(i.get("price_range")).strip()
    if price is not None and price > 0:
        lines.append(f"Priced ${price:.2f}")
    elif rng:
        lines.append(rng if rng.startswith("$") else f"${rng}")
    offer = _finite(i.get("offer_usd"))
    if offer is not None and offer > 0:
        lines.append(f"{_money(offer)} offer")
    return {"title": f"{name} IPO" if name else "IPO",
            "when": _when_text(None, i.get("date")), "lines": lines}


def _data_tiles(data, now, cfg):
    tiles, by_title = [], {}
    for ind in data:
        title = _str(ind.get("tile")).strip() or _str(ind.get("label")).strip()
        if not title:
            continue
        state = indicator_state(ind, now, cfg)
        tile = by_title.get(title)
        if tile is None:
            tile = {"title": title, "when": state["next"], "lines": [], "indicators": []}
            by_title[title] = tile
            tiles.append(tile)
        tile["indicators"].append(state)
    return tiles


def _group_note(sources, names):
    states = [sources[n] for n in names
              if isinstance(sources.get(n), str) and sources[n] != "off"]
    if not states or any(s not in ("stale", "never") for s in states):
        return None
    return _NOTE_NEVER if all(s == "never" for s in states) else _NOTE_STALE


def calendar_groups(payload, *, now) -> list:
    """The calendar's three groups, in order: ``{"title", "tiles", "note",
    "empty"}``. A tile is ``{"title", "when", "lines"}`` (a data tile adds
    ``indicators``, each an ``indicator_state``). ``note`` greys a group whose
    sources all failed; ``empty`` is the plain sentence for a group with no
    tiles (``None`` otherwise).

    A tile with nothing to say is skipped (an event with no title, a dividend
    with no symbol or ex-date, an IPO with no name or date, a data item with no
    tile or label), and so is anything already past ``now`` - an event by its
    instant, else by its Central date; a dividend or IPO by its Central date."""
    cfg = payload.get("settings") if isinstance(payload, dict) else None
    raw_sources = payload.get("sources") if isinstance(payload, dict) else None
    sources = raw_sources if isinstance(raw_sources, dict) else {}
    today = _today_ct(now)
    events = [_event_tile(e, now, today) for e in _list(payload, "events")]
    others = ([_dividend_tile(d, today) for d in _list(payload, "dividends")]
              + [_ipo_tile(i, today) for i in _list(payload, "ipos")])
    tile_sets = (
        [t for t in events if t is not None],
        [t for t in others if t is not None],
        _data_tiles(_list(payload, "data"), now, cfg),
    )
    return [{"title": title, "tiles": tiles, "note": _group_note(sources, names),
             "empty": None if tiles else empty}
            for (title, names, empty), tiles in zip(_GROUPS, tile_sets)]
