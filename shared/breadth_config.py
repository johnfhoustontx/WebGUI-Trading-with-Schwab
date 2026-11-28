"""The daily breadth history's settings, from ``config/breadth.toml``.

Read by ``services/market_svc/breadth_daily.py`` at each check and by
``tools/breadth_backfill.py``, so a saved override applies without a restart.
Tier 1 does not read it.

Missing file / bad TOML / missing key / unusable value -> the built-in default
for that key, never a raise. ``load()`` returns a fresh, cleaned mapping.
Design: docs/plans/2026-11-27-market-cycle-design.md.
"""
import datetime as _dt
import re

from repo_paths import BREADTH_TOML
from shared.config_toml import toml_loader
from shared.numeric import finite

DEFAULTS = {
    "enabled": True,
    # Minutes after the close (the early close on a half day) for the close row.
    # Above the new highs / lows settle (10), so the day's counts are final.
    "settle_min": 20,
    # Central times between which the previous session is corrected from history.
    "correct_at": "07:10",
    "correct_until": "12:00",
    "retry_min": 30,
    # A correction moving a closing-quote value by more than this (percent) is a degrade.
    "tolerance_pct": 2.0,
    # Advancers + decliners below this at the close is no reading.
    "min_issues": 1000,
    "history_years": 20,
    "timeout_sec": 20,
}

load_raw, reset_cache = toml_loader(BREADTH_TOML, DEFAULTS, label="breadth.toml")

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def clock(text: str) -> _dt.time:
    """``"HH:MM"`` as a time. Raises on anything else: ``load()`` only hands out
    text that passed ``_time``."""
    m = _TIME.match(text)
    if not m:
        raise ValueError(f"not a HH:MM time: {text!r}")
    return _dt.time(int(m.group(1)), int(m.group(2)))


def _whole(value, default, low, high):
    f = finite(value)
    return int(f) if f is not None and f == int(f) and low <= f <= high else default


def _real(value, default, low, high):
    f = finite(value)
    return float(f) if f is not None and low <= f <= high else default


def _time(value, default):
    return value if isinstance(value, str) and _TIME.match(value) else default


def load() -> dict:
    """The cleaned settings, as a fresh mapping the caller may keep."""
    raw = load_raw()
    start = _time(raw.get("correct_at"), DEFAULTS["correct_at"])
    until = _time(raw.get("correct_until"), DEFAULTS["correct_until"])
    if clock(until) <= clock(start):
        start, until = DEFAULTS["correct_at"], DEFAULTS["correct_until"]
    return {
        "enabled": raw.get("enabled") is not False,
        "settle_min": _whole(raw.get("settle_min"), DEFAULTS["settle_min"], 11, 120),
        "correct_at": start,
        "correct_until": until,
        "retry_min": _whole(raw.get("retry_min"), DEFAULTS["retry_min"], 5, 240),
        "tolerance_pct": _real(raw.get("tolerance_pct"), DEFAULTS["tolerance_pct"], 0.1, 50),
        "min_issues": _whole(raw.get("min_issues"), DEFAULTS["min_issues"], 0, 5000),
        "history_years": _whole(raw.get("history_years"), DEFAULTS["history_years"], 1, 20),
        "timeout_sec": _whole(raw.get("timeout_sec"), DEFAULTS["timeout_sec"], 5, 120),
    }
