"""Google News RSS search → items. Links are Google redirects (kept as-is in v1);
the publisher comes from each entry's <source> and is stripped off the title."""
import re
from urllib.parse import urlencode

from services.news_svc.adapters import rss

_BASE = "https://news.google.com/rss/search?"


def url(feed: dict):
    """The search URL, or ``None`` when the feed has no usable ``query``
    (missing, ``None``, not a string, or blank) - the poll cycle treats
    ``None`` as "skip this feed" rather than fetching ``q=None``."""
    query = feed.get("query")
    if not isinstance(query, str) or not query.strip():
        return None
    return _BASE + urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def _strip_publisher(title: str, pub: str) -> str:
    """Remove a trailing ``<ws>[-–—]<ws><publisher>``; the publisher is matched
    literally. Never strips a title down to nothing."""
    if not pub:
        return title
    stripped = re.sub(rf"\s+[-–—]\s+{re.escape(pub)}\s*$", "", title).rstrip()
    return stripped if stripped else title


def parse(body: bytes, feed: dict, now: str, *, universe) -> list:
    out = rss.parse(body, feed, now, universe=universe)
    for it in out:
        it["kind"] = "google_news"
        it["title"] = _strip_publisher(it["title"], it["original_source"] or "")
    return out
