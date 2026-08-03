"""Single source of truth for the market calendar and session windows.

**Foundation only -- this module currently replaces nothing.** It WILL replace
the ten duplicated holiday sets and the fourteen hardcoded window constants
spread across the five Tier-2 services, the webgui and the options-scanner
engines; that migration is Phase B. Of the ten holiday sites, eight are in
scope, ``claude-driver/config.py`` is exempt, and
``options-scanner/scanner_engine.py`` diverges from the common set.

What ships today is the holiday calendar and the trading-day helpers below.
Session windows, the ``Session`` vocabulary and ``config/sessions.toml``
loading (mtime-cached, mirroring
``services/options_svc/flow_alerts.load_thresholds``) arrive in Phase B.

Everything here is a PURE function of its arguments -- no network, no
database, no clock.

``shared/`` is a namespace package (no ``__init__.py``), so
``from shared.market_calendar import ...`` resolves once the repo root is on
``sys.path`` -- which the services and webgui already arrange. The one caller
needing a bootstrap is ``options-scanner/scanner.py`` (legacy standalone CLI);
use the three-line pattern from ``services/options_svc/commission.py``.

**Update HOLIDAYS yearly** -- add the next year, drop the oldest.
"""
import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

# NYSE full-closure holidays, 2026-2027. Observed dates follow NYSE rules
# (Saturday -> prior Friday, Sunday -> following Monday). Includes Juneteenth
# and Good Friday.
HOLIDAYS = frozenset({
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3),                                        # Good Friday
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26),                                       # Good Friday
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

_MAX_SPAN_DAYS = 10  # longest plausible market closure; bounds the search loops


def is_holiday(d) -> bool:
    """True if ``d`` (a ``date``) is an NYSE full-closure holiday."""
    return d in HOLIDAYS


def is_trading_day(d) -> bool:
    """True if ``d`` is a weekday that is not an NYSE full-closure holiday."""
    return d.weekday() < 5 and d not in HOLIDAYS


def prev_trading_day(d):
    """Most recent trading day STRICTLY before ``d``."""
    for _ in range(_MAX_SPAN_DAYS):
        d = d - timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days before {d}")


def next_trading_day(d):
    """Next trading day STRICTLY after ``d``."""
    for _ in range(_MAX_SPAN_DAYS):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days after {d}")
