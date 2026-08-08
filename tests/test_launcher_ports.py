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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402

ROOT = pathlib.Path(repo_paths.__file__).resolve().parent
START_DEV = (ROOT / "start_dev.bat").read_text(encoding="utf-8")
PROMOTE = (ROOT / "tools" / "promote.bat").read_text(encoding="utf-8")


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
