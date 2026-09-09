"""Tests for the once-daily income-window scan gate.

``income_slot_due`` mirrors ``action_alert_due`` — the house pattern for a
once-per-trading-day slot with a grace window. The cadence is deliberate rather
than incidental: a 35-DTE candidate does not meaningfully re-rank inside fifteen
minutes, so running this on the autoscan's cadence would buy nothing and cost
~690 extra ``/chains`` calls a day instead of ~23.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from shared import market_calendar as mc

from services.options_svc import scheduler

_CT = ZoneInfo("America/Chicago")


def _ct(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=_CT)


# ── the config resolves at all ───────────────────────────────────────────────
# This is the test most likely to be skipped and the one that matters most.
# ``scheduler`` reads ``mc.slot_times("income")`` at MODULE level, and
# ``_slot_group`` does a bare ``_DEFAULTS["slots"][name]`` — so a slot name
# missing from the built-in defaults raises KeyError at IMPORT, i.e. a hard
# service-startup failure rather than a degraded tick. The TOML only overrides;
# the defaults are the real values.
def test_the_income_slot_group_resolves_from_the_builtin_defaults():
    times = mc.slot_times("income")
    assert times, "slots.income must resolve — a KeyError here is a startup crash"
    assert all(isinstance(v, dt.time) for v in times.values())
    # grace_min shares the table with the times and must never be read as one
    assert "grace_min" not in times
    assert isinstance(mc.slot_grace_min("income"), int)


def test_the_scheduler_agrees_with_the_calendar_on_the_income_slots():
    """The gate's targets ARE the config's — not a second copy that can drift."""
    assert scheduler._INCOME_SLOTS == {
        k: (t.hour, t.minute) for k, t in mc.slot_times("income").items()}
    assert scheduler._INCOME_GRACE_MIN == mc.slot_grace_min("income")


# ── income_slot_due ──────────────────────────────────────────────────────────
# 2026-09-08 is a Tuesday (a normal trading day).
def test_income_slot_fires_at_its_target():
    assert scheduler.income_slot_due(_ct(2026, 9, 8, 8, 45), set()) == "morning"


def test_income_slot_fires_within_grace():
    """The grace tolerates a missed 30 s tick or a mid-window service start."""
    assert scheduler.income_slot_due(_ct(2026, 9, 8, 9, 0), set()) == "morning"


def test_income_slot_fires_once_per_trading_day():
    ran = set()
    slot = scheduler.income_slot_due(_ct(2026, 9, 8, 8, 45), ran)
    assert slot == "morning"
    ran.add(("2026-09-08", slot))
    assert scheduler.income_slot_due(_ct(2026, 9, 8, 8, 50), ran) is None


def test_income_slot_does_not_backfill_a_long_stale_slot():
    """Grace tolerates a missed tick; it must NOT fire hours late — a 13:30
    firing would publish a board stamped as the morning's read."""
    assert scheduler.income_slot_due(_ct(2026, 9, 8, 13, 30), set()) is None


def test_income_slot_is_silent_before_its_target():
    assert scheduler.income_slot_due(_ct(2026, 9, 8, 8, 0), set()) is None


def test_income_slot_is_silent_on_a_weekend():
    # 2026-09-05 is a Saturday.
    assert scheduler.income_slot_due(_ct(2026, 9, 5, 8, 45), set()) is None


def test_income_slot_is_silent_on_a_holiday():
    # 2026-07-03 is an NYSE holiday (observed Independence Day).
    assert scheduler.income_slot_due(_ct(2026, 7, 3, 8, 45), set()) is None
