"""Signal age + score-trend display vocabulary (Tier 1, pure)."""
import datetime

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


def test_score_delta_reads_the_WINDOW_not_the_whole_series():
    # A regression to values[0] renders "▲ −31.0" — an up arrow beside a
    # negative number — on the very series test_the_window_reads_from_the_END
    # exists to protect. That test pins the ARROW; this one pins the NUMBER.
    assert persistence.score_delta([90.0, 50.0, 52.0, 55.0, 59.0]) == 9.0


def test_a_delta_exactly_on_the_deadband_is_steady():
    assert persistence.score_trend([60.0, 61.0, 59.0, 62.0]) == "steady"


def test_a_string_score_does_not_raise():
    # fmt.num COERCES, so filtering with it and keeping the RAW entry lets a
    # str through to the subtraction. Parsing guard != value guard.
    assert persistence.score_trend(["60.0", "61.0", "62.0", "66.0"]) == "rising"
    assert persistence.score_delta(["60.0", "61.0", "62.0", "66.0"]) == 6.0


@pytest.mark.parametrize("junk", [None, float("nan"), True, "abc", {}, []])
def test_unusable_entries_are_dropped_from_the_series(junk):
    # Dropped, not zeroed: a 0.0 would read as a real collapse to the floor.
    assert persistence.score_trend([60.0, junk, 61.0, 62.0, 66.0]) == "rising"


@pytest.mark.parametrize("junk", [None, float("nan"), True, "abc"])
def test_an_unusable_entry_is_DROPPED_not_zeroed(junk):
    # The 5-reading version of this cannot see the difference: zeroed, the junk
    # falls outside the 4-wide window. Four readings puts it inside.
    #
    # ⚠ "new", not "steady": four readings minus one unusable is THREE, under
    # the window, so no direction may be named. It is ZEROING that yields four
    # readings and a nameable direction — [60, 0, 61, 62] reads "steady", since
    # score_trend compares only the ENDPOINTS (62 and 60, two apart, inside the
    # deadband) and the zero here sits between them. So the discriminator is
    # the LENGTH, and the mutant's answer is the steady one.
    assert persistence.score_trend([60.0, junk, 61.0, 62.0]) == "new"


@pytest.mark.parametrize("junk", [None, float("nan"), True, "abc"])
def test_an_unusable_entry_at_the_END_is_dropped_not_zeroed(junk):
    # The position that shows the harm the rule exists for. Here the zero WOULD
    # land on an endpoint: [60, 61, 62, 0] reads "▼ -60.0", a scoreless entry
    # rendering as a hard collapse to the score floor.
    assert persistence.score_trend([60.0, 61.0, 62.0, junk]) == "new"


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


def test_age_unknown_beats_a_first_seen_that_is_present():
    # Tier 2 omits first_seen whenever it sets age_unknown, so the two coincide
    # in production and `not first` short-circuits — which is exactly why this
    # branch is invisible to every other test. It is the explicit record of "we
    # could not know"; a stamp arriving beside it must not override it.
    facts = persistence.persistence_facts(
        {"age_unknown": True, "first_seen": "2026-09-17T12:03:00",
         "seen": 3, "gaps": 0, "scores": []})
    assert facts["since"] == "—"


def test_persistence_facts_for_a_missing_setup():
    # A row with no derivable setup_key, or a map that never got built.
    facts = persistence.persistence_facts(None)
    assert facts["since"] == "—"
    assert facts["trend_text"] == "—"


@pytest.mark.parametrize("junk", ["x", None, float("nan"), [], {"a": 1}])
def test_a_corrupt_seen_or_gaps_claims_nothing(junk):
    # These come off Redis. Guarding against RAISING is not enough — the first
    # draft rendered "09:15 · 0x" (seen zero times, beside a stamp saying it was
    # live at 09:15) and handed consumers gaps=0, a positive claim of continuity.
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": junk, "gaps": junk,
         "scores": []})
    assert facts["since"] == "09:15"        # count omitted, never "· 0x"
    assert facts["gaps"] is None            # no claim, never a zero
    assert "gap" not in facts["detail"]


@pytest.mark.parametrize("dateless", [
    "2026-09-17", datetime.date(2026, 9, 17),   # parse, but carry no time
    "garbage", "", None,                        # do not parse at all
])
def test_a_timestamp_with_no_TIME_in_it_is_a_dash(dateless):
    # The dash, not "" (what the old positional slice produced) and not "00:00"
    # — datetime.fromisoformat accepts a date-only string and INVENTS midnight,
    # which trades a malformed value for a confidently wrong one. The unparseable
    # cases are the ones that pin "a dash, never an empty string": the date-only
    # pair alone cannot, since they are rejected before the parse is attempted.
    facts = persistence.persistence_facts(
        {"first_seen": dateless, "seen": 14, "gaps": 0, "scores": []})
    assert facts["since"] == "—"
    assert facts["detail"] == ""


@pytest.mark.parametrize("stamped", [
    "2026-09-17T09:15:00",              # what Tier 2 writes
    "2026-09-17T09:15:00+00:00",        # ... if it ever carried an offset
    "2026-09-17 09:15:00",              # the space-separated spelling
    datetime.datetime(2026, 9, 17, 9, 15),
])
def test_every_stamped_shape_reads_the_same_clock_time(stamped):
    facts = persistence.persistence_facts(
        {"first_seen": stamped, "seen": 14, "gaps": 0, "scores": []})
    assert facts["since"] == "09:15 · 14x"
    assert facts["detail"] == "Live since 09:15"


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
