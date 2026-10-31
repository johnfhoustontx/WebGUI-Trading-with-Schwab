"""The /ES and /NQ 0-DTE chain feed's shared vocabulary.

The chains come from ThinkorSwim on the operator's PC, never from Schwab (which
serves no futures-option chains). A capture sends one snapshot per instrument per
interval; options_svc stores it and the private Dealer Positioning page draws it.
Every end imports this module, so the stream name, the cache keys, the symbols and
the session clock cannot drift between the PC, the service and the page. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md

Internal symbols are ``FOP.ES`` and ``FOP.NQ``: they pass the app's one ticker rule
(``shared.symbols``), which is NOT loosened, and cannot collide with a real ticker
(``ES`` alone is Eversource). The page shows ``/ES`` and ``/NQ`` through
``display_symbol``.

Stdlib, ``shared.futures_chain_config`` and ``shared.symbols`` only: Tier 1 imports
it, and ``shared/tests/test_futures_chain.py`` pins that set.
"""
import re
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from shared import futures_chain_config as _cfg
from shared.symbols import clean_symbol

# ── names ────────────────────────────────────────────────────────────────────

# The capture's snapshots. Its own stream, with its own low [stream_keep] entry
# (shared/service_limits.py): each command carries a document. Not cmd:options,
# whose table of commands guards the paper books.
STREAM = "cmd:futures_chain"

_CHAIN_PREFIX = "cache:options:futures_chain:"
# The feed's health: capture age, what each instrument last did, and why not.
STATUS_KEY = "cache:options:futures_chain_status"
# What the stopped-capture alert has already told the operator, per instrument.
# Its own key: STATUS_KEY is read, changed and written back by the stream's
# consumer thread, and a second writer on it would lose one of the two writes.
# Written by options_svc's watch only; no page reads it.
ALERT_STATE_KEY = "cache:options:futures_chain_alert"


def chain_key(symbol) -> str:
    """The cache key of ``symbol``'s newest chain, e.g. ``FOP.ES``."""
    return f"{_CHAIN_PREFIX}{symbol}"


def frozen_key(symbol) -> str:
    """The cache key of ``symbol``'s chain as last captured at least
    ``frozen_before_expiry_min`` before expiry, kept for the hours between
    sessions."""
    return f"{chain_key(symbol)}:frozen"


# ── the symbols ──────────────────────────────────────────────────────────────

def _feed() -> dict:
    """``{symbol: instrument}`` for every configured instrument whose symbol the
    ticker rule accepts unchanged. The symbol becomes part of a Redis key name, so
    one the rule would refuse never joins the feed."""
    return {inst["symbol"]: root for root, inst in _cfg.instruments().items()
            if clean_symbol(inst["symbol"]) == inst["symbol"]}


def _normal(symbol) -> str:
    return symbol.strip().upper() if isinstance(symbol, str) else ""


def feed_symbols() -> list[str]:
    """The internal symbols, in the order the config lists them."""
    return list(_feed())


def is_feed_symbol(symbol) -> bool:
    """True for ``FOP.ES`` and ``FOP.NQ`` only; ``ES`` is Eversource."""
    return _normal(symbol) in _feed()


def instrument_for(symbol):
    """``"ES"`` for ``FOP.ES``, else ``None``."""
    return _feed().get(_normal(symbol))


def display_symbol(symbol):
    """``/ES`` for ``FOP.ES``; anything else is returned unchanged."""
    root = instrument_for(symbol)
    return _cfg.instruments()[root]["label"] if root else symbol


# ── a contract's own description ─────────────────────────────────────────────

class ContractTerms(NamedTuple):
    underlying: str      # "/ESZ26:XCME": the future the option is written on
    multiplier: int      # dollars a point: 50 for /ES, 20 for /NQ
    expiry: date         # the date the description states, not EXPIRATION_DAY
    am_settled: bool     # settles on the morning: gone before the session
    series: str          # "E1AX26": root, month code, two-digit year


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
# /ESZ26:XCME 1/50 2 NOV 26 (Monday) (Wk1) /E1AX26:XCME 7600 CALL
_DESCRIPTION = re.compile(
    r"\s*(/[A-Z0-9]+:[A-Z]+)\s+1/(\d+)\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{2})\b"
    r"(.*?)\s+/([A-Z0-9]+):[A-Z]+\s+\d+(?:\.\d+)?\s+(?:CALL|PUT)\s*",
    re.IGNORECASE)


def parse_description(text):
    """The terms a ThinkorSwim ``DESCRIPTION`` states, or ``None``.

    Reading them off the contract means no hand-kept expiry table: the underlying
    future, the multiplier, the date and morning settlement are all in the text.
    ``None`` for anything that is not a full description (``N/A`` for a contract
    that is not listed, a date that is not a date, a missing term)."""
    if not isinstance(text, str):
        return None
    found = _DESCRIPTION.fullmatch(text)
    if found is None:
        return None
    underlying, multiplier, day, month, year, between, series = found.groups()
    month = month.upper()
    if month not in _MONTHS:
        return None
    try:
        expiry = date(2000 + int(year), _MONTHS.index(month) + 1, int(day))
    except ValueError:
        return None
    return ContractTerms(underlying.upper(), int(multiplier), expiry,
                         "AM SETTLED" in between.upper(), series.upper())


def date_text(expiry: date) -> str:
    """The date as a description prints it: ``2 NOV 26``."""
    return f"{expiry.day} {_MONTHS[expiry.month - 1]} {expiry.year % 100:02d}"


# ── which series might hold an expiry ────────────────────────────────────────

_MONTH_CODES = "FGHJKMNQUVXZ"
_QUARTER_MONTHS = (3, 6, 9, 12)
_WEEKS = range(1, 6)


def _suffix(year: int, month: int) -> str:
    return f"{_MONTH_CODES[month - 1]}{year % 100:02d}"


def _next_quarter(expiry: date) -> tuple[int, int]:
    """The quarterly month strictly after the expiry's own, as (year, month)."""
    for month in _QUARTER_MONTHS:
        if month > expiry.month:
            return expiry.year, month
    return expiry.year + 1, _QUARTER_MONTHS[0]


def candidate_series(instrument, expiry: date) -> list[str]:
    """Series names that MIGHT hold ``expiry``, for ``instrument`` (``"ES"``).

    Monday to Thursday: weeks 1 to 5 of that weekday's root. Friday: weeks 1 to 5
    of the Friday root. Always the month-end and the quarterly names, because a
    fifth Monday can also be month-end and trade under the month-end root (30 NOV
    26 is ``EWX26``, not ``E5AX26``). The quarterly names take the expiry's own
    month and the next quarterly month. A name that does not exist is harmless: the
    capture keeps only series whose description states this date."""
    spec = _cfg.instruments().get(instrument) if isinstance(instrument, str) else None
    if spec is None:
        return []
    suffix = _suffix(expiry.year, expiry.month)
    names: list[str] = []
    weekday = expiry.weekday()
    pattern = (spec["weekday_roots"][weekday] if weekday < 4
               else spec["friday_root"] if weekday == 4 else None)
    if pattern is not None:
        names += [pattern.replace("{w}", str(week)) + suffix for week in _WEEKS]
    names.append(spec["eom_root"] + suffix)
    quarter_year, quarter_month = _next_quarter(expiry)
    names += [spec["quarterly_root"] + suffix,
              spec["quarterly_root"] + _suffix(quarter_year, quarter_month)]
    return list(dict.fromkeys(names))


# ── the futures session ──────────────────────────────────────────────────────

_CENTRAL = ZoneInfo("America/Chicago")


def _central(now: datetime) -> datetime:
    """``now`` in Central time. A naive datetime means Central (the project's
    convention)."""
    if now.tzinfo is None:
        return now.replace(tzinfo=_CENTRAL)
    return now.astimezone(_CENTRAL)


def _at(day: date, clock: str) -> datetime:
    hour, minute = clock.split(":")
    return datetime.combine(day, time(int(hour), int(minute)), tzinfo=_CENTRAL)


def _weekday_on_or_after(day: date) -> date:
    """Weekdays are the only expiries. A CME holiday differs from the NYSE's and
    is not modelled: a series that does not resolve is reported instead."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def session_expiry(now: datetime) -> date:
    """The expiry date of the session ``now`` belongs to, or comes next.

    One session is one expiry: trading opens for it the evening before and it ends
    at ``close_ct`` on the day. So a Sunday-evening capture belongs to Monday, and
    the hours after the close (the daily gap) name the next day's session. A
    weekend rolls to Monday."""
    local = _central(now)
    cutoff = _at(local.date(), _cfg.session()["close_ct"])
    day = local.date() if local < cutoff else local.date() + timedelta(days=1)
    return _weekday_on_or_after(day)


def session_bounds(expiry: date) -> tuple[int, int]:
    """``(opens, ends)`` of ``expiry``'s session as epoch seconds. It opens at
    ``open_ct`` on the calendar day before the expiry (so Monday's opens on the
    Sunday) and ends at ``close_ct`` on it: two calendar dates."""
    clocks = _cfg.session()
    return (int(_at(expiry - timedelta(days=1), clocks["open_ct"]).timestamp()),
            int(_at(expiry, clocks["close_ct"]).timestamp()))


def in_session(now: datetime) -> bool:
    """Whether trading is open for the session ``now`` belongs to. False in the
    daily gap, at weekends and before the Sunday open."""
    opens, ends = session_bounds(session_expiry(now))
    return opens <= _central(now).timestamp() < ends


# ── rows on a one-minute page ────────────────────────────────────────────────

def fill_rows(rows: list, interval_sec: int, step_sec: int) -> list:
    """``rows`` (each led by its epoch second) with every row repeated every
    ``step_sec`` until the next one, but never for longer than ``interval_sec``.

    The store keeps one truthful row per capture; the page draws a column a
    minute. A longer outage stays a gap rather than being drawn as data, and the
    newest row is not drawn into the future. The rest of each row is kept as it
    was, and ``rows`` is not changed."""
    ordered = sorted(rows, key=lambda row: row[0])
    if step_sec <= 0 or interval_sec <= 0:
        return ordered
    out: list = []
    for index, row in enumerate(ordered):
        out.append(row)
        if index + 1 == len(ordered):
            break
        stop = min(ordered[index + 1][0], row[0] + interval_sec)
        copies = 1
        while row[0] + copies * step_sec < stop:
            repeat = list(row)
            repeat[0] = row[0] + copies * step_sec
            out.append(tuple(repeat) if isinstance(row, tuple) else repeat)
            copies += 1
    return out


# ── the plan the capture works from ──────────────────────────────────────────

# The capture machine's own tunables. The script holds a stated default for each,
# because the PC and the server do not update together.
_PC_SETTINGS = ("min_read_fraction", "fill_settle_sec", "fill_wait_max_sec",
                "probe_round_steps", "probe_reach", "recentre_share", "ssh_timeout_sec",
                "no_plan_retry_sec", "plan_retry_max_sec", "hang_limit_sec")


def build_plan(now: datetime) -> dict:
    """What the capture needs to know at ``now``, as plain JSON: the expiry it is
    capturing, how to print it, the series to try for each instrument and the
    numbers to try them with. The server builds it, so a change of config or of
    session reaches the PC without editing a file there."""
    expiry = session_expiry(now)
    opens, ends = session_bounds(expiry)
    capture = _cfg.capture()
    return {
        "expiry": expiry.isoformat(),
        "date_text": date_text(expiry),
        "in_session": in_session(now),
        "opens": opens,
        "valid_until": ends,
        "interval_sec": capture["interval_sec"],
        "min_fill_fraction": capture["min_fill_fraction"],
        "max_snapshot_kb": capture["max_snapshot_kb"],
        **{key: capture[key] for key in _PC_SETTINGS},
        "instruments": {
            root: {key: inst[key] for key in
                   ("symbol", "multiplier", "exchange", "strike_step",
                    "strike_width_pct", "parity_limit_pts")}
            | {"candidates": candidate_series(root, expiry)}
            for root, inst in _cfg.instruments().items()},
    }
