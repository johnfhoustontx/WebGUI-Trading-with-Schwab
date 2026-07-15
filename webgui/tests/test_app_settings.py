import json
import app_settings


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_PATH", tmp_path / "settings.json")
    app_settings.reset_cache()  # in-memory cache must not leak across tests


def test_load_caches_in_memory_until_set_or_reset(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text(json.dumps({"alert_volume": 0.3}))
    assert app_settings.load()["alert_volume"] == 0.3
    # An external file change is NOT re-read while cached (no per-call disk hit).
    (tmp_path / "settings.json").write_text(json.dumps({"alert_volume": 0.9}))
    assert app_settings.load()["alert_volume"] == 0.3
    # set() updates the cache (and disk).
    app_settings.set("alert_volume", 0.5)
    assert app_settings.load()["alert_volume"] == 0.5
    # reset_cache() forces a fresh disk read.
    app_settings.reset_cache()
    assert app_settings.load()["alert_volume"] == 0.5  # last set persisted to disk


def test_load_returns_defaults_when_no_file(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert app_settings.load() == app_settings.DEFAULTS


def test_set_persists_and_get_reads_back(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    app_settings.set("alert_enabled", False)
    assert app_settings.get("alert_enabled") is False
    # round-trips through disk
    assert json.loads((tmp_path / "settings.json").read_text())["alert_enabled"] is False


def test_load_merges_partial_file_over_defaults(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text(json.dumps({"alert_volume": 0.2}))
    loaded = app_settings.load()
    assert loaded["alert_volume"] == 0.2
    assert loaded["alert_sound"] == app_settings.DEFAULTS["alert_sound"]  # default kept


def test_load_falls_back_on_garbage_file(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text("{not json")
    assert app_settings.load() == app_settings.DEFAULTS


def test_get_unknown_key_returns_none(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert app_settings.get("nope") is None


def test_nav_pinned_defaults_to_off():
    """Pinning the nav drawer open is opt-in; the hover rail is the default."""
    assert app_settings.DEFAULTS["nav_pinned"] is False
