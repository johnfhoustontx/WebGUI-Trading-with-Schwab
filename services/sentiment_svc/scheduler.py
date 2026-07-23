"""Server-side sentiment refresher (Task 1.3).

The Tier-2 analog of the page's former ``_bg_loop``: run a **full** refresh
(with sectors) once at startup, then a composite-only refresh every 120 s —
plus a **with-sectors refresh once per RTH hour** (``sectors_due``) so the
sector P/C (a live option-volume ratio, empty premarket) populates after the
open even when the service started premarket. The blocking ``handlers.refresh``
runs in the default executor so the event loop stays responsive. Passed to the
scaffold as ``make_app(scheduler=loop)``; the scaffold runs it once and the
loop+sleep here own the cadence.
"""
import asyncio
import logging
from datetime import date as _date, time as _time
from zoneinfo import ZoneInfo

from services.sentiment_svc import handlers, order_flow_consumer

log = logging.getLogger(__name__)

REFRESH_INTERVAL_SEC = 120
TREND_INTERVAL_SEC = 900   # 15 minutes — directional Market Trend recompute cadence
REGIME_INTERVAL_MIN = 5    # 5 minutes — blended market-regime recompute cadence
ORDER_FLOW_PUBLISH_SEC = 30   # publish cache:sentiment:order_flow at this cadence

# ── Off-hours refresh gating (P4) ────────────────────────────────────────────
# The 120 s refresh used to run UNCONDITIONALLY 24/7 — ~30-40 proxy→Schwab calls
# (a full backfill + live composite + sector fan-out) every 2 minutes, nights and
# weekends. Gate it like options_svc.periodic_refresh_due: every tick during RTH
# (so the intraday graphs stay live at the 120 s cadence), but only once per
# _OFFHOURS_INTERVAL_MIN slot off-hours/weekends/holidays. The RTH window matches
# the intraday-recording window (handlers._is_rth_now) so the RTH-gated 2-min
# recording keeps firing every cycle while the market is open.
_CT = ZoneInfo("America/Chicago")
_RTH_START = (8, 30)   # 08:30 CT (mirrors handlers._is_rth_now open)
_RTH_END = (15, 0)     # 15:00 CT (exclusive — mirrors handlers._is_rth_now close)
_OFFHOURS_INTERVAL_MIN = 15  # off-hours throttle: refresh at most once per 15 min
# US market holidays 2026–2027 (kept in sync with the other service schedulers,
# options-scanner Config.HOLIDAYS, and webgui/alerts.py). Includes Juneteenth;
# observed dates per NYSE (Sat→prior Fri, Sun→following Mon). Update yearly.
_HOLIDAYS = {
    # 2026
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    # 2027
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
}


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


def sectors_due(now, last_slot):
    """(should_refresh_sectors, slot) — hourly RTH sector-fan-out recompute.

    The sector view (quotes/trends/**P/C**/RRG) used to be computed ONLY at
    service startup + the manual Refresh button. A premarket start (the normal
    morning routine) meant every ETF's chain had zero option volume, so
    ``pcr_from_chain`` returned None for all 11 sectors and the P/C column
    stayed BLANK all day (2026-07-09). Fire once per RTH hour: the first tick
    at/after the 08:30 CT open heals the blank within one refresh cycle, and
    the hourly recompute keeps P/C (a live volume ratio) current. ~24 extra
    proxy calls per fire — cheap at 7×/day. When not due, ``last_slot`` passes
    through unchanged."""
    if not _is_rth(now):
        return (False, last_slot)
    slot = (now.date().isoformat(), now.hour)
    return (slot != last_slot, slot)


def regime_due(now, last_slot):
    """(should_recompute, slot) — the 5-min RTH gate for the blended market regime.

    Its OWN slot, deliberately NOT a speedup of the 15-min trend recompute: 5-min
    bars only finalize every 5 minutes, so sampling faster just re-reads an
    unfinished bar. RTH only — off-hours the last committed regime PERSISTS (its
    ``as_of`` shows the age) rather than being recomputed on stale bars, so
    ``last_slot`` passes through unchanged. Mirrors ``sectors_due``: pure, the
    caller owns the clock and holds the slot.

    The gate is polled from the 120 s refresh, so a 5-min slot actually fires
    within <=2 min of its boundary — accepted in the design (the one place where
    latency is expensive, crisis onset, has its own per-refresh fast path in
    ``handlers.run_crisis_check``)."""
    if not _is_rth(now):
        return (False, last_slot)
    slot = (now.date().isoformat(), now.hour, now.minute // REGIME_INTERVAL_MIN)
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

    # Streaming aggressor order-flow (aggression axis input). Start the blocking
    # SSE consumer on a daemon thread + a ~30 s publish task, both guarded so a
    # consumer/publish failure can NEVER break the main refresh loop below.
    of_stop = None
    of_opt_stop = None
    of_task = None
    try:
        of_stop = order_flow_consumer.start_consumer(bus)
        of_opt_stop = order_flow_consumer.start_option_consumer(bus)
        of_task = asyncio.create_task(_order_flow_publish_loop(bus, loop_))
    except Exception:  # noqa: BLE001 — order-flow is best-effort; refresh must go on.
        log.exception("order-flow consumer failed to start")

    last_slot = None      # off-hours throttle slot (see refresh_due)
    sectors_slot = None   # hourly RTH sector-recompute slot (see sectors_due)
    try:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL_SEC)
            now = _market_now()
            due, last_slot = refresh_due(now, last_slot)
            if not due:
                continue
            # Hourly RTH sector recompute rides the same tick: with_sectors=True
            # once per RTH hour so the P/C column (live option-volume ratio)
            # populates after the open even when the service started premarket.
            with_sectors, sectors_slot = sectors_due(now, sectors_slot)
            try:
                await loop_.run_in_executor(None, handlers.refresh, bus, with_sectors)
            except Exception:  # noqa: BLE001 — never let the scheduler die.
                pass
    finally:
        if of_stop is not None:
            of_stop.set()
        if of_opt_stop is not None:
            of_opt_stop.set()
        if of_task is not None:
            of_task.cancel()


async def _order_flow_publish_loop(bus, loop_):
    """Publish ``cache:sentiment:order_flow`` every ~30 s off the event loop.

    Fully isolated from the main refresh loop — any publish failure is swallowed
    (``order_flow_consumer.publish`` is itself defensive) so streaming order-flow
    can never break the composite refresh cadence."""
    while True:
        await asyncio.sleep(ORDER_FLOW_PUBLISH_SEC)
        try:
            await loop_.run_in_executor(None, order_flow_consumer.publish, bus)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort publish.
            log.debug("order-flow publish tick failed", exc_info=True)
