"""Emit the systemd **user** units that run this checkout's stack.

These are GENERATED, not hand-written, and there are no ``.service`` files in
git. Every value comes from ``repo_paths`` -- ports, the checkout root, the
environment name, whether this environment owns a proxy -- for exactly the
reason ports live in one config file: a committed unit file would be a second
copy of all of that, free to drift from the first, and the drift would only
show up as a Restart button that errors in prod.

Run it in the checkout you want units for::

    .venv/bin/python -m deploy.systemd.generate_units --install
    systemctl --user daemon-reload
    systemctl --user enable --now trading-<env>.target

**Why user units and not system units.** The System Status page restarts its own
siblings with ``systemctl --user``. That needs no polkit rule and no sudoers
entry; a system-unit equivalent would mean handing root to a network-facing app.
``loginctl enable-linger <user>`` is what makes them start at boot and survive
logout.

**What these replace.** ``wait_and_run.bat``'s port waiting (``After=`` +
``ExecStartPre``), ``start_all_wt.bat``'s ordering (``Requires=``/``After=``),
``tools/watchdog.py``'s storm-capped restarts (``Restart=`` +
``StartLimitBurst``), ``stop_all.py``'s WMI hunt for this checkout's PIDs
(``PartOf=`` -- systemd owns the PIDs), and the ``logs\\*.out.log`` redirection
(the journal).
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from repo_paths import (ENV_NAME, NICEGUI_PORT, OWNS_PROXY,  # noqa: E402
                        PROXY_PORT, REPO_ROOT, SERVICE_PORTS)



def _python():
    """The venv interpreter, as a POSIX path.

    Computed per call, not frozen at import, so a test can point REPO_ROOT at a
    POSIX root and exercise the real Linux shape from any host. `as_posix` because
    a unit file is a Linux artifact unconditionally -- there is no case where a
    backslash in one is correct.
    """
    return pathlib.PurePosixPath(REPO_ROOT.as_posix()) / ".venv" / "bin" / "python"


def _env_file():
    return pathlib.PurePosixPath(REPO_ROOT.as_posix()) / ".env"


def _workdir():
    return pathlib.PurePosixPath(REPO_ROOT.as_posix())

# A crash-looping component is retried a few times and then LEFT DOWN and
# logged, rather than thrashed forever -- the same contract tools/watchdog.py
# had via MAX_RESTARTS / STORM_WINDOW_SEC.
RESTART_SEC = 5
START_LIMIT_BURST = 5
START_LIMIT_INTERVAL_SEC = 300

# How long a service waits for the proxy to ANSWER HTTP before giving up.
PROXY_WAIT_TIMEOUT_SEC = 120


def target_name():
    return f"trading-{ENV_NAME}.target"


def unit_name(component):
    return f"trading-{ENV_NAME}-{component}.service"


def components():
    """``(component, port, script)`` for every unit this environment runs.

    ⚠ ``component`` is consumed VERBATIM as the unit suffix and must equal what
    ``webgui.pages.status.restart_spec`` puts in ``spec["name"]``. That equality
    is pinned by ``tests/test_systemd_units.py`` and is the one seam where a
    rename would otherwise surface only as a Restart button that errors.

    The proxy appears **only when this environment owns it**. Dev borrows prod's
    (the Schwab OAuth refresh token is a single rotating credential, so there can
    be only one holder), so ownership is encoded in which units exist rather than
    in a filter someone has to remember to apply.
    """
    out = []
    if OWNS_PROXY:
        out.append(("proxy", PROXY_PORT, "schwab-proxy/schwab_proxy.py"))
    for key, port in SERVICE_PORTS.items():
        out.append((f"{key}_svc", port, f"services/{key}_svc/app.py"))
    out.append(("webgui", NICEGUI_PORT, "webgui/main.py"))
    return out


def _service_text(component, port, script):
    is_proxy = component == "proxy"
    is_webgui = component == "webgui"

    unit = [f"Description=NeuralStrike {ENV_NAME} - {component} (:{port})",
            f"PartOf={target_name()}"]

    if not is_proxy and OWNS_PROXY:
        # The web GUI is ordered after the proxy but does NOT require it: it
        # renders a proxy-down banner and is fully usable without one, which
        # restart_spec already encodes as wait_port 0. A dead proxy must not
        # keep the UI down.
        unit.append(f"After={unit_name('proxy')}")
        if not is_webgui:
            unit.append(f"Requires={unit_name('proxy')}")

    unit += [
        "# A crash-looping unit is retried this many times in this window, then",
        "# left down and logged. Replaces tools/watchdog.py's storm cap.",
        "# NOTE: these belong in [Unit]; systemd moved them there in v229",
        "# and silently ignores them in [Service].",
        f"StartLimitIntervalSec={START_LIMIT_INTERVAL_SEC}",
        f"StartLimitBurst={START_LIMIT_BURST}",
    ]

    service = ["Type=simple",
               f"WorkingDirectory={_workdir()}",
               "Environment=PYTHONUNBUFFERED=1",
               "# Belt-and-braces beside repo_paths' boot assertion: a tz-naive",
               "# datetime in this codebase MEANS Central.",
               "Environment=TZ=America/Chicago",
               "# Secrets ONLY here. `systemctl show` prints Environment= to any",
               "# local user; it shows only the PATH of an EnvironmentFile.",
               "# No leading '-': a missing file must fail the unit loudly, not",
               "# start a stack that is silently mute.",
               f"EnvironmentFile={_env_file()}"]

    if not is_proxy and not is_webgui and OWNS_PROXY:
        # After= orders process START and says nothing about readiness. A dead
        # accept loop stays bound and passes a TCP connect -- which is how a
        # promote once left prod serving no UI at all. wait_http.py does a GET.
        service.append(
            f"ExecStartPre={_python()} tools/wait_http.py "
            f"--port {PROXY_PORT} --timeout {PROXY_WAIT_TIMEOUT_SEC} --label 'the proxy'")

    service += [f"ExecStart={_python()} {script}",
                "Restart=on-failure",
                f"RestartSec={RESTART_SEC}"]

    return ("[Unit]\n" + "\n".join(unit)
            + "\n\n[Service]\n" + "\n".join(service)
            + f"\n\n[Install]\nWantedBy={target_name()}\n")


def _target_text():
    wants = " ".join(unit_name(c) for c, _, _ in components())
    return ("[Unit]\n"
            f"Description=NeuralStrike {ENV_NAME} stack\n"
            f"Wants={wants}\n"
            "\n[Install]\n"
            "WantedBy=default.target\n")


def _backup_units():
    """The nightly backup service + timer.

    Deliberately NOT ``PartOf`` the stack target. Backups must not stop when the
    stack does -- the moment you most want yesterday's copy is the moment the
    stack is down. It is a oneshot on a timer, not a member of the fleet.

    17:30 local, which is after everything that writes: collection stops 15:20,
    the momentum cascade runs 16:20 and the calibration rebuild 16:30. Backing up
    mid-cascade would capture a half-written night.

    ``Persistent=true`` runs a missed occurrence at next boot -- a machine that
    was off at 17:30 still gets its backup rather than silently skipping a day.
    """
    svc = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - nightly backup
# No PartOf: this must survive the stack being stopped.

[Service]
Type=oneshot
WorkingDirectory={_workdir()}
Environment=TZ=America/Chicago
EnvironmentFile={_env_file()}
ExecStart={_python()} tools/backup_local.py
"""
    tmr = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - nightly backup timer

[Timer]
# After collection (15:20), the momentum cascade (16:20) and the calibration
# rebuild (16:30) -- so the copy is of a settled night, not a half-written one.
OnCalendar=*-*-* 17:30:00
# Run a MISSED occurrence at boot rather than skipping the day.
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
"""
    return {f"trading-{ENV_NAME}-backup.service": svc,
            f"trading-{ENV_NAME}-backup.timer": tmr}


def render_all():
    """``{unit filename: text}`` for this environment."""
    out = {unit_name(c): _service_text(c, p, s) for c, p, s in components()}
    out[target_name()] = _target_text()
    out.update(_backup_units())
    return out


def install(dest=None):
    """Write the units into the systemd user directory. Returns written paths."""
    dest = pathlib.Path(dest or (pathlib.Path.home() / ".config" / "systemd" / "user"))
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render_all().items():
        p = dest / name
        p.write_text(text, encoding="utf-8")
        written.append(p)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--install", action="store_true",
                    help="write the units into ~/.config/systemd/user")
    ap.add_argument("--dest", help="write them somewhere else instead")
    args = ap.parse_args(argv)

    if args.install or args.dest:
        for p in install(args.dest):
            print(f"wrote {p}")
        print(f"\nNow:  systemctl --user daemon-reload"
              f"\n      systemctl --user enable --now {target_name()}")
        return 0

    for name, text in render_all().items():
        print(f"# ===== {name} " + "=" * (60 - len(name)))
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
