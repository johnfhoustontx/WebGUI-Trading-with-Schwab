"""Server-side signal push notifications (Telegram / Discord / Google Fi SMS).

Called from options_svc handlers when a new scanner or captured signal is
published. Pure formatters + key/diff logic are unit-tested; senders are thin
I/O wrappers. Every send is best-effort (never raises into the caller).

Config: shared/notifications.json (gitignored) with env-var overrides. A channel
with no usable creds silently no-ops. Built service-owned (NOT importing the
legacy options-scanner/notifier.py) to avoid its winsound/winotify baggage and
the documented `notifier` cross-app module-name collision.
"""
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText

import requests

from repo_paths import NOTIFICATIONS_CONFIG

log = logging.getLogger(__name__)
_CONFIG_PATH = NOTIFICATIONS_CONFIG

_DEFAULTS = {
    "enabled": True,
    "market_hours_only": True,
    "min_score": 0,
    "telegram": {"bot_token": "", "chat_id": 0},
    "discord": {"webhook_url": ""},
    "sms": {"fi_number": "", "smtp_user": "", "smtp_app_password": ""},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """Merged config: DEFAULTS < file < env. Never raises (bad file → defaults)."""
    cfg = _deep_merge(_DEFAULTS, {})
    try:
        raw = json.loads(_CONFIG_PATH.read_text())
        if isinstance(raw, dict):
            cfg = _deep_merge(cfg, raw)
    except Exception:
        pass
    # Env overrides (win over file).
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"]["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            cfg["telegram"]["chat_id"] = int(os.environ["TELEGRAM_CHAT_ID"])
        except ValueError:
            pass
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        cfg["discord"]["webhook_url"] = os.environ["DISCORD_WEBHOOK_URL"]
    if os.environ.get("FI_SMS_NUMBER"):
        cfg["sms"]["fi_number"] = os.environ["FI_SMS_NUMBER"]
    if os.environ.get("SMS_SMTP_USER"):
        cfg["sms"]["smtp_user"] = os.environ["SMS_SMTP_USER"]
    if os.environ.get("SMS_SMTP_APP_PASSWORD"):
        cfg["sms"]["smtp_app_password"] = os.environ["SMS_SMTP_APP_PASSWORD"]
    if os.environ.get("NOTIFY_ENABLED"):
        cfg["enabled"] = os.environ["NOTIFY_ENABLED"].lower() not in ("0", "false", "no")
    return cfg
