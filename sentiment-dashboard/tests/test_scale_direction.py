"""The composite's direction: calm, supportive conditions score HIGH, stress LOW.

The app described this 1-10 scale as contrarian ("10 = max fear") on every
screen and in every manual until 2026-09-11, while each scorer did the
opposite - a VIX spike, heavy put buying and a weak tape all score low. On a
calm, rising morning the Desk's summary then told the reader investors were
fearful. The screens now say what these tests pin; if a scorer is ever flipped
to a true contrarian reading, these fail and every sentence describing the
scale has to change with it.
"""
from scoring import breadth, put_call, vix


def test_heavy_put_buying_scores_low_and_call_buying_high():
    assert put_call.score(1.35).score < put_call.score(0.55).score
    assert put_call.score(1.35).score <= 2
    assert put_call.score(0.55).score >= 8


def test_a_vix_spike_scores_low_and_a_calm_vix_high():
    spike = vix.score_term(vix=30.0, vix_ma=20.0)   # 1.5x its 10-day average
    calm = vix.score_term(vix=15.0, vix_ma=19.0)    # well under it
    assert spike.score <= 2 and calm.score >= 8


def test_vix_backwardation_scores_low_and_contango_high():
    assert vix.score_term_slope(vix9d=24.0, vix=20.0).score <= 2
    assert vix.score_term_slope(vix9d=16.0, vix=20.0).score >= 8


def test_a_weak_tape_scores_low_and_a_strong_one_high():
    weak = breadth.score(breadth_ratio=0.3)
    strong = breadth.score(breadth_ratio=4.0)
    assert weak.score < strong.score
