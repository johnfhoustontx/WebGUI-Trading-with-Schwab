"""Limits every service shares, from ``config/services.toml``.

COMMAND AGE. How old a queued command may be before a service refuses to run it.

A consumer group is created at stream id 0, so a group that is new (a first
launch, a flushed Redis, a renamed group) is handed the stream's whole history.
The documented incident: a first launch "burned a day's API budget in one go".
Two limits, from ``config/services.toml``:

* ``side_effect_max_sec`` (180) - commands that change a paper book, spend a
  paid call or post in public. A click that waited longer than this is acted on
  stale information; it is refused and the page is told where one is watching.
* ``replay_max_sec`` (900) - every other command on a service's own stream. A
  command this old is not a click somebody is waiting on; it is history.

A command with no readable enqueue stamp has no age and is never refused: that
is a command serialized before the stamp existed.

Stdlib and the config loader only, so every tier may read it.
"""
import datetime as _dt
import math

from repo_paths import SERVICES_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "age": {"side_effect_max_sec": 180, "replay_max_sec": 900},
    # /health says a service is not up when its scheduler loop has not gone
    # round for this long; and a scheduler that ran this long without dying
    # gets its restart budget back.
    "health": {"tick_stale_sec": 600, "restart_reset_sec": 3600},
    # Commands a service could not run are kept on a list for a person to read.
    # Only the newest this-many are kept.
    "dead_letters": {"keep": 200},
    # Threads in each service's shared pool: scheduler branches and anything
    # else handed to the event loop's default executor. Each command queue has
    # a thread of its own outside this pool.
    "pool": {"workers": 16},
}
MAX_SEC = 7 * 24 * 3600        # past a week a "limit" is a typo

load, reset_cache = toml_loader(SERVICES_TOML, DEFAULTS, label="services.toml")


def _seconds(key, table="age") -> int:
    """``[<table>].<key>`` as whole seconds from 1 to a week, else the shipped value."""
    sec = load().get(table)
    raw = sec.get(key) if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULTS[table][key]
    try:
        ok = math.isfinite(raw) and 1 <= raw <= MAX_SEC
    except (TypeError, OverflowError):
        ok = False
    return int(raw) if ok else DEFAULTS[table][key]


def dead_letter_keep() -> int:
    """How many un-run commands each stream's dead-letter list keeps (newest)."""
    sec = load().get("dead_letters")
    raw = sec.get("keep") if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 100000:
        return DEFAULTS["dead_letters"]["keep"]
    return raw


def pool_workers() -> int:
    """Threads in a service's shared pool (2 to 256), else the shipped value."""
    sec = load().get("pool")
    raw = sec.get("workers") if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 2 <= raw <= 256:
        return DEFAULTS["pool"]["workers"]
    return raw


def tick_stale_sec() -> int:
    """How long a scheduler loop may go without a pass before ``/health``
    reports the service as not up."""
    return _seconds("tick_stale_sec", "health")


def restart_reset_sec() -> int:
    """How long a scheduler must run without dying to get its restart budget
    back."""
    return _seconds("restart_reset_sec", "health")


def side_effect_max_sec() -> int:
    return _seconds("side_effect_max_sec")


def replay_max_sec() -> int:
    return _seconds("replay_max_sec")


def age_seconds(command):
    """Seconds since ``command`` was enqueued, or None when it carries no
    readable stamp. A stamp with no timezone is read as UTC."""
    ts = getattr(command, "ts", None)
    if not ts or not isinstance(ts, str):
        return None
    try:
        when = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds()


def older_than(command, limit_sec) -> bool:
    """True only when the command HAS an age and it is over ``limit_sec``."""
    age = age_seconds(command)
    return age is not None and age > limit_sec
