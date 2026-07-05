"""Server-side signal push notifications (Telegram / Discord / Google Fi SMS).

Called from options_svc handlers when a new scanner or captured signal is
published. Pure formatters + key/diff logic are unit-tested; senders are thin
I/O wrappers. Every send is best-effort (never raises into the caller).

Config: shared/notifications.json (gitignored) with env-var overrides. A channel
with no usable creds silently no-ops. Built service-owned (NOT importing the
legacy options-scanner/notifier.py) to avoid its winsound/winotify baggage and
the documented `notifier` cross-app module-name collision.
"""
import html as _html
import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests

from repo_paths import NOTIFICATIONS_CONFIG

log = logging.getLogger(__name__)
_CONFIG_PATH = NOTIFICATIONS_CONFIG

_TZ = ZoneInfo("America/Chicago")
_MULT = 100
_D_GREEN, _D_YELLOW, _D_GRAY = 0x2ECC71, 0xF1C40F, 0x95A5A6

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


def signal_key(s: dict) -> str:
    """Stable identity for a scanner signal (symbol/type/strikes/expiration).

    Mirrors the fields signal_db dedups on. IC folds in the call legs so a
    different call wing is a distinct signal.
    """
    parts = [str(s.get("symbol", "")), str(s.get("type", "")),
             str(s.get("short_strike", "")), str(s.get("long_strike", "")),
             str(s.get("expiration", ""))]
    if str(s.get("type", "")).upper() == "IC":
        parts += [str(s.get("call_short", "")), str(s.get("call_long", ""))]
    return "|".join(parts)


def captured_key(s: dict) -> str:
    """Identity for a captured signal — its signal_id when present, else signal_key."""
    sid = s.get("signal_id")
    return str(sid) if sid not in (None, "") else signal_key(s)


def _strikes_str(s: dict) -> str:
    if str(s.get("type", "")).upper() == "IC":
        return (f"{s.get('short_strike')}/{s.get('long_strike')}p — "
                f"{s.get('call_short', '')}/{s.get('call_long', '')}c")
    return f"{s.get('short_strike')}/{s.get('long_strike')} ({s.get('width', '')}-wide)"


def telegram_signal_text(s: dict) -> str:
    e = lambda v: _html.escape(str(v))
    rr = s.get("rr_pct", 0) or 0
    emoji = "🟢" if rr >= 25 else ("🟡" if rr >= 15 else "⚪")
    return (
        f"{emoji} <b>{e(s.get('symbol'))} {e(s.get('type'))}</b> ({e(s.get('trade_type', ''))})\n"
        f"Exp <code>{e(s.get('expiration'))}</code> • {e(_strikes_str(s))}\n"
        f"Credit <b>${(s.get('credit') or 0):.2f}</b> "
        f"(${(s.get('credit') or 0) * _MULT:,.0f}/ct) • Max loss ${(s.get('max_loss') or 0):.2f}\n"
        f"R:R <b>{rr:.1f}%</b> • PoP {s.get('pop_pct', 0):.0f}% • Δ {s.get('short_delta', 0):.3f}"
    )


def discord_signal_embed(s: dict) -> dict:
    rr = s.get("rr_pct", 0) or 0
    color = _D_GREEN if rr >= 25 else (_D_YELLOW if rr >= 15 else _D_GRAY)
    return {
        "title": f"{s.get('symbol')} {s.get('type')} ({s.get('trade_type', '')})",
        "description": f"Exp {s.get('expiration')} • Strikes {_strikes_str(s)}",
        "color": color,
        "fields": [
            {"name": "Credit", "value": f"${(s.get('credit') or 0):.2f}", "inline": True},
            {"name": "Max Loss", "value": f"${(s.get('max_loss') or 0):.2f}", "inline": True},
            {"name": "R:R", "value": f"{rr:.1f}%", "inline": True},
            {"name": "PoP", "value": f"{s.get('pop_pct', 0):.0f}%", "inline": True},
            {"name": "Δ short", "value": f"{s.get('short_delta', 0):.3f}", "inline": True},
            {"name": "Score", "value": f"{s.get('composite_score', 0):.0f}", "inline": True},
        ],
        "timestamp": datetime.now(_TZ).isoformat(),
    }


def sms_summary_text(sigs: list, kind: str, cap: int = 5) -> str:
    n = len(sigs)
    head = f"{n} new {kind} signal" + ("" if n == 1 else "s")
    lines = [head]
    for s in sigs[:cap]:
        lines.append(f"{s.get('symbol')} {s.get('type')} "
                     f"{_strikes_str(s)} Cr ${ (s.get('credit') or 0):.2f} "
                     f"R:R {(s.get('rr_pct') or 0):.0f}%")
    if n > cap:
        lines.append(f"…+{n - cap} more")
    return "\n".join(lines)


_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_SMTP_HOST, _SMTP_PORT = "smtp.gmail.com", 587
_FI_GATEWAY = "@msg.fi.google.com"


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


def send_discord(webhook_url: str, embed: dict) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=8)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord send failed: %s", exc)


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


def new_keys(current: list, prev: dict | None, today: str):
    """Return (new_keys_in_order, next_state).

    `prev` is {"date", "keys"} or None. On a date change the seen-set resets
    (a persisting signal doesn't re-spam, but each new day's signals fire once).
    Order-preserving + deduped.
    """
    same_day = bool(prev and prev.get("date") == today)
    seen = set(prev["keys"]) if same_day else set()
    out, ordered_seen = [], list(prev["keys"]) if same_day else []
    for k in current:
        if k not in seen:
            seen.add(k)
            out.append(k)
            ordered_seen.append(k)
    # dedup `out` while preserving order (current may repeat)
    out = list(dict.fromkeys(out))
    return out, {"date": today, "keys": list(dict.fromkeys(ordered_seen))}


def load_seen(bus, key: str):
    env = bus.cache_get(key)
    return env.payload if (env is not None and isinstance(env.payload, dict)) else None


def save_seen(bus, key: str, state: dict) -> None:
    bus.cache_set(key, state)
