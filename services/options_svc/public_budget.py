"""One daily Schwab budget, shared by every public worker.

The public live origin reaches Schwab through three tools - the Rescue form, the
Calculator and the Simulator - but there is ONE Schwab allowance behind them,
already spent at 68-76k calls a day by the stack itself. So there is one budget
too: a count of Schwab-spending public requests per CT trading day, across every
public worker together, capped by ``shared.public_rescue.budget()``. Separate
per-tool budgets would each look reasonable and add up to more than the operator
agreed to spend.

Each worker calls ``spend`` as its LAST gate, at the point where the Schwab work
would start, so a request refused for any earlier reason (invalid, expired,
cached, duplicate, closed, ...) never spends. ``by_kind`` records what the
budget went on, for Settings.

Why a ``threading.Lock`` is enough: every public worker is a consumer-loop
thread inside this ONE options_svc process (``make_app(extra_consumers=...)``),
so a process-local lock around the read-modify-write of one Redis key makes it
atomic for every writer there is.

⚠ A public worker moved into ANOTHER process would bypass this lock and could
overspend the budget (two processes read the same count, both increment it).
That move needs a Redis-side atomic counter (``INCR`` on a dated key, or a Lua
check-and-increment) in place of this lock.
"""
from __future__ import annotations

import math
import threading
from zoneinfo import ZoneInfo

BUDGET_KEY = "cache:options:public_budget"
# The key outlives its day briefly so Settings can read yesterday's total just
# after midnight; a new day rolls over on read regardless.
KEEP_SEC = 2 * 24 * 3600

_LOCK = threading.Lock()
CT = ZoneInfo("America/Chicago")


def _fresh(today: str) -> dict:
    return {"date": today, "spent": 0, "by_kind": {}}


def _is_count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _read(bus, now) -> dict:
    """Today's record. A missing, corrupt or older record reads as a fresh day,
    never a raise: a garbled key must not stop the public tools for the day."""
    # The CT day whatever zone ``now`` arrives in: a UTC ``now`` would
    # otherwise start the new day at 19:00 CT.
    today = now.astimezone(CT).date().isoformat()
    env = bus.cache_get(BUDGET_KEY)
    raw = env.payload if env is not None else None
    if (not isinstance(raw, dict) or raw.get("date") != today
            or not _is_count(raw.get("spent"))
            or not isinstance(raw.get("by_kind"), dict)):
        return _fresh(today)
    by_kind = {str(k): v for k, v in raw["by_kind"].items() if _is_count(v)}
    return {"date": today, "spent": raw["spent"], "by_kind": by_kind}


def _usable_limit(limit) -> int | None:
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return None
    if not math.isfinite(limit) or limit < 1:
        return None
    return int(limit)


def spend(bus, kind, limit, now) -> bool:
    """Spend one unit of today's budget on ``kind``; False when it is spent.

    A non-positive or non-numeric ``limit`` refuses (and spends nothing): an
    unreadable cap must never become "no cap"."""
    cap = _usable_limit(limit)
    if cap is None:
        return False
    with _LOCK:
        rec = _read(bus, now)
        if rec["spent"] >= cap:
            return False
        rec["spent"] += 1
        rec["by_kind"][str(kind)] = rec["by_kind"].get(str(kind), 0) + 1
        bus.cache_set(BUDGET_KEY, rec, ttl=KEEP_SEC)
        return True


def status(bus, now) -> dict:
    """``{"date", "spent", "by_kind"}`` for today (CT)."""
    with _LOCK:
        return _read(bus, now)
