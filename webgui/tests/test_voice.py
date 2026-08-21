"""Pure speech builders for the Desk's spoken alerts.

No network anywhere in this file: synthesis is monkeypatched in the cache
tests below, and the builders here touch nothing but strings.
"""
import voice


# ── spell ────────────────────────────────────────────────────────────────────
def test_spell_separates_the_letters_of_a_ticker():
    assert voice.spell("SPY") == "S P Y"


def test_spell_drops_the_index_dollar_sign():
    # "$SPX" spoken as "dollar S P X" would be wrong, and as "spux" worse.
    assert voice.spell("$SPX") == "S P X"


def test_spell_upcases_and_keeps_digits():
    assert voice.spell("brk.b") == "B R K B"


def test_spell_of_nothing_is_empty_not_a_crash():
    assert voice.spell(None) == ""
    assert voice.spell("") == ""


# ── more_tail ────────────────────────────────────────────────────────────────
def test_more_tail_is_silent_when_nothing_else_arrived():
    assert voice.more_tail(0) == ""
    assert voice.more_tail(None) == ""
    assert voice.more_tail(-3) == ""


def test_more_tail_counts_the_rest():
    assert voice.more_tail(5) == "Plus 5 more."


def test_more_tail_reads_naturally_at_one():
    # Deliberately not "1 more alerts" — and deliberately not the word "alert"
    # at all, since positions use this same tail.
    assert voice.more_tail(1) == "Plus 1 more."


# ── flow_phrase ──────────────────────────────────────────────────────────────
# The rows below are the shape ``pages.options.flow.alert_rows`` publishes:
# ``kind`` and ``side`` are already the DISPLAY labels, not the raw keys.
def test_flow_phrase_names_the_ticker_then_the_cause():
    row = {"symbol": "SPY", "kind": "Crossover", "side": "Calls over"}
    assert voice.flow_phrase(row) == "S P Y. Crossover alert, calls over."


def test_flow_phrase_covers_all_four_alert_kinds():
    cases = {
        ("NDX", "Unusual activity", "Put"): "N D X. Unusual activity alert, put.",
        ("QQQ", "Gamma flip", "To negative"): "Q Q Q. Gamma flip alert, to negative.",
        ("AMD", "Big delta", "Call"): "A M D. Big delta alert, call.",
        ("SPY", "Crossover", "Puts over"): "S P Y. Crossover alert, puts over.",
    }
    for (sym, kind, side), want in cases.items():
        assert voice.flow_phrase(
            {"symbol": sym, "kind": kind, "side": side}) == want


def test_flow_phrase_omits_a_missing_side_without_a_dangling_comma():
    row = {"symbol": "SPY", "kind": "Crossover", "side": ""}
    assert voice.flow_phrase(row) == "S P Y. Crossover alert."


def test_flow_phrase_folds_the_burst_count_into_the_same_sentence():
    row = {"symbol": "SPY", "kind": "Crossover", "side": "Calls over"}
    assert voice.flow_phrase(row, extra=5) == \
        "S P Y. Crossover alert, calls over. Plus 5 more."


def test_flow_phrase_survives_a_junk_row():
    # Total over a malformed row, like every other builder the Desk reads.
    assert voice.flow_phrase(None) == "Flow alert."
    assert voice.flow_phrase({}) == "Flow alert."


# ── position_phrase ──────────────────────────────────────────────────────────
def test_position_phrase_names_the_ticker_and_the_strategy():
    row = {"symbol": "SPY", "strategy": "put_credit_spread"}
    assert voice.position_phrase(row) == "S P Y. New position, put credit spread."


def test_position_phrase_without_a_strategy_still_announces_the_position():
    assert voice.position_phrase({"symbol": "QQQ"}) == "Q Q Q. New position."


def test_position_phrase_takes_the_burst_tail_too():
    row = {"symbol": "SPY", "strategy": "iron_condor"}
    assert voice.position_phrase(row, extra=2) == \
        "S P Y. New position, iron condor. Plus 2 more."
