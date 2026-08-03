"""Tests for the PURE extended-trading-hours eligibility extraction (Task C1).

Two cases here are load-bearing, and both fail SAFE — toward *not* widening
collection: a chain missing ``ethOptionEligible`` reads False (never True), and a
stale-dated cache envelope yields an empty eligible set. A false positive would
send the GTH poll after a symbol that does not quote in extended hours, burning
API budget on empty responses.
"""
from services.options_svc import eth


# ── chain_eth_eligible ──────────────────────────────────────────────────────

def test_chain_eth_eligible_true():
    assert eth.chain_eth_eligible({"symbol": "NVDA", "ethOptionEligible": True}) is True


def test_chain_eth_eligible_false():
    assert eth.chain_eth_eligible({"symbol": "SPY", "ethOptionEligible": False}) is False


def test_chain_eth_eligible_missing_field_is_false():
    """LOAD-BEARING: an absent field must never read as eligible."""
    assert eth.chain_eth_eligible({"symbol": "SPY", "callExpDateMap": {}}) is False


def test_chain_eth_eligible_none_chain_is_false():
    assert eth.chain_eth_eligible(None) is False


def test_chain_eth_eligible_non_dict_is_false():
    assert eth.chain_eth_eligible("ethOptionEligible") is False
    assert eth.chain_eth_eligible([{"ethOptionEligible": True}]) is False


def test_chain_eth_eligible_truthy_non_bool_coerces_to_true():
    """A JSON-ish 'true' string still means eligible; the return stays a real bool."""
    out = eth.chain_eth_eligible({"ethOptionEligible": "true"})
    assert out is True


def test_chain_eth_eligible_returns_a_real_bool():
    out = eth.chain_eth_eligible({"ethOptionEligible": 0})
    assert out is False


# ── merge_eligibility ───────────────────────────────────────────────────────

def test_merge_eligibility_seeds_an_empty_envelope():
    out = eth.merge_eligibility(None, "NVDA", True, date_iso="2026-08-17")
    assert out == {"date": "2026-08-17", "symbols": {"NVDA": True}}


def test_merge_eligibility_accumulates_across_calls():
    out = eth.merge_eligibility({}, "NVDA", True, date_iso="2026-08-17")
    out = eth.merge_eligibility(out, "SPY", False, date_iso="2026-08-17")
    out = eth.merge_eligibility(out, "TSLA", True, date_iso="2026-08-17")
    assert out == {"date": "2026-08-17",
                   "symbols": {"NVDA": True, "SPY": False, "TSLA": True}}


def test_merge_eligibility_updates_an_existing_symbol():
    prior = {"date": "2026-08-17", "symbols": {"NVDA": True}}
    out = eth.merge_eligibility(prior, "NVDA", False, date_iso="2026-08-17")
    assert out["symbols"] == {"NVDA": False}


def test_merge_eligibility_new_date_resets_the_map():
    """Cboe re-balances the eligible list semi-annually — a daily reset means a
    dropped symbol falls out within one session rather than lingering forever."""
    prior = {"date": "2026-08-17", "symbols": {"NVDA": True, "MU": True}}
    out = eth.merge_eligibility(prior, "NVDA", True, date_iso="2026-08-18")
    assert out == {"date": "2026-08-18", "symbols": {"NVDA": True}}


def test_merge_eligibility_does_not_mutate_prior():
    prior = {"date": "2026-08-17", "symbols": {"NVDA": True}}
    eth.merge_eligibility(prior, "SPY", False, date_iso="2026-08-17")
    assert prior == {"date": "2026-08-17", "symbols": {"NVDA": True}}


def test_merge_eligibility_non_dict_prior_degrades_cleanly():
    for junk in (None, "", [], 7, {"date": "2026-08-17", "symbols": "nope"}):
        out = eth.merge_eligibility(junk, "NVDA", True, date_iso="2026-08-17")
        assert out == {"date": "2026-08-17", "symbols": {"NVDA": True}}


def test_merge_eligibility_coerces_the_flag_to_bool():
    out = eth.merge_eligibility({}, "NVDA", 1, date_iso="2026-08-17")
    assert out["symbols"]["NVDA"] is True


# ── eligible_symbols ────────────────────────────────────────────────────────

def _payload():
    return {"date": "2026-08-17",
            "symbols": {"NVDA": True, "TSLA": True, "SPY": False, "IWM": False}}


def test_eligible_symbols_returns_only_the_true_ones():
    assert eth.eligible_symbols(_payload()) == {"NVDA", "TSLA"}


def test_eligible_symbols_honors_a_matching_date():
    assert eth.eligible_symbols(_payload(), date_iso="2026-08-17") == {"NVDA", "TSLA"}


def test_eligible_symbols_stale_date_is_empty():
    """LOAD-BEARING: yesterday's map must not authorize today's GTH poll."""
    assert eth.eligible_symbols(_payload(), date_iso="2026-08-18") == set()


def test_eligible_symbols_no_date_arg_ignores_the_date():
    """The cold-start read (before today's harvest) deliberately opts out of the
    date gate so it can fall back to the previous session's map."""
    assert eth.eligible_symbols(_payload(), date_iso=None) == {"NVDA", "TSLA"}


def test_eligible_symbols_malformed_payload_is_empty():
    for junk in (None, "", [], 7, {}, {"symbols": None},
                 {"date": "2026-08-17", "symbols": ["NVDA"]}):
        assert eth.eligible_symbols(junk) == set()


def test_eligible_symbols_returns_a_set():
    assert isinstance(eth.eligible_symbols(_payload()), set)


def test_cache_key_is_the_documented_one():
    assert eth.CACHE_KEY == "cache:options:eth_eligible"
