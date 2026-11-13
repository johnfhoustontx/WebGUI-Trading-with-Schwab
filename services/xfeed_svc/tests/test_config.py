import pathlib

from shared.config_toml import toml_loader


def test_defaults_load_without_file(tmp_path):
    from services.xfeed_svc import config
    load, _reset = toml_loader(tmp_path / "none.toml", config.DEFAULTS)
    cfg = load()
    assert cfg["poller"]["interval_s"] == 60
    assert cfg["views"]["latest_rows"] == 300


def test_tracked_file_matches_defaults():
    import tomllib

    from repo_paths import REPO_ROOT
    from services.xfeed_svc import config
    path = pathlib.Path(REPO_ROOT) / "config" / "xfeed.toml"
    on_disk = tomllib.loads(path.read_text(encoding="utf-8"))
    assert on_disk == config.DEFAULTS
    config.reset()
    assert config.load() == config.DEFAULTS
