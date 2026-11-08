"""The Status page's Schwab sign-in clock (2026-11-08).

The proxy card's "Auth: authorized — sign-in lasts another 7 hours" became a
countdown that ticks every second: green while it runs, yellow under
``[system] status_clock_warn_hours`` (6) left, red once it has expired. These
are the pure halves; the rendered clock is tested in ``test_status.py``.
"""
import datetime as _dt

import pytest

from pages import status
from pages.options import theme

UTC = _dt.timezone.utc
NOW = _dt.datetime(2026, 11, 8, 15, 0, 0, tzinfo=UTC)


# ── the clock's face ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("seconds, text", [
    (6 * 86400 + 22 * 3600 + 14 * 60 + 5, "6d 22:14:05"),
    (86400, "1d 00:00:00"),
    (86399.9, "23:59:59"),          # rounded DOWN, never up to a day
    (5 * 3600 + 59 * 60 + 12, "05:59:12"),
    (59, "00:00:59"),
    (0, "00:00:00"),
    (-30, "00:00:00"),
])
def test_countdown_text(seconds, text):
    assert status.countdown_text(seconds) == text


@pytest.mark.parametrize("seconds, level", [
    (7 * 86400, "ok"),
    (6 * 3600, "ok"),               # exactly six hours is not yet "under six"
    (6 * 3600 - 1, "warn"),
    (1, "warn"),
    (0, "expired"),
    (-1, "expired"),
])
def test_countdown_level(seconds, level):
    assert status.countdown_level(seconds, 6) == level


def test_each_level_has_its_own_colour():
    green = status.auth_clock_view(NOW + _dt.timedelta(days=3), NOW, 6)
    yellow = status.auth_clock_view(NOW + _dt.timedelta(hours=5), NOW, 6)
    red = status.auth_clock_view(NOW - _dt.timedelta(seconds=1), NOW, 6)
    assert theme.TXT_POS in green[1] and green[0] == "Auth: 3d 00:00:00 left"
    assert theme.TXT_WARN in yellow[1] and yellow[0] == "Auth: 05:00:00 left"
    assert theme.TXT_NEG in red[1] and red[0] == status.CLOCK_EXPIRED_TEXT
    assert len({green[1], yellow[1], red[1]}) == 3


def test_the_yellow_line_follows_the_setting():
    five_hours = NOW + _dt.timedelta(hours=5)
    assert theme.TXT_POS in status.auth_clock_view(five_hours, NOW, 4)[1]
    assert theme.TXT_WARN in status.auth_clock_view(five_hours, NOW, 6)[1]


# ── where the deadline comes from ────────────────────────────────────────────
def test_the_deadline_is_the_proxys_own_stamp():
    """The stamp, not the hours: the hours are rounded to 36 seconds, and a
    deadline rebuilt from them would jump on every sweep."""
    health = {"refresh_token_expires_at": "2026-11-10T09:30:00Z",
              "refresh_token_hours_left": 1.0}
    assert status.sign_in_deadline(health, NOW) == \
        _dt.datetime(2026, 11, 10, 9, 30, tzinfo=UTC)


def test_a_naive_stamp_is_utc_not_central():
    """The proxy writes ``utcnow().isoformat()``. Read as Central it would be
    off by five or six hours, which is the whole yellow band."""
    health = {"refresh_token_expires_at": "2026-11-10T09:30:00"}
    assert status.sign_in_deadline(health, NOW) == \
        _dt.datetime(2026, 11, 10, 9, 30, tzinfo=UTC)


def test_the_hours_are_the_fallback():
    health = {"refresh_token_hours_left": 2.5}
    assert status.sign_in_deadline(health, NOW) == NOW + _dt.timedelta(hours=2.5)
    garbled = {"refresh_token_expires_at": "soon", "refresh_token_hours_left": 2.5}
    assert status.sign_in_deadline(garbled, NOW) == NOW + _dt.timedelta(hours=2.5)


@pytest.mark.parametrize("health", [
    None, {}, {"refresh_token_expires_at": ""},
    {"refresh_token_hours_left": None}, {"refresh_token_hours_left": "12"},
    {"refresh_token_hours_left": True}, {"refresh_token_hours_left": float("nan")},
    {"refresh_token_hours_left": -3},
])
def test_no_deadline_without_a_reading(health):
    """An unknown must never read as zero OR as plenty."""
    assert status.sign_in_deadline(health, NOW) is None


# ── the probe carries the deadline only for a good sign-in ───────────────────
_GOOD = {"up": True, "has_token": True, "refresh_token_expired": False,
         "refresh_token_rejected": False, "token_expired": False,
         "refresh_token_expires_at": "2026-11-10T09:30:00Z",
         "refresh_token_hours_left": 42.5}


def test_a_good_sign_in_carries_its_deadline():
    out = status._probe_one({"key": "schwab_auth", "kind": "auth"},
                            proxy_health=dict(_GOOD))
    assert out["up"] is True
    assert out["expires_at"] == "2026-11-10T09:30:00+00:00"


def test_a_rejected_sign_in_carries_no_deadline():
    """The stamp said 165 hours while Schwab was rejecting the token: that
    card stays a red sentence, never a green clock."""
    out = status._probe_one({"key": "schwab_auth", "kind": "auth"},
                            proxy_health={**_GOOD, "refresh_token_rejected": True})
    assert out["up"] is False
    assert out["expires_at"] is None


# ── the setting ──────────────────────────────────────────────────────────────
def test_the_warn_hours_are_read_from_config(monkeypatch):
    monkeypatch.setattr(status, "_notify_load",
                        lambda: {"system": {"status_clock_warn_hours": 12}})
    assert status.clock_warn_hours() == 12


@pytest.mark.parametrize("raw", [None, "6", True, 0, 169, float("nan"), -1])
def test_an_unusable_warn_setting_is_the_default(monkeypatch, raw):
    monkeypatch.setattr(status, "_notify_load",
                        lambda: {"system": {"status_clock_warn_hours": raw}})
    assert status.clock_warn_hours() == 6


def test_the_shipped_file_says_six():
    status._notify_reset()
    assert status.clock_warn_hours() == 6
