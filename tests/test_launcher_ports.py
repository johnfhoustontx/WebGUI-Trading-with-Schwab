"""The launchers' port literals and environment guards — see
docs/plans/2026-08-08-dev-prod-environments-design.md.

A .bat file cannot be unit-tested the way a function can, but two things about
these launchers CAN silently rot and are worth pinning.

**The port numbers.** Every process reads its own port from ``repo_paths``, so a
wrong literal in ``start_dev.bat`` misconfigures nothing — it misinforms, which
is worse in the one situation the launcher exists for. A tab titled "9214" that
is really 9314 sends you looking at the wrong service; a banner still naming
8500 sends you to PROD's web GUI while you debug dev. Deriving the expected
numbers from ``config/ports.toml`` plus the ``[dev]`` profile means changing the
offset in one place fails here rather than drifting.

**The guards.** ``start_dev.bat`` must refuse in prod and ``tools/promote.bat``
must refuse in dev — genuinely inverse, not accidentally identical. Both were
verified by execution, but the assertion is worth keeping: these are the two
statements standing between "restart the dev stack" and "stop the live one,
check out a different branch, and restart it".

``repo_paths`` reports PROD ports under pytest by design, so the expectations
here come from the pure ``_derive_ports`` applied to the shipped ``[dev]``
profile — the one way to see dev's numbers from inside a test.
"""
import pathlib
import re
import sys
import tomllib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402

ROOT = pathlib.Path(repo_paths.__file__).resolve().parent
START_DEV = (ROOT / "start_dev.bat").read_text(encoding="utf-8")
PROMOTE = (ROOT / "tools" / "promote.bat").read_text(encoding="utf-8")

# The three PROD launchers, and all four that start a stack. Named once so a new
# launcher is added in one place rather than to four assertions.
PROD_LAUNCHERS = ("start_all.bat", "start_all_wt.bat", "start_all_hidden.bat")
ALL_LAUNCHERS = PROD_LAUNCHERS + ("start_dev.bat",)


def _launcher(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _ports_for(profile_overrides):
    """A profile's derived ports, from the tracked config. PURE."""
    ports = tomllib.loads((ROOT / "config" / "ports.toml").read_text(encoding="utf-8-sig"))
    flags = dict(repo_paths._ENV_DEFAULTS)
    flags.update(profile_overrides)
    return repo_paths._derive_ports(ports, flags)


def _profile(name):
    profiles = tomllib.loads(
        (ROOT / "config" / "environments.toml").read_text(encoding="utf-8-sig"))
    return profiles[name]


def _numbers(text):
    """Every 4-digit port-shaped literal in a file."""
    return set(re.findall(r"(?<!\d)(\d{4})(?!\d)", text))


def test_dev_service_and_webgui_ports_are_present():
    dev = _ports_for(_profile("dev"))
    for name, port in dev["service_ports"].items():
        assert str(port) in START_DEV, f"start_dev.bat never names dev's {name} port {port}"
    assert str(dev["nicegui_port"]) in START_DEV


def test_no_prod_port_leaks_into_the_dev_launcher():
    """The failure mode this exists for: a copied-but-unedited literal.

    Prod's proxy (:8100) is the ONE prod port that legitimately appears — dev
    borrows it — so it is excluded by name rather than the test being weakened.
    """
    prod = _ports_for(_profile("prod"))
    leaked = {str(prod["nicegui_port"])} | {str(p) for p in prod["service_ports"].values()}
    assert not (leaked & _numbers(START_DEV)), "start_dev.bat still names a PROD port"


def test_dev_borrows_prods_proxy_rather_than_starting_one():
    dev = _ports_for(_profile("dev"))
    assert dev["proxy_port"] == 8100, "the [dev] profile stopped pinning prod's proxy"
    assert "schwab_proxy.py" not in START_DEV, (
        "start_dev.bat launches a proxy — dev must borrow prod's, because there is "
        "one rotating Schwab OAuth refresh token and a second holder is not safe")
    assert "wait_and_run.bat 8100" in START_DEV, (
        "the dev tabs no longer wait on prod's proxy, so a down prod would be silent")


def test_every_service_and_the_webgui_is_launched():
    for name in _ports_for(_profile("dev"))["service_ports"]:
        assert f"services\\{name}_svc\\app.py" in START_DEV
    assert "webgui\\main.py" in START_DEV


###############################################
# The guards — inverse, and failing closed
###############################################

_GUARD = re.compile(r"sys\.exit\((\d) if repo_paths\.IS_DEV else (\d)\)")


def test_the_two_guards_are_inverse():
    """start_dev proceeds (0) in dev; promote proceeds (0) in prod."""
    dev = _GUARD.search(START_DEV)
    promote = _GUARD.search(PROMOTE)
    assert dev, "start_dev.bat lost its IS_DEV guard"
    assert promote, "promote.bat lost its IS_DEV guard"
    assert dev.groups() == ("0", "1"), "start_dev.bat's guard is inverted"
    assert promote.groups() == ("1", "0"), "promote.bat's guard is inverted"
    assert dev.groups() == promote.groups()[::-1]


def test_both_guards_fail_closed():
    """`if errorlevel 1` is "1 or greater", so a python crash refuses too —
    rather than launching onto prod's ports, or stopping the live stack."""
    for text, name in ((START_DEV, "start_dev.bat"), (PROMOTE, "promote.bat")):
        after = text.split("repo_paths.IS_DEV", 1)
        assert len(after) == 2, f"{name} lost its guard"
        assert re.match(r'[^\n]*\r?\n\s*if errorlevel 1 \(', after[1]), (
            f"{name}'s guard does not branch on errorlevel right after the probe")


def test_promote_refuses_a_dirty_tree_before_stopping_anything():
    """Two claims, and the ORDER is the load-bearing one.

    `git checkout main` fails loudly only on a CONFLICTING local change; a
    non-conflicting one is carried along silently, leaving prod running
    something that is not main — which defeats the premise that prod is pinned
    and changes only when the operator says so. And the check has to precede
    ``stop_all``, or a refusal hands you a stopped stack and a message.
    """
    code = [ln for ln in PROMOTE.splitlines()
            if not ln.strip().upper().startswith("REM")]
    joined = "\n".join(code)
    assert "git status --porcelain" in joined, "promote.bat lost its dirty-tree check"
    dirty_at = next(i for i, ln in enumerate(code) if "git status --porcelain" in ln)
    stop_at = next(i for i, ln in enumerate(code) if "call stop_all.bat" in ln)
    assert dirty_at < stop_at, (
        "the dirty-tree check runs AFTER stop_all — a refusal would leave the "
        "stack down")


def test_promote_does_not_read_head_at_1():
    """Measured wrong twice over: a fresh clone (which the prod checkout is) has
    one reflog entry so HEAD@{1} exits 128, and a pull that brings nothing new
    leaves HEAD@{1} pointing at whatever `git checkout main` moved off — both
    reporting a lockfile change the pull never made."""
    code = "\n".join(ln for ln in PROMOTE.splitlines()
                     if not ln.strip().upper().startswith("REM"))
    assert "HEAD@{1}" not in code, (
        "promote.bat is back to reading HEAD@{1} instead of capturing the "
        "pre-pull commit")
    assert "git rev-parse HEAD" in code


###############################################
# GUARD A — a PROD launcher must refuse in DEV
###############################################
#
# The mirror image of start_dev.bat's guard, and the one that actually cost
# something. The desktop shortcut still pointed at the old folder, which is now
# dev, so a double-click ran a PROD launcher from the DEV checkout. Those
# launchers start a schwab-proxy, and dev's PROXY_PORT is 8100 - PROD'S, borrowed
# - so the proxy bound prod's port while prod itself never started. Nothing
# looked wrong. Prod was entirely down and a dev-checkout process was serving its
# market data.
#
# start_dev.bat was guarded against running in prod from the beginning; the
# inverse was never written.

def _code_lines(text):
    """Lines with the REM comments dropped.

    Every marker these tests look for is also DISCUSSED in the comments - the
    guard blocks explain the incident by name - so scanning raw text would match
    prose and pass with the code deleted.
    """
    return [ln for ln in text.splitlines() if not ln.strip().upper().startswith("REM")]


def _first_index(lines, needles):
    for i, line in enumerate(lines):
        if any(n in line for n in needles):
            return i
    return None


# Anything that actually starts a process. `Start-Process` covers both
# start_all_wt.bat's hidden launcher and start_all_hidden.bat's self-relaunch.
_LAUNCH_MARKERS = (
    "schwab_proxy.py", "wt new-tab", "call :launch_hidden", "Start-Process",
    "wait_and_run.bat", "nq_hud.py", "start_all_wt.bat", "webgui\\main.py",
)


@pytest.mark.parametrize("name", PROD_LAUNCHERS)
def test_a_prod_launcher_refuses_in_a_dev_checkout(name):
    guard = _GUARD.search(_launcher(name))
    assert guard, f"{name} has no IS_DEV guard - it would start prod's stack from dev"
    assert guard.groups() == ("1", "0"), (
        f"{name}'s guard is inverted: it must exit 1 (refuse) in dev, 0 in prod")


@pytest.mark.parametrize("name", PROD_LAUNCHERS)
def test_the_prod_guard_fails_closed(name):
    """`if errorlevel 1` is "1 or greater", so a python crash refuses too -
    rather than launching a proxy onto prod's port from a dev checkout."""
    after = _launcher(name).split("repo_paths.IS_DEV", 1)
    assert len(after) == 2, f"{name} lost its guard"
    assert re.match(r'[^\n]*\r?\n\s*if errorlevel 1 \(', after[1]), (
        f"{name}'s guard does not branch on errorlevel right after the probe")


@pytest.mark.parametrize("name", PROD_LAUNCHERS)
def test_the_prod_guard_explains_why_it_matters(name):
    """A bare "wrong checkout" tells the operator to go elsewhere but not what
    they narrowly avoided - and the shortcut that caused this is still the most
    convenient way to start the stack."""
    echoed = "\n".join(_code_lines(_launcher(name)))
    assert "DEV checkout" in echoed
    assert "8100" in echoed, (
        f"{name}'s refusal never mentions the proxy port at stake")


@pytest.mark.parametrize("name", PROD_LAUNCHERS)
def test_the_prod_guard_precedes_every_process_launch(name):
    """Placement is the guard. Refusing after the proxy is already spawned would
    reproduce the incident and then complain about it."""
    lines = _code_lines(_launcher(name))
    guard_at = _first_index(lines, ["repo_paths.IS_DEV"])
    launch_at = _first_index(lines, _LAUNCH_MARKERS)
    assert guard_at is not None, f"{name} lost its guard"
    assert launch_at is not None, f"{name} no longer launches anything - check the markers"
    assert guard_at < launch_at, (
        f"{name} starts a process before its IS_DEV guard runs")


def test_the_hidden_launcher_guards_before_relaunching_itself():
    """start_all_hidden.bat is the one the incident came through, and its shape
    makes placement subtle: it relaunches ITSELF hidden and THEN starts the HUD.
    A guard in :run would fire only inside the hidden second pass, printing to a
    console nobody can see, and the double-clicked shortcut would appear to do
    nothing at all. So it must sit ahead of the __hidden dispatch."""
    lines = _code_lines(_launcher("start_all_hidden.bat"))
    guard_at = _first_index(lines, ["repo_paths.IS_DEV"])
    check_at = _first_index(lines, ["check_stack_down.py"])
    dispatch_at = _first_index(lines, ['"%~1"=="__hidden" goto run'])
    hud_at = _first_index(lines, ["nq_hud.py"])
    assert None not in (guard_at, check_at, dispatch_at, hud_at)
    assert guard_at < dispatch_at, "the IS_DEV guard runs only on the hidden pass"
    assert check_at < dispatch_at, "the already-running check runs only on the hidden pass"
    assert dispatch_at < hud_at, "the HUD moved ahead of the dispatch - re-check the guards"


def test_the_hidden_launcher_does_not_start_the_hud_if_the_stack_did_not_start():
    """Belt and braces for the second pass: start_all_wt.bat runs the same two
    guards, and if one of them refuses there the HUD must not come up polling a
    stack that was never started."""
    lines = _code_lines(_launcher("start_all_hidden.bat"))
    delegate_at = _first_index(lines, ["start_all_wt.bat"])
    branch_at = _first_index(lines[delegate_at:], ["if errorlevel 1"])
    hud_call_at = _first_index(lines[delegate_at:], ["call :launch_hud"])
    assert branch_at is not None and hud_call_at is not None
    assert branch_at < hud_call_at, (
        "start_all_hidden.bat calls the HUD without checking whether the "
        "delegated launcher refused")


def test_the_dev_launcher_has_no_prod_guard():
    """Non-vacuity partner for the four tests above: the guard must be specific
    to the PROD launchers. start_dev.bat carries the OPPOSITE polarity, and a
    copy-paste that inverted it would silently make the dev stack unstartable."""
    guard = _GUARD.search(START_DEV)
    assert guard.groups() == ("0", "1")


###############################################
# GUARD B — refuse to start a stack twice
###############################################
#
# Starting the same stack twice produces short-lived duplicates that each do a
# full startup - real Schwab API calls - before failing to bind and exiting. The
# operator has done it twice.
#
# The port set lives in tools/check_stack_down.py, which reads it from
# stop_all._targets(); the launchers only call it. That is what keeps the STARTER
# and the STOPPER from disagreeing about what this environment owns - the exact
# drift behind the incident Guard A covers.

@pytest.mark.parametrize("name", ALL_LAUNCHERS)
def test_every_launcher_checks_that_the_stack_is_down(name):
    assert "check_stack_down.py" in "\n".join(_code_lines(_launcher(name))), (
        f"{name} can start a second copy of a running stack")


@pytest.mark.parametrize("name", ALL_LAUNCHERS)
def test_the_already_running_check_fails_closed(name):
    """Split the COMMENT-STRIPPED text: each guard block explains itself by
    naming the script, so splitting raw text lands in the prose and the
    assertion measures nothing."""
    after = "\n".join(_code_lines(_launcher(name))).split("check_stack_down.py", 1)
    assert len(after) == 2, f"{name} only mentions the check in a comment"
    assert re.match(r'[^\n]*\r?\n\s*if errorlevel 1 \(', after[1]), (
        f"{name} runs the check but does not branch on its result")


@pytest.mark.parametrize("name", ALL_LAUNCHERS)
def test_the_already_running_check_precedes_every_process_launch(name):
    lines = _code_lines(_launcher(name))
    check_at = _first_index(lines, ["check_stack_down.py"])
    launch_at = _first_index(lines, _LAUNCH_MARKERS)
    assert check_at is not None, f"{name} lost its already-running check"
    assert check_at < launch_at, (
        f"{name} starts a process before checking whether one is already up")


@pytest.mark.parametrize("name", ALL_LAUNCHERS)
def test_no_launcher_carries_its_own_port_list_for_the_check(name):
    """The check must be invoked, not reimplemented. A launcher probing a
    hardcoded port would drift from stop_all the moment the offsets moved."""
    code = "\n".join(_code_lines(_launcher(name)))
    window = "\n".join(code.split("check_stack_down.py", 1)[1].splitlines()[:6])
    assert not re.findall(r"(?<!\d)(\d{4})(?!\d)", window), (
        f"{name} names a port next to the check instead of letting the check "
        f"read them from stop_all")


def test_the_check_exists_and_reads_its_ports_from_stop_all():
    """The single claim the four launchers all rest on."""
    src = (ROOT / "tools" / "check_stack_down.py").read_text(encoding="utf-8")
    assert "from stop_all import _targets" in src, (
        "check_stack_down.py stopped sourcing its ports from stop_all._targets - "
        "the starter and the stopper can now disagree about what this "
        "environment owns")


def _bat(name):
    return (pathlib.Path(__file__).resolve().parents[1] / name).read_text(
        encoding="utf-8", errors="surrogateescape")


def test_start_webgui_derives_its_port_instead_of_hardcoding_it():
    """It used to say :8500 everywhere. Correct until a second environment
    existed — from dev it starts on :9500 while announcing prod's port."""
    src = "\n".join(l for l in _bat("start_webgui.bat").splitlines()
                    if not l.strip().upper().startswith("REM"))
    # Matched loosely on purpose: the probe imports `repo_paths as r`, so a
    # literal "repo_paths.NICEGUI_PORT" match broke on the alias while the
    # property it was checking still held. Assert the property, not the spelling.
    assert "repo_paths" in src and "NICEGUI_PORT" in src
    assert "8500" not in src, "a literal web GUI port is back in start_webgui.bat"


def test_start_webgui_checks_only_its_own_component():
    """Scoped: starting the web GUI while the services run is legitimate."""
    src = "\n".join(l for l in _bat("start_webgui.bat").splitlines()
                    if not l.strip().upper().startswith("REM"))
    assert "check_stack_down.py --only webgui" in src


def test_open_webgui_helper_takes_the_port_as_an_argument():
    src = "\n".join(l for l in _bat("_open_webgui.bat").splitlines()
                    if not l.strip().upper().startswith("REM"))
    assert "%~1" in src
    assert "%WEBPORT%" in src


def test_start_webgui_probe_uses_no_percent_formatting():
    """`%` is a batch metacharacter. cmd rewrites `'set X=%s' % val` into
    `'set X= val` - an unterminated Python string - before python -c sees it,
    and the launcher then refuses in BOTH environments with "could not read the
    port". Cost two live-test rounds; concatenation cannot regress this way."""
    for line in _bat("start_webgui.bat").splitlines():
        s = line.strip()
        if s.upper().startswith("REM") or "-c " not in s:
            continue
        assert "%s" not in s and "%d" not in s, (
            f"%-formatting inside a batch -c argument will be mangled by cmd: {s}")
