"""Session / remember-device token minting and verification.

These tests pass ``now=`` explicitly and never patch a clock -- see the CLOCK
comment in ``auth.py`` for why the module stamps its own ``issued_at`` instead of
leaning on ``URLSafeTimedSerializer``'s internal ``time.time()``.
"""
import itsdangerous

import auth

KEY = "k" * 43
OTHER_KEY = "j" * 43
T0 = 1_757_000_000


def _flip_last(token: str) -> str:
    """Change the final character to something it definitely is not.

    ``token[:-1] + "X"`` is a no-op whenever the token already ends in ``X``, and
    a tamper test that silently stops tampering passes for the wrong reason.
    """
    return token[:-1] + ("Y" if token[-1] == "X" else "X")


def test_a_fresh_token_verifies():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is True


def test_a_tampered_token_is_refused():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    tampered = _flip_last(tok)
    assert tampered != tok
    assert auth.verify_token(tampered, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_tampering_with_the_payload_half_is_refused():
    """The signature is over the payload, so editing the payload must break it."""
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    body, _, sig = tok.rpartition(".")
    tampered = _flip_last(body) + "." + sig
    assert auth.verify_token(tampered, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_a_token_signed_with_another_key_is_refused():
    tok = auth.mint_token(OTHER_KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_the_two_keys_really_do_produce_different_tokens():
    """Guards the check above against a salt that swallowed the key."""
    assert auth.mint_token(KEY, epoch=1, now=T0) != auth.mint_token(OTHER_KEY, epoch=1, now=T0)


def test_an_expired_token_is_refused():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=60, now=T0 + 61) is False


def test_a_token_verifies_right_up_to_its_max_age():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=60, now=T0 + 60) is True


def test_a_token_from_the_future_is_refused():
    """A backwards clock jump must not mint a token that outlives its window.

    The signature cannot be forged, but a token minted while the box's clock was
    ahead is a real thing -- and dating it forward is exactly how you would try to
    stretch a 12 h session into a longer one.
    """
    tok = auth.mint_token(KEY, epoch=1, now=T0 + 5)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_bumping_the_epoch_invalidates_every_outstanding_token():
    """This is 'sign out everywhere'. It is the ONLY revocation this design has."""
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=2, max_age_sec=3600, now=T0) is False


def test_an_older_epoch_is_refused_too():
    tok = auth.mint_token(KEY, epoch=3, now=T0)
    assert auth.verify_token(tok, KEY, epoch=2, max_age_sec=3600, now=T0) is False


def test_garbage_is_refused_without_raising():
    for junk in ("", "x", "a.b.c", None, b"abc", 17, [], "z" * 200_000):
        assert auth.verify_token(junk, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_a_lone_surrogate_is_refused_without_raising():
    """itsdangerous encodes to UTF-8 before it can decide the value is bad.

    A lone surrogate therefore raises UnicodeEncodeError -- a ValueError, NOT a
    subclass of BadData -- so catching BadData alone would turn this input into a
    500 on the login route instead of a refusal.
    """
    assert auth.verify_token("\ud800", KEY, epoch=1, max_age_sec=3600, now=T0) is False
    assert auth.verify_token("\ud800.\ud800", KEY, epoch=1, max_age_sec=3600, now=T0) is False


def _sign(payload, key=KEY):
    """Mint a token around an ARBITRARY payload, with a genuine signature."""
    return itsdangerous.URLSafeSerializer(key, salt=auth._SALT).dumps(payload)


def test_a_validly_signed_non_dict_payload_is_refused():
    for payload in ([1, 2], "epoch", 1, None):
        assert auth.verify_token(_sign(payload), KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_a_validly_signed_payload_with_no_issued_at_is_refused():
    assert auth.verify_token(_sign({"epoch": 1}), KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_a_non_finite_issued_at_is_refused():
    """NaN survives the signer intact, and every comparison against it is False.

    Written down because this repo has five separate incidents of a non-finite
    value reading as a confident answer: an expiry check spelled ``if age >
    max_age: return False`` would ACCEPT this token forever.
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        tok = _sign({"epoch": 1, "iat": bad})
        assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_a_non_numeric_issued_at_is_refused():
    for bad in ("1757000000", None, [], {"t": 1}, True):
        tok = _sign({"epoch": 1, "iat": bad})
        assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_the_two_cookie_lifetimes_are_the_documented_ones():
    assert auth.SESSION_MAX_AGE_SEC == 12 * 3600
    assert auth.REMEMBER_MAX_AGE_SEC == 30 * 24 * 3600


def test_now_defaults_to_the_wall_clock():
    """The only place the real clock is used, so it is the only place to pin it."""
    tok = auth.mint_token(KEY, epoch=1)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=60) is True
