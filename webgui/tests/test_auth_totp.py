import pyotp
import pytest

import auth

SECRET = "JBSWY3DPEHPK3PXP"
T0 = 1_757_000_000          # an arbitrary fixed instant


def _code(at):
    return pyotp.TOTP(SECRET).at(at)


def test_the_current_code_is_accepted():
    ok, counter = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=0)
    assert ok is True
    assert counter == T0 // 30


def test_the_previous_and_next_windows_are_accepted_for_clock_drift():
    for offset in (-30, 30):
        ok, _ = auth.verify_totp(SECRET, _code(T0 + offset), now=T0, last_counter=0)
        assert ok is True, f"offset {offset}s should be within drift"


def test_two_windows_away_is_rejected():
    ok, _ = auth.verify_totp(SECRET, _code(T0 + 90), now=T0, last_counter=0)
    assert ok is False


def test_a_code_cannot_be_replayed_inside_its_own_window():
    ok, counter = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=0)
    assert ok is True
    again, _ = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=counter)
    assert again is False, "the same counter must not be accepted twice"


def test_an_older_counter_is_rejected_even_though_it_is_within_drift():
    # Someone who shoulder-surfed the previous code must not be able to use it
    # after you have used the current one.
    used = T0 // 30
    ok, _ = auth.verify_totp(SECRET, _code(T0 - 30), now=T0, last_counter=used)
    assert ok is False


def test_the_drift_window_is_what_the_older_counter_test_rests_on():
    """Pins that the previous test fails on the REPLAY guard, not on drift.

    The same code, same instant, differing only in ``last_counter``, must be
    accepted -- otherwise the test above would pass merely because -30s was out
    of range, and the guard it claims to exercise would be untested.
    """
    ok, counter = auth.verify_totp(SECRET, _code(T0 - 30), now=T0, last_counter=0)
    assert ok is True
    assert counter == (T0 - 30) // 30


def test_a_rejection_hands_back_the_unchanged_counter():
    """The caller persists whatever comes back, so a failure must not rewind it."""
    used = T0 // 30
    ok, counter = auth.verify_totp(SECRET, "000000", now=T0, last_counter=used)
    assert ok is False
    assert counter == used


def test_a_code_padded_with_whitespace_is_accepted():
    """Decided: pasting from an authenticator app often brings a trailing space.

    The failure would be invisible -- the login screen shows only the generic
    "Sign-in failed" -- and admitting it widens the accepted set by nothing.
    """
    ok, _ = auth.verify_totp(SECRET, f"  {_code(T0)}  ", now=T0, last_counter=0)
    assert ok is True


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "abc",
        "12345",
        "1234567",
        "12 456",          # an internal space is NOT whitespace padding
        "１２３４５６",  # fullwidth digits: str.isdigit() says True
        None,
        123456,
    ],
)
def test_malformed_input_is_rejected_without_raising(junk):
    ok, _ = auth.verify_totp(SECRET, junk, now=T0, last_counter=0)
    assert ok is False
