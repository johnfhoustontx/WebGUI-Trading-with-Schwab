"""Pure credential logic for the webgui login: password, TOTP, tokens, lockout.

Everything here is a pure function over its arguments plus the credentials
dataclass, so it tests without a server, a browser or a clock monkeypatch.
"""
from __future__ import annotations

import hmac
import time

import argon2
import pyotp

# Argon2id parameters, deliberately BELOW argon2-cffi's defaults (t=3, 64 MiB, p=4).
#
# Argon2 is expensive on purpose, and on a PUBLIC endpoint that is also an
# amplifier: ten concurrent POSTs at the default would take ~640 MB and all four
# cores. Measured on the prod box, stream hours leave ~1.7 idle cores and there is
# NO SWAP, so a spike does not degrade -- it gets OOM-killed, and the visible
# symptom is the public YouTube broadcast dropping frames.
#
# 19 MiB / t=2 / p=1 is OWASP's floor and remains far beyond what one strong
# single-user password needs. Do not "restore the defaults" without also reading
# the Load and exposure section of the design doc.
TIME_COST = 2
MEMORY_COST_KIB = 19456        # 19 MiB
PARALLELISM = 1

_hasher = argon2.PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST_KIB,
    parallelism=PARALLELISM,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """True when the password matches. Fails CLOSED on a malformed hash."""
    try:
        return bool(_hasher.verify(stored_hash, password))
    except Exception:       # noqa: BLE001 - every failure mode means "no".
        return False


TOTP_PERIOD_SEC = 30
TOTP_DRIFT_STEPS = 1           # +/- one 30 s window


def verify_totp(secret: str, code: str | None, *, now: float | None = None,
                last_counter: int = 0) -> tuple[bool, int]:
    """Verify a TOTP code. Returns ``(ok, counter)``.

    ``counter`` is the accepted time step, which the caller MUST persist as the
    new ``last_counter``. Rejecting ``counter <= last_counter`` is what stops a
    code being replayed inside its own 30 s window -- ``pyotp.TOTP.verify`` alone
    would happily accept the same code repeatedly for 30 seconds, and with drift
    enabled, for 90.

    On rejection the caller's ``last_counter`` is handed straight back, so
    persisting the result unconditionally can never rewind the replay guard.
    Leading and trailing whitespace is accepted; see the comment below.
    """
    if not isinstance(code, str):
        return False, last_counter

    # DECIDED: padded input is accepted. Pasting a code out of an authenticator
    # app routinely brings a trailing space, and the only feedback the login
    # screen may give is the generic "Sign-in failed" -- so the user sees a
    # correct code rejected for no stated reason, on the one screen where that
    # is least affordable. Stripping widens the accepted set by nothing: exactly
    # one six-digit value still passes. An INTERNAL space is not padding and is
    # still refused, since that is a different string, not a badly-copied one.
    code = code.strip()

    # isascii() before isdigit(): str.isdigit() is True for fullwidth and other
    # Unicode digits, and hmac.compare_digest RAISES TypeError on a non-ASCII
    # str -- which would turn attacker-controlled input into a 500 on the login
    # route rather than a refusal.
    if len(code) != 6 or not (code.isascii() and code.isdigit()):
        return False, last_counter

    at = time.time() if now is None else now
    totp = pyotp.TOTP(secret, interval=TOTP_PERIOD_SEC)
    for step in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        moment = at + step * TOTP_PERIOD_SEC
        counter = int(moment // TOTP_PERIOD_SEC)
        if counter <= last_counter:
            continue
        if hmac.compare_digest(totp.at(moment), code):
            return True, counter
    return False, last_counter


# ---------------------------------------------------------------------------
# Session and remember-device tokens.
#
# Both cookies carry the SAME kind of stateless bearer token; only the max age
# the verifier applies differs -- 12 h for the session cookie, 30 days for the
# "trust this device" cookie that lets a later login skip the TOTP prompt.
#
# There is deliberately NO server-side registry of issued tokens: nothing to
# expire, leak, or keep in sync with the credentials file. The accepted cost is
# that revocation is all-or-nothing -- bump ``epoch`` and every outstanding token
# on every device dies at once ("sign out everywhere"), then re-trust your two or
# three machines. For one user that beats maintaining a device table.
#
# CLOCK (decided -- please do not re-litigate). This signs with the UNTIMED
# ``URLSafeSerializer`` and stamps ``issued_at`` into the payload itself, rather
# than using ``URLSafeTimedSerializer``, which reads ``time.time()`` inside the
# library and gives the caller no way to pass an instant in. Owning the timestamp
# is what lets ``now=`` be a real argument, so the expiry rules below are plain
# arithmetic over a value this module chose. The alternative is monkeypatching
# the module-level ``time`` inside ``itsdangerous.timed``, which tests the
# library's clock as much as ours -- and this repo has an expensive precedent for
# tests that end up pinning whatever the code happens to do
# (``test_adx_uses_wilder_smoothing`` pinned a wrong ADX for years). An expiry
# check on an internet-facing login is the last place that should happen.
import math

import itsdangerous

SESSION_MAX_AGE_SEC = 12 * 3600
REMEMBER_MAX_AGE_SEC = 30 * 24 * 3600

# Mixed into the HMAC key derivation, so changing it invalidates every
# outstanding token exactly as an epoch bump does. Version it; never edit it.
_SALT = "webgui-auth-v1"


def _serializer(key: str) -> itsdangerous.URLSafeSerializer:
    return itsdangerous.URLSafeSerializer(key, salt=_SALT)


def mint_token(key: str, *, epoch: int, now: float | None = None) -> str:
    """A stateless bearer token carrying only the epoch it was issued under.

    There is NO server-side token registry on purpose: nothing to expire, leak or
    keep in sync. The cost, stated in the design, is that revocation is
    all-or-nothing -- bump ``epoch`` and re-trust your devices.

    The payload is signed, not encrypted, so whoever holds the token can read the
    epoch and the issue time. Neither is a secret; the security is that the token
    cannot be PRODUCED without ``key``.
    """
    at = time.time() if now is None else now
    return _serializer(key).dumps({"epoch": int(epoch), "iat": float(at)})


def verify_token(token: str | None, key: str, *, epoch: int,
                 max_age_sec: int, now: float | None = None) -> bool:
    """True only for an untampered, unexpired token issued under ``epoch``.

    Never raises on the TOKEN, whatever it contains: that argument is fully
    attacker-controlled cookie input on a public endpoint, where an exception is
    a 500 rather than a refusal. ``key`` and ``epoch`` come from our own
    credentials file and are deliberately NOT defended -- a malformed one is a
    bug that should be loud, not a login that quietly fails shut.
    """
    if not isinstance(token, str) or not token:
        return False
    try:
        payload = _serializer(key).loads(token)
    except (itsdangerous.BadData, ValueError):
        # BadData covers every "this is not our token" case. ValueError is here
        # for UnicodeEncodeError, which is NOT a BadData subclass: itsdangerous
        # encodes to UTF-8 before it can judge the value, so a lone surrogate in
        # a cookie would otherwise escape as a 500 on the login route.
        return False
    if not isinstance(payload, dict) or payload.get("epoch") != int(epoch):
        return False

    issued_at = payload.get("iat")
    if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)) or not math.isfinite(issued_at):
        return False

    age = (time.time() if now is None else now) - issued_at
    # Both bounds, stated as one range on purpose. A NEGATIVE age means the token
    # is dated into the future -- a clock that has since moved backwards, and the
    # shape anyone trying to stretch a 12 h session would aim for -- so it is
    # refused rather than tolerated.
    #
    # This range spelling is ALSO what refuses a non-finite age today, since
    # every comparison against NaN is False. Measured, so state it accurately:
    # the ``math.isfinite`` clause above is redundant while this line reads as it
    # does, and is kept as belt-and-braces for the obvious future refactor --
    # ``if age > max_age_sec: return False`` then ``return True`` -- which would
    # otherwise hand a NaN-dated token a pass that never expires.
    return 0 <= age <= max_age_sec
