"""Tests for SentimentDashboard.notifier.

No real HTTP — requests.post is patched in every test that could
otherwise hit the network. The notifier dispatches on daemon threads,
so tests join them before asserting.
"""
import json
import threading
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

import notifier as notifier_mod
from notifier import (
    SentimentNotifier,
    _discord_sentiment_embed,
    _telegram_sentiment_text,
    _bias_color,
    _today_str,
)


@pytest.fixture
def fresh_bridge(tmp_path, monkeypatch):
    """Write a sample bridge file at a tmp path and point the notifier
    at it. Yields a callable that updates the file contents in place."""
    path = tmp_path / "sentiment_bridge.json"

    def write(payload):
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    monkeypatch.setattr(notifier_mod, "CANONICAL_BRIDGE_PATH", path)
    return write


def _sample_payload(**overrides):
    payload = {
        "date": "2026-05-17",
        "composite_score": 6.2,
        "bias": "BULLISH",
        "position_size_modifier": "1.10x",
        "aggregate_confidence": 0.82,
        "change": 0.4,
        "component_scores": {
            "vix_complex":  7.0,
            "vix":          6.0,
            "vix_term":     6.0,
            "vix1d":        8.0,
            "term_slope":   5.0,
            "put_call":     6.0,
            "breadth":      5.0,
            "rotation":     7.0,
            "sector_perf":  6.0,
            "credit_pulse": 4.0,
        },
        "component_confidence": {
            "vix_complex":  0.9,
            "put_call":     0.7,
            "breadth":      0.8,
            "rotation":     0.6,
            "sector_perf":  0.85,
            "credit_pulse": 0.5,
        },
        "velocity": {"regime_break": False},
        "divergence_flag": None,
    }
    payload.update(overrides)
    return payload


def _wait_for_threads():
    """Best-effort join of daemon threads spawned by the notifier."""
    for t in threading.enumerate():
        if t is threading.current_thread() or not t.daemon:
            continue
        t.join(timeout=2.0)


# ── 1) no channels configured ──

def test_no_channels_configured():
    n = SentimentNotifier(load_config=False, armed=True)
    assert n.telegram_token is None
    assert n.discord_webhook is None

    with patch.object(notifier_mod.requests, "post") as mock_post:
        n.post_sentiment(_sample_payload())
        _wait_for_threads()
        mock_post.assert_not_called()


# ── 1b) disarmed notifier never sends ──

def test_disarmed_notifier_never_sends(fresh_bridge):
    fresh_bridge(_sample_payload())
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False,
    )
    assert n._armed is False

    with patch.object(notifier_mod.requests, "post") as mock_post:
        n.post_sentiment(_sample_payload())
        _wait_for_threads()
        mock_post.assert_not_called()


def test_arm_enables_sending(fresh_bridge):
    fresh_bridge(_sample_payload())
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False,
    )
    n.arm()
    assert n._armed is True
    with patch.object(notifier_mod.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        n.post_sentiment(_sample_payload())
        _wait_for_threads()
        assert mock_post.called


# ── 2) discord embed shape ──

def test_discord_embed_shape():
    payload = _sample_payload(bias="BULLISH")
    embed = _discord_sentiment_embed(payload)

    assert "6.2/10" in embed["title"]
    assert "BULLISH" in embed["title"]
    assert embed["color"] == 0x2ECC71  # green for BULLISH
    assert len(embed["fields"]) == 6
    field_names = {f["name"] for f in embed["fields"]}
    assert field_names == {
        "VIX Complex", "Put/Call", "Breadth",
        "Rotation", "Sector Perf", "Credit Pulse",
    }
    # VIX field shows sub-scores compactly
    vix_field = next(f for f in embed["fields"] if f["name"] == "VIX Complex")
    assert "Term" in vix_field["value"]
    assert "1D" in vix_field["value"]
    assert "Slope" in vix_field["value"]
    # No footer when nothing flagged
    assert "footer" not in embed

    # With a regime break + divergence, footer appears
    flagged = _sample_payload(
        velocity={"regime_break": True},
        divergence_flag="vix_vs_breadth")
    embed2 = _discord_sentiment_embed(flagged)
    assert "footer" in embed2
    assert "⚠" in embed2["footer"]["text"]


# ── 3) telegram text includes all components ──

def test_telegram_text_includes_components():
    text = _telegram_sentiment_text(_sample_payload())
    for label in ("VIX Complex", "Put/Call", "Breadth",
                  "Rotation", "Sector Perf", "Credit Pulse"):
        assert label in text, f"missing {label}"
    assert "6.2/10" in text
    assert "BULLISH" in text


# ── 4) HTML escape ──

def test_html_escape():
    payload = _sample_payload(
        bias="<script>alert(1)</script>",
        divergence_flag="<img src=x>")
    text = _telegram_sentiment_text(payload)
    assert "<script>" not in text.lower()
    assert "&lt;script&gt;" in text.lower()
    assert "<img" not in text.lower()
    assert "&lt;img" in text.lower()


# ── 5) first post always sent ──

def test_throttle_first_post_always_sent(fresh_bridge):
    fresh_bridge(_sample_payload())
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False, armed=True,
    )
    with patch.object(notifier_mod.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        n.post_sentiment(_sample_payload())
        _wait_for_threads()
        assert mock_post.called, "first post must always fire"


# ── 6) second post with no change is throttled ──

def test_throttle_blocks_repeat_with_no_change(fresh_bridge):
    fresh_bridge(_sample_payload())
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False, armed=True,
    )
    payload = _sample_payload()
    with patch.object(notifier_mod.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        n.post_sentiment(payload)
        _wait_for_threads()
        first_count = mock_post.call_count
        assert first_count >= 1

        # Same payload immediately — should be throttled
        n.post_sentiment(payload)
        _wait_for_threads()
        assert mock_post.call_count == first_count, \
            "repeat post within 60min with no change should be throttled"


# ── 7) score-delta release ──

def test_throttle_releases_on_score_change(fresh_bridge):
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False, armed=True,
    )
    with patch.object(notifier_mod.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        fresh_bridge(_sample_payload(composite_score=6.0))
        n.post_sentiment(_sample_payload(composite_score=6.0))
        _wait_for_threads()
        first_count = mock_post.call_count

        # Move score by 0.4 (>= 0.3 threshold) — should fire again
        fresh_bridge(_sample_payload(composite_score=6.4))
        n.post_sentiment(_sample_payload(composite_score=6.4))
        _wait_for_threads()
        assert mock_post.call_count > first_count, \
            "score delta >= 0.3 must release throttle"


# ── 8) bridge missing/stale skips send ──

def test_missing_bridge_skips_send(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notifier_mod, "CANONICAL_BRIDGE_PATH",
        tmp_path / "does_not_exist.json")
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False, armed=True,
    )
    with patch.object(notifier_mod.requests, "post") as mock_post:
        n.post_sentiment(_sample_payload())
        _wait_for_threads()
        mock_post.assert_not_called()


def test_stale_bridge_skips_send(fresh_bridge, monkeypatch):
    path = fresh_bridge(_sample_payload())
    # Backdate the file 1 hour
    import os
    old = time.time() - 3700
    os.utime(path, (old, old))
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False, armed=True,
    )
    with patch.object(notifier_mod.requests, "post") as mock_post:
        n.post_sentiment(_sample_payload())
        _wait_for_threads()
        mock_post.assert_not_called()


def test_post_uses_bridge_as_source_of_truth(fresh_bridge):
    """The notifier reads the bridge file and sends *its* values,
    not whatever the caller passed in. Guards against UI/bridge drift."""
    fresh_bridge(_sample_payload(composite_score=9.9, bias="STRONG BULLISH"))
    n = SentimentNotifier(
        discord_webhook="https://discord.test/webhook",
        load_config=False, armed=True,
    )
    with patch.object(notifier_mod.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        # Pass in a stale payload — should be ignored
        n.post_sentiment(_sample_payload(composite_score=1.0, bias="BEARISH"))
        _wait_for_threads()
        assert mock_post.called
        sent = mock_post.call_args.kwargs["json"]
        embed = sent["embeds"][0]
        assert "9.9/10" in embed["title"]
        assert "STRONG BULLISH" in embed["title"]


# ── 9) formatters use wall-clock today, not payload date ──

def test_formatters_use_today_not_payload_date():
    payload = _sample_payload(date="2020-01-01")
    text = _telegram_sentiment_text(payload)
    embed = _discord_sentiment_embed(payload)
    today = _today_str()
    assert today in text
    assert "2020-01-01" not in text
    assert today in embed["description"]


# ── 10) trend/sector/rotation lines render ──

def test_trend_sector_rotation_lines_in_telegram():
    payload = _sample_payload(
        trend_regime={
            "state": "bull_trend", "label": "Bull Trend",
            "days_in_state": 12,
        },
        sector_breakdown=[
            {"sector": "Information Technology",
             "etf": "XLK", "day_pct": 1.23},
            {"sector": "Financials", "etf": "XLF", "day_pct": 0.85},
            {"sector": "Health Care", "etf": "XLV", "day_pct": 0.10},
            {"sector": "Energy", "etf": "XLE", "day_pct": -1.51},
            {"sector": "Utilities", "etf": "XLU", "day_pct": -0.62},
        ],
        rotation_detail={
            "day_spread_pct": 0.42, "3d_spread_pct": 0.30,
            "week_spread_pct": 0.20, "top3": [], "bot3": [],
            "blended_raw": 6.1,
        },
    )
    text = _telegram_sentiment_text(payload)
    assert "Bull Trend" in text
    assert "day 12" in text
    assert "Information Technology" in text and "XLK" in text
    assert "+1.23%" in text
    assert "Energy" in text and "XLE" in text
    assert "-1.51%" in text
    assert "Cyc&gt;Def" in text or "Cyc>Def" in text
    assert "+0.42%" in text


def test_trend_sector_rotation_fields_in_discord():
    payload = _sample_payload(
        trend_regime={"state": "bear_trend", "label": "Bear Trend",
                      "days_in_state": 4},
        sector_breakdown=[
            {"sector": "Information Technology",
             "etf": "XLK", "day_pct": 1.23},
            {"sector": "Energy", "etf": "XLE", "day_pct": -1.51},
        ],
        rotation_detail={
            "day_spread_pct": -0.55, "3d_spread_pct": None,
            "week_spread_pct": None, "top3": [], "bot3": [],
            "blended_raw": 3.0,
        },
    )
    embed = _discord_sentiment_embed(payload)
    names = {f["name"] for f in embed["fields"]}
    assert "Trend" in names
    assert "Top Sectors" in names
    assert "Bottom Sectors" in names
    assert "Cyc/Def Rotation" in names
    rot = next(
        f for f in embed["fields"] if f["name"] == "Cyc/Def Rotation")
    assert "Def>Cyc" in rot["value"]
    assert "-0.55%" in rot["value"]


def test_no_extra_fields_when_data_missing():
    """If sector_breakdown / rotation_detail / trend_regime are absent,
    the extra fields are simply omitted — message stays compact."""
    payload = _sample_payload()
    embed = _discord_sentiment_embed(payload)
    names = {f["name"] for f in embed["fields"]}
    assert "Trend" not in names
    assert "Top Sectors" not in names
    assert "Bottom Sectors" not in names
    assert "Cyc/Def Rotation" not in names


# ── Sanity: bias color mapping ──

def test_bias_color_mapping():
    assert _bias_color("BULLISH") == 0x2ECC71
    assert _bias_color("STRONG BULLISH") == 0x2ECC71
    assert _bias_color("NEUTRAL") == 0x3498DB
    assert _bias_color("BEARISH") == 0xF1C40F
    assert _bias_color("STRONG BEARISH") == 0xE74C3C


# ── Config loading: shared OptionsScanner fallback ──

def test_shared_config_fallback_loads_creds(tmp_path, monkeypatch):
    """When config_notifications isn't on sys.path, the notifier should
    fall back to loading the shared file at SHARED_CONFIG_PATH by
    absolute file path."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    shared_file = tmp_path / "config_notifications.py"
    shared_file.write_text(
        'TELEGRAM_BOT_TOKEN = "fake-token-abc"\n'
        'TELEGRAM_CHAT_ID = 42\n'
        'DISCORD_WEBHOOK_URL = "https://discord.example/hook"\n'
    )

    monkeypatch.setattr(
        SentimentNotifier, "SHARED_CONFIG_PATH", str(shared_file))

    # Make sure no stray local config exists on sys.path that would
    # mask the shared fallback.
    monkeypatch.setattr(
        SentimentNotifier, "_load_local_config", lambda self: None)

    n = SentimentNotifier(load_config=True)
    assert n.telegram_token == "fake-token-abc"
    assert n.telegram_chat_id == 42
    assert n.discord_webhook == "https://discord.example/hook"


def test_local_config_wins_over_shared(tmp_path, monkeypatch):
    """A local config_notifications on sys.path takes precedence over
    the shared OptionsScanner fallback."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    # Shared file should NOT be touched if local exists.
    shared_file = tmp_path / "shared_cfg.py"
    shared_file.write_text(
        'TELEGRAM_BOT_TOKEN = "SHARED"\n'
        'TELEGRAM_CHAT_ID = 1\n'
        'DISCORD_WEBHOOK_URL = "https://shared.example"\n'
    )
    monkeypatch.setattr(
        SentimentNotifier, "SHARED_CONFIG_PATH", str(shared_file))

    class FakeLocalModule:
        TELEGRAM_BOT_TOKEN = "LOCAL"
        TELEGRAM_CHAT_ID = 99
        DISCORD_WEBHOOK_URL = "https://local.example"

    monkeypatch.setattr(
        SentimentNotifier, "_load_local_config",
        lambda self: FakeLocalModule)

    n = SentimentNotifier(load_config=True)
    assert n.telegram_token == "LOCAL"
    assert n.telegram_chat_id == 99
    assert n.discord_webhook == "https://local.example"


def test_env_wins_over_files(tmp_path, monkeypatch):
    """Env vars set before construction win over either config file."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "FROM_ENV")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://env.example")

    class FakeLocalModule:
        TELEGRAM_BOT_TOKEN = "LOCAL"
        TELEGRAM_CHAT_ID = 99
        DISCORD_WEBHOOK_URL = "https://local.example"

    monkeypatch.setattr(
        SentimentNotifier, "_load_local_config",
        lambda self: FakeLocalModule)

    n = SentimentNotifier(load_config=True)
    assert n.telegram_token == "FROM_ENV"
    assert n.telegram_chat_id == 777
    assert n.discord_webhook == "https://env.example"
