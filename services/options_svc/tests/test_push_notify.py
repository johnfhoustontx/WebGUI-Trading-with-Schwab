import json

from services.options_svc import push_notify as pn


def test_load_config_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({
        "enabled": True, "min_score": 20,
        "telegram": {"bot_token": "T", "chat_id": 5},
        "discord": {"webhook_url": "https://d"},
        "sms": {"fi_number": "5551234567", "smtp_user": "u@gmail.com",
                "smtp_app_password": "pw"},
    }))
    monkeypatch.setattr(pn, "_CONFIG_PATH", p)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = pn.load_config()
    assert cfg["min_score"] == 20
    assert cfg["telegram"]["bot_token"] == "T"
    assert cfg["sms"]["fi_number"] == "5551234567"


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "FILE", "chat_id": 1}}))
    monkeypatch.setattr(pn, "_CONFIG_PATH", p)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ENV")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://envhook")
    cfg = pn.load_config()
    assert cfg["telegram"]["bot_token"] == "ENV"
    assert cfg["discord"]["webhook_url"] == "https://envhook"


def test_missing_file_returns_disabled_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "_CONFIG_PATH", tmp_path / "nope.json")
    for k in ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "FI_SMS_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    cfg = pn.load_config()
    assert cfg["telegram"]["bot_token"] == ""
    assert cfg["enabled"] is True  # default-on; channels self-gate on creds


def test_signal_key_stable_and_distinct():
    a = {"symbol": "SPY", "type": "PCS", "short_strike": 500, "long_strike": 495,
         "expiration": "2026-07-10"}
    b = dict(a, short_strike=499)
    assert pn.signal_key(a) == pn.signal_key(a)
    assert pn.signal_key(a) != pn.signal_key(b)


def test_signal_key_ic_folds_call_legs():
    ic1 = {"symbol": "QQQ", "type": "IC", "short_strike": 400, "long_strike": 395,
           "call_short": 420, "call_long": 425, "expiration": "2026-07-10"}
    ic2 = dict(ic1, call_short=421)
    assert pn.signal_key(ic1) != pn.signal_key(ic2)


def test_captured_key_prefers_signal_id():
    assert pn.captured_key({"signal_id": "abc", "symbol": "SPY"}) == "abc"


def _sig():
    return {"symbol": "SPY", "type": "PCS", "trade_type": "0DTE",
            "short_strike": 500, "long_strike": 495, "width": 5,
            "expiration": "2026-07-10", "credit": 1.20, "max_loss": 3.80,
            "rr_pct": 31.6, "pop_pct": 72, "short_delta": -0.18,
            "net_theta": 0.05, "short_iv": 14.2, "breakeven": 498.8,
            "composite_score": 80}


def test_telegram_text_has_symbol_and_credit():
    t = pn.telegram_signal_text(_sig())
    assert "SPY" in t and "PCS" in t and "1.20" in t


def test_discord_embed_has_fields():
    e = pn.discord_signal_embed(_sig())
    assert e["title"].startswith("SPY PCS")
    names = {f["name"] for f in e["fields"]}
    assert "Credit" in names and "R:R" in names


def test_sms_summary_batches_and_caps():
    sigs = [dict(_sig(), symbol=f"S{i}") for i in range(8)]
    txt = pn.sms_summary_text(sigs, kind="scanner", cap=5)
    assert txt.startswith("8 new scanner")
    assert txt.count("\n") <= 6  # header + <=5 lines
    assert "S0" in txt


def test_send_telegram_posts_to_bot_api(monkeypatch):
    calls = {}
    monkeypatch.setattr(pn.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    pn.send_telegram("TOK", 42, "hello")
    assert calls["url"].endswith("/botTOK/sendMessage")
    assert calls["json"]["chat_id"] == 42 and calls["json"]["parse_mode"] == "HTML"


def test_send_discord_posts_embed(monkeypatch):
    calls = {}
    monkeypatch.setattr(pn.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    pn.send_discord("https://hook", {"title": "x"})
    assert calls["url"] == "https://hook"
    assert calls["json"]["embeds"] == [{"title": "x"}]


def test_send_sms_emails_fi_gateway(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg): sent["to"] = msg["To"]; sent["body"] = msg.get_payload()

    monkeypatch.setattr(pn.smtplib, "SMTP", FakeSMTP)
    pn.send_sms("5551234567", "u@gmail.com", "pw", "2 new signals")
    assert sent["to"] == "5551234567@msg.fi.google.com"
    assert sent["login"] == ("u@gmail.com", "pw")
    assert "2 new signals" in sent["body"]


def test_senders_never_raise(monkeypatch):
    def boom(*a, **k): raise RuntimeError("net")
    monkeypatch.setattr(pn.requests, "post", boom)
    pn.send_telegram("T", 1, "x")   # no exception
    pn.send_discord("https://h", {})
