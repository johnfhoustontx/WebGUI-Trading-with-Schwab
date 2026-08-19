"""Confirm the stack really came back up, on EVIDENCE rather than an exit code.

Exit **0** when every port this environment owns answers an HTTP health probe,
**1** when one does not — printing which. ``tools/promote.bat`` calls it after the
restart, instead of branching on the launcher's ``errorlevel``.

**Why this exists.** ``promote.bat`` used to judge the restart by whether
``start_all_wt.bat`` returned non-zero, and that was wrong in BOTH directions on
consecutive runs:

  * once the launcher never ran at all (``cmd`` could not resolve a bare ``.bat``
    name, because ``NoDefaultCurrentDirectoryInExePath`` was set in the caller's
    environment) — promote printed nothing alarming while prod sat entirely
    stopped;
  * once the launcher started all eight processes successfully and THEN returned
    non-zero, because a ``timeout``/``pause`` inside it hit "Input redirection is
    not supported" under a non-interactive stdin — promote announced "Prod is now
    STOPPED" over a fully healthy stack.

A batch file's exit code reports whether its last console operation succeeded,
which is not the question. The question is whether the stack is serving, and the
only honest answer to that comes from asking the stack.

**HTTP, not TCP — the difference is load-bearing.** ``check_stack_down`` probes
with ``connect_ex`` because it asks a different question: *is this port occupied?*
Occupancy is exactly what a TCP connect measures. This module asks *is this
service healthy?*, and a process whose accept loop has died stays bound and still
completes a TCP handshake. That precise failure has taken this app's UI down
before: the port answered, the service did not, and every check that looked at the
socket alone reported green.

**The port set comes from ``stop_all._targets()``**, like ``check_stack_down`` — so
the starter, the stopper and this verifier can never disagree about which ports
belong to this environment. There is deliberately no port list here.

**Degradation is the INVERSE of ``check_stack_down``'s, on purpose.** That module
degrades to "proceed", because the cost of a failed check there is a duplicate
process. Here the cost of a wrong answer is announcing a good promote over a dead
trading stack, so anything it cannot confirm reads as NOT up. It retries until a
deadline first, since services legitimately take time to bind.
"""
import pathlib
import sys
import time
import urllib.error
import urllib.request

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
for _path in (str(_REPO_ROOT), str(_TOOLS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from repo_paths import ENV_NAME, REPO_ROOT  # noqa: E402
from stop_all import _targets  # noqa: E402

# The web GUI is a NiceGUI app and exposes no /health; its root answers (with a
# redirect to the landing page, which is itself proof the router is alive).
# Everything else is a FastAPI service carrying the standard endpoint.
HEALTH_PATH = {"webgui": "/"}
DEFAULT_HEALTH_PATH = "/health"

PROBE_TIMEOUT_SEC = 4.0       # one attempt; generous, a loopback GET is instant
DEFAULT_DEADLINE_SEC = 90.0   # whole stack, cold, incl. the proxy's token load
POLL_INTERVAL_SEC = 2.0


def health_url(label, port):
    """The probe URL for one target. PURE."""
    return f"http://127.0.0.1:{port}{HEALTH_PATH.get(label, DEFAULT_HEALTH_PATH)}"


def is_healthy(label, port, timeout=PROBE_TIMEOUT_SEC):
    """True when the service answers HTTP with a non-server-error status.

    Any status below 500 counts: a redirect is a live router, and a 4xx means the
    process is serving and merely disagrees about the path. Only a 5xx, a refused
    connection or a timeout mean "not up". Never raises — an unexpected failure
    reads as NOT healthy, per the module docstring's degradation choice.
    """
    try:
        req = urllib.request.Request(health_url(label, port), method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status) < 500
    except urllib.error.HTTPError as exc:
        return int(getattr(exc, "code", 500)) < 500
    except Exception:  # noqa: BLE001
        return False


def unhealthy_targets(targets=None):
    """The ``(label, port)`` entries not answering. PURE-ish.

    ``targets`` is a parameter so the rule is testable against a server bound in
    the test rather than against the live stack, whose state would otherwise
    decide the result.
    """
    if targets is None:
        targets = _targets()
    down = []
    for entry in targets or ():
        try:
            label, port = entry
        except (TypeError, ValueError):
            continue
        if not is_healthy(label, port):
            down.append((label, port))
    return down


def wait_until_up(targets=None, deadline_sec=DEFAULT_DEADLINE_SEC,
                  interval_sec=POLL_INTERVAL_SEC, sleep=time.sleep,
                  monotonic=time.monotonic):
    """Poll until every target is healthy or the deadline passes.

    Returns the still-unhealthy list — empty means the stack is up. ``sleep`` and
    ``monotonic`` are injected so a test can drive the deadline without waiting on
    a real clock.
    """
    if targets is None:
        targets = _targets()
    started = monotonic()
    while True:
        down = unhealthy_targets(targets)
        if not down:
            return []
        if monotonic() - started >= deadline_sec:
            return down
        sleep(interval_sec)


def failure_text(down, env_name=None, root=None):
    """The operator-facing failure. PURE.

    Names the environment and the checkout because two stacks share this machine,
    and lists the ports rather than saying "the stack" — after a promote the
    operator needs to know whether one service failed to bind or nothing came back
    at all, and those have different next steps.
    """
    env = (env_name or ENV_NAME or "?").upper()
    lines = [
        f"The {env} stack did NOT come back up after the restart.",
        f"  checkout: {root or REPO_ROOT}",
        "",
        "Not answering:",
    ]
    lines += [f"  {label} (:{port})  {health_url(label, port)}"
              for label, port in down]
    lines += [
        "",
        "The code IS promoted - only the restart failed, so re-running promote is",
        "not what you want. Bring the stack back:",
        "",
        "  stop_all.bat        (clear anything half-started)",
        "  start_all_wt.bat    (or  start_all_wt.bat nowindow)",
        "",
        "Then read the launcher's logs for a bind error.",
    ]
    return "\n".join(lines)


def _deadline_arg(argv):
    """The ``--timeout SECONDS`` argument, or the default. PURE."""
    argv = list(argv or ())
    if "--timeout" in argv:
        i = argv.index("--timeout")
        if i + 1 < len(argv):
            try:
                return float(argv[i + 1])
            except (TypeError, ValueError):
                pass
    return DEFAULT_DEADLINE_SEC


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    deadline = _deadline_arg(argv)
    try:
        targets = _targets()
    except Exception as exc:  # noqa: BLE001
        # Cannot even name the ports. Unlike check_stack_down this does NOT
        # degrade to success: an unverifiable promote must not be announced as a
        # good one.
        print(f"  ! could not determine which ports to check: {exc}")
        print("  ! treating the restart as UNCONFIRMED.")
        return 1
    print(f"Confirming the stack is up ({len(targets)} ports, "
          f"up to {deadline:.0f}s)...")
    down = wait_until_up(targets, deadline_sec=deadline)
    if not down:
        print(f"  all {len(targets)} ports answering.")
        return 0
    print()
    print(failure_text(down))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
