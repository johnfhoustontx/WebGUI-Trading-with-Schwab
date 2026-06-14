"""
notifier.py - Notification System for Options Scanner
Version: 1.0.0
Last Updated: 2026-04-03

Handles Windows toast notifications, audio alerts, and console logging.
Designed for easy addition of Telegram and Discord channels later.

Usage:
    from notifier import Notifier
    n = Notifier(audio=True, toast=True)
    n.signal_alert(signal_dict)

Future integrations (add keys to config):
    n = Notifier(telegram_token="...", telegram_chat_id="...",
                 discord_webhook="...")

Version 1.0.0 Changes:
- Windows toast notifications via winotify
- Audio alerts via winsound
- Pluggable channel architecture for future Telegram/Discord
"""

import html
import time
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

log = logging.getLogger("scanner")
TZ = ZoneInfo("America/Chicago")

# Icon for toast notifications
ICON_PATH = Path(__file__).parent / "assets" / "scanner_icon.ico"

#############################################
# WINDOWS TOAST NOTIFICATIONS
#############################################

_winotify_available = False
try:
    from winotify import Notification, audio as wn_audio
    _winotify_available = True
except ImportError:
    try:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "winotify", "-q"])
        from winotify import Notification, audio as wn_audio
        _winotify_available = True
    except Exception:
        log.warning("winotify not available — toast notifications disabled")

#############################################
# REQUESTS (for Discord/Telegram HTTP)
#############################################

_requests_available = False
try:
    import requests
    _requests_available = True
except ImportError:
    try:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
        import requests
        _requests_available = True
    except Exception:
        log.warning("requests not available — Discord/Telegram notifications disabled")

#############################################
# AUDIO
#############################################

def _play_beep(pattern="signal"):
    """Windows beep patterns."""
    try:
        import winsound
        if pattern == "signal":
            winsound.Beep(880, 150)
            time.sleep(0.03)
            winsound.Beep(1100, 150)
        elif pattern == "high_value":
            for f in [660, 880, 1100, 1320]:
                winsound.Beep(f, 120)
                time.sleep(0.02)
        elif pattern == "scan_start":
            winsound.Beep(440, 80)
        elif pattern == "trade_executed":
            winsound.Beep(1320, 100)
            time.sleep(0.05)
            winsound.Beep(1320, 100)
            time.sleep(0.05)
            winsound.Beep(1760, 200)
        elif pattern == "error":
            winsound.Beep(300, 400)
    except Exception:
        pass

#############################################
# TOAST NOTIFICATION BUILDER
#############################################

def _send_toast(title, body, signal_type="info"):
    """Send a Windows 10/11 toast notification."""
    if not _winotify_available:
        return

    try:
        toast = Notification(
            app_id="Options Scanner",
            title=title,
            msg=body,
            duration="short",
        )

        # Set icon if available
        if ICON_PATH.exists():
            toast.set_icon(str(ICON_PATH))

        # Set audio based on type
        if signal_type == "high_value":
            toast.set_audio(wn_audio.LoopingCall2, loop=False)
        elif signal_type == "trade":
            toast.set_audio(wn_audio.SMS, loop=False)
        elif signal_type == "error":
            toast.set_audio(wn_audio.Reminder, loop=False)
        else:
            toast.set_audio(wn_audio.Default, loop=False)

        toast.show()
    except Exception as e:
        log.debug(f"Toast failed: {e}")

#############################################
# TELEGRAM
#############################################

def _telegram_signal_text(s):
    mult = 100
    e = lambda v: html.escape(str(v))
    if s["type"] == "IC":
        strikes = (f"{e(s['short_strike'])}/{e(s['long_strike'])}p — "
                   f"{e(s.get('call_short',''))}/{e(s.get('call_long',''))}c")
    else:
        strikes = f"{e(s['short_strike'])}/{e(s['long_strike'])} ({e(s.get('width',''))}-wide)"
    rr = s.get("rr_pct", 0)
    emoji = "🟢" if rr >= 25 else ("🟡" if rr >= 15 else "⚪")
    return (
        f"{emoji} <b>{e(s['symbol'])} {e(s['type'])}</b> ({e(s.get('trade_type',''))})\n"
        f"Exp <code>{e(s['expiration'])}</code> • {strikes}\n"
        f"Credit <b>${s['credit']:.2f}</b> (${s['credit']*mult:,.0f}/ct) • "
        f"Max loss ${s['max_loss']:.2f}\n"
        f"R:R <b>{rr:.1f}%</b> • PoP {s.get('pop_pct',0):.0f}% • "
        f"Δ {s.get('short_delta',0):.3f}\n"
        f"Θ ${s.get('net_theta',0):.3f}/day • IV {s.get('short_iv',0):.1f}% • "
        f"BE {e(s.get('breakeven','—'))}"
    )


def _telegram_trade_text(t):
    return f"✅ <b>EXECUTED</b>\n<pre>{html.escape(_format_trade_notification(t))}</pre>"


def _telegram_error_text(message):
    return f"🔴 <b>Scanner error</b>\n<code>{html.escape(str(message)[:3500])}</code>"


def _send_telegram(token, chat_id, payload, kind="signal"):
    """Send a Telegram message. kind ∈ {'signal','trade','error'}."""
    if not _requests_available or not token or not chat_id:
        return
    try:
        if kind == "signal":
            text = _telegram_signal_text(payload)
        elif kind == "trade":
            text = _telegram_trade_text(payload)
        elif kind == "error":
            text = _telegram_error_text(payload)
        else:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=8)
    except Exception as e:
        log.warning(f"Telegram send failed ({kind}): {e}")

#############################################
# DISCORD
#############################################

_DISCORD_COLOR_GREEN = 0x2ECC71
_DISCORD_COLOR_YELLOW = 0xF1C40F
_DISCORD_COLOR_GRAY = 0x95A5A6
_DISCORD_COLOR_BLUE = 0x3498DB
_DISCORD_COLOR_RED = 0xE74C3C


def _discord_signal_embed(s):
    mult = 100
    if s["type"] == "IC":
        strikes = (f"{s['short_strike']}/{s['long_strike']}p — "
                   f"{s.get('call_short','')}/{s.get('call_long','')}c")
    else:
        strikes = f"{s['short_strike']}/{s['long_strike']} ({s.get('width','')}-wide)"
    rr = s.get("rr_pct", 0)
    color = (_DISCORD_COLOR_GREEN if rr >= 25
             else _DISCORD_COLOR_YELLOW if rr >= 15
             else _DISCORD_COLOR_GRAY)
    return {
        "title": f"{s['symbol']} {s['type']} ({s.get('trade_type','')})",
        "description": f"Exp {s['expiration']} • Strikes {strikes}",
        "color": color,
        "fields": [
            {"name": "Credit",    "value": f"${s['credit']:.2f} (${s['credit']*mult:,.0f}/ct)", "inline": True},
            {"name": "Max Loss",  "value": f"${s['max_loss']:.2f}",                              "inline": True},
            {"name": "R:R",       "value": f"{rr:.1f}%",                                         "inline": True},
            {"name": "PoP",       "value": f"{s.get('pop_pct',0):.0f}%",                         "inline": True},
            {"name": "Δ short",   "value": f"{s.get('short_delta',0):.3f}",                      "inline": True},
            {"name": "Θ/day",     "value": f"${s.get('net_theta',0):.3f}",                       "inline": True},
            {"name": "IV",        "value": f"{s.get('short_iv',0):.1f}%",                        "inline": True},
            {"name": "Breakeven", "value": str(s.get("breakeven", "—")),                          "inline": True},
        ],
        "timestamp": datetime.now(TZ).isoformat(),
    }


def _discord_trade_embed(t):
    return {
        "title": f"EXECUTED: {t['symbol']} {t['strategy']}",
        "description": _format_trade_notification(t),
        "color": _DISCORD_COLOR_BLUE,
        "timestamp": datetime.now(TZ).isoformat(),
    }


def _discord_error_embed(message):
    return {
        "title": "Scanner error",
        "description": str(message)[:1900],
        "color": _DISCORD_COLOR_RED,
        "timestamp": datetime.now(TZ).isoformat(),
    }


def _send_discord(webhook_url, payload, kind="signal"):
    """Send a Discord webhook message. kind ∈ {'signal','trade','error'}."""
    if not _requests_available or not webhook_url:
        return
    try:
        if kind == "signal":
            embed = _discord_signal_embed(payload)
        elif kind == "trade":
            embed = _discord_trade_embed(payload)
        elif kind == "error":
            embed = _discord_error_embed(payload)
        else:
            return
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=8)
    except Exception as e:
        log.warning(f"Discord send failed ({kind}): {e}")

#############################################
# SIGNAL FORMATTER
#############################################

def _format_signal_short(s):
    """One-line signal summary for notifications."""
    mult = 100
    if s["type"] == "IC":
        strikes = f"{s['short_strike']}/{s['long_strike']}p — {s.get('call_short','')}/{s.get('call_long','')}c"
    else:
        strikes = f"{s['short_strike']}/{s['long_strike']}"
    return (f"{s['symbol']} {s['type']} {strikes} | "
            f"Cr=${s['credit']:.2f} (${s['credit']*mult:,.0f}) | "
            f"R:R {s['rr_pct']:.1f}% | PoP {s.get('pop_pct',0):.0f}%")


def _format_signal_detail(s):
    """Multi-line signal detail for Telegram/Discord."""
    mult = 100
    if s["type"] == "IC":
        strikes = f"{s['short_strike']}/{s['long_strike']}p — {s.get('call_short','')}/{s.get('call_long','')}c"
    else:
        strikes = f"{s['short_strike']}/{s['long_strike']} ({s['width']}-wide)"
    lines = [
        f"{'='*30}",
        f"{s['symbol']} {s['type']} ({s.get('trade_type','')}) — {s['expiration']}",
        f"Strikes: {strikes}",
        f"Credit: ${s['credit']:.2f} (${s['credit']*mult:,.0f}/ct)",
        f"Max loss: ${s['max_loss']:.2f} (${s['max_loss']*mult:,.0f}/ct)",
        f"R:R: {s['rr_pct']:.1f}%  |  PoP: {s.get('pop_pct',0):.0f}%",
        f"Delta: {s.get('short_delta',0):.4f}  |  Theta: ${s.get('net_theta',0):.3f}/day",
        f"IV: {s.get('short_iv',0):.1f}%  |  Breakeven: {s.get('breakeven','')}",
    ]
    return "\n".join(lines)


def _format_trade_notification(trade):
    """Format paper trade execution for notifications."""
    return (f"PAPER TRADE: {trade['symbol']} {trade['strategy']} "
            f"{trade['short_strike']}/{trade['long_strike']} x{trade['quantity']}\n"
            f"Credit: ${trade['entry_credit_total']:,.0f}  |  "
            f"Max risk: ${trade['max_loss_total']:,.0f}")

#############################################
# NOTIFIER CLASS
#############################################

class Notifier:
    """Central notification dispatcher.
    
    Sends alerts through enabled channels:
    - Audio beeps (Windows winsound)
    - Windows toast notifications (winotify)
    - Telegram (future - pass telegram_token + telegram_chat_id)
    - Discord (future - pass discord_webhook)
    """

    def __init__(self, audio=True, toast=True,
                 telegram_token=None, telegram_chat_id=None,
                 discord_webhook=None, load_config=False):
        self.audio = audio
        self.toast = toast
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook
        self.telegram_enabled = True
        self.discord_enabled = True

        if load_config:
            self._load_notification_config()

        # Log which channels are active
        active = []
        if self.discord_webhook: active.append("discord")
        if self.telegram_token and self.telegram_chat_id: active.append("telegram")
        if active:
            log.info(f"Notifier remote channels active: {', '.join(active)}")
        else:
            log.info("Notifier: no remote channels configured (toast/audio only)")

    def _load_notification_config(self):
        """Populate missing channel creds from env vars, then config_notifications.py.

        Explicit constructor kwargs win over env, env wins over file.
        """
        import os
        # Env first
        if not self.telegram_token:
            self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN") or None
        if not self.telegram_chat_id:
            cid = os.environ.get("TELEGRAM_CHAT_ID")
            if cid:
                try: self.telegram_chat_id = int(cid)
                except ValueError: pass
        if not self.discord_webhook:
            self.discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL") or None

        # File fallback
        try:
            import config_notifications as cfg
            if not self.telegram_token:
                self.telegram_token = getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or None
            if not self.telegram_chat_id:
                cid = getattr(cfg, "TELEGRAM_CHAT_ID", 0)
                if cid: self.telegram_chat_id = int(cid)
            if not self.discord_webhook:
                self.discord_webhook = getattr(cfg, "DISCORD_WEBHOOK_URL", "") or None
        except ImportError:
            pass  # No local config file, env-only or kwargs-only
        except Exception as e:
            log.warning(f"config_notifications.py failed to load ({e}); "
                        f"falling back to env/kwargs only")

    def _async(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    #--- Public alert methods ---

    def scan_started(self, scan_num):
        """Called when a new scan cycle begins."""
        if self.audio:
            self._async(_play_beep, "scan_start")

    def new_signals(self, signals, new_count):
        """Called when new signals are found."""
        if new_count == 0:
            return

        # Determine priority
        high_value = [s for s in signals if s.get("rr_pct", 0) >= 25]
        is_high = len(high_value) > 0

        # Audio
        if self.audio:
            self._async(_play_beep, "high_value" if is_high else "signal")

        # Toast
        if self.toast:
            if is_high:
                best = max(high_value, key=lambda s: s["rr_pct"])
                title = f"HIGH VALUE: {best['symbol']} {best['type']} R:R {best['rr_pct']:.0f}%"
                body = _format_signal_short(best)
            else:
                title = f"{new_count} new signal{'s' if new_count > 1 else ''}"
                body = _format_signal_short(signals[0]) if signals else ""
            self._async(_send_toast, title, body,
                       "high_value" if is_high else "info")

        # Telegram
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            for s in signals:
                self._async(_send_telegram, self.telegram_token,
                            self.telegram_chat_id, s, "signal")

        # Discord
        if self.discord_enabled and self.discord_webhook:
            for s in signals:
                self._async(_send_discord, self.discord_webhook, s, "signal")

    def trade_executed(self, trade):
        """Called when a paper (or future live) trade is executed."""
        if self.audio:
            self._async(_play_beep, "trade_executed")

        if self.toast:
            title = f"Trade: {trade['symbol']} {trade['strategy']} ({trade['mode']})"
            body = _format_trade_notification(trade)
            self._async(_send_toast, title, body, "trade")

        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            self._async(_send_telegram, self.telegram_token,
                        self.telegram_chat_id, trade, "trade")

        if self.discord_enabled and self.discord_webhook:
            self._async(_send_discord, self.discord_webhook, trade, "trade")

    def trade_closed(self, symbol, reason):
        """Called when a captured signal is auto-closed at target/stop.

        reason is the exit_reason (e.g. 'TARGET_HIT', 'STOPPED').
        """
        if self.audio:
            self._async(_play_beep, "trade_executed")
        if self.toast:
            title = f"Auto-closed: {symbol}"
            body = f"{symbol} closed — {reason}"
            self._async(_send_toast, title, body, "trade")

    def scan_summary(self, scan_num, count_0dte, count_swing, new_total):
        """Called at the end of each scan with summary stats."""
        if self.toast and new_total > 0:
            title = f"Scan #{scan_num} complete"
            body = f"0-DTE: {count_0dte}  |  Swing: {count_swing}  |  {new_total} new"
            self._async(_send_toast, title, body, "info")

    def error(self, message):
        """Called on scanner errors."""
        if self.audio:
            self._async(_play_beep, "error")
        if self.toast:
            self._async(_send_toast, "Scanner error", message, "error")
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            self._async(_send_telegram, self.telegram_token,
                        self.telegram_chat_id, message, "error")
        if self.discord_enabled and self.discord_webhook:
            self._async(_send_discord, self.discord_webhook, message, "error")

    def set_audio(self, enabled):
        self.audio = enabled

    def set_toast(self, enabled):
        self.toast = enabled

    def set_telegram(self, enabled):
        self.telegram_enabled = bool(enabled)

    def set_discord(self, enabled):
        self.discord_enabled = bool(enabled)
