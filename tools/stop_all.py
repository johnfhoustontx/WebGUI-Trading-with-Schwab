"""Stop every Schwab-Trading process this repo starts, by listening port.

Reads the ports from ``repo_paths`` (single source of truth) and terminates
whatever process is LISTENING on each — the proxy, the five domain services, and
the web GUI. **Memurai (:6379) is intentionally left alone**: it's a shared
Windows service, not something this repo launches.

The web GUI (NICEGUI_PORT) is killed **last** so that when this script is spawned
from the GUI's own "Terminate" button, it finishes killing the proxy + services
before it pulls the rug out from under the page that launched it.

Windows-first: discovers PIDs via ``netstat -ano`` and kills via ``taskkill``.
Best-effort and chatty — it prints what it finds/kills and never hard-fails on a
single port.
"""
import pathlib
import subprocess
import sys

# Repo root on sys.path so ``repo_paths`` imports cleanly when run directly.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import NICEGUI_PORT, PROXY_PORT, SERVICE_PORTS  # noqa: E402


def _targets():
    """Ordered (label, port) list — proxy + services first, web GUI LAST."""
    targets = [("proxy", PROXY_PORT)]
    targets += [(f"{name}_svc", port) for name, port in SERVICE_PORTS.items()]
    targets.append(("webgui", NICEGUI_PORT))
    return targets


def _listening_pids(port):
    """PIDs LISTENING on ``port`` (TCP), parsed from ``netstat -ano``."""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        print(f"  ! netstat failed: {exc}")
        return set()
    pids = set()
    needle = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        # e.g. ['TCP', '127.0.0.1:8100', '0.0.0.0:0', 'LISTENING', '12345']
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" \
                and parts[1].endswith(needle):
            pid = parts[-1]
            if pid.isdigit() and pid not in ("0", "4"):
                pids.add(pid)
    return pids


def _kill(pid):
    """taskkill /F /T a single PID; return True on success."""
    try:
        res = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", pid],
            capture_output=True, text=True, timeout=15,
        )
        return res.returncode == 0
    except Exception as exc:  # noqa: BLE001
        print(f"    ! taskkill {pid} failed: {exc}")
        return False


def main():
    print("Stopping Schwab-Trading services (Memurai :6379 is left running)...")
    killed = 0
    for label, port in _targets():
        pids = _listening_pids(port)
        if not pids:
            print(f"  - {label} (:{port}): not running")
            continue
        for pid in pids:
            ok = _kill(pid)
            print(f"  {'x' if ok else '!'} {label} (:{port}): "
                  f"{'killed' if ok else 'FAILED to kill'} PID {pid}")
            killed += int(ok)
    print(f"Done — {killed} process(es) terminated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
