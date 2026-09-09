"""How wide a dashboard panel has to be, derived from its own grid string.

The Desk's panels are not tables. Each is a set of CSS grids — a head row plus
one grid per data row — that all share a single ``grid-template-columns`` string
of ``minmax(<floor>, <weight>fr)`` tracks. **A CSS grid will not shrink a track
below its ``minmax()`` floor.** So a panel handed less width than its floors add
up to does not reflow: its rows overflow the card they sit in, and (once the
page's padding chain is used up) the whole document scrolls sideways, carrying
the panel heading and the row-identifying first column off screen.

The fix is to make each panel its own horizontal scroll container — and that
needs an explicit ``min-width`` on the grids, because without one the tracks
keep being squeezed and there is nothing for the container to scroll. This
module is where that width comes from. It is PURE: ``re`` and nothing else, no
NiceGUI, no I/O, so the arithmetic is testable with no browser and no app.

**The number is derived from the CSS, and then confirmed against the browser.**
Both halves matter — a width constant that merely matches what the code
currently computes is the "characterization test pins the wrong value" failure
this repo has already paid for. What the two halves say, for the Positions
panel (the widest, and so the one that sets the page's minimum):

======  =========================================================
  725   the ten ``minmax()`` floors, summed
  +72   nine 8px column gaps (``COL_GAP_PX``)
  =797  the grid's CONTENT box
   +8   the row's own ``px-1``, both sides   (``ROW_INSET_PX``)
  =805  the row's BORDER box — what the grids get as ``min-width``
  +32   the panel's ``px-4``, both sides     (``PANEL_INSET_PX``)
   +2   the card's 1px border, both sides    (``CARD_BORDER_PX``)
  =839  the panel CARD's border box — what has to fit
======  =========================================================

⚠ **Two of those lines cannot be seen in a ``scrollWidth`` reading, and knowing
which is what reconciles the arithmetic with the browser.** Measured live on
``live.neuralstrike.co/desk`` in headless Chrome, at a width where the panel is
clipping, the Positions row reports ``scrollWidth`` **801** and its card
**817** — neither of which is 805 or 839, and both of which look like evidence
against the sums above. They are not:

* ``scrollWidth`` is a **padding-box** measure, so it can never include the
  card's 2px border.
* On a box that is **not a scroll container**, ``scrollWidth`` also omits the
  **end** padding. A synthetic control settled this rather than the internet
  did: one 100px box, 7px of left padding and 11px of right around a 400px
  child, reported **407** at ``overflow-x: visible`` and **418** at ``auto``
  and at ``hidden`` — i.e. ``7+400`` versus ``7+400+11``. The live Positions
  row reproduces it exactly: flipping that one element to ``overflow-x: auto``
  and reading again moved it **801 -> 805**, which is this module's
  ``grid_min_width_px``. Task 3 gives the panel ``overflow-x: auto``, so the
  browser agrees with the arithmetic once the scroll container exists.

So 817 falls 22px short of 839, and that 22 is fully accounted for: 16 of the
card's own padding-right, 4 of the row's, and 2 of border.

**The 839 is also measured directly, with no reconstruction at all.** Stepping
the public origin's window width, the Positions card's ``offsetWidth`` reached
839 at exactly the width where its ``scrollWidth - clientWidth`` first reached
0. The card stops overflowing at 839px; that is the boundary rather than an
estimate of it.
"""
import re

# What a panel spends before its first track. Split into the three CSS values
# it is actually made of, because "42" on its own is the kind of constant that
# survives the change that invalidates it. Every one read straight off
# ``getComputedStyle`` on the live page: the card reports ``borderLeftWidth:
# 1px`` and ``paddingLeft: 16px``, the row ``paddingLeft: 4px``.
CARD_BORDER_PX = 2                # the card's 1px border, both sides
PANEL_INSET_PX = 32               # the panel's ``px-4``, both sides (``_panel``)
ROW_INSET_PX = 8                  # the row's ``px-1``, both sides (``_ROW``)
PANEL_PAD_PX = CARD_BORDER_PX + PANEL_INSET_PX + ROW_INSET_PX

# The ``gap-x-[8px]`` every panel grid carries (``desk._GAP``). There are
# ``len(tracks) - 1`` of them, which is why a track dropped from a panel takes
# a gap with it and a track added brings one.
COL_GAP_PX = 8

# A Tailwind arbitrary grid template: ``grid-cols-[64px_minmax(53px,0.8fr)_...]``
# — underscores where the CSS would have spaces, and the whole thing is ONE
# class, so it has to be dug out of a class string rather than split on
# whitespace.
_TEMPLATE_RE = re.compile(r"grid-cols-\[([^\]]*)\]")
# One track: either a bare pixel width, or a ``minmax()`` whose FIRST argument
# is the floor. The second argument is slack (``fr``) and never a width.
_TRACK_RE = re.compile(
    r"^(?:minmax\(\s*(?P<floor>\d+(?:\.\d+)?)px\s*,[^)]*\)"
    r"|(?P<fixed>\d+(?:\.\d+)?)px)$")


def track_floors(grid_classes):
    """The minimum width of each column, in order, read from the class string.

    ``track_floors("grid grid-cols-[64px_minmax(53px,0.8fr)] gap-x-[8px]")``
    is ``[64, 53]``: a bare ``64px`` track floors at 64, and a
    ``minmax(53px, 0.8fr)`` floors at its first argument — the ``0.8fr`` is how
    the leftover is shared out, never a width the track is entitled to.

    ⚠ It **raises** rather than returning ``[]`` or skipping a track it cannot
    read. Both of the quiet options turn "I could not parse this" into a
    smaller, entirely plausible minimum width — a panel would be given a
    ``min-width`` it fits inside and would clip exactly as it does today, with
    nothing anywhere saying why. A track shape with no floor at all (``auto``,
    a bare ``1fr``) is the same problem: the sum stops being a minimum, and
    this is the only place that can notice.
    """
    match = _TEMPLATE_RE.search(grid_classes or "")
    if not match:
        raise ValueError(
            f"no grid-cols-[...] template in {grid_classes!r}")
    body = match.group(1).strip()
    if not body:
        raise ValueError(f"empty grid-cols-[] template in {grid_classes!r}")

    floors = []
    for track in _split_tracks(body):
        found = _TRACK_RE.match(track)
        if not found:
            raise ValueError(
                f"track {track!r} has no pixel floor this arithmetic can use "
                f"(in {grid_classes!r})")
        floors.append(float(found.group("floor") or found.group("fixed")))
    return [int(f) if f.is_integer() else f for f in floors]


def _split_tracks(body):
    """Split on the underscores BETWEEN tracks, not the ones inside ``minmax()``.

    Tailwind writes the template's spaces as underscores, so
    ``64px_minmax(53px,0.8fr)`` is one string with a separator that also
    appears — in principle — inside a function's arguments. Depth-counting is
    two lines and cannot be surprised by one; ``body.split("_")`` would shred
    any ``minmax(min(…),…)`` the day someone writes one.
    """
    tracks, depth, current = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "_" and depth == 0:
            tracks.append("".join(current))
            current = []
        else:
            current.append(char)
    tracks.append("".join(current))
    return [t for t in tracks if t]


def grid_content_width_px(grid_classes):
    """The grid's CONTENT box at its narrowest: every track on its floor.

    Floors plus the ``len(tracks) - 1`` gaps between them. This is the width
    the tracks physically occupy — 797px on Positions, confirmed in the DOM by
    the row's ten children spanning x=4 to x=801 of its border box.
    """
    floors = track_floors(grid_classes)
    total = sum(floors) + (len(floors) - 1) * COL_GAP_PX
    # ⚠ Round UP, never truncate. No floor on the Desk is fractional today, so
    # this looks like a formality — but the whole point of the number is that a
    # panel given one pixel less than it needs still clips, and ``int()`` on a
    # 796.5 would round in exactly the direction that reintroduces the bug.
    # Spelled out rather than imported from ``math``: this module stays a leaf
    # over ``re``, so there is nothing it can drag into Tier 1.
    return int(total) + (1 if total % 1 else 0)


def grid_min_width_px(grid_classes):
    """The ``min-width`` for the grid ELEMENTS — head row and data rows alike.

    The content width plus the row's own ``px-1``. Everything on this page is
    ``box-sizing: border-box`` (Tailwind's preflight), so a ``min-width`` set
    on the row has to carry that padding itself.

    805px on Positions — and 805 is exactly what the live row's ``scrollWidth``
    reports the moment it is made a scroll container, which is what Task 3 does
    to it.
    """
    return grid_content_width_px(grid_classes) + ROW_INSET_PX


def panel_min_width_px(grid_classes):
    """The narrowest the panel CARD can be drawn without its rows overflowing.

    The grid content plus ``PANEL_PAD_PX`` — the row's ``px-1``, the panel's
    ``px-4`` and the card's border. 839px on Positions, which is the widest of
    the Desk's four panels and therefore the number the page's minimum
    supported window is built out of (see ``desk.PANEL_BUDGET_PX``, the budget
    this is spent against).
    """
    return grid_content_width_px(grid_classes) + PANEL_PAD_PX
