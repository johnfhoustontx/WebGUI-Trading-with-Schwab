from datetime import datetime
from zoneinfo import ZoneInfo

from gex_status import MARKET_OPEN, classify_collector_status

TZ = ZoneInfo("America/Chicago")


def test_fresh_during_market_hours():
    now = datetime(2026, 4, 13, 10, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=120, now_ct=now, has_data=True, last_ts=None,
    )
    assert "2m" in text
    assert color == "green"


def test_stale_during_market_hours():
    now = datetime(2026, 4, 13, 10, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=1080, now_ct=now, has_data=True, last_ts=None,
    )
    assert "stale" in text.lower()
    assert color == "#c48b00"


def test_no_data_after_first_poll_window():
    now = datetime(2026, 4, 13, 8, 40, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert "not running" in text.lower()
    assert color == "red"


def test_premarket_shows_scheduled_start():
    """The message names the CONFIGURED collection start, not a literal.

    It asserted a hardcoded "8:30" until 2026-08-03, pinning a live misreport:
    collection moved to 08:00 CT on 2026-07-11 but this classifier still said
    "starts 8:30" for that half hour every morning.
    """
    now = datetime(2026, 4, 13, 5, 0, tzinfo=TZ)   # before any plausible open
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert f"{MARKET_OPEN.hour}:{MARKET_OPEN.minute:02d}" in text
    assert color == "gray"


def test_scheduled_start_matches_the_shared_collection_window():
    """Drift guard: the classifier's open IS config/sessions.toml's collection
    start (08:00 CT), so the status line can never diverge from what the
    collector actually schedules on."""
    from shared import market_calendar as mc
    assert MARKET_OPEN == mc.window_bounds("collection")[0]
    assert MARKET_OPEN == datetime(2026, 4, 13, 8, 0).time()


def test_after_hours_with_last_run():
    now = datetime(2026, 4, 13, 16, 0, tzinfo=TZ)
    last_ts = int(datetime(2026, 4, 13, 15, 15, tzinfo=TZ).timestamp())
    text, color = classify_collector_status(
        age_seconds=2700, now_ct=now, has_data=True, last_ts=last_ts,
    )
    assert "15:15" in text
    assert color == "gray"


def test_weekend_shows_idle():
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)  # Saturday
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert color == "gray"
