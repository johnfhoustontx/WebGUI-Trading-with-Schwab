"""The authenticator code that unlocks live orders (live-order design, Phase 1).

``config/live_orders.toml totp`` says when it is asked: ``daily`` (before the
first Send of each Central day) or ``every_send``. A right code is recorded in
the app's credential store (``auth_store``, field ``live_unlock_date``), never
in the browser, so a reload or a second tab on the same sign-in stays unlocked
for the rest of that day.

It is the sign-in's own second factor, held to the sign-in's own rules:

* ``auth.verify_totp`` with the stored ``last_totp_counter``, which is advanced
  on every accepted code, so a code used here cannot be used again (here or at
  the login) inside its 30-second window;
* a wrong code counts toward the sign-in's failure lockout
  (``login_page.lockout_state``), and a locked-out address cannot try at all,
  so the code box cannot be guessed at.

Lives beside ``auth.py`` and ``login_page.py``: the credential primitives stay
in these files and nowhere else.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import time
from zoneinfo import ZoneInfo

import auth
import auth_store
import login_page
from shared import live_orders_config

log = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")

#: What the page shows on a refusal: one sentence for every reason, as the
#: sign-in does, so the box tells a guesser nothing.
REFUSED = "That code was not accepted."


def _today(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _CT).date().isoformat()


def needed(now: float | None = None) -> bool:
    """Whether the next Send must ask for the code."""
    at = time.time() if now is None else now
    if live_orders_config.totp_mode() == "every_send":
        return True
    try:
        creds = auth_store.load()
    except auth_store.CredentialsError:
        return True
    return creds is None or creds.live_unlock_date != _today(at)


def unlock(code: str | None, client: str, now: float | None = None) -> bool:
    """Check ``code``; on a right one, record today's unlock. Never raises."""
    at = time.time() if now is None else now
    lockout = login_page.lockout_state()
    if lockout.client_locked_until(client, now=at):
        lockout.record_failure(client, now=at, hashed=False)
        log.warning("Live-order code refused for %s (locked out)", client)
        return False
    try:
        creds = auth_store.load()
    except auth_store.CredentialsError:
        creds = None
    if creds is None:
        log.warning("Live-order code refused for %s (credentials unavailable)", client)
        return False
    ok, counter = auth.verify_totp(creds.totp_secret, code, now=at,
                                   last_counter=creds.last_totp_counter)
    if not ok:
        lockout.record_failure(client, now=at, hashed=True)
        log.warning("Live-order code refused for %s (code)", client)
        return False
    try:
        auth_store.save(dataclasses.replace(creds, last_totp_counter=counter,
                                            live_unlock_date=_today(at)))
    except OSError as exc:
        # A code that cannot be recorded can be replayed: refuse it.
        log.warning("Could not record the live-order unlock: %s", exc)
        return False
    lockout.record_success(client)
    log.info("Live orders unlocked for %s until the end of the Central day", client)
    return True
