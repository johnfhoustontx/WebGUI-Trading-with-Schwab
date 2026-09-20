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


def test_card_shell_is_the_apps_card_not_a_console_one():
    """RE-AIMED 2026-09-19, deliberately. It asserted ``rounded-none`` — the
    console's hard edges were its own visual premise, and the consistency
    standard retires that premise along with the gradient card it sat on: a
    card on this screen is the same card as on every other. It still fires if a
    bespoke ground, border or radius comes back, which is the half worth
    keeping."""
    from pages import console as K
    from pages.options import theme
    assert CC.CARD_SHELL.startswith(theme.CARD)
    assert theme.CARD == K.CARD
    assert "rounded-none" not in CC.CARD_SHELL


def test_the_card_text_ladder_is_the_apps_three_steps():
    """The console drew SIX neutral steps over a near-black ground; the app has
    three, and the collapse follows the ROLE rather than the old step number —
    a title is a value, a kicker is a label, a meta line is the quietest
    furniture. Taken from ``console`` so the two card modules cannot drift."""
    from pages import console as K
    assert K.TXT in CC.HEAD_TITLE
    assert K.DIM in CC.HEAD_META
    assert K.MUTED in CC.KICKER
    for cls in (CC.HEAD_TITLE, CC.HEAD_META, CC.KICKER):
        assert "font-[" not in cls, "the condensed display face is retired"


def test_the_cell_tint_lifts_the_apps_card_ground_not_a_near_black():
    """A 2x2 cell is its value colour mixed a tenth into the ground BEHIND it.
    The ground moved, so the base had to move with it — left at #080c11 the
    tint would have been a near-black patch inside a navy card."""
    from pages.options import theme
    card_bg = theme.THEME["palette"]["card_bg"]
    assert CC.cell_tint(card_bg) == card_bg          # no value, no lift
    assert CC.cell_tint("#ffffff") != CC.cell_tint("#000000")
    assert "#080c11" not in CC.cell_tint("#35d68a")


def test_the_card_data_colours_are_untouched():
    """Must-not-change, and it passes before and after by design: the hero
    bands, the delta arrows and the three chromatic tones are READINGS."""
    assert CC.hero_parts(73)[1] == "#35d68a"
    assert CC.delta_parts(80, 60, "WEEK")[2] == "#35d68a"
    assert CC.delta_parts(60, 80, "WEEK")[2] == "#e0b74e"
    assert CC.tone_hex("pos") == "#35d68a"
    assert CC.tone_hex("neg") == "#f2646b"
    assert CC.tone_hex("warn") == "#e0b74e"


def test_a_flat_tone_and_a_missing_hero_take_the_apps_muted_step():
    """``flat`` is the ABSENCE of a direction and a missing hero is the absence
    of a reading; neither is a colour that means something, so both take the
    app's muted step rather than the console's own grey."""
    from pages.options import theme
    muted = theme.THEME["palette"]["muted"]
    assert CC.tone_hex("flat") == muted
    assert CC.tone_hex("nonsense") == muted
    assert CC.hero_parts(None)[1] == muted
