"""Single source of truth for the market calendar and session windows.

**Holidays are consolidated here (Phase A, done).** The eight duplicated holiday
sets across the five Tier-2 services, the webgui and ``options-scanner/scanner.py``
now read from this module. Two sites remain outside it, both deliberately:
``claude-driver/config.py`` is exempt (legacy; its consumers were removed
2026-07-08), and ``options-scanner/scanner_engine.py`` still holds a DIVERGENT
9-date 2026-only set — a tracked follow-up, since correcting it is a behavior
change rather than a refactor.

**The session vocabulary now ships here too (Phase B, tasks B1-B2).** That is:
the ``Session`` enum (``CLOSED``/``GTH``/``REGULAR``/``CURB`` -- Cboe's own
naming, NOT "premarket"), the mtime-cached ``config/sessions.toml`` loader
(mirroring ``services/options_svc/flow_alerts.load_thresholds``), the
extended-hours activation gate, ``session_at`` and its predicates, and the
named operating windows (``in_window``, ``window_bounds``,
``in_collection_window``, ``session_flip_time``).

**No consumer has been migrated yet.** The fourteen hardcoded window constants
still live in their own modules and are still what the running services use;
repointing them at the helpers below is a separate, still-pending Phase B task.
Until then this half of the module has no live callers -- it is the target, not
yet the source of truth in production.

Every extended-hours branch is INERT before ``activation_date()``
(2026-08-17): ``session_at`` reports ``CLOSED`` for both ETH windows and
``in_collection_window`` ignores ``eth_eligible``, so behavior is
byte-identical to the pre-ETH app.

Everything here is a PURE function of its arguments -- no network, no
database, no clock.

``shared/`` is a namespace package (no ``__init__.py``), so
``from shared.market_calendar import ...`` resolves once the repo root is on
``sys.path`` -- which the services and webgui already arrange. The one caller
needing a bootstrap is ``options-scanner/scanner.py`` (legacy standalone CLI);
use the three-line pattern from ``services/options_svc/commission.py``.

The holiday set is **derived algorithmically** (see ``nyse_holidays``), so
there is no yearly maintenance edit. This module absorbed the former
``sentiment-dashboard/market_calendar.py``, whose derivation it carries over
verbatim; see the note on ``prev_trading_day`` for the one semantic
difference between the two.
"""
import logging
import os
import tomllib
from datetime import date, datetime, time, timedelta
from enum import Enum
from functools import lru_cache
from zoneinfo import ZoneInfo

from repo_paths import SESSIONS_TOML

log = logging.getLogger(__name__)

_MAX_SPAN_DAYS = 10  # longest plausible market closure; bounds the search loops


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm -- Easter Sunday for ``year``."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """``n``th occurrence of ``weekday`` (Mon=0..Sun=6) in (year, month)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of ``weekday`` in (year, month)."""
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    d = next_first - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date) -> date:
    """NYSE observance rule: Sat -> prior Fri; Sun -> following Mon."""
    if d.weekday() == 5:    # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:    # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset:
    """Full-day NYSE closures for ``year``, derived from today's NYSE rules.

    Memoized -- ``is_trading_day`` runs on scheduler ticks and must stay cheap.

    **Validity: 2022 onward.** The rule set encoded here is the *current* one,
    so historical years are wrong: Juneteenth only became an NYSE holiday in
    2022 (guarded below), and earlier rule changes are not modelled at all.

    **Ad-hoc closures are NOT derivable and are NOT included** -- national days
    of mourning (e.g. Presidents Ford/Bush/Carter), 9/11, and weather closures
    such as Hurricane Sandy. If one of those matters, it must be handled by the
    caller.

    Note the year-boundary spill: when 1 Jan falls on a Saturday, the observed
    holiday is 31 Dec of the *previous* year, so ``nyse_holidays(y)`` can
    contain a date in ``y - 1``. See ``is_holiday`` for how that is handled.
    """
    holidays = {
        _observed(date(year, 1, 1)),                  # New Year's Day
        _nth_weekday(year, 1, 0, 3),                  # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),                  # Presidents Day
        _easter(year) - timedelta(days=2),            # Good Friday
        _last_weekday(year, 5, 0),                    # Memorial Day
        _observed(date(year, 6, 19)),                 # Juneteenth (from 2022)
        _observed(date(year, 7, 4)),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),                  # Labor Day
        _nth_weekday(year, 11, 3, 4),                 # Thanksgiving
        _observed(date(year, 12, 25)),                # Christmas
    }
    # Juneteenth didn't become a federal holiday until 2021 (NYSE 2022).
    if year < 2022:
        holidays.discard(_observed(date(year, 6, 19)))
    return frozenset(holidays)


# The 2026-2027 union, kept as a name because the Phase B migrations bind it as
# their local ``_HOLIDAYS`` alias. Derived, not a literal -- but the pinned test
# in shared/tests/test_market_calendar.py asserts it still equals the exact 20
# dates every live site contains, which is what keeps that migration
# behavior-preserving.
HOLIDAYS = nyse_holidays(2026) | nyse_holidays(2027)


def is_holiday(d) -> bool:
    """True if ``d`` (a ``date``) is an NYSE full-closure holiday.

    Derived per-year, so any year works -- there is no set to update.

    **Both ``nyse_holidays(d.year)`` and ``nyse_holidays(d.year + 1)`` are
    consulted**, which is what catches the year-boundary spill: when 1 Jan falls
    on a Saturday the NYSE observes New Year's Day on the PRIOR 31 December, so
    that closure lives in the *next* year's set (next occurrence 2027-12-31, for
    New Year's 2028). Checking only the date's own year read those days as
    trading days. No holiday spills the other way -- Christmas observed is the
    latest in-year closure and never lands in January -- so the two lookups are
    sufficient.
    """
    return d in nyse_holidays(d.year) or d in nyse_holidays(d.year + 1)


def is_trading_day(d) -> bool:
    """True if ``d`` is a weekday that is not an NYSE full-closure holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def prev_trading_day(d):
    """Most recent trading day STRICTLY before ``d``.

    **Exclusive.** ``sentiment-dashboard/market_calendar.py`` (absorbed into
    this module) exported a same-named *inclusive* variant -- "largest trading
    day <= d", returning ``d`` itself when ``d`` was a trading day. The
    exclusive form was kept because ``services/options_svc/scheduler.py``'s
    ``_prev_trading_day`` is exclusive and is the load-bearing caller.

    The difference is invisible at a call site -- both return a date and
    neither raises -- so do not "simplify" one into the other.
    """
    for _ in range(_MAX_SPAN_DAYS):
        d = d - timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days before {d}")


def next_trading_day(d):
    """Next trading day STRICTLY after ``d``.

    **Exclusive** -- see the note on ``prev_trading_day``. The absorbed
    ``sentiment-dashboard`` module's variant was inclusive ("smallest trading
    day >= d"), and the difference is invisible at a call site.
    """
    for _ in range(_MAX_SPAN_DAYS):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days after {d}")


# ---------------------------------------------------------------------------
# Sessions, config loading and the extended-hours activation gate
# ---------------------------------------------------------------------------

CT = ZoneInfo("America/Chicago")


class Session(Enum):
    """Which market session a moment falls in.

    Cboe's own vocabulary -- ``GTH`` (Global Trading Hours, the morning
    extended session) and ``CURB`` (the afternoon one). Deliberately NOT
    "premarket"/"afterhours": Cboe distinguishes option GTH from the equity
    pre-market, and conflating them invites wrong assumptions about which
    symbols quote and when.
    """

    CLOSED = "closed"
    GTH = "gth"
    REGULAR = "regular"
    CURB = "curb"


# Mirrors config/sessions.toml exactly. The file is the knob; this is the
# floor the loader falls back to when it is missing or unparseable, so the app
# still has a coherent calendar rather than crashing on a scheduler tick.
_DEFAULTS = {
    "activation": {"extended_hours_from": "2026-08-17"},
    "sessions": {
        "gth": {"start": "06:30", "end": "08:25"},
        "regular": {"start": "08:30", "end": "15:00"},
        "curb": {"start": "15:00", "end": "15:15"},
    },
    "windows": {
        "scan": {"start": "08:00", "end": "15:15"},
        "collection": {"start": "08:00", "eth_start": "06:30", "stop": "15:20"},
        "session_flip": {"at": "08:00"},
        "market_snapshot": {"start": "08:30", "end": "15:00"},
        # ``end_exclusive`` lives here, not only in the TOML, so a missing or
        # corrupt file still degrades to the SAFE behavior: falling back to
        # inclusive would silently re-open the 15:30 ET entry slot.
        "driver_entry": {"tz": "America/New_York", "start": "09:45",
                         "end": "15:30", "end_exclusive": True},
    },
    "alerts": {"fire_in_extended_hours": False},
}

_TOML_PATH = SESSIONS_TOML


def _merge(base, over):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        # A scalar where the default is a table (``sessions = "oops"``) would
        # otherwise be stored verbatim and detonate later, on a scheduler tick
        # rather than at load time. Reject it here -- this runs BEFORE the
        # recursion, so it also catches the nested form ([windows] with
        # scan = "oops"), where the bad value is one level down.
        if isinstance(out.get(k), dict) and not isinstance(v, dict):
            log.warning("sessions.toml: %r should be a table, got %r "
                        "-- using default", k, v)
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_CACHE = {"mtime": None, "cfg": None}


def reset_config_cache():
    """Drop the mtime-cached config (test helper)."""
    _CACHE.update(mtime=None, cfg=None)


def load_config() -> dict:
    """``config/sessions.toml`` merged over ``_DEFAULTS``. Never raises.

    mtime-cached, mirroring
    ``services/options_svc/flow_alerts.load_thresholds``: this is consulted on
    every scheduler tick and every session predicate, but the file changes
    approximately never -- so re-parse only when its mtime moves (or it is
    missing). A missing or corrupt file degrades to the defaults; a scheduler
    must never die because someone fat-fingered a TOML. That covers bad
    *shapes* as well as bad values -- a scalar where a table belongs is
    rejected by ``_merge`` rather than stored to detonate downstream.

    **Returns the CACHED dict -- treat it as read-only.** A caller that
    mutates the result poisons the cache for every other caller in the
    process, including the scheduler ticks in five services.
    """
    try:
        mtime = os.stat(_TOML_PATH).st_mtime
    except Exception:
        mtime = None
    if _CACHE["cfg"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["cfg"]
    try:
        with open(_TOML_PATH, "rb") as fh:
            cfg = _merge(_DEFAULTS, tomllib.load(fh))
    except Exception:
        log.debug("sessions.toml load failed → defaults", exc_info=True)
        cfg = _merge(_DEFAULTS, {})
    _CACHE.update(mtime=mtime, cfg=cfg)
    return cfg


def _parse_time(raw, fallback: str) -> time:
    """``"HH:MM"`` -> ``time``. Malformed input warns and uses ``fallback``.

    Falling back rather than raising is deliberate: a typo in one window's
    start time should not take a service down, and the warning names the value
    so the mistake is still visible in the log.
    """
    for candidate, is_fallback in ((raw, False), (fallback, True)):
        if isinstance(candidate, time):
            return candidate
        try:
            hh, mm = str(candidate).split(":")
            return time(int(hh), int(mm))
        except Exception:
            if not is_fallback:
                log.warning("sessions.toml: bad time %r → using %r",
                            candidate, fallback)
    # Both the configured value and the default are unusable -- can only
    # happen if _DEFAULTS itself is edited wrongly.
    raise ValueError(f"unparseable time fallback {fallback!r}")


def _session_bounds(name: str):
    """``(start, end)`` times for session ``name``, both inclusive."""
    cfg = load_config().get("sessions", {}).get(name, {})
    dflt = _DEFAULTS["sessions"][name]
    return (_parse_time(cfg.get("start"), dflt["start"]),
            _parse_time(cfg.get("end"), dflt["end"]))


def activation_date() -> date:
    """The date Cboe extended options hours go live (``2026-08-17``).

    Every extended-hours branch in the app is gated on this, so a bad value
    falls back to the default rather than raising -- an unparseable date must
    not accidentally enable (or disable) ETH behavior.
    """
    raw = load_config().get("activation", {}).get("extended_hours_from")
    for candidate in (raw, _DEFAULTS["activation"]["extended_hours_from"]):
        if isinstance(candidate, date) and not isinstance(candidate, datetime):
            return candidate
        try:
            return date.fromisoformat(str(candidate))
        except Exception:
            if candidate is raw:
                log.warning("sessions.toml: bad extended_hours_from %r", raw)
    raise ValueError("unparseable activation date default")


def extended_hours_active(d: date) -> bool:
    """True on and after the activation date. Before it, every ETH branch is
    inert and the app behaves byte-identically to the pre-ETH build."""
    return d >= activation_date()


def _ct_of(now) -> datetime:
    """``now`` as a CT-aware datetime. A NAIVE datetime is treated as CT --
    the app's canonical zone -- not UTC."""
    if now.tzinfo is None:
        return now.replace(tzinfo=CT)
    return now.astimezone(CT)


def session_at(now) -> Session:
    """Which session ``now`` (a datetime; naive is treated as CT) falls in.

    Returns CLOSED on weekends, holidays, and -- before the activation date --
    for both extended-hours windows. REGULAR wins the 15:00 overlap with the
    curb window so RTH-gated work does not lose its final minute.
    """
    ct = _ct_of(now)
    d, t = ct.date(), ct.time()
    if not is_trading_day(d):
        return Session.CLOSED
    # Regular first: it owns the 15:00 minute it shares with the curb open.
    reg_start, reg_end = _session_bounds("regular")
    if reg_start <= t <= reg_end:
        return Session.REGULAR
    if not extended_hours_active(d):
        return Session.CLOSED
    for name, session in (("gth", Session.GTH), ("curb", Session.CURB)):
        start, end = _session_bounds(name)
        if start <= t <= end:
            return session
    return Session.CLOSED


def is_regular_hours(now) -> bool:
    """True during the regular 08:30-15:00 CT session only."""
    return session_at(now) is Session.REGULAR


def is_extended_hours(now) -> bool:
    """True during GTH or Curb -- so always False before the activation date."""
    return session_at(now) in (Session.GTH, Session.CURB)


def alerts_fire_in_extended_hours() -> bool:
    """Whether pushes/toasts should fire during GTH and Curb. Off by default:
    a 06:30 CT push on a handful of thin GTH prints is noise."""
    val = load_config().get("alerts", {}).get("fire_in_extended_hours")
    if isinstance(val, bool):
        return val
    return bool(_DEFAULTS["alerts"]["fire_in_extended_hours"])


# ---------------------------------------------------------------------------
# Named operating windows
# ---------------------------------------------------------------------------
#
# These are SERVICE CADENCE windows, not market sessions -- the scan window
# opens 30 min before the regular session by design, collection runs 5 min past
# the curb close, and so on. They legitimately differ from each other and from
# the sessions above, so they are kept distinct rather than merged.


def _window(name: str) -> dict:
    """The config block for window ``name``, merged over its default.

    Raises ``KeyError`` for an unknown name -- a typo'd window is a
    programming error, not something to degrade quietly past.
    """
    dflt = _DEFAULTS["windows"][name]
    return _merge(dflt, load_config().get("windows", {}).get(name, {}))


def _window_tz(win: dict):
    """A window's own timezone, defaulting to CT. Only ``driver_entry`` sets
    one (it is specified in ET, matching the constant it replaces)."""
    name = win.get("tz")
    if not name:
        return CT
    try:
        return ZoneInfo(str(name))
    except Exception:
        log.warning("sessions.toml: bad window tz %r → CT", name)
        return CT


def window_bounds(name: str):
    """``(start, end)`` times for window ``name``, in the window's own tz.

    The close is keyed ``end`` for most windows but ``stop`` for
    ``collection`` -- both are read, matching what the file actually says.
    """
    win, dflt = _window(name), _DEFAULTS["windows"][name]
    close_key = "end" if "end" in dflt else "stop"
    return (_parse_time(win.get("start"), dflt["start"]),
            _parse_time(win.get(close_key), dflt[close_key]))


def in_window(name: str, now) -> bool:
    """True when ``now`` falls inside named window ``name`` on a trading day.

    **INCLUSIVE at both ends** by default, matching the ``_is_market_hours``
    predicates this replaces -- UNLESS the window declares
    ``end_exclusive = true``, in which case the close is exclusive
    (``start <= t < end``).

    Only ``driver_entry`` declares it: ``driver_svc``'s gate is
    ``hm < start or hm >= end``, so the whole 15:30 ET minute is OUTSIDE the
    window. That is what enforces "no new entries in the last 30 min before the
    close" -- inclusive there would open a 16th checkpoint slot at 15:30, firing
    a Claude call and possibly a position inside the no-entry zone. The flag
    makes that a property of the window, so the migration is a straight swap
    with nothing to special-case.

    Note the asymmetry with ``in_collection_window``, whose stop is likewise
    exclusive -- the two mirror different existing predicates and are NOT
    unified on purpose.
    """
    win = _window(name)
    local = now.replace(tzinfo=CT) if now.tzinfo is None else now
    local = local.astimezone(_window_tz(win))
    if not is_trading_day(local.date()):
        return False
    start, end = window_bounds(name)
    t = local.time()
    if win.get("end_exclusive") is True:
        return start <= t < end
    return start <= t <= end


def session_flip_time() -> time:
    """When the Gamma display flips from the prior session to today (08:00 CT).

    Held SEPARATE from the collection start on purpose: widening GTH
    collection to 06:30 must not silently move the display flip.
    """
    win = _window("session_flip")
    return _parse_time(win.get("at"), _DEFAULTS["windows"]["session_flip"]["at"])


def in_collection_window(now, *, eth_eligible: bool = False) -> bool:
    """True when GEX history collection should run for a symbol right now.

    ``eth_eligible`` symbols start at ``collection.eth_start`` (06:30) on and
    after the activation date; everything else -- and everything before
    activation -- starts at ``collection.start`` (08:00). The stop is EXCLUSIVE,
    matching the ``_in_gex_window`` semantics this replaces.
    """
    ct = _ct_of(now)
    d = ct.date()
    if not is_trading_day(d):
        return False
    win, dflt = _window("collection"), _DEFAULTS["windows"]["collection"]
    if eth_eligible and extended_hours_active(d):
        start = _parse_time(win.get("eth_start"), dflt["eth_start"])
    else:
        start = _parse_time(win.get("start"), dflt["start"])
    stop = _parse_time(win.get("stop"), dflt["stop"])
    return start <= ct.time() < stop
