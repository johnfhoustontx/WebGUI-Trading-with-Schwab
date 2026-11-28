"""The Macro Board's new highs / lows counts, from ``config/new_highs.toml``.

Read by ``services/market_svc/new_highs.py`` on every refresh, so a saved
override applies at the next refresh without a restart. Tier 1 does not read it:
what the page needs (the price floor, the exchanges, the public switch) travels
in the published ``cache:market:new_highs`` view.

Missing file / bad TOML / missing key / unusable value -> the built-in default
for that key, never a raise. ``load()`` returns a fresh, cleaned mapping.
"""
from repo_paths import NEW_HIGHS_TOML
from shared.config_toml import toml_loader
from shared.numeric import finite
from shared.sectors import SECTORS

#: The 11 GICS sectors, spelled as the paper book's sector map spells them. The
#: board groups each count's stocks by these, not by TradingView's own 21
#: "sectors" (Finance, Technology Services, ...), which are its industry groups.
GICS_SECTORS = frozenset(SECTORS - {"INDEX"})

_EXCHANGES = ("NYSE", "NASDAQ", "AMEX")

DEFAULTS = {
    "enabled": True,
    # TradingView's own screener address. Unofficial: no published contract.
    "url": "https://scanner.tradingview.com/america/scan",
    # The exchanges counted (AMEX is NYSE American).
    "exchanges": list(_EXCHANGES),
    # Only stocks at or above this price are counted; 0 counts every price.
    "min_price": 5.0,
    # Seconds between refreshes in the regular session.
    "rth_refresh_sec": 60,
    # Minutes after the close for the one refresh that settles the day.
    "after_close_min": 10,
    # Seconds one request may wait for each read.
    "timeout_sec": 10,
    # Seconds before a failed refresh is tried again.
    "retry_sec": 120,
    # Stocks listed behind each count (the board's hover and click); 0 lists
    # none. 5000 is past the whole universe, so nothing is cut.
    "list_max": 5000,
    # TradingView industry -> GICS sector. Shipped in the file alone (127 rows);
    # an industry it lacks is listed as Unclassified.
    "gics": {},
    "public": {"show": True},
}

load_raw, reset_cache = toml_loader(NEW_HIGHS_TOML, DEFAULTS, label="new_highs.toml")


def _whole(value, default, low, high):
    f = finite(value)
    return int(f) if f is not None and low <= f <= high else default


def _price(value, default, low, high):
    f = finite(value)
    return float(f) if f is not None and low <= f <= high else default


def _exchanges(value) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str) and item.strip() and item.strip().upper() not in out:
            out.append(item.strip().upper())
    return out or list(_EXCHANGES)


def _url(value) -> str:
    return value.strip() if isinstance(value, str) and value.strip().startswith("https://") \
        else DEFAULTS["url"]


def _gics(value) -> dict[str, str]:
    """The industry map, keeping only rows whose sector is one of the 11."""
    out: dict[str, str] = {}
    for industry, sector in (value.items() if isinstance(value, dict) else ()):
        if isinstance(industry, str) and industry.strip() and isinstance(sector, str) \
                and sector.strip() in GICS_SECTORS:
            out[industry.strip()] = sector.strip()
    return out


def load() -> dict:
    """The cleaned settings, as a fresh mapping the caller may keep."""
    raw = load_raw()
    public = raw.get("public")
    show = public.get("show") if isinstance(public, dict) else None
    return {
        "enabled": raw.get("enabled") is not False,
        "url": _url(raw.get("url")),
        "exchanges": _exchanges(raw.get("exchanges")),
        "min_price": _price(raw.get("min_price"), DEFAULTS["min_price"], 0, 1000),
        "rth_refresh_sec": _whole(raw.get("rth_refresh_sec"),
                                  DEFAULTS["rth_refresh_sec"], 15, 3600),
        "after_close_min": _whole(raw.get("after_close_min"),
                                  DEFAULTS["after_close_min"], 0, 120),
        "timeout_sec": _whole(raw.get("timeout_sec"), DEFAULTS["timeout_sec"], 2, 60),
        "retry_sec": _whole(raw.get("retry_sec"), DEFAULTS["retry_sec"], 15, 3600),
        "list_max": _whole(raw.get("list_max"), DEFAULTS["list_max"], 0, 5000),
        "gics": _gics(raw.get("gics")),
        "public_show": show is not False,
    }
