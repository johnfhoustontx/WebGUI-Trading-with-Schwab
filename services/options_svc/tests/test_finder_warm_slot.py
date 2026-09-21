"""The public Strategy Finder's morning warm-up slot."""
import datetime as dt
import pathlib
from zoneinfo import ZoneInfo

from services.options_svc import scheduler

CT = ZoneInfo("America/Chicago")


def _at(h, m, day=21):
    return dt.datetime(2026, 9, day, h, m, tzinfo=CT)       # 21st is a Monday


def test_it_fires_once_at_its_slot_on_a_trading_day():
    ran = set()
    assert scheduler.finder_warm_due(_at(9, 7), ran) is None
    slot = scheduler.finder_warm_due(_at(9, 8), ran)
    assert slot == "warm"
    ran.add(("2026-09-21", slot))
    assert scheduler.finder_warm_due(_at(9, 9), ran) is None


def test_it_does_not_backfill_a_stale_slot():
    assert scheduler.finder_warm_due(_at(10, 30), set()) is None


def test_it_does_not_fire_on_a_weekend():
    assert scheduler.finder_warm_due(_at(9, 8, day=19), set()) is None


def test_the_branch_queues_through_the_worker_never_scans_directly():
    src = pathlib.Path(scheduler.__file__).read_text(encoding="utf-8")
    assert "finder_public.warm, bus" in src
    assert "finder_payload" not in src
