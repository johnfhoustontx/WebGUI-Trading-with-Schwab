#!/usr/bin/env python
"""Tell a person that a systemd unit ended up FAILED.

Run by ``OnFailure=`` (see ``deploy/systemd/generate_units.py``), as::

    python tools/notify_failure.py trading-prod-backup.service

systemd starts it when a unit enters the failed state: a service that
crash-looped until its restart budget ran out, or a timer job - the nightly
backup, a report - whose run exited non-zero. It does NOT fire for a single
crash that ``Restart=on-failure`` recovers from, which is the point: an alert
means "this is down and staying down".

Until 2026-10-03 none of this reached anyone without an open Status tab (audit
AR-03); a failed backup in particular was silent.

What it sends is the unit, why systemd says it failed, and the command that
shows its log. ⚠ It never quotes the log: a log line can carry a credential (a
connection error prints its request URL), and this text goes to a chat app.

One alert per unit per ``[system] failure_repeat_hours`` (config/notify.toml),
remembered in ``logs/notify_failure/`` - a job on a 15-minute timer would
otherwise send four an hour.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import socket
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "logs" / "notify_failure"

_RESULTS = {
    "start-limit-hit": "It kept failing, so systemd stopped trying to restart it.",
    "exit-code": "Its process exited with an error.",
    "signal": "Its process was killed by a signal.",
    "timeout": "It did not finish inside its time limit.",
    "oom-kill": "It was killed for using too much memory.",
    "core-dump": "Its process crashed.",
    "watchdog": "It stopped responding.",
}


def show(unit) -> dict:
    """``Result`` and ``ExecMainStatus`` for ``unit`` from systemd; {} on any failure."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "Result", "-p", "ExecMainStatus"],
            capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return {}
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)


def message(unit, facts, *, host):
    """``(title, lines)`` for a failed ``unit``. Pure."""
    name = unit[:-len(".service")] if unit.endswith(".service") else unit
    result = str(facts.get("Result") or "unknown")
    lines = [_RESULTS.get(result, "systemd marked it failed."),
             f"Reason: {result}"
             + (f", status {facts['ExecMainStatus']}" if facts.get("ExecMainStatus") else ""),
             f"Server: {host}",
             f"Log: journalctl --user -u {unit} -n 50"]
    return f"{name} failed", lines


def _stamp_path(state_dir, unit) -> pathlib.Path:
    return pathlib.Path(state_dir) / (re.sub(r"[^A-Za-z0-9_.@-]", "_", unit) + ".stamp")


def _default_send(title, lines):
    sys.path.insert(0, str(ROOT))
    from shared.notify import system_alert
    return system_alert.send(title, lines)


def _repeat_hours() -> int:
    try:
        sys.path.insert(0, str(ROOT))
        from shared.notify import switches
        return switches.system_settings()["failure_repeat_hours"]
    except Exception:  # noqa: BLE001
        return 6


def run(unit, *, now=None, state_dir=STATE_DIR, repeat_hours=None, show=show,
        send=None, host=None) -> str:
    """Alert about ``unit`` unless one was sent inside the repeat window.

    Returns ``sent``, ``throttled``, ``not_sent`` (no channel took it) or
    ``ignored`` (the notifier's own unit). Never raises.
    """
    if "notify-failure@" in unit:
        return "ignored"          # never alert about the alerter: that is a loop
    now = now or dt.datetime.now(dt.timezone.utc)
    repeat_hours = _repeat_hours() if repeat_hours is None else repeat_hours
    stamp = _stamp_path(state_dir, unit)
    try:
        last = dt.datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip())
        if now - last < dt.timedelta(hours=repeat_hours):
            return "throttled"
    except Exception:  # noqa: BLE001 - no stamp, or an unreadable one: send
        pass
    try:
        title, lines = message(unit, show(unit), host=host or socket.gethostname())
        channels = (send or _default_send)(title, lines)
    except Exception:  # noqa: BLE001
        return "not_sent"
    if not channels:
        return "not_sent"
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(now.isoformat(), encoding="utf-8")
    except Exception:  # noqa: BLE001 - the alert went out; a repeat is the worst case
        pass
    return "sent"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: notify_failure.py <unit>", file=sys.stderr)
        return 2
    print(f"notify_failure {argv[0]}: {run(argv[0])}")
    return 0          # never fails: a failed notifier must not look like a second outage


if __name__ == "__main__":
    sys.exit(main())
