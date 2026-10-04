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
               "max_entries": 400, "shadow_compare_max_age_sec": 120,
               "wide_refetch_max_days": 7},
    "quotes": {"enabled": True, "max_age_sec": 5, "max_symbols": 5000},
    "bars": {"enabled": True, "today_bar": "ttl", "session_ttl_sec": 1740,
             "session_spread": True,
             "today_quote_max_age_sec": 120, "settle_min": 10, "max_entries": 4000},
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


def all_at_once(h, slow, first, others):
    """Run ``first`` until it is inside the upstream call, start every one of
    ``others`` and wait until each has passed its own first look at the store
    and is queueing for the same request lock, then let the call finish.
    Returns every answer, ``first``'s first; a request that raised hands back
    its exception.

    From that point the outcome no longer depends on which thread runs next."""
    queued, asked = threading.Event(), []
    out = [None] * (1 + len(others))
    real = h.gw._locks.holding

    def holding(key):
        asked.append(key)
        if len(asked) == len(out):
            queued.set()
        return real(key)

    h.gw._locks.holding = holding

    def run(i, fn):
        try:
            out[i] = fn()
        except Exception as e:  # noqa: BLE001 - the test inspects it.
            out[i] = e

    threads = [threading.Thread(target=run, args=(0, first))]
    threads[0].start()
    assert slow.started.wait(5)
    for i, fn in enumerate(others, start=1):
        threads.append(threading.Thread(target=run, args=(i, fn)))
        threads[-1].start()
    assert queued.wait(5)
    slow.gate.set()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads)
    return out


def two_at_once(h, slow, first, second):
    return all_at_once(h, slow, first, [second])


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
    slow = Slow(lambda endpoint, params: chain())
    h = Harness(responses=slow)
    # The second request is queueing on the lock before the first call returns.
    kinds = [served.kind for served in two_at_once(
        h, slow, lambda: h.gw.chains(P(), "a"), lambda: h.gw.chains(P(), "a"))]
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


def test_a_callers_limit_is_capped_at_an_hour():
    # A typo such as 1e12 must not make an old entry look fresh.
    cfg = {"max_age_sec": 45, "closed_max_age_sec": 1800}
    assert ms.MAX_REQUEST_AGE_SEC == 3600
    for huge in (1e12, "1e12", 3601, 10 ** 30):
        assert ms.effective_max_age(huge, cfg, closed=False) == 3600.0
    for fine in (0, "0", 1, 210, "210", 3600):
        assert ms.effective_max_age(fine, cfg, closed=False) == float(fine)


def test_the_cap_is_on_the_callers_limit_not_the_configured_one():
    cfg = {"max_age_sec": 45, "closed_max_age_sec": 7200}
    assert ms.effective_max_age(None, cfg, closed=True) == 7200.0
    assert ms.effective_max_age("soon", cfg, closed=True) == 7200.0


def test_a_huge_chain_age_limit_serves_nothing_older_than_an_hour():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 3599
    assert h.gw.chains(P(), "a", max_age=1e12).kind == "hit"
    h.clock += 2
    assert h.gw.chains(P(), "a", max_age=1e12).kind == "miss"


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
    # The week, then the request as asked (audit AR-100); both failed.
    assert err.value.status_code == 503
    assert [c[1]["toDate"] for c in h.calls] == ["2026-10-12", "2026-10-12", "2026-10-09"]
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


def test_a_degrade_is_counted_under_a_lock(monkeypatch):
    # Many worker threads can degrade at once; a read-then-write loses counts.
    h = Harness()
    held = []

    class Watched(dict):
        def __setitem__(self, area, count):
            held.append(h.gw._degrade_lock.locked())
            super().__setitem__(area, count)

    h.gw.degrades = Watched()
    monkeypatch.setattr(h.gw.chain_store, "lookup", boom)
    h.gw.chains(P(), "a")
    h.gw.chains(P(), "a")
    assert held == [True, True] and h.gw.degrades == {"chains": 2}
    assert not h.gw._degrade_lock.locked()


def test_a_store_bug_after_the_fetch_still_answers_and_is_counted(monkeypatch):
    # The answer is already in hand: it is returned, and Schwab is not asked twice.
    h = Harness()
    monkeypatch.setattr(h.gw.chain_store, "put", boom)
    got = h.gw.chains(P(), "a")
    assert got.kind == "miss" and body(got) == chain()
    assert len(h.calls) == 1 and h.gw.degrades == {"chains": 1}


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


def test_shadow_would_serve_inside_the_serving_limit_and_only_compares_past_it():
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "a")                            # stored
    h.clock += 30                                    # inside the serving limit, 45
    h.gw.chains(P(), "a")
    h.clock += 30                                    # 60 s old: the would-be hit did
    h.gw.chains(P(), "a")                            # not refresh it. Inside 120.
    h.clock += 121                                   # the compare-only fetch did
    h.gw.chains(P(), "a")
    assert h.outcomes() == ["upstream",
                            "upstream", "shadow_hit_match",     # a call on would save
                            "upstream", "shadow_cmp_match",     # a comparison, no saving
                            "upstream"]                         # too old to compare


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
    # a: look, re-check, fetch begins (1002).  b: look (1003), re-check (1004).
    assert (first.kind, second.kind, second.age) == ("miss", "coalesced", 2.0)


# ---- quotes ----------------------------------------------------------------

def quotes_for(endpoint, params):
    return {s: {"quote": {"lastPrice": 100.0, "openPrice": 99.0, "highPrice": 101.0,
                          "lowPrice": 98.0, "totalVolume": 1000}}
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
    assert out == ["upstream", "shadow_bar_match", "shadow_bar_volume_match",
                   "upstream", "shadow_hit_match", "shadow_moving_same",
                   "shadow_bar_match", "shadow_bar_volume_match"]


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


def quote_100_series_90(endpoint, params):
    """The quote says 100; Schwab's own bar for today closes at 90."""
    return (quotes_for(endpoint, params) if endpoint == "/quotes"
            else series(FRI, MON, close=90.0))


def last_close(served):
    return body(served)["candles"][-1]["close"]


def test_quote_mode_never_builds_todays_bar_from_a_quote_fetched_before_the_open():
    h = Harness(Cfg(bars__today_bar="quote"), responses=quote_100_series_90)
    set_time(h, 8, 29, 30)
    h.gw.quotes("SPY", "market_svc")                 # yesterday's open, high, low
    set_time(h, 8, 30, 10)
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"     # first of the period
    set_time(h, 8, 30, 30)
    # The quote is 60 s old, inside the 120 s limit, but the session is 30 s old:
    # it is not used. The series is 20 s old, so Schwab's own bar is served.
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "hit" and last_close(got) == 90.0
    assert bar_calls(h) == 1
    set_time(h, 8, 30, 40)
    h.gw.quotes("SPY", "market_svc", max_age=0)      # fetched since the open
    set_time(h, 8, 30, 50)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and last_close(got) == 100.0
    assert bar_calls(h) == 1


def test_a_pre_open_quote_is_not_used_when_the_series_is_past_its_limit_either():
    h = Harness(Cfg(bars__today_bar="quote", bars__session_ttl_sec=15),
                responses=quote_100_series_90)
    set_time(h, 8, 29, 30)
    h.gw.quotes("SPY", "market_svc")
    set_time(h, 8, 30, 10)
    h.gw.pricehistory(BAR, "scan")
    set_time(h, 8, 30, 30)                           # series 20 s old, limit 15
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "miss" and last_close(got) == 90.0
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
                               "upstream", "shadow_hit_match", "shadow_moving_same",
                               "shadow_bar_match", "shadow_bar_volume_match"]


def test_at_the_instant_of_the_open_no_quote_is_usable():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    set_time(h, 8, 30, 0)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream"]


def test_well_after_the_open_the_quotes_own_age_limit_still_applies():
    h = Harness(Cfg(bars__today_bar="quote"), responses=quote_100_series_90)
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    h.clock += 120
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and last_close(got) == 100.0
    h.clock += 1                                     # the quote is now too old
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "hit" and last_close(got) == 90.0 and bar_calls(h) == 1


# ---- quote mode is never worse than ttl mode --------------------------------

def test_quote_mode_without_a_quote_serves_the_series_inside_the_session_limit():
    # The plain limit, to the second: the spread (PF-100) is tested on its own.
    h = Harness(Cfg(bars__today_bar="quote", bars__session_spread=False),
                responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 600
    got = h.gw.pricehistory(BAR, "sentiment")
    assert (got.kind, got.age) == ("hit", 600.0) and body(got) == series(FRI, MON)
    h.clock += 1140                                  # the limit itself still hits
    assert h.gw.pricehistory(BAR, "sentiment").kind == "hit"
    assert len(h.calls) == 1
    assert bar_outcomes(h) == ["upstream", "hit", "hit"]


def test_quote_mode_without_a_quote_fetches_past_the_session_limit():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 1741
    assert h.gw.pricehistory(BAR, "scan").kind == "miss" and len(h.calls) == 2
    h.clock += 600                                   # the new series is 600 s old
    assert h.gw.pricehistory(BAR, "scan").kind == "hit" and len(h.calls) == 2


@pytest.mark.parametrize("series_age", [0, 600, 1740, 1741, 20000])
def test_quote_mode_with_a_usable_quote_composes_however_old_the_series_is(series_age):
    h = Harness(Cfg(bars__today_bar="quote"), responses=quote_100_series_90)
    h.gw.pricehistory(BAR, "scan")
    h.clock += series_age
    h.gw.quotes("SPY", "market_svc", max_age=0)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and last_close(got) == 100.0
    assert bar_calls(h) == 1 and bar_outcomes(h) == ["upstream", "composed"]


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
    got = h.gw.quotes("SPY, DIA", "b")               # DIA is not held: no full answer
    assert got.kind == "pass" and list(got.data) == ["SPY", "DIA"]
    assert h.calls[-1][1]["symbols"] == "SPY,DIA"
    assert h.outcomes() == ["upstream", "upstream", "shadow_partial"]
    h.clock += 6                                     # past the limit: no verdict
    h.gw.quotes("SPY", "c")
    assert h.outcomes() == ["upstream", "upstream", "shadow_partial", "upstream"]


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
    # The plain limit, to the second: the spread (PF-100) is tested on its own.
    h = Harness(Cfg(bars__session_spread=False), responses=bars_for)
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


def test_the_bar_is_not_reused_until_it_has_settled():
    """From the close to ``settle_min`` after it the day's bar is still being
    finalised, so every request is fetched; the first fetch at the settle is
    the one reused all evening. (Until AC-101 a request at 15:09:59 was a HIT
    on whatever had been fetched at 15:05 - and, worse, at 14:45.)"""
    h = Harness(responses=bars_for)
    set_time(h, 15, 5)                               # closed, not yet settled
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    set_time(h, 15, 9, 59)
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    set_time(h, 15, 10)                              # settle_min = 10
    assert h.gw.pricehistory(BAR, "x").kind == "miss" and len(h.calls) == 3
    set_time(h, 15, 40)
    assert h.gw.pricehistory(BAR, "x").kind == "hit" and len(h.calls) == 3

    h = Harness(Cfg(bars__settle_min=20), responses=bars_for)
    set_time(h, 15, 5)
    h.gw.pricehistory(BAR, "x")
    set_time(h, 15, 19)
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    set_time(h, 15, 20)
    assert h.gw.pricehistory(BAR, "x").kind == "miss" and len(h.calls) == 3


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


def test_a_quote_that_cannot_build_a_bar_is_the_same_as_no_quote():
    h = Harness(Cfg(bars__today_bar="quote"),
                responses=lambda e, p: {"SPY": {"quote": {"lastPrice": 100.0}}}
                if e == "/quotes" else series(FRI, MON, close=90.0))
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    got = h.gw.pricehistory(BAR, "scan")             # inside the session limit
    assert got.kind == "hit" and last_close(got) == 90.0 and bar_calls(h) == 1
    h.clock += 1741
    h.gw.quotes("SPY", "market_svc", max_age=0)
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
    assert bar_outcomes(h) == ["upstream", "upstream", "shadow_hit_match"]


def test_shadow_reports_a_bar_that_disagrees_and_a_day_schwab_has_not_sent():
    h = Harness(Cfg(mode="shadow"),
                responses=lambda e, p: quotes_for(e, p) if e == "/quotes"
                else series(FRI, MON, close=90.0))
    h.gw.quotes("SPY", "market_svc")
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "pass" and got.data == series(FRI, MON, close=90.0)
    assert bar_outcomes(h) == ["upstream", "shadow_bar_mismatch",
                               "shadow_bar_volume_match"]

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


# ---- the store's three remaining limits are settings ------------------------

def test_the_harness_defaults_are_the_shipped_defaults():
    # A stand-in config that drifts from the real one tests nothing.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from shared import marketdata_config
    for name, table in DEFAULTS.items():
        assert table == marketdata_config.DEFAULTS[name]


def test_the_configured_wide_refetch_limit_is_enforced():
    for limit, fetched_to in ((6, "2026-10-09"), (7, "2026-10-12")):
        h = Harness(Cfg(chains__wide_refetch_max_days=limit))
        h.gw.chains(P(), "collector")                # a 7-day window is held
        h.clock += 50
        assert h.gw.chains(P(to="2026-10-09"), "scan").kind == "miss"
        assert h.calls[-1][1]["toDate"] == fetched_to and len(h.calls) == 2


def test_a_longer_configured_limit_refetches_a_longer_held_window():
    for limit, fetched_to in ((10, "2026-10-09"), (45, "2026-11-19")):
        h = Harness(Cfg(chains__wide_refetch_max_days=limit))
        h.gw.chains(P(to="2026-11-19"), "scan")      # a 45-day window is held
        h.clock += 50
        h.gw.chains(P(to="2026-10-09"), "scan")
        assert h.calls[-1][1]["toDate"] == fetched_to


def test_the_configured_symbol_limit_is_enforced():
    h = Harness(Cfg(quotes__max_symbols=1), responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.gw.quotes("QQQ", "a")
    assert h.gw.quotes("QQQ", "a").kind == "hit"
    assert h.gw.quotes("SPY", "a").kind == "miss"         # SPY was dropped

    h = Harness(Cfg(mode="shadow", quotes__max_symbols=1), responses=quotes_for)
    for symbol in ("SPY", "QQQ", "SPY"):
        h.gw.quotes(symbol, "a")
    assert h.outcomes() == ["upstream"] * 3               # no would-be hit
    h.gw.quotes("SPY", "a")
    assert h.outcomes()[-1] == "shadow_hit"


def test_the_configured_series_limit_is_enforced():
    qqq = {**BAR, "symbol": "QQQ"}
    h = Harness(Cfg(bars__max_entries=1), responses=bars_for)
    h.gw.pricehistory(BAR, "a")
    h.gw.pricehistory(qqq, "a")
    assert h.gw.pricehistory(qqq, "a").kind == "hit"
    assert h.gw.pricehistory(BAR, "a").kind == "miss"     # SPY was dropped

    h = Harness(Cfg(mode="shadow", bars__max_entries=1), responses=bars_for)
    for params in (BAR, qqq, BAR):
        h.gw.pricehistory(params, "a")
    assert bar_outcomes(h) == ["upstream"] * 3            # no would-be hit
    h.gw.pricehistory(BAR, "a")
    assert bar_outcomes(h)[-2:] == ["shadow_hit_match", "shadow_moving_same"]


# ---- shadow measures what mode on would do ----------------------------------
# Shadow's counts are read as "calls that on would have saved", so shadow makes
# on's decision with on's limits, and keeps the store as on would have left it:
# a request on would have answered locally stores nothing.

def test_shadow_uses_the_callers_own_age_limit():
    h = Harness(Cfg(mode="shadow"))
    for _ in range(5):                               # the collector's poll
        h.gw.chains(P(), "collector", max_age=20)
        h.clock += 60
    assert not [o for o in h.outcomes() if o.startswith(("shadow_hit", "shadow_subset"))]
    assert h.outcomes() == ["upstream"] + ["upstream", "shadow_cmp_match"] * 4


def test_shadow_uses_the_closed_market_limit():
    h = Harness(Cfg(mode="shadow"))
    FakeCal.state = "CLOSED"
    h.gw.chains(P(), "a")
    h.clock += 600
    h.gw.chains(P(), "a")
    assert h.outcomes()[-1] == "shadow_hit_match"


def test_a_compare_only_verdict_reports_a_shape_difference():
    state = {"thin": False}
    h = Harness(Cfg(mode="shadow"),
                responses=lambda e, p: chain(exps=EXPS[:3]) if state["thin"] else chain())
    h.gw.chains(P(), "a")
    h.clock += 60
    state["thin"] = True
    h.gw.chains(P(), "a")
    assert h.outcomes() == ["upstream", "upstream", "shadow_cmp_mismatch"]


def test_a_would_be_chain_answer_is_not_stored():
    state = {"spot": 100.0}
    h = Harness(Cfg(mode="shadow"), responses=lambda e, p: chain(spot=state["spot"]))
    wide, narrow = ms.ChainKey.from_params(P()), ms.ChainKey.from_params(P(to="2026-10-09"))
    h.gw.chains(P(), "collector")
    h.clock += 10
    state["spot"] = 101.0
    h.gw.chains(P(), "collector")                    # on would have answered this
    h.gw.chains(P(to="2026-10-09"), "scan")          # and this, from the wide chain
    assert h.outcomes()[-4:] == ["upstream", "shadow_hit_match",
                                 "upstream", "shadow_subset_mismatch"]
    held = h.gw.chain_store.lookup(wide, max_age=45, now=h.clock, state="REGULAR")
    assert held.age == 10.0 and json.loads(held.body)["underlyingPrice"] == 100.0
    # Nothing was stored under the narrow request either: it is still a cut.
    assert h.gw.chain_store.lookup(narrow, max_age=45, now=h.clock,
                                   state="REGULAR").kind == "subset"


def test_a_would_be_quote_hit_leaves_the_stored_quote_ageing():
    h = Harness(Cfg(mode="shadow"), responses=quotes_for)
    for _ in range(4):                               # every 3 s against a 5 s limit
        h.gw.quotes("SPY", "a")
        h.clock += 3
    assert h.outcomes() == ["upstream", "upstream", "shadow_hit",
                            "upstream", "upstream", "shadow_hit"]


def test_a_shadow_partial_stores_only_what_on_would_have_fetched():
    h = Harness(Cfg(mode="shadow"), responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.clock += 3
    h.gw.quotes("SPY,DIA", "a")                      # on would have fetched DIA alone
    h.clock += 3
    h.gw.quotes("SPY", "a")                          # 6 s old: the partial did not refresh it
    h.gw.quotes("DIA", "a")                          # 3 s old
    assert h.outcomes() == ["upstream", "upstream", "shadow_partial",
                            "upstream", "upstream", "shadow_hit"]


def test_shadow_bars_follow_the_session_limit():
    # The plain limit: the spread (PF-100) is tested on its own.
    h = Harness(Cfg(mode="shadow", bars__session_spread=False), responses=bars_for)
    for _ in range(5):                               # every 600 s against 1740 s
        h.gw.pricehistory(BAR, "scan")
        h.clock += 600
    hit = ["upstream", "shadow_hit_match", "shadow_moving_same"]
    assert bar_outcomes(h) == ["upstream"] + hit + hit + ["upstream"] + hit


def test_shadow_reports_a_would_be_composed_bar_and_stores_nothing():
    h = Harness(Cfg(mode="shadow", bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")                   # stored at 1000
    h.clock += 5000                                  # far past the session limit
    h.gw.quotes("SPY", "market_svc", max_age=0)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "pass" and got.data == series(FRI, MON)
    assert bar_outcomes(h) == ["upstream",
                               "upstream", "shadow_composed", "shadow_bar_match",
                               "shadow_bar_volume_match"]
    _, fetched_at = h.gw.bar_store.get(ms.bar_key(BAR), epoch=("2026-10-05", "live"))
    assert fetched_at == 1000.0


def test_shadow_quote_mode_without_a_quote_follows_the_session_limit():
    h = Harness(Cfg(mode="shadow", bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 600
    h.gw.pricehistory(BAR, "scan")
    h.clock += 1141                                  # 1741 s old: on would fetch
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h) == ["upstream", "upstream", "shadow_hit_match",
                               "shadow_moving_same", "upstream"]


# The sequences below are run once in on and once in shadow with the same
# clock. Shadow's would-be answers must be exactly on's local answers.

LOCAL_KINDS = ("hit", "subset", "composed", "partial")


def would_be(outcome):
    """What a shadow outcome says on would have answered, or None for one that
    claims no saving (the compare-only, today's-bar and moving-bar verdicts)."""
    if (not outcome.startswith("shadow_")
            or outcome.startswith(("shadow_cmp_", "shadow_bar_", "shadow_moving_"))):
        return None
    return outcome[len("shadow_"):].split("_")[0]


def play(mode, steps, responses, **over):
    h = Harness(Cfg(mode=mode, **over), responses=responses)
    answers = []
    for advance, ask in steps:
        h.clock += advance
        before = len(h.records)
        served = ask(h)
        if mode == "on":
            answers.append(served.kind if served.kind in LOCAL_KINDS else "fetch")
        else:
            assert served.kind == "pass"
            said = [w for w in (would_be(r[2]) for r in h.records[before:]) if w]
            assert len(said) <= 1
            answers.append(said[0] if said else "fetch")
    return answers, len(h.calls)


def collector(h):
    return h.gw.chains(P(), "collector", max_age=20)


def scan_same(h):
    return h.gw.chains(P(), "scan")


def scan_narrow(h):
    return h.gw.chains(P(to="2026-10-09"), "scan")


def daily(h):
    return h.gw.pricehistory(BAR, "scan")


def poll_quote(h):
    return h.gw.quotes("SPY", "market_svc", max_age=0)


SEQUENCES = {
    # The collector's own poll never re-reads itself: on saves nothing.
    "collector every 60 s with its own 20 s limit":
        ([(60, collector)] * 20, lambda e, p: chain(), {},
         ["fetch"] * 20),
    # The same chain asked every 30 s against the 45 s limit: every other one.
    "one chain every 30 s":
        ([(30, scan_same)] * 8, lambda e, p: chain(), {},
         ["fetch", "hit"] * 4),
    # A scan 5 s behind each collector poll is cut from the collector's chain.
    "a scan behind the collector":
        ([(55, collector), (5, scan_narrow)] * 6, lambda e, p: chain(), {},
         ["fetch", "subset"] * 6),
    # Quotes every 3 s against the 5 s limit, with a second caller overlapping.
    "quotes every 3 s and an overlapping caller":
        ([(3, lambda h: h.gw.quotes("SPY,QQQ", "a")),
          (1, lambda h: h.gw.quotes("SPY,DIA", "b"))] * 4, quotes_for, {},
         ["fetch", "partial", "hit", "hit", "fetch", "partial", "hit", "hit"]),
    # A daily series every 600 s during the session, against 1740 s.
    # (The plain limit: the spread, PF-100, is tested on its own.)
    "a daily series every 600 s":
        ([(600, daily)] * 8, bars_for, {"bars__session_spread": False},
         ["fetch", "hit", "hit", "fetch", "hit", "hit", "fetch", "hit"]),
    # Quote mode with the dashboard's poll keeping the quote fresh.
    "quote mode with a polled quote":
        ([(0, daily)] + [(700, poll_quote), (1, daily)] * 4, bars_for,
         {"bars__today_bar": "quote"},
         ["fetch"] + ["fetch", "composed"] * 4),
}


@pytest.mark.parametrize("name", list(SEQUENCES))
def test_shadow_counts_exactly_what_on_would_have_answered_locally(name):
    steps, responses, over, expected = SEQUENCES[name]
    on, on_calls = play("on", steps, responses, **over)
    shadow, shadow_calls = play("shadow", steps, responses, **over)
    assert on == expected
    assert shadow == on
    assert shadow_calls == len(steps)                # shadow always calls
    assert on_calls == sum(a in ("fetch", "partial") for a in on)


# ---- a fetch that fails is not a store bug ----------------------------------

ASKS = {"chains": lambda h: h.gw.chains(P(), "a"),
        "quotes": lambda h: h.gw.quotes("SPY", "a"),
        "pricehistory": lambda h: h.gw.pricehistory(BAR, "a")}


@pytest.mark.parametrize("mode", ["on", "shadow", "off"])
@pytest.mark.parametrize("endpoint", list(ASKS))
def test_a_fetch_failure_that_is_not_an_upstream_error_is_raised_as_it_is(mode, endpoint):
    crash = RuntimeError("connection reset")
    h = Harness(Cfg(mode=mode), responses=lambda e, p: crash)
    with pytest.raises(RuntimeError) as err:
        ASKS[endpoint](h)
    assert err.value is crash                        # the original, not a wrapper
    assert len(h.calls) == 1                         # no second call
    assert h.gw.degrades == {} and h.records == []   # and no store bug counted


def test_the_original_failure_keeps_its_own_cause():
    def respond(endpoint, params):
        raise RuntimeError("connection reset") from OSError("socket closed")

    h = Harness(responses=respond)
    with pytest.raises(RuntimeError) as err:
        h.gw.chains(P(), "a")
    assert isinstance(err.value.__cause__, OSError)
    assert h.gw.degrades == {}


def test_a_fetch_failure_on_the_wide_refetch_is_raised_as_it_is():
    state = {"crash": None}
    h = Harness(responses=lambda e, p: state["crash"] or chain())
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["crash"] = RuntimeError("connection reset")
    with pytest.raises(RuntimeError) as err:
        h.gw.chains(P(to="2026-10-09"), "scan")
    # The week, then the request as asked (audit AR-100); both failed.
    assert err.value is state["crash"] and len(h.calls) == 3 and h.gw.degrades == {}


def test_a_fetch_failure_in_the_fallback_after_a_store_bug_is_raised_as_it_is(monkeypatch):
    crash = RuntimeError("connection reset")
    h = Harness(responses=lambda e, p: crash)
    monkeypatch.setattr(h.gw.chain_store, "lookup", boom)
    with pytest.raises(RuntimeError) as err:
        h.gw.chains(P(), "a")
    assert err.value is crash and len(h.calls) == 1
    assert h.gw.degrades == {"chains": 1}            # the store bug, once


# ---- waiters behind a failed fetch share its error --------------------------

FAILED_ASKS = {"chains": (lambda h, who: h.gw.chains(P(), who), chain),
               "pricehistory": (lambda h, who: h.gw.pricehistory(BAR, who),
                                lambda: series(FRI, MON))}


@pytest.mark.parametrize("endpoint", list(FAILED_ASKS))
def test_waiters_behind_a_failed_fetch_raise_its_error_and_make_no_call(endpoint):
    ask, good = FAILED_ASKS[endpoint]
    state = {"fail": True}
    slow = Slow(lambda e, p: ms.UpstreamError(503, "unavailable")
                if state["fail"] else good())
    h = Harness(responses=slow)
    out = all_at_once(h, slow, lambda: ask(h, "a"), [lambda: ask(h, "b")] * 5)
    assert len(h.calls) == 1                         # six requests, one call
    assert len(out) == 6
    for error in out:
        assert isinstance(error, ms.UpstreamError)
        assert (error.status_code, error.detail) == (503, "unavailable")
    assert h.records == [] and h.gw.degrades == {}
    # A request that arrives after the failure asks Schwab itself.
    with pytest.raises(ms.UpstreamError):
        ask(h, "c")
    assert len(h.calls) == 2
    state["fail"] = False
    assert ask(h, "c").kind == "miss" and len(h.calls) == 3
    assert h.gw._failures == {}                      # cleared by the success
    assert ask(h, "c").kind == "hit" and len(h.calls) == 3


@pytest.mark.parametrize("endpoint", list(FAILED_ASKS))
def test_waiters_behind_a_fetch_that_crashed_raise_the_same_crash(endpoint):
    ask, _good = FAILED_ASKS[endpoint]
    crash = RuntimeError("connection reset")
    slow = Slow(lambda e, p: crash)
    h = Harness(responses=slow)
    out = all_at_once(h, slow, lambda: ask(h, "a"), [lambda: ask(h, "b")] * 3)
    assert len(h.calls) == 1 and all(error is crash for error in out)
    assert h.gw.degrades == {}


def test_a_waiter_is_answered_from_the_store_when_the_fetch_succeeded():
    # The failure record of an EARLIER fetch must not reach later waiters.
    state = {"fail": True}
    slow = Slow(lambda e, p: ms.UpstreamError(503, "unavailable")
                if state["fail"] else chain())
    h = Harness(responses=slow)
    slow.gate.set()
    with pytest.raises(ms.UpstreamError):
        h.gw.chains(P(), "a")
    slow.gate.clear()
    slow.started.clear()
    state["fail"] = False
    first, second = two_at_once(h, slow, lambda: h.gw.chains(P(), "a"),
                                lambda: h.gw.chains(P(), "b"))
    assert (first.kind, second.kind) == ("miss", "coalesced") and len(h.calls) == 2


# ---- the wide refetch stays inside the collector's week ---------------------

def test_the_collectors_week_is_never_widened_to_a_longer_held_window():
    h = Harness()
    h.gw.chains(P(to="2026-10-15"), "term")          # today -> +10 is held
    h.clock += 50
    got = h.gw.chains(P(), "collector", max_age=20)  # today -> +7, a near miss
    assert got.kind == "miss" and h.calls[-1][1]["toDate"] == "2026-10-12"
    assert sorted(got.data["callExpDateMap"]) == list(EXPS)


def test_a_near_miss_inside_the_collectors_week_still_refetches_the_week():
    h = Harness()
    h.gw.chains(P(), "collector")                    # today -> +7 is held
    h.clock += 50
    got = h.gw.chains(P(to="2026-10-09"), "scan")    # today -> +4
    assert got.kind == "miss" and h.calls[-1][1]["toDate"] == "2026-10-12"
    assert sorted(body(got)["callExpDateMap"]) == list(EXPS[:3])


# ---- an entry is as old as the moment its fetch began -----------------------

def taking(h, seconds, answer):
    """An upstream call that takes ``seconds`` on the harness clock."""
    def respond(endpoint, params):
        h.clock += seconds
        return answer(endpoint, params)
    return respond


@pytest.mark.parametrize("mode", ["on", "shadow"])
def test_a_stored_entry_is_stamped_when_its_fetch_began(mode):
    h = Harness(Cfg(mode=mode))
    h.responses = taking(h, 4, lambda e, p: chain())
    h.gw.chains(P(), "a")                            # asked at 1000, answered at 1004
    held = h.gw.chain_store.lookup(ms.ChainKey.from_params(P()), max_age=45,
                                   now=h.clock, state="REGULAR")
    assert held.age == 4.0

    h = Harness(Cfg(mode=mode))
    h.responses = taking(h, 4, quotes_for)
    h.gw.quotes("SPY", "a")
    assert h.gw.quote_store.split(["SPY"], max_age=5, now=h.clock)[2] == 4.0

    h = Harness(Cfg(mode=mode))
    h.responses = taking(h, 4, bars_for)
    h.gw.pricehistory(BAR, "a")
    assert h.gw.bar_store.get(ms.bar_key(BAR), epoch=("2026-10-05", "live"))[1] == 1000.0


def test_a_hit_reports_the_age_since_the_fetch_began():
    h = Harness()
    h.responses = taking(h, 4, lambda e, p: chain())
    h.gw.chains(P(), "a")
    got = h.gw.chains(P(), "b")
    assert (got.kind, got.age) == ("hit", 4.0)


def test_a_quote_asked_before_the_open_and_answered_after_it_is_not_post_open():
    h = Harness(Cfg(bars__today_bar="quote"))
    state = {"slow": True}

    def respond(endpoint, params):
        if endpoint == "/quotes" and state["slow"]:
            set_time(h, 8, 30, 5)                    # the answer lands after the open
        return quote_100_series_90(endpoint, params)

    h.responses = respond
    set_time(h, 8, 29, 50)
    h.gw.quotes("SPY", "market_svc")                 # sent before the open
    state["slow"] = False
    set_time(h, 8, 30, 10)
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"     # first of the period
    set_time(h, 8, 30, 20)
    got = h.gw.pricehistory(BAR, "scan")
    # The quote's values are the prior session's, whenever the answer arrived.
    assert got.kind == "hit" and last_close(got) == 90.0


# ---- after the close, before the bar settles --------------------------------

def test_after_the_close_quote_mode_does_not_build_todays_bar():
    h = Harness(Cfg(bars__today_bar="quote"), responses=quote_100_series_90)
    set_time(h, 14, 59, 0)
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"
    set_time(h, 14, 59, 30)
    h.gw.quotes("SPY", "market_svc", max_age=0)
    got = h.gw.pricehistory(BAR, "scan")             # still in the session
    assert got.kind == "composed" and last_close(got) == 100.0
    # From the close on, today's bar is not BUILT from the quote - and (AC-101)
    # the series fetched before the close is not served either: Schwab is asked.
    set_time(h, 15, 0, 0)                            # the close itself
    h.gw.quotes("SPY", "market_svc", max_age=0)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "miss" and last_close(got) == 90.0
    set_time(h, 15, 1, 0)                            # the quote may hold later prints
    h.gw.quotes("SPY", "market_svc", max_age=0)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "miss" and last_close(got) == 90.0 and bar_calls(h) == 3


def test_after_the_close_the_time_limit_rule_decides():
    h = Harness(Cfg(bars__today_bar="quote", bars__session_ttl_sec=60),
                responses=quote_100_series_90)
    set_time(h, 14, 59, 30)
    h.gw.pricehistory(BAR, "scan")
    set_time(h, 15, 1, 0)                            # 90 s old against 60
    h.gw.quotes("SPY", "market_svc", max_age=0)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "miss" and last_close(got) == 90.0 and bar_calls(h) == 2


def test_shadow_gives_no_bar_verdict_and_no_composed_answer_after_the_close():
    h = Harness(Cfg(mode="shadow", bars__today_bar="quote"), responses=bars_for)
    set_time(h, 14, 59, 0)
    h.gw.pricehistory(BAR, "scan")
    set_time(h, 15, 1, 0)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    # No verdict at all: after the close "on" serves nothing from the store, so
    # shadow has no would-be answer to judge. (This listed shadow_hit_match and
    # shadow_moving_same while a pre-close fetch was still served - AC-101.)
    assert bar_outcomes(h) == ["upstream", "upstream"]


# ---- reported ages and the order of a partial answer ------------------------

def test_a_partial_answer_is_in_the_callers_order_and_as_old_as_its_oldest_symbol():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.clock += 2
    h.gw.quotes("QQQ", "a")
    h.clock += 2
    got = h.gw.quotes("DIA,QQQ,IWM,SPY", "b")        # SPY is 4 s old, QQQ 2 s
    assert h.calls[-1][1]["symbols"] == "DIA,IWM"
    assert (got.kind, got.age) == ("partial", 4.0)
    assert list(got.data) == ["DIA", "QQQ", "IWM", "SPY"]


def test_what_schwab_sent_beside_the_symbols_follows_them_in_a_partial_answer():
    def respond(endpoint, params):
        out = {s: quotes_for(endpoint, {"symbols": s})[s]
               for s in params["symbols"].split(",") if s != "XX"}
        out["errors"] = {"invalidSymbols": ["XX"]}
        return out

    h = Harness(responses=respond)
    h.gw.quotes("SPY", "a")
    got = h.gw.quotes("XX,DIA,SPY", "a")
    assert got.kind == "partial" and list(got.data) == ["DIA", "SPY", "errors"]


def test_a_quote_miss_reports_no_age_and_is_schwabs_answer_untouched():
    h = Harness(responses=quotes_for)
    got = h.gw.quotes("SPY,QQQ", "a")
    assert (got.kind, got.age) == ("miss", 0.0) and list(got.data) == ["SPY", "QQQ"]


def test_a_composed_bar_is_as_old_as_the_quote_it_was_built_from():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 500
    h.gw.quotes("SPY", "market_svc")
    h.clock += 7
    got = h.gw.pricehistory(BAR, "scan")
    assert (got.kind, got.age) == ("composed", 7.0)


def test_a_coalesced_series_reports_its_real_age():
    slow = Slow(bars_for)
    h = Harness(responses=slow)
    ticks = iter(range(1000, 2000))
    h.gw._clock = lambda: float(next(ticks))         # one second per reading
    first, second = two_at_once(h, slow,
                                lambda: h.gw.pricehistory(BAR, "a"),
                                lambda: h.gw.pricehistory(BAR, "b"))
    # a: the fetch begins (1000).  b: reads the clock once, on its answer (1001).
    assert (first.kind, second.kind, second.age) == ("miss", "coalesced", 1.0)


# ---- a stored body is valid JSON --------------------------------------------

def with_gamma(value):
    out = chain()
    out["callExpDateMap"]["2026-10-05:0"]["95.0"][0]["gamma"] = value
    return out


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_chain_holding_a_number_that_is_not_json_is_returned_and_never_stored(bad):
    for payload in (with_gamma(bad), {**chain(), "underlyingPrice": bad}):
        h = Harness(responses=lambda e, p: payload)
        got = h.gw.chains(P(), "a")
        assert got.kind == "miss" and got.data is payload    # what Schwab sent
        assert h.gw.chains(P(), "a").kind == "miss" and len(h.calls) == 2
        assert h.gw.degrades == {}

        h = Harness(Cfg(mode="shadow"), responses=lambda e, p: payload)
        h.gw.chains(P(), "a")
        h.gw.chains(P(), "a")
        assert h.outcomes() == ["upstream", "upstream"]      # nothing was held


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_series_holding_a_number_that_is_not_json_is_returned_and_never_stored(bad):
    payload = series(FRI, MON, close=bad)
    h = Harness(responses=lambda e, p: payload)
    got = h.gw.pricehistory(BAR, "a")
    assert got.kind == "miss" and got.data is payload
    assert h.gw.pricehistory(BAR, "a").kind == "miss" and len(h.calls) == 2
    assert h.gw.degrades == {}


# ---- the today's-bar verdict checks open and volume --------------------------

DROP = object()


def quote_with(bar_volume=1000, **fields):
    """A fixture quote for SPY with some fields replaced (or, for ``DROP``,
    removed), beside a series whose bars carry ``bar_volume`` (``DROP``: no
    volume field at all)."""
    def respond(endpoint, params):
        if endpoint != "/quotes":
            out = series(FRI, MON)
            for candle in out["candles"]:
                if bar_volume is DROP:
                    del candle["volume"]
                else:
                    candle["volume"] = bar_volume
            return out
        out = quotes_for(endpoint, params)
        for block in out.values():
            for name, value in fields.items():
                if value is DROP:
                    block["quote"].pop(name, None)
                else:
                    block["quote"][name] = value
        return out
    return respond


def shadow_bar_verdicts(responses):
    h = Harness(Cfg(mode="shadow"), responses=responses)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    return bar_outcomes(h)


def test_shadow_reports_a_quote_built_bar_whose_open_disagrees():
    assert shadow_bar_verdicts(quote_with(openPrice=95.0)) == [
        "upstream", "shadow_bar_mismatch", "shadow_bar_volume_match"]


def test_shadow_reports_volume_on_its_own_beside_the_price_verdict():
    assert shadow_bar_verdicts(quote_with(totalVolume=10)) == [
        "upstream", "shadow_bar_match", "shadow_bar_volume_mismatch"]
    assert shadow_bar_verdicts(quote_with(totalVolume=960)) == [
        "upstream", "shadow_bar_match", "shadow_bar_volume_match"]


PRICE_ONLY = ["upstream", "shadow_bar_match"]
ZEROED = ["upstream", "shadow_bar_match", "shadow_bar_volume_mismatch"]


def test_a_symbol_with_no_quoted_volume_still_gets_its_price_verdict():
    # An index: no volume in the quote and none in Schwab's bar. The price
    # verdict stands and there is no volume outcome at all.
    assert shadow_bar_verdicts(quote_with(bar_volume=0, totalVolume=0)) == PRICE_ONLY
    assert shadow_bar_verdicts(quote_with(bar_volume=0, totalVolume=DROP)) == PRICE_ONLY
    assert shadow_bar_verdicts(quote_with(bar_volume=DROP, totalVolume=DROP)) == PRICE_ONLY
    # Schwab's bar HAS volume: the price verdict still stands, and the volume
    # the quote-built bar would write (0) is reported as the mismatch it is.
    assert shadow_bar_verdicts(quote_with(bar_volume=1000, totalVolume=0)) == ZEROED
    assert shadow_bar_verdicts(quote_with(bar_volume=1000, totalVolume=DROP)) == ZEROED


def test_no_usable_quote_means_no_volume_verdict_either():
    assert shadow_bar_verdicts(quote_with(lastPrice=0)) == ["upstream"]


def test_a_composed_bar_carries_the_quotes_volume():
    h = Harness(Cfg(bars__today_bar="quote"), responses=quote_with(totalVolume=4321))
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and got.data["candles"][-1]["volume"] == 4321


# ---- a recorded failure keeps no exception alive ----------------------------

def test_a_recorded_upstream_failure_holds_no_exception_object():
    # An exception object carries its traceback, and so every frame's locals,
    # until the next success on that key.
    h = Harness(responses=lambda e, p: ms.UpstreamError(503, "unavailable"))
    for ask in (lambda: h.gw.chains(P(), "a"), lambda: h.gw.pricehistory(BAR, "a")):
        with pytest.raises(ms.UpstreamError):
            ask()
    assert len(h.gw._failures) == 2
    for _ticket, what in h.gw._failures.values():
        assert what == (503, "unavailable")
        assert not isinstance(what, BaseException)


def test_a_recorded_crash_keeps_the_original_to_hand_to_waiters():
    crash = RuntimeError("connection reset")
    h = Harness(responses=lambda e, p: crash)
    with pytest.raises(RuntimeError):
        h.gw.chains(P(), "a")
    ((_ticket, what),) = h.gw._failures.values()
    assert what.original is crash


# ---- the newest held chain is the one served ---------------------------------

def test_the_collectors_not_due_request_gets_the_scans_newer_chain():
    # Its own 7-day chain is 80 s old; the scan's 45-day chain is 10 s old.
    state = {"spot": 100.0}
    h = Harness(responses=lambda e, p: chain(spot=state["spot"]))
    h.gw.chains(P(), "collector")
    h.clock += 70
    state["spot"] = 101.0
    h.gw.chains(P(to="2026-11-19"), "scan")
    h.clock += 10
    got = h.gw.chains(P(), "collector", max_age=210)
    assert (got.kind, got.age) == ("subset", 10.0) and len(h.calls) == 2
    assert body(got)["underlyingPrice"] == 101.0
    assert h.records[-1] == ("chains", "collector", "subset")


def test_shadow_reports_the_newer_covering_chain_as_the_would_be_answer():
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "collector")
    h.clock += 70
    h.gw.chains(P(to="2026-11-19"), "scan")
    h.clock += 10
    h.gw.chains(P(), "collector", max_age=210)
    assert h.outcomes()[-2:] == ["upstream", "shadow_subset_match"]


# ---- shadow compares a stored daily series with Schwab's fresh one -----------

def moving_series(state):
    """Schwab's series, whatever ``state["series"]`` is at the time."""
    return lambda e, p: quotes_for(e, p) if e == "/quotes" else state["series"]


def revised(src, index, **fields):
    out = json.loads(json.dumps(src))
    out["candles"][index].update(fields)
    return out


def shadow_repeat(first, second, at=(10, 0)):
    """Two requests for one series in shadow; Schwab sends ``first`` then
    ``second``. Returns the outcomes of the second request."""
    state = {"series": first}
    h = Harness(Cfg(mode="shadow"), responses=moving_series(state))
    set_time(h, *at)
    h.gw.pricehistory(BAR, "scan")
    state["series"] = second
    before = len(h.records)
    h.gw.pricehistory(BAR, "scan")
    return [r[2] for r in h.records[before:]]


SETTLED = (16, 0)
THREE = series(dt.date(2026, 10, 1), FRI, MON)


def test_an_identical_settled_series_is_a_match():
    assert shadow_repeat(THREE, THREE, at=SETTLED) == ["upstream", "shadow_hit_match"]


def test_a_revised_close_on_the_last_bar_after_it_settled_is_a_mismatch():
    assert shadow_repeat(THREE, revised(THREE, -1, close=101.5), at=SETTLED) == [
        "upstream", "shadow_hit_mismatch"]


def test_todays_bar_moving_during_the_session_is_still_a_match():
    moved = revised(THREE, -1, high=105.0, close=104.0, volume=5000)
    # The SERIES still matches - and the moving bar's own verdict says how far
    # the stored close (100) was from the fresh one (104).
    assert shadow_repeat(THREE, moved) == ["upstream", "shadow_hit_match",
                                           "shadow_moving_over_50bp"]
    # The same difference once the day is over is a mismatch.
    assert shadow_repeat(THREE, moved, at=SETTLED) == ["upstream", "shadow_hit_mismatch"]


def test_a_revised_historical_bar_during_the_session_is_a_mismatch():
    assert shadow_repeat(THREE, revised(THREE, 0, close=101.5)) == [
        "upstream", "shadow_hit_mismatch", "shadow_moving_same"]


def test_a_split_adjusted_history_is_a_mismatch():
    halved = json.loads(json.dumps(THREE))
    for candle in halved["candles"]:
        for field in ("open", "high", "low", "close"):
            candle[field] /= 2
    assert shadow_repeat(THREE, halved, at=SETTLED) == ["upstream", "shadow_hit_mismatch"]
    assert shadow_repeat(THREE, halved) == ["upstream", "shadow_hit_mismatch",
                                            "shadow_moving_over_50bp"]


def test_a_mismatching_series_is_never_stored_over_the_held_one():
    # On would have answered locally: there was no fetch to store.
    state = {"series": THREE}
    h = Harness(Cfg(mode="shadow"), responses=moving_series(state))
    set_time(h, *SETTLED)
    h.gw.pricehistory(BAR, "scan")
    state["series"] = revised(THREE, -1, close=101.5)
    h.gw.pricehistory(BAR, "scan")
    stored, _ = h.gw.bar_store.get(ms.bar_key(BAR), epoch=("2026-10-05", "settled"))
    assert json.loads(stored) == THREE


def test_a_series_mismatch_is_logged_once_per_series_not_once_per_request(caplog):
    state = {"series": THREE}
    h = Harness(Cfg(mode="shadow"), responses=moving_series(state))
    set_time(h, *SETTLED)
    qqq = {**BAR, "symbol": "QQQ"}
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(qqq, "scan")
    state["series"] = revised(THREE, -1, close=101.5)
    with caplog.at_level("WARNING", logger="market_store"):
        for _ in range(4):
            h.gw.pricehistory(BAR, "scan")
        h.gw.pricehistory(qqq, "scan")
    assert bar_outcomes(h).count("shadow_hit_mismatch") == 5     # every one counted
    warned = [r.getMessage() for r in caplog.records if r.name == "market_store"]
    assert len(warned) == 2                                      # one per series
    assert "SPY" in warned[0] and "QQQ" in warned[1]


def test_a_matching_series_logs_nothing(caplog):
    with caplog.at_level("WARNING", logger="market_store"):
        assert shadow_repeat(THREE, THREE, at=SETTLED) == ["upstream", "shadow_hit_match"]
    assert not [r for r in caplog.records if r.name == "market_store"]


def test_a_would_be_composed_bar_keeps_its_own_name():
    h = Harness(Cfg(mode="shadow", bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    assert "shadow_composed" in bar_outcomes(h)
    assert not [o for o in bar_outcomes(h) if o.startswith("shadow_hit")]


def test_no_daily_series_outcome_is_a_bare_shadow_hit_any_more():
    assert shadow_repeat(THREE, THREE) == ["upstream", "shadow_hit_match",
                                           "shadow_moving_same"]
    assert shadow_repeat(THREE, THREE, at=(4, 0)) == ["upstream", "shadow_hit_match"]


# ---- a store bug after the fetch never discards the answer -------------------

STORE_PUTS = {"chains": ("chain_store", "put"),
              "quotes": ("quote_store", "put_many"),
              "pricehistory": ("bar_store", "put")}


def any_answer(endpoint, params):
    if endpoint == "/chains":
        return chain()
    return bars_for(endpoint, params)


@pytest.mark.parametrize("mode", ["shadow", "on"])
@pytest.mark.parametrize("endpoint", list(ASKS))
def test_a_put_that_raises_costs_neither_the_answer_nor_a_second_call(
        mode, endpoint, monkeypatch):
    h = Harness(Cfg(mode=mode), responses=any_answer)
    store, method = STORE_PUTS[endpoint]
    monkeypatch.setattr(getattr(h.gw, store), method, boom)
    got = ASKS[endpoint](h)
    assert len(h.calls) == 1                                 # one upstream call
    assert got.data == any_answer(h.calls[0][0], h.calls[0][1])      # Schwab's answer
    assert got.kind == ("pass" if mode == "shadow" else "miss")
    assert h.gw.degrades == {endpoint: 1}                    # counted, once
    assert h.outcomes() == ["upstream"]


def test_a_comparison_that_raises_in_shadow_costs_no_second_call(monkeypatch):
    h = Harness(Cfg(mode="shadow"), responses=any_answer)
    h.gw.chains(P(), "a")
    h.gw.pricehistory(BAR, "a")
    monkeypatch.setattr(ms, "chain_difference", boom)
    monkeypatch.setattr(ms, "series_difference", boom)
    assert h.gw.chains(P(), "a").data == chain()
    assert h.gw.pricehistory(BAR, "a").data == series(FRI, MON)
    assert len(h.calls) == 4 and h.gw.degrades == {"chains": 1, "pricehistory": 1}


def test_a_bar_verdict_that_raises_in_shadow_costs_no_second_call(monkeypatch):
    h = Harness(Cfg(mode="shadow"), responses=any_answer)
    h.gw.quotes("SPY", "market_svc")
    monkeypatch.setattr(ms, "compare_today_bar", boom)
    got = h.gw.pricehistory(BAR, "a")
    assert got.kind == "pass" and got.data == series(FRI, MON)
    assert bar_calls(h) == 1 and h.gw.degrades == {"pricehistory": 1}


def test_a_quote_store_bug_after_the_partial_fetch_still_answers_in_full(monkeypatch):
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "a")
    monkeypatch.setattr(h.gw.quote_store, "put_many", boom)
    got = h.gw.quotes("DIA,SPY", "a")
    assert got.kind == "partial" and list(got.data) == ["DIA", "SPY"]
    assert len(h.calls) == 2 and h.gw.degrades == {"quotes": 1}


def test_a_store_bug_after_the_fetch_says_the_answer_was_kept(monkeypatch, caplog):
    h = Harness()
    monkeypatch.setattr(h.gw.chain_store, "put", boom)
    with caplog.at_level("WARNING", logger="market_store"):
        h.gw.chains(P(), "a")
    (rec,) = [r for r in caplog.records if r.name == "market_store"]
    assert "after the fetch" in rec.getMessage() and "chains" in rec.getMessage()
    assert rec.exc_info is not None and rec.exc_info[0] is RuntimeError


@pytest.mark.parametrize("method", ["put", "cut"])
def test_a_store_bug_on_a_wide_refetch_still_falls_back_to_a_plain_fetch(method, monkeypatch):
    # The wide chain cannot be cut without the store, and the caller asked for
    # the narrow window: that is fetched as asked.
    h = Harness()
    h.gw.chains(P(), "collector")
    h.clock += 50
    monkeypatch.setattr(h.gw.chain_store, method, boom)
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert [c[1]["toDate"] for c in h.calls] == ["2026-10-12", "2026-10-12", "2026-10-09"]
    assert got.kind == "pass" and got.data == chain()
    assert h.gw.degrades == {"chains": 1}


# ---- one log line per repeating difference -----------------------------------

def store_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "market_store" and r.levelname == "WARNING"]


def test_a_chain_shape_difference_is_logged_once_per_request_and_outcome(caplog):
    # The fake sends the full chain for the narrow window too, so every cut of
    # the collector's chain differs from "Schwab's" answer.
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "collector")
    with caplog.at_level("WARNING", logger="market_store"):
        for _ in range(3):
            h.gw.chains(P(to="2026-10-09"), "scan")
        assert h.outcomes().count("shadow_subset_mismatch") == 3     # all counted
        assert len(store_warnings(caplog)) == 1                      # one line
        h.clock += 60                    # past the serving limit: compare-only
        h.gw.chains(P(to="2026-10-08"), "scan")      # shadow_cmp_mismatch; stored
        h.gw.chains(P(to="2026-10-07"), "scan")      # a cut of that one: subset
    assert h.outcomes()[-3:] == ["shadow_cmp_mismatch", "upstream", "shadow_subset_mismatch"]
    assert len(store_warnings(caplog)) == 3          # each request its own line


def test_the_same_request_is_logged_again_for_a_different_outcome(caplog):
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "collector")
    with caplog.at_level("WARNING", logger="market_store"):
        h.gw.chains(P(to="2026-10-09"), "scan")      # shadow_subset_mismatch
        h.gw.chains(P(to="2026-10-09"), "scan")
        h.clock += 60
        h.gw.chains(P(to="2026-10-09"), "scan")      # shadow_cmp_mismatch
    assert h.outcomes()[-1] == "shadow_cmp_mismatch"
    assert len(store_warnings(caplog)) == 2


# ---- per-day state is dropped when the date changes --------------------------

TUE = dt.datetime(2026, 10, 6, 10, 0, tzinfo=CT)


def test_idle_locks_are_dropped_and_a_lock_in_use_never_is():
    locks = ms.KeyedLocks()
    with locks.holding("a"):
        pass
    with locks.holding("b"):
        assert locks.prune() == ["a"]                # "b" is held
        assert list(locks._locks) == ["b"]
    assert locks.prune() == ["b"] and locks._locks == {}
    assert locks.prune() == []


def test_a_lock_a_request_is_waiting_on_is_never_dropped():
    locks = ms.KeyedLocks()
    inside, peak = [], []
    entered, gate = threading.Event(), threading.Event()

    def work(wait):
        with locks.holding("k"):
            inside.append(1)
            peak.append(len(inside))
            entered.set()
            if wait:
                gate.wait(5)
            inside.pop()

    a = threading.Thread(target=work, args=(True,))
    a.start()
    assert entered.wait(5)
    b = threading.Thread(target=work, args=(False,))
    b.start()
    for _ in range(500):                             # until b is queueing
        if locks._locks["k"][1] == 2:
            break
        threading.Event().wait(0.01)
    assert locks._locks["k"][1] == 2
    assert locks.prune() == []                       # held by a, awaited by b
    c = threading.Thread(target=work, args=(False,))
    c.start()                                        # must queue on the SAME lock
    gate.set()
    for t in (a, b, c):
        t.join(5)
    assert peak == [1, 1, 1]                         # never two inside at once
    assert locks.prune() == ["k"]


def test_a_new_day_drops_yesterdays_locks_and_failure_records():
    state = {"fail": False}
    h = Harness(responses=lambda e, p: ms.UpstreamError(503, "unavailable")
                if state["fail"] else any_answer(e, p))
    for to in ("2026-10-09", "2026-10-10", "2026-10-12"):
        h.gw.chains(P(to=to), "a")
    h.gw.pricehistory(BAR, "a")
    state["fail"] = True
    with pytest.raises(ms.UpstreamError):
        h.gw.chains(P(to="2026-10-13"), "a")
    state["fail"] = False
    assert len(h.gw._locks._locks) == 5 and len(h.gw._failures) == 1
    # The same day: nothing is dropped.
    h.gw.chains(P(to="2026-10-14"), "a")
    assert len(h.gw._locks._locks) == 6 and len(h.gw._failures) == 1
    # The next day's first request drops what nobody is using.
    h.now_ct = TUE
    h.clock += 86400
    h.gw.chains(P(frm="2026-10-06", to="2026-10-13"), "a")
    assert list(h.gw._locks._locks) == [
        ("chains", ms.ChainKey.from_params(P(frm="2026-10-06", to="2026-10-13")))]
    assert h.gw._failures == {}


def test_a_daily_series_request_drops_yesterdays_state_too():
    h = Harness(responses=any_answer)
    h.gw.chains(P(), "a")
    h.now_ct = TUE
    h.clock += 86400
    h.gw.pricehistory(BAR, "a")
    assert list(h.gw._locks._locks) == [("bars", ms.bar_key(BAR))]


def test_a_fetch_in_flight_across_midnight_keeps_its_lock():
    slow = Slow(lambda e, p: chain(), hold={"SPY"})
    h = Harness(responses=slow)
    out = {}
    a = threading.Thread(target=lambda: out.update(a=h.gw.chains(P(), "a").kind))
    a.start()
    assert slow.started.wait(5)                      # SPY's fetch is in flight
    h.now_ct = TUE
    h.gw.chains({**P(), "symbol": "QQQ"}, "x")       # the new day's first request
    assert ("chains", ms.ChainKey.from_params(P())) in h.gw._locks._locks
    queued = threading.Event()
    real = h.gw._locks.holding

    def holding(key):
        queued.set()
        return real(key)

    h.gw._locks.holding = holding
    b = threading.Thread(target=lambda: out.update(b=h.gw.chains(P(), "b").kind))
    b.start()
    assert queued.wait(5)
    slow.gate.set()
    a.join(5)
    b.join(5)
    assert out == {"a": "miss", "b": "coalesced"}
    assert [c[1]["symbol"] for c in h.calls].count("SPY") == 1


def test_a_repeating_difference_is_logged_again_on_a_new_day(caplog):
    h = Harness(Cfg(mode="shadow"))
    with caplog.at_level("WARNING", logger="market_store"):
        for day in (h.now_ct, TUE):
            h.now_ct = day
            h.gw.chains(P(), "collector")
            h.gw.chains(P(to="2026-10-09"), "scan")
            h.gw.chains(P(to="2026-10-09"), "scan")
    assert h.outcomes().count("shadow_subset_mismatch") == 4
    assert len(store_warnings(caplog)) == 2


# ---- the bar-series mismatch log names what differed -------------------------

def series_warning(first, second, at):
    """The one WARNING a mismatching repeat of one series logs."""
    import logging
    state = {"series": first}
    h = Harness(Cfg(mode="shadow"), responses=moving_series(state))
    set_time(h, *at)
    seen = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler = Catch(level=logging.WARNING)
    logging.getLogger("market_store").addHandler(handler)
    try:
        h.gw.pricehistory(BAR, "scan")
        state["series"] = second
        h.gw.pricehistory(BAR, "scan")
        h.gw.pricehistory(BAR, "scan")
    finally:
        logging.getLogger("market_store").removeHandler(handler)
    assert bar_outcomes(h).count("shadow_hit_mismatch") == 2 and h.gw.degrades == {}
    (line,) = seen                                   # still one line per series
    return line


def test_the_mismatch_warning_says_what_differed():
    line = series_warning(THREE, revised(THREE, -1, volume=1200), SETTLED)
    assert "SPY" in line and line.endswith("last bar: volume 1000 vs 1200")
    line = series_warning(THREE, revised(THREE, 0, close=101.5), (10, 0))
    assert line.endswith("bar 0 (oldest): close 100.0 vs 101.5")
    line = series_warning(series(dt.date(2026, 10, 1), FRI), THREE, (10, 0))
    assert line.endswith("length 2 vs 3")


def test_the_warning_is_the_description_series_difference_gives():
    fresh = revised(THREE, 1, high=111.0)
    line = series_warning(THREE, fresh, SETTLED)
    assert line.endswith(ms.series_difference(THREE, fresh))


def test_a_fault_while_describing_the_difference_still_returns_schwabs_answer(monkeypatch):
    state = {"series": THREE}
    h = Harness(Cfg(mode="shadow"), responses=moving_series(state))
    set_time(h, *SETTLED)
    h.gw.pricehistory(BAR, "scan")
    state["series"] = revised(THREE, -1, close=101.5)
    monkeypatch.setattr(ms, "series_difference", boom)
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "pass" and got.data == state["series"]
    assert bar_calls(h) == 2 and h.gw.degrades == {"pricehistory": 1}


# ---- shadow: the moving bar (audit AC-100) ----------------------------------
# During the session a repeat's "shadow_hit_match" compares every bar EXCEPT
# today's, the only one a stored series can be stale on. The stored close and
# the fresh one get a verdict of their own, so the counts show how far off the
# answer ``on`` would have served actually was.

def _bars_closing_at(closes):
    """Each /pricehistory call returns Friday + today, today closing at the
    next value of ``closes``."""
    it = iter(closes)

    def respond(endpoint, params):
        if endpoint == "/quotes":
            return quotes_for(endpoint, params)
        out = series(FRI, MON)
        out["candles"][-1]["close"] = next(it)       # only TODAY's bar moves
        return out
    return respond


def test_shadow_gives_the_moving_bar_its_own_verdict():
    h = Harness(Cfg(mode="shadow"), responses=_bars_closing_at([501.50, 501.97]))
    h.gw.pricehistory(BAR, "scan")
    h.clock += 600
    h.gw.pricehistory(BAR, "scan")
    out = bar_outcomes(h)
    assert out == ["upstream", "upstream", "shadow_hit_match",
                   "shadow_moving_under_10bp"]


def test_an_unmoved_bar_is_recorded_as_the_same():
    h = Harness(Cfg(mode="shadow"), responses=_bars_closing_at([100.0, 100.0]))
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h)[-1] == "shadow_moving_same"


def test_a_large_move_on_the_stored_bar_is_recorded_as_one():
    h = Harness(Cfg(mode="shadow"), responses=_bars_closing_at([100.0, 101.0]))
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert bar_outcomes(h)[-2:] == ["shadow_hit_match", "shadow_moving_over_50bp"]


def test_the_settled_bar_gets_no_moving_verdict():
    h = Harness(Cfg(mode="shadow"), responses=_bars_closing_at([100.0, 100.0, 100.0]))
    set_time(h, 15, 30)
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert not [o for o in bar_outcomes(h) if o.startswith("shadow_moving_")]


def test_a_moved_bar_is_logged_with_both_closes_and_the_entrys_age(caplog):
    h = Harness(Cfg(mode="shadow"), responses=_bars_closing_at([501.50, 501.97, 502.40]))
    h.gw.pricehistory(BAR, "scan")
    h.clock += 600
    with caplog.at_level("WARNING", logger="market_store"):
        h.gw.pricehistory(BAR, "scan")
        h.clock += 60
        h.gw.pricehistory(BAR, "scan")
    lines = [r.getMessage() for r in caplog.records if "today's bar" in r.getMessage()]
    assert len(lines) == 1                      # once per series, not per request
    assert "501.5" in lines[0] and "501.97" in lines[0]
    assert "600" in lines[0]


def test_on_mode_records_no_moving_verdict():
    h = Harness(Cfg(), responses=_bars_closing_at([100.0, 101.0]))
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert not [o for o in bar_outcomes(h) if o.startswith("shadow_moving_")]


# --- AC-101: a bar fetched before the close is not the day's bar after it ------

def test_a_series_fetched_before_the_close_is_not_served_after_it():
    h = Harness(responses=bars_for)
    set_time(h, 14, 45)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 20 * 60
    set_time(h, 15, 5)
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"
    assert [c[0] for c in h.calls].count("/pricehistory") == 2


def test_between_the_close_and_the_settle_every_request_is_fetched():
    """The bar is still being finalised: nothing fetched in these minutes is
    handed to a second caller."""
    h = Harness(responses=bars_for)
    set_time(h, 15, 2)
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"
    h.clock += 60
    set_time(h, 15, 3)
    assert h.gw.pricehistory(BAR, "ideas").kind == "miss"
    assert [c[0] for c in h.calls].count("/pricehistory") == 2


def test_a_caller_can_demand_a_fresh_daily_series():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 60
    assert h.gw.pricehistory(BAR, "scan").kind == "hit"
    assert h.gw.pricehistory(BAR, "ideas", max_age=0).kind == "miss"
    assert [c[0] for c in h.calls].count("/pricehistory") == 2


def test_a_callers_age_limit_is_honoured_and_a_larger_one_never_loosens_the_stores():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 120
    assert h.gw.pricehistory(BAR, "x", max_age=300).kind == "hit"
    assert h.gw.pricehistory(BAR, "x", max_age=60).kind == "miss"
    h.clock += 1741                                   # past the session limit
    assert h.gw.pricehistory(BAR, "x", max_age=3600).kind == "miss"


def test_an_unusable_age_limit_is_no_limit():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 60
    for junk in ("soon", "nan", "-5", ""):
        assert h.gw.pricehistory(BAR, "x", max_age=junk).kind == "hit"


# ---- the shadow chain verdict says what differed (audit AC-103) ---------------

def test_a_stored_chain_with_another_day_count_is_a_mismatch_and_says_so(caplog):
    # Same dates and strikes, every key's day count one higher: the stored
    # answer is not the one Schwab sends now.
    state = {"later": False}

    def answer(endpoint, params):
        if not state["later"]:
            return chain()
        return chain(exps=tuple(f"{e.split(':')[0]}:{int(e.split(':')[1]) + 1}"
                                for e in EXPS))

    h = Harness(Cfg(mode="shadow"), responses=answer)
    h.gw.chains(P(), "a")
    h.clock += 10
    state["later"] = True
    with caplog.at_level("WARNING", logger="market_store"):
        h.gw.chains(P(), "a")
    assert h.outcomes() == ["upstream", "upstream", "shadow_hit_mismatch"]
    (line,) = store_warnings(caplog)
    assert "2026-10-05:0" in line and "2026-10-05:1" in line


def test_a_stored_chain_with_another_contract_count_is_a_mismatch():
    state = {"more": False}

    def answer(endpoint, params):
        out = chain()
        if state["more"]:
            out["numberOfContracts"] += 2
        return out

    h = Harness(Cfg(mode="shadow"), responses=answer)
    h.gw.chains(P(), "a")
    h.clock += 10
    state["more"] = True
    h.gw.chains(P(), "a")
    assert h.outcomes()[-1] == "shadow_hit_mismatch"


# ---- a failed week fetch is not the caller's answer (audit AR-100) ------------

def week_fails(state):
    """Schwab fails the collector's week once ``state['fail']`` is set, and
    answers every narrower window."""
    def respond(endpoint, params):
        if params["toDate"] == "2026-10-12":
            return state["fail"] or chain()
        days = {"2026-10-09": 3, "2026-10-08": 2, "2026-10-07": 2}[params["toDate"]]
        return chain(exps=EXPS[:days], spot=101.0)
    return respond


def test_a_failed_week_fetch_is_followed_by_the_request_as_asked():
    state = {"fail": None}
    h = Harness(responses=week_fails(state))
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["fail"] = ms.UpstreamError(503, "unavailable")
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert [c[1]["toDate"] for c in h.calls] == ["2026-10-12", "2026-10-12", "2026-10-09"]
    assert got.kind == "miss" and body(got)["underlyingPrice"] == 101.0
    assert h.records[1:] == [("chains", "scan", "wide_failed"),
                             ("chains", "scan", "upstream")]
    assert h.gw.degrades == {}
    # It was stored under its own request, so a repeat is answered locally.
    h.clock += 5
    assert h.gw.chains(P(to="2026-10-09"), "scan").kind == "hit"
    assert len(h.calls) == 3


def test_a_week_fetch_that_cannot_be_reached_is_followed_by_the_request_as_asked():
    state = {"fail": None}
    h = Harness(responses=week_fails(state))
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["fail"] = RuntimeError("read timed out")
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert got.kind == "miss" and len(h.calls) == 3 and h.gw.degrades == {}


def test_requests_waiting_on_one_failed_week_fetch_each_get_their_own_answer():
    state = {"fail": None}
    slow = Slow(week_fails(state))
    h = Harness(responses=slow)
    slow.gate.set()
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["fail"] = ms.UpstreamError(503, "unavailable")
    slow.gate.clear()
    slow.started.clear()
    answers = all_at_once(
        h, slow, lambda: h.gw.chains(P(to="2026-10-09"), "a"),
        [lambda: h.gw.chains(P(to="2026-10-08"), "b"),
         lambda: h.gw.chains(P(to="2026-10-07"), "c")])
    assert [type(a).__name__ for a in answers] == ["Served"] * 3
    asked = sorted(c[1]["toDate"] for c in h.calls[1:])
    # ONE failed week fetch, then each request once, as it was asked.
    assert asked == ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"]


@pytest.mark.parametrize("status", [401, 429])
def test_a_week_fetch_refused_for_a_reason_that_would_repeat_is_raised_at_once(status):
    # Not authorized, or rate limited: asking again says the same and, for a
    # rate limit, adds to the load.
    state = {"fail": None}
    h = Harness(responses=week_fails(state))
    h.gw.chains(P(), "collector")
    h.clock += 50
    state["fail"] = ms.UpstreamError(status, "no")
    with pytest.raises(ms.UpstreamError) as err:
        h.gw.chains(P(to="2026-10-09"), "scan")
    assert err.value.status_code == status and len(h.calls) == 2


# ---- the session limit is spread across series (audit PF-100) -----------------
#
# Every series fetched by one quarter-hour scan aged out together, 29 minutes
# later, so every SECOND scan refetched the whole watchlist and the scans in
# between refetched none of it.

SCAN_EVERY = 900
NAMES = [f"S{i:03d}" for i in range(120)]


def scan_fetches(spread, scans=9):
    """How many series each quarter-hour scan had to fetch."""
    h = Harness(Cfg(bars__session_spread=spread), responses=bars_for)
    h.now_ct = dt.datetime(2026, 10, 5, 8, 31, tzinfo=CT)
    counts, oldest = [], 0.0
    for _ in range(scans):
        before = bar_calls(h)
        for name in NAMES:
            got = h.gw.pricehistory({**BAR, "symbol": name}, "scan")
            if got.kind == "hit":
                oldest = max(oldest, got.age)
        counts.append(bar_calls(h) - before)
        h.clock += SCAN_EVERY
        h.now_ct += dt.timedelta(seconds=SCAN_EVERY)
    return counts, oldest


def test_without_the_spread_every_second_scan_refetches_everything():
    counts, _ = scan_fetches(False)
    assert counts == [120, 0, 120, 0, 120, 0, 120, 0, 120]


def test_with_the_spread_no_scan_after_the_first_refetches_everything_or_nothing():
    counts, _ = scan_fetches(True)
    assert counts[0] == 120
    assert all(30 <= n <= 90 for n in counts[1:]), counts
    # The same work in total, to within the first scan's shortened windows.
    assert sum(counts[1:]) <= 120 * 4 + 120


def test_the_spread_never_serves_a_series_older_than_the_limit():
    _, oldest = scan_fetches(True)
    assert 0 < oldest <= 1740


def test_a_series_keeps_its_own_window_from_run_to_run():
    # Python salts hash() per process; the window offset must not move with it.
    key = ms.bar_key(BAR)
    assert ms.session_slot(key, 1000.0, 1740.0) == ms.session_slot(key, 1000.0, 1740.0)
    import zlib
    offset = zlib.crc32(repr(key).encode()) % 1740
    assert ms.session_slot(key, 1740.0 - offset, 1740.0) == 1
    assert ms.session_slot(key, 1739.0 - offset, 1740.0) == 0


# ---- the store's own state, for /health (audit CQ-100) ------------------------

def test_the_gateway_reports_its_mode_per_store_and_its_fault_count():
    h = Harness(Cfg(mode="shadow", quotes__enabled=False))
    assert h.gw.state() == {
        "mode": "shadow",
        "stores": {"chains": "shadow", "quotes": "off", "bars": "shadow"},
        "faults": 0, "faults_by_area": {}}
    h.gw._degraded("chains")
    h.gw._degraded("chains", answered=True)
    h.gw._degraded("quotes")
    assert h.gw.state()["faults"] == 3
    assert h.gw.state()["faults_by_area"] == {"chains": 2, "quotes": 1}


def test_the_gateway_state_never_raises_on_unreadable_config():
    class Broken(Cfg):
        def mode(self):
            raise RuntimeError("unreadable")

    state = Harness(Broken()).gw.state()
    assert state["mode"] == "unknown"
    assert state["stores"] == {"chains": "off", "quotes": "off", "bars": "off"}
