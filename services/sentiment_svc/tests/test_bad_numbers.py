"""A NaN, an infinity or a bool is no reading in the sentiment service.

Measured 2026-11-28 before the fix: ``compute._safe_float`` was a bare
``float()`` with a 0.0 default, so an infinite or ``True`` composite total
entered the composite series as a point. It must read as a missing one.
"""
import pytest

from services.sentiment_svc import compute as C


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "inf"],
                         ids=["nan", "inf", "-inf", "True", "'inf'"])
def test_a_bad_total_reads_as_a_missing_one(bad):
    def series(v):
        return C.composite_series([{"ts": 1764000000, "composite": {"total_score": v}}])
    assert series(bad) == series(None)


def test_safe_float_keeps_its_default_and_reads_text():
    assert C._safe_float(float("nan"), 5.0) == 5.0
    assert C._safe_float("6.5") == 6.5
    assert C._safe_float(0.0, 5.0) == 0.0      # a real zero is a reading
