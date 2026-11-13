"""PURE view model for the X Feed page (``pages/xfeed.py``).

Imports nothing from nicegui, bus_client or ``services.*``: stdlib and
``pages.news_view.safe_href`` only (the Tier-1 allow-list - pinned by
``tests/test_xfeed_view.py``). The payloads it reads are the ones
``services/xfeed_svc`` publishes (``cache:xfeed:latest`` / ``cache:xfeed:accounts``);
every time is epoch milliseconds and displays in CENTRAL time, the app's convention.

⚠ A post's text, names and URLs are THIRD-PARTY text. ``split_text`` hands the page
segments to draw through labels and links (which escape); a link is only ever a
``safe_href`` http(s) address. Never give any of it to ``ui.html``.

Behaviour mirrors the Node page's ``public/app.js`` (headline, indicators, gap
text, day dividers, ``$TICKER`` search, rich text).
"""
import datetime as dt
import re as _re
from zoneinfo import ZoneInfo

from pages.news_view import safe_href

_CT = ZoneInfo("America/Chicago")
VIEW_LATEST = "xfeed:latest"
VIEW_ACCOUNTS = "xfeed:accounts"
TEXT_MAX = 240
_TYPE_WORD = {"post": "POST", "repost": "RT", "reply": "REPLY"}
# app.js richText, verbatim: a URL, an @mention, a $cashtag.
_RICH = _re.compile(r"(https?://[^\s]+)|(@[A-Za-z0-9_]{1,15})|(\$[A-Za-z][A-Za-z0-9.]{0,9})\b")
_DOLLAR = _re.compile(r"^\$[A-Za-z]")


def answer_view(request_id) -> str:
    return f"xfeed:answer:{request_id}"


def _int(v, default=0) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _ct(ms):
    try:
        return dt.datetime.fromtimestamp(_int(ms) / 1000, tz=_CT)
    except (OverflowError, OSError, ValueError):
        return dt.datetime.fromtimestamp(0, tz=_CT)


def _hhmm(ms) -> str:
    return _ct(ms).strftime("%H:%M")


def _day_label(ms) -> str:
    d = _ct(ms)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _headline(row) -> str:
    text = row.get("text")
    text = text if isinstance(text, str) else ""
    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    first = " ".join(first.split())
    if len(first) > TEXT_MAX:
        first = first[:TEXT_MAX] + "…"
    if first:
        return first
    if _list(row.get("media")):
        return "(media)"
    return "(quote)" if row.get("quote") else "(no text)"


def row_line(row: dict, *, now_ms: int, new_highlight_s: int) -> dict:
    media = [m for m in _list(row.get("media")) if isinstance(m, dict)]
    photos = sum(1 for m in media if m.get("type") == "photo")
    tags = [t for t in _list(row.get("cashtags")) if isinstance(t, str)]
    first_seen = _int(row.get("first_seen_at"))
    return {
        "time": _hhmm(row.get("sort_at")),
        "type_word": _TYPE_WORD.get(row.get("type"), "POST"),
        "text": _headline(row),
        "photos": photos,
        "videos": len(media) - photos,
        "has_quote": bool(row.get("quote")),
        "chips": tags[:2],
        "more": max(len(tags) - 2, 0),
        "handle": f"@{row.get('account_handle') or ''}",
        "is_new": row.get("origin") == "live" and now_ms - first_seen < new_highlight_s * 1000,
    }


def with_dividers(rows: list) -> list:
    out, prev = [], None
    for r in _list(rows):
        if not isinstance(r, dict):
            continue
        day = _ct(r.get("sort_at")).date()
        if day != prev:
            out.append(("day", _day_label(r.get("sort_at"))))
            prev = day
        out.append(("gap" if r.get("kind") == "gap" else "row", r))
    return out


def gap_text(gap: dict) -> str:
    return (f"Some posts from @{gap.get('account_handle') or ''} between "
            f"{_hhmm(gap.get('from_at'))} and {_hhmm(gap.get('to_at'))} may be missing")


def split_text(text: str) -> list:
    """Segments ``{"kind", "text", "href"}``; adjacent plain text is one segment."""
    if not isinstance(text, str) or not text:
        return []
    out: list = []

    def add(kind, shown, href=None):
        if kind == "text" and out and out[-1]["kind"] == "text":
            out[-1]["text"] += shown
        else:
            out.append({"kind": kind, "text": shown, "href": href})

    last = 0
    for m in _RICH.finditer(text):
        if m.start() > last:
            add("text", text[last:m.start()])
        tok = m.group(0)
        if m.group(1):
            href = safe_href(tok)
            if href:
                add("url", _re.sub(r"^https?://(www\.)?", "", tok), href)
            else:
                add("text", tok)
        elif m.group(2):
            add("mention", tok, f"https://x.com/{tok[1:]}")
        else:
            add("cashtag", tok)
        last = m.end()
    if last < len(text):
        add("text", text[last:])
    return out


def symbol_href(tag) -> str:
    return f"/symbol?symbol={str(tag).strip().lstrip('$').upper()}"


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _ticker_of(state):
    q = _s(state.get("q"))
    if _DOLLAR.match(q):
        return q[1:].upper()
    t = _s(state.get("ticker")).lstrip("$").upper()
    return t or None


def is_filtered(state: dict) -> bool:
    """A filter that can make the list empty (the group tab does not count, as in app.js)."""
    return any(_s(state.get(k)) for k in ("account", "type", "q", "ticker"))


def query_args(state: dict, *, before_row: "dict | None" = None, limit: int) -> dict:
    """The ``query`` command's args for the page state (``group``: None=All, ""=Ungrouped)."""
    args: dict = {}
    group = state.get("group")
    if group == "":
        args["ungrouped"] = True
    elif isinstance(group, str):
        args["group"] = group
    for k in ("account", "type"):
        if _s(state.get(k)):
            args[k] = _s(state.get(k))
    ticker = _ticker_of(state)
    if ticker:
        args["ticker"] = ticker
    elif _s(state.get("q")):
        args["q"] = _s(state.get("q"))
    if before_row:
        args["before"] = before_row.get("sort_at")
        args["before_key"] = f"{before_row.get('status_id')}:{before_row.get('account_handle')}"
    args["limit"] = limit
    return args


def _accounts(view) -> list:
    return [a for a in _list(view.get("accounts") if isinstance(view, dict) else None) if isinstance(a, dict)]


def _counts(view) -> dict:
    c = view.get("counts") if isinstance(view, dict) else None
    return c if isinstance(c, dict) else {}


def _group(a) -> str:
    g = a.get("group_name")
    return g if isinstance(g, str) and g else ""


def _names(accounts) -> list:
    seen: list = []
    for a in accounts:
        g = _group(a)
        if g and g not in seen:
            seen.append(g)
    return seen


def group_tabs(accounts_view: dict) -> list:
    accounts, counts = _accounts(accounts_view), _counts(accounts_view)
    names = _names(accounts)
    if not names:
        return []

    def n(sel):
        return sum(_int(counts.get(a.get("handle"))) for a in accounts if sel(a))

    tabs = [{"key": None, "label": "All", "count": n(lambda a: True)}]
    tabs += [{"key": g, "label": g, "count": n(lambda a, g=g: _group(a) == g)} for g in names]
    if any(not _group(a) for a in accounts):
        tabs.append({"key": "", "label": "Ungrouped", "count": n(lambda a: not _group(a))})
    return tabs


def accounts_by_group(accounts_view: dict) -> list:
    """``[(group, accounts)]``; no groups at all is one flat section titled ``""``."""
    accounts = _accounts(accounts_view)
    if not accounts:
        return []
    names = _names(accounts)
    if not names:
        return [("", accounts)]
    out = [(g, [a for a in accounts if _group(a) == g]) for g in names]
    loose = [a for a in accounts if not _group(a)]
    if loose:
        out.append(("Ungrouped", loose))
    return out


def _ago(then_ms, now_ms) -> str:
    if not then_ms:
        return "never"
    s = max(0, round((now_ms - then_ms) / 1000))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def checked_text(account: dict, now_ms: int) -> tuple:
    err = account.get("last_error")
    if isinstance(err, str) and err:
        return err, "error"
    return f"checked {_ago(_int(account.get('last_checked_at')), now_ms)}", "ok"


def status_text(accounts_view: dict, *, stale: bool) -> tuple:
    if stale:
        return "X feed service not answering", "error"
    secs = _int(accounts_view.get("interval_s") if isinstance(accounts_view, dict) else None)
    return f"Live · checking every {secs}s", "ok"
