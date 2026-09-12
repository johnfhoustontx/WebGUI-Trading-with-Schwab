"""Every expiration listed, strikes loaded on demand (2026-09-12).

The Calculator and Simulator used to fetch ``today → +60 days`` (Simulator: +90)
in one ``/chains`` call. TSLA stopped at Oct 30 with eleven more expirations out
to Dec 2028 never shown, and ``$SPX``'s 60 days was too large for the proxy at
all. Measured on prod: the expiration list is 0.2 s, one ``$SPX`` expiry 0.6 s,
TSLA's whole chain 4.3 s / 5.6 MB. So the pages now list every expiration and
fetch strikes per expiry — the first two up front, the rest on click.
"""
import datetime as dt
import types

from services.options_svc import compute, handlers
from shared.bus import Bus
from shared.contracts.envelope import Command


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


EXPS = ["2026-09-14", "2026-09-16", "2026-09-18", "2026-10-30", "2026-11-20"]


def _exp_payload(exps=EXPS):
    return {"status": "SUCCESS", "expirationList": [
        {"expirationDate": e, "daysToExpiration": 1, "expirationType": "W"} for e in exps]}


def _chain_for(frm, to):
    """A raw chain holding every listed expiry between frm and to (inclusive)."""
    side = {}
    for e in EXPS:
        if frm <= e <= to:
            side[f"{e}:5"] = {"100.0": [{"bid": 1.0, "ask": 1.2, "mark": 1.1, "delta": 0.4,
                                         "openInterest": 10, "description": "x"}]}
    return {"callExpDateMap": side, "putExpDateMap": side, "status": "SUCCESS"}


def _patch_client(monkeypatch, exp_payload=None, exp_status=200):
    calls = {"chains": [], "expirations": 0}

    def _chain(api, contract_type=None, from_date=None, to_date=None, **kw):
        calls["chains"].append((api, str(from_date), str(to_date)))
        return _Resp(_chain_for(str(from_date), str(to_date)))

    def _exps(api):
        calls["expirations"] += 1
        return _Resp(exp_payload if exp_payload is not None else _exp_payload(), exp_status)

    client = compute._proxy.schwab_py_client
    monkeypatch.setattr(client, "get_option_chain", _chain)
    monkeypatch.setattr(client, "get_option_expirations", _exps, raising=False)
    monkeypatch.setattr(client, "get_quotes",
                        lambda syms: _Resp({s: {"quote": {"lastPrice": 101.0}} for s in syms}))
    return calls


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_parse_expiration_list_is_sorted_unique_iso_and_total():
    payload = {"expirationList": [{"expirationDate": "2026-09-16"},
                                  {"expirationDate": "2026-09-14T00:00:00"},
                                  {"expirationDate": "2026-09-16"}, {"expirationDate": "junk"},
                                  "junk", {}]}
    assert compute.parse_expiration_list(payload) == ["2026-09-14", "2026-09-16"]
    assert compute.parse_expiration_list(None) == []
    assert compute.parse_expiration_list({"expirationList": None}) == []
    assert compute.parse_expiration_list([1, 2]) == []


def test_initial_expiries_are_the_first_two_plus_what_the_page_needs():
    assert compute.initial_expiries(EXPS) == ["2026-09-14", "2026-09-16"]
    # a restored / handed-off leg on Nov 20 must arrive WITH the first load, or
    # its strike is coerced away before the page can ask for it
    assert compute.initial_expiries(EXPS, ["2026-11-20", "2027-01-01"]) == \
        ["2026-09-14", "2026-09-16", "2026-11-20"]
    assert compute.initial_expiries([], ["2026-11-20"]) == []


def test_expiry_runs_group_consecutive_listed_expiries_into_one_call():
    # one /chains call per run: from=run[0], to=run[-1] returns exactly that run,
    # because no LISTED expiry falls between two consecutive listed ones
    assert compute.expiry_runs(EXPS, ["2026-09-14", "2026-09-16", "2026-11-20"]) == \
        [["2026-09-14", "2026-09-16"], ["2026-11-20"]]
    assert compute.expiry_runs(EXPS, ["2026-11-20", "2026-09-14"]) == \
        [["2026-09-14"], ["2026-11-20"]]
    assert compute.expiry_runs(EXPS, ["2031-01-01"]) == []
    assert compute.expiry_runs(EXPS, []) == []


def test_merge_chains_adds_expiries_and_never_mutates_its_inputs():
    a = {"callExpDateMap": {"2026-09-14:2": {"1.0": []}}, "putExpDateMap": {}}
    b = {"callExpDateMap": {"2026-11-20:69": {"2.0": []}}, "putExpDateMap": {"2026-11-20:69": {}}}
    out = compute.merge_chains(a, b)
    assert sorted(out["callExpDateMap"]) == ["2026-09-14:2", "2026-11-20:69"]
    assert list(a["callExpDateMap"]) == ["2026-09-14:2"]
    assert compute.merge_chains(None, None) == {"putExpDateMap": {}, "callExpDateMap": {}}


# ── the Calculator ───────────────────────────────────────────────────────────

def test_lazy_calc_load_lists_every_expiry_and_fetches_only_the_first_two(monkeypatch):
    calls = _patch_client(monkeypatch)
    cc = compute.calc_load_symbol("TSLA", lazy=True)
    assert cc["expirations"] == EXPS
    assert calls["chains"] == [("TSLA", "2026-09-14", "2026-09-16")]      # ONE call
    assert sorted(k.split(":")[0] for k in cc["chain"]["callExpDateMap"]) == \
        ["2026-09-14", "2026-09-16"]
    c = cc["chain"]["callExpDateMap"]["2026-09-14:5"]["100.0"][0]
    assert "description" not in c and c["openInterest"] == 10             # thinned
    assert cc["price"] == 101.0 and cc["symbol"] == "TSLA" and cc["api"] == "TSLA"


def test_lazy_calc_load_also_fetches_a_wanted_far_expiry(monkeypatch):
    calls = _patch_client(monkeypatch)
    cc = compute.calc_load_symbol("TSLA", lazy=True, expiries=["2026-11-20"])
    assert calls["chains"] == [("TSLA", "2026-09-14", "2026-09-16"),
                               ("TSLA", "2026-11-20", "2026-11-20")]
    assert "2026-11-20:5" in cc["chain"]["callExpDateMap"]


def test_lazy_calc_load_falls_back_to_the_60_day_fetch_without_a_list(monkeypatch):
    calls = _patch_client(monkeypatch, exp_status=502)
    cc = compute.calc_load_symbol("TSLA", lazy=True)
    today = dt.date.today()
    assert calls["chains"] == [("TSLA", str(today), str(today + dt.timedelta(days=60)))]
    assert "expirations" not in cc


def test_eager_calc_load_is_unchanged_for_rescue(monkeypatch):
    calls = _patch_client(monkeypatch)
    cc = compute.calc_load_symbol("TSLA")
    assert calls["expirations"] == 0 and len(calls["chains"]) == 1
    assert "expirations" not in cc


def test_calc_load_expiry_merges_one_expiry_into_the_cached_payload(monkeypatch):
    calls = _patch_client(monkeypatch)
    cur = compute.calc_load_symbol("TSLA", lazy=True)
    calls["chains"].clear()
    out = compute.calc_load_expiry(cur, "TSLA", "2026-10-30")
    assert calls["chains"] == [("TSLA", "2026-10-30", "2026-10-30")]
    assert sorted(k.split(":")[0] for k in out["chain"]["callExpDateMap"]) == \
        ["2026-09-14", "2026-09-16", "2026-10-30"]
    assert out["added"] == "2026-10-30" and out["expirations"] == EXPS
    assert "added" not in cur and "2026-10-30:5" not in cur["chain"]["callExpDateMap"]


def test_calc_load_expiry_refuses_a_stale_or_unlisted_click(monkeypatch):
    calls = _patch_client(monkeypatch)
    cur = compute.calc_load_symbol("TSLA", lazy=True)
    calls["chains"].clear()
    assert compute.calc_load_expiry(cur, "AAPL", "2026-10-30") is None      # symbol moved on
    assert compute.calc_load_expiry(cur, "TSLA", "2031-01-01") is None      # not listed
    assert compute.calc_load_expiry(None, "TSLA", "2026-10-30") is None
    assert compute.calc_load_expiry({"symbol": "TSLA", "chain": {}}, "TSLA", "2026-10-30") is None
    assert calls["chains"] == []


def test_calc_load_expiry_already_loaded_answers_without_a_fetch(monkeypatch):
    calls = _patch_client(monkeypatch)
    cur = compute.calc_load_symbol("TSLA", lazy=True)
    calls["chains"].clear()
    out = compute.calc_load_expiry(cur, "tsla", "2026-09-16")
    assert calls["chains"] == [] and out["added"] == "2026-09-16"


def test_calc_load_expiry_marks_a_failed_fetch_rather_than_hanging(monkeypatch):
    _patch_client(monkeypatch)
    cur = compute.calc_load_symbol("TSLA", lazy=True)
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _Resp(None, 502))
    out = compute.calc_load_expiry(cur, "TSLA", "2026-10-30")
    assert out["added"] == "2026-10-30" and out["failed"] is True
    assert "2026-10-30:5" not in out["chain"]["callExpDateMap"]


def test_calc_load_expiry_handler_writes_only_a_real_answer(monkeypatch):
    bus = Bus(fake=True)
    _patch_client(monkeypatch)
    cur = compute.calc_load_symbol("TSLA", lazy=True)
    bus.cache_set("cache:options:calc_chain", cur)
    v0 = bus.cache_get("cache:options:calc_chain").version

    handlers.handle_command(bus, Command(type="calc_load_expiry",
                                         args={"symbol": "AAPL", "expiry": "2026-10-30"}))
    assert bus.cache_get("cache:options:calc_chain").version == v0          # stale: untouched

    handlers.handle_command(bus, Command(type="calc_load_expiry",
                                         args={"symbol": "TSLA", "expiry": "2026-10-30"}))
    env = bus.cache_get("cache:options:calc_chain")
    assert env.version > v0 and env.payload["added"] == "2026-10-30"


def test_calc_load_handler_passes_lazy_and_expiries_only_when_asked(monkeypatch):
    bus = Bus(fake=True)
    seen = []
    monkeypatch.setattr(handlers.compute, "calc_load_symbol",
                        lambda symbol, **kw: (seen.append((symbol, kw)), {"symbol": symbol})[1])
    handlers.handle_command(bus, Command(type="calc_load", args={"symbol": "SPY"}))
    handlers.handle_command(bus, Command(type="calc_load", args={
        "symbol": "TSLA", "lazy": True, "expiries": ["2026-11-20"]}))
    assert seen == [("SPY", {}), ("TSLA", {"lazy": True, "expiries": ["2026-11-20"]})]


# ── the Simulator ────────────────────────────────────────────────────────────

class _Row(types.SimpleNamespace):
    pass


def _patch_snapshots(monkeypatch):
    """A fake options_simulator.data whose snapshot holds the contracts for the
    expiries its date range covers — and a call log to count fetches by."""
    import sys

    log = []

    def fetch_snapshot(client, symbol, expiry=None, horizon_days=90, on_chain=None,
                       from_date=None, to_date=None, with_history=True):
        frm = str(expiry or from_date)
        to = str(expiry or to_date)
        log.append((frm, to, with_history))
        rows = [_Row(expiry=dt.date.fromisoformat(e), kind=k, strike=100.0)
                for e in EXPS if frm <= e <= to for k in ("call", "put")]
        if on_chain is not None:
            on_chain(_chain_for(frm, to))
        return types.SimpleNamespace(symbol=symbol.upper(), spot=101.0, contracts=rows)

    fake = types.ModuleType("options_simulator.data")
    fake.fetch_snapshot = fetch_snapshot
    pkg = types.ModuleType("options_simulator")
    pkg.data = fake
    monkeypatch.setitem(sys.modules, "options_simulator", pkg)
    monkeypatch.setitem(sys.modules, "options_simulator.data", fake)
    compute.reset_sim_snapshots()
    return log


def test_lazy_sim_fetch_lists_every_expiry_and_prices_the_first_two(monkeypatch):
    _patch_client(monkeypatch)
    log = _patch_snapshots(monkeypatch)
    meta = compute.sim_fetch("TSLA", lazy=True)
    assert meta["expirations"] == EXPS
    assert meta["expiries"] == ["2026-09-14", "2026-09-16"]
    assert log == [("2026-09-14", "2026-09-16", True)]
    assert sorted(k.split(":")[0] for k in meta["chain"]["callExpDateMap"]) == \
        ["2026-09-14", "2026-09-16"]


def test_lazy_sim_fetch_fetches_history_once_across_runs(monkeypatch):
    _patch_client(monkeypatch)
    log = _patch_snapshots(monkeypatch)
    meta = compute.sim_fetch("TSLA", lazy=True, expiries=["2026-11-20"])
    assert log == [("2026-09-14", "2026-09-16", True), ("2026-11-20", "2026-11-20", False)]
    assert "2026-11-20" in meta["expiries"]
    assert len(compute._SIM_SNAPSHOTS["TSLA"].contracts) == 6


def test_sim_fetch_expiry_adds_contracts_to_the_stashed_snapshot(monkeypatch):
    _patch_client(monkeypatch)
    log = _patch_snapshots(monkeypatch)
    compute.sim_fetch("TSLA", lazy=True)
    log.clear()
    meta = compute.sim_fetch_expiry("TSLA", "2026-10-30")
    assert log == [("2026-10-30", "2026-10-30", False)]
    assert meta["added"] == "2026-10-30"
    assert meta["expiries"] == ["2026-09-14", "2026-09-16", "2026-10-30"]
    assert meta["strikes"]["2026-10-30"] == {"call": [100.0], "put": [100.0]}
    assert sorted(k.split(":")[0] for k in meta["chain"]["callExpDateMap"]) == ["2026-10-30"]
    # already loaded: no second fetch, no duplicated contracts
    compute.sim_fetch_expiry("TSLA", "2026-10-30")
    assert len(log) == 1 and len(compute._SIM_SNAPSHOTS["TSLA"].contracts) == 6


def test_sim_fetch_expiry_refuses_without_a_snapshot_or_listing(monkeypatch):
    _patch_client(monkeypatch)
    _patch_snapshots(monkeypatch)
    assert compute.sim_fetch_expiry("TSLA", "2026-10-30") is None          # never loaded
    compute.sim_fetch("TSLA", lazy=True)
    assert compute.sim_fetch_expiry("TSLA", "2031-01-01") is None          # not listed


def test_sim_fetch_expiry_handler_merges_into_the_cached_sim_chain(monkeypatch):
    bus = Bus(fake=True)
    _patch_client(monkeypatch)
    _patch_snapshots(monkeypatch)
    handlers.handle_command(bus, Command(type="sim_fetch",
                                         args={"symbol": "TSLA", "lazy": True}))
    order = []
    real = bus.cache_set
    monkeypatch.setattr(bus, "cache_set",
                        lambda key, payload, **kw: (order.append(key), real(key, payload, **kw))[1])
    handlers.handle_command(bus, Command(type="sim_fetch_expiry",
                                         args={"symbol": "TSLA", "expiry": "2026-10-30"}))
    assert order == ["cache:options:sim_chain", "cache:options:sim_meta"]
    chain = bus.cache_get("cache:options:sim_chain").payload
    assert sorted(k.split(":")[0] for k in chain["chain"]["callExpDateMap"]) == \
        ["2026-09-14", "2026-09-16", "2026-10-30"]
    meta = bus.cache_get("cache:options:sim_meta").payload
    assert meta["added"] == "2026-10-30" and "chain" not in meta


def test_a_failed_fetch_marker_does_not_leak_into_the_next_merge(monkeypatch):
    _patch_client(monkeypatch)
    cur = compute.calc_load_symbol("TSLA", lazy=True)
    good_chain = compute._proxy.schwab_py_client.get_option_chain
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _Resp(None, 502))
    failed = compute.calc_load_expiry(cur, "TSLA", "2026-10-30")
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain", good_chain)
    out = compute.calc_load_expiry(failed, "TSLA", "2026-11-20")
    assert out["added"] == "2026-11-20" and "failed" not in out
