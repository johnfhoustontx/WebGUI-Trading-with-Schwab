"""Tests for event_calendar.py."""
import json
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def macro_json(tmp_path, monkeypatch):
    """Point event_calendar at a tmp macro JSON file."""
    data = {
        "note": "test",
        "last_updated": "2026-04-23",
        "events": [
            {"date": "2026-04-28", "label": "FOMC Day 1", "category": "FOMC"},
            {"date": "2026-04-29", "label": "FOMC Decision", "category": "FOMC"},
            {"date": "2026-04-30", "label": "GDP Advance", "category": "GDP"},
            {"date": "2026-05-13", "label": "CPI", "category": "CPI"},
        ],
    }
    path = tmp_path / "macro_events.json"
    path.write_text(json.dumps(data))
    import event_calendar
    monkeypatch.setattr(event_calendar, "MACRO_JSON_PATH", path)
    monkeypatch.setattr(event_calendar, "EARNINGS_CACHE_PATH", tmp_path / "earnings_cache.json")
    return path


def test_get_macro_events_within_range(macro_json):
    from event_calendar import get_macro_events
    events = get_macro_events(date(2026, 4, 23), date(2026, 5, 1))
    assert len(events) == 3
    assert events[0].category == "FOMC"
    assert events[-1].category == "GDP"


def test_get_macro_events_excludes_out_of_range(macro_json):
    from event_calendar import get_macro_events
    events = get_macro_events(date(2026, 5, 1), date(2026, 5, 31))
    # April events excluded, May 13 CPI included
    labels = [e.label for e in events]
    assert "CPI" in labels
    assert "FOMC Day 1" not in labels


def test_get_macro_events_inclusive_boundaries(macro_json):
    from event_calendar import get_macro_events
    events = get_macro_events(date(2026, 4, 28), date(2026, 4, 28))
    assert len(events) == 1
    assert events[0].category == "FOMC"


# === Finviz earnings test helpers ===

class _FakeResponse:
    """Minimal duck-typed stand-in for requests.Response used by event_calendar."""
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_get_factory(csv_body, status_code=200):
    """Return a fake requests.get that always responds with the given CSV."""
    def _get(*args, **kwargs):
        return _FakeResponse(csv_body, status_code)
    return _get


def _fake_appsettings(monkeypatch, tmp_path, token="test-token"):
    """Patch event_calendar.APPSETTINGS to point at a temp file with a token."""
    import event_calendar
    cfg_path = tmp_path / "appsettings.json"
    cfg_path.write_text(json.dumps({"FinvizEliteAuth": token}), encoding="utf-8")
    monkeypatch.setattr(event_calendar, "APPSETTINGS", cfg_path)


# === Earnings tests (replacing the three yfinance-based tests) ===

def test_get_earnings_date_uses_cache_within_ttl(macro_json, tmp_path, monkeypatch):
    """Subsequent fetches within TTL hit the cache, not requests.get."""
    import event_calendar
    _fake_appsettings(monkeypatch, tmp_path)
    call_count = {"n": 0}

    def _get(*args, **kwargs):
        call_count["n"] += 1
        return _FakeResponse("Ticker,Earnings\nAAPL,May 02 AMC\n")

    monkeypatch.setattr(event_calendar.requests, "get", _get)
    first = event_calendar.get_earnings_date("AAPL")
    second = event_calendar.get_earnings_date("AAPL")
    assert first == second
    assert call_count["n"] == 1  # second call hit cache, not network


def test_get_earnings_date_returns_none_on_failure(macro_json, tmp_path, monkeypatch):
    """A network or HTTP failure degrades to None silently."""
    import event_calendar
    _fake_appsettings(monkeypatch, tmp_path)

    def _get(*args, **kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(event_calendar.requests, "get", _get)
    assert event_calendar.get_earnings_date("AAPL") is None


def test_get_earnings_date_returns_none_when_token_missing(macro_json, tmp_path, monkeypatch):
    """No FinvizEliteAuth in appsettings.json -> None, no HTTP call made."""
    import event_calendar
    cfg_path = tmp_path / "appsettings.json"
    cfg_path.write_text(json.dumps({"AppKey": "x"}), encoding="utf-8")  # no FinvizEliteAuth
    monkeypatch.setattr(event_calendar, "APPSETTINGS", cfg_path)
    called = {"n": 0}

    def _get(*args, **kwargs):
        called["n"] += 1
        return _FakeResponse("")

    monkeypatch.setattr(event_calendar.requests, "get", _get)
    assert event_calendar.get_earnings_date("AAPL") is None
    assert called["n"] == 0  # short-circuited before HTTP


def test_get_earnings_date_handles_dash_cell(macro_json, tmp_path, monkeypatch):
    """Finviz returning '-' (no upcoming earnings) -> None."""
    import event_calendar
    _fake_appsettings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        event_calendar.requests, "get",
        _fake_get_factory("Ticker,Earnings\nAAPL,-\n"),
    )
    assert event_calendar.get_earnings_date("AAPL") is None


def test_get_earnings_date_year_inference_rolls_forward(macro_json, tmp_path, monkeypatch):
    """A month-day in the past resolves to next year."""
    import event_calendar
    from datetime import date as _date

    _fake_appsettings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        event_calendar.requests, "get",
        _fake_get_factory("Ticker,Earnings\nAAPL,Jan 02 AMC\n"),
    )

    # Freeze "today" to mid-year so Jan 02 is unambiguously past.
    class _FrozenDate(_date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 15)

    monkeypatch.setattr(event_calendar, "date", _FrozenDate)

    result = event_calendar.get_earnings_date("AAPL")
    assert result is not None
    assert result.year == 2027
    assert result.month == 1
    assert result.day == 2


def test_get_events_between_unified(macro_json, tmp_path, monkeypatch):
    """Combined macro + earnings stream merges correctly with finviz source."""
    import event_calendar
    from datetime import date

    _fake_appsettings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        event_calendar.requests, "get",
        _fake_get_factory("Ticker,Earnings\nAAPL,Jul 24 AMC\n"),
    )

    # Freeze "today" so year-inference resolves deterministically.
    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 1)

    monkeypatch.setattr(event_calendar, "date", _FrozenDate)

    events = event_calendar.get_events_between(
        "AAPL", date(2026, 7, 1), date(2026, 8, 1),
    )
    earnings = [e for e in events if e.category == "EARNINGS"]
    assert len(earnings) == 1
    assert earnings[0].source == "finviz"
    assert earnings[0].date == date(2026, 7, 24)


def test_get_events_between_includes_dividend_from_quote_data(macro_json):
    from event_calendar import get_events_between
    qd = {"divDate": "2026-05-15"}
    events = get_events_between("AAPL", date(2026, 4, 23), date(2026, 5, 30),
                                 quote_data=qd, include_earnings=False)
    div_events = [e for e in events if e.category == "DIVIDEND"]
    assert len(div_events) == 1
    assert div_events[0].date == date(2026, 5, 15)


def test_warn_if_stale_silent_when_fresh(tmp_path, monkeypatch, capsys):
    import event_calendar
    path = tmp_path / "macro_events.json"
    fresh = (date.today() - timedelta(days=10)).isoformat()
    path.write_text(json.dumps({"last_updated": fresh, "events": []}))
    monkeypatch.setattr(event_calendar, "MACRO_JSON_PATH", path)
    event_calendar.warn_if_stale()
    assert capsys.readouterr().out == ""


def test_warn_if_stale_prints_when_old(tmp_path, monkeypatch, capsys):
    import event_calendar
    path = tmp_path / "macro_events.json"
    stale = (date.today() - timedelta(days=80)).isoformat()
    last_event = (date.today() + timedelta(days=10)).isoformat()
    path.write_text(json.dumps({
        "last_updated": stale,
        "events": [{"date": last_event, "label": "X", "category": "FOMC"}],
    }))
    monkeypatch.setattr(event_calendar, "MACRO_JSON_PATH", path)
    event_calendar.warn_if_stale()
    out = capsys.readouterr().out
    assert "REMINDER" in out
    assert "80 days ago" in out
    assert last_event in out


def test_warn_if_stale_handles_missing_file(tmp_path, monkeypatch, capsys):
    import event_calendar
    monkeypatch.setattr(event_calendar, "MACRO_JSON_PATH",
                        tmp_path / "nope.json")
    event_calendar.warn_if_stale()
    assert "missing" in capsys.readouterr().out
