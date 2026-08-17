"""Pure-transform tests for the Sector Rotation board (/sentiment/rotation).

Everything the board computes — the diverging spread gauge's geometry, the
weight-proportional flow band, the four quadrant panels and the derived prose —
lives in ``pages.rotation_view`` so it can be pinned without a browser.
"""
import pytest
from pages import rotation_view as V


# The live payload shape, trimmed. Numbers are the real 2026-08-17 reading, which
# is also what the reference design was built against.
def _sectors():
    return [
        {"name": "Utilities", "etf": "XLU", "rs_ratio": 100.14,
         "rs_momentum": 102.98, "quadrant": "Leading", "direction": "INTO"},
        {"name": "Real Estate", "etf": "XLRE", "rs_ratio": 100.09,
         "rs_momentum": 102.10, "quadrant": "Leading", "direction": "INTO"},
        {"name": "Materials", "etf": "XLB", "rs_ratio": 99.69,
         "rs_momentum": 101.98, "quadrant": "Improving", "direction": "INTO"},
        {"name": "Financials", "etf": "XLF", "rs_ratio": 99.95,
         "rs_momentum": 100.88, "quadrant": "Improving", "direction": "INTO"},
        {"name": "Technology", "etf": "XLK", "rs_ratio": 100.39,
         "rs_momentum": 99.71, "quadrant": "Weakening", "direction": "FROM"},
        {"name": "Discretionary", "etf": "XLY", "rs_ratio": 99.42,
         "rs_momentum": 97.27, "quadrant": "Lagging", "direction": "FROM"},
    ]


_WEIGHTS = {"XLU": 2.09, "XLRE": 2.12, "XLB": 2.74, "XLF": 13.42,
            "XLK": 32.53, "XLY": 9.94}


# ── the quadrant palette ─────────────────────────────────────────────────────
def test_every_quadrant_has_a_hue_a_chroma_and_a_blurb():
    for q in V.QUADRANT_ORDER:
        assert q in V.QUAD_HUE and q in V.QUAD_CHROMA and q in V.QUAD_BLURB
        assert V.QUAD_BLURB[q].endswith(".")


def test_panel_order_is_the_reading_order_not_alphabetical():
    # Improving → Leading across the top of the rotation, then Lagging →
    # Weakening: the source of the move, then where it is going.
    assert V.QUADRANT_ORDER == ("Improving", "Leading", "Lagging", "Weakening")


def test_quadrant_classes_are_static_tailwind_and_distinct_per_quadrant():
    seen = set()
    for q in V.QUADRANT_ORDER:
        cls = V.QUAD_CLASSES[q]
        for role in ("dot", "title", "mom", "bar", "chip", "seg", "seg_top",
                     "ticker"):
            assert role in cls, (q, role)
            assert "[#" in cls[role] and cls[role].endswith("]")
        seen.add(cls["title"])
    assert len(seen) == len(V.QUADRANT_ORDER)   # no two quadrants share an accent


def test_unknown_quadrant_degrades_to_a_neutral_rather_than_raising():
    cls = V.quad_classes("not-a-quadrant")
    assert cls is V.QUAD_CLASSES[V.FALLBACK_QUADRANT]


# ── the diverging spread gauge ───────────────────────────────────────────────
def test_gauge_places_the_reference_reading_exactly_where_the_design_does():
    g = V.spread_gauge(-1.51, 1.50)
    # −3…+3 scale: (−1.51 + 3) / 6 = 24.83%. The design hard-codes 24.83/25.17.
    assert g["value_pct"] == pytest.approx(24.83, abs=0.01)
    assert g["fill_left_pct"] == pytest.approx(24.83, abs=0.01)
    assert g["fill_width_pct"] == pytest.approx(25.17, abs=0.01)
    assert g["lo_pct"] == pytest.approx(25.0)
    assert g["hi_pct"] == pytest.approx(75.0)
    assert g["tone"] == "down"


def test_gauge_fill_always_spans_between_the_reading_and_zero():
    up = V.spread_gauge(1.20, 1.50)
    assert up["fill_left_pct"] == pytest.approx(50.0)
    assert up["fill_width_pct"] == pytest.approx(up["value_pct"] - 50.0)
    assert up["tone"] == "up"
    dn = V.spread_gauge(-1.20, 1.50)
    assert dn["fill_left_pct"] == pytest.approx(dn["value_pct"])
    assert dn["fill_left_pct"] + dn["fill_width_pct"] == pytest.approx(50.0)


def test_gauge_zero_reading_has_no_fill_and_no_direction():
    g = V.spread_gauge(0.0, 1.50)
    assert g["value_pct"] == pytest.approx(50.0)
    assert g["fill_width_pct"] == pytest.approx(0.0)
    assert g["tone"] == "flat"


def test_gauge_clamps_a_reading_past_the_end_of_the_scale():
    # An off-scale spread must pin to the end, never render outside the track.
    for v in (-99.0, 99.0):
        g = V.spread_gauge(v, 1.50)
        assert 0.0 <= g["value_pct"] <= 100.0
        assert 0.0 <= g["fill_left_pct"] <= 100.0
        assert g["fill_left_pct"] + g["fill_width_pct"] <= 100.0 + 1e-9


def test_gauge_triggers_follow_the_service_threshold_not_a_constant():
    g = V.spread_gauge(0.0, 3.0)
    assert g["lo_pct"] == pytest.approx(0.0) and g["hi_pct"] == pytest.approx(100.0)


def test_gauge_survives_a_missing_spread():
    g = V.spread_gauge(None, 1.50)
    assert g is None


def test_gauge_axis_labels_name_the_scale_and_both_triggers():
    labels = V.gauge_axis(1.50)
    assert [l["text"] for l in labels] == \
        ["−3.0", "−1.50 trigger", "0", "+1.50 trigger", "+3.0"]


# ── the derived prose ────────────────────────────────────────────────────────
def test_trigger_note_reads_fresh_just_past_the_threshold():
    assert V.trigger_note(-1.51, 1.50) == \
        "Just past the trigger — a fresh signal, not an entrenched one."


def test_trigger_note_reads_entrenched_well_past_the_threshold():
    note = V.trigger_note(-3.0, 1.50)
    assert note.startswith("Well past the trigger") and "entrenched" in note
    assert note != V.trigger_note(-1.51, 1.50)


def test_trigger_note_inside_the_band_says_no_signal_fired():
    note = V.trigger_note(-0.4, 1.50)
    assert "±1.50" in note and "trigger" not in note.split("—")[0]


def test_trigger_note_is_symmetric_in_sign():
    assert V.trigger_note(1.51, 1.50) == V.trigger_note(-1.51, 1.50)


def test_trigger_note_without_a_reading_is_empty():
    assert V.trigger_note(None, 1.50) == ""


def test_regime_display_words_and_tone():
    assert V.regime_display("Risk-OFF") == ("Risk-off", "down")
    assert V.regime_display("Risk-ON") == ("Risk-on", "up")
    assert V.regime_display("Mixed") == ("Mixed", "flat")
    assert V.regime_display(None) == ("—", "flat")


def test_regime_sentence_is_plain_prose_not_the_services_log_line():
    # The service text is "Risk-OFF rotation - money rotating into defensives,
    # out of cyclicals" — a log line, with the regime repeated and an ASCII dash.
    s = V.regime_sentence("Risk-OFF", "Risk-OFF rotation - money rotating into "
                                      "defensives, out of cyclicals")
    assert s == "Money is rotating into defensives and out of cyclicals."
    assert V.regime_sentence("Risk-ON", "") == \
        "Money is rotating into cyclicals and out of defensives."


def test_regime_sentence_falls_back_to_the_service_text_when_unrecognised():
    assert V.regime_sentence("Something New", "the service said this") == \
        "the service said this"
    assert V.regime_sentence("Something New", "") == ""


# ── the flow band ────────────────────────────────────────────────────────────
def test_flow_sides_split_on_direction_and_total_the_weights():
    sides = V.flow_sides(_sectors(), _WEIGHTS)
    assert sides["from"]["total"] == pytest.approx(42.47)
    assert sides["into"]["total"] == pytest.approx(20.37)
    assert sides["from"]["count"] == 2 and sides["into"]["count"] == 4


def test_flow_segments_are_ordered_widest_first():
    sides = V.flow_sides(_sectors(), _WEIGHTS)
    assert [s["etf"] for s in sides["from"]["rows"]] == ["XLK", "XLY"]
    assert [s["etf"] for s in sides["into"]["rows"]] == \
        ["XLF", "XLB", "XLRE", "XLU"]


def test_flow_segment_labels_hide_when_the_slice_is_too_thin_to_hold_them():
    # The gate is share of the sector's OWN side, so it needs a side with a
    # genuine long tail — the live "into" side has one (XLF 13.4% against
    # XLU 2.1%, out of 57.5%).
    weights = {"XLF": 13.42, "XLC": 10.16, "XLI": 8.86, "XLV": 8.63,
               "XLE": 4.89, "XLP": 4.61, "XLB": 2.74, "XLRE": 2.12, "XLU": 2.09}
    sectors = [{"name": e, "etf": e, "rs_momentum": 100.0,
                "quadrant": "Leading", "direction": "INTO"} for e in weights]
    rows = V.flow_sides(sectors, weights)["into"]["rows"]
    wide = {r["etf"]: r["wide"] for r in rows}
    assert wide["XLF"] is True and wide["XLP"] is True      # 23.3% / 8.0%
    assert wide["XLB"] is False and wide["XLU"] is False    # 4.8% / 3.6%


def test_a_single_sector_side_is_always_wide_enough_to_label():
    one = [s for s in _sectors() if s["etf"] == "XLK"]
    assert V.flow_sides(one, _WEIGHTS)["from"]["rows"][0]["wide"] is True


def test_flow_labels_state_the_count_in_words():
    sides = V.flow_sides(_sectors(), _WEIGHTS)
    assert sides["from"]["label"] == "Rotating out · 2 sectors"
    assert sides["into"]["label"] == "Rotating in · 4 sectors"


def test_flow_label_is_singular_for_one_sector():
    one = [s for s in _sectors() if s["etf"] == "XLK"]
    sides = V.flow_sides(one, _WEIGHTS)
    assert sides["from"]["label"] == "Rotating out · 1 sector"


def test_flow_side_with_nothing_in_it_is_empty_not_a_divide_by_zero():
    sides = V.flow_sides([s for s in _sectors() if s["direction"] == "INTO"],
                         _WEIGHTS)
    assert sides["from"]["rows"] == [] and sides["from"]["total"] == 0.0
    assert sides["from"]["count"] == 0
    assert sides["from"]["label"] == "Rotating out · 0 sectors"


def test_a_sector_with_no_weight_still_appears_with_a_zero_share():
    rows = V.flow_sides(_sectors(), {})["from"]["rows"]
    assert len(rows) == 2
    assert all(r["weight"] == 0.0 and r["wide"] is False for r in rows)


# ── the quadrant panels ──────────────────────────────────────────────────────
def test_quadrant_panels_cover_all_four_in_reading_order():
    panels = V.quadrant_panels(_sectors(), _WEIGHTS)
    assert [p["name"] for p in panels] == list(V.QUADRANT_ORDER)


def test_quadrant_panel_weight_is_the_sum_of_its_sectors():
    by = {p["name"]: p for p in V.quadrant_panels(_sectors(), _WEIGHTS)}
    assert by["Leading"]["weight"] == pytest.approx(4.21)     # XLU + XLRE
    assert by["Improving"]["weight"] == pytest.approx(16.16)  # XLB + XLF
    assert by["Weakening"]["weight"] == pytest.approx(32.53)
    assert by["Lagging"]["weight"] == pytest.approx(9.94)


def test_quadrant_sectors_are_ranked_by_momentum_descending():
    by = {p["name"]: p for p in V.quadrant_panels(_sectors(), _WEIGHTS)}
    assert [s["etf"] for s in by["Leading"]["sectors"]] == ["XLU", "XLRE"]


def test_chip_bars_scale_against_the_heaviest_sector_on_the_page():
    # XLK is the heaviest at 32.53, so it is the only full-width bar; every
    # other bar is that sector's share of it. One scale across all four panels,
    # not per-panel — otherwise a 2% sector alone in a quadrant would draw the
    # same bar as a 32% one.
    by = {p["name"]: p for p in V.quadrant_panels(_sectors(), _WEIGHTS)}
    assert by["Weakening"]["sectors"][0]["bar_pct"] == pytest.approx(100.0)
    xlf = next(s for s in by["Improving"]["sectors"] if s["etf"] == "XLF")
    assert xlf["bar_pct"] == pytest.approx(13.42 / 32.53 * 100, abs=0.05)
    xlu = next(s for s in by["Leading"]["sectors"] if s["etf"] == "XLU")
    assert xlu["bar_pct"] == pytest.approx(2.09 / 32.53 * 100, abs=0.05)


def test_an_empty_quadrant_still_renders_its_panel():
    only_leading = [s for s in _sectors() if s["quadrant"] == "Leading"]
    by = {p["name"]: p for p in V.quadrant_panels(only_leading, _WEIGHTS)}
    assert by["Lagging"]["sectors"] == []
    assert by["Lagging"]["weight"] == 0.0


def test_panels_survive_missing_weights_without_dividing_by_zero():
    panels = V.quadrant_panels(_sectors(), {})
    for p in panels:
        for s in p["sectors"]:
            assert s["bar_pct"] == 0.0


def test_panels_survive_an_empty_assessment():
    assert V.quadrant_panels([], {}) == V.quadrant_panels(None, None)
    assert len(V.quadrant_panels(None, None)) == 4


# ── formatting ───────────────────────────────────────────────────────────────
def test_momentum_renders_to_two_places():
    assert V.fmt_mom(102.98) == "102.98"
    assert V.fmt_mom(None) == "—"


def test_weight_renders_to_one_place_with_a_percent():
    assert V.fmt_weight(32.53) == "32.5%"
    assert V.fmt_weight(0) == "0.0%"
    assert V.fmt_weight(None) == "—"


def test_spread_uses_a_typographic_minus_to_match_the_mono_face():
    assert V.fmt_spread(-1.51) == "−1.51"
    assert V.fmt_spread(1.51) == "+1.51"
    assert V.fmt_spread(None) == "—"


def test_eyebrow_names_the_benchmark_and_the_date():
    assert V.eyebrow("2026-08-17") == "RRG vs SPY · as of 2026-08-17"
    assert V.eyebrow(None) == "RRG vs SPY · awaiting data"
