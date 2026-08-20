"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, never blended: absolute trend (raw.trend) and relative strength
(raw.excess). See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
import ast
import difflib
import pathlib
import re
from decimal import Decimal

import pytest

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


# ── the display language: labels and the colour vocabulary ───────────────────
def test_the_tables_key_exactly_on_quadrants_with_no_empty_entries():
    """Set equality both ways, because the two directions of drift differ.

    A MISSING key renders "No reading" for that quadrant instead of raising, so
    nothing at runtime reports it — though two tests below happen to catch it as
    well. A STRAY key, left behind for a quadrant since renamed, is caught here
    alone: every other test iterates QUADRANTS, so none can see an entry that
    QUADRANTS no longer names.
    """
    for name, table in (("_LABELS", B._LABELS), ("_CLASSES", B._CLASSES)):
        assert set(table) == set(B.QUADRANTS), (
            f"{name} and QUADRANTS have drifted. Missing: "
            f"{sorted(set(B.QUADRANTS) - set(table))}; stray: "
            f"{sorted(set(table) - set(B.QUADRANTS))}.")
        for q, value in table.items():
            assert value, f"{name}[{q!r}] is empty, so that row renders nothing."


def test_quadrant_labels_say_both_axes():
    """"Bullish" alone is the ambiguity this page exists to remove, so every
    directional label names the absolute axis and then the relative one."""
    assert B.quadrant_label("rising_leading") == "Rising · Leading"
    assert B.quadrant_label("falling_leading") == "Falling · Leading"
    assert B.quadrant_label("unknown") == "No reading"
    # The literals pin the copy; the loop pins the convention over every member,
    # including the two that carry no literal assertion above.
    for q in B.QUADRANTS:
        if q == "unknown":
            continue
        trend, excess = q.split("_")
        label = B.quadrant_label(q)
        assert label.count(" · ") == 1, (
            f"{q} must name both axes as 'absolute · relative', got {label!r}")
        absolute, relative = label.split(" · ")
        assert absolute.lower() == trend, (
            f"{q}'s label leads with {absolute!r}, not its trend axis {trend!r}")
        assert relative.lower() == excess, (
            f"{q}'s label ends with {relative!r}, not its strength axis {excess!r}")


def test_the_class_palette_is_finite_deduped_and_neutral_for_no_reading():
    """Tailwind-first: a data-driven colour maps from a known finite set onto a
    static class. No two quadrants may share one, or the map collapses a distinction
    it exists to draw; and the no-reading style must borrow no direction colour,
    since grey reads as an absence where green or red reads as a call."""
    classes = {q: B.quadrant_class(q) for q in B.QUADRANTS}
    assert len(set(classes.values())) == len(B.QUADRANTS)
    unknown = B.quadrant_class("unknown")
    for hue in ("emerald", "amber", "rose"):
        assert hue not in unknown, f"the no-reading style borrows {hue}"


def test_the_lookups_degrade_rather_than_raise_on_an_impossible_key():
    """quadrant() is total, so no caller reaches this. It is pinned so it is not
    "tidied" into a KeyError, which would forfeit a whole page build to spare one
    row. The keys are derived to stay clear of the tables, so this cannot also
    fire on a stray entry — that case belongs to the set-equality test."""
    absent = "not_a_quadrant_" + "_".join(B.QUADRANTS)
    for bogus in (absent, None, 0, ("rising_leading",)):
        assert bogus not in B._LABELS and bogus not in B._CLASSES
        assert B.quadrant_label(bogus) == B.quadrant_label("unknown")
        assert B.quadrant_class(bogus) == B.quadrant_class("unknown")


# ── the headline: counts, not a verdict ──────────────────────────────────────
def _row(trend, excess):
    """Only ``raw`` is read, so a row is only ``raw`` — a fuller fixture would
    imply fields the counter consults."""
    return {"raw": {"trend": trend, "excess": excess}}


def test_quadrant_counts_bucket_every_row_exactly_once():
    rows = [_row(1.0, 0.1), _row(1.0, -0.1), _row(-1.0, 0.1),
            _row(-1.0, -0.1), _row(None, None)]
    counts = B.quadrant_counts(rows)
    assert counts == {"rising_leading": 1, "rising_lagging": 1,
                      "falling_leading": 1, "falling_lagging": 1, "unknown": 1}


def test_quadrant_counts_report_every_bucket_even_when_empty():
    """A missing key raises on the headline's numerator, and leaves every other
    caller guarding key by key. It cannot move the denominator — an absent bucket
    contributes nothing to sum() — so reporting all five is what makes the
    distribution readable without a guard per lookup."""
    counts = B.quadrant_counts([_row(1.0, 0.1)])
    assert set(counts) == set(B.QUADRANTS)
    assert counts["falling_lagging"] == 0


def test_quadrant_counts_of_nothing_is_all_zero():
    """A cold cache and a published-but-empty list are the two shapes of absence.
    They take the same path, so this is one claim asserted over both spellings."""
    assert B.quadrant_counts(None) == {q: 0 for q in B.QUADRANTS}
    assert B.quadrant_counts([]) == {q: 0 for q in B.QUADRANTS}


def test_raw_refuses_a_row_that_is_not_a_mapping():
    """Shape must be right, contents may be null. None in an array is routine
    JSON and a null ``raw`` is a row the cascade could not score; a string where
    an object belongs means a different document, and rendering part of it is
    guessing. Widening to isinstance(row, dict) would promise a totality nothing
    else at the container level promises."""
    assert B._raw(None) == {} and B._raw({"raw": None}) == {}
    with pytest.raises(AttributeError):
        B._raw("SPY")


def test_headline_is_empty_when_there_is_nothing_to_count():
    """"0 of 0 sectors rising and leading" reads as a maximally bearish tape
    where nothing was in fact published — the invented reading this module
    rejects everywhere else (NaN degrades to unknown; unknown takes slate).
    momentum_view:301 suppresses the same "N of M" shape. The page module owns
    the cold-cache state, so handing it an absence is what lets it."""
    assert B.headline(B.quadrant_counts([]), "sectors") == ""
    assert B.headline(B.quadrant_counts(None), "sectors") == ""


def test_quadrant_counts_treat_a_row_with_no_raw_block_as_unknown():
    """A row too thin to score arrives without ``raw``, or with None in it. That
    is a reading the cascade does not have, not a page build to abandon."""
    assert B.quadrant_counts([{}, {"raw": None}, None])["unknown"] == 3


def test_headline_states_a_count_not_a_regime_word():
    """Guard for the design decision. /sentiment/sectors and /sentiment/rotation
    already print contradictory risk-on/risk-off verdicts from incommensurable
    quantities; this page reports arithmetic instead."""
    text = B.headline(B.quadrant_counts([_row(1.0, 0.1), _row(1.0, 0.1),
                                         _row(-1.0, -0.1)]), "sectors")
    assert "2 of 3" in text and "sectors" in text
    for banned in ("risk-on", "risk-off", "bullish regime", "bearish regime"):
        assert banned not in text.lower()


def test_headline_raises_rather_than_reporting_a_confident_zero():
    """The asymmetry with quadrant_label/quadrant_class is deliberate, and this
    is what holds it. Those degrade because a bad key costs one row its chip;
    headline must not, because .get(..., 0) would print "0 of 11 sectors rising
    and leading" from a dict that never reported its rising bucket — a wrong
    number carrying the authority this page claims precisely because a count
    cannot be argued with. Tidying it to .get for symmetry fails here. The dict
    is sparse but populated: {} is now a legitimate empty payload."""
    with pytest.raises(KeyError):
        B.headline({"rising_lagging": 2, "falling_lagging": 1}, "sectors")


def test_headline_counts_unreadable_rows_in_its_denominator():
    """"1 of 3", not "1 of 2". The denominator is the rows on screen; dropping
    the unscored ones would inflate the fraction on exactly the thinnest days."""
    assert "1 of 3" in B.headline(B.quadrant_counts(
        [_row(1.0, 0.1), _row(None, None), _row(None, None)]), "sectors")
