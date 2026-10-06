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

STREAM LENGTH. How many entries a command stream keeps (``stream_keep``). One
number for every stream was sized for commands of a few hundred bytes; a stream
whose entries each carry a whole document needs a much smaller one.

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
    # How many entries each command stream keeps: ``default`` for every stream,
    # and a number of its own for a stream named here. The two named ones carry
    # a whole blog document in each entry (up to config/blog.toml [limits]
    # max_html_kb), so a thousand of them is hundreds of megabytes of Redis.
    # ⚠ Keyed by stream NAME. shared/tests/test_service_limits.py pins these two
    # against shared.blog_inbox, so a renamed stream cannot quietly lose its cap.
    "stream_keep": {"default": 1000, "cmd:blog": 50, "cmd:blog_inbox": 50},
}
MAX_SEC = 7 * 24 * 3600        # past a week a "limit" is a typo
# What a stream cap may be. The floor is 1 because XADD with MAXLEN 0 empties
# the stream as it writes: the command just queued would be gone before its
# service read it.
STREAM_KEEP_MIN, STREAM_KEEP_MAX = 1, 100000

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


def _keep(raw):
    """``raw`` when it is a usable stream cap, else None. A bool is refused
    (``True`` is an int and would read as 1), and so is any float: a cap is a
    count."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if STREAM_KEEP_MIN <= raw <= STREAM_KEEP_MAX else None


def stream_keep(stream) -> int:
    """How many entries the command stream ``stream`` keeps (``cmd:blog``).

    ``Bus.enqueue_command`` trims the stream to this on every write, so it is
    the most Redis ever holds of that stream's history. Read at every enqueue:
    a change applies to the next command with no restart.

    In order: the stream's own number in ``[stream_keep]``; else the number the
    stream SHIPS with, if it has one; else the table's ``default``; else 1000.
    The second step is the point. A typo in ``"cmd:blog" = 50`` reads as 50 and
    not as the default, because falling back to "keep a thousand documents" is
    the failure this table exists to prevent. Never raises; a stream this table
    has never heard of, or a name that is not a string, gets the default."""
    sec = load().get("stream_keep")
    table = sec if isinstance(sec, dict) else {}
    shipped = DEFAULTS["stream_keep"]
    if isinstance(stream, str) and stream != "default":
        own = _keep(table.get(stream))
        if own is not None:
            return own
        if stream in shipped:
            return shipped[stream]
    default = _keep(table.get("default"))
    return shipped["default"] if default is None else default


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
