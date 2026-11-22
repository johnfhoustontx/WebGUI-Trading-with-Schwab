#!/usr/bin/env python
"""What the operator runs by hand on the warm standby's two boxes.

    standby_failover.py takeover [--peer-is-down]   turn THIS standby into prod
    standby_failover.py drill                       boot the restored stack as dev, then undo it
    standby_failover.py become-standby              turn a returned old prod into the standby
    standby_failover.py fence-neighbours            put the start fence on the neighbour apps

The roles are symmetric: every name, address and probe comes from
``config/standby.toml`` and the box identity (``~/.config/neuralstrike/box.toml``),
never a literal box name. Nothing here needs passwordless sudo: the root steps
are printed for the operator. Design: docs/plans/2026-11-22-warm-standby-design.md,
sections 4 to 6.

``takeover`` writes the identity as ``prod`` BEFORE anything starts, because the
start fence reads it. A step that fails after that point stops the run, says what
was done and what was not, and exits 1; the identity is never rolled back silently.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import repo_paths  # noqa: E402
from deploy.systemd import generate_units as gu  # noqa: E402
from deploy.systemd import standby_units  # noqa: E402
from shared import standby_config  # noqa: E402
from shared.standby_config import Identity  # noqa: E402
from tools import standby_fence, standby_sync  # noqa: E402
from tools import wait_http  # noqa: E402

CURL_TIMEOUT_S = 8
SSH_PORT = 22
SSH_TIMEOUT_S = 5
HEALTH_WAIT_S = 90
PREDEMOTE_DIR = "backups/predemote"
LOOKUP_TIMEOUT_S = 5.0
FENCE_CONF = "fence.conf"
UNIT_SUFFIXES = (".service", ".timer")
NOT_UNITS = ("venv-freeze.txt", "venv-pins.txt")


def _unit_dir(home: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(home) / ".config" / "systemd" / "user"


def _runner(run):
    """Adapt ``run`` (a ``subprocess.run`` look-alike) to ``(ok, output)``."""
    def call(cmd):
        try:
            p = run(list(cmd), capture_output=True, text=True, timeout=300)
        except Exception as exc:  # noqa: BLE001 - reported to the operator by the caller
            return False, f"{type(exc).__name__}: {exc}"
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    return call


def _ctl(run, *args):
    return _runner(run)(["systemctl", "--user", *args])


# --- the peer's probes ----------------------------------------------------------

def probe_peer_detail(peer: dict, app_host: str, run=subprocess.run,
                      connect=socket.create_connection):
    """``(answered, skipped)``: the probes that answered, and ``(name, why)`` for
    every probe the config had no address for. A skipped probe is NOT a silent peer."""
    answered, skipped = [], []
    health, ip = peer.get("proxy_health") or "", peer.get("public_ip") or ""

    def curl(argv):
        try:
            return run(argv, capture_output=True, text=True,
                       timeout=CURL_TIMEOUT_S + 5).returncode == 0
        except Exception:  # noqa: BLE001 - an unreachable peer is the expected answer
            return False

    if not health:
        skipped.append(("tailnet proxy", "the peer has no proxy_health in standby.toml"))
    elif curl(["curl", "-fsS", "--max-time", str(CURL_TIMEOUT_S), health]):
        answered.append("tailnet proxy")
    if not ip:
        why = "the peer has no public_ip in standby.toml"
        skipped += [("public app", why), ("public ssh", why)]
        return answered, skipped
    # The app host is sent to the peer's address, never looked up: the probe then
    # hits the peer whichever way DNS points. Any HTTP status is an answer.
    if curl(["curl", "-sS", "-o", "/dev/null", "--max-time", str(CURL_TIMEOUT_S),
             "--resolve", f"{app_host}:443:{ip}", f"https://{app_host}/login"]):
        answered.append("public app")
    try:
        sock = connect((ip, SSH_PORT), SSH_TIMEOUT_S)
    except OSError:
        sock = None
    if sock is not None:
        try:
            sock.close()
        except Exception:  # noqa: BLE001 - it answered; closing is housekeeping
            pass
        answered.append("public ssh")
    return answered, skipped


def probe_peer(peer: dict, app_host: str, run=subprocess.run,
               connect=socket.create_connection) -> list:
    """Names of the probes that answered (``tailnet proxy``, ``public app``, ``public ssh``)."""
    return probe_peer_detail(peer, app_host, run, connect)[0]


# --- small file helpers ---------------------------------------------------------

def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_toml_key(text: str, key: str, value: str) -> str:
    """``text`` with the top-level ``key = "value"`` set, other lines untouched.
    A commented-out line is not the key; a missing key is appended."""
    line = f'{key} = "{value}"'
    pat = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    if pat.search(text):
        return pat.sub(lambda _m: line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def _set_env_key(env_path: pathlib.Path, key: str, value: str) -> None:
    old = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    _atomic_write(env_path, set_toml_key(old, key, value))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


# --- the fence on the neighbours --------------------------------------------------

def _fence_text() -> str:
    name = gu.fence_name()
    return f"[Unit]\nRequires={name}\nAfter={name}\n"


def fence_neighbours(specs, home, run=subprocess.run) -> list:
    """Write the fence drop-in for every neighbour ``.service`` file (a template
    included: its drop-in covers all its instances) present in the systemd user
    folder, then ``daemon-reload``. Timers and existing ``.d`` folders get none.
    Returns the drop-in paths. Writing it twice changes nothing."""
    unit_dir = _unit_dir(home)
    written = []
    if unit_dir.is_dir():
        for spec in specs:
            for pattern in spec.get("units", []):
                for path in sorted(unit_dir.glob("*")):
                    if (not fnmatch.fnmatch(path.name, pattern) or not path.is_file()
                            or not path.name.endswith(".service")):
                        continue
                    conf = unit_dir / (path.name + ".d") / FENCE_CONF
                    conf.parent.mkdir(parents=True, exist_ok=True)
                    conf.write_text(_fence_text(), encoding="utf-8")
                    if conf not in written:
                        written.append(conf)
    _ctl(run, "daemon-reload")
    return written


def _install_neighbour_units(specs, home) -> tuple:
    """Copy each neighbour's kept units (and drop-in folders) from
    ``~/standby/units/<name>/`` into the systemd user folder. Returns
    ``(installed unit names, [(spec, kept folder)] with a kept folder)``."""
    unit_dir = _unit_dir(home)
    installed, kept_specs = [], []
    for spec in specs:
        kept = pathlib.Path(home) / "standby" / "units" / spec["name"]
        if not kept.is_dir():
            continue
        kept_specs.append((spec, kept))
        for entry in sorted(kept.iterdir()):
            if entry.name in NOT_UNITS:
                continue
            if entry.is_dir() and re.search(r"\.(service|timer)\.d$", entry.name):
                shutil.copytree(entry, unit_dir / entry.name, dirs_exist_ok=True)
            elif entry.is_file() and entry.name.endswith(UNIT_SUFFIXES):
                unit_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, unit_dir / entry.name)
                installed.append(entry.name)
    return installed, kept_specs


# --- takeover -----------------------------------------------------------------------

def _data_report(home, settings, now, out) -> None:
    """Print the newest synced generation, its age and the last sync status."""
    gens = [p for p in standby_sync._finished(pathlib.Path(settings["generations"]))
            if (p / standby_sync.SYNCED).is_file()]
    status = standby_sync._read_status(home)
    limit = settings["max_age_h"]
    out("DATA ON THIS BOX")
    if not gens:
        out("  !! NO synced generation here: the standby holds no copy of prod's data.")
    else:
        newest = gens[-1].name
        age = standby_sync.generation_age_h(newest, now)
        loud = "  !! OLDER than the limit" if age > limit else ""
        out(f"  newest synced generation: {newest}, {age:.1f} h old "
            f"(limit {limit} h){loud}")
    result, detail = status.get("result"), status.get("detail") or ""
    out(f"  last sync result: {result or 'none recorded'}"
        + (f" - {detail}" if detail else ""))
    if result not in ("synced", "unchanged"):
        out("  !! the last sync did not finish cleanly; check the data before trusting it.")


def _steps_report(done, not_done, out):
    out("\nDONE:")
    for s in done:
        out(f"  - {s}")
    out("NOT DONE:")
    for s in not_done:
        out(f"  - {s}")


def _operator_steps(box: dict, out) -> None:
    ip = box.get("public_ip") or "<this box's public address>"
    py = ".venv/bin/python"
    out("\nSTEPS ONLY YOU CAN DO, in order:")
    out("  1. sudo ufw allow 80,443/tcp")
    out("  2. sudo tailscale serve --bg --https=8100 http://127.0.0.1:8100")
    out("     (the peer's probes and the Authorize link reach this box's proxy through it)")
    out(f"  3. sudo {py} -m deploy.caddy.generate_caddyfile --install, then enable and start Caddy")
    out(f"  4. At Wix, point these four A records at {ip}:")
    for label, host in (("@", repo_paths.SITE_HOST), ("app", repo_paths.APP_HOST),
                        ("live", repo_paths.LIVE_HOST), ("cos", repo_paths.COS_HOST)):
        out(f"       {label:<5} {host} -> {ip}")
    out("  5. Sign in to Schwab again from Status -> Authorize if the restored sign-in")
    out("     is past its 7 days.")
    out("  6. Sign Claude Code in (run `claude`, then /login); the market reports need it.")
    out("  7. Re-point the two workstation tasks (the backup pull and the social cards")
    out("     pull) from the old prod to this box.")
    out("  8. When the old prod comes back, run `become-standby` on it BEFORE it starts anything.")


def takeover(identity, *, peer_is_down=False, home=None, repo=None, run=subprocess.run,
             connect=socket.create_connection, wait=None, identity_path=None,
             settings=None, specs=None, now=None, app_host=None, out=print) -> int:
    """Turn this standby into prod. 0 on success, 1 on any refusal or failure."""
    home = pathlib.Path(home or pathlib.Path.home())
    repo = pathlib.Path(repo or ROOT)
    wait = wait or (lambda url, timeout: wait_http.wait_for(url, timeout=timeout))
    app_host = app_host or repo_paths.APP_HOST
    # 1. refusals: nothing has changed until all of them pass.
    if identity is None:
        out("REFUSED: this box has no identity file; it is not a configured standby.")
        return 1
    if identity.role == "prod":
        out(f"REFUSED: {identity.self_name} is already prod; there is nothing to take over.")
        return 1
    if repo_paths.ENV_NAME != "prod":
        out(f"REFUSED: this checkout is the {repo_paths.ENV_NAME!r} profile; a takeover "
            "installs the prod stack. Remove config/env.local.toml's name = ... first.")
        return 1
    try:
        peer_name, peer = standby_config.peer(identity)
    except ValueError as exc:
        out(f"REFUSED: {exc}")
        return 1
    box = standby_config.boxes()[identity.self_name]
    answered, skipped = probe_peer_detail(peer, app_host, run, connect)
    for name, why in skipped:
        out(f"  probe skipped - {name}: {why}")
    if answered and not peer_is_down:
        out(f"REFUSED: {peer_name} still answers ({', '.join(answered)}). "
            "Two stacks must never run at once. If it is half-alive and cannot be "
            "stopped, decide that and pass --peer-is-down.")
        return 1
    if not answered and len(skipped) == 3 and not peer_is_down:
        out(f"REFUSED: no probe of {peer_name} could run (standby.toml lacks its addresses), "
            "so nothing shows it is down. Fix the config, or pass --peer-is-down.")
        return 1
    if peer_is_down and answered:
        out(f"  --peer-is-down given although {', '.join(answered)} answered; going on.")

    settings = settings or standby_config.sync_settings()
    specs = standby_config.neighbours() if specs is None else specs
    _data_report(home, settings, now or dt.datetime.now(), out)

    done, unit_dir = [], _unit_dir(home)
    env_path = repo / "config" / "env.local.toml"

    def fail(what, not_done):
        out(f"\nFAILED: {what}")
        _steps_report(done, not_done, out)
        out("The identity says prod and was NOT rolled back. Fix the cause, then finish "
            "the NOT DONE steps by hand (or set role = \"standby\" in the identity file "
            "and run `takeover` again).")
        return 1

    # 2. from here on something has changed.
    removed = standby_units.uninstall(dest=unit_dir, runner=_runner(run))
    done.append(f"removed the standby sync units ({len(removed)} files)")
    standby_config.write_identity(Identity(identity.self_name, "prod", True), identity_path)
    done.append("identity written: prod, taking_over = true")
    later = ["proxy_public_url", "neighbour units", "stack units and timers",
             "start and probe"]
    origin = _origin(box.get("proxy_health", ""))
    try:
        if origin:
            _set_env_key(env_path, "proxy_public_url", origin)
            done.append(f"proxy_public_url = {origin} in {env_path.name}")
        else:
            out("  note: this box has no proxy_health in standby.toml; "
                "proxy_public_url left as it was.")
        later.remove("proxy_public_url")

        installed, kept_specs = _install_neighbour_units(specs, home)
        fence_neighbours(specs, home, run)
        for spec, kept in kept_specs:
            freeze = kept / "venv-freeze.txt"
            if not freeze.is_file():
                continue
            if standby_sync.editable_self(freeze.read_text(encoding="utf-8"), home, spec):
                folder = home / spec["dir"]
                ok, text = _runner(run)([
                    str(home / ".local" / "bin" / "uv"), "pip", "install", "-e",
                    str(folder), "--python", str(folder / ".venv" / "bin" / "python")])
                if not ok:
                    return fail(f"the editable install of {spec['name']}: {text[:200]}", later)
                done.append(f"installed {spec['name']} editable")
        done.append(f"neighbour units installed and fenced ({len(installed)} files)")
        later.remove("neighbour units")

        written = gu.install(unit_dir)
        bad = [f"{u}: {s}" for u, s in gu.activate(_runner(run))
               if s.startswith("FAILED") or s.startswith("not attempted")]
        if bad:
            return fail("arming the stack's units: " + "; ".join(bad)[:300], later)
        done.append(f"stack units written ({len(written)} files) and timers armed")
        later.remove("stack units and timers")

        starts = [gu.target_name()] + [u for u in installed
                                       if u.endswith(".service") and "@" not in u]
        for unit in starts:
            ok, text = _ctl(run, "enable", unit)
            if not ok:
                return fail(f"enabling {unit}: {text[:200]}", later)
            done.append(f"enabled {unit}")
            ok, text = _ctl(run, "start", unit)
            if not ok:
                return fail(f"starting {unit}: {text[:200]}", later)
            done.append(f"started {unit}")
        for unit in installed:
            if unit.endswith(".timer"):
                ok, text = _ctl(run, "enable", "--now", unit)
                if not ok:
                    return fail(f"enabling {unit}: {text[:200]}", later)
                done.append(f"enabled {unit}")
    except Exception as exc:  # noqa: BLE001 - reported, with what is and is not done
        return fail(f"{type(exc).__name__}: {exc}", later)

    # 3. probe every component as promote.sh does.
    down = []
    for component, port, _script in gu.components():
        path = "/" if component in ("proxy", "webgui", "webgui_live") else "/health"
        url = f"http://127.0.0.1:{port}{path}"
        if wait(url, HEALTH_WAIT_S):
            out(f"  up    {component:<14} {url}")
        else:
            out(f"  DOWN  {component:<14} {url}")
            down.append(component)
    if down:
        return fail(f"no answer from {', '.join(down)}", ["operator steps"])
    out(f"\nThis box ({identity.self_name}) is prod and every component answers.")
    _operator_steps(box, out)
    return 0


# --- drill --------------------------------------------------------------------------

def _dev_ports(repo: pathlib.Path) -> dict:
    """``{component: port}`` under the DEV profile: the same arithmetic
    ``repo_paths`` does at import, applied to ``environments.toml``'s ``[dev]``."""
    profiles = tomllib.loads((repo / "config" / "environments.toml")
                             .read_text(encoding="utf-8-sig"))
    ports = tomllib.loads((repo / "config" / "ports.toml").read_text(encoding="utf-8-sig"))
    flags = {**repo_paths._ENV_DEFAULTS, **profiles["dev"]}
    got = repo_paths._derive_ports(ports, flags)
    out = {f"{k}_svc": p for k, p in got["service_ports"].items()}
    out["webgui"], out["webgui_live"] = got["nicegui_port"], got["nicegui_live_port"]
    return out


def _snapshot(folder: pathlib.Path) -> set:
    return {p.name for p in folder.iterdir()} if folder.is_dir() else set()


def _drill_body(repo, unit_dir, before, env_path, run, wait, out) -> int:
    _set_env_key(env_path, "name", "dev")
    parsed = tomllib.loads(env_path.read_text(encoding="utf-8"))
    if parsed.get("name") != "dev":
        out('FAIL  config/env.local.toml does not say name = "dev" at the top level; '
            "rendering now would write the PROD units. Nothing was rendered.")
        return 1
    ok, text = _runner(run)([sys.executable, "-m", "deploy.systemd.generate_units",
                             "--install", "--no-activate", "--dest", str(unit_dir)])
    new = _snapshot(unit_dir) - before
    if not ok:
        out(f"FAIL  rendering the dev units: {text[:300]}")
        return 1
    if any(n.startswith("trading-prod") for n in new):
        out("FAIL  the render wrote trading-prod files (the profile did not resolve to "
            "dev); they are being removed.")
        return 1
    if "trading-dev.target" not in new:
        out("FAIL  the render did not write trading-dev.target.")
        return 1
    if "trading-dev-proxy.service" in new:
        out("REFUSED: the dev profile rendered a proxy unit; a drill must never start "
            "a second proxy (environments.toml [dev] owns_proxy must be false).")
        return 1
    ok, text = _ctl(run, "daemon-reload")
    if ok:
        ok, text = _ctl(run, "start", "trading-dev.target")
    if not ok:
        out(f"FAIL  starting trading-dev.target: {text[:300]}")
        return 1
    failed = 0
    for component, port in _dev_ports(repo).items():
        path = "/" if component in ("webgui", "webgui_live") else "/health"
        url = f"http://127.0.0.1:{port}{path}"
        if wait(url, HEALTH_WAIT_S):
            out(f"PASS  {component:<14} {url}")
        else:
            out(f"FAIL  {component:<14} {url}")
            failed += 1
    return 0 if not failed else 1


def _drill_cleanup(run, unit_dir, env_path, original, before, out) -> bool:
    """Undo the drill. env.local.toml first; each step on its own. True only when
    every step worked. A unit that would not stop keeps its file."""
    good = True
    try:
        if original is None:
            env_path.unlink(missing_ok=True)
        else:
            env_path.write_bytes(original)
    except Exception as exc:  # noqa: BLE001 - reported, the next step still runs
        good = False
        out(f'!! could not restore {env_path}: {exc}. It may still say name = "dev": '
            "fix it by hand before anything else.")
    new = sorted(_snapshot(unit_dir) - before)
    stuck = []
    for unit in [n for n in new if n == "trading-dev.target"] + [
            n for n in new if n.endswith(UNIT_SUFFIXES)]:
        try:
            ok, _text = _ctl(run, "stop", unit)
        except Exception:  # noqa: BLE001 - counted as a stop that failed
            ok = False
        if not ok and unit not in stuck:
            stuck.append(unit)
    if stuck:
        out("!! these units would not stop, so NO unit file was deleted: "
            + ", ".join(stuck))
        out("   Finish by hand: systemctl --user stop " + " ".join(stuck)
            + "; then delete the files this drill added to " + str(unit_dir)
            + " and run systemctl --user daemon-reload")
        return False
    for name in new:
        try:
            path = unit_dir / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - the next file still goes
            good = False
            out(f"!! could not remove {name}: {exc}")
    try:
        _ctl(run, "daemon-reload")
    except Exception:  # noqa: BLE001 - a reload that cannot run is not a second failure
        good = False
    return good


def drill(identity, *, home=None, repo=None, run=subprocess.run, wait=None,
          out=print) -> int:
    """Boot the restored stack under the dev profile, probe it, and put everything
    back. The dev profile owns no proxy, so nothing here touches Schwab."""
    home = pathlib.Path(home or pathlib.Path.home())
    repo = pathlib.Path(repo or ROOT)
    wait = wait or (lambda url, timeout: wait_http.wait_for(url, timeout=timeout))
    if identity is None or identity.role != "standby":
        out("REFUSED: the drill runs only on a box whose role is standby "
            f"(this one is {'unconfigured' if identity is None else identity.role}).")
        return 1
    unit_dir = _unit_dir(home)
    if (unit_dir / "trading-prod.target").exists():
        out("REFUSED: trading-prod.target is installed here; this box has been promoted.")
        return 1
    env_path = repo / "config" / "env.local.toml"
    original = env_path.read_bytes() if env_path.is_file() else None
    before = _snapshot(unit_dir)
    rc = 1
    try:
        rc = _drill_body(repo, unit_dir, before, env_path, run, wait, out)
    except Exception as exc:  # noqa: BLE001 - the cleanup below undoes everything
        out(f"FAIL  {type(exc).__name__}: {exc}")
    finally:
        clean = _drill_cleanup(run, unit_dir, env_path, original, before, out)
    if not clean:
        out("\nDrill FAILED: the cleanup did not finish (see the !! lines above).")
        return 1
    out("drill units removed and config/env.local.toml restored.")
    out(f"\nDrill {'PASSED: every component answered' if rc == 0 else 'FAILED'}.")
    return rc


# --- demotion -----------------------------------------------------------------------

def become_standby(identity, *, home=None, repo=None, run=subprocess.run,
                   lookup=standby_fence.resolve, identity_path=None, specs=None,
                   app_host=None, out=print) -> int:
    """Turn a returned old prod into the standby. Needs POSITIVE evidence that the
    public name no longer points here: a failed lookup refuses (unlike the fence)."""
    home = pathlib.Path(home or pathlib.Path.home())
    repo = pathlib.Path(repo or ROOT)
    app_host = app_host or repo_paths.APP_HOST
    specs = standby_config.neighbours() if specs is None else specs
    if identity is None:
        out("REFUSED: this box has no identity file.")
        return 1
    if identity.role == "standby":
        out(f"REFUSED: {identity.self_name} is already the standby.")
        return 1
    try:
        _peer_name, _peer = standby_config.peer(identity)
    except ValueError as exc:
        out(f"REFUSED: {exc}")
        return 1
    self_ip = standby_config.boxes()[identity.self_name].get("public_ip") or ""
    if not self_ip:
        out("REFUSED: standby.toml has no public_ip for this box, so DNS cannot be compared.")
        return 1
    addresses = lookup(app_host, LOOKUP_TIMEOUT_S)
    if addresses is None:
        out(f"REFUSED: the lookup of {app_host} failed or timed out. Demoting a box needs "
            "positive evidence it is not prod: retry when DNS answers.")
        return 1
    if self_ip in addresses:
        out(f"REFUSED: {app_host} still resolves to this box ({self_ip}); it is prod. "
            "Move the DNS records to the new prod first.")
        return 1
    dest = home / PREDEMOTE_DIR
    ok, text = _runner(run)([sys.executable, "tools/backup_local.py", "--no-offsite",
                             "--dest", str(dest), "--keep", "50"])
    if not ok:
        out(f"REFUSED: the pre-demotion backup failed, so nothing was changed: {text[:300]}")
        return 1
    out(f"pre-demotion backup taken under {dest}")
    unit_dir = _unit_dir(home)
    names = sorted(p.name for p in unit_dir.glob("trading-prod*")) if unit_dir.is_dir() else []
    keep = {gu.notifier_name()}
    mine = [n for n in names if n not in keep]
    neighbour = [p.name for p in (unit_dir.glob("*") if unit_dir.is_dir() else [])
                 if p.is_file() and p.name.endswith(UNIT_SUFFIXES)
                 and any(fnmatch.fnmatch(p.name, pat)
                         for s in specs for pat in s.get("units", []))]
    templates = [n for n in neighbour if "@." in n and n.endswith(".service")]
    plain = [n for n in neighbour if n.endswith(".service") and "@" not in n]
    timers = [n for n in mine + neighbour if n.endswith(".timer")]
    # The fence goes on BEFORE anything else changes: a returning box must not
    # start a second hub or second reports whichever way this run ends.
    fence_neighbours(specs, home, run)
    problems, warnings = [], []
    for unit in timers + plain:
        ok, text = _ctl(run, "disable", "--now", unit)
        if not ok:
            problems.append(f"{unit}: {text[:120]}")
    for unit in templates:
        # systemctl refuses a bare template name: stop its running instances.
        ok, text = _ctl(run, "stop", unit.replace("@.", "@*."))
        if not ok:
            warnings.append(f"{unit} instances: {text[:120]}")
    if (unit_dir / gu.target_name()).exists():
        for verb in ("stop", "disable"):
            ok, text = _ctl(run, verb, gu.target_name())
            if not ok:
                problems.append(f"{gu.target_name()} {verb}: {text[:120]}")
    for unit in (n for n in mine if n.endswith(".service")):
        ok, text = _ctl(run, "stop", unit)
        if not ok:
            problems.append(f"{unit}: {text[:120]}")
    if problems:
        out("FAILED: could not stop everything; nothing was deleted and the role is unchanged:")
        for p in problems:
            out(f"  - {p}")
        return 1
    for w in warnings:
        out(f"  note: {w}")
    for name in mine:
        try:
            (unit_dir / name).unlink()
        except OSError:
            pass
    standby_config.write_identity(Identity(identity.self_name, "standby", False), identity_path)
    standby_units.install(dest=unit_dir)
    bad = [f"{u}: {s}" for u, s in standby_units.activate(_runner(run))
           if s.startswith("FAILED")]
    out(f"removed {len(mine)} generated unit files; role is now standby.")
    if bad:
        out("WARNING: the sync timer is not armed: " + "; ".join(bad))
    out("\nKEYS TO EXCHANGE with the new prod (the roles swapped):")
    out("  - here: deploy/standby/provision.sh <this box> \"<new prod's standby_watch "
        "public key>\" \"<age public key line>\" creates ~/.ssh/standby_pull;")
    out("  - on the new prod: add that standby_pull public key to authorized_keys "
        "(read-only rrsync of ~/backups) and create ~/.ssh/standby_watch whose "
        "public half goes into this box's authorized_keys.")
    out(f"The first nightly sync replaces this box's data with the new prod's. "
        f"Its own last day is kept in {dest}.")
    return 0


# --- command line -------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("takeover", help="turn this standby into prod")
    t.add_argument("--peer-is-down", action="store_true",
                   help="go on although the peer still answers a probe")
    sub.add_parser("drill", help="boot the restored stack as dev, then undo it")
    sub.add_parser("become-standby", help="turn a returned old prod into the standby")
    sub.add_parser("fence-neighbours", help="fence the neighbour apps' units")
    args = ap.parse_args(argv)
    identity = standby_config.read_identity()
    if args.cmd == "takeover":
        return takeover(identity, peer_is_down=args.peer_is_down)
    if args.cmd == "drill":
        return drill(identity)
    if args.cmd == "become-standby":
        return become_standby(identity)
    paths = fence_neighbours(standby_config.neighbours(), pathlib.Path.home())
    for p in paths:
        print(f"fenced {p}")
    print(f"{len(paths)} drop-in(s) in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
