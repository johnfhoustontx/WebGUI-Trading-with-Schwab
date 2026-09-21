"""The public Calculator/Simulator workers: each refusal, in order, and what
each request writes.

Every call that reaches Schwab - the Calculator's lazy chain load, the
one-expiration fetch, the rating engine, the Simulator snapshot fetch and its
one-expiration fetch - is stubbed and recorded, as are the three pure engines.
Each refusal asserts none of the Schwab calls ran and nothing was spent from
the ONE daily public budget.
"""
import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute
from services.options_svc import handlers
from services.options_svc import public_budget
from services.options_svc import public_chain
from services.options_svc import rate_trade
from services.options_svc import rescue_public as rp
from services.options_svc import tools_public as tp
from shared import public_rescue as pr
from shared import public_tools as pt
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

CT = ZoneInfo("America/Chicago")
OPEN = dt.datetime(2026, 9, 21, 10, 0, tzinfo=CT)        # a Monday, in session
CLOSED = dt.datetime(2026, 9, 21, 16, 30, tzinfo=CT)
NEAR, MID, FAR = "2026-10-02", "2026-10-09", "2027-01-15"
LISTED = [NEAR, MID, "2026-10-16", FAR]
STRIKES = (495.0, 500.0, 505.0)
SCHWAB = ("load", "fetch", "rate", "snapshot", "sim_fetch_expiry")


def _chain(expiries):
    maps = {f"{e}:9": {f"{k:.1f}": [{"bid": 1.0, "ask": 1.2, "mark": 1.1,
                                     "delta": -0.3}]
                       for k in STRIKES} for e in expiries}
    return {"callExpDateMap": dict(maps), "putExpDateMap": dict(maps)}


class _Row(types.SimpleNamespace):
    pass


def _snap(symbol, expiries=(NEAR, MID)):
    rows = [_Row(expiry=dt.date.fromisoformat(e), kind=k, strike=s, iv=0.3)
            for e in expiries for k in ("call", "put") for s in STRIKES]
    return types.SimpleNamespace(symbol=symbol, spot=502.37, contracts=rows)


@pytest.fixture
def bus():
    reset_fake_bus()
    tp.reset_memory()
    rp.reset_memory()
    return Bus(fake=True)


@pytest.fixture
def schwab(monkeypatch):
    calls = []

    class Stub:
        now = OPEN
        expirations = list(LISTED)
        rating = {"row": {"type": "PCS", "score": 71.0, "ledger_risk_basis": 380.0,
                          "ledger_risk_per_contract": 380.0, "friction_pct": 4.0},
                  "error": None}
        snapshot_empty = False
        compute_result = {"summary": {"max_profit": 120.0},
                          "eval_labels": ["Now", "Exp"], "pnl_data": [[1, 2]]}
        iv_result = {"iv": 23.45, "strike": 500.0, "option_type": "put",
                     "mark": 1.1, "T": 0.03, "error": None}
        sweep_result = {"spot": 502.37, "symbol": "SPY", "legs": [], "dt": 5.0,
                        "mult": 1.5, "whatif_rows": [{"underlying": 500.0,
                                                      "theo_price": 110.0}],
                        "whatif_baseline": 100.0,
                        "ivshock": {"base": {"value": 1.0}, "shock": {"value": 2.0}}}

    def load(symbol, lazy=False, expiries=None):
        calls.append(("load", symbol, tuple(expiries or ())))
        loaded = [NEAR, MID] + [e for e in (expiries or []) if e in Stub.expirations]
        return {"symbol": symbol, "api": symbol, "price": 502.37,
                "expirations": list(Stub.expirations), "chain": _chain(loaded)}

    def fetch(api, runs):
        calls.append(("fetch", api, tuple(tuple(r) for r in runs)))
        return _chain(runs[0])

    def rate(symbol, structure, legs, cc, market_state=None):
        calls.append(("rate", symbol, structure, cc is not None))
        return {"row": dict(Stub.rating["row"]) if Stub.rating.get("row") else None,
                "error": Stub.rating.get("error")}

    def snapshot(symbol, lazy, expiries):
        calls.append(("snapshot", symbol, lazy, tuple(expiries or ())))
        if Stub.snapshot_empty:
            return types.SimpleNamespace(symbol=symbol, spot=None, contracts=[]), \
                list(LISTED), {}
        return _snap(symbol), list(LISTED), _chain([NEAR, MID])

    def fetch_snapshot(client, symbol, expiry=None, with_history=True,
                       on_chain=None, **kw):
        calls.append(("sim_fetch_expiry", symbol, str(expiry)))
        if on_chain is not None:
            on_chain(_chain([str(expiry)]))
        return _snap(symbol, (str(expiry),))

    fake = types.ModuleType("options_simulator.data")
    fake.fetch_snapshot = fetch_snapshot
    pkg = types.ModuleType("options_simulator")
    pkg.data = fake
    monkeypatch.setitem(sys.modules, "options_simulator", pkg)
    monkeypatch.setitem(sys.modules, "options_simulator.data", fake)

    def calc(**kw):
        calls.append(("calc_compute", kw))
        return dict(Stub.compute_result)

    def calc_iv(spot, strike, option_type, mark, expiry, rate=None, now=None):
        calls.append(("calc_iv", spot, strike, option_type, mark, expiry))
        return dict(Stub.iv_result)

    def sim_run(symbol, expiry=None, kind=None, strike=None, direction=None,
                dt=5.0, mult=1.5, legs=None, store=None):
        calls.append(("sim_run", symbol, dt, store))
        if store is None or store.get(symbol) is None:
            return {}
        return {**Stub.sweep_result, "legs": list(legs or []), "dt": float(dt)}

    monkeypatch.setattr(compute, "calc_load_symbol", load)
    monkeypatch.setattr(compute, "_fetch_thin_runs", fetch)
    monkeypatch.setattr(rate_trade, "rate", rate)
    monkeypatch.setattr(compute, "_fetch_sim_snapshot", snapshot)
    monkeypatch.setattr(compute, "calc_compute", calc)
    monkeypatch.setattr(compute, "calc_iv", calc_iv)
    monkeypatch.setattr(compute, "sim_run", sim_run)
    monkeypatch.setattr(tp, "_now", lambda: Stub.now)
    monkeypatch.setattr(rp, "_now", lambda: Stub.now)
    Stub.calls = calls
    yield Stub
    compute.reset_sim_snapshots()


def _schwab_calls(stub):
    return [c for c in stub.calls if c[0] in SCHWAB]


def _spent(bus):
    return public_budget.status(bus, OPEN)["spent"]


def _ts(age_s=0.0, now=OPEN):
    return (now - dt.timedelta(seconds=age_s)).astimezone(dt.timezone.utc).isoformat()


def _calc_legs(expiry=NEAR, short=500.0, long=495.0, premium=(1.1, 0.6)):
    return [{"option_type": "put", "side": "short", "qty": 1, "premium": premium[0],
             "strike": short, "expiry": expiry},
            {"option_type": "put", "side": "long", "qty": 1, "premium": premium[1],
             "strike": long, "expiry": expiry}]


def _sim_legs(expiry=NEAR, short=500.0, long=495.0):
    return [{"kind": "put", "side": "short", "qty": 1, "strike": short,
             "expiry": expiry},
            {"kind": "put", "side": "long", "qty": 1, "strike": long,
             "expiry": expiry}]


def _tool(age_s=0.0, now=OPEN, **args):
    return Command(type=pt.TOOLS_TYPE, args=args, ts=_ts(age_s, now))


def _math(age_s=0.0, now=OPEN, **args):
    return Command(type=pt.MATH_TYPE, args=args, ts=_ts(age_s, now))


def _price_args(**over):
    args = {"kind": "price", "symbol": "SPY", "strategy": "PCS", "spot": 502.37,
            "iv": 0.2, "rate": 0.045, "ivadj": 0.0, "qty": 1, "expiry": NEAR,
            "legs": _calc_legs(), "num_strikes": 5}
    args.update(over)
    return args


def _key(cmd):
    """The answer key the worker must use: the CLEAN command's."""
    today = OPEN.date()
    build = pt.tools_command if cmd.type == pt.TOOLS_TYPE else pt.math_command
    return pt.request_key(build(cmd.args, today), today)


def _answer(bus, cmd):
    env = bus.cache_get(pt.cache_key(pt.answer_view(_key(cmd))))
    return env.payload["outcome"] if env else None


def _result(bus, cmd):
    env = bus.cache_get(pt.cache_key(pt.result_view(_key(cmd))))
    return env.payload if env else None


def _status(bus):
    env = bus.cache_get(pt.STATUS_KEY)
    return env.payload if env else None


def _load_chain(bus, symbol="SPY"):
    tp.handle_tools(bus, _tool(kind="chain", symbol=symbol))


def _load_snapshot(bus, symbol="SPY"):
    tp.handle_tools(bus, _tool(kind="sim_snapshot", symbol=symbol))


# ── chain / expiry: Rescue's ladder, on this module's answer key ────────────

def test_a_chain_request_publishes_the_shared_chain_and_answers_on_its_own_key(
        bus, schwab):
    cmd = _tool(kind="chain", symbol=" spy ")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "done"
    env = bus.cache_get(pr.cache_key(pr.ladder_view("SPY")))
    assert env.payload["strikes"][NEAR]["put"] == list(STRIKES)
    assert "mark" not in repr(env.payload)
    assert public_chain.held("SPY") is not None
    assert public_budget.status(bus, OPEN)["by_kind"] == {"chain": 1}
    assert bus.cache_get(pr.cache_key(pr.answer_view(pr.ladder_key("SPY")))) is None, \
        "answered on Rescue's key"


def test_a_cached_chain_costs_nothing(bus, schwab):
    _load_chain(bus)
    schwab.calls.clear()
    cmd = _tool(kind="chain", symbol="SPY")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "cached"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


def test_a_cached_chain_is_reconciled_with_the_quotes_switch(bus, schwab, monkeypatch):
    from shared import public_scan
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    _load_chain(bus)
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    schwab.calls.clear()
    tp.handle_tools(bus, _tool(kind="chain", symbol="SPY"))
    env = bus.cache_get(pr.cache_key(pr.ladder_view("SPY")))
    assert "quotes" not in env.payload
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


def test_one_more_expiration_merges_into_the_shared_chain(bus, schwab):
    _load_chain(bus)
    schwab.calls.clear()
    cmd = _tool(kind="expiry", symbol="SPY", expiry=FAR)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "done"
    assert schwab.calls == [("fetch", "SPY", ((FAR,),))]


def test_a_fresh_chain_with_nothing_held_is_reloaded(bus, schwab):
    _load_chain(bus)
    public_chain.reset()
    tp.reset_memory()
    schwab.calls.clear()
    cmd = _tool(kind="chain", symbol="SPY")
    tp.handle_tools(bus, cmd)
    assert [c[0] for c in schwab.calls] == ["load"]
    assert _answer(bus, cmd) == "done"


# ── refusals shared by every tools request ──────────────────────────────────

def _every_tools_request():
    return [dict(kind="chain", symbol="SPY"),
            dict(kind="expiry", symbol="SPY", expiry=FAR),
            dict(kind="rate", symbol="SPY", structure="PCS", legs=_calc_legs()),
            dict(kind="sim_snapshot", symbol="SPY"),
            dict(kind="sim_expiry", symbol="SPY", expiry=FAR)]


@pytest.mark.parametrize("age", [10_000, -3600])
@pytest.mark.parametrize("args", _every_tools_request())
def test_an_old_or_future_tools_request_expires_unrun(bus, schwab, args, age):
    cmd = _tool(age_s=age, **args)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "expired"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 0


@pytest.mark.parametrize("args", [
    dict(kind="chain", symbol="SPY"),
    dict(kind="sim_snapshot", symbol="SPY"),
])
def test_outside_the_window_nothing_runs(bus, schwab, args):
    schwab.now = CLOSED
    cmd = _tool(now=CLOSED, **args)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "closed"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 0


def test_a_rate_outside_the_window_is_closed(bus, schwab):
    _load_chain(bus)
    schwab.calls.clear()
    schwab.now = CLOSED
    cmd = _tool(now=CLOSED, kind="rate", symbol="SPY", structure="PCS",
                legs=_calc_legs())
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "closed"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


@pytest.mark.parametrize("args", _every_tools_request())
def test_an_exhausted_budget_refuses_and_runs_nothing(bus, schwab, monkeypatch, args):
    monkeypatch.setattr(pr, "budget", lambda: 1)
    _load_chain(bus)                    # held chain for rate; spends the one unit
    _load_snapshot(bus)                 # budget: the snapshot is refused
    assert tp.PUBLIC_SIM.get("SPY") is None
    schwab.calls.clear()
    if args["kind"] == "sim_expiry":
        tp.PUBLIC_SIM.put("SPY", _snap("SPY"))
        tp.PUBLIC_SIM.set_expirations("SPY", list(LISTED))
    cmd = _tool(**args)
    tp.handle_tools(bus, cmd)
    if args["kind"] == "chain":
        assert _answer(bus, cmd) == "cached"      # held, so no spend needed
    else:
        assert _answer(bus, cmd) == "budget"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


def test_the_budget_is_shared_with_rescue(bus, schwab, monkeypatch):
    monkeypatch.setattr(pr, "budget", lambda: 1)
    rp.handle(bus, Command(type=pr.LADDER_TYPE, args={"symbol": "QQQ"}, ts=_ts()))
    assert _spent(bus) == 1
    schwab.calls.clear()
    cmd = _tool(kind="chain", symbol="SPY")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "budget"
    assert _schwab_calls(schwab) == []
    assert _status(bus)["budget_left"] == 0


def test_a_repeat_inside_the_dedup_window_is_a_duplicate(bus, schwab):
    schwab.snapshot_empty = True
    cmd = _tool(kind="sim_snapshot", symbol="SPY")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "error"
    schwab.calls.clear()
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "duplicate"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


@pytest.mark.parametrize("cmd", [
    Command(type=pt.TOOLS_TYPE, args={"kind": "chain", "symbol": "../x"}),
    Command(type=pt.TOOLS_TYPE, args={"kind": "price", "symbol": "SPY"}),
    Command(type=pt.TOOLS_TYPE, args={"kind": "sim_snapshot", "symbol": "SPY",
                                      "expiries": ["soon"]}),
    Command(type=pt.TOOLS_TYPE, args={"kind": "rate", "symbol": "SPY",
                                      "structure": "PCS",
                                      "legs": [{"option_type": "put"}]}),
    Command(type=pt.MATH_TYPE, args={"kind": "chain", "symbol": "SPY"}),
    Command(type="calc_rate", args={"symbol": "SPY"}),
])
def test_an_invalid_tools_request_is_counted_and_runs_nothing(bus, schwab, cmd):
    tp.handle_tools(bus, cmd)
    assert _schwab_calls(schwab) == [] and _spent(bus) == 0
    assert _status(bus)["invalid_today"] == 1


# ── rate ────────────────────────────────────────────────────────────────────

def _rate_cmd(**over):
    args = dict(kind="rate", symbol="SPY", structure="PCS", legs=_calc_legs())
    args.update(over)
    return _tool(**args)


def test_rate_without_a_held_chain_is_load_first_and_spends_nothing(bus, schwab):
    cmd = _rate_cmd()
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "load_first"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 0


def test_rate_grades_against_the_held_chain_and_drops_the_paper_book_inputs(
        bus, schwab):
    _load_chain(bus)
    cmd = _rate_cmd()
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "done"
    assert ("rate", "SPY", "PCS", True) in schwab.calls
    row = _result(bus, cmd)["row"]
    assert row["score"] == 71.0
    # The checklist's Paper book line is built from these two stamps against
    # the OWNER's ledger caps; a public row must not carry them.
    assert "ledger_risk_basis" not in row
    assert "ledger_risk_per_contract" not in row
    assert "ledger" not in repr(_result(bus, cmd))
    assert public_budget.status(bus, OPEN)["by_kind"] == {"chain": 1, "rate": 1}


def test_rate_passes_the_market_state(bus, schwab, monkeypatch):
    seen = []
    monkeypatch.setattr(handlers, "_market_state", lambda b: "Bullish")

    def rate(symbol, structure, legs, cc, market_state=None):
        seen.append(market_state)
        return {"row": {"type": "PCS"}, "error": None}
    monkeypatch.setattr(rate_trade, "rate", rate)
    _load_chain(bus)
    tp.handle_tools(bus, _rate_cmd())
    assert seen == ["Bullish"]


def test_a_fresh_rating_is_served_with_no_call(bus, schwab):
    _load_chain(bus)
    tp.handle_tools(bus, _rate_cmd())
    schwab.calls.clear()
    tp.reset_memory(keep_chains=True)       # past the dedup memory, same result key
    cmd = _rate_cmd()
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "cached"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 2


def test_a_rate_error_is_answered_error_and_never_cached(bus, schwab):
    schwab.rating = {"row": None,
                     "error": "The rating could not be computed (ConnectionError)."}
    _load_chain(bus)
    cmd = _rate_cmd()
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _result(bus, cmd) is None
    tp.reset_memory(keep_chains=True)
    schwab.rating = {"row": {"type": "PCS"}, "error": None}
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "done", "the failure was served as a result"


def test_a_rate_that_raises_is_an_error(bus, schwab, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setattr(rate_trade, "rate", boom)
    _load_chain(bus)
    cmd = _rate_cmd()
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _result(bus, cmd) is None


def test_the_structure_cap_throttles_the_fourth_price_variant(bus, schwab):
    _load_chain(bus)
    limit = pt.limits()["structure_runs"]
    for i in range(limit):
        cmd = _rate_cmd(legs=_calc_legs(premium=(1.0 + i / 100, 0.5)))
        tp.handle_tools(bus, cmd)
        assert _answer(bus, cmd) == "done"
    schwab.calls.clear()
    cmd = _rate_cmd(legs=_calc_legs(premium=(2.5, 0.5)))
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "throttled"
    assert _schwab_calls(schwab) == []
    other = _rate_cmd(legs=_calc_legs(short=505.0, long=500.0))
    tp.handle_tools(bus, other)
    assert _answer(bus, other) == "done", "the cap bled onto another trade"


def test_a_rate_on_an_unlisted_expiration_is_refused(bus, schwab):
    _load_chain(bus)
    schwab.calls.clear()
    cmd = _rate_cmd(legs=_calc_legs(expiry="2026-10-05"))
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "not_listed"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


# ── sim_snapshot / sim_expiry ───────────────────────────────────────────────

def test_a_snapshot_goes_to_the_public_store_never_the_owners(bus, schwab):
    owner = _snap("SPY", (FAR,))
    compute._SIM_SNAPSHOTS["SPY"] = owner
    cmd = _tool(kind="sim_snapshot", symbol="SPY", expiries=[MID])
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "done"
    assert compute._SIM_SNAPSHOTS["SPY"] is owner
    assert tp.PUBLIC_SIM.get("SPY") is not None
    assert ("snapshot", "SPY", True, (MID,)) in schwab.calls
    meta = _result(bus, cmd)
    assert "chain" not in meta
    assert meta["expirations"] == LISTED
    assert meta["strikes"][NEAR]["put"] == list(STRIKES)
    assert meta["spot"] == 502.37
    assert public_budget.status(bus, OPEN)["by_kind"] == {"sim_snapshot": 1}


def test_a_second_snapshot_request_for_a_held_symbol_is_cached_and_not_refetched(
        bus, schwab):
    _load_snapshot(bus)
    held = tp.PUBLIC_SIM.get("SPY")
    schwab.calls.clear()
    tp.reset_memory(keep_chains=True, keep_snapshots=True)
    cmd = _tool(kind="sim_snapshot", symbol="SPY", expiries=[FAR])
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "cached"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1
    assert tp.PUBLIC_SIM.get("SPY") is held, "another visitor's snapshot was replaced"
    meta = _result(bus, cmd)
    assert meta["strikes"][NEAR]["put"] == list(STRIKES) and "chain" not in meta


def test_a_failed_snapshot_is_an_error_publishes_nothing_and_holds_nothing(bus, schwab):
    schwab.snapshot_empty = True
    cmd = _tool(kind="sim_snapshot", symbol="SPY")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _result(bus, cmd) is None
    assert tp.PUBLIC_SIM.get("SPY") is None, "an empty snapshot would answer cached"


def test_one_more_snapshot_expiration(bus, schwab):
    _load_snapshot(bus)
    schwab.calls.clear()
    cmd = _tool(kind="sim_expiry", symbol="SPY", expiry=FAR)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "done"
    assert schwab.calls == [("sim_fetch_expiry", "SPY", FAR)]
    meta = _result(bus, cmd)
    assert FAR in meta["strikes"] and "chain" not in meta
    assert compute.expiries_of(tp.PUBLIC_SIM.get("SPY")) == [NEAR, MID, FAR]


def test_a_snapshot_expiration_already_held_is_cached(bus, schwab):
    _load_snapshot(bus)
    schwab.calls.clear()
    cmd = _tool(kind="sim_expiry", symbol="SPY", expiry=MID)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "cached"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1
    assert MID in _result(bus, cmd)["strikes"]


def test_a_snapshot_expiration_not_listed_is_refused(bus, schwab):
    _load_snapshot(bus)
    schwab.calls.clear()
    cmd = _tool(kind="sim_expiry", symbol="SPY", expiry="2026-10-05")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "not_listed"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


def test_sim_expiry_after_the_snapshot_was_evicted_is_load_first(bus, schwab):
    _load_snapshot(bus)
    tp.PUBLIC_SIM.clear()
    schwab.calls.clear()
    cmd = _tool(kind="sim_expiry", symbol="SPY", expiry=FAR)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "load_first"
    assert _schwab_calls(schwab) == [] and _spent(bus) == 1


def test_sim_expiry_that_loses_the_race_is_load_first(bus, schwab, monkeypatch):
    _load_snapshot(bus)
    monkeypatch.setattr(compute, "sim_fetch_expiry", lambda *a, **k: None)
    cmd = _tool(kind="sim_expiry", symbol="SPY", expiry=FAR)
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "load_first"
    assert _result(bus, cmd) is None


# ── math: price ─────────────────────────────────────────────────────────────

def test_price_without_a_held_chain_is_load_first(bus, schwab):
    cmd = _math(**_price_args())
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "load_first"
    assert not [c for c in schwab.calls if c[0] == "calc_compute"]


def test_price_derives_its_price_rows_server_side(bus, schwab):
    _load_chain(bus)
    cmd = _math(**_price_args(num_strikes=5))
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "done"
    (kw,) = [c[1] for c in schwab.calls if c[0] == "calc_compute"]
    assert kw["price_rows"] == tp.strikes_window(list(STRIKES), 502.37, 5)
    assert kw["price_rows"] == [495.0, 500.0, 505.0]
    assert kw["num_strikes"] == 5 and kw["strategy"] == "PCS"
    assert kw["legs"][0] == {"option_type": "put", "side": "short", "qty": 1,
                             "premium": 1.1, "strike": 500.0, "expiry": NEAR}
    assert _result(bus, cmd)["summary"] == {"max_profit": 120.0}


def test_a_price_request_cannot_carry_its_own_price_rows(bus, schwab):
    _load_chain(bus)
    cmd = _math(**_price_args(price_rows=list(range(100_000))))
    tp.handle_math(bus, cmd)
    (kw,) = [c[1] for c in schwab.calls if c[0] == "calc_compute"]
    assert kw["price_rows"] == [495.0, 500.0, 505.0]


def test_price_with_a_strike_off_the_held_chain_is_off_ladder(bus, schwab):
    _load_chain(bus)
    cmd = _math(**_price_args(legs=_calc_legs(short=500.5)))
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "off_ladder"
    assert not [c for c in schwab.calls if c[0] == "calc_compute"]


def test_price_with_a_leg_on_an_expiration_not_loaded_is_off_ladder(bus, schwab):
    _load_chain(bus)
    cmd = _math(**_price_args(legs=_calc_legs(expiry=FAR)))
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "off_ladder"


def test_a_share_leg_needs_no_strike_on_the_chain(bus, schwab):
    _load_chain(bus)
    legs = [{"option_type": "stock", "side": "long", "qty": 1, "premium": 502.0},
            {"option_type": "call", "side": "short", "qty": 1, "premium": 1.1,
             "strike": 505.0, "expiry": NEAR}]
    cmd = _math(**_price_args(strategy="COVERED_CALL", legs=legs))
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "done"


def test_an_engine_error_on_price_is_error_and_not_cached(bus, schwab):
    schwab.compute_result = {"error": "ValueError: boom"}
    _load_chain(bus)
    cmd = _math(**_price_args())
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _result(bus, cmd) is None


def test_a_fresh_price_result_is_cached(bus, schwab):
    _load_chain(bus)
    cmd = _math(**_price_args())
    tp.handle_math(bus, cmd)
    schwab.calls.clear()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "cached"
    assert not [c for c in schwab.calls if c[0] == "calc_compute"]


# ── math: iv ────────────────────────────────────────────────────────────────

def _iv_cmd(**over):
    args = dict(kind="iv", symbol="SPY", expiry=NEAR, strike=500.0,
                option_type="put")
    args.update(over)
    return _math(**args)


def test_iv_reads_the_mark_off_the_held_chain_and_publishes_only_the_iv(bus, schwab):
    _load_chain(bus)
    cmd = _iv_cmd()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "done"
    assert ("calc_iv", 502.37, 500.0, "put", 1.1, NEAR) in schwab.calls
    result = _result(bus, cmd)
    assert result == {"iv": 23.45}
    for word in ("mark", "bid", "ask"):
        assert word not in repr(result)


def test_iv_with_no_mark_is_an_error_with_nothing_published(bus, schwab):
    _load_chain(bus)
    cmd = _iv_cmd(strike=700.0)
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _result(bus, cmd) is None
    assert not [c for c in schwab.calls if c[0] == "calc_iv"]


def test_iv_that_cannot_be_solved_is_an_error(bus, schwab):
    schwab.iv_result = {"iv": None, "mark": 1.1, "error": "could not imply IV"}
    _load_chain(bus)
    cmd = _iv_cmd()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _result(bus, cmd) is None


def test_iv_without_a_held_chain_is_load_first(bus, schwab):
    cmd = _iv_cmd()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "load_first"


@pytest.mark.parametrize("row, want", [
    ({"mark": 1.1, "bid": 1.0, "ask": 1.2}, 1.1),
    ({"mark": None, "bid": 1.0, "ask": 1.4}, 1.2),
    ({"mark": float("nan"), "bid": 1.0, "ask": 1.4}, 1.2),
    ({"mark": 0.0, "bid": 1.0, "ask": 1.4}, 1.2),
    ({"mark": None, "bid": None, "ask": 1.4}, None),
    ({"mark": None, "bid": float("inf"), "ask": 1.4}, None),
    ({"mark": True, "bid": None, "ask": None}, None),
])
def test_mark_from_chain(row, want):
    chain = {"putExpDateMap": {f"{NEAR}:9": {"500.0": [row]}}}
    got = tp.mark_from_chain(chain, "put", NEAR, 500.0)
    assert got == (pytest.approx(want) if want is not None else None)


def test_mark_from_chain_matches_a_strike_spelled_as_an_integer():
    chain = {"callExpDateMap": {f"{NEAR}:9": {"500": [{"mark": 2.0}]}}}
    assert tp.mark_from_chain(chain, "call", NEAR, 500.0) == 2.0
    assert tp.mark_from_chain(chain, "put", NEAR, 500.0) is None
    assert tp.mark_from_chain(None, "call", NEAR, 500.0) is None


# ── math: sweep ─────────────────────────────────────────────────────────────

def _sweep_cmd(**over):
    args = dict(kind="sweep", symbol="SPY", dt=5.0, legs=_sim_legs())
    args.update(over)
    return _math(**args)


def test_sweep_without_a_public_snapshot_is_load_first(bus, schwab):
    compute._SIM_SNAPSHOTS["SPY"] = _snap("SPY")         # the owner's is no use
    cmd = _sweep_cmd()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "load_first"


def test_sweep_publishes_the_what_if_half_only(bus, schwab):
    _load_snapshot(bus)
    cmd = _sweep_cmd()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "done"
    (call,) = [c for c in schwab.calls if c[0] == "sim_run"]
    assert call[3] is tp.PUBLIC_SIM
    result = _result(bus, cmd)
    assert "ivshock" not in result and "shock" not in repr(result)
    assert result["whatif_rows"] and result["whatif_baseline"] == 100.0
    assert result["spot"] == 502.37 and result["dt"] == 5.0
    assert result["legs"] == _sim_legs()


def test_sweep_with_a_leg_not_in_the_snapshot_is_off_ladder(bus, schwab):
    _load_snapshot(bus)
    cmd = _sweep_cmd(legs=_sim_legs(short=500.5))
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "off_ladder"
    assert not [c for c in schwab.calls if c[0] == "sim_run"]


def test_an_empty_sweep_is_load_first(bus, schwab, monkeypatch):
    _load_snapshot(bus)
    monkeypatch.setattr(compute, "sim_run", lambda *a, **k: {})
    cmd = _sweep_cmd()
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "load_first"
    assert _result(bus, cmd) is None


# ── math spends nothing ─────────────────────────────────────────────────────

def test_math_spends_no_budget_even_when_it_is_exhausted(bus, schwab, monkeypatch):
    monkeypatch.setattr(pr, "budget", lambda: 2)
    _load_chain(bus)
    _load_snapshot(bus)
    assert _status(bus)["budget_left"] == 0
    for cmd in (_math(**_price_args()), _iv_cmd(), _sweep_cmd()):
        tp.handle_math(bus, cmd)
        assert _answer(bus, cmd) == "done"
    assert _spent(bus) == 2


def test_math_runs_outside_the_window(bus, schwab):
    _load_chain(bus)
    schwab.now = CLOSED
    cmd = _math(now=CLOSED, **_price_args())
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "done"


@pytest.mark.parametrize("age", [10_000, -3600])
def test_an_old_math_request_expires_unrun(bus, schwab, age):
    _load_chain(bus)
    cmd = _math(age_s=age, **_price_args())
    tp.handle_math(bus, cmd)
    assert _answer(bus, cmd) == "expired"
    assert not [c for c in schwab.calls if c[0] == "calc_compute"]


@pytest.mark.parametrize("cmd", [
    Command(type=pt.MATH_TYPE, args={"kind": "price", "symbol": "SPY",
                                     "spot": float("nan")}),
    Command(type=pt.MATH_TYPE, args={"kind": "iv", "symbol": "SPY"}),
    Command(type=pt.TOOLS_TYPE, args={"kind": "price", "symbol": "SPY"}),
    Command(type="calc_compute", args={}),
])
def test_an_invalid_math_request_is_counted_and_runs_nothing(bus, schwab, cmd):
    tp.handle_math(bus, cmd)
    assert not [c for c in schwab.calls if c[0] in ("calc_compute", "calc_iv",
                                                    "sim_run")]
    assert _status(bus)["invalid_today"] == 1


# ── status, and never raising ───────────────────────────────────────────────

def test_the_status_view_holds_counts_and_nothing_about_requests(bus, schwab):
    _load_chain(bus)
    tp.handle_tools(bus, _rate_cmd())
    tp.handle_math(bus, _math(**_price_args()))
    status = _status(bus)
    assert status["tools_today"] == 2 and status["math_today"] == 1
    assert status["invalid_today"] == 0 and status["busy"] is None
    assert status["budget_left"] == pr.budget() - 2
    text = repr(status)
    assert "SPY" not in text and "500" not in text and NEAR not in text
    assert _key(_rate_cmd()) not in text


@pytest.mark.parametrize("handler", ["handle_tools", "handle_math"])
def test_a_redis_error_reading_the_status_never_escapes(bus, schwab, monkeypatch,
                                                        handler):
    def broken(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(tp, "_read_status", broken)
    getattr(tp, handler)(bus, _tool(kind="chain", symbol="SPY"))
    getattr(tp, handler)(bus, _math(**_price_args()))


@pytest.mark.parametrize("handler", ["handle_tools", "handle_math"])
def test_a_broken_bus_never_escapes(schwab, handler):
    class Broken:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise ConnectionError("redis down")
            return fail
    getattr(tp, handler)(Broken(), _tool(kind="chain", symbol="SPY"))
    getattr(tp, handler)(Broken(), _math(**_price_args()))


def test_a_crash_mid_run_clears_busy(bus, schwab, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(compute, "sim_fetch", boom)
    cmd = _tool(kind="sim_snapshot", symbol="SPY")
    tp.handle_tools(bus, cmd)
    assert _answer(bus, cmd) == "error"
    assert _status(bus)["busy"] is None


def test_the_public_store_reads_its_config():
    assert tp.PUBLIC_SIM is not compute.PRIVATE_SIM
    assert tp.PUBLIC_SIM.snapshots is not compute._SIM_SNAPSHOTS
    assert tp.PUBLIC_SIM._limit() == pt.limits()["snapshot_limit"]
    assert tp.PUBLIC_SIM._ttl() == pt.limits()["snapshot_ttl_min"] * 60


@pytest.mark.parametrize("strikes, spot, n, want", [
    ([490, 495, 500, 505, 510], 502.0, 1, [500.0, 505.0]),
    ([490, 495, 500, 505, 510], 500.0, 2, [495.0, 500.0, 505.0, 510.0]),
    ([500, 500.0, "x", None, float("nan"), 505], 501.0, 5, [500.0, 505.0]),
    ([], 500.0, 5, []),
    ([500.0], None, 5, []),
])
def test_strikes_window(strikes, spot, n, want):
    assert tp.strikes_window(strikes, spot, n) == want


# ── source-level pins ───────────────────────────────────────────────────────

def _source():
    import pathlib
    return pathlib.Path(tp.__file__).read_text(encoding="utf-8")


def test_the_worker_writes_only_public_keys():
    import ast
    tree = ast.parse(_source())
    targets = {ast.unparse(n.args[0]) for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "cache_set"}
    assert targets == {"pt.STATUS_KEY", "pt.cache_key(view)"}
    enqueues = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) in ("enqueue_command", "request")]
    assert enqueues == []


def test_the_worker_never_names_the_owners_snapshots_or_slots():
    """In CODE (names, attributes, strings): the docstring may explain them."""
    import ast
    tree = ast.parse(_source())
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            used.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str)                 and len(n.value) < 60:
            used.add(n.value)
    for owner in ("_SIM_SNAPSHOTS", "_SIM_EXPIRATIONS", "PRIVATE_SIM",
                  "_stash_sim_snapshot", "cache:options:calc_chain",
                  "cache:options:calc_rating", "cache:options:sim_meta"):
        assert not any(owner in u for u in used), owner
