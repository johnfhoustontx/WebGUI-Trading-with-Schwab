"""The Dealer Positioning heatmap's pure transforms (value, scale, frame).

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
import ast
import pathlib

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
