"""The Desk's spoken alerts — the pure phrase builders and the mp3 cache.

No network anywhere in this file, but it gets there two different ways. The
``ensure`` tests monkeypatch ``_synthesize`` outright. The ``_synthesize`` tests
run the REAL function against a fake ``sys.modules['edge_tts']``, so they
exercise the temp-file/rename/cleanup dance and the bounded wait without ever
opening a socket. The one test that touches the installed ``edge_tts`` reads its
signature only, and skips when the package is absent.
"""
import asyncio
import hashlib
import inspect
import logging
import os
import pathlib
import sys
import types

import pytest

import voice


@pytest.fixture(autouse=True)
def _breaker_closed():
    """Every test starts with the synthesis breaker CLOSED.

    ``ensure`` backs the endpoint off for ``BREAKER_SEC`` after a failure, and
    the breaker is module state — so a test that deliberately fails synthesis
    would silently suppress the synthesis of every test that ran within the next
    minute, in whatever order the run happened to pick. That is a cross-test
    dependency that presents as a flake, so it is closed before AND after.
    """
    voice.reset_breaker()
    yield
    voice.reset_breaker()


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


def test_more_tail_refuses_a_bool():
    # ``float(True) == 1.0`` has slipped through numeric guards in this repo
    # before (see the NaN/bool notes in CLAUDE.md), and pages/fmt.py's ``num``
    # rejects bool explicitly for exactly this reason. A True that arrived where
    # a count belongs is a caller bug, not "one more alert".
    assert voice.more_tail(True) == ""
    assert voice.more_tail(False) == ""


def test_more_tail_survives_a_non_finite_count():
    # ``int(inf)`` raises OverflowError, not ValueError — the module promises
    # that nothing here raises, so the guard has to cover it.
    assert voice.more_tail(float("inf")) == ""
    assert voice.more_tail(float("-inf")) == ""
    assert voice.more_tail(float("nan")) == ""


# ── say_number ───────────────────────────────────────────────────────────────
# Every one of these was confirmed by LISTENING to the synthesized clip, which
# is the only test that counts for a pronunciation rule — "2 05" is here because
# the voice reads "205" as "two hundred and five" and a trader hears "two oh
# five". Do not "simplify" the rule against these; they ARE the rule.
_NUMBER_CASES = {
    5: "5",                      # 1-2 digits are left alone
    12.5: "12. point 5",
    205: "2 05",                 # lead digits singly, the last two as a pair
    207.5: "2 07. point 5",
    380: "3 80",
    472.5: "4 72. point 5",
    715: "7 15",
    1250: "1 2 50",
    4500: "4 5 hundred",         # a trailing "00" is a word, not "zero zero"
    7710: "7 7 10",
    21500: "2 1 5 hundred",
    24350: "2 4 3 50",
}


def test_say_number_speaks_a_strike_the_way_a_trader_hears_it():
    for value, want in _NUMBER_CASES.items():
        assert voice.say_number(value) == want, value


def test_say_number_gives_the_same_answer_for_the_int_and_the_float():
    # Strikes reach here as floats off a cache read and as ints from a hand-
    # written test; a rule that split on the type would be a rule nobody could
    # reason about.
    assert voice.say_number(205.0) == voice.say_number(205) == "2 05"


def test_say_number_refuses_junk_rather_than_speaking_it():
    """Nothing on this module's surface raises, and nothing invents a reading.

    ``True`` is in the list for the documented ``float(True) == 1.0`` reason —
    a flag arriving where a strike belongs must not be announced as the $1
    strike. ``pages/fmt.py``'s ``num`` is the guard, and it rejects bool and
    non-finite ahead of the coercion.
    """
    for junk in (None, "", "abc", float("nan"), float("inf"), float("-inf"),
                 True, False, object(), [1]):
        assert voice.say_number(junk) == "", junk


def test_say_number_says_a_negative_out_loud_rather_than_dropping_the_sign():
    # A silently absolute value is how a debit comes to sound like a credit.
    # Nothing feeds this a negative today (strikes are positive and ``say_entry``
    # takes its own absolute value), which is exactly why the honest answer is
    # the cheap one to pick.
    assert voice.say_number(-205) == "minus 2 05"


def test_say_number_never_leaves_a_dangling_point(monkeypatch):
    """A fraction that ROUNDS AWAY must not emit "4 72. point " with nothing after.

    ``f"{0.99999:.4f}"`` is ``"1.0000"``, whose digits strip to the empty string
    — the one input shape the reference rule got wrong, and a half-word is the
    thing this feature is least allowed to say.
    """
    assert voice.say_number(472.99999) == "4 72"
    assert not voice.say_number(472.99999).endswith("point ")


# ── say_expiry ───────────────────────────────────────────────────────────────
def test_say_expiry_spells_out_zero_dte():
    # "0DTE" read as a word is a noise; the letters are the whole point.
    assert voice.say_expiry("2026-08-18", 0) == "0-D T E"


def test_say_expiry_is_month_and_day_with_no_leading_zeros():
    # The spaced hyphen is what makes the voice PAUSE between the two numbers —
    # "8-28" runs together into something that is not a date.
    assert voice.say_expiry("2026-08-28", 3) == "8 - 28"
    assert voice.say_expiry("2026-09-05", 12) == "9 - 5"
    assert voice.say_expiry("2026-08-31", None) == "8 - 31"


def test_say_expiry_zero_dte_wins_over_an_unusable_date():
    assert voice.say_expiry("", 0) == "0-D T E"
    assert voice.say_expiry(None, 0) == "0-D T E"


def test_say_expiry_refuses_what_it_cannot_read():
    """Unlike ``flow._exp_short``, which falls back to the RAW string.

    That is right for a table cell — the reader can see it is odd — and wrong
    out loud, where "two zero two six dash oh eight" is unintelligible.
    """
    for junk in (None, "", "nope", "2026-08", 7, True):
        assert voice.say_expiry(junk, None) == "", junk


def test_say_expiry_does_not_take_a_bool_for_zero_dte():
    # ``False == 0`` is True in Python, so an unguarded ``dte == 0`` would call
    # a flag a 0DTE contract.
    assert voice.say_expiry("2026-08-28", False) == "8 - 28"


# ── say_entry ────────────────────────────────────────────────────────────────
def test_say_entry_names_a_credit_and_a_debit_by_their_sign():
    """The paper book stores a DEBIT as a negative ``entry_credit``.

    A debit that sounds like a credit is the most dangerous sentence this
    feature can say, so the sign picks the word rather than being dropped.
    """
    assert voice.say_entry(0.56) == "entry 56 cent credit"
    assert voice.say_entry(-1.25) == "entry 1 dollar 25 debit"


def test_say_entry_uses_cents_below_a_dollar_and_dollars_above():
    assert voice.say_entry(0.05) == "entry 5 cent credit"
    assert voice.say_entry(0.99) == "entry 99 cent credit"
    assert voice.say_entry(1.0) == "entry 1 dollar credit"
    assert voice.say_entry(-2.0) == "entry 2 dollar debit"
    assert voice.say_entry(12.5) == "entry 12 dollar 50 credit"


def test_say_entry_rounds_into_the_dollar_rather_than_saying_100_cents():
    assert voice.say_entry(0.999) == "entry 1 dollar credit"


def test_say_entry_speaks_a_genuine_zero_rather_than_hiding_it():
    # Absent and zero are different facts (pages/fmt.py's governing rule). A
    # zero entry is a real reading, and swallowing it would degrade the whole
    # sentence back to the short form over a number that was actually there.
    assert voice.say_entry(0) == "entry 0 cent credit"


def test_say_entry_refuses_junk():
    for junk in (None, "", "abc", float("nan"), float("inf"), True, False):
        assert voice.say_entry(junk) == "", junk


def test_say_entry_survives_a_finite_number_too_big_to_scale():
    """``_num`` passes ``1.7e308`` — it IS finite — and then ``× 100`` is not.

    ``round(inf)`` raises ``OverflowError``, exactly as ``int(inf)`` does in
    ``more_tail``, and this module's promise that nothing on its public surface
    raises is categorical rather than "for values we expect".
    """
    assert voice.say_entry(1.7e308) == ""
    assert voice.say_entry(-1.7e308) == ""


# ── say_strikes ──────────────────────────────────────────────────────────────
def test_say_strikes_speaks_both_legs_of_a_spread():
    # ``desk.strikes_text`` builds this string for the panel; the spoken form
    # reads the SAME string rather than re-deriving one from the raw fields.
    assert voice.say_strikes("207.5/205") == "2 07. point 5, 2 05"
    assert voice.say_strikes("600.0/595.0") == "6 hundred, 5 95"


def test_say_strikes_handles_a_single_leg():
    assert voice.say_strikes("100") == "1 hundred"


def test_say_strikes_refuses_the_em_dash_and_the_unreadable():
    # ``strikes_text`` returns "—" for a position with no strike pair at all.
    for junk in (None, "", "—", "/", "abc/def", 7, True):
        assert voice.say_strikes(junk) == "", junk


def test_say_strikes_drops_only_the_unusable_half():
    assert voice.say_strikes("100/") == "1 hundred"
    assert voice.say_strikes("/205") == "2 05"


# ── flow_phrase ──────────────────────────────────────────────────────────────
# The rows below are the shape ``pages.options.flow.alert_rows`` publishes:
# ``kind`` and ``side`` are already the DISPLAY labels, not the raw keys.
def test_flow_phrase_names_the_ticker_then_the_cause():
    row = {"symbol": "SPY", "kind": "Premium shift", "side": "Calls over"}
    assert voice.flow_phrase(row) == "S P Y. Premium shift alert, calls over."


# ⚠ These rows carry NO strike/expiry, so the two contract-carrying kinds land on
# the DEGRADE path here on purpose — the short form is what a uoa alert with an
# unreadable contract still says. The contract form has its own block below.
# ⚠ Every kind here is a NOUN phrase, and that is load-bearing rather than
# stylistic: this form is f"{kind} alert", so the clause "Hedging flipped" would
# speak as "Hedging flipped alert, now damping." A gamma flip names no contract,
# so it ALWAYS takes this path.
_FLOW_CASES = {
    ("SPY", "Premium shift", "Calls over"): "S P Y. Premium shift alert, calls over.",
    ("SPY", "Premium shift", "Puts over"): "S P Y. Premium shift alert, puts over.",
    ("NDX", "Unusual volume", "Put"): "N D X. Unusual volume alert, put.",
    ("NDX", "Unusual volume", "Call"): "N D X. Unusual volume alert, call.",
    ("QQQ", "Hedging flip", "Now amplifying"): "Q Q Q. Hedging flip alert, now amplifying.",
    ("QQQ", "Hedging flip", "Now damping"): "Q Q Q. Hedging flip alert, now damping.",
    ("AMD", "Outsized bet", "Call"): "A M D. Outsized bet alert, call.",
    ("AMD", "Outsized bet", "Put"): "A M D. Outsized bet alert, put.",
}


def test_flow_phrase_covers_all_four_alert_kinds():
    for (sym, kind, side), want in _FLOW_CASES.items():
        assert voice.flow_phrase(
            {"symbol": sym, "kind": kind, "side": side}) == want


def test_flow_phrase_cases_are_complete_against_the_flow_pages_own_labels():
    """The table above is hand-written; this is what stops it rotting.

    ``flow_phrase`` reads the DISPLAY labels ``pages.options.flow`` stamps, so
    the spoken vocabulary and the printed one are the same words by
    construction — but only for labels somebody remembered to test. Add a fifth
    alert kind, or relabel "Outsized bet", and every assertion stays green
    while the new phrase is never once spoken aloud in a test. Drift between
    the spoken and the printed vocabulary is invisible otherwise, which is the
    documented sectors-vs-rotation failure in a new place.

    Same shape as ``test_desk.py``'s
    ``test_flow_rows_delegates_to_the_flow_pages_own_builder``: import the real
    module and compare against it, never against a copy.
    """
    from pages.options import flow
    tested_kinds = {kind for _sym, kind, _side in _FLOW_CASES}
    tested_sides = {side for _sym, _kind, side in _FLOW_CASES}
    assert tested_kinds >= set(flow._KIND_LABEL.values())
    assert tested_sides >= set(flow._SIDE_LABEL.values())


# ── flow_phrase: the CONTRACT ────────────────────────────────────────────────
# Two of the four alert kinds name a specific contract, and until 2026-08-21 the
# squawk threw that away: "N D X. Unusual volume alert, put." told the listener
# something was moving and then refused to say WHAT, so every alert cost a look
# at the screen anyway. These are the user's own reference phrases.
def test_flow_phrase_speaks_the_contract_for_unusual_activity():
    row = {"symbol": "$NDX", "kind": "Unusual volume", "side": "Put",
           "strike": 715.0, "expiry": "2026-08-18", "dte": 0}
    assert voice.flow_phrase(row) == "N D X. Unusual volume, 0-D T E 7 15 Put."


def test_flow_phrase_speaks_a_five_digit_index_strike():
    row = {"symbol": "$NDX", "kind": "Unusual volume", "side": "Put",
           "strike": 21500.0, "expiry": "2026-08-18", "dte": 0}
    assert voice.flow_phrase(row) == \
        "N D X. Unusual volume, 0-D T E 2 1 5 hundred Put."


def test_flow_phrase_speaks_the_contract_for_big_delta():
    row = {"symbol": "AMD", "kind": "Outsized bet", "side": "Call",
           "strike": 472.5, "expiry": "2026-08-28", "dte": 3}
    assert voice.flow_phrase(row) == "A M D. Outsized bet, 8 - 28 4 72. point 5 Call."


def test_the_contract_form_drops_the_word_alert_and_moves_the_side_last():
    """Both changes are deliberate, and both are the user's wording.

    "Unusual volume alert, put, 8 - 28 4 72. point 5" would put the side
    before the contract it belongs to; naming the contract and THEN its side is
    how the contract is spoken aloud everywhere else.
    """
    row = {"symbol": "AMD", "kind": "Outsized bet", "side": "Call",
           "strike": 472.5, "expiry": "2026-08-28", "dte": 3}
    said = voice.flow_phrase(row)
    assert "alert" not in said
    assert said.index("4 72") < said.index("Call")


def test_the_contract_less_kinds_are_untouched():
    """A premium shift and a hedging flip carry no contract, so the phrase stays.

    They keep the word "alert" precisely because there is nothing to put in its
    place — the shortening was paid for by the detail that replaced it.
    """
    assert voice.flow_phrase(
        {"symbol": "SPY", "kind": "Premium shift", "side": "Calls over",
         "strike": None, "expiry": None, "dte": None}) == \
        "S P Y. Premium shift alert, calls over."
    assert voice.flow_phrase(
        {"symbol": "QQQ", "kind": "Hedging flip", "side": "Now amplifying",
         "strike": None, "expiry": None, "dte": None}) == \
        "Q Q Q. Hedging flip alert, now amplifying."


def test_a_contract_alert_takes_the_burst_tail_too():
    row = {"symbol": "AMD", "kind": "Outsized bet", "side": "Call",
           "strike": 472.5, "expiry": "2026-08-28", "dte": 3}
    assert voice.flow_phrase(row, extra=2) == \
        "A M D. Outsized bet, 8 - 28 4 72. point 5 Call. Plus 2 more."


# ── flow_phrase: the DEGRADE path ────────────────────────────────────────────
# ⚠ The rule is SHORTER, never HALF. A missing strike must not produce
# "Outsized bet, 8 - 28  Call." with a hole in it — a terse alert is worth having,
# a broken sentence is not, and silence is worse than both.
@pytest.mark.parametrize("missing", ["strike", "expiry", "side"])
def test_a_contract_alert_missing_a_piece_falls_back_to_the_short_form(missing):
    row = {"symbol": "NDX", "kind": "Unusual volume", "side": "Put",
           "strike": 715.0, "expiry": "2026-08-18", "dte": 3}
    row[missing] = None
    said = voice.flow_phrase(row)
    if missing == "side":
        assert said == "N D X. Unusual volume alert."
    else:
        assert said == "N D X. Unusual volume alert, put."
    assert ",," not in said and "  " not in said


def test_an_unreadable_strike_degrades_rather_than_speaking_nonsense():
    for junk in ("abc", float("nan"), True, ""):
        row = {"symbol": "NDX", "kind": "Unusual volume", "side": "Put",
               "strike": junk, "expiry": "2026-08-18", "dte": 3}
        assert voice.flow_phrase(row) == "N D X. Unusual volume alert, put.", junk


def test_flow_phrase_omits_a_missing_side_without_a_dangling_comma():
    row = {"symbol": "SPY", "kind": "Premium shift", "side": ""}
    assert voice.flow_phrase(row) == "S P Y. Premium shift alert."


def test_flow_phrase_folds_the_burst_count_into_the_same_sentence():
    row = {"symbol": "SPY", "kind": "Premium shift", "side": "Calls over"}
    assert voice.flow_phrase(row, extra=5) == \
        "S P Y. Premium shift alert, calls over. Plus 5 more."


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


# ── position_phrase: the CONTRACT ────────────────────────────────────────────
def _pos_row(**over):
    """The shape ``desk.position_rows`` publishes — note ``strikes`` is the
    already-built STRING, and ``expiration`` (not ``expiry``) the ISO date."""
    row = {"symbol": "SPY", "strategy": "put_credit_spread",
           "strikes": "207.5/205", "expiration": "2026-08-31", "dte": 10,
           "entry_credit": 0.56}
    row.update(over)
    return row


def test_position_phrase_speaks_the_strikes_the_expiry_and_the_entry():
    assert voice.position_phrase(_pos_row()) == (
        "S P Y. New position, put credit spread. "
        "2 07. point 5, 2 05, 8 - 31, entry 56 cent credit.")


def test_a_position_opened_for_a_debit_says_so():
    """The book stores a debit as a NEGATIVE ``entry_credit``, and a debit
    announced as a credit is the worst sentence this feature could say."""
    said = voice.position_phrase(_pos_row(entry_credit=-1.25))
    assert said.endswith("entry 1 dollar 25 debit.")
    assert "credit spread" in said        # the STRATEGY word is untouched


def test_a_zero_dte_position_says_so_rather_than_naming_the_date():
    assert "0-D T E" in voice.position_phrase(_pos_row(dte=0))


def test_a_position_contract_takes_the_burst_tail_too():
    assert voice.position_phrase(_pos_row(), extra=3).endswith(
        "entry 56 cent credit. Plus 3 more.")


# ── position_phrase: the DEGRADE path ────────────────────────────────────────
_SHORT_POS = "S P Y. New position, put credit spread."


@pytest.mark.parametrize("over", [
    {"strikes": "—"},               # what ``strikes_text`` returns with no pair
    {"strikes": None},
    {"expiration": "", "dte": None},
    {"entry_credit": None},
    {"entry_credit": float("nan")},
])
def test_a_position_missing_a_piece_falls_back_to_the_short_form(over):
    said = voice.position_phrase(_pos_row(**over))
    assert said == _SHORT_POS
    assert ", ," not in said and "  " not in said


def test_a_single_leg_position_speaks_its_one_strike():
    """A single leg is not a malformed spread — it is a long call. Degrading it
    to the short form would silence the one number the listener wants."""
    assert voice.position_phrase(
        _pos_row(strategy="long_call", strikes="600.0")) == (
        "S P Y. New position, long call. 6 hundred, 8 - 31, entry 56 cent credit.")


# ── cache keys ───────────────────────────────────────────────────────────────
def test_clip_name_is_stable_for_the_same_phrase():
    a = voice.clip_name("S P Y. Crossover alert.")
    assert a == voice.clip_name("S P Y. Crossover alert.")
    assert a.endswith(".mp3")


def test_clip_name_changes_with_the_voice():
    # The voice is part of the key, or switching voices in Settings would keep
    # serving clips spoken by the previous one.
    a = voice.clip_name("hello", voice_name="en-US-AriaNeural")
    b = voice.clip_name("hello", voice_name="en-US-BrianNeural")
    assert a != b


def test_clip_name_changes_with_the_rate():
    # The rate is in the key for the same reason the voice is: a clip spoken at
    # the old speed is not the clip Settings now asks for, and the filename is
    # the only place that difference can be recorded.
    assert voice.clip_name("hello", rate="+0%") != voice.clip_name("hello", rate="+8%")


def test_clip_name_hash_is_pinned_so_warmed_clips_are_never_orphaned():
    """The digest is a CACHE KEY on disk, so changing it strands every clip.

    ``usedforsecurity=False`` documents that intent and silences FIPS/Bandit
    flags, but it must not move a single byte of output — this pins both the
    formula and the literal digest observed before the flag was added.
    """
    expected = hashlib.sha1(
        f"{voice.DEFAULT_VOICE}|{voice.RATE}|hello".encode("utf-8")).hexdigest()
    assert voice.clip_name("hello") == expected + ".mp3"
    assert voice.clip_name("hello") == "aef6ec44b2d50d96a34158c500217874aa8f5131.mp3"


def test_clip_url_is_served_from_the_voice_mount():
    url = voice.clip_url("hello")
    assert url.startswith("/voice/") and url.endswith(".mp3")


# ── ensure ───────────────────────────────────────────────────────────────────
def test_ensure_synthesizes_once_then_serves_the_cached_file(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    calls = []

    def _fake(text, voice_name, rate, dest, timeout=None):
        calls.append(text)
        dest.write_bytes(b"ID3fake")

    monkeypatch.setattr(voice, "_synthesize", _fake)

    first = voice.ensure("S P Y. Crossover alert.")
    second = voice.ensure("S P Y. Crossover alert.")
    assert first == second and first.startswith("/voice/")
    assert calls == ["S P Y. Crossover alert."]      # the second call is a hit


def test_ensure_returns_none_when_synthesis_fails(tmp_path, monkeypatch):
    # No internet, no edge_tts, unwritable dir — all land here, and all must
    # degrade to silence rather than a traceback on the landing page.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    voice.reset_warning()
    assert voice.ensure("anything") is None


def test_ensure_ignores_an_empty_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    assert voice.ensure("") is None
    assert voice.ensure(None) is None


def test_ensure_does_not_serve_a_zero_byte_clip(tmp_path, monkeypatch):
    # A crashed or interrupted synthesis can leave an empty file. Serving it
    # would be a silent permanent failure for that one phrase.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    dest = tmp_path / voice.clip_name("hello")
    dest.write_bytes(b"")
    monkeypatch.setattr(voice, "_synthesize",
                        lambda t, v, r, d, timeout=None:
                        d.write_bytes(b"ID3real"))
    assert voice.ensure("hello") is not None
    assert dest.read_bytes() == b"ID3real"


def test_ensure_degrades_to_silence_when_synthesis_times_out(tmp_path, monkeypatch):
    """The degradation HALF of the timeout story, not the cancellation half.

    ``test_synthesize_timeout_is_bounded_and_cancels_the_request`` below is what
    proves the bounded wait actually fires and cancels; this only pins that a
    ``TimeoutError`` surfacing out of ``_synthesize`` reaches the caller as
    silence and not as a traceback on the landing page. It also pins that the
    dead ``dest`` is not served: a timed-out synthesis leaves nothing behind, so
    ``ensure``'s post-failure ``_usable`` re-check (the lost-race path) must NOT
    manufacture a URL for a file that does not exist.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)

    def _hang(text, voice_name, rate, dest, timeout=None):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(voice, "_synthesize", _hang)
    voice.reset_warning()
    assert voice.ensure("anything") is None


def test_ensure_survives_a_lone_surrogate_in_the_phrase(tmp_path, monkeypatch):
    """A malformed payload must not traceback the landing page.

    ``json.loads('"\\ud800"')`` yields a lone surrogate WITHOUT error, so a bad
    Redis payload is a real source of one — and ``clip_name``'s
    ``.encode("utf-8")`` refuses it. The hash of the phrase is therefore work
    that can raise, and it has to sit INSIDE ``ensure``'s guard, not above it.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    voice.reset_warning()
    assert voice.ensure("SPY \udcff alert") is None


def test_ensure_serves_a_clip_that_a_lost_replace_race_already_wrote(
        tmp_path, monkeypatch):
    """Losing the rename race is not a reason to go silent.

    The prewarm daemon and a live Desk tick can synthesize the SAME phrase at
    once. Thread A wins, the browser starts streaming ``dest``, and thread B's
    ``tmp.replace(dest)`` then fails with ``PermissionError [WinError 5]``
    because the destination has an open handle. A good clip now exists — B must
    serve it rather than return ``None`` and burn the one-shot warning.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    dest = tmp_path / voice.clip_name("hello")

    def _lost_the_race(text, voice_name, rate, d, timeout=None):
        d.write_bytes(b"ID3winner")            # the OTHER thread got there first
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(voice, "_synthesize", _lost_the_race)
    voice.reset_warning()
    assert voice.ensure("hello") == voice.clip_url("hello")
    assert dest.read_bytes() == b"ID3winner"
    assert voice._WARNED["done"] is False      # not a failure, so not a warning


def test_ensure_warns_only_once_per_process(tmp_path, monkeypatch, caplog):
    """A page polling every two seconds would otherwise log 43,200 tracebacks."""
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    voice.reset_warning()
    with caplog.at_level(logging.WARNING, logger="webgui"):
        # The breaker is re-closed between calls ON PURPOSE: it would suppress
        # calls two and three on its own, and then this test would pass without
        # the one-shot latch it is meant to be about.
        for phrase in ("one", "two", "three"):
            voice.reset_breaker()
            assert voice.ensure(phrase) is None
    warnings = [r for r in caplog.records if "voice synthesis" in r.message]
    assert len(warnings) == 1


# ── the per-call synthesis budget, and the breaker over it ───────────────────
def test_the_live_path_can_pass_a_shorter_budget_than_the_prewarm(
        tmp_path, monkeypatch):
    """One module-wide timeout cannot serve both callers. The prewarm is on a
    daemon thread where waiting is free; the Desk's poll AWAITS its speak step,
    so 20 s there is 20 s of a landing page that fetches nothing."""
    seen = []

    def _fake(text, voice_name, rate, dest, timeout=None):
        seen.append(timeout)
        dest.write_bytes(b"ID3fake")

    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize", _fake)
    voice.ensure("live", timeout=voice.LIVE_SYNTH_TIMEOUT_SEC)
    voice.ensure("background")
    assert seen == [voice.LIVE_SYNTH_TIMEOUT_SEC, None]  # None = SYNTH_TIMEOUT_SEC
    assert voice.LIVE_SYNTH_TIMEOUT_SEC < voice.SYNTH_TIMEOUT_SEC


def test_synthesize_honours_a_per_call_budget_over_the_module_default(
        monkeypatch, tmp_path):
    """The parameter has to reach ``asyncio.wait_for``, not merely be accepted."""
    monkeypatch.setattr(voice, "SYNTH_TIMEOUT_SEC", 30.0)

    class _Hang:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            await asyncio.sleep(30)

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Hang))
    with pytest.raises(asyncio.TimeoutError):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE,
                          tmp_path / "x.mp3", 0.05)


def test_ensure_backs_a_failing_endpoint_off_instead_of_paying_the_timeout_again(
        tmp_path, monkeypatch):
    """Bounding ONE call is not enough. A two-phrase burst pays the timeout
    twice, and the next burst pays it again — every burst, all day. After a
    failure the endpoint is left alone for ``BREAKER_SEC``."""
    calls = []
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: calls.append(1) or
                        (_ for _ in ()).throw(OSError("endpoint gone")))
    voice.reset_warning()
    assert voice.ensure("first") is None
    assert voice.ensure("second") is None
    assert voice.ensure("third") is None
    assert len(calls) == 1          # one attempt, not three timeouts


def test_the_breaker_expires_so_a_transient_outage_heals_itself(
        tmp_path, monkeypatch):
    """A minute, not forever — nobody should have to restart the web GUI to get
    their spoken alerts back after a blip."""
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blip")))
    voice.reset_warning()
    assert voice.ensure("first") is None
    assert voice._breaker_open() is True
    assert voice._breaker_open(now=voice._BREAKER["until"] + 1) is False
    assert voice.BREAKER_SEC == 60.0

    def _works(text, voice_name, rate, dest, timeout=None):
        dest.write_bytes(b"ID3fake")

    monkeypatch.setattr(voice, "_synthesize", _works)
    voice.reset_breaker()
    assert voice.ensure("second") is not None
    assert voice._breaker_open() is False    # ...and success closes it again


def test_the_breaker_never_stands_between_a_caller_and_a_CACHED_clip(
        tmp_path, monkeypatch):
    """A cache hit touches no network. Backing one off would silence phrases the
    prewarm already paid for, which is the whole point of the prewarm."""
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    (tmp_path / voice.clip_name("warm")).write_bytes(b"ID3warm")
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))
    voice.reset_warning()
    assert voice.ensure("cold") is None          # trips the breaker
    assert voice.ensure("warm") == voice.clip_url("warm")


def test_a_lost_replace_race_does_not_leave_the_breaker_tripped(
        tmp_path, monkeypatch):
    """A clip exists, so the endpoint is demonstrably alive — that failure was
    the documented rename race, not an outage, and must not cost the next
    phrase a minute of silence."""
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)

    def _lost(text, voice_name, rate, d, timeout=None):
        d.write_bytes(b"ID3winner")
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(voice, "_synthesize", _lost)
    voice.reset_warning()
    assert voice.ensure("hello") == voice.clip_url("hello")
    assert voice._breaker_open() is False


def test_synthesize_timeout_is_bounded_and_cancels_the_request(monkeypatch, tmp_path):
    """The timeout must CANCEL the coroutine, not orphan it in the background.

    Injecting a tiny timeout keeps the test instant — waiting the real 20 s to
    prove a 20 s wait works would be its own bug.
    """
    monkeypatch.setattr(voice, "SYNTH_TIMEOUT_SEC", 0.05)
    cancelled = {"hit": False}

    class _Hang:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled["hit"] = True
                raise

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Hang))
    with pytest.raises(asyncio.TimeoutError):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE, tmp_path / "x.mp3")
    assert cancelled["hit"]


def test_synthesize_leaves_no_part_file_behind(monkeypatch, tmp_path):
    """A failed synthesis must not litter the cache with .part turds."""
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            pathlib.Path(path).write_bytes(b"half")
            raise OSError("dropped")

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Boom))
    with pytest.raises(OSError):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE, tmp_path / "x.mp3")
    assert list(tmp_path.iterdir()) == []


def test_synthesize_cleanup_failure_does_not_mask_the_real_error(
        monkeypatch, tmp_path):
    """The ``finally`` must not overwrite the exception it is cleaning up after.

    Unlinking a file with an open handle raises ``PermissionError [WinError 32]``
    on Windows, and an antivirus scanner touching the ``.part`` is enough to do
    it (Malwarebytes is a documented actor on this machine). If that escapes,
    ``ensure``'s one-shot ``exc_info=True`` warning — the only diagnostic the
    feature has — reports the cleanup, never the cause.
    """
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            pathlib.Path(path).write_bytes(b"half")
            raise OSError("dropped mid-stream")

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Boom))
    monkeypatch.setattr(
        pathlib.Path, "unlink",
        lambda self, missing_ok=False: (_ for _ in ()).throw(
            PermissionError(32, "used by another process")))

    with pytest.raises(OSError, match="dropped mid-stream"):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE, tmp_path / "x.mp3")


# ── the edge_tts contract ────────────────────────────────────────────────────
def test_edge_tts_communicate_accepts_the_call_we_make():
    """The one test that reads the INSTALLED package rather than a fake.

    Both ``_synthesize`` tests inject a ``Communicate`` whose ``__init__(self,
    *a, **k)`` swallows any signature, which is what makes them fast and
    offline — and also what makes them blind. ``edge-tts>=7.0`` is an unpinned
    floor, so a renamed ``rate`` kwarg upstream would leave this whole file
    green while production went permanently silent behind a single warning.
    Binding the real signature costs nothing and no network.
    """
    edge_tts = pytest.importorskip("edge_tts")
    sig = inspect.signature(edge_tts.Communicate.__init__)
    sig.bind(object(), "some text", voice.DEFAULT_VOICE, rate=voice.RATE)


def test_importing_voice_does_not_import_edge_tts():
    """The lazy import is the module's entire Tier-1 justification.

    ``voice`` must stay importable on a machine that never installed
    ``edge_tts`` — the webgui imports only ``nicegui`` + ``shared.bus`` +
    ``shared.contracts``, and a hoisted top-level import would make an optional
    speech package a hard dependency of the landing page. Same shape as
    ``test_options_gamma.test_page_imports_no_engine_or_proxy``.
    """
    import importlib
    for name in ("edge_tts", "voice"):
        sys.modules.pop(name, None)
    try:
        importlib.import_module("voice")
        assert "edge_tts" not in sys.modules
    finally:
        sys.modules.pop("voice", None)
        importlib.import_module("voice")


# ── prewarm ──────────────────────────────────────────────────────────────────
def test_prewarm_texts_covers_every_symbol_and_cause():
    texts = voice.prewarm_texts(["SPY", "QQQ"])
    assert len(texts) == 2 * len(voice.FLOW_CAUSES)
    assert "S P Y. Premium shift alert, calls over." in texts
    assert "Q Q Q. Hedging flip alert, now amplifying." in texts


def test_the_prewarm_skips_the_kinds_whose_phrase_embeds_a_contract():
    """Warming those would synthesize clips that can NEVER be played.

    A uoa phrase now names a strike and an expiry, so its phrase space is the
    whole chain — the eight-pair list warmed "N D X. Unusual volume alert,
    put.", a sentence no live alert produces any more. Pure network and disk for
    nothing, on every first Desk open.
    """
    warmed_kinds = {kind for kind, _side in voice.FLOW_CAUSES}
    assert warmed_kinds.isdisjoint(voice.CONTRACT_KINDS)
    assert warmed_kinds == {"Premium shift", "Hedging flip"}
    assert len(voice.FLOW_CAUSES) == 4          # was 8 before the contract form
    for text in voice.prewarm_texts(["SPY"]):
        assert "alert" in text                  # only the short form is warmable


def test_prewarm_texts_of_nothing_is_empty():
    assert voice.prewarm_texts(None) == []
    assert voice.prewarm_texts([]) == []


def test_prewarm_texts_survives_a_non_iterable():
    # The module's headline promise is categorical: nothing on the public
    # surface raises. ``symbols`` reaches here off a Redis payload, the same
    # source that produces the lone surrogate above — "that would be a caller
    # bug" is not a defence when the caller is a malformed cache read.
    assert voice.prewarm_texts(7) == []
    assert voice.prewarm_texts(object()) == []
    assert voice.prewarm(7) is None


def test_prewarm_returns_none_when_there_is_nothing_to_warm():
    assert voice.prewarm([]) is None
    assert voice.prewarm(None) is None


def test_prewarm_runs_on_a_daemon_thread(monkeypatch):
    """A non-daemon thread would hold the web GUI open at shutdown."""
    monkeypatch.setattr(voice, "ensure", lambda *a, **k: "/voice/x.mp3")
    t = voice.prewarm(["SPY"])
    assert t is not None and t.daemon
    t.join(timeout=5)
    assert not t.is_alive()


def test_prewarm_stops_at_the_first_failure(monkeypatch):
    """Synthesis failures come in one flavour — the endpoint is unreachable —
    so grinding through the remaining 15 phrases buys nothing but 15 timeouts."""
    calls = []

    def _fake(text, voice_name=None, **kw):
        calls.append(text)
        return None if len(calls) == 2 else "/voice/x.mp3"

    monkeypatch.setattr(voice, "ensure", _fake)
    t = voice.prewarm(["SPY", "QQQ"])
    t.join(timeout=5)
    assert len(calls) == 2                      # stopped, did not run all 16


def test_prewarm_failure_does_not_burn_the_live_paths_one_shot_warning(
        tmp_path, monkeypatch):
    """The warning slot belongs to the LIVE path, which is the one a user hears.

    A transient blip during the startup prewarm used to set ``_WARNED``
    permanently, so a genuinely broken lazy path an hour later logged nothing at
    all and the feature failed in total silence.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blip")))
    voice.reset_warning()
    t = voice.prewarm(["SPY"])
    t.join(timeout=5)
    assert voice._WARNED["done"] is False


# ── the .part sweep ──────────────────────────────────────────────────────────
def test_sweep_parts_removes_only_stale_leftovers(tmp_path, monkeypatch):
    """Orphaned ``.part`` files accumulate forever in a SERVED directory.

    A daemon prewarm thread killed at interpreter shutdown never runs its
    ``finally``, and nothing else ever looks at ``data/voice``. The sweep must
    not touch clips, and must not touch a ``.part`` that a live synthesis is
    still writing.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    stale = tmp_path / "abc.mp3.123.456.part"
    fresh = tmp_path / "def.mp3.789.012.part"
    clip = tmp_path / "abc.mp3"
    for p in (stale, fresh, clip):
        p.write_bytes(b"x")
    old = os.stat(stale).st_mtime - (voice._PART_MAX_AGE_SEC + 60)
    os.utime(stale, (old, old))

    assert voice._sweep_parts() == 1
    assert not stale.exists()
    assert fresh.exists() and clip.exists()


def test_sweep_parts_never_raises_on_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path / "nope")
    assert voice._sweep_parts() == 0


def test_prewarm_sweeps_stale_parts_before_it_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "ensure", lambda *a, **k: "/voice/x.mp3")
    stale = tmp_path / "abc.mp3.1.2.part"
    stale.write_bytes(b"x")
    old = os.stat(stale).st_mtime - (voice._PART_MAX_AGE_SEC + 60)
    os.utime(stale, (old, old))

    t = voice.prewarm(["SPY"])
    t.join(timeout=5)
    assert not stale.exists()


def test_all_causes_cover_every_pair_the_flow_page_can_emit():
    """``_ALL_CAUSES`` is a deliberate COPY of pages.options.flow's labels — it
    is restated so ``voice`` stays importable with no ``pages`` package on the
    path (the prewarm runs before any page is built). A copy with no guard is
    a copy waiting to rot, so this is the guard: ``flow._TONE``'s keys enumerate
    exactly the (type, side) pairs the panel can produce, mapped through the
    page's OWN label functions rather than hand-copied a second time.
    """
    from pages.options import flow
    real = {(flow.alert_kind_label({"type": t}), flow.side_label({"side": s}))
            for t, s in flow._TONE}
    assert set(voice._ALL_CAUSES) == real


def test_the_prewarm_list_is_exactly_the_contract_less_pairs():
    """The prewarm shrank; this is what stops it shrinking by accident.

    ``FLOW_CAUSES`` is DERIVED (``_ALL_CAUSES`` minus ``CONTRACT_KINDS``) rather
    than written out a second time, so the two can only disagree if somebody
    edits the derivation. Recomputing it here from the flow page's own labels
    catches the case the test above cannot: a FIFTH contract-less alert kind
    that nobody remembers to warm. It would land in ``_ALL_CAUSES`` (that test
    forces it to) and then here, unwarmed.
    """
    from pages.options import flow
    real = {(flow.alert_kind_label({"type": t}), flow.side_label({"side": s}))
            for t, s in flow._TONE}
    want = {(k, s) for k, s in real if k not in voice.CONTRACT_KINDS}
    assert set(voice.FLOW_CAUSES) == want
    assert len(want) == 4
