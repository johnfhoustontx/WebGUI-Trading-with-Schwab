"""Server-side options auto-scan scheduler (Task 2.4).

The Tier-2 analog of the page's former ``_autoscan_loop`` in
``webgui/pages/options/scanner.py``. The schedule helpers (constants +
``_is_trading_day``/``_is_market_hours``/``_slot_key``/``autoscan_due``/
``_market_now``) are ported VERBATIM from that page — replicated here (not
imported from the legacy ``scanner`` CLI) to avoid dragging its heavy import
chain — and the cross-app module-name collisions — in.

Cadence: check the current slot every 30 s and run at most one rescan per
trading-day 15-min slot within the 08:00–15:15 CT window. Passed to the
scaffold as ``make_app(scheduler=loop)``; the scaffold runs it once and the
loop+sleep here own the cadence. The BLOCKING rescan runs in the default
executor so the event loop stays responsive.
"""
import asyncio
from datetime import date as _date, time as _time
from zoneinfo import ZoneInfo

from services.options_svc import handlers

# ── Schedule logic (ported verbatim from scanner.py) ───────────────────────
_CT = ZoneInfo("America/Chicago")
_SCAN_START = (8, 0)    # 08:00 CT
_SCAN_END = (15, 15)    # 15:15 CT
# US market holidays 2026 (keep in sync with options-scanner/scanner.py Config.HOLIDAYS)
_HOLIDAYS = {
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
}


def _is_trading_day(now):
    return now.weekday() < 5 and now.date() not in _HOLIDAYS


def _is_market_hours(now):
    t = now.time()
    return _time(*_SCAN_START) <= t <= _time(*_SCAN_END)


def _slot_key(now):
    return (now.date().isoformat(), now.hour, now.minute // 15)


def autoscan_due(now, last_slot):
    """(should_scan, slot_key): True at most once per 15-min slot, only on a
    trading day within the 08:00–15:15 CT window."""
    if not (_is_trading_day(now) and _is_market_hours(now)):
        return (False, last_slot)
    slot = _slot_key(now)
    return (slot != last_slot, slot)


def _market_now():
    import datetime as _dt
    return _dt.datetime.now(_CT)


# ── Scheduler loop ─────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30  # check the slot every 30s (mirrors the page's autoscan loop cadence)


async def loop(bus):
    """Server-side 15-min auto-scan + per-tick header refresh.

    Each 30 s tick: refresh the compact header view (quotes + VIX regime +
    sentiment dot) — its natural cadence — then, on each trading-day 15-min slot
    within 08:00-15:15 CT, run one rescan. Mirrors the page's former
    _autoscan_loop. Both BLOCKING calls run in an executor so the event loop stays
    responsive. Each is independently guarded so one failure can't kill the loop
    or skip the other."""
    loop_ = asyncio.get_event_loop()
    last_slot = None
    # One-shot startup refresh so the Paper Portfolio page has data on first
    # load. The paper account only changes on user actions (entry/manage/reset
    # commands re-publish it), so it is NOT polled every tick. Guarded so a
    # cold DB / missing account never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_paper_account, bus)
    except Exception:
        pass
    # One-shot startup refresh of the paper-trade ledger so the Paper Trades page
    # has data on first load. The ledger only changes on user actions
    # (reload/close/delete/delete-all commands re-publish it), so it is NOT polled
    # every tick. Guarded so a cold DB never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_paper_trades, bus)
    except Exception:
        pass
    # One-shot startup refresh of the open captured-signals view so the Captured
    # Signals page has data on first load. The signal set only changes on user
    # actions (reload/reprice/close commands re-publish it), so it is NOT polled
    # every tick. Guarded so a cold DB never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_captured, bus)
    except Exception:
        pass
    while True:
        # Header refresh runs EVERY tick (the header's ~30s cadence), guarded so a
        # quotes/sentiment hiccup never stalls the autoscan below or the loop.
        try:
            await loop_.run_in_executor(None, handlers.refresh_header, bus)
        except Exception:
            pass
        try:
            now = _market_now()
            due, slot = autoscan_due(now, last_slot)
            if due:
                last_slot = slot
                await loop_.run_in_executor(None, handlers.rescan, bus)
        except Exception:
            pass  # never let the scheduler die
        await asyncio.sleep(POLL_INTERVAL_SEC)
