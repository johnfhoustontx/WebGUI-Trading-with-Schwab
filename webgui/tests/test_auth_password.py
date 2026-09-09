import logging

import auth


def test_a_hash_verifies_against_its_own_password():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password(h, "correct horse battery staple") is True


def test_a_wrong_password_does_not_verify():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password(h, "Correct horse battery staple") is False


def test_a_corrupt_hash_returns_false_rather_than_raising():
    # A truncated file must fail closed, not 500 the login route.
    assert auth.verify_password("not-a-hash", "anything") is False


def test_two_hashes_of_one_password_differ():
    a = auth.hash_password("same")
    b = auth.hash_password("same")
    assert a != b, "each hash must carry its own salt"


def test_hash_uses_the_tuned_low_memory_parameters():
    """The design's mitigation 3: 19 MiB / t=2, not argon2-cffi's 64 MiB default.

    On a public endpoint the default is a 3.4x memory amplifier, and this box has
    ~1.7 free cores and NO SWAP during stream hours.
    """
    h = auth.hash_password("x")
    assert "$argon2id$" in h
    assert "m=19456" in h and "t=2" in h


def test_a_corrupt_hash_says_so_in_the_log(caplog):
    """A truncated credentials file locks the only user out of the only UI that
    can stop the stack. The login screen shows the same generic "Sign-in failed"
    a typo produces, so the log line is the ONLY place the difference exists."""
    with caplog.at_level(logging.WARNING, logger="auth"):
        assert auth.verify_password("not-a-hash", "anything") is False
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "an unusable stored hash must leave a trace"
    assert warnings[0].exc_info is not None, "log the traceback, not just a line"


def test_an_ordinary_wrong_password_does_not_warn(caplog):
    """Otherwise the warning means nothing: every failed login would emit one."""
    h = auth.hash_password("correct horse battery staple")
    with caplog.at_level(logging.WARNING, logger="auth"):
        assert auth.verify_password(h, "wrong") is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
