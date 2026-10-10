"""The gravity well's arithmetic: net gamma by strike as terrain, where price
sits on it, and which way the ground slopes.

Design: docs/plans/2026-10-10-gamma-contours-and-well-design.md
"""
import ast
import pathlib

import pytest

from pages.options import gamma_well as gw

STRIKES = [90.0, 95.0, 100.0, 105.0, 110.0]


def _terrain(nets):
    return gw.terrain(STRIKES, nets)


# ── the terrain ──────────────────────────────────────────────────────────────

def test_positive_gamma_is_a_valley_and_negative_gamma_a_hill():
    t = _terrain([-4.0, 0.0, 10.0, 2.0, -6.0])
    assert [(k, h, n) for k, h, n in t] == [
        (90.0, 4.0, -4.0), (95.0, 0.0, 0.0), (100.0, -10.0, 10.0),
        (105.0, -2.0, 2.0), (110.0, 6.0, -6.0)]


def test_terrain_is_in_strike_order_and_skips_what_is_not_a_number():
    t = gw.terrain([100.0, 90.0, 95.0, "x", 105.0, 110.0, True],
                   [1.0, 2.0, None, 3.0, float("nan"), float("inf"), 4.0])
    assert t == [(90.0, -2.0, 2.0), (100.0, -1.0, 1.0)]
    assert gw.terrain(None, None) == [] and gw.terrain([], [1.0]) == []


def test_a_root_scale_compresses_the_height_and_keeps_the_net():
    """One strike can hold fifty times its neighbours: drawn in proportion, it
    is a spike on flat ground. The square root keeps every strike's side of
    zero and their order, and lets the small ones show."""
    t = gw.terrain([90.0, 95.0, 100.0], [-4.0, 0.0, 16.0], scale="root")
    assert t == [(90.0, 2.0, -4.0), (95.0, 0.0, 0.0), (100.0, -4.0, 16.0)]
    linear = gw.terrain([90.0, 95.0, 100.0], [-4.0, 0.0, 16.0], scale="linear")
    assert linear == [(90.0, 4.0, -4.0), (95.0, 0.0, 0.0), (100.0, -16.0, 16.0)]
    # Anything else is linear: the plain reading is the fallback.
    assert gw.terrain([90.0, 100.0], [-4.0, 16.0], scale="cube") == [
        (90.0, 4.0, -4.0), (100.0, -16.0, 16.0)]


def test_the_net_at_price_is_read_from_the_nets_not_the_drawn_height():
    """On a root scale the height is not the net any more, and the caption's
    "positive gamma" must still come from the real figure."""
    t = gw.terrain([90.0, 100.0], [-4.0, 16.0], scale="root")
    r = gw.read(t, 95.0)
    assert r["net"] == 6.0 and r["ground"] == "valley"       # halfway between -4 and 16
    assert r["height"] == -1.0                               # halfway between 2 and -4


def test_the_height_between_two_strikes_is_on_the_line_between_them():
    t = _terrain([0.0, 0.0, 10.0, 2.0, 0.0])
    assert gw.height_at(t, 100.0) == -10.0
    assert gw.height_at(t, 102.5) == -6.0
    assert gw.height_at(t, 110.0) == 0.0


@pytest.mark.parametrize("spot", [89.9, 110.1, None, "100", True, float("nan")])
def test_there_is_no_height_off_the_terrain(spot):
    assert gw.height_at(_terrain([0.0, 0.0, 10.0, 2.0, 0.0]), spot) is None


# ── where price sits, and which way is downhill ──────────────────────────────

def test_price_in_a_valley_rolls_to_its_floor():
    r = gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 103.0)
    assert r["ground"] == "valley" and r["net"] == pytest.approx(5.2)
    assert (r["direction"], r["floor"], r["floor_net"]) == (-1, 100.0, 10.0)
    assert r["height"] == pytest.approx(-5.2) and r["floor_height"] == -10.0


def test_price_on_the_floor_has_nowhere_to_roll():
    r = gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 100.0)
    assert (r["direction"], r["floor"]) == (0, 100.0)


def test_the_roll_carries_on_down_to_the_local_low():
    # Downhill from 92 runs through 95 and 100 to 105, and stops there.
    r = gw.read(_terrain([1.0, 3.0, 6.0, 10.0, 2.0]), 92.0)
    assert (r["direction"], r["floor"]) == (1, 105.0)


def test_from_the_top_of_a_hill_price_rolls_the_steeper_way():
    # At 100: the ground drops 11 toward 95 and 8 toward 105.
    r = gw.read(_terrain([5.0, 1.0, -10.0, -2.0, 8.0]), 100.0)
    assert r["ground"] == "hill" and r["net"] == -10.0
    assert (r["direction"], r["floor"], r["floor_net"]) == (-1, 90.0, 5.0)


def test_a_low_point_can_still_be_negative_gamma():
    r = gw.read(_terrain([-10.0, -2.0, -8.0, -9.0, -20.0]), 99.0)
    assert (r["direction"], r["floor"], r["floor_net"]) == (-1, 95.0, -2.0)
    assert r["ground"] == "hill"


def test_flat_ground_has_no_downhill():
    r = gw.read(_terrain([5.0, 5.0, 5.0, 5.0, 5.0]), 97.0)
    assert (r["direction"], r["floor"]) == (0, 95.0)          # the nearer strike
    assert gw.read(_terrain([5.0, 5.0, 5.0, 5.0, 5.0]), 98.0)["floor"] == 100.0


def test_level_ground_is_told_apart_from_a_low_point():
    """Both have no downhill. A low point has higher ground beside it; level
    ground does not, and calling it "the low point" would be a claim about a
    shape that is not there."""
    flat = _terrain([5.0, 5.0, 5.0, 5.0, 5.0])
    assert gw.read(flat, 97.0)["level"] is True               # between two strikes
    assert gw.read(flat, 100.0)["level"] is True              # on a strike
    low = gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 100.0)
    assert low["direction"] == 0 and low["level"] is False
    assert gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 103.0)["level"] is False


def test_ground_with_no_gamma_at_all_is_empty():
    assert gw.empty(_terrain([0.0, 0.0, 0.0, 0.0, 0.0])) is True
    assert gw.empty([]) is True
    assert gw.empty(_terrain([0.0, 0.0, 0.1, 0.0, 0.0])) is False


def test_price_exactly_where_net_gamma_is_zero_is_level_ground():
    r = gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 95.0)
    assert r["ground"] == "level" and r["net"] == 0.0
    assert (r["direction"], r["floor"]) == (1, 100.0)


@pytest.mark.parametrize("points, spot", [
    ([], 100.0), ([(100.0, -1.0, 1.0)], 100.0)])
def test_there_is_no_read_without_a_terrain(points, spot):
    assert gw.read(points, spot) is None


def test_there_is_no_read_when_price_is_off_the_terrain():
    assert gw.read(_terrain([1.0, 2.0, 3.0, 2.0, 1.0]), 120.0) is None
    assert gw.read(_terrain([1.0, 2.0, 3.0, 2.0, 1.0]), None) is None


def test_read_does_not_write_to_the_terrain():
    t = _terrain([-4.0, 0.0, 10.0, 2.0, -6.0])
    before = list(t)
    gw.read(t, 103.0)
    assert t == before


# ── the sentence under the panel ─────────────────────────────────────────────

def test_the_caption_for_a_valley_names_the_floor_and_how_far():
    text = gw.caption(gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 103.0), 103.0)
    assert "positive gamma" in text and "damps" in text
    assert "toward 100.00, 3.00 below" in text
    assert "still negative" not in text


def test_the_caption_for_a_hill_says_hedging_amplifies():
    text = gw.caption(gw.read(_terrain([5.0, 1.0, -10.0, -2.0, 8.0]), 104.0), 104.0)
    assert "negative gamma" in text and "amplifies" in text
    assert "toward 110.00, 6.00 above" in text


def test_the_caption_says_when_the_low_point_is_still_negative_gamma():
    text = gw.caption(gw.read(_terrain([-10.0, -2.0, -8.0, -9.0, -20.0]), 99.0), 99.0)
    assert "toward 95.00, 4.00 below" in text
    assert "That low point is still negative gamma" in text


def test_the_caption_on_level_ground_does_not_name_a_low_point():
    text = gw.caption(gw.read(_terrain([5.0, 5.0, 5.0, 5.0, 5.0]), 97.0), 97.0)
    assert "The ground is level here." in text
    assert "low point" not in text and "toward" not in text


def test_the_caption_with_no_gamma_says_there_is_nothing_to_draw():
    """An index's net gamma reads zero at every strike outside market hours."""
    t = _terrain([0.0, 0.0, 0.0, 0.0, 0.0])
    text = gw.caption(gw.read(t, 97.0), 97.0, empty=gw.empty(t))
    assert text == ("Net gamma is zero at every strike shown, so there is no "
                    "ground to draw.")


def test_the_caption_at_the_floor_says_so():
    text = gw.caption(gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 100.0), 100.0)
    assert "at the low point, 100.00" in text and "toward" not in text


def test_every_caption_says_it_is_not_a_forecast():
    for nets, spot in (([-4.0, 0.0, 10.0, 2.0, -6.0], 103.0),
                       ([5.0, 1.0, -10.0, -2.0, 8.0], 100.0),
                       ([5.0, 5.0, 5.0, 5.0, 5.0], 97.0)):
        assert gw.caption(gw.read(_terrain(nets), spot), spot).endswith(
            "A picture of the gamma profile, not a forecast.")
    assert gw.caption(None, 100.0) == ""


def test_the_arrow_points_at_the_floor():
    left = gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 103.0)
    right = gw.read(_terrain([1.0, 3.0, 6.0, 10.0, 2.0]), 92.0)
    still = gw.read(_terrain([-4.0, 0.0, 10.0, 2.0, -6.0]), 100.0)
    assert gw.arrow(left) == chr(0x2190) + " 100.00"       # a left arrow
    assert gw.arrow(right) == "105.00 " + chr(0x2192)      # a right arrow
    assert gw.arrow(still) == "" and gw.arrow(None) == ""


# ── the module ───────────────────────────────────────────────────────────────

def test_the_module_is_pure():
    """No NiceGUI, no bus, nothing from ``gamma`` (which imports it)."""
    tree = ast.parse(pathlib.Path(gw.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert names <= {"math", "pages"}, names
