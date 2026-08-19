"""The Desk (/desk) — one screen carrying the most decision-relevant element of
every other page, so the morning read is a single glance rather than a tour.

Tier-1 reader: it consumes ``cache:options:matrix``, ``cache:options:paper_account``,
``cache:options:driver_paper_account``, ``cache:options:flow_alerts``,
``cache:options:gex_status`` and ``cache:sentiment:regime`` and renders them. No
engine imports, no Schwab calls, no arithmetic of its own.

**The load-bearing principle: the Desk composes, it never restates.** Every
number here is produced by the same pure function its owning page uses —
``flow.alert_rows`` for the alert feed, ``paper``'s DTE helper for expiries,
``console_regime``'s label derivation for the regime word. This is not tidiness.
The app already carries a documented open bug where ``/sentiment/sectors`` and
``/sentiment/rotation`` print OPPOSITE regime verdicts, because each computed its
own headline from a different quantity on a different scale. A screen that
aggregates ten pages is ten chances to repeat that mistake, and a Desk that
contradicts the page it links to is worse than no Desk.

This module is pure display logic only — module-level functions over plain dicts.
The widgets and wiring land later, and none of it belongs here.
"""
import math

# The four symbols the Desk watches. Deliberately short: the Desk is a glance,
# and the Opportunity Board already exists for the full watchlist.
DESK_SYMBOLS = ("$SPX", "SPY", "QQQ", "$NDX")


def _finite(v):
    """``float(v)`` when it is a real, finite number — otherwise ``None``.

    This is the guard the app's documented NaN trap demands. ``min(hi, nan)``
    returns ``hi`` and ``max(lo, nan)`` returns ``lo`` (every comparison against
    NaN is False, so the running value survives), so an unguarded non-finite
    value does not degrade to "no reading" — it PINS a bound and renders as a
    confident extreme. Filter at the call site; never trust a clamp to notice.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── structure map ────────────────────────────────────────────────────────────
def structure_positions(spot, flip, put_wall, call_wall):
    """Percentage positions along the structure bar, or None if undrawable.

    Returns ``{"put_wall": 0.0, "call_wall": 100.0, "spot": pct, "flip": pct|None}``
    with the walls pinned to the ends, since the bar's whole job is to show where
    price sits BETWEEN them.

    Percentages, not a viewBox: the caller applies them as ``left-[{pct}%]``
    Tailwind arbitrary values. Drawing this as a scaled SVG would need
    ``vector-effect: non-scaling-stroke`` to stop the non-uniform scale smearing
    the strokes, and DOMPurify strips that attribute — leaving strokes thick
    horizontally and hairline vertically while the server-side string stays
    perfectly correct, which is invisible to every test. Never raises.
    """
    lo, hi = _finite(put_wall), _finite(call_wall)
    s = _finite(spot)
    # No walls, no bar — and a non-finite spot is withheld rather than clamped,
    # because the clamp would place it exactly ON a wall (see ``_finite``).
    if lo is None or hi is None or s is None or hi <= lo:
        return None
    span = hi - lo

    def _pct(v):
        f = _finite(v)
        if f is None:
            return None
        return round(min(100.0, max(0.0, (f - lo) / span * 100.0)), 2)

    # The flip is optional decoration on a bar the walls already define, so a
    # missing (or non-finite) flip costs the tick, not the whole bar.
    return {"put_wall": 0.0, "call_wall": 100.0, "spot": _pct(s),
            "flip": _pct(flip)}


# ── dealer positioning rows ──────────────────────────────────────────────────
# ONE regime word, ONE source. ``gex_regime`` (spot vs the flip) is the only
# input; ``net_gex`` is displayed as a magnitude beside it and must never reach
# this map. The two can legitimately disagree — a symbol can sit above its flip
# while net GEX prints negative — and a row that made two conflicting regime
# claims would be the /sentiment/sectors-vs-/sentiment/rotation bug reproduced
# inside a single line of text.
REGIME_WORDS = {"above": "LONG GAMMA · PINS", "below": "SHORT GAMMA · RUNS",
                "na": "—"}
_NO_REGIME = "—"


def regime_word(gex_regime):
    """The dealer-regime headline for a matrix row's ``gex_regime``."""
    return REGIME_WORDS.get(gex_regime, _NO_REGIME)


def _walls_trustworthy(net_gex, stale):
    """Whether this row's call/put walls may be shown at all.

    Two ways they cannot be. **Stale**: the collector has stopped, so the walls
    describe some earlier tape. **net GEX present-but-exactly-zero**: index
    option open interest reads 0 after hours, which yields an all-zero GEX grid,
    and the wall picked out of an all-zero grid is an artefact of the argmax tie-
    break — an arbitrary strike wearing the authority of a level. Absent net GEX
    is NOT that signature (the symbol simply doesn't publish the figure), so it
    keeps its walls.
    """
    if stale:
        return False
    return not (net_gex is not None and net_gex == 0.0)


def dealer_rows(matrix_view, stale):
    """Dealer-positioning rows for ``DESK_SYMBOLS``, in that order.

    ``matrix_view`` is the ``cache:options:matrix`` payload. Symbols the matrix
    does not carry are simply absent — the Desk never invents a row. Total over a
    missing / malformed view.
    """
    rows = (matrix_view or {}).get("rows") if isinstance(matrix_view, dict) else None
    if not isinstance(rows, list):
        return []
    # First row per symbol wins; the matrix publishes one row per symbol, so a
    # duplicate is a producer bug and taking the later one would hide it.
    by_symbol = {}
    for r in rows:
        if isinstance(r, dict) and r.get("symbol") not in by_symbol:
            by_symbol[r.get("symbol")] = r

    out = []
    for sym in DESK_SYMBOLS:
        r = by_symbol.get(sym)
        if r is None:
            continue
        spot, flip = _finite(r.get("spot")), _finite(r.get("flip"))
        net_gex = _finite(r.get("net_gex"))
        show_walls = _walls_trustworthy(net_gex, stale)
        call_wall = _finite(r.get("call_wall")) if show_walls else None
        put_wall = _finite(r.get("put_wall")) if show_walls else None
        side, dist = _flip_read(spot, flip)
        out.append({
            "symbol": sym,
            "spot": spot,
            "day_pct": _finite(r.get("day_pct")),
            "flip": flip,
            "flip_distance": dist,
            "flip_side": side,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "net_gex": net_gex,
            "regime_word": regime_word(r.get("gex_regime")),
            "structure": structure_positions(spot, flip, put_wall, call_wall),
            "stale": bool(stale),
        })
    return out


def _flip_read(spot, flip):
    """``(side, distance_pct)`` — which side of the flip spot sits on, and how far.

    The distance is a MAGNITUDE in percent of the flip level (so $SPX and SPY are
    comparable at a glance); the side carries the sign. ``(None, None)`` whenever
    either input is missing or non-finite — a flip side is a claim about dealer
    hedging, and there is no honest one to make without both numbers.
    """
    if spot is None or flip is None or flip == 0:
        return None, None
    return ("above" if spot >= flip else "below",
            round(abs(spot - flip) / abs(flip) * 100.0, 4))
