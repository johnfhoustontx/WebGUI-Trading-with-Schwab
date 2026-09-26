"""Plain RSS / Atom -> items. Pure over the fetched bytes."""
import calendar
import datetime as dt
import html
import io
import logging
import re
from urllib.parse import urlsplit

import feedparser

from services.news_svc import items

log = logging.getLogger(__name__)

# Only TAG-SHAPED text is stripped: '<' followed by a letter, '!', '/' or '?'
# (``<b>``, ``</i>``, ``<!-- -->``, ``<?pi?>``). A plain-text title such as
# "S&P < 5000 but > 4000" or "Yields <3%" keeps its brackets. ``[^<>]*`` rather
# than ``[^>]+``: the latter backtracks quadratically on a long run of '<'
# (~20 s at 200 KB).
_TAGS = re.compile(r"<[A-Za-z!/?][^<>]*>")
# The raw summary is capped BEFORE stripping - the teaser keeps 400 chars.
MAX_RAW_SUMMARY = 8000
MAX_TEASER = 400
# A published time further than this past ``now`` is a feed clock error (or a
# pinned-to-the-top trick) and is clamped to ``now``.
FUTURE_SKEW_SEC = 600
_SCHEMES = frozenset({"http", "https"})


def _clean(raw) -> str:
    """Strip tags, unescape entities, collapse every run of whitespace
    (``\\xa0`` and newlines included) to one space."""
    if not isinstance(raw, str):
        return ""
    return " ".join(html.unescape(_TAGS.sub("", raw[:MAX_RAW_SUMMARY])).split())


def _iso(entry, now):
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return now
    try:
        when = dt.datetime.fromtimestamp(calendar.timegm(st), dt.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return now  # a year outside 1..9999 - an unusable stamp, not a lost feed
    try:
        limit = dt.datetime.fromisoformat(now) + dt.timedelta(seconds=FUTURE_SKEW_SEC)
    except (TypeError, ValueError):
        return when.isoformat()
    return now if when > limit else when.isoformat()


def _normalise_now(now):
    """``now`` as an aware ISO string. A NAIVE ``now`` is read as UTC - left
    naive, the future-date clamp would compare aware with naive, raise, and
    every dated entry would be skipped. An unparseable ``now`` is returned
    unchanged (``_iso`` then keeps feed dates unclamped)."""
    try:
        when = dt.datetime.fromisoformat(now)
    except (TypeError, ValueError):
        return now
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.isoformat()


def _teaser(entry):
    return _clean(entry.get("summary") or "")[:MAX_TEASER]


def _link(entry):
    """The entry's link, or ``""`` when it has none usable (missing, blank,
    not a string, or not http/https) - ``make_item`` refuses an item without
    one. The url is rendered as an href on a public page, so ``javascript:``
    and ``data:`` must never get through."""
    url = entry.get("link")
    if not isinstance(url, str):
        return ""
    url = url.strip()
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return ""
    return url if scheme in _SCHEMES else ""


def _item(entry, feed, now, universe):
    url = _link(entry)
    if not url:
        return None
    title = _clean(entry.get("title") or "")
    teaser = _teaser(entry)
    source = entry.get("source") or {}
    return items.make_item(
        source=feed["name"], kind=feed["kind"], public=feed.get("public", False),
        title=title, teaser=teaser, url=url,
        published_at=_iso(entry, now), now=now,
        original_source=_clean(source.get("title", "")) if isinstance(source, dict) else "",
        tickers=items.extract_tickers(f"{title} {teaser}", universe))


def parse(body: bytes, feed: dict, now: str, *, universe) -> list:
    # feedparser reads a bare bytes/str as a URL or FILENAME; only ever hand it
    # a stream over the bytes we fetched.
    if not isinstance(body, (bytes, bytearray)):
        return []
    try:
        parsed = feedparser.parse(io.BytesIO(bytes(body)))
    except Exception:  # noqa: BLE001 - a broken feed is an empty poll, logged by the caller
        return []
    now = _normalise_now(now)
    out, skipped = [], 0
    for entry in parsed.get("entries") or []:
        try:
            it = _item(entry, feed, now, universe)
        except Exception:  # noqa: BLE001 - one bad entry must not lose the feed
            skipped += 1
            log.debug("rss entry skipped (%s)", feed.get("name"), exc_info=True)
            continue
        if it is not None:
            out.append(it)
    if skipped:
        log.warning("rss feed %s: %d entr%s skipped (parse error; detail at DEBUG)",
                    feed.get("name"), skipped, "y" if skipped == 1 else "ies")
    return out
