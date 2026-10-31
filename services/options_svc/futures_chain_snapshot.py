"""The /ES and /NQ Dealer Positioning snapshot, from the stored chain.

These symbols have no chain to fetch: the newest capture in the cache is the only
one they have, so the snapshot is built from it and never reaches Schwab or the
proxy. Everything that makes the session a futures one rather than a stock day is
handed to ``compute.gamma_snapshot`` as a ``FeedSession``:

* the rows are those of the expiry's session, which spans two calendar dates;
* the figures are priced at the moment of the capture, with the hours it had left,
  so a read of the wall clock cannot move them;
* the store holds one truthful row per capture, and the rows are repeated across
  the columns the page draws, up to one interval (a longer outage stays a gap).

Which capture is shown, and how it is labelled, is decided here and said in the
payload's ``feed`` block:

* while its expiry's session has not ended, the newest capture, marked ``stale``
  past the limit with its numbers kept;
* after that, the frozen capture (the last taken far enough before expiry that the
  closing spike in gamma is not left on screen), marked ``frozen``, and also
  ``stale`` when the next session is open and no capture has arrived for it;
* with neither, nothing: the page shows its empty view.

It never falls back to anything else. Any /ES or /NQ number taken from another
symbol would be wrong, however plausible. The wall clock is read for one purpose,
to choose between those cases and word the block, never for a figure.

Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.7 to
5.9 and 5.11. Nothing from ``compute`` at module level, which has a line ceiling: it
is imported only inside the function that needs it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable, NamedTuple
from zoneinfo import ZoneInfo

from services.options_svc import futures_chain_feed as _feed
from shared import futures_chain, futures_chain_config

_CENTRAL = ZoneInfo("America/Chicago")


class FeedSession(NamedTuple):
    span: object                     # gex_history_db.SessionSpan of the expiry
    priced_at: datetime              # the capture's own instant, Central
    hours_left: float                # from the capture to the expiry's close
    fill: Callable[[list], list]     # the rows, repeated onto the page's columns


def _session(captured: dict) -> FeedSession:
    """The session a cached capture (``futures_chain_feed.latest``) belongs to."""
    _, gex_history, _ = _feed._engines()
    settings = futures_chain_config.capture()
    interval, step = settings["interval_sec"], settings["fill_step_sec"]
    opens, ends = futures_chain.session_bounds(date.fromisoformat(captured["expiry"]))
    return FeedSession(
        span=gex_history.SessionSpan(opens, ends),
        priced_at=datetime.fromtimestamp(captured["captured_at"], _CENTRAL),
        hours_left=captured["hours_left"],
        fill=lambda rows: futures_chain.fill_rows(rows, interval, step))


def _strike_range(chain: dict) -> tuple:
    """The lowest and highest strike captured; ``None`` for a chain holding none.
    The page's window is narrower: this says how far the sheet itself reached."""
    strikes = [float(strike)
               for side in ("callExpDateMap", "putExpDateMap")
               for by_strike in (chain.get(side) or {}).values()
               for strike in (by_strike or {})]
    return (min(strikes), max(strikes)) if strikes else (None, None)


def _choose(bus, symbol: str, now_ts: float):
    """``(capture, frozen)``: what to show at ``now_ts``, or ``(None, False)``."""
    newest = _feed.latest(bus, symbol)
    if newest is not None:
        ends = futures_chain.session_bounds(date.fromisoformat(newest["expiry"]))[1]
        if now_ts < ends:
            return newest, False
    kept = _feed.frozen(bus, symbol)
    return (kept, True) if kept is not None else (None, False)


def _why_not_capturing(bus, symbol: str) -> str:
    """What the server last heard: this instrument's refusal, else the PC's report."""
    status = _feed._read_status(bus)
    pc = status.get("pc")
    return (_feed._entry(status, symbol)["reason"]
            or (pc.get("reason") if isinstance(pc, dict) else "") or "")


def _feed_block(bus, symbol: str, captured: dict, frozen: bool, now: datetime) -> dict:
    """The payload's account of itself. A stale or frozen capture keeps its numbers
    and this is what says so; a number must never read as current when it is not."""
    taken = captured["captured_at"]
    age = now.timestamp() - taken
    why = _why_not_capturing(bus, symbol)
    if frozen:
        stale = futures_chain.in_session(now)
        when = datetime.fromtimestamp(taken, _CENTRAL)
        reason = f"Showing the capture from {when:%H:%M} Central on {when:%A, %B} {when.day}."
        if stale:
            reason = f"No capture has arrived for this session. {reason}"
    else:
        stale = age > futures_chain_config.capture()["stale_after_sec"]
        reason = f"The last capture is {_feed._span(age)} old." if stale else ""
    low, high = _strike_range(captured["chain"])
    return {"label": futures_chain.display_symbol(symbol), "captured_at": taken,
            "expiry": captured["expiry"], "frozen": frozen, "stale": stale,
            "reason": " ".join(part for part in (reason, why) if part),
            "strike_low": low, "strike_high": high}


def snapshot(bus, symbol, now=None) -> dict | None:
    """``compute.gamma_snapshot``'s payload for a feed symbol plus its ``feed``
    block, or ``None`` when there is nothing to show. ``now`` (the wall clock when
    omitted; naive means Central) decides the block and never a figure."""
    from services.options_svc import compute

    symbol = symbol.strip().upper()
    if now is None:
        now = datetime.fromtimestamp(_feed._now(), _CENTRAL)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_CENTRAL)
    captured, frozen = _choose(bus, symbol, now.timestamp())
    if captured is None:
        return None
    snap = compute.gamma_snapshot(symbol, chain=captured["chain"], with_term=False,
                                  session=_session(captured))
    if snap is None:
        return None
    if frozen:
        # The rows run on past the frozen capture, into an afternoon that has
        # already happened: a band projected from it would be drawn over them.
        for entry in snap["views"].values():
            entry.pop("forward", None)
    snap["feed"] = _feed_block(bus, symbol, captured, frozen, now)
    return snap


def private_symbol_options(options) -> list:
    """The private page's list: ``options`` (``compute.gamma_symbol_options``) plus
    the feed while it is switched on. The public list is ``options`` as it stands."""
    if not futures_chain_config.capture()["show_in_dropdown"]:
        return list(options)
    return [*options, *futures_chain.feed_symbols()]
