"""The listed-stocks classification file, read for the Trade Analyzer.

``options-scanner/data/us_listed_stocks_classification.csv``
(``repo_paths.LISTED_STOCKS_CSV``) is a roster of US listed stocks a person
curated; ``tools/rank_listed_stocks.py`` ranks each one inside its industry
every Saturday. This module only LOOKS THINGS UP in it: a symbol's sector,
industry and sub-industry, and the top-ranked stocks of its industry. It never
writes the file and never calls Schwab.

``classification(symbol)`` is the whole interface. It returns ``None`` when
there is nothing to say - the symbol is not in the file (an ETF, an index), or
the file is not there - so the page shows a dash rather than a blank
classification.

**The file is gitignored.** It exists on the server and on no other checkout,
so with no ``csv_path`` given the read is skipped under pytest: a test that
reached the live file would pass in one place only.

**The rank is the tool's, not this module's.** ``Industry_Rank`` is popularity
(average dollar volume blended with market cap), and an industry the tool has
not ranked yet has NO leaders: file order is the curator's, and five rows in
that order are not a ranking.

Settings: ``[display]`` in ``config/listed_stocks.toml``, read on every call,
so a change needs no restart.
"""
import csv
import logging
import os
import pathlib
import re

from repo_paths import LISTED_STOCKS_CSV
from services import _degrade
from shared.listed_stocks_config import DEFAULTS, load_config
from shared.numeric import parsed_finite

log = logging.getLogger(__name__)

# The share-class wording the file's Name column ends with ("Apple Inc. Common
# Stock", "... Class A Ordinary Shares (Ireland)", "... Sponsored American
# Depositary Shares each representing ...", "... Class A Limited Voting
# Shares"). Everything from it onward is dropped. Fitted to the names of every
# industry's top five in the file as it stood on 2026-10-13; a name it does not
# recognise is shown whole, which is clumsy and never wrong.
_SHARE_WORDING = re.compile(
    r"\s+(?:(?:Un)?sponsored\s+|New\s+)?(?:Class\s+\S+\s+)?"
    r"(?:(?:Common|Ordinary|Capital)\s+(?:Stock|Shares?|Units?)"
    r"|(?:(?:Limited|Subordinate|Multiple)\s+)?Voting\s+Shares?"
    r"|American\s+Depositary\s+(?:Shares?|Receipts?)"
    r"|Deposit[ao]ry\s+(?:Shares?|Receipts?|Units?)"
    r"|(?:NY|New\s+York)\s+Registry\s+Shares?)\b.*$", re.IGNORECASE)
# The abbreviations only at the END of the name (or before a bracketed
# country): mid-name, "Ads" is as likely to be part of the company's name.
_ADR_TAIL = re.compile(r"\s+(?:(?:Un)?sponsored\s+)?AD[RS]s?(?:\s*\(.*)?$",
                       re.IGNORECASE)
_THE_TAIL = " (The)"

_CACHE = {"key": None, "table": None}


def reset_cache():
    """Test hook: forget the parsed file."""
    _CACHE.update(key=None, table=None)


def industry_top() -> int:
    """How many of an industry's top-ranked stocks to hand the page."""
    value = (load_config().get("display") or {}).get("industry_top")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return DEFAULTS["display"]["industry_top"]
    return value


def short_name(name) -> str:
    """The company's name without the share-class wording after it.

    A name that is nothing BUT that wording is kept whole: an empty name is
    worse than a clumsy one. The file writes a leading article last
    ("Boeing Company (The)"); it is put back in front."""
    text = str(name or "").strip()
    out = _ADR_TAIL.sub("", _SHARE_WORDING.sub("", text)).strip() or text
    if out.endswith(_THE_TAIL) and len(out) > len(_THE_TAIL):
        out = "The " + out[:-len(_THE_TAIL)].strip()
    return out


def _whole(value):
    """A rank or a count as an int above zero, else None ("not ranked")."""
    out = parsed_finite(value)
    return int(out) if out is not None and out >= 1 else None


def _market_cap(row):
    """The cap the rank was computed on, else the file's own; None for neither."""
    for column in ("Market_Cap_Used_USD", "Market_Cap_USD"):
        out = parsed_finite(row.get(column))
        if out is not None and out > 0:
            return out
    return None


def _read(path):
    """Parse the file -> ``{"rows": {symbol: row}, "ranked": {industry: [row]}}``.

    ``ranked`` holds only rows the tool has given an ``Industry_Rank``, in rank
    order. Raises what opening or parsing the file raises."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and "Symbol" not in rows[0]:
        raise ValueError(f"{path} has no Symbol column")
    by_symbol, ranked = {}, {}
    for row in rows:
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        by_symbol[symbol] = row
        industry = (row.get("Industry") or "").strip()
        rank = _whole(row.get("Industry_Rank"))
        if industry and rank is not None:
            ranked.setdefault(industry, []).append((rank, symbol, row))
    return {"rows": by_symbol,
            "ranked": {industry: [row for _rank, _sym, row in sorted(
                members, key=lambda m: (m[0], m[1]))]
                for industry, members in ranked.items()}}


def _table(path):
    """The parsed file, re-read only when it changes; None when it is not there.

    The Saturday run REPLACES the file under a running service, so the cache is
    keyed on the file's modification time."""
    try:
        key = (str(path), os.stat(path).st_mtime)
    except OSError:
        if _CACHE["key"] != (str(path), None):
            log.warning("%s is not there - the Trade Analyzer shows no "
                        "sector, industry or industry leaders", path)
            _CACHE.update(key=(str(path), None), table=None)
        return None
    if _CACHE["key"] != key:
        _CACHE.update(key=key, table=_read(path))
    return _CACHE["table"]


def _under_pytest():
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def classification(symbol, csv_path=None, *, top=None):
    """``symbol``'s sector, industry, sub-industry and industry leaders, or None.

    ``name`` is the file's company name with the share-class wording dropped
    (the page's last resort when Schwab returns none). ``leaders`` is the industry's top ``top`` stocks by ``Industry_Rank``
    (``[display] industry_top`` when not given), each ``{rank, symbol, name,
    market_cap}``. ``industry_rank`` / ``industry_count`` are the symbol's own
    place, ``None`` where the tool has not ranked it. Never raises.

    ⚠ ``csv_path`` is resolved at CALL time, and with none given the read is
    skipped under pytest (see the module doc)."""
    sym = str(symbol or "").strip().upper()
    if not sym or (csv_path is None and _under_pytest()):
        return None
    try:
        table = _table(pathlib.Path(csv_path or LISTED_STOCKS_CSV))
        row = (table or {}).get("rows", {}).get(sym)
        if row is None:
            return None
        industry = (row.get("Industry") or "").strip()
        count = industry_top() if top is None else max(1, int(top))
        leaders = [{"rank": _whole(peer.get("Industry_Rank")),
                    "symbol": (peer.get("Symbol") or "").strip().upper(),
                    "name": short_name(peer.get("Name")),
                    "market_cap": _market_cap(peer)}
                   for peer in table["ranked"].get(industry, [])[:count]]
        return {
            "name": short_name(row.get("Name")),
            "sector": (row.get("Sector") or "").strip(),
            "industry": industry,
            "sub_industry": (row.get("Sub_Industry") or "").strip(),
            "industry_rank": _whole(row.get("Industry_Rank")),
            "industry_count": _whole(row.get("Industry_Count")),
            "leaders": leaders,
        }
    except Exception:
        _degrade.degraded("trade.classification", detail=sym)
        return None
