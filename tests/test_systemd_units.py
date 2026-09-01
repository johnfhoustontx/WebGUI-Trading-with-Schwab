"""The systemd user units that replace the twelve .bat launchers.

These are DERIVED from repo_paths, not written by hand, for the same reason
ports live in one config file: a second list of component names, ports or paths
is a mirror free to drift. The generator is the single source, and these tests
pin the invariants that a hand-edited unit would quietly break.

⚠ The sharpest test here is `test_unit_names_match_what_the_status_page_restarts`.
A regrouping or rename that leaves the Status page's Restart button naming a unit
that does not exist is invisible to every other test in the repo -- the button
would simply report an error at runtime, in prod, when someone needs it.
"""
import configparser
import pathlib

import pytest

from repo_paths import (ENV_NAME, NICEGUI_PORT, OWNS_PROXY, PROXY_PORT,
                        REPO_ROOT, SERVICE_PORTS)

# NOT importorskip. This module is not optional -- it is what replaces the
# launchers. An importorskip here would turn "the generator is missing" into a
# silent skip, which is the failure mode this repo has been bitten by before
# (a skipped test reads as a passing suite).
from deploy.systemd import generate_units as units


POSIX_ROOT = pathlib.PurePosixPath("/home/administrator/prod")


@pytest.fixture(autouse=True)
def _posix_root(monkeypatch):
    r"""Pin REPO_ROOT to a POSIX path for every test in this file.

    A unit file is a Linux artifact unconditionally, so its shape must be
    asserted as Linux regardless of the host running the suite. Without this the
    path tests would assert a `D:\...` WorkingDirectory on Windows and a real
    one on the VPS -- the same test, two meanings, decided by machine state."""
    monkeypatch.setattr(units, "REPO_ROOT", POSIX_ROOT)


def _parse(text):
    # strict=False: systemd permits repeated keys (two Environment= lines here);
    # configparser rejects them by default.
    cp = configparser.ConfigParser(strict=False)
    cp.read_string(text)
    return cp


@pytest.fixture
def rendered():
    """Every unit as parsed ini: {unit_name: ConfigParser}."""
    return {n: _parse(t) for n, t in units.render_all().items()}


def stack_services():
    """The units the TARGET pulls up -- the fleet, and nothing else.

    Auxiliary units (the nightly backup) are deliberately excluded: they are not
    PartOf the target, carry no restart policy because they are oneshots, and
    must not stop when the stack does. The moment you most want yesterday's
    backup is the moment the stack is down.

    Derived from the target's own Wants= rather than a second list here, so a
    unit added to the fleet is covered by every fleet test automatically."""
    all_units = units.render_all()
    target = all_units[f"trading-{ENV_NAME}.target"]
    return {w for line in target.splitlines() if line.startswith("Wants=")
            for w in line.split("=", 1)[1].split()}


# --- naming: the seam with the Status page ----------------------------------
def test_unit_names_match_what_the_status_page_restarts():
    """restart_spec builds `trading-{ENV_NAME}-{name}`; the generator must emit
    exactly those units. This is the drift the old WMI tests could not see."""
    import sys
    sys.path.insert(0, str(REPO_ROOT / "webgui"))
    from pages import status

    # `.service` is appended here on purpose. restart_command emits
    # `systemctl restart trading-prod-options_svc` with no suffix, which is
    # correct -- systemctl resolves a bare name to .service. The generator names
    # the FILE, which must carry it. Both are right; this test bridges the two
    # spellings explicitly rather than letting the difference hide a real
    # mismatch in the component names themselves.
    wanted = set()
    for target in status.component_targets():
        spec = status.restart_spec(target)
        if spec is not None:
            wanted.add(f"trading-{ENV_NAME}-{spec['name']}.service")

    emitted = stack_services()
    assert emitted == wanted, f"missing={wanted - emitted} extra={emitted - wanted}"


def test_every_unit_is_scoped_to_this_environment():
    for name in units.render_all():
        assert name.startswith(f"trading-{ENV_NAME}"), name


# --- the target -------------------------------------------------------------
def test_the_target_wants_every_service_and_nothing_else():
    all_units = units.render_all()
    target = all_units[f"trading-{ENV_NAME}.target"]
    wanted = stack_services()
    # Every wanted unit exists, and the set is exactly the fleet (the backup
    # service is emitted too, and must NOT be pulled up by the target).
    assert wanted <= set(all_units)
    assert all(n.endswith(".service") for n in wanted)
    assert f"trading-{ENV_NAME}-backup.service" not in wanted


def test_stopping_the_target_stops_the_services(rendered):
    """PartOf= is what makes the Terminate page's single target stop work."""
    for name in stack_services():
        assert rendered[name]["Unit"]["PartOf"] == f"trading-{ENV_NAME}.target"


# --- restart policy ---------------------------------------------------------
def test_every_service_restarts_on_failure_with_a_storm_cap(rendered):
    """Replaces tools/watchdog.py, including its MAX_RESTARTS storm cap: a
    crash-looping component is retried a few times then LEFT DOWN and logged,
    rather than thrashed forever."""
    for name in stack_services():
        cp = rendered[name]
        assert cp["Service"]["Restart"] == "on-failure", name
        assert int(cp["Service"]["RestartSec"]) > 0, name
        assert int(cp["Unit"]["StartLimitBurst"]) > 0, name
        assert int(cp["Unit"]["StartLimitIntervalSec"]) > 0, name


def test_start_limit_lives_in_the_Unit_section_not_Service(rendered):
    """systemd moved these to [Unit] in v229. In [Service] they are silently
    ignored, so the storm cap would look configured and not exist."""
    for name, cp in rendered.items():
        if name.endswith(".service"):
            assert "StartLimitBurst" not in cp["Service"], name


# --- the timezone, which is load-bearing ------------------------------------
def test_every_service_pins_central_time():
    """Belt-and-braces against a host whose zone is wrong. repo_paths' boot
    assertion is the other half; this makes the unit itself state it.

    Read from every raw `Environment=` line rather than the parsed value -- see
    `_directives`. The units put TZ last today, so the parsed read happened to
    work, and would have kept working right up until it didn't."""
    for name, text in units.render_all().items():
        if name.endswith(".service"):
            assert "TZ=America/Chicago" in _directives(text, "Environment"), name


# --- secrets ----------------------------------------------------------------
def _directives(text, key):
    """EVERY raw value for `key` in a unit, in file order.

    ⚠ Not from the parsed ini, and that distinction is the whole point.
    systemd lets several of these keys REPEAT and accumulate -- `Environment`,
    `EnvironmentFile`, `ExecStartPre` and `OnCalendar` all do -- while
    configparser (even at strict=False) collapses repeats to the LAST
    occurrence. A test reading the parsed value is therefore blind to anything
    placed on an earlier line.

    That is not hypothetical. Every unit happens to emit
    `Environment=TZ=America/Chicago` last, so a secret added on a FIRST
    `Environment=` line sailed straight through the test whose entire purpose
    was to catch it; and an extra `OnCalendar=Sat ...` ahead of the weekday line
    would have left the backup's no-weekends test green while it ran Saturdays.

    Matches `key=` exactly, so `Environment` does not also collect
    `EnvironmentFile` lines.
    """
    want = key + "="
    return [line.split("=", 1)[1] for line in text.splitlines()
            if line.startswith(want)]


def _environment_files(text):
    """EVERY `EnvironmentFile=` value in a unit -- see `_directives`."""
    return _directives(text, "EnvironmentFile")


def test_secrets_come_from_an_EnvironmentFile():
    for name, text in units.render_all().items():
        if name.endswith(".service"):
            assert str(POSIX_ROOT / ".env") in _environment_files(text), name


def test_the_environment_file_has_no_leading_dash():
    """`EnvironmentFile=-/path` starts the unit when the file is missing, and the
    stack comes up MUTE: allow_claude falls into its no-API-key path, the
    notification channels no-op, the bus fails to authenticate. A unit that
    refuses to start is recoverable in seconds; one running without its secrets
    looks healthy for a day. The stream is the same shape one level out: a
    missing key file means ffmpeg encodes nine hours into nowhere.

    Checked over ALL of a unit's environment files, not just the last."""
    for name, text in units.render_all().items():
        if not name.endswith(".service"):
            continue
        for value in _environment_files(text):
            assert not value.startswith("-"), (name, value)


def test_no_secret_is_ever_in_an_Environment_line():
    """`systemctl show <unit>` prints Environment= to ANY local user, with no
    privilege. EnvironmentFile= shows only the path.

    ⚠ Checked over EVERY `Environment=` line. Reading the parsed value made this
    security test blind to a secret on any line but the last -- and since every
    unit ends its block with `Environment=TZ=America/Chicago`, that is every
    line a secret would realistically be added to."""
    for name, text in units.render_all().items():
        if not name.endswith(".service"):
            continue
        for value in _directives(text, "Environment"):
            for smell in ("KEY", "TOKEN", "PASSWORD", "SECRET"):
                assert smell not in value.upper(), (name, smell, value)


# --- paths ------------------------------------------------------------------
def test_units_run_from_this_checkout_with_its_own_venv(rendered):
    """Derived from REPO_ROOT, never a literal home directory. The plan first
    hardcoded /home/john; the account turned out to be `administrator`.

    ⚠ Widened from "every ExecStart starts with the venv python" when the wall
    stream arrived, because that unit runs a SHELL SCRIPT
    (tools/stream_wall.sh -- it needs Xvfb, Chrome and ffmpeg around the Python,
    not just Python). The invariant that actually mattered was never "the first
    word is python": it was that a unit runs THIS checkout's code with THIS
    checkout's interpreter, and can never be satisfied by whatever happens to be
    on PATH. Both halves are still asserted -- the executable lives inside the
    checkout, and any unit that names an interpreter names the checkout's venv.
    The script resolves `$ROOT/.venv/bin/python` from its own location for the
    same reason."""
    venv_python = str(POSIX_ROOT / ".venv" / "bin" / "python")
    for name, cp in rendered.items():
        if not name.endswith(".service"):
            continue
        exec_start = cp["Service"]["ExecStart"]
        assert cp["Service"]["WorkingDirectory"] == str(POSIX_ROOT), name
        assert exec_start.startswith(str(POSIX_ROOT) + "/"), name
        assert "python" not in exec_start or exec_start.startswith(venv_python), name


def test_no_unit_carries_a_windows_path_or_launcher():
    for name, text in units.render_all().items():
        low = text.lower()
        for banned in (".bat", "\\", "cmd /c", "pythonw", "taskkill", "powershell"):
            assert banned not in low, (name, banned)


# --- dependency shape -------------------------------------------------------
def test_services_wait_for_the_proxy_to_ANSWER_not_merely_to_bind(rendered):
    """After= orders process START and says nothing about readiness. A dead
    accept loop stays bound and passes a TCP connect -- which is exactly how a
    promote once left prod with no UI. tools/wait_http.py is the HTTP probe."""
    if not OWNS_PROXY:
        pytest.skip("no proxy unit in an environment that borrows one")
    for key in SERVICE_PORTS:
        cp = rendered[f"trading-{ENV_NAME}-{key}_svc.service"]
        # Every ExecStartPre=, not the collapsed last one: systemd runs them
        # all in order, so a second one added later would hide this probe from
        # the parsed read while leaving it in the unit -- or worse, replace it
        # in the test's eyes while the real ordering guarantee moved.
        pre = " ".join(_directives(
            units.render_all()[f"trading-{ENV_NAME}-{key}_svc.service"],
            "ExecStartPre"))
        assert "wait_http.py" in pre
        assert str(PROXY_PORT) in pre
        assert cp["Unit"]["Requires"] == f"trading-{ENV_NAME}-proxy.service"


def test_the_webgui_is_ordered_after_the_proxy_but_does_not_require_it(rendered):
    """restart_spec already encodes this as wait_port 0: the GUI renders a
    proxy-down banner and is fully usable without one. Ordering it after the
    proxy just means first paint usually has data; a dead proxy must not keep
    the UI down."""
    cp = rendered[f"trading-{ENV_NAME}-webgui.service"]
    assert "Requires" not in cp["Unit"]
    assert "ExecStartPre" not in cp["Service"]
    if OWNS_PROXY:
        assert cp["Unit"]["After"] == f"trading-{ENV_NAME}-proxy.service"


def test_a_borrowed_proxy_means_no_proxy_unit(monkeypatch):
    """Ownership is encoded in which units EXIST, not in a kill-list filter.
    Dev borrows prod's proxy -- one rotating OAuth refresh token, so there can
    be only one -- so dev's target must not pull a second one up."""
    monkeypatch.setattr(units, "OWNS_PROXY", False)
    emitted = units.render_all()
    assert not any("proxy" in n for n in emitted)
    for key in SERVICE_PORTS:
        cp = _parse(emitted[f"trading-{ENV_NAME}-{key}_svc.service"])
        assert "Requires" not in cp["Unit"]
        assert "ExecStartPre" not in cp["Service"]


def test_boot_starts_the_stack(rendered):
    all_units = units.render_all()
    cp = _parse(all_units[f"trading-{ENV_NAME}.target"])
    assert cp["Install"]["WantedBy"] == "default.target"


def test_units_are_pure_ascii():
    """Unit files are infrastructure read by a C daemon. systemd handles UTF-8,
    but nothing here needs it, and ASCII-by-construction removes a whole class
    of encoding question from a file you debug at 6am."""
    for name, text in units.render_all().items():
        text.encode("ascii")   # raises if not



# --- the nightly backup, which is NOT a fleet member -------------------------
def test_the_backup_is_a_timer_not_a_stack_service(rendered):
    """It must not be PartOf the target. A backup that stops when the stack
    stops is missing at exactly the moment it is wanted."""
    svc = rendered[f"trading-{ENV_NAME}-backup.service"]
    assert "PartOf" not in svc["Unit"]
    assert svc["Service"]["Type"] == "oneshot"


def test_the_backup_timer_catches_up_after_downtime(rendered):
    """Persistent=true runs a MISSED occurrence at next boot. Kept even though the
    VPS is always on: it costs nothing when the timer fires normally, and the box
    does reboot. A silently skipped night is the failure this job exists to
    prevent."""
    text = units.render_all()[f"trading-{ENV_NAME}-backup.timer"]
    tmr = rendered[f"trading-{ENV_NAME}-backup.timer"]
    assert tmr["Timer"]["Persistent"].lower() == "true"
    schedule = _directives(text, "OnCalendar")
    assert schedule and all(s.endswith("20:00:00") for s in schedule), schedule
    assert tmr["Install"]["WantedBy"] == "timers.target"


def test_the_backup_does_not_run_on_weekend_nights(rendered):
    """Nothing writes at the weekend, so a Sat/Sun run ships ~1.5 GB of bytes
    identical to Friday's -- encrypted, uploaded, and counted against
    KEEP_REMOTE, which is the real cost: three weekend runs would push the last
    three WEEKDAY archives off Drive and leave only copies of a closed market.

    Sunday evening reopens the futures session, but collection windows are
    market hours and Monday's 20:00 run captures anything written before it.

    ⚠ Persistent=true still applies. A Friday run missed because the box was
    down is executed at next boot even if that boot is a Saturday -- the day
    filter picks the schedule, not what a catch-up is allowed to do.
    """
    # EVERY OnCalendar=, because each one ADDS a trigger. The parsed value is
    # the last line only, so `OnCalendar=Sat ...` inserted above the weekday
    # line would leave this test green while the backup ran on Saturdays --
    # exactly the failure the docstring above is about.
    schedule = _directives(
        units.render_all()[f"trading-{ENV_NAME}-backup.timer"], "OnCalendar")
    assert schedule, "backup timer has no OnCalendar at all"
    for oncal in schedule:
        assert oncal.startswith("Mon..Fri "), (
            f"backup timer would fire at the weekend: OnCalendar={oncal!r}")


def test_the_backup_runs_after_everything_that_writes():
    """20:00 CT is after collection (15:20), the momentum cascade (16:20) and the
    calibration rebuild (16:30). Backing up mid-cascade captures a half-written
    night that looks complete. Asserted as an INEQUALITY against the last writer,
    not as the literal time, so moving the backup an hour does not fail this and
    moving it before the cascade does."""
    schedule = _directives(
        units.render_all()[f"trading-{ENV_NAME}-backup.timer"], "OnCalendar")
    assert schedule, "backup timer has no OnCalendar at all"
    for oncal in schedule:   # every trigger must clear the last writer
        hh, mm, _ = oncal.split()[-1].split(":")
        assert int(hh) * 60 + int(mm) > 16 * 60 + 30, oncal


def test_the_backup_has_a_timeout_long_enough_to_finish(rendered):
    """⚠ A oneshot inherits DefaultTimeoutStartSec, measured at 90s on this host.
    The backup encrypts ~1.5 GB and uploads it -- 6m18s measured -- so without an
    explicit TimeoutStartSec systemd SIGTERMs it mid-upload every night, leaving
    a partial object in Drive and a unit in `failed`.

    Asserted as a generous floor rather than the exact value: the point is that
    it comfortably exceeds a real run, not that it equals any particular number.
    """
    svc = rendered[f"trading-{ENV_NAME}-backup.service"]
    assert int(svc["Service"]["TimeoutStartSec"]) >= 1800


# --- the wall stream, which is a TIMER-owned member of the stack -------------
def test_stream_units_are_generated():
    units_ = units.render_all()
    assert f"trading-{ENV_NAME}-stream.service" in units_
    assert f"trading-{ENV_NAME}-stream.timer" in units_


def test_storm_cap_is_in_the_unit_section_not_the_service_section():
    """systemd moved StartLimit* to [Unit] in v229 and SILENTLY IGNORES them in
    [Service] -- the cap would look configured and not exist.

    Split on the section HEADER (a whole line), not the bare substring: the unit
    carries a comment saying these must not go in [Service], and a substring
    split cuts the file in the middle of that comment -- failing the test for
    documenting the very rule it checks."""
    svc = units.render_all()[f"trading-{ENV_NAME}-stream.service"]
    unit_section, service_section = svc.split("\n[Service]\n")
    assert "StartLimitBurst=" in unit_section
    assert "StartLimitBurst=" not in service_section


def test_stream_stops_with_the_stack_but_does_not_start_with_it():
    """PartOf so a stack stop takes it down; NOT WantedBy the target, because
    the timer owns when it runs -- otherwise `systemctl start target` would
    start a broadcast at any hour."""
    svc = units.render_all()[f"trading-{ENV_NAME}-stream.service"]
    assert f"PartOf={units.target_name()}" in svc
    assert f"WantedBy={units.target_name()}" not in svc


def test_the_target_does_not_pull_the_stream_up():
    """The other half of the asymmetry, asserted where it can actually be
    broken: PartOf is on the service, but a stray Wants= on the TARGET would
    start a broadcast on every `systemctl start trading-<env>.target` -- which
    is what a promote does, at whatever hour the promote happens."""
    assert f"trading-{ENV_NAME}-stream.service" not in stack_services()


def test_runtime_cap_matches_the_configured_window():
    """Derived, never typed: the unit and config cannot disagree about when the
    broadcast ends."""
    from shared import market_calendar as mc
    start, end = mc.window_bounds("stream")
    expected = (end.hour * 60 + end.minute - start.hour * 60 - start.minute) * 60
    svc = units.render_all()[f"trading-{ENV_NAME}-stream.service"]
    assert f"RuntimeMaxSec={expected}" in svc


def test_timer_fires_at_the_window_start_on_weekdays():
    from shared import market_calendar as mc
    start, _ = mc.window_bounds("stream")
    text = units.render_all()[f"trading-{ENV_NAME}-stream.timer"]
    schedule = _directives(text, "OnCalendar")
    # Exactly one: a second OnCalendar= is a second start, and a second start
    # inside the window is a second encoder pushing to the same RTMP key.
    assert schedule == [f"Mon..Fri *-*-* {start.hour:02d}:{start.minute:02d}:00"], schedule


def test_the_stream_key_file_must_exist_for_the_unit_to_start():
    """No leading '-': a missing key must fail the unit loudly rather than
    encode nine hours into nowhere."""
    svc = units.render_all()[f"trading-{ENV_NAME}-stream.service"]
    assert f"EnvironmentFile={units.STREAM_ENV_FILE}" in svc
    assert f"EnvironmentFile=-{units.STREAM_ENV_FILE}" not in svc


def test_the_stream_requires_the_webgui_and_does_not_duplicate_its_wait(rendered):
    """Unlike every other unit, which only orders itself AFTER the proxy because
    the UI degrades gracefully without it, a stream with no web GUI is nine
    hours of a connection-refused page on a public channel. It genuinely
    Requires= it.

    And it carries NO ExecStartPre: tools/stream_wall.sh already calls
    tools/wait_http.py itself, because it needs the port and the wall route out
    of Python anyway. A second probe in the unit would be a second copy of the
    timeout, free to disagree with the first."""
    cp = rendered[f"trading-{ENV_NAME}-stream.service"]
    webgui = units.unit_name("webgui")
    assert cp["Unit"]["Requires"] == webgui
    assert cp["Unit"]["After"] == webgui
    assert "ExecStartPre" not in cp["Service"]


def test_the_stream_timer_does_not_catch_up_after_downtime(rendered):
    """The deliberate opposite of the backup timer. A missed backup is still
    worth taking late; a missed BROADCAST WINDOW is gone. Persistent=true would
    run a missed 08:00 occurrence at the next boot -- starting a public stream
    at whatever time of day the box came back, which the script's own window
    gate would then stand down from anyway, one spawn later."""
    tmr = rendered[f"trading-{ENV_NAME}-stream.timer"]
    assert "Persistent" not in tmr["Timer"]
    assert tmr["Install"]["WantedBy"] == "timers.target"
