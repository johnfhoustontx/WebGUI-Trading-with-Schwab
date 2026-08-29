"""`proxy_host` — where this checkout reaches the Schwab proxy.

WHY IT EXISTS. Dev owns no proxy: the Schwab OAuth refresh token is a single
rotating credential, so there can be only one holder, and dev borrows prod's.
That worked because the two checkouts shared a machine — ``PROXY_URL`` was
hardcoded ``http://127.0.0.1:{PROXY_PORT}``. It stops working the moment the two
are on different hosts, which is exactly the migration's parallel-run week: a VPS
shadow stack borrowing the Windows box's proxy over Tailscale.

⚠ SCOPE. This moves ONE url. ``SERVICE_URLS``, ``NICEGUI_URL`` and
``MEMURAI_URL`` stay on 127.0.0.1 because each host runs its own services, its
own web GUI and its own Redis. Generalising all four is the mistake this change
invites, and the tests below exist to catch it.
"""
import tomllib

import pytest

import repo_paths


def _marker(root, body):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "env.local.toml").write_text(body, encoding="utf-8")
    # A profile file must exist or _resolve_env falls back to prod defaults.
    (root / "config" / "environments.toml").write_text(
        (repo_paths.REPO_ROOT / "config" / "environments.toml").read_text(
            encoding="utf-8"), encoding="utf-8")


def test_both_profiles_default_to_loopback():
    """The default must keep existing behaviour byte-identical: before this knob
    every consumer talked to 127.0.0.1, and a co-located dev still should."""
    profiles = tomllib.loads(
        (repo_paths.REPO_ROOT / "config" / "environments.toml").read_text(encoding="utf-8"))
    for name in ("prod", "dev"):
        assert profiles[name]["proxy_host"] == "127.0.0.1", name


def test_it_is_in_the_defaults_so_an_older_marker_still_resolves():
    assert repo_paths._ENV_DEFAULTS["proxy_host"] == "127.0.0.1"


def test_a_machine_local_marker_can_point_it_elsewhere(tmp_path):
    """Machine-local and gitignored, exactly like peer_root — a git pull must
    never carry one checkout's proxy address into another."""
    _marker(tmp_path, 'name = "dev"\nproxy_host = "100.64.1.2"\n')
    _, flags, _ = repo_paths._resolve_env(tmp_path, under_pytest=False)
    assert flags["proxy_host"] == "100.64.1.2"


def test_an_absent_override_leaves_the_profile_default(tmp_path):
    _marker(tmp_path, 'name = "dev"\n')
    _, flags, _ = repo_paths._resolve_env(tmp_path, under_pytest=False)
    assert flags["proxy_host"] == "127.0.0.1"


def test_a_malformed_marker_still_yields_a_usable_host(tmp_path):
    """_read_env_marker never raises; a broken marker must not leave PROXY_URL
    unbuildable."""
    _marker(tmp_path, "name = “dev”  # smart quotes: invalid TOML\n")
    _, flags, _ = repo_paths._resolve_env(tmp_path, under_pytest=False)
    assert flags["proxy_host"] == "127.0.0.1"


def test_pytest_forces_loopback(tmp_path):
    """The process presents as PROD under pytest — ports, Redis DB, ownership,
    identity. The proxy host belongs to that same topology: a suite that could
    resolve a REMOTE proxy would be one monkeypatch away from a test reaching a
    live trading stack's market data."""
    _marker(tmp_path, 'name = "dev"\nproxy_host = "100.64.1.2"\n')
    _, flags, _ = repo_paths._resolve_env(tmp_path, under_pytest=True)
    assert flags["proxy_host"] == "127.0.0.1"


def test_proxy_url_is_built_from_it():
    assert repo_paths.PROXY_URL == \
        f"http://{repo_paths.PROXY_HOST}:{repo_paths.PROXY_PORT}"


def test_this_checkout_still_resolves_to_loopback():
    """Under pytest, always. Non-vacuity for the test above."""
    assert repo_paths.PROXY_HOST == "127.0.0.1"


# --- the mistake this change invites ----------------------------------------
def test_only_the_proxy_url_moves(monkeypatch, tmp_path):
    """SERVICE_URLS, NICEGUI_URL and MEMURAI_URL must stay on loopback.

    Each host runs its OWN services, its own web GUI and its own Redis. Pointing
    those at a remote host would make a dev checkout drive prod's services and
    write prod's Redis — the precise blast radius `owns_proxy = false` exists to
    prevent, arrived at from the opposite direction."""
    for url in repo_paths.SERVICE_URLS.values():
        assert "127.0.0.1" in url
    assert "127.0.0.1" in repo_paths.NICEGUI_URL
    assert "127.0.0.1" in repo_paths.MEMURAI_URL


def test_the_knob_is_declared_in_both_profiles():
    """tests/test_env_profile.py guards that the key SETS match, so a one-sided
    addition fails there. This states the requirement locally too, because a
    reader of this file should not have to find that guard to know it holds."""
    profiles = tomllib.loads(
        (repo_paths.REPO_ROOT / "config" / "environments.toml").read_text(encoding="utf-8"))
    assert "proxy_host" in profiles["prod"]
    assert "proxy_host" in profiles["dev"]


@pytest.mark.parametrize("host", ["127.0.0.1", "100.64.1.2", "prod.tailnet.ts.net"])
def test_a_hostname_works_as_well_as_an_address(host, tmp_path):
    """Tailscale gives you a NAME. Requiring an address would push people to
    hardcode one that changes."""
    _marker(tmp_path, f'name = "dev"\nproxy_host = "{host}"\n')
    _, flags, _ = repo_paths._resolve_env(tmp_path, under_pytest=False)
    assert flags["proxy_host"] == host
