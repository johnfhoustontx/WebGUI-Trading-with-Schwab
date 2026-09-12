"""D4: the share-leg kind name is spelled in TWO tiers, so it is pinned.

``webgui/pages/options/strategies.py`` declares ``STOCK`` and
``options-scanner/options_calculator.py`` declares ``STOCK_KIND``. They must be
the same string: Tier 1 takes no engine import (the allow-list), so the leg model
cannot read the pricer's constant, and the pricer cannot read the model's.

⚠ **A disagreement here fails SILENTLY and expensively.** ``leg_value`` matches
the kind name exactly, so if the model emitted ``"equity"`` while the pricer
looked for ``"stock"``, every share leg would fall through to ``bs_price`` with
``strike=None`` — a raise on the Calculator's every keystroke — and if it were
the other way, a share leg would be *dropped* from the readiness check while
still being priced as an option. This is the same class as the five regime
display words in ``shared/tests/test_cross_tier_mirrors.py``, which exists for
exactly this reason.

Read by AST rather than imported: ``options_calculator`` lives in a hyphenated
app folder that Tier-1 tests do not put on ``sys.path``, and importing it here
would be the very thing the allow-list forbids.
"""
import ast
import pathlib

from pages.options import strategies as S

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PRICER = _ROOT / "options-scanner" / "options_calculator.py"


def _module_constant(path, name):
    """The literal value assigned to a module-level ``name``, by AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at module level in {path.name}")


def test_the_pricer_declares_a_stock_kind():
    assert _module_constant(_PRICER, "STOCK_KIND") == "stock"


def test_the_leg_model_and_the_pricer_agree_on_the_kind_name():
    """The mirror. One string, two tiers that cannot import each other."""
    assert S.STOCK == _module_constant(_PRICER, "STOCK_KIND")


def test_the_pricer_exposes_the_two_functions_the_model_assumes():
    """``is_stock_leg`` and ``leg_value`` are the contract: the model produces
    legs the pricer must recognise and value. A rename on either side would
    otherwise surface as a raise on the Calculator's next keystroke."""
    tree = ast.parse(_PRICER.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"is_stock_leg", "leg_value", "has_stock_leg"} <= names


def test_both_predicates_match_the_kind_EXACTLY():
    """⚠ Neither side may match by prefix. ``"stocks"`` must read as an OPTION
    and fail on its missing strike rather than silently pricing as a share — and
    a prefix match on one side only would make the two tiers disagree about the
    same leg."""
    assert S.is_stock_leg({"option_type": "stocks"}) is False
    assert S.is_stock_leg({"option_type": "stock"}) is True
    src = _PRICER.read_text(encoding="utf-8")
    assert "startswith" not in src.split("def is_stock_leg")[1].split("def ")[0]
