"""Tests for the pushed Market Regime Console (``market_console``).

The module is a MIRROR of ``webgui/pages/console*.py``; these tests pin the
decisions that would silently drift or silently lie — the score bands, the
"no reading" path, the scale, the ranked share maths, and the two SVG builders.
Cosmetic coordinates are deliberately NOT pinned (a nudge should not turn the
suite red).
"""
import datetime as dt

import pytest

from services.options_svc import market_console as mc


# --- score bands (MIRRORS console.score_band) --------------------------------

@pytest.mark.parametrize("value,band", [
    (73, "positive"), (70, "positive"), (68, "yellow"), (65, "yellow"),
    (60, "olive"), (55, "olive"), (53, "warning"), (35, "warning"),
    (20, "negative"), (0, "negative"), (None, "muted"), ("junk", "muted"),
])
def test_score_band_reproduces_the_tier1_thresholds(value, band):
    assert mc.score_band(value) == band


def test_band_hex_never_returns_none():
    for key in ("positive", "yellow", "olive", "warning", "negative", "muted",
                "nonsense"):
        assert mc.band_hex(key).startswith("#")


# --- hero / delta ------------------------------------------------------------

def test_hero_parts_missing_reads_an_em_dash_not_a_zero():
    """A 0 on a 0-100 score means 'maximally bearish', not 'no reading'."""
    text, hexv = mc.hero_parts(None)
    assert text == "—" and hexv == mc.PALETTE["text_dim"]
    assert mc.hero_parts(57.93)[0] == "58"


def test_delta_parts_sign_drives_glyph_and_colour():
    assert mc.delta_parts(73, 60, "WEEK") == ("▲", "+13 vs WEEK",
                                              mc.PALETTE["positive"])
    assert mc.delta_parts(50, 56, "WEEK") == ("▼", "−6 vs WEEK",
                                              mc.PALETTE["warning"])
    assert mc.delta_parts(50, None, "WEEK") is None
    assert mc.delta_parts(None, 50, "WEEK") is None


# --- meters ------------------------------------------------------------------

def test_meter_row_no_read_is_hatched_never_a_fabricated_neutral():
    row = mc.meter_row("WEEK", None)
    assert row["no_read"] and row["text"] == "—" and row["pct"] == 0.0
    html = mc._meter_html(row)
    assert "NO READ" in html and "cn-hatch" in html
    assert "cn-fill" not in html          # no bar at all, not a zero-width one


def test_meter_row_clamps_and_bands():
    assert mc.meter_row("DAY", 140)["pct"] == 100.0
    assert mc.meter_row("DAY", -5)["pct"] == 0.0
    assert mc.meter_row("DAY", 80)["band"] == "positive"


def test_meters_html_carries_the_shared_ruler():
    html = mc._meters_html([{"caption": "DAY", "value": 50},
                            {"caption": "WEEK", "value": None}])
    for mark in mc.RULER_MARKS:
        assert f">{mark}</span>" in html


# --- segmented + bipolar meters ---------------------------------------------

def test_segmented_cells_round_half_up():
    """round() is banker's — round(4.5)==4 while round(5.5)==6, so a meter whose
    midpoint flips on the parity of the cell count is a surprise nobody wants."""
    assert sum(mc.segmented_cells(0.45)) == 5
    assert sum(mc.segmented_cells(0.55)) == 6
    assert sum(mc.segmented_cells(0.9)) == 9
    assert sum(mc.segmented_cells(None)) == 0
    assert sum(mc.segmented_cells(3.0)) == mc.SEGMENTS


def test_bipolar_geometry_is_a_share_of_the_full_track():
    """``pct`` starts at the centre, so it reaches at most 50% of the track."""
    assert mc.bipolar_geometry(1.63)["side"] == "right"
    assert round(mc.bipolar_geometry(1.63)["pct"], 1) == 32.6
    assert round(mc.bipolar_geometry(0.48)["pct"], 1) == 9.6
    left = mc.bipolar_geometry(-0.69)
    assert left["side"] == "left" and left["text"] == "-0.69"
    assert mc.bipolar_geometry(99)["pct"] == 50.0
    none = mc.bipolar_geometry(None)
    assert none["side"] == "none" and none["text"] == "—"


# --- Signals -----------------------------------------------------------------

def test_signal_rows_colour_each_cell_from_its_own_word():
    """bias/signal carry ``signal_band``'s own vocabularies, not the composite's
    ``bias`` field — so 'Long' must not read amber forever."""
    rows = {r["key"]: r for r in mc.signal_rows("Long", "Strong Bear", 7.0, 6.0)}
    assert rows["bias"]["tone"] == "pos"
    assert rows["signal"]["tone"] == "neg"
    assert rows["yesterday"]["value"] == "6.00" and rows["yesterday"]["tone"] == "warn"
    assert rows["change"]["value"] == "+1.00" and rows["change"]["tone"] == "pos"


def test_signal_rows_without_a_prior_session_are_dashes_not_a_band():
    rows = {r["key"]: r for r in mc.signal_rows("Neutral", "Neutral", 5.04, None)}
    assert rows["yesterday"]["value"] == "—" and rows["yesterday"]["tone"] == "flat"
    assert rows["change"]["value"] == "—" and rows["change"]["tone"] == "flat"


def test_signals_card_counts_only_the_cells_that_actually_read():
    html = mc.signals_card_html(
        mc.signal_rows("Neutral", "Neutral", 5.04, None), {}, None)
    assert "2 READS" in html
    # No LIVE claim in a still frame — freshness is the header's job.
    assert "READS · LIVE" not in html


def test_signals_card_renders_the_three_signed_meters_and_the_divergence():
    html = mc.signals_card_html(
        mc.signal_rows("Neutral", "Neutral", 5.04, 5.16),
        {"roc_3d": -0.69, "roc_5d": -0.66, "z_20d": -0.2266},
        {"high": {"name": "VIX Complex", "score": 8.0},
         "low": {"name": "Market Breadth", "score": 2.0}})
    assert "3D ROC" in html and "5D ROC" in html and "20D Z" in html
    assert "-0.69" in html and "-0.23" in html
    assert "DIVERGENCE · LOW CONVICTION" in html
    assert "VIX Complex 8 vs Market Breadth 2" in html


def test_divergence_bars_are_proportional_and_all_or_nothing():
    bars = mc.divergence_bars({"high": {"name": "A", "score": 10},
                               "low": {"name": "B", "score": 5}})
    assert bars[0][2] == mc.DIVERGENCE_BAR_H
    assert bars[1][2] == round(mc.DIVERGENCE_BAR_H / 2)
    assert mc.divergence_bars({"high": {"name": "A"}, "low": {"name": "B", "score": 5}}) == []
    assert mc.divergence_bars(None) == [] and mc.divergence_text(None) == ""


# --- the sentiment card's scale ---------------------------------------------

def test_sentiment_card_hero_and_meters_share_one_scale():
    """The bug this rebuild fixes: the old ring's centre read the 0-10 composite
    while its own WEEK/MONTH legend read 0-100. Here the hero is 0-100 and the
    0-10 number appears ONLY inside the pill, labelled by the word beside it."""
    html = mc.sentiment_card_html(
        [{"caption": "DAY", "value": 50.4}, {"caption": "WEEK", "value": 57.0},
         {"caption": "MONTH", "value": 53.0}], "Neutral", "5.04", 0.9)
    assert ">50</span>" in html                 # hero, 0-100
    assert ">57</span>" in html and ">53</span>" in html
    assert "NEUTRAL 5.04" in html               # the 0-10 composite, labelled
    assert ">5.0</span>" not in html            # never bare on the 0-100 face
    assert "SCALE 0—100" in html and "90%" in html


def test_trend_card_carries_the_verdict_and_guidance():
    html = mc.trend_card_html(
        [{"caption": "DAY", "value": 58}, {"caption": "WEEK", "value": 80},
         {"caption": "MONTH", "value": 89}], "Neutral", "Neutral",
        "Balance — sell premium (iron condors/straddles), fade extremes.")
    assert ">58</span>" in html and "NEUTRAL" in html
    assert "sell premium" in html
    assert "−31 vs MONTH" in html               # day vs the structural horizon


# --- ranked regime share -----------------------------------------------------

def test_rank_rows_rank_by_share_and_report_change_since_open():
    pts = [{"memberships": {"trending": 0.5, "choppy": 0.3, "crisis": 0.2}},
           {"memberships": {"trending": 0.2, "choppy": 0.5, "crisis": 0.3}}]
    rows = mc.rank_rows(pts)
    assert [r["key"] for r in rows][:3] == ["choppy", "crisis", "trending"]
    assert rows[0]["now"] == 0.5 and abs(rows[0]["change"] - 0.2) < 1e-9


def test_rank_rows_break_ties_on_the_fixed_order_so_repaints_cannot_jitter():
    pts = [{"memberships": {k: 0.2 for k in mc.REGIME_ORDER}}]
    assert [r["key"] for r in mc.rank_rows(pts)] == list(mc.REGIME_ORDER)


def test_session_points_measure_change_from_THIS_sessions_open():
    """A 4h gap is a session boundary; deltas taken across it would compare
    today against whatever yesterday happened to close on."""
    pts = [{"ts": 0, "memberships": {"trending": 0.9}},
           {"ts": 100000, "memberships": {"trending": 0.4}},
           {"ts": 100060, "memberships": {"trending": 0.5}}]
    assert len(mc.session_points(pts)) == 2
    assert abs(mc.rank_rows(pts)[0]["change"] - 0.1) < 1e-9


def test_lead_margin_reports_now_and_the_tightest_of_the_session():
    """`unclear` measures evidence strength, not how close the top two are — so
    without this the headline can be a coin toss and nothing says so."""
    pts = [{"memberships": {"trending": 0.40, "crisis": 0.39}},   # 1pp tightest
           {"memberships": {"trending": 0.50, "crisis": 0.30}}]   # 20pp now
    key, now, tightest = mc.lead_margin(pts)
    assert key == "trending"
    assert abs(now - 0.20) < 1e-9 and abs(tightest - 0.01) < 1e-9
    assert mc.lead_margin([]) == (None, None, None)


def test_callouts_pick_dominant_biggest_and_a_band_waking_from_zero():
    pts = [{"memberships": {"trending": 0.6, "choppy": 0.4, "crisis": 0.0}},
           {"memberships": {"trending": 0.3, "choppy": 0.4, "crisis": 0.1}}]
    c = mc.callouts(pts)
    assert c["dominant"]["key"] == "choppy"
    assert c["biggest_move"]["key"] == "trending"      # −30pp, largest |change|
    assert c["emerging"]["key"] == "crisis"            # 0 -> 10pp
    empty = mc.callouts([])
    assert empty == {"dominant": None, "biggest_move": None, "emerging": None}


def test_a_dormant_band_is_muted_and_says_so():
    """At 0.0% there is no bar length left to carry the meaning, so the state
    rides on the colour, the note and a dashed sparkline instead."""
    assert mc.regime_hex("breakout", 0.0) == mc.PALETTE["regime_breakout_zero"]
    assert mc.regime_hex("breakout", 0.3) == mc.PALETTE["regime_breakout"]
    assert mc.regime_note({"key": "breakout", "now": 0.0}) == mc.ZERO_NOTE
    assert mc.regime_note({"key": "breakout", "now": 0.3}) == "RANGE EXPANSION"


def test_share_table_never_prints_a_raw_contract_key():
    html = mc.share_table_html(
        [{"ts": 1, "memberships": {"choppy": 0.4, "mean_reversion": 0.3,
                                   "trending": 0.3}}])
    assert "WHIPSAW" in html and "BALANCED" in html
    for raw in ("mean_reversion", "choppy", "crisis", "breakout"):
        assert raw not in html


def test_share_table_bars_are_normalised_to_the_leader():
    html = mc.share_table_html(
        [{"ts": 1, "memberships": {"trending": 0.4, "crisis": 0.2}}])
    assert "width:100.0%" in html and "width:50.0%" in html


def test_share_table_waits_rather_than_showing_an_empty_frame():
    assert "Waiting for regime" in mc.share_table_html([])


def test_sparkline_draws_a_dashed_rule_for_a_dead_flat_series():
    """Auto-scaling a constant would amplify floating-point dust into a
    convincing squiggle."""
    flat = mc.sparkline_svg([0.0, 0.0, 0.0], "#fff")
    assert "stroke-dasharray" in flat and "<polyline" not in flat
    live = mc.sparkline_svg([0.1, 0.3, 0.2], "#fff")
    assert "<polyline" in live and "stroke-dasharray" not in live
    assert mc.sparkline_svg([], "#fff").startswith("<svg")


# --- the confidence dial -----------------------------------------------------

def test_dial_full_confidence_draws_a_circle_not_an_empty_arc():
    """A 360-degree arc's endpoints coincide and an SVG arc between identical
    points draws NOTHING — a confidence of 1.0 would render an EMPTY ring."""
    svg = mc.dial_svg(1.0, "Trending")
    assert svg.count("<path") == 0 and "100%" in svg
    assert mc.dial_svg(0.62, "Trending").count("<path") == 2   # halo + value


def test_dial_missing_confidence_is_an_em_dash_not_a_zero():
    for bad in (None, "junk", float("nan"), float("inf")):
        svg = mc.dial_svg(bad, "Unclear")
        assert ">—</text>" in svg and ">0%</text>" not in svg
    assert ">0%</text>" in mc.dial_svg(0.0, "Unclear")   # a real zero says zero


def test_dial_font_family_attribute_is_not_broken_by_quotes():
    """The stack is interpolated into an SVG font-family ATTRIBUTE; a double
    quote inside would terminate it early — which it silently did."""
    assert '"' not in mc.DISPLAY_FONT
    assert f'font-family="{mc.DISPLAY_FONT}"' in mc.dial_svg(0.5, "Trending")


def test_dial_card_reports_the_lead_and_the_intraday_minimum():
    html = mc.dial_card_html(
        {"label": "Trending", "confidence": 0.62},
        [{"ts": 1, "memberships": {"trending": 0.40, "crisis": 0.39}},
         {"ts": 2, "memberships": {"trending": 0.50, "crisis": 0.30}}])
    assert "+20.0 pp" in html and "over Stressed" in html
    assert "1.0 pp" in html and "TRENDING" in html


def test_dial_card_unclear_shows_no_confidence_number():
    html = mc.dial_card_html({"label": "Whipsaw", "confidence": 0.6,
                              "unclear": True}, [])
    assert "—" in html and "60%" not in html


# --- transition line (the one deliberate addition to the mirror) ------------

def test_transition_renders_display_labels_not_raw_keys():
    """A "mean_reversion → trending" reaching a phone is the bug the regime
    rename exists to prevent."""
    line = mc.transition_line(
        {"transition": {"from": "mean_reversion", "to": "trending",
                        "progress": 0.6}})
    assert line == "Balanced → Trending · 60%"
    assert "mean_reversion" not in line


def test_transition_carries_the_direction_word():
    line = mc.transition_line(
        {"direction": 1, "direction_strong": True,
         "transition": {"from": "mean_reversion", "to": "trending"}})
    assert line == "Balanced → Rallying"


def test_transition_absent_renders_nothing():
    assert mc.transition_line({}) == ""
    assert mc.transition_line({"transition": None}) == ""
    assert mc.transition_line("nope") == ""


def test_transition_unknown_key_still_renders():
    assert "nonsense" in mc.transition_line(
        {"transition": {"from": "nonsense", "to": "trending"}})


# --- diagnostic tags ---------------------------------------------------------

def test_split_tag_lifts_the_number_from_either_end():
    assert mc.split_tag("Balanced profile 0.53") == ("BALANCED PROFILE", "0.53")
    assert mc.split_tag("3 failed OR breaks") == ("FAILED OR BREAKS", "3")
    assert mc.split_tag("EMA flat") == ("EMA FLAT", "")
    assert mc.split_tag("") == ("", "")


def test_tags_card_falls_back_to_the_flat_evidence_list():
    """The regime view is RTH-gated, so a stale overnight snapshot really can
    lack ``evidence_detail``."""
    detail = mc.evidence_detail({"evidence": ["EMA flat", "ADX 50 rising"]})
    assert [d["severity"] for d in detail] == ["info", "info"]
    html = mc.tags_card_html(detail)
    assert "2 ACTIVE" in html and "EMA FLAT" in html


def test_tags_card_uses_tier2_severity_not_a_guess_from_the_wording():
    html = mc.tags_card_html([{"text": "9 EMA whipsaws", "severity": "warn"}])
    assert mc.PALETTE["negative"].lstrip("#") in html.lower()


def test_tags_card_with_no_tags_says_so():
    assert "No active tags" in mc.tags_card_html([])


# --- header chips + footer ---------------------------------------------------

def test_as_of_flips_to_stale_past_three_missed_cycles():
    now = dt.datetime(2026, 8, 18, 2, 0, tzinfo=dt.timezone.utc)
    fresh, stale_a = mc.as_of_parts("2026-08-18T01:59:00+00:00", now)
    assert "LIVE" in fresh and stale_a is False
    old, stale_b = mc.as_of_parts("2026-08-18T01:30:00+00:00", now)
    assert "STALE" in old and stale_b is True


def test_as_of_missing_is_no_data_never_a_confident_time():
    text, stale = mc.as_of_parts(None)
    assert text == "NO DATA" and stale is True
    assert mc.as_of_parts("not-a-date")[0] == "NO DATA"


def test_session_chip_never_raises_and_names_a_session():
    assert mc.session_chip(dt.datetime(2026, 8, 15, 10, 0)).startswith("US EQUITIES")
    assert mc.session_chip("nonsense") == "US EQUITIES"


def test_footer_summary_names_the_leader_the_margin_and_the_dormant():
    pts = [{"ts": 1, "memberships": {"trending": 0.4, "crisis": 0.3,
                                     "breakout": 0.0}},
           {"ts": 2, "memberships": {"trending": 0.5, "crisis": 0.3,
                                     "breakout": 0.0}}]
    line = mc.footer_summary(pts)
    assert "Trending leads Stressed by 20.0 pp" in line
    assert "tightest spread today 10.0 pp" in line
    assert "balanced, breakout, whipsaw dormant" in line
    assert mc.footer_summary([]) == "Waiting for the regime classifier"


# --- assembly ----------------------------------------------------------------

def test_console_html_renders_every_section_in_the_pages_order():
    html = mc.console_html({
        "sent_arcs": [{"caption": "DAY", "value": 50}],
        "trend_arcs": [{"caption": "DAY", "value": 58}],
        "bias": "Neutral", "total": "5.04", "confidence": 0.9,
        "trend_short": "Neutral", "trend_verdict": "Neutral",
        "trend_guidance": "Balance.",
        "signal_rows": mc.signal_rows("Neutral", "Neutral", 5.04, 5.16),
        "velocity_values": {"roc_3d": -0.69},
        "divergence_detail": None,
        "regime": {"label": "Trending", "confidence": 0.62},
        "regime_points": [{"ts": 1, "memberships": {"trending": 0.4}}],
        "as_of": None, "session": "US EQUITIES · RTH",
    })
    assert html.index("MARKET READ") < html.index("</div><div class=\"cn-cards\">")
    # Search past the header: its eyebrow lists the same four section words.
    body = html[html.index('class="cn-cards"'):]
    order = ["MARKET SENTIMENT", "MARKET TREND", "SIGNALS", "REGIME IDENTIFIED",
             "DIAGNOSTIC TAGS", "REGIME SHARE", "DOMINANT",
             "NOT FINANCIAL ADVICE"]
    positions = [body.index(s) for s in order]
    assert positions == sorted(positions)


def test_console_html_never_raises_on_an_empty_ctx():
    for ctx in (None, {}, {"regime_points": None, "signal_rows": None}):
        assert mc.console_html(ctx).startswith('<div class="cn">')


def test_console_html_escapes_hostile_text():
    html = mc.console_html({"trend_guidance": "<script>x</script>",
                            "regime": {"label": "<b>x</b>"}})
    assert "<script>" not in html and "&lt;script&gt;" in html
