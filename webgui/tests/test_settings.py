"""Tests for the Settings page pure helpers."""
from pages import settings as S


def test_api_stats_rows_formats_counts():
    rows = S.api_stats_rows({"today": 1234, "last_7_days": 56789,
                             "last_30_days": 250000, "since": "2026-07-12"})
    assert rows == [("Today", "1,234"), ("Last 7 days", "56,789"),
                    ("Last 30 days", "250,000")]


def test_api_stats_rows_placeholder_when_proxy_down():
    assert S.api_stats_rows(None) == [
        ("Today", "—"), ("Last 7 days", "—"), ("Last 30 days", "—")]
    # malformed values degrade per-field, never raise
    rows = S.api_stats_rows({"today": "x"})
    assert rows[0] == ("Today", "—") and rows[1] == ("Last 7 days", "0")
