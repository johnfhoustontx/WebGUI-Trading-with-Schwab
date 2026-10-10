"""The Dealer Positioning heatmap's pure transforms (value, scale, frame).

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
import ast
import pathlib
import re

import pytest

from pages.options import gamma_heat as gh

CELL = {"call": 300.0, "put": -280.0, "net": 20.0}


# ── the value of a cell ──────────────────────────────────────────────────────

@pytest.mark.parametrize("mode, want", [
    ("net", 20.0), ("call", 300.0), ("put", -280.0), ("size", 580.0)])
def test_cell_value(mode, want):
    assert gh.cell_value(CELL, mode) == want


def test_size_takes_its_sign_from_net():
    assert gh.cell_value({"call": 100.0, "put": -400.0, "net": -300.0}, "size") == -500.0


def test_size_of_a_cell_with_zero_net_sits_on_the_call_side():
    assert gh.cell_value({"call": 250.0, "put": -250.0, "net": 0.0}, "size") == 500.0


def test_size_uses_magnitudes_whatever_the_stored_signs():
    """Charm and vanna store calls and puts of either sign."""
    assert gh.cell_value({"call": -60.0, "put": -40.0, "net": -100.0}, "size") == -100.0


def test_size_without_a_stored_net_signs_by_the_sum_of_the_sides():
    assert gh.cell_value({"call": 10.0, "put": -30.0}, "size") == -40.0


@pytest.mark.parametrize("cell", [7.0, None, {}, {"net": 3.0}, {"call": 1.0},
                                  {"call": float("nan"), "put": 1.0, "net": 1.0},
                                  {"call": True, "put": 1.0, "net": 1.0}])
def test_size_needs_both_sides(cell):
    """A bare number, a missing side, a NaN or a flag is absent, never zero."""
    assert gh.cell_value(cell, "size") is None


@pytest.mark.parametrize("cell", [7.0, None, {}, {"net": 3.0},
                                  {"call": float("nan"), "put": 1.0, "net": 1.0},
                                  {"call": True, "put": 1.0, "net": 1.0}])
def test_a_cell_with_no_usable_call_has_no_call_value(cell):
    assert gh.cell_value(cell, "call") is None


def test_one_side_is_readable_without_the_other():
    assert gh.cell_value({"call": float("nan"), "put": -4.0}, "put") == -4.0


def test_a_bare_number_is_its_own_net():
    assert gh.cell_value(7.0, "net") == 7.0
    assert gh.cell_value(None, "net") is None


def test_an_unknown_value_is_refused():
    with pytest.raises(ValueError):
        gh.cell_value(CELL, "gross")


def test_the_picker_lists_the_values_in_reading_order():
    assert list(gh.VALUES) == ["net", "call", "put", "size"]
    assert gh.VALUES["call"] == "Calls"


def test_has_sides():
    assert gh.has_sides([{100.0: {"net": 1.0}}, {100.0: CELL}])
    assert not gh.has_sides([{100.0: {"net": 1.0}}, {100.0: 5.0}, {}, None])
    assert not gh.has_sides(None)


def test_it_imports_nothing_from_gamma():
    """gamma imports this module; the reverse would be a cycle."""
    tree = ast.parse(pathlib.Path(gh.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.endswith("gamma") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").endswith("gamma")
            assert "gamma" not in {a.name for a in node.names}


# ── balanced strikes ─────────────────────────────────────────────────────────

GRID = {
    95.0: {"call": 1.0, "put": -1.0, "net": 0.0},          # balanced but tiny
    100.0: {"call": 500.0, "put": -480.0, "net": 20.0},    # balanced and large
    105.0: {"call": 400.0, "put": -10.0, "net": 390.0},    # large, one-sided
    110.0: {"call": 300.0, "put": -290.0, "net": 10.0},    # balanced and large
    115.0: 7.0,                                            # a legacy bare number
}
KW = dict(max_polarity=0.15, min_size_quantile=0.5, max_marks=3)


def test_balanced_marks_are_the_large_strikes_that_barely_lean():
    assert gh.balanced_marks(GRID, sorted(GRID), **KW) == [100.0, 110.0]


def test_balanced_marks_are_capped_largest_first():
    assert gh.balanced_marks(GRID, sorted(GRID), **{**KW, "max_marks": 1}) == [100.0]
    assert gh.balanced_marks(GRID, sorted(GRID), **{**KW, "max_marks": 0}) == []


def test_balanced_marks_look_only_at_the_strikes_on_screen():
    assert gh.balanced_marks(GRID, [105.0, 110.0], **KW) == [110.0]


def test_a_one_sided_strike_is_never_marked_however_loose_the_size_rule():
    marks = gh.balanced_marks(GRID, sorted(GRID), **{**KW, "min_size_quantile": 0.0})
    assert marks == [100.0, 110.0, 95.0]


def test_balanced_marks_of_nothing():
    assert gh.balanced_marks({}, [], **KW) == []
    assert gh.balanced_marks(None, [100.0], **KW) == []
    assert gh.balanced_marks({100.0: 5.0}, [100.0], **KW) == []


# ── the legend strip ─────────────────────────────────────────────────────────

STOPS = [[0.0, "rgba(255,186,220,0.98)"], [0.5, "rgba(0,0,0,0.0)"],
         [1.0, "rgba(190,248,255,0.98)"]]


def _dompurify_allowlist():
    """The names DOMPurify keeps, read from the copy NiceGUI ships. Mirrors
    test_flow_panels.py; runs containing ``script`` are deny lists and skipped."""
    from nicegui import ui
    src = (pathlib.Path(ui.__file__).parent / "static" / "dompurify.mjs") \
        .read_text(encoding="utf-8", errors="replace")
    names = set()
    for run in re.findall(r'(?:"[a-z][a-z0-9-]*",){19,}"[a-z][a-z0-9-]*"', src):
        tokens = set(re.findall(r'"([a-z][a-z0-9-]*)"', run))
        if "script" not in tokens:
            names |= tokens
    assert len(names) > 300, "allowlist extraction found too little"
    return names


def test_legend_prints_both_ends_the_unit_and_the_caption():
    svg = gh.legend_svg(1_200_000_000.0, "adapts to what is visible", STOPS,
                        unit="$ gamma per 1% move")
    assert "-$1.20B" in svg and "+$1.20B" in svg
    assert "$ gamma per 1% move · adapts to what is visible" in svg


def test_legend_without_a_dollar_unit_prints_the_bare_number():
    svg = gh.legend_svg(4_500_000.0, "adapts to what is visible", STOPS, unit="")
    assert "-4.50M" in svg and "+4.50M" in svg and "$" not in svg
    assert ">adapts to what is visible<" in svg       # no stray separator


@pytest.mark.parametrize("zmax", [None, 0, 0.0, float("nan"), "big", True])
def test_legend_of_no_scale_is_empty(zmax):
    assert gh.legend_svg(zmax, "x", STOPS, unit="") == ""


def test_legend_runs_put_side_to_call_side_with_every_stop():
    svg = gh.legend_svg(10.0, "x", STOPS, unit="")
    assert svg.count("<stop ") == len(STOPS)
    assert svg.index("rgb(255,186,220)") < svg.index("rgb(190,248,255)")
    assert 'stop-opacity="0.00"' in svg               # the transparent zero stop


def test_legend_survives_the_sanitizer():
    """ui.html runs DOMPurify; a stripped tag or attribute fails silently."""
    allow = _dompurify_allowlist()
    svg = gh.legend_svg(10.0, "held since 09:30", STOPS, unit="$ delta")
    names = (set(re.findall(r"<([a-zA-Z][\w-]*)", svg))
             | set(re.findall(r'([a-zA-Z][\w-]*)="', svg)))
    stripped = sorted(n for n in names if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    assert {"svg", "linearGradient", "stop", "rect", "text"} <= names
    assert "dominant-baseline" not in svg and "data-" not in svg


def test_legend_units_name_the_two_dollar_views():
    assert gh.UNITS["GEX"].startswith("$") and gh.UNITS["DEX"].startswith("$")
    assert "Charm" not in gh.UNITS and "Vanna" not in gh.UNITS


def test_legend_in_percent_for_a_share_scale():
    svg = gh.legend_svg(8.3, "share of each column", STOPS, unit="%")
    assert "-8.30%" in svg and "+8.30%" in svg
    assert ">share of each column<" in svg and "$" not in svg


# ── the colour scale ─────────────────────────────────────────────────────────

Z = [[1.0, -2.0, None], [3.0, 6.0, 0.0], [-4.0, 2.0, 5.0]]
LOCKED = {"minutes": 60, "net": 40.0, "call": 70.0, "put": 30.0, "size": 100.0}


def test_the_scales_in_reading_order():
    assert list(gh.SCALES) == ["locked", "adaptive", "share"]


def test_robust_max_is_a_high_percentile_of_the_absolute_cell():
    assert gh.robust_max(Z) == 5.0            # 0.95 of [1,2,2,3,4,5,6] by index
    assert gh.robust_max(Z, q=1.0) == 6.0
    assert gh.robust_max([[None], [], [0.0]]) is None
    assert gh.robust_max(None) is None


def test_share_of_column_is_each_cell_over_its_columns_total_size():
    out = gh.share_of_column(Z)
    assert out[0] == [12.5, -20.0, None]
    assert out[1] == [37.5, 60.0, 0.0]
    assert out[2] == [-50.0, 20.0, 100.0]
    assert Z[0] == [1.0, -2.0, None]            # the input is untouched


def test_share_of_an_empty_column_is_a_gap():
    assert gh.share_of_column([[0.0], [None]]) == [[None], [None]]
    assert gh.share_of_column([]) == []


def test_scale_max_locked_uses_the_published_lock_for_the_value():
    assert gh.scale_max(Z, "locked", LOCKED, "size") == 100.0
    assert gh.scale_max(Z, "locked", LOCKED, "net") == 40.0


@pytest.mark.parametrize("lock", [None, {}, {"net": None}, {"net": 0}, {"net": "x"},
                                  {"net": float("nan")}, {"size": 9.0}])
def test_scale_max_falls_back_to_adaptive_without_a_lock(lock):
    assert gh.scale_max(Z, "locked", lock, "net") == gh.robust_max(Z)


def test_adaptive_and_share_never_use_the_lock():
    assert gh.scale_max(Z, "adaptive", LOCKED, "net") == gh.robust_max(Z)
    assert gh.scale_max(Z, "share", LOCKED, "net") == gh.robust_max(Z)


def test_scale_caption_says_what_the_scale_is_tied_to():
    assert gh.scale_caption("adaptive", LOCKED, "net", "09:30") == "adapts to what is visible"
    assert gh.scale_caption("share", LOCKED, "net", "09:30") == "share of each column"
    assert gh.scale_caption("locked", LOCKED, "net", "09:30") == "held since 09:30"
    assert gh.scale_caption("locked", None, "net", "09:30") == "settling until 09:30"


def test_a_lock_that_lacks_this_value_is_still_settling():
    """A session of bare-number cells locks net only."""
    lock = {"minutes": 60, "net": 4.0, "call": None, "put": None, "size": None}
    assert gh.scale_caption("locked", lock, "size", "09:30") == "settling until 09:30"


def test_no_lock_and_no_time_says_the_scale_adapts():
    """A payload that predates the lock, off-hours: nothing to wait for."""
    assert gh.scale_caption("locked", None, "net", "") == "adapts to what is visible"


# ── level or change ──────────────────────────────────────────────────────────

TS = [1000, 1060, 1120, 2800, 2860]


def test_the_shows_in_reading_order():
    assert list(gh.SHOWS) == ["level", "open", "window"]
    assert gh.show_labels(30) == {"level": "Level", "open": "Change since open",
                                  "window": "Change over 30 min"}
    assert gh.show_suffix("level", 30) == ""
    assert gh.show_suffix("open", 30) == "change since open"
    assert gh.show_suffix("window", 45) == "change over 45 min"


def test_change_since_open_subtracts_the_first_column():
    z = [[5.0, 7.0, 4.0, 9.0, 9.5]]
    assert gh.delta(z, TS) == [[0.0, 2.0, -1.0, 4.0, 4.5]]
    assert z == [[5.0, 7.0, 4.0, 9.0, 9.5]]            # the input is untouched


def test_change_over_a_window_uses_the_latest_column_at_least_that_old():
    """28 minutes before 2800 is 1120, the third column; before 2860 it is 1180,
    and the latest column at or before that is still the third."""
    out = gh.delta([[5.0, 7.0, 4.0, 9.0, 9.5]], TS, window_min=28)
    assert out == [[None, None, None, 5.0, 5.5]]


def test_a_column_with_nothing_old_enough_behind_it_is_a_gap():
    assert gh.delta([[1.0, 2.0, 4.0]], [0, 60, 120], window_min=1) == [[None, 1.0, 2.0]]
    assert gh.delta([[1.0, 2.0, 4.0]], [0, 60, 120], window_min=5) == [[None, None, None]]


def test_a_strike_absent_at_the_basis_is_a_gap_not_a_zero():
    """Zero would claim the exposure had not moved."""
    assert gh.delta([[None, 7.0], [3.0, None]], [1000, 1060]) == [[None, None], [0.0, None]]


def test_change_over_a_window_needs_real_times():
    """Rows labelled with clock text (old fixtures) cannot say how old they are."""
    assert gh.delta([[1.0, 2.0]], ["09:30", "09:31"], window_min=1) == [[None, None]]
    assert gh.delta([[1.0, 2.0]], ["09:30", "09:31"]) == [[0.0, 1.0]]     # since open is fine


def test_change_of_nothing():
    assert gh.delta([], []) == []
    assert gh.delta([[]], []) == [[]]


def _basis_rows():
    return [(1000, 100.0, None, None, None, 0, {100.0: {"net": 1.0}}),
            (1060, 100.0, None, None, None, 0, {100.0: {"net": 2.0}}),
            (2800, 100.0, None, None, None, 0, {100.0: {"net": 3.0}}),
            (2860, 100.0, None, None, None, 0, {100.0: {"net": 4.0}})]


def test_the_bars_basis_is_the_row_the_last_heatmap_column_is_measured_from():
    rows = _basis_rows()
    assert gh.basis_grid(rows, "level", 28) is None
    assert gh.basis_grid(rows, "open", 28) == {100.0: {"net": 1.0}}
    # 28 minutes before the last row (2860) is 1180: the latest row by then is 1060.
    assert gh.basis_grid(rows, "window", 28) == {100.0: {"net": 2.0}}
    # Nothing that old yet: no basis, so no bars rather than bars against nothing.
    assert gh.basis_grid(rows[:2], "window", 28) is None
    assert gh.basis_grid([], "open", 28) is None and gh.basis_grid(None, "window", 28) is None


def test_the_basis_matches_the_heatmaps_last_column():
    """The two panels must agree about what "30 minutes ago" was."""
    rows = _basis_rows()
    z = [[r[6][100.0]["net"] for r in rows]]
    last_change = gh.delta(z, [r[0] for r in rows], window_min=28)[0][-1]
    assert last_change == 4.0 - gh.basis_grid(rows, "window", 28)[100.0]["net"]


# ── the spot frame ───────────────────────────────────────────────────────────

def test_the_frames_in_reading_order():
    assert list(gh.FRAMES) == ["strike", "spot"]


def test_spot_frame_centres_each_column_on_its_own_spot():
    strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
    z = [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0], [4.0, 40.0], [5.0, 50.0]]
    offsets, out = gh.to_spot_frame(strikes, z, [100.0, 95.0], step=5.0, half=1)
    assert offsets == [-5.0, 0.0, 5.0]
    assert [row[0] for row in out] == [2.0, 3.0, 4.0]      # around 100
    assert [row[1] for row in out] == [10.0, 20.0, 30.0]   # around 95
    assert z[0] == [1.0, 10.0]                             # the input is untouched


def test_spot_frame_interpolates_between_strikes():
    _, out = gh.to_spot_frame([100.0, 105.0], [[10.0], [20.0]], [102.5], step=5.0, half=0)
    assert out == [[15.0]]


def test_spot_frame_never_extrapolates():
    """An offset with no strikes on one side of it is a gap."""
    _, out = gh.to_spot_frame([100.0, 105.0], [[10.0], [20.0]], [100.0], step=5.0, half=1)
    assert [row[0] for row in out] == [None, 10.0, 20.0]


def test_spot_frame_does_not_bridge_a_hole_in_the_ladder():
    _, out = gh.to_spot_frame([100.0, 105.0, 130.0], [[1.0], [2.0], [3.0]], [115.0],
                              step=5.0, half=0)
    assert out == [[None]]


def test_spot_frame_skips_cells_that_have_no_reading():
    """A strike with no value in this column is not a zero to interpolate from."""
    strikes = [95.0, 100.0, 105.0]
    _, out = gh.to_spot_frame(strikes, [[1.0], [None], [3.0]], [100.0], step=5.0, half=0)
    assert out == [[2.0]]          # bridged across one missing strike (two steps)


def test_spot_frame_of_a_column_with_no_spot_is_a_gap():
    _, out = gh.to_spot_frame([100.0, 105.0], [[1.0, 1.0], [2.0, 2.0]],
                              [None, 100.0], step=5.0, half=0)
    assert out == [[None, 1.0]]


def test_spot_frame_of_nothing():
    assert gh.to_spot_frame([], [], [], step=5.0, half=1) == ([-5.0, 0.0, 5.0], [[], [], []])
    assert gh.to_spot_frame([100.0], [[7.0]], [100.0], step=5.0, half=0) == ([0.0], [[7.0]])


def test_the_spot_frames_axis_holds_every_row_whole():
    """Half a row above the top offset and below the bottom one, as the cells
    are centred on their offsets."""
    assert gh.spot_frame_range(5.0, 10) == [-52.5, 52.5]
    assert gh.spot_frame_range(1.0, 0) == [-0.5, 0.5]


def test_bar_max_is_the_locks_extent_for_the_bars():
    assert gh.bar_max(LOCKED, "net") == 40.0
    assert gh.bar_max(LOCKED, "put") == 30.0
    # Size draws a call bar and a put bar, so its extent is the larger side's.
    assert gh.bar_max(LOCKED, "size") == 70.0
    assert gh.bar_max(None, "net") is None
    assert gh.bar_max({"net": 0}, "net") is None
    assert gh.bar_max({"call": None, "put": None}, "size") is None


# ── contour lines ────────────────────────────────────────────────────────────
# ``z[yi][xi]``: rows are strikes (``ys`` ascending), columns are minutes.

def _lines(points):
    """A flat series' polylines: split at the ``[x, None]`` breaks."""
    out, cur = [], []
    for x, y in points:
        if y is None:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append((x, y))
    if cur:
        out.append(cur)
    return out


def _contours(ys, z, zmax, **kw):
    kw = {"steps": 1, "min_columns": 0, "max_points": 10_000, **kw}
    return gh.contours(ys, z, zmax, **kw)


def test_contour_levels_halve_down_from_the_top_of_the_scale():
    assert gh.contour_levels(8.0, 3) == [2.0, 4.0, 8.0]
    assert gh.contour_levels(8.0, 1) == [8.0]
    for zmax, steps in ((None, 3), (0, 3), (-1.0, 3), (8.0, 0), (float("nan"), 2)):
        assert gh.contour_levels(zmax, steps) == []


def test_a_band_is_outlined_by_a_line_each_side_of_it():
    """A strike that holds 10 all session between two that hold 0: the level-5
    line runs halfway to each neighbour, and a straight run is two points."""
    z = [[0.0] * 4, [10.0] * 4, [0.0] * 4]
    got = _contours([100.0, 105.0, 110.0], z, 5.0)
    assert sorted(_lines(got["pos"])) == [[(0, 102.5), (3, 102.5)],
                                          [(0, 107.5), (3, 107.5)]]
    assert got["neg"] == []


def test_a_single_peak_is_a_closed_loop_around_it():
    z = [[0.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 0.0]]
    (loop,) = _lines(_contours([0.0, 1.0, 2.0], z, 5.0)["pos"])
    assert loop[0] == loop[-1]                       # closed
    assert set(loop) == {(0.5, 1.0), (1.0, 0.5), (1.5, 1.0), (1.0, 1.5)}


def test_negative_cells_are_drawn_in_their_own_series():
    z = [[0.0] * 3, [-10.0] * 3, [0.0] * 3]
    got = _contours([0.0, 1.0, 2.0], z, 5.0)
    assert got["pos"] == []
    assert sorted(_lines(got["neg"])) == [[(0, 0.5), (2, 0.5)], [(0, 1.5), (2, 1.5)]]


def test_the_crossing_is_interpolated_between_the_two_strikes():
    # Level 2 between a 0 and an 8 sits a quarter of the way up.
    z = [[0.0, 0.0], [8.0, 8.0]]
    (line,) = _lines(_contours([100.0, 104.0], z, 2.0)["pos"])
    assert line == [(0, 101.0), (1, 101.0)]


def test_every_step_draws_its_own_level():
    z = [[0.0, 0.0], [8.0, 8.0]]
    lines = _lines(_contours([0.0, 8.0], z, 8.0, steps=3)["pos"])
    # Levels 2, 4 and 8: the 8 line sits ON the top row.
    assert sorted(line[0][1] for line in lines) == [2.0, 4.0, 8.0]


def test_a_gap_in_the_grid_is_not_crossed():
    """The spot frame leaves None where a column has no strikes: no line may be
    interpolated through a cell that was never measured."""
    z = [[0.0, 0.0, None, 0.0, 0.0], [10.0, 10.0, None, 10.0, 10.0]]
    lines = sorted(_lines(_contours([0.0, 1.0], z, 5.0)["pos"]))
    assert lines == [[(0, 0.5), (1, 0.5)], [(3, 0.5), (4, 0.5)]]


def test_a_speck_narrower_than_the_floor_is_dropped():
    z = [[0.0] * 6, [0.0, 10.0, 0.0, 0.0, 0.0, 0.0], [0.0] * 6,
         [10.0] * 6, [0.0] * 6]
    ys = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert len(_lines(_contours(ys, z, 5.0)["pos"])) == 3            # loop + two lines
    kept = _lines(_contours(ys, z, 5.0, min_columns=3)["pos"])
    assert sorted(kept) == [[(0, 2.5), (5, 2.5)], [(0, 3.5), (5, 3.5)]]


def test_the_point_budget_keeps_the_longest_lines():
    z = [[0.0] * 9, [10.0] * 9, [0.0] * 9, [0.0] * 9,
         [0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 0.0, 0.0, 0.0], [0.0] * 9]
    ys = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    everything = _lines(_contours(ys, z, 5.0)["pos"])
    assert len(everything) == 3                      # two long lines and a loop
    kept = _lines(_contours(ys, z, 5.0, max_points=6)["pos"])
    assert sorted(kept) == [[(0, 0.5), (8, 0.5)], [(0, 1.5), (8, 1.5)]]


def test_a_gentle_wobble_is_kept_as_drawn():
    """Simplifying may drop points ON a straight run, never bend a line."""
    z = [[0.0, 0.0, 0.0, 0.0], [10.0, 20.0, 10.0, 20.0]]
    (line,) = _lines(_contours([0.0, 1.0], z, 5.0)["pos"])
    assert [x for x, _y in line] == [0, 1, 2, 3]
    assert [y for _x, y in line] == [0.5, 0.25, 0.5, 0.25]


def test_contours_of_nothing_are_nothing():
    for ys, z, zmax in (([], [], 5.0), ([0.0, 1.0], [[0.0], [10.0]], 5.0),
                        ([0.0], [[1.0, 2.0]], 1.0), ([0.0, 1.0], [[0.0, 0.0], [9.0, 9.0]], None)):
        assert _contours(ys, z, zmax) == {"pos": [], "neg": []}


def test_contours_do_not_write_to_the_grid():
    z = [[0.0, 0.0], [10.0, 10.0]]
    before = [list(r) for r in z]
    _contours([0.0, 1.0], z, 5.0)
    assert z == before
