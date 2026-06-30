from shared.contracts.sentiment import IntradayHistory


def test_intraday_history_accepts_points():
    h = IntradayHistory(points=[{"ts": 1, "sentiment": 6.0, "trend": 70.0}])
    assert h.points[0]["sentiment"] == 6.0


def test_intraday_history_defaults_empty():
    assert IntradayHistory().points == []
