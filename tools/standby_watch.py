#!/usr/bin/env python
"""Prod's morning check that the standby synced.

A switched-off or stalled standby sends nothing itself, so prod reads the
standby's ``~/standby/status.json`` every morning and pushes a "Server alerts"
notification when the standby is behind or silent. Run once a day by
``trading-<env>-standby-watch.timer`` on PROD only; on any other box it does
nothing. Design: docs/plans/2026-11-22-warm-standby-design.md, "Watching the
standby".

On prod the standby authorizes one key whose forced command is
``cat /home/administrator/standby/status.json``, so a plain ``ssh <target>``
prints the file.

The check never raises and always exits 0: a failed alert is printed, not a
second failure.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import standby_config  # noqa: E402
from tools.standby_sync import generation_age_h  # noqa: E402

WATCH_KEY = "~/.ssh/standby_watch"
TITLE = "The standby is not in step with prod"
UNREADABLE = "standby status unreadable"


def _now() -> dt.datetime:
    """Naive Central time, like a generation's name (the process runs in Central)."""
    return dt.datetime.now()


def _send(title, lines):
    from shared.notify import system_alert
    return system_alert.send(title, lines)


def fetch_status(targets, connect_timeout_sec, run=subprocess.run):
    """The standby's status text, or None. Tries each target in order; the first
    that exits 0 with output wins."""
    key = str(pathlib.Path.home() / WATCH_KEY[2:])
    for target in targets:
        cmd = ["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
               "-o", f"ConnectTimeout={connect_timeout_sec}", target]
        try:
            done = run(cmd, capture_output=True, text=True,
                       timeout=connect_timeout_sec * 2 + 10)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0 and (done.stdout or "").strip():
            return done.stdout
    return None


def assess(status_text, now, max_age_h):
    """The alert's one-line reason, or None when the standby is healthy. Pure."""
    if status_text is None:
        return "standby did not answer"
    try:
        status = json.loads(status_text)
    except ValueError:
        return UNREADABLE
    if not isinstance(status, dict) or "result" not in status:
        return UNREADABLE
    result, detail = status.get("result"), status.get("detail") or ""
    if result in ("failed", "refused"):
        return f"last run {result}: {detail}"
    generation = status.get("generation")
    if not isinstance(generation, str):
        return UNREADABLE
    try:
        age = generation_age_h(generation, now)
    except ValueError:
        return UNREADABLE
    if age > max_age_h:
        return f"last good sync {int(age)} h ago ({generation})"
    return None


def _alert(lines, title=TITLE) -> None:
    try:
        sent = _send(title, lines)
    except Exception as exc:  # noqa: BLE001 - a failed push must not fail the unit
        print(f"standby_watch: alert not sent ({exc})")
        return
    print("standby_watch: alert sent" if sent else "standby_watch: alert not sent")


def main() -> int:
    identity = standby_config.read_identity()
    if identity is None or identity.role != "prod":
        print("standby_watch: not prod / no identity")
        return 0
    try:
        _name, entry = standby_config.peer(identity)
        targets = [t for t in entry.get("ssh") or [] if isinstance(t, str) and t]
        if not targets:
            raise ValueError("the standby has no ssh target")
    except ValueError as exc:
        print(f"standby_watch: standby config unusable ({exc})")
        _alert(["The standby's configuration is unusable, so prod cannot check it.",
                f"Reason: {exc}", "Fix config/standby.toml."],
               title="The standby check is unusable")
        return 0
    settings = standby_config.sync_settings()
    text = fetch_status(targets, settings["connect_timeout_sec"])
    reason = assess(text, _now(), settings["max_age_h"])
    if reason is None:
        print("standby_watch: standby healthy")
        return 0
    print(f"standby_watch: {reason}")
    _alert([reason, "Check the standby box (its nightly sync) and ~/standby/status.json."])
    return 0


if __name__ == "__main__":
    sys.exit(main())
