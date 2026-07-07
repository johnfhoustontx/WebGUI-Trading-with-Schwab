"""Tests for the options service scheduler's GEX-collection cadence.

``gex_due`` mirrors ``autoscan_due`` but on the gex_collector cadence/window
(2-min slots within 08:30–15:20 CT on trading days) so intraday history is
written whenever this always-on service is up — replacing the fragile standalone
gex_collector.py window. Pure schedule logic, tested with fixed CT datetimes.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import scheduler

_CT = ZoneInfo("America/Chicago")


def _ct(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=_CT)


# 2026-06-15 is a Monday (a normal trading day).
def test_gex_due_first_tick_in_window():
    due, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 40), None)
    assert due is True and slot is not None


def test_gex_due_not_repeated_within_same_2min_slot():
    due, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 40), None)
    due2, slot2 = scheduler.gex_due(_ct(2026, 6, 15, 8, 41), slot)
    assert due2 is False and slot2 == slot   # 08:40–08:41 is one slot


def test_gex_due_fires_on_next_2min_slot():
    _, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 42), None)
    due, slot2 = scheduler.gex_due(_ct(2026, 6, 15, 8, 44), slot)
    assert due is True and slot2 != slot


def test_gex_due_before_window():
    due, _ = scheduler.gex_due(_ct(2026, 6, 15, 8, 15), None)
    assert due is False


def test_gex_due_after_window():
    due, _ = scheduler.gex_due(_ct(2026, 6, 15, 15, 25), None)
    assert due is False


def test_gex_due_weekend():
    # 2026-06-13 is a Saturday.
    due, _ = scheduler.gex_due(_ct(2026, 6, 13, 9, 0), None)
    assert due is False


def test_gex_due_holiday():
    # 2026-07-03 is in scheduler._HOLIDAYS.
    due, _ = scheduler.gex_due(_ct(2026, 7, 3, 9, 0), None)
    assert due is False


def test_gex_due_juneteenth():
    # Juneteenth is a full NYSE closure — 2026-06-19 and the next occurrence,
    # 2027-06-18 (observed, since 6/19/2027 is a Saturday), are both holidays.
    assert scheduler.gex_due(_ct(2026, 6, 19, 9, 0), None)[0] is False
    assert scheduler.gex_due(_ct(2027, 6, 18, 9, 0), None)[0] is False


# ── manage_due (paper auto-manage cadence) ──────────────────────────────────
def test_manage_due_first_tick_in_window():
    due, slot = scheduler.manage_due(_ct(2026, 6, 15, 9, 0), None)
    assert due is True and slot is not None


def test_manage_due_not_repeated_within_same_5min_slot():
    _, slot = scheduler.manage_due(_ct(2026, 6, 15, 9, 0), None)
    due2, slot2 = scheduler.manage_due(_ct(2026, 6, 15, 9, 3), slot)
    assert due2 is False and slot2 == slot


def test_manage_due_fires_on_next_5min_slot():
    _, slot = scheduler.manage_due(_ct(2026, 6, 15, 9, 2), None)
    due, slot2 = scheduler.manage_due(_ct(2026, 6, 15, 9, 6), slot)
    assert due is True and slot2 != slot


def test_manage_due_before_market_open():
    due, _ = scheduler.manage_due(_ct(2026, 6, 15, 7, 30), None)
    assert due is False


def test_manage_due_after_market_close():
    due, _ = scheduler.manage_due(_ct(2026, 6, 15, 15, 30), None)
    assert due is False


def test_manage_due_weekend():
    # 2026-06-13 is a Saturday.
    due, _ = scheduler.manage_due(_ct(2026, 6, 13, 9, 0), None)
    assert due is False


def test_manage_due_holiday():
    due, _ = scheduler.manage_due(_ct(2026, 7, 3, 9, 0), None)
    assert due is False


# ── action_alert_due (10/1/3 CT digest) ─────────────────────────────────────
def test_action_alert_due_fires_at_each_slot():
    # 2026-06-15 is a Monday.
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 10, 0), set()) == "morning"
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 13, 0), set()) == "midday"
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 15, 0), set()) == "close"


def test_action_alert_due_within_grace():
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 10, 15), set()) == "morning"


def test_action_alert_due_not_after_grace():
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 10, 25), set()) is None


def test_action_alert_due_once_per_slot():
    ran = {("2026-06-15", "morning")}
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 10, 5), ran) is None


def test_action_alert_due_off_hours_none():
    assert scheduler.action_alert_due(_ct(2026, 6, 15, 9, 0), set()) is None   # before first slot


def test_action_alert_due_weekend_none():
    # 2026-06-13 is a Saturday.
    assert scheduler.action_alert_due(_ct(2026, 6, 13, 10, 0), set()) is None


def test_action_alert_due_holiday_none():
    assert scheduler.action_alert_due(_ct(2026, 7, 3, 10, 0), set()) is None


# ── periodic_refresh_due (header + gex_status per-tick gating) ───────────────
# During market hours the header/status refresh every tick; off-hours/weekends
# they throttle to a longer interval so the service stops making proxy + SQLite +
# Redis calls every 30s, 24/7, with no browser open.
def test_periodic_refresh_due_every_tick_in_market_hours():
    # Same last_slot on consecutive market-hours ticks -> always due.
    due1, slot1 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 10, 0), "x")
    due2, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 10, 0), slot1)
    assert due1 is True and due2 is True


def test_periodic_refresh_due_offhours_first_tick():
    due, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 0), None)
    assert due is True and slot is not None


def test_periodic_refresh_due_offhours_throttled_within_slot():
    _, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 0), None)
    due2, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 2), slot)
    assert due2 is False and slot2 == slot  # 20:00-20:02 share one 5-min slot


def test_periodic_refresh_due_offhours_fires_next_slot():
    _, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 2), None)
    due, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 15, 20, 6), slot)
    assert due is True and slot2 != slot


def test_periodic_refresh_due_weekend_throttled():
    # Saturday 10:00 — not a trading day, so the off-hours throttle applies even
    # though the clock time is within the market-hours window.
    _, slot = scheduler.periodic_refresh_due(_ct(2026, 6, 13, 10, 0), None)
    due2, slot2 = scheduler.periodic_refresh_due(_ct(2026, 6, 13, 10, 1), slot)
    assert due2 is False and slot2 == slot


# ── Gamma display persistence (active session date + cleared window) ─────────
# Reference weekdays: 2026-06-22 Mon, 23 Tue, 24 Wed, 25 Thu, 26 Fri, 27 Sat, 28 Sun.
def test_active_session_date_is_today_on_a_trading_day():
    assert scheduler.active_session_date(_ct(2026, 6, 23, 16, 0)).isoformat() == "2026-06-23"


def test_active_session_date_holds_friday_through_weekend():
    assert scheduler.active_session_date(_ct(2026, 6, 27, 10, 0)).isoformat() == "2026-06-26"  # Sat
    assert scheduler.active_session_date(_ct(2026, 6, 28, 23, 0)).isoformat() == "2026-06-26"  # Sun


def test_active_session_date_holds_prior_day_on_holiday():
    # 2026-01-19 (MLK) is a Monday holiday → most recent trading day is Fri 2026-01-16.
    assert scheduler.active_session_date(_ct(2026, 1, 19, 12, 0)).isoformat() == "2026-01-16"


def test_gamma_cleared_overnight_on_a_trading_day():
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 0, 0)) is True    # Tue midnight
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 7, 0)) is True    # Tue pre-session
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 10, 0)) is False  # Tue live session
    assert scheduler.gamma_cleared(_ct(2026, 6, 23, 23, 0)) is False  # Tue after close → persist


def test_gamma_not_cleared_on_non_trading_days():
    assert scheduler.gamma_cleared(_ct(2026, 6, 27, 2, 0)) is False   # Sat → persist Friday
    assert scheduler.gamma_cleared(_ct(2026, 1, 19, 2, 0)) is False   # MLK Mon → persist


# ── Driver-account manage tick wiring (Phase 5 / Task 5.1) ──────────────────
# The driver's ISOLATED paper account reprices on the SAME 5-min manage cadence
# as the manual account (it reuses the ``manage_due`` gate — no new cadence). The
# loop() is an infinite coroutine so it can't be unit-driven; assert (a) the
# wiring target exists + is callable, and (b) the loop source runs the driver
# tick inside its OWN try/except so a driver-side failure can't skip the manual
# refresh or kill the loop.
def test_driver_manage_handler_is_wired():
    from services.options_svc import handlers

    assert callable(handlers.run_driver_manage_and_refresh)


def test_loop_refreshes_gamma_after_collection():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The GEX-collection branch also republishes the current gamma snapshot, so the
    # heatmap stays fresh server-side with no page open. It runs AFTER collection.
    assert "refresh_gamma_current" in src
    seg = src.split("collect_gex_history", 1)[1]
    assert "refresh_gamma_current" in seg


def test_loop_runs_driver_manage_under_its_own_guard():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The driver manage+refresh is invoked in the loop.
    assert "run_driver_manage_and_refresh" in src
    # …and the manual manage tick is still invoked (not replaced).
    assert "run_manage_and_refresh" in src
    # The driver tick has its OWN try/except so it's independent of the manual
    # refresh (a separate guarded block, not piggybacked on the manual try).
    mdue = src.split("m_due, m_slot = manage_due", 1)[1]
    # Within the manage block, both the manual and driver refreshes appear, and
    # the driver call is preceded by its own ``try:`` (a sibling guard).
    assert "run_manage_and_refresh" in mdue and "run_driver_manage_and_refresh" in mdue
    driver_seg = mdue.split("run_driver_manage_and_refresh", 1)[0]
    # Between the manual refresh and the driver refresh there is a fresh ``try:``
    # opening the driver's own guarded block.
    assert "try:" in driver_seg.split("run_manage_and_refresh", 1)[1]


# ── Cadence-mirror drift guard ──────────────────────────────────────────────
# The GEX-collection cadence is intentionally mirrored (not imported) between the
# standalone collector and this Tier-2 scheduler to keep the scheduler's import
# light (see scheduler.py's _GEX_* comment). Mirrors drift silently, so assert the
# two interval constants — and the staleness threshold — stay in lockstep. This is
# the only safety net the half-edit hazard has.
def test_gex_interval_mirrors_collector():
    import gex_collector
    import gex_status

    assert scheduler._GEX_INTERVAL_MIN == gex_collector.POLL_INTERVAL_MIN
    # Staleness threshold == 2 poll intervals (in seconds).
    assert gex_status.STALE_AFTER_SEC == gex_collector.POLL_INTERVAL_MIN * 60 * 2


# ── Scheduled Gamma Analyze cadence (analyze_slot_due) ──────────────────────
# Four fixed CT slots fire once per trading day within a grace window after the
# target; latched via the caller's ran-set. 2026-06-15 is a Monday.
def test_analyze_slot_fires_at_target():
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 8, 0), set()) == "premarket"
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 8, 48), set()) == "open"
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 11, 30), set()) == "midday"
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 14, 58), set()) == "close"


def test_analyze_slot_fires_within_grace_window():
    # 09:05 CT is 5 min past the 09:00... actually premarket is 08:00 CT; check 08:19.
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 8, 19), set()) == "premarket"


def test_analyze_slot_skips_past_grace():
    # 08:20 CT is 20 min past the 08:00 target → outside the [target, target+20) window.
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 8, 20), set()) is None


def test_analyze_slot_latched_once_per_day():
    ran = {("2026-06-15", "premarket")}
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 8, 1), ran) is None


def test_analyze_slot_none_outside_all_windows():
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 10, 0), set()) is None


def test_analyze_slot_skips_weekend():
    # 2026-06-13 is a Saturday.
    assert scheduler.analyze_slot_due(_ct(2026, 6, 13, 8, 0), set()) is None


def test_analyze_slot_skips_holiday():
    # 2026-12-25 is in _HOLIDAYS.
    assert scheduler.analyze_slot_due(_ct(2026, 12, 25, 8, 0), set()) is None


def test_loop_wires_scheduled_analyze():
    import inspect
    src = inspect.getsource(scheduler.loop)
    assert "analyze_slot_due" in src and "run_scheduled_gamma_analyze" in src
    # The slot is latched in analyze_ran BEFORE the blocking call (no double-fire).
    seg = src.split("analyze_slot_due", 1)[1].split("run_scheduled_gamma_analyze", 1)[0]
    assert "analyze_ran.add" in seg


# ── Concurrent due-branch execution (A4) ────────────────────────────────────
# When multiple slot branches are due on the same tick they must run
# CONCURRENTLY, so a slow branch (e.g. the 15-min rescan) can't delay the START
# of the time-critical ones (2-min GEX collect / 5-min manage). Each branch keeps
# its OWN isolation so one failure/hang can't sink the others.
import asyncio  # noqa: E402


def test_gather_due_runs_branches_concurrently():
    """Two due branches that each 'sleep' complete in ~max(t), not ~sum(t) —
    proving they run concurrently rather than serially awaited."""
    order = []

    async def branch_slow():
        order.append("slow_start")
        await asyncio.sleep(0.05)
        order.append("slow_end")

    async def branch_fast():
        order.append("fast_start")
        await asyncio.sleep(0.01)
        order.append("fast_end")

    async def _drive():
        await scheduler._gather_due([branch_slow(), branch_fast()])

    asyncio.run(_drive())
    # Both branches STARTED before either finished (interleaved) → concurrent.
    assert order[:2] == ["slow_start", "fast_start"]
    # The fast branch finished before the slow one (it wasn't blocked behind it).
    assert order.index("fast_end") < order.index("slow_end")


def test_gather_due_isolates_branch_failures():
    """A branch that RAISES must not prevent the others from running (per-branch
    isolation preserved through the concurrent gather)."""
    ran = []

    async def branch_boom():
        ran.append("boom")
        raise RuntimeError("branch failed")

    async def branch_ok():
        ran.append("ok")

    async def _drive():
        # Must NOT raise despite branch_boom blowing up.
        await scheduler._gather_due([branch_boom(), branch_ok()])

    asyncio.run(_drive())
    assert "boom" in ran and "ok" in ran


def test_loop_runs_due_branches_concurrently():
    """The tick loop gathers its due branches (concurrent) rather than awaiting
    each executor call serially in sequence."""
    import inspect
    src = inspect.getsource(scheduler.loop)
    assert "_gather_due" in src
