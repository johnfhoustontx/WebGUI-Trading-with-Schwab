"""Pure classifier for GEX collector health status label."""
from __future__ import annotations

import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

from datetime import date, datetime, time as dtime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from shared import market_calendar as mc  # noqa: E402

TZ = ZoneInfo("America/Chicago")
# The collection window is config, not a literal here: it comes from
# config/sessions.toml via shared/market_calendar.py, the same source the
# collector itself schedules on. These were hardcoded 08:30/15:20, and the open
# had been WRONG since 2026-07-11 -- collection moved to 08:00 CT but this
# classifier still reported "starts 8:30", misreporting for that half hour every
# morning.
MARKET_OPEN, MARKET_CLOSE = mc.window_bounds("collection")
STALE_AFTER_SEC = 120  # 2 x poll interval (1 min)


def _plus_minutes(t: dtime, minutes: int) -> dtime:
    """``t`` shifted by ``minutes`` (same-day; the offsets here are tiny)."""
    return (datetime.combine(date.min, t) + timedelta(minutes=minutes)).time()


# Grace before "no data" reads as a dead collector: the first snapshot lands ~one
# poll interval after the open, so open + 3 min covers it with a small buffer.
# DERIVED from the configured open for the same reason as above -- pinning it to
# a literal 08:33 while collection starts at 08:00 would leave a 33-minute
# window where a genuinely dead collector still read "starting...".
FIRST_POLL_GRACE = _plus_minutes(MARKET_OPEN, 3)


# Cboe's own session vocabulary -- deliberately NOT "pre-market"/"after-hours",
# which would invite wrong assumptions about which symbols quote and when.
_SESSION_LABEL = {
    mc.Session.GTH: "GTH",
    mc.Session.REGULAR: "Regular",
    mc.Session.CURB: "Curb",
    mc.Session.CLOSED: "Closed",
}


def session_label(now_ct) -> str:
    """The named market session at ``now_ct`` -- GTH / Regular / Curb / Closed.

    Surfaced in the Gamma collector status strip so a user seeing sparse data at
    07:00 CT can tell WHY: during GTH only the ~7 ETH-eligible symbols are
    collected. Both extended sessions are inert before the activation date, so
    this reads Regular/Closed exactly as it always did until 2026-08-17.

    Returns ``""`` if the calendar is somehow unreadable -- the strip then omits
    the session rather than asserting a wrong one.
    """
    try:
        return _SESSION_LABEL.get(mc.session_at(now_ct), "")
    except Exception:
        return ""


def _fmt_age(sec: int) -> str:
    if sec < 60:
        return f"{sec}s ago"
    return f"{sec // 60}m ago"


def _fmt_ts(last_ts: int | None) -> str:
    if last_ts is None:
        return "?"
    return datetime.fromtimestamp(last_ts, TZ).strftime("%H:%M")


def classify_collector_status(
    age_seconds: int | None,
    now_ct: datetime,
    has_data: bool,
    last_ts: int | None,
) -> tuple[str, str]:
    """Return (label_text, tk_color) for the collector status widget."""
    is_weekday = now_ct.weekday() < 5
    current_time = now_ct.time()

    if not is_weekday or current_time >= MARKET_CLOSE:
        if has_data and last_ts is not None:
            return f"Collector: last run {_fmt_ts(last_ts)}", "gray"
        return "Collector: idle (outside market hours)", "gray"

    if current_time < MARKET_OPEN:
        if mc.session_at(now_ct) is mc.Session.GTH:
            # A GROWING AGE IS NORMAL HERE -- never a fault. Two reasons stack:
            # only the ~7 ETH-eligible symbols are collected during GTH (the
            # probe symbol $SPX is not one of them, so its age always grows),
            # and per Cboe a class begins its GTH opening rotation only "upon
            # receipt of the first round-lot print in the underlying... and
            # observation of a two-sided bid/ask", with GTH underlying liquidity
            # that "may likely not be as liquid". So an eligible symbol can
            # legitimately have no quotes for part or all of GTH, writing no
            # rows. Reporting that as "stale"/"not running" would cry wolf every
            # single morning.
            #
            # Note this branch is bounded by ``current_time < MARKET_OPEN``:
            # 08:00-08:25 CT is inside BOTH the GTH session and the regular
            # collection window, where the full universe IS polled -- there a
            # growing age is a real fault and still reads as one.
            if has_data and age_seconds is not None and age_seconds <= STALE_AFTER_SEC:
                # Leniency is not blindness: if rows ARE landing, say so.
                return f"● Collector: {_fmt_age(age_seconds)} (GTH)", "green"
            return "Collector: awaiting GTH opens", "gray"
        # Formatted from the CONFIGURED open, never a hardcoded time.
        return f"Collector: starts {MARKET_OPEN.hour}:{MARKET_OPEN.minute:02d}", "gray"

    # In market hours from here on
    if not has_data and current_time >= FIRST_POLL_GRACE:
        return "\u2716 Collector not running", "red"
    if not has_data:
        return "Collector: starting...", "gray"

    if age_seconds is not None and age_seconds > STALE_AFTER_SEC:
        return f"\u26a0 Collector stale: {_fmt_age(age_seconds)}", "#c48b00"

    return f"\u25cf Collector: {_fmt_age(age_seconds or 0)}", "green"
