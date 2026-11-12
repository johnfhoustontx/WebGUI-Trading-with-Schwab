"""The Macro Board's ETF hover card, from ``config/etf_holdings.toml``.

Each fund's top holdings, the names they print under, and a note for a fund
whose card has no stocks to show (USO holds oil futures). Read by
``services/market_svc/holdings.py`` on every refresh, so a saved override applies
at the next refresh without a restart. Tier 1 does not read it: the page draws a
card for whatever the published ``cache:market:holdings`` view carries.

⚠ THE LISTS ARE KEPT BY HAND. Schwab's API serves no fund holdings, so nothing
here is derived: a fund's top five drift as prices move and as the fund
rebalances. ``as_of`` is the date they were last checked against the issuer, and
the card prints it.

Missing file / bad TOML / missing key / unusable value -> the built-in default
for that key, never a raise. ``load()`` returns a fresh, cleaned mapping.
"""
from repo_paths import ETF_HOLDINGS_TOML
from shared.config_toml import toml_loader
from shared.numeric import finite
from shared.symbols import clean_symbol

DEFAULTS = {
    "enabled": True,
    # How many holdings a card shows (the lists may carry more).
    "top_n": 5,
    # Seconds between quote fetches for the holdings, in the regular session and
    # outside it. One proxy request each, apart from the board's 3-second poll.
    "rth_refresh_sec": 15,
    "offhours_refresh_sec": 300,
    # A holding whose last trade is older than this has no move to show.
    "stale_after_h": 96,
    "as_of": "",
    "funds": {},
    "names": {},
    "notes": {},
}

# A name or a note longer than this is cut: the card is a tooltip.
MAX_TEXT = 80

load_raw, reset_cache = toml_loader(ETF_HOLDINGS_TOML, DEFAULTS, label="etf_holdings.toml")


def _whole(value, default, low, high):
    f = finite(value)
    return int(f) if f is not None and low <= f <= high else default


def _text(value) -> str:
    return value.strip()[:MAX_TEXT] if isinstance(value, str) else ""


def _symbols(value) -> list[str]:
    """Clean tickers in order, deduplicated; anything else dropped."""
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        sym = clean_symbol(item) if isinstance(item, str) else None
        if sym and sym not in out:
            out.append(sym)
    return out


def _table(raw, name) -> dict:
    got = raw.get(name)
    return got if isinstance(got, dict) else {}


def load() -> dict:
    """The cleaned settings. A fund key that is not a clean ticker is dropped, as
    is a fund whose list has no clean ticker and no note."""
    raw = load_raw()
    funds: dict[str, list[str]] = {}
    for fund, syms in _table(raw, "funds").items():
        key = clean_symbol(fund)
        if key:
            funds[key] = _symbols(syms)
    names = {}
    for sym, name in _table(raw, "names").items():
        key, text = clean_symbol(sym), _text(name)
        if key and text:
            names[key] = text
    notes = {}
    for fund, note in _table(raw, "notes").items():
        key, text = clean_symbol(fund), _text(note)
        if key and text:
            notes[key] = text
    funds = {f: s for f, s in funds.items() if s or f in notes}
    for f in notes:
        funds.setdefault(f, [])
    return {
        "enabled": raw.get("enabled") is not False,
        "top_n": _whole(raw.get("top_n"), DEFAULTS["top_n"], 1, 10),
        "rth_refresh_sec": _whole(raw.get("rth_refresh_sec"),
                                  DEFAULTS["rth_refresh_sec"], 3, 3600),
        "offhours_refresh_sec": _whole(raw.get("offhours_refresh_sec"),
                                       DEFAULTS["offhours_refresh_sec"], 15, 86400),
        "stale_after_h": _whole(raw.get("stale_after_h"),
                                DEFAULTS["stale_after_h"], 1, 720),
        "as_of": _text(raw.get("as_of")),
        "funds": funds,
        "names": names,
        "notes": notes,
    }
