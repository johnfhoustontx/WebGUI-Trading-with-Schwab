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
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# `import requests`/`import smtplib` are retained (unused directly here) so that
# `push_notify.requests`/`push_notify.smtplib` resolve to the same module
# singletons the shared senders use — existing tests monkeypatch those module
# attributes and expect them to affect the delegated senders.
import requests  # noqa: F401 — module handle for test monkeypatching
import smtplib  # noqa: F401 — module handle for test monkeypatching

from repo_paths import NOTIFICATIONS_CONFIG
from services.options_svc import briefing_image
from services.options_svc import market_snapshot
from shared.notify.channels import (
    discord_target,
    load_config as _shared_load_config,
    telegram_target,
    send_telegram,
    send_telegram_photo,
    send_discord,
    send_discord_file,
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
    grade = s.get("grade")
    grade_part = f" • Grade <b>{e(grade)}</b>" if grade else ""
    return (
        f"{emoji} <b>{e(s.get('symbol'))} {e(s.get('type'))}</b> ({e(s.get('trade_type', ''))})\n"
        f"Exp <code>{e(s.get('expiration'))}</code> • {e(_strikes_str(s))}\n"
        f"Credit <b>${(s.get('credit') or 0):.2f}</b> "
        f"(${(s.get('credit') or 0) * _MULT:,.0f}/ct) • Max loss ${(s.get('max_loss') or 0):.2f}\n"
        f"R:R <b>{rr:.1f}%</b> • PoP {s.get('pop_pct', 0):.0f}% • Δ {s.get('short_delta', 0):.3f}{grade_part}"
    )


# ── Twitter / X public post ──────────────────────────────────────────────────
# A tweet is capped at 280 chars, so this is a compact single-line variant of the
# multi-line Telegram/Discord formatters, with a mandatory not-advice disclaimer
# (a public post of a trade signal reads as advice without one). Deliberately
# terse — see twitter_signal_text.
_TWEET_MAX = 280
_TWEET_DISCLAIMER = "Not advice. Paper/educational."


def _tweet_footer(hashtags, discord_url, extra_text, disclaimer) -> str:
    """Assemble the configurable static footer (all parts optional).

    Order: extra text · Discord link · disclaimer · hashtags. Each on its own
    block so the tweet stays readable. Empty pieces are dropped.
    """
    lines = []
    promo = " ".join(p for p in (extra_text or "", discord_url or "") if p).strip()
    if promo:
        lines.append(promo)
    if disclaimer:
        lines.append(disclaimer)
    tags = " ".join(h for h in (hashtags or []) if h).strip()
    if tags:
        lines.append(tags)
    return ("\n\n" + "\n".join(lines)) if lines else ""


def twitter_signal_text(s: dict, *, hashtags=None, discord_url=None,
                        extra_text=None, disclaimer: str = _TWEET_DISCLAIMER) -> str:
    """A ≤280-char public tweet for a scanner signal.

    Compact single-line signal body (symbol/type/exp/strikes/credit/R:R/PoP)
    followed by a configurable static footer — optional promo text + Discord
    link, the not-advice disclaimer, and hashtags — all supplied from the
    ``twitter`` config block so they change without a code edit.

    Budget defense: the footer is what the user added for reach/compliance, so it
    is preserved verbatim and the BODY is truncated (with an ellipsis) to fit
    ``_TWEET_MAX``. A footer that alone exceeds the budget is still hard-capped to
    280 (the API would otherwise reject the post) — keep the configured footer
    comfortably short.

    NOTE: X counts every URL as 23 chars via t.co wrapping; this measures the
    URL's real length, so the budget check is conservative (safe), not exact.
    """
    rr = s.get("rr_pct", 0) or 0
    emoji = "🟢" if rr >= 25 else ("🟡" if rr >= 15 else "⚪")
    grade = s.get("grade")
    grade_part = f" · {grade}" if grade else ""
    body = (
        f"{emoji} {s.get('symbol')} {s.get('type')} ({s.get('trade_type', '')}) "
        f"exp {s.get('expiration')} {_strikes_str(s)}{grade_part} · "
        f"Cr ${(s.get('credit') or 0):.2f} · R:R {rr:.0f}% · PoP {s.get('pop_pct', 0):.0f}%"
    )
    footer = _tweet_footer(hashtags, discord_url, extra_text, disclaimer)
    # Truncate the BODY (never the footer) to fit the budget.
    room = _TWEET_MAX - len(footer)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + "…"
    return (body + footer)[:_TWEET_MAX]


_TWITTER_CRED_KEYS = ("api_key", "api_secret", "access_token", "access_secret")


def _has_twitter_creds(creds: dict) -> bool:
    return bool(creds) and all(creds.get(k) for k in _TWITTER_CRED_KEYS)


def _twitter_client(creds: dict):
    """Build a tweepy v2 Client with OAuth 1.0a user context (lazy import).

    OAuth 1.0a (not app-only bearer) is required — posting a tweet is a
    user-context write. tweepy is imported here, not at module load, so the
    dependency is only needed when Twitter is actually enabled.
    """
    import tweepy  # lazy — only needed when the channel is live
    return tweepy.Client(
        consumer_key=creds["api_key"], consumer_secret=creds["api_secret"],
        access_token=creds["access_token"], access_token_secret=creds["access_secret"],
    )


def send_twitter(creds: dict, text: str, *, dry_run: bool = False) -> bool:
    """Post one tweet. Best-effort — never raises into the caller.

    Returns True if a post was attempted (or dry-run logged), False if skipped
    (missing creds) or the API call failed. A duplicate-content (187) or
    rate-limit (429) error is caught here and reported False, so a rejected post
    never breaks the scan/publish path that calls it.
    """
    if dry_run:
        log.info("Twitter DRY-RUN (not posted): %s", text)
        return True
    if not _has_twitter_creds(creds):
        return False
    try:
        _twitter_client(creds).create_tweet(text=text)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort (187/429/network/etc.)
        log.warning("Twitter send failed: %s", exc)
        return False


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
            {"name": "Grade", "value": f"{s.get('grade', '—')}", "inline": True},
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
    sms = cfg.get("sms", {})
    tok, chat = telegram_target(cfg, "action_alert")
    send_telegram(tok, chat, action_digest_text(items, slot_label))
    send_discord(discord_target(cfg, "action_alert"),
                 action_digest_embed(items, slot_label))
    send_sms(sms.get("fi_number"), sms.get("smtp_user"), sms.get("smtp_app_password"),
             action_sms_text(items, slot_label), subject="Trades need action")
    return True


# ── Options-flow alerts (call/put premium crossover + contract-level UOA) ──────
# An alert dict: {"type": "crossover"|"uoa", "side": ..., "symbol": ..., "text": ...}
# (built by flow_alerts). Green = bullish (calls overtook / unusual call activity);
# red = bearish (puts overtook / unusual put activity).
_FLOW_GREEN = 0x2ECC71
_FLOW_RED = 0xE74C3C


def _flow_is_bullish(a) -> bool:
    return (a.get("type") == "crossover" and a.get("side") == "calls_over") or \
           (a.get("type") == "uoa" and a.get("side") == "call") or \
           (a.get("type") == "gamma_flip" and a.get("side") == "to_positive")


def flow_alert_telegram_text(a) -> str:
    icon = "🟢" if _flow_is_bullish(a) else "🔴"
    return f"{icon} <b>Flow alert</b> — {a.get('text', '')}"


def flow_alert_discord_embed(a) -> dict:
    return {"title": "Options-flow alert",
            "description": a.get("text", ""),
            "color": _FLOW_GREEN if _flow_is_bullish(a) else _FLOW_RED}


_FLOW_CATEGORIES = {"uoa": "flow_uoa",
                    "crossover": "flow_crossover",
                    "gamma_flip": "flow_gamma_flip"}


def flow_category(a) -> str:
    """Routing category for a flow alert — one per alert TYPE, so UOA / crossover
    / gamma-flip can each land in their own channel.

    An unrecognized type is passed through UNMAPPED on purpose: the resolvers know
    no such category, so it falls all the way through to the GLOBAL webhook/chat
    rather than being silently lumped onto another feed."""
    t = (a or {}).get("type")
    return _FLOW_CATEGORIES.get(t, t if isinstance(t, str) else "")


def send_flow_alert(a, *, config: dict | None = None) -> bool:
    """Push one flow alert to Telegram + Discord. Best-effort, never raises.

    Routing is per alert type (see `flow_category`), resolved route -> legacy
    `discord.flow_*_webhook_url` key -> global. Returns True if a send was
    attempted (config enabled)."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    category = flow_category(a)
    tok, chat = telegram_target(cfg, category)
    send_telegram(tok, chat, flow_alert_telegram_text(a))
    send_discord(discord_target(cfg, category), flow_alert_discord_embed(a))
    return True


# ── Scheduled end-of-day summary (post-close push, ~15:10 CT) ────────────────
# A once-daily push summarizing the day's result per paper book (manual + driver):
# day P&L, today's closed W-L + realized, open count, and halt flag. Unlike the action
# digest there is NO "skip if empty" — the day's P&L is the point — but it does skip
# when no paper book is seeded at all (no "no books" spam). Book state is read as-is at
# call time (see compute.collect_eod_summary).
_EOD_BOOK_ORDER = ("manual", "driver")
_D_RED = 0xE74C3C


def _money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def _signed_money(v) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}" if isinstance(v, (int, float)) else "—"


def eod_book_line(b: dict) -> str:
    """Compact one-book line, e.g.
    ``Manual: +$120 day · 2-1 closed (+$180) · 3 open [HALTED]``. Tolerant of None."""
    b = b or {}
    parts = [f"{b.get('label', '?')}: {_signed_money(b.get('day_pnl'))} day"]
    if b.get("closed_today"):
        parts.append(f"{b.get('wins', 0)}-{b.get('losses', 0)} closed "
                     f"({_signed_money(b.get('realized_today'))})")
    if isinstance(b.get("open_count"), int):
        parts.append(f"{b['open_count']} open")
    line = " · ".join(parts)
    if b.get("halted"):
        line += " [HALTED]"
    return line


def eod_book_count(summary: dict) -> int:
    """How many books in the summary are actually seeded (drives the send gate)."""
    s = summary or {}
    return sum(1 for k in _EOD_BOOK_ORDER
               if ((s.get("books") or {}).get(k) or {}).get("has_account"))


def _eod_active_books(summary: dict) -> list:
    s = summary or {}
    return [(s.get("books") or {}).get(k) or {} for k in _EOD_BOOK_ORDER
            if ((s.get("books") or {}).get(k) or {}).get("has_account")]


def _eod_total_day_pnl(summary: dict) -> float:
    return sum((b.get("day_pnl") or 0) for b in _eod_active_books(summary))


def eod_summary_text(summary: dict) -> str:
    """Telegram HTML EOD summary — one line per seeded book."""
    e = lambda v: _html.escape(str(v))
    s = summary or {}
    lines = [f"📊 <b>End-of-day summary</b> — {e(s.get('date', ''))}"]
    for b in _eod_active_books(s):
        lines.append("• " + e(eod_book_line(b)))
    if len(lines) == 1:
        lines.append("No paper books active.")
    return "\n".join(lines)


def eod_summary_embed(summary: dict) -> dict:
    s = summary or {}
    fields = [{"name": b.get("label", "?"), "value": eod_book_line(b), "inline": False}
              for b in _eod_active_books(s)]
    total = _eod_total_day_pnl(s)
    color = _D_GREEN if total > 0 else (_D_GRAY if total == 0 else _D_RED)
    return {
        "title": f"📊 End-of-day summary — {s.get('date', '')}",
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(_TZ).isoformat(),
    }


def eod_summary_sms(summary: dict) -> str:
    s = summary or {}
    body = " | ".join(eod_book_line(b) for b in _eod_active_books(s)) or "No paper books active."
    return f"EOD {s.get('date', '')}: {body}"


def send_eod_summary(summary: dict, *, config: dict | None = None) -> bool:
    """Push the EOD summary to Telegram + Discord (+ SMS if configured).

    Returns True if a send was attempted. Skips entirely when notifications are disabled
    OR no paper book is seeded (no "no books" spam). Always sends when a book exists —
    the day's P&L is the whole point, so there is no empty-content skip. Best-effort per
    channel (never raises)."""
    cfg = config or load_config()
    if not cfg.get("enabled", True) or eod_book_count(summary) == 0:
        return False
    sms = cfg.get("sms", {})
    tok, chat = telegram_target(cfg, "eod_summary")
    send_telegram(tok, chat, eod_summary_text(summary))
    send_discord(discord_target(cfg, "eod_summary"), eod_summary_embed(summary))
    send_sms(sms.get("fi_number"), sms.get("smtp_user"), sms.get("smtp_app_password"),
             eod_summary_sms(summary), subject="EOD summary")
    return True


# ---------------------------------------------------------------- gamma briefing
# The 4x/day Gamma Analyze briefing ships as a RENDERED PNG of the self-contained
# HTML doc (briefing_image.render_html_png → shared.notify.send_telegram_photo /
# send_discord_file). It used to ship as the .html file itself, but **Discord
# auto-previews an .html attachment as syntax-highlighted raw source** — the reader
# got a wall of CSS above the file card, and the suppress-embeds flag does not
# suppress attachment previews. An image renders inline in both platforms with no
# tap and no preview problem. The HTML generation is UNCHANGED (it still powers the
# in-app /options/analyze page); it is now the render SOURCE.
# Labels are duplicated from handlers.ANALYZE_SLOT_TITLES deliberately —
# push_notify must not import handlers (handlers imports THIS module; the reverse
# would be circular).
_BRIEFING_SLOT_LABELS = {
    "premarket": "Premarket",
    "open": "After open",
    "midday": "Midday",
    "close": "EOD recap",
}
_CAPTION_MAX = 1024   # Telegram's sendDocument ceiling; Discord's 2000 is looser


def briefing_caption(res: dict, slot: str = "") -> str:
    """One scannable plain-text line to ride along with the attached briefing.

    Budget-defended: the leading identifiers (slot / regime / bias) ALWAYS survive
    and only the headline truncates, so a runaway model headline can never push the
    slot label out of the caption."""
    a = (res or {}).get("analysis") or {}
    bits = [f"Gamma · {_BRIEFING_SLOT_LABELS.get(slot, slot or 'Briefing')}"]
    regime = (a.get("regime") or "").strip()
    if regime:
        bits.append(regime)
    bias = a.get("bias")
    if isinstance(bias, (int, float)) and not isinstance(bias, bool):
        bits.append(f"Bias {bias:+.0f}")
    lead = " · ".join(bits)
    headline = (a.get("headline") or "").strip()
    room = _CAPTION_MAX - len(lead) - 1          # -1 for the newline
    if not headline or room <= 0:
        return lead[:_CAPTION_MAX]
    if len(headline) > room:
        headline = headline[:max(0, room - 1)].rstrip() + "…"
    return f"{lead}\n{headline}"


def briefing_filename(res: dict, slot: str = "", now=None) -> str:
    """Stable, sortable attachment name: gamma-briefing-YYYY-MM-DD-{slot}.png.

    Prefers the run's own ``generated_at`` so the filename matches the briefing's
    session even if the push is retried later. Slot is sanitized because the ad-hoc
    label carries a colon, which is illegal in a Windows filename."""
    ts = (res or {}).get("generated_at")
    day = ts[:10] if isinstance(ts, str) and len(ts) >= 10 else ""
    if not day:
        day = (now or datetime.now(_TZ)).date().isoformat()
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (slot or "briefing"))
    return f"gamma-briefing-{day}-{safe}.png"


_BRIEFING_MAX_BYTES = 7_500_000   # under Discord's 8 MB webhook ceiling


def _briefing_text_embed(caption: str) -> dict:
    """Minimal Discord embed for the render-failure text fallback."""
    return {"title": "Gamma briefing", "description": caption, "color": _D_GRAY}


def send_gamma_briefing(res: dict, *, slot: str, config: dict | None = None) -> bool:
    """Push a scheduled Gamma briefing to Telegram + Discord as a rendered PNG.

    Returns True if a send was attempted. THREE independent gates: the master
    `enabled`, the `gamma_briefing.enabled`/`slots` block, and a content gate that
    requires a real `analysis` (a degraded page has HTML but no analysis).
    No SMS — an image cannot ride SMS and a bare text line there is noise.

    If the headless render fails (no browser installed, timeout, …) this falls
    back to a TEXT-only push — never to the HTML attachment, which is the payload
    this feature deliberately moved away from. Going silent is worse than a plain
    caption: the read still arrives, just without the infographic.
    Best-effort per channel (the primitives never raise)."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    gb = cfg.get("gamma_briefing") or {}
    if not gb.get("enabled", True):
        return False
    slots = gb.get("slots")
    # `is not None`, NOT truthiness: an operator who sets "slots": [] means "mute
    # every slot", and a falsy test would skip the gate and push all four instead.
    # Key absent -> None -> no slot filter (push all).
    if slots is not None and slot not in slots:
        return False
    res = res or {}
    if not res.get("analysis"):        # degraded run — never push
        return False
    html = res.get("html") or ""
    if not html:
        return False

    caption = briefing_caption(res, slot)
    # route -> the legacy dedicated `gamma_briefing.webhook_url` -> global.
    tok, chat = telegram_target(cfg, "gamma_briefing")
    webhook = discord_target(cfg, "gamma_briefing")

    png = briefing_image.render_html_png(html)
    if not png:
        log.warning("gamma briefing %s: image render failed — pushing text only", slot)
        # send_telegram posts with parse_mode=HTML and the caption carries a
        # MODEL-WRITTEN headline, so a bare '<'/'&' would 400 the whole message.
        # Discord renders plain text, so its embed keeps the unescaped original.
        send_telegram(tok, chat, _html.escape(caption))
        send_discord(webhook, _briefing_text_embed(caption))
        return True

    # The PNG is what gets uploaded, so the size guard measures IT (the source
    # HTML's size is now irrelevant). ~530 KB measured; a silent 413 would be
    # invisible, so fail loudly-in-the-log instead.
    if len(png) > _BRIEFING_MAX_BYTES:
        log.warning("gamma briefing %s too large to push (%d bytes)", slot, len(png))
        return False
    filename = briefing_filename(res, slot)
    send_telegram_photo(tok, chat, filename, png, caption)
    send_discord_file(webhook, filename, png, caption, content_type="image/png")
    return True


_MS_MAX_BYTES = _BRIEFING_MAX_BYTES   # reuse the same size ceiling


def market_snapshot_caption(trend, sentiment, regime) -> str:
    t = (trend or {}).get("label") or "—"
    raw = (sentiment or {}).get("total_score")
    # sentiment_svc publishes total_score as a formatted string (e.g. "7.80");
    # accept int/float OR a numeric string, but never a bool.
    try:
        s = "—" if isinstance(raw, bool) else f"{float(raw):.1f}"
    except (TypeError, ValueError):
        s = "—"
    r = (regime or {}).get("label") or "—"
    return f"📊 Market Snapshot — Trend: {t} · Sentiment: {s}/10 · Regime: {r}"


def send_market_snapshot(dashboard, trend, sentiment, regime, intraday, regime_hist,
                         *, slot: str, config: dict | None = None) -> bool:
    """Push the 30-min market snapshot PNG to Telegram + Discord. Never raises.

    Two gates: the master ``enabled`` and the ``market_snapshot.enabled`` block.
    On render failure, falls back to a TEXT caption (never goes silent). No SMS."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    block = cfg.get("market_snapshot") or {}
    if not block.get("enabled", True):
        return False
    caption = market_snapshot_caption(trend, sentiment, regime)
    # route -> the legacy `discord.market_snapshot_webhook_url` -> global.
    tok, chat = telegram_target(cfg, "market_snapshot")
    webhook = discord_target(cfg, "market_snapshot")
    doc = market_snapshot.market_snapshot_doc(dashboard, trend, sentiment, regime,
                                              intraday, regime_hist, subtitle=f"{slot} CT")
    png = briefing_image.render_html_png(doc)
    if not png:
        log.warning("market snapshot %s: render failed — pushing text only", slot)
        send_telegram(tok, chat, _html.escape(caption))
        send_discord(webhook, {"description": caption})
        return True
    if len(png) > _MS_MAX_BYTES:
        log.warning("market snapshot %s too large (%d bytes)", slot, len(png))
        return False
    filename = f"market-snapshot-{slot.replace(':', '')}.png"
    send_telegram_photo(tok, chat, filename, png, caption)
    send_discord_file(webhook, filename, png, caption, content_type="image/png")
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


def load_post_count(bus, key: str, today: str) -> int:
    """Today's tweet count from the date-scoped counter (0 on a new day)."""
    env = bus.cache_get(key)
    p = env.payload if (env is not None and isinstance(env.payload, dict)) else None
    return int(p.get("count") or 0) if (p and p.get("date") == today) else 0


def save_post_count(bus, key: str, today: str, count: int) -> None:
    bus.cache_set(key, {"date": today, "count": count})


def notify_twitter(bus, deduped_new: list, *, today: str, count_key: str,
                   cfg: dict | None = None, key_fn=signal_key) -> list:
    """Post fresh scanner signals to X/Twitter, gated + capped. Returns posted keys.

    Independent of the private channels' gates: applies the ``twitter`` block's
    OWN ``min_score`` (so only your stronger signals go PUBLIC while weaker ones
    still push to Telegram/Discord) and a persisted per-day ``daily_cap`` (a quota
    guard against the free-tier monthly write cap + spam-flagging). ``dry_run``
    formats + logs without posting. Best-effort — a per-tweet failure is skipped,
    never raised. Off by default (``twitter.enabled`` absent/false → no-op).
    """
    cfg = cfg or load_config()
    tw = (cfg.get("twitter") or {})
    if not tw.get("enabled"):
        return []

    dry = bool(tw.get("dry_run"))
    creds = {k: tw.get(k) for k in _TWITTER_CRED_KEYS}
    if not dry and not _has_twitter_creds(creds):
        return []

    min_score = tw.get("min_score", 0) or 0
    cands = ([s for s in deduped_new if (s.get("composite_score") or 0) >= min_score]
             if min_score else list(deduped_new))
    if not cands:
        return []

    count = load_post_count(bus, count_key, today)
    cap = tw.get("daily_cap", 0) or 0
    remaining = (cap - count) if cap else len(cands)
    if remaining <= 0:
        return []

    disclaimer = tw.get("disclaimer", _TWEET_DISCLAIMER)
    posted = []
    for s in cands[:remaining]:
        text = twitter_signal_text(s, hashtags=tw.get("hashtags"),
                                   discord_url=tw.get("discord_url"),
                                   extra_text=tw.get("extra_text"), disclaimer=disclaimer)
        if send_twitter(creds, text, dry_run=dry):
            posted.append(s)
    if posted:
        save_post_count(bus, count_key, today, count + len(posted))
    return [key_fn(s) for s in posted]


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
    deduped, seen_fresh = [], set()
    for s, k in zip(signals, keys):
        if k in new_set and k not in seen_fresh:
            seen_fresh.add(k)
            deduped.append(s)

    # Public X/Twitter post (scanner signals only) — its OWN gates (twitter.enabled
    # / twitter.min_score / daily cap), independent of the private channels below.
    # Guarded so a Twitter failure can NEVER break the private-channel sends.
    if kind == "scanner":
        try:
            notify_twitter(bus, deduped, today=today,
                           count_key=f"{seen_key}:twitter_count", cfg=cfg)
        except Exception:  # noqa: BLE001 — best-effort, never break the base sends
            log.exception("notify_twitter failed")

    # min_score is scanner-only: captured signals carry no composite_score.
    fresh = deduped
    if kind == "scanner" and min_score:
        fresh = [s for s in fresh if (s.get("composite_score") or 0) >= min_score]
    if not fresh:
        return []

    sms = cfg.get("sms", {})
    # Both kinds (scanner + captured) share the one `signals` category.
    tok, chat = telegram_target(cfg, "signals")
    webhook = discord_target(cfg, "signals")
    for s in fresh:
        send_telegram(tok, chat, telegram_signal_text(s))
        send_discord(webhook, discord_signal_embed(s))
    send_sms(sms.get("fi_number"), sms.get("smtp_user"), sms.get("smtp_app_password"),
             sms_summary_text(fresh, kind), subject=f"{len(fresh)} new {kind} signal(s)")
    return [key_fn(s) for s in fresh]
