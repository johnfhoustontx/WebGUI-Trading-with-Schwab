"""Tests for Discord/Telegram notification channels."""
import pytest
from unittest.mock import MagicMock, patch
import notifier


SAMPLE_SIGNAL = {
    "symbol": "SPY",
    "type": "PCS",
    "trade_type": "0DTE",
    "expiration": "2026-04-26",
    "short_strike": 500.0,
    "long_strike": 495.0,
    "width": 5,
    "credit": 1.25,
    "max_loss": 3.75,
    "rr_pct": 33.3,
    "pop_pct": 72.0,
    "short_delta": -0.18,
    "net_theta": 0.42,
    "short_iv": 18.5,
    "breakeven": "498.75",
}

SAMPLE_TRADE = {
    "symbol": "SPY",
    "strategy": "PCS",
    "mode": "paper",
    "short_strike": 500.0,
    "long_strike": 495.0,
    "quantity": 1,
    "entry_credit_total": 125.0,
    "max_loss_total": 375.0,
}


# ---------- DISCORD ----------

def test_discord_signal_payload_shape(monkeypatch):
    captured = {}
    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return MagicMock(status_code=204)
    monkeypatch.setattr(notifier.requests, "post", fake_post)

    notifier._send_discord("https://discord.com/hook/x", SAMPLE_SIGNAL, "signal")

    assert captured["url"] == "https://discord.com/hook/x"
    embed = captured["json"]["embeds"][0]
    assert "SPY" in embed["title"]
    assert "PCS" in embed["title"]
    assert len(embed["fields"]) == 8
    field_names = [f["name"] for f in embed["fields"]]
    assert "Credit" in field_names
    assert "R:R" in field_names
    assert "Breakeven" in field_names


def test_discord_color_tier_high(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    s = dict(SAMPLE_SIGNAL); s["rr_pct"] = 30
    notifier._send_discord("https://x", s, "signal")
    assert captured["json"]["embeds"][0]["color"] == 0x2ECC71  # green


def test_discord_color_tier_mid(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    s = dict(SAMPLE_SIGNAL); s["rr_pct"] = 20
    notifier._send_discord("https://x", s, "signal")
    assert captured["json"]["embeds"][0]["color"] == 0xF1C40F  # yellow


def test_discord_color_tier_low(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    s = dict(SAMPLE_SIGNAL); s["rr_pct"] = 10
    notifier._send_discord("https://x", s, "signal")
    assert captured["json"]["embeds"][0]["color"] == 0x95A5A6  # gray


def test_discord_trade_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    notifier._send_discord("https://x", SAMPLE_TRADE, "trade")
    embed = captured["json"]["embeds"][0]
    assert embed["color"] == 0x3498DB  # blue
    assert "EXECUTED" in embed["title"]


def test_discord_error_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    notifier._send_discord("https://x", "boom", "error")
    embed = captured["json"]["embeds"][0]
    assert embed["color"] == 0xE74C3C  # red


def test_discord_send_failure_does_not_raise(monkeypatch):
    def boom(*a, **k): raise ConnectionError("network down")
    monkeypatch.setattr(notifier.requests, "post", boom)
    # Should not raise
    notifier._send_discord("https://x", SAMPLE_SIGNAL, "signal")


# ---------- TELEGRAM ----------

def test_telegram_signal_payload_shape(monkeypatch):
    captured = {}
    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return MagicMock(status_code=200)
    monkeypatch.setattr(notifier.requests, "post", fake_post)

    notifier._send_telegram("TOKEN123", 1584104508, SAMPLE_SIGNAL, "signal")

    assert "bot" in captured["url"] and "sendMessage" in captured["url"]
    assert "TOKEN123" in captured["url"]
    assert captured["json"]["chat_id"] == 1584104508
    assert captured["json"]["parse_mode"] == "HTML"
    text = captured["json"]["text"]
    assert "SPY" in text and "PCS" in text and "33.3" in text


def test_telegram_emoji_high(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    s = dict(SAMPLE_SIGNAL); s["rr_pct"] = 30
    notifier._send_telegram("T", 1, s, "signal")
    assert "\U0001F7E2" in captured["json"]["text"]


def test_telegram_emoji_mid(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    s = dict(SAMPLE_SIGNAL); s["rr_pct"] = 20
    notifier._send_telegram("T", 1, s, "signal")
    assert "\U0001F7E1" in captured["json"]["text"]


def test_telegram_trade_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    notifier._send_telegram("T", 1, SAMPLE_TRADE, "trade")
    text = captured["json"]["text"]
    assert "EXECUTED" in text
    assert "SPY" in text


def test_telegram_error_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    notifier._send_telegram("T", 1, "boom", "error")
    text = captured["json"]["text"]
    assert "Scanner error" in text
    assert "boom" in text


def test_telegram_send_failure_does_not_raise(monkeypatch):
    def boom(*a, **k): raise ConnectionError("network down")
    monkeypatch.setattr(notifier.requests, "post", boom)
    notifier._send_telegram("T", 1, SAMPLE_SIGNAL, "signal")  # must not raise


def test_telegram_skips_when_no_token(monkeypatch):
    called = []
    monkeypatch.setattr(notifier.requests, "post",
        lambda *a, **k: called.append(True) or MagicMock())
    notifier._send_telegram("", 1, SAMPLE_SIGNAL, "signal")
    notifier._send_telegram("T", 0, SAMPLE_SIGNAL, "signal")
    assert called == []


# ---------- DISPATCHER ----------

def _make_signal(rr=20.0):
    s = dict(SAMPLE_SIGNAL)
    s["rr_pct"] = rr
    s["id"] = f"sig-{rr}"
    return s


def test_dispatcher_sends_all_signals_to_both_channels(monkeypatch):
    monkeypatch.setattr(notifier.Notifier, "_async",
        lambda self, fn, *args: fn(*args))
    discord_calls, telegram_calls = [], []
    monkeypatch.setattr(notifier, "_send_discord",
        lambda url, payload, kind="signal": discord_calls.append((url, kind)))
    monkeypatch.setattr(notifier, "_send_telegram",
        lambda token, chat_id, payload, kind="signal": telegram_calls.append((token, kind)))

    n = notifier.Notifier(
        audio=False, toast=False,
        telegram_token="T", telegram_chat_id=1,
        discord_webhook="https://x",
    )
    sigs = [_make_signal(r) for r in (10, 18, 22, 28, 35)]
    n.new_signals(sigs, len(sigs))

    assert len(discord_calls) == 5
    assert len(telegram_calls) == 5
    assert all(k == "signal" for _, k in discord_calls)


def test_dispatcher_sends_trade_to_both_channels(monkeypatch):
    monkeypatch.setattr(notifier.Notifier, "_async",
        lambda self, fn, *args: fn(*args))
    discord_calls, telegram_calls = [], []
    monkeypatch.setattr(notifier, "_send_discord",
        lambda url, payload, kind="signal": discord_calls.append(kind))
    monkeypatch.setattr(notifier, "_send_telegram",
        lambda token, chat_id, payload, kind="signal": telegram_calls.append(kind))

    n = notifier.Notifier(
        audio=False, toast=False,
        telegram_token="T", telegram_chat_id=1,
        discord_webhook="https://x",
    )
    n.trade_executed(SAMPLE_TRADE)

    assert discord_calls == ["trade"]
    assert telegram_calls == ["trade"]


def test_dispatcher_skips_disabled_channels(monkeypatch):
    monkeypatch.setattr(notifier.Notifier, "_async",
        lambda self, fn, *args: fn(*args))
    discord_calls, telegram_calls = [], []
    monkeypatch.setattr(notifier, "_send_discord",
        lambda *a, **k: discord_calls.append(1))
    monkeypatch.setattr(notifier, "_send_telegram",
        lambda *a, **k: telegram_calls.append(1))

    n = notifier.Notifier(audio=False, toast=False)  # no creds
    n.new_signals([_make_signal(30)], 1)
    n.trade_executed(SAMPLE_TRADE)

    assert discord_calls == []
    assert telegram_calls == []


# ---------- CONFIG LOADER ----------

def test_load_config_reads_env_vars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://env-hook")
    n = notifier.Notifier(audio=False, toast=False, load_config=True)
    assert n.telegram_token == "env-token"
    assert n.telegram_chat_id == 9999
    assert n.discord_webhook == "https://env-hook"


def test_load_config_explicit_kwargs_win_over_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    n = notifier.Notifier(audio=False, toast=False,
                          telegram_token="explicit", load_config=True)
    assert n.telegram_token == "explicit"


def test_load_config_missing_creds_disables_silently(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    # Also mask any local config_notifications.py
    import sys
    sys.modules.pop("config_notifications", None)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    def guarded_import(name, *a, **k):
        if name == "config_notifications":
            raise ImportError("masked")
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", guarded_import)
    n = notifier.Notifier(audio=False, toast=False, load_config=True)
    assert not n.telegram_token
    assert not n.discord_webhook


# ---------- HARDENING (post-review fixes) ----------

def test_telegram_escapes_html_in_symbol(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or MagicMock())
    s = dict(SAMPLE_SIGNAL); s["symbol"] = "A&B<C>"
    notifier._send_telegram("T", 1, s, "signal")
    text = captured["json"]["text"]
    assert "A&amp;B&lt;C&gt;" in text
    assert "<C>" not in text  # raw angle brackets must not survive


def test_load_config_survives_syntaxerror_in_config_file(monkeypatch, tmp_path):
    import sys
    # Build a bogus config_notifications module that raises on import
    bad = tmp_path / "config_notifications.py"
    bad.write_text("this is = not valid python ===\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("config_notifications", None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    # Must not raise — channels should silently disable
    n = notifier.Notifier(audio=False, toast=False, load_config=True)
    assert not n.telegram_token
    assert not n.discord_webhook


# ---------- ENABLE/DISABLE TOGGLES ----------

def test_set_discord_disables_dispatch(monkeypatch):
    monkeypatch.setattr(notifier.Notifier, "_async",
        lambda self, fn, *args: fn(*args))
    discord_calls = []
    monkeypatch.setattr(notifier, "_send_discord",
        lambda *a, **k: discord_calls.append(1))
    n = notifier.Notifier(audio=False, toast=False, discord_webhook="https://x")
    n.set_discord(False)
    n.new_signals([_make_signal(30)], 1)
    n.trade_executed(SAMPLE_TRADE)
    n.error("boom")
    assert discord_calls == []
    n.set_discord(True)
    n.new_signals([_make_signal(30)], 1)
    assert len(discord_calls) == 1


def test_set_telegram_disables_dispatch(monkeypatch):
    monkeypatch.setattr(notifier.Notifier, "_async",
        lambda self, fn, *args: fn(*args))
    telegram_calls = []
    monkeypatch.setattr(notifier, "_send_telegram",
        lambda *a, **k: telegram_calls.append(1))
    n = notifier.Notifier(audio=False, toast=False,
                          telegram_token="T", telegram_chat_id=1)
    n.set_telegram(False)
    n.new_signals([_make_signal(30)], 1)
    n.trade_executed(SAMPLE_TRADE)
    n.error("boom")
    assert telegram_calls == []
    n.set_telegram(True)
    n.new_signals([_make_signal(30)], 1)
    assert len(telegram_calls) == 1


def test_toggles_default_enabled():
    n = notifier.Notifier(audio=False, toast=False)
    assert n.discord_enabled is True
    assert n.telegram_enabled is True
