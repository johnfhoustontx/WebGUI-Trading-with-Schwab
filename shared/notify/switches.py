"""Per-category push-notification channel switches (config/notify.toml).

Settings -> General flips these by writing ``config/local/notify.toml``. They are
read at SEND time through the layered, mtime-cached ``toml_loader``, so a switch
applies on the next send with no service restart.

Enforced at the two routing chokepoints, ``channels.discord_target`` and
``channels.telegram_target``: a switched-off category resolves to no target, and
every sender already treats that as a no-op.

A missing or malformed value reads as the DEFAULT - ON for discord/telegram, OFF
for calendar - because a typo must never silently mute a feed, and must never
start writing to someone's calendar either.
"""
from repo_paths import NOTIFY_TOML
from shared.config_toml import toml_loader

_DEFAULT_ON = ("discord", "telegram")
# lead_min 5: at 1 minute the popup never arrived (the device had not synced the
# event before its reminder was due); 4 worked on phone and browser (2026-09-28).
_CAL_DEFAULTS = {"calendar_id": "", "lead_min": 5, "duration_min": 5}
_CAL_FLOOR = {"lead_min": 0, "duration_min": 1}
# [site]: each posted trade idea is also written into deploy/site (the public
# site's home-page strip and ideas.html). keep_days counts POSTING days.
# refresh_min: how often each open idea's result is recomputed from the stock price.
_SITE_DEFAULTS = {"trade_ideas": True, "keep_days": 6, "refresh_min": 15}
_SITE_RANGES = {"keep_days": (1, 30), "refresh_min": (5, 60)}


def _make_loader(path):
    return toml_loader(path, {"channels": {}, "calendar": dict(_CAL_DEFAULTS),
                              "site": dict(_SITE_DEFAULTS)},
                       label="notify.toml")


_load, reset = _make_loader(NOTIFY_TOML)


def enabled(category, channel) -> bool:
    """Is ``channel`` ("discord" | "telegram" | "calendar") on for ``category``?"""
    default = channel in _DEFAULT_ON
    try:
        row = _load().get("channels", {}).get(category)
        value = row.get(channel) if isinstance(row, dict) else None
    except Exception:  # noqa: BLE001 - a switch read must never break a send
        return default
    return value if isinstance(value, bool) else default


def calendar_settings() -> dict:
    """``{"calendar_id", "lead_min", "duration_min"}`` - bad values read as the
    defaults, one key at a time."""
    out = dict(_CAL_DEFAULTS)
    try:
        block = _load().get("calendar")
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(block, dict):
        return out
    if isinstance(block.get("calendar_id"), str):
        out["calendar_id"] = block["calendar_id"].strip()
    for key, floor in _CAL_FLOOR.items():
        v = block.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= floor:
            out[key] = v
    return out


def site_settings() -> dict:
    """``{"trade_ideas", "keep_days", "refresh_min"}`` for publishing trade ideas
    to the site. Bad values read as the defaults, one key at a time; never raises."""
    out = dict(_SITE_DEFAULTS)
    try:
        block = _load().get("site")
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(block, dict):
        return out
    if isinstance(block.get("trade_ideas"), bool):
        out["trade_ideas"] = block["trade_ideas"]
    for key, (lo, hi) in _SITE_RANGES.items():
        v = block.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
            out[key] = v
    return out
