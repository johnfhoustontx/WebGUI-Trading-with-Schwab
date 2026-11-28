"""Breadth's new highs / new lows, from the market service's view.

Schwab has no $NYHGH / $NYLOW (measured 2026-11-27: /quotes answers
invalidSymbols and /pricehistory empty for those and every alternate spelling),
so both breadth reads - the composite's Breadth component and the Market Trend's
breadth sub-score - take the counts from ``cache:market:new_highs``
(``services/market_svc/new_highs.py``, TradingView's screener).

That view counts US common stocks at $5 and up on NYSE, Nasdaq and NYSE American
(about 3,400), not NYSE issues. Both scorers read only a RATIO of highs to lows,
so the different universe moves no threshold.

A count is a reading only when it is TODAY'S (the view's ``session`` is today's
Central date) and, in the regular session, no older than the view's own
``stale_after_sec`` by its confirmed ``:ts`` stamp - the age at which the Macro
Board greys it. Before the open the view still holds the last session's close;
that is no reading for today, exactly as the A/D counters are not. Everything
else - no view, switched off, a count missing or not a whole number - is None,
never 0.

The service reads Redis; services never import one another, so the view's key
and window names are mirrored here and pinned by
``shared/tests/test_cross_tier_mirrors.py``.
"""
import datetime as _dt
import logging
import sys
from zoneinfo import ZoneInfo

from repo_paths import SENTIMENT
from shared import market_calendar as mc

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

import composite_config  # noqa: E402  (sentiment-dashboard, config/sentiment.toml)

log = logging.getLogger(__name__)

CACHE = "cache:market:new_highs"
_CT = ZoneInfo("America/Chicago")


def _whole(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _in_session(now) -> bool:
    d = now.date()
    return mc.is_trading_day(d) and mc.regular_open_on(d) <= now < mc.session_close_on(d)


def _fresh(view, ts, now) -> bool:
    limit = view.get("stale_after_sec")
    if not _in_session(now) or not isinstance(limit, int) or limit <= 0:
        return True                    # nothing refreshes then: age says nothing
    try:
        when = _dt.datetime.fromisoformat(str(ts)) if ts else None
    except ValueError:
        when = None
    if when is None:
        return False                   # in the session, an unconfirmed age is no reading
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (now - when).total_seconds() <= limit


def pick(view, ts, now, window):
    """PURE: ``(new_highs, new_lows)`` for ``window`` from the view, or None.

    ``ts`` is the view's confirmed stamp (``cache_metas``), ``now`` an aware
    datetime."""
    if not isinstance(view, dict) or view.get("enabled") is False:
        return None
    now = now.astimezone(_CT)
    if view.get("session") != now.date().isoformat():
        return None
    counts = view.get("counts")
    one = counts.get(window) if isinstance(counts, dict) else None
    if not isinstance(one, dict):
        return None
    highs, lows = _whole(one.get("highs")), _whole(one.get("lows"))
    if highs is None or lows is None or not _fresh(view, ts, now):
        return None
    return float(highs), float(lows)


def read(bus, now=None):
    """Today's ``(new_highs, new_lows)`` from the bus, or None. Never raises."""
    now = now or _dt.datetime.now(_CT)
    try:
        env = bus.cache_get(CACHE)
        if env is None:
            return None
        _ver, ts = bus.cache_metas([CACHE]).get(CACHE, (None, None))
        return pick(env.payload, ts, now, composite_config.highs_lows_window())
    except Exception:  # noqa: BLE001 - a failed read is no reading, never an abort
        log.debug("new highs / lows read failed", exc_info=True)
        return None
