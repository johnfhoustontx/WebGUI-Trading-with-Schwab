"""Anthropic API key resolution for driver_svc (never commit the key).

The autonomous decision layer calls Claude through the ``anthropic`` SDK, which
needs an API key. This module is the single resolution point so the key is never
hard-coded or committed:

* First the ``ANTHROPIC_API_KEY`` environment variable (the preferred source —
  set it in the service's launch environment).
* Then an optional, **gitignored** ``shared/anthropic_key.txt`` file (a local
  convenience fallback; the template/secret-handling rules of the repo apply).

Returns ``None`` when the key is unset rather than raising — so the decider
degrades to a stand-down decision (the safe failure mode) instead of crashing
the autonomous cycle.
"""
import os

from repo_paths import SHARED_DIR


def anthropic_api_key() -> str | None:
    """The Anthropic API key, or ``None`` if unset (never raises).

    Order: ``ANTHROPIC_API_KEY`` env var → gitignored ``shared/anthropic_key.txt``.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        p = SHARED_DIR / "anthropic_key.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 — a missing/unreadable file is non-fatal.
        pass
    return None
