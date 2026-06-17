"""Pure helpers for app-wide scanner alerts + nav badges (unit-tested).

The wiring (timer, audio element, badge UI) lives in main.py's _layout; this
module holds only the decision logic so it can be tested without NiceGUI.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from pages.options.scanner import _sig_key

CT = ZoneInfo("America/Chicago")
_OPEN, _CLOSE = dt.time(8, 0), dt.time(15, 0)   # CT trading window


def _signals(scan):
    scan = scan or {}
    return (scan.get("signals_0dte") or []) + (scan.get("signals_swing") or [])


def scanner_keys(scan):
    """Set of stable signal keys across both tables."""
    return {_sig_key(s) for s in _signals(scan)}


def scanner_scores(scan):
    """{signal_key: composite_score} across both tables."""
    return {_sig_key(s): (s.get("composite_score") or 0) for s in _signals(scan)}


def unread_count(current_keys, acked_keys):
    """How many current keys have not been acknowledged."""
    return len(set(current_keys) - set(acked_keys or set()))


def qualifying_new(scan, alerted, min_score):
    """Signal keys that are new (not yet alerted) AND score >= min_score."""
    scores = scanner_scores(scan)
    alerted = alerted or set()
    return {k for k, sc in scores.items() if k not in alerted and sc >= (min_score or 0)}


def in_market_hours(now):
    """True on a weekday within 08:00–15:00 CT (now is a tz-aware datetime)."""
    ct = now.astimezone(CT)
    return ct.weekday() < 5 and _OPEN <= ct.time() <= _CLOSE


def should_alert(settings, qualifying, now):
    """Whether to chime: enabled, something qualifies, and the market-hours gate passes."""
    if not settings.get("alert_enabled") or not qualifying:
        return False
    if settings.get("alert_market_hours_only") and not in_market_hours(now):
        return False
    return True
