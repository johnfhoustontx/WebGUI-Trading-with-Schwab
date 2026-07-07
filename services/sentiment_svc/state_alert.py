"""Market-state transition push alert (sentiment domain).

When the five-state market classifier's COMMITTED state flips (e.g.
Bullish → Lack of Bullishness), push a phone notification — the user-facing
payoff of the market-state feature. Reuses the shared, proven push channels
(``shared.notify.channels``) rather than reimplementing any I/O; only the
domain-specific formatters + the transition gate live here.

Every send is best-effort per channel (never raises into the caller). A
transition only fires between two VALID new-vocab states (skips the cold-start
old-vocab→new-vocab first cycle) and honors the same enabled / market-hours
gates the options-domain push helper uses.
"""
import html as _html
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from repo_paths import SENTIMENT

# ``market_state`` (STATE_LABELS) is imported standalone — the sentiment dir on
# ``sys.path`` — the same way ``compute`` does. Safe because the sentiment
# service runs in its own process (no options ``scoring.py`` collision).
if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

from scoring import market_state  # noqa: E402

from shared.notify.channels import (  # noqa: E402
    load_config,
    send_telegram,
    send_discord,
    send_sms,
    _in_market_hours,
)

log = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Chicago")
_D_GREEN, _D_AMBER, _D_RED = 0x2ECC71, 0xF1C40F, 0xE74C3C


def _state_color(state: str) -> int:
    if state == "bullish":
        return _D_GREEN
    if state == "bearish":
        return _D_RED
    return _D_AMBER


def transition_telegram(old_label, new_label, description, evidence) -> str:
    """Telegram HTML: a headline flip + description + evidence bullets."""
    e = lambda v: _html.escape(str(v))
    lines = [f"🔀 <b>Market state: {e(old_label)} → {e(new_label)}</b>"]
    if description:
        lines.append(e(description))
    for ev in (evidence or []):
        lines.append(f"• {e(ev)}")
    return "\n".join(lines) + "\n"


def transition_discord(old_label, new_label, new_state, description,
                       evidence) -> dict:
    """Discord embed — state-colored, evidence in a field."""
    fields = []
    if evidence:
        val = "\n".join(str(x) for x in evidence)[:1000]
        fields.append({"name": "Evidence", "value": val, "inline": False})
    return {
        "title": f"Market state: {old_label} → {new_label}",
        "description": str(description or ""),
        "color": _state_color(new_state),
        "fields": fields,
        "timestamp": datetime.now(_TZ).isoformat(),
    }


def transition_sms(old_label, new_label) -> str:
    return f"Market state: {old_label} → {new_label}"


def _safe(fn, *args, **kwargs) -> None:
    """Call a channel sender, swallowing any error so one channel can't abort
    the others (the shared senders already self-gate/swallow — this is belt +
    suspenders so ``send_state_transition`` truly never raises)."""
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — best-effort per channel.
        log.warning("state transition channel send failed", exc_info=True)


def send_state_transition(old_state, new_state, trend, *, config=None) -> bool:
    """Push a phone alert on a real committed market-state flip.

    Gate order: (1) notifications enabled, (2) BOTH states are valid new-vocab
    states AND differ (skips the cold-start old-vocab→new-vocab first cycle),
    (3) market-hours (when ``market_hours_only``). Returns True if a send was
    attempted. Best-effort per channel; never raises.
    """
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    labels = market_state.STATE_LABELS
    if old_state not in labels or new_state not in labels or old_state == new_state:
        return False
    if cfg.get("market_hours_only") and not _in_market_hours():
        return False

    old_label, new_label = labels[old_state], labels[new_state]
    t = trend or {}
    description = t.get("description", "")
    evidence = t.get("evidence") or []

    tg = cfg.get("telegram", {})
    dc = cfg.get("discord", {})
    sms = cfg.get("sms", {})
    _safe(send_telegram, tg.get("bot_token"), tg.get("chat_id"),
          transition_telegram(old_label, new_label, description, evidence))
    _safe(send_discord, dc.get("webhook_url"),
          transition_discord(old_label, new_label, new_state, description, evidence))
    _safe(send_sms, sms.get("fi_number"), sms.get("smtp_user"),
          sms.get("smtp_app_password"),
          transition_sms(old_label, new_label), subject="Market state change")
    return True
