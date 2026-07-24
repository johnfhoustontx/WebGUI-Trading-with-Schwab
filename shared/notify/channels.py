"""Shared, domain-agnostic push-notification channels + config + market gate.

Lifted out of services/options_svc/push_notify.py so a second service
(sentiment_svc) can reuse the exact same proven senders, config resolution, and
market-hours/holiday gate. NO behavior change — this is the same code, moved.

Config: shared/notifications.json (gitignored) with env-var overrides. A channel
with no usable creds silently no-ops. Every send is best-effort (never raises
into the caller). Built service-owned (NOT importing the legacy
options-scanner/notifier.py) to avoid its winsound/winotify baggage and the
documented `notifier` cross-app module-name collision.
"""
import datetime as _dt
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests

from repo_paths import NOTIFICATIONS_CONFIG

log = logging.getLogger(__name__)
_CONFIG_PATH = NOTIFICATIONS_CONFIG

_TZ = ZoneInfo("America/Chicago")

_DEFAULTS = {
    "enabled": True,
    "market_hours_only": True,
    "min_score": 0,
    "telegram": {"bot_token": "", "chat_id": 0},
    "discord": {"webhook_url": ""},
    "sms": {"fi_number": "", "smtp_user": "", "smtp_app_password": ""},
    # Gamma Analyze briefing -> HTML file attachment (options_svc, scheduled slots
    # only). `slots` subsets which of the four push, so thinning the cadence needs
    # no code change. `webhook_url` is a DEDICATED Discord webhook (falls back to
    # discord.webhook_url when blank) so briefings stay out of the signal feed.
    # Ships OFF, like `twitter` below: with `enabled` on and no dedicated webhook
    # set, an install that has only configured the SIGNAL webhook would silently
    # start dropping four HTML attachments a day into the signal channel — the very
    # thing the dedicated webhook exists to prevent. Opt in explicitly.
    "gamma_briefing": {
        "enabled": False,
        "slots": ["premarket", "open", "midday", "close"],
        "webhook_url": "",
    },
    # X/Twitter public post channel (options scanner signals only). OFF by
    # default; ships with dry_run ON so it formats+logs without posting until you
    # add OAuth 1.0a keys AND flip enabled + dry_run. min_score is the PUBLIC
    # gate (independent of the top-level min_score); daily_cap guards the
    # free-tier monthly write cap. hashtags/discord_url/extra_text/disclaimer are
    # the static footer.
    "twitter": {
        "enabled": False,
        "dry_run": True,
        "min_score": 70,
        "daily_cap": 40,
        "api_key": "", "api_secret": "", "access_token": "", "access_secret": "",
        "hashtags": [], "discord_url": "", "extra_text": "",
        "disclaimer": "Not advice. Paper/educational.",
    },
}


def _copy_containers(v):
    """Deep-copy the containers in a default value (dicts AND lists).

    A shallow `dict(v)` leaves nested lists shared, so a caller filtering e.g.
    gamma_briefing.slots or twitter.hashtags in place would poison _DEFAULTS for
    the rest of the process."""
    if isinstance(v, dict):
        return {k: _copy_containers(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_copy_containers(x) for x in v]
    return v


def _deep_merge(base: dict, over: dict) -> dict:
    out = {k: _copy_containers(v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None) -> dict:
    """Merged config: DEFAULTS < file < env. Never raises (bad file → defaults).

    `path` overrides the module-level `_CONFIG_PATH` (which callers may also
    monkeypatch). Both are honored so an options-domain wrapper can pass its own
    path while shared tests patch the module global.
    """
    cfg = _deep_merge(_DEFAULTS, {})
    src = path if path is not None else _CONFIG_PATH
    try:
        raw = json.loads(src.read_text())
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
    # The dedicated briefing webhook embeds a token — same env escape hatch as
    # every other secret here (enabled/slots stay file-only; they aren't secrets).
    if os.environ.get("GAMMA_BRIEFING_WEBHOOK_URL"):
        cfg["gamma_briefing"]["webhook_url"] = os.environ["GAMMA_BRIEFING_WEBHOOK_URL"]
    # Twitter/X OAuth 1.0a keys (env wins over file), so secrets can stay out of
    # the config file.
    for env_name, key in (("TWITTER_API_KEY", "api_key"),
                          ("TWITTER_API_SECRET", "api_secret"),
                          ("TWITTER_ACCESS_TOKEN", "access_token"),
                          ("TWITTER_ACCESS_SECRET", "access_secret")):
        if os.environ.get(env_name):
            cfg["twitter"][key] = os.environ[env_name]
    if os.environ.get("TWITTER_ENABLED"):
        cfg["twitter"]["enabled"] = os.environ["TWITTER_ENABLED"].lower() not in ("0", "false", "no")
    return cfg


_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_DOC_API = "https://api.telegram.org/bot{token}/sendDocument"
_TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"
_TG_CAPTION_MAX = 1024      # Telegram sendDocument/sendPhoto caption ceiling
_UPLOAD_TIMEOUT = 60        # images run larger than the ~14 KB HTML docs
_DISCORD_CONTENT_MAX = 2000  # Discord webhook message content ceiling
_SMTP_HOST, _SMTP_PORT = "smtp.gmail.com", 587
_FI_GATEWAY = "@msg.fi.google.com"
_HTTP_BODY_LOG_MAX = 300


def _log_http(what: str, resp) -> None:
    """Warn when a channel accepted the request but REJECTED the message.

    ``requests`` does not raise on 4xx/5xx, so without this a rejected send returns
    normally and vanishes — the file simply never arrives and nothing explains why.
    That exact failure mode cost a live debugging session. Delivery still isn't
    guaranteed (a 2xx only means the API accepted it), but a rejection is now
    visible in the service log. Never raises — a malformed response object must not
    turn a best-effort notification into an exception."""
    try:
        code = getattr(resp, "status_code", None)
        if code is None or 200 <= code < 300:
            return
        body = (getattr(resp, "text", "") or "")[:_HTTP_BODY_LOG_MAX]
        log.warning("%s rejected: HTTP %s %s", what, code, body)
    except Exception:  # noqa: BLE001 — logging must never break a send
        pass


def send_telegram(token: str, chat_id, text: str) -> None:
    if not token or not chat_id:
        return
    try:
        requests.post(_TELEGRAM_API.format(token=token), json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=8)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("Telegram send failed: %s", exc)


def send_telegram_document(token: str, chat_id, filename: str, content: bytes,
                           caption: str = "") -> None:
    """Upload a file to Telegram via sendDocument (multipart).

    Used for artifacts a chat message cannot express — Telegram's HTML parse mode
    accepts only a ~10-tag subset, so a full infographic must travel as a file the
    recipient opens in a browser. Caption is PLAIN text (no parse_mode), so no
    field needs HTML-escaping. Best-effort, like every sender here: no creds → no-op,
    any failure → warn, never raises.

    Empty `content` also no-ops: requests SKIPS a None file part rather than
    raising, so posting anyway would send a document-less request that 400s —
    and this layer ignores the response, so that failure would be silent."""
    if not token or not chat_id or not content:
        return
    try:
        resp = requests.post(
            _TELEGRAM_DOC_API.format(token=token),
            data={"chat_id": chat_id, "caption": (caption or "")[:_TG_CAPTION_MAX]},
            files={"document": (filename, content, "text/html")},
            timeout=20,   # longer than the 8s text timeout — this is an upload
        )
        _log_http("Telegram document", resp)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("Telegram document send failed: %s", exc)


def send_telegram_photo(token: str, chat_id, filename: str, content: bytes,
                        caption: str = "") -> None:
    """Upload an image to Telegram via sendPhoto (multipart).

    Preferred over ``send_telegram_document`` for a rendered briefing: a photo
    renders INLINE in the chat (no tap to open, no download), where a document is
    a file card. Telegram downscales large photos server-side, which is fine — the
    source is rendered at a 2x device scale precisely so it survives that.

    Caption is PLAIN text (no parse_mode) so nothing needs HTML-escaping.
    Best-effort like every sender here: no creds/content → no-op, any failure →
    warn, never raises."""
    if not token or not chat_id or not content:
        return
    try:
        resp = requests.post(
            _TELEGRAM_PHOTO_API.format(token=token),
            data={"chat_id": chat_id, "caption": (caption or "")[:_TG_CAPTION_MAX]},
            files={"photo": (filename, content, "image/png")},
            timeout=_UPLOAD_TIMEOUT,
        )
        _log_http("Telegram photo", resp)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("Telegram photo send failed: %s", exc)


def send_discord(webhook_url: str, embed: dict) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=8)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord send failed: %s", exc)


def send_discord_file(webhook_url: str, filename: str, content: bytes,
                      caption: str = "", content_type: str = "text/html") -> None:
    """Upload a file to a Discord webhook (multipart).

    Discord accepts embeds, not HTML, so a rich document travels as an attachment
    with an optional plain-text caption. `content_type` must match the payload —
    an ``image/png`` attachment renders INLINE, while ``text/html`` gets
    auto-previewed as syntax-highlighted raw source (the reason the gamma briefing
    moved from HTML to a rendered PNG). Defaults to text/html for back-compat.

    Best-effort: no webhook → no-op, any failure (incl. a 429 rate limit) → warn,
    never raises. Empty `content` no-ops for the same reason as the Telegram
    sender — a missing file part yields an empty-message 400 nobody would ever
    see."""
    if not webhook_url or not content:
        return
    try:
        resp = requests.post(
            webhook_url,
            data={"payload_json": json.dumps(
                {"content": (caption or "")[:_DISCORD_CONTENT_MAX]})},
            files={"files[0]": (filename, content, content_type)},
            timeout=_UPLOAD_TIMEOUT,
        )
        _log_http("Discord file", resp)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord file send failed: %s", exc)


def send_sms(fi_number: str, smtp_user: str, smtp_pw: str, body: str,
             subject: str = "") -> None:
    if not (fi_number and smtp_user and smtp_pw):
        return
    try:
        msg = MIMEText(body)
        msg["From"] = smtp_user
        msg["To"] = f"{fi_number}{_FI_GATEWAY}"
        msg["Subject"] = subject
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_pw)
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("Fi SMS send failed: %s", exc)


# Local NYSE full-closure holidays + trading window, copied from webgui/alerts.py
# so this gate agrees with the rest of the stack WITHOUT importing that module
# (alerts.py pulls in pages.options.scanner → NiceGUI/engine deps we don't want
# in the always-on service process). **Update yearly** alongside alerts._HOLIDAYS.
_MKT_OPEN, _MKT_CLOSE = _dt.time(8, 0), _dt.time(15, 0)   # CT trading window
_HOLIDAYS = frozenset({
    # 2026
    _dt.date(2026, 1, 1), _dt.date(2026, 1, 19), _dt.date(2026, 2, 16), _dt.date(2026, 4, 3),
    _dt.date(2026, 5, 25), _dt.date(2026, 6, 19), _dt.date(2026, 7, 3), _dt.date(2026, 9, 7),
    _dt.date(2026, 11, 26), _dt.date(2026, 12, 25),
    # 2027
    _dt.date(2027, 1, 1), _dt.date(2027, 1, 18), _dt.date(2027, 2, 15), _dt.date(2027, 3, 26),
    _dt.date(2027, 5, 31), _dt.date(2027, 6, 18), _dt.date(2027, 7, 5), _dt.date(2027, 9, 6),
    _dt.date(2027, 11, 25), _dt.date(2027, 12, 24),
})


def _today_ct() -> str:
    return _dt.datetime.now(_TZ).date().isoformat()


def _in_market_hours() -> bool:
    """Trading-day 08:00–15:00 CT. Uses a local weekday + holiday check (a copy of
    webgui/alerts.py's calendar) so the gate agrees with the rest of the stack
    without importing that NiceGUI-coupled module. Defensive → True on any error
    (fail-open: better a rare off-hours notify than silently dropping all)."""
    try:
        ct = _dt.datetime.now(_TZ)
        return (ct.weekday() < 5 and ct.date() not in _HOLIDAYS
                and _MKT_OPEN <= ct.time() <= _MKT_CLOSE)
    except Exception:
        return True
