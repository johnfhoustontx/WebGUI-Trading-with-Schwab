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
