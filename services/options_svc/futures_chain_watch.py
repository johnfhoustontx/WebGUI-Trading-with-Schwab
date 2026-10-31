"""The /ES and /NQ chain feed's watch: a phone alert when capture stops, and the
feed's report on ``/health``.

The capture runs on the operator's PC, and a PC that is off or logged out cannot
say so itself, so the check is made here from the status view
(``futures_chain.STATUS_KEY``) that ``futures_chain_feed`` keeps. During the CME
session an instrument is STOPPED when its newest accepted capture is older than
``[capture] stale_after_sec``, or when its last ``frozen_repeats`` captures read
exactly the same values. One message goes out through the ``system`` category of
the existing notification path when it stops, the same message again at most every
``alert_repeat_min`` minutes while it stays stopped (so an overnight logout does
not repeat all night), and one message when it resumes.

Quiet where it should be:

* outside the session (the daily gap, the weekend, before the Sunday open), when
  nothing is expected;
* for ``stale_after_sec`` after a session opens, since the capture has had no time
  to take its first snapshot;
* for an instrument with no status entry at all, which is a feed the PC has not
  yet tried (not installed, or not started).

An instrument the PC HAS tried, whose every capture was refused, has an entry with
a reason and no accepted capture. That is stopped like any other, once the session
has been open for ``stale_after_sec``, and the alert carries the refusal.

What has already been told lives in its own Redis key
(``futures_chain.ALERT_STATE_KEY``), so a service restart does not repeat an alert
and a second writer never shares ``STATUS_KEY`` with the stream's consumer. State
is saved for an alert only after its message was sent: a send that raised, or
reached no channel, leaves the state as it was and the next tick, 30 seconds on,
tries again.

Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, section 5.9.
Imports nothing from ``compute`` (which has a line ceiling) and calls no client.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from services import _degrade
from services.options_svc import futures_chain_feed as feed
from shared import futures_chain, futures_chain_config
from shared.notify import system_alert

log = logging.getLogger(__name__)

# The swallowed-exception counter this watch reports under (``/health``).
AREA = "options.futures_chain_watch"

_CENTRAL = ZoneInfo("America/Chicago")


class Alert(NamedTuple):
    kind: str          # "stopped" or "resumed"
    symbol: str        # the internal symbol, e.g. "FOP.ES"
    title: str
    lines: list


def _now() -> datetime:
    """The current time in Central. A function so tests can pin the clock."""
    return datetime.now(_CENTRAL)


def _epoch(now: datetime) -> float:
    """``now`` as epoch seconds; a naive datetime means Central."""
    return (now if now.tzinfo else now.replace(tzinfo=_CENTRAL)).timestamp()


def _number(value) -> bool:
    return feed._number(value) and math.isfinite(value)


def _captured_at(entry):
    """When the instrument's newest accepted capture was taken, or ``None`` for
    one that has none (or a status entry that is not one)."""
    value = entry.get("captured_at") if isinstance(entry, dict) else None
    return float(value) if _number(value) else None


def _refused(entry) -> bool:
    """Whether the feed has refused a capture of this instrument: its entry keeps
    the time and the reason. An entry with neither is a blank one."""
    return isinstance(entry, dict) and (_number(entry.get("refused_at"))
                                        or bool(_text(entry.get("reason"))))


def _repeats(entry) -> int:
    value = entry.get("repeats") if isinstance(entry, dict) else None
    return int(value) if _number(value) else 0


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _opens(now: datetime) -> float:
    """When the session ``now`` belongs to opened (or opens), epoch seconds."""
    return float(futures_chain.session_bounds(futures_chain.session_expiry(now))[0])


# ── the decision ─────────────────────────────────────────────────────────────

def _assess(entry, at: float, opens: float, capture: dict) -> str:
    """Where one instrument stands during the session: ``"never"`` (no capture has
    ever been accepted and none refused: the PC has not tried), ``"stopped"``,
    ``"healthy"``, or ``"starting"`` (the session opened too recently to judge, and
    the last capture is the last one's).

    An instrument with refusals and no accepted capture is judged from the
    session's open alone: it is trying, and nothing it sends is used.

    Age counts from the later of the capture and the session's open: a feed that
    stopped on Friday has had no chance since the Sunday evening open. A run of
    unchanged values counts only when the capture belongs to this session; the
    run carried over from the last expiry is no news."""
    captured = _captured_at(entry)
    limit = capture["stale_after_sec"]
    if captured is None:
        if not _refused(entry):
            return "never"
        return "stopped" if at - opens > limit else "starting"
    frozen = captured >= opens and _repeats(entry) >= capture["frozen_repeats"]
    if frozen or at - max(captured, opens) > limit:
        return "stopped"
    return "healthy" if at - captured <= limit else "starting"


def _stopped_lines(entry, pc, at: float, repeat: bool) -> list:
    captured = _captured_at(entry)
    if captured is None:
        lines = ["No capture has been accepted yet: every capture the PC sent was "
                 "refused." + (" Capture is still stopped." if repeat else "")]
    else:
        when = datetime.fromtimestamp(captured, _CENTRAL).strftime("%A %H:%M")
        lines = [f"The last good capture was {feed._span(at - captured)} ago, on "
                 f"{when} Central time."
                 + (" Capture is still stopped." if repeat else "")]
    reason = _text(entry.get("reason"))
    if reason:
        lines.append(reason)
    elif (captured is not None
          and _repeats(entry) >= futures_chain_config.capture()["frozen_repeats"]):
        lines.append(f"The last {_repeats(entry)} captures read exactly the same values.")
    said = _text(pc.get("reason")) if isinstance(pc, dict) else ""
    if said:
        reported = pc.get("at")
        ago = (f" {feed._span(at - reported)} ago"
               if _number(reported) and 0 <= at - reported else "")
        lines.append(f"The capturing PC reported{ago}: {said}")
    return lines


def decide(now: datetime, status: dict, state: dict):
    """``(alerts, new_state)``: what to tell the operator at ``now``, and the alert
    state once all of it has been told. Pure: ``status`` and ``state`` are not
    changed. ``status`` is the feed's status view; ``state`` is
    ``{symbol: {"alerted_at": epoch}}`` for each instrument last reported stopped."""
    new = {symbol: dict(told) for symbol, told in state.items() if isinstance(told, dict)}
    if not futures_chain.in_session(now):
        return [], new
    capture = futures_chain_config.capture()
    at, opens = _epoch(now), _opens(now)
    repeat_sec = capture["alert_repeat_min"] * 60
    pc = status.get("pc")
    alerts: list[Alert] = []
    for symbol in futures_chain.feed_symbols():
        entry = status.get(symbol)
        standing = _assess(entry, at, opens, capture)
        label = futures_chain.display_symbol(symbol)
        told = new.get(symbol)
        if standing == "stopped":
            last = told.get("alerted_at") if told else None
            if _number(last) and 0 <= at - last < repeat_sec:
                continue
            alerts.append(Alert("stopped", symbol, f"{label} chain capture has stopped",
                                _stopped_lines(entry, pc, at, repeat=told is not None)))
            new[symbol] = {"alerted_at": at}
        elif standing == "healthy" and told is not None:
            captured = _captured_at(entry)
            when = datetime.fromtimestamp(captured, _CENTRAL).strftime("%H:%M")
            alerts.append(Alert("resumed", symbol, f"{label} chain capture has resumed",
                                [f"A capture was taken at {when} Central time."]))
            del new[symbol]
    return alerts, new


# ── one tick ─────────────────────────────────────────────────────────────────

def _read_state(bus) -> dict:
    envelope = bus.cache_get(futures_chain.ALERT_STATE_KEY)
    payload = envelope.payload if envelope is not None else None
    return dict(payload) if isinstance(payload, dict) else {}


def _delivered(alert: Alert) -> bool:
    """Send one alert. ``True`` when ``send`` named a channel. ``[]`` means the
    message went nowhere because notifications are off or unconfigured (the master
    switch, the ``system`` category, no destination), which is retried like a raise
    and costs nothing. A channel's own network failure is NOT seen here:
    ``send_telegram`` and ``send_discord`` swallow it and ``send`` still names the
    channel, so that alert counts as told and waits for the next repeat."""
    try:
        return bool(system_alert.send(alert.title, alert.lines))
    except Exception:
        _degrade.degraded(AREA, detail=f"{alert.symbol} {alert.kind} alert not sent")
        return False


def tick(bus, now: datetime | None = None) -> None:
    """Decide, send, and save what was told. Run each scheduler tick, in session
    or not: it reads one key and does nothing else when there is nothing to say.

    An alert whose send failed keeps the state it had, so the next tick decides
    the same alert again."""
    now = now or _now()
    try:
        old = _read_state(bus)
        alerts, new = decide(now, feed._read_status(bus), old)
    except Exception:
        _degrade.degraded(AREA, detail="deciding")
        return
    for alert in alerts:
        if _delivered(alert):
            continue
        if alert.symbol in old:
            new[alert.symbol] = old[alert.symbol]
        else:
            new.pop(alert.symbol, None)
    if new != old:
        bus.cache_set(futures_chain.ALERT_STATE_KEY, new)


# ── /health ──────────────────────────────────────────────────────────────────

_WORDS = {"stopped": "stopped", "healthy": "capturing", "starting": "starting"}


def health(bus) -> dict:
    """The feed's report for ``/health``: one read of the status view, nothing
    else. It describes and never judges: ``/health``'s ``up`` is not touched (a PC
    that is off is not a failed options service)."""
    status = feed._read_status(bus)
    now = _now()
    in_session = futures_chain.in_session(now)
    capture = futures_chain_config.capture()
    at, opens = _epoch(now), _opens(now)
    instruments = {}
    for symbol in futures_chain.feed_symbols():
        entry = status.get(symbol)
        captured = _captured_at(entry)
        if captured is None and not _refused(entry):
            continue
        standing = _assess(entry, at, opens, capture) if in_session else None
        # No accepted capture: no time, never 0 or a made-up one.
        outside = "between sessions" if captured is not None else "no capture accepted"
        instruments[futures_chain.display_symbol(symbol)] = {
            "captured_at": captured,
            "age_s": None if captured is None else int(max(0, at - captured)),
            "refused_at": entry.get("refused_at") if _number(entry.get("refused_at")) else None,
            "state": _WORDS.get(standing, outside),
            "repeats": _repeats(entry),
            "listed": entry.get("listed"),
            "with_gamma": entry.get("with_gamma"),
            "reason": _text(entry.get("reason")),
        }
    pc = status.get("pc")
    return {"in_session": in_session, "instruments": instruments,
            "pc": ({"reason": _text(pc.get("reason")), "at": pc.get("at")}
                   if isinstance(pc, dict) else None)}
