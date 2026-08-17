"""Pure-transform tests for the Sector & Industry heat-tile redesign.

Everything the /sentiment/sectors grid paints — the oklch heat ramp, the
per-column normalisation, the flat bands, ranking, sorting and the header
strings — lives in ``pages.sector_heat`` as pure functions so it can be pinned
here without a browser.
"""
import math

import pytest
from pages import sector_heat as H


# ── the oklch → sRGB conversion ──────────────────────────────────────────────
def test_oklch_hex_matches_known_conversions():
    # White and black are the two anchors every oklch implementation agrees on.
    assert H.oklch_hex(1.0, 0.0, 0.0) == "#FFFFFF"
    assert H.oklch_hex(0.0, 0.0, 0.0) == "#000000"
    # The ramp's strongest UP stop, oklch(0.300 0.110 158) — the value the
    # reference design renders for a full-intensity green tile.
    assert H.oklch_hex(0.300, 0.110, 158.0) == "#003D16"


def test_oklch_hex_clamps_out_of_gamut_instead_of_raising():
    # A chroma no sRGB primary can reach must clamp, never throw or wrap.
    v = H.oklch_hex(0.60, 0.40, 158.0)
    assert v.startswith("#") and len(v) == 7
    assert all(c in "0123456789ABCDEF" for c in v[1:])


# ── the ramp palettes ────────────────────────────────────────────────────────
def test_heat_palettes_cover_every_level_both_directions():
    want = set(range(-H.LEVELS, H.LEVELS + 1))
    assert set(H.HEAT_BG) == want
    assert set(H.HEAT_TXT) == want


def test_heat_palette_entries_are_static_tailwind_classes():
    for lvl, cls in H.HEAT_BG.items():
        assert cls.startswith("bg-[#") and cls.endswith("]"), lvl
    for lvl, cls in H.HEAT_TXT.items():
        assert cls.startswith("text-[#") and cls.endswith("]"), lvl


def test_heat_palette_is_a_finite_deduped_vocabulary():
    # The house rule is a FIXED finite palette, not a per-datum colour: 13 levels
    # → 13 distinct fills, and the two directions never collide.
    assert len(set(H.HEAT_BG.values())) == 2 * H.LEVELS + 1
    assert len(set(H.HEAT_TXT.values())) == 2 * H.LEVELS + 1


def _lum(cls):
    """Rough relative brightness of a ``…-[#rrggbb]`` class, for ordering."""
    s = cls.split("[#", 1)[1].rstrip("]")
    r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_background_darkens_toward_flat_and_saturates_with_level():
    # Intensity must be monotone in BOTH directions or the band lies about size.
    ups = [_lum(H.HEAT_BG[i]) for i in range(1, H.LEVELS + 1)]
    dns = [_lum(H.HEAT_BG[-i]) for i in range(1, H.LEVELS + 1)]
    assert ups == sorted(ups)
    assert dns == sorted(dns)


def test_text_lifts_with_intensity():
    ups = [_lum(H.HEAT_TXT[i]) for i in range(1, H.LEVELS + 1)]
    dns = [_lum(H.HEAT_TXT[-i]) for i in range(1, H.LEVELS + 1)]
    assert ups == sorted(ups)
    assert dns == sorted(dns)
    # A flat cell's figure is the dimmest thing in the column.
    assert _lum(H.HEAT_TXT[0]) < _lum(H.HEAT_TXT[1])
    assert _lum(H.HEAT_TXT[0]) < _lum(H.HEAT_TXT[-1])


def test_remove_sets_list_every_class_so_repaints_cannot_stack():
    # Reactive recolours swap via .classes(remove=…); the remove set must be the
    # WHOLE vocabulary or a second repaint leaves two fills fighting.
    assert set(H.HEAT_BG_CLASSES.split()) == set(H.HEAT_BG.values())
    assert set(H.HEAT_TXT_CLASSES.split()) == set(H.HEAT_TXT.values())


# ── flat bands + per-column normalisation ────────────────────────────────────
def test_flat_bands_widen_with_the_horizon():
    # A quiet month must not glow just because a month drifts further than a day.
    assert H.FLAT_BAND["day"] < H.FLAT_BAND["week"] < H.FLAT_BAND["month"]
    assert H.FLAT_BAND["day"] == 0.50


def test_column_scale_spans_sectors_and_industries():
    # Industries share the scale whether or not they are expanded — otherwise
    # opening a sector would repaint every tile above it.
    rows = [{"day": 0.1}, {"day": 0.2}, {"day": None}]
    inds = [{"day": 0.3}, {"day": -2.0}]
    assert H.column_scale(rows, "day") < H.column_scale(rows + inds, "day")


def test_column_scale_is_the_90th_percentile_of_magnitude():
    rows = [{"day": v} for v in (1.0, -2.0, 3.0, -4.0, 5.0,
                                 6.0, -7.0, 8.0, 9.0, -10.0)]
    # Ten readings → index 9 * 0.9 = 8.1, i.e. 9.0 + (10.0 - 9.0) * 0.1.
    assert H.column_scale(rows, "day") == pytest.approx(9.1)


def test_a_single_outlier_does_not_set_the_whole_column():
    # The failure this replaced: one +27% industry against a ~3% median pinned
    # every sector into the bottom of the ramp. The scale must stay near the
    # body of the distribution, and the outlier saturates instead.
    body = [{"month": 3.0} for _ in range(20)]
    scale = H.column_scale(body + [{"month": 27.0}], "month")
    assert scale < 10.0
    assert H.heat_level(27.0, scale, H.FLAT_BAND["month"]) == H.LEVELS


def test_column_scale_of_one_reading_is_that_reading():
    assert H.column_scale([{"day": 2.5}], "day") == 2.5
    assert H.column_scale([{"day": -2.5}], "day") == 2.5


def test_column_scale_with_no_data_is_zero():
    assert H.column_scale([], "day") == 0.0
    assert H.column_scale([{"day": None}], "day") == 0.0
    assert H.column_scale([{"day": "n/a"}, {"day": float("nan")}], "day") == 0.0


def test_column_scales_builds_all_three_independently():
    rows = [{"day": 1.0, "week": 2.0, "month": 3.0}]
    assert H.column_scales(rows) == {"day": 1.0, "week": 2.0, "month": 3.0}


# ── the level map ────────────────────────────────────────────────────────────
def test_heat_level_none_stays_none():
    assert H.heat_level(None, 2.0, 0.5) is None


def test_inside_the_flat_band_reads_neutral():
    assert H.heat_level(0.34, 2.0, 0.5) == 0
    assert H.heat_level(-0.49, 2.0, 0.5) == 0
    assert H.heat_level(0.0, 2.0, 0.5) == 0


def test_the_column_maximum_is_full_intensity():
    assert H.heat_level(2.0, 2.0, 0.5) == H.LEVELS
    assert H.heat_level(-2.0, 2.0, 0.5) == -H.LEVELS


def test_just_past_the_band_is_the_faintest_step_not_a_jump():
    assert H.heat_level(0.51, 2.0, 0.5) == 1
    assert H.heat_level(-0.51, 2.0, 0.5) == -1


def test_level_is_monotone_across_the_range():
    seen = [H.heat_level(v / 10, 2.0, 0.5) for v in range(5, 21)]
    assert seen == sorted(seen)
    assert seen[0] == 0 and seen[-1] == H.LEVELS


def test_sign_is_preserved_symmetrically():
    for v in (0.6, 0.9, 1.4, 1.9):
        assert H.heat_level(-v, 2.0, 0.5) == -H.heat_level(v, 2.0, 0.5)


def test_a_scale_at_or_below_the_band_never_divides_by_zero():
    # Every value flat → scale == band. Anything past it still has to land
    # somewhere finite rather than raise.
    assert H.heat_level(0.4, 0.5, 0.5) == 0
    assert H.heat_level(0.7, 0.5, 0.5) == H.LEVELS
    assert H.heat_level(0.7, 0.0, 0.5) == H.LEVELS


def test_a_value_above_the_column_scale_clamps_to_full():
    assert H.heat_level(99.0, 2.0, 0.5) == H.LEVELS


def test_heat_classes_pair_background_and_text():
    bg, txt = H.heat_classes(1.42, 1.42, 0.5)
    assert bg == H.HEAT_BG[H.LEVELS] and txt == H.HEAT_TXT[H.LEVELS]
    bg, txt = H.heat_classes(None, 1.42, 0.5)
    assert bg == H.HEAT_BG[0] and txt == H.HEAT_TXT[0]


# ── sorting + ranking ────────────────────────────────────────────────────────
def _rows():
    return [
        {"sector": "Energy", "day": 1.00, "week": 7.67, "month": 8.58},
        {"sector": "Industrials", "day": 0.34, "week": 0.72, "month": 3.53},
        {"sector": "Utilities", "day": -0.26, "week": 1.61, "month": -2.55},
        {"sector": "Nowhere", "day": None, "week": None, "month": None},
    ]


def test_sort_rows_descending_puts_missing_data_last():
    got = [r["sector"] for r in H.sort_rows(_rows(), "day", True)]
    assert got == ["Energy", "Industrials", "Utilities", "Nowhere"]


def test_sort_rows_ascending_still_puts_missing_data_last():
    got = [r["sector"] for r in H.sort_rows(_rows(), "day", False)]
    assert got == ["Utilities", "Industrials", "Energy", "Nowhere"]


def test_sort_rows_switches_column():
    got = [r["sector"] for r in H.sort_rows(_rows(), "month", True)]
    assert got == ["Energy", "Industrials", "Utilities", "Nowhere"]
    got = [r["sector"] for r in H.sort_rows(_rows(), "week", True)]
    assert got == ["Energy", "Utilities", "Industrials", "Nowhere"]


def test_sort_rows_does_not_mutate_its_input():
    rows = _rows()
    H.sort_rows(rows, "week", True)
    assert [r["sector"] for r in rows] == ["Energy", "Industrials",
                                           "Utilities", "Nowhere"]


def test_rank_line_restates_position_in_words():
    assert H.rank_line(0, 11, "day") == "RANK 1 OF 11 · DAY"
    assert H.rank_line(10, 11, "month") == "RANK 11 OF 11 · MONTH"


# ── formatting ───────────────────────────────────────────────────────────────
def test_fmt_pct_is_signed_to_two_places():
    assert H.fmt_pct(1.0) == "+1.00%"
    assert H.fmt_pct(-0.07) == "-0.07%"
    assert H.fmt_pct(0.0) == "+0.00%"


def test_fmt_pct_missing_is_an_em_dash_not_a_zero():
    # A missing reading must never render as "+0.00%" — that reads as "flat".
    assert H.fmt_pct(None) == "—"


def test_fmt_pcr_plain_two_places():
    assert H.fmt_pcr(0.43) == "0.43"
    assert H.fmt_pcr(None) == "—"
    assert H.fmt_pcr(0) == "—"          # a zero ratio means "not computed"


def test_put_call_tints_amber_only_when_put_heavy():
    assert H.pcr_tone(2.25) == "warn"
    assert H.pcr_tone(1.51) == "warn"
    assert H.pcr_tone(1.50) == "plain"
    assert H.pcr_tone(0.43) == "plain"
    assert H.pcr_tone(None) == "muted"


# ── header strings ───────────────────────────────────────────────────────────
def test_eyebrow_renders_the_stamp_in_eastern_time():
    # 2026-08-17T20:00:00Z is 16:00 ET (EDT, UTC-4).
    assert H.eyebrow("2026-08-17T20:00:00+00:00") == \
        "MARKET STRUCTURE · AUG 17, 2026 · 16:00 ET"


def test_eyebrow_without_a_stamp_says_so_rather_than_inventing_a_time():
    assert H.eyebrow(None) == "MARKET STRUCTURE · AWAITING DATA"
    assert H.eyebrow("not-a-timestamp") == "MARKET STRUCTURE · AWAITING DATA"


def test_regime_headline_words_and_tone():
    word, tone, detail = H.regime_headline({
        "day_spread": -0.25, "day_cyc": -0.41, "day_def": -0.66})
    assert word == "Mixed regime" and tone == "warn"
    assert detail == "cyclicals -0.41% vs defensives -0.66%"


def test_regime_headline_covers_every_band():
    assert H.regime_headline({"day_spread": 1.5})[:2] == ("Strong risk-on regime", "up")
    assert H.regime_headline({"day_spread": 0.5})[:2] == ("Risk-on regime", "up")
    assert H.regime_headline({"day_spread": -0.5})[:2] == ("Risk-off regime", "down")
    assert H.regime_headline({"day_spread": -1.5})[:2] == ("Strong risk-off regime", "down")


def test_regime_headline_cold_cache_is_honest():
    word, tone, detail = H.regime_headline(None)
    assert word == "No regime read" and tone == "muted"
    assert "Refresh" in detail


def test_regime_headline_falls_back_through_the_timeframes():
    # Same day→3d→week fallback the shared rotation banner uses.
    word, _tone, detail = H.regime_headline({"week_spread": 1.2, "week_cyc": 2.0,
                                             "week_def": 0.4})
    assert word == "Strong risk-on regime"
    assert detail == "cyclicals +2.00% vs defensives +0.40%"


def test_regime_headline_missing_legs_render_dashes_not_zeros():
    _w, _t, detail = H.regime_headline({"day_spread": 0.0})
    assert detail == "cyclicals — vs defensives —"


# ── the summary line ─────────────────────────────────────────────────────────
def _sd():
    return [{"kind": "sector", "etf": "XLK"}, {"kind": "sector", "etf": "XLU"},
            {"kind": "industry", "etf": "SMH"}]


def test_summary_line_reports_breadth_weight_and_score():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": -0.5},
              "SMH": {"change_pct": 9.0}}
    line = H.summary_line(_sd(), quotes, {"wpct": 0.70, "score": 7.8})
    # Industries are excluded from breadth — 1 of 2 SECTORS green.
    assert line == "50% green · cap-weighted +0.70% · score 7.8/10"


def test_summary_line_cold_cache_admits_the_gap():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": -0.5}}
    line = H.summary_line(_sd(), quotes, None)
    assert line == "50% green · cap-weighted —"     # no invented weight or score


def test_summary_line_with_no_quotes_is_empty_not_a_zero():
    assert H.summary_line(_sd(), {}, {"wpct": 0.7}) == ""
    assert H.summary_line([], {}, None) == ""


# ── row assembly ─────────────────────────────────────────────────────────────
def test_all_heat_values_flattens_sectors_and_every_industry():
    sectors = [{"sector": "Energy", "day": 1.0, "week": 2.0, "month": 3.0}]
    industries = {"Energy": [{"day": 1.42, "week": 9.10, "month": 11.20}],
                  "Utilities": [{"day": -0.4, "week": 1.0, "month": 2.0}]}
    got = H.all_heat_values(sectors, industries)
    assert len(got) == 3
    # Every column's scale sits inside the range its own readings span.
    for field, lo, hi in (("day", 0.4, 1.42), ("week", 1.0, 9.10),
                          ("month", 2.0, 11.20)):
        assert lo <= H.column_scales(got)[field] <= hi


def test_all_heat_values_survives_a_cold_industries_map():
    sectors = [{"sector": "Energy", "day": 1.0, "week": 2.0, "month": 3.0}]
    assert H.all_heat_values(sectors, None) == sectors
    assert H.all_heat_values(sectors, {"Energy": None}) == sectors


@pytest.mark.parametrize("field", ["day", "week", "month"])
def test_every_sortable_column_has_a_flat_band(field):
    assert field in H.FLAT_BAND
    assert math.isfinite(H.FLAT_BAND[field])
