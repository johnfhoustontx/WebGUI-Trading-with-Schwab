"""Tests for the sentiment service scheduler gating (Phase 4)."""
from services.sentiment_svc import scheduler


def test_trend_interval_is_15_min():
    assert scheduler.TREND_INTERVAL_SEC == 900


def test_trend_due_when_interval_elapsed():
    assert scheduler.trend_due(900, 0) is True


def test_trend_not_due_before_interval():
    assert scheduler.trend_due(600, 0) is False


def test_trend_due_on_cold_start():
    assert scheduler.trend_due(900, None) is True
