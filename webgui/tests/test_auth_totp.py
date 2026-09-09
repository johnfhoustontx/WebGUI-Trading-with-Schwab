import logging

import pyotp
import pytest

import auth

SECRET = "JBSWY3DPEHPK3PXP"
T0 = 1_757_000_000          # an arbitrary fixed instant
STEP = auth.TOTP_PERIOD_SEC


def _code(at):
    return pyotp.TOTP(SECRET).at(at)


def test_the_current_code_is_accepted():
    ok, counter = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=0)
    assert ok is True
    assert counter == T0 // 30


@pytest.mark.parametrize("offset", [-30, 30])
def test_the_previous_and_next_windows_are_accepted_for_clock_drift(offset):
    ok, counter = auth.verify_totp(SECRET, _code(T0 + offset), now=T0, last_counter=0)
    assert ok is True, f"offset {offset}s should be within drift"
    # Pin the counter, not just the verdict. The caller persists whatever comes
    # back as the new ``last_counter``, so a value that is too LOW silently
    # re-opens the window a replayed code could arrive in.
    assert counter == (T0 + offset) // 30


@pytest.mark.parametrize("delta", [-2, 2])
def test_the_drift_window_is_exactly_one_step(delta):
    """+/-1 step is the whole attack surface; two steps away must be refused.

    Stated in COUNTER units, not seconds. ``T0`` sits 20 s into its 30 s window,
    so "+90 seconds" is three steps away, not two -- the +/-2 boundary that
    ``TOTP_DRIFT_STEPS`` actually controls was never exercised by the old
    seconds-based test, and widening the constant to 2 left every test green.
    """
    at = (T0 // STEP + delta) * STEP
    ok, _ = auth.verify_totp(SECRET, _code(at), now=T0, last_counter=0)
    assert ok is False


def test_three_windows_away_is_rejected():
    """Renamed: ``T0 + 90`` is THREE counter steps from ``T0``, not two."""
    assert (T0 + 90) // STEP - T0 // STEP == 3
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


# ---------------------------------------------------------------------------
# An unusable secret must fail CLOSED.
#
# ``base64.b32decode("")`` is ``b""`` -- a perfectly valid, empty HMAC key. So an
# empty secret does not disable the second factor, it replaces it with a sequence
# anyone can compute from the clock alone.

def test_the_empty_secret_code_an_attacker_can_compute_is_refused():
    """THE attack: no shared secret is needed to produce this code."""
    public = pyotp.TOTP("").at(T0)
    assert len(public) == 6            # it really is a well-formed code
    ok, counter = auth.verify_totp("", public, now=T0, last_counter=0)
    assert ok is False
    assert counter == 0


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "JBSWY3DP",                    # 8 chars / 40 bits -- brute-forceable
        "A" * (auth.MIN_TOTP_SECRET_LEN - 1),
    ],
)
def test_a_too_short_secret_will_not_authenticate_its_OWN_code(secret):
    """These decode fine, so the code is real -- it is just not worth anything.

    Feeding each secret its own code is what makes this discriminating: a test
    that passed some unrelated "123456" would go green with the guard deleted,
    because a wrong code is refused anyway.
    """
    ok, counter = auth.verify_totp(secret, pyotp.TOTP(secret).at(T0), now=T0, last_counter=7)
    assert ok is False
    assert counter == 7


@pytest.mark.parametrize(
    "secret",
    [
        "not-base32!",
        "not-base32!!!!!!!!!!!!!",      # long enough to reach the DECODE check
        None,
        123456,
    ],
)
def test_a_secret_pyotp_cannot_decode_refuses_rather_than_raising(secret):
    """A truncated or hand-edited credentials file must not 500 the login page."""
    ok, counter = auth.verify_totp(secret, "123456", now=T0, last_counter=7)
    assert ok is False
    assert counter == 7


def test_a_secret_of_exactly_the_minimum_length_is_usable():
    """Guards the length floor against being set above the fixture it must admit."""
    assert len(SECRET) == auth.MIN_TOTP_SECRET_LEN
    ok, _ = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=0)
    assert ok is True


def test_a_pyotp_generated_secret_is_usable():
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).at(T0)
    ok, _ = auth.verify_totp(secret, code, now=T0, last_counter=0)
    assert ok is True


def test_an_unusable_secret_says_so_in_the_log(caplog):
    """Single user, single UI: a silent refusal here is an unexplained lockout."""
    with caplog.at_level(logging.WARNING, logger="auth"):
        auth.verify_totp("", "123456", now=T0, last_counter=0)
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_an_ordinary_wrong_code_does_not_warn(caplog):
    """The log line must mean 'your credentials file is broken', not 'typo'."""
    with caplog.at_level(logging.WARNING, logger="auth"):
        auth.verify_totp(SECRET, "000000", now=T0, last_counter=0)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
