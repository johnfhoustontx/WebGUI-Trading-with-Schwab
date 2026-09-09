"""The arithmetic that decides how wide a Desk panel has to be.

A Desk panel is not a table: it is a set of CSS grids — a head row plus one
grid per data row — sharing a single ``grid-template-columns`` string of
``minmax()`` tracks. A CSS grid will not shrink a track below its ``minmax()``
floor, so a panel whose floors oversubscribe the width it is given does not
reflow; it OVERFLOWS its card. Making the panel its own horizontal scroll
container needs an explicit ``min-width``, and this is where that number comes
from.

**Every number asserted here was measured in a real browser**, headless Chrome
against ``https://live.neuralstrike.co/desk`` (``tools/measure_screen_widths.py``
builds the driver). The measurements are quoted at their assertions rather than
summarised, because a width test that pins whatever the code happens to compute
is the "characterization test records the bug" failure this repo has already
paid for once.
"""
import ast
import pathlib
import re

import pytest
from pages import desk as d
from pages import panel_scroll as ps


# ---------------------------------------------------------------------------
# track_floors — reading the floors back out of the Tailwind class string
# ---------------------------------------------------------------------------

def test_track_floors_reads_a_mixed_fixed_and_minmax_template():
    """The documented example: a bare px track, then two minmax tracks."""
    grid = ("grid grid-cols-[64px_minmax(53px,0.8fr)_minmax(42px,0.6fr)] "
            "gap-x-[8px] w-full")
    assert ps.track_floors(grid) == [64, 53, 42]


def test_track_floors_takes_the_floor_of_a_minmax_never_the_ceiling():
    """``minmax(200px, 5fr)`` floors at 200 — the 5fr is slack, not width."""
    assert ps.track_floors("grid-cols-[minmax(200px,5fr)]") == [200]


def test_track_floors_reads_every_desk_grid_at_its_real_track_count():
    """The four panels have 7 / 8 / 4 / 10 tracks — measured in the DOM.

    ``getComputedStyle(row).gridTemplateColumns`` on the live page returned
    seven, eight, four and ten values respectively, so a parser that silently
    dropped a track would be caught here rather than by a 1px width drift.
    """
    assert len(ps.track_floors(d.DEALER_GRID)) == 7
    assert len(ps.track_floors(d.BOARD_GRID)) == 8
    assert len(ps.track_floors(d.FLOW_GRID)) == 4
    assert len(ps.track_floors(d.POS_GRID)) == 10


def test_track_floors_refuses_a_string_with_no_template():
    """A caller that hands over the wrong class string gets an error.

    Returning ``[]`` would make ``panel_min_width_px`` report ``PANEL_PAD_PX``
    — a small, plausible number — for a grid it never parsed. That is the
    degrade-into-a-confident-answer shape, so it raises instead.
    """
    with pytest.raises(ValueError):
        ps.track_floors("grid gap-x-[8px] w-full")


def test_track_floors_refuses_a_template_it_only_half_understands():
    """An ``auto`` or ``1fr`` track has no floor this arithmetic can use.

    None is used on the Desk today. If one is introduced, the sum below stops
    being a minimum and this must be the thing that says so.
    """
    with pytest.raises(ValueError):
        ps.track_floors("grid-cols-[64px_auto_minmax(42px,0.6fr)]")
    with pytest.raises(ValueError):
        ps.track_floors("grid-cols-[64px_1fr]")


# ---------------------------------------------------------------------------
# The POS_GRID anchor — and the reconciliation behind the number
# ---------------------------------------------------------------------------

def test_pos_grid_floors_sum_to_the_725_desk_states_independently():
    """64+53+42+144+53+53+126+36+94+60.

    MEASURED: ``getComputedStyle`` on the live Positions row, at a width where
    every track is on its floor, returned exactly
    ``64px 53px 42px 144px 53px 53px 126px 36px 94px 60px``.
    """
    assert sum(ps.track_floors(d.POS_GRID)) == 725


def test_pos_grid_content_minimum_is_797():
    """Ten floors (725) plus NINE 8px gaps (72). The grid's content box.

    MEASURED: the live row's ten children spanned x=4 to x=801 relative to its
    border box — 797px of content, sitting inside its own 4px left padding.
    """
    assert ps.grid_content_width_px(d.POS_GRID) == 797


def test_pos_grid_row_minimum_is_805_which_is_what_the_browser_reports():
    """The row's own border box: 797 of tracks plus its ``px-1``, both sides.

    ⚠ This is the number that reconciles the formula with the browser, and it
    only appears once the row is a SCROLL CONTAINER.

    MEASURED on the live Positions row, same element, same instant::

        overflow-x: visible  ->  scrollWidth 801
        overflow-x: auto     ->  scrollWidth 805

    801 is not a smaller row; it is ``scrollWidth`` omitting the END padding of
    a box that does not scroll. A synthetic control pinned that as the whole of
    the difference — a 100px box with 7px left and 11px right padding around a
    400px child reported 407 at ``overflow:visible`` and 418 at ``auto``, i.e.
    7+400 versus 7+400+11. Task 3 gives the panel ``overflow-x: auto``, so 805
    is the number the browser will agree with afterwards.
    """
    assert ps.grid_min_width_px(d.POS_GRID) == 805


def test_pos_grid_panel_minimum_is_the_measured_839():
    """The anchor. The panel CARD's border box, which is what has to fit.

    805 of row, plus the panel's ``px-4`` both sides (32) and the card's 1px
    border both sides (2) — the three that ``PANEL_PAD_PX`` bundles with the
    row's own padding.

    MEASURED, and this one is direct rather than reconstructed: stepping the
    public origin's window width, the Positions card's ``offsetWidth`` reached
    **839** at exactly the width its ``scrollWidth - clientWidth`` first
    reached **0**. The card stops overflowing precisely at 839px, so this is
    the boundary itself and not an estimate of it.
    """
    assert ps.panel_min_width_px(d.POS_GRID) == 839


def test_the_gap_between_839_and_the_browser_is_fully_accounted_for():
    """Why no ``scrollWidth`` reading will ever equal the panel minimum.

    ``scrollWidth`` is a PADDING-box measure, so it can never include the
    card's 2px border, and — on a non-scrolling box — it drops the end padding
    too. Against the measured card ``scrollWidth`` of 817 that is exactly
    16 (card padding-right) + 4 (row padding-right) + 2 (border) = 22.
    """
    measured_card_scroll_width = 817          # live, at a clipping width
    assert ps.panel_min_width_px(d.POS_GRID) - measured_card_scroll_width == 22
    # And against the row's own scrolling measurement, the remainder is the
    # card's padding and border and nothing else.
    assert (ps.panel_min_width_px(d.POS_GRID)
            - ps.grid_min_width_px(d.POS_GRID)) == 32 + 2


# ---------------------------------------------------------------------------
# THE DISCRIMINATING TEST — the anchor above is worthless without this
# ---------------------------------------------------------------------------

def test_the_minimum_moves_when_a_track_floor_moves():
    """Mutate one floor; every number downstream must follow it.

    A typed constant passes the 839 anchor on the day it is written and rots
    silently the first time a track changes — which is exactly how this page
    already accumulated a stale "the Board is over by 78px" (it is over by
    100px since NET PREMIUM widened). So the anchor is pinned by arithmetic
    that has to MOVE, not by a number that has to match.
    """
    base = d.POS_GRID
    widened = base.replace("minmax(94px,1.3fr)", "minmax(104px,1.3fr)")
    assert widened != base, "the fixture no longer matches POS_GRID"

    assert ps.panel_min_width_px(widened) - ps.panel_min_width_px(base) == 10
    assert ps.grid_content_width_px(widened) - ps.grid_content_width_px(base) == 10
    assert sum(ps.track_floors(widened)) - sum(ps.track_floors(base)) == 10


def test_the_minimum_moves_when_a_track_is_added_or_removed():
    """A dropped track takes its floor AND one gap with it."""
    base = d.FLOW_GRID
    dropped = base.replace("minmax(126px,2fr)_", "", 1).replace(
        "_minmax(126px,2fr)", "", 1)
    assert len(ps.track_floors(dropped)) == len(ps.track_floors(base)) - 1
    # 126 of track and one 8px gap.
    assert (ps.panel_min_width_px(base)
            - ps.panel_min_width_px(dropped)) == 126 + ps.COL_GAP_PX


def test_a_fractional_floor_rounds_the_minimum_UP_never_down():
    """A minimum rounded down is not a minimum.

    No Desk track is fractional today, so nothing exercises this in the wild —
    which is exactly why it is pinned here. A ``min-width`` one pixel short
    leaves the panel clipping in the one case the whole feature exists for.
    """
    grid = "grid-cols-[64.5px_minmax(53px,0.8fr)]"
    # 64.5 + 53 + one 8px gap = 125.5.
    assert ps.track_floors(grid) == [64.5, 53]
    assert ps.grid_content_width_px(grid) == 126
    assert ps.panel_min_width_px(grid) == 126 + ps.PANEL_PAD_PX


def test_the_gap_constant_is_load_bearing_not_decorative():
    """Nine gaps on Positions — change the constant, move the answer by nine."""
    tracks = ps.track_floors(d.POS_GRID)
    assert ps.grid_content_width_px(d.POS_GRID) == (
        sum(tracks) + (len(tracks) - 1) * ps.COL_GAP_PX)


# ---------------------------------------------------------------------------
# Every panel against the budget it is spent from
# ---------------------------------------------------------------------------

# Measured live on the public Desk with every track on its floor:
#   dealer  gridTemplateColumns summed to 667 over 7 tracks
#   board                              to 707 over 8
#   positions                          to 725 over 10
# (Flow was above its floors at the width measured, which is why the floors are
# read from the SOURCE string and never from computed style.)
DESK_PANELS = {
    "dealer": (d.DEALER_GRID, 757),
    "board": (d.BOARD_GRID, 805),
    "flow": (d.FLOW_GRID, 508),
    "positions": (d.POS_GRID, 839),
}


@pytest.mark.parametrize("name", sorted(DESK_PANELS))
def test_every_desk_panel_fits_the_budget_it_is_spent_from(name):
    """The whole point of the number: it is an upper bound, not a target."""
    grid, _ = DESK_PANELS[name]
    assert ps.panel_min_width_px(grid) <= d.PANEL_BUDGET_PX


@pytest.mark.parametrize("name", sorted(DESK_PANELS))
def test_each_panel_minimum_is_the_width_desk_documents_for_it(name):
    """The four figures ``desk.py``'s own prose quotes, held to the code."""
    grid, expected = DESK_PANELS[name]
    assert ps.panel_min_width_px(grid) == expected


def test_positions_is_the_panel_that_sets_the_page_minimum():
    """Not an aside — the comments in ``desk.py`` size every other track
    against Positions being the widest, so a reordering must be caught."""
    widest = max(DESK_PANELS, key=lambda n: ps.panel_min_width_px(DESK_PANELS[n][0]))
    assert widest == "positions"


# ---------------------------------------------------------------------------
# One home per constant
# ---------------------------------------------------------------------------

def test_desk_takes_the_padding_constants_from_here_rather_than_restating_them():
    """``desk.py`` must IMPORT these, not carry a second copy.

    Two homes for a padding constant is how the 839 and the 78 drifted apart
    from the CSS in the first place.
    """
    assert d.PANEL_PAD_PX is ps.PANEL_PAD_PX
    assert d.COL_GAP_PX is ps.COL_GAP_PX
    source = pathlib.Path(d.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^PANEL_PAD_PX\s*=", source, re.M)
    assert not re.search(r"^COL_GAP_PX\s*=", source, re.M)


def test_panel_pad_decomposes_into_the_three_css_values_it_is_made_of():
    """42 is not a magic number: 2 of border, 32 of ``px-4``, 8 of ``px-1``.

    All three read straight off ``getComputedStyle`` on the live page — the
    card reported ``borderLeftWidth: 1px`` and ``paddingLeft: 16px``, the row
    ``paddingLeft: 4px``.
    """
    assert ps.PANEL_PAD_PX == (ps.CARD_BORDER_PX + ps.PANEL_INSET_PX
                               + ps.ROW_INSET_PX)
    assert (ps.CARD_BORDER_PX, ps.PANEL_INSET_PX, ps.ROW_INSET_PX) == (2, 32, 8)


def test_the_module_is_pure():
    """No NiceGUI, no I/O — it has to be testable with no browser and no app."""
    tree = ast.parse(pathlib.Path(ps.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"re"}, imported
