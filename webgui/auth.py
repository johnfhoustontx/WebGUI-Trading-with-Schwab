"""Pure credential logic for the webgui login: password, TOTP, tokens, lockout.

Everything here is a pure function over its arguments plus the credentials
dataclass, so it tests without a server, a browser or a clock monkeypatch. The
module logger is the one permitted side effect, and it fires only on the two
"your credentials file is broken" paths -- never on an ordinary wrong password
or a mistyped code, or the warning would mean nothing.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
import math
import time

import argon2
import itsdangerous
import pyotp

log = logging.getLogger(__name__)

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
    """True when the password matches. Fails CLOSED on a malformed hash.

    The security decision is the same either way -- "no" -- but the two causes are
    not. argon2-cffi separates them: ``VerifyMismatchError``/``VerificationError``
    is the ordinary wrong password, ``InvalidHashError`` means the stored hash
    cannot be parsed at all. On a SINGLE-USER app that second case is a truncated
    or hand-edited ``shared/webgui_auth.json`` locking the only user out of the
    only UI that can stop the stack -- and the login screen shows the same generic
    "Sign-in failed" a typo produces, so this log line is the only place the
    difference exists. Warn on that one alone; warning on both would make the
    signal indistinguishable from someone fat-fingering their password.
    """
    try:
        return bool(_hasher.verify(stored_hash, password))
    except argon2.exceptions.InvalidHashError:
        log.warning(
            "Stored password hash is unparseable -- refusing every password. "
            "The credentials file is corrupt, not the password.", exc_info=True)
        return False
    except Exception:       # noqa: BLE001 - every other failure mode means "no".
        return False


TOTP_PERIOD_SEC = 30
TOTP_DRIFT_STEPS = 1           # +/- one 30 s window

# 16 base32 chars = 80 bits. ``pyotp.random_base32()`` emits 32 chars / 160 bits,
# so a real secret clears this by a wide margin and the floor only ever catches a
# truncated or hand-typed one.
MIN_TOTP_SECRET_LEN = 16


def _usable_secret(secret: str | None) -> bool:
    """A secret we cannot use must REFUSE -- never accept, never raise.

    base32-decoding "" succeeds and yields an empty HMAC key, so pyotp derives a
    publicly computable code from it. That is a fail-OPEN, not a fail-closed:
    login keeps working and the second factor is silently gone. A very short
    secret is the same failure with more arithmetic in front of it.

    The decode mirrors ``pyotp.OTP.byte_secret`` exactly -- same padding, same
    casefold -- so this predicate accepts precisely the set pyotp would, and a
    secret that clears it cannot then raise ``binascii.Error`` out of the login
    route. Pure: the caller does the logging.
    """
    if not isinstance(secret, str) or len(secret) < MIN_TOTP_SECRET_LEN:
        return False
    padded = secret + "=" * (-len(secret) % 8)
    try:
        base64.b32decode(padded, casefold=True)
    except (binascii.Error, ValueError):    # binascii.Error IS a ValueError.
        return False
    return True


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

    # AFTER the format checks and BEFORE any comparison. An unusable secret is a
    # broken credentials file, not a failed login, so it is worth a log line --
    # but only once the input is well-formed, or a bot spraying junk at the login
    # route would fill the file with them.
    if not _usable_secret(secret):
        log.warning(
            "TOTP secret is unusable (empty, shorter than %d chars, or not "
            "base32) -- refusing every code. An empty secret does not disable "
            "the second factor, it makes the code publicly computable, so this "
            "refuses rather than degrades.", MIN_TOTP_SECRET_LEN)
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
# Both cookies carry the same KIND of stateless bearer token, but the payload
# names which one it is and the verifier demands a match. That discriminator is
# load-bearing, not bookkeeping: without it the two are byte-identical and the
# only thing separating a 12 h session from a 30-day "trust this device" cookie
# is which ``max_age_sec`` the verifier happens to pass. The remember cookie is
# the one that sits on disk for a month, and its INTENDED power is merely to skip
# the TOTP prompt -- so stealing it and replaying it in the session slot would
# otherwise hand over a full authenticated session with no password and no TOTP,
# silently promoting the weaker, longer-lived credential into the stronger one.
#
# ``kind`` is keyword-only with NO default on both functions, on purpose. A
# default is exactly how a future call site would re-open this hole without
# anyone noticing the omission at the call.
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

KIND_SESSION = "session"
KIND_REMEMBER = "remember"

SESSION_MAX_AGE_SEC = 12 * 3600
REMEMBER_MAX_AGE_SEC = 30 * 24 * 3600

# Mixed into the HMAC key derivation, so changing it invalidates every
# outstanding token exactly as an epoch bump does. Version it; never edit it.
_SALT = "webgui-auth-v1"


def _serializer(key: str) -> itsdangerous.URLSafeSerializer:
    return itsdangerous.URLSafeSerializer(key, salt=_SALT)


def mint_token(key: str, *, kind: str, epoch: int, now: float | None = None) -> str:
    """A stateless bearer token naming its kind and the epoch it was issued under.

    ``kind`` is ``KIND_SESSION`` or ``KIND_REMEMBER`` and is what stops the
    long-lived remember-device cookie being replayed as a session -- see the
    section comment above.

    There is NO server-side token registry on purpose: nothing to expire, leak or
    keep in sync. The cost, stated in the design, is that revocation is
    all-or-nothing -- bump ``epoch`` and re-trust your devices.

    The payload is signed, not encrypted, so whoever holds the token can read the
    kind, the epoch and the issue time. None is a secret; the security is that the
    token cannot be PRODUCED without ``key``.
    """
    at = time.time() if now is None else now
    return _serializer(key).dumps(
        {"kind": kind, "epoch": int(epoch), "iat": float(at)})


def verify_token(token: str | None, key: str, *, kind: str, epoch: int,
                 max_age_sec: int, now: float | None = None) -> bool:
    """True only for an untampered, unexpired ``kind`` token issued under ``epoch``.

    Never raises on the TOKEN, whatever it contains: that argument is fully
    attacker-controlled cookie input on a public endpoint, where an exception is
    a 500 rather than a refusal. ``key``, ``kind`` and ``epoch`` come from our own
    code and credentials file and are deliberately NOT defended -- a malformed one
    is a bug that should be loud, not a login that quietly fails shut.
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
    # A token minted before ``kind`` existed has no such key, so ``.get`` returns
    # None and it is refused -- which is the correct treatment for the ambiguous
    # bytes this discriminator was added to disambiguate.
    if payload.get("kind") != kind:
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
