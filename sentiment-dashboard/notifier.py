"""notifier.py - Discord + Telegram notifier for SentimentDashboard

Self-contained module modeled after
``options-scanner/notifier.py``. Sends sentiment
readings (composite score + per-component scores) to Discord and/or
Telegram. Only ``requests`` is required at runtime; if missing, the
module attempts a quiet pip install (matches OptionsScanner behavior).

Config sources (constructor kwargs win, then env, then file):
  - env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL
  - file: ``config_notifications.py`` anywhere on sys.path
          (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL)

Throttling: see :class:`SentimentNotifier.post_sentiment`. The intent is
to avoid one post per 15-min autofetch (~30/day) while still surfacing
material moves quickly.
"""

import html
import json
import logging
import sys
import pathlib
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import BRIDGE_PATH, OPTIONS_SCANNER  # noqa: E402

log = logging.getLogger("sentiment.notifier")
TZ = ZoneInfo("America/Chicago")

# v4.2 — canonical bridge file the notifier re-reads as source-of-truth
# before sending. Mirrors bridge.CANONICAL_PATH; duplicated as a literal
# so the notifier module has no scoring/bridge import cycle.
CANONICAL_BRIDGE_PATH = BRIDGE_PATH

# Skip the send if the bridge file on disk is older than this. Keeps us
# from broadcasting stale data if the dashboard crashed between writes.
BRIDGE_MAX_AGE_SEC = 600

# ── requests w/ auto-install fallback (same pattern as OptionsScanner) ──
_requests_available = False
try:
    import requests
    _requests_available = True
except ImportError:
    try:
        import subprocess, sys
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "requests", "-q"])
        import requests
        _requests_available = True
    except Exception:
        log.warning("requests not available — notifications disabled")


# ── Discord embed colors keyed by bias ──
_DISCORD_COLOR_GREEN  = 0x2ECC71
_DISCORD_COLOR_BLUE   = 0x3498DB
_DISCORD_COLOR_YELLOW = 0xF1C40F
_DISCORD_COLOR_RED    = 0xE74C3C
_DISCORD_COLOR_GRAY   = 0x95A5A6


def _bias_color(bias: str) -> int:
    """Map a bias label to a Discord embed color."""
    b = (bias or "").strip().upper()
    if "STRONG" in b and "BULL" in b:    return _DISCORD_COLOR_GREEN
    if "BULL" in b:                       return _DISCORD_COLOR_GREEN
    if "STRONG" in b and "BEAR" in b:    return _DISCORD_COLOR_RED
    if "BEAR" in b:                       return _DISCORD_COLOR_YELLOW
    if "NEUTRAL" in b:                    return _DISCORD_COLOR_BLUE
    return _DISCORD_COLOR_GRAY


_COMPONENT_LABELS = [
    ("vix_complex",  "VIX Complex"),
    ("put_call",     "Put/Call"),
    ("breadth",      "Breadth"),
    ("rotation",     "Rotation"),
    ("sector_perf",  "Sector Perf"),
    ("credit_pulse", "Credit Pulse"),
]


def _today_str() -> str:
    """Wall-clock date in the dashboard's TZ. Used in messages so the
    user sees when the notification was sent, not whatever stale value
    sat in the loaded session JSON."""
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _top_bot_sectors(payload: dict, n: int = 2):
    """Pick the top-N and bottom-N sectors from sector_breakdown by
    day_pct. Returns (top, bot) where each is a list of
    ``{sector, etf, day_pct}`` dicts. Empty lists when no breakdown."""
    rows = payload.get("sector_breakdown") or []
    if not rows:
        return [], []
    ranked = sorted(
        (r for r in rows if r.get("day_pct") is not None),
        key=lambda r: float(r["day_pct"]),
        reverse=True,
    )
    return ranked[:n], list(reversed(ranked[-n:]))


def _fmt_sector_line(rows) -> str:
    """Render a sector list as 'Name (ETF) +1.23% · Name (ETF) -0.45%'."""
    return " · ".join(
        f"{r.get('sector') or '?'} ({r.get('etf') or '?'}) "
        f"{float(r['day_pct']):+.2f}%"
        for r in rows
    )


def _trend_line(payload: dict) -> str:
    """Format the trend_regime block as 'Bull Trend · day 12'. Empty
    string when classifier hasn't produced a result yet."""
    tr = payload.get("trend_regime") or {}
    label = tr.get("label")
    if not label:
        return ""
    days = tr.get("days_in_state")
    if days:
        return f"{label} · day {int(days)}"
    return str(label)


def _rotation_line(payload: dict) -> str:
    """Format rotation_detail as 'Cyc vs Def +0.40% (day)'. Prefers
    day_spread, falls back to 3d then week. Empty string when missing."""
    r = payload.get("rotation_detail") or {}
    for key, label in (("day_spread_pct", "day"),
                       ("3d_spread_pct", "3d"),
                       ("week_spread_pct", "wk")):
        sp = r.get(key)
        if sp is None:
            continue
        direction = "Cyc>Def" if sp >= 0 else "Def>Cyc"
        return f"{direction} {float(sp):+.2f}% ({label})"
    return ""


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _conf_pct(payload: dict, key: str) -> int:
    """Return component confidence as 0–100 int. Missing -> 0."""
    conf = (payload.get("component_confidence") or {}).get(key)
    return int(round(_f(conf, 0.0) * 100))


def _score_str(payload: dict, key: str) -> str:
    score = (payload.get("component_scores") or {}).get(key, 0)
    return f"{_f(score):.1f}"


# ── Formatters ──

def _discord_sentiment_embed(payload: dict) -> dict:
    """Build a Discord embed dict from a bridge-shaped payload."""
    score = _f(payload.get("composite_score"))
    bias = str(payload.get("bias", ""))
    bias_disp = bias.upper() if bias else "—"
    color = _bias_color(bias)
    modifier = str(payload.get("position_size_modifier", "1.00x"))
    date = _today_str()

    # change line
    change = payload.get("change") or payload.get("score_change")
    if change in (None, "", "—"):
        change_str = "—"
    else:
        try:
            change_str = f"{float(change):+.2f}"
        except (TypeError, ValueError):
            change_str = str(change)

    agg_conf = int(round(_f(payload.get("aggregate_confidence")) * 100))

    raw = payload.get("raw_data") or {}
    # Sub-scores compact display for VIX Complex field
    cs = payload.get("component_scores") or {}
    vix_term  = _f(cs.get("vix_term") or cs.get("vix"))
    vix1d     = _f(cs.get("vix1d"))
    term_slope = _f(cs.get("term_slope"))

    fields = []
    for key, label in _COMPONENT_LABELS:
        conf = _conf_pct(payload, key)
        score_s = _score_str(payload, key)
        if key == "vix_complex":
            value = (f"{score_s}/10 ({conf}%)\n"
                     f"Term {vix_term:.0f} · 1D {vix1d:.0f} · "
                     f"Slope {term_slope:.0f}")
        else:
            value = f"{score_s}/10 ({conf}%)"
        fields.append({"name": label, "value": value, "inline": True})

    description = (f"Change {change_str} · @{agg_conf}% conf · "
                   f"Modifier {modifier} · {date}")

    # v4.2 — extra context fields: trend regime, top/bottom sectors, rotation
    trend = _trend_line(payload)
    if trend:
        fields.append({"name": "Trend", "value": trend, "inline": False})
    top, bot = _top_bot_sectors(payload, n=2)
    if top:
        fields.append({
            "name": "Top Sectors",
            "value": _fmt_sector_line(top),
            "inline": False})
    if bot:
        fields.append({
            "name": "Bottom Sectors",
            "value": _fmt_sector_line(bot),
            "inline": False})
    rot = _rotation_line(payload)
    if rot:
        fields.append({
            "name": "Cyc/Def Rotation", "value": rot, "inline": False})

    velocity = payload.get("velocity") or {}
    regime_break = bool(velocity.get("regime_break"))
    divergence = payload.get("divergence_flag")
    footer_bits = []
    if regime_break:
        footer_bits.append("⚠ regime break")
    if divergence:
        footer_bits.append(f"⚠ {divergence}")
    embed = {
        "title": f"📊 Sentiment {score:.1f}/10 · {bias_disp}",
        "description": description,
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(TZ).isoformat(),
    }
    if footer_bits:
        embed["footer"] = {"text": " · ".join(footer_bits)}
    return embed


def _telegram_sentiment_text(payload: dict) -> str:
    """Build a compact HTML-flavored Telegram message body."""
    e = html.escape
    score = _f(payload.get("composite_score"))
    bias = str(payload.get("bias", ""))
    bias_disp = bias.upper() if bias else "—"
    change = payload.get("change") or payload.get("score_change")
    if change in (None, "", "—"):
        change_str = "—"
    else:
        try:
            change_str = f"{float(change):+.2f}"
        except (TypeError, ValueError):
            change_str = str(change)
    modifier = str(payload.get("position_size_modifier", "1.00x"))
    agg_conf = int(round(_f(payload.get("aggregate_confidence")) * 100))

    lines = [
        f"<b>📊 Sentiment {score:.1f}/10 · {e(bias_disp)} "
        f"({e(change_str)})</b>",
        f"<i>{e(modifier)} · @{agg_conf}% conf · "
        f"{e(_today_str())}</i>",
    ]
    for key, label in _COMPONENT_LABELS:
        conf = _conf_pct(payload, key)
        score_s = _score_str(payload, key)
        lines.append(f"• {e(label)}: <b>{score_s}/10</b> ({conf}%)")

    # v4.2 — trend / sectors / rotation context
    trend = _trend_line(payload)
    if trend:
        lines.append(f"📈 Trend: <b>{e(trend)}</b>")
    top, bot = _top_bot_sectors(payload, n=2)
    if top:
        lines.append(f"🏆 Top: {e(_fmt_sector_line(top))}")
    if bot:
        lines.append(f"📉 Bot: {e(_fmt_sector_line(bot))}")
    rot = _rotation_line(payload)
    if rot:
        lines.append(f"🔄 Rotation: {e(rot)}")

    velocity = payload.get("velocity") or {}
    if velocity.get("regime_break"):
        lines.append("⚠ Regime break (|z| &gt; 1.5)")
    div = payload.get("divergence_flag")
    if div:
        lines.append(f"⚠ {e(str(div))}")

    text = "\n".join(lines)
    # Target < 1500 chars (Telegram caption-ish budget); hard-truncate
    if len(text) > 1500:
        text = text[:1497] + "..."
    return text


# ── Channel send primitives ──

def _send_telegram(token, chat_id, payload: dict) -> None:
    """POST a sentiment payload to Telegram. Silent on misconfig."""
    if not _requests_available or not token or not chat_id:
        return
    try:
        text = _telegram_sentiment_text(payload)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=8)
    except Exception as e:
        log.warning(f"Telegram sentiment send failed: {e}")


def _send_discord(webhook_url, payload: dict) -> None:
    """POST a sentiment payload to a Discord webhook. Silent on misconfig."""
    if not _requests_available or not webhook_url:
        return
    try:
        embed = _discord_sentiment_embed(payload)
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=8)
    except Exception as e:
        log.warning(f"Discord sentiment send failed: {e}")


# ── Public class ──

# Throttling thresholds (module-level so tests can monkeypatch)
THROTTLE_SCORE_DELTA = 0.3
THROTTLE_MIN_INTERVAL_SEC = 60 * 60  # 60 minutes


class SentimentNotifier:
    """Async dispatcher that posts the latest sentiment reading to
    configured channels with simple throttling.
    """

    def __init__(self, *, telegram_token=None, telegram_chat_id=None,
                 discord_webhook=None, load_config=True, armed=False):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook

        if load_config:
            self._load_notification_config()

        # v4.2 — gate startup posts. The dashboard recomputes scores at
        # launch (so the bridge file timestamp refreshes) and the first
        # Schwab fetch refreshes scores again as each leg returns. Both
        # are no-ops as far as users care. The dashboard calls .arm()
        # once the initial fetch has finished applying so the *next*
        # recalc is the first real notification.
        self._armed = bool(armed)

        # Throttle state
        self._last_posted_score: float | None = None
        self._last_posted_bias: str | None = None
        self._last_posted_at: datetime | None = None
        self._last_posted_regime_break: bool | None = None
        self._last_posted_divergence: str | None = None

        active = []
        if self.discord_webhook: active.append("discord")
        if self.telegram_token and self.telegram_chat_id:
            active.append("telegram")
        if active:
            log.info(
                f"SentimentNotifier channels active: {', '.join(active)}")
        else:
            log.info(
                "SentimentNotifier: no channels configured — post_sentiment "
                "calls will be no-ops")

    # ── Config loading ──

    # Shared config file used by OptionsScanner — looked up as a fallback
    # so both apps draw from the same place. Override here if the
    # OptionsScanner repo lives elsewhere on this machine.
    SHARED_CONFIG_PATH = str(OPTIONS_SCANNER / "config_notifications.py")

    def _load_notification_config(self):
        """Populate missing creds from env, then ``config_notifications.py``.

        Lookup order (constructor kwargs always win):
          1. Env vars (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``,
             ``DISCORD_WEBHOOK_URL``).
          2. ``config_notifications`` importable on ``sys.path``
             (e.g. one placed next to ``sentiment_dashboard.py``).
          3. The shared OptionsScanner config at
             :attr:`SHARED_CONFIG_PATH`, loaded by absolute file path
             so we don't have to pollute ``sys.path``.

        Same precedence model as ``OptionsScanner/notifier.py``.
        """
        import os
        if not self.telegram_token:
            self.telegram_token = (
                os.environ.get("TELEGRAM_BOT_TOKEN") or None)
        if not self.telegram_chat_id:
            cid = os.environ.get("TELEGRAM_CHAT_ID")
            if cid:
                try:
                    self.telegram_chat_id = int(cid)
                except ValueError:
                    self.telegram_chat_id = cid  # keep as string fallback
        if not self.discord_webhook:
            self.discord_webhook = (
                os.environ.get("DISCORD_WEBHOOK_URL") or None)

        cfg = self._load_local_config()
        if cfg is None:
            cfg = self._load_shared_config()
        if cfg is not None:
            self._apply_config_module(cfg)

    def _load_local_config(self):
        """Try importing ``config_notifications`` from ``sys.path``."""
        try:
            import config_notifications as cfg
            return cfg
        except ImportError:
            return None
        except Exception as e:
            log.warning(
                f"local config_notifications failed to load ({e})")
            return None

    def _load_shared_config(self):
        """Fallback: load the OptionsScanner config_notifications.py by path."""
        from pathlib import Path
        path = Path(self.SHARED_CONFIG_PATH)
        if not path.exists():
            return None
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "sentiment_shared_notif_cfg", str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            log.info("Notifier: loaded shared config from %s", path)
            return mod
        except Exception as e:
            log.warning(
                f"shared config_notifications failed to load ({e})")
            return None

    def _apply_config_module(self, cfg):
        """Pull credentials from a loaded config module, skipping any
        slot already set by kwargs or env."""
        if not self.telegram_token:
            self.telegram_token = (
                getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or None)
        if not self.telegram_chat_id:
            cid = getattr(cfg, "TELEGRAM_CHAT_ID", 0)
            if cid:
                try:
                    self.telegram_chat_id = int(cid)
                except (TypeError, ValueError):
                    self.telegram_chat_id = cid
        if not self.discord_webhook:
            self.discord_webhook = (
                getattr(cfg, "DISCORD_WEBHOOK_URL", "") or None)

    # ── Internals ──

    def _async(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _any_channel(self) -> bool:
        return bool(
            self.discord_webhook
            or (self.telegram_token and self.telegram_chat_id))

    def _should_post(self, payload: dict) -> bool:
        """Throttle gate. Returns True iff payload should be posted now."""
        # First post per session — always go.
        if self._last_posted_at is None:
            return True

        score = _f(payload.get("composite_score"))
        bias = str(payload.get("bias", ""))
        velocity = payload.get("velocity") or {}
        regime_break = bool(velocity.get("regime_break"))
        divergence = payload.get("divergence_flag") or None

        # Material moves: score delta, bias change, new regime break, new divergence
        if (self._last_posted_score is not None
                and abs(score - self._last_posted_score)
                    >= THROTTLE_SCORE_DELTA):
            return True
        if bias != (self._last_posted_bias or ""):
            return True
        if regime_break and not self._last_posted_regime_break:
            return True
        if divergence and not self._last_posted_divergence:
            return True

        # Heartbeat: at least one post per hour
        elapsed = (datetime.now(TZ) - self._last_posted_at).total_seconds()
        if elapsed >= THROTTLE_MIN_INTERVAL_SEC:
            return True

        log.debug("Notifier: throttled (no material change)")
        return False

    def _mark_posted(self, payload: dict):
        self._last_posted_score = _f(payload.get("composite_score"))
        self._last_posted_bias = str(payload.get("bias", ""))
        self._last_posted_at = datetime.now(TZ)
        velocity = payload.get("velocity") or {}
        self._last_posted_regime_break = bool(velocity.get("regime_break"))
        self._last_posted_divergence = payload.get("divergence_flag") or None

    # ── Public API ──

    def arm(self) -> None:
        """Enable real sending. Called by the dashboard once startup is
        complete (after the initial Schwab fetch). Until armed,
        ``post_sentiment`` is a no-op so launch-time recomputes never
        broadcast."""
        if not self._armed:
            log.info("SentimentNotifier armed — future posts will send")
        self._armed = True

    def _load_bridge_for_validation(self) -> dict | None:
        """Read the canonical bridge JSON. Returns the dict on success,
        ``None`` if missing, stale (> BRIDGE_MAX_AGE_SEC old), or
        unreadable. Used to confirm what we're about to send matches
        what was actually persisted for downstream consumers."""
        try:
            p = CANONICAL_BRIDGE_PATH
            if not p.exists():
                log.warning(
                    "post_sentiment: bridge file missing (%s) — "
                    "skipping send", p)
                return None
            age = (datetime.now().timestamp() - p.stat().st_mtime)
            if age > BRIDGE_MAX_AGE_SEC:
                log.warning(
                    "post_sentiment: bridge file is %.0fs old "
                    "(> %ds) — skipping send",
                    age, BRIDGE_MAX_AGE_SEC)
                return None
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(
                "post_sentiment: bridge read failed (%s) — skipping send",
                e)
            return None

    def post_sentiment(self, payload: dict) -> None:
        """Async-post the sentiment payload to all configured channels.

        No-op if no channels are configured, the notifier hasn't been
        armed yet, the bridge file on disk doesn't validate, or the
        throttle gate blocks the send. Sends happen on daemon background
        threads — this call returns immediately.
        """
        if not self._any_channel():
            return
        if not self._armed:
            log.debug(
                "post_sentiment: notifier disarmed (startup) — skipping")
            return
        if not isinstance(payload, dict):
            log.warning("post_sentiment: payload is not a dict — skipping")
            return

        # v4.2 — re-read the bridge file we just wrote and broadcast
        # *that* as source of truth. Guarantees Discord/Telegram never
        # show a value the downstream consumers don't also see.
        validated = self._load_bridge_for_validation()
        if validated is None:
            return
        payload = validated

        if not self._should_post(payload):
            return

        if self.discord_webhook:
            self._async(_send_discord, self.discord_webhook, payload)
        if self.telegram_token and self.telegram_chat_id:
            self._async(
                _send_telegram, self.telegram_token,
                self.telegram_chat_id, payload)

        self._mark_posted(payload)
