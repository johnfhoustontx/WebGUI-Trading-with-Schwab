"""Server-side sentiment refresher (Task 1.3).

The Tier-2 analog of the page's former ``_bg_loop``: run a **full** refresh
(with sectors) once at startup, then a composite-only refresh every 120 s. The
blocking ``handlers.refresh`` runs in the default executor so the event loop
stays responsive. Passed to the scaffold as ``make_app(scheduler=loop)``; the
scaffold runs it once and the loop+sleep here own the cadence.
"""
import asyncio
from datetime import time as _time

from services.sentiment_svc import handlers
from shared.market_calendar import CT as _CT, HOLIDAYS as _HOLIDAYS

REFRESH_INTERVAL_SEC = 120
TREND_INTERVAL_SEC = 900   # 15 minutes — directional Market Trend recompute cadence

# ── Off-hours refresh gating (P4) ────────────────────────────────────────────
# The 120 s refresh used to run UNCONDITIONALLY 24/7 — ~30-40 proxy→Schwab calls
# (a full backfill + live composite + sector fan-out) every 2 minutes, nights and
# weekends. Gate it like options_svc.periodic_refresh_due: every tick during RTH
# (so the intraday graphs stay live at the 120 s cadence), but only once per
# _OFFHOURS_INTERVAL_MIN slot off-hours/weekends/holidays. The RTH window matches
# the intraday-recording window (handlers._is_rth_now) so the RTH-gated 2-min
# recording keeps firing every cycle while the market is open.
_RTH_START = (8, 30)   # 08:30 CT (mirrors handlers._is_rth_now open)
_RTH_END = (15, 0)     # 15:00 CT (exclusive — mirrors handlers._is_rth_now close)
_OFFHOURS_INTERVAL_MIN = 15  # off-hours throttle: refresh at most once per 15 min


def _is_rth(now):
    """Mon-Fri, not a holiday, 08:30 <= t < 15:00 CT (mirrors handlers._is_rth_now)."""
    if now.weekday() >= 5 or now.date() in _HOLIDAYS:
        return False
    t = now.time()
    return _time(*_RTH_START) <= t < _time(*_RTH_END)


def refresh_due(now, last_slot):
    """(should_refresh, slot) for the periodic sentiment refresh.

    ``now`` is a CT-aware datetime. During RTH → always True (refresh every tick,
    keeping the intraday graphs live at the 120 s cadence), ``last_slot``
    unchanged. Off-hours/weekends/holidays → True at most once per
    _OFFHOURS_INTERVAL_MIN slot (the first off-hours tick still refreshes, then it
    throttles). Pure so the gate is unit-testable; the loop owns the clock."""
    if _is_rth(now):
        return (True, last_slot)
    slot = (now.date().isoformat(), now.hour, now.minute // _OFFHOURS_INTERVAL_MIN)
    return (slot != last_slot, slot)


def _market_now():
    import datetime as _dt
    return _dt.datetime.now(_CT)


def trend_due(now, last):
    """True when the 15-min directional-trend recompute is due.

    ``last`` is the monotonic timestamp of the last recompute (None on cold
    start). Pure so the gate is unit-testable; the refresh path owns the clock."""
    return last is None or (now - last) >= TREND_INTERVAL_SEC


async def loop(bus):
    """Full refresh (with sectors) once, then a GATED composite-only refresh.

    Mirrors the page's former ``_bg_loop`` but throttles off-hours: the 120 s
    refresh runs every tick during RTH (keeping the intraday graphs live) and at
    most once per _OFFHOURS_INTERVAL_MIN slot off-hours/weekends (see
    ``refresh_due``) — cutting the round-the-clock ~30-40-proxy-call churn. The
    30-day history backfill is additionally cached once per session-day inside
    ``handlers.refresh`` (see ``handlers._load_snapshots_cached``), so even an
    RTH tick no longer re-fetches ~6 months of history + rescores 35 days. Runs
    the BLOCKING refresh in an executor so the event loop stays responsive.
    """
    loop_ = asyncio.get_event_loop()
    await loop_.run_in_executor(None, handlers.refresh, bus, True)
    # One-shot rotation refresh at startup so the (manual-refresh-only) Sector
    # Rotation page has data on first load. Guarded so a failure can't kill the
    # loop; NOT polled (rotation is static-ish).
    try:
        await loop_.run_in_executor(None, handlers.refresh_rotation, bus)
    except Exception:  # noqa: BLE001
        pass
    last_slot = None  # off-hours throttle slot (see refresh_due)
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SEC)
        due, last_slot = refresh_due(_market_now(), last_slot)
        if not due:
            continue
        try:
            await loop_.run_in_executor(None, handlers.refresh, bus, False)
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            pass
