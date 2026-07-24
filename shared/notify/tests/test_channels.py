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


def test_send_telegram_document_noop_without_content(monkeypatch):
    # requests skips a None file part rather than raising, so posting anyway would
    # send a document-less request that 400s silently (this layer ignores status).
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_telegram_document("TOK", 42, "b.html", None)
    ch.send_telegram_document("TOK", 42, "b.html", b"")
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


def test_send_telegram_photo_posts_multipart(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append((url, kw)))
    ch.send_telegram_photo("TOK", 42, "b.png", b"\x89PNG-bytes", "cap")
    url, kw = calls[0]
    assert url == "https://api.telegram.org/botTOK/sendPhoto"
    assert kw["data"] == {"chat_id": 42, "caption": "cap"}
    assert kw["files"]["photo"] == ("b.png", b"\x89PNG-bytes", "image/png")
    assert "parse_mode" not in kw["data"]


def test_send_telegram_photo_noop_without_creds(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_telegram_photo("", 42, "b.png", b"x")
    ch.send_telegram_photo("TOK", None, "b.png", b"x")
    assert calls == []


def test_send_telegram_photo_noop_without_content(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_telegram_photo("TOK", 42, "b.png", None)
    ch.send_telegram_photo("TOK", 42, "b.png", b"")
    assert calls == []


def test_send_telegram_photo_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_telegram_photo("TOK", 42, "b.png", b"x")   # must not raise


def test_send_telegram_photo_truncates_caption(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda url, **kw: calls.append(kw))
    ch.send_telegram_photo("TOK", 42, "b.png", b"x", "z" * 2000)
    assert len(calls[0]["data"]["caption"]) == 1024


def test_send_telegram_photo_logs_rejected_status(monkeypatch, caplog):
    monkeypatch.setattr(ch.requests, "post",
                        lambda *a, **k: _Resp(400, '{"description":"PHOTO_INVALID"}'))
    with caplog.at_level("WARNING"):
        ch.send_telegram_photo("TOK", 42, "b.png", b"x", "cap")
    assert "Telegram photo rejected: HTTP 400" in caplog.text
    assert "PHOTO_INVALID" in caplog.text


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


def test_send_discord_file_posts_multipart(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append((url, kw)))
    ch.send_discord_file("https://hook", "b.html", b"<html>hi</html>", "cap")
    url, kw = calls[0]
    assert url == "https://hook"
    assert json.loads(kw["data"]["payload_json"]) == {"content": "cap"}
    assert kw["files"]["files[0]"] == ("b.html", b"<html>hi</html>", "text/html")


def test_send_discord_file_honors_content_type(monkeypatch):
    """The briefing now uploads a PNG; Discord must be told so it renders inline
    instead of treating the bytes as a text/html attachment."""
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append(kw))
    ch.send_discord_file("https://hook", "b.png", b"\x89PNG", "cap",
                         content_type="image/png")
    assert calls[0]["files"]["files[0]"] == ("b.png", b"\x89PNG", "image/png")


def test_send_discord_file_content_type_defaults_to_html(monkeypatch):
    """Back-compatible: existing callers that pass no content_type are unchanged."""
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append(kw))
    ch.send_discord_file("https://hook", "b.html", b"<html/>")
    assert calls[0]["files"]["files[0]"][2] == "text/html"


def test_send_discord_file_noop_without_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_discord_file("", "b.html", b"x")
    assert calls == []


def test_send_discord_file_noop_without_content(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_discord_file("https://hook", "b.html", None)
    ch.send_discord_file("https://hook", "b.html", b"")
    assert calls == []


def test_send_discord_file_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_discord_file("https://hook", "b.html", b"x")   # must not raise


def test_send_discord_file_truncates_caption(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append(kw))
    ch.send_discord_file("https://hook", "b.html", b"x", "z" * 3000)
    content = json.loads(calls[0]["data"]["payload_json"])["content"]
    assert len(content) == 2000


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
    monkeypatch.delenv("GAMMA_BRIEFING_WEBHOOK_URL", raising=False)
    cfg = ch.load_config()
    gb = cfg["gamma_briefing"]
    # Ships OFF (opt-in). With it on and no dedicated webhook, an install that has
    # only configured the SIGNAL webhook would start dropping four HTML attachments
    # a day into the signal channel without anyone asking for it.
    assert gb["enabled"] is False
    assert gb["slots"] == ["premarket", "open", "midday", "close"]
    assert gb["webhook_url"] == ""


def test_gamma_briefing_file_values_override_defaults(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"gamma_briefing": {"enabled": False,
                                                "webhook_url": "https://hook"}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    # This asserts the FILE value wins, so a real GAMMA_BRIEFING_WEBHOOK_URL in the
    # developer's environment (the override's whole purpose) would otherwise fail it.
    monkeypatch.delenv("GAMMA_BRIEFING_WEBHOOK_URL", raising=False)
    cfg = ch.load_config()
    assert cfg["gamma_briefing"]["enabled"] is False
    assert cfg["gamma_briefing"]["webhook_url"] == "https://hook"
    # unspecified key still falls back to the default (deep merge, not replace)
    assert cfg["gamma_briefing"]["slots"] == ["premarket", "open", "midday", "close"]


def test_gamma_briefing_webhook_env_overrides_file(tmp_path, monkeypatch):
    # A Discord webhook URL embeds its token, so it gets the same env escape hatch
    # every other secret here has. enabled/slots stay file-only (not secrets).
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"gamma_briefing": {"webhook_url": "https://file"}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    monkeypatch.setenv("GAMMA_BRIEFING_WEBHOOK_URL", "https://env")
    cfg = ch.load_config()
    assert cfg["gamma_briefing"]["webhook_url"] == "https://env"


def test_default_lists_are_copied_not_shared(tmp_path, monkeypatch):
    # _deep_merge copies nested dicts; nested LISTS must be copied too, or a
    # consumer filtering slots in place poisons _DEFAULTS for the whole process.
    monkeypatch.setattr(ch, "_CONFIG_PATH", tmp_path / "nope.json")
    first = ch.load_config()["gamma_briefing"]["slots"]
    assert first is not ch._DEFAULTS["gamma_briefing"]["slots"]
    first.remove("open")
    second = ch.load_config()["gamma_briefing"]["slots"]
    assert second is not first
    assert second == ["premarket", "open", "midday", "close"]


def test_public_names_reexported_from_package():
    from shared.notify import (load_config, send_telegram, send_discord,
                               send_sms, _in_market_hours, _today_ct)
    assert load_config is ch.load_config
    assert send_telegram is ch.send_telegram
    assert _in_market_hours is ch._in_market_hours
    assert _today_ct is ch._today_ct


def test_package_reexports_file_senders():
    import shared.notify as sn
    assert sn.send_telegram_document is ch.send_telegram_document
    assert sn.send_discord_file is ch.send_discord_file
    assert sn.send_telegram_photo is ch.send_telegram_photo
    assert "send_telegram_photo" in sn.__all__


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text


def test_file_senders_log_rejected_status(monkeypatch, caplog):
    """requests does NOT raise on 4xx, so an API rejection would otherwise return
    normally and vanish -- the file never arrives and nothing says why."""
    monkeypatch.setattr(ch.requests, "post",
                        lambda *a, **k: _Resp(400, '{"description":"chat not found"}'))
    with caplog.at_level("WARNING"):
        ch.send_telegram_document("TOK", 42, "b.html", b"x", "cap")
        ch.send_discord_file("https://hook", "b.html", b"x", "cap")
    text = caplog.text
    assert "Telegram document rejected: HTTP 400" in text
    assert "chat not found" in text
    assert "Discord file rejected: HTTP 400" in text


def test_file_senders_quiet_on_success(monkeypatch, caplog):
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: _Resp(200, "ok"))
    with caplog.at_level("WARNING"):
        ch.send_telegram_document("TOK", 42, "b.html", b"x")
        ch.send_discord_file("https://hook", "b.html", b"x")
    assert caplog.text == ""


def test_log_http_never_raises_on_odd_response(monkeypatch):
    ch._log_http("X", None)          # no attributes at all
    ch._log_http("X", object())      # no status_code
