"""Plain RSS / Atom -> items. Pure over the fetched bytes."""
import calendar
import datetime as dt
import html
import re

import feedparser

from services.news_svc import items

_TAGS = re.compile(r"<[^>]+>")


def _iso(entry, now):
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return now
    return dt.datetime.fromtimestamp(calendar.timegm(st), dt.timezone.utc).isoformat()


def _teaser(entry):
    raw = entry.get("summary") or ""
    return html.unescape(_TAGS.sub("", raw)).strip()[:400]


def _link(entry):
    """The entry's link, or ``""`` when it has none usable (missing, blank,
    or not a string) - ``make_item`` refuses an item without one."""
    url = entry.get("link")
    return url.strip() if isinstance(url, str) else ""


def parse(body: bytes, feed: dict, now: str, *, universe) -> list:
    try:
        parsed = feedparser.parse(body)
    except Exception:  # noqa: BLE001 - a broken feed is an empty poll, logged by the caller
        return []
    out = []
    for entry in parsed.get("entries") or []:
        url = _link(entry)
        if not url:
            continue
        title = entry.get("title") or ""
        teaser = _teaser(entry)
        source = entry.get("source") or {}
        out.append(items.make_item(
            source=feed["name"], kind=feed["kind"], public=feed.get("public", True),
            title=title, teaser=teaser, url=url,
            published_at=_iso(entry, now), now=now,
            original_source=source.get("title", "") if isinstance(source, dict) else "",
            tickers=items.extract_tickers(f"{title} {teaser}", universe)))
    return out
