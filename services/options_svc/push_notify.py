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
from datetime import datetime
from zoneinfo import ZoneInfo

# `import requests`/`import smtplib` are retained (unused directly here) so that
# `push_notify.requests`/`push_notify.smtplib` resolve to the same module
# singletons the shared senders use — existing tests monkeypatch those module
# attributes and expect them to affect the delegated senders.
import requests  # noqa: F401 — module handle for test monkeypatching
import smtplib  # noqa: F401 — module handle for test monkeypatching

from repo_paths import NOTIFICATIONS_CONFIG
from shared.notify.channels import (
    load_config as _shared_load_config,
    send_telegram,
    send_discord,
    send_sms,
    _in_market_hours,
    _today_ct,
)

# Kept so the config-path override (which tests monkeypatch on this module) is
# honored — `load_config()` passes it through to the shared resolver.
_CONFIG_PATH = NOTIFICATIONS_CONFIG

_TZ = ZoneInfo("America/Chicago")
_MULT = 100
_D_GREEN, _D_YELLOW, _D_GRAY = 0x2ECC71, 0xF1C40F, 0x95A5A6


def load_config() -> dict:
    """Options-domain config load — delegates to shared, honoring `_CONFIG_PATH`.

    A thin wrapper (not a copy) so `push_notify._CONFIG_PATH` monkeypatches keep
    working while the resolution logic lives in `shared.notify.channels`.
    """
    return _shared_load_config(_CONFIG_PATH)


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
                     f"{_strikes_str(s)} Cr ${(s.get('credit') or 0):.2f} "
                     f"R:R {(s.get('rr_pct') or 0):.0f}%")
    if n > cap:
        lines.append(f"…+{n - cap} more")
    return "\n".join(lines)


# ── Scheduled action digest (10:00 / 13:00 / 15:00 CT "trades needing action") ─
# A thrice-daily push summarizing open positions/signals that need a human
# decision: captured signals recommending CUT/TAKE_PROFIT, anything expiring
# today, at-risk (short strike breached/near), and account positions nearing
# their stop/target. Unlike new-signal alerts there is NO seen-set — each slot
# pushes the CURRENT snapshot (the same trade legitimately re-appears at 10/1/3
# because it still needs action); the scheduler's once-per-slot latch prevents
# re-firing within a slot.
_ACTION_SLOT_LABELS = {
    "morning": "Morning (10:00 AM CT)",
    "midday": "Midday (1:00 PM CT)",
    "close": "Pre-close (3:00 PM CT)",
}
_ACTION_SECTIONS = ("captured_action", "expiring_today", "at_risk", "account_near")


def action_slot_label(slot) -> str:
    return _ACTION_SLOT_LABELS.get(slot, "")


def action_total(items: dict) -> int:
    """Total count of actionable rows across all sections."""
    return sum(len((items or {}).get(k) or []) for k in _ACTION_SECTIONS)


def _row_text(section: str, s: dict) -> str:
    sym, strat = s.get("symbol", ""), s.get("strategy", "")
    if section == "captured_action":
        extra = f" ({s.get('reason')})" if s.get("reason") else ""
        return f"{sym} {strat} — {s.get('recommendation', '')}{extra}"
    if section == "expiring_today":
        return f"{sym} {strat} [{s.get('book', '')}] exp {s.get('expiration', '')}"
    if section == "at_risk":
        heat = s.get("heat")
        h = f" (heat {heat:.0f})" if isinstance(heat, (int, float)) else ""
        return f"{sym} {strat} — {s.get('rescue_state', '')}{h}"
    return f"{sym} {strat} — {s.get('note', '')}"   # account_near


_ACTION_SECTION_HEADS = {
    "captured_action": "⚠️ Captured — act now",
    "expiring_today": "⏰ Expiring today",
    "at_risk": "🔴 At risk",
    "account_near": "🎯 Near stop/target",
}
_ACTION_CAP = 8   # rows per section in the message


def action_digest_text(items: dict, slot_label: str = "") -> str:
    """Telegram HTML digest. Sections with no rows are omitted."""
    e = lambda v: _html.escape(str(v))
    head = "🔔 <b>Trades needing action</b>"
    if slot_label:
        head += f" — {e(slot_label)}"
    lines = [head]
    for sec in _ACTION_SECTIONS:
        rows = (items or {}).get(sec) or []
        if not rows:
            continue
        lines.append(f"\n<b>{_ACTION_SECTION_HEADS[sec]} ({len(rows)})</b>")
        for s in rows[:_ACTION_CAP]:
            lines.append(f"• {e(_row_text(sec, s))}")
        if len(rows) > _ACTION_CAP:
            lines.append(f"…+{len(rows) - _ACTION_CAP} more")
    return "\n".join(lines)


def action_digest_embed(items: dict, slot_label: str = "") -> dict:
    fields = []
    for sec in _ACTION_SECTIONS:
        rows = (items or {}).get(sec) or []
        if not rows:
            continue
        val = "\n".join(_row_text(sec, s) for s in rows[:_ACTION_CAP])
        if len(rows) > _ACTION_CAP:
            val += f"\n…+{len(rows) - _ACTION_CAP} more"
        fields.append({"name": f"{_ACTION_SECTION_HEADS[sec]} ({len(rows)})",
                       "value": val[:1000], "inline": False})
    return {
        "title": "🔔 Trades needing action" + (f" — {slot_label}" if slot_label else ""),
        "color": _D_YELLOW,
        "fields": fields,
        "timestamp": datetime.now(_TZ).isoformat(),
    }


def action_sms_text(items: dict, slot_label: str = "") -> str:
    counts = [(_ACTION_SECTION_HEADS[sec].split(" ", 1)[-1], len((items or {}).get(sec) or []))
              for sec in _ACTION_SECTIONS]
    parts = [f"{n} {name.lower()}" for name, n in counts if n]
    head = "Trades need action"
    if slot_label:
        head += f" ({slot_label.split(' (')[0]})"
    return head + ": " + (", ".join(parts) if parts else "none")


def send_action_digest(items: dict, *, slot_label: str = "", config: dict | None = None) -> bool:
    """Push the action digest to Telegram + Discord (+ SMS if configured).

    Returns True if a send was attempted. Skips entirely when notifications are
    disabled OR nothing needs action (no empty "all clear" spam). Best-effort per
    channel (never raises)."""
    cfg = config or load_config()
    if not cfg.get("enabled", True) or action_total(items) == 0:
        return False
    tg = cfg.get("telegram", {})
    dc = cfg.get("discord", {})
    sms = cfg.get("sms", {})
    send_telegram(tg.get("bot_token"), tg.get("chat_id"),
                  action_digest_text(items, slot_label))
    send_discord(dc.get("webhook_url"), action_digest_embed(items, slot_label))
    send_sms(sms.get("fi_number"), sms.get("smtp_user"), sms.get("smtp_app_password"),
             action_sms_text(items, slot_label), subject="Trades need action")
    return True


def new_keys(current: list, prev: dict | None, today: str):
    """Return (new_keys_in_order, next_state).

    `prev` is {"date", "keys"} or None. On a date change the seen-set resets
    (a persisting signal doesn't re-spam, but each new day's signals fire once).
    Order-preserving + deduped.
    """
    # Read the prior keys defensively: a malformed `prev` (missing/non-list
    # "keys") must never raise — that would break the module's never-raises
    # contract (notify_signals → the caller).
    same_day = bool(prev) and prev.get("date") == today
    raw = prev.get("keys") if same_day else []
    raw = raw if isinstance(raw, list) else []
    seen = set(raw)
    out, ordered_seen = [], list(raw)
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


def notify_signals(bus, signals: list, *, kind: str, seen_key: str,
                   today: str | None = None, seed: bool = False,
                   config: dict | None = None) -> list:
    """Diff `signals` against the date-scoped seen-set and notify the new ones.

    kind: "scanner" | "captured". Returns the list of newly-notified keys.
    seed=True updates the seen-set WITHOUT sending (first run after restart).
    Never raises — a send failure is swallowed per-channel.
    """
    cfg = config or load_config()
    today = today or _today_ct()
    key_fn = captured_key if kind == "captured" else signal_key

    prev = load_seen(bus, seen_key)
    keys = [key_fn(s) for s in signals]
    new, nxt = new_keys(keys, prev, today)
    # Mark keys seen BEFORE the enable/market-hours/min-score gates — intentional,
    # mirroring webgui/main.py's unconditional `_ALERT_STATE["alerted"] |= keys`
    # after gating the chime. Each signal is considered exactly once; a signal
    # first seen off-hours or while disabled is absorbed here and is NOT deferred
    # to re-notify later.
    save_seen(bus, seen_key, nxt)

    if seed or not cfg.get("enabled", True) or not new:
        return new if seed else []

    if cfg.get("market_hours_only") and not _in_market_hours():
        return []

    min_score = cfg.get("min_score", 0) or 0
    new_set = set(new)
    # dedup by key so two signals sharing a key notify only once (keep the first)
    fresh, seen_fresh = [], set()
    for s, k in zip(signals, keys):
        if k in new_set and k not in seen_fresh:
            seen_fresh.add(k)
            fresh.append(s)
    # min_score is scanner-only: captured signals carry no composite_score.
    if kind == "scanner" and min_score:
        fresh = [s for s in fresh if (s.get("composite_score") or 0) >= min_score]
    if not fresh:
        return []

    tg = cfg.get("telegram", {})
    dc = cfg.get("discord", {})
    sms = cfg.get("sms", {})
    for s in fresh:
        send_telegram(tg.get("bot_token"), tg.get("chat_id"), telegram_signal_text(s))
        send_discord(dc.get("webhook_url"), discord_signal_embed(s))
    send_sms(sms.get("fi_number"), sms.get("smtp_user"), sms.get("smtp_app_password"),
             sms_summary_text(fresh, kind), subject=f"{len(fresh)} new {kind} signal(s)")
    return [key_fn(s) for s in fresh]
