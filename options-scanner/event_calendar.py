"""Event calendar: unifies macro (curated JSON), earnings (Finviz Elite),
and dividends (Schwab quote_data) into a single source of
dated events for trade-analysis risk flagging.

Cache strategy: earnings lookups are written to a file cache with 24h TTL
so repeated analyze_trade calls don't hit the network.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

MACRO_JSON_PATH = Path(__file__).parent / "data" / "macro_events.json"
EARNINGS_CACHE_PATH = Path(__file__).parent / "data" / "earnings_cache.json"
EARNINGS_TTL_HOURS = 24

APPSETTINGS = Path(__file__).parent.parent / "appsettings.json"
FINVIZ_EXPORT_URL = "https://elite.finviz.com/export.ashx"
# Discovered via tools/finviz_column_discover.py — verify after Finviz schema updates.
FINVIZ_EARNINGS_COL_ID = 68


def _load_finviz_auth():
    """Read FinvizEliteAuth from the parent-directory appsettings.json.

    Returns None if the file is missing, malformed, or lacks the key.
    """
    try:
        with open(APPSETTINGS, encoding="utf-8") as f:
            return json.load(f).get("FinvizEliteAuth")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _fetch_earnings_finviz(sym):
    """Fetch the next-upcoming earnings date for `sym` from Finviz Elite.

    Returns None on missing token, network/HTTP failure, malformed CSV,
    "-" cell (no upcoming earnings), or unparseable date string.
    Never raises — preserves the existing fail-soft contract.
    """
    auth = _load_finviz_auth()
    if not auth:
        log.warning(
            "FinvizEliteAuth not in appsettings.json; "
            "cannot fetch earnings for %s", sym,
        )
        return None
    try:
        r = requests.get(
            FINVIZ_EXPORT_URL,
            params={
                "v": "152",
                "t": sym,
                "c": f"2,{FINVIZ_EARNINGS_COL_ID}",
                "auth": auth,
            },
            timeout=10,
        )
        if r.status_code == 401:
            log.error(
                "Finviz auth invalid (401) - "
                "rotate FinvizEliteAuth in appsettings.json"
            )
            return None
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
        if len(rows) < 2:
            return None
        cell = rows[1][1].strip() if len(rows[1]) > 1 else ""
        if not cell or cell == "-":
            return None
        # Format: "May 02 AMC" or "May 02 BMO" or just "May 02"
        parts = cell.split()
        if len(parts) < 2:
            return None
        try:
            parsed = datetime.strptime(f"{parts[0]} {parts[1]}", "%b %d").date()
        except ValueError:
            return None
        # Year inference - past month-days roll forward to next year.
        today = date.today()
        candidate = parsed.replace(year=today.year)
        if candidate < today:
            candidate = candidate.replace(year=today.year + 1)
        return candidate
    except Exception as exc:
        log.warning("Finviz earnings lookup failed for %s: %s", sym, exc)
        return None


@dataclass
class Event:
    date: date
    label: str
    category: str
    source: str

    def as_dict(self):
        return {
            "date": self.date.isoformat(),
            "label": self.label,
            "category": self.category,
            "source": self.source,
        }


def _parse_date(v):
    """Parse many date-like inputs to a `date`. Returns None on any failure."""
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


STALE_AFTER_DAYS = 75  # refresh reminder threshold (nominal 90-day file)


def warn_if_stale() -> None:
    """Print a stdout reminder if `macro_events.json` hasn't been refreshed
    in a while. Called from the dashboard launcher. Silent on success."""
    if not MACRO_JSON_PATH.exists():
        print(f"[macro calendar] WARNING: {MACRO_JSON_PATH.name} missing.")
        return
    try:
        data = json.loads(MACRO_JSON_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        print(f"[macro calendar] WARNING: {MACRO_JSON_PATH.name} unreadable.")
        return

    last_updated = _parse_date(data.get("last_updated"))
    if last_updated is None:
        return
    age_days = (date.today() - last_updated).days
    if age_days < STALE_AFTER_DAYS:
        return

    # Determine coverage end so the message points at a concrete horizon.
    events = data.get("events", [])
    last_event = None
    for e in events:
        d = _parse_date(e.get("date"))
        if d and (last_event is None or d > last_event):
            last_event = d
    tail = f", last seeded event {last_event.isoformat()}" if last_event else ""
    print(
        f"[macro calendar] REMINDER: data/macro_events.json was last updated "
        f"{age_days} days ago{tail}. Refresh from fed.gov and bls.gov so "
        "event-risk flagging stays accurate."
    )


def get_macro_events(start: date, end: date) -> list[Event]:
    """Read curated macro JSON and return events in [start, end]."""
    if not MACRO_JSON_PATH.exists():
        return []
    try:
        data = json.loads(MACRO_JSON_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("macro_events.json unreadable: %s", exc)
        return []
    out = []
    for entry in data.get("events", []):
        d = _parse_date(entry.get("date"))
        if d is None:
            continue
        if start <= d <= end:
            out.append(Event(
                date=d,
                label=entry.get("label", ""),
                category=entry.get("category", "MACRO"),
                source="macro_json",
            ))
    out.sort(key=lambda e: e.date)
    return out


def _read_earnings_cache() -> dict:
    if not EARNINGS_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(EARNINGS_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_earnings_cache(cache: dict) -> None:
    """Atomic write: tmp-file + os.replace so partial writes can't corrupt."""
    import os
    try:
        EARNINGS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = EARNINGS_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, EARNINGS_CACHE_PATH)
    except OSError as exc:
        log.warning("earnings_cache.json write failed: %s", exc)


def get_earnings_date(symbol: str) -> date | None:
    """Next earnings date for symbol. 24h file cache. None on failure."""
    sym = symbol.upper()
    cache = _read_earnings_cache()
    entry = cache.get(sym)
    if entry:
        try:
            cached_at = datetime.fromisoformat(entry["cached_at"])
            if datetime.now() - cached_at < timedelta(hours=EARNINGS_TTL_HOURS):
                if entry.get("date") is None:
                    return None
                return _parse_date(entry["date"])
        except (KeyError, ValueError):
            pass  # corrupt cache entry — refetch

    earn_date = _fetch_earnings_finviz(sym)

    cache[sym] = {
        "date": earn_date.isoformat() if earn_date else None,
        "cached_at": datetime.now().isoformat(),
    }
    _write_earnings_cache(cache)
    return earn_date


def get_events_between(symbol: str, start: date, end: date,
                        quote_data: dict | None = None,
                        include_earnings: bool = True) -> list[Event]:
    """Unified event stream for [start, end], sorted by date."""
    events = list(get_macro_events(start, end))

    if include_earnings:
        e = get_earnings_date(symbol)
        if e and start <= e <= end:
            events.append(Event(
                date=e, label=f"{symbol.upper()} earnings",
                category="EARNINGS", source="finviz",
            ))

    if quote_data:
        d = _parse_date(quote_data.get("divDate"))
        if d and start <= d <= end:
            events.append(Event(
                date=d, label=f"{symbol.upper()} ex-dividend",
                category="DIVIDEND", source="schwab",
            ))

    events.sort(key=lambda ev: ev.date)
    return events
