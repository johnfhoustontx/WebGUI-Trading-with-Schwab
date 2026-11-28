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


# ---- positive_finite: finite and above zero ----------------------------------

@pytest.mark.parametrize("value, want", [(1, 1.0), (2.5e-9, 2.5e-9), (450.25, 450.25)])
def test_positive_finite_returns_a_positive_number_as_a_float(value, want):
    got = numeric.positive_finite(value)
    assert got == want and isinstance(got, float)


@pytest.mark.parametrize("value", [
    0, 0.0, -0.0, -1, -1.5, None, True, False, "3", float("nan"), float("inf"),
    float("-inf"), HUGE, decimal.Decimal("1.5"), [1],
])
def test_positive_finite_is_none_for_zero_negatives_and_everything_finite_refuses(value):
    # Zero is refused: where this helper is used, a 0 is a missing price.
    assert numeric.positive_finite(value) is None


def test_positive_finite_is_finite_restricted_to_above_zero():
    for value in (0, 1, -1.5, 2.5e-9, None, True, float("nan"), HUGE, "3", [1]):
        f = numeric.finite(value)
        assert numeric.positive_finite(value) == (f if f is not None and f > 0 else None)


def test_none_of_them_ever_raises_or_returns_a_number_that_is_not_finite():
    class Odd:
        def __float__(self):
            raise RuntimeError("no")

    for value in (Odd(), object(), HUGE, "1e999", float("nan"), b"\xff", range(3)):
        for fn in (numeric.finite, numeric.parsed_finite, numeric.positive_finite):
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
#   * webgui/pages: ``shared.numeric`` joined the Tier-1 allow-list on
#     2026-11-28 and the four exact copies moved; those left differ in
#     behaviour (NaN or a bool passes, or a default argument).
#   * shared modules whose import set is pinned to ``math`` alone
#     (book_caps, calibration, structures, vol_gate) - 4.
#   * helpers that pass NaN or infinity through, or map a missing value to 0.
#     Each needs its callers read before it moves, because moving it changes
#     what they receive - 14.
PRIVATE_HELPERS = {
    "services/market_svc/classify.py": 1,
    "services/news_svc/impact.py": 1,
    "services/options_svc/captured_score.py": 1,
    "services/options_svc/compute.py": 1,
    "services/options_svc/flow_alerts.py": 1,
    "services/options_svc/net_premium.py": 1,
    "shared/book_caps.py": 1,
    "shared/calibration.py": 1,
    "shared/public_rescue.py": 1,
    "shared/structures.py": 1,
    "shared/vol_gate.py": 1,
    "webgui/pages/eod.py": 1,
    "webgui/pages/options/rescue.py": 1,
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
    # 2026-11-28: helpers under other names, each run over one battery of
    # inputs and found to match the shared function on every one except an
    # int too large for a float and an object whose ``float()`` raises, where
    # the private copy raised.
    ("options-scanner/signal_recommender.py", "_finite", "finite"),
    ("options-scanner/signal_recorder.py", "_real", "finite"),
    ("options-scanner/structure_marks.py", "_real", "finite"),
    ("services/news_svc/store.py", "_obs_value", "finite"),
    ("services/options_svc/dossier.py", "_finite_or_none", "finite"),
    ("services/options_svc/dossier.py", "_usable_price", "positive_finite"),
    ("services/options_svc/compute.py", "_usable_spot", "positive_finite"),
    ("services/options_svc/tracked.py", "_real", "finite"),
    ("shared/idea_review.py", "_real", "finite"),
    ("tools/hiro_report.py", "_finite", "finite"),
    # The web tier, once shared.numeric joined its allow-list (same battery).
    ("webgui/pages/news_view.py", "_finite", "finite"),
    ("webgui/pages/options/chain_grid.py", "_finite", "finite"),
    ("webgui/pages/options/pub_chain_view.py", "_finite", "finite"),
    ("webgui/pages/options/flow_panels.py", "_num", "finite"),
    ("webgui/pages/options/leg_editor.py", "_usable_price", "finite"),
    # 2026-11-28, group 1: bare float() helpers that let NaN, an infinity and
    # a bool through on the money paths. Not equivalent moves: each was
    # measured to misread a bad input (a NaN strike read CRITICAL in Rescue,
    # "$nan" on Captured), and now takes the missing-value branch. Pinned by
    # services/options_svc/tests/test_rescue_bad_numbers.py and
    # webgui/tests/test_bad_numbers_money_pages.py.
    ("services/options_svc/rescue.py", "_num", "parsed_finite"),
    ("webgui/pages/options/paper.py", "_num", "parsed_finite"),
    ("webgui/pages/options/captured.py", "_float", "parsed_finite"),
    ("webgui/pages/options/rescue.py", "_parsed_finite", "parsed_finite"),
    ("services/sentiment_svc/momentum_db.py", "_f", "parsed_finite"),
    ("schwab-proxy/schwab_proxy.py", "_parsed_finite", "parsed_finite"),
    # 2026-11-28, group 2: type-check-only helpers that returned NaN, an
    # infinity (and, in the services, a bool) unchanged. Measured to put "nan"
    # in the scorecard and the excursions, and to make build_gamma_read raise.
    # Pinned by services/options_svc/tests/test_perf_bad_numbers.py and
    # webgui/tests/test_strategy_table_bad_numbers.py.
    ("services/options_svc/book_perf.py", "_num", "finite"),
    ("services/options_svc/perf_analytics.py", "_num", "finite"),
    ("webgui/pages/options/strategy_table.py", "_num", "finite"),
    # 2026-11-28, group 3: text-parsing helpers that refused NaN but passed an
    # infinity. Measured to open and close model-book positions on an infinite
    # price and to crash trade_plan.build. Pinned by
    # services/trade_svc/tests/test_bad_numbers.py and test_classify.py.
    # (calc_live binds pages.fmt.num instead: test_fmt.py pins it.)
    ("services/trade_svc/dealer_context.py", "_num", "parsed_finite"),
    ("services/trade_svc/live_ic.py", "_num", "parsed_finite"),
    ("services/trade_svc/model_book.py", "_num", "parsed_finite"),
    ("services/trade_svc/trade_plan.py", "_num", "parsed_finite"),
    ("services/options_svc/market_snapshot.py", "_num", "parsed_finite"),
    ("services/market_svc/classify.py", "_num_or_none", "parsed_finite"),
])
def test_the_first_two_groups_are_on_the_shared_helpers(path, private_name,
                                                        shared_name):
    """The strict group and the text-parsing group in ``services``: each module
    keeps its private NAME (its callers and tests use it) bound to the shared
    function, so there is one definition."""
    assert _bound_to(path, private_name) == shared_name
