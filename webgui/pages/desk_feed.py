"""The /ES and /NQ rows on the Desk's Dealer Positioning panel.

These two have no row in ``options:matrix``: each accepted capture from ThinkorSwim
writes its own short row (``services/options_svc/futures_chain_desk.py``). This turns
that view into rows of the panel's shape, against the clock, because the page can
hold a view written hours ago.

* **Their own clock.** The stock feed's freshness says nothing about these. A row is
  stale outside the capture hours, past ``stale_after_sec``, or when the feed reports
  a run of identical captures, and its walls are withheld as a stock row's are.
* **No day change.** A capture carries no prior close, so the second line under the
  price says when the numbers are from instead. A number is never left to read as
  current when it is not.
* **After expiry** the flip, walls and net gamma fall back to the capture kept before
  the last hour; the price and its time stay those of the session's last capture.
* **Private.** ``desk.live_views`` keeps the published Desk from ever reading the view.
"""
from datetime import date

from pages.fmt import num as _finite
from pages.structure import (flip_read, regime_word, structure_positions,
                             walls_trustworthy)
from shared import futures_chain, futures_chain_config

VIEW = futures_chain.DESK_VIEW

# The flip's hover note while the feed finds it cumulatively ([flip] rule).
FLIP_NOTE = ("Cumulative flip: where net gamma, summed up the strikes from the "
             "lowest captured, changes sign. Other symbols use the per-strike flip.")


def _entry(view, symbol):
    entry = view.get(symbol) if isinstance(view, dict) else None
    return entry if isinstance(entry, dict) else {}


def _session_ended(row, now) -> bool:
    try:
        expiry = date.fromisoformat(row.get("expiry"))
    except (TypeError, ValueError):
        return True
    return now >= futures_chain.session_bounds(expiry)[1]


def _chosen(view, symbol, now):
    """The row to show at ``now``: the newest until its expiry, then the kept one's
    levels with the price, and its time, of the session's last capture."""
    entry = _entry(view, symbol)
    latest = entry.get("latest")
    if isinstance(latest, dict) and not _session_ended(latest, now):
        return latest
    kept = entry.get("frozen")
    if not isinstance(kept, dict):
        return None
    if not isinstance(latest, dict) or latest.get("expiry") != kept.get("expiry"):
        return kept
    side, _ = flip_read(_finite(latest.get("spot")), _finite(kept.get("flip")))
    return {**kept, "spot": latest.get("spot"), "captured_at": latest.get("captured_at"),
            "gex_regime": side or "na"}


def _stale(row, now) -> bool:
    captured = _finite(row.get("captured_at"))
    return (captured is None
            or not futures_chain.in_session(futures_chain.central_time(now))
            or now - captured > futures_chain_config.capture()["stale_after_sec"]
            or bool(row.get("not_moving")))


def as_of(captured, now) -> str:
    """``as of 13:55`` today, ``as of Fri 13:55`` on any other day."""
    when = futures_chain.central_time(captured)
    same_day = when.date() == futures_chain.central_time(now).date()
    return f"as of {when:%H:%M}" if same_day else f"as of {when:%a %H:%M}"


def rows(view, now) -> list:
    """Panel rows for the feed's symbols that have a row to show, at ``now`` (epoch
    seconds). Total over a missing or malformed view. A row opens Dealer Positioning
    only while the operator offers these symbols in its list."""
    out = []
    opens = futures_chain_config.capture()["show_in_dropdown"]
    flip_note = FLIP_NOTE if futures_chain_config.flip()["rule"] == "cumulative" else ""
    for symbol in futures_chain.feed_symbols():
        row = _chosen(view, symbol, now)
        captured = _finite(row.get("captured_at")) if row else None
        if captured is None:
            continue
        stale = _stale(row, now)
        spot, flip = _finite(row.get("spot")), _finite(row.get("flip"))
        net_gex = _finite(row.get("net_gex"))
        show_walls = walls_trustworthy(net_gex, stale)
        call_wall = _finite(row.get("call_wall")) if show_walls else None
        put_wall = _finite(row.get("put_wall")) if show_walls else None
        side, dist = flip_read(spot, flip)
        out.append({
            "symbol": symbol,
            "label": futures_chain.display_symbol(symbol),
            "spot": spot,
            "day_pct": None,
            "as_of": as_of(captured, now),
            "flip": flip,
            "flip_note": flip_note,
            "flip_distance": dist,
            "flip_side": side,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "net_gex": net_gex,
            "regime_word": regime_word(row.get("gex_regime")),
            "structure": structure_positions(spot, flip, put_wall, call_wall),
            "stale": stale,
            "opens": opens,
        })
    return out


def phase(view, now) -> tuple:
    """What the clock alone can change about these rows. The Desk repaints the panel
    when this moves, because a capture that stops arriving bumps no version."""
    picks = []
    for symbol in futures_chain.feed_symbols():
        row = _chosen(view, symbol, now)
        picks.append(None if row is None else (row.get("captured_at"), _stale(row, now)))
    return tuple(picks)
