"""A failed send must not write the credential into the log (audit SE-05).

``requests`` puts the full URL in its connection-error text, and for Telegram
the URL contains the bot token; for Discord the URL IS the credential. Logging
``%s`` of the exception wrote both to the service log on every network blip.
"""
import logging

import pytest
import requests

from shared.notify import channels

TOKEN = "123456789:AAsecretBotTokenValue_xyz"
HOOK = "https://discord.com/api/webhooks/987654321/secretWebhookTail-abc"


def _boom(url, *a, **k):
    raise requests.exceptions.ConnectionError(
        f"HTTPSConnectionPool(host='x', port=443): Max retries exceeded with url: {url}")


@pytest.fixture
def failing(monkeypatch, caplog):
    monkeypatch.setattr(channels.requests, "post", _boom)
    with caplog.at_level(logging.DEBUG, logger=channels.log.name):
        yield caplog


def _text(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


def test_telegram_senders_do_not_log_the_token(failing):
    channels.send_telegram(TOKEN, "42", "hello")
    channels.send_telegram_document(TOKEN, "42", "a.html", b"<p>x</p>")
    channels.send_telegram_photo(TOKEN, "42", "a.png", b"png")
    text = _text(failing)
    assert "AAsecretBotTokenValue_xyz" not in text and TOKEN not in text
    assert text.count("ConnectionError") == 3        # still says what failed


def test_discord_senders_do_not_log_the_webhook(failing):
    channels.send_discord(HOOK, {"title": "t"})
    channels.send_discord_file(HOOK, "a.png", b"png")
    text = _text(failing)
    assert "secretWebhookTail-abc" not in text and "987654321" not in text
    assert text.count("ConnectionError") == 2


def test_a_rejected_send_does_not_echo_a_credential_from_the_body(caplog):
    class Resp:
        status_code = 404
        text = f"Not Found: {HOOK} and bot{TOKEN}"

    with caplog.at_level(logging.DEBUG, logger=channels.log.name):
        channels._log_http("Discord file", Resp())
    text = _text(caplog)
    assert "HTTP 404" in text
    assert "secretWebhookTail-abc" not in text and "AAsecretBotTokenValue_xyz" not in text


def test_no_sender_formats_the_exception_itself():
    """Source guard: ``log.warning("... %s", exc)`` is how the URL got out."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(channels.__file__).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "log"):
                if any(isinstance(a, ast.Name) and a.id == node.name for a in call.args):
                    bad.append(call.lineno)
    assert not bad, f"exception object passed to a log call at lines {bad}"
