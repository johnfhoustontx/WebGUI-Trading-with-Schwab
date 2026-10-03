"""The gateway: per request, a stored answer or a call to Schwab."""
import datetime as dt
import json
import pathlib
import sys
import threading
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402
from test_market_store_bars import Cal, series  # noqa: E402
from test_market_store_chains import chain  # noqa: E402

CT = ZoneInfo("America/Chicago")
DEFAULTS = {
    "chains": {"enabled": True, "max_age_sec": 45, "closed_max_age_sec": 1800,
               "max_entries": 400, "shadow_compare_max_age_sec": 120},
    "quotes": {"enabled": True, "max_age_sec": 5},
    "bars": {"enabled": True, "today_bar": "ttl", "session_ttl_sec": 1740,
             "today_quote_max_age_sec": 120, "settle_min": 10},
}


class Session:
    def __init__(self, name):
        self.name = name


class FakeCal(Cal):
    state = "REGULAR"

    @classmethod
    def session_at(cls, now):
        return Session(cls.state)


class Cfg:
    def __init__(self, mode="on", **over):
        self._mode, self._s = mode, json.loads(json.dumps(DEFAULTS))
        for dotted, v in over.items():
            sec, key = dotted.split("__")
            self._s[sec][key] = v

    def mode(self):
        return self._mode

    def store_on(self, name):
        return self._s[name]["enabled"] is True

    def section(self, name):
        return self._s[name]

    def today_bar(self):
        return self._s["bars"]["today_bar"]


class Harness:
    def __init__(self, cfg=None, responses=None):
        self.calls, self.records = [], []
        self.clock = 1000.0
        self.now_ct = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)
        self.responses = responses or (lambda endpoint, params: chain())
        FakeCal.state = "REGULAR"
        self.gw = ms.Gateway(
            fetch=self._fetch, config=cfg or Cfg(), calendar=FakeCal,
            record=lambda *a: self.records.append(a),
            clock=lambda: self.clock, now_ct=lambda: self.now_ct)

    def _fetch(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        out = self.responses(endpoint, params)
        if isinstance(out, Exception):
            raise out
        return out

    def outcomes(self):
        return [r[2] for r in self.records]


def P(frm="2026-10-05", to="2026-10-12", **kw):
    return {"symbol": "SPY", "contractType": "ALL", "range": "ALL",
            "fromDate": frm, "toDate": to, **kw}


def body(served):
    return json.loads(served.body) if served.body is not None else served.data


# ---- mode: off -------------------------------------------------------------

def test_off_passes_every_request_through_and_stores_nothing():
    h = Harness(Cfg(mode="off"))
    h.gw.chains(P(), "a")
    h.gw.chains(P(), "a")
    assert len(h.calls) == 2 and h.outcomes() == ["upstream", "upstream"]
    assert h.gw.chain_store.lookup(ms.ChainKey.from_params(P()), max_age=99,
                                   now=h.clock, state="REGULAR") is None


def test_a_switched_off_store_is_off_even_when_the_mode_is_on():
    h = Harness(Cfg(chains__enabled=False))
    h.gw.chains(P(), "a")
    h.gw.chains(P(), "a")
    assert len(h.calls) == 2


# ---- mode: on --------------------------------------------------------------

def test_a_repeat_request_makes_no_call():
    h = Harness()
    first = h.gw.chains(P(), "a")
    h.clock += 10
    second = h.gw.chains(P(), "b")
    assert len(h.calls) == 1
    assert (first.kind, second.kind, second.age) == ("miss", "hit", 10.0)
    assert body(second) == chain()
    assert h.records == [("chains", "a", "upstream"), ("chains", "b", "hit")]


def test_a_narrower_window_makes_no_call():
    h = Harness()
    h.gw.chains(P(), "collector")
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert len(h.calls) == 1 and got.kind == "subset"
    assert "2026-10-12:7" not in body(got)["callExpDateMap"]


def test_a_stale_entry_is_refetched():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 46
    assert h.gw.chains(P(), "a").kind == "miss" and len(h.calls) == 2


def test_the_callers_own_age_limit_wins():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 30
    assert h.gw.chains(P(), "collector", max_age=20).kind == "miss"
    h.clock += 100
    assert h.gw.chains(P(), "collector", max_age=210).kind == "hit"
    assert h.gw.chains(P(), "x", max_age=0).kind == "miss"


def test_a_nonsense_age_limit_falls_back_to_the_configured_one():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 10
    for bad in (float("nan"), -5, "soon"):
        assert h.gw.chains(P(), "a", max_age=bad).kind == "hit"


def test_closed_markets_use_the_longer_limit():
    h = Harness()
    FakeCal.state = "CLOSED"
    h.gw.chains(P(), "a")
    h.clock += 600
    assert h.gw.chains(P(), "a").kind == "hit"


def test_a_near_miss_fetches_the_wide_window_and_the_collector_then_hits():
    h = Harness()
    h.gw.chains(P(), "collector")                    # today -> +7 is now known
    h.clock += 50                                    # too old for the scan
    scan = h.gw.chains(P(to="2026-10-09"), "scan")
    assert h.calls[-1][1]["toDate"] == "2026-10-12"  # fetched the WIDE window
    assert "2026-10-12:7" not in body(scan)["callExpDateMap"]
    h.clock += 5
    assert h.gw.chains(P(), "collector", max_age=20).kind == "hit"
    assert len(h.calls) == 2


def test_a_strike_filtered_request_is_never_served_from_an_all_strikes_chain():
    h = Harness()
    h.gw.chains(P(), "collector")
    filtered = P(to="2026-10-09", range="NTM", strikeCount=50)
    assert h.gw.chains(filtered, "sentiment").kind == "miss"
    assert h.calls[-1][1]["strikeCount"] == 50
    assert h.gw.chains(filtered, "sentiment").kind == "hit"      # exact repeat


def test_an_upstream_error_is_raised_and_never_answered_from_a_stale_entry():
    state = {"fail": False}
    h = Harness(responses=lambda e, p: ms.UpstreamError(502, "bad gateway")
                if state["fail"] else chain())
    h.gw.chains(P(), "a")
    h.clock += 100
    state["fail"] = True
    with pytest.raises(ms.UpstreamError) as err:
        h.gw.chains(P(), "a")
    assert err.value.status_code == 502


def test_a_store_bug_falls_through_to_a_plain_fetch_and_is_counted(monkeypatch):
    h = Harness()
    monkeypatch.setattr(h.gw.chain_store, "lookup",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = h.gw.chains(P(), "a")
    assert got.kind == "pass" and body(got) == chain()
    assert h.gw.degrades == {"chains": 1}


def test_two_concurrent_identical_misses_make_one_call():
    gate, started = threading.Event(), threading.Event()

    def slow(endpoint, params):
        started.set()
        gate.wait(5)
        return chain()

    h = Harness(responses=slow)
    kinds = []
    threads = [threading.Thread(target=lambda: kinds.append(h.gw.chains(P(), "a").kind))
               for _ in range(2)]
    threads[0].start()
    started.wait(5)
    threads[1].start()
    gate.set()
    for t in threads:
        t.join(5)
    assert len(h.calls) == 1 and sorted(kinds) == ["coalesced", "miss"]


def test_the_configured_entry_limit_is_enforced():
    h = Harness(Cfg(chains__max_entries=1))
    h.gw.chains(P(), "a")
    h.gw.chains({**P(), "symbol": "QQQ"}, "a")
    assert h.gw.chains(P(), "a").kind == "miss"          # SPY was dropped


# ---- mode: shadow ----------------------------------------------------------

def test_shadow_always_calls_schwab_and_reports_what_it_would_have_done():
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "collector")
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert len(h.calls) == 2 and got.kind == "pass"
    assert h.outcomes() == ["upstream", "upstream", "shadow_subset_mismatch"]


def test_shadow_reports_a_match_when_the_cut_equals_what_schwab_sent():
    from test_market_store_chains import EXPS
    h = Harness(Cfg(mode="shadow"),
                responses=lambda e, p: chain(exps=EXPS if p["toDate"] == "2026-10-12"
                                             else EXPS[:3]))
    h.gw.chains(P(), "collector")
    h.gw.chains(P(to="2026-10-09"), "scan")
    assert h.outcomes()[-1] == "shadow_subset_match"


# ---- chains: gaps found by review and mutation testing ----------------------

from test_market_store_chains import EXPS  # noqa: E402

EMPTY = {**chain(), "callExpDateMap": {}, "putExpDateMap": {}}


def boom(*args, **kwargs):
    raise RuntimeError("boom")


class Slow:
    """An upstream call that blocks until ``gate`` is set, for the symbols in
    ``hold`` (every symbol when ``hold`` is None)."""

    def __init__(self, answer, hold=None):
        self.started, self.gate = threading.Event(), threading.Event()
        self.answer, self.hold = answer, hold

    def __call__(self, endpoint, params):
        symbol = params.get("symbol") or params.get("symbols")
        if self.hold is None or symbol in self.hold:
            self.started.set()
            self.gate.wait(5)
        return self.answer(endpoint, params)


def two_at_once(h, slow, first, second):
    """Run ``first`` until it is inside the upstream call, start ``second`` and
    wait until it has passed its own first look at the store and is queueing
    for the same request lock, then let the call finish. Returns both answers.

    From that point the outcome no longer depends on which thread runs next."""
    queued, asked, out = threading.Event(), [], [None, None]
    real = h.gw._locks.get

    def get(key):
        asked.append(key)
        if len(asked) == 2:
            queued.set()
        return real(key)

    h.gw._locks.get = get

    def run(i, fn):
        out[i] = fn()

    a = threading.Thread(target=run, args=(0, first))
    a.start()
    assert slow.started.wait(5)
    b = threading.Thread(target=run, args=(1, second))
    b.start()
    assert queued.wait(5)
    slow.gate.set()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive()
    return out


def test_a_mode_that_is_not_one_of_the_three_answers_nothing_locally():
    for odd in ("ON", "On", "true", True, None, 1):
        h = Harness(Cfg(mode=odd))
        h.gw.chains(P(), "a")
        assert h.gw.chains(P(), "a").kind == "pass" and len(h.calls) == 2
        assert h.gw.chain_store.lookup(ms.ChainKey.from_params(P()), max_age=99,
                                       now=h.clock, state="REGULAR") is None


def test_an_unreadable_config_answers_nothing_locally():
    class Broken(Cfg):
        def mode(self):
            raise OSError("config unreadable")

    h = Harness(Broken())
    h.gw.chains(P(), "a")
    assert h.gw.chains(P(), "a").kind == "pass" and len(h.calls) == 2
    assert h.outcomes() == ["upstream", "upstream"] and h.gw.degrades == {}


def test_an_entry_stored_while_closed_is_refetched_after_the_open():
    h = Harness()
    FakeCal.state = "CLOSED"
    h.gw.chains(P(), "a")
    FakeCal.state = "REGULAR"
    h.clock += 1
    assert h.gw.chains(P(), "a").kind == "miss" and len(h.calls) == 2


def test_the_longer_limit_is_for_closed_markets_only():
    for state in ("REGULAR", "GTH", "CURB"):
        h = Harness()
        FakeCal.state = state
        h.gw.chains(P(), "a")
        h.clock += 600
        assert h.gw.chains(P(), "a").kind == "miss"


def test_a_callers_limit_given_as_text_is_used():
    # The handler hands over the query string's value as it arrived.
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 30
    assert h.gw.chains(P(), "a", max_age="20").kind == "miss"
    h.clock += 30
    assert h.gw.chains(P(), "a", max_age="210").kind == "hit"


def test_an_infinite_age_limit_falls_back_to_the_configured_one():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 46
    assert h.gw.chains(P(), "a", max_age=float("inf")).kind == "miss"


def test_the_wide_refetch_is_one_call_counted_once():
    h = Harness()
    h.gw.chains(P(), "collector")
    h.clock += 50
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert got.kind == "miss" and got.age == 0.0 and len(h.calls) == 2
    assert h.records == [("chains", "collector", "upstream"),
                         ("chains", "scan", "upstream")]
    assert body(got)["numberOfContracts"] == 2 * 3 * 3


@pytest.mark.parametrize("unkept", [EMPTY, {"status": "FAILED"}, None])
def test_a_wide_refetch_the_store_will_not_keep_is_never_answered_from_the_old_entry(unkept):
    state = {"bad": False}

    def respond(endpoint, params):
        if params["toDate"] == "2026-10-09":
            return chain(exps=EXPS[:3], spot=101.0)
        return unkept if state["bad"] else chain(spot=100.0)

    h = Harness(responses=respond)
    h.gw.chains(P(), "collector")                    # held: today -> +7, spot 100
    h.clock += 50
    state["bad"] = True
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    # The wide window was tried, kept nothing, and the request was then fetched
    # exactly as asked. The 50-second-old entry is still held and must not be cut.
    assert [c[1]["toDate"] for c in h.calls] == ["2026-10-12", "2026-10-12", "2026-10-09"]
    assert got.kind == "pass" and body(got)["underlyingPrice"] == 101.0
    assert h.outcomes() == ["upstream", "upstream", "upstream"]


def test_a_wide_refetch_with_no_expiration_in_the_window_is_fetched_as_asked():
    state = {"thin": False}

    def respond(endpoint, params):
        if params["toDate"] == "2026-10-09":
            return chain(exps=EXPS[:3], spot=101.0)
        return chain(exps=EXPS[3:]) if state["thin"] else chain()

    h = Harness(responses=respond)
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["thin"] = True
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert got.kind == "pass" and body(got)["underlyingPrice"] == 101.0
    assert len(h.calls) == 3
    # The held wide chain now has nothing in the window, so the next request
    # for it is one call, not two.
    h.clock += 50
    assert h.gw.chains(P(to="2026-10-09"), "scan").kind == "miss"
    assert len(h.calls) == 4 and h.calls[-1][1]["toDate"] == "2026-10-09"


def test_an_upstream_error_on_the_wide_refetch_is_raised_not_cut_from_the_old_entry():
    state = {"fail": False}
    h = Harness(responses=lambda e, p: ms.UpstreamError(503, "unavailable")
                if state["fail"] else chain())
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["fail"] = True
    with pytest.raises(ms.UpstreamError) as err:
        h.gw.chains(P(to="2026-10-09"), "scan")
    assert err.value.status_code == 503 and len(h.calls) == 2
    assert h.gw.degrades == {}                       # an upstream error is no store bug


def test_an_upstream_error_is_raised_in_shadow_and_when_the_store_is_off():
    for cfg in (Cfg(mode="shadow"), Cfg(mode="off"), Cfg(chains__enabled=False)):
        h = Harness(cfg, responses=lambda e, p: ms.UpstreamError(500, "boom"))
        with pytest.raises(ms.UpstreamError):
            h.gw.chains(P(), "a")
        assert len(h.calls) == 1 and h.records == [] and h.gw.degrades == {}


def test_the_fallback_after_a_store_bug_still_raises_an_upstream_error(monkeypatch):
    h = Harness(responses=lambda e, p: ms.UpstreamError(502, "bad gateway"))
    monkeypatch.setattr(h.gw.chain_store, "lookup", boom)
    with pytest.raises(ms.UpstreamError) as err:
        h.gw.chains(P(), "a")
    assert err.value.status_code == 502 and h.gw.degrades == {"chains": 1}


def test_a_store_bug_leaves_a_warning_with_its_traceback(monkeypatch, caplog):
    h = Harness()
    monkeypatch.setattr(h.gw.chain_store, "lookup", boom)
    with caplog.at_level("WARNING", logger="market_store"):
        h.gw.chains(P(), "a")
    (rec,) = [r for r in caplog.records if r.name == "market_store"]
    assert rec.levelname == "WARNING" and "chains" in rec.getMessage()
    assert rec.exc_info is not None and rec.exc_info[0] is RuntimeError


def test_a_store_bug_after_the_fetch_still_answers_and_is_counted(monkeypatch):
    # The accepted cost: Schwab is called a second time.
    h = Harness()
    monkeypatch.setattr(h.gw.chain_store, "put", boom)
    got = h.gw.chains(P(), "a")
    assert got.kind == "pass" and body(got) == chain()
    assert len(h.calls) == 2 and h.gw.degrades == {"chains": 1}


def test_a_store_bug_in_shadow_falls_through_and_is_counted(monkeypatch):
    h = Harness(Cfg(mode="shadow"))
    monkeypatch.setattr(h.gw.chain_store, "lookup", boom)
    got = h.gw.chains(P(), "a")
    assert got.kind == "pass" and body(got) == chain()
    assert h.gw.degrades == {"chains": 1} and h.outcomes() == ["upstream"]


def test_shadow_judges_an_exact_repeat_too():
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "a")
    got = h.gw.chains(P(), "a")
    assert got.kind == "pass" and got.data == chain() and got.body is None
    assert h.outcomes() == ["upstream", "upstream", "shadow_hit_match"]


def test_shadow_compares_inside_its_own_age_limit_not_the_serving_one():
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "a")
    h.clock += 100                                   # past 45, inside 120
    h.gw.chains(P(), "a")
    assert h.outcomes()[-1] == "shadow_hit_match"
    h.clock += 121
    h.gw.chains(P(), "a")
    assert h.outcomes()[-1] == "upstream"            # too old to judge


def test_shadow_enforces_the_configured_entry_limit():
    h = Harness(Cfg(mode="shadow", chains__max_entries=1))
    h.gw.chains(P(), "a")
    h.gw.chains({**P(), "symbol": "QQQ"}, "a")
    h.gw.chains(P(), "a")                            # SPY was dropped: no verdict
    assert h.outcomes() == ["upstream"] * 3


def test_a_forced_fetch_waiting_on_an_identical_one_still_fetches():
    slow = Slow(lambda e, p: chain())
    h = Harness(responses=slow)
    first, second = two_at_once(h, slow,
                                lambda: h.gw.chains(P(), "a", max_age=0),
                                lambda: h.gw.chains(P(), "b", max_age=0))
    assert (first.kind, second.kind) == ("miss", "miss") and len(h.calls) == 2
    assert h.outcomes() == ["upstream", "upstream"]


def test_a_request_waiting_on_a_forced_fetch_takes_its_answer():
    slow = Slow(lambda e, p: chain())
    h = Harness(responses=slow)
    first, second = two_at_once(h, slow,
                                lambda: h.gw.chains(P(), "a", max_age=0),
                                lambda: h.gw.chains(P(), "b"))
    assert (first.kind, second.kind) == ("miss", "coalesced") and len(h.calls) == 1
    assert body(second) == chain()
    assert h.records == [("chains", "a", "upstream"), ("chains", "b", "coalesced")]


def test_a_narrow_request_waiting_on_the_wide_refetch_takes_its_cut():
    slow = Slow(lambda e, p: chain())
    h = Harness(responses=slow)
    slow.gate.set()
    h.gw.chains(P(), "collector")                    # today -> +7 is now known
    slow.gate.clear()
    slow.started.clear()
    h.clock += 50
    wide, narrow = two_at_once(h, slow,
                               lambda: h.gw.chains(P(), "collector"),
                               lambda: h.gw.chains(P(to="2026-10-09"), "scan"))
    assert (wide.kind, narrow.kind) == ("miss", "coalesced") and len(h.calls) == 2
    assert sorted(body(narrow)["callExpDateMap"]) == list(EXPS[:3])


def test_a_slow_fetch_for_one_symbol_does_not_hold_up_another():
    slow = Slow(lambda e, p: chain(), hold={"SPY"})
    h = Harness(responses=slow)
    out = {}
    a = threading.Thread(target=lambda: out.update(spy=h.gw.chains(P(), "a").kind))
    a.start()
    assert slow.started.wait(5)
    b = threading.Thread(
        target=lambda: out.update(qqq=h.gw.chains({**P(), "symbol": "QQQ"}, "a").kind))
    b.start()
    b.join(3)
    try:
        assert not b.is_alive() and out == {"qqq": "miss"}   # SPY is still upstream
    finally:
        slow.gate.set()
        a.join(5)
    assert out == {"qqq": "miss", "spy": "miss"}


def test_the_chain_call_is_never_made_under_a_store_lock():
    held = []

    def respond(endpoint, params):
        held.append(any(s._lock.locked() for s in
                        (h.gw.chain_store, h.gw.quote_store, h.gw.bar_store)))
        return chain()

    h = Harness(responses=respond)
    h.gw.chains(P(), "collector")
    h.clock += 50
    h.gw.chains(P(to="2026-10-09"), "scan")          # the wide refetch
    assert held == [False, False]


def test_a_coalesced_answer_reports_the_age_of_the_entry_it_took():
    slow = Slow(lambda e, p: chain())
    h = Harness(responses=slow)
    ticks = iter(range(1000, 2000))
    h.gw._clock = lambda: float(next(ticks))         # one second per reading
    first, second = two_at_once(h, slow,
                                lambda: h.gw.chains(P(), "a"),
                                lambda: h.gw.chains(P(), "b"))
    # a: look, re-check, store (1003).  b: look (1002), re-check (1004).
    assert (first.kind, second.kind, second.age) == ("miss", "coalesced", 1.0)


# ---- quotes ----------------------------------------------------------------

def quotes_for(endpoint, params):
    return {s: {"quote": {"lastPrice": 100.0, "openPrice": 99.0, "highPrice": 101.0,
                          "lowPrice": 98.0, "totalVolume": 10}}
            for s in params["symbols"].split(",")}


def test_fresh_quotes_make_no_call():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY,QQQ,IWM", "market_svc")
    h.clock += 3
    got = h.gw.quotes("QQQ,SPY", "sentiment_svc")
    assert len(h.calls) == 1 and got.kind == "hit"
    assert list(got.data) == ["QQQ", "SPY"]              # the caller's order
    assert h.outcomes() == ["upstream", "hit"]


def test_only_the_stale_and_missing_symbols_are_fetched():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY,QQQ", "a")
    h.clock += 3
    got = h.gw.quotes("SPY,DIA,QQQ", "b")
    assert h.calls[-1][1] == {"symbols": "DIA", "fields": "quote"}
    assert got.kind == "partial" and set(got.data) == {"SPY", "DIA", "QQQ"}
    assert h.outcomes()[-1] == "partial"


def test_the_dashboard_polls_own_limit_stops_it_rereading_itself():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "market_svc", max_age=1)
    h.clock += 3
    assert h.gw.quotes("SPY", "market_svc", max_age=1).kind == "miss"
    assert len(h.calls) == 2


def test_duplicate_and_blank_symbols_are_cleaned():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY, SPY,,QQQ ", "a")
    assert h.calls[-1][1]["symbols"] == "SPY,QQQ"


def test_quotes_in_shadow_always_call_and_count_would_be_hits():
    h = Harness(Cfg(mode="shadow"), responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.gw.quotes("SPY", "b")
    assert len(h.calls) == 2
    assert h.outcomes() == ["upstream", "upstream", "shadow_hit"]


def test_an_upstream_quote_error_is_raised_even_when_some_symbols_were_fresh():
    state = {"fail": False}
    h = Harness(responses=lambda e, p: ms.UpstreamError(429, "slow down")
                if state["fail"] else quotes_for(e, p))
    h.gw.quotes("SPY", "a")
    state["fail"] = True
    with pytest.raises(ms.UpstreamError):
        h.gw.quotes("SPY,DIA", "a")


# ---- daily bars ------------------------------------------------------------

BAR = {"symbol": "SPY", "periodType": "year", "period": 1,
       "frequencyType": "daily", "frequency": 1, "needExtendedHoursData": "false"}
MON = dt.date(2026, 10, 5)
FRI = dt.date(2026, 10, 2)


def bars_for(endpoint, params):
    return quotes_for(endpoint, params) if endpoint == "/quotes" else series(FRI, MON)


def test_a_repeat_daily_request_in_the_same_period_makes_no_call():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 600
    assert h.gw.pricehistory(BAR, "sentiment").kind == "hit"
    assert len(h.calls) == 1


def test_during_the_session_ttl_mode_refetches_past_the_limit():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 1741
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"


def test_after_the_bar_settles_one_refetch_then_hits_all_evening():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.now_ct = dt.datetime(2026, 10, 5, 15, 11, tzinfo=CT)
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    h.clock += 20000
    assert h.gw.pricehistory(BAR, "x").kind == "hit"
    assert len(h.calls) == 2


def test_quote_mode_builds_todays_bar_and_makes_no_call():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    h.clock += 5000                                   # far past any ttl
    h.gw.quotes("SPY", "market_svc", max_age=0)       # the poll keeps it fresh
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and got.data["candles"][-1]["close"] == 100.0
    assert [c[0] for c in h.calls].count("/pricehistory") == 1


def test_quote_mode_without_a_fresh_quote_fetches():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 5000
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"


def test_intraday_and_extended_hours_requests_pass_straight_through():
    h = Harness(responses=bars_for)
    minute = {**BAR, "periodType": "day", "period": 10, "frequencyType": "minute",
              "frequency": 5}
    ext = {**BAR, "needExtendedHoursData": "true"}
    for params in (minute, minute, ext, ext):
        assert h.gw.pricehistory(params, "x").kind == "pass"
    assert len(h.calls) == 4


def test_bars_in_shadow_count_repeats_and_judge_the_quote_built_bar():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert [c[0] for c in h.calls].count("/pricehistory") == 2
    out = [r[2] for r in h.records if r[0] == "pricehistory"]
    assert out == ["upstream", "shadow_bar_match",
                   "upstream", "shadow_hit", "shadow_bar_match"]


# ---- today's bar and the open ----------------------------------------------
# A quote fetched before 08:30 CT still carries the PRIOR session's open, high
# and low, so today's bar is never built from one, nor judged against one.

def set_time(h, hour, minute, second=0):
    """Move both of the harness's clocks to this time on MON."""
    h.now_ct = dt.datetime(2026, 10, 5, hour, minute, second, tzinfo=CT)
    h.clock = 1_000_000.0 + hour * 3600 + minute * 60 + second


def bar_calls(h):
    return [c[0] for c in h.calls].count("/pricehistory")


def bar_outcomes(h):
    return [r[2] for r in h.records if r[0] == "pricehistory"]


def test_quote_mode_never_builds_todays_bar_from_a_quote_fetched_before_the_open():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    set_time(h, 8, 29, 30)
    h.gw.quotes("SPY", "market_svc")                 # yesterday's open, high, low
    set_time(h, 8, 30, 10)
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"     # first of the period
    set_time(h, 8, 30, 30)
    # The quote is 60 s old, inside the 120 s limit, but the session is 30 s old.
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"
    assert bar_calls(h) == 2
    set_time(h, 8, 30, 40)
    h.gw.quotes("SPY", "market_svc", max_age=0)      # fetched since the open
    set_time(h, 8, 30, 50)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and got.data["candles"][-1]["close"] == 100.0
    assert bar_calls(h) == 2


def test_shadow_never_judges_todays_bar_against_a_quote_fetched_before_the_open():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    set_time(h, 8, 29, 30)
    h.gw.quotes("SPY", "market_svc")
    set_time(h, 8, 30, 30)
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream"]           # no shadow_bar_* verdict
    set_time(h, 8, 30, 40)
    h.gw.quotes("SPY", "market_svc")
    set_time(h, 8, 30, 50)
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream",
                               "upstream", "shadow_hit", "shadow_bar_match"]


def test_at_the_instant_of_the_open_no_quote_is_usable():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    set_time(h, 8, 30, 0)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream"]


def test_well_after_the_open_the_quotes_own_age_limit_still_applies():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    h.clock += 120
    assert h.gw.pricehistory(BAR, "scan").kind == "composed"
    h.clock += 1
    assert h.gw.pricehistory(BAR, "scan").kind == "miss" and bar_calls(h) == 2


# ---- quotes: gaps found by review and mutation testing ----------------------

def test_quotes_pass_through_when_the_mode_or_the_store_is_off():
    for cfg in (Cfg(mode="off"), Cfg(quotes__enabled=False)):
        h = Harness(cfg, responses=quotes_for)
        h.gw.quotes("SPY, SPY", "a")
        got = h.gw.quotes("SPY, SPY", "a")
        assert got.kind == "pass" and len(h.calls) == 2
        assert h.calls[-1] == ("/quotes", {"symbols": "SPY, SPY", "fields": "quote"})
        assert h.records == [("quotes", "a", "upstream")] * 2
        assert h.gw.quote_store.get("SPY", max_age=99, now=h.clock) is None


def test_a_quote_is_served_up_to_the_configured_limit_and_no_longer():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.clock += 5
    assert h.gw.quotes("SPY", "a").kind == "hit"
    h.clock += 0.5
    assert h.gw.quotes("SPY", "a").kind == "miss" and len(h.calls) == 2


def test_a_quote_hit_reports_the_age_of_its_oldest_symbol():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.clock += 2
    h.gw.quotes("QQQ", "a")
    h.clock += 2
    got = h.gw.quotes("QQQ,SPY", "a")
    assert (got.kind, got.age) == ("hit", 4.0)
    assert got.data == quotes_for("/quotes", {"symbols": "QQQ,SPY"})


def test_a_nonsense_quote_age_limit_falls_back_to_the_configured_one():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.clock += 3
    for bad in (float("nan"), float("inf"), -1, "soon"):
        assert h.gw.quotes("SPY", "a", max_age=bad).kind == "hit"
    h.clock += 3
    assert h.gw.quotes("SPY", "a", max_age=float("inf")).kind == "miss"


def test_a_request_naming_no_symbol_is_passed_through_as_it_was_asked():
    # Before the store, Schwab answered this request; it still does.
    for mode in ("on", "shadow"):
        h = Harness(Cfg(mode=mode), responses=lambda e, p: {})
        got = h.gw.quotes(" , ", "a")
        assert got.kind == "pass" and got.data == {}
        assert h.calls == [("/quotes", {"symbols": " , ", "fields": "quote"})]
        assert h.records == [("quotes", "a", "upstream")]


def test_schwabs_invalid_symbol_bucket_is_passed_on_and_never_stored():
    def respond(endpoint, params):
        asked = params["symbols"].split(",")
        out = {s: quotes_for(endpoint, {"symbols": s})[s] for s in asked if s != "XX"}
        if "XX" in asked:
            out["errors"] = {"invalidSymbols": ["XX"]}
        return out

    h = Harness(responses=respond)
    first = h.gw.quotes("SPY,XX", "a")
    assert first.data["errors"] == {"invalidSymbols": ["XX"]} and "SPY" in first.data
    got = h.gw.quotes("SPY,XX", "a")                 # only the unknown one is asked
    assert h.calls[-1][1]["symbols"] == "XX" and got.kind == "partial"
    assert got.data["errors"] == {"invalidSymbols": ["XX"]} and "SPY" in got.data
    assert h.gw.quotes("errors", "a").kind == "miss"


def test_an_answer_that_is_not_a_quote_mapping_is_passed_on_untouched():
    h = Harness(responses=lambda e, p: None)
    got = h.gw.quotes("SPY", "a")
    assert got.kind == "miss" and got.data is None and h.gw.degrades == {}


def test_quotes_in_shadow_return_schwabs_answer_and_judge_only_a_full_answer():
    h = Harness(Cfg(mode="shadow"), responses=quotes_for)
    h.gw.quotes("SPY", "a")
    got = h.gw.quotes("SPY, DIA", "b")               # DIA is not held: no verdict
    assert got.kind == "pass" and list(got.data) == ["SPY", "DIA"]
    assert h.calls[-1][1]["symbols"] == "SPY,DIA"
    assert h.outcomes() == ["upstream", "upstream"]
    h.clock += 6                                     # past the limit: no verdict
    h.gw.quotes("SPY", "c")
    assert h.outcomes() == ["upstream"] * 3


def test_an_upstream_quote_error_is_raised_in_every_mode():
    for cfg in (Cfg(), Cfg(mode="shadow"), Cfg(mode="off")):
        h = Harness(cfg, responses=lambda e, p: ms.UpstreamError(429, "slow down"))
        with pytest.raises(ms.UpstreamError) as err:
            h.gw.quotes("SPY", "a")
        assert err.value.status_code == 429 and len(h.calls) == 1
        assert h.records == [] and h.gw.degrades == {}


def test_a_quote_store_bug_falls_through_to_a_plain_fetch_and_is_counted(monkeypatch):
    h = Harness(responses=quotes_for)
    monkeypatch.setattr(h.gw.quote_store, "split", boom)
    got = h.gw.quotes("SPY, QQQ", "a")
    assert got.kind == "pass" and set(got.data) == {"SPY", " QQQ"}
    assert h.calls == [("/quotes", {"symbols": "SPY, QQQ", "fields": "quote"})]
    assert h.gw.degrades == {"quotes": 1} and h.outcomes() == ["upstream"]


# ---- daily bars: gaps found by review and mutation testing ------------------

def test_bars_pass_through_when_the_mode_or_the_store_is_off():
    for cfg in (Cfg(mode="off"), Cfg(bars__enabled=False)):
        h = Harness(cfg, responses=bars_for)
        h.gw.pricehistory(BAR, "a")
        assert h.gw.pricehistory(BAR, "a").kind == "pass" and len(h.calls) == 2
        assert h.calls[-1] == ("/pricehistory", BAR)
        assert h.records == [("pricehistory", "a", "upstream")] * 2
        assert h.gw.bar_store.get(ms.bar_key(BAR), epoch=("2026-10-05", "live")) is None


def test_a_weekly_series_passes_straight_through():
    h = Harness(responses=bars_for)
    weekly = {**BAR, "frequencyType": "weekly"}
    for _ in range(2):
        assert h.gw.pricehistory(weekly, "x").kind == "pass"
    assert len(h.calls) == 2


def test_a_daily_request_that_does_not_mention_extended_hours_is_stored():
    h = Harness(responses=bars_for)
    plain = {k: v for k, v in BAR.items() if k != "needExtendedHoursData"}
    h.gw.pricehistory(plain, "x")
    assert h.gw.pricehistory({**plain, "needExtendedHoursData": False}, "x").kind == "hit"
    assert len(h.calls) == 1


def test_a_bar_hit_is_the_stored_series_and_its_age():
    h = Harness(responses=bars_for)
    first = h.gw.pricehistory(BAR, "scan")
    h.clock += 1740                                  # the limit itself still hits
    got = h.gw.pricehistory(BAR, "sentiment")
    assert (first.kind, first.data) == ("miss", series(FRI, MON))
    assert (got.kind, got.age) == ("hit", 1740.0) and body(got) == series(FRI, MON)
    assert h.records == [("pricehistory", "scan", "upstream"),
                         ("pricehistory", "sentiment", "hit")]


def test_before_the_open_one_fetch_lasts_until_the_open():
    h = Harness(responses=bars_for)
    set_time(h, 4, 0)
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    set_time(h, 8, 29)                               # far past the session limit
    got = h.gw.pricehistory(BAR, "x")
    assert (got.kind, got.age) == ("hit", 4 * 3600 + 29 * 60.0)
    set_time(h, 8, 30)
    assert h.gw.pricehistory(BAR, "x").kind == "miss" and len(h.calls) == 2


def test_the_session_in_progress_lasts_until_the_bar_has_settled():
    h = Harness(responses=bars_for)
    set_time(h, 15, 5)                               # closed, not yet settled
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    set_time(h, 15, 9, 59)
    assert h.gw.pricehistory(BAR, "x").kind == "hit"
    set_time(h, 15, 10)                              # settle_min = 10
    assert h.gw.pricehistory(BAR, "x").kind == "miss" and len(h.calls) == 2

    h = Harness(Cfg(bars__settle_min=20), responses=bars_for)
    set_time(h, 15, 5)
    h.gw.pricehistory(BAR, "x")
    set_time(h, 15, 19)
    assert h.gw.pricehistory(BAR, "x").kind == "hit"
    set_time(h, 15, 20)
    assert h.gw.pricehistory(BAR, "x").kind == "miss" and len(h.calls) == 2


def test_quote_mode_builds_no_bar_once_the_session_has_settled():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    set_time(h, 15, 30)
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "hit" and body(got) == series(FRI, MON)
    assert bar_outcomes(h) == ["upstream", "hit"]


def test_a_composed_bar_is_counted_and_leaves_the_stored_series_alone():
    h = Harness(Cfg(bars__today_bar="quote"),
                responses=lambda e, p: quotes_for(e, p) if e == "/quotes"
                else series(FRI, MON, close=90.0))
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and got.body is None
    assert [c["close"] for c in got.data["candles"]] == [90.0, 100.0]
    assert bar_outcomes(h) == ["upstream", "composed"]
    stored, _ = h.gw.bar_store.get(ms.bar_key(BAR), epoch=("2026-10-05", "live"))
    assert json.loads(stored) == series(FRI, MON, close=90.0)


def test_a_quote_that_cannot_build_a_bar_means_a_fetch():
    h = Harness(Cfg(bars__today_bar="quote"),
                responses=lambda e, p: {"SPY": {"quote": {"lastPrice": 100.0}}}
                if e == "/quotes" else series(FRI, MON))
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    assert h.gw.pricehistory(BAR, "scan").kind == "miss" and bar_calls(h) == 2


def test_todays_bar_mode_is_read_through_the_validated_accessor():
    # Never the raw table: the accessor is what turns a typo into "ttl".
    class Says(Cfg):
        def __init__(self, says, **over):
            super().__init__(**over)
            self._says = says

        def today_bar(self):
            return self._says

    for raw, says, kind in (("quote", "ttl", "hit"), ("ttl", "quote", "composed")):
        h = Harness(Says(says, bars__today_bar=raw), responses=bars_for)
        h.gw.pricehistory(BAR, "scan")
        h.gw.quotes("SPY", "market_svc")
        assert h.gw.pricehistory(BAR, "scan").kind == kind


def test_an_upstream_bar_error_is_raised_and_never_answered_from_an_old_series():
    for cfg in (Cfg(), Cfg(bars__today_bar="quote"), Cfg(mode="shadow"), Cfg(mode="off")):
        state = {"fail": False}
        h = Harness(cfg, responses=lambda e, p: ms.UpstreamError(502, "bad gateway")
                    if state["fail"] else bars_for(e, p))
        h.gw.pricehistory(BAR, "a")
        h.clock += 1741
        state["fail"] = True
        with pytest.raises(ms.UpstreamError) as err:
            h.gw.pricehistory(BAR, "a")
        assert err.value.status_code == 502 and bar_calls(h) == 2
        assert h.gw.degrades == {}


def test_a_bar_store_bug_falls_through_to_a_plain_fetch_and_is_counted(monkeypatch):
    for mode in ("on", "shadow"):
        h = Harness(Cfg(mode=mode), responses=bars_for)
        monkeypatch.setattr(h.gw.bar_store, "get", boom)
        got = h.gw.pricehistory(BAR, "a")
        assert got.kind == "pass" and got.data == series(FRI, MON)
        assert h.gw.degrades == {"pricehistory": 1}
        assert h.records == [("pricehistory", "a", "upstream")]


def test_shadow_gives_no_bar_verdict_outside_the_session_in_progress():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    set_time(h, 15, 30)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream", "upstream", "shadow_hit"]


def test_shadow_reports_a_bar_that_disagrees_and_a_day_schwab_has_not_sent():
    h = Harness(Cfg(mode="shadow"),
                responses=lambda e, p: quotes_for(e, p) if e == "/quotes"
                else series(FRI, MON, close=90.0))
    h.gw.quotes("SPY", "market_svc")
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "pass" and got.data == series(FRI, MON, close=90.0)
    assert bar_outcomes(h) == ["upstream", "shadow_bar_mismatch"]

    h = Harness(Cfg(mode="shadow"),
                responses=lambda e, p: quotes_for(e, p) if e == "/quotes" else series(FRI))
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream", "shadow_bar_no_today"]


def test_shadow_counts_a_repeat_only_inside_the_same_period():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    set_time(h, 15, 30)
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream", "upstream"]


def test_two_concurrent_identical_bar_misses_make_one_call():
    slow = Slow(bars_for)
    h = Harness(responses=slow)
    first, second = two_at_once(h, slow,
                                lambda: h.gw.pricehistory(BAR, "a"),
                                lambda: h.gw.pricehistory(BAR, "b"))
    assert (first.kind, second.kind) == ("miss", "coalesced") and len(h.calls) == 1
    assert body(second) == series(FRI, MON)
    assert h.records == [("pricehistory", "a", "upstream"),
                         ("pricehistory", "b", "coalesced")]


def test_two_concurrent_refetches_of_a_stale_series_make_one_call():
    slow = Slow(bars_for)
    h = Harness(responses=slow)
    slow.gate.set()
    h.gw.pricehistory(BAR, "a")
    slow.gate.clear()
    slow.started.clear()
    h.clock += 1741
    first, second = two_at_once(h, slow,
                                lambda: h.gw.pricehistory(BAR, "a"),
                                lambda: h.gw.pricehistory(BAR, "b"))
    assert (first.kind, second.kind) == ("miss", "coalesced") and len(h.calls) == 2
    assert second.age == 0.0


def test_a_slow_bar_fetch_for_one_symbol_does_not_hold_up_another():
    slow = Slow(bars_for, hold={"SPY"})
    h = Harness(responses=slow)
    out = {}
    a = threading.Thread(target=lambda: out.update(spy=h.gw.pricehistory(BAR, "a").kind))
    a.start()
    assert slow.started.wait(5)
    b = threading.Thread(target=lambda: out.update(
        qqq=h.gw.pricehistory({**BAR, "symbol": "QQQ"}, "a").kind))
    b.start()
    b.join(3)
    try:
        assert not b.is_alive() and out == {"qqq": "miss"}   # SPY is still upstream
    finally:
        slow.gate.set()
        a.join(5)
    assert out == {"qqq": "miss", "spy": "miss"}


def test_no_upstream_call_is_ever_made_under_a_store_lock():
    held = []

    def respond(endpoint, params):
        held.append(any(s._lock.locked() for s in
                        (h.gw.chain_store, h.gw.quote_store, h.gw.bar_store)))
        return bars_for(endpoint, params)

    for cfg in (Cfg(), Cfg(mode="shadow"), Cfg(bars__today_bar="quote")):
        h = Harness(cfg, responses=respond)
        h.gw.quotes("SPY", "a")
        h.clock += 3
        h.gw.quotes("SPY,DIA", "a")                  # the partial fetch
        h.gw.pricehistory(BAR, "a")
        h.clock += 1741
        h.gw.pricehistory(BAR, "a")
    assert held and not any(held)
