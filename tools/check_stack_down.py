"""Refuse to start a stack that is already running.

Exit **0** when every port this environment owns is free, **1** when something is
already listening — printing which. The four launchers call it before they start
anything, so a second launch says so instead of producing a handful of
short-lived duplicates that each do a full startup (real Schwab API calls) before
failing to bind and exiting. That has happened twice.

**The port set comes from ``stop_all._targets()``, deliberately.** That function
already knows the two things this check must not get wrong: it drops the proxy
when ``OWNS_PROXY`` is false (dev borrows prod's on :8100, so a dev checkout must
neither stop nor claim it) and it orders the web GUI last. Importing it means the
STARTER and the STOPPER can never disagree about which ports belong to this
environment — and a launcher that thinks it owns a port the stopper will not stop
is precisely the drift that produced the incident this guard exists for. There is
no port list in this file; adding one would reintroduce it.

``tools/`` has no ``__init__.py``. Run as a script, this file's own directory is
``sys.path[0]`` and ``from stop_all import ...`` resolves; imported as
``tools.check_stack_down`` (pytest) it does not, so both the repo root — needed
for ``repo_paths`` — and this directory are put on the path explicitly.

Defensive throughout: a failure to probe must not stand between the operator and
a start. The pre-guard behavior was "launch anyway", the cost of which is a
duplicate process, so an unreadable answer degrades to exit 0 rather than
refusing on evidence it does not have.
"""
import pathlib
import socket
import sys

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
for _path in (str(_REPO_ROOT), str(_TOOLS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from repo_paths import ENV_NAME, REPO_ROOT  # noqa: E402
from stop_all import _targets  # noqa: E402

# Short, because this runs on the path to a start the operator is waiting on and
# every target is on the loopback: a listener answers in microseconds and a free
# port is refused immediately. The timeout only bounds the pathological case
# (a firewall silently dropping a loopback SYN), where waiting longer buys
# nothing anyway.
PROBE_TIMEOUT_SEC = 0.35


def is_listening(port, timeout=PROBE_TIMEOUT_SEC):
    """True when something accepts a TCP connection on ``127.0.0.1:port``.

    ``connect_ex`` rather than ``connect``: it reports the error as a return
    value instead of raising, which is what "never raise" wants here. Anything
    unexpected — a non-numeric port, a socket the OS refuses to hand out — reads
    as NOT listening, so the launcher proceeds. That matches the degradation
    choice in the module docstring: this check may cost a duplicate process, it
    may never cost a start.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:  # noqa: BLE001
        return False


def busy_targets(targets=None):
    """The ``(label, port)`` entries that are already listening. PURE-ish.

    ``targets`` defaults to ``stop_all._targets()``. It is a parameter so the
    rule is testable against a socket bound in the test rather than against the
    live stack's real ports — probing those from a test would make the result
    depend on whether prod happened to be up.
    """
    if targets is None:
        targets = _targets()
    busy = []
    for entry in targets or ():
        try:
            label, port = entry
        except (TypeError, ValueError):
            continue
        if is_listening(port):
            busy.append((label, port))
    return busy


def refusal_text(busy, env_name=None, root=None):
    """The operator-facing refusal. PURE.

    Names the ENVIRONMENT and the CHECKOUT, both on purpose: with two stacks on
    one machine "already running" is ambiguous, and the answer to it differs —
    the dev stack is yours to stop, the prod one is serving live market data and
    stopping it is a decision, not a step.
    """
    env = (env_name or ENV_NAME or "?").upper()
    lines = [
        f"The {env} stack is already running - refusing to start a second copy.",
        f"  checkout: {root or REPO_ROOT}",
        "",
        "Already listening:",
    ]
    lines += [f"  {label} (:{port})" for label, port in busy]
    lines += [
        "",
        "Starting again would spawn duplicates that each do a full startup -",
        "including real Schwab API calls - before failing to bind and exiting.",
        "",
        "Stop the stack first:  stop_all.bat",
        "  (or the web GUI's More > Terminate page)",
    ]
    return "\n".join(lines)


def main():
    try:
        busy = busy_targets()
    except Exception as exc:  # noqa: BLE001
        # Degrade to the pre-guard behavior. See the module docstring: refusing
        # on a check we could not run would be the more expensive mistake.
        print(f"  ! could not check whether a stack is already running: {exc}")
        print("  ! continuing anyway.")
        return 0
    if not busy:
        return 0
    print(refusal_text(busy))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
