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


# ── ticker toggle → market_svc ─────────────────────────────────────────────
# Turning the ticker off must also stop market_svc's periodic Claude verdict —
# otherwise the toggle only hides the marquee while the API calls continue.


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(S.bus_client, "request",
                        lambda domain, cmd: sent.append((domain, cmd)))
    return sent


def test_apply_ticker_enabled_persists_and_commands_the_service(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()
    sent = _capture(monkeypatch)

    S.apply_ticker_enabled(False)
    assert S.app_settings.load()["ticker_enabled"] is False
    assert sent == [("market", {"type": "disable_summary"})]

    S.apply_ticker_enabled(True)
    assert S.app_settings.load()["ticker_enabled"] is True
    assert sent[-1] == ("market", {"type": "enable_summary"})


def test_apply_ticker_enabled_persists_even_if_the_bus_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()

    def _boom(domain, cmd):
        raise RuntimeError("redis down")

    monkeypatch.setattr(S.bus_client, "request", _boom)
    S.apply_ticker_enabled(False)  # must not raise out of the click handler
    assert S.app_settings.load()["ticker_enabled"] is False
