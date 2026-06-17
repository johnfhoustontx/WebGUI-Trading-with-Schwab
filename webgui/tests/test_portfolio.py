"""Tests for the Portfolio page pure display builders + render API.

The engine orchestration + formatting live in ``services/portfolio_svc``; this
page is a Tier-3 reader that renders the service's already-formatted rows. Only
its pure transforms (status badges, suggestion detail, column defs) and the
``render`` callable are exercised here.
"""
from pages import portfolio


def test_proxy_status():
    up_text, up_color = portfolio.proxy_status({"proxy_up": True})
    assert "up" in up_text.lower() and up_color == portfolio.UP_COLOR
    down_text, down_color = portfolio.proxy_status({"proxy_up": False})
    assert "down" in down_text.lower() and down_color == portfolio.DOWN_COLOR
    assert portfolio.proxy_status(None)[0]  # tolerates None payload


def test_stream_status():
    live_text, live_color = portfolio.stream_status({"streaming": True})
    assert "live" in live_text.lower() and live_color == portfolio.UP_COLOR
    off_text, off_color = portfolio.stream_status({"streaming": False})
    assert off_color == portfolio.MUTED_COLOR


def test_suggestion_text_joins_reasons():
    suggestions = {"AAPL": [{"action": "TRIM", "reason": "too big"},
                            {"action": "HOLD", "reason": "ok"}]}
    txt = portfolio.suggestion_text(suggestions, "AAPL")
    assert "[TRIM] too big" in txt
    assert "[HOLD] ok" in txt


def test_suggestion_text_empty_and_unselected():
    assert "no suggestions" in portfolio.suggestion_text({}, "AAPL").lower()
    assert "no suggestions" in portfolio.suggestion_text({"AAPL": []}, "AAPL").lower()
    # nothing selected -> a prompt, not a crash
    assert "select" in portfolio.suggestion_text({}, None).lower()


def test_columns_have_required_keys():
    for cols in (portfolio.HOLDINGS_COLS, portfolio.SECTOR_COLS,
                 portfolio.PERF_COLS):
        assert cols
        assert all({"name", "label", "field"} <= set(c) for c in cols)


def test_status_line_counts_holdings():
    line = portfolio.status_line({"holdings_rows": [{"symbol": "AAPL"},
                                                    {"symbol": "MSFT"}],
                                  "errors": []})
    assert "2" in line
    # an error surfaces in the status line
    err_line = portfolio.status_line({"holdings_rows": [], "errors": ["down"]})
    assert "down" in err_line


def test_status_line_tolerates_none():
    assert isinstance(portfolio.status_line(None), str)


def test_render_is_callable():
    assert callable(portfolio.render)
