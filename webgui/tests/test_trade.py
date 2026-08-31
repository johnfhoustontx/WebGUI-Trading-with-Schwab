"""Tests for the Trade page pure display builders + render API.

The engine orchestration lives in ``services/trade_svc/compute``; this page is a
Tier-3 reader, so only its pure transforms (verdict coloring, momentum/breakdown/
alignment rows) and the ``render`` callable are exercised here.
"""
from pages import trade


def test_verdict_text_class_maps_states():
    assert trade.verdict_text_class("BUY") == "text-[#2e7d32]"
    assert trade.verdict_text_class("buy") == "text-[#2e7d32]"
    assert trade.verdict_text_class("SELL") == "text-[#c62828]"
    assert trade.verdict_text_class("HOLD") == "text-[#f9a825]"
    assert trade.verdict_text_class(None) == "text-[#f9a825]"  # default amber


def test_bias_text_class_maps_states():
    assert trade.bias_text_class("BULLISH") == "text-[#2e7d32]"
    assert trade.bias_text_class("BEARISH") == "text-[#c62828]"
    assert trade.bias_text_class("NEUTRAL") == "text-[#f9a825]"
    assert trade.bias_text_class("") == "text-[#f9a825]"


def test_momentum_rows_formats_and_handles_missing():
    rows = trade.momentum_rows({"rsi": 55.0, "adx": 22.4, "macd_hist": 0.4321,
                                "vwap": 211.0, "relative_volume": 1.34})
    d = dict(rows)
    assert d["RSI"] == "55.0"
    assert d["MACD histogram"] == "0.432"  # 3 decimals
    assert d["VWAP"] == "211.00"
    assert d["Relative Volume"] == "1.34"


def test_momentum_rows_missing_value_is_dash():
    rows = dict(trade.momentum_rows({"rsi": None, "adx": 10.0, "macd_hist": 0.0,
                                     "vwap": None, "relative_volume": 1.0}))
    assert rows["RSI"] == "—"
    assert rows["VWAP"] == "—"


def test_momentum_rows_empty():
    assert trade.momentum_rows({}) == []
    assert trade.momentum_rows(None) == []


def test_breakdown_rows():
    v = {"breakdown": [
        {"factor": "ema_alignment", "weight": 20, "raw_score": 60, "contribution": 12.0},
        {"factor": "adx", "weight": 10, "raw_score": 5, "contribution": 0.49},
    ]}
    rows = trade.breakdown_rows(v)
    assert rows[0] == {"factor": "EMA alignment", "weight": 20,
                       "raw_score": 60, "contribution": 12.0}
    assert rows[1]["factor"] == "ADX"  # snake_case key humanized
    assert rows[1]["contribution"] == 0.5  # rounded to 1 dp


def test_breakdown_rows_empty():
    assert trade.breakdown_rows({}) == []
    assert trade.breakdown_rows(None) == []


def test_alignment_rows():
    ema = {"timeframes": [
        {"timeframe": "daily", "status": "BULLISH", "ema12": 1},
        {"timeframe": "5min", "status": "MIXED"},
    ]}
    assert trade.alignment_rows(ema) == [
        {"timeframe": "daily", "status": "BULLISH"},
        {"timeframe": "5min", "status": "MIXED"},
    ]


def test_fundamentals_rows_formats_percents_and_margin():
    rows = dict(trade.fundamentals_rows({
        "pe_ratio": 28.0, "peg_ratio": 0.8, "rev_growth_ttm": 0.20,
        "eps_growth_ttm": 0.25, "roe": 1.41, "margin_expanding": True,
        "days_to_earnings": None,
    }))
    assert rows["P/E"] == "28.0"
    assert rows["PEG"] == "0.80"
    assert rows["Revenue growth"] == "20.0%"
    assert rows["ROE"] == "141.0%"
    assert rows["Margins"] == "expanding"
    assert "Earnings in" not in rows  # None days-to-earnings omitted


def test_fundamentals_rows_missing_and_contracting():
    rows = dict(trade.fundamentals_rows({
        "pe_ratio": None, "margin_expanding": False, "days_to_earnings": 12,
    }))
    assert rows["P/E"] == "—"
    assert rows["Revenue growth"] == "—"
    assert rows["Margins"] == "contracting"
    assert rows["Earnings in"] == "12 days"


def test_fundamentals_rows_empty():
    assert trade.fundamentals_rows({}) == []
    assert trade.fundamentals_rows(None) == []


def test_render_is_callable():
    assert callable(trade.render)


def test_seed_symbol():
    assert trade.seed_symbol({"symbol": "TSLA"}) == "TSLA"
    assert trade.seed_symbol(None) == "AAPL"
    assert trade.seed_symbol({}) == "AAPL"


def test_should_request():
    # changed symbol fires immediately; an identical repeat within the window is
    # deduped (collapses the blur-then-click double fire); a repeat after the
    # window is a deliberate refresh; empty never fires.
    assert trade.should_request("TSLA", "AAPL", 0.1) is True
    assert trade.should_request("tsla", "TSLA", 0.1) is False
    assert trade.should_request("TSLA", "TSLA", 2.0) is True
    assert trade.should_request("   ", "AAPL", 9.0) is False


def test_should_open_tab():
    # open only when a request is pending AND the cache version advanced past the
    # baseline captured at click time (so a page-load with a stale result never opens).
    assert trade.should_open_tab(pending=True, version=5, baseline=4) is True
    assert trade.should_open_tab(pending=True, version=4, baseline=4) is False
    assert trade.should_open_tab(pending=False, version=9, baseline=4) is False
    assert trade.should_open_tab(pending=True, version=None, baseline=None) is False


_SM = {
    "verdict": "BUY", "score": 0.636, "percentile": 90,
    "expected_fwd": 0.0135, "hit_rate": 0.523, "horizon_days": 20,
    "contributions": [
        {"factor": "mom_12_1", "z": 1.63, "weight": 0.211,
         "contribution": 0.343, "ic": 0.041},
        {"factor": "low_vol", "z": -0.88, "weight": -0.15,
         "contribution": 0.132, "ic": -0.066},
        {"factor": "turnover", "z": 0.40, "weight": 0.10,
         "contribution": 0.040, "ic": None},
    ],
    "model_version": "2026-06-28", "oos_ic": 0.0367, "source": "validated",
}


def test_swing_tilt_maps_verdict_to_ranked_tilt():
    # The validated model's BUY/SELL/HOLD renders as a cross-sectional RANK plus a
    # mild directional tilt — never a bold trade call (the measured edge is thin).
    assert trade.swing_tilt(_SM) == ("90th percentile · slight bullish tilt", "pos")
    assert trade.swing_tilt({"verdict": "SELL", "percentile": 8}) == (
        "8th percentile · slight bearish tilt", "neg")
    assert trade.swing_tilt({"verdict": "HOLD", "percentile": 50}) == (
        "50th percentile · no clear edge", "neutral")
    # lowercase verdicts normalize the same way
    assert trade.swing_tilt({"verdict": "buy", "percentile": 90})[1] == "pos"


def test_swing_tilt_unranked_without_percentile():
    # A missing percentile degrades to "unranked" — the tilt still reads.
    assert trade.swing_tilt({"verdict": "BUY"}) == ("unranked · slight bullish tilt", "pos")
    assert trade.swing_tilt({}) == ("unranked · no clear edge", "neutral")
    assert trade.swing_tilt(None) == ("unranked · no clear edge", "neutral")


def test_tilt_text_class_maps_tones():
    assert trade.tilt_text_class("pos") == "text-[#2e7d32]"
    assert trade.tilt_text_class("neg") == "text-[#c62828]"
    assert trade.tilt_text_class("neutral") == "text-[#f9a825]"
    assert trade.tilt_text_class("nonsense") == "text-[#f9a825]"  # default amber
    assert trade.tilt_text_class(None) == "text-[#f9a825]"


def test_swing_headline_tilt_and_line():
    head = trade.swing_headline(_SM)
    # the percentile lives in `tilt` (the ranked read), NOT in `line`
    assert head["tilt"] == "90th percentile · slight bullish tilt"
    assert head["tone"] == "pos"
    assert "+1.4% excess / 20 days" in head["line"]
    assert "52% beat-SPY" in head["line"]


def test_swing_headline_partial_fields():
    # Missing optional fields are simply omitted from the line (no crash); the
    # ranked tilt still renders.
    head = trade.swing_headline({"verdict": "HOLD", "percentile": 50})
    assert head["tilt"] == "50th percentile · no clear edge"
    assert head["tone"] == "neutral"
    assert head["line"] == ""


def test_swing_headline_none_tolerant():
    # None/empty both degrade to None (the page renders legacy-only in that case).
    assert trade.swing_headline(None) is None
    assert trade.swing_headline({}) is None


def test_swing_contrib_rows_formats_signed_and_ic():
    rows = trade.swing_contrib_rows(_SM)
    assert rows[0] == {"factor": "12-1 momentum", "z": "+1.63", "weight": "+0.211",
                       "contribution": "+0.343", "ic": "+0.041"}
    assert rows[1]["z"] == "-0.88" and rows[1]["weight"] == "-0.150"
    assert rows[1]["ic"] == "-0.066"
    assert rows[2]["ic"] == "—"  # None IC renders as a dash


def test_swing_contrib_rows_empty():
    assert trade.swing_contrib_rows(None) == []
    assert trade.swing_contrib_rows({}) == []
    assert trade.swing_contrib_rows({"contributions": []}) == []


def test_swing_model_meta():
    meta = trade.swing_model_meta(_SM)
    assert meta["version"] == "2026-06-28"
    assert meta["oos_ic"] == "+0.0367"


def test_swing_model_meta_missing_oos():
    meta = trade.swing_model_meta({"model_version": "x"})
    assert meta["version"] == "x" and meta["oos_ic"] == "—"


def test_swing_model_meta_none_tolerant():
    assert trade.swing_model_meta(None) is None


def test_model_staleness_warns_when_old():
    import datetime as dt
    today = dt.date(2026, 9, 1)
    # 2026-06-28 fit is 65 days before 2026-09-01 → stale
    warn = trade.model_staleness("2026-06-28", today=today, threshold_days=60)
    assert "65 days old" in warn and "fit_swing_model.py" in warn


def test_model_staleness_silent_when_fresh_or_unparseable():
    import datetime as dt
    today = dt.date(2026, 7, 10)
    assert trade.model_staleness("2026-06-28", today=today, threshold_days=60) == ""  # 12 days
    assert trade.model_staleness("?", today=today) == ""       # unparseable → no false warn
    assert trade.model_staleness(None, today=today) == ""


def test_days_whole_words():
    assert trade._days(1) == "1 day"      # singular
    assert trade._days(20) == "20 days"
    assert trade._days(0) == "0 days"


def test_humanize_factor_known_and_fallback():
    assert trade.humanize_factor("mom_12_1") == "12-1 momentum"
    assert trade.humanize_factor("rs_vs_sector") == "Relative strength vs sector"
    assert trade.humanize_factor("rsi") == "RSI"            # trader acronym kept verbatim
    assert trade.humanize_factor("growth_quality") == "Growth quality"
    assert trade.humanize_factor("some_new_key") == "Some new key"  # underscores→spaces fallback
    assert trade.humanize_factor("") == ""
    assert trade.humanize_factor(None) == ""


def test_humanize_reason_swaps_leading_key():
    assert trade.humanize_reason("growth_quality (+16)") == "Growth quality (+16)"
    assert trade.humanize_reason("rs_vs_sector (-10)") == "Relative strength vs sector (-10)"
    # a reason that is not "<key> (+score)" is returned unchanged
    assert trade.humanize_reason("Insufficient fundamental data") == "Insufficient fundamental data"
    assert trade.humanize_reason("") == ""
    assert trade.humanize_reason(None) is None




# ── two-sided reads (Phase 2, task 2.5) ──────────────────────────────────────

class TestClearanceRows:
    def test_both_sides_render_even_when_one_is_blocked(self):
        """A blocked side WITH its reasons is a research finding. Rendering
        only the permitted side would make the reader infer the absence."""
        dc = {"market": {"summary": "SPY above a rising 200-DMA"},
              "long": {"state": "cleared", "reasons": ["SPY above a rising 200-DMA"]},
              "short": {"state": "relative_only",
                        "reasons": ["SPY above a rising 200-DMA",
                                    "committed direction is Softening"]}}
        rows = trade.clearance_rows(dc)
        assert [r["side"] for r in rows] == ["Long", "Short"]
        assert rows[1]["state"] == "relative only"
        assert len(rows[1]["reasons"]) == 2

    def test_absent_clearance_renders_nothing_rather_than_a_placeholder(self):
        assert trade.clearance_rows(None) == []
        assert trade.clearance_rows({}) == []

    def test_each_state_maps_to_a_finite_palette_class(self):
        """Data-driven colours map to a FIXED set of static Tailwind classes,
        never a runtime-built arbitrary value (the house rule)."""
        seen = {trade.clearance_text_class(s)
                for s in ("cleared", "relative_only", "blocked", "nonsense", None)}
        assert seen <= set(trade.CLEARANCE_TEXT_CLASSES)


class TestDealerRows:
    def test_a_collected_fresh_row_yields_readable_fields(self):
        ctx = {"collected": True, "stale": False, "gamma_regime": "above",
               "regime_words": "long gamma — dealers damp moves",
               "setup_words": "Grind — charm drift into the close",
               "flip": 306.5, "call_wall": 315.0, "put_wall": 300.0,
               "call_wall_pct": 1.71, "put_wall_pct": -3.13,
               "atm_iv": 27.4, "iv_state": "stable", "net_gex": 4.12e8,
               "summary": "long gamma · call wall 315"}
        rows = trade.dealer_rows(ctx)
        labels = {r["label"] for r in rows}
        assert "Gamma regime" in labels and "Call wall" in labels
        cw = next(r for r in rows if r["label"] == "Call wall")
        assert "315" in cw["value"] and "+1.7" in cw["value"]

    def test_an_uncollected_symbol_yields_no_rows(self):
        assert trade.dealer_rows({"collected": False, "summary": "Not collected"}) == []
        assert trade.dealer_rows(None) == []

    def test_suppressed_walls_are_simply_absent_not_shown_as_none(self):
        """Off-hours the walls are withheld deliberately. Printing 'None' would
        read as a level."""
        ctx = {"collected": True, "stale": False, "gamma_regime": "above",
               "regime_words": "long gamma", "setup_words": "",
               "flip": 306.5, "call_wall": None, "put_wall": None,
               "call_wall_pct": None, "put_wall_pct": None,
               "atm_iv": None, "iv_state": "na", "net_gex": 0.0, "summary": "x"}
        labels = {r["label"] for r in trade.dealer_rows(ctx)}
        assert "Call wall" not in labels and "Put wall" not in labels


class TestPeerRow:
    PEERS = {"sector": "Technology", "rank": 3, "n": 5,
             "strongest": {"symbol": "NVDA", "score": 91},
             "weakest": {"symbol": "INTC", "score": 12},
             "above": {"symbol": "AVGO", "score": 74},
             "below": {"symbol": "MSFT", "score": 66},
             "ranked": [{"symbol": "NVDA", "score": 91}]}

    def test_names_the_placement_in_words(self):
        line = trade.peer_line(self.PEERS, "AAPL")
        assert "3rd of 5" in line and "Technology" in line

    def test_absent_peers_render_nothing(self):
        assert trade.peer_line(None, "AAPL") == ""
        assert trade.peer_line({"ranked": []}, "AAPL") == ""

    def test_peer_chips_carry_every_named_peer(self):
        chips = trade.peer_chips(self.PEERS, "AAPL")
        syms = {c["symbol"] for c in chips}
        assert {"NVDA", "AVGO", "MSFT", "INTC"} <= syms
        assert all(c["role"] for c in chips)


class TestShortGateRows:
    def test_short_gates_render_separately_from_long_ones(self):
        v = {"gates_triggered": ["Below 200EMA: cannot be BUY"],
             "short_gates": ["Squeeze risk (17.1 days to cover): cannot be SELL"]}
        assert trade.gate_rows(v) == ["Below 200EMA: cannot be BUY"]
        assert trade.short_gate_rows(v) == [
            "Squeeze risk (17.1 days to cover): cannot be SELL"]

    def test_a_verdict_without_short_gates_is_fine(self):
        assert trade.short_gate_rows({"gates_triggered": []}) == []
        assert trade.short_gate_rows(None) == []


class TestEarningsCoverageNote:
    def test_an_unlisted_symbol_says_the_date_is_UNKNOWN(self):
        """The gate's silence must not read as an all-clear. Alpha Vantage's
        coverage is measurably patchy (MSFT/AMZN/META absent while AAPL and
        GOOGL are listed on the same cycle), so an unlisted symbol has to say
        so rather than let the reader infer 'no earnings'."""
        note = trade.earnings_note("not_listed", None)
        assert "unknown" in note.lower()
        assert "no earnings" not in note.lower()

    def test_a_covered_symbol_with_nothing_scheduled_can_say_so_plainly(self):
        note = trade.earnings_note("none_scheduled", None)
        assert "none scheduled" in note.lower()

    def test_a_known_date_is_reported_with_its_distance(self):
        assert "12 days" in trade.earnings_note("upcoming", 12)

    def test_no_coverage_information_renders_nothing(self):
        assert trade.earnings_note(None, None) == ""


class TestTradePlanRows:
    PLAN = {
        "symbol": "NVDA", "side": "long", "action": "debit",
        "structure": "call debit spread", "dte_min": 30, "dte_max": 45,
        "rationale": "IV is cheap — pay for convexity rather than sell it.",
        "entry_zone": "pull back toward the 178 flip; avoid entering into the call wall",
        "stop": 173.7, "stop_note": "1.8x ATR — whichever is tighter",
        "target": "+1.6% vs SPY over 20 trading days",
        "short_strike_guidance": "",
        "time_stop_trading_days": 20, "time_stop_date": "2026-09-21",
        "time_stop_note": "Exit or re-underwrite at 20 trading days — past the model's horizon the read is unmodelled.",
        "events": "Earnings: none scheduled in the calendar",
        "what_would_change_it": [],
    }

    def test_every_field_becomes_a_labelled_row(self):
        rows = trade.plan_rows(self.PLAN)
        labels = [r["label"] for r in rows]
        for expected in ("Structure", "Entry zone", "Stop", "Target",
                         "Time stop", "Events"):
            assert expected in labels

    def test_the_structure_row_carries_the_tenor(self):
        row = next(r for r in trade.plan_rows(self.PLAN) if r["label"] == "Structure")
        assert "call debit spread" in row["value"]
        assert "30" in row["value"] and "45" in row["value"]

    def test_the_time_stop_shows_the_date_AND_the_horizon(self):
        row = next(r for r in trade.plan_rows(self.PLAN) if r["label"] == "Time stop")
        assert "20 trading days" in row["value"]
        assert "2026-09-21" in row["value"]

    def test_an_absent_stop_is_omitted_not_rendered_as_none(self):
        plan = dict(self.PLAN, stop=None, stop_note="")
        assert not any(r["label"] == "Stop" for r in trade.plan_rows(plan))

    def test_no_plan_renders_nothing(self):
        assert trade.plan_rows(None) == []

    def test_a_no_trade_plan_renders_what_would_change_it_instead(self):
        plan = dict(self.PLAN, action="none", structure=None,
                    what_would_change_it=["SPY losing its 200-DMA"])
        rows = trade.plan_rows(plan)
        assert not any(r["label"] == "Structure" for r in rows)
        assert trade.plan_headline(plan)[1] == "none"

    def test_the_headline_names_the_side_and_the_action(self):
        text, kind = trade.plan_headline(self.PLAN)
        assert "long" in text.lower() and "debit" in text.lower()
        assert kind == "debit"

    def test_a_relative_plan_says_so_in_the_headline(self):
        plan = dict(self.PLAN, action="relative",
                    structure="pair vs a top-decile name")
        text, kind = trade.plan_headline(plan)
        assert kind == "relative"
        assert "pair" in text.lower()


# ── Phase 4.3: the page says which regime's weights scored ───────────────────
# A verdict scored under regime weights and one scored under the pooled fit are
# different claims about the same symbol. When they can differ, the card has to
# say which it made — otherwise the evidence expander shows contributions whose
# weights the reader cannot account for.

def test_a_named_regime_is_described_in_WORDS_not_its_artifact_key():
    """`highvol` is a dict key. The card says what it means about the tape."""
    note = trade.swing_regime_note({"regime_key": "highvol"})
    assert "volatility" in note.lower()
    assert "highvol" not in note.lower()


def test_each_regime_gets_its_own_description():
    notes = {k: trade.swing_regime_note({"regime_key": k})
             for k in ("trend", "chop", "highvol")}
    assert len(set(notes.values())) == 3


def test_an_unrecognised_key_still_says_something_rather_than_nothing():
    """A fit that grows a fourth regime must not render a blank line."""
    assert trade.swing_regime_note({"regime_key": "crisis"}).strip()


def test_the_pooled_fit_says_so_rather_than_printing_a_key():
    """'all' is an internal artifact key, not a market condition. Printing it
    raw would read as a regime named 'all'."""
    note = trade.swing_regime_note({"regime_key": "all"})
    assert "all" not in note.lower().split()
    assert "regime" in note.lower()


def test_an_artifact_predating_regimes_shows_nothing_rather_than_a_guess():
    assert trade.swing_regime_note({"model_version": "2026-06-28"}) == ""
    assert trade.swing_regime_note(None) == ""


# ── Phase 4: the card states the model's directional exposure ────────────────
# Phase 4 measured this composite at cross-sectional IC +0.16 when the market's
# forward 20 days were up and -0.11 when they were down, with the whole
# asymmetry carried by the volatility factors — and the live artifact puts 48%
# of its absolute weight there. A BUY from this model therefore skews toward
# high-beta names, which is exposure that reverses in exactly the drawdown a
# 1-8 week position cannot sit through. The card has to say so.

def test_the_exposure_note_states_the_share():
    note = trade.swing_exposure_note({"risk_share": 0.476})
    assert "48%" in note or "47.6%" in note


def test_a_material_share_carries_the_reversal_caveat():
    note = trade.swing_exposure_note({"risk_share": 0.476})
    assert "revers" in note.lower() or "falls" in note.lower()


def test_a_small_share_reports_the_number_without_the_caveat():
    note = trade.swing_exposure_note({"risk_share": 0.04})
    assert note
    assert "revers" not in note.lower() and "falls" not in note.lower()


def test_an_unknown_share_says_nothing_rather_than_implying_zero():
    """None means the factor registry was unreachable, not that the model has
    no exposure. Printing '0%' there would be a confident wrong answer."""
    assert trade.swing_exposure_note({"risk_share": None}) == ""
    assert trade.swing_exposure_note({}) == ""
    assert trade.swing_exposure_note(None) == ""


# ── Phase 6: is the live edge holding? ───────────────────────────────────────
# The monitor reads the recommendation journal. Two traps it must not fall into,
# both of which would produce a confident number from nothing:
#   * a young journal has too few labelled rows to correlate anything, and that
#     is "no measurement", not "a thin edge";
#   * the live POOLED statistic is not the artifact's per-date cross-sectional
#     OOS IC, so printing them side by side as though they were comparable would
#     manufacture a decay finding out of a units mismatch.

_LIC_OK = {
    "status": "ok", "n_labelled": 64, "min_required": 20,
    "pooled_ic": 0.081, "pooled_ic_beta_adj": -0.004,
    "by_date_ic": None, "comparable_to_artifact": False,
    "ic_market_up": 0.14, "ic_market_down": -0.09,
    "artifact_oos_ic": 0.0206, "decay": None,
    "long": {"n": 30, "mean_fwd": 0.011, "hit_rate": 0.5, "ic": 0.05},
    "short": {"n": 18, "mean_fwd": -0.004, "hit_rate": 0.44, "ic": 0.02},
    "horizon_days": 20,
}


def test_live_ic_line_reports_the_pooled_reading():
    line = trade.live_ic_line(_LIC_OK)
    assert "+0.081" in line or "0.081" in line


def test_it_says_the_pooled_number_is_NOT_the_artifacts_statistic():
    """Different units: a pooled correlation over all readings versus a mean of
    per-date cross-sectional correlations."""
    line = trade.live_ic_line(_LIC_OK).lower()
    assert "not" in line or "pooled" in line


def test_a_young_journal_reports_how_far_off_a_reading_is():
    line = trade.live_ic_line({"status": "insufficient", "n_labelled": 3,
                               "min_required": 20})
    assert "3" in line and "20" in line
    assert "0.0" not in line          # no number that could read as an IC


def test_no_monitor_block_renders_nothing():
    assert trade.live_ic_line(None) == ""
    assert trade.live_ic_line({}) == ""


def test_the_beta_split_is_surfaced_because_that_is_the_whole_question():
    line = trade.live_ic_split_line(_LIC_OK)
    assert "+0.14" in line and "-0.09" in line


def test_the_beta_adjusted_reading_is_shown_when_present():
    line = trade.live_ic_split_line(_LIC_OK).lower()
    assert "beta" in line


def test_the_split_line_is_empty_without_a_reading():
    assert trade.live_ic_split_line({"status": "insufficient"}) == ""


def test_decay_is_only_claimed_from_the_COMPARABLE_statistic():
    assert trade.live_ic_decay_note(_LIC_OK) == ""
    comparable = dict(_LIC_OK, by_date_ic=0.004, comparable_to_artifact=True,
                      decay=-0.0166)
    assert "decay" in trade.live_ic_decay_note(comparable).lower()


#############################################
# The wait: a 96-second analysis must not look like a hang
#############################################

def test_analyzing_label_counts_up():
    assert trade.analyzing_label("COIN", 0) == "Analyzing COIN… 0s"
    assert trade.analyzing_label("COIN", 41.6) == "Analyzing COIN… 41s"


def test_analyzing_label_says_so_once_it_runs_long_rather_than_going_quiet():
    """Past the typical duration the wait needs a different word, or the reader
    is left deciding for themselves whether it has died. It must NOT claim
    failure -- the analysis genuinely does take this long sometimes."""
    txt = trade.analyzing_label("COIN", trade.TYPICAL_ANALYZE_SEC + 20)
    assert "COIN" in txt and "s" in txt
    assert txt != trade.analyzing_label("COIN", 5)
    assert "fail" not in txt.lower() and "error" not in txt.lower()


def test_analyzing_label_survives_a_missing_symbol_or_clock():
    for sym, sec in (("", 10), (None, 10), ("COIN", None), ("COIN", -1)):
        out = trade.analyzing_label(sym, sec)
        assert isinstance(out, str) and out


def test_the_analyze_backstop_outlasts_a_real_analysis():
    """MEASURED: a COIN analysis took 96s end to end on 2026-08-31.

    busy.BUSY_TIMEOUT_SEC is 30s -- sized for the Simulator's ~19s fetch -- so
    the spinner vanished at t=30 and left an empty panel for another 66 seconds.
    The operator reported it as "did not return anything", which is exactly what
    it looked like. The page's own backstop must clear the work it actually
    waits for."""
    from pages import busy
    assert trade.ANALYZE_TIMEOUT_SEC > 96, \
        "the backstop must outlast a measured analysis"
    assert trade.ANALYZE_TIMEOUT_SEC > busy.BUSY_TIMEOUT_SEC, \
        "the shared default is too short for this page and must be overridden"
