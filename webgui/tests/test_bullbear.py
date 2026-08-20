"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, never blended: absolute trend (raw.trend) and relative strength
(raw.excess). Participation is a third, independent dimension drawn beside the
quadrant rather than folded into it.
See docs/plans/2026-08-19-bull-bear-map-design.md.
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


def test_signed_pct_signs_a_move_but_leaves_a_flat_tape_unsigned():
    """"+0.00%" claims a rise the digits deny, and the sign is the only part of
    the string a reader takes at a glance — so it is decided on the ROUNDED
    value. -0.0 and a move too small to show are both real: the first arrives
    from float arithmetic, the second whenever a quote ticks a hundredth. Two
    decimals because merge_live passes netPercentChange through (-0.5578)."""
    assert B.signed_pct(1.25) == "+1.25%"
    assert B.signed_pct(-0.5578) == "-0.56%"
    assert B.signed_pct(0) == "0.00%" and B.signed_pct(-0.0) == "0.00%"
    assert B.signed_pct(-0.004) == "0.00%" and B.signed_pct(0.004) == "0.00%"


def test_signed_pct_shows_a_dash_when_there_is_no_quote():
    """merge_live leaves day_pct None on a missing quote and the dash is what
    holds that apart from a flat tape — the same distinction breadth_width draws
    between None and 0. Through _num, so a bool that would otherwise print
    "+1.00%" out of a malformed payload is refused here too."""
    assert B.signed_pct(None) == B.NO_READING
    assert B.signed_pct("x") == B.NO_READING
    assert B.signed_pct(True) == B.NO_READING
    assert B.signed_pct(float("nan")) == B.NO_READING


# ── the headline: counts, not a verdict ──────────────────────────────────────
def _row(trend, excess, **fields):
    """``raw``, plus whatever top-level fields the case under test is about.

    The counter reads only ``raw``. The tree also reads ``label``/``sector``/
    ``industry``, which are SIBLINGS of ``raw`` rather than entries in it
    (services/sentiment_svc/compute.py, ``_momentum_score_level``).
    """
    return {"raw": {"trend": trend, "excess": excess}, **fields}


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


def test_row_axes_reads_both_axes_through_the_same_policy_as_quadrant():
    """The pair Tasks 9 and 11 need — the map prints both numbers per row, the
    Desk strip buckets each sector. Hand-rolled at either call site it drifts
    from _raw: a numeric string would print uncoerced, and a row with no raw
    block would raise where the counter treats it as a reading we lack."""
    assert B.row_axes({"raw": {"trend": "0.5", "excess": -0.1}}) == (0.5, -0.1)
    assert B.row_axes({}) == (None, None) and B.row_axes(None) == (None, None)
    assert B.quadrant(*B.row_axes(_row(1.0, 0.1))) == "rising_leading"


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


# ── participation: the breadth bar ───────────────────────────────────────────
def test_breadth_width_is_a_percentage_of_the_track():
    """A whole percent, so the caller sets one Tailwind width and no CSS has to
    round it. Zero is a width, not an absence — a sector nothing confirms owns
    the empty bar. The type is pinned because == cannot see it: 50.0 == 50, so a
    tidy to round(p * 100, 0) stays green on every line below while the page
    emits w-[50.0%], for which the bundled Tailwind JIT generates no rule."""
    assert B.breadth_width(0.0) == 0
    assert B.breadth_width(0.5) == 50
    assert B.breadth_width(1.0) == 100
    assert B.breadth_width(0.9623) == 96
    assert isinstance(B.breadth_width(0.5), int)


def test_breadth_width_is_none_when_there_is_no_participation_to_show():
    """Two upstream causes, and both are named because only one is obvious: a
    stock row carries no participation (one name has no constituents), and
    momentum.participation returns None when usable == 0 — sentiment_svc filters
    members to the admitted set, so a SECTOR row can carry None too. Both render
    nothing; the zero-width bar belongs to a genuine 0.0."""
    assert B.breadth_width(None) is None


def test_breadth_width_reads_a_share_through_num_not_a_second_guard():
    """Rejecting what float() cannot read and coercing what it can are one
    policy, and it is _num's. The bool assertion is what carries this test's
    name: it is the SOLE behavioural difference _num makes here, since the 0..1
    range check already eats NaN and inf. Without it a hand-rolled float() guard
    passes every other line while breadth_width(True) returns 100 — a full bar
    asserting "every member confirms" from a malformed payload, and quadrant()
    has a dedicated bool test for the same reason on the same field."""
    assert B.breadth_width("x") is None
    assert B.breadth_width("0.5") == 50
    assert B.breadth_width(True) is None


def test_breadth_width_refuses_a_share_outside_zero_to_one():
    """momentum.participation is above/usable with 0 <= above <= usable, so this
    is unreachable through the real producer: defence-in-depth against a contract
    change, costing one row its bar, and nobody will notice when it fires. It
    refuses rather than clamps because the two are not both silent degrades —
    None is a silent absence, while 1.4 clamped to 100 draws the full bar and
    asserts "every constituent confirms": maximally confident, exactly inverted,
    and indistinguishable from a real reading."""
    assert B.breadth_width(1.4) is None
    assert B.breadth_width(-0.2) is None
    # Only -0.2 binds _share here; 1.4 exceeds the threshold anyway, so it reads
    # False under a _num-only implementation too.
    assert B.breadth_is_thin(-0.2) is False


def test_breadth_is_thin_flags_a_move_its_members_do_not_confirm():
    """The two live rows the threshold was read off, 2026-08-19: Real Estate was
    rising on 0.23 while Energy sat flat on 0.96. A map that paints those the same
    green hides the only thing separating a fragile advance from a broad one."""
    assert B.breadth_is_thin(0.23) is True
    assert B.breadth_is_thin(0.96) is False
    assert B.breadth_is_thin(None) is False    # a stock row is not a thin one


def test_breadth_is_thin_brackets_the_threshold_at_a_third():
    """Together these bound the constant to [1/3, 0.34). The lower assertion is
    the only case <= decides, and it is reachable: momentum.participation skips
    short-history members rather than counting them below, so one of three usable
    constituents lands on it exactly. Flagging it is quadrant()'s tie rule one
    level down, and a tidied 0.33 would read that row as broad. The upper
    assertion is what bounds the constant from above — 1/3 alone allows 0.35."""
    assert B.breadth_is_thin(1 / 3) is True
    assert B.breadth_is_thin(0.34) is False


def test_row_participation_reads_the_share_not_the_within_level_zscore():
    """The name is used twice in one row and the wrong one fails silently.
    ``row["participation"]`` is the 0..1 share; ``row["components"]
    ["participation"]`` is a within-level z-score, signed and unbounded
    (services/sentiment_svc/compute.py, ``_momentum_score_level``). Read through
    components, every negative row loses its bar and the rest draw a plausible
    wrong one — no exception to notice."""
    row = {"raw": {}, "participation": 0.25,
           "components": {"participation": -1.8}}
    assert B.row_participation(row) == 0.25
    assert B.breadth_width(B.row_participation(row)) == 25
    assert B.row_participation(None) is None and B.row_participation({}) is None


# ── the tree: sector -> industry -> stock ────────────────────────────────────
def test_build_tree_nests_industries_and_stocks_under_their_parents():
    levels = {
        "sector": [_row(1.0, 0.1, symbol="XLV", label="Health Care")],
        "industry": [_row(1.0, 0.2, symbol="XBI", label="Biotech",
                          sector="Health Care"),
                     _row(0.5, 0.2, symbol="IHI", label="Devices",
                          sector="Health Care")],
        "stock": [_row(1.0, 0.3, symbol="AMGN", sector="Health Care",
                       industry="Biotech")],
    }
    tree = B.build_tree(levels)
    assert [s["label"] for s in tree] == ["Health Care"]
    assert [i["label"] for i in tree[0]["industries"]] == ["Biotech", "Devices"]
    assert [k["symbol"] for k in tree[0]["industries"][0]["stocks"]] == ["AMGN"]
    # Every list key exists on every node, children or not, so the page iterates
    # them with no guard per node — at BOTH levels, not just the sector. Devices
    # is the reachable case: 3 of 69 industries held no admitted member stock on
    # 2026-08-19.
    assert tree[0]["industries"][1]["stocks"] == []
    assert tree[0]["orphan_stocks"] == []


def test_build_tree_keeps_a_stock_whose_industry_has_no_row_of_its_own():
    """The ordinary case, not a curiosity. compute.py builds industry_entries
    only for industries whose ETF cleared ``_momentum_admit``, while every member
    stock is scored regardless — so one illiquid industry ETF strands its whole
    membership, which still rolls up to the sector and must not vanish."""
    levels = {
        "sector": [_row(1.0, 0.1, symbol="XLV", label="Health Care")],
        "stock": [_row(1.0, 0.3, symbol="AMGN", sector="Health Care",
                       industry="Cannabis")],
    }
    tree = B.build_tree(levels)
    assert tree[0]["industries"] == []
    assert [k["symbol"] for k in tree[0]["orphan_stocks"]] == ["AMGN"]


def test_build_tree_drops_a_row_whose_parent_sector_is_unknown():
    """A row naming a sector with no sector row cannot be placed, and inventing a
    bucket for it would put a phantom row in the counts. BOTH child levels are
    given one, because each takes that decision on its own. Reached rather than
    hypothetical: compute.py maps a stock in no scored industry to ``("", "")``,
    and no sector row is ever labelled ``""``."""
    levels = {"sector": [],
              "industry": [_row(1.0, 0.2, symbol="XBI", label="Biotech",
                                sector="Nowhere")],
              "stock": [_row(1.0, 0.3, symbol="ZZZ", sector="Nowhere",
                             industry="Biotech")]}
    assert B.build_tree(levels) == []


def test_build_tree_keys_an_industry_by_its_sector_and_name_together():
    """An industry name is unique only within its sector — the cascade keys them
    ``(sector, industry)`` throughout (sectors_ref.constituents_by_industry). A
    name-only key files both sectors' constituents under whichever row was
    inserted last."""
    levels = {"sector": [_row(2.0, 0.1, symbol="XLV", label="HC"),
                         _row(1.0, 0.1, symbol="XLI", label="Ind")],
              "industry": [_row(1.0, 0.1, symbol="A", label="Robotics", sector="HC"),
                           _row(1.0, 0.1, symbol="B", label="Robotics", sector="Ind")],
              "stock": [_row(1.0, 0.1, symbol="ISRG", sector="HC", industry="Robotics"),
                        _row(1.0, 0.1, symbol="ROK", sector="Ind", industry="Robotics")]}
    tree = B.build_tree(levels)
    assert [k["symbol"] for k in tree[0]["industries"][0]["stocks"]] == ["ISRG"]
    assert [k["symbol"] for k in tree[1]["industries"][0]["stocks"]] == ["ROK"]


def test_by_strength_orders_bare_rows_exactly_as_the_tree_orders_sectors():
    """One ordering, two callers. The Desk strip sorts sectors without building
    a tree, and two screens showing the same sectors in different orders is a
    defect neither can display — /sentiment/sectors and /sentiment/rotation
    already print contradictory verdicts for a neighbouring reason."""
    rows = [_row(-1.0, -0.1, symbol="XLU", label="U"), None,
            _row(2.0, 0.5, symbol="XLV", label="V"),
            _row(None, None, symbol="XLE", label="E")]
    assert [r["label"] for r in B.by_strength(rows)] == ["V", "U", "E"]
    assert [s["label"] for s in B.build_tree({"sector": rows})] == ["V", "U", "E"]


def test_build_tree_returns_one_node_per_sector_row_not_one_per_label():
    """The ordered list, not the lookup index keyed on ``label``. Sector labels
    are unique in the live payload (11 of 11 on 2026-08-19) and unique upstream
    by construction — ``_momentum_universe`` builds them from a dict keyed on the
    name — so returning the index reads as identical right up until that stops
    holding, and then it drops a row the headline is still counting. Children of
    a duplicated label would attach to whichever row was inserted LAST, which —
    sectors sorting strongest-first — is the WEAKER of the two; unreachable, so
    it is recorded here rather than pinned."""
    dup = [_row(2.0, 0.1, symbol="XLV", label="Dup"),
           _row(1.0, 0.1, symbol="XLE", label="Dup")]
    assert [s["symbol"] for s in B.build_tree({"sector": dup})] == ["XLV", "XLE"]


def test_build_tree_orders_sectors_strongest_first():
    levels = {"sector": [_row(-1.0, -0.1, symbol="XLU", label="Utilities"),
                         _row(2.0, 0.5, symbol="XLV", label="Health Care"),
                         _row(0.5, 0.1, symbol="XLF", label="Financials")]}
    assert [s["label"] for s in B.build_tree(levels)] == \
        ["Health Care", "Financials", "Utilities"]


def test_build_tree_orders_industries_and_stocks_strongest_first_too():
    """The sector ordering test leaves both child levels unpinned — an
    implementation that sorts only the top level passes it, and a mis-ordered
    list is least visible at the depth carrying the most rows."""
    levels = {"sector": [_row(1.0, 0.1, symbol="XLV", label="HC")],
              "industry": [_row(0.1, 0.1, symbol="A", label="Weak", sector="HC"),
                           _row(2.0, 0.1, symbol="B", label="Strong", sector="HC")],
              "stock": [_row(0.1, 0.1, symbol="LOW", sector="HC", industry="Strong"),
                        _row(2.0, 0.1, symbol="HIGH", sector="HC", industry="Strong"),
                        _row(0.5, 0.1, symbol="MID", sector="HC", industry="Gone"),
                        _row(3.0, 0.1, symbol="TOP", sector="HC", industry="Gone")]}
    tree = B.build_tree(levels)
    assert [i["label"] for i in tree[0]["industries"]] == ["Strong", "Weak"]
    assert [k["symbol"] for k in tree[0]["industries"][0]["stocks"]] == ["HIGH", "LOW"]
    assert [k["symbol"] for k in tree[0]["orphan_stocks"]] == ["TOP", "MID"]


def test_build_tree_puts_unscored_sectors_last():
    levels = {"sector": [_row(None, None, symbol="XLU", label="Utilities"),
                         _row(-1.0, -0.1, symbol="XLE", label="Energy")]}
    assert [s["label"] for s in B.build_tree(levels)] == ["Energy", "Utilities"]


def test_build_tree_sorts_a_non_finite_trend_as_unscored_rather_than_raising():
    """A bare float() here would let a NaN sort unpredictably — every comparison
    against it is False — and would let a signalling Decimal raise inside
    sorted(). Both are what _num exists to prevent."""
    levels = {"sector": [_row(float("nan"), 0.1, symbol="A", label="A"),
                         _row(Decimal("sNaN"), 0.1, symbol="B", label="B"),
                         _row(1.0, 0.1, symbol="C", label="C")]}
    assert [s["label"] for s in B.build_tree(levels)] == ["C", "A", "B"]


def test_build_tree_returns_nodes_that_carry_their_row_and_leave_it_untouched():
    """The nesting keys are the tree's, not the payload's. /sentiment/momentum
    renders that same cached read, so a node built by mutating the row in place
    would hand the other page rows that had grown an ``industries`` list — and a
    node rebuilt from scratch would drop every field the row came with."""
    row = _row(1.0, 0.1, symbol="XLV", label="HC", score=0.8)
    node = B.build_tree({"sector": [row]})[0]
    assert node["symbol"] == "XLV" and node["score"] == 0.8
    assert node["raw"] == row["raw"]
    assert "industries" not in row and "orphan_stocks" not in row


def test_build_tree_copies_child_rows_too_not_only_the_sectors():
    """The copy invariant reads for all three levels and a sector-only fixture
    sees none of it — the blind spot the ordering test above names for itself. An
    industry node built by mutating its row hands /sentiment/momentum a row grown
    a ``stocks`` key; one rebuilt from scratch drops ``participation``, which is
    the breadth bar's whole input and which only the upper two levels carry."""
    industry = _row(1.0, 0.1, symbol="XBI", label="Bio", sector="HC",
                    participation=0.5)
    stock = _row(1.0, 0.1, symbol="AMGN", sector="HC", industry="Bio", score=0.6)
    tree = B.build_tree({"sector": [_row(1.0, 0.1, symbol="XLV", label="HC")],
                         "industry": [industry], "stock": [stock]})
    kid = tree[0]["industries"][0]
    assert kid["participation"] == 0.5 and kid["stocks"][0]["score"] == 0.6
    assert "stocks" not in industry and kid["stocks"][0] is not stock


def test_build_tree_drops_a_null_row_but_still_refuses_a_different_document():
    """``_raw``'s split, one level up: a null in a JSON array is a row we do not
    have and can be no node here — it names neither itself nor a parent — so it
    takes the same path as an unplaceable row. A non-dict row is a different
    document and still raises, through the same ``_raw`` the sort key reads."""
    levels = {"sector": [None, _row(1.0, 0.1, symbol="XLV", label="HC")],
              "industry": [None], "stock": [None]}
    assert [s["label"] for s in B.build_tree(levels)] == ["HC"]
    with pytest.raises(AttributeError):
        B.build_tree({"sector": ["XLV"]})


def test_build_tree_handles_an_empty_payload():
    assert B.build_tree({}) == []
    assert B.build_tree(None) == []
