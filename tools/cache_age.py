"""How stale is a cache view? Answer it without writing a Python one-liner.

Every "is this page showing old numbers" question ends the same way: open a
Bus, read ``{key}:ts``, subtract. Doing that as an inline ``python -c`` through
cmd.exe → ssh → bash → python is four layers of quoting, and it goes wrong more
often than it goes right.

⚠ **The reason this exists NOW is that the answer stopped being obvious.** Redis
gained a ``requirepass`` on 2026-08-31, so a bare ``python -c`` that used to work
fails with ``AuthenticationError: HELLO must be called with the client already
authenticated`` — a message that says nothing about the actual cause, which is a
missing ``MEMURAI_PASSWORD`` in the environment. The services get it from their
systemd ``EnvironmentFile``; an ad-hoc shell does not. This script does not fix
that (it cannot read a file it is not told about), but it fails with a sentence
that names the cause instead of the symptom.

Usage, from the repo root with the environment loaded::

    set -a && . ./.env && set +a
    .venv/bin/python tools/cache_age.py                      # every cache view
    .venv/bin/python tools/cache_age.py sentiment:momentum   # just these
    .venv/bin/python tools/cache_age.py cache:options:matrix # prefix optional
"""
import datetime
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.bus import Bus  # noqa: E402

# A view older than this is called out. Not a correctness threshold -- the
# cadences here run from 30 s to nightly -- just the point past which "is that
# supposed to be old?" is worth a human glance.
STALE_AFTER_MIN = 30

PREFIX = "cache:"


def normalize_key(name):
    """``sentiment:momentum`` and ``cache:sentiment:momentum`` are the same view.

    The prefix is on every key, so requiring it is pure ceremony -- but pasting
    a key copied out of a log or this repo's docs must also work, so accept both.
    """
    name = name.strip()
    return name if name.startswith(PREFIX) else PREFIX + name


def format_age(seconds):
    """Seconds since the write, as something a human reads at a glance.

    Minutes up to an hour, then hours, then days -- because the useful question
    changes with the scale: a 90-second-old tick view is fine, a 90-minute-old
    one is not, and a nightly view is meant to be hours old.
    """
    if seconds is None:
        return "never written"
    if seconds < 90:
        return "%.0fs ago" % seconds
    if seconds < 5400:
        return "%.0f min ago" % (seconds / 60)
    if seconds < 172800:
        return "%.1f h ago" % (seconds / 3600)
    return "%.1f days ago" % (seconds / 86400)


def ages(bus, keys, now=None):
    """``[(key, seconds_or_None, iso_or_None)]`` for each key, in the given order.

    Reads only ``{key}:ts`` -- the tiny side key ``cache_set`` writes -- never the
    payload. A stale-check that deserialised a 5 MB view to read its timestamp
    would cost more than the thing it is checking.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for key in keys:
        raw = bus._r.get(key + ":ts")
        if not raw:
            out.append((key, None, None))
            continue
        written = datetime.datetime.fromisoformat(raw)
        out.append((key, (now - written).total_seconds(), raw))
    return out


def discover(bus):
    """Every cache view that has a timestamp, sorted. Excludes the side keys."""
    found = set()
    for k in bus._r.keys(PREFIX + "*"):
        if k.endswith(":ts"):
            found.add(k[: -len(":ts")])
    return sorted(found)


def render(rows, stale_after_min=STALE_AFTER_MIN):
    """The report, as a list of lines. Pure, so the formatting is testable."""
    if not rows:
        return ["no cache views found"]
    width = max(len(k) for k, _s, _r in rows)
    lines = []
    for key, secs, _raw in rows:
        mark = ""
        if secs is None:
            mark = "  <- never written"
        elif secs > stale_after_min * 60:
            mark = "  <- stale?"
        lines.append("%-*s  %-14s%s" % (width, key, format_age(secs), mark))
    return lines


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        bus = Bus()
        keys = [normalize_key(a) for a in argv] if argv else discover(bus)
        rows = ages(bus, keys)
    except Exception as exc:  # noqa: BLE001 - the message IS the feature
        # Name the likely cause. An auth error here almost always means the
        # environment was not loaded, and the raw exception says nothing about
        # that -- which is the whole reason this script exists.
        hint = ""
        if "authenticat" in str(exc).lower() or "AUTH" in str(exc):
            hint = ("\nRedis needs a password. Load it first, from the repo root:"
                    "\n    set -a && . ./.env && set +a")
        sys.stderr.write("could not read the cache: %s%s\n" % (exc, hint))
        return 2
    for line in render(rows):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
