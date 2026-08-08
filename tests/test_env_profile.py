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
    name, flags, peer = repo_paths._resolve_env(_root(tmp_path))
    assert name == "prod"
    assert flags["port_offset"] == 0
    assert flags["owns_proxy"] is True
    assert flags["allow_claude"] is True
    assert peer is None


def test_dev_marker_selects_dev_profile(tmp_path):
    root = _root(tmp_path, 'name = "dev"\npeer_root = "D:/WebGUI Trading Prod"\n')
    name, flags, peer = repo_paths._resolve_env(root)
    assert name == "dev"
    assert flags["port_offset"] == 1000
    assert flags["proxy_port"] == 8100
    assert flags["redis_db"] == 1
    assert flags["owns_proxy"] is False
    assert peer == pathlib.Path("D:/WebGUI Trading Prod")


def test_garbage_marker_resolves_to_prod(tmp_path):
    """A truncated or hand-mangled marker must not silently half-apply a profile."""
    name, flags, _ = repo_paths._resolve_env(_root(tmp_path, "name = [broken"))
    assert name == "prod"
    assert flags["allow_notifications"] is True


def test_unknown_env_name_resolves_to_prod(tmp_path):
    name, _, _ = repo_paths._resolve_env(_root(tmp_path, 'name = "staging"\n'))
    assert name == "prod"


def test_missing_profiles_file_still_yields_prod_defaults(tmp_path):
    """No environments.toml at all (e.g. a stale checkout) must not crash import."""
    (tmp_path / "config").mkdir()
    name, flags, _ = repo_paths._resolve_env(tmp_path)
    assert name == "prod"
    assert flags["schedulers"] is True


def test_pytest_forces_suppression_but_keeps_prod_ports():
    """Deliberate: tests are hermetic, so ports are inert constants and the
    existing suites keep passing inside a dev checkout — but no test may ever
    reach Anthropic or a notification channel.

    Asserted on the EXPORTED ``ENV`` rather than ``_resolve_env``'s return: the
    guard is applied at the module-constant site (see repo_paths), so ``ENV`` is
    the thing every consumer reads and the thing this rule governs. It holds in
    either checkout — a dev marker cannot lift a suppression here.
    """
    assert repo_paths.ENV["port_offset"] == 0        # prod ports under pytest
    assert repo_paths.ENV["proxy_port"] is None
    assert repo_paths.ENV["allow_claude"] is False
    assert repo_paths.ENV["allow_notifications"] is False
    assert repo_paths.ENV["schedulers"] is False
    assert repo_paths.ENV["autonomous_trading"] is False


def test_live_module_constants_exist():
    """The import-time resolution ran and exported the public names."""
    assert repo_paths.ENV_NAME in ("dev", "prod")
    assert isinstance(repo_paths.ENV, dict)
    assert repo_paths.IS_DEV == (repo_paths.ENV_NAME == "dev")
