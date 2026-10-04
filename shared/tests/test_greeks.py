"""One answer to "is this Greek a reading?" for every tier that reads a chain.

Audit AC-09: Schwab sends ``-999.0`` for a Greek it could not compute. Three
sites took it as a number. One contract produced a -1.8e12 exposure cell, a
delta stop fired on a position that had not moved, and the book's net delta read
-998.7. AC-52: a NaN delta made every later comparison False and silenced every
big-delta alert for the symbol.
"""
import math

import pytest

from shared import greeks


@pytest.mark.parametrize("value", [-999.0, -999, float("nan"), float("inf"),
                                   float("-inf"), None, "0.3", True, False, [], {}])
def test_an_unusable_value_is_refused(value):
    assert greeks.usable(value) is None


@pytest.mark.parametrize("value", [0.0, 0.31, -0.42, 12.5, -3.0, 1])
def test_a_real_number_passes_as_a_float(value):
    got = greeks.usable(value)
    assert got == float(value) and isinstance(got, float)


@pytest.mark.parametrize("value,expected", [
    (0.31, 0.31), (-0.42, -0.42), (1.0, 1.0), (-1.0, -1.0), (0.0, 0.0),
    (1.5, None), (-1.2, None), (-999.0, None), (float("nan"), None), (None, None),
])
def test_a_delta_is_a_fraction_between_minus_one_and_one(value, expected):
    assert greeks.delta(value) == expected


@pytest.mark.parametrize("value,expected", [
    (0.012, 0.012), (0.0, 0.0), (-0.001, None), (-999.0, None),
    (float("nan"), None), (None, None),
])
def test_a_gamma_is_never_negative(value, expected):
    assert greeks.gamma(value) == expected


def test_zero_is_a_reading_not_an_absence():
    """A balanced condor's delta is 0.0; ``x or default`` would lose it."""
    assert greeks.delta(0.0) == 0.0 and greeks.delta(0.0) is not None


def test_the_module_imports_only_math():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(greeks.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names == {"math"}
    assert math.isfinite(greeks.SENTINEL)
