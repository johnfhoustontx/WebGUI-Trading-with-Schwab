"""Server-side driver scheduler (Task #29c; autonomous cadence Phase 6).

Two coexisting cadences over one ~30 s poll loop:

* **Legacy morning approval** — runs the morning pipeline once per trading day
  at/after 09:28 ET (the ``claude-driver/morning_agent`` cadence,
  ``config.AGENT_RUN_HOUR/MIN``) and keeps the performance view warm. The
  scheduler NEVER executes orders here — ``run_morning`` only produces a *pending*
  approval; execution happens only on an explicit ``approve`` command. Retained
  unchanged so the approval-queue/back-compat behaviour still works.
* **Autonomous checkpoints (Phase 6)** — during the entry window (09:45–15:30 ET,
  weekdays — the open's first ~15 min skipped, no new entries in the last 30 min),
  fire ``handlers.run_autonomous_cycle`` at most once per ``settings.CHECKPOINT_MIN``
  (30)-minute slot, plus a daily halt **re-arm**: a banked/loss-capped/VIX day
  latches ``cache:driver:control`` ``halted``; the next trading day clears that
  stale overnight latch so checkpoints can run again. The cycle SELF-GATES on the
  control key (it no-ops unless ``enabled and not halted``), so the scheduler may
  call it whenever a checkpoint is due; autonomy ships OFF by default
  (``enabled=False``), so until the user enables it the checkpoints are no-ops and
  only the legacy morning path runs. They coexist intentionally.

The run-time gates (``morning_due``, ``checkpoint_due``, ``should_rearm``) are
pure (tested); the ``loop`` owns its sleep cadence and runs every BLOCKING handler
in the default executor so the event loop stays responsive
(``run_autonomous_cycle`` is slow — a proxy market fetch + a Claude API call — so
it MUST go through the executor, never directly on the loop). Each branch is
independently try/except-guarded so one failure can't kill the loop or skip the
others. Passed to the scaffold as ``make_app(scheduler=loop)``.

**Catch-up semantics (single-user):** ``last_run_date`` lives in memory, so if
the service starts after 09:28 on a trading day it fires once immediately
(``last_run_date=None`` → due) so the page has a fresh pending approval; a
restart later the same day re-fires and overwrites the cached approval. The
market-holiday short-circuit lives in ``compute.run_morning`` (it returns a
``no_trade`` payload), so the gate here only checks weekday + time. The
autonomous ``last_slot`` is the analogous in-memory de-dupe for checkpoints.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from services.driver_svc import handlers, settings as _settings

_ET = ZoneInfo("America/New_York")
RUN_HOUR, RUN_MIN = 9, 28  # mirrors config.AGENT_RUN_HOUR / AGENT_RUN_MIN

# Autonomous ENTRY-window bounds for the checkpoint clock (ET, h:m tuples).
# Deliberately INSIDE regular trading hours, aligned to the daily playbook:
#  * start 09:45 (not the 09:30 open) — skip the first ~15 min so the post-open
#    structure is readable before the Driver opens risk (mirrors the user's
#    08:48 CT post-open review); the open-bell slot never fires.
#  * end 15:30 — no NEW entries in the last 30 min before the 16:00 close (pin /
#    gamma risk into the bell for defined-risk + 0-DTE spreads). A slot at/after
#    RTH_END never fires, so the last entry decision is the 15:00 ET slot (14:00 CT).
# Management/exits are UNAFFECTED — they run on options_svc's separate 5-min
# manage cycle right into the close; this bound only gates NEW driver entries.
RTH_START = (9, 45)
RTH_END = (15, 30)

POLL_INTERVAL_SEC = 30        # check the run gate every 30 s
PERF_REFRESH_SEC = 300        # recompute the perf view ~every 5 min


def morning_due(now, last_run_date):
    """(should_run, run_date): True at most once per weekday, at/after 09:28 ET.

    ``now`` is an ET-aware datetime; ``last_run_date`` is the ISO date string of
    the last fire (or None). When not yet due, the passed-in ``last_run_date`` is
    returned unchanged.
    """
    if now.weekday() >= 5:  # Sat/Sun
        return (False, last_run_date)
    if (now.hour, now.minute) < (RUN_HOUR, RUN_MIN):
        return (False, last_run_date)
    today = now.date().isoformat()
    return (today != last_run_date, today)


def checkpoint_due(now, last_slot):
    """(due, slot_key): True at most once per ``settings.CHECKPOINT_MIN`` slot in RTH.

    ``now`` is an ET-aware datetime; ``last_slot`` is the date-prefixed key of the
    last checkpoint that fired (or None). Due only on weekdays inside the entry
    window (09:45–15:30 ET) — the open-bell 09:30 slot is intentionally skipped
    (the first fire-able slot is 09:45) and a time at/after 15:30 never fires (no
    new entries into the close, so the last entry decision is the 15:00 ET slot).
    The slot key embeds the date
    (``"YYYY-MM-DD:<slot-index>"``) so the same intraday slot index on the next
    trading day is a fresh key. When not due, the passed-in ``last_slot`` is
    returned unchanged (so the loop's state survives an off-hours poll).
    """
    if now.weekday() >= 5:  # Sat/Sun
        return (False, last_slot)
    hm = (now.hour, now.minute)
    if hm < RTH_START or hm >= RTH_END:
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
    """Run the morning approval cadence + the autonomous checkpoint cadence.

    One-shot perf refresh at startup so the page has data on first load, then a
    30 s poll that, each tick:

    * fires the morning pipeline when ``morning_due`` (once/day at 09:28 ET) —
      the legacy approval path, retained;
    * **re-arms** a stale overnight halt (``should_rearm``) BEFORE the checkpoint,
      so a just-cleared latch lets the same poll's checkpoint fire;
    * fires ``handlers.run_autonomous_cycle`` when ``checkpoint_due`` (once per
      30-min RTH slot) — the cycle self-gates on the control key, so the clock
      itself needs no control pre-check;
    * recomputes the perf view every ~5 min.

    Every blocking call runs in the default executor (``run_autonomous_cycle`` is
    SLOW — a proxy fetch + a Claude API call — so it must never run on the event
    loop) and each branch is independently try/except-guarded so one failure can't
    kill the loop or skip the others. ``last_run_date`` / ``last_slot`` are the
    in-memory once-per-day / once-per-slot de-dupes.
    """
    loop_ = asyncio.get_event_loop()
    last_run_date = None
    last_slot = None
    try:
        await loop_.run_in_executor(None, handlers.refresh_perf, bus)
    except Exception:  # noqa: BLE001
        pass
    secs_since_perf = 0
    while True:
        now = _now_et()
        try:
            due, run_date = morning_due(now, last_run_date)
            if due:
                last_run_date = run_date
                await loop_.run_in_executor(None, handlers.run_morning, bus)
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            pass
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
        if secs_since_perf >= PERF_REFRESH_SEC:
            secs_since_perf = 0
            try:
                await loop_.run_in_executor(None, handlers.refresh_perf, bus)
            except Exception:  # noqa: BLE001
                pass
        secs_since_perf += POLL_INTERVAL_SEC
        await asyncio.sleep(POLL_INTERVAL_SEC)
