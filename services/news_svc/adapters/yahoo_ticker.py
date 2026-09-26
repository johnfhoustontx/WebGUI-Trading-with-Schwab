"""Yahoo Finance per-ticker RSS: one URL per symbol; every item is tagged with
the symbol it was fetched for (the feed IS the attribution)."""
from urllib.parse import quote

from services.news_svc.adapters import rss
from shared.symbols import clean_symbol

_PLACEHOLDER = "{symbol}"


def _yahoo_form(sym: str) -> str:
    """Yahoo spells class shares with a dash (``BRK-B``); the dotted form
    returns an EMPTY channel, silently. Percent-encoded for the query."""
    return quote(sym.replace(".", "-"), safe="")


def urls(feed: dict, universe) -> list:
    """``[(symbol, url)]`` — index symbols ($SPX) have no Yahoo news feed.

    The pair keeps the ORIGINAL symbol (``BRK.B``) as its tag; only the URL
    uses Yahoo's spelling. A template without ``{symbol}`` yields nothing: it
    would fetch the same page once per ticker and tag each copy with a
    different symbol. A non-string ``url`` (malformed config) yields nothing."""
    template = feed.get("url")
    if not isinstance(template, str) or _PLACEHOLDER not in template:
        return []
    return [(sym, template.replace(_PLACEHOLDER, _yahoo_form(sym)))
            for sym in universe if not sym.startswith("$")]


def parse(body: bytes, feed: dict, now: str, *, universe, symbol) -> list:
    """Items tagged with the fetched symbol first. ``symbol`` goes through
    ``clean_symbol``; one that does not clean is NOT forced onto the items."""
    sym = clean_symbol(symbol) if isinstance(symbol, str) else None
    out = rss.parse(body, feed, now, universe=universe)
    for it in out:
        it["kind"] = "yahoo_ticker"
        if sym:
            it["tickers"] = [sym] + [t for t in it["tickers"] if t != sym]
    return out
