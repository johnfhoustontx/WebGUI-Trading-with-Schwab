"""Tests for the shared notify channel/gate/config helper.

These pin the domain-agnostic pieces lifted out of
services/options_svc/push_notify.py: config resolution (DEFAULTS < file < env),
the three best-effort channel senders, and the market-hours gate.
"""
import json

from shared.notify import channels as ch


def test_load_config_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({
        "enabled": True, "min_score": 20,
        "telegram": {"bot_token": "T", "chat_id": 5},
        "discord": {"webhook_url": "https://d"},
        "sms": {"fi_number": "5551234567", "smtp_user": "u@gmail.com",
                "smtp_app_password": "pw"},
    }))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = ch.load_config()
    assert cfg["min_score"] == 20
    assert cfg["telegram"]["bot_token"] == "T"
    assert cfg["sms"]["fi_number"] == "5551234567"


def test_load_config_path_arg_overrides_module_global(tmp_path, monkeypatch):
    p = tmp_path / "explicit.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "VIA_ARG", "chat_id": 9}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", tmp_path / "nope.json")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = ch.load_config(p)
    assert cfg["telegram"]["bot_token"] == "VIA_ARG"


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "FILE", "chat_id": 1}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ENV")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://envhook")
    cfg = ch.load_config()
    assert cfg["telegram"]["bot_token"] == "ENV"
    assert cfg["discord"]["webhook_url"] == "https://envhook"


def test_missing_file_returns_disabled_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_CONFIG_PATH", tmp_path / "nope.json")
    for k in ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "FI_SMS_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    cfg = ch.load_config()
    assert cfg["telegram"]["bot_token"] == ""
    assert cfg["enabled"] is True  # default-on; channels self-gate on creds


def test_send_telegram_noop_without_creds(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not post without creds")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_telegram("", 0, "hi")     # no token/chat → silent no-op
    ch.send_telegram("T", 0, "hi")    # no chat_id → silent no-op


def test_send_telegram_posts_to_bot_api(monkeypatch):
    calls = {}
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    ch.send_telegram("TOK", 42, "hello")
    assert calls["url"].endswith("/botTOK/sendMessage")
    assert calls["json"]["chat_id"] == 42 and calls["json"]["parse_mode"] == "HTML"


def test_send_telegram_document_posts_multipart(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append((url, kw)))
    ch.send_telegram_document("TOK", 42, "b.html", b"<html>hi</html>", "cap")
    url, kw = calls[0]
    assert url == "https://api.telegram.org/botTOK/sendDocument"
    assert kw["data"] == {"chat_id": 42, "caption": "cap"}
    assert kw["files"]["document"] == ("b.html", b"<html>hi</html>", "text/html")
    # plain-text caption: no parse_mode, so nothing needs HTML-escaping
    assert "parse_mode" not in kw["data"]


def test_send_telegram_document_noop_without_creds(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_telegram_document("", 42, "b.html", b"x")
    ch.send_telegram_document("TOK", None, "b.html", b"x")
    assert calls == []


def test_send_telegram_document_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_telegram_document("TOK", 42, "b.html", b"x")   # must not raise


def test_send_telegram_document_truncates_caption(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append(kw))
    ch.send_telegram_document("TOK", 42, "b.html", b"x", "z" * 2000)
    assert len(calls[0]["data"]["caption"]) == 1024


def test_send_discord_noop_without_url(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not post without webhook")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_discord("", {"title": "x"})   # no url → silent no-op


def test_send_discord_posts_embed(monkeypatch):
    calls = {}
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    ch.send_discord("https://hook", {"title": "x"})
    assert calls["url"] == "https://hook"
    assert calls["json"]["embeds"] == [{"title": "x"}]


def test_send_sms_noop_without_creds(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("should not connect without creds")
    monkeypatch.setattr(ch.smtplib, "SMTP", Boom)
    ch.send_sms("", "u", "p", "body")            # no fi_number
    ch.send_sms("555", "", "p", "body")          # no smtp_user
    ch.send_sms("555", "u", "", "body")          # no password


def test_send_sms_emails_fi_gateway(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg): sent["to"] = msg["To"]; sent["body"] = msg.get_payload()

    monkeypatch.setattr(ch.smtplib, "SMTP", FakeSMTP)
    ch.send_sms("5551234567", "u@gmail.com", "pw", "2 new signals")
    assert sent["to"] == "5551234567@msg.fi.google.com"
    assert sent["login"] == ("u@gmail.com", "pw")
    assert "2 new signals" in sent["body"]


def test_senders_never_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_telegram("T", 1, "x")   # no exception
    ch.send_discord("https://h", {})

    class BoomSMTP:
        def __init__(self, *a, **k):
            raise RuntimeError("net")
    monkeypatch.setattr(ch.smtplib, "SMTP", BoomSMTP)
    ch.send_sms("555", "u", "p", "body")  # no exception


def test_market_hours_gate_is_bool():
    assert isinstance(ch._in_market_hours(), bool)


def test_today_ct_is_iso_date():
    d = ch._today_ct()
    assert isinstance(d, str) and len(d) == 10 and d[4] == "-"


def test_defaults_include_gamma_briefing_block(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "T", "chat_id": 1}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    cfg = ch.load_config()
    gb = cfg["gamma_briefing"]
    assert gb["enabled"] is True
    assert gb["slots"] == ["premarket", "open", "midday", "close"]
    assert gb["webhook_url"] == ""


def test_gamma_briefing_file_values_override_defaults(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"gamma_briefing": {"enabled": False,
                                                "webhook_url": "https://hook"}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    cfg = ch.load_config()
    assert cfg["gamma_briefing"]["enabled"] is False
    assert cfg["gamma_briefing"]["webhook_url"] == "https://hook"
    # unspecified key still falls back to the default (deep merge, not replace)
    assert cfg["gamma_briefing"]["slots"] == ["premarket", "open", "midday", "close"]


def test_public_names_reexported_from_package():
    from shared.notify import (load_config, send_telegram, send_discord,
                               send_sms, _in_market_hours, _today_ct)
    assert load_config is ch.load_config
    assert send_telegram is ch.send_telegram
    assert _in_market_hours is ch._in_market_hours
    assert _today_ct is ch._today_ct
