"""Tests for the console's three top cards (pages/console_cards.py)."""
import re

import pytest

from pages import console_cards as CC


# ------------------------------------------------------------------- hero
def test_hero_uses_the_band_colour():
    text, hexv = CC.hero_parts(73)
    assert text == "73" and hexv == "#35d68a"
    assert CC.hero_parts(53)[1] == "#e0b74e"


def test_missing_hero_is_a_dash_not_a_zero():
    """On a 0-100 score a 0 means 'maximally bearish', not 'no reading'."""
    text, hexv = CC.hero_parts(None)
    assert text == "—" and hexv != "#35d68a"


# ------------------------------------------------------------------ delta
def test_delta_matches_the_handoffs_two_examples():
    assert CC.delta_parts(73, 60, "WEEK")[:2] == ("▲", "+13 vs WEEK")
    assert CC.delta_parts(68, 83, "MONTH")[:2] == ("▼", "−15 vs MONTH")


def test_delta_colours_follow_the_sign():
    up = CC.delta_parts(73, 60, "WEEK")
    down = CC.delta_parts(68, 83, "MONTH")
    assert up[2] != down[2]


@pytest.mark.parametrize("a,b", [(None, 60), (73, None), (None, None)])
def test_delta_is_absent_when_either_side_is(a, b):
    assert CC.delta_parts(a, b, "WEEK") is None


def test_zero_delta_reads_as_up_not_down():
    assert CC.delta_parts(70, 70, "WEEK")[:2] == ("▲", "+0 vs WEEK")


# ------------------------------------------------------------------ tones
def test_tone_colours_are_distinct_per_state():
    hexes = {CC.tone_hex(t) for t in ("pos", "neg", "warn", "flat")}
    assert len(hexes) == 4


def test_unknown_tone_degrades_to_muted():
    assert CC.tone_hex("nonsense") == CC.tone_hex("flat")


def test_cell_tint_is_near_black_but_not_black():
    tint = CC.cell_tint("#35d68a")
    assert re.fullmatch(r"#[0-9a-f]{6}", tint)
    assert tint != "#000000" and tint < "#40"      # stays very dark


# ------------------------------------------------------------- divergence
def _detail(hi=10.0, lo=5.0):
    return {"high": {"name": "Market Breadth", "score": hi},
            "low": {"name": "Rotation", "score": lo}}


def test_divergence_bars_are_proportional_to_the_scores():
    bars = CC.divergence_bars(_detail())
    assert len(bars) == 2
    assert bars[0][2] == CC.DIVERGENCE_BAR_H          # 10/10 -> full height
    assert bars[1][2] == CC.DIVERGENCE_BAR_H // 2     # 5/10 -> half
    assert bars[0][3] > bars[1][3]                    # the high bar is stronger


def test_divergence_text_names_both_components():
    assert CC.divergence_text(_detail()) == "Market Breadth 10 vs Rotation 5"


@pytest.mark.parametrize("bad", [None, {}, "x", {"high": {}}, {"high": 1, "low": 2},
                                 {"high": {"score": "x"}, "low": {"score": 1}}])
def test_divergence_degrades_to_nothing(bad):
    """Tier 2 sends None when the engine did not fire; the alert then hides
    rather than rendering an empty box."""
    assert CC.divergence_bars(bad) == []
    assert CC.divergence_text(bad) == ""


def test_a_zero_score_still_draws_a_visible_stub():
    """A 0 component is a real reading — it must not vanish into a 0px bar."""
    bars = CC.divergence_bars(_detail(hi=10.0, lo=0.0))
    assert bars[1][2] >= 2


# ------------------------------------------------------------------ chrome
def test_pill_classes_are_spaceless_arbitraries():
    for arb in re.findall(r"\[[^\]]*\]", CC.pill_classes("#35d68a")):
        assert " " not in arb


def test_card_shell_keeps_square_corners():
    """Hard edges are the console's whole visual premise, so the reset is
    explicit rather than inherited."""
    assert "rounded-none" in CC.CARD_SHELL
