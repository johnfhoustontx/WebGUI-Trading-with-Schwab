"""The Position card's recommendation.

The card used to lead with a RANK and refuse to name an action, because the
measured edge is thin. It now leads with the action the user asked for — so the
honesty has to move into the recommendation rather than disappear with the
rank: a confidence word derived from the band's own hit rate, the exposure
caveat, and the rank retained as an informational line underneath.

The thing these tests exist to prevent is a confident green BUY over a coin
flip, and a "Sell short" on a side the tape never cleared.
"""
import pytest

from pages import terminal_theme as T
from pages import trade_terminal as tt


def _a(verdict="BUY", pct=90, hit=0.5268, exp=0.0157, state="cleared",
       side="long", action="debit", structure="call debit spread",
       risk_share=0.476, **kw):
    """An analysis payload shaped like the live one."""
    out = {
        "symbol": "MU",
        "swing_model": {"verdict": verdict, "percentile": pct, "hit_rate": hit,
                        "expected_fwd": exp, "horizon_days": 20,
                        "risk_share": risk_share},
        "direction_clearance": {
            "long": {"state": state if side == "long" else "cleared",
                     "reasons": []},
            "short": {"state": state if side == "short" else "relative_only",
                      "reasons": ["SPY above a rising 200-DMA"]}},
        "trade_plan": {"side": side, "action": action, "structure": structure},
    }
    out.update(kw)
    return out


class TestTheActionIsTheHeadline:
    def test_a_cleared_long_says_buy(self):
        r = tt.recommendation(_a())
        assert r["action"] == "Buy"
        assert r["action_class"] == T.POS

    def test_a_cleared_short_says_sell_short(self):
        r = tt.recommendation(_a(verdict="SELL", pct=10, side="short",
                                 state="cleared", hit=0.4377))
        assert r["action"] == "Sell short"
        assert r["action_class"] == T.NEG

    def test_a_relative_only_short_does_NOT_say_sell_short(self):
        """The trap. A bottom-band name is predicted to LAG the index, not to
        fall, and the tape has refused a directional short — rendering that as
        "Sell short" is the single most expensive thing this card could do."""
        r = tt.recommendation(_a(verdict="SELL", pct=10, side="short",
                                 state="relative_only", hit=0.4377))
        assert "short" not in r["action"].lower() or "pair" in r["action"].lower()
        assert r["action_class"] == T.WARN
        assert "s&p" in r["detail"].lower() or "index" in r["detail"].lower()

    def test_a_relative_only_long_says_pair_it(self):
        r = tt.recommendation(_a(state="relative_only"))
        assert "pair" in r["action"].lower()
        assert r["action_class"] == T.WARN

    def test_a_blocked_short_says_stand_aside(self):
        r = tt.recommendation(_a(verdict="SELL", pct=10, side="short",
                                 state="blocked", action="none"))
        assert r["action"] == "Stand aside"
        assert r["action_class"] == T.OFF

    def test_the_middle_band_says_no_trade_rather_than_hold(self):
        """"Hold" reads as advice about a position you own. The model has no
        opinion here at all, which is a different statement."""
        r = tt.recommendation(_a(verdict="HOLD", pct=50, side=None,
                                 action="none", structure=None))
        assert r["action"] == "No trade"
        assert r["action_class"] == T.DIM

    def test_no_model_reading_declines_to_recommend(self):
        r = tt.recommendation({"symbol": "X"})
        assert r["action"] == "No recommendation"
        assert r["action_class"] == T.OFF
        assert r["rank_line"] == ""

    def test_it_never_raises_on_a_partial_payload(self):
        for payload in ({}, None, {"swing_model": {}},
                        {"swing_model": {"verdict": "BUY"}},
                        {"swing_model": {"verdict": "BUY"}, "trade_plan": {}}):
            assert tt.recommendation(payload)["action"]


class TestTheDetailSaysWhatToDo:
    def test_a_cleared_long_names_the_structure_from_the_plan(self):
        r = tt.recommendation(_a(structure="call debit spread"))
        assert "call debit spread" in r["detail"]

    def test_a_credit_action_reads_as_selling_premium(self):
        r = tt.recommendation(_a(action="credit",
                                 structure="put credit spread"))
        assert "put credit spread" in r["detail"]

    def test_a_missing_structure_still_gives_an_instruction(self):
        r = tt.recommendation(_a(structure=None, action="none"))
        assert len(r["detail"]) > 20


class TestConfidenceIsHonest:
    """The card now names an action, so it has to name how well that action has
    worked. The hit rate is "how often this band beat the S&P", so the edge is
    its DISTANCE from a coin flip — which for a short band means a LOW hit rate
    is a strong reading, not a weak one."""

    def test_a_top_band_near_a_coin_flip_reads_low(self):
        assert tt.recommendation(_a(hit=0.5268))["confidence"] == "Low"

    def test_a_bottom_band_lagging_often_is_NOT_penalised_for_it(self):
        r = tt.recommendation(_a(verdict="SELL", pct=10, side="short",
                                 state="cleared", hit=0.4377))
        assert r["confidence"] == "Moderate"

    def test_a_true_coin_flip_reads_very_low(self):
        assert tt.recommendation(_a(hit=0.502))["confidence"] == "Very low"

    def test_the_confidence_note_states_the_actual_hit_rate(self):
        note = tt.recommendation(_a(hit=0.5268))["confidence_note"]
        assert "53%" in note

    def test_an_unknown_hit_rate_does_not_invent_confidence(self):
        r = tt.recommendation(_a(hit=None))
        assert r["confidence"] == "Unknown"
        assert "%" not in r["confidence_note"]


class TestTheRankSurvivesAsInformation:
    def test_the_rank_line_carries_the_band_and_the_calibrated_stats(self):
        line = tt.recommendation(_a())["rank_line"]
        assert "90th" in line
        assert "+1.6%" in line
        assert "53%" in line

    def test_the_rank_line_does_not_claim_a_rank_among_todays_names(self):
        line = tt.recommendation(_a())["rank_line"].lower()
        assert "percentile" not in line
        assert "cross-section" not in line

    def test_an_unranked_reading_has_no_rank_line(self):
        assert tt.recommendation(_a(pct=None, hit=None, exp=None))["rank_line"] == ""


class TestTheCaveatTravelsWithTheRecommendation:
    def test_the_exposure_share_is_disclosed_on_the_recommendation(self):
        """It used to sit on the Evidence screen. A card that only ranked could
        afford that; a card that says "Buy" cannot."""
        assert "48%" in tt.recommendation(_a(risk_share=0.476))["caveat"]

    def test_an_unknown_share_says_nothing_rather_than_implying_zero(self):
        assert tt.recommendation(_a(risk_share=None))["caveat"] == ""

    def test_a_no_trade_recommendation_carries_no_exposure_caveat(self):
        """There is no exposure to caveat when nothing is being recommended."""
        r = tt.recommendation(_a(verdict="HOLD", pct=50, side=None,
                                 action="none", structure=None))
        assert r["caveat"] == ""


class TestTheTwoHeadlinesShareOneCasing:
    """The Short Term card said "Buy" and the Long Term card said "BUY" — the
    same word, two conventions, side by side on one screen.

    Sentence case wins because only one of the two is a single word: the
    recommendation carries phrases ("Pair short", "Stand aside", "No
    recommendation"), and those set in caps at 30px read as shouting and wrap.
    """

    def test_the_long_term_verdict_is_sentence_case(self):
        assert tt.verdict_word("BUY") == "Buy"
        assert tt.verdict_word("sell") == "Sell"
        assert tt.verdict_word("Hold") == "Hold"

    def test_an_absent_verdict_is_a_dash_not_an_empty_headline(self):
        assert tt.verdict_word(None) == "—"
        assert tt.verdict_word("") == "—"

    def test_both_cards_agree_on_the_word_for_the_same_call(self):
        assert tt.recommendation(_a())["action"] == tt.verdict_word("BUY")

    def test_neither_headline_shouts(self):
        """The rule, stated so a future edit to either card trips it."""
        words = [tt.verdict_word(v) for v in ("BUY", "HOLD", "SELL")]
        words += [tt.recommendation(_a(**kw))["action"] for kw in (
            {},
            {"state": "relative_only"},
            {"verdict": "SELL", "pct": 10, "side": "short",
             "state": "cleared", "hit": 0.4377},
            {"verdict": "SELL", "pct": 10, "side": "short",
             "state": "relative_only", "hit": 0.4377},
            {"verdict": "SELL", "pct": 10, "side": "short",
             "state": "blocked", "action": "none"},
        )]
        shouted = [w for w in words if len(w) > 3 and w == w.upper()]
        assert shouted == [], shouted


class TestTheRenameIsComplete:
    """A half-done rename is the failure mode: the panel title changes and the
    prose around it keeps naming the old one, so the page contradicts its own
    tooltips and the nav guide."""

    def _src(self, rel):
        import pathlib as _p
        return (_p.Path(__file__).resolve().parents[1]
                / rel).read_text(encoding="utf-8")

    def test_the_overview_panels_carry_the_new_titles(self):
        src = self._src("pages/trade_overview.py")
        assert 'ui.label("Short Term")' in src
        assert 'ui.label("Long Term")' in src
        assert 'ui.label("Position")' not in src
        assert 'ui.label("Investor")' not in src

    def test_no_user_facing_string_still_says_the_old_card_names(self):
        """Engine keys (`position_verdict`, `investor_verdict`) and the dead
        legacy page keep their names — this checks the PROSE."""
        import re
        for rel in ("pages/trade_overview.py", "pages/trade_evidence.py",
                    "pages/trade_help.py"):
            src = self._src(rel)
            for m in re.finditer(r'"([^"]*(?:Position|Investor)[^"]*)"', src):
                s = m.group(1)
                assert "_" in s, (rel, s)      # an engine key, not prose

    def test_the_nav_guide_describes_the_screens_that_exist(self):
        src = self._src("page_help.py")
        i = src.index('"/trade": """')
        guide = src[i:src.index('"""', i + 20)]
        assert "Short Term" in guide and "Long Term" in guide
        # The guide described the pre-Signal-Desk single page for months.
        assert "Legacy heuristic" not in guide
        assert "Rank Board" in guide


class TestTheTwoHeadlinesShareOneColour:
    """Measured live: the Short Term "Buy" rendered rgb(52,211,153) and the
    Long Term "Buy" rgb(46,125,50) — the same word, the same call, two greens.
    The second came from `pages.trade`'s LOCAL palette, whose own comment says
    its hexes are deliberately darker than the theme's. That palette belongs to
    the old light-background page, not to the Signal Desk."""

    def test_buy_is_the_same_class_on_both_cards(self):
        assert tt.verdict_class("BUY") == tt.recommendation(_a())["action_class"]

    def test_sell_is_the_same_class_on_both_cards(self):
        rec = tt.recommendation(_a(verdict="SELL", pct=10, side="short",
                                   state="cleared", hit=0.4377))
        assert tt.verdict_class("SELL") == rec["action_class"]

    def test_every_verdict_maps_into_the_terminal_palette(self):
        for v in ("BUY", "HOLD", "SELL", "", None):
            assert tt.verdict_class(v) in T.STATE_TEXT.split()

    def test_hold_is_the_caution_colour_not_the_absent_one(self):
        assert tt.verdict_class("HOLD") == T.WARN


class TestTheLongTermCardAlsoStatesConfidence:
    """It cannot mean what the Short Term card's means. That one is a
    BACKTESTED hit rate; this card is a weighted scorecard that was never
    tested against forward returns, so there is no hit rate to quote.

    What it CAN say is how decisively the scorecard cleared its own verdict
    boundary (+/-40), on the range actually reachable (+/-85, because
    earnings trajectory's 15 points can never score). The note has to say
    which of the two it is, or the chip borrows credibility it has not got.
    """

    def _iv(self, score, breakdown=True):
        return {"verdict": "BUY" if score >= 40 else
                           "SELL" if score <= -40 else "HOLD",
                "score": score,
                "breakdown": [{"factor": "valuation", "contribution": 1.0}]
                             if breakdown else []}

    def test_a_score_well_past_the_boundary_reads_moderate(self):
        assert tt.investor_confidence(self._iv(75))[0] == "Moderate"

    def test_a_score_just_past_the_boundary_reads_low(self):
        assert tt.investor_confidence(self._iv(58))[0] == "Low"

    def test_a_hold_that_never_cleared_the_boundary_reads_very_low(self):
        assert tt.investor_confidence(self._iv(20))[0] == "Very low"

    def test_the_short_side_is_symmetric(self):
        assert (tt.investor_confidence(self._iv(-75))[0]
                == tt.investor_confidence(self._iv(75))[0])

    def test_no_fundamentals_is_unknown_not_very_low(self):
        """"Very low confidence" is a claim about a reading. No reading was
        taken — the fundamentals never arrived."""
        assert tt.investor_confidence(self._iv(0, breakdown=False))[0] == "Unknown"
        assert tt.investor_confidence(None)[0] == "Unknown"
        assert tt.investor_confidence({"verdict": "HOLD"})[0] == "Unknown"

    def test_the_note_does_not_borrow_the_other_card_credibility(self):
        note = tt.investor_confidence(self._iv(58))[1].lower()
        assert "not" in note and ("backtest" in note or "tested" in note)

    def test_the_note_explains_the_reachable_range(self):
        assert "85" in tt.investor_confidence(self._iv(58))[1]

    def test_both_cards_draw_confidence_from_ONE_vocabulary(self):
        """The chip palette maps a finite set of words. A card inventing its
        own word would fall through to the neutral chip silently."""
        long_words = {tt.investor_confidence(self._iv(s))[0]
                      for s in (75, 58, 20, -75)}
        long_words.add(tt.investor_confidence(None)[0])
        short_words = {tt.recommendation(_a(hit=h))["confidence"]
                       for h in (0.4377, 0.5268, 0.502, None)}
        assert long_words <= {"Moderate", "Low", "Very low", "Unknown"}
        assert short_words <= {"Moderate", "Low", "Very low", "Unknown"}
