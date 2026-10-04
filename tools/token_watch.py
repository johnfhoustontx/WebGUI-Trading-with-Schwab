#!/usr/bin/env python
"""Warn before the Schwab sign-in runs out.

Schwab's refresh token lasts 7 days and is renewed only by a person signing in
again on the proxy's ``/auth`` page. When it lapses every market-data call
fails. Nothing warned before that happened: the Status page shows the state,
and only to someone looking at it (audit AR-03).

Run once a day by ``trading-<env>-token-watch.timer``. It asks the proxy's
``/health`` and sends a system alert when:

* the token has ``[system] token_warn_hours`` (config/notify.toml) or fewer
  left, with the hours;
* it has expired, Schwab has rejected it, or there is none;
* the proxy does not answer, or does not say how long is left. An unknown is
  reported, never read as "plenty of time".

Once a day, so the warning repeats daily inside the window and never more.
"""
from __future__ import annotations

import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_HOW = "Sign in again on the proxy's /auth page (Status page -> Authorize)."


def fetch():
    """The proxy's ``/health`` body, or None when it does not answer."""
    import json
    import urllib.request
    sys.path.insert(0, str(ROOT))
    from repo_paths import PROXY_URL
    try:
        with urllib.request.urlopen(f"{PROXY_URL}/health", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def assess(health, *, warn_hours):
    """``(title, lines)`` when a person should be told, else None. Pure."""
    if not isinstance(health, dict):
        return ("The Schwab proxy is not answering",
                ["The daily sign-in check could not reach the proxy, so the "
                 "state of the Schwab sign-in is unknown.",
                 "Check the Status page."])
    if not health.get("has_token"):
        return ("No Schwab sign-in on the proxy", [_HOW])
    if health.get("refresh_token_rejected"):
        return ("Schwab rejected the sign-in",
                ["Market data has stopped.", _HOW])
    if health.get("refresh_token_expired"):
        return ("The Schwab sign-in has expired",
                ["Market data has stopped.", _HOW])
    left = health.get("refresh_token_hours_left")
    if (isinstance(left, bool) or not isinstance(left, (int, float))
            or not math.isfinite(left)):
        return ("The Schwab sign-in's remaining time cannot be read",
                ["The proxy did not say how long the sign-in has left.",
                 "It lasts 7 days from the last sign-in. " + _HOW])
    if left <= warn_hours:
        hours = int(left)
        return (f"The Schwab sign-in expires in {hours} hours",
                ["Market data stops when it does.", _HOW])
    return None


def _default_send(title, lines):
    sys.path.insert(0, str(ROOT))
    from shared.notify import system_alert
    return system_alert.send(title, lines)


def _warn_hours() -> int:
    try:
        sys.path.insert(0, str(ROOT))
        from shared.notify import switches
        return switches.system_settings()["token_warn_hours"]
    except Exception:  # noqa: BLE001
        return 48


def run(*, fetch=fetch, send=None, warn_hours=None) -> str:
    """``quiet``, ``sent`` or ``not_sent``. Never raises."""
    try:
        health = fetch()
    except Exception:  # noqa: BLE001
        health = None
    found = assess(health, warn_hours=_warn_hours() if warn_hours is None else warn_hours)
    if found is None:
        return "quiet"
    try:
        return "sent" if (send or _default_send)(*found) else "not_sent"
    except Exception:  # noqa: BLE001
        return "not_sent"


def main() -> int:
    print(f"token_watch: {run()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
