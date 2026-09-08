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

from repo_paths import (ENV_NAME, NICEGUI_LIVE_PORT,  # noqa: E402
                        NICEGUI_PORT, OWNS_PROXY, PROXY_PORT, REPO_ROOT,
                        SERVICE_PORTS)
from shared.market_calendar import slot_times, window_bounds  # noqa: E402



def _python():
    """The venv interpreter, as a POSIX path.

    Computed per call, not frozen at import, so a test can point REPO_ROOT at a
    POSIX root and exercise the real Linux shape from any host. `as_posix` because
    a unit file is a Linux artifact unconditionally -- there is no case where a
    backslash in one is correct.
    """
    return pathlib.PurePosixPath(REPO_ROOT.as_posix()) / ".venv" / "bin" / "python"


def _env_file():
    """The checkout's own ``.env`` -- where every STACK secret lives.

    ``ANTHROPIC_API_KEY``, ``TELEGRAM_BOT_TOKEN``, ``PROXY_SHARED_SECRET``,
    ``SMS_SMTP_APP_PASSWORD``, ``DISCORD_WEBHOOK_URL``, ``MEMURAI_PASSWORD``,
    ``GAMMA_BRIEFING_WEBHOOK_URL``. ``STREAM_ENV_FILE`` is the counter-example
    to keeping a secret here, and the difference is OWNERSHIP -- the RTMP key
    belongs to the operator, not to the checkout, so it lives outside it.

    ⚠ **The public live unit does NOT load this file** -- see
    :func:`_live_env_file`. That is the one exception, and it is about BLAST
    RADIUS rather than ownership.
    """
    return pathlib.PurePosixPath(REPO_ROOT.as_posix()) / ".env"


def _live_env_file():
    """``.env.live`` -- the PUBLIC process's own, minimal environment.

    ``webgui_live`` is the one internet-facing, unauthenticated process in the
    fleet, and until 2026-09-07 it loaded the same ``.env`` as everything else:
    the strongest credential set in the fleet held by the weakest-protected
    process. It needs exactly two values -- ``REDIS_LIVE_URL`` (the read-only
    Redis ACL user) and ``MEMURAI_PASSWORD`` (which redis-py uses only when
    ``REDIS_LIVE_URL`` names a user without a password; a password IN the URL
    wins over the explicit kwarg ``shared/bus`` passes -- verified, not assumed).

    ⚠ No current code path in this process reads any of the others; the module
    closure was swept for ``os.environ`` sinks and found none. The split is not
    a bug fix, it is the difference between "an RCE in NiceGUI costs the public
    screens" and "an RCE in NiceGUI costs the Anthropic key and the Telegram
    bot". Blast radius is worth bounding before there is a reason to.

    **No leading '-', deliberately, and the reasoning is now stronger than the
    house rule.** A ``-`` would start the unit with the file missing -- which
    means with no ``REDIS_LIVE_URL``, which ``live_main.require_acl_url``
    refuses to serve prod in anyway. So the dash cannot buy a running process;
    it can only replace a systemd error naming the exact missing path with a
    Python one naming the variable. Fail at the more specific message.
    """
    return pathlib.PurePosixPath(REPO_ROOT.as_posix()) / ".env.live"


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

# ── The public process's memory cap ──────────────────────────────────────────
# ⚠ ONLY the public unit carries these, and that is deliberate. The other eight
# are not internet-facing and a wrong value there kills the trading stack.
#
# WHY AT ALL. `webgui_live` is unauthenticated and unthrottled: Caddy's
# rate_limit needs an xcaddy build (see deploy/caddy/generate_caddyfile.py's
# docstring) and none is in place. Measured: 20 plain unauthenticated
# `GET /desk` requests created 20 NiceGUI `Client` objects retaining ~619 KB
# EACH against an empty cache -- no websocket, no cookie. NiceGUI prunes
# socketless clients after 60 s on a 10 s timer, so the window is ~70 s, but
# nothing bounds the arrival rate; at 100 req/s that is thousands of live
# clients holding 4 GB+. Websocket connections have no cap and no prune at all.
# This does NOT stop that. It decides WHO DIES when it happens: the public
# screens, alone, restarted by Restart=on-failure -- rather than the box's OOM
# killer choosing among the six services, the private trading UI and Redis.
#
# WHY THESE NUMBERS. The host is 8 GB (16 preferred), and the whole nine-unit
# stack is budgeted at ~1.2 GB of process memory -- roughly 150 MB per process,
# the rest being page cache for the 1.52 GB gex_history.db. 1 GiB is ~7x that
# average for a process that mounts fourteen page modules, so normal use never
# approaches it; and against the measured 619 KB per client it is several
# hundred concurrent anonymous clients above baseline, far past any legitimate
# concurrency for a personal site. Against the box, it is one eighth of the
# smallest supported host: the trading stack and the page cache are untouched.
#
# MemoryHigh THROTTLES before MemoryMax KILLS. Above 768M the kernel applies
# reclaim pressure and the process slows, which surfaces as slow public screens
# -- a signal, and a recoverable one -- instead of a kill with nothing before
# it. The gap is deliberately wide enough (256 MiB) that a genuine burst has
# room to drain rather than oscillating on the edge of the cap.
#
# ⚠ CONFIRM THE IDLE BASELINE ON THE BOX, once, after deploying:
#   systemctl --user show trading-<env>-webgui_live -p MemoryCurrent
# If it idles above ~500 MB these two numbers are the ones to raise -- and they
# are the only two. (MemoryAccounting is implied by both directives, so it is
# not restated. On a cgroup v1 host they would be silently ignored, the same
# family of trap as StartLimit* in the wrong section; this host is v2 unified.)
LIVE_MEMORY_HIGH = "768M"
LIVE_MEMORY_MAX = "1G"

# How often the public grid's thumbnails are refreshed. Every 15 minutes inside
# [windows.live_capture]; the script's own gate decides the days and hours, so
# this only has to be the cadence.
LIVE_CAPTURE_INTERVAL_MIN = 15
# Slack on top of the derived per-screen budget, for interpreter start and the
# WebP encodes.
LIVE_CAPTURE_TIMEOUT_SLACK_SEC = 60

# Slack on top of the gallery run's derived per-shot budget: interpreter start,
# the WebP encodes, and the cookie-bootstrap server coming up. The verification
# render is NOT in here -- it is a full render with the same per-shot ceiling
# and is counted as a shot in _gallery_capture_timeout_seconds.
GALLERY_CAPTURE_TIMEOUT_SLACK_SEC = 60


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

    ⚠ **The live screens DO get a unit in dev**, and the asymmetry with the proxy
    is deliberate. Withholding a unit has only ever been about a **single
    exclusive resource** two environments would fight over -- there is exactly
    one Schwab refresh token, and a second holder invalidates the first. Nothing
    like that exists here: the live process binds dev's own offset port, reads
    Redis and nothing else, spends no Schwab call, no Claude call and sends no
    notification, so there is nothing for the four dev suppressions to suppress.
    And the standing development rule is that work is *verified running in dev*
    before it is promoted; a dev checkout with no live unit would make that
    verification a hand-started process outside systemd, which is exactly the
    shape that ships unit config nobody has run.
    """
    out = []
    if OWNS_PROXY:
        out.append(("proxy", PROXY_PORT, "schwab-proxy/schwab_proxy.py"))
    for key, port in SERVICE_PORTS.items():
        out.append((f"{key}_svc", port, f"services/{key}_svc/app.py"))
    out.append(("webgui", NICEGUI_PORT, "webgui/main.py"))
    # The PUBLIC read-only screens, a separate process on a separate origin.
    # Separate so a public traffic spike or a crash cannot reach the trading UI.
    out.append(("webgui_live", NICEGUI_LIVE_PORT, "webgui/live_main.py"))
    return out


def _service_text(component, port, script):
    is_proxy = component == "proxy"
    is_webgui = component == "webgui"
    # The public read-only screens read Redis -- a SYSTEM unit, outside this
    # target entirely -- and nothing else: no proxy call, no Schwab call, no
    # service call. So they order themselves against nothing here. An After= on
    # a component this process never speaks to would read as a real dependency
    # and be none, and the next person to touch this file would have to prove
    # it was fake before deleting it.
    is_live = component == "webgui_live"

    unit = [f"Description=NeuralStrike {ENV_NAME} - {component} (:{port})",
            f"PartOf={target_name()}"]

    if not is_proxy and not is_live and OWNS_PROXY:
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
               # The PUBLIC process gets its OWN minimal file. It is the one
               # internet-facing, unauthenticated process in the fleet and must
               # not hold the fleet's strongest credentials -- see
               # _live_env_file for the whole argument.
               f"EnvironmentFile={_live_env_file() if is_live else _env_file()}"]

    if not is_proxy and not is_webgui and not is_live and OWNS_PROXY:
        # After= orders process START and says nothing about readiness. A dead
        # accept loop stays bound and passes a TCP connect -- which is how a
        # promote once left prod serving no UI at all. wait_http.py does a GET.
        service.append(
            f"ExecStartPre={_python()} tools/wait_http.py "
            f"--port {PROXY_PORT} --timeout {PROXY_WAIT_TIMEOUT_SEC} --label 'the proxy'")

    if is_live:
        # ⚠ [Service], where cgroup resource control belongs -- unlike
        # StartLimit* above, which systemd moved to [Unit] in v229 and silently
        # ignores here. See LIVE_MEMORY_HIGH for the numbers and the reasoning;
        # this is on the PUBLIC unit alone.
        service += [
            "# The one internet-facing, unauthenticated, unthrottled process.",
            "# Caps WHO DIES under a request flood: these screens, alone.",
            f"MemoryHigh={LIVE_MEMORY_HIGH}",
            f"MemoryMax={LIVE_MEMORY_MAX}",
        ]

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


def _live_capture_timeout_seconds():
    """The unit's ``TimeoutStartSec``, DERIVED from the script's own budget.

    ``tools/capture_live_shots.py`` gives each screen a wall-clock ceiling and
    photographs every screen ``webgui/live_screens.py`` publishes, so the run's
    worst case is those two multiplied. Typed as a literal it would be wrong the
    first time a screen is added -- and wrong in the way the backup unit was
    before it had a timeout at all: systemd SIGTERMs the job partway, so some
    tiles refresh and the rest silently do not.
    """
    from tools import capture_live_shots as capture

    return (capture.SCREEN_TIMEOUT_SEC * len(capture.targets())
            + LIVE_CAPTURE_TIMEOUT_SLACK_SEC)


def _live_capture_units():
    """The thumbnail capture: a oneshot the timer owns, plus its timer.

    **No ``Restart=``, deliberately.** A oneshot that fails should be visible in
    ``systemctl --user --failed`` and then wait for the next quarter hour, not
    retry: the two failures worth distinguishing are "no browser on this host",
    which no amount of retrying fixes, and "the live process is down", which the
    next firing picks up on its own. The script itself already declines to fail
    for the ordinary reasons -- outside the window it stands down with exit 0,
    and one unreachable screen is logged rather than raised.

    **Not ``PartOf`` the target and not ``WantedBy`` it.** Like the backup and
    the stream, this is not a member of the fleet; the timer decides when it
    runs, so the ``[Install]`` section belongs there.

    **No ``After=`` on the live web GUI either**, though it is the thing being
    photographed. Ordering matters only at boot, and a capture that lands before
    the live process is up costs one logged failure and fifteen minutes. Against
    that, ``tests/test_systemd_units.py`` pins that NOTHING in this file names
    the public unit as a dependency -- the whole point of running the public
    origin as a separate process -- and an ordering edge here would be the first
    exception someone has to reason about later.
    """
    timeout = _live_capture_timeout_seconds()

    svc = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - live screen thumbnails
# No PartOf and no [Install]: the timer owns this, and a stop of the stack has
# nothing to stop -- it is a oneshot that runs for a minute every quarter hour.
# A crash-looping unit is retried this many times in this window, then left
# down and logged.
# NOTE: these belong in [Unit]; systemd moved them there in v229
# and silently ignores them in [Service].
StartLimitIntervalSec={START_LIMIT_INTERVAL_SEC}
StartLimitBurst={START_LIMIT_BURST}

[Service]
Type=oneshot
WorkingDirectory={_workdir()}
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/Chicago
EnvironmentFile={_env_file()}
# Fourteen screens with a per-screen ceiling. WITHOUT THIS the oneshot inherits
# DefaultTimeoutStartSec (90s here) and would be killed partway through, leaving
# some tiles fresh and the rest stale with nothing to say which.
TimeoutStartSec={timeout}
ExecStart={_python()} tools/capture_live_shots.py
"""

    tmr = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - live screen thumbnail timer

[Timer]
# Every {LIVE_CAPTURE_INTERVAL_MIN} minutes, all day. The DAYS and HOURS are not
# filtered here on purpose: the script gates on [windows.live_capture] via the
# market calendar, which is the only thing that can see a holiday -- the same
# division of labour the stream timer uses. A stand-down is a sub-second
# interpreter start.
OnCalendar=*:0/{LIVE_CAPTURE_INTERVAL_MIN}
# Deliberately NOT Persistent=true. A missed capture is worthless later: the
# next one is at most {LIVE_CAPTURE_INTERVAL_MIN} minutes away and shows the
# market as it is now, not as it was during the downtime.
Persistent=false

[Install]
WantedBy=timers.target
"""
    return {f"trading-{ENV_NAME}-live-capture.service": svc,
            f"trading-{ENV_NAME}-live-capture.timer": tmr}


def _gallery_capture_timeout_seconds():
    """The unit's ``TimeoutStartSec``, DERIVED from the script's own budget.

    ``tools/capture_gallery_shots.py`` gives each render a wall-clock ceiling
    (``SHOT_TIMEOUT_SEC``) and renders every shot in ``tools/gallery_screens.py``
    -- plus ONE more, the session verification render it does before publishing
    anything, which is why the shot count is taken ``+ 1``.

    Typed as a literal this would be wrong the first time a shot is added, and
    wrong in the way that costs most here: systemd SIGTERMs the job partway, so
    some tiles are refreshed and the rest are not, with nothing on the page to
    say which. ``targets()`` deliberately counts the shots the tool declines to
    take as well -- a skip is a sub-millisecond branch, so counting it is free
    and the alternative is a second definition of what the run covers.
    """
    from tools import capture_gallery_shots as capture

    renders = len(capture.targets()) + 1
    return (capture.SHOT_TIMEOUT_SEC * renders
            + GALLERY_CAPTURE_TIMEOUT_SLACK_SEC)


def _gallery_capture_units():
    """The marketing-gallery recapture: a oneshot the timer owns, plus its timer.

    **Once a trading day, at ``[slots.gallery_capture]``.** ⚠ That is the one
    ``[slots]`` entry read by systemd rather than by a service scheduler -- the
    other four are resolved at module import inside ``options_svc`` and
    ``sentiment_svc``. ``[slots]`` is still the right home for the time: it is a
    named clock mark that fires once per trading day, exactly what that table
    models, where ``[windows]`` models a span (which is why ``live_capture``
    needed one -- it fires every fifteen minutes inside it). The consequence to
    know is that moving this time needs ``generate_units --install`` and a
    ``daemon-reload``, not a service restart.

    **Nine o'clock, not eight.** Half an hour after the 08:30 CT regular open,
    so the pages have painted live data. A pre-open capture publishes a gallery
    of blank panels over the product's showcase images.

    **No ``Restart=``, deliberately** -- the same reasoning as the live capture.
    The tool exits non-zero for exactly two things worth telling apart: no
    browser on the box, which retrying cannot fix, and the app unreachable or
    the session refused, which the next day picks up. Crucially it publishes
    NOTHING in the failure case (it verifies the session against a real render
    first), so a failed run leaves the existing gallery intact and shows up in
    ``systemctl --user --failed``.

    **No ``After=`` and no ``Requires=`` on the web GUI**, though loopback
    ``:8500`` is the thing being photographed. Ordering decides BOOT sequence
    only, and this unit is never started at boot -- the timer starts it
    mid-session, hours after the stack is up. A ``Requires=`` would be actively
    worse: it would let a screenshot job pull the trading UI around. The failure
    it might seem to prevent -- the timer firing during a webgui restart -- is
    not an ordering problem and is already handled the right way, by publishing
    nothing and exiting non-zero.

    **The checkout's ``.env``, like every other unit here**, even though this
    tool needs no environment secret of its own -- it reads the 0600
    credentials file directly and talks to loopback. Omitting it was considered
    and rejected: ``test_secrets_come_from_an_EnvironmentFile`` is a file-wide
    invariant, and buying a marginal blast-radius reduction for a local,
    owner-run, once-a-day job by carving an exemption into it would let a future
    unit skip its env file unnoticed. ``webgui_live``'s ``.env.live`` split is
    the shape to copy if that reduction is ever actually wanted.

    ⚠ **``Mon..Fri`` filters weekends and NOT holidays.** The stream and live
    capture timers deliberately push that decision down to their scripts,
    because only the market calendar can see Thanksgiving -- but
    ``capture_gallery_shots.py`` has no window or trading-day gate at all today.
    So on a holiday this fires and republishes the gallery from a flat tape.
    That is a stale-looking gallery, not a broken one, and the honest fix is a
    gate in the tool (``in_window``/``is_trading_day``), not a holiday table
    here that would be a second, driftable copy of the calendar.
    """
    at = slot_times("gallery_capture")["at"]
    timeout = _gallery_capture_timeout_seconds()

    svc = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - marketing gallery recapture
# No PartOf and no [Install]: the timer owns this. It is not a member of the
# fleet -- it is a oneshot that runs for a few minutes once a trading day.
# A crash-looping unit is retried this many times in this window, then left
# down and logged.
# NOTE: these belong in [Unit]; systemd moved them there in v229
# and silently ignores them in [Service].
StartLimitIntervalSec={START_LIMIT_INTERVAL_SEC}
StartLimitBurst={START_LIMIT_BURST}

[Service]
Type=oneshot
WorkingDirectory={_workdir()}
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/Chicago
# No leading '-': the file-wide rule, even though this tool reads no secret
# from it -- see _gallery_capture_units for why it is not exempted.
EnvironmentFile={_env_file()}
# Twenty-two shots plus the verification render, each with its own ceiling.
# WITHOUT THIS the oneshot inherits DefaultTimeoutStartSec (90s here) and is
# killed partway through, leaving some tiles refreshed and the rest not.
TimeoutStartSec={timeout}
ExecStart={_python()} tools/capture_gallery_shots.py
"""

    tmr = f"""[Unit]
Description=NeuralStrike {ENV_NAME} - marketing gallery recapture timer

[Timer]
# Derived from [slots.gallery_capture] in config/sessions.toml -- the unit and
# the config cannot disagree about when the gallery is refreshed.
# NOTE: Mon..Fri excludes weekends only. systemd has no market calendar, and unlike
# the stream and live-capture scripts this tool carries no trading-day gate, so
# a holiday firing republishes a flat tape. See _gallery_capture_units.
OnCalendar=Mon..Fri *-*-* {at.hour:02d}:{at.minute:02d}:00
# Deliberately NOT Persistent=true. A missed day is a day of slightly older
# pictures; a catch-up run would recapture at whatever hour the box came back
# and publish an overnight or pre-open render -- which is the exact thing the
# 09:00 slot exists to avoid.
Persistent=false

[Install]
WantedBy=timers.target
"""
    return {f"trading-{ENV_NAME}-gallery-capture.service": svc,
            f"trading-{ENV_NAME}-gallery-capture.timer": tmr}


def render_all():
    """``{unit filename: text}`` for this environment."""
    out = {unit_name(c): _service_text(c, p, s) for c, p, s in components()}
    out[target_name()] = _target_text()
    out.update(_backup_units())
    out.update(_stream_units())
    out.update(_live_capture_units())
    out.update(_gallery_capture_units())
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
