"""Tests for the compact Options header strip pure helpers."""
from pages.options import header


def test_sentiment_dot_no_data_when_inactive():
    assert header.sentiment_dot({"active": False})[1] == "No data"
    assert header.sentiment_dot(None)[1] == "No data"


def test_sentiment_dot_bullish_when_ccs_blocked():
    assert header.sentiment_dot({"active": True, "allow_ccs": False, "allow_pcs": True})[1] == "Bullish"


def test_sentiment_dot_bearish_when_pcs_blocked():
    assert header.sentiment_dot({"active": True, "allow_ccs": True, "allow_pcs": False})[1] == "Bearish"


def test_sentiment_dot_neutral_when_both_allowed():
    assert header.sentiment_dot({"active": True, "allow_ccs": True, "allow_pcs": True})[1] == "Neutral"


def test_quote_last_extracts_last_price():
    raw = {"SPY": {"quote": {"lastPrice": 742.36}}}
    assert header.quote_last(raw, "SPY") == 742.36


def test_quote_last_missing_returns_none():
    assert header.quote_last({}, "SPY") is None
    assert header.quote_last(None, "SPY") is None
