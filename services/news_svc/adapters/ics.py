"""iCalendar (RFC 5545) release schedules -> events. Pure over the fetched bytes.

Two agencies publish their release calendars as ICS, and they disagree on the
time basis:

* **BLS** - ``DTSTART;TZID=US-Eastern:20261014T083000`` (Eastern wall clock;
  ``US-Eastern`` is BLS's own VTIMEZONE name, not an IANA zone).
* **BEA** - ``DTSTART:20261029T123000Z`` / ``DTSTART;VALUE=DATE-TIME:...Z`` (UTC).

Every timed event comes out as an AWARE UTC instant (``at``, ISO) plus its
Eastern calendar ``date``; a ``VALUE=DATE`` event has ``at = None``.

Order matters: lines are UNFOLDED before any escape is undone, because the real
BEA file folds between the backslash and the comma of ``\\,``. ``parse`` never
raises - a malformed event is skipped, a malformed body yields ``[]``.

Lines split on CRLF, LF or a bare CR ONLY - never ``str.splitlines()``, which also
breaks on U+2028, U+0085, form feeds and friends and would let text inside a
SUMMARY inject a property line. A property's value starts at the first colon OUTSIDE double
quotes (``DESCRIPTION;ALTREP="cid:x":text``). Only the VEVENT's OWN properties are
read: anything inside a nested block (a VALARM) is ignored.

A VEVENT cannot nest, so a broken event never costs the events after it: a
``BEGIN:VEVENT`` while one is open drops the open one and starts afresh (even
from inside an unclosed VALARM), ``END:VEVENT`` closes the current event whatever
nested block is still open, and a ``BEGIN:``/``END:VCALENDAR`` inside an event
drops it. Each drop is logged at DEBUG.

Recurrence is NOT expanded: ``RRULE`` / ``RDATE`` / ``EXDATE`` are ignored, so a
recurring event yields its first occurrence (its DTSTART) only. The agencies we
read publish one VEVENT per release.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")
_UTC = dt.timezone.utc
# TZIDs seen in (or plausible for) the government calendars, mapped to IANA.
_TZ_ALIASES = {
    "us-eastern": "America/New_York",
    "us/eastern": "America/New_York",
    "eastern standard time": "America/New_York",
    "america/new_york": "America/New_York",
    "utc": "UTC",
    "gmt": "UTC",
}
_FOLD = re.compile(r"(?:\r\n|\r|\n)[ \t]")
_LINES = re.compile(r"\r\n|\r|\n")
_ESCAPE = re.compile(r"\\([\\,;nN])")
_DATETIME = re.compile(r"^(\d{8})T(\d{6})(Z?)$")
_DATE = re.compile(r"^(\d{8})$")


def _unescape(text: str) -> str:
    return _ESCAPE.sub(lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _first_unquoted_colon(line: str) -> int:
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            return i
    return -1


def _split_unquoted(text: str, sep: str) -> list[str]:
    parts, buf, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _split_prop(line: str) -> tuple[str, dict, str] | None:
    """``NAME;P=V;Q=W:value`` -> (NAME, {P: V}, value). The value may itself hold ':'."""
    colon = _first_unquoted_colon(line)
    if colon <= 0:
        return None
    head, value = line[:colon], line[colon + 1:]
    parts = _split_unquoted(head, ";")
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return parts[0].strip().upper(), params, value


def _zone(tzid: str):
    name = _TZ_ALIASES.get(tzid.strip().lower(), tzid.strip())
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone name / bad key
        return None


def _when(params: dict, value: str) -> tuple[str | None, str] | None:
    """-> (UTC ISO instant or None, Eastern date ISO), or None when unusable."""
    value = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or _DATE.match(value):
            m = _DATE.match(value)
            if not m:
                return None
            d = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
            return None, d.isoformat()
        m = _DATETIME.match(value)
        if not m:
            return None
        naive = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        if m.group(3):
            aware = naive.replace(tzinfo=_UTC)
        elif "TZID" in params:
            zone = _zone(params["TZID"])
            if zone is None:
                log.debug("ics: unknown TZID %r - event skipped", params["TZID"])
                return None
            aware = naive.replace(tzinfo=zone)
        else:
            # A floating time. The agencies we read never emit one; Eastern is
            # the only sensible reading for a US release schedule.
            aware = naive.replace(tzinfo=_EASTERN)
    except (ValueError, OverflowError):
        return None
    at = aware.astimezone(_UTC)
    return at.isoformat(), aware.astimezone(_EASTERN).date().isoformat()


def _event(lines: list[str]) -> dict | None:
    summary = description = None
    start = None
    for line in lines:
        prop = _split_prop(line)
        if prop is None:
            continue
        name, params, value = prop
        if name == "SUMMARY":
            summary = _unescape(value).strip()
        elif name == "DESCRIPTION":
            description = _unescape(value).strip()
        elif name == "DTSTART":
            start = (params, value)
    if not summary or start is None:
        return None
    when = _when(*start)
    if when is None:
        return None
    at, date = when
    return {"summary": summary, "at": at, "date": date, "description": description or ""}


def parse(body) -> list[dict]:
    """ICS bytes -> ``[{"summary", "at", "date", "description"}]``, file order."""
    if not isinstance(body, (bytes, bytearray)):
        return []
    try:
        text = bytes(body).decode("utf-8-sig", errors="replace")
        text = _FOLD.sub("", text)
        out: list[dict] = []
        current: list[str] | None = None
        depth = 0           # nested blocks open inside the current VEVENT
        for line in _LINES.split(text):
            upper = line.strip().upper()
            if upper == "BEGIN:VEVENT":
                if current is not None:
                    log.debug("ics: VEVENT not closed before the next one - dropped")
                current, depth = [], 0
                continue
            if current is None:
                continue
            if upper in ("BEGIN:VCALENDAR", "END:VCALENDAR"):
                log.debug("ics: calendar boundary inside a VEVENT - dropped")
                current = None
            elif upper == "END:VEVENT":
                ev = _event(current)
                if ev is not None:
                    out.append(ev)
                current = None
            elif upper.startswith("BEGIN:"):
                depth += 1
            elif upper.startswith("END:"):
                if depth:
                    depth -= 1
            elif depth == 0:
                current.append(line)
        return out
    except Exception:  # noqa: BLE001 - a feed must never take the poll down
        log.warning("ics: unparseable body", exc_info=True)
        return []
