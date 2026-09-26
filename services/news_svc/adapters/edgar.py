"""EDGAR: the "current filings" Atom feed, Form 4 purchases, shelf offerings.

Pure parsers over bytes - no network, and none of them raises. The FETCH plan
(compute.py): one Atom fetch per form type per poll; for each NEW Form 4
accession, one fetch of the filing folder's ``index.json`` to find the XML,
one fetch of the XML. <= 10 req/s, always with the configured User-Agent - the
SEC blocks anything else.
"""
import math
import re
import xml.etree.ElementTree as ET

from services.news_svc import items

ATOM = "{http://www.w3.org/2005/Atom}"
CURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
           "&type={form}&owner=include&count=100&output=atom")
TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"

_TITLE = re.compile(r"^(?P<form>[\w/-]+) - (?P<company>.+?) \((?P<cik>\d+)\) \((?P<role>[^)]+)\)")
_ACCESSION = re.compile(r"/(\d{10}-\d{2}-\d{6})-index\.htm")
# A default namespace on the root (some filers add one) hides every
# un-prefixed path from ElementTree's lookups; the schema has no other.
_XMLNS = re.compile(rb'\sxmlns="[^"]*"')

FORM_LABEL = {"S-1": "IPO / new registration", "S-3": "shelf registration",
              "424B5": "prospectus supplement (offering)"}


def _parse_xml(body):
    """The document root, or ``None`` for anything that is not XML."""
    if not isinstance(body, (bytes, bytearray)) or not body.strip():
        return None
    try:
        return ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return None


def parse_current(body: bytes) -> list:
    """Entries of a current-filings Atom, one per accession (a Form 4 lists the
    filing twice, as Reporting and as Issuer - the Issuer row wins).

    ``cik`` carries no leading zeros, so it keys ``cik_map`` directly."""
    root = _parse_xml(body)
    if root is None:
        return []
    by_acc = {}
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        link = entry.find(f"{ATOM}link")
        href = (link.get("href") or "").strip() if link is not None else ""
        m, a = _TITLE.match(title), _ACCESSION.search(href)
        if not m or not a:
            continue
        acc = a.group(1)
        rec = {"form": m["form"], "company": m["company"], "cik": str(int(m["cik"])),
               "role": m["role"], "accession": acc, "index_url": href,
               "updated": (entry.findtext(f"{ATOM}updated") or "").strip()}
        if acc not in by_acc or m["role"] == "Issuer":
            by_acc[acc] = rec
    return list(by_acc.values())


def xml_url(index_url: str, index_json):
    """The primary Form 4 XML inside a filing folder, from its ``index.json``."""
    if not isinstance(index_json, dict) or not index_url:
        return None
    folder = index_url.rsplit("/", 1)[0]
    for f in (index_json.get("directory") or {}).get("item") or []:
        name = f.get("name", "") if isinstance(f, dict) else ""
        if name.endswith(".xml") and "FilingSummary" not in name:
            return f"{folder}/{name}"
    return None


def _num(el, path):
    """A finite number at ``path``, or ``None`` - never a NaN or infinity,
    which would carry straight through a sum into the headline."""
    try:
        v = float((el.findtext(path) or "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _flag(rel, tag):
    return (rel.findtext(tag) or "").strip().lower() in ("1", "true")


def parse_form4(body: bytes):
    """A Form 4's open-market purchases (code P), or None when it has none.

    A purchase whose shares or price is missing, non-numeric or non-finite is
    SKIPPED rather than counted as zero-or-NaN: it cannot be valued."""
    if isinstance(body, (bytes, bytearray)):
        body = _XMLNS.sub(b"", bytes(body), count=1)
    root = _parse_xml(body)
    if root is None:
        return None
    groups = []
    for tx in root.iter("nonDerivativeTransaction"):
        if (tx.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        shares = _num(tx, "transactionAmounts/transactionShares/value")
        price = _num(tx, "transactionAmounts/transactionPricePerShare/value")
        if shares is None or price is None:
            continue
        groups.append({"code": "P",
                       "security": (tx.findtext("securityTitle/value") or "").strip(),
                       "shares": shares, "price": price, "value": shares * price,
                       "date": (tx.findtext("transactionDate/value") or "").strip()})
    if not groups:
        return None
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    labels = []
    if rel is not None:
        if _flag(rel, "isDirector"):
            labels.append("Director")
        if _flag(rel, "isOfficer"):
            labels.append((rel.findtext("officerTitle") or "").strip() or "Officer")
        if _flag(rel, "isTenPercentOwner"):
            labels.append("10% Owner")
    return {"symbol": (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper(),
            "company": (root.findtext("issuer/issuerName") or "").strip(),
            "insider": (root.findtext("reportingOwner/reportingOwnerId/rptOwnerName")
                        or "").strip(),
            "relationship": ", ".join(labels) or "Insider",
            "groups": groups, "total_value": sum(g["value"] for g in groups),
            "transaction_date": groups[0]["date"]}


def _money(v):
    """``$136.4M``-style; a negative keeps its sign, a non-number reads $0."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "$0"
    if not math.isfinite(v):
        return "$0"
    sign, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e9:
        return f"{sign}${a/1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.0f}K"
    if round(a) == 0:
        return "$0"
    return f"{sign}${a:.0f}"


def _value(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def form4_item(detail: dict, feed: dict, index_url: str, now: str, *, universe):
    """The item for one Form 4 purchase, or ``None`` when it has no url or is
    neither on a tracked ticker nor at least ``min_value_usd``."""
    if not str(index_url or "").strip():
        return None
    detail = detail or {}
    sym = str(detail.get("symbol") or "").strip().upper()
    total = _value(detail.get("total_value"))
    floor = _value(feed.get("min_value_usd"))
    if sym not in set(universe or ()) and total < floor:
        return None
    who = detail.get("insider") or "Insider"
    rel = detail.get("relationship") or "Insider"
    title = f"{sym or detail.get('company') or 'Unknown'} — {who} ({rel}) bought {_money(total)}"
    return items.make_item(
        source=feed.get("name", "SEC"), kind="edgar_form4", public=feed.get("public", True),
        title=title, teaser="Form 4 insider purchase filed with the SEC",
        url=index_url, published_at=now, now=now, tickers=[sym] if sym else [],
        topics=["SEC Filing", "Insider Transaction"], detail=detail)


def cik_map(company_tickers) -> dict:
    """``{cik (no leading zeros): TICKER}`` from the SEC's company_tickers.json."""
    out = {}
    for v in (company_tickers or {}).values() if isinstance(company_tickers, dict) else ():
        if not isinstance(v, dict) or not v.get("ticker"):
            continue
        try:
            out[str(int(v["cik_str"]))] = str(v["ticker"]).upper()
        except (KeyError, TypeError, ValueError):
            continue
    return out


def filing_item(entry: dict, feed: dict, now: str, *, cik_to_ticker):
    """The item for one registration filing, or ``None`` without a url."""
    entry = entry or {}
    url = str(entry.get("index_url") or "").strip()
    if not url:
        return None
    form = entry.get("form") or "filing"
    sym = (cik_to_ticker or {}).get(entry.get("cik") or "")
    title = (f"{entry.get('company') or 'Unknown company'} files {form} "
             f"({FORM_LABEL.get(form, 'registration')})")
    return items.make_item(
        source=feed.get("name", "SEC"), kind="edgar_filings", public=feed.get("public", True),
        title=title, teaser="", url=url,
        published_at=entry.get("updated") or now, now=now,
        tickers=[sym] if sym else [], topics=["SEC Filing", "Offering"],
        detail={"form": form, "cik": entry.get("cik") or "",
                "accession": entry.get("accession") or ""})
