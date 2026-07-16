"""Single source of truth for the US-equity (NYSE) market calendar used across
the stack: the full-closure holiday set + trading-day / prev / next helpers.

This REPLACES the per-file ``_HOLIDAYS`` copies previously duplicated in
services/{options,sentiment,portfolio}_svc/scheduler.py, webgui/alerts.py, and
options-scanner/scanner.py. Each consumer keeps its own MARKET-HOURS WINDOW
(scan 08:00-15:15, RTH 08:30-15:00, GEX 08:30-15:20) — only the holiday set and
trading-day logic are shared here.

Observed dates follow NYSE rules (Sat->prior Fri, Sun->following Mon), include
Juneteenth. **Update yearly**: add the next year's dates to ``HOLIDAYS`` — this
ONE edit propagates to every consumer.
"""
from __future__ import annotations

import datetime as _dt
from datetime import date as _date
from zoneinfo import ZoneInfo

# Central Time — the stack's market clock (schedulers gate in CT).
CT = ZoneInfo("America/Chicago")

# NYSE full-closure holidays. frozenset so it is safe to share (immutable).
HOLIDAYS: frozenset[_date] = frozenset({
    # 2026
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    # 2027
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
})


def _as_date(d) -> _date:
    return d.date() if hasattr(d, "date") else d


def is_holiday(day) -> bool:
    """True if ``day`` (date or datetime) is an NYSE full-closure holiday."""
    return _as_date(day) in HOLIDAYS


def is_trading_day(day) -> bool:
    """True if ``day`` (date or datetime) is a weekday and not a holiday."""
    d = _as_date(day)
    return d.weekday() < 5 and d not in HOLIDAYS


def prev_trading_day(day) -> _date:
    """Most recent trading day strictly BEFORE ``day``."""
    d = _as_date(day) - _dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= _dt.timedelta(days=1)
    return d


def next_trading_day(day) -> _date:
    """Earliest trading day strictly AFTER ``day``."""
    d = _as_date(day) + _dt.timedelta(days=1)
    while not is_trading_day(d):
        d += _dt.timedelta(days=1)
    return d


def market_now() -> _dt.datetime:
    """Current CT-aware datetime (the stack's market clock)."""
    return _dt.datetime.now(CT)
