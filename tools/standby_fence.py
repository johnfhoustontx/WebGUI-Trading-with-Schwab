#!/usr/bin/env python
"""The start fence: the production stack starts only on the box that is prod.

Every stack unit ``Requires=`` the oneshot that runs this (``deploy/systemd/
generate_units.py``), so a box that was restored from the other one's backup, or
that was left behind after a takeover, cannot start a second copy of the stack.

**The fence fails OPEN.** It reaches the live production server through an
ordinary deploy, where no identity file exists yet, and a fence that wrongly
refuses takes the live trading stack down. It refuses in exactly two cases (the
two "refuse" rows of ``decide``); every other condition, an unreadable file, a
config without two boxes, a lookup that failed, exits 0 and says why.

Design: docs/plans/2026-11-22-warm-standby-design.md
"""
from __future__ import annotations

import pathlib
import socket
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from repo_paths import APP_HOST, ENV_NAME  # noqa: E402
from shared import standby_config  # noqa: E402

LOOKUP_TIMEOUT_S = 5.0


def decide(identity, env_name, self_ip, peer_ip, addresses):
    """``(start_allowed, reason, clear_takeover)``. ``addresses`` is None when
    the lookup failed or timed out. An empty or missing IP is unknown."""
    if env_name == "dev":
        return True, "dev is never fenced", False
    if identity is None:
        return True, "no box identity", False
    if identity.role != "prod":
        return False, "role is standby", False
    if addresses is None:
        return True, "lookup failed; not evidence", False
    if not self_ip or not peer_ip or not addresses:
        return True, "an address is unknown; not evidence", False
    if self_ip in addresses:
        return True, "public name points at this box", bool(identity.taking_over)
    if all(a == peer_ip for a in addresses):
        if identity.taking_over:
            return True, "taking over; DNS not moved yet", False
        return False, f"public name points at {peer_ip}", False
    return True, "unknown address; not evidence", False


def resolve(host, timeout_s):
    """The IPv4 addresses of ``host``, or None on failure or after
    ``timeout_s``. The lookup runs on a daemon thread, so a resolver that hangs
    cannot hold the fence (or the process's exit) past the timeout."""
    box: list = []

    def work():
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            box.append(sorted({info[4][0] for info in infos}))
        except Exception:  # noqa: BLE001 - a failed lookup is "not evidence"
            box.append(None)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    return box[0] if box else None


def _run():
    """``(allowed, reason)``; may raise, ``main`` turns that into an allow."""
    identity = standby_config.read_identity()
    if identity is None or ENV_NAME == "dev":
        return decide(identity, ENV_NAME, None, None, None)[:2]
    if identity.role != "prod":
        return decide(identity, ENV_NAME, None, None, None)[:2]
    self_ip = standby_config.boxes()[identity.self_name]["public_ip"] or None
    peer_ip = standby_config.peer(identity)[1]["public_ip"] or None
    allowed, reason, clear = decide(
        identity, ENV_NAME, self_ip, peer_ip, resolve(APP_HOST, LOOKUP_TIMEOUT_S))
    if allowed and clear:
        try:
            standby_config.write_identity(
                standby_config.Identity(identity.self_name, identity.role, False))
            reason += "; takeover marker cleared"
        except Exception as exc:  # noqa: BLE001 - still allowed
            reason += f"; could not clear the takeover marker ({exc})"
    return allowed, reason


def main():
    """0 allowed, 1 refused. Prints one line either way (it lands in the journal)."""
    try:
        allowed, reason = _run()
    except Exception as exc:  # noqa: BLE001 - any doubt allows the start
        print(f"fence: allow - could not decide ({type(exc).__name__}: {exc})")
        return 0
    if allowed:
        print(f"fence: allow - {reason}")
        return 0
    print(f"fence: REFUSE - {reason}; this box is not prod, stack held")
    return 1


if __name__ == "__main__":
    sys.exit(main())
