import datetime as dt
from zoneinfo import ZoneInfo
import alerts

CT = ZoneInfo("America/Chicago")


def _scan():
    return {
        "signals_0dte": [
            {"symbol": "SPX", "type": "PUT", "short_strike": 5000, "long_strike": 4990,
             "expiration": "2026-06-17", "composite_score": 80},
        ],
        "signals_swing": [
            {"symbol": "QQQ", "type": "CALL", "short_strike": 500, "long_strike": 510,
             "expiration": "2026-06-20", "composite_score": 40},
        ],
    }


def test_scanner_keys_covers_both_tables():
    assert len(alerts.scanner_keys(_scan())) == 2
    assert alerts.scanner_keys({}) == set()


def test_unread_count_is_current_minus_acked():
    keys = alerts.scanner_keys(_scan())
    assert alerts.unread_count(keys, set()) == 2
    one = {next(iter(keys))}
    assert alerts.unread_count(keys, one) == 1
    assert alerts.unread_count(keys, keys) == 0


def test_qualifying_new_respects_min_score_and_alerted():
    scan = _scan()
    # min_score 70 → only the 80-score SPX signal qualifies
    q = alerts.qualifying_new(scan, alerted=set(), min_score=70)
    assert len(q) == 1
    # already-alerted keys are excluded
    assert alerts.qualifying_new(scan, alerted=q, min_score=70) == set()
    # min_score 0 → both qualify
    assert len(alerts.qualifying_new(scan, alerted=set(), min_score=0)) == 2


def test_in_market_hours():
    assert alerts.in_market_hours(dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT))   # Wed 10:00
    assert not alerts.in_market_hours(dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT))  # after 15:00
    assert not alerts.in_market_hours(dt.datetime(2026, 6, 13, 10, 0, tzinfo=CT))  # Saturday


def test_should_alert_truth_table():
    now_open = dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT)
    now_closed = dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT)
    q = {"k"}
    base = {"alert_enabled": True, "alert_market_hours_only": True}
    assert alerts.should_alert(base, q, now_open)
    assert not alerts.should_alert(base, q, now_closed)         # gated by market hours
    assert not alerts.should_alert(base, set(), now_open)       # nothing new
    assert not alerts.should_alert({**base, "alert_enabled": False}, q, now_open)
    # market-hours gate off → fires even after close
    assert alerts.should_alert({**base, "alert_market_hours_only": False}, q, now_closed)
