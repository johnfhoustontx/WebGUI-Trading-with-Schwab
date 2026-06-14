from unittest.mock import patch, MagicMock
import trade_tracker_client as ttc


def _trade():
    return {
        "trade_id": "t1", "symbol": "SPX", "strategy": "PCS",
        "expiration": "2026-05-30", "quantity": 1, "entry_credit": 1.50,
        "short_strike": 5200, "long_strike": 5150,
    }


def test_track_payload_includes_derived_thresholds():
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return MagicMock(status_code=200)

    with patch("trade_tracker_client.requests.post", side_effect=fake_post):
        ok = ttc.track(_trade())
    assert ok is True
    assert captured["url"].endswith("/track")
    assert captured["json"]["target_mid"] == 0.75
    assert captured["json"]["stop_mid"] == 4.50
    assert captured["json"]["call_short"] is None  # vertical


def test_track_swallows_connection_error():
    with patch("trade_tracker_client.requests.post",
               side_effect=ttc.requests.exceptions.ConnectionError):
        ok = ttc.track(_trade())  # proxy down: must not raise
    assert ok is False


def test_untrack_swallows_errors():
    with patch("trade_tracker_client.requests.post",
               side_effect=ttc.requests.exceptions.Timeout):
        assert ttc.untrack("t1") is False


def test_track_includes_ic_call_legs():
    captured = {}
    tr = _trade()
    tr["strategy"] = "IC"
    tr["call_short"] = 5300
    tr["call_long"] = 5350
    with patch("trade_tracker_client.requests.post",
               side_effect=lambda url, json, timeout: captured.update(json=json) or MagicMock(status_code=200)):
        ttc.track(tr)
    assert captured["json"]["call_short"] == 5300
    assert captured["json"]["call_long"] == 5350
