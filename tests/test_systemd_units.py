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
    # interpolation=None: '%' is systemd's SPECIFIER prefix (%i, %h) and is a
    # literal in values like CPUQuota=100%. configparser's default interpolation
    # reads it as its own syntax and raises, which would make this harness refuse
    # to parse a perfectly valid unit -- a test failure that says nothing about
    # the unit.
    cp = configparser.ConfigParser(strict=False, interpolation=None)
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
    """Every service reads its secrets from a FILE in this checkout.

    ⚠ Which file is not uniform, and the exception is load-bearing: the PUBLIC
    ``webgui_live`` unit loads ``.env.live`` and NOT ``.env`` -- see
    ``test_the_public_process_does_not_inherit_the_stacks_secrets``. Asserted as
    "some checkout env file" here so this test keeps saying the thing it means
    (secrets are not inline) rather than silently becoming a second, weaker copy
    of the split test below."""
    live = units.unit_name("webgui_live")
    for name, text in units.render_all().items():
        if not name.endswith(".service"):
            continue
        want = ".env.live" if name == live else ".env"
        assert str(POSIX_ROOT / want) in _environment_files(text), name


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


# --- the public live screens: a PEER of the trading UI, not a child ----------
def test_the_live_screens_have_their_own_unit():
    """A separate unit is the point: a public traffic spike or a crash on the
    open origin must not take the trading UI down with it."""
    names = {c for c, _p, _s in units.components()}
    assert "webgui_live" in names


def test_the_live_unit_binds_the_live_port():
    import repo_paths
    port = next(p for c, p, _s in units.components() if c == "webgui_live")
    assert port == repo_paths.NICEGUI_LIVE_PORT


def test_the_live_unit_runs_the_live_entrypoint():
    script = next(s for c, _p, s in units.components() if c == "webgui_live")
    assert script == "webgui/live_main.py"


def test_the_live_unit_comes_up_and_down_with_the_stack():
    """WantedBy the target so a promote brings the public screens back, PartOf
    it so a stop takes them down. It is a member of the fleet -- unlike the
    stream, which the TIMER owns."""
    assert units.unit_name("webgui_live") in stack_services()


def test_nothing_in_the_stack_depends_on_the_live_screens(rendered):
    """The whole reason it is a second process. A dependency edge pointing AT
    the public origin would hand a public traffic spike a way into the trading
    UI's start-up -- which is exactly what the separate process buys, thrown
    away in one directive.

    ``Wants=`` is checked as well as ``Requires=``: a Wants on a crash-looping
    unit does not fail its dependent, but it does drag the restart storm into
    every ``systemctl start`` of the thing that wants it."""
    live = units.unit_name("webgui_live")
    for name, cp in rendered.items():
        if not name.endswith(".service") or name == live:
            continue
        for key in ("Requires", "Requisite", "BindsTo", "After", "Wants"):
            assert live not in cp["Unit"].get(key, ""), (name, key)


def test_the_live_unit_orders_itself_against_nothing_it_talks_to(rendered):
    """It reads Redis -- a SYSTEM unit, outside this target entirely -- and
    nothing else. No proxy call, no Schwab call, no service call. So no
    ``After=``, no ``Requires=`` and no readiness probe: an ordering against a
    component it never speaks to reads as a real dependency and is none, and
    the next person to touch this file would have to prove it was fake."""
    cp = rendered[units.unit_name("webgui_live")]
    assert "Requires" not in cp["Unit"]
    assert "After" not in cp["Unit"]
    assert "ExecStartPre" not in cp["Service"]


def test_the_live_redis_credential_never_lands_in_an_Environment_line():
    """``REDIS_LIVE_URL`` carries a password inside a URL, where the generic
    KEY/TOKEN/PASSWORD/SECRET smell test above cannot see it -- and
    ``systemctl show`` prints every ``Environment=`` to any local user with no
    privilege. So it is asserted as an ABSENCE, by name."""
    text = units.render_all()[units.unit_name("webgui_live")]
    for value in _directives(text, "Environment"):
        assert "REDIS_LIVE_URL" not in value.upper(), value


def test_the_public_process_does_not_inherit_the_stacks_secrets():
    """⚠ The one internet-facing, unauthenticated process in the fleet must not
    hold the fleet's strongest credential set.

    ``.env`` supplies ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN,
    PROXY_SHARED_SECRET, SMS_SMTP_APP_PASSWORD, DISCORD_WEBHOOK_URL,
    MEMURAI_PASSWORD and GAMMA_BRIEFING_WEBHOOK_URL. The public screens need two
    values. No current code path in that process reads the others -- this is not
    a bug fix, it is the difference between "an RCE in NiceGUI costs the public
    screens" and "an RCE in NiceGUI costs the Anthropic key and the Telegram
    bot".

    Asserted in BOTH directions. The presence half alone would stay green if
    someone added ``.env.live`` beside ``.env`` rather than instead of it, which
    is the shape a "make it work again" edit takes."""
    live = units.render_all()[units.unit_name("webgui_live")]
    files = _environment_files(live)
    assert str(POSIX_ROOT / ".env.live") in files
    assert str(POSIX_ROOT / ".env") not in files, (
        "the public process still loads the stack's own .env")


def test_no_other_unit_loads_the_public_processs_env_file():
    """The converse, and the reason it is worth pinning separately: the split
    only bounds the blast radius if it stays a split. A second unit picking up
    ``.env.live`` would not break anything visible -- it would just quietly make
    the public credential a stack-wide one again, from the other end."""
    live = units.unit_name("webgui_live")
    for name, text in units.render_all().items():
        if not name.endswith(".service") or name == live:
            continue
        assert str(POSIX_ROOT / ".env.live") not in _environment_files(text), name


# --- the public process's memory cap ----------------------------------------
def _bytes(value):
    """A systemd memory value ("768M", "1G", "1073741824") as an int.

    systemd's suffixes are BINARY: K/M/G are 1024-based (KiB/MiB/GiB), which is
    the whole reason this is a helper and not an eyeballed comparison."""
    units_ = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    return (int(value[:-1]) * units_[value[-1].upper()]
            if value[-1].upper() in units_ else int(value))


def test_the_public_unit_caps_its_memory(rendered):
    """⚠ The one internet-facing, unauthenticated, UNTHROTTLED process.

    There is no rate limit -- Caddy's needs an xcaddy build and none is in
    place -- and a measured 619 KB of retained NiceGUI `Client` per plain
    anonymous GET, pruned only after ~70 s. This does not stop a flood; it
    decides WHO DIES in one: the public screens alone, restarted by
    Restart=on-failure, rather than the box's OOM killer choosing among the six
    services, the private trading UI and Redis."""
    svc = rendered[units.unit_name("webgui_live")]["Service"]
    assert svc["MemoryMax"] == units.LIVE_MEMORY_MAX
    assert svc["MemoryHigh"] == units.LIVE_MEMORY_HIGH


def test_the_memory_cap_lives_in_the_Service_section():
    """The sibling trap this file already carries for StartLimit* runs the other
    way: those belong in [Unit] and are silently ignored in [Service]; these
    belong in [Service] and are a parse error in [Unit]. Getting either wrong
    produces a unit that reads as configured."""
    text = units.render_all()[units.unit_name("webgui_live")]
    unit_part, service_part = text.split("[Service]", 1)
    for key in ("MemoryMax", "MemoryHigh"):
        assert f"{key}=" in service_part, key
        assert f"{key}=" not in unit_part, f"{key} is in [Unit], where it is invalid"


def test_the_throttle_sits_below_the_kill():
    """MemoryHigh must THROTTLE before MemoryMax KILLS. Set equal or inverted it
    is decoration: the process would be killed with no reclaim pressure first,
    which is the signal the pair exists to produce."""
    high = _bytes(units.LIVE_MEMORY_HIGH)
    cap = _bytes(units.LIVE_MEMORY_MAX)
    assert high < cap, (units.LIVE_MEMORY_HIGH, units.LIVE_MEMORY_MAX)
    assert cap - high >= 128 * 1024 ** 2, (
        "the gap is too narrow for a burst to drain -- the process would "
        "oscillate on the edge of the cap instead of throttling")


def test_the_cap_is_generous_enough_for_normal_use_and_tight_enough_to_matter():
    """Both bounds, because either alone is satisfiable by an absurd number.

    The floor: the whole nine-unit stack is budgeted at ~1.2 GB of process
    memory, so a cap under 512 MiB would be inside a single NiceGUI process's
    plausible working set. The ceiling: the host's minimum is 8 GB and the
    trading stack plus the page cache for the 1.52 GB gex_history.db have to
    survive whatever the public origin does."""
    cap = _bytes(units.LIVE_MEMORY_MAX)
    assert 512 * 1024 ** 2 <= cap <= 2 * 1024 ** 3, units.LIVE_MEMORY_MAX


def test_no_other_unit_carries_a_memory_cap():
    """⚠ NOT a drive-by. The other units are not internet-facing, and a wrong
    value on one of them kills the trading stack -- the failure this cap exists
    to prevent, moved onto the processes that matter most."""
    live = units.unit_name("webgui_live")
    for name, text in units.render_all().items():
        if name == live:
            continue
        for key in ("MemoryMax=", "MemoryHigh=", "MemoryLimit=", "MemorySwapMax="):
            assert key not in text, (name, key)


def test_the_public_env_file_must_exist_for_the_unit_to_start():
    """No leading '-'. Covered by the file-wide rule above, and pinned again
    here because the reasoning is stronger for this file than for the others: a
    '-' would start the unit with no ``REDIS_LIVE_URL``, which
    ``live_main.require_acl_url`` refuses to serve prod in anyway. The dash
    cannot buy a running process -- only a vaguer error."""
    text = units.render_all()[units.unit_name("webgui_live")]
    assert f"EnvironmentFile={POSIX_ROOT / '.env.live'}" in text
    assert f"EnvironmentFile=-{POSIX_ROOT / '.env.live'}" not in text


# --- the thumbnail capture, a oneshot the TIMER owns -------------------------
def test_the_live_capture_units_are_generated():
    """The public grid is fourteen <img> tags. Without this timer they are
    fourteen 404s on the first day and fourteen stale pictures after that --
    and nothing about the site or the live process looks broken while it
    happens."""
    all_units = units.render_all()
    assert f"trading-{ENV_NAME}-live-capture.service" in all_units
    assert f"trading-{ENV_NAME}-live-capture.timer" in all_units


def test_the_capture_runs_every_fifteen_minutes_without_catching_up(rendered):
    """A missed capture is worthless later -- it would put the market as it was
    during the downtime onto a page whose whole job is to be current -- so this
    is the deliberate opposite of the backup timer's Persistent=true."""
    tmr = rendered[f"trading-{ENV_NAME}-live-capture.timer"]
    assert tmr["Timer"]["OnCalendar"] == "*:0/15"
    assert tmr["Timer"].get("Persistent", "false").lower() != "true"
    assert tmr["Install"]["WantedBy"] == "timers.target"


def test_the_capture_timer_does_not_filter_the_days_the_script_gates_on():
    """The division of labour the stream timer already uses: systemd has no
    market calendar, so the gate that CAN see a holiday is the one that decides.
    A Mon..Fri here would be a second, driftable copy of half of it."""
    text = units.render_all()[f"trading-{ENV_NAME}-live-capture.timer"]
    schedule = [line.split("=", 1)[1] for line in text.splitlines()
                if line.startswith("OnCalendar=")]
    assert schedule, "the capture timer has no OnCalendar at all"
    for line in schedule:
        assert "Mon" not in line and "Sat" not in line, line


def test_the_capture_is_a_oneshot_that_does_not_retry(rendered):
    """A oneshot that fails should be VISIBLE and then wait for the next
    quarter hour. The two failures worth telling apart are a host with no
    browser, which retrying cannot fix, and a live process that is down, which
    the next firing picks up on its own. The script already declines to fail for
    the ordinary reasons -- outside the window it exits 0, and one unreachable
    screen is logged rather than raised."""
    svc = rendered[f"trading-{ENV_NAME}-live-capture.service"]
    assert svc["Service"]["Type"] == "oneshot"
    assert "Restart" not in svc["Service"]
    # The storm cap still belongs in [Unit]; systemd silently ignores it in
    # [Service], which is how a cap can look configured and not exist.
    assert int(svc["Unit"]["StartLimitBurst"]) > 0
    assert "StartLimitBurst" not in svc["Service"]


def test_the_capture_is_not_a_member_of_the_fleet(rendered):
    """PartOf/WantedBy would make `systemctl start target` fire a capture at
    whatever hour someone promotes, and a stack stop try to stop a job that
    runs for a minute every quarter hour."""
    svc = rendered[f"trading-{ENV_NAME}-live-capture.service"]
    assert "PartOf" not in svc["Unit"]
    assert "Install" not in svc
    assert f"trading-{ENV_NAME}-live-capture.service" not in stack_services()


def test_the_capture_has_a_timeout_derived_from_its_own_budget(rendered):
    """A oneshot inherits DefaultTimeoutStartSec (90s on this host) and the run
    photographs fourteen screens, so without this systemd would kill it partway:
    some tiles fresh, the rest stale, and nothing on the page to say which.

    Derived from the script's per-screen ceiling times the number of published
    screens, so adding a screen cannot silently reintroduce the truncation.
    """
    from tools import capture_live_shots as capture

    svc = rendered[f"trading-{ENV_NAME}-live-capture.service"]
    timeout = int(svc["Service"]["TimeoutStartSec"])
    assert timeout >= capture.SCREEN_TIMEOUT_SEC * len(capture.targets())


def test_the_capture_runs_the_script_and_not_a_shell_wrapper(rendered):
    svc = rendered[f"trading-{ENV_NAME}-live-capture.service"]
    assert svc["Service"]["ExecStart"].endswith("tools/capture_live_shots.py")
    assert str(POSIX_ROOT) in svc["Service"]["ExecStart"]


# --- the gallery recapture, a once-a-day oneshot the TIMER owns --------------
#
# ⚠ Unlike every other [slots] entry in config/sessions.toml, this one is read
# by SYSTEMD, not by a service scheduler: the four existing groups are resolved
# at module import by options_svc/scheduler.py and sentiment_svc/scheduler.py,
# and this one is resolved here, at unit-GENERATION time. The slot is still the
# right home for the time -- it is a named clock mark that fires once per
# trading day, which is exactly what [slots] models -- but the consequence is
# that moving it needs `generate_units --install` + `daemon-reload`, where
# moving an analyze slot only needs a service restart.

def test_the_gallery_capture_units_are_generated():
    """The App gallery on neuralstrike.co is twenty-two <img> tags whose files
    are written by tools/capture_gallery_shots.py. Without a timer they are
    whatever the last hand-run left behind, and nothing about the site looks
    wrong while they age."""
    all_units = units.render_all()
    assert f"trading-{ENV_NAME}-gallery-capture.service" in all_units
    assert f"trading-{ENV_NAME}-gallery-capture.timer" in all_units


def test_the_gallery_capture_fires_once_a_day_at_the_configured_slot():
    """Derived from [slots.gallery_capture], never typed: the unit and the
    config cannot disagree about when the gallery is refreshed.

    Exactly ONE OnCalendar. A second line is a second full recapture -- twenty
    minutes of Chrome against the live trading UI -- for no second picture."""
    from shared import market_calendar as mc
    at = mc.slot_times("gallery_capture")["at"]
    text = units.render_all()[f"trading-{ENV_NAME}-gallery-capture.timer"]
    schedule = _directives(text, "OnCalendar")
    assert schedule == [f"Mon..Fri *-*-* {at.hour:02d}:{at.minute:02d}:00"], schedule


def test_the_gallery_schedule_follows_the_slot_rather_than_a_literal(monkeypatch):
    """Mutate the slot and the unit must move. A hardcoded 09:00 passes the
    test above and fails this one, which is the whole difference between a
    derived value and a copy of one."""
    import datetime as _dt

    monkeypatch.setattr(units, "slot_times",
                        lambda name: {"at": _dt.time(13, 37)})
    text = units.render_all()[f"trading-{ENV_NAME}-gallery-capture.timer"]
    assert _directives(text, "OnCalendar") == ["Mon..Fri *-*-* 13:37:00"]


def test_the_gallery_capture_does_not_catch_up_after_downtime(rendered):
    """Persistent=true would recapture at whatever hour the box came back --
    publishing an overnight or pre-open render over the gallery, which is the
    exact thing the 09:00 CT slot exists to avoid. A missed day is a day of
    slightly older pictures; a caught-up one is twenty-two wrong ones."""
    tmr = rendered[f"trading-{ENV_NAME}-gallery-capture.timer"]
    assert tmr["Timer"].get("Persistent", "false").lower() != "true"
    assert tmr["Install"]["WantedBy"] == "timers.target"


def test_the_gallery_capture_is_a_oneshot_that_does_not_retry(rendered):
    """The tool exits non-zero when the app is unreachable or the session is
    refused, and publishes NOTHING in that case -- so a failure leaves the old
    gallery intact and shows up in `systemctl --user --failed`. A Restart= would
    turn a down app into a retry loop against it, which fixes nothing and hides
    the failure by eventually succeeding at an unknown hour."""
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert svc["Service"]["Type"] == "oneshot"
    assert "Restart" not in svc["Service"]


def test_the_gallery_storm_cap_is_in_the_unit_section(rendered):
    """⚠ systemd moved StartLimitIntervalSec/StartLimitBurst to [Unit] in v229
    and SILENTLY IGNORES them in [Service] -- so a cap there looks configured
    and does not exist."""
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert int(svc["Unit"]["StartLimitBurst"]) > 0
    assert int(svc["Unit"]["StartLimitIntervalSec"]) > 0
    assert "StartLimitBurst" not in svc["Service"]
    assert "StartLimitIntervalSec" not in svc["Service"]


def test_the_gallery_capture_is_not_a_member_of_the_fleet(rendered):
    """PartOf/WantedBy would fire a twenty-minute Chrome run at whatever hour
    someone promotes, and make a stack stop try to stop a job that is not
    running."""
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert "PartOf" not in svc["Unit"]
    assert "Install" not in svc
    assert f"trading-{ENV_NAME}-gallery-capture.service" not in stack_services()


def test_the_gallery_capture_orders_itself_against_nothing(rendered):
    """It photographs the private webgui on loopback, so an After= reads like a
    real dependency -- and would be fake. Ordering only decides BOOT sequence,
    and this unit is never started at boot: the timer starts it mid-session,
    hours after everything is up. A Requires= would be worse than useless: it
    would let a capture drag the trading UI around."""
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert "After" not in svc["Unit"]
    assert "Requires" not in svc["Unit"]
    assert "Wants" not in svc["Unit"]


def test_the_gallery_capture_has_a_timeout_derived_from_the_shot_count(rendered):
    """A oneshot inherits DefaultTimeoutStartSec (90s) and this run renders
    twenty-two pages plus a verification render, so without a timeout systemd
    kills it partway: some tiles refreshed, the rest not, and nothing on the
    page to say which."""
    from tools import capture_gallery_shots as capture

    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    timeout = int(svc["Service"]["TimeoutStartSec"])
    assert timeout > capture.SHOT_TIMEOUT_SEC * len(capture.targets())
    # ⚠ The bound above pins the HELPER only. Without this line the rendered
    # unit could carry a typed literal that happens to satisfy it -- measured:
    # hardcoding TimeoutStartSec=1440 into the template passed the whole file.
    assert timeout == units._gallery_capture_timeout_seconds()


def test_the_gallery_units_state_shot_counts_they_read_rather_than_type():
    """The unit ships an operator-facing comment naming how many renders it
    budgets for and how many are skipped. The first draft TYPED those and was
    already wrong -- it said twenty-two renders happen where nineteen do, while
    the sessions.toml comment beside it said nineteen. Mutating either count
    must move the shipped text, which a typed one would not.
    """
    from tools import capture_gallery_shots as capture

    budgeted, skipped = units._gallery_capture_shot_counts()
    assert (budgeted, skipped) == (len(capture.targets()),
                                   len(capture.unreachable_shots()))
    # render_all(), not the `rendered` fixture: configparser drops comments,
    # and the comment IS the subject here.
    text = units.render_all()[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert f"all {budgeted} shots" in text
    assert f"{skipped} of those are skipped" in text


def test_the_gallery_timeout_moves_with_the_shot_count(monkeypatch):
    """The discriminating half: a literal satisfies the bound above on the day
    it is written and stops satisfying it the first time a shot is added."""
    from tools import capture_gallery_shots as capture

    real = units._gallery_capture_timeout_seconds()
    grown = [("u", "p")] * (len(capture.targets()) + 7)
    monkeypatch.setattr(capture, "targets", lambda: grown)
    assert units._gallery_capture_timeout_seconds() > real


def test_the_gallery_capture_never_lands_on_a_live_capture_run():
    """The quarter hour is spoken for, so this job does not sit on it.

    live-capture's timer is OnCalendar=*:0/15 and fires all day -- since
    2026-09-08 it stands down in under a second outside its 15:25-15:50 window,
    so at 09:00 the overlap would cost almost nothing. This is a courtesy rather
    than the load fix (that is CPUQuota, next test), and it also keeps the run
    off the GEX collector's own :00 minute boundary.

    Derived from LIVE_CAPTURE_INTERVAL_MIN, never from a restated fifteen: if the
    live cadence changes, this constraint has to move with it, and a test that
    typed the interval would keep passing while the collision came back.
    """
    from shared import market_calendar as mc

    at = mc.slot_times("gallery_capture")["at"]
    minutes = at.hour * 60 + at.minute
    assert minutes % units.LIVE_CAPTURE_INTERVAL_MIN != 0, (
        f"{at} coincides with a live-capture run "
        f"(OnCalendar=*:0/{units.LIVE_CAPTURE_INTERVAL_MIN})")


def test_the_gallery_capture_is_cpu_contained(rendered):
    """⚠ THE LOAD FIX. The offset above is a courtesy; this is the control.

    Since live-capture moved post-close on 2026-09-08, this is the ONLY headless
    Chrome that runs during the session -- and the heavier of the two. It cannot
    follow live-capture out of the session: index option open interest zeroes
    after hours, so a post-close run photographs all-zero GEX grids. So the peak
    is bounded where it is, rather than relocated.
    ⚠ CPUQuota belongs in [Service]: cgroup resource control lives there, which
    is the exact inverse of the storm cap's [Unit] home, and carrying both traps
    in one file is why each gets its own test.
    """
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert svc["Service"]["CPUQuota"] == f"{units.GALLERY_CAPTURE_CPU_QUOTA_PCT}%"
    assert svc["Service"]["Nice"] == str(units.GALLERY_CAPTURE_NICE)
    assert "CPUQuota" not in svc["Unit"]


def test_the_gallery_capture_carries_no_memory_cap(rendered):
    """⚠ Only webgui_live carries one, for a documented reason. The file-wide
    test_no_other_unit_carries_a_memory_cap covers this too; pinned locally so
    the intent is visible at the unit that could most plausibly attract one (it
    runs Chrome)."""
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    for key in ("MemoryMax", "MemoryHigh", "MemoryLimit", "MemorySwapMax"):
        assert key not in svc["Service"], key


def test_the_gallery_capture_runs_the_script_and_not_a_shell_wrapper(rendered):
    svc = rendered[f"trading-{ENV_NAME}-gallery-capture.service"]
    assert svc["Service"]["ExecStart"].endswith("tools/capture_gallery_shots.py")
    assert str(POSIX_ROOT) in svc["Service"]["ExecStart"]


def test_the_gallery_slot_is_after_the_open_so_the_screens_carry_live_data():
    """The whole reason it is not at 08:00: a pre-open render publishes a
    gallery of blank panels and overnight numbers. Half an hour after the
    regular open is enough for the watcher tick and the first scans to have
    painted, and the bound is derived from [sessions.regular] rather than
    restating 08:30."""
    from shared import market_calendar as mc
    at = mc.slot_times("gallery_capture")["at"]
    open_, close = mc._session_bounds("regular")
    assert open_ < at < close, (open_, at, close)
    assert (at.hour * 60 + at.minute) - (open_.hour * 60 + open_.minute) >= 20


# --- arming, which is not the same thing as writing --------------------------
#
# A generated `.timer` that nothing enables is a FILE, not a schedule. That is
# not hypothetical: `--install` wrote trading-prod-flow-delta.timer correct and
# `disabled`, so it never fired, and the only symptom was a report directory
# that stopped gaining dates for eleven days. Every other timer on that host was
# armed by a human running `enable` once, unrecorded -- so a rebuilt box would
# have got the whole set, all disabled. These pin the arming to the generator.

def _fake_systemctl(fail_on=()):
    """Record `systemctl --user ...` calls; optionally fail matching ones."""
    calls = []

    def run(cmd):
        assert cmd[:2] == ["systemctl", "--user"], cmd
        rest = cmd[2:]
        calls.append(rest)
        for pat in fail_on:
            if pat in rest:
                return False, f"boom: {pat}"
        # `is-enabled --quiet` answering False is "not yet enabled"
        if rest and rest[0] == "is-enabled":
            return False, ""
        return True, ""

    run.calls = calls
    return run


def test_every_generated_timer_gets_armed():
    """The set is DERIVED from render_all(), so a timer added to the generator
    and forgotten cannot be written-but-disabled -- which is the whole failure."""
    run = _fake_systemctl()
    statuses = dict(units.activate(runner=run))
    enabled = {c[2] for c in run.calls if c[0] == "enable"}
    assert enabled == set(units.timer_units())
    assert enabled, "a stack with no timers would make this test vacuous"
    for t in units.timer_units():
        assert statuses[t] == "ENABLED", (t, statuses[t])


def test_the_flow_delta_timer_is_one_of_them():
    """Named explicitly because it is the one whose absence cost the reports."""
    assert f"trading-{ENV_NAME}-flow-delta.timer" in units.timer_units()


def test_arming_uses_enable_AND_now():
    """`enable` alone only writes the symlink -- the timer is not armed in the
    running manager until something starts it, so a promote would leave it
    inert until the next boot."""
    run = _fake_systemctl()
    units.activate(runner=run)
    for c in run.calls:
        if c[0] == "enable":
            assert "--now" in c, c


def test_the_daemon_reload_comes_before_any_enable():
    """`enable --now` asks the RUNNING manager to start a unit; for a newly
    generated timer it has not read the file yet and the --now half fails with
    'Unit not found' -- exactly the case this exists for."""
    run = _fake_systemctl()
    units.activate(runner=run)
    verbs = [c[0] for c in run.calls]
    assert verbs[0] == "daemon-reload"
    assert "daemon-reload" not in verbs[1:]


def test_a_failed_reload_does_not_report_timers_as_armed():
    run = _fake_systemctl(fail_on=("daemon-reload",))
    statuses = dict(units.activate(runner=run))
    assert statuses["daemon-reload"].startswith("FAILED")
    for t in units.timer_units():
        assert statuses[t] == "not attempted (daemon-reload failed)"
    assert not [c for c in run.calls if c[0] == "enable"]


def test_a_failed_enable_is_reported_never_swallowed():
    """The failure mode being fixed is silence, so a timer that could not be
    armed must not come back looking armed."""
    victim = units.timer_units()[0]
    run = _fake_systemctl(fail_on=(victim,))
    statuses = dict(units.activate(runner=run))
    assert statuses[victim].startswith("FAILED"), statuses[victim]
    assert "boom" in statuses[victim]


def test_an_already_armed_timer_is_reported_as_such_not_as_newly_enabled():
    """Arming runs on every promote and must be honest about changing nothing."""
    def run(cmd):
        rest = cmd[2:]
        return True, ""          # is-enabled succeeds => already enabled
    statuses = dict(units.activate(runner=run))
    for t in units.timer_units():
        assert statuses[t] == "already enabled", (t, statuses[t])


def test_dev_never_arms_a_schedule(monkeypatch):
    """Dev generates the same timers and must not run them: its stores are a
    disposable copy of prod's (so trading-dev-backup.timer is deliberately
    disabled), and stream/gallery/live-capture drive PUBLIC surfaces a second
    checkout must never publish to. IS_DEV is a by-value import, so it is
    patched on the module that consumed it."""
    monkeypatch.setattr(units, "IS_DEV", True)
    run = _fake_systemctl()
    statuses = dict(units.activate(runner=run))
    assert run.calls == [], "dev must not touch systemctl at all"
    for t in units.timer_units():
        assert statuses[t].startswith("skipped")


def test_a_dest_run_writes_without_arming(tmp_path, monkeypatch, capsys):
    """--dest is a dry run into another directory; arming would enable units
    systemd is not loading from there."""
    called = []
    monkeypatch.setattr(units, "activate", lambda *a, **k: called.append(1) or [])
    assert units.main(["--dest", str(tmp_path)]) == 0
    assert called == []
    assert "Not armed" in capsys.readouterr().out


def test_install_reports_a_failure_to_arm_with_a_nonzero_exit(tmp_path, monkeypatch, capsys):
    """A promote runs under `set -e`. If arming fails, the run must not print
    success over a stack whose schedules are files."""
    monkeypatch.setattr(units, "install", lambda dest=None: [])
    monkeypatch.setattr(units, "activate",
                        lambda *a, **k: [("trading-x.timer", "FAILED: boom")])
    assert units.main(["--install"]) == 1
    assert "NOT armed" in capsys.readouterr().out
