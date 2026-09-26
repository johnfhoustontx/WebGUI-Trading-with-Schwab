"""Yahoo Finance per-ticker RSS: one URL per symbol; every item is tagged with
the symbol it was fetched for (the feed IS the attribution)."""
from services.news_svc.adapters import rss

_PLACEHOLDER = "{symbol}"


def urls(feed: dict, universe) -> list:
    """``[(symbol, url)]`` — index symbols ($SPX) have no Yahoo news feed.

    A template without ``{symbol}`` yields nothing: it would fetch the same
    page once per ticker and tag each copy with a different symbol."""
    template = feed.get("url") or ""
    if _PLACEHOLDER not in template:
        return []
    return [(sym, template.replace(_PLACEHOLDER, sym))
            for sym in universe if not sym.startswith("$")]


def parse(body: bytes, feed: dict, now: str, *, universe, symbol) -> list:
    out = rss.parse(body, feed, now, universe=universe)
    for it in out:
        it["kind"] = "yahoo_ticker"
        it["tickers"] = [symbol] + [t for t in it["tickers"] if t != symbol]
    return out
