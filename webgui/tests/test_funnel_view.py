"""``pages/options/funnel_view`` turns the scan funnel into sentences.

The entries below are HAND-BUILT from the engine's own key literals rather than
produced by running ``run_full_scan``: Tier 1 takes no ``sys.path`` glue into a
hyphenated app folder, so a webgui test cannot import ``scanner_engine`` without
breaking the rule the whole tier is pinned on. ``test_keys_mirror_the_engine``
closes the gap the other way — it READS the engine's source as text and fails if
a counter this module renders is not one the engine writes.

The tests that carry the weight are the two that cannot be satisfied by a
fallback: every width reason and every stage must produce a DISTINCT sentence
that never prints its own raw code, and nothing may print a zero the payload did
not supply.
"""
import pathlib
import re
import subprocess
import sys

import pytest

from pages.options import funnel_view as fv

REPO = pathlib.Path(__file__).resolve().parents[2]
ENGINE = REPO / "options-scanner" / "scanner_engine.py"


# ── fixtures, in the engine's own shape ─────────────────────────────────────

def _strikes(**over):
    """A strike tally that PARTITIONS, exactly as ``screen_spreads`` promises:

    delta_pass == mark_fail + delta_ceiling + em_fail + liq_fail_short
                  + width_found + sum(width_reasons.values())
    """
    d = {"expiration_sides_in_window": 4,
         "expiration_sides_skipped_earnings": 0,
         "delta_reject": 120,
         "delta_pass": 60,
         "mark_fail": 2,
         "delta_ceiling": 5,
         "em_fail": 9,
         "liq_fail_short": 6,
         "width_found": 3,
         "strikes_dropped_no_delta": 0,
         "strikes_dropped_off_increment": 0,
         "width_reasons": {"over_trade_cap": 35}}
    d.update(over)
    return d


def _spreads(**over):
    d = {"built": 3, "momentum_veto": 0, "iron_condors": 1,
         "kept_after_cap": 4, "regime_pass_added": 0, "regime_filter": 0,
         "below_iv_floor": 0, "no_iv_history": 0, "gamma_gate": 0,
         "emitted": 4}
    d.update(over)
    return d


def _directional(**over):
    d = {"windows_without_candidates": 0, "built": 8, "vol_gate": 2,
         "score_cut": 4, "capped": 0, "emitted": 2, "build_failed": False}
    d.update(over)
    return d


def _entry(strikes=None, spreads=None, directional=None, **over):
    e = {"price": 178.42, "iv_rank": 41.0, "earnings_date": "2026-11-19",
         "stop": None,
         "buckets": {
             "0DTE": {"chain": True, "strikes": _strikes(),
                      "spreads": _spreads()},
             "SWING": {"chain": True,
                       "strikes": strikes if strikes is not None else _strikes(),
                       "spreads": spreads if spreads is not None else _spreads()},
             "DIRECTIONAL": (directional if directional is not None
                             else _directional())}}
    e.update(over)
    return e


def _payload(**symbols):
    return {"timestamp": "2026-09-15T09:31:00-05:00", "symbols": dict(symbols)}


def _headline(**kw):
    return fv.bucket_card(_entry(**kw), "SWING", symbol="MU")["headline"]


def _labels(card):
    return [s["label"] for s in card["stages"]]


def _remaining(card):
    return {s["label"]: s["remaining"] for s in card["stages"]}


def _binding(card):
    return [s["label"] for s in card["stages"] if s["binding"]]


def _no_raw_code(key, text):
    """A snake_case stage key must never appear verbatim in prose.

    Only the underscored keys are checked: ``liquid``, ``priced``, ``built``
    and ``emitted`` are ordinary English words, and a sentence about the
    liquidity gate legitimately contains "liquid". The distinctness tests are
    what catch a stage falling back to a shared sentence.
    """
    if "_" in key:
        assert key not in text, f"{key} fell back to its raw code"


# ── empty_symbols ───────────────────────────────────────────────────────────

def test_empty_symbols_returns_the_zero_emitters_sorted():
    payload = _payload(
        MU=_entry(spreads=_spreads(emitted=0)),
        AAPL=_entry(spreads=_spreads(emitted=2)),
        ABNB=_entry(spreads=_spreads(emitted=0)),
        ZM=_entry(spreads=_spreads(emitted=0)))
    assert fv.empty_symbols(payload, "SWING") == ["ABNB", "MU", "ZM"]


def test_empty_symbols_skips_a_symbol_whose_bucket_is_missing():
    """A bucket that is not there has not emitted zero - it has said nothing.

    Listing it as "produced nothing" is the same claim as printing a 0 for it.
    """
    e = _entry()
    del e["buckets"]["SWING"]
    payload = _payload(MU=e, ZM=_entry(spreads=_spreads(emitted=0)))
    assert fv.empty_symbols(payload, "SWING") == ["ZM"]


def test_empty_symbols_skips_a_bucket_with_no_emitted_count():
    e = _entry()
    del e["buckets"]["SWING"]["spreads"]["emitted"]
    payload = _payload(MU=e, ZM=_entry(spreads=_spreads(emitted=0)))
    assert fv.empty_symbols(payload, "SWING") == ["ZM"]


def test_empty_symbols_reads_the_directional_bucket_at_its_own_level():
    payload = _payload(MU=_entry(directional=_directional(emitted=0)),
                       ZM=_entry(directional=_directional(emitted=3)))
    assert fv.empty_symbols(payload, "DIRECTIONAL") == ["MU"]


def test_empty_symbols_includes_a_symbol_the_scan_stopped_on():
    payload = _payload(MU=_entry(spreads=_spreads(emitted=0), stop="no_quote"))
    assert fv.empty_symbols(payload, "SWING") == ["MU"]


@pytest.mark.parametrize("payload", [None, {}, {"symbols": None},
                                     {"symbols": {"MU": None}},
                                     {"symbols": {"MU": "junk"}}, "junk"])
def test_empty_symbols_never_raises_on_junk(payload):
    assert fv.empty_symbols(payload, "SWING") == []


# ── the stage list over a realistic entry ───────────────────────────────────

def test_stage_list_runs_strikes_then_spreads_with_the_engines_arithmetic():
    card = fv.bucket_card(_entry(), "SWING", symbol="MU")
    rem = _remaining(card)
    # Strike side: delta_pass, then each counter subtracted in search order.
    assert rem[fv.LABELS["delta_band"]] == 60
    assert rem[fv.LABELS["priced"]] == 58            # - mark_fail 2
    assert rem[fv.LABELS["delta_ceiling"]] == 53     # - delta_ceiling 5
    assert rem[fv.LABELS["em_window"]] == 44         # - em_fail 9
    assert rem[fv.LABELS["liquid"]] == 38            # - liq_fail_short 6
    assert rem[fv.LABELS["width_found"]] == 3        # absolute, from the tally
    # Spread side. kept_after_cap is ABSOLUTE and already carries the condors,
    # which is why it can exceed built - momentum_veto.
    assert rem[fv.LABELS["built"]] == 3
    assert rem[fv.LABELS["momentum_veto"]] == 3
    assert rem[fv.LABELS["kept_after_cap"]] == 4
    assert rem[fv.LABELS["regime_pass_added"]] == 4
    assert rem[fv.LABELS["regime_filter"]] == 4
    assert rem[fv.LABELS["iv_floor"]] == 4
    assert rem[fv.LABELS["gamma_gate"]] == 4
    assert rem[fv.LABELS["emitted"]] == 4


def test_the_iron_condors_are_folded_in_where_the_engine_adds_them():
    """``kept_after_cap`` = top-3 PCS + top-3 CCS + the condors BUILT from them.

    So the count can RISE across that stage, and rendering it as a subtraction
    from ``built`` would show a number the engine never computed.
    """
    card = fv.bucket_card(
        _entry(spreads=_spreads(built=6, momentum_veto=0, iron_condors=2,
                                kept_after_cap=8, emitted=8)),
        "SWING", symbol="MU")
    rem = _remaining(card)
    assert rem[fv.LABELS["momentum_veto"]] == 6
    assert rem[fv.LABELS["kept_after_cap"]] == 8


def test_the_regime_pass_adds_rather_than_removes():
    card = fv.bucket_card(
        _entry(spreads=_spreads(regime_pass_added=2, emitted=6)),
        "SWING", symbol="MU")
    assert _remaining(card)[fv.LABELS["regime_pass_added"]] == 6


def test_every_stage_carries_a_label_a_count_and_a_binding_flag():
    card = fv.bucket_card(_entry(), "SWING", symbol="MU")
    assert card["stages"]
    for s in card["stages"]:
        assert set(s) == {"label", "remaining", "binding"}
        assert isinstance(s["label"], str) and s["label"]
        assert isinstance(s["remaining"], int)
        assert isinstance(s["binding"], bool)


def test_a_remaining_count_never_goes_negative():
    """A funnel that has lost a counter must not render a negative survivor."""
    card = fv.bucket_card(
        _entry(strikes=_strikes(delta_pass=2, mark_fail=40, width_found=0,
                                width_reasons={})),
        "SWING", symbol="MU")
    assert all(s["remaining"] >= 0 for s in card["stages"])


# ── the binding marker ──────────────────────────────────────────────────────

def test_binding_marks_the_first_stage_whose_remaining_is_zero():
    card = fv.bucket_card(
        _entry(strikes=_strikes(delta_pass=60, mark_fail=2, delta_ceiling=5,
                                em_fail=9, liq_fail_short=44, width_found=0,
                                width_reasons={}),
               spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                emitted=0)),
        "SWING", symbol="MU")
    assert _binding(card) == [fv.LABELS["liquid"]]


def test_only_one_stage_is_ever_binding():
    card = fv.bucket_card(
        _entry(strikes=_strikes(delta_pass=0, mark_fail=0, delta_ceiling=0,
                                em_fail=0, liq_fail_short=0, width_found=0,
                                width_reasons={}),
               spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                emitted=0)),
        "SWING", symbol="MU")
    assert _binding(card) == [fv.LABELS["delta_band"]]


def test_nothing_is_binding_when_the_bucket_emitted():
    card = fv.bucket_card(_entry(), "SWING", symbol="MU")
    assert _binding(card) == []
    assert "4 signals" in card["headline"]


def test_the_emitted_stage_binds_when_every_gate_passed_and_nothing_landed():
    card = fv.bucket_card(_entry(spreads=_spreads(emitted=0)),
                          "SWING", symbol="MU")
    assert _binding(card) == [fv.LABELS["emitted"]]


# ── the headline: one sentence per binding stage ────────────────────────────

def test_the_headline_names_the_symbol_and_the_bucket():
    assert _headline().startswith("MU · Swing:")
    assert fv.bucket_card(_entry(), "0DTE", symbol="MU")["headline"].startswith(
        "MU · 0-DTE:")
    assert fv.bucket_card(_entry(), "DIRECTIONAL",
                          symbol="MU")["headline"].startswith("MU · Directional:")


def test_the_headline_reads_the_symbol_off_the_entry_when_not_passed():
    e = _entry(symbol="MU")
    assert fv.bucket_card(e, "SWING")["headline"].startswith("MU · Swing:")


def test_the_headline_drops_the_prefix_when_no_symbol_is_known():
    assert fv.bucket_card(_entry(), "SWING")["headline"].startswith("Swing:")


def test_the_design_docs_worked_example():
    """The sentence the design doc set as the target, minus the cap FIGURE.

    The funnel does not carry ``max_risk_dollars``, and the two books disagree
    about it ($750 Ledger, $250 Account), so the number is named only when the
    payload supplies it. See ``test_the_cap_figure_*`` below.
    """
    h = _headline(strikes=_strikes(liq_fail_short=6, width_found=0,
                                   width_reasons={"over_trade_cap": 38}),
                  spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                   emitted=0))
    assert h == ("MU · Swing: 38 short strikes priced, and every width that "
                 "cleared the credit and edge floors cost more than the "
                 "per-trade risk cap.")


def test_the_cap_figure_is_named_only_when_the_payload_carries_it():
    e = _entry(strikes=_strikes(liq_fail_short=6, width_found=0,
                                width_reasons={"over_trade_cap": 38}),
               spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                emitted=0),
               max_risk_dollars=250)
    assert "$250 per-trade risk cap" in fv.bucket_card(e, "SWING",
                                                       symbol="MU")["headline"]


def test_the_cap_figure_is_omitted_when_it_is_not_a_real_reading():
    for junk in (None, 0, "", float("nan"), "abc", True):
        e = _entry(strikes=_strikes(liq_fail_short=6, width_found=0,
                                    width_reasons={"over_trade_cap": 38}),
                   spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                    emitted=0),
                   max_risk_dollars=junk)
        assert "$" not in fv.bucket_card(e, "SWING", symbol="MU")["headline"]


# Every width stage gets a sentence of its own — no fallback to a raw code.
def _width_headline(reason, n=38):
    return _headline(strikes=_strikes(liq_fail_short=6, width_found=0,
                                      width_reasons={reason: n}),
                     spreads=_spreads(built=0, iron_condors=0,
                                      kept_after_cap=0, emitted=0))


@pytest.mark.parametrize("reason", fv.WIDTH_STAGES)
def test_every_width_reason_has_its_own_sentence(reason):
    h = _width_headline(reason)
    assert reason not in h, f"{reason} fell back to its raw code"
    assert h.startswith("MU · Swing: 38 short strikes priced, and ")
    assert h.endswith(".")
    assert len(h) > 60


def test_the_width_reason_sentences_are_all_distinct():
    seen = {r: _width_headline(r) for r in fv.WIDTH_STAGES}
    assert len(set(seen.values())) == len(fv.WIDTH_STAGES), seen


def test_no_positive_ev_has_a_sentence_although_it_is_unreachable_today():
    """Unreachable at the shipped ``EDGE_MARGIN`` (recorded in 65e1612): the
    edge floor already demands credit/width > |delta| + margin, which is the
    zero-margin E[PnL] > 0 test with the margin made explicit. The sentence
    exists so a margin change does not ship a raw code to the screen.
    """
    h = _width_headline("no_positive_ev")
    assert "expected" in h.lower()
    assert "no_positive_ev" not in h


def test_the_top_width_reason_wins_and_ties_break_in_search_order():
    h = _width_headline("credit_floor", n=1)
    assert "credit floor" in h
    two = _headline(strikes=_strikes(liq_fail_short=6, width_found=0,
                                     width_reasons={"credit_floor": 20,
                                                    "over_trade_cap": 18}),
                    spreads=_spreads(built=0, iron_condors=0,
                                     kept_after_cap=0, emitted=0))
    assert "credit floor" in two
    # A tie is broken by WIDTH_STAGES order — the earlier stage is the one the
    # search reached first, and reporting the later one would send the reader
    # past the real wall.
    tie = _headline(strikes=_strikes(liq_fail_short=6, width_found=0,
                                     width_reasons={"credit_floor": 19,
                                                    "over_trade_cap": 19}),
                    spreads=_spreads(built=0, iron_condors=0,
                                     kept_after_cap=0, emitted=0))
    assert "credit floor" in tie


def test_a_width_search_that_recorded_no_reason_says_so():
    h = _headline(strikes=_strikes(liq_fail_short=44, width_found=0,
                                   width_reasons={}),
                  spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                   emitted=0))
    # liq_fail_short eats the whole band, so LIQUID binds, not width_found.
    assert "liquidity" in h
    h2 = _headline(strikes=_strikes(delta_pass=44, mark_fail=0,
                                    delta_ceiling=0, em_fail=0,
                                    liq_fail_short=0, width_found=0,
                                    width_reasons={}),
                   spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                    emitted=0))
    assert "no reason" in h2


# Every strike + spread + directional stage gets a sentence of its own.
STRIKE_BINDERS = {
    "delta_band": dict(strikes=_strikes(delta_pass=0, mark_fail=0,
                                        delta_ceiling=0, em_fail=0,
                                        liq_fail_short=0, width_found=0,
                                        width_reasons={})),
    "priced": dict(strikes=_strikes(mark_fail=60, delta_ceiling=0, em_fail=0,
                                    liq_fail_short=0, width_found=0,
                                    width_reasons={})),
    "delta_ceiling": dict(strikes=_strikes(mark_fail=0, delta_ceiling=60,
                                           em_fail=0, liq_fail_short=0,
                                           width_found=0, width_reasons={})),
    "em_window": dict(strikes=_strikes(mark_fail=0, delta_ceiling=0,
                                       em_fail=60, liq_fail_short=0,
                                       width_found=0, width_reasons={})),
    "liquid": dict(strikes=_strikes(mark_fail=0, delta_ceiling=0, em_fail=0,
                                    liq_fail_short=60, width_found=0,
                                    width_reasons={})),
    "width_found": dict(strikes=_strikes(width_found=0,
                                         width_reasons={"edge_floor": 38})),
}

SPREAD_BINDERS = {
    "built": dict(spreads=_spreads(built=0, iron_condors=0, kept_after_cap=0,
                                   emitted=0)),
    "momentum_veto": dict(spreads=_spreads(built=3, momentum_veto=3,
                                           iron_condors=0, kept_after_cap=0,
                                           emitted=0)),
    "kept_after_cap": dict(spreads=_spreads(built=3, momentum_veto=0,
                                            iron_condors=0, kept_after_cap=0,
                                            emitted=0)),
    "regime_filter": dict(spreads=_spreads(regime_filter=4, emitted=0)),
    "iv_floor": dict(spreads=_spreads(below_iv_floor=4, emitted=0)),
    "gamma_gate": dict(spreads=_spreads(gamma_gate=4, emitted=0)),
    "emitted": dict(spreads=_spreads(emitted=0)),
}


@pytest.mark.parametrize("stage", sorted(STRIKE_BINDERS))
def test_every_strike_stage_has_its_own_binding_sentence(stage):
    kw = dict(STRIKE_BINDERS[stage])
    kw.setdefault("spreads", _spreads(built=0, iron_condors=0,
                                      kept_after_cap=0, emitted=0))
    card = fv.bucket_card(_entry(**kw), "SWING", symbol="MU")
    assert _binding(card) == [fv.LABELS[stage]], stage
    assert card["headline"].endswith(".")
    _no_raw_code(stage, card["headline"])


@pytest.mark.parametrize("stage", sorted(SPREAD_BINDERS))
def test_every_spread_stage_has_its_own_binding_sentence(stage):
    card = fv.bucket_card(_entry(**SPREAD_BINDERS[stage]), "SWING",
                          symbol="MU")
    assert _binding(card) == [fv.LABELS[stage]], stage
    assert card["headline"].endswith(".")
    _no_raw_code(stage, card["headline"])


def test_the_stage_sentences_are_all_distinct():
    out = {}
    for stage, kw in STRIKE_BINDERS.items():
        kw = dict(kw)
        kw.setdefault("spreads", _spreads(built=0, iron_condors=0,
                                          kept_after_cap=0, emitted=0))
        out[stage] = _headline(**kw)
    for stage, kw in SPREAD_BINDERS.items():
        out[stage] = _headline(**kw)
    assert len(set(out.values())) == len(out), out


def test_the_regime_pass_stage_has_a_sentence_even_though_it_cannot_bind():
    """It can only be zero when the stage before it is already zero, so the
    earlier stage always binds first. The sentence exists so a future counter
    order change cannot ship a raw code.
    """
    assert fv.LABELS["regime_pass_added"] in fv.STAGE_SENTENCES
    s = fv.STAGE_SENTENCES[fv.LABELS["regime_pass_added"]]
    assert callable(s) or isinstance(s, str)


def test_the_two_volatility_floor_reasons_read_differently():
    below = _headline(spreads=_spreads(below_iv_floor=4, emitted=0))
    none_ = _headline(spreads=_spreads(no_iv_history=4, emitted=0))
    assert below != none_
    assert "cheap" in below
    assert "history" in none_
    assert "no_iv_history" not in none_ and "below_iv_floor" not in below


def test_the_dominant_volatility_reason_wins():
    h = _headline(spreads=_spreads(below_iv_floor=1, no_iv_history=3,
                                   emitted=0))
    assert "history" in h


# ── the DIRECTIONAL bucket ──────────────────────────────────────────────────

def test_the_directional_bucket_has_its_own_stage_list():
    card = fv.bucket_card(_entry(), "DIRECTIONAL", symbol="MU")
    rem = _remaining(card)
    assert rem[fv.LABELS["dir_built"]] == 8
    assert rem[fv.LABELS["dir_vol_gate"]] == 6
    assert rem[fv.LABELS["dir_score_cut"]] == 2
    assert rem[fv.LABELS["dir_capped"]] == 2
    assert rem[fv.LABELS["emitted"]] == 2
    assert _binding(card) == []


DIR_BINDERS = {
    "dir_built": dict(directional=_directional(built=0, vol_gate=0,
                                               score_cut=0, capped=0,
                                               emitted=0,
                                               windows_without_candidates=2)),
    "dir_vol_gate": dict(directional=_directional(built=8, vol_gate=8,
                                                  score_cut=0, capped=0,
                                                  emitted=0)),
    "dir_score_cut": dict(directional=_directional(built=8, vol_gate=0,
                                                   score_cut=8, capped=0,
                                                   emitted=0)),
    "dir_capped": dict(directional=_directional(built=8, vol_gate=0,
                                                score_cut=0, capped=8,
                                                emitted=0)),
    "emitted": dict(directional=_directional(built=8, vol_gate=2, score_cut=4,
                                             capped=0, emitted=0)),
}


@pytest.mark.parametrize("stage", sorted(DIR_BINDERS))
def test_every_directional_stage_has_its_own_binding_sentence(stage):
    card = fv.bucket_card(_entry(**DIR_BINDERS[stage]), "DIRECTIONAL",
                          symbol="MU")
    assert _binding(card) == [fv.LABELS[stage]], stage
    assert card["headline"].endswith(".")


def test_the_directional_sentences_are_all_distinct():
    out = {k: fv.bucket_card(_entry(**kw), "DIRECTIONAL",
                             symbol="MU")["headline"]
           for k, kw in DIR_BINDERS.items()}
    assert len(set(out.values())) == len(out), out


def test_a_crashed_directional_build_says_so_instead_of_reading_as_zero():
    """The whole block sits under a bare ``except``; without the flag a crash
    renders as an honest zero, which is the one thing it is not.
    """
    card = fv.bucket_card(
        _entry(directional=_directional(built=0, vol_gate=0, score_cut=0,
                                        capped=0, emitted=0,
                                        build_failed=True)),
        "DIRECTIONAL", symbol="MU")
    assert "failed" in card["headline"]
    assert card["stages"] == []


def test_a_window_that_offered_nothing_reads_differently_from_no_chain():
    offered = fv.bucket_card(
        _entry(directional=_directional(built=0, vol_gate=0, score_cut=0,
                                        capped=0, emitted=0,
                                        windows_without_candidates=2)),
        "DIRECTIONAL", symbol="MU")["headline"]
    no_chain = fv.bucket_card(
        _entry(directional=_directional(built=0, vol_gate=0, score_cut=0,
                                        capped=0, emitted=0,
                                        windows_without_candidates=0)),
        "DIRECTIONAL", symbol="MU")["headline"]
    assert offered != no_chain
    assert "2" in offered


# ── never a zero the payload did not supply ─────────────────────────────────

def test_a_symbol_absent_from_the_payload_is_said_in_words():
    card = fv.bucket_card(None, "SWING", symbol="MU")
    assert card["headline"] == "MU · Swing: This symbol was not in the last scan."
    assert card["stages"] == []


@pytest.mark.parametrize("bucket", ["0DTE", "SWING", "DIRECTIONAL"])
def test_an_absent_symbol_prints_no_stages_in_any_bucket(bucket):
    assert fv.bucket_card(None, bucket)["stages"] == []


def test_a_stopped_symbol_names_the_stop_and_prints_no_stages():
    quote = fv.bucket_card(_entry(stop="no_quote"), "SWING", symbol="MU")
    assert quote["headline"] == ("MU · Swing: Schwab returned no quote for "
                                 "this symbol.")
    assert quote["stages"] == []
    data = fv.bucket_card(_entry(stop="no_data"), "SWING", symbol="MU")
    assert data["headline"] == ("MU · Swing: The scan could not load this "
                                "symbol's price history or chains.")
    assert data["stages"] == []


def test_an_unrecognised_stop_still_refuses_to_print_stages():
    card = fv.bucket_card(_entry(stop="something_new"), "SWING", symbol="MU")
    assert card["stages"] == []
    assert "something_new" not in card["headline"]


@pytest.mark.parametrize("bucket", ["0DTE", "SWING", "DIRECTIONAL"])
def test_a_stop_silences_every_bucket(bucket):
    assert fv.bucket_card(_entry(stop="no_quote"), bucket)["stages"] == []


def test_a_window_with_no_usable_chain_prints_no_stages():
    e = _entry()
    e["buckets"]["SWING"]["chain"] = False
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert card["stages"] == []
    assert "chain" in card["headline"]


def test_a_missing_bucket_is_said_in_words():
    e = _entry()
    del e["buckets"]["SWING"]
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert card["stages"] == []
    assert "no" in card["headline"].lower()


def test_a_chain_read_with_no_strike_detail_shows_only_the_spread_stages():
    """``screen_spreads`` returns early on an empty chain without writing the
    tally, so the strike counts are ABSENT - not zero.
    """
    e = _entry()
    e["buckets"]["SWING"]["strikes"] = {}
    card = fv.bucket_card(e, "SWING", symbol="MU")
    labels = _labels(card)
    assert fv.LABELS["delta_band"] not in labels
    assert fv.LABELS["width_found"] not in labels
    assert fv.LABELS["built"] in labels


def test_a_junk_counter_is_treated_as_absent_not_as_zero():
    e = _entry()
    e["buckets"]["SWING"]["strikes"] = {"delta_pass": "lots"}
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert fv.LABELS["delta_band"] not in _labels(card)


@pytest.mark.parametrize("entry", [{}, {"buckets": None}, {"buckets": "x"},
                                   {"buckets": {"SWING": None}},
                                   {"buckets": {"SWING": "x"}}])
def test_bucket_card_never_raises_on_junk(entry):
    card = fv.bucket_card(entry, "SWING", symbol="MU")
    assert card["stages"] == []
    assert isinstance(card["headline"], str) and card["headline"]


def test_an_unknown_bucket_name_is_said_in_words():
    card = fv.bucket_card(_entry(), "WEEKLY", symbol="MU")
    assert card["stages"] == []


# ── the staleness note ──────────────────────────────────────────────────────

def test_the_note_says_so_when_the_funnel_is_older_than_the_scan():
    payload = _payload(MU=_entry())
    assert fv.stale_note(payload, "2026-09-15T14:02:00-05:00") == \
        "From an earlier scan."


def test_the_note_is_absent_when_the_stamps_agree():
    payload = _payload(MU=_entry())
    assert fv.stale_note(payload, payload["timestamp"]) is None


@pytest.mark.parametrize("scan_ts", [None, ""])
def test_the_note_claims_nothing_when_a_stamp_is_missing(scan_ts):
    """Two stamps are needed to say one is older. With one, say nothing."""
    assert fv.stale_note(_payload(MU=_entry()), scan_ts) is None
    assert fv.stale_note({"timestamp": None}, "2026-09-15T14:02:00-05:00") is None


def test_the_note_rides_onto_the_card():
    card = fv.bucket_card(_entry(), "SWING", symbol="MU",
                          note="From an earlier scan.")
    assert card["note"] == "From an earlier scan."


def test_the_card_carries_no_note_by_default():
    assert fv.bucket_card(_entry(), "SWING", symbol="MU")["note"] is None


def test_the_note_survives_every_early_return():
    for entry in (None, _entry(stop="no_quote")):
        card = fv.bucket_card(entry, "SWING", symbol="MU", note="N.")
        assert card["note"] == "N."


# ── the module is pure, and mirrors the engine ──────────────────────────────

# `math` may legitimately be ABSENT: an interpreter can preload it during site
# setup, and then it is in sys.modules before the probe's snapshot. Mirrors
# test_book_caps_tier1.py.
EXPECTED_IMPORTS = {"math", "pages", "pages.fmt", "pages.options",
                    "pages.options.funnel_view"}
REQUIRED_IMPORTS = {"pages.options.funnel_view", "pages.fmt"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import pages.options.funnel_view
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(new)))
""" % (REPO / "webgui")


def test_funnel_view_imports_only_its_pinned_set():
    """PURE: no nicegui, no bus_client, no engine, no I/O.

    Run in a FRESH interpreter so a transitive import cannot hide behind a
    module some earlier test already loaded.
    """
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO / "webgui",
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip().splitlines()[-1]
    assert line.startswith("NEW:"), r.stdout
    new = set(filter(None, line[len("NEW:"):].split(",")))
    assert new <= EXPECTED_IMPORTS, sorted(new - EXPECTED_IMPORTS)
    assert REQUIRED_IMPORTS <= new, sorted(REQUIRED_IMPORTS - new)


def test_funnel_view_carries_no_inline_style():
    """The Tailwind-first guard, kept here rather than in
    ``test_no_inline_style.py`` because this module is a PURE builder with no
    widgets of its own. The PAGE that mounts it belongs in that file's list.
    """
    src = (REPO / "webgui" / "pages" / "options"
           / "funnel_view.py").read_text(encoding="utf-8")
    assert ".style(" not in src
    assert ":style=" not in src


def test_keys_mirror_the_engine():
    """Every counter this module renders is one ``run_full_scan`` writes.

    Read as TEXT, never imported: Tier 1 takes no ``sys.path`` glue into a
    hyphenated app folder. A counter renamed in the engine fails here rather
    than rendering an em-dash on a page nobody is watching.
    """
    src = ENGINE.read_text(encoding="utf-8")
    seed = re.search(r"def _spread_bucket\(\):(.*?)\n\n", src, re.S)
    assert seed, "the engine's _spread_bucket seed moved"
    for key in fv.SPREAD_COUNTERS:
        assert f'"{key}"' in seed.group(1), key
    dir_seed = re.search(r'"DIRECTIONAL": \{(.*?)\}\},', src, re.S)
    assert dir_seed, "the engine's DIRECTIONAL seed moved"
    for key in fv.DIRECTIONAL_COUNTERS:
        assert f'"{key}"' in dir_seed.group(1), key
    for key in fv.STRIKE_COUNTERS:
        assert f'("{key}"' in src or f'"{key}",' in src, key


def test_width_stages_mirror_the_engines_tuple():
    src = ENGINE.read_text(encoding="utf-8")
    block = re.search(r"WIDTH_STAGES = \((.*?)\)\n", src, re.S)
    assert block, "the engine's WIDTH_STAGES moved"
    names = tuple(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert names == fv.WIDTH_STAGES, (names, fv.WIDTH_STAGES)


# ── a chain that arrived with no underlying price (engine commit 7c8b19f) ───

def test_a_chain_with_no_underlying_price_says_so_not_no_chain():
    """``chain: True, underlying_zero: True`` is NOT the no-chain case: the
    expirations WERE listed, the spot was not, and ``screen_spreads`` returns
    before counting - so the zero-filled strike tally must not be read as
    "no expiration was listed" (engine commit 7c8b19f).
    """
    e = _entry()
    e["buckets"]["SWING"].update({"chain": True, "underlying_zero": True,
                                  "strikes": _zeroed_strikes()})
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert card["stages"] == []
    assert card["headline"].endswith(fv.NO_UNDERLYING)
    assert card["headline"] != _prefixed(fv.NO_CHAIN)
    assert "no expiration" not in card["headline"].lower()


def test_no_chain_still_reads_as_no_chain_when_the_flag_is_false():
    """The engine leaves ``underlying_zero`` False when no chain arrived at
    all; the two absences must keep reading differently."""
    e = _entry()
    e["buckets"]["SWING"].update({"chain": False, "underlying_zero": False})
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert card["headline"] == _prefixed(fv.NO_CHAIN)


def test_the_flag_is_ignored_once_a_chain_carried_a_price():
    e = _entry()
    e["buckets"]["SWING"]["underlying_zero"] = False
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert card["stages"], "the normal path must still count"


def test_a_payload_published_before_the_flag_existed_still_reads():
    """``underlying_zero`` is absent from every funnel published before
    7c8b19f, and ``strikes`` was ``{}`` rather than zero-filled there."""
    e = _entry()
    e["buckets"]["SWING"] = {"chain": True, "strikes": {},
                             "spreads": _spreads(emitted=0)}
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert fv.LABELS["built"] in _labels(card)
    assert fv.NO_UNDERLYING not in card["headline"]


def test_a_zero_filled_tally_still_names_the_delta_band():
    """The post-7c8b19f shape for a window that read a chain and found no
    listed expiration: every counter present and zero."""
    e = _entry()
    e["buckets"]["SWING"].update({"chain": True, "underlying_zero": False,
                                  "strikes": _zeroed_strikes()})
    card = fv.bucket_card(e, "SWING", symbol="MU")
    assert _binding(card) == [fv.LABELS["delta_band"]]


def _zeroed_strikes():
    return {k: (0 if k != "width_reasons" else {}) for k in _strikes()}


def _prefixed(sentence):
    return f"MU · {fv.BUCKET_LABELS['SWING']}: {sentence}"
