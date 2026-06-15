"""Server-side sentiment refresher (Task 1.3).

The Tier-2 analog of the page's former ``_bg_loop``: run a **full** refresh
(with sectors) once at startup, then a composite-only refresh every 120 s. The
blocking ``handlers.refresh`` runs in the default executor so the event loop
stays responsive. Passed to the scaffold as ``make_app(scheduler=loop)``; the
scaffold runs it once and the loop+sleep here own the cadence.
"""
import asyncio

from services.sentiment_svc import handlers

REFRESH_INTERVAL_SEC = 120


async def loop(bus):
    """Full refresh (with sectors) once, then composite-only every 120s.

    Mirrors the page's former ``_bg_loop``. Runs the BLOCKING refresh in an
    executor so the event loop stays responsive.
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
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SEC)
        await loop_.run_in_executor(None, handlers.refresh, bus, False)
