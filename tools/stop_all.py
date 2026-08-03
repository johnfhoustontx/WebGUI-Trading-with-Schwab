"""Stop every Schwab-Trading process this repo starts.

Reads the ports from ``repo_paths`` (single source of truth) and terminates
whatever process is LISTENING on each — the proxy, the six domain services, and
the web GUI. **Memurai (:6379) is intentionally left alone**: it's a shared
Windows service, not something this repo launches.

The web GUI (NICEGUI_PORT) is killed **last** so that when this script is spawned
from the GUI's own "Terminate" button, it finishes killing the proxy + services
before it pulls the rug out from under the page that launched it.

Plus ONE process that has no port: the Dealer-Positioning HUD
(``tools/nq_hud.py``), started by ``start_all_hidden.bat``. It is killed FIRST,
so it stops polling a stack that is being torn down underneath it.

WHY THE HUD NEEDS ITS OWN CAREFUL MATCH. Every other target is found by
listening port, which is self-limiting — a port identifies exactly one process.
The HUD binds none, so it must be found by command line, and that is the
dangerous kind of match: the proxy, all six services and the web GUI ALSO run
under ``pythonw.exe``. Matching the interpreter instead of the script is not
hypothetical — ``taskkill /F /IM pythonw.exe`` was used to restart the HUD once
and took the whole stack down mid-session. See ``hud_root_pids``.

Windows-first: discovers PIDs via ``netstat -ano`` (ports) and PowerShell CIM
(command lines), and kills via ``taskkill``. Best-effort and chatty — it prints
what it finds/kills and never hard-fails on a single target.
"""
import csv
import os
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


# The HUD's script, as it appears on the command line. Matched on the full
# filename, never a prefix: nq_signal.py, nq_signal_log.py and nq_state.py sit
# beside it in tools/ and are imported BY it, so anything looser would sweep up
# siblings.
HUD_SCRIPT = "nq_hud.py"


def _is_hud(name, cmdline):
    """True when a process row is the Dealer-Positioning HUD.

    TWO conditions, and BOTH are load-bearing:

    * the command line runs HUD_SCRIPT. This is what protects the stack. Every
      service also runs under pythonw.exe, so keying on the interpreter would
      kill the proxy, all six services and the web GUI — which is exactly what
      happened once, costing ~90 minutes of a live session.
    * the image is a python interpreter. This is what protects your terminal.
      Command line alone also matches the shell you typed the script name into,
      the editor with the file open, and the PowerShell running the query. The
      launcher's duplicate guard hit this and measured 6 matches where only 2
      were real; here the consequence would be killing them.
    """
    if not name or not cmdline:
        return False
    if not str(name).lower().startswith("python"):
        return False
    return HUD_SCRIPT in str(cmdline).lower()


def hud_root_pids(rows, own_pid=None):
    """Root PIDs of running HUDs, from ``(pid, ppid, name, cmdline)`` rows. PURE.

    ROOTS ONLY, because one HUD is a PARENT/CHILD PAIR: this repo's venv
    ``pythonw.exe`` is a redirector that re-executes the base interpreter. Since
    ``taskkill /T`` kills the tree, returning both would have the second call
    report "not found" and read as a failure.

    Kept pure and separate from the process query so the matching rule — the
    part that can take the stack down if it is wrong — is unit-testable without
    spawning anything.
    """
    matched = {}
    for row in rows or ():
        try:
            pid, ppid, name, cmdline = row
        except (TypeError, ValueError):
            continue
        if pid is None or not _is_hud(name, cmdline):
            continue
        if own_pid is not None and pid == own_pid:
            continue
        matched[pid] = ppid
    # A child whose parent is also a match is killed by the parent's /T sweep.
    return sorted(p for p, ppid in matched.items() if ppid not in matched)


def _process_rows():
    """(pid, ppid, name, cmdline) for every process. Windows/PowerShell.

    CSV rather than a formatted table: a command line contains spaces, quotes
    and commas, and ConvertTo-Csv quotes them properly where a fixed-width table
    would truncate. Returns [] on any failure — an unreadable process list must
    not abort the port-based shutdown that follows.
    """
    script = ("Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
              "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
              "ConvertTo-Csv -NoTypeInformation")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not list processes: {exc}")
        return []

    rows = []
    for rec in csv.DictReader(out.splitlines()):
        try:
            rows.append((int(rec["ProcessId"]), int(rec["ParentProcessId"]),
                         rec["Name"], rec["CommandLine"]))
        except (TypeError, ValueError, KeyError):
            # A process that exits mid-query can leave a blank field.
            continue
    return rows


def stop_hud():
    """Terminate the Dealer-Positioning HUD. Returns the number killed."""
    pids = hud_root_pids(_process_rows(), own_pid=os.getpid())
    if not pids:
        print("  - dealer HUD (no port): not running")
        return 0
    killed = 0
    for pid in pids:
        ok = _kill(str(pid))
        print(f"  {'x' if ok else '!'} dealer HUD (no port): "
              f"{'killed' if ok else 'FAILED to kill'} PID {pid}")
        killed += int(ok)
    return killed


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
    # The HUD first: it polls Redis and the GEX history every 2s, so stopping it
    # before its sources means it never sees the stack disappear underneath it.
    killed = stop_hud()
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
