"""Tests for the console's regime block (regime_mix callouts + console_regime)."""
import re

import pytest

from pages import console_regime as CR
from pages import regime_mix as RM


def _pt(ts, **mem):
    full = {k: 0.0 for k in RM.REGIME_ORDER}
    full.update(mem)
    return {"ts": ts, "memberships": full, "confidence": 0.5, "label": "choppy"}


def _session():
    """The real 2026-08-14 shape: Balanced bleeds, Stressed wakes from zero,
    Whipsaw takes and holds the lead, Breakout never moves."""
    return [
        _pt(1000, mean_reversion=0.37, trending=0.26, choppy=0.367, crisis=0.0),
        _pt(1300, mean_reversion=0.33, trending=0.27, choppy=0.378, crisis=0.02),
        _pt(1600, mean_reversion=0.286, trending=0.277, choppy=0.388, crisis=0.049),
    ]


# ---------------------------------------------------------------- callouts
def test_callouts_reproduce_the_days_story():
    c = RM.callouts(_session())
    assert c["dominant"]["key"] == "choppy"          # largest share
    assert c["biggest_move"]["key"] == "mean_reversion"   # -8.4pp, largest |Δ|
    assert c["emerging"]["key"] == "crisis"          # 0 -> +4.9pp


def test_emerging_prefers_a_band_waking_from_zero_over_a_bigger_riser():
    """A band waking from nothing is a change of KIND; a bigger riser that was
    already present is only a change of degree."""
    pts = [_pt(1000, trending=0.30, choppy=0.40, crisis=0.0),
           _pt(1600, trending=0.50, choppy=0.40, crisis=0.02)]
    assert RM.callouts(pts)["emerging"]["key"] == "crisis"


def test_emerging_falls_back_to_the_largest_riser():
    pts = [_pt(1000, trending=0.30, choppy=0.40, crisis=0.10),
           _pt(1600, trending=0.42, choppy=0.44, crisis=0.10)]
    c = RM.callouts(pts)
    assert c["biggest_move"]["key"] == "trending"
    assert c["emerging"]["key"] == "choppy"          # not the biggest_move


def test_callouts_are_empty_without_history():
    c = RM.callouts([])
    assert c == {"dominant": None, "biggest_move": None, "emerging": None}


def test_biggest_move_takes_the_largest_absolute_change_either_way():
    pts = [_pt(1000, trending=0.50, choppy=0.10),
           _pt(1600, trending=0.20, choppy=0.30)]
    assert RM.callouts(pts)["biggest_move"]["key"] == "trending"   # -30pp


# ------------------------------------------------------------------- notes
def test_notes_gloss_each_regime_and_dormant_overrides():
    rows = {r["key"]: r for r in RM.rank_rows(_session())}
    assert RM.regime_note(rows["choppy"]) == "CHOP · NO EDGE"
    assert RM.regime_note(rows["crisis"]) == "VOL EXPANSION"
    # Breakout holds nothing all session -> its usual gloss would describe
    # something absent.
    assert RM.regime_note(rows["breakout"]) == RM.ZERO_NOTE


def test_every_regime_has_a_note():
    assert set(RM.REGIME_NOTES) == set(RM.REGIME_ORDER)


def test_regime_note_is_safe_on_junk():
    assert RM.regime_note(None) == "" and RM.regime_note({}) == RM.ZERO_NOTE


# ------------------------------------------------------------------ colour
def test_a_dormant_regime_is_muted():
    from pages.options import theme
    assert CR.regime_hex("breakout", 0.0) == theme.CONSOLE_COLORS["regime_zero"]
    assert CR.regime_hex("breakout", 0.2) != CR.regime_hex("breakout", 0.0)


def test_console_regime_hues_are_the_handoffs_not_the_charts_set():
    assert CR.regime_hex("mean_reversion", 0.3) == "#6f86ff"
    assert CR.regime_hex("choppy", 0.3) == "#c3ccd6"


# -------------------------------------------------------------- sparkline
def test_sparkline_scales_to_its_own_range():
    svg = CR.sparkline_svg([0.30, 0.31, 0.32], "#35d68a")
    ys = [float(p.split(",")[1])
          for p in re.search(r'points="([^"]+)"', svg).group(1).split()]
    assert min(ys) == pytest.approx(0.0)
    assert max(ys) == pytest.approx(CR.SPARK_H)


def test_a_flat_series_draws_a_dashed_rule():
    """Auto-scaling a constant would amplify floating-point dust into a
    convincing squiggle."""
    for series in ([0.0, 0.0, 0.0], [0.4], []):
        svg = CR.sparkline_svg(series, "#35d68a")
        assert "stroke-dasharray" in svg and "<polyline" not in svg


def test_sparkline_fills_the_cell_exactly():
    assert 'preserveAspectRatio="none"' in CR.sparkline_svg([1, 2], "#fff")


@pytest.mark.parametrize("series", [None, ["x"], [None, None], [float("nan")]])
def test_sparkline_never_raises(series):
    assert CR.sparkline_svg(series, "#35d68a").startswith("<svg")


# ---------------------------------------------------------------- change
def test_change_text_signs_and_colours():
    rows = {r["key"]: r for r in RM.rank_rows(_session())}
    assert CR.change_text(rows["crisis"])[0] == "+4.9pp"
    assert CR.change_text(rows["mean_reversion"])[0] == "−8.4pp"
    assert CR.change_text(rows["crisis"])[1] != CR.change_text(
        rows["mean_reversion"])[1]


def test_a_band_that_never_moved_gets_a_dash():
    rows = {r["key"]: r for r in RM.rank_rows(_session())}
    assert CR.change_text(rows["breakout"])[0] == "—"
    assert CR.change_text(None)[0] == "—"


# ------------------------------------------------------------------- grid
def test_head_and_data_rows_share_one_grid_definition():
    """The handoff calls this alignment out explicitly, and it is the first
    thing to break if the two are spelled separately."""
    assert CR.GRID.count("grid-cols-") == 1
    assert "190px_1fr_132px_74px" in CR.GRID
