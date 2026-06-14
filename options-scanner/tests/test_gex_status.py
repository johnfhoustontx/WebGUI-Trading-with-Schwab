from datetime import datetime
from zoneinfo import ZoneInfo

from gex_status import classify_collector_status

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
    now = datetime(2026, 4, 13, 7, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert "8:30" in text
    assert color == "gray"


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
