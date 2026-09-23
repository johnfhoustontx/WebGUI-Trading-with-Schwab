"""The dealer-structure bar's geometry — where spot and the gamma flip sit
between the put wall and the call wall, as percentages along the bar.

Shared by the Desk (``/desk``) and the Symbol Dossier. It lived in
``pages/desk.py`` until a second page needed it; a page reaching into another
PAGE module for its arithmetic is the wrong shape, and this follows the
``pages/scorecard.py`` precedent (moved out of a page module on 2026-09-12
for the same reason). ``desk`` re-exports ``structure_positions``,
so ``desk.structure_positions`` still resolves for its existing tests.

Everything here is PURE (no bus reads, never raises) except ``structure_map``,
the one widget builder: it draws the bar the geometry describes. It lives
beside that geometry rather than in either page so the Desk and the Dossier
cannot draw the same span two ways. The wall-trust rule (``walls_trustworthy``),
the flip read (``flip_read``) and the regime word (``regime_word``) moved here
from ``pages/desk.py`` for the same reason; ``desk`` re-exports each by name.
"""
from nicegui import ui

from pages.console import left_class as _left_class
from pages.fmt import num as _finite  # the ONE copy (pages/fmt.py)


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


# ── the regime word ──────────────────────────────────────────────────────────
# ONE regime word, ONE source. ``gex_regime`` (spot vs the flip) is the only
# input; ``net_gex`` is displayed as a magnitude beside it and must never reach
# this map. The two can legitimately disagree — a symbol can sit above its flip
# while net GEX prints negative — and a row that made two conflicting regime
# claims would be the /sentiment/sectors-vs-/sentiment/rotation bug reproduced
# inside a single line of text.
REGIME_WORDS = {"above": "LONG GAMMA · PINS", "below": "SHORT GAMMA · RUNS",
                "na": "—"}
NO_REGIME = "—"


def regime_word(gex_regime):
    """The dealer-regime headline for a matrix row's ``gex_regime``."""
    return REGIME_WORDS.get(gex_regime, NO_REGIME)


# ── wall trust and the flip read ─────────────────────────────────────────────
def walls_trustworthy(net_gex, stale):
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


def flip_read(spot, flip):
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


# ── the drawn bar ────────────────────────────────────────────────────────────
# The wall/flip/spot palette. Fixed constants, so the class set stays finite.
CALL_HEX = "#2dd4a7"                   # the call wall, and its marker on the map
PUT_HEX = "#fb5f7c"                    # the put wall, and its marker
FLIP_HEX = "#f5b841"                   # the gamma flip tick
SPOT_HEX = "#22d3ee"                   # the spot dot
MAP_EDGE = "border-[#14202c]"          # the map's two end walls


def structure_map(pos):
    """The put-wall → call-wall span with spot and the gamma flip marked on it.

    This is a CELL of the dealer row (the flexible 4th track), not a tier of its
    own: the whole reading is "where does price sit between the walls", which is
    only legible beside the symbol and the two wall prices it refers to.

    Positioned divs at ``left-[{pct}%]``, NOT a scaled ``viewBox`` SVG: that
    would need ``vector-effect: non-scaling-stroke`` to stop the non-uniform
    scale smearing the strokes, and NiceGUI's bundled DOMPurify strips that
    attribute — the strokes render thick horizontally and hairline vertically
    while the server-side string stays perfectly correct, so no test can see it.
    This repo has been bitten by that twice.

    The percentages are a genuinely continuous computed position, which is the
    documented exception to the map-to-a-finite-palette rule; every COLOUR below
    is a fixed palette constant.
    """
    with ui.element("div").classes(
            f"relative h-[34px] w-full border-l border-r {MAP_EDGE} px-[2px]"):
        # ── the track, and it is NOT decoration ──────────────────────────────
        # These two elements are the reference design's own, and they were lost
        # because the reference markup was transcribed from a TRUNCATED extract
        # that ended before them. Without them the map is four floating markers
        # over empty space: there is nothing connecting the put wall to the call
        # wall, so the row reads as scattered ticks rather than as one span with
        # price somewhere along it — which is the entire reading this cell
        # exists to give. They are the FIRST two children because painting order
        # is what puts them BEHIND the markers; move them and the band covers
        # the spot ring.
        #
        # 1. The hairline: the axis itself, running the full width so the span
        #    is continuous even where the band's rounded ends fall short.
        ui.element("div").classes(
            "absolute left-0 right-0 top-[21px] h-px bg-[#1a2836]")
        # 2. The band: rose at the put end fading to green at the call end, so
        #    the DIRECTION of the span is legible before any number is read —
        #    the spot ring's position on it then says which half price is in.
        #    Both stops carry the wall hexes at .16 alpha; written as `rgba()`
        #    with NO SPACES, because a Tailwind arbitrary value cannot contain
        #    one (underscore is the escape, commas are fine).
        ui.element("div").classes(
            "absolute top-[14px] h-[8px] rounded-[1px] left-[2px] right-[2px] "
            "bg-gradient-to-r from-[rgba(251,95,124,0.16)] "
            "to-[rgba(45,212,167,0.16)]")
        # The markers, painted over that track. Two hairline uprights for the
        # walls: they are the span's ends, the only part of it that is a fixed
        # fact.
        ui.element("div").classes(
            f"absolute top-[6px] bottom-[4px] w-[2px] bg-[{PUT_HEX}] "
            f"shadow-[0_0_7px_rgba(251,95,124,0.7)] "
            f"{_left_class(pos['put_wall'])}")
        # The call wall sits at 100%, so without pulling it back by its own
        # width it would hang past the map and widen the whole panel by 2px —
        # enough to make the row report a horizontal overflow.
        ui.element("div").classes(
            f"absolute top-[6px] bottom-[4px] w-[2px] ml-[-2px] "
            f"bg-[{CALL_HEX}] shadow-[0_0_7px_rgba(45,212,167,0.7)] "
            f"{_left_class(pos['call_wall'])}")
        if pos.get("flip") is not None:
            ui.element("div").classes(
                f"absolute top-[2px] bottom-0 w-px bg-[{FLIP_HEX}] "
                f"opacity-[.85] {_left_class(pos['flip'])}")
            # Named, because a lone amber hairline between two glowing walls is
            # not self-explanatory. `ml-[-6px]` half-centres it on the tick.
            ui.label("FLIP").classes(
                f"absolute bottom-[-4px] ml-[-7px] text-[9px] "
                f"text-[#4b6070] {_left_class(pos['flip'])}")
        if pos.get("spot") is not None:
            # A ring, not a dot: it has to read as a position ON the span
            # rather than as a third wall. `ml-[-5px]` centres its 11px on the
            # percentage (a negative margin, so nothing overflows to the right).
            ui.element("div").classes(
                f"absolute top-[10px] w-[11px] h-[11px] ml-[-5px] rounded-full "
                f"border-2 border-[{SPOT_HEX}] bg-[#06121a] "
                f"shadow-[0_0_12px_rgba(34,211,238,0.75)] "
                f"{_left_class(pos['spot'])}")
