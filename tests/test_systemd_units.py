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
def test_every_service_pins_central_time(rendered):
    """Belt-and-braces against a host whose zone is wrong. repo_paths' boot
    assertion is the other half; this makes the unit itself state it."""
    for name, cp in rendered.items():
        if name.endswith(".service"):
            assert "TZ=America/Chicago" in cp["Service"]["Environment"], name


# --- secrets ----------------------------------------------------------------
def test_secrets_come_from_an_EnvironmentFile(rendered):
    for name, cp in rendered.items():
        if name.endswith(".service"):
            assert cp["Service"]["EnvironmentFile"] == str(POSIX_ROOT / ".env"), name


def test_the_environment_file_has_no_leading_dash(rendered):
    """`EnvironmentFile=-/path` starts the unit when the file is missing, and the
    stack comes up MUTE: allow_claude falls into its no-API-key path, the
    notification channels no-op, the bus fails to authenticate. A unit that
    refuses to start is recoverable in seconds; one running without its secrets
    looks healthy for a day."""
    for name, cp in rendered.items():
        if name.endswith(".service"):
            assert not cp["Service"]["EnvironmentFile"].startswith("-"), name


def test_no_secret_is_ever_in_an_Environment_line(rendered):
    """`systemctl show <unit>` prints Environment= to ANY local user, with no
    privilege. EnvironmentFile= shows only the path."""
    for name, cp in rendered.items():
        if not name.endswith(".service"):
            continue
        env = cp["Service"]["Environment"].upper()
        for smell in ("KEY", "TOKEN", "PASSWORD", "SECRET"):
            assert smell not in env, (name, smell)


# --- paths ------------------------------------------------------------------
def test_units_run_from_this_checkout_with_its_own_venv(rendered):
    """Derived from REPO_ROOT, never a literal home directory. The plan first
    hardcoded /home/john; the account turned out to be `administrator`."""
    for name, cp in rendered.items():
        if not name.endswith(".service"):
            continue
        assert cp["Service"]["WorkingDirectory"] == str(POSIX_ROOT), name
        assert cp["Service"]["ExecStart"].startswith(
            str(POSIX_ROOT / ".venv" / "bin" / "python")), name


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
        pre = cp["Service"]["ExecStartPre"]
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
    tmr = rendered[f"trading-{ENV_NAME}-backup.timer"]
    assert tmr["Timer"]["Persistent"].lower() == "true"
    assert tmr["Timer"]["OnCalendar"].endswith("20:00:00")
    assert tmr["Install"]["WantedBy"] == "timers.target"


def test_the_backup_runs_after_everything_that_writes():
    """20:00 CT is after collection (15:20), the momentum cascade (16:20) and the
    calibration rebuild (16:30). Backing up mid-cascade captures a half-written
    night that looks complete. Asserted as an INEQUALITY against the last writer,
    not as the literal time, so moving the backup an hour does not fail this and
    moving it before the cascade does."""
    tmr = _parse(units.render_all()[f"trading-{ENV_NAME}-backup.timer"])
    hh, mm, _ = tmr["Timer"]["OnCalendar"].split()[-1].split(":")
    assert int(hh) * 60 + int(mm) > 16 * 60 + 30


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
