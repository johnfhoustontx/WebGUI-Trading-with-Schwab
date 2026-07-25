"""Tests for the options service scheduler's GEX-collection cadence.

``gex_due`` mirrors ``autoscan_due`` but on the gex_collector cadence/window
(1-min slots within 08:30–15:20 CT on trading days) so intraday history is
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


def test_gex_due_not_repeated_within_same_1min_slot():
    due, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 40), None)
    due2, slot2 = scheduler.gex_due(_ct(2026, 6, 15, 8, 40), slot)
    assert due is True and slot is not None
    assert due2 is False and slot2 == slot   # same minute is one slot


def test_gex_due_fires_on_next_1min_slot():
    _, slot = scheduler.gex_due(_ct(2026, 6, 15, 8, 42), None)
    due, slot2 = scheduler.gex_due(_ct(2026, 6, 15, 8, 43), slot)
    assert due is True and slot2 != slot


def test_gex_due_before_window():
    # Collection starts 08:00 CT (30 min pre-open) → 07:45 is still outside.
    due, _ = scheduler.gex_due(_ct(2026, 6, 15, 7, 45), None)
    assert due is False
    due2, _ = scheduler.gex_due(_ct(2026, 6, 15, 8, 0), None)
    assert due2 is True          # the window's first slot


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


# ── paper_cycle_due (manual Paper Portfolio hourly entry+manage) ────────────
# The MANUAL Paper Portfolio runs entry + manage once at the TOP OF THE HOUR,
# 09:00–14:00 CT (last run 14:00 / 2pm; NO 15:00 run at the regular-session
# close). Trading days only; each hour fires once within a grace window
# (mirrors analyze_slot_due), latched via the caller's ran-set.
def test_paper_cycle_due_fires_at_each_hour():
    # 2026-06-15 is a Monday.
    for h in (9, 10, 11, 12, 13, 14):
        assert scheduler.paper_cycle_due(_ct(2026, 6, 15, h, 0), set()) == h


def test_paper_cycle_due_within_grace():
    assert scheduler.paper_cycle_due(_ct(2026, 6, 15, 9, 15), set()) == 9


def test_paper_cycle_due_not_after_grace():
    assert scheduler.paper_cycle_due(_ct(2026, 6, 15, 9, 25), set()) is None


def test_paper_cycle_due_latched_once_per_hour():
    ran = {("2026-06-15", 9)}
    assert scheduler.paper_cycle_due(_ct(2026, 6, 15, 9, 5), ran) is None


def test_paper_cycle_due_no_run_at_3pm_close():
    # No 15:00 (3pm CT) run — the regular session closes then.
    assert scheduler.paper_cycle_due(_ct(2026, 6, 15, 15, 0), set()) is None


def test_paper_cycle_due_before_first_hour():
    assert scheduler.paper_cycle_due(_ct(2026, 6, 15, 8, 30), set()) is None


def test_paper_cycle_due_weekend_none():
    # 2026-06-13 is a Saturday.
    assert scheduler.paper_cycle_due(_ct(2026, 6, 13, 9, 0), set()) is None


def test_paper_cycle_due_holiday_none():
    # 2026-07-03 is in _HOLIDAYS.
    assert scheduler.paper_cycle_due(_ct(2026, 7, 3, 9, 0), set()) is None


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


# ── eod_summary_due (~15:10 CT post-close push) ──────────────────────────────
def test_eod_summary_due_fires_at_slot():
    # 2026-06-15 is a Monday.
    assert scheduler.eod_summary_due(_ct(2026, 6, 15, 15, 10), set()) == "close"


def test_eod_summary_due_within_grace():
    assert scheduler.eod_summary_due(_ct(2026, 6, 15, 15, 35), set()) == "close"


def test_eod_summary_due_not_before_or_after():
    assert scheduler.eod_summary_due(_ct(2026, 6, 15, 15, 5), set()) is None   # before slot
    assert scheduler.eod_summary_due(_ct(2026, 6, 15, 15, 45), set()) is None  # past grace


def test_eod_summary_due_once_per_day():
    ran = {("2026-06-15", "close")}
    assert scheduler.eod_summary_due(_ct(2026, 6, 15, 15, 12), ran) is None


def test_eod_summary_due_weekend_and_holiday_none():
    assert scheduler.eod_summary_due(_ct(2026, 6, 13, 15, 10), set()) is None   # Saturday
    assert scheduler.eod_summary_due(_ct(2026, 7, 3, 15, 10), set()) is None    # holiday


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


def test_active_session_date_premarket_holds_prior_session():
    # PRE-market on a trading day (before collection starts at 08:00 CT) → today has
    # no snapshots yet, so the prior session is shown. Tue 2026-06-23 07:00 → Mon
    # 2026-06-22. At 08:00 (the window's first slot) it flips to today.
    assert scheduler.active_session_date(_ct(2026, 6, 23, 0, 0)).isoformat() == "2026-06-22"
    assert scheduler.active_session_date(_ct(2026, 6, 23, 7, 59)).isoformat() == "2026-06-22"
    assert scheduler.active_session_date(_ct(2026, 6, 23, 8, 0)).isoformat() == "2026-06-23"


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


def test_loop_runs_driver_manage_on_5min_slot():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The DRIVER manage+refresh is invoked on the 5-min manage_due slot, under its
    # own guarded block. The MANUAL account is NOT managed on this slot anymore.
    mdue = src.split("m_due, m_slot = manage_due", 1)[1].split("paper_cycle_due", 1)[0]
    assert "run_driver_manage_and_refresh" in mdue
    assert "run_manage_and_refresh" not in mdue   # manual moved off the 5-min slot


def test_loop_runs_manual_paper_cycle_hourly():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The manual Paper Portfolio entry+manage runs on the hourly paper_cycle_due
    # slot via run_paper_entry_and_manage.
    assert "paper_cycle_due" in src and "run_paper_entry_and_manage" in src
    # The hour is latched in paper_ran BEFORE the blocking call (no double-fire).
    seg = src.split("paper_cycle_due", 1)[1].split("run_paper_entry_and_manage", 1)[0]
    assert "paper_ran.add" in seg


def test_loop_wires_eod_summary_branch():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The EOD-summary push is gated by eod_summary_due, latched in eod_summary_ran BEFORE
    # the blocking branch, and invoked via run_eod_summary in its own guarded branch.
    assert "eod_summary_due" in src and "run_eod_summary" in src
    seg = src.split("eod_summary_due", 1)[1].split("run_eod_summary", 1)[0]
    assert "eod_summary_ran.add" in seg


def test_loop_wires_market_snapshot_branch():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The market-snapshot push is gated by market_snapshot_due, latched in
    # market_snapshot_ran BEFORE the blocking branch, and invoked via
    # run_market_snapshot in its own guarded branch.
    assert "market_snapshot_due" in src and "run_market_snapshot" in src
    assert "market_snapshot_ran" in src
    seg = src.split("market_snapshot_due", 1)[1].split("run_market_snapshot", 1)[0]
    assert "market_snapshot_ran.add" in seg


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
    assert scheduler.analyze_slot_due(_ct(2026, 6, 15, 15, 15), set()) == "close"


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


def test_launch_branches_starts_all_when_idle():
    """Idle tick: every due (key, coro) pair is launched as a background task."""
    async def _drive():
        ran = []

        async def mk(name):
            ran.append(name)

        running = {}
        launched = scheduler.launch_branches(
            running, [("a", mk("a")), ("b", mk("b"))], asyncio.create_task)
        assert launched == ["a", "b"]
        await asyncio.gather(*running.values())
        assert sorted(ran) == ["a", "b"]

    asyncio.run(_drive())


def test_launch_branches_skips_branch_still_running():
    """A branch whose PREVIOUS instance is still running is skipped (no stacking)
    — but OTHER branches still launch, so a slow rescan can never stall the
    1-min GEX slot. This is the measured C1 fix: the old gather blocked the whole
    tick until every branch finished, dropping ~37% of collection slots."""
    async def _drive():
        release = asyncio.Event()
        ran = []

        async def slow():
            ran.append("slow")
            await release.wait()

        async def gex():
            ran.append("gex")

        running = {}
        scheduler.launch_branches(running, [("rescan", slow())], asyncio.create_task)
        await asyncio.sleep(0)  # let the slow branch start
        launched = scheduler.launch_branches(
            running, [("rescan", slow()), ("gex", gex())], asyncio.create_task)
        assert launched == ["gex"]         # rescan skipped (still running), gex ran
        release.set()
        await asyncio.gather(*running.values())
        assert ran.count("slow") == 1      # the skipped instance never ran

    asyncio.run(_drive())


def test_launch_branches_relaunches_after_done():
    async def _drive():
        n = {"runs": 0}

        async def branch():
            n["runs"] += 1

        running = {}
        scheduler.launch_branches(running, [("gex", branch())], asyncio.create_task)
        await asyncio.gather(*running.values())
        launched = scheduler.launch_branches(
            running, [("gex", branch())], asyncio.create_task)
        assert launched == ["gex"]
        await asyncio.gather(*running.values())
        assert n["runs"] == 2

    asyncio.run(_drive())


def test_loop_launches_branches_without_blocking_the_tick():
    """The tick loop LAUNCHES its due branches as background tasks (keyed, with a
    still-running skip) instead of gathering them — a slow branch must never
    delay the next tick's slot gates."""
    import inspect
    src = inspect.getsource(scheduler.loop)
    assert "launch_branches" in src
    assert "_gather_due" not in src


# ── market_snapshot_due (:00 & :30, 08:30–15:00 CT) ──────────────────────────
def _at(h, m):  # a trading day (Wed 2026-07-22)
    return dt.datetime(2026, 7, 22, h, m, tzinfo=_CT)


def test_market_snapshot_due_fires_on_the_half_hour():
    assert scheduler.market_snapshot_due(_at(9, 0), set()) == "09:00"
    assert scheduler.market_snapshot_due(_at(9, 30), set()) == "09:30"


def test_market_snapshot_due_boundary_slots():
    assert scheduler.market_snapshot_due(_at(8, 30), set()) == "08:30"   # start fires
    assert scheduler.market_snapshot_due(_at(15, 0), set()) == "15:00"   # end fires


def test_market_snapshot_due_once_per_slot():
    ran = set()
    slot = scheduler.market_snapshot_due(_at(9, 0), ran)
    ran.add(("2026-07-22", slot))
    assert scheduler.market_snapshot_due(_at(9, 2), ran) is None   # within grace, already ran


def test_market_snapshot_due_outside_window_none():
    assert scheduler.market_snapshot_due(_at(7, 0), set()) is None     # before start
    assert scheduler.market_snapshot_due(_at(15, 30), set()) is None   # after end
    assert scheduler.market_snapshot_due(_at(9, 15), set()) is None    # not a :00/:30 slot


def test_market_snapshot_due_skips_weekend():
    sat = dt.datetime(2026, 7, 25, 9, 0, tzinfo=_CT)
    assert scheduler.market_snapshot_due(sat, set()) is None


# ── EOD retrospective: the close slot moved after the cash close ────────────
def test_close_analyze_slot_is_after_the_cash_close():
    from services.options_svc import scheduler as s
    assert s._ANALYZE_SLOTS["close"] == (15, 15)   # was (14, 58) -- pre-close, useless


def test_close_slot_fires_at_1515_not_1458():
    import datetime as dt
    from zoneinfo import ZoneInfo
    from services.options_svc import scheduler as s
    ct = ZoneInfo("America/Chicago")
    # A Tuesday.
    assert s.analyze_slot_due(dt.datetime(2026, 7, 21, 14, 58, tzinfo=ct), set()) != "close"
    assert s.analyze_slot_due(dt.datetime(2026, 7, 21, 15, 15, tzinfo=ct), set()) == "close"
