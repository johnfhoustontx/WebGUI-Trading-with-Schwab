"""Quality-triggered trade ideas, IN SHADOW: after each scan, decide whether a new
high-grade candidate would have been posted on top of the hourly idea.

Design: docs/plans/2026-11-06-trade-idea-quality-shadow-design.md. Nothing here
posts. ``handlers.run_idea_shadow`` books the pick into the Paper Ledger as an
``idea_shadow`` row, and ``tools/idea_shadow_compare.py`` measures those rows
against the hourly posts.

Why grade and novelty, not the composite score: every hourly post from 10-21 to
11-05 scored 77-81, so a score threshold fires on every scan or on none. Grade
Strong was the one measured separator (+0.45R on 8 trades against +0.22R).

PURE: no bus, no store, no clock of its own. ``decide`` takes the scan, today's
state, the time and the two config dicts, and hands back the new state, the pick
(or None) and the reason. The state is a plain dict cached between scans::

    {"date": "2026-09-17", "scan_ts": "<the last scan checked>",
     "keys": [<trade keys of that scan's qualifying candidates>] | None,
     "clock_at": "<the last hourly post seen>",
     "posts": [{"at", "symbol", "key", "kind": "clock" | "shadow"}, ...]}
"""
import datetime as _dt

from services.options_svc import trade_idea
from shared import vol_gate

_HOUR = _dt.timedelta(hours=1)


def trade_key(idea) -> str:
    """The trade itself: symbol, structure, expiry and legs. A scan row's ``id``
    carries its index in a list, so it is not the same trade's id in the next scan."""
    legs = sorted(f"{lg['side']}:{lg['kind']}:{float(lg['strike']):.4f}:{lg['qty']}"
                  for lg in idea.get("legs") or [])
    return "|".join([str(idea.get("symbol")), str(idea.get("type")),
                     str(idea.get("expiration"))] + legs)


def day_state(state, today) -> dict:
    """``state`` when it is today's and well formed, else a fresh day."""
    if (isinstance(state, dict) and state.get("date") == today
            and isinstance(state.get("posts"), list)
            and (state.get("keys") is None or isinstance(state.get("keys"), list))):
        return {"date": today, "scan_ts": state.get("scan_ts"),
                "keys": None if state.get("keys") is None else list(state["keys"]),
                "clock_at": state.get("clock_at"),
                "posts": [p for p in state["posts"] if isinstance(p, dict)]}
    return {"date": today, "scan_ts": None, "keys": None, "clock_at": None, "posts": []}


def _when(stamp):
    try:
        return _dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None


def _since(now, stamp):
    """``now - stamp`` (naive on both sides when either is), or None."""
    then = _when(stamp)
    if then is None:
        return None
    if then.tzinfo is None or now.tzinfo is None:
        then, now = then.replace(tzinfo=None), now.replace(tzinfo=None)
    return now - then


def note_clock(state, clock) -> dict:
    """Add the hourly post to today's posts the first time it is seen.
    ``clock`` is ``cache:options:trade_idea``'s payload; only a ``posted`` result
    of today counts."""
    if not isinstance(clock, dict) or clock.get("status") != "posted":
        return state
    at, idea = clock.get("at"), clock.get("idea")
    when = _when(at)
    if when is None or when.date().isoformat() != state["date"] or at == state["clock_at"]:
        return state
    idea = idea if isinstance(idea, dict) else {}
    post = {"at": at, "symbol": idea.get("symbol"),
            "key": trade_key(idea) if idea.get("legs") else None, "kind": "clock"}
    return {**state, "clock_at": at, "posts": state["posts"] + [post]}


def record_pick(state, idea, now) -> dict:
    """Today's state after ``idea`` was booked at ``now``."""
    post = {"at": now.isoformat(), "symbol": idea.get("symbol"),
            "key": trade_key(idea), "kind": "shadow"}
    return {**state, "posts": state["posts"] + [post]}


def vol_blocked(idea, scan, floor) -> bool:
    """True when the trade sells premium below the Vol Rank floor. The row's own
    reading first, then the scan's per-symbol one; an unknown reading or vega
    skips the floor, and a floor of 0 is off (``vol_gate.blocks``' own rules)."""
    iv = idea.get("iv_rank")
    if iv is None and isinstance(scan, dict):
        per = (scan.get("iv_data") or {}).get(idea.get("symbol"))
        iv = per.get("iv_rank") if isinstance(per, dict) else None
    return vol_gate.blocks(iv, idea.get("position_vega"), floor=floor) == vol_gate.IV_TOO_LOW


def limit_reason(posts, now, cfg):
    """Why the combined feed has no room for another post now, or None."""
    if len(posts) >= cfg["max_per_day"]:
        return f"{len(posts)} posts today"
    ages = [a for a in (_since(now, p.get("at")) for p in posts) if a is not None]
    recent = sum(1 for a in ages if a < _HOUR)
    if recent >= cfg["max_per_hour"]:
        return f"{recent} posts in the last hour"
    gap = _dt.timedelta(minutes=cfg["min_gap_min"])
    newest = min(ages, default=None)
    if newest is not None and newest < gap:
        return f"last post {int(newest.total_seconds() // 60)} min ago"
    return None


def decide(scan, state, now, cfg, idea_cfg, clock=None, *, in_session=True) -> dict:
    """``{"state", "idea", "reason"}`` for one published scan.

    ``cfg`` is ``paper_limits.idea_shadow()``; ``idea_cfg`` the hourly idea's
    ``trade_idea`` block (its age, expiry, score and structure knobs are shared).
    ``reason`` is ``"picked"`` when ``idea`` is set, else what stopped it."""
    today = now.date().isoformat()
    state = note_clock(day_state(state, today), clock)

    def out(reason, idea=None, new_state=None):
        return {"state": new_state or state, "idea": idea, "reason": reason}

    if not isinstance(scan, dict):
        return out("no scan")
    ts = scan.get("timestamp")
    if ts is not None and ts == state["scan_ts"]:
        return out("scan already checked")
    checked = {**state, "scan_ts": ts}
    age = trade_idea.scan_age_min(scan, now)
    max_age = idea_cfg.get("max_age_min", trade_idea.DEFAULT_MAX_AGE_MIN)
    if age is None or age > max_age:
        return out("stale scan" if age is None else f"stale scan ({age:.0f} min old)",
                   new_state=checked)
    grades = tuple(cfg["grades"])
    ideas = trade_idea.candidates(
        scan, grades=grades, min_score=idea_cfg.get("min_score") or 0,
        today=now.date(), min_dte=idea_cfg.get("min_dte", trade_idea.DEFAULT_MIN_DTE),
        other_structures=idea_cfg.get("other_structures", True) is not False)
    keyed = [(trade_key(i), i) for i in ideas]
    prev = state["keys"]
    checked["keys"] = sorted({k for k, _ in keyed})
    if prev is None:
        return out("first scan today", new_state=checked)
    if not in_session:
        return out("outside the regular session", new_state=checked)

    pool = [(k, i) for k, i in keyed if k not in set(prev)]
    if not pool:
        return out("nothing new", new_state=checked)
    posts = state["posts"]
    done_keys = {p.get("key") for p in posts}
    done_syms = {p.get("symbol") for p in posts} if cfg["one_per_symbol"] else set()
    pool = [(k, i) for k, i in pool if k not in done_keys and i["symbol"] not in done_syms]
    if not pool:
        return out("already posted today", new_state=checked)
    floor = cfg["min_vol_rank"]
    pool = [(k, i) for k, i in pool if not vol_blocked(i, scan, floor)]
    if not pool:
        return out(f"Vol Rank below {floor:.0f}", new_state=checked)
    held = limit_reason(posts, now, cfg)
    if held:
        return out(held, new_state=checked)
    idea = trade_idea.pick([i for _, i in pool], None, grades=grades)
    return out("picked", idea=idea, new_state=checked)
