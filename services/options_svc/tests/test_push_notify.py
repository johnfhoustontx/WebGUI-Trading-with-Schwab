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
