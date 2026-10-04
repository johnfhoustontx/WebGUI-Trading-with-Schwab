"""The shared numeric helpers, and the ratchet on private copies (audit CQ-07).

Measured 2026-10-04 by cutting every private one-argument ``_num`` / ``_finite``
out of ``services/``, ``shared/`` and ``webgui/pages`` and running each over one
battery of inputs: 35 helpers, 15 different behaviours. Nine let NaN through;
eleven raised ``OverflowError`` on an int too large for a float.

``shared/numeric.py`` holds the behaviours by NAME, so a caller picks one on
purpose. Groups are moved onto it one at a time, each after its members were
shown to behave alike; never by search-and-replace.
"""
import ast
import decimal
import math
import pathlib

import pytest

from shared import numeric

REPO = pathlib.Path(__file__).resolve().parents[2]
HUGE = 10 ** 400                      # an int that float() cannot hold


# ---- finite: a number that already IS one ------------------------------------

@pytest.mark.parametrize("value, want", [
    (0, 0.0), (1, 1.0), (-1.5, -1.5), (2.5e-9, 2.5e-9),
])
def test_finite_returns_a_real_number_as_a_float(value, want):
    got = numeric.finite(value)
    assert got == want and isinstance(got, float)


@pytest.mark.parametrize("value", [
    None, True, False, "3", " 2.5 ", "x", "", float("nan"), float("inf"),
    float("-inf"), [1], {}, (1,), decimal.Decimal("1.5"), HUGE, -HUGE, b"1",
    complex(1, 0),
])
def test_finite_is_none_for_everything_else(value):
    # A string is not a number here, however numeric it looks: this is the
    # helper for values that should already BE numbers.
    assert numeric.finite(value) is None


# ---- parsed_finite: a number, or text that spells one ------------------------

@pytest.mark.parametrize("value, want", [
    (0, 0.0), (1, 1.0), (-1.5, -1.5), ("3", 3.0), (" 2.5 ", 2.5), ("-1e3", -1000.0),
    (decimal.Decimal("1.5"), 1.5),
])
def test_parsed_finite_reads_numbers_and_numeric_text(value, want):
    got = numeric.parsed_finite(value)
    assert got == want and isinstance(got, float)


@pytest.mark.parametrize("value", [
    None, True, False, "x", "", "nan", "inf", "-inf", float("nan"), float("inf"),
    [1], {}, (1,), HUGE, complex(1, 0), b"x",
])
def test_parsed_finite_is_none_for_everything_else(value):
    # Text that spells nan or inf parses to one, and is refused like one.
    assert numeric.parsed_finite(value) is None


def test_the_two_differ_only_where_text_or_another_number_type_is_parsed():
    for value in ("3", " 2.5 ", decimal.Decimal("1.5")):
        assert numeric.finite(value) is None
        assert numeric.parsed_finite(value) is not None
    for value in (0, 1, -1.5, None, True, float("nan"), float("inf"), HUGE, [1]):
        a, b = numeric.finite(value), numeric.parsed_finite(value)
        assert a == b


def test_neither_ever_raises_and_neither_returns_a_number_that_is_not_finite():
    class Odd:
        def __float__(self):
            raise RuntimeError("no")

    for value in (Odd(), object(), HUGE, "1e999", float("nan"), b"\xff", range(3)):
        for fn in (numeric.finite, numeric.parsed_finite):
            got = fn(value)
            assert got is None or math.isfinite(got)


def test_the_module_imports_only_the_standard_library():
    tree = ast.parse((REPO / "shared" / "numeric.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= {"math", "__future__"}, names


# ---- the ratchet: private copies can only go away ----------------------------

# Every file that still defines its own one-argument ``_num`` or ``_finite``,
# with how many. Moving a group onto ``shared.numeric`` REMOVES entries. Adding
# one, or a new file, fails: pick a named helper instead.
#
# What is left, by behaviour (the 2026-10-04 measurement):
#   * webgui/pages (Tier 1 cannot import ``shared.numeric`` until it is on the
#     allow-list; ``pages.fmt`` is its shared home) - 9 helpers.
#   * shared modules whose import set is pinned to ``math`` alone
#     (book_caps, calibration, structures, vol_gate) - 4.
#   * helpers that pass NaN or infinity through, or map a missing value to 0.
#     Each needs its callers read before it moves, because moving it changes
#     what they receive - 14.
PRIVATE_HELPERS = {
    "services/market_svc/classify.py": 1,
    "services/news_svc/impact.py": 1,
    "services/options_svc/book_perf.py": 1,
    "services/options_svc/captured_score.py": 1,
    "services/options_svc/compute.py": 2,
    "services/options_svc/flow_alerts.py": 1,
    "services/options_svc/market_snapshot.py": 1,
    "services/options_svc/net_premium.py": 1,
    "services/options_svc/perf_analytics.py": 1,
    "services/options_svc/rescue.py": 1,
    "services/trade_svc/dealer_context.py": 1,
    "services/trade_svc/live_ic.py": 1,
    "services/trade_svc/model_book.py": 1,
    "services/trade_svc/trade_plan.py": 1,
    "shared/book_caps.py": 1,
    "shared/calibration.py": 1,
    "shared/public_rescue.py": 1,
    "shared/structures.py": 1,
    "shared/vol_gate.py": 1,
    "webgui/pages/eod.py": 1,
    "webgui/pages/news_view.py": 1,
    "webgui/pages/options/calc_live.py": 1,
    "webgui/pages/options/chain_grid.py": 1,
    "webgui/pages/options/flow_panels.py": 1,
    "webgui/pages/options/paper.py": 1,
    "webgui/pages/options/pub_chain_view.py": 1,
    "webgui/pages/options/rescue.py": 1,
    "webgui/pages/options/strategy_table.py": 1,
    "webgui/pages/sentiment_momentum.py": 1,
}


def _private_helpers():
    """``{file: count}`` of one-required-argument ``def _num`` / ``def _finite``
    in the three trees, tests excluded."""
    found = {}
    for root in ("services", "shared", "webgui/pages"):
        for path in sorted((REPO / root).rglob("*.py")):
            if "tests" in path.relative_to(REPO).parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            n = 0
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef)
                        and node.name in ("_num", "_finite")):
                    required = len(node.args.args) - len(node.args.defaults)
                    if required == 1 and not any(
                            d is None for d in node.args.kw_defaults):
                        n += 1
            if n:
                found[path.relative_to(REPO).as_posix()] = n
    return found


def test_no_new_private_numeric_helper_has_appeared():
    found = _private_helpers()
    extra = {f: n for f, n in found.items() if n > PRIVATE_HELPERS.get(f, 0)}
    assert not extra, (
        f"new private _num/_finite: {extra}. Use shared.numeric.finite or "
        "parsed_finite (pages.fmt in the web tier) instead of a new copy.")


def test_the_list_is_lowered_when_a_helper_goes():
    found = _private_helpers()
    stale = {f: n for f, n in PRIVATE_HELPERS.items() if found.get(f, 0) < n}
    assert not stale, f"these were moved; lower PRIVATE_HELPERS: {stale}"


def _bound_to(path, private_name):
    """Which ``shared.numeric`` function ``private_name`` is imported as in
    ``path``, or None. Read from the SOURCE: importing a service here would put
    its engine folders on this suite's path."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "shared.numeric":
            for alias in node.names:
                if (alias.asname or alias.name) == private_name:
                    return alias.name
    return None


@pytest.mark.parametrize("path, private_name, shared_name", [
    ("services/options_svc/compute.py", "_finite", "finite"),
    ("services/options_svc/hiro.py", "_finite", "finite"),
    ("services/options_svc/rate_trade.py", "_finite", "finite"),
    ("services/options_svc/tools_public.py", "_finite", "finite"),
    ("services/news_svc/econ.py", "_finite", "finite"),
    ("services/options_svc/card_kit.py", "_finite", "parsed_finite"),
    ("services/options_svc/site_ideas.py", "_finite", "parsed_finite"),
    ("services/options_svc/trade_idea.py", "_num", "parsed_finite"),
])
def test_the_first_two_groups_are_on_the_shared_helpers(path, private_name,
                                                        shared_name):
    """The strict group and the text-parsing group in ``services``: each module
    keeps its private NAME (its callers and tests use it) bound to the shared
    function, so there is one definition."""
    assert _bound_to(path, private_name) == shared_name
