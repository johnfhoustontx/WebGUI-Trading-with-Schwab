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
    # 2026-07-03 is an NYSE holiday (observed Independence Day).
    due, _ = scheduler.gex_due(_ct(2026, 7, 3, 9, 0), None)
    assert due is False


def test_gex_due_fires_in_gth_on_the_activation_date():
    """From 2026-08-17 the slot gate opens at 06:30 so the GTH session can be
    collected. WHICH symbols get polled is compute's decision, not the gate's."""
    due, _ = scheduler.gex_due(_ct(2026, 8, 17, 7, 0), None)
    assert due is True


def test_gex_due_does_not_fire_in_gth_before_activation():
    """2026-08-14 is the Friday before -- the widening must be inert."""
    due, _ = scheduler.gex_due(_ct(2026, 8, 14, 7, 0), None)
    assert due is False


def test_gex_due_juneteenth():
    # Juneteenth is a full NYSE closure — 2026-06-19 and the next occurrence,
    # 2027-06-18 (observed, since 6/19/2027 is a Saturday), are both holidays.
    assert scheduler.gex_due(_ct(2026, 6, 19, 9, 0), None)[0] is False
    assert scheduler.gex_due(_ct(2027, 6, 18, 9, 0), None)[0] is False


# ── captured_manage_due (captured auto-manage cadence, 5-min) ────────────────
def test_captured_manage_due_first_tick_in_window():
    due, slot = scheduler.captured_manage_due(_ct(2026, 6, 15, 9, 0), None)
    assert due is True and slot is not None


def test_captured_manage_due_not_repeated_within_5min_slot():
    _, slot = scheduler.captured_manage_due(_ct(2026, 6, 15, 9, 0), None)
    due2, _ = scheduler.captured_manage_due(_ct(2026, 6, 15, 9, 3), slot)  # same 5-min slot
    assert due2 is False


def test_captured_manage_due_fires_next_5min_slot():
    _, slot = scheduler.captured_manage_due(_ct(2026, 6, 15, 9, 0), None)
    due, _ = scheduler.captured_manage_due(_ct(2026, 6, 15, 9, 5), slot)  # next slot
    assert due is True


def test_captured_manage_due_before_market_open():
    due, _ = scheduler.captured_manage_due(_ct(2026, 6, 15, 7, 30), None)
    assert due is False


def test_captured_manage_due_weekend():
    due, _ = scheduler.captured_manage_due(_ct(2026, 6, 13, 9, 0), None)  # Saturday
    assert due is False


# ── idea_marks_due (the trade ideas' marks, every [idea_trades] mark_interval_min) ─
def _fired(start, end, interval, day=(2026, 6, 15)):
    """The minutes ``idea_marks_due`` fires on, ticking once a minute like the loop."""
    fired, slot = [], None
    t = _ct(*day, *start)
    stop = _ct(*day, *end)
    while t <= stop:
        due, slot = scheduler.idea_marks_due(t, slot, interval)
        if due:
            fired.append((t.hour, t.minute))
        t += dt.timedelta(minutes=1)
    return fired


def test_idea_marks_due_slots_and_gate():
    # one mark per 5-minute slot, a slot whose first minutes are a burst window
    # marking at its first clear minute instead
    assert [m for _, m in _fired((9, 0), (9, 59), 5)] == [
        1, 6, 10, 15, 21, 25, 31, 36, 40, 45, 51, 55]
    assert scheduler.IDEA_MARK_FORBIDDEN_MINUTES == frozenset(
        {0, 2, 3, 4, 5, 17, 18, 19, 20, 30, 32, 33, 34, 35, 47, 48, 49, 50})
    # a slot that is ALL forbidden is skipped; the next slot catches up
    assert _fired((9, 0), (9, 6), 1) == [(9, 1), (9, 6)]
    # the regular session only: 08:30 is a burst minute, 15:00 is too, and the
    # 5-minute cycle never runs after the close (the 15:05 settle slot does that)
    assert _fired((8, 0), (8, 40), 5) == [(8, 31), (8, 36), (8, 40)]
    assert _fired((14, 50), (16, 0), 5) == [(14, 51), (14, 55)]
    # trading days only
    assert _fired((9, 0), (15, 0), 5, day=(2026, 6, 13)) == []      # Saturday
    assert _fired((9, 0), (15, 0), 5, day=(2026, 6, 19)) == []      # Juneteenth


def test_idea_mark_forbidden_minutes_follow_the_scan_offset(monkeypatch):
    """The scan-burst minutes are derived from ``windows.scan.offset_min``; the
    first minute after the hour and the half hour is forbidden at any offset."""
    shipped = frozenset({0, 2, 3, 4, 5, 17, 18, 19, 20, 30, 32, 33, 34, 35,
                         47, 48, 49, 50})
    assert scheduler.idea_mark_forbidden_minutes(2) == shipped
    monkeypatch.setattr(scheduler.mc, "scan_offset_min", lambda: 2)
    assert scheduler.idea_mark_forbidden_minutes() == shipped
    moved = frozenset({0, 5, 6, 7, 8, 20, 21, 22, 23, 30, 35, 36, 37, 38,
                       50, 51, 52, 53})
    assert scheduler.idea_mark_forbidden_minutes(5) == moved
    assert scheduler.idea_mark_forbidden_minutes(0) == frozenset(
        {0, 1, 2, 3, 15, 16, 17, 18, 30, 31, 32, 33, 45, 46, 47, 48})
    monkeypatch.setattr(scheduler.mc, "scan_offset_min", lambda: 5)
    assert scheduler.idea_mark_forbidden_minutes() == moved
    # and the gate follows the configured offset: 09:02 is clear, 09:05 is not
    assert scheduler.idea_marks_due(_ct(2026, 6, 15, 9, 2), None, 5)[0] is True
    assert scheduler.idea_marks_due(_ct(2026, 6, 15, 9, 5), None, 5)[0] is False
    assert [m for _, m in _fired((9, 0), (9, 29), 5)] == [1, 9, 10, 15, 24, 25]


def test_idea_marks_due_reads_the_interval_it_is_given():
    assert [m for _, m in _fired((10, 0), (10, 59), 15)] == [1, 15, 31, 45]


def test_idea_marks_due_holds_the_slot_on_a_skipped_tick():
    due, slot = scheduler.idea_marks_due(_ct(2026, 6, 15, 9, 0), "LAST", 5)
    assert (due, slot) == (False, "LAST")


def test_the_loop_runs_the_idea_marks_without_the_autoclose_switch():
    """The idea branch is gated by its own ``enabled`` (inside ``mark_cycle``), never
    by the captured auto-close toggle."""
    import inspect
    src = inspect.getsource(scheduler.loop)
    start = src.index("idea_marks_due(")
    block = src[start:src.index('branches.append(("idea_marks"', start)]
    assert "run_idea_marks_and_publish" in block
    assert "autoclose" not in block


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
    # 2026-07-03 is an NYSE holiday (observed Independence Day).
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


def test_active_session_date_is_year_general_past_2027():
    """Regression: ``active_session_date``/``_prev_trading_day`` used to test
    membership against the shared calendar's bounded 2026-27 ``HOLIDAYS`` set
    while ``_is_trading_day`` was year-general — so the SAME module disagreed
    with itself about a 2028 holiday. MLK Day 2028-01-17 is a Monday: the
    display must hold Friday 2028-01-14, not flip to the closed Monday."""
    assert scheduler._is_trading_day(_ct(2028, 1, 17, 12, 0)) is False
    assert scheduler.active_session_date(_ct(2028, 1, 17, 9, 0)).isoformat() == "2028-01-14"
    # ...and the prior-day walk skips it from the following trading day too.
    assert scheduler.active_session_date(_ct(2028, 1, 18, 7, 0)).isoformat() == "2028-01-14"
    # Non-vacuity: an ordinary 2028 trading day still resolves to itself.
    assert scheduler.active_session_date(_ct(2028, 1, 18, 9, 0)).isoformat() == "2028-01-18"


def test_active_session_date_premarket_holds_prior_session():
    # PRE-market on a trading day (before collection starts at 08:00 CT) → today has
    # no snapshots yet, so the prior session is shown. Tue 2026-06-23 07:00 → Mon
    # 2026-06-22. At 08:00 (the window's first slot) it flips to today.
    assert scheduler.active_session_date(_ct(2026, 6, 23, 0, 0)).isoformat() == "2026-06-22"
    assert scheduler.active_session_date(_ct(2026, 6, 23, 7, 59)).isoformat() == "2026-06-22"
    assert scheduler.active_session_date(_ct(2026, 6, 23, 8, 0)).isoformat() == "2026-06-23"


def test_session_flip_is_independent_of_collection_start():
    """Widening GTH collection to 06:30 must NOT move the Gamma display flip.
    The flip stays at 08:00 -- see hazard H2 in the ETH design doc."""
    ct = ZoneInfo("America/Chicago")
    # 07:00 CT on the ETH activation day: eligible symbols may be collecting,
    # but the display still shows the prior session.
    assert scheduler.active_session_date(dt.datetime(2026, 8, 17, 7, 0, tzinfo=ct)) == dt.date(2026, 8, 14)
    assert scheduler.active_session_date(dt.datetime(2026, 8, 17, 8, 0, tzinfo=ct)) == dt.date(2026, 8, 17)


def test_session_flip_is_not_vacuously_independent_of_collection():
    """Power check for the test above: on the activation date collection really
    HAS widened to 06:30 for an eligible symbol, so 07:00 CT is a moment when the
    two genuinely disagree -- collecting, but still displaying yesterday."""
    from shared import market_calendar as mc

    now = dt.datetime(2026, 8, 17, 7, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert mc.in_collection_window(now, eth_eligible=True) is True
    assert scheduler.active_session_date(now) == dt.date(2026, 8, 14)


def test_loop_refreshes_gamma_after_collection():
    import inspect

    src = inspect.getsource(scheduler.loop)
    # The GEX-collection branch also republishes the current gamma snapshot, so the
    # heatmap stays fresh server-side with no page open. It runs AFTER collection.
    assert "refresh_gamma_current" in src
    seg = src.split("collect_gex_history", 1)[1]
    assert "refresh_gamma_current" in seg


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
    # 2026-12-25 is an NYSE holiday (Christmas).
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


# ── autoscan: each scan starts a set number of minutes after its quarter hour ──
#
# Measured on prod over three sessions (2026-10-02, 10-05, 10-06): Schwab's
# "429 Too Many Requests" fell in the first minute after the hour and half hour
# and almost never at :15 or :45, although the scan's own burst is the same at
# every quarter hour (Friday: 4,944 requests in the first two minutes of the
# twelve hour/half-hour scans, 5,051 in the twelve quarter scans, and all 41
# refusals in the first group). So the scan waits `windows.scan.offset_min`.

def _scan_at(monkeypatch, offset):
    monkeypatch.setattr(scheduler.mc, "scan_offset_min", lambda: offset)


def _slot_of(h, m):
    return scheduler._slot_key(_ct(2026, 6, 15, h, m))


def test_with_no_offset_a_scan_is_due_on_the_quarter_hour(monkeypatch):
    _scan_at(monkeypatch, 0)
    due, slot = scheduler.autoscan_due(_ct(2026, 6, 15, 9, 0), _slot_of(8, 45))
    assert due is True and slot == _slot_of(9, 0)


def test_a_scan_waits_for_its_offset(monkeypatch):
    _scan_at(monkeypatch, 2)
    last = _slot_of(8, 45)
    for minute in (0, 1):
        due, slot = scheduler.autoscan_due(_ct(2026, 6, 15, 9, minute), last)
        assert due is False and slot == last
    due, slot = scheduler.autoscan_due(_ct(2026, 6, 15, 9, 2), last)
    assert due is True and slot == _slot_of(9, 0)      # still the 09:00 slot, started late


def test_an_offset_scan_runs_once_per_slot(monkeypatch):
    _scan_at(monkeypatch, 2)
    _due, slot = scheduler.autoscan_due(_ct(2026, 6, 15, 9, 2), _slot_of(8, 45))
    for minute in (3, 14, 15, 16):
        assert scheduler.autoscan_due(_ct(2026, 6, 15, 9, minute), slot)[0] is False
    due, nxt = scheduler.autoscan_due(_ct(2026, 6, 15, 9, 17), slot)
    assert due is True and nxt == _slot_of(9, 15)


def test_an_offset_keeps_the_same_scans_at_both_ends_of_the_window(monkeypatch):
    # The window is 08:00-15:15 CT. Every slot it held still runs, each two
    # minutes later: the first at 08:02, the last (the 15:15 slot) at 15:17.
    _scan_at(monkeypatch, 2)
    assert scheduler.autoscan_due(_ct(2026, 6, 15, 8, 0), None)[0] is False
    assert scheduler.autoscan_due(_ct(2026, 6, 15, 8, 1), None)[0] is False
    due, first = scheduler.autoscan_due(_ct(2026, 6, 15, 8, 2), None)
    assert due is True and first == _slot_of(8, 0)
    before_last = _slot_of(15, 0)
    assert scheduler.autoscan_due(_ct(2026, 6, 15, 15, 16), before_last)[0] is False
    due, last = scheduler.autoscan_due(_ct(2026, 6, 15, 15, 17), before_last)
    assert due is True and last == _slot_of(15, 15)
    for minute in (18, 29, 31, 32):
        assert scheduler.autoscan_due(_ct(2026, 6, 15, 15, minute), last)[0] is False


def test_the_number_of_scans_in_a_day_does_not_depend_on_the_offset(monkeypatch):
    def scans(offset):
        _scan_at(monkeypatch, offset)
        last, ran = None, []
        t = _ct(2026, 6, 15, 7, 30)
        while t.hour < 16:
            due, slot = scheduler.autoscan_due(t, last)
            if due:
                last = slot
                ran.append((t.hour, t.minute))
            t += dt.timedelta(seconds=30)
        return ran
    plain, shifted = scans(0), scans(2)
    assert len(plain) == len(shifted) == 30
    assert plain[0] == (8, 0) and shifted[0] == (8, 2)
    assert all(m % 15 == 0 for _h, m in plain) and all(m % 15 == 2 for _h, m in shifted)


def test_a_restart_inside_a_slot_still_scans_at_once(monkeypatch):
    # No remembered slot: the service has just started. It scans now, as before.
    _scan_at(monkeypatch, 2)
    assert scheduler.autoscan_due(_ct(2026, 6, 15, 10, 7), None)[0] is True


def test_an_offset_scan_still_skips_weekends_and_holidays(monkeypatch):
    _scan_at(monkeypatch, 2)
    assert scheduler.autoscan_due(_ct(2026, 6, 13, 9, 2), None)[0] is False   # Saturday
    assert scheduler.autoscan_due(_ct(2026, 1, 1, 9, 2), None)[0] is False    # holiday


def test_the_scan_reads_the_real_setting():
    # Not patched: whatever config/sessions.toml ships is what the scan obeys.
    offset = scheduler.mc.scan_offset_min()
    last = _slot_of(8, 45)
    assert scheduler.autoscan_due(_ct(2026, 6, 15, 9, offset), last)[0] is True
    if offset:
        assert scheduler.autoscan_due(_ct(2026, 6, 15, 9, offset - 1), last)[0] is False


# ── Opening Scalp nightly bar top-up ([slots.scalp_bars]) ───────────────────
def test_scalp_bars_due_once_per_day_inside_grace():
    ran = set()
    assert scheduler.scalp_bars_due(_ct(2026, 6, 15, 16, 19), ran) is None   # before 16:20
    slot = scheduler.scalp_bars_due(_ct(2026, 6, 15, 16, 20), ran)
    assert slot == "close"
    ran.add(("2026-06-15", slot))
    assert scheduler.scalp_bars_due(_ct(2026, 6, 15, 16, 21), ran) is None   # latched
    # a late start inside the 120-minute grace still runs it; past it, never
    assert scheduler.scalp_bars_due(_ct(2026, 6, 15, 18, 19), set()) == "close"
    assert scheduler.scalp_bars_due(_ct(2026, 6, 15, 18, 20), set()) is None


def test_scalp_bars_due_skips_weekends_and_holidays():
    assert scheduler.scalp_bars_due(_ct(2026, 6, 13, 16, 30), set()) is None  # Saturday
    assert scheduler.scalp_bars_due(_ct(2026, 1, 1, 16, 30), set()) is None   # holiday


# ── Opening Scalp weekly refit of the chance's weights ([slots.scalp_prob]) ──
def test_scalp_prob_due_only_on_the_refit_weekday():
    # 2026-06-13 is a Saturday, 2026-06-12 a Friday. NOT gated on a trading day.
    ran = set()
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 7, 59), ran) is None    # before 08:00
    slot = scheduler.scalp_prob_due(_ct(2026, 6, 13, 8, 5), ran)
    assert slot == "refit"
    assert scheduler.scalp_prob_due(_ct(2026, 6, 12, 8, 5), ran) is None     # Friday
    assert scheduler.scalp_prob_due(_ct(2026, 6, 15, 8, 5), ran) is None     # Monday
    # a late start inside the 180-minute grace still runs it; past it, never
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 10, 59), set()) == "refit"
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 11, 0), set()) is None
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 11, 30), set()) is None
    # latched once run
    ran.add(("2026-06-13", slot))
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 8, 6), ran) is None
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 10, 59), ran) is None


def test_scalp_prob_due_follows_the_configured_weekday(monkeypatch):
    monkeypatch.setattr(scheduler.scalp_config, "prob_refit_weekday", lambda: 6)
    assert scheduler.scalp_prob_due(_ct(2026, 6, 14, 8, 5), set()) == "refit"   # Sunday
    assert scheduler.scalp_prob_due(_ct(2026, 6, 13, 8, 5), set()) is None      # Saturday


def test_scalp_prob_branch_latches_before_it_runs_and_logs_the_refit():
    import inspect

    src = inspect.getsource(scheduler.loop)
    assert "scalp_prob_ran = set()" in src
    assert "scalp_prob_due(now, scalp_prob_ran)" in src
    assert "run_in_executor(None, scalp_prob_fit.refit)" in src
    latch = src.index("scalp_prob_ran.add(")
    run = src.index("run_in_executor(None, scalp_prob_fit.refit)")
    assert latch < run
    assert "scalp prob refit degraded" in src


def test_scalp_bars_branch_rebuilds_the_study_after_the_top_up_independently():
    import inspect

    src = inspect.getsource(scheduler.loop)
    seg = src.split("async def _scalp_bars_branch", 1)[1].split("if bars_slot:", 1)[0]
    # The rebuild comes after the top-up, in the executor, in its OWN try: a
    # failed top-up must not stop it, and a failed rebuild must not hide the top-up.
    assert seg.index("scalp_fetch.top_up") < seg.index("scalp_study.rebuild")
    between = seg.split("scalp_fetch.top_up", 1)[1].split("scalp_study.rebuild", 1)[0]
    assert "except Exception" in between and "try:" in between
    assert "run_in_executor(None, scalp_study.rebuild)" in seg


def test_scalp_bars_branch_grades_after_the_rebuild_independently():
    import inspect

    src = inspect.getsource(scheduler.loop)
    seg = src.split("async def _scalp_bars_branch", 1)[1].split("if bars_slot:", 1)[0]
    # top_up -> rebuild -> grade, each in its OWN try so one failing never hides
    # the others (a failed rebuild still grades the days whose bars are in).
    assert (seg.index("scalp_fetch.top_up") < seg.index("scalp_study.rebuild")
            < seg.index("scalp_grade.grade_pending"))
    between = seg.split("scalp_study.rebuild", 1)[1].split("scalp_grade.grade_pending", 1)[0]
    assert "except Exception" in between and "try:" in between
    assert "run_in_executor(None, scalp_grade.grade_pending)" in seg


def test_scalp_bars_branch_grades_the_intraday_calls_after_the_0836_grading_independently():
    import inspect

    src = inspect.getsource(scheduler.loop)
    seg = src.split("async def _scalp_bars_branch", 1)[1].split("if bars_slot:", 1)[0]
    # 08:36 grading -> intraday grading -> scorecard republish, the intraday one in
    # the executor and in its OWN try: a failed 08:36 grading still grades the slot
    # calls, and a failed intraday grading still republishes the scorecard.
    assert "run_in_executor(None, scalp_intraday_grade.grade_pending)" in seg
    assert (seg.index("scalp_grade.grade_pending")
            < seg.index("scalp_intraday_grade.grade_pending")
            < seg.index("scalp_call.republish_scorecard"))
    between = seg.split("scalp_grade.grade_pending", 1)[1].split(
        "scalp_intraday_grade.grade_pending", 1)[0]
    assert "except Exception" in between and "try:" in between
    between = seg.split("scalp_intraday_grade.grade_pending", 1)[1].split(
        "scalp_call.republish_scorecard", 1)[0]
    assert "except Exception" in between and "try:" in between


def test_scalp_bars_branch_republishes_the_scorecard_after_grading_independently():
    import inspect

    src = inspect.getsource(scheduler.loop)
    seg = src.split("async def _scalp_bars_branch", 1)[1].split("if bars_slot:", 1)[0]
    # grade -> republish the view's scorecard, in its OWN try: a failed grading
    # still republishes what is graded, and a failed republish hides nothing.
    assert (seg.index("scalp_grade.grade_pending")
            < seg.index("scalp_call.republish_scorecard"))
    between = seg.split("scalp_grade.grade_pending", 1)[1].split(
        "scalp_call.republish_scorecard", 1)[0]
    assert "except Exception" in between and "try:" in between
    assert "run_in_executor(None, scalp_call.republish_scorecard, bus)" in seg
    tail = seg.split("scalp_call.republish_scorecard", 1)[1]
    assert "except Exception" in tail


def test_scalp_bars_branch_rebuilds_the_intraday_study_after_the_0836_one_independently():
    import inspect

    src = inspect.getsource(scheduler.loop)
    seg = src.split("async def _scalp_bars_branch", 1)[1].split("if bars_slot:", 1)[0]
    # The intraday study runs AFTER the 08:36 rebuild, in the executor, in its OWN
    # try: a failed 08:36 rebuild must not stop it, and a failed (or slow)
    # intraday rebuild must not hide or hold up anything before it.
    assert "run_in_executor(None, scalp_intraday_study.rebuild)" in seg
    assert seg.index("scalp_study.rebuild") < seg.index("scalp_intraday_study.rebuild")
    between = seg.split("scalp_study.rebuild", 1)[1].split(
        "scalp_intraday_study.rebuild", 1)[0]
    assert "except Exception" in between and "try:" in between
    tail = seg.split("scalp_intraday_study.rebuild", 1)[1]
    assert "except Exception" in tail
    # It runs last: the grading and the scorecard never wait on the longer study.
    assert seg.index("scalp_call.republish_scorecard") < \
        seg.index("scalp_intraday_study.rebuild")


def test_scalp_bars_slot_comes_after_the_flow_delta_report_and_collection_stop():
    # The slot is configured in sessions.toml; pin the ordering its comment argues.
    (name, t), = scheduler.mc.slot_times("scalp_bars").items()
    assert (name, (t.hour, t.minute)) == ("close", (16, 20))
    assert scheduler.mc.slot_grace_min("scalp_bars") == 120


# ── Opening Scalp intraday recording ([intraday] slots) ─────────────────────
def test_scalp_intraday_due_fires_each_slot_once():
    from shared import scalp_config

    day = _ct(2026, 6, 15, 8, 0).date()
    slots = scalp_config.intraday_slots(day)
    assert slots[0] == "08:51" and slots[-1] == "13:51" and len(slots) == 21
    ran = set()
    assert scheduler.scalp_intraday_due(_ct(2026, 6, 15, 8, 50), ran) is None
    fired = []
    for hh, mm in ((8, 51), (9, 6), (13, 51)):
        slot = scheduler.scalp_intraday_due(_ct(2026, 6, 15, hh, mm), ran)
        assert slot == f"{hh:02d}:{mm:02d}"
        ran.add(("2026-06-15", slot))
        fired.append(slot)
        # latched: the same minute and the next never refire it
        assert scheduler.scalp_intraday_due(_ct(2026, 6, 15, hh, mm + 1), ran) is None
    # a late tick inside the 5-minute grace still fires; past it, never
    assert scheduler.scalp_intraday_due(_ct(2026, 6, 15, 9, 10), set()) == "09:06"
    assert scheduler.scalp_intraday_due(_ct(2026, 6, 15, 9, 11), set()) is None
    assert scheduler.scalp_intraday_due(_ct(2026, 6, 15, 13, 57), set()) is None   # after the end


def test_not_on_a_holiday():
    assert scheduler.scalp_intraday_due(_ct(2026, 6, 13, 9, 6), set()) is None   # Saturday
    assert scheduler.scalp_intraday_due(_ct(2026, 1, 1, 9, 6), set()) is None    # holiday


def test_the_intraday_branch_records_in_its_own_try():
    # Task 8 of the intraday calls: the branch now runs the slot's call, which
    # records the slot itself (after publishing, with each call's direction).
    import inspect

    src = inspect.getsource(scheduler.loop)
    seg = src.split("async def _scalp_intraday_branch", 1)[1].split("if intraday_slot:", 1)[0]
    assert "run_in_executor(None, scalp_intraday_call.run_scheduled, bus, slot, at)" in seg
    assert "except Exception" in seg
    assert 'branches.append(("scalp_intraday"' in src
