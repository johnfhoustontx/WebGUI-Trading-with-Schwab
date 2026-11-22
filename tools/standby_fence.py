#!/usr/bin/env python
"""The start fence: the production stack starts only on the box that is prod.

Every stack unit ``Requires=`` the oneshot that runs this (``deploy/systemd/
generate_units.py``), so a box that was restored from the other one's backup, or
that was left behind after a takeover, cannot start a second copy of the stack.

**The fence fails OPEN.** It reaches the live production server through an
ordinary deploy, where no identity file exists yet, and a fence that wrongly
refuses takes the live trading stack down. It refuses in exactly three cases (the
"refuse" rows of ``decide``): the role is standby; the peer's proxy answers its
health URL and this box is not taking over; the public name points at the peer.
Every other condition, an unreadable file, a config without two boxes, a lookup
that failed, a peer that did not answer, even a module that will not import,
exits 0 and says why. So this module imports only the stdlib and ``repo_paths``
at the top; ``shared.standby_config`` is imported inside ``_run``, where
``main``'s catch-all turns a failed import into an allow.

Design: docs/plans/2026-11-22-warm-standby-design.md
"""
from __future__ import annotations

import importlib
import pathlib
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from repo_paths import APP_HOST, ENV_NAME  # noqa: E402

# A front with nothing behind it, never a proxy's own answer (see probe_proxy).
GATEWAY_ERRORS = (502, 503, 504)


def _config():
    """``shared.standby_config``, imported when it is needed (see the docstring)."""
    return importlib.import_module("shared.standby_config")


def decide(identity, env_name, self_ip, peer_ip, addresses, peer_answers=None):
    """``(start_allowed, reason, clear_takeover)``. ``addresses`` is None when
    the lookup failed or timed out. An empty or missing IP is unknown.
    ``peer_answers`` is True only when the peer's proxy health URL answered with
    an HTTP status; None or False (not probed, no connection, a timeout) is no
    evidence."""
    if env_name == "dev":
        return True, "dev is never fenced", False
    if identity is None:
        return True, "no box identity", False
    if identity.role != "prod":
        return False, "role is standby", False
    if peer_answers is True and not identity.taking_over:
        return False, "the peer's proxy answers; this box is not prod", False
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


def lookup(host, settings, resolve_fn=None, sleep=time.sleep, clock=time.monotonic):
    """``resolve`` retried while it fails, for at most ``settings["lookup_retry_s"]``
    seconds in all (each attempt bounded by ``lookup_timeout_s``). At boot a user
    unit cannot wait for the network, so the first lookup can fail on a box whose
    DNS is fine a few seconds later. None when every attempt failed."""
    resolve_fn = resolve_fn or resolve
    timeout, pause = settings["lookup_timeout_s"], settings["lookup_pause_s"]
    deadline = clock() + settings["lookup_retry_s"]
    while True:
        got = resolve_fn(host, timeout)
        if got is not None:
            return got
        if clock() + pause + timeout > deadline:
            return None
        sleep(pause)


def probe_proxy(url, timeout_s):
    """True when ``url`` answers with an HTTP status (a proxy is running there);
    None when it was not probed or not reached: no connection, a timeout, a TLS
    failure, or a GATEWAY error (502, 503, 504). A live proxy's ``/health``
    answers 200 whatever its state; a gateway error is ``tailscale serve``
    answering with no proxy behind it (a demoted box keeps its serve config), and
    reading that as a proxy would hold a healthy prod at boot. The certificate is
    checked against the system's authorities, as ``curl`` does (a tailnet name
    carries a public certificate). Runs on a daemon thread, so a peer that accepts
    and then stalls cannot hold the fence."""
    if not url:
        return None
    box: list = []

    def work():
        try:
            with urllib.request.urlopen(url, timeout=timeout_s,
                                        context=ssl.create_default_context()):
                box.append(True)
        except urllib.error.HTTPError as exc:
            box.append(None if exc.code in GATEWAY_ERRORS else True)
            try:
                exc.close()
            except Exception:  # noqa: BLE001 - it answered; closing is housekeeping
                pass
        except Exception:  # noqa: BLE001 - an unreachable peer is not evidence
            box.append(None)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    return box[0] if box else None


def _run():
    """``(allowed, reason)``; may raise, ``main`` turns that into an allow."""
    standby_config = _config()
    identity = standby_config.read_identity()
    if identity is None or ENV_NAME == "dev" or identity.role != "prod":
        return decide(identity, ENV_NAME, None, None, None)[:2]
    settings = standby_config.fence_settings()
    self_ip = standby_config.boxes()[identity.self_name]["public_ip"] or None
    peer = standby_config.peer(identity)[1]
    peer_ip = peer["public_ip"] or None
    answers = None
    if not identity.taking_over:
        answers = probe_proxy(peer.get("proxy_health") or "",
                              settings["peer_probe_timeout_s"])
        if answers is True:
            return decide(identity, ENV_NAME, self_ip, peer_ip, None, answers)[:2]
    allowed, reason, clear = decide(identity, ENV_NAME, self_ip, peer_ip,
                                    lookup(APP_HOST, settings), answers)
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
