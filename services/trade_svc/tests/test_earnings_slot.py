"""Tests for the nightly earnings-calendar refresh slot.

``trade_svc`` had no scheduler at all: the calendar was pulled LAZILY inside
``compute._refresh_earnings_calendar``, reached only from ``_enrich_earnings_date``
during a Trade Analyzer *analyze* -- a user action. So the store went stale
whenever nobody opened that page, and it is no longer only that page's store:
the 30-45 DTE income window reads it through ``shared/earnings.py`` at 08:45 CT
every trading morning, and vendor coverage decays with distance (measured on a
live key: ~2,125 rows for the current month against 11 nine months out).

⚠ **No test here may make a live vendor call.** ``fetch_calendar`` refuses
without a key and the repo-root conftest refuses ``sqlite3.connect`` into a live
data dir, but both are backstops: every test below stubs the seam it exercises
outright, so a test cannot start passing for the wrong reason if either backstop
is ever relaxed.
"""
import asyncio
import datetime as dt
import threading
from zoneinfo import ZoneInfo

from shared import market_calendar as mc

from services.trade_svc import compute, scheduler

_CT = ZoneInfo("America/Chicago")


def _ct(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=_CT)


# ── the config resolves at all ───────────────────────────────────────────────
# The test most likely to look redundant and the one that matters most.
# ``scheduler`` reads ``mc.slot_times("earnings")`` at MODULE level, and
# ``_slot_group`` does a bare ``_DEFAULTS["slots"][name]`` -- so a slot name
# living only in sessions.toml raises KeyError at IMPORT: a hard service-startup
# failure, not a degraded tick. The defaults are the real values; the TOML only
# overrides.
def test_the_earnings_slot_group_resolves_from_the_builtin_defaults():
    times = mc.slot_times("earnings")
    assert times, "slots.earnings must resolve — a KeyError here is a startup crash"
    assert all(isinstance(v, dt.time) for v in times.values())
    # grace_min shares the table with the times and must never be read as one
    assert "grace_min" not in times
    assert isinstance(mc.slot_grace_min("earnings"), int)


def test_the_scheduler_agrees_with_the_calendar_on_the_slot():
    """The gate's target IS the config's — not a second copy free to drift."""
    at = mc.slot_times("earnings")["at"]
    assert scheduler._EARNINGS_AT == (at.hour, at.minute)
    assert scheduler._EARNINGS_GRACE_MIN == mc.slot_grace_min("earnings")


def test_the_slot_lands_in_the_evening_between_its_two_neighbours():
    """The three constraints that picked the time, asserted rather than assumed.

    After the day's other nightly jobs (so three services are not pulling at the
    same minute), and before the 08:45 CT income scan that consumes the store —
    which, because that scan is the NEXT morning, means simply "in the evening"."""
    at = mc.slot_times("earnings")["at"]
    assert at > mc.slot_times("calibration")["at"]   # 16:30
    assert at > mc.slot_times("momentum")["at"]      # 16:20
    assert at > mc.slot_times("income")["morning"]   # 08:45 — same-day ordering
    # and the whole grace window is still inside the same evening, so it can
    # never reach across midnight into the morning it is feeding.
    assert (dt.datetime.combine(dt.date(2026, 9, 8), at)
            + dt.timedelta(minutes=mc.slot_grace_min("earnings"))).date() == dt.date(2026, 9, 8)


# ── earnings_slot_due ────────────────────────────────────────────────────────
# 2026-09-08 is a Tuesday (a normal trading day).
def test_earnings_slot_fires_at_its_target():
    assert scheduler.earnings_slot_due(_ct(2026, 9, 8, 20, 0), set()) == "at"


def test_earnings_slot_fires_within_grace():
    """The grace tolerates a missed tick or a mid-evening service restart."""
    assert scheduler.earnings_slot_due(_ct(2026, 9, 8, 20, 59), set()) == "at"


def test_earnings_slot_fires_once_per_day():
    ran = set()
    slot = scheduler.earnings_slot_due(_ct(2026, 9, 8, 20, 0), ran)
    assert slot == "at"
    ran.add(("2026-09-08", slot))
    assert scheduler.earnings_slot_due(_ct(2026, 9, 8, 20, 30), ran) is None


def test_the_next_day_is_a_new_firing():
    """The positive twin of the once-per-day negative above: the sentinel is
    keyed on the DATE, so yesterday's entry must not silence today."""
    ran = {("2026-09-08", "at")}
    assert scheduler.earnings_slot_due(_ct(2026, 9, 9, 20, 0), ran) == "at"


def test_earnings_slot_does_not_backfill_a_long_stale_slot():
    """A service started at 03:00 must not immediately spend the day's vendor
    request on a slot that belonged to the previous evening — the pull would be
    stamped as that evening's read and the real one would then be skipped."""
    assert scheduler.earnings_slot_due(_ct(2026, 9, 9, 3, 0), set()) is None


def test_earnings_slot_is_silent_before_its_target():
    assert scheduler.earnings_slot_due(_ct(2026, 9, 8, 19, 59), set()) is None


# ── the deliberate divergence: this slot ignores the trading calendar ─────────
# Every other slot gate in the repo opens with ``if not _is_trading_day(now)``.
# This one does not, and that is a decision rather than an oversight:
#
#   * The subject is not market data. Alpha Vantage's file is a research product
#     that changes whenever an issuer announces or moves a date, and those
#     changes accumulate over a weekend exactly as they do overnight. There is
#     no "the market was closed, so there is nothing new" here.
#   * The consumer is a Monday morning. Gated on the trading calendar, the 08:45
#     CT Monday income scan would read a calendar pulled Friday at 20:00 — ~61 h
#     old — where firing on Sunday evening makes it ~13 h old. The holiday case
#     is worse and less obvious: skipping Thanksgiving Thursday leaves Friday's
#     (half-day, still a trading day) scan reading Wednesday's pull.
#   * It is free. One bulk request against a 25-a-day allowance, so the whole
#     cost of the divergence is two extra requests a week out of ~175.
def test_earnings_slot_fires_on_a_weekend_by_design():
    # 2026-09-05 is a Saturday; 2026-09-06 a Sunday.
    assert scheduler.earnings_slot_due(_ct(2026, 9, 5, 20, 0), set()) == "at"
    assert scheduler.earnings_slot_due(_ct(2026, 9, 6, 20, 0), set()) == "at"


def test_earnings_slot_fires_on_a_holiday_by_design():
    # 2026-07-03 is an NYSE holiday (observed Independence Day). Its evening is
    # the last chance to refresh before Monday's income scan.
    assert not mc.is_trading_day(dt.date(2026, 7, 3)), "fixture is not a holiday"
    assert scheduler.earnings_slot_due(_ct(2026, 7, 3, 20, 0), set()) == "at"


# ── the loop ─────────────────────────────────────────────────────────────────

def _drive(monkeypatch, clock, fake_refresh, ticks=40):
    """Run the scheduler loop against a stubbed clock + refresh, then cancel it.

    ``clock`` is a one-element list holding the datetime ``_market_now`` reports,
    so a test can advance time from inside its stubbed refresh."""
    monkeypatch.setattr(scheduler, "TICK_SEC", 0.001)
    monkeypatch.setattr(scheduler, "_market_now", lambda: clock[0])
    # Patched on the MODULE the scheduler resolves at call time, so this really
    # replaces the vendor path rather than shadowing a local alias.
    monkeypatch.setattr(compute, "refresh_earnings_calendar", fake_refresh)

    async def _go():
        task = asyncio.create_task(scheduler.loop(None))
        for _ in range(ticks):
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_go())


def test_the_refresh_runs_off_the_event_loop(monkeypatch):
    """The vendor call is blocking HTTP plus a bulk upsert of thousands of rows.

    Run inline it would stall the loop — and this service's real job is the
    on-demand analyze the command consumer shares that loop with."""
    seen = {}

    def _fake():
        seen["thread"] = threading.get_ident()
        return True

    _drive(monkeypatch, [_ct(2026, 9, 8, 20, 0)], _fake)

    assert "thread" in seen, "the slot never fired"
    assert seen["thread"] != threading.get_ident(), (
        "the blocking vendor call ran on the event-loop thread")


def test_a_vendor_failure_does_not_kill_the_loop(monkeypatch):
    """The service must survive an Alpha Vantage outage: its actual job is
    on-demand analysis, and a dead scheduler task would also stop every LATER
    night's refresh, turning one bad evening into an indefinitely stale store."""
    clock = [_ct(2026, 9, 8, 20, 0)]
    calls = []

    def _fake():
        calls.append(len(calls))
        if len(calls) == 1:
            # Advance to the next evening so a surviving loop has something to
            # do — the day sentinel would otherwise silence it forever.
            clock[0] = _ct(2026, 9, 9, 20, 0)
            raise RuntimeError("vendor exploded")
        return True

    _drive(monkeypatch, clock, _fake)

    assert len(calls) >= 2, f"loop died after the first failure (calls={len(calls)})"


def test_the_loop_is_quiet_when_no_slot_is_due(monkeypatch):
    """The positive twin's negative: the two tests above prove the loop CAN
    call the refresh, so this one proving it does not is not vacuous."""
    calls = []
    _drive(monkeypatch, [_ct(2026, 9, 8, 12, 0)],   # midday, nowhere near 20:00
           lambda: calls.append(1))
    assert calls == []


# ── the refresh entry point ─────────────────────────────────────────────────
# ``compute.refresh_earnings_calendar`` deliberately goes through the SAME
# ``_refresh_earnings_calendar`` day latch the lazy analyze path uses, rather
# than calling ``earnings_calendar.refresh`` directly. The free tier allows 25
# requests a day and the bulk CSV needs one; the schedule's job is to guarantee
# that one pull happens, not to add a second.

def _stub_vendor(monkeypatch, rows, calls):
    """Replace the network seam itself, so nothing can reach Alpha Vantage."""
    from services.trade_svc import earnings_calendar as ec

    def _fake_fetch(horizon=ec.HORIZON):
        calls.append(horizon)
        return rows

    monkeypatch.setattr(ec, "fetch_calendar", _fake_fetch)


def test_the_scheduled_refresh_writes_the_calendar_to_its_store(monkeypatch, tmp_path):
    from services.trade_svc import earnings_calendar as ec
    from shared import earnings as shared_earnings

    db = tmp_path / "ec.db"
    monkeypatch.setattr(compute, "_earnings_db_path", lambda: db)
    monkeypatch.setattr(compute, "_EC_REFRESH_DAY", [None])
    soon = (dt.date.today() + dt.timedelta(days=9)).isoformat()
    calls = []
    _stub_vendor(monkeypatch, [{"symbol": "AAPL", "report_date": soon,
                                "fiscal_date_ending": "", "estimate": None}], calls)

    assert compute.refresh_earnings_calendar() is True
    assert calls == [ec.HORIZON], "the pull did not happen"

    conn = shared_earnings.init_db(db)
    try:
        assert shared_earnings.days_to_earnings(conn, "AAPL") == 9
    finally:
        shared_earnings.close_db(conn)


def test_the_scheduled_pull_and_a_users_analyze_share_one_request_a_day(monkeypatch, tmp_path):
    """The reason for reusing the lazy latch instead of calling ``refresh``.

    Two pulls on one CT date would spend two of the vendor's 25 for a file that
    is regenerated daily -- and the second would be the one that fails when the
    per-symbol EPS budget has already eaten into the allowance."""
    db = tmp_path / "ec.db"
    monkeypatch.setattr(compute, "_earnings_db_path", lambda: db)
    monkeypatch.setattr(compute, "_EC_REFRESH_DAY", [None])
    calls = []
    _stub_vendor(monkeypatch, [], calls)

    assert compute.refresh_earnings_calendar() is True
    assert len(calls) == 1
    # ... and the analyze path, reaching the same latch, adds nothing.
    conn = None
    try:
        from services.trade_svc import earnings_calendar as ec
        conn = ec.init_db(db)
        compute._refresh_earnings_calendar(conn)
    finally:
        if conn is not None:
            conn.close()
    assert len(calls) == 1, "a second vendor request was spent on the same day"


def test_a_new_day_pulls_again(monkeypatch, tmp_path):
    """The positive twin of the latch test above -- without it, a latch that
    never released would satisfy that assertion perfectly."""
    db = tmp_path / "ec.db"
    monkeypatch.setattr(compute, "_earnings_db_path", lambda: db)
    monkeypatch.setattr(compute, "_EC_REFRESH_DAY", ["1999-01-01"])
    calls = []
    _stub_vendor(monkeypatch, [], calls)

    assert compute.refresh_earnings_calendar() is True
    assert len(calls) == 1


def test_the_scheduled_refresh_degrades_rather_than_raising(monkeypatch):
    """A store it cannot open must return False, not propagate -- the loop's own
    guard is a backstop, not the contract."""
    monkeypatch.setattr(compute, "_earnings_db_path",
                        lambda: (_ for _ in ()).throw(OSError("no store")))
    assert compute.refresh_earnings_calendar() is False


def test_the_scheduled_refresh_is_skipped_under_pytest_by_default(monkeypatch):
    """Same isolation rule as ``_enrich_earnings_date``: unpatched it would open
    a SQLite file in the repo AND issue a live vendor request during the suite.

    Asserted as "skipped SILENTLY", not merely "returned False" -- the repo-root
    conftest would refuse the connect anyway, so a False alone proves nothing
    about the guard. What separates a deliberate skip from a crash caught by the
    backstop is that the skip does not degrade: dropping the guard would put a
    WARNING traceback and a degrade-counter bump into every run of this suite."""
    from services import _degrade

    seen = []
    monkeypatch.setattr(_degrade, "degraded",
                        lambda area, **kw: seen.append(area))
    assert compute.refresh_earnings_calendar() is False
    assert seen == [], f"skipped by crashing into the backstop, not by the guard: {seen}"
