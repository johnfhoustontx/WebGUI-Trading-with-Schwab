"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, never blended: absolute trend (raw.trend) and relative strength
(raw.excess). See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
import ast
import difflib
import pathlib
import re
from decimal import Decimal

from pages import bullbear as B


def test_quadrant_names_the_four_states():
    assert B.quadrant(0.5, 0.1) == "rising_leading"
    assert B.quadrant(0.5, -0.1) == "rising_lagging"
    assert B.quadrant(-0.5, 0.1) == "falling_leading"
    assert B.quadrant(-0.5, -0.1) == "falling_lagging"


def test_quadrant_treats_exact_zero_as_the_bearish_side():
    """A flat trend is not a rising one. Ties go to the cautious reading, so a
    dead-flat row never renders as strength."""
    assert B.quadrant(0.0, 0.1) == "falling_leading"
    assert B.quadrant(0.5, 0.0) == "rising_lagging"


def test_quadrant_is_unknown_when_either_axis_is_missing():
    """A thin or newly-listed symbol scores None. It must not default into a
    bucket — an invented reading is worse than an absent one."""
    assert B.quadrant(None, 0.1) == "unknown"
    assert B.quadrant(0.5, None) == "unknown"
    assert B.quadrant(None, None) == "unknown"


def test_quadrant_is_unknown_when_either_axis_is_non_finite():
    """A NaN means what None means — the cascade produced no usable score. The
    asymmetry is why this needs its own guard: forget None and the comparison
    raises TypeError, but every comparison against NaN silently returns False,
    so an unguarded NaN trend falls through to the falling branch and paints a
    confident bearish row. That is the shape of the bug CLAUDE.md records
    shipping twice in sentiment_svc, where a NaN reaching min(hi, nan) returned
    hi and a data outage rendered as a maximum reading."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert B.quadrant(bad, 0.1) == "unknown"
        assert B.quadrant(0.5, bad) == "unknown"
        assert B.quadrant(bad, bad) == "unknown"


def test_quadrant_rejects_booleans_even_though_bool_is_an_int():
    """bool subclasses int, so True passes every numeric guard — float(True) is
    1.0 and True > 0 is True — and would render as a rising trend. A boolean in
    a regression-slope field is a malformed payload, not a reading."""
    assert B.quadrant(True, True) == "unknown"
    assert B.quadrant(False, 0.1) == "unknown"
    assert B.quadrant(0.5, True) == "unknown"


def test_quadrant_coerces_a_numeric_string_rather_than_discarding_it():
    """_num is shared verbatim with sector_heat/rotation_view/rrg_view/
    momentum_view, which read these same payload fields, so it coerces what
    float() can read. The cascade emits floats, so this is unreachable today —
    it is pinned because two notions of "is this a reading" between adjacent
    screens is how they end up disagreeing about identical data."""
    assert B.quadrant("0.5", 0.1) == "rising_leading"
    assert B.quadrant(0.5, "-0.1") == "rising_lagging"
    assert B.quadrant(Decimal("0.5"), Decimal("0.1")) == "rising_leading"


def test_quadrant_is_unknown_for_a_value_that_is_not_a_number():
    """A malformed payload must not raise inside a page build. Degrading to
    unknown is honest rather than masking: it renders the absence instead of
    inventing a plausible number, which is the distinction that matters.
    Decimal("sNaN") is the one that bites — float() raises ValueError on it
    rather than returning nan, so a TypeError-only guard lets it through."""
    assert B.quadrant("abc", 0.1) == "unknown"
    assert B.quadrant(0.5, "abc") == "unknown"
    assert B.quadrant({}, []) == "unknown"
    assert B.quadrant(Decimal("sNaN"), 0.1) == "unknown"
    assert B.quadrant(0.5, Decimal("NaN")) == "unknown"


def test_quadrant_only_ever_returns_a_member_of_quadrants():
    """QUADRANTS is the vocabulary the labels and the Tailwind class palette
    will be keyed by, so the tuple and the function must not drift apart."""
    # Membership alone would pass an implementation that returned "unknown" for
    # everything; the tests above are what pin each bucket as reachable.
    assert len(set(B.QUADRANTS)) == len(B.QUADRANTS)
    values = (0.5, -0.5, 0.0, -0.0, 1, None, float("nan"), float("inf"),
              float("-inf"), True, False, "x", "0.5", Decimal("sNaN"), [])
    for trend in values:
        for excess in values:
            assert B.quadrant(trend, excess) in B.QUADRANTS


# ── the claim that bullbear._num is the house helper ─────────────────────────
# ``bullbear._num``'s docstring asserts in prose that its body is byte-identical
# to four siblings. Nothing enforces prose, and this repo has already paid for
# that: the 2026-07-01 audit closed "single-source r" as FIXED and
# test_expiry_time_rate_consistency documented "a single RISK_FREE_RATE source
# of truth" while gamma_tool still carried five 0.045 literals and
# backtest_0dte its own RISK_FREE = 0.04 — two artefacts asserting a property
# neither checked, and the divergence survived seven weeks under a green suite.
_PAGES = pathlib.Path(B.__file__).resolve().parent


def _num_ast(module_name):
    """The ``_num`` function node in ``pages/<module_name>.py``."""
    path = _PAGES / f"{module_name}.py"
    assert path.exists(), (
        f"{path} does not exist, so bullbear._num's docstring names a module "
        "that is gone. Update the docstring and this guard together."
    )
    found = [n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(n, ast.FunctionDef) and n.name == "_num"]
    assert len(found) == 1, (
        f"expected exactly one _num in {path.name}, found {len(found)}. If it "
        "was renamed or removed, bullbear._num's docstring still claims to "
        "match it — update both."
    )
    return found[0]


def _num_body(module_name):
    """``_num``'s statements as normalised source, with the docstring stripped.

    Docstrings legitimately differ — rrg_view and momentum_view carry none at
    all, and bullbear deliberately keeps a fuller rationale — so only the
    executable body is compared. ``ast.unparse`` normalises formatting, which
    makes this a digest in every respect that matters while staying printable
    in a failure message; a hexdigest could only say "different".
    """
    body = _num_ast(module_name).body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    assert body, f"{module_name}._num has no body beyond its docstring."
    return [ast.unparse(stmt) for stmt in body]


def _siblings_named_in_the_docstring():
    """The modules bullbear._num's own docstring claims to be identical to.

    Read out of the prose instead of hardcoded here, so that editing the claim
    without editing the guard fails loudly rather than leaving this test
    checking a stale list — which is the failure mode it exists to prevent.
    """
    doc = ast.get_docstring(_num_ast("bullbear")) or ""
    m = re.search(r"Byte-identical to the ``_num`` in(.+?), the sibling modules",
                  doc, re.S)
    assert m, (
        "bullbear._num's docstring no longer makes its byte-identical claim in "
        "the form this guard parses. If the wording changed, update the regex; "
        "if the claim is gone, delete it and this test together."
    )
    return tuple(re.findall(r"``(\w+)``", m.group(1)))


def test_num_body_is_identical_to_every_sibling_its_docstring_names():
    """The prose claim inside bullbear._num, actually enforced.

    A shared helper that drifts is worse than four honest copies: the Bull/Bear
    map and the Sector Heat grid read the same payload fields, so two notions of
    "is this a reading" is how adjacent screens come to disagree about identical
    data — and the docstring would still be telling the next reader they cannot.
    """
    siblings = _siblings_named_in_the_docstring()
    # Not a vacuous pass: the claim names four modules, and a typo or a rename
    # must fail here rather than silently comparing against nothing.
    assert siblings == ("sector_heat", "rotation_view", "rrg_view",
                        "momentum_view"), (
        f"bullbear._num's docstring now names {siblings}. That is a change to "
        "the claim itself — confirm the new list is right, then update this "
        "expectation."
    )
    mine = _num_body("bullbear")
    for name in siblings:
        theirs = _num_body(name)
        assert mine == theirs, (
            f"bullbear._num has diverged from {name}._num, which its docstring "
            "claims to be byte-identical to. Either restore the shared body, "
            "or — if the divergence is deliberate — amend that docstring so it "
            "stops claiming an identity that no longer holds.\n"
            + "\n".join(difflib.unified_diff(
                theirs, mine, fromfile=f"{name}._num", tofile="bullbear._num",
                lineterm="")))
