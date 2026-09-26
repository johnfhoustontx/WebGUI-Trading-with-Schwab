"""Google News RSS search → items. Links are Google redirects (kept as-is in v1);
the publisher comes from each entry's <source> and is stripped off the title."""
from urllib.parse import urlencode

from services.news_svc.adapters import rss

_BASE = "https://news.google.com/rss/search?"


def url(feed: dict) -> str:
    return _BASE + urlencode({"q": feed["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"})


def parse(body: bytes, feed: dict, now: str, *, universe) -> list:
    out = rss.parse(body, feed, now, universe=universe)
    for it in out:
        it["kind"] = "google_news"
        suffix = f" - {it['original_source']}" if it["original_source"] else ""
        if suffix and it["title"].endswith(suffix):
            it["title"] = it["title"][: -len(suffix)].rstrip()
    return out
