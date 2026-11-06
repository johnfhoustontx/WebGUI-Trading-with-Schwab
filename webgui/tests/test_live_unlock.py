"""live_unlock.py: the authenticator code before the first live Send of the day."""
import datetime as _dt
import json
from zoneinfo import ZoneInfo

import pyotp
import pytest

import auth
import auth_store
import live_unlock
import login_page
from shared import live_orders_config

CT = ZoneInfo("America/Chicago")
T0 = _dt.datetime(2026, 11, 6, 9, 0, tzinfo=CT).timestamp()
SECRET = "JBSWY3DPEHPK3PXP" * 2
CLIENT = "203.0.113.7"


@pytest.fixture(autouse=True)
def _fresh_lockout():
    login_page.reset_lockout()
    yield
    login_page.reset_lockout()


@pytest.fixture
def creds(tmp_path, monkeypatch):
    c = auth_store.Credentials(password_hash=auth.hash_password("pw"), totp_secret=SECRET,
                               session_secret="k" * 43)
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    monkeypatch.setattr(live_orders_config, "load",
                        lambda: {**live_orders_config.DEFAULTS, "totp": "daily"})
    return c


def _code(at):
    return pyotp.TOTP(SECRET).at(at)


def test_asked_on_the_first_send_and_not_again_that_day(creds):
    assert live_unlock.needed(T0)
    assert live_unlock.unlock(_code(T0), CLIENT, T0)
    assert not live_unlock.needed(T0 + 60)
    assert not live_unlock.needed(_dt.datetime(2026, 11, 6, 23, 59, tzinfo=CT).timestamp())


def test_asked_again_after_midnight_central(creds):
    live_unlock.unlock(_code(T0), CLIENT, T0)
    assert live_unlock.needed(_dt.datetime(2026, 11, 7, 0, 1, tzinfo=CT).timestamp())


def test_every_send_asks_every_time(creds, monkeypatch):
    monkeypatch.setattr(live_orders_config, "load",
                        lambda: {**live_orders_config.DEFAULTS, "totp": "every_send"})
    live_unlock.unlock(_code(T0), CLIENT, T0)
    assert live_unlock.needed(T0 + 1)


def test_the_unlock_is_stored_server_side_and_advances_the_replay_guard(creds):
    live_unlock.unlock(_code(T0), CLIENT, T0)
    stored = auth_store.load()
    assert stored.live_unlock_date == "2026-11-06"
    assert stored.last_totp_counter > 0
    # The same code cannot be used again inside its window, here or at the login.
    assert not live_unlock.unlock(_code(T0), CLIENT, T0 + 5)


def test_a_wrong_code_counts_toward_the_sign_in_lockout(creds):
    assert not live_unlock.unlock("000000", CLIENT, T0)
    assert login_page.lockout_state().tracked_failures(CLIENT) == 1
    assert live_unlock.needed(T0)


def test_a_locked_out_address_cannot_try(creds):
    at = T0
    for i in range(60):
        at = T0 + i
        live_unlock.unlock("000000", CLIENT, at)
        if login_page.lockout_state().client_locked_until(CLIENT, now=at):
            break
    assert login_page.lockout_state().client_locked_until(CLIENT, now=at)
    assert not live_unlock.unlock(_code(at), CLIENT, at)


def test_no_credentials_means_asked_and_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", tmp_path / "absent.json")
    assert live_unlock.needed(T0)
    assert not live_unlock.unlock("123456", CLIENT, T0)


def test_an_older_credentials_file_reads_with_no_unlock(tmp_path, monkeypatch):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"password_hash": "h", "totp_secret": SECRET,
                                "session_secret": "s", "epoch": 1,
                                "last_totp_counter": 5}), encoding="utf-8")
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    assert auth_store.load().live_unlock_date == ""
