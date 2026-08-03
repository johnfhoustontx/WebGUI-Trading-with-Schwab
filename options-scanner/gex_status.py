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
