"""Print each Dealer Positioning view's colour-scale lock from the live cache.

The read-only check for the heatmap's Locked scale after a promote: options_svc
publishes ``scale_lock`` in every view of ``cache:options:gamma`` once the
session's first minutes have passed (``services/options_svc/gamma_window.py``,
``config/gamma_heat.toml [lock]``). Before that, and off a payload from before
the field existed, each view prints ``None`` and the page's legend says the
scale is settling or adapting.

A script and not a ``python -c`` one-liner because the command is run through
PowerShell and ssh, which drop inner quotes. It takes no arguments. From the
repo root with the environment loaded (Redis needs its password)::

    set -a && . ./.env && set +a
    .venv/bin/python tools/show_gamma_scale_lock.py

It writes nothing.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.bus import Bus  # noqa: E402

KEY = "cache:options:gamma"


def report(snapshot):
    """The lines to print for one snapshot payload (a dict, or None)."""
    if not isinstance(snapshot, dict):
        return [f"{KEY}: nothing cached"]
    views = snapshot.get("views") or {}
    if not views:
        return [f"{KEY}: {snapshot.get('symbol')} has no views"]
    lines = [f"symbol {snapshot.get('symbol')}"]
    for name, entry in views.items():
        if not isinstance(entry, dict) or "scale_lock" not in entry:
            lines.append(f"{name:6} no scale_lock field (a payload from before it)")
        else:
            lines.append(f"{name:6} {entry['scale_lock']}")
    return lines


def main():
    try:
        # cache_get returns an envelope; the snapshot is its payload.
        env = Bus().cache_get(KEY)
    except Exception as exc:  # noqa: BLE001 - the message is the point
        hint = ""
        if "authenticat" in str(exc).lower() or "AUTH" in str(exc):
            hint = ("\nRedis needs a password. Load it first, from the repo root:"
                    "\n  set -a && . ./.env && set +a")
        print(f"show_gamma_scale_lock: cannot read the cache: {exc}{hint}",
              file=sys.stderr)
        return 2
    lines = report(env.payload if env else None)
    print("\n".join(lines))
    return 0 if len(lines) > 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
