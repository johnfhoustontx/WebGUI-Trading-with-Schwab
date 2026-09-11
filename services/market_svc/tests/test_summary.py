import datetime as dt
from zoneinfo import ZoneInfo

from services.market_svc import compute

_CT = ZoneInfo("America/Chicago")
_OPEN = dt.datetime(2026, 9, 10, 10, 0, tzinfo=_CT)      # Thursday, session open
_PRE = dt.datetime(2026, 9, 10, 7, 0, tzinfo=_CT)        # before the bell


def _composite(**trend):
    return {"live": {"composite": {"total_score": "3.98", "bias": "Cautious"}},
            "derived": {"size": "0.85x", "bias": "Cautious", "signal": "Bearish",
                        "trend": {"state": "lack_of_bearishness",
                                  "smoothed_score": 38.6, **trend}}}


def _regime(**over):
    return {"label": "Whipsaw", "committed_label": "choppy",
            "confidence": 0.62, **over}


def _row(sym, trend, excess, day_pct=None, day_excess=None):
    return {"symbol": sym, "raw": {"trend": trend, "excess": excess},
            "day_pct": day_pct, "day_excess": day_excess}


def _bullbear(benchmark=0.4):
    return {"benchmark_day_pct": benchmark, "levels": {"sector": [
        _row("XLK", 0.3, 0.2, day_pct=-0.5, day_excess=-0.9),   # quarter RL, today FL
        _row("XLU", -0.2, 0.1, day_pct=0.8, day_excess=0.4),    # quarter FL, today RL
        _row("XLE", -0.1, -0.3, day_pct=1.1, day_excess=0.7),   # quarter FLag, today RL
    ]}}


def test_packet_carries_the_six_readings_in_the_screens_words():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert p["sentiment"] == {"composite": 3.98}
    assert p["trend"] == {"word": "Gliding", "score": 38.6}
    assert (p["bias"], p["signal"], p["size"]) == ("Cautious", "Bearish", "0.85x")
    assert p["regime"] == {"word": "Whipsaw", "confidence": 0.62}


def test_packet_counts_bullbear_on_today_once_the_bell_has_rung():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert p["bullbear"]["horizon"] == "today"
    assert p["bullbear"]["counts"]["rising_leading"] == 2
    assert p["bullbear"]["counts"]["falling_lagging"] == 1


def test_packet_counts_bullbear_on_the_quarter_before_the_bell():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_PRE)
    assert p["bullbear"]["horizon"] == "quarter"
    assert p["bullbear"]["counts"]["rising_leading"] == 1


def test_packet_counts_the_quarter_when_no_benchmark_move_exists():
    """A dead proxy mid-session leaves benchmark_day_pct None - the Desk strip's
    rule: no benchmark, no 'today'."""
    p = compute.build_summary_packet(_composite(), _regime(),
                                     _bullbear(benchmark=None), now=_OPEN)
    assert p["bullbear"]["horizon"] == "quarter"


def test_packet_withholds_confidence_on_an_unclear_sample():
    p = compute.build_summary_packet(_composite(), _regime(unclear=True), {},
                                     now=_OPEN)
    assert p["regime"]["confidence"] is None
    assert p["regime"]["word"] == "Whipsaw"


def test_packet_quotes_no_prices():
    """Index moves, vol quotes and sector movers were dropped on purpose: a
    sentence quoting them is stale between refreshes."""
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert set(p) == {"sentiment", "trend", "bias", "signal", "size",
                      "regime", "bullbear"}


def test_packet_from_cold_caches_is_all_absent_never_neutral():
    p = compute.build_summary_packet({}, {}, {}, now=_OPEN)
    assert p["sentiment"]["composite"] is None and p["trend"]["word"] is None
    assert p["bias"] is None and p["signal"] is None
    assert p["regime"]["word"] == "Unclear"
    assert p["bullbear"] == {"horizon": None, "counts": {}, "totals": {}}


def _packet(**over):
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    p.update(over)
    return p


def test_fingerprint_ignores_movement_below_display_resolution():
    a = _packet()
    b = _packet(sentiment={"composite": 4.10})               # 3.98 -> 4.10: both 4.0
    b["trend"] = {"word": "Gliding", "score": 39.9}          # 38.6 -> 39.9: both 40
    assert compute.summary_fingerprint(a) == compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_word_changes():
    a = _packet()
    for key, val in (("bias", "Neutral"), ("signal", "Neutral")):
        assert compute.summary_fingerprint(a) != compute.summary_fingerprint(
            _packet(**{key: val})), key
    b = _packet()
    b["regime"] = {"word": "Balanced", "confidence": 0.62}
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_number_crosses_its_step():
    a = _packet()
    b = _packet(sentiment={"composite": 4.40})               # 4.0 -> 4.5
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_sector_changes_quadrant():
    a = _packet()
    b = _packet()
    b["bullbear"] = {"horizon": "today",
                     "counts": {**a["bullbear"]["counts"], "rising_leading": 3,
                                "falling_lagging": 0}}
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_nothing_to_summarize_has_no_fingerprint():
    """Cold caches must not trigger a paid call to describe nothing."""
    assert compute.summary_fingerprint(
        compute.build_summary_packet({}, {}, {}, now=_OPEN)) is None
    assert compute.summary_fingerprint(None) is None


class _Msg:
    def __init__(self, text, stop="end_turn"):
        self.content = [type("B", (), {"text": text, "type": "text"})()]
        self.stop_reason = stop


def _client(text, stop="end_turn", seen=None):
    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                if seen is not None:
                    seen.update(kw)
                return _Msg(text, stop)
    return _C()


def _facts_text(packet=None):
    """The packet's facts, joined as written — what a faithful reply contains."""
    return " ".join(compute.summary_facts(packet or _packet()))


def _with_facts(posture, packet=None):
    return f"{_facts_text(packet)} {posture}"


# ── the facts are stated by the code, not by the model ───────────────────────
# Four reworded facts in four sentences on 2026-09-10: the trend ("real weight
# behind the slide" for a trend whose sellers are absent), a spread's direction,
# the sector counts, then "absent buyers" for that same trend. Now the code
# writes every factual statement; the model may only join them and add the
# posture, and a reply that changes a fact is withheld.

def test_the_facts_state_each_reading_in_plain_english():
    assert compute.summary_facts(_packet()) == [
        "Investors are growing complacent, which this model reads as a warning.",
        "The model suggests trading smaller than usual.",
        "Prices are drifting lower, but sellers are not pushing them.",
        "The market is choppy: lots of movement but no progress.",
        "Today, 2 of the 3 sectors are rising and 1 is falling, and 2 are "
        "beating the S&P 500.",
    ]


def test_every_word_the_screen_can_show_has_its_fact():
    for word in compute._TREND_WORDS.values():
        assert compute._TREND_FACTS[word].endswith("."), word
    assert "sellers are not pushing" in compute._TREND_FACTS["Gliding"]
    assert "heavy selling" in compute._TREND_FACTS["Diving"]
    for word, fact in compute._REGIME_FACTS.items():
        assert fact == "" if word == "Unclear" else fact.endswith("."), word
    assert all(f.endswith(".") for f in compute._SENTIMENT_FACTS.values())


def test_no_fact_carries_punctuation_the_model_rewrites():
    """A fact must survive the word-for-word check, so it may not carry
    punctuation the model is known to change. Measured on prod 2026-09-11: the
    Circling fact's semicolon came back as a comma in 3 of 3 live replies, and
    every summary written while the trend read Circling was withheld - the Desk
    held one sentence for hours. Apostrophes and quotes: a curly one in the
    reply fails the check the same way."""
    facts = [*compute._SENTIMENT_FACTS.values(), *compute._TREND_FACTS.values(),
             *compute._REGIME_FACTS.values()]
    for fact in facts:
        for mark in (";", "'", '"', "‘", "’", "“", "”"):
            assert mark not in fact, (mark, fact)
    assert "no clear direction" in compute._TREND_FACTS["Circling"]
    assert "balanced" in compute._TREND_FACTS["Circling"]


def test_absent_readings_state_no_fact():
    """An absent reading says nothing - never a neutral stand-in. 'Unclear' is
    the regime's word for having no reading, so it states nothing either."""
    assert compute.summary_facts(
        compute.build_summary_packet({}, {}, {}, now=_OPEN)) == []
    assert compute.summary_facts(None) == []


def test_the_size_fact_reads_the_multiplier_without_quoting_it():
    f = compute._size_fact
    assert f("0.85x") == "The model suggests trading smaller than usual."
    assert f("1.00x") == "The model suggests trading at normal size."
    assert f("1.25x") == "The model suggests trading larger than usual."
    assert f(None) == "" and f("wat") == ""


def test_the_sector_fact_uses_the_ready_made_totals_and_grammar():
    f = compute._sector_fact
    assert f("today", _TOTALS_21H) == (
        "Today, 2 of the 11 sectors are rising and 9 are falling, and 6 are "
        "beating the S&P 500.")
    assert f("quarter", {"sectors": 11, "rising": 1, "falling": 10,
                         "beating_sp500": 1, "trailing_sp500": 10}) == (
        "Over the quarter, 1 of the 11 sectors is rising and 10 are falling, "
        "and 1 is beating the S&P 500.")
    assert f(None, {}) == ""


def test_the_fact_check_finds_a_reworded_fact():
    facts = compute.summary_facts(_packet())
    assert compute._missing_fact(_with_facts("Stay defensive."), facts) is None
    reworded = _facts_text().replace("sellers are not pushing them",
                                     "buyers are absent")
    assert compute._missing_fact(reworded, facts) == (
        "Prices are drifting lower, but sellers are not pushing them.")


def test_a_fact_joined_mid_sentence_still_counts_as_stated():
    """Joining may lower-case a fact's first letter and swap its full stop for
    a comma or 'and' - the words themselves must survive."""
    facts = compute.summary_facts(_packet())
    joined = ("Investors are growing complacent, which this model reads as a "
              "warning, and "
              "the model suggests trading smaller than usual; prices are "
              "drifting lower, but sellers are not pushing them. The market is "
              "choppy: lots of movement but no progress. Today, 2 of the 3 "
              "sectors are rising and 1 is falling, and 2 are beating the "
              "S&P 500. Stay defensive.")
    assert compute._missing_fact(joined, facts) is None


def test_a_reply_that_changes_a_fact_is_not_published(caplog):
    import logging
    reworded = _with_facts("Stay defensive.").replace(
        "sellers are not pushing them", "buyers are absent")
    with caplog.at_level(logging.WARNING, logger="market_svc.compute"):
        assert compute.generate_summary(_packet(), client=_client(reworded)) is None
    assert any("fact" in r.getMessage() for r in caplog.records)


def test_the_prompt_asks_only_for_joining_the_facts_and_a_posture():
    s = compute._SUMMARY_SYSTEM.lower()
    for phrase in ("plain everyday english", "include every fact exactly as written",
                   "do not reword", "one closing sentence", "posture",
                   "no prices"):
        assert phrase in s, phrase


def test_generate_summary_returns_the_sentence_its_inputs_and_when():
    p = _packet()
    reply = _with_facts("Stay defensive and keep new trades small.")
    out = compute.generate_summary(p, client=_client(reply))
    assert out["narrative"] == reply
    assert out["inputs"] == p
    assert out["as_of"].endswith("+00:00")


def test_generate_summary_sends_only_the_facts():
    """The model never sees the app's labels or numbers - only the statements
    the code wrote from them - so it has nothing to mistranslate."""
    import json
    seen = {}
    p = _packet()
    compute.generate_summary(p, client=_client(_with_facts("x."), seen=seen))
    assert json.loads(seen["messages"][0]["content"]) == {
        "facts": compute.summary_facts(p)}
    assert seen["model"] == compute._SUMMARY_MODEL


def test_nothing_to_state_makes_no_call():
    """No facts means nothing for the model to join - no paid call, no sentence."""
    seen = {}
    out = compute.generate_summary(
        compute.build_summary_packet({}, {}, {}, now=_OPEN),
        client=_client("x.", seen=seen))
    assert seen == {} and out is None


def test_max_tokens_keeps_headroom():
    """A cap, not a spend: billing is on generated tokens. A trim below 300 risks
    a sentence cut mid-word that still renders as if complete."""
    assert compute._SUMMARY_MAX_TOKENS >= 300


def test_a_cut_off_reply_is_logged(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="market_svc.compute"):
        compute.generate_summary(_packet(), client=_client("Fear bu", stop="max_tokens"))
    assert any("max_tokens" in r.getMessage() for r in caplog.records)


def test_no_client_is_an_empty_sentence_never_a_made_up_one():
    out = compute.generate_summary(_packet(), client=None)
    assert out["narrative"] == "" and out["inputs"] == _packet()


def test_a_bool_is_not_a_reading():
    assert compute._finite(True) is None and compute._finite(False) is None
    view = {"benchmark_day_pct": True, "levels": {"sector": [
        {"symbol": "XLK", "raw": {"trend": 0.3, "excess": 0.2},
         "day_pct": 1.0, "day_excess": 0.5}]}}
    assert compute.bullbear_counts(view, now=_OPEN)[0] == "quarter"


def test_a_failed_attempt_returns_none_so_the_last_good_sentence_stays():
    class _Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("rate limited")
    assert compute.generate_summary(_packet(), client=_Boom()) is None


def test_the_quadrant_rule_matches_the_bull_bear_maps():
    """MIRRORS webgui/pages/bullbear.quadrant (this tier cannot import it):
    ties go to the cautious side and a missing axis is 'unknown'. If the map's
    rule changes, this table must change with it."""
    q = compute._quadrant
    assert q(0.1, 0.1) == "rising_leading"
    assert q(0.1, 0.0) == "rising_lagging"        # a zero excess is not leading
    assert q(0.0, 0.1) == "falling_leading"       # a flat trend is not rising
    assert q(-0.1, -0.1) == "falling_lagging"
    assert q(None, 0.1) == "unknown" and q(0.1, None) == "unknown"


def test_the_packet_reader_rebuilds_only_when_a_view_moves(monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    compute.reset_packet_memo()
    bus.cache_set("cache:sentiment:composite", _composite())
    bus.cache_set("cache:sentiment:regime", _regime())
    bus.cache_set("cache:sentiment:bullbear", _bullbear())
    builds = []
    real = compute.build_summary_packet
    monkeypatch.setattr(compute, "build_summary_packet",
                        lambda *a, **k: builds.append(1) or real(*a, **k))
    first = compute.read_summary_packet(bus, now=_OPEN)
    again = compute.read_summary_packet(bus, now=_OPEN)
    assert first == again and len(builds) == 1          # no version moved
    bus.cache_set("cache:sentiment:regime", _regime(label="Balanced"))
    assert compute.read_summary_packet(bus, now=_OPEN)["regime"]["word"] == "Balanced"
    assert len(builds) == 2


# ── sector counts: computed in code, checked before publishing ───────────────
# At 21:00 CT the model wrote "only 2 of the 11 sectors are beating the S&P 500"
# when 6 were. The totals are computed in code, the sector fact states them, and
# any other count the reply adds is checked against them.

def test_the_packet_carries_the_combined_sector_counts_ready_made():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    # today: XLU and XLE rising and beating, XLK falling and trailing
    assert p["bullbear"]["totals"] == {"sectors": 3, "rising": 2, "falling": 1,
                                       "beating_sp500": 2, "trailing_sp500": 1}


def test_the_totals_add_the_buckets_the_way_the_words_mean():
    t = compute._bullbear_totals({"rising_leading": 2, "rising_lagging": 0,
                                  "falling_leading": 4, "falling_lagging": 5,
                                  "unknown": 0})
    assert t == {"sectors": 11, "rising": 2, "falling": 9,
                 "beating_sp500": 6, "trailing_sp500": 5}
    assert compute._bullbear_totals({}) == {}


_TOTALS_21H = {"sectors": 11, "rising": 2, "falling": 9,
               "beating_sp500": 6, "trailing_sp500": 5}


def test_the_count_check_catches_the_21_00_ct_mistake():
    bad = compute._count_claim_error
    assert bad("only 2 of the 11 sectors are beating the S&P 500 today", _TOTALS_21H)
    assert bad("only 2 of the 11 sectors are outperforming today", _TOTALS_21H)
    assert bad("3 of the 11 sectors are rising", _TOTALS_21H)
    assert bad("two of the eleven sectors are falling", _TOTALS_21H)
    assert bad("2 of the 12 sectors are rising", _TOTALS_21H)   # wrong denominator


def test_the_count_check_passes_correct_counts_and_leaves_other_text_alone():
    ok = compute._count_claim_error
    assert ok("6 of the 11 sectors are beating the S&P 500", _TOTALS_21H) is None
    assert ok("only 2 of the 11 sectors are rising, with most falling",
              _TOTALS_21H) is None
    assert ok("nine of the 11 sectors are falling", _TOTALS_21H) is None
    assert ok("5 of 11 sectors are trailing the S&P 500", _TOTALS_21H) is None
    assert ok("2 of the 11 sectors, while most are falling", _TOTALS_21H) is None
    assert ok("only 2 of the 11 sectors are actually holding up", _TOTALS_21H) is None
    assert ok("anything at all", {}) is None     # no totals: nothing to check against


def test_a_sentence_with_a_wrong_sector_count_is_not_published(caplog):
    import logging
    wrong = _with_facts("Also, 3 of the 3 sectors are rising.")   # packet: 2 rising
    with caplog.at_level(logging.WARNING, logger="market_svc.compute"):
        assert compute.generate_summary(_packet(), client=_client(wrong)) is None
    assert any("sector count" in r.getMessage() for r in caplog.records)


# ── never a cut-off sentence ─────────────────────────────────────────────────
# On 2026-09-10 a 400-character slice published "... rather than chas". A reply
# that finished is shown whole; one that ran out of room keeps only its
# complete sentences; one with no complete sentence publishes nothing.

def test_a_long_complete_reply_is_published_whole():
    long = _with_facts(("Keep new trades small and stay defensive. " * 8).strip())
    assert len(long) > 400
    out = compute.generate_summary(_packet(), client=_client(long))
    assert out["narrative"] == long


def test_a_reply_that_ran_out_of_room_keeps_only_its_complete_sentences():
    out = compute.generate_summary(
        _packet(), client=_client(_with_facts("Stay defensive and favor sma"),
                                  stop="max_tokens"))
    assert out["narrative"] == _facts_text()


def test_a_cut_off_reply_with_no_complete_sentence_publishes_nothing():
    assert compute.generate_summary(
        _packet(), client=_client("Prices drift lower and", stop="max_tokens")) is None


def test_a_decimal_point_is_not_a_sentence_end():
    assert compute._complete_sentences("Size is 0.85x now. Then it tri") == \
        "Size is 0.85x now."


# ── accuracy is paramount: spread directions ─────────────────────────────────
# The 20:42 CT sentence called put credit spreads "bearish trades". They are
# bullish-to-neutral (they profit when prices hold up).

def test_the_prompt_puts_accuracy_first_and_states_each_spreads_direction():
    s = compute._SUMMARY_SYSTEM.lower()
    for phrase in ("accuracy is paramount",
                   "a put credit spread profits if prices hold up or rise",
                   "a call credit spread profits if prices stay down",
                   "never describe a put credit spread as bearish",
                   "never describe a call credit spread as bullish",
                   "if the facts point different ways, say so"):
        assert phrase in s, phrase


def test_a_sentence_that_gets_a_spreads_direction_wrong_is_not_published(caplog):
    """Withheld, not shown: the last good sentence stays, and the loop retries
    the same readings after the gap (generate_summary returning None)."""
    import logging
    wrong = _with_facts("Stay defensive with smaller bearish trades like put "
                        "credit spreads.")
    with caplog.at_level(logging.WARNING, logger="market_svc.compute"):
        assert compute.generate_summary(_packet(), client=_client(wrong)) is None
    assert any("spread" in r.getMessage() for r in caplog.records)


def test_the_spread_direction_check_flags_real_contradictions():
    bad = compute._spread_direction_error
    assert bad("favor hedged, smaller bearish trades like put credit spreads")
    assert bad("put credit spreads are a bearish play here")
    assert bad("put credit spreads, a bearish bet")
    assert bad("lean bullish with call credit spreads")
    assert bad("call credit spreads are a bullish trade")


def test_the_spread_direction_check_leaves_correct_sentences_alone():
    ok = compute._spread_direction_error
    assert ok("despite the bearish read, put credit spreads still fit a market "
              "that is holding up") is None
    assert ok("favor put credit spreads, a bullish-to-neutral trade") is None
    assert ok("the tone is bearish, so favor call credit spreads") is None
    assert ok("lean bearish with put debit spreads") is None
    assert ok("") is None and ok(None) is None
