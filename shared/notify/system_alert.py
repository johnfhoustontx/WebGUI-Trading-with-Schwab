"""System alerts: one push for "something on the server needs a person".

Three callers, all outside the services (so they still work when a service is
the thing that failed):

* ``tools/notify_failure.py`` - systemd runs it when a unit ends up FAILED: a
  service that crash-looped until its restart budget ran out, or a timer job
  (the nightly backup, a report) whose run exited non-zero;
* ``tools/token_watch.py`` - the Schwab refresh token is inside its last hours,
  or Schwab has rejected it.

Until 2026-10-03 each of these was visible on the Status page and nowhere else:
with no browser tab open, nobody was told (audit AR-03).

Category ``system`` in ``config/notify.toml`` / ``shared/notifications.json``'s
``routes``, so it can be sent to its own chat or switched off like any other
feed. The destination is resolved through ``channels.telegram_target`` /
``discord_target`` - the two chokepoints the per-category switches live at.
Never raises: an alert about a failure must not become a second failure.
"""
import html
import logging

from shared.notify.channels import (discord_target, load_config, send_discord,
                                    send_telegram, telegram_target)

log = logging.getLogger(__name__)

CATEGORY = "system"
_DISCORD_RED = 0xC0392B


def send(title, lines, *, config=None) -> list:
    """Push ``title`` and ``lines`` to the ``system`` category's channels.

    Returns the channels a send was made on, in order. Nothing is sent - and
    ``[]`` returned - when notifications are off (the master switch, a dev
    checkout, a test run), when the category is switched off, or when no
    destination is configured.
    """
    try:
        cfg = config if config is not None else load_config()
    except Exception:  # noqa: BLE001 - no config is "notifications off"
        log.warning("system alert not sent: notification config unreadable",
                    exc_info=True)
        return []
    if not isinstance(cfg, dict) or not cfg.get("enabled", True):
        return []

    title = str(title)
    body = [str(line) for line in (lines or [])]
    sent = []

    try:
        tok, chat = telegram_target(cfg, CATEGORY)
        if tok and chat not in (None, ""):
            text = "<b>" + html.escape(title) + "</b>\n" + "\n".join(
                html.escape(line) for line in body)
            send_telegram(tok, chat, text)
            sent.append("telegram")
    except Exception as exc:  # noqa: BLE001
        # The exception TYPE only: a connection error's text carries the bot
        # token in the request URL.
        log.warning("system alert: telegram send failed (%s)", type(exc).__name__)

    try:
        webhook = discord_target(cfg, CATEGORY)
        if webhook:
            send_discord(webhook, {"title": title, "description": "\n".join(body),
                                   "color": _DISCORD_RED})
            sent.append("discord")
    except Exception as exc:  # noqa: BLE001
        log.warning("system alert: discord send failed (%s)", type(exc).__name__)

    return sent
