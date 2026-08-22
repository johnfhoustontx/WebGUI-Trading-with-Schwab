"""The traded symbol universe, from ``config/symbols.toml``.

The same lists lived as **four separate literals** across all three tiers:

* ``options-scanner/gex_collector.py``   - what the 1-min GEX collector polls
* ``services/options_svc/net_premium.py`` - the Net Prem display groups + BIG10
* ``services/market_svc/symbols.py``      - the BIG10 basket again
* ``webgui/pages/options/gamma.py``       - a byte-copy of the display groups,
  because Tier 1 may not import ``services.*``

Tests pinned them equal to each other, so drift was caught - but adding one
ticker still meant editing four files, and the webgui copy carried a comment
explaining that it was a copy. A config file genuinely deduplicates this rather
than relocating it: **the Tier-1 rule bans Python imports of `services.*`, not
reading a file**, and ``theme.toml`` is the standing precedent for Tier 1 reading
config directly.

⚠ Adding to ``[collection]`` costs real Schwab API budget - see the header
comment in the TOML for the arithmetic.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
from repo_paths import SYMBOLS_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "collection": {
        "base": ["$SPX", "$VIX", "SPY", "QQQ", "$NDX"],
        "broad": ["IWM", "DIA"],
        "sectors": ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                    "XLP", "XLRE", "XLU", "XLV", "XLY"],
        "megacaps": ["NVDA", "MSFT", "GOOGL", "AMZN", "META",
                     "AAPL", "TSLA", "AVGO", "PLTR", "AMD"],
    },
    "baskets": {
        "big10": ["NVDA", "MSFT", "GOOGL", "AMZN", "META",
                  "AAPL", "TSLA", "AVGO", "PLTR", "AMD"],
    },
    "netprem_groups": [
        {"key": "indices", "label": "Indices & Broad",
         "symbols": ["$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA"]},
        {"key": "sectors", "label": "SPDR Sectors",
         "symbols": ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                     "XLP", "XLRE", "XLU", "XLV", "XLY"]},
        {"key": "megacaps", "label": "Mega-caps",
         "symbols": ["NVDA", "AVGO", "AAPL", "META", "MSFT",
                     "TSLA", "PLTR", "AMZN", "GOOGL", "AMD"]},
    ],
}

load, reset_cache = toml_loader(SYMBOLS_TOML, DEFAULTS, label="symbols.toml")


def _list(section, key):
    sec = load().get(section)
    val = sec.get(key) if isinstance(sec, dict) else None
    if not isinstance(val, list) or not all(isinstance(s, str) for s in val):
        val = DEFAULTS[section][key]
    return list(val)


def collection_base() -> list:
    """The GEX collector's poll list, in the documented order: indices first,
    then the broad ETFs, sectors and mega-caps added for the Net Prem view.

    De-duplicated while PRESERVING order - a symbol repeated across two sections
    would otherwise be fetched twice every minute.
    """
    out, seen = [], set()
    for key in ("base", "broad", "sectors", "megacaps"):
        for sym in _list("collection", key):
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


def big10() -> tuple:
    """The BIG10 basket, as a tuple (both consumers index and compare it)."""
    return tuple(_list("baskets", "big10"))


def netprem_groups() -> tuple:
    """The Net Prem display groups: ``({"key","label","symbols": tuple}, ...)``.

    Returned in the nested-tuple shape both the service and the page already use,
    so neither consumer changes shape. A malformed group is dropped rather than
    breaking the subtab.
    """
    raw = load().get("netprem_groups")
    if not isinstance(raw, list):
        raw = DEFAULTS["netprem_groups"]
    out = []
    for g in raw:
        try:
            out.append({"key": str(g["key"]), "label": str(g["label"]),
                        "symbols": tuple(str(s) for s in g["symbols"])})
        except Exception:
            continue
    if not out:
        out = [{"key": g["key"], "label": g["label"],
                "symbols": tuple(g["symbols"])} for g in DEFAULTS["netprem_groups"]]
    return tuple(out)
