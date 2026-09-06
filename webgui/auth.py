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
