"""Stop every Schwab-Trading process this repo starts.

Reads the ports from ``repo_paths`` (single source of truth) and terminates
whatever process is LISTENING on each — the proxy, the six domain services, and
the web GUI. **Memurai (:6379) is intentionally left alone**: it's a shared
Windows service, not something this repo launches.

**The proxy is only a target when this environment owns it.** Dev runs no proxy
of its own — there is one rotating Schwab OAuth refresh token, so there can be
only one — and borrows prod's on :8100. That means a dev checkout's PROXY_PORT
*is* prod's, and killing it from here (or from dev's "Terminate" button) would
take the live stack's market data down mid-session. ``OWNS_PROXY`` is False in
dev, and the proxy simply drops out of the list.

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

A port also *attributes* a process: with two checkouts on one machine, dev's
services answer on 9210-9215 and prod's on 8210-8214, so a port-based kill can
never cross over. The HUD has no such tell, which is why its match is scoped to
this checkout's root as well.

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

from repo_paths import (  # noqa: E402
    NICEGUI_PORT, OWNS_PROXY, PROXY_PORT, REPO_ROOT, SERVICE_PORTS,
)


def _targets():
    """Ordered (label, port) list — proxy + services first, web GUI LAST.

    The proxy is included ONLY when this environment owns it. In dev PROXY_PORT
    is 8100 — PROD'S proxy, borrowed — so listing it here would have dev's
    Terminate button kill the live stack's market data.

    Everything else is unconditional: dev owns its own six services and its own
    web GUI, on their own offset ports, and must still stop them.
    """
    targets = [("proxy", PROXY_PORT)] if OWNS_PROXY else []
    targets += [(f"{name}_svc", port) for name, port in SERVICE_PORTS.items()]
    targets.append(("webgui", NICEGUI_PORT))
    return targets


# The HUD's script, as it appears on the command line. Matched on the full
# filename, never a prefix: nq_signal.py, nq_signal_log.py and nq_state.py sit
# beside it in tools/ and are imported BY it, so anything looser would sweep up
# siblings.
HUD_SCRIPT = "nq_hud.py"


def _norm_path(text):
    """Lowercase a path-ish string and settle on one separator. Windows-first."""
    return str(text).lower().replace("/", "\\")


def _is_hud(name, cmdline, root=None):
    """True when a process row is the Dealer-Positioning HUD.

    THREE conditions now, and every one is load-bearing:

    * the command line runs HUD_SCRIPT. This is what protects the stack. Every
      service also runs under pythonw.exe, so keying on the interpreter would
      kill the proxy, all six services and the web GUI — which is exactly what
      happened once, costing ~90 minutes of a live session.
    * the image is a python interpreter. This is what protects your terminal.
      Command line alone also matches the shell you typed the script name into,
      the editor with the file open, and the PowerShell running the query. The
      launcher's duplicate guard hit this and measured 6 matches where only 2
      were real; here the consequence would be killing them.
    * when ``root`` is given, the command line names THIS checkout. This is what
      protects the OTHER environment. Every other target is attributable by
      port; the HUD binds none, so with two checkouts on one machine an unscoped
      match has dev's stop_all killing prod's HUD. ``root=None`` keeps the
      pre-environment behavior, so existing two-argument callers are unchanged.

    The root needle carries a TRAILING SEPARATOR, because a plain substring test
    is wrong in the case that matters: "D:\\Trading" is a prefix of
    "D:\\Trading Prod", so an unanchored match would let a dev checkout claim the
    sibling it was scoped away from — the whole hazard, reintroduced.

    A command line naming NO checkout (``pythonw tools\\nq_hud.py``, launched by
    hand from an activated venv) is deliberately read as NOT ours: it cannot be
    attributed, and leaving a stale HUD window open is a far smaller cost than
    killing the live stack's. The launcher does not produce that shape —
    start_all_hidden.bat runs ``%~dp0.venv\\Scripts\\pythonw.exe``, an absolute
    path that names the checkout, and ``taskkill /T`` sweeps up the venv
    redirector's child, whose own command line carries only the base interpreter.
    """
    if not name or not cmdline:
        return False
    if not str(name).lower().startswith("python"):
        return False
    cl = _norm_path(cmdline)
    if HUD_SCRIPT not in cl:
        return False
    if root is not None and _norm_path(root).rstrip("\\") + "\\" not in cl:
        return False
    return True


def hud_root_pids(rows, own_pid=None, root=None):
    """Root PIDs of running HUDs, from ``(pid, ppid, name, cmdline)`` rows. PURE.

    ``root`` scopes the match to one checkout — see ``_is_hud``. It defaults to
    None (unscoped) so the rule stays testable in isolation; the real call site
    in ``stop_hud`` passes this repo's root.

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
        if pid is None or not _is_hud(name, cmdline, root=root):
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
    """Terminate THIS checkout's Dealer-Positioning HUD. Returns the number killed.

    Scoped to ``REPO_ROOT``: the HUD is the one target with no port to attribute
    it, so without this a dev shutdown would reach prod's HUD.
    """
    pids = hud_root_pids(_process_rows(), own_pid=os.getpid(), root=REPO_ROOT)
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
