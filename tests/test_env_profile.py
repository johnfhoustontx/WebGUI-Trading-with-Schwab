"""Environment-profile resolution (dev/prod) — see
docs/plans/2026-08-08-dev-prod-environments-design.md."""
import pathlib
import sys
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

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


def test_dev_marker_without_peer_root(tmp_path):
    """peer_root is optional — only the snapshot tool needs it."""
    name, _, peer = repo_paths._resolve_env(
        _root(tmp_path, 'name = "dev"\n'), under_pytest=False)
    assert name == "dev"
    assert peer is None


def test_marker_with_utf8_bom_is_honored(tmp_path):
    """PowerShell's `>` and Out-File write UTF-8 WITH BOM on this box. tomllib
    rejects a BOM, so without utf-8-sig this silently resolved to prod — a dev
    checkout that binds prod's ports with every suppression off."""
    root = _root(tmp_path)
    (root / "config" / "env.local.toml").write_bytes(
        '\ufeffname = "dev"\n'.encode("utf-8"))
    name, flags, _ = repo_paths._resolve_env(root, under_pytest=False)
    assert name == "dev"
    assert flags["port_offset"] == 1000


def test_garbage_marker_resolves_to_prod_but_warns(tmp_path, capsys):
    """A truncated or hand-mangled marker must not silently half-apply a profile.

    It still fails safe to prod, but a file that EXISTS and will not parse is
    positive evidence someone meant a non-default environment, so it must not be
    silent — the launchers capture stderr to logs/<name>.err.log.
    """
    name, flags, _ = repo_paths._resolve_env(
        _root(tmp_path, "name = [broken"), under_pytest=False)
    assert name == "prod"
    assert flags["allow_notifications"] is True
    assert "env.local.toml" in capsys.readouterr().err


def test_backslash_peer_root_warns(tmp_path, capsys):
    """The natural Windows path is an invalid \\W escape that discards the WHOLE
    document, `name = "dev"` with it. Encoding cannot fix this, so the warning is
    the mitigation (and the example marker uses a literal string to avoid it)."""
    marker = 'name = "dev"\npeer_root = "D:\\WebGUI Trading Prod"\n'
    name, _, _ = repo_paths._resolve_env(
        _root(tmp_path, marker), under_pytest=False)
    assert name == "prod"
    assert "env.local.toml" in capsys.readouterr().err


def test_literal_quoted_windows_peer_root_parses(tmp_path):
    """The form config/env.local.example.toml ships: a TOML literal string takes
    a backslash path verbatim."""
    marker = "name = 'dev'\npeer_root = 'D:\\WebGUI Trading Prod'\n"
    name, _, peer = repo_paths._resolve_env(
        _root(tmp_path, marker), under_pytest=False)
    assert name == "dev"
    assert peer == pathlib.Path("D:\\WebGUI Trading Prod")


def test_missing_marker_is_silent(tmp_path, capsys):
    """Absent is the normal case (prod) — it must not warn, or every prod service
    start would log a spurious line."""
    repo_paths._resolve_env(_root(tmp_path), under_pytest=False)
    assert capsys.readouterr().err == ""


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

    Uses a DEV marker on purpose: against a prod marker the topology assertions
    would pass even with no guard at all (prod's profile carries those values
    natively), so only a dev marker actually proves the documented decision.
    """
    _, flags, _ = repo_paths._resolve_env(
        _root(tmp_path, 'name = "dev"\n'), under_pytest=True)
    assert flags["port_offset"] == 0        # prod ports despite the dev marker
    assert flags["proxy_port"] is None
    # redis_db is part of the same connection topology: it feeds MEMURAI_URL, and
    # tests/test_repo_paths_ports.py asserts that URL ends "/0". Without forcing it
    # here, that pre-existing test fails inside a dev checkout.
    assert flags["redis_db"] == 0
    # owns_proxy likewise. It is not a port, but it says WHOSE the port is, and a
    # consumer that branches on it (tools/stop_all.py drops the proxy from its
    # kill list) would otherwise behave differently in a dev checkout than in a
    # prod one — exactly the divergence this guard exists to rule out.
    assert flags["owns_proxy"] is True
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
    assert isinstance(repo_paths.ENV_FLAGS, dict)
    assert repo_paths.IS_DEV == (repo_paths.ENV_NAME == "dev")
    assert repo_paths.ENV_FLAGS["allow_claude"] is False
    assert repo_paths.ENV_FLAGS["allow_notifications"] is False


# ------------------------------------------------------------- port derivation

def test_port_derivation_prod():
    """Under a prod profile the transform is the IDENTITY on every port.

    Note what this does and does not establish: the table here is synthetic, so
    this pins the transform, not the shipped numbers. Byte-identity of the exported
    constants against the pre-environment repo is guarded by
    tests/test_repo_paths_ports.py, which asserts the literal 8210-8214 and a
    MEMURAI_URL ending "/0".
    """
    ports = {"proxy": 8100, "nicegui": 8500, "memurai": 6379,
             "services": {"options": 8211, "market": 8215}}
    d = repo_paths._derive_ports(ports, {"port_offset": 0, "proxy_port": None,
                                         "redis_db": 0})
    assert d["proxy_port"] == 8100
    assert d["nicegui_port"] == 8500
    assert d["service_ports"] == {"options": 8211, "market": 8215}
    assert d["memurai_port"] == 6379
    assert d["redis_db"] == 0


def test_port_derivation_dev_offsets_and_borrows_proxy():
    ports = {"proxy": 8100, "nicegui": 8500, "memurai": 6379,
             "services": {"options": 8211, "market": 8215}}
    d = repo_paths._derive_ports(ports, {"port_offset": 1000, "proxy_port": 8100,
                                         "redis_db": 1})
    assert d["proxy_port"] == 8100          # borrowed, NOT offset
    assert d["nicegui_port"] == 9500
    assert d["service_ports"] == {"options": 9211, "market": 9215}
    # Memurai PORT is shared (one server); only the logical DB differs.
    assert d["memurai_port"] == 6379
    assert d["redis_db"] == 1


def test_port_derivation_offsets_proxy_when_not_pinned():
    """Without an explicit proxy_port the offset applies to the proxy too — the
    documented meaning of `proxy_port = None`. Pinning it is what makes dev BORROW
    prod's proxy; the two derivation tests above share proxy 8100, so neither one
    would notice the override being ignored."""
    ports = {"proxy": 8100, "nicegui": 8500, "memurai": 6379, "services": {}}
    d = repo_paths._derive_ports(ports, {"port_offset": 1000, "proxy_port": None,
                                         "redis_db": 0})
    assert d["proxy_port"] == 9100


def test_port_derivation_leaves_external_ports_alone():
    """Fed the REAL ports.toml, the derivation must emit exactly these five keys.

    The tests above pass synthetic tables that simply OMIT options_analytics /
    approval / ml_servers, so to them "deliberately not offset" and "not in the
    input" are indistinguishable — the decision is guarded by prose alone. Reading
    the shipped file is the point: it is the only test here that notices a port
    being ADDED to that file, which is exactly the case that silently goes
    un-offset (right for an external process, a bug for one this repo starts).
    """
    ports = tomllib.loads(
        (REPO / "config" / "ports.toml").read_text(encoding="utf-8-sig"))
    assert {"options_analytics", "approval", "ml_servers"} <= set(ports)  # non-vacuous
    d = repo_paths._derive_ports(ports, {"port_offset": 1000, "proxy_port": None,
                                         "redis_db": 1})
    assert set(d) == {"proxy_port", "nicegui_port", "service_ports",
                      "memurai_port", "redis_db"}
    # The assertion above pins the OUTPUT, which only moves when someone teaches
    # _derive_ports a new port. The silent direction is the other one: a top-level
    # port ADDED to ports.toml and not taught is simply never offset, and no output
    # key changes. So pin the input's scalar keys too — this is a decision tripwire,
    # not a value check. Adding a port here should fail once and make the author
    # answer: does this repo START it (offset it, and extend _derive_ports) or not
    # (external — leave it, and add it below)?
    external = {"proxy", "options_analytics", "approval", "dashboard_frontend",
                "nicegui", "memurai"}
    scalars = {k for k, v in ports.items() if not isinstance(v, dict)}
    assert scalars == external, (
        f"ports.toml top-level keys changed: {scalars ^ external}. If this repo "
        f"starts the new process, offset it in repo_paths._derive_ports; if it is "
        f"external, list it here.")


# --------------------------------------------------------------- shipped config
# The tests above all build their own PROFILES string, so none of them would
# notice a typo in the file this repo actually ships. Because every default is
# permissive, a misspelled or omitted key in [dev] does not error — it silently
# resolves to the EMITTING behavior. These two read the real file.

def _shipped_profiles():
    return tomllib.loads(
        (REPO / "config" / "environments.toml").read_text(encoding="utf-8-sig"))


def test_shipped_profiles_match_the_flag_contract():
    """Every profile's keys must correspond to _ENV_DEFAULTS — no unknown keys
    (a typo) and none missing (an omission, or a flag added to the defaults later
    and forgotten here). `proxy_port` may be absent: TOML cannot express None."""
    profiles = _shipped_profiles()
    assert set(profiles) == {"prod", "dev"}
    expected = set(repo_paths._ENV_DEFAULTS)
    for name, table in profiles.items():
        keys = set(table)
        assert not keys - expected, f"[{name}] has unknown key(s): {keys - expected}"
        missing = expected - keys - {"proxy_port"}
        assert not missing, f"[{name}] is missing key(s): {missing}"
        # TYPES, not just names. The three numeric keys reach an unguarded int()
        # in _derive_ports, which runs at IMPORT — so `redis_db = "one"` in this
        # tracked file crashes every process in the stack, and nothing else in the
        # suite can see it: the pytest guard overwrites exactly these three keys,
        # so no test ever pushes a real profile value through the function.
        # isinstance(..., int) also catches a float silently truncating
        # (port_offset = 1000.7 -> 1000). bool is an int subclass, hence the
        # explicit exclusion.
        for key in ("port_offset", "redis_db"):
            val = table[key]
            assert isinstance(val, int) and not isinstance(val, bool), \
                f"[{name}] {key} must be an integer, got {val!r}"
        proxy_port = table.get("proxy_port", 0)
        assert isinstance(proxy_port, int) and not isinstance(proxy_port, bool), \
            f"[{name}] proxy_port must be an integer, got {proxy_port!r}"


def test_shipped_dev_profile_suppresses_and_offsets():
    """The shipped file's VALUES, not just its shape: dev must emit nothing and
    move off prod's ports, prod must stay fully permissive."""
    profiles = _shipped_profiles()
    emitting = ("allow_claude", "allow_notifications", "schedulers",
                "autonomous_trading")
    assert all(profiles["prod"][k] is True for k in emitting)
    assert not any(profiles["dev"][k] for k in emitting)
    assert profiles["dev"]["port_offset"] > 0
    assert profiles["dev"]["redis_db"] != profiles["prod"]["redis_db"]
    assert profiles["dev"]["owns_proxy"] is False
