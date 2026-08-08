"""Environment-profile resolution (dev/prod) — see
docs/plans/2026-08-08-dev-prod-environments-design.md."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402

PROFILES = """
[prod]
port_offset = 0
redis_db = 0
owns_proxy = true
allow_claude = true
allow_notifications = true
schedulers = true
autonomous_trading = true

[dev]
port_offset = 1000
proxy_port = 8100
redis_db = 1
owns_proxy = false
allow_claude = false
allow_notifications = false
schedulers = false
autonomous_trading = false
"""


def _root(tmp_path, marker=None):
    """A fake checkout root: config/environments.toml always, marker optionally."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "environments.toml").write_text(PROFILES, encoding="utf-8")
    if marker is not None:
        (cfg / "env.local.toml").write_text(marker, encoding="utf-8")
    return tmp_path


def test_missing_marker_resolves_to_prod(tmp_path):
    """The repo's behavior before this feature existed. Fail-safe."""
    name, flags, peer = repo_paths._resolve_env(_root(tmp_path), under_pytest=False)
    assert name == "prod"
    assert flags["port_offset"] == 0
    assert flags["owns_proxy"] is True
    assert flags["allow_claude"] is True
    assert peer is None


def test_dev_marker_selects_dev_profile(tmp_path):
    root = _root(tmp_path, 'name = "dev"\npeer_root = "D:/WebGUI Trading Prod"\n')
    name, flags, peer = repo_paths._resolve_env(root, under_pytest=False)
    assert name == "dev"
    assert flags["port_offset"] == 1000
    assert flags["proxy_port"] == 8100
    assert flags["redis_db"] == 1
    assert flags["owns_proxy"] is False
    assert peer == pathlib.Path("D:/WebGUI Trading Prod")


def test_garbage_marker_resolves_to_prod(tmp_path):
    """A truncated or hand-mangled marker must not silently half-apply a profile."""
    name, flags, _ = repo_paths._resolve_env(
        _root(tmp_path, "name = [broken"), under_pytest=False)
    assert name == "prod"
    assert flags["allow_notifications"] is True


def test_unknown_env_name_resolves_to_prod(tmp_path):
    name, _, _ = repo_paths._resolve_env(
        _root(tmp_path, 'name = "staging"\n'), under_pytest=False)
    assert name == "prod"


def test_missing_profiles_file_still_yields_prod_defaults(tmp_path):
    """No environments.toml at all (e.g. a stale checkout) must not crash import."""
    (tmp_path / "config").mkdir()
    name, flags, _ = repo_paths._resolve_env(tmp_path, under_pytest=False)
    assert name == "prod"
    assert flags["schedulers"] is True


def test_pytest_forces_suppression_but_keeps_prod_ports(tmp_path):
    """Deliberate: tests are hermetic, so ports are inert constants and the
    existing suites keep passing inside a dev checkout — but no test may ever
    reach Anthropic or a notification channel.

    Uses a DEV marker on purpose: against a prod marker the two port assertions
    would pass even with no guard at all (prod's profile carries those values
    natively), so only a dev marker actually proves the documented decision.
    """
    _, flags, _ = repo_paths._resolve_env(
        _root(tmp_path, 'name = "dev"\n'), under_pytest=True)
    assert flags["port_offset"] == 0        # prod ports despite the dev marker
    assert flags["proxy_port"] is None
    assert flags["allow_claude"] is False
    assert flags["allow_notifications"] is False
    assert flags["schedulers"] is False
    assert flags["autonomous_trading"] is False


def test_pytest_detection_is_the_default(tmp_path):
    """Omitting ``under_pytest`` must detect pytest by itself — otherwise the
    parameterized tests above would pass while the live wiring did nothing."""
    _, flags, _ = repo_paths._resolve_env(_root(tmp_path, 'name = "dev"\n'))
    assert flags["port_offset"] == 0
    assert flags["allow_claude"] is False


def test_live_module_constants_exist():
    """The import-time resolution ran and exported the public names — and the
    real, unparameterized call took the suppressed path."""
    assert repo_paths.ENV_NAME in ("dev", "prod")
    assert isinstance(repo_paths.ENV, dict)
    assert repo_paths.IS_DEV == (repo_paths.ENV_NAME == "dev")
    assert repo_paths.ENV["allow_claude"] is False
    assert repo_paths.ENV["allow_notifications"] is False
