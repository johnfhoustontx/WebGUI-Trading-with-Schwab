"""shared.public_rescue: the public Rescue form's requests, validators and config."""
import datetime as dt
import pathlib
import subprocess
import sys

import pytest

from shared import public_rescue as pr

REPO = pathlib.Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 9, 21)
EXP = "2026-10-16"


def _pcs(**over):
    spec = {"symbol": "spy", "strategy": "PCS", "short_strike": 500.0,
            "long_strike": 495.0, "expiration": EXP, "quantity": 2,
            "entry_credit": 1.25}
    spec.update(over)
    return spec


# ── the stream and the keys ─────────────────────────────────────────────────

def test_the_stream_is_its_own_and_never_the_owners():
    assert pr.STREAM == "cmd:rescue_public"
    from shared import public_scan
    assert pr.STREAM != public_scan.STREAM


def test_result_keys_are_never_the_private_slot():
    key = pr.spec_key(pr.clean_spec(_pcs(), TODAY))
    assert pr.cache_key(pr.result_view(key)) != "cache:options:rescue:adhoc"
    assert pr.cache_key(pr.ladder_view("spy")) == "cache:options:pub_chain:SPY"


# ── clean_spec ──────────────────────────────────────────────────────────────

def test_a_put_credit_spread_normalizes():
    assert pr.clean_spec(_pcs(), TODAY) == {
        "symbol": "SPY", "strategy": "PCS", "expiration": EXP, "quantity": 2,
        "entry_credit": 1.25, "short_strike": 500.0, "long_strike": 495.0}


def test_unknown_keys_are_dropped_not_passed_through():
    spec = pr.clean_spec(_pcs(position_id=7, apply_kind="execute",
                              note="x" * 5000), TODAY)
    assert set(spec) == {"symbol", "strategy", "expiration", "quantity",
                         "entry_credit", "short_strike", "long_strike"}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"),
                                 True, False, "500", None, 0, -5, 2_000_000])
def test_an_unusable_strike_refuses_the_trade(bad):
    assert pr.clean_spec(_pcs(short_strike=bad), TODAY) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1.2", 1e9])
def test_an_unusable_credit_refuses_the_trade(bad):
    assert pr.clean_spec(_pcs(entry_credit=bad), TODAY) is None


def test_a_missing_credit_is_zero_as_the_service_reads_it():
    raw = _pcs()
    del raw["entry_credit"]
    assert pr.clean_spec(raw, TODAY)["entry_credit"] == 0.0


def test_a_debit_is_a_negative_credit_and_allowed():
    spec = pr.clean_spec(_pcs(strategy="VERT_PUT_DEBIT", short_strike=495.0,
                              long_strike=500.0, entry_credit=-2.1), TODAY)
    assert spec["entry_credit"] == -2.1


@pytest.mark.parametrize("bad", [0, 101, 2.5, True, float("nan"), "2"])
def test_an_unusable_quantity_refuses_the_trade(bad):
    assert pr.clean_spec(_pcs(quantity=bad), TODAY) is None


def test_a_whole_float_quantity_is_accepted():
    assert pr.clean_spec(_pcs(quantity=3.0), TODAY)["quantity"] == 3


@pytest.mark.parametrize("bad", ["COVERED_CALL", "", None, "pcs; flushall"])
def test_an_unknown_strategy_refuses_the_trade(bad):
    assert pr.clean_spec(_pcs(strategy=bad), TODAY) is None


def test_the_strategy_is_case_insensitive():
    assert pr.clean_spec(_pcs(strategy="pcs"), TODAY)["strategy"] == "PCS"


@pytest.mark.parametrize("bad", ["2026-13-01", "10/16/2026", "2026-10-16T00:00",
                                 "2026-09-01", "", None, 20261016])
def test_an_unusable_or_past_expiration_refuses_the_trade(bad):
    assert pr.clean_spec(_pcs(expiration=bad), TODAY) is None


def test_a_spread_needs_both_strikes_and_an_iron_condor_all_four():
    raw = _pcs()
    del raw["long_strike"]
    assert pr.clean_spec(raw, TODAY) is None
    ic = _pcs(strategy="IC", call_short=510.0)
    assert pr.clean_spec(ic, TODAY) is None
    ic["call_long"] = 515.0
    assert pr.clean_spec(ic, TODAY)["call_long"] == 515.0


def test_a_single_option_needs_only_its_strike():
    spec = pr.clean_spec({"symbol": "AAPL", "strategy": "LONG_CALL",
                          "short_strike": 230.0, "expiration": EXP,
                          "quantity": 1, "entry_credit": -3.4}, TODAY)
    assert spec["short_strike"] == 230.0 and "long_strike" not in spec


def test_a_butterfly_takes_a_legs_list():
    legs = [{"right": "call", "side": "long", "strike": 495, "qty": 1},
            {"right": "CALL", "side": "short", "strike": 500, "qty": 2},
            {"right": "CALL", "side": "LONG", "strike": 505, "qty": 1, "x": 1}]
    spec = pr.clean_spec({"symbol": "SPY", "strategy": "BUTTERFLY_CALL",
                          "legs": legs, "expiration": EXP, "quantity": 1,
                          "entry_credit": -1.0}, TODAY)
    assert spec["legs"] == [
        {"right": "CALL", "side": "long", "strike": 495.0, "qty": 1},
        {"right": "CALL", "side": "short", "strike": 500.0, "qty": 2},
        {"right": "CALL", "side": "long", "strike": 505.0, "qty": 1}]


@pytest.mark.parametrize("legs", [
    None, [], "legs",
    [{"right": "CALL", "side": "long", "strike": float("nan"), "qty": 1}],
    [{"right": "STOCK", "side": "long", "strike": 1, "qty": 1}],
    [{"right": "CALL", "side": "long", "strike": 1, "qty": 1}] * 5,
])
def test_a_range_structure_refuses_bad_legs(legs):
    assert pr.clean_spec({"symbol": "SPY", "strategy": "CONDOR_PUT",
                          "legs": legs, "expiration": EXP, "quantity": 1},
                         TODAY) is None


@pytest.mark.parametrize("bad", [None, "spy", [], 5])
def test_a_non_mapping_is_refused(bad):
    assert pr.clean_spec(bad, TODAY) is None


def test_an_invalid_symbol_refuses_the_trade():
    assert pr.clean_spec(_pcs(symbol="../x"), TODAY) is None


# ── keys ────────────────────────────────────────────────────────────────────

def test_the_same_trade_always_hashes_the_same_whatever_its_spelling():
    a = pr.clean_spec(_pcs(), TODAY)
    b = pr.clean_spec(_pcs(symbol=" SPY ", strategy="pcs", quantity=2.0,
                           short_strike=500, junk=1), TODAY)
    assert pr.spec_key(a) == pr.spec_key(b)
    assert pr.is_key(pr.spec_key(a))


def test_a_different_trade_hashes_differently():
    a = pr.spec_key(pr.clean_spec(_pcs(), TODAY))
    b = pr.spec_key(pr.clean_spec(_pcs(entry_credit=1.3), TODAY))
    assert a != b


def test_the_key_says_nothing_about_the_trade():
    key = pr.spec_key(pr.clean_spec(_pcs(), TODAY))
    assert "SPY" not in key.upper() and "500" not in key


def test_ladder_answer_keys_do_not_spell_the_symbol():
    assert "SPY" not in pr.ladder_key("SPY").upper()
    assert pr.ladder_key("SPY") != pr.ladder_key("SPY", EXP)


# ── commands ────────────────────────────────────────────────────────────────

def test_ladder_commands():
    assert pr.ladder_command(" spy ") == {"type": pr.LADDER_TYPE,
                                          "args": {"symbol": "SPY"}}
    assert pr.ladder_command("SPY", EXP, TODAY)["args"] == {"symbol": "SPY",
                                                            "expiry": EXP}
    assert pr.ladder_command("../x") is None
    assert pr.ladder_command("SPY", "nope", TODAY) is None


def test_compute_command_carries_only_the_normalized_trade():
    cmd = pr.compute_command(_pcs(extra=1), TODAY)
    assert cmd == {"type": pr.COMPUTE_TYPE,
                   "args": {"spec": pr.clean_spec(_pcs(), TODAY)}}
    assert pr.compute_command(_pcs(short_strike=float("nan")), TODAY) is None


def test_request_key_matches_what_each_side_computes():
    cmd = pr.compute_command(_pcs(), TODAY)
    assert pr.request_key(cmd) == pr.spec_key(cmd["args"]["spec"])
    assert pr.request_key(pr.ladder_command("SPY")) == pr.ladder_key("SPY")
    assert pr.request_key({"type": "rescue_apply", "args": {}}) is None
    assert pr.request_key("x") is None


def test_every_outcome_has_words():
    assert set(pr.OUTCOME_TEXT) == set(pr.OUTCOMES)


# ── config ──────────────────────────────────────────────────────────────────

def test_the_shipped_file_matches_the_defaults():
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "rescue_public.toml")
                            .read_text(encoding="utf-8"))
    for section, values in pr.DEFAULTS.items():
        assert shipped[section] == values, section


def test_limits_fall_back_on_bad_values(monkeypatch):
    # ``limits.daily_budget`` moved to ``budget.daily_budget`` (one budget shared
    # by every public worker), so the boolean fallback is exercised on another
    # limit here and on the budget in the test below.
    monkeypatch.setattr(pr, "load", lambda: {"limits": {"result_ttl_min": True,
                                                        "dedup_sec": 0,
                                                        "max_wait_sec": -3}})
    lim = pr.limits()
    assert lim["result_ttl_min"] == pr.DEFAULTS["limits"]["result_ttl_min"]
    assert lim["dedup_sec"] == 0
    assert lim["max_wait_sec"] == pr.DEFAULTS["limits"]["max_wait_sec"]


def test_there_is_one_shared_budget_and_no_per_tool_budgets():
    assert pr.DEFAULTS["budget"] == {"daily_budget": 600}
    assert pr.budget() == 600
    assert "daily_budget" not in pr.DEFAULTS["limits"]
    assert "ladder_budget" not in pr.DEFAULTS["limits"]


@pytest.mark.parametrize("bad", [True, 0, -1, "lots", None, float("nan"),
                                 float("inf"), -float("inf")])
def test_the_budget_falls_back_on_a_bad_value(monkeypatch, bad):
    monkeypatch.setattr(pr, "load", lambda: {"budget": {"daily_budget": bad}})
    assert pr.budget() == pr.DEFAULTS["budget"]["daily_budget"]


def test_the_budget_reads_the_file(monkeypatch):
    monkeypatch.setattr(pr, "load", lambda: {"budget": {"daily_budget": 42}})
    assert pr.budget() == 42


# ── Tier 1 may import it ────────────────────────────────────────────────────

EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.public_rescue",
            "shared.symbols", "tzdata"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.public_rescue
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % REPO


def test_public_rescue_imports_only_config_and_the_symbol_allow_list():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert {"shared.public_rescue", "shared.symbols"} <= new


# ── structure and strikes (from the 2026-09-21 review) ─────────────────────

def test_the_structure_key_ignores_price_and_size_only():
    a = pr.clean_spec(_pcs(), TODAY)
    assert pr.structure_key(a) == pr.structure_key(
        pr.clean_spec(_pcs(entry_credit=3.0, quantity=7), TODAY))
    assert pr.structure_key(a) != pr.structure_key(
        pr.clean_spec(_pcs(short_strike=505.0), TODAY))


def test_strikes_are_read_on_the_right_side():
    ic = pr.clean_spec(_pcs(strategy="IC", call_short=510.0, call_long=515.0), TODAY)
    assert pr.spec_strikes(ic) == [("put", 500.0), ("put", 495.0),
                                   ("call", 510.0), ("call", 515.0)]
    ccs = pr.clean_spec(_pcs(strategy="CCS", short_strike=510.0,
                             long_strike=515.0), TODAY)
    assert {s for s, _ in pr.spec_strikes(ccs)} == {"call"}
    ladder = {"call": [510.0, 515.0], "put": [495.0, 500.0]}
    assert pr.strikes_on_ladder(ic, ladder)
    assert not pr.strikes_on_ladder(ic, {"call": [510.0], "put": [495.0, 500.0]})
    assert not pr.strikes_on_ladder(ccs, {"put": [510.0, 515.0]}), \
        "a call strike was matched against the put list"


def test_a_huge_integer_is_refused_not_raised():
    assert pr.clean_spec(_pcs(short_strike=10 ** 400), TODAY) is None
