"""The raw feed's ``tee``: the one-minute collection also keeps what it fetched.

In ``config/feed.toml [feed] mode = "tee"`` every minute ``gex_collector.poll_once``
fetches is written to the raw store before it is derived, so the shadow check
(``raw_shadow``) can re-derive it from there and compare. It costs no Schwab
call: the minute is the one the poll already paid for. Design:
docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §7.

Never raises into collection: a store failure is a degrade and the poll goes
on. In ``off`` nothing is written, and in ``on`` nothing either -- there
``feed_svc`` owns the raw store and the collector does not run.

Imports nothing from ``compute``.
"""
from __future__ import annotations

import datetime as _dt
import logging
from zoneinfo import ZoneInfo

from services import _degrade
from shared import feed_config, raw_store

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")

# Session dates already pruned by this process: retention runs once per session.
_PRUNED: set = set()
# ``(session date, ts)`` of the last minute this process wrote, for the shadow
# check to pick up. None until the first write.
_LAST = None


def last_written():
    """``(session date, ts)`` of the last minute written, or None."""
    return _LAST


def session_of(ts) -> str:
    """The CT session date a minute belongs to (never the UTC date)."""
    return _dt.datetime.fromtimestamp(int(ts), CT).date().isoformat()


def maybe_write(minute, root=None) -> bool:
    """Write ``minute`` (a ``RawMinute``) to the raw store when the mode is
    ``tee``. Returns whether it wrote. Never raises."""
    global _LAST
    if feed_config.mode() != "tee":
        return False
    try:
        date_iso = session_of(minute.ts)
        raw_store.write_minute(date_iso, minute, root=root)
        _LAST = (date_iso, int(minute.ts))
        if date_iso not in _PRUNED:
            _PRUNED.add(date_iso)
            gone = raw_store.prune(feed_config.keep_sessions(), root=root)
            if gone:
                log.info("raw store: dropped session(s) %s", ", ".join(gone))
        return True
    except Exception:
        _degrade.degraded("options.raw_tee")
        return False
