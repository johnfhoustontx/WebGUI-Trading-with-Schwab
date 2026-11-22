"""The warm standby's configuration and the machine-local box identity.

Design: docs/plans/2026-11-22-warm-standby-design.md
"""
import ast
import os
import pathlib
import sys

import pytest

from shared import standby_config as sc
from shared.standby_config import Identity


@pytest.fixture(autouse=True)
def _fresh():
    sc.reset_cache()
    yield
    sc.reset_cache()


def test_defaults_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "load", sc.toml_loader(tmp_path / "missing.toml", sc.DEFAULTS,
                                                   label="standby.toml")[0])
    s = sc.sync_settings()
    assert s["keep"] == 3
    assert s["at"] == "22:15"
    assert s["max_age_h"] == 80
    assert s["max_shrink_pct"] == 50
    assert isinstance(s["generations"], pathlib.Path)
    assert "~" not in str(s["generations"])
    assert "options-scanner/gex_history.db" in s["shrink_stores"]


def test_shipped_file_holds_two_boxes_and_two_neighbours():
    assert set(sc.boxes()) == {"vps2", "vps3"}
    assert sc.boxes()["vps3"]["ssh"] == ["trading-standby", "192.227.249.22"]
    names = [n["name"] for n in sc.neighbours()]
    assert names == ["cos-hub", "neuralstrike-reports"]
    assert sc.neighbours()[1]["files"] == [".config/neuralstrike/claude.env"]


def test_identity_round_trip(tmp_path):
    p = tmp_path / "sub" / "box.toml"
    sc.write_identity(Identity("vps3", "standby", False), p)
    assert sc.read_identity(p) == Identity("vps3", "standby", False)
    sc.write_identity(Identity("vps3", "prod", True), p)
    assert sc.read_identity(p) == Identity("vps3", "prod", True)
    assert not [f for f in p.parent.iterdir() if f != p]
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_identity_absent_is_none(tmp_path):
    assert sc.read_identity(tmp_path / "nope.toml") is None


def test_identity_bad_role_is_none(tmp_path):
    p = tmp_path / "box.toml"
    p.write_text('self = "vps2"\nrole = "boss"\ntaking_over = false\n')
    assert sc.read_identity(p) is None


def test_identity_unreadable_is_none(tmp_path):
    p = tmp_path / "box.toml"
    p.write_text("this is = = not toml")
    assert sc.read_identity(p) is None


def test_peer_is_the_other_box():
    name, entry = sc.peer(Identity("vps2", "prod", False))
    assert name == "vps3"
    assert entry["public_ip"] == "192.227.249.22"
    assert sc.peer(Identity("vps3", "standby", False))[0] == "vps2"


def test_peer_rejects_unknown_self():
    with pytest.raises(ValueError):
        sc.peer(Identity("vps9", "prod", False))


def test_peer_rejects_a_config_without_two_boxes(monkeypatch):
    monkeypatch.setattr(sc, "boxes", lambda: {"vps2": {}, "vps3": {}, "vps4": {}})
    with pytest.raises(ValueError):
        sc.peer(Identity("vps2", "prod", False))


def test_import_set_is_stdlib_config_toml_repo_paths():
    tree = ast.parse(pathlib.Path(sc.__file__).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mods.add(node.module)
    stdlib = set(sys.stdlib_module_names)
    extra = {m for m in mods
             if m.split(".")[0] not in stdlib
             and m not in {"repo_paths", "shared.config_toml"}}
    assert not extra, extra
