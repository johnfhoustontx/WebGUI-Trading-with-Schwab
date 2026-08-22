"""`_clamp` and `_num` were copy-pasted across the scoring package.

Measured 2026-08-21 by AST (docstrings stripped): **`_clamp` had NINE
byte-identical definitions** and **`_num` six**, plus a seventh in
`market_regime` written differently but behaving the same. That is the "patch one
of nine" trap - and this package is precisely where the NaN-guard bug class keeps
recurring, so the odds of a partial fix are not theoretical.

⚠ Consolidating the PRIMITIVE is not the thing root CLAUDE.md warns against.
That warning is about changing `_clamp`'s NaN SEMANTICS centrally - a NaN
reaching `_clamp(50 + 50*direction, 0, 100)` means "neutral 50", reaching
`_clamp(adx/40, 0.3, 1.0)` means "floor the magnitude", and reaching
`_clamp(n/3, 0, 1)` means "confidence 0". Only the caller knows. The body here is
byte-identical to the nine it replaces; the NaN policy stays at the call sites.
"""
import math

import pytest

from scoring._common import clamp, num


class TestClamp:
    def test_bounds(self):
        assert clamp(5, 0, 10) == 5.0
        assert clamp(-1, 0, 10) == 0.0
        assert clamp(99, 0, 10) == 10.0

    def test_always_returns_a_float(self):
        assert isinstance(clamp(5, 0, 10), float)

    def test_nan_still_pins_the_HIGH_bound(self):
        """Deliberately unchanged. min(hi, nan) is hi, and the nine copies all
        did this. Fixing it here would silently change three different intended
        meanings at once - the guard belongs at the call site."""
        assert clamp(float("nan"), 0, 100) == 100.0


class TestNum:
    def test_parses(self):
        assert num("3.5") == 3.5
        assert num(7) == 7.0

    def test_missing_is_none(self):
        assert num(None) is None
        assert num("abc") is None
        assert num([]) is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_is_missing_not_a_reading(self, bad):
        assert num(bad) is None

    def test_a_finite_float_survives(self):
        assert num(-0.0) == 0.0 and math.isfinite(num(1e300))


def _scoring_modules():
    import pathlib
    d = pathlib.Path(__file__).resolve().parents[1] / "scoring"
    return sorted(p for p in d.glob("*.py") if p.name != "_common.py")


def test_no_module_redefines_the_shared_primitives():
    """The point of the exercise: nine copies must not become ten."""
    import ast

    offenders = []
    for path in _scoring_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ("_clamp", "_num"):
                offenders.append(f"{path.name}:{node.lineno} def {node.name}")
    assert not offenders, (
        "these redefine a primitive that lives in scoring/_common.py - import "
        "`clamp`/`num` from there instead:\n  " + "\n  ".join(offenders))


def test_finite_is_deliberately_NOT_consolidated():
    """Three functions share the name `_finite` and only two share a contract:
    `regime_evidence._finite` and `intraday_trend._finite` take a scalar, while
    `momentum_regime._finite` takes an ITERABLE and returns a filtered list.
    Hoisting that name into _common.py would hand someone the wrong one silently,
    so they stay put - and this test records why, so a later tidy-up does not
    'finish the job'.
    """
    import ast
    import pathlib

    d = pathlib.Path(__file__).resolve().parents[1] / "scoring"
    arities = {}
    for name in ("regime_evidence", "momentum_regime", "intraday_trend"):
        tree = ast.parse((d / f"{name}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_finite":
                arities[name] = node.args.args[0].arg
    assert arities["momentum_regime"] == "values", \
        "momentum_regime._finite takes an iterable - a different contract"
    assert arities["regime_evidence"] in ("v", "x")
    assert arities["intraday_trend"] in ("v", "x")
