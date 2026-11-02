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
  past the age limit or after a run of identical captures, with its numbers kept;
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

import re
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
    interval, step = futures_chain_config.slowest_interval(), futures_chain_config.column_step()
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


def _pc_reason_for(reason: str, symbol: str) -> str:
    """The PC's report as it concerns ``symbol``. It can name instruments by root, as
    ``ES: ...; NQ: ...``: only this one's part belongs on its label, under its label.
    A report naming none is about the PC, and so about every instrument."""
    labels = {futures_chain.display_symbol(s)[1:]: futures_chain.display_symbol(s)
              for s in futures_chain.feed_symbols()}
    names = "|".join(re.escape(root) for root in labels)
    parts = re.split(rf"(?:^|;\s+)({names}):\s+", reason)
    if len(parts) == 1:
        return reason
    mine = futures_chain.display_symbol(symbol)
    for root, text in zip(parts[1::2], parts[2::2]):
        if labels[root] == mine:
            return f"{mine}: {text}"
    return ""


def _why_not_capturing(bus, symbol: str) -> str:
    """What the server last heard: this instrument's refusal, else the PC's report."""
    status = _feed._read_status(bus)
    pc = status.get("pc")
    return (_feed._entry(status, symbol)["reason"]
            or _pc_reason_for((pc.get("reason") if isinstance(pc, dict) else "") or "", symbol))


def _feed_block(bus, symbol: str, captured: dict, frozen: bool, now: datetime,
                last_row_at=None) -> dict:
    """The payload's account of itself. A stale or frozen capture keeps its numbers
    and this is what says so; a number must never read as current when it is not.
    ``last_row_at`` is where the session's rows end: past a frozen capture."""
    taken = captured["captured_at"]
    age = now.timestamp() - taken
    why = _why_not_capturing(bus, symbol)
    if frozen:
        stale = futures_chain.in_session(now)
        if not stale:
            # Nothing is expected between sessions, and a cause from days ago is not
            # why: it must not read as current through a weekend.
            why = ""
        when = datetime.fromtimestamp(taken, _CENTRAL)
        reason = f"Showing the capture from {when:%H:%M} Central on {when:%A, %B} {when.day}."
        if stale:
            reason = f"No capture has arrived for this session. {reason}"
    else:
        settings = futures_chain_config.capture()
        too_old = age > settings["stale_after_sec"]
        # A feed that stopped moving goes on arriving on time, so its age says
        # nothing: the run of identical captures is what marks it. The status
        # entry's reason (``why``) says so in words.
        repeats = _feed._entry(_feed._read_status(bus), symbol)["repeats"]
        stale = too_old or repeats >= settings["frozen_repeats"]
        reason = f"The last capture is {_feed._span(age)} old." if too_old else ""
    low, high = _strike_range(captured["chain"])
    return {"label": futures_chain.display_symbol(symbol), "captured_at": taken,
            "expiry": captured["expiry"], "frozen": frozen, "stale": stale,
            "reason": " ".join(part for part in (reason, why) if part),
            # The cause alone. ``reason`` also holds an age worked out now, which the
            # page's own label would contradict once the payload has sat in the cache.
            "why": why,
            "strike_low": low, "strike_high": high, "last_row_at": last_row_at}


def _flip_by_rule(snap: dict, symbol: str, captured_at: float) -> None:
    """Put the feed's own gamma flip on the GEX view: the one stored for the shown
    capture's minute, which the ingest wrote by ``[flip] rule``, so the page's
    summary says what the Desk and the Flip track say. ``flip_rule`` names the rule
    for the summary's wording. The per-strike rule leaves the view as it is."""
    from services.options_svc import futures_chain_flip
    from services.options_svc.collect_derive import minute_of

    entry = snap["views"].get("GEX")
    rule = futures_chain_config.flip()["rule"]
    if not entry or rule == "per_strike":
        return
    _, gex_history, _ = _feed._engines()
    conn = gex_history.connect(read_only=True)
    try:
        level = futures_chain_flip.stored(conn, gex_history, symbol, minute_of(captured_at))
    finally:
        conn.close()
    entry["flip"] = level
    entry["summary"] = {**(entry.get("summary") or {}), "flip": level, "flip_rule": rule}


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
    _flip_by_rule(snap, symbol, captured["captured_at"])
    rows = (snap["views"].get("GEX") or {}).get("history") or []
    snap["feed"] = _feed_block(bus, symbol, captured, frozen, now,
                               last_row_at=rows[-1][0] if rows else None)
    if frozen or snap["feed"]["stale"]:
        # The rows run on past a frozen capture, into an afternoon that has already
        # happened, and a stale one was priced at a time long past: a band projected
        # from either would be drawn as a forecast from a "now" that is not now.
        for entry in snap["views"].values():
            entry.pop("forward", None)
    return snap


def private_symbol_options(options) -> list:
    """The private page's list: ``options`` (``compute.gamma_symbol_options``) plus
    the feed while it is switched on. The public list is ``options`` as it stands."""
    if not futures_chain_config.capture()["show_in_dropdown"]:
        return list(options)
    return [*options, *futures_chain.feed_symbols()]
