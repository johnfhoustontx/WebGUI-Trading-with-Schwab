"""Nasdaq's IPO calendar: ``api.nasdaq.com/api/ipo/calendar?date=YYYY-MM``.

Pure, over bytes - no network. Undocumented and unauthenticated; it refuses
the repo User-Agent and answers a browser one (the fetch is the caller's job).
The body carries four tables - priced, upcoming, filed, withdrawn - and only
the first two are calendar rows: a filing is not a date, and a withdrawal is
the absence of one.

Every value arrives as a STRING (``"$2,530,000,000"``, ``"40.00-44.00"``,
``"9/30/2026"``) or ``null``, so each is parsed explicitly and a value that
does not parse becomes ``None`` rather than a guess.

⚠ ``parse`` raises ``ValueError`` in exactly one case: the body is not a
calendar - not JSON, no ``data`` table, or a failure ``status.rCode`` (which
Nasdaq sends under HTTP 200). That is a failed source, and the caller must be
able to tell it from an empty month (``rCode`` 200, every ``rows`` ``null``),
which returns ``[]``. A bad ROW is skipped, never raised.
"""
import html
import json
import re
from datetime import date

from shared.symbols import clean_symbol

URL = "https://api.nasdaq.com/api/ipo/calendar?date={year:04d}-{month:02d}"

_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", re.ASCII)
# "$240,000,000" / "$828,000,000.00" / "2530000000" - properly grouped or not
# grouped at all. Anything else (an exponent, a sign, "n/a", "nan") is None.
# re.ASCII: without it \d matches full-width and Arabic-Indic digits too, and
# int() would happily convert them.
_MONEY = re.compile(r"^\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$", re.ASCII)

# (status, path to the rows list, the field holding the row's date)
_TABLES = (
    ("priced", ("priced", "rows"), "pricedDate"),
    ("upcoming", ("upcoming", "upcomingTable", "rows"), "expectedPriceDate"),
)


def url(year, month):
    """The calendar URL for one month."""
    return URL.format(year=int(year), month=int(month))


def _money(raw):
    """A whole-dollar amount from Nasdaq's string, or ``None``."""
    if not isinstance(raw, str):
        return None
    m = _MONEY.fullmatch(raw.strip())
    if not m:
        return None
    value = int(m.group(1).replace(",", ""))
    return value if value > 0 else None


def _iso_date(raw):
    if not isinstance(raw, str):
        return None
    m = _DATE.fullmatch(raw)
    if not m:
        return None
    month, day, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _text(raw):
    if not isinstance(raw, str):
        return None
    cleaned = html.unescape(raw).strip()
    return cleaned or None


def _symbol(raw):
    return clean_symbol(raw) if isinstance(raw, str) else None


def _rows_at(data, path):
    node = data
    for key in path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def _row(status, raw, date_field, index):
    if not isinstance(raw, dict):
        return None
    when = _iso_date(raw.get(date_field))
    if when is None:
        return None
    symbol = _symbol(raw.get("proposedTickerSymbol"))
    company = _text(raw.get("companyName"))
    deal = _text(raw.get("dealID"))
    # The id is per DEAL, never per status: a deal listed as upcoming on one
    # month's page and priced on the next is one row (see ``dedupe``). Without
    # a dealID, symbol + company + date + the row's index in its table keep two
    # anonymous rows apart.
    ident = deal or f"{symbol or ''}|{company or ''}|{when}|{index}"
    return {
        "symbol": symbol,
        "company": company,
        "status": status,
        "date": when,
        "price": _text(raw.get("proposedSharePrice")),
        "offer_usd": _money(raw.get("dollarValueOfSharesOffered")),
        "exchange": _text(raw.get("proposedExchange")),
        "id": f"nasdaq_ipo:{ident}",
    }


def parse(body):
    """Priced and upcoming IPO rows from one month's body.

    Raises ``ValueError`` only when the body is a failure (see the module
    docstring); anything malformed inside a good body is skipped.
    """
    try:
        doc = json.loads(body.decode("utf-8-sig") if isinstance(body, bytes) else body)
    except (UnicodeDecodeError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError(f"nasdaq_ipo: body is not JSON ({type(exc).__name__})") from None
    if not isinstance(doc, dict):
        raise ValueError("nasdaq_ipo: body is not a JSON object")
    status = doc.get("status")
    code = status.get("rCode") if isinstance(status, dict) else None
    if code != 200:
        raise ValueError(f"nasdaq_ipo: status rCode {code!r}")
    data = doc.get("data")
    if not isinstance(data, dict):
        raise ValueError("nasdaq_ipo: no data table")
    out = []
    for status_name, path, date_field in _TABLES:
        for index, raw in enumerate(_rows_at(data, path)):
            row = _row(status_name, raw, date_field, index)
            if row is not None:
                out.append(row)
    return out


# Most-settled first: a priced deal supersedes its upcoming listing, and so on.
_STATUS_RANK = {"priced": 0, "upcoming": 1, "filed": 2, "withdrawn": 3}


def dedupe(rows):
    """One row per ``id``, keeping the most settled status.

    Preference: priced > upcoming > filed > withdrawn (an unknown status ranks
    last); on a tie the first row seen wins. Order follows each id's first
    appearance, so merging two months' pages is stable.
    """
    best = {}
    order = []
    for row in rows:
        key = row.get("id")
        rank = _STATUS_RANK.get(row.get("status"), len(_STATUS_RANK))
        if key not in best:
            order.append(key)
            best[key] = (rank, row)
        elif rank < best[key][0]:
            best[key] = (rank, row)
    return [best[k][1] for k in order]
