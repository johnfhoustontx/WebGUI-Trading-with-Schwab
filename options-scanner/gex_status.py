"""Pure classifier for GEX collector health status label."""
from __future__ import annotations

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
MARKET_OPEN  = dtime(8, 30)
MARKET_CLOSE = dtime(15, 20)
STALE_AFTER_SEC = 120  # 2 x poll interval (1 min)
# Grace before "no data" reads as a dead collector: the first snapshot lands ~one
# poll interval after open (08:30 + ~2 min), so 08:33 covers it with a small buffer.
FIRST_POLL_GRACE = dtime(8, 33)


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
        return "Collector: starts 8:30", "gray"

    # In market hours from here on
    if not has_data and current_time >= FIRST_POLL_GRACE:
        return "\u2716 Collector not running", "red"
    if not has_data:
        return "Collector: starting...", "gray"

    if age_seconds is not None and age_seconds > STALE_AFTER_SEC:
        return f"\u26a0 Collector stale: {_fmt_age(age_seconds)}", "#c48b00"

    return f"\u25cf Collector: {_fmt_age(age_seconds or 0)}", "green"
