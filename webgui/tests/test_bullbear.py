"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, never blended: absolute trend (raw.trend) and relative strength
(raw.excess). See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
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
