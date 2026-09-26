"""PURE facts for the news page, the Desk strip and the Symbol band.

Imports nothing from nicegui or bus_client; stdlib and ``shared.symbols`` only
(the Tier-1 allow-list - pinned by ``tests/test_news_view.py``).

Everything here tolerates junk: a payload that is not a dict, ``items`` that is
not a list, an item that is not a dict, or a ``tickers`` / ``sources`` field that
is not a list of strings is skipped or cleaned, never raised on. The published
payload is ``{"items": [...]}``; nothing here reads any other key of it.

⚠ A row's ``title`` and ``teaser`` are PLAIN TEXT from third-party feeds. The
page renders them through labels and links, which escape; a caller must never
hand them to ``ui.html`` unescaped.

Times display in ET. A naive ``published_at`` / ``first_seen`` is read as UTC
(the adapters emit UTC); an unparseable one - or one too extreme to convert,
like year 1 or 9999 - gives an empty time, no age and ``today`` False. A naive
``now`` follows the project convention instead: it is CENTRAL wall-clock time.
"""
import datetime as dt
import math
from collections import Counter
from zoneinfo import ZoneInfo

from shared.symbols import clean_symbol

_ET = ZoneInfo("America/New_York")
_CT = ZoneInfo("America/Chicago")   # a naive ``now`` is Central (CLAUDE.md)
VIEW = "news:feed"
VIEW_PUBLIC = "news:feed_public"
VIEW_STATUS = "news:status"

DESK_LIMIT = 5     # rows on the Desk's news strip
SYMBOL_LIMIT = 8   # rows on the Symbol dossier's news band


def _dt(s):
    """An aware datetime (naive read as UTC), or ``None``.

    A stamp that parses but cannot be carried into UTC and ET (year 1 with a
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
        when.astimezone(_ET)
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


def _time(et):
    return et.strftime("%I:%M %p").lstrip("0") if et else ""


def _day(et):
    """``"Sep 5"`` - month and an UNPADDED day."""
    return f"{et.strftime('%b')} {et.day}" if et else ""


def rows(payload, *, now) -> list:
    """One display dict per usable item, in payload order."""
    now = _aware(now)
    try:
        today_et = now.astimezone(_ET).date()
    except (ValueError, OverflowError):
        today_et = None
    out = []
    for it in _items(payload):
        published = it.get("published_at")
        when = _dt(published)
        et = when.astimezone(_ET) if when else None
        today = et is not None and et.date() == today_et
        time_, day = _time(et), _day(et)
        topics = it.get("topics")
        detail = it.get("detail")
        out.append({
            "id": it.get("id"), "title": _str(it.get("title")), "teaser": _str(it.get("teaser")),
            "url": _str(it.get("url")), "tickers": _tickers(it.get("tickers")),
            "sources": _sources(it), "original_source": _str(it.get("original_source")),
            "kind": _str(it.get("kind")),
            "topics": [t for t in topics if isinstance(t, str)] if isinstance(topics, list) else [],
            "detail": detail if isinstance(detail, dict) else {},
            "time": time_, "day": day, "today": today,
            "when": (time_ if today else f"{day} {time_}") if et else "",
            "published_at": published, "first_seen": it.get("first_seen"),
            "age_min": ((now - when).total_seconds() / 60) if when else None,
        })
    return out


def trending(payload, *, now, window_h) -> list:
    """``[(TICKER, n_items)]`` over items published within ``window_h`` hours,
    most-mentioned first, ties alphabetical. An undated item never trends."""
    counts = Counter()
    for r in rows(payload, now=now):
        if r["age_min"] is not None and r["age_min"] <= window_h * 60:
            counts.update(r["tickers"])
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def sources_present(rows_) -> list:
    return sorted({s for r in rows_ for s in (r.get("sources") or []) if isinstance(s, str) and s})


def _row_symbols(r):
    raw = r.get("tickers") if isinstance(r, dict) else None
    return set(_tickers(raw))


def filter_rows(rows_, *, sources, symbol, watchlist=None) -> list:
    """Rows matching every given filter. ``symbol`` is cleaned on both sides;
    a symbol that does not clean matches NOTHING (not everything)."""
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
