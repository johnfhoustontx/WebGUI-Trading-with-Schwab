"""shared.public_tools: the public Calculator/Simulator requests."""
import datetime as dt
import pathlib
import subprocess
import sys

import pytest

from shared import public_tools as pt

REPO = pathlib.Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 9, 21)
EXP = "2026-10-16"


def test_two_streams_neither_the_owners_nor_rescues():
    from shared import public_rescue, public_scan
    assert pt.TOOLS_STREAM == "cmd:tools_public"
    assert pt.MATH_STREAM == "cmd:tools_public_math"
    assert len({pt.TOOLS_STREAM, pt.MATH_STREAM, public_rescue.STREAM,
                public_scan.STREAM, "cmd:options"}) == 5


def test_every_request_kind_is_on_exactly_one_stream():
    assert set(pt.TOOLS_KINDS) & set(pt.MATH_KINDS) == set()
    assert set(pt.TOOLS_KINDS) == {"chain", "expiry", "rate", "sim_snapshot",
                                   "sim_expiry"}
    assert set(pt.MATH_KINDS) == {"price", "iv", "sweep"}


def test_the_shipped_file_matches_the_defaults():
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "tools_public.toml")
                            .read_text(encoding="utf-8"))
    for section, values in pt.DEFAULTS.items():
        assert shipped[section] == values, section


EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.public_rescue",
            "shared.public_tools", "shared.symbols", "tzdata"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.public_tools
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % REPO


def test_public_tools_imports_only_config_and_validators():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)


# ── the request builders ────────────────────────────────────────────────────

def _legs(**over):
    leg = {"option_type": "put", "side": "short", "strike": 500.0,
           "expiry": EXP, "qty": 1, "premium": 1.2}
    leg.update(over)
    return [leg, {**leg, "side": "long", "strike": 495.0, "premium": 0.5}]


def _price(**over):
    req = {"kind": "price", "symbol": "spy", "strategy": "PCS", "spot": 502.0,
           "iv": 0.18, "rate": 0.045, "ivadj": 0.0, "qty": 1, "expiry": EXP,
           "legs": _legs(), "num_strikes": 24}
    req.update(over)
    return req


def test_a_price_request_normalizes_to_calc_compute_arguments():
    cmd = pt.math_command(_price(junk=1), TODAY)
    assert cmd["type"] == pt.MATH_TYPE
    args = cmd["args"]
    assert args["kind"] == "price" and args["symbol"] == "SPY"
    assert set(args) == {"kind", "symbol", "strategy", "spot", "iv", "rate",
                         "ivadj", "qty", "expiry", "legs", "num_strikes"}


@pytest.mark.parametrize("field, bad", [
    ("spot", float("nan")), ("spot", 0), ("spot", True),
    ("iv", float("inf")), ("iv", 0), ("iv", 5.01),
    ("rate", -0.01), ("rate", 0.21), ("ivadj", 2.0),
    ("qty", 0), ("qty", 101), ("num_strikes", 4), ("num_strikes", 61),
    ("expiry", "2026-09-01"), ("strategy", "x" * 40),
])
def test_an_unusable_price_field_refuses_the_request(field, bad):
    assert pt.math_command(_price(**{field: bad}), TODAY) is None


@pytest.mark.parametrize("leg", [
    {"strike": float("nan")}, {"premium": float("inf")}, {"premium": True},
    {"option_type": "future"}, {"side": "both"}, {"qty": 0},
    {"expiry": "yesterday"},
])
def test_an_unusable_leg_refuses_the_request(leg):
    assert pt.math_command(_price(legs=_legs(**leg)), TODAY) is None


def test_a_share_leg_carries_no_strike_or_expiry():
    legs = [{"option_type": "stock", "side": "long", "qty": 1, "premium": 500.0},
            {"option_type": "call", "side": "short", "strike": 510.0,
             "expiry": EXP, "qty": 1, "premium": 2.0}]
    args = pt.math_command(_price(strategy="COVERED_CALL", legs=legs), TODAY)["args"]
    assert args["legs"][0] == {"option_type": "stock", "side": "long", "qty": 1,
                               "premium": 500.0, "strike": None, "expiry": None}


def test_too_many_legs_refuses_the_request():
    assert pt.math_command(_price(legs=_legs() * 5), TODAY) is None   # max 8


def test_iv_and_sweep_requests():
    iv = pt.math_command({"kind": "iv", "symbol": "SPY", "expiry": EXP,
                          "strike": 500.0, "option_type": "put"}, TODAY)
    assert iv["args"] == {"kind": "iv", "symbol": "SPY", "expiry": EXP,
                          "strike": 500.0, "option_type": "put"}
    sweep = pt.math_command({"kind": "sweep", "symbol": "SPY", "dt": 5.0,
                             "legs": [{"kind": "put", "strike": 500.0,
                                       "expiry": EXP, "side": "short", "qty": 1}]},
                            TODAY)
    assert sweep["args"]["dt"] == 5.0
    assert pt.math_command({"kind": "sweep", "symbol": "SPY", "dt": -1,
                            "legs": sweep["args"]["legs"]}, TODAY) is None
    assert pt.math_command({"kind": "sweep", "symbol": "SPY", "dt": 400,
                            "legs": sweep["args"]["legs"]}, TODAY) is None


def test_tools_requests():
    assert pt.tools_command({"kind": "chain", "symbol": " spy "})["args"] == \
        {"kind": "chain", "symbol": "SPY"}
    assert pt.tools_command({"kind": "expiry", "symbol": "SPY", "expiry": EXP},
                            TODAY)["args"]["expiry"] == EXP
    assert pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY"}) is not None
    rate = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCS",
                             "legs": _legs()}, TODAY)
    assert rate["args"]["structure"] == "PCS"
    for bad in ({"kind": "price", "symbol": "SPY"},
                {"kind": "chain", "symbol": "../x"},
                {"kind": "expiry", "symbol": "SPY", "expiry": "soon"},
                {"kind": "nope"}, "x", None):
        assert pt.tools_command(bad, TODAY) is None


def test_a_math_kind_is_refused_by_the_tools_builder_and_vice_versa():
    assert pt.math_command({"kind": "chain", "symbol": "SPY"}) is None


def test_request_keys_are_content_addressed_and_say_nothing():
    a = pt.request_key(pt.math_command(_price(), TODAY))
    b = pt.request_key(pt.math_command(_price(symbol=" SPY ", junk=2), TODAY))
    assert a == b and pt.is_key(a) and "SPY" not in a.upper()
    assert a != pt.request_key(pt.math_command(_price(iv=0.2), TODAY))


def test_rate_structure_key_ignores_price_and_size():
    one = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCS",
                            "legs": _legs()}, TODAY)
    two = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCS",
                            "legs": _legs(premium=3.0, qty=4)}, TODAY)
    assert pt.structure_key(one["args"]) == pt.structure_key(two["args"])


@pytest.mark.parametrize("field", ["qty", "num_strikes"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_a_non_finite_count_is_refused_not_raised(field, bad):
    # A count has no range bound that would also catch a NaN (NaN > 0 is False
    # everywhere else), so only the finiteness check stands between it and
    # ``int(nan)`` raising inside the builder.
    assert pt.math_command(_price(**{field: bad}), TODAY) is None
    assert pt.math_command(_price(legs=_legs(qty=bad)), TODAY) is None


# ── review findings (2026-09-21) ────────────────────────────────────────────

@pytest.mark.parametrize("field, edge", [
    ("rate", 0), ("rate", 0.20), ("ivadj", -1), ("ivadj", 1), ("iv", 5.0),
    ("spot", 1e6),
])
def test_a_price_field_at_its_edge_is_accepted(field, edge):
    assert pt.math_command(_price(**{field: edge}), TODAY)["args"][field] == edge


@pytest.mark.parametrize("premium", [0.0, 100000.0])
def test_a_premium_at_its_edge_is_accepted(premium):
    args = pt.math_command(_price(legs=_legs(premium=premium)), TODAY)["args"]
    assert args["legs"][0]["premium"] == premium


@pytest.mark.parametrize("premium", [100000.01, -0.01])
def test_a_premium_past_its_edge_is_refused(premium):
    assert pt.math_command(_price(legs=_legs(premium=premium)), TODAY) is None


def _sweep(**over):
    req = {"kind": "sweep", "symbol": "SPY", "dt": 5.0,
           "legs": [{"kind": "put", "strike": 500.0, "expiry": EXP,
                     "side": "short", "qty": 1}]}
    req.update(over)
    return req


@pytest.mark.parametrize("dt", [0, 366])
def test_a_sweep_horizon_at_its_edge_is_accepted(dt):
    assert pt.math_command(_sweep(dt=dt), TODAY)["args"]["dt"] == dt


def test_a_sweep_horizon_past_its_edge_is_refused():
    assert pt.math_command(_sweep(dt=366.0001), TODAY) is None


@pytest.mark.parametrize("leg", [
    {"kind": "stock"}, {"strike": float("nan")}, {"qty": 0},
])
def test_an_unusable_sim_leg_refuses_the_sweep(leg):
    legs = [{**_sweep()["legs"][0], **leg}]
    assert pt.math_command(_sweep(legs=legs), TODAY) is None


def test_a_calc_leg_without_a_premium_is_refused():
    missing = _legs()
    del missing[0]["premium"]
    assert pt.math_command(_price(legs=missing), TODAY) is None
    assert pt.math_command(_price(legs=_legs(premium=None)), TODAY) is None


def test_a_share_legs_strike_and_expiry_are_forced_to_none():
    legs = [{"option_type": "stock", "side": "long", "qty": 1, "premium": 500.0,
             "strike": float("nan"), "expiry": "garbage"}]
    leg = pt.math_command(_price(strategy="COVERED_CALL", legs=legs),
                          TODAY)["args"]["legs"][0]
    assert leg["strike"] is None and leg["expiry"] is None


@pytest.mark.parametrize("raw, want", [
    ("PCS;X", None), ("pcs", "PCS"), ("ß", None), ("covered_call", "COVERED_CALL"),
])
def test_codes(raw, want):
    cmd = pt.math_command(_price(strategy=raw), TODAY)
    assert (cmd["args"]["strategy"] if cmd else None) == want


def test_num_strikes_must_be_whole():
    assert pt.math_command(_price(num_strikes=24.5), TODAY) is None


def test_an_iv_request_drops_a_mark():
    cmd = pt.math_command({"kind": "iv", "symbol": "SPY", "expiry": EXP,
                           "strike": 500.0, "option_type": "put", "mark": 9.9},
                          TODAY)
    assert "mark" not in cmd["args"]


@pytest.mark.parametrize("over", [{"structure": "P C S"}, {"structure": None},
                                  {"legs": _legs(strike=float("nan"))}])
def test_a_rate_request_with_a_bad_structure_or_leg_is_refused(over):
    raw = {"kind": "rate", "symbol": "SPY", "structure": "PCS", "legs": _legs()}
    raw.update(over)
    assert pt.tools_command(raw, TODAY) is None


def test_limits_fall_back_on_bad_values(monkeypatch):
    monkeypatch.setattr(pt, "load", lambda: {
        "limits": {"result_ttl_min": True, "dedup_sec": 0, "max_wait_sec": -3},
        "visitor": {"tools_per_hour": True, "math_per_hour": 0}})
    lim = pt.limits()
    assert lim["result_ttl_min"] == pt.DEFAULTS["limits"]["result_ttl_min"]
    assert lim["dedup_sec"] == 0
    assert lim["max_wait_sec"] == pt.DEFAULTS["limits"]["max_wait_sec"]
    assert pt.tools_per_hour() == pt.DEFAULTS["visitor"]["tools_per_hour"]
    assert pt.math_per_hour() == pt.DEFAULTS["visitor"]["math_per_hour"]


def test_visitor_limits_read_a_good_value(monkeypatch):
    monkeypatch.setattr(pt, "load", lambda: {
        "visitor": {"tools_per_hour": 7, "math_per_hour": 70}})
    assert (pt.tools_per_hour(), pt.math_per_hour()) == (7, 70)


def test_request_key_renormalizes_a_hand_built_command():
    built = pt.math_command(_price(), TODAY)
    want = pt.request_key(built, TODAY)
    junk = {"type": pt.MATH_TYPE, "args": {**built["args"], "junk": 1}}
    loose = {"type": pt.MATH_TYPE, "args": {**built["args"], "symbol": " spy "}}
    assert pt.request_key(junk, TODAY) == want
    assert pt.request_key(loose, TODAY) == want
    bad = {"type": pt.MATH_TYPE, "args": {**built["args"], "spot": float("nan")}}
    assert pt.request_key(bad, TODAY) is None
    chain = pt.tools_command({"kind": "chain", "symbol": "SPY"})
    assert pt.request_key({**chain, "type": pt.MATH_TYPE}, TODAY) is None


def test_structure_key_of_a_non_mapping_is_none():
    assert pt.structure_key(None) is None
    assert pt.structure_key("x") is None


def test_the_chain_view_is_the_one_shared_public_chain_key():
    assert pt.chain_view("spy") == "options:pub_chain:SPY"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_a_non_finite_config_value_falls_back_never_raises(monkeypatch, bad):
    """TOML accepts ``nan`` and ``inf``; ``int()`` of either raises, and that
    on every request would leave every visitor unanswered."""
    monkeypatch.setattr(pt, "load", lambda: {
        "limits": {k: bad for k in pt.DEFAULTS["limits"]},
        "visitor": {k: bad for k in pt.DEFAULTS["visitor"]}})
    assert pt.limits() == pt.DEFAULTS["limits"]
    assert pt.tools_per_hour() == pt.DEFAULTS["visitor"]["tools_per_hour"]
    assert pt.math_per_hour() == pt.DEFAULTS["visitor"]["math_per_hour"]


# ── Task 7: the worker's outcomes, and a snapshot's expirations ─────────────

def test_outcomes_are_rescues_plus_load_first():
    from shared import public_rescue
    # ``price_needed`` joined on the 2026-09-21 review (a rating at the
    # chain's own marks would publish them).
    assert pt.OUTCOMES == public_rescue.OUTCOMES + ("load_first", "price_needed")
    assert set(pt.OUTCOME_TEXT) == set(pt.OUTCOMES)
    assert pt.OUTCOME_TEXT["load_first"] == "Load the symbol first."
    for code, text in pt.OUTCOME_TEXT.items():
        assert text and "rescue" not in text.lower(), code


def test_a_snapshot_request_may_carry_its_legs_expirations():
    # Two raw entries at most since the review's cap, so the de-duplication is
    # shown on a repeated pair (it was a three-entry list before the cap).
    cmd = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                            "expiries": [EXP, EXP]}, TODAY)
    assert cmd["args"]["expiries"] == [EXP], "not de-duplicated"
    cmd = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                            "expiries": [EXP, "2026-10-02"]}, TODAY)
    assert cmd["args"]["expiries"] == ["2026-10-02", EXP], "not sorted"


def test_a_snapshot_request_without_expirations_is_unchanged():
    bare = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY"}, TODAY)
    empty = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                              "expiries": []}, TODAY)
    assert bare["args"] == empty["args"] == {"kind": "sim_snapshot", "symbol": "SPY"}
    assert pt.request_key(bare, TODAY) == pt.request_key(empty, TODAY)


def test_expiration_order_does_not_change_a_snapshot_requests_key():
    a = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                          "expiries": [EXP, "2026-10-02"]}, TODAY)
    b = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                          "expiries": ["2026-10-02", EXP]}, TODAY)
    assert pt.request_key(a, TODAY) == pt.request_key(b, TODAY)


@pytest.mark.parametrize("bad", [
    ["soon"], [EXP, None], [EXP, 20261016], "2026-10-16", {"a": EXP}, 7, True,
    ["2026-09-01"],                                   # already past
    [f"2026-10-{d:02d}" for d in range(1, 10)],       # nine entries
])
def test_a_bad_expirations_list_refuses_the_snapshot_request(bad):
    assert pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                             "expiries": bad}, TODAY) is None


def test_two_expirations_are_accepted_and_three_are_not():
    """Capped at two (2026-09-21 review): each expiration is Schwab work the
    visitor chooses, and a reloading page needs its legs' expirations only."""
    exps = ["2026-10-01", "2026-10-02"]
    cmd = pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                            "expiries": exps}, TODAY)
    assert cmd["args"]["expiries"] == exps
    assert pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY",
                             "expiries": exps + ["2026-10-03"]}, TODAY) is None


def test_only_a_snapshot_request_carries_expirations():
    cmd = pt.tools_command({"kind": "chain", "symbol": "SPY",
                            "expiries": [EXP]}, TODAY)
    assert cmd["args"] == {"kind": "chain", "symbol": "SPY"}


# ── the 2026-09-21 review: codes the engines know, the rest are CUSTOM ──────

def test_an_unknown_code_is_custom_so_it_cannot_split_the_cache():
    a = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCSA",
                          "legs": _legs()}, TODAY)
    b = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCSB",
                          "legs": _legs()}, TODAY)
    assert a["args"]["structure"] == b["args"]["structure"] == "CUSTOM"
    assert pt.request_key(a, TODAY) == pt.request_key(b, TODAY)
    assert pt.structure_key(a["args"]) == pt.structure_key(b["args"])
    p1 = pt.math_command(_price(strategy="ZZZ"), TODAY)
    p2 = pt.math_command(_price(strategy="QQQQ"), TODAY)
    assert p1["args"]["strategy"] == "CUSTOM"
    assert pt.request_key(p1, TODAY) == pt.request_key(p2, TODAY)


def test_a_known_code_is_kept():
    for code in ("PCS", "IC", "COVERED_CALL", "CUSTOM"):
        cmd = pt.math_command(_price(strategy=code.lower()), TODAY)
        assert cmd["args"]["strategy"] == code
    assert "CUSTOM" in pt.STRUCTURE_CODES


def test_price_needed_is_a_worded_outcome():
    assert "price_needed" in pt.OUTCOMES
    assert pt.OUTCOME_TEXT["price_needed"]
