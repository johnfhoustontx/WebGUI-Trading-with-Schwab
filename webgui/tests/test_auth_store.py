"""The credentials file: load, save, defaults, and the permissions it must carry."""
import json
import pytest

import auth_store


def test_load_returns_none_when_the_file_is_absent(tmp_path):
    assert auth_store.load(tmp_path / "nope.json") is None


def test_round_trip_preserves_every_field(tmp_path):
    path = tmp_path / "webgui_auth.json"
    creds = auth_store.Credentials(
        password_hash="$argon2id$v=19$m=19456,t=2,p=1$abc$def",
        totp_secret="JBSWY3DPEHPK3PXP",
        session_secret="s" * 43,
        epoch=3,
        last_totp_counter=99,
    )
    auth_store.save(creds, path)
    assert auth_store.load(path) == creds


def test_a_malformed_file_raises_rather_than_degrading(tmp_path):
    # Deliberately NOT the repo's usual "never raises" config contract: a config
    # file degrades to defaults, but degrading a credentials file means booting
    # with no password. Fail loudly instead.
    path = tmp_path / "webgui_auth.json"
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(auth_store.CredentialsError):
        auth_store.load(path)


def test_save_writes_owner_only_permissions(tmp_path):
    import os
    import sys
    if sys.platform == "win32":
        pytest.skip("POSIX mode bits; prod is Linux")
    path = tmp_path / "webgui_auth.json"
    auth_store.save(auth_store.Credentials("h", "s", "k", 1, 0), path)
    assert oct(os.stat(path).st_mode)[-3:] == "600"


def test_save_is_atomic_and_leaves_no_partial_file_behind(tmp_path):
    path = tmp_path / "webgui_auth.json"
    auth_store.save(auth_store.Credentials("h", "s", "k", 1, 0), path)
    auth_store.save(auth_store.Credentials("h2", "s2", "k2", 2, 5), path)
    assert json.loads(path.read_text(encoding="utf-8"))["epoch"] == 2
    assert list(p.name for p in tmp_path.iterdir()) == ["webgui_auth.json"]
