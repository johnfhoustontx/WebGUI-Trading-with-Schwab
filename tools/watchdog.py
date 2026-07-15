"""Unattended process watchdog for the local stack.

Probes every tier's health on an interval and restarts a DEAD process via the same
windowless ``tools/restart_one.bat`` the System Status page uses. This closes the gap the
in-process scheduler-restart + alerting could not: a whole PROCESS that dies (crash, OOM,
manual kill) stays dead until you notice — for a stack meant to run all day, unattended.

**OPT-IN — run it yourself** (``python tools/watchdog.py``); it is deliberately NOT started
by ``start_all``. Every restart is **storm-capped** (``MAX_RESTARTS`` per component within
``STORM_WINDOW_SEC``) so a crash-looping component is restarted a few times and then LEFT
DOWN + logged, rather than thrashed forever. Memurai is only *started* if stopped (never
force-restarted — it is a shared Windows service).

Components probed: Memurai (Redis PING), schwab-proxy ``/health``, the six domain services
``/health``, and the webgui (TCP connect to :8500). CORS/secret hardening is irrelevant here
— ``/health`` is unguarded.

Usage:
    python tools/watchdog.py                 # 30s interval, restarts on
    python tools/watchdog.py --interval 60   # custom interval (seconds)
    python tools/watchdog.py --dry-run       # probe + log only, never restart
    python tools/watchdog.py --once          # one sweep then exit (for cron/testing)
"""
import argparse
import logging
import pathlib
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import (MEMURAI_URL, NICEGUI_PORT, PROXY_PORT, PROXY_URL,
                        SERVICE_PORTS, SERVICE_URLS)

log = logging.getLogger("watchdog")

INTERVAL_SEC = 30
MAX_RESTARTS = 3          # per component …
STORM_WINDOW_SEC = 600    # … within this rolling window → then give up + log
HEALTH_TIMEOUT_SEC = 3
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── PURE helpers (unit-testable, no I/O) ─────────────────────────────────────
def under_storm_cap(timestamps, now, *, max_restarts=MAX_RESTARTS,
                    window_sec=STORM_WINDOW_SEC) -> bool:
    """True if a restart is still allowed given prior restart ``timestamps``.

    Counts only restarts within the last ``window_sec``; allows the next one iff fewer
    than ``max_restarts`` are in that window. Pure — pass ``now`` and the history in."""
    recent = [t for t in (timestamps or []) if now - t < window_sec]
    return len(recent) < max_restarts


def component_targets() -> list:
    """The components to probe, in dependency order (Memurai → proxy → services → webgui).

    Each: ``{name, kind, url|port, restart}`` where ``restart`` is the argv to spawn (or
    None for none). Reuses ``repo_paths`` so ports never drift. Mirrors the Status page's
    ``restart_spec`` mapping to ``tools/restart_one.bat``."""
    def _bat(kill_port, wait_port, name, script):
        return ["cmd", "/c", r"tools\restart_one.bat", str(kill_port), str(wait_port),
                name, script]

    targets = [
        {"name": "memurai", "kind": "redis", "url": MEMURAI_URL,
         "restart": ["powershell", "-NoProfile", "-Command",
                     "try { Get-Service -Name 'Memurai' | Where-Object {$_.Status -ne "
                     "'Running'} | Start-Service } catch {}"]},
        {"name": "proxy", "kind": "http", "url": f"{PROXY_URL}/health",
         "restart": _bat(PROXY_PORT, 0, "proxy", r"schwab-proxy\schwab_proxy.py")},
    ]
    for key, port in SERVICE_PORTS.items():
        url = SERVICE_URLS.get(key, f"http://127.0.0.1:{port}")
        targets.append({"name": f"{key}_svc", "kind": "http", "url": f"{url}/health",
                        "restart": _bat(port, PROXY_PORT, f"{key}_svc",
                                        rf"services\{key}_svc\app.py")})
    targets.append({"name": "webgui", "kind": "tcp", "port": NICEGUI_PORT,
                    "restart": _bat(NICEGUI_PORT, 0, "webgui", r"webgui\main.py")})
    return targets


# ── probes (I/O) ─────────────────────────────────────────────────────────────
def _http_ok(url) -> bool:
    try:
        with urlopen(url, timeout=HEALTH_TIMEOUT_SEC) as r:  # noqa: S310 — localhost only
            return 200 <= r.status < 300
    except Exception:
        return False


def _tcp_ok(port) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=HEALTH_TIMEOUT_SEC):
            return True
    except Exception:
        return False


def _redis_ok(url) -> bool:
    try:
        import redis
        return bool(redis.Redis.from_url(url, socket_timeout=HEALTH_TIMEOUT_SEC).ping())
    except Exception:
        return False


def probe(target) -> bool:
    """True if the component is healthy."""
    kind = target["kind"]
    if kind == "http":
        return _http_ok(target["url"])
    if kind == "tcp":
        return _tcp_ok(target["port"])
    if kind == "redis":
        return _redis_ok(target["url"])
    return False


def _restart(target) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    subprocess.Popen(target["restart"], cwd=str(repo_root), creationflags=_NO_WINDOW,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def sweep(targets, history, *, now, dry_run=False) -> None:
    """One probe-and-maybe-restart pass. Mutates ``history`` (name → [restart ts])."""
    for t in targets:
        name = t["name"]
        if probe(t):
            continue
        if not t.get("restart"):
            log.warning("%s DOWN — no restart configured", name)
            continue
        if not under_storm_cap(history.get(name, []), now):
            log.error("%s DOWN — storm cap hit (%d restarts / %ds); leaving down",
                      name, MAX_RESTARTS, STORM_WINDOW_SEC)
            continue
        if dry_run:
            log.warning("%s DOWN — would restart (dry-run)", name)
            continue
        log.warning("%s DOWN — restarting", name)
        _restart(t)
        history.setdefault(name, []).append(now)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Local stack process watchdog.")
    ap.add_argument("--interval", type=float, default=INTERVAL_SEC)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    targets = component_targets()
    history: dict = {}
    log.info("watchdog started — %d components, interval %ss%s",
             len(targets), args.interval, " (dry-run)" if args.dry_run else "")
    while True:
        try:
            sweep(targets, history, now=time.monotonic(), dry_run=args.dry_run)
        except Exception:
            log.exception("watchdog sweep error (continuing)")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
