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
from shared.market_calendar import window_bounds  # noqa: E402



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

# The nightly backup encrypts ~1.5 GB and uploads it (6m18s measured). A oneshot
# would otherwise inherit the 90s default and be killed mid-upload.
BACKUP_TIMEOUT_SEC = 7200

# The operator's 0600 file holding RTMP_URL -- the YouTube stream key. It lives
# OUTSIDE the checkout on purpose: a key in the repo is a key in every clone, in
# every backup archive, and one `git add -A` from being public. Named here as a
# constant rather than buried in a template so the tests can assert the exact
# path the unit loads, and so moving it is one edit.
STREAM_ENV_FILE = "/etc/neuralstrike-stream/env"


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

    20:00 Central, comfortably after everything that writes: collection stops
    15:20, the momentum cascade runs 16:20 and the calibration rebuild 16:30.
    Backing up mid-cascade would capture a half-written night that looks
    complete. The host runs America/Chicago, so OnCalendar is already CT.

    ``Persistent=true`` is KEPT even though the VPS is always on. It costs
    nothing when the timer fires normally, and the box does reboot -- it was
    rebooted during the migration itself. A missed night that silently never
    happens is the failure this whole job exists to prevent.
    """
    svc = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - nightly backup
# No PartOf: this must survive the stack being stopped.

[Service]
Type=oneshot
WorkingDirectory={_workdir()}
Environment=TZ=America/Chicago
EnvironmentFile={_env_file()}
# WITHOUT THIS systemd kills the job. A oneshot inherits
# DefaultTimeoutStartSec (90s on this host); the backup encrypts ~1.5 GB and
# then uploads it, which took 6m18s measured. It would be SIGTERMed mid-upload
# every night, leaving a partial object in Drive and a failed unit.
TimeoutStartSec={BACKUP_TIMEOUT_SEC}
ExecStart={_python()} tools/backup_local.py
"""
    tmr = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - nightly backup timer

[Timer]
# 20:00 CENTRAL on TRADING DAYS (the host TZ is America/Chicago, so this is
# already CT). After collection (15:20), the momentum cascade (16:20) and the
# calibration rebuild (16:30) -- so the copy is of a settled night, not a
# half-written one.
#
# Mon..Fri, because nothing writes at the weekend: a Sat/Sun run ships ~1.5 GB
# byte-identical to Friday's, and counts against KEEP_REMOTE. Three weekend
# runs would push the last three WEEKDAY archives off Drive and leave only
# copies of a closed market -- the cost is not the bandwidth, it is the
# retention.
#
# Market holidays are NOT filtered. systemd has no calendar for them, and the
# failure modes are asymmetric: a redundant holiday copy wastes one slot, a
# missed real session loses a day of the trading record.
OnCalendar=Mon..Fri *-*-* 20:00:00
# Run a MISSED occurrence at boot rather than skipping the day.
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
"""
    return {f"trading-{ENV_NAME}-backup.service": svc,
            f"trading-{ENV_NAME}-backup.timer": tmr}


def _stream_window_seconds():
    """Length of ``[windows.stream]`` in seconds, derived -- never typed.

    The unit and ``config/sessions.toml`` cannot disagree about when the
    broadcast ends, which is the same reason ports live in one file. Both bounds
    are wall-clock times on the same day, so this is minute arithmetic and not a
    timedelta: there is no date to subtract, and no DST transition inside a
    single trading session.
    """
    start, end = window_bounds("stream")
    return (end.hour * 60 + end.minute - start.hour * 60 - start.minute) * 60


def _stream_units():
    """The public YouTube wall stream: a service the TIMER owns, plus its timer.

    **PartOf the target but deliberately NOT WantedBy it.** Every other unit is
    ``WantedBy`` the target, so ``systemctl start trading-<env>.target`` brings
    it up -- which is exactly what a promote does, at whatever hour someone
    promotes. A broadcast must not start that way. ``PartOf`` without
    ``WantedBy`` gives precisely the asymmetry wanted: stopping the stack stops
    the stream, starting the stack does not start it. The ``[Install]`` section
    therefore belongs on the TIMER (``WantedBy=timers.target``) and the service
    has none at all -- it is not a thing you enable, it is a thing the timer
    starts.

    **Requires= the web GUI, not merely After=.** The other units only order
    themselves after the proxy, because the UI renders a proxy-down banner and
    stays usable. A stream with no web GUI has no such degrade path: it is nine
    hours of a connection-refused page on a public channel.

    **No ExecStartPre.** ``tools/stream_wall.sh`` already calls
    ``tools/wait_http.py`` itself -- it has to, since it reads the port and the
    wall route out of Python in the same breath. A probe here would be a second
    copy of the timeout, free to disagree with the first.

    **RuntimeMaxSec, not a second timer.** The broadcast has to stop at the
    window's close. A stop-timer could do it, but then the thing that ends the
    stream is a separate unit that can fail to fire, be disabled, or be missed
    over a reboot -- and its failure mode is a public stream of frozen overnight
    numbers, unnoticed until a viewer says so. ``RuntimeMaxSec`` is enforced by
    the same manager that started the process, so it cannot be missed.

    ⚠ **The unit bounces once at the close, and that is accepted, not
    overlooked.** When ``RuntimeMaxSec`` expires systemd terminates the unit with
    result ``timeout``, which ``Restart=on-failure`` does restart. The restarted
    script runs its own ``in_window`` gate, finds itself outside the window,
    prints the stand-down line and exits **0** -- so systemd sees success and the
    unit goes inactive. One wasted spawn, well inside ``StartLimitBurst``.
    ``SuccessExitStatus`` does NOT prevent it: a RuntimeMaxSec kill sets the
    failure *result* to ``timeout`` regardless of exit status, so nothing about
    exit-code interpretation reaches it. Preventing the bounce would mean
    dropping ``Restart=on-failure``, which is the entire recovery mechanism for
    the mid-session case the script's Xvfb/Chrome watchdog exists to trigger.
    A daily one-spawn bounce is a much cheaper price than a black stream nobody
    restarts.

    ⚠ **Known limit: a mid-session restart resets the RuntimeMaxSec clock.** The
    cap is per invocation, so a crash-and-restart at 14:00 would run until 21:20
    rather than 15:20. The window gate only runs at startup, so nothing else
    stops it. Not fixed here -- it needs either a stop-timer or a window
    re-check inside the script's watchdog loop, and the script is out of scope
    for this change.
    """
    runtime = _stream_window_seconds()
    start, _end = window_bounds("stream")
    webgui = unit_name("webgui")

    svc = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - wall stream (YouTube)
# PartOf, so stopping the stack stops the broadcast -- but NO [Install]
# WantedBy the target, so starting the stack does NOT start one. The timer owns
# when this runs; see this function's docstring.
PartOf={target_name()}
# Requires, not just After: unlike the services, a stream with no web GUI has
# no degraded mode -- it is a connection-refused page on a public channel.
Requires={webgui}
After={webgui}
# A crash-looping unit is retried this many times in this window, then left
# down and logged.
# NOTE: these belong in [Unit]; systemd moved them there in v229
# and silently ignores them in [Service].
StartLimitIntervalSec={START_LIMIT_INTERVAL_SEC}
StartLimitBurst={START_LIMIT_BURST}

[Service]
Type=simple
WorkingDirectory={_workdir()}
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/Chicago
# TWO environment files, NEITHER with a leading '-'. The repo's .env for the
# stack's own secrets; the operator's file for RTMP_URL. A missing file must
# fail the unit loudly rather than encode nine hours into nowhere -- the same
# rule the services follow, for the same reason.
EnvironmentFile={_env_file()}
EnvironmentFile={STREAM_ENV_FILE}
# Stop at the window's close, enforced by the manager that started us rather
# than by a second timer that could fail to fire. Derived from
# config/sessions.toml [windows.stream]; never a literal.
RuntimeMaxSec={runtime}
# No ExecStartPre: the script calls tools/wait_http.py itself.
ExecStart={_workdir()}/tools/stream_wall.sh
Restart=on-failure
RestartSec={RESTART_SEC}
"""

    tmr = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - wall stream timer

[Timer]
# The window's OWN start time (the host TZ is America/Chicago, so this is
# already CT). Mon..Fri because the exchange is shut at the weekend.
#
# Market holidays are NOT filtered, and here that is fine: the script's own
# in_window() gate covers them, standing down with exit 0. This is the OPPOSITE
# of the backup timer's choice, deliberately -- there, an unfiltered holiday
# run wastes a retention slot, so erring towards running is right. Here, an
# unfiltered holiday run would put frozen numbers in front of an audience, so
# the gate that CAN see the calendar has to be the one that decides.
OnCalendar=Mon..Fri *-*-* {start.hour:02d}:{start.minute:02d}:00
# Deliberately NO Persistent=true (the backup timer has it). A missed backup is
# still worth taking late; a missed broadcast window is simply gone, and a
# catch-up would start a public stream at whatever hour the box came back.

[Install]
WantedBy=timers.target
"""
    return {f"trading-{ENV_NAME}-stream.service": svc,
            f"trading-{ENV_NAME}-stream.timer": tmr}


def render_all():
    """``{unit filename: text}`` for this environment."""
    out = {unit_name(c): _service_text(c, p, s) for c, p, s in components()}
    out[target_name()] = _target_text()
    out.update(_backup_units())
    out.update(_stream_units())
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
