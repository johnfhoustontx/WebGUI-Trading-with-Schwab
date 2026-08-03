"""Server-side driver scheduler (autonomous checkpoint cadence).

**Autonomous checkpoints** over one ~30 s poll loop — during the entry window
(09:45–15:30 ET, weekdays — the open's first ~15 min skipped, no new entries in
the last 30 min), fire ``handlers.run_autonomous_cycle`` at most once per
``settings.CHECKPOINT_MIN`` (30)-minute slot, plus a daily halt **re-arm**: a
banked/loss-capped/VIX day latches ``cache:driver:control`` ``halted``; the next
trading day clears that stale overnight latch so checkpoints can run again. The
cycle SELF-GATES on the control key (it no-ops unless ``enabled and not halted``),
so the scheduler may call it whenever a checkpoint is due; autonomy ships OFF by
default (``enabled=False``), so until the user enables it the checkpoints are
no-ops.

The run-time gates (``checkpoint_due``, ``should_rearm``) are pure (tested); the
``loop`` owns its sleep cadence and runs every BLOCKING handler in the default
executor so the event loop stays responsive (``run_autonomous_cycle`` is slow — a
proxy market fetch + a Claude API call — so it MUST go through the executor, never
directly on the loop). Each branch is independently try/except-guarded so one
failure can't kill the loop or skip the others. Passed to the scaffold as
``make_app(scheduler=loop)``.

**Catch-up semantics (single-user):** the autonomous ``last_slot`` is the
in-memory once-per-slot de-dupe for checkpoints. The gate skips weekends AND NYSE
market holidays (``_is_trading_day``), so no cycle — and no Claude
API call — fires on a day the market is closed.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from services.driver_svc import handlers, settings as _settings
from shared import market_calendar as mc
from shared.market_calendar import is_trading_day as _cal_is_trading_day

_ET = ZoneInfo("America/New_York")

# NYSE full-closure holidays come from shared/market_calendar.py (derived, not a
# literal — no yearly edit). ``compute._mtd_trading_days`` reads that module
# directly rather than borrowing an alias from here. The autonomous checkpoint
# gate below treats a weekday holiday like a weekend so no cycle (and no Claude
# API call) fires on a day the market is closed.


def _is_trading_day(now) -> bool:
    """True on a weekday that is not an NYSE full-closure holiday (``now`` is ET-aware)."""
    return _cal_is_trading_day(now.date())

# Autonomous ENTRY window for the checkpoint clock: the named ``driver_entry``
# window in config/sessions.toml (see shared/market_calendar.py), which is
# specified in ET — the only window that is — and carries ``end_exclusive``.
# Deliberately INSIDE regular trading hours, aligned to the daily playbook:
#  * start 09:45 (not the 09:30 open) — skip the first ~15 min so the post-open
#    structure is readable before the Driver opens risk (mirrors the user's
#    08:48 CT post-open review); the open-bell slot never fires.
#  * end 15:30 EXCLUSIVE — no NEW entries in the last 30 min before the 16:00
#    close (pin / gamma risk into the bell for defined-risk + 0-DTE spreads). The
#    whole 15:30 minute is OUT, so the last entry decision is the 15:00 ET slot
#    (14:00 CT). That exclusivity is why the window declares
#    ``end_exclusive = true``: inclusive would re-open a 15:30 checkpoint inside
#    the no-entry zone, firing a Claude call and possibly a position.
# Management/exits are UNAFFECTED — they run on options_svc's separate 5-min
# manage cycle right into the close; this bound only gates NEW driver entries.
RTH_START, RTH_END = mc.window_bounds("driver_entry")   # 09:45, 15:30 ET

POLL_INTERVAL_SEC = 30        # check the run gate every 30 s


def checkpoint_due(now, last_slot):
    """(due, slot_key): True at most once per ``settings.CHECKPOINT_MIN`` slot in RTH.

    ``now`` is an ET-aware datetime; ``last_slot`` is the date-prefixed key of the
    last checkpoint that fired (or None). Due only on trading days (weekday and not
    an NYSE holiday) inside the entry window (09:45–15:30 ET) — the open-bell 09:30
    slot is intentionally skipped (the first fire-able slot is 09:45) and a time
    at/after 15:30 never fires (no new entries into the close, so the last entry
    decision is the 15:00 ET slot). The slot key embeds the date
    (``"YYYY-MM-DD:<slot-index>"``) so the same intraday slot index on the next
    trading day is a fresh key. When not due, the passed-in ``last_slot`` is
    returned unchanged (so the loop's state survives an off-hours poll). Firing is
    gated to trading days so no autonomous cycle (hence no Claude API call) runs on
    a market holiday, even with autonomy enabled.
    """
    if not _is_trading_day(now):  # weekend or market holiday
        return (False, last_slot)
    # ``in_window`` re-checks the trading day, so the explicit gate above is
    # redundant — kept deliberately (as in options_svc's ``_in_gex_window``)
    # because this is the path that spends a Claude call and can open risk, and
    # the holiday gate reads better stated at the call site. The window's close
    # is EXCLUSIVE (``end_exclusive`` in config/sessions.toml), which is what
    # keeps the whole 15:30 ET minute out of the entry zone.
    if not mc.in_window("driver_entry", now):
        return (False, last_slot)
    slot = (now.hour * 60 + now.minute) // _settings.CHECKPOINT_MIN
    key = f"{now.date().isoformat()}:{slot}"
    return (key != last_slot, key)


def should_rearm(control, today) -> bool:
    """True iff a stale OVERNIGHT halt latch should be cleared on ``today``.

    The kill-switch latches ``halted`` for the rest of the day when the cycle
    banks the target / hits the loss cap / sees VIX over the ceiling (or the user
    hits STOP), recording ``halted_date``. A new trading day must clear that latch
    so autonomy re-arms. Returns True only when ``control`` is halted AND its
    ``halted_date`` is a real prior date (not ``None`` and not ``today``) — a halt
    with no recorded date can't be proven stale, so it is left latched.
    """
    return bool(control.get("halted")) and control.get("halted_date") not in (None, today)


def _now_et():
    return datetime.now(_ET)


def _rearm_if_stale(bus, today) -> None:
    """Clear a stale overnight halt latch (blocking bus I/O; runs in the executor).

    Reads the control key, and only if ``should_rearm`` says the latch is a real
    prior-day halt, clears it (``halted=False``, reason cleared). A no-op when not
    halted or already cleared today — so it is cheap to call every poll.
    """
    if should_rearm(handlers.read_control(bus), today):
        handlers.set_control(bus, halted=False, reason=None)


async def loop(bus):
    """Run the autonomous checkpoint cadence.

    A 30 s poll that, each tick:

    * **re-arms** a stale overnight halt (``should_rearm``) BEFORE the checkpoint,
      so a just-cleared latch lets the same poll's checkpoint fire;
    * fires ``handlers.run_autonomous_cycle`` when ``checkpoint_due`` (once per
      30-min RTH slot) — the cycle self-gates on the control key, so the clock
      itself needs no control pre-check.

    Every blocking call runs in the default executor (``run_autonomous_cycle`` is
    SLOW — a proxy fetch + a Claude API call — so it must never run on the event
    loop) and each branch is independently try/except-guarded so one failure can't
    kill the loop or skip the others. ``last_slot`` is the in-memory once-per-slot
    de-dupe.
    """
    loop_ = asyncio.get_event_loop()
    last_slot = None
    while True:
        now = _now_et()
        # Re-arm a stale overnight halt FIRST so a just-cleared latch lets this
        # same poll's checkpoint fire.
        try:
            await loop_.run_in_executor(None, _rearm_if_stale, bus, now.date().isoformat())
        except Exception:  # noqa: BLE001
            pass
        try:
            ck_due, slot = checkpoint_due(now, last_slot)
            if ck_due:
                last_slot = slot
                # BLOCKING + SLOW (proxy fetch + Claude call) → executor, not the loop.
                await loop_.run_in_executor(None, handlers.run_autonomous_cycle, bus)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(POLL_INTERVAL_SEC)
