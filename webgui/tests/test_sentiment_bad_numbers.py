"""A NaN, an infinity or a bool is no reading on the Sentiment page.

Measured 2026-11-28 before the fix: ``_safe_float`` was ``float_or``, which
passed NaN, the infinities and ``True`` (as 1.0). A NaN composite pinned the
gauge at 100 and a NaN trend score pinned the needle at 100 - the clamp's HIGH
bound, the trap CLAUDE.md's sentiment section names - while a missing one reads
0 and 50. The Yesterday tile printed "nan" and "+nan", the component table a
NaN score and contribution, and an infinite total reached the composite series.
Each bad input must now read exactly as a missing one does.
"""
import pytest

from pages import sentiment as S

BAD = [float("nan"), float("inf"), float("-inf"), True]
IDS = ["nan", "inf", "-inf", "True"]

CASES = {
    "gauge_score": S.gauge_score,
    "traffic_color": S.traffic_color,
    "trend_gauge_value": lambda v: S.trend_gauge_value({"score": v}),
    "sc_text_class": S.sc_text_class,
    "composite_series": lambda v: S.composite_series(
        [{"ts": 1764000000, "composite": {"total_score": v}}]),
    "composite_arc_value": lambda v: S._composite_arc_value(
        {"composite": {"total_score": v}}),
    "trend_arc_value score": lambda v: S._trend_arc_value(
        {"score": v, "confidence": 0.8}),
    "total_or_none": lambda v: S._total_or_none({"total_score": v}),
    "previous_total": lambda v: S.previous_total({"prev_total": v}, {}, []),
    "tiles yesterday": lambda v: S.tiles({"composite": {"total_score": 6.0}}, v, None),
    "component rows score": lambda v: S.component_table_rows(
        {"component_scores": {"breadth": v}, "component_confidence": {"breadth": 0.8}},
        {"breadth": 0.2}, "", ""),
}


@pytest.mark.parametrize("bad", BAD, ids=IDS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_bad_number_reads_as_a_missing_one(case, bad):
    build = CASES[case]
    assert repr(build(bad)) == repr(build(None))


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_cases_are_not_vacuous(case):
    build = CASES[case]
    assert repr(build(7.5)) != repr(build(None))


def test_a_real_zero_score_is_still_a_reading():
    # 0.0 is a real, maximally bearish reading (CLAUDE.md): not the default.
    assert S._safe_float(0.0, 5.0) == 0.0
    assert S._safe_float("6.25") == 6.25
