"""Bought / sold / unlabelled volume per option contract. PURE (stdlib only).

An ESTIMATE: Schwab publishes no time-and-sales tape, so new volume is labelled
from where the latest trade price sits against the bid and ask -- once a minute
on the polled chain, per tick on the level-one stream. The rule is
``hiro.classify_side``, shared with the hedging-flow model.

Volume the service did not watch print (before a restart, across a poll gap) is
UNLABELLED, never dropped, so bought + sold + unlabelled always equals the
contract's volume. Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
from shared.numeric import finite as _finite

from services.options_svc.hiro import _contracts, classify_side

# One contract's entry is a list, not a dict: every contract with volume in
# every fetched chain has one, and a list is about a third of the memory.
HW, BOUGHT, SOLD, UNLABELLED, OI = range(5)


def new_entry() -> list:
    """``[volume high-water mark, bought, sold, unlabelled, open interest]``."""
    return [0.0, 0.0, 0.0, 0.0, None]


def tally(entry) -> dict:
    return {"bought": entry[BOUGHT], "sold": entry[SOLD],
            "unlabelled": entry[UNLABELLED]}


def _book(entry, dv, side) -> None:
    entry[BOUGHT if side > 0 else SOLD if side < 0 else UNLABELLED] += dv


def advance(chain, book, *, seeded, label=True) -> int:
    """Book one fetched chain's new volume into ``book`` ({contract symbol:
    entry}), IN PLACE. Returns how many contracts booked volume.

    ``seeded`` -- this symbol was already polled this session, so a contract
    with no entry stood at zero volume last time and its volume is new.
    Unseeded, that volume predates the watch and is unlabelled.
    ``label`` -- False after a poll gap: several minutes of volume must not
    take one minute's bid/ask label.

    The stored volume is a HIGH-WATER mark: volume never falls within a
    session, so a glitch read of 0 books nothing and cannot re-book the day."""
    if not isinstance(chain, dict):
        return 0
    booked = 0
    for _is_call, c in _contracts(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None or vol < 0:
            continue
        entry = book.get(osi)
        if entry is None:
            if vol <= 0:
                continue
            entry = book[osi] = new_entry()
            dv, can_label = vol, seeded and label
        else:
            dv, can_label = vol - entry[HW], label
        if dv > 0:
            side = (classify_side(c.get("last"), c.get("bid"), c.get("ask"))
                    if can_label else 0)
            _book(entry, dv, side)
            entry[HW] = vol
            booked += 1
        oi = _finite(c.get("openInterest"))
        if oi is not None and oi >= 0:
            entry[OI] = oi
    return booked


def advance_tick(book, quotes, tick, *, label=True) -> bool:
    """Book one level-one tick. ``quotes`` is ``{contract symbol: {last, bid,
    ask}}`` and is merged IN PLACE: a tick after the first carries only the
    fields that changed. A contract's first tick only seeds -- the stream
    measures what trades AFTER the alert. True when volume was booked."""
    if not isinstance(tick, dict):
        return False
    osi = tick.get("symbol")
    if not osi:
        return False
    q = quotes.setdefault(osi, {})
    for key in ("last", "bid", "ask"):
        v = _finite(tick.get(key))
        if v is not None:
            q[key] = v
    vol = _finite(tick.get("total_volume"))
    if vol is None or vol < 0:
        return False
    entry = book.get(osi)
    if entry is None:
        entry = book[osi] = new_entry()
        entry[HW] = vol
        return False
    dv = vol - entry[HW]
    if dv <= 0:
        return False
    side = classify_side(q.get("last"), q.get("bid"), q.get("ask")) if label else 0
    _book(entry, dv, side)
    entry[HW] = vol
    return True


def verdict(oi_prev, oi_next, volume, *, expiry, session_date,
            opened_ratio, closed_ratio):
    """``(code, ratio)`` for a flagged contract's next-day open interest.

    ratio = (next - previous) / that day's volume. ``opened`` at or above
    ``opened_ratio``, ``closed`` at or below ``closed_ratio``, else ``mixed``.
    ``expired`` when the contract expired on (or before) the alert day;
    ``none`` (ratio None) for any unusable input -- never a guessed verdict.
    Both dates are ISO ``YYYY-MM-DD`` text, which orders as dates do."""
    if expiry and session_date and str(expiry) <= str(session_date):
        return "expired", None
    p, n, v = _finite(oi_prev), _finite(oi_next), _finite(volume)
    if p is None or n is None or v is None or v <= 0 or p < 0 or n < 0:
        return "none", None
    ratio = (n - p) / v
    if ratio >= opened_ratio:
        return "opened", ratio
    if ratio <= closed_ratio:
        return "closed", ratio
    return "mixed", ratio
