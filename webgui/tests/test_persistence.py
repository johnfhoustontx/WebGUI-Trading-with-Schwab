"""Signal age + score-trend display vocabulary (Tier 1, pure)."""
import pytest

from pages.options import persistence


@pytest.mark.parametrize("scores", [[], [60.0], [60.0, 61.0], [60.0, 61.0, 62.0]])
def test_no_direction_is_claimed_under_a_full_window(scores):
    # Same discipline as commit_direction: never name a direction two
    # independent reads do not back.
    assert persistence.score_trend(scores) == "new"


def test_a_rise_past_the_deadband_is_rising():
    assert persistence.score_trend([60.0, 61.0, 62.0, 66.0]) == "rising"


def test_a_fall_past_the_deadband_is_fading():
    assert persistence.score_trend([70.0, 68.0, 66.0, 63.0]) == "fading"


def test_a_wobble_inside_the_deadband_is_steady():
    # The composite is recomputed from scratch every scan; +-1 is noise.
    assert persistence.score_trend([60.0, 61.0, 59.0, 61.5]) == "steady"


def test_the_window_reads_from_the_END_not_the_start():
    # A setup that crashed this morning and has been climbing for an hour is
    # RISING. Comparing against scores[0] would call it fading all day.
    assert persistence.score_trend([90.0, 50.0, 52.0, 55.0, 59.0]) == "rising"


def test_a_string_score_does_not_raise():
    # fmt.num COERCES, so filtering with it and keeping the RAW entry lets a
    # str through to the subtraction. Parsing guard != value guard.
    assert persistence.score_trend(["60.0", "61.0", "62.0", "66.0"]) == "rising"
    assert persistence.score_delta(["60.0", "61.0", "62.0", "66.0"]) == 6.0


@pytest.mark.parametrize("junk", [None, float("nan"), True, "abc", {}, []])
def test_unusable_entries_are_dropped_from_the_series(junk):
    # Dropped, not zeroed: a 0.0 would read as a real collapse to the floor.
    assert persistence.score_trend([60.0, junk, 61.0, 62.0, 66.0]) == "rising"


def test_persistence_facts_for_a_steady_setup():
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": 14, "gaps": 0,
         "scores": [60.0, 61.0, 62.0, 66.0]})
    assert facts["since"] == "09:15 · 14x"
    assert facts["trend"] == "rising"
    assert facts["trend_text"] == "▲ +6.0"


def test_persistence_facts_for_an_unknown_age():
    facts = persistence.persistence_facts(
        {"age_unknown": True, "seen": 3, "gaps": 0, "scores": []})
    assert facts["since"] == "—"
    assert facts["trend"] == "new"
    assert facts["trend_text"] == "new"


def test_persistence_facts_for_a_missing_setup():
    # A row with no derivable setup_key, or a map that never got built.
    facts = persistence.persistence_facts(None)
    assert facts["since"] == "—"
    assert facts["trend_text"] == "—"


@pytest.mark.parametrize("junk", ["x", None, float("nan"), [], {"a": 1}])
def test_a_corrupt_seen_or_gaps_does_not_raise(junk):
    # These come off Redis. int(float("nan")) raises ValueError, which would
    # propagate out of here into the row stamper.
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": junk, "gaps": junk,
         "scores": []})
    assert isinstance(facts["gaps"], int)
    assert facts["since"].startswith("09:15")


def test_gaps_are_reported():
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": 9, "gaps": 2,
         "scores": []})
    assert facts["gaps"] == 2
    assert "2 gaps" in facts["detail"]


def test_one_gap_is_singular():
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": 9, "gaps": 1,
         "scores": []})
    assert "1 gap" in facts["detail"] and "gaps" not in facts["detail"]
