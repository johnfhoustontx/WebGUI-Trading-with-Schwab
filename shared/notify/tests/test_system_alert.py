"""System alerts: the push for "something on the server needs a person".

Audit AR-03 (2026-10-03): failure detection existed only in an open browser
tab. A unit that crash-looped until systemd gave up, a nightly backup that
failed, a Schwab refresh token inside its last two days - each was visible on
the Status page and nowhere else.

``system_alert.send`` is the one sender for all three, on the existing
Telegram and Discord channels under the ``system`` category.
"""
import pytest

from shared.notify import channels, system_alert


CFG = {"enabled": True,
       "telegram": {"enabled": True, "bot_token": "T", "chat_id": 1},
       "discord": {"enabled": True, "webhook_url": "https://discord.test/hook"}}


@pytest.fixture
def sent(monkeypatch):
    out = {"telegram": [], "discord": []}
    monkeypatch.setattr(system_alert, "send_telegram",
                        lambda tok, chat, text: out["telegram"].append((tok, chat, text)))
    monkeypatch.setattr(system_alert, "send_discord",
                        lambda url, embed: out["discord"].append((url, embed)))
    monkeypatch.setattr(channels, "_switch_on", lambda category, channel: True)
    return out


def test_system_is_a_routable_category():
    assert "system" in channels.ROUTE_CATEGORIES


def test_an_alert_goes_to_both_channels(sent):
    assert system_alert.send("Backup failed", ["trading-prod-backup.service"],
                             config=CFG) == ["telegram", "discord"]
    (tok, chat, text), = sent["telegram"]
    assert (tok, chat) == ("T", 1)
    assert "Backup failed" in text and "trading-prod-backup.service" in text
    (url, embed), = sent["discord"]
    assert url == "https://discord.test/hook"
    assert embed["title"] == "Backup failed"
    assert "trading-prod-backup.service" in embed["description"]


def test_the_master_switch_off_sends_nothing(sent):
    assert system_alert.send("x", ["y"], config={**CFG, "enabled": False}) == []
    assert sent == {"telegram": [], "discord": []}


def test_the_categorys_own_switch_is_honoured(sent, monkeypatch):
    monkeypatch.setattr(channels, "_switch_on",
                        lambda category, channel: not (category == "system"
                                                       and channel == "discord"))
    assert system_alert.send("x", ["y"], config=CFG) == ["telegram"]
    assert sent["discord"] == []


def test_a_channel_with_no_target_is_skipped(sent):
    cfg = {"enabled": True, "telegram": {"enabled": True, "bot_token": "T", "chat_id": 1}}
    assert system_alert.send("x", ["y"], config=cfg) == ["telegram"]


def test_a_channel_that_raises_does_not_stop_the_other_and_never_raises(sent, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("telegram is down")

    monkeypatch.setattr(system_alert, "send_telegram", boom)
    assert system_alert.send("x", ["y"], config=CFG) == ["discord"]


def test_markup_in_the_message_is_escaped_for_telegram(sent):
    system_alert.send("a <b> title", ["exit code <1> & more"], config=CFG)
    text = sent["telegram"][0][2]
    assert "&lt;1&gt;" in text and "&amp;" in text
    assert "<1>" not in text


def test_an_unreadable_config_sends_nothing_and_never_raises(monkeypatch, sent):
    def boom():
        raise OSError("no notifications.json")

    monkeypatch.setattr(system_alert, "load_config", boom)
    assert system_alert.send("x", ["y"]) == []
