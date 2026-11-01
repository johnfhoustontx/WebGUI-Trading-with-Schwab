"""The /ES and /NQ figures the Desk's Dealer Positioning panel shows.

The panel reads one small row per symbol, and the matrix it reads for the stock
symbols must not carry these two (it is a public view, and they never reach a public
key). So each accepted capture writes its own short row to
``futures_chain.DESK_KEY``: the same flip, walls and net gamma ``compute`` reads for
a matrix row, off the store's newest row of the capture's session.

Each symbol keeps two rows, as the page's chain does (``futures_chain_feed._publish``):
``latest``, and ``frozen``, the last taken ``frozen_before_expiry_min`` before expiry.
The gamma of the last hour swings on very little time left, and must not be left on
screen between sessions.

One writer, the stream's consumer thread, so the read and write back lose nothing.
Imported lazily by nothing: ``compute`` is imported here only when a row is built.
"""
from __future__ import annotations

from services.options_svc import futures_chain_feed as _feed
from services.options_svc.matrix import gex_regime
from shared import futures_chain


def _levels(symbol: str, expiry) -> dict:
    """The newest stored flip, walls and net gamma for ``expiry``'s session."""
    from services.options_svc import compute

    _, gex_history, _ = _feed._engines()
    span = gex_history.SessionSpan(*futures_chain.session_bounds(expiry))
    conn = gex_history.connect(read_only=True)
    try:
        return {"flip": gex_history.latest_flip(conn, symbol, "gex", span),
                **compute._matrix_dealer_levels(gex_history, conn, symbol, span)}
    finally:
        conn.close()


def publish(bus, adapted, price, capture: dict, *, not_moving: bool) -> None:
    """Write ``adapted``'s row. ``not_moving`` is the feed's own reading of a run of
    identical captures: it arrives on time, so its age cannot say so."""
    levels = _levels(adapted.symbol, adapted.expiry)
    row = {**levels, "symbol": adapted.symbol, "spot": price,
           "gex_regime": gex_regime(price, levels["flip"]),
           "captured_at": adapted.captured_at, "expiry": adapted.expiry.isoformat(),
           "not_moving": not_moving}
    envelope = bus.cache_get(futures_chain.DESK_KEY)
    payload = envelope.payload if envelope is not None else None
    view = dict(payload) if isinstance(payload, dict) else {}
    entry = view.get(adapted.symbol)
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry["latest"] = row
    close = futures_chain.session_bounds(adapted.expiry)[1]
    if adapted.captured_at <= close - capture["frozen_before_expiry_min"] * 60:
        entry["frozen"] = row
    view[adapted.symbol] = entry
    bus.cache_set(futures_chain.DESK_KEY, view)
