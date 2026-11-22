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

It also finishes a takeover: the start fence clears ``taking_over`` only when
it runs (at a cold start), so the watch clears it on the first morning the public
name points at this box, and alerts every morning it does not. A marker left set
would let this box start a second stack after a later failover the other way.

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

from repo_paths import APP_HOST  # noqa: E402
from shared import standby_config  # noqa: E402
from shared.standby_config import Identity  # noqa: E402
from tools import standby_fence  # noqa: E402
from tools.standby_sync import generation_age_h  # noqa: E402

WATCH_KEY = "~/.ssh/standby_watch"
TITLE = "The standby is not in step with prod"
MARKER_TITLE = "This box is still marked as taking over"
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


def _lookup(host):
    """The fence's retried lookup, with the fence's own limits."""
    return standby_fence.lookup(host, standby_config.fence_settings())


def assess(status_text, now, max_age_h, status_max_age_h=None):
    """The alert's one-line reason, or None when the standby is healthy. Pure.

    ``status_max_age_h`` (when given) is the age past which the status file
    itself is stale: the standby's sync has not run at all, whatever its last
    result said."""
    if status_text is None:
        return "standby did not answer"
    try:
        status = json.loads(status_text)
    except ValueError:
        return UNREADABLE
    if not isinstance(status, dict) or "result" not in status:
        return UNREADABLE
    checked = status.get("checked_at")
    if status_max_age_h is not None and checked is not None:
        try:
            ran = dt.datetime.fromisoformat(str(checked))
            stale = (now - ran).total_seconds() > status_max_age_h * 3600
        except (TypeError, ValueError):
            return UNREADABLE
        if stale:
            return f"the standby has not run since {checked}"
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


def check_takeover(identity) -> None:
    """Clear the takeover marker once the public name points at this box;
    alert (every morning) while it does not. Does nothing without the marker."""
    if not identity.taking_over:
        return
    self_ip = (standby_config.boxes().get(identity.self_name) or {}).get("public_ip") or ""
    addresses = _lookup(APP_HOST)
    if self_ip and addresses and self_ip in addresses:
        try:
            standby_config.write_identity(Identity(identity.self_name, identity.role, False))
        except OSError as exc:
            print(f"standby_watch: could not clear the takeover marker ({exc})")
            _alert([f"The takeover marker could not be cleared: {exc}",
                    "Set taking_over = false in ~/.config/neuralstrike/box.toml."],
                   title=MARKER_TITLE)
            return
        print("standby_watch: takeover marker cleared (the public name points at this box)")
        return
    seen = "the lookup failed" if addresses is None else         f"it resolves to {', '.join(addresses) or 'nothing'}"
    print(f"standby_watch: taking over, but {APP_HOST} does not point here ({seen})")
    _alert(["This box is marked as taking over but the public name does not point at it yet.",
            f"{APP_HOST}: {seen}; this box is {self_ip or 'not in standby.toml'}.",
            "Point the Wix A records at this box. The marker clears itself the first "
            "morning they do."], title=MARKER_TITLE)


def main() -> int:
    identity = standby_config.read_identity()
    if identity is None or identity.role != "prod":
        print("standby_watch: not prod / no identity")
        return 0
    try:
        check_takeover(identity)
    except Exception as exc:  # noqa: BLE001 - the standby check below still runs
        print(f"standby_watch: takeover check failed ({type(exc).__name__}: {exc})")
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
    reason = assess(text, _now(), settings["max_age_h"],
                    standby_config.watch_settings()["status_max_age_h"])
    if reason is None:
        print("standby_watch: standby healthy")
        return 0
    print(f"standby_watch: {reason}")
    _alert([reason, "Check the standby box (its nightly sync) and ~/standby/status.json."])
    return 0


if __name__ == "__main__":
    sys.exit(main())
