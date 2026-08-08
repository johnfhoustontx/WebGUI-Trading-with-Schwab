"""``start_dev.bat``'s port literals — see
docs/plans/2026-08-08-dev-prod-environments-design.md.

A .bat file cannot be unit-tested the way a function can, but the thing about
the dev launcher that CAN silently rot is worth pinning: its port numbers.
Every process reads its own port from ``repo_paths``, so a wrong literal here
misconfigures nothing — it misinforms, which is worse in the one situation the
launcher exists for. A tab titled "9214" that is really 9314 sends you looking
at the wrong service; a banner still naming 8500 sends you to PROD's web GUI
while you debug dev. Deriving the expected numbers from ``config/ports.toml``
plus the ``[dev]`` profile means changing the offset in one place fails here
rather than drifting.

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


def test_the_marker_guard_is_present_and_fails_closed():
    """Refuses in prod, and `if errorlevel 1` (>= 1) means a python crash also
    refuses rather than launching seven processes onto prod's ports."""
    after = START_DEV.split("repo_paths.IS_DEV", 1)
    assert len(after) == 2, "start_dev.bat lost its IS_DEV guard"
    assert "sys.exit(0 if repo_paths.IS_DEV else 1)" in START_DEV
    assert re.match(r'[^\n]*\n\s*if errorlevel 1 \(', after[1]), (
        "the guard does not branch on errorlevel immediately after the probe")
