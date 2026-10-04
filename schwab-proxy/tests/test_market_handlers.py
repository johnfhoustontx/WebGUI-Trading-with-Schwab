"""The three market-data handlers are thin adapters over the gateway."""
import datetime as dt
import json
import pathlib
import sys

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402
import schwab_proxy  # noqa: E402
from test_market_gateway import CT, Cfg, FakeCal  # noqa: E402
from test_market_store_chains import chain  # noqa: E402


class Req:
    def __init__(self, caller=None):
        self.headers = {"x-caller": caller} if caller else {}


class FakeGateway:
    def __init__(self, served=None, error=None):
        self.served, self.error, self.seen = served, error, []
        self.degrades = {"chains": 2}

    def _answer(self, *args):
        self.seen.append(args)
        if self.error:
            raise self.error
        return self.served

    def chains(self, params, caller, max_age=None):
        return self._answer("chains", params, caller, max_age)

    def quotes(self, symbols, caller, max_age=None):
        return self._answer("quotes", symbols, caller, max_age)

    def pricehistory(self, params, caller, max_age=None):
        return self._answer("pricehistory", params, caller)


class FakeTokenMgr:
    """Stand-in for the TokenManager the proxy builds at startup. Under pytest
    ``schwab_proxy.token_mgr`` is None: nothing has started the app."""

    def __init__(self, api_request):
        self.api_request = api_request


@pytest.fixture(autouse=True)
def _no_callers_seen_yet(monkeypatch):
    """Each test starts with the caller names of a proxy that just started."""
    monkeypatch.setattr(schwab_proxy, "_CALLERS_SEEN", {"unknown"})


@pytest.fixture
def gw(monkeypatch):
    def install(**kw):
        g = FakeGateway(**kw)
        monkeypatch.setattr(schwab_proxy, "_GATEWAY", g)
        return g
    return install


def test_chains_passes_the_request_through_unchanged(gw):
    g = gw(served=ms.Served("pass", 0.0, data={"ok": 1}))
    out = schwab_proxy.get_option_chain(
        Req("options_svc"), symbol="SPY", contractType="ALL", range="ALL",
        fromDate="2026-10-05", toDate="2026-10-12", strikeCount=None, maxAge=20.0)
    kind, params, caller, max_age = g.seen[0]
    assert params == {"symbol": "SPY", "contractType": "ALL", "range": "ALL",
                      "fromDate": "2026-10-05", "toDate": "2026-10-12"}
    assert (caller, max_age) == ("options_svc", 20.0)
    assert json.loads(out.body) == {"ok": 1}
    assert out.headers["x-store"] == "pass" and out.headers["x-store-age"] == "0.0"


def test_a_stored_body_is_sent_as_is_with_its_age(gw):
    gw(served=ms.Served("subset", 12.34, body=b'{"a":1}'))
    out = schwab_proxy.get_option_chain(
        Req(), symbol="SPY", contractType="ALL", range="ALL",
        fromDate=None, toDate=None, strikeCount=None, maxAge=None)
    assert out.body == b'{"a":1}' and out.media_type == "application/json"
    assert out.headers["x-store"] == "subset" and out.headers["x-store-age"] == "12.3"


def test_a_missing_caller_header_is_unknown(gw):
    g = gw(served=ms.Served("pass", 0.0, data={}))
    schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)
    assert g.seen[0][2] == "unknown"


def test_single_quote_uses_the_same_store_as_the_batch(gw):
    g = gw(served=ms.Served("hit", 1.0, data={"SPY": {}}))
    schwab_proxy.get_quote(Req("x"), symbol="SPY", maxAge=None)
    assert g.seen[0][:2] == ("quotes", "SPY")


def test_pricehistory_sends_the_same_parameters_as_before(gw):
    g = gw(served=ms.Served("pass", 0.0, data={}))
    schwab_proxy.get_price_history(
        Req("x"), symbol="SPY", periodType="year", period=1,
        frequencyType="daily", frequency=1, needExtendedHoursData=False)
    assert g.seen[0][1] == {"symbol": "SPY", "periodType": "year", "period": 1,
                            "frequencyType": "daily", "frequency": 1,
                            "needExtendedHoursData": "false"}


def test_an_upstream_error_becomes_the_same_http_error_as_before(gw):
    gw(error=ms.UpstreamError(429, "slow down"))
    with pytest.raises(HTTPException) as err:
        schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)
    assert err.value.status_code == 429 and err.value.detail == "slow down"


def test_the_real_fetch_raises_upstream_error_on_a_non_200(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "token_mgr", FakeTokenMgr(
        lambda endpoint, params=None: {
            "status_code": 400, "data": None, "error": "bad"}))
    with pytest.raises(ms.UpstreamError) as err:
        schwab_proxy._upstream("/chains", {"symbol": "X"})
    assert (err.value.status_code, err.value.detail) == (400, "bad")


def test_stats_carry_the_local_breakdown_and_degrades(gw):
    gw(served=None)
    out = schwab_proxy.api_call_stats()
    assert {"today", "last_7_days", "last_30_days", "since"} <= set(out)
    assert set(out["store"]) == {"served_locally", "by_outcome", "rows"}
    assert out["store_degrades"] == {"chains": 2}


#############################################
# THE REAL FETCH
#############################################

def test_the_real_fetch_returns_schwabs_data_on_a_200(monkeypatch):
    seen = []

    def api_request(endpoint, params=None):
        seen.append((endpoint, params))
        return {"status_code": 200, "data": {"SPY": {}}, "error": None}

    monkeypatch.setattr(schwab_proxy, "token_mgr", FakeTokenMgr(api_request))
    assert schwab_proxy._upstream("/quotes", {"symbols": "SPY"}) == {"SPY": {}}
    assert seen == [("/quotes", {"symbols": "SPY"})]


def test_the_real_fetch_returns_an_empty_200_as_it_is(monkeypatch):
    # The handlers returned ``result["data"]`` whatever it was.
    monkeypatch.setattr(schwab_proxy, "token_mgr", FakeTokenMgr(
        lambda endpoint, params=None: {
            "status_code": 200, "data": None, "error": None}))
    assert schwab_proxy._upstream("/quotes", {"symbols": "SPY"}) is None


def test_a_token_failure_is_an_upstream_error_not_a_store_bug(monkeypatch):
    # ``api_request`` raises (it does not return a dict) when the token cannot
    # be made valid. The gateway reads any exception other than UpstreamError
    # as a bug in the store, counts a degrade and fetches a SECOND time.
    def api_request(endpoint, params=None):
        raise RuntimeError("Refresh token expired — re-auth required")

    monkeypatch.setattr(schwab_proxy, "token_mgr", FakeTokenMgr(api_request))
    with pytest.raises(ms.UpstreamError) as err:
        schwab_proxy._upstream("/chains", {"symbol": "X"})
    assert err.value.status_code == 500
    assert "Refresh token expired" in err.value.detail


def test_a_request_before_startup_is_an_upstream_error(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "token_mgr", None)
    with pytest.raises(ms.UpstreamError) as err:
        schwab_proxy._upstream("/chains", {"symbol": "X"})
    assert err.value.status_code == 500


#############################################
# THE RESPONSE BODY
#############################################

def test_a_passed_through_body_is_the_bytes_fastapi_sent_before(gw):
    # Before the store the handler returned the dict and FastAPI rendered it
    # with JSONResponse: compact, non-ASCII characters as UTF-8.
    data = {"description": "Société Générale — ADR", "nested": [1.5, None, True]}
    gw(served=ms.Served("pass", 0.0, data=data))
    out = schwab_proxy.get_quotes(Req(), symbols="SCGLY", maxAge=None)
    assert out.body == JSONResponse(content=data).body
    assert out.media_type == "application/json"


def test_an_empty_upstream_answer_is_sent_as_null(gw):
    gw(served=ms.Served("pass", 0.0, data=None))
    out = schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)
    assert out.body == b"null" == JSONResponse(content=None).body


def test_a_nan_is_refused_as_it_was_before(gw):
    # ``NaN`` is not JSON. FastAPI refused to render one (a 500); the adapter
    # must not start sending it.
    gw(served=ms.Served("pass", 0.0, data={"SPY": {"mark": float("nan")}}))
    with pytest.raises(ValueError):
        JSONResponse(content={"SPY": {"mark": float("nan")}})
    with pytest.raises(ValueError):
        schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)


def test_a_long_caller_name_is_cut_to_the_counter_column(gw):
    g = gw(served=ms.Served("pass", 0.0, data={}))
    schwab_proxy.get_quotes(Req("x" * 200), symbols="SPY", maxAge=None)
    assert g.seen[0][2] == "x" * 40


#############################################
# THE CALLER NAME IS A BOUNDED COUNTER KEY
#############################################
# Anything can send X-Caller, and every distinct value is a row in the counts.

def test_a_caller_name_is_cut_to_plain_characters():
    # Header bytes reach the app decoded as latin-1.
    assert schwab_proxy._caller(Req("night job/" + chr(0xE9))) == "night_job__"
    assert schwab_proxy._caller(Req("label-journal_v1.2")) == "label-journal_v1.2"
    assert schwab_proxy._caller(Req("  dev.options_svc  ")) == "dev.options_svc"


def test_a_blank_caller_name_is_unknown():
    for blank in ("   ", chr(9)):
        assert schwab_proxy._caller(Req(blank)) == "unknown"


def test_the_number_of_caller_names_is_bounded():
    got = [schwab_proxy._caller(Req(f"visitor{i}")) for i in range(300)]
    assert schwab_proxy.MAX_CALLER_NAMES == 64
    assert len(set(got)) <= 65
    # The first names keep their own label; everything after them is "other".
    assert got[:63] == [f"visitor{i}" for i in range(63)]
    assert set(got[63:]) == {"other"}
    assert len(schwab_proxy._CALLERS_SEEN) == 64


def test_a_name_seen_before_the_bound_keeps_its_label_after_it():
    assert schwab_proxy._caller(Req("options_svc")) == "options_svc"
    for i in range(300):
        schwab_proxy._caller(Req(f"visitor{i}"))
    assert schwab_proxy._caller(Req("brand_new")) == "other"
    assert schwab_proxy._caller(Req("options_svc")) == "options_svc"
    assert schwab_proxy._caller(Req("visitor5")) == "visitor5"
    assert schwab_proxy._caller(Req()) == "unknown"


def test_a_request_with_no_name_never_uses_up_the_bound():
    for _ in range(300):
        assert schwab_proxy._caller(Req()) == "unknown"
    assert schwab_proxy._CALLERS_SEEN == {"unknown"}


def test_the_bound_reaches_the_counter_over_http(real):
    _, records = real
    client = TestClient(schwab_proxy.app)
    for i in range(80):
        client.get("/quotes?symbols=SPY", headers={"X-Caller": f"visitor {i}"})
    callers = {r[1] for r in records}
    assert len(callers) == 64 and "other" in callers and "visitor_0" in callers


#############################################
# THE REAL GATEWAY BEHIND THE HANDLERS
#############################################
# The tests above use a fake gateway, so they cannot see a handler and the
# gateway disagreeing about a signature. These drive the real one.

QUOTES = {"SPY": {"quote": {"lastPrice": 500.0}},
          "QQQ": {"quote": {"lastPrice": 430.0}}}


class Upstream:
    """A fake Schwab: counts calls and answers by endpoint."""

    def __init__(self):
        self.calls = []

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == "/chains":
            return chain()
        if endpoint == "/quotes":
            return {s: QUOTES[s] for s in params["symbols"].split(",")}
        return {"symbol": params["symbol"], "empty": False,
                "candles": [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                             "volume": 10, "datetime": 1759381200000}]}


@pytest.fixture
def real(monkeypatch):
    """The real gateway in mode ``on`` over a fake Schwab, installed behind the
    handlers. Returns ``(upstream, records)``."""
    up, records = Upstream(), []
    FakeCal.state = "REGULAR"
    monkeypatch.setattr(schwab_proxy, "_GATEWAY", ms.Gateway(
        fetch=up, config=Cfg("on"), calendar=FakeCal,
        record=lambda *a: records.append(a), clock=lambda: 1000.0,
        # A Sunday: the daily series is settled, so a repeat is a plain hit.
        now_ct=lambda: dt.datetime(2026, 10, 4, 10, 0, tzinfo=CT)))
    return up, records


def _chain_request(caller="options_svc"):
    return schwab_proxy.get_option_chain(
        Req(caller), symbol="SPY", contractType="ALL", range="ALL",
        fromDate="2026-10-05", toDate="2026-10-12", strikeCount=None, maxAge=None)


def test_a_repeat_chain_request_is_answered_from_the_store(real):
    up, records = real
    first, second = _chain_request(), _chain_request("webgui")
    assert first.headers["x-store"] == "miss"
    assert second.headers["x-store"] == "hit"
    assert json.loads(first.body) == json.loads(second.body) == chain()
    assert up.calls == [("/chains", {"symbol": "SPY", "contractType": "ALL",
                                     "range": "ALL", "fromDate": "2026-10-05",
                                     "toDate": "2026-10-12"})]
    assert records == [("chains", "options_svc", "upstream"),
                       ("chains", "webgui", "hit")]


def test_a_repeat_quotes_request_is_answered_from_the_store(real):
    up, records = real
    first = schwab_proxy.get_quotes(Req("market_svc"), symbols="SPY,QQQ", maxAge=None)
    second = schwab_proxy.get_quotes(Req("market_svc"), symbols="SPY,QQQ", maxAge=None)
    assert first.headers["x-store"] == "miss"
    assert second.headers["x-store"] == "hit"
    assert json.loads(first.body) == json.loads(second.body) == QUOTES
    assert up.calls == [("/quotes", {"symbols": "SPY,QQQ", "fields": "quote"})]
    assert [r[2] for r in records] == ["upstream", "hit"]


def test_a_single_quote_is_answered_from_what_the_batch_fetched(real):
    up, _ = real
    schwab_proxy.get_quotes(Req(), symbols="SPY,QQQ", maxAge=None)
    out = schwab_proxy.get_quote(Req(), symbol="QQQ", maxAge=None)
    assert out.headers["x-store"] == "hit"
    assert json.loads(out.body) == {"QQQ": QUOTES["QQQ"]}
    assert len(up.calls) == 1


def test_an_age_limit_of_zero_always_asks_schwab(real):
    up, _ = real
    schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)
    out = schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=0.0)
    assert out.headers["x-store"] == "miss" and len(up.calls) == 2


def _bars_request(frequency_type="daily"):
    return schwab_proxy.get_price_history(
        Req("trade_svc"), symbol="SPY", periodType="year", period=1,
        frequencyType=frequency_type, frequency=1, needExtendedHoursData=False)


def test_a_repeat_daily_series_is_answered_from_the_store(real):
    up, _ = real
    first, second = _bars_request(), _bars_request()
    assert (first.headers["x-store"], second.headers["x-store"]) == ("miss", "hit")
    assert json.loads(first.body) == json.loads(second.body)
    assert len(up.calls) == 1


def test_an_intraday_series_is_never_stored(real):
    up, _ = real
    first, second = _bars_request("minute"), _bars_request("minute")
    assert (first.headers["x-store"], second.headers["x-store"]) == ("pass", "pass")
    assert len(up.calls) == 2


#############################################
# END TO END, OVER HTTP
#############################################
# TestClient(app) outside a ``with`` block does not run the app's startup, so
# no TokenManager is built, no thread starts and nothing needs credentials.

def test_chains_over_http(real):
    up, records = real
    client = TestClient(schwab_proxy.app)
    url = "/chains?symbol=SPY&fromDate=2026-10-05&toDate=2026-10-12&maxAge=30"
    first = client.get(url, headers={"X-Caller": "options_svc"})
    second = client.get(url, headers={"X-Caller": "options_svc"})
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "application/json"
    assert first.json() == second.json() == chain()
    assert (first.headers["x-store"], second.headers["x-store"]) == ("miss", "hit")
    assert second.headers["x-store-age"] == "0.0"
    assert len(up.calls) == 1
    assert records == [("chains", "options_svc", "upstream"),
                       ("chains", "options_svc", "hit")]


def test_quotes_over_http(real):
    up, records = real
    client = TestClient(schwab_proxy.app)
    first = client.get("/quotes?symbols=SPY,QQQ")
    second = client.get("/quotes?symbols=SPY,QQQ")
    single = client.get("/quote?symbol=SPY")
    assert first.status_code == second.status_code == single.status_code == 200
    assert first.json() == second.json() == QUOTES
    assert single.json() == {"SPY": QUOTES["SPY"]}
    assert [r.headers["x-store"] for r in (first, second, single)] == [
        "miss", "hit", "hit"]
    assert len(up.calls) == 1
    assert {r[1] for r in records} == {"unknown"}


def test_an_upstream_error_over_http_keeps_its_status_and_detail(gw):
    gw(error=ms.UpstreamError(429, "slow down"))
    resp = TestClient(schwab_proxy.app).get("/quotes?symbols=SPY")
    assert resp.status_code == 429 and resp.json() == {"detail": "slow down"}


#############################################
# AN AGE-LIMIT HINT NEVER FAILS A REQUEST
#############################################
# ``maxAge`` is optional advice. A value that is not a usable number falls back
# to the configured limit (quotes 5 s, chains 45 s here); it is never a 422.

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def timed(monkeypatch):
    """The real gateway in mode ``on`` with a clock the test moves. Returns
    ``(client, upstream, clock)``."""
    up, clock = Upstream(), Clock()
    FakeCal.state = "REGULAR"
    monkeypatch.setattr(schwab_proxy, "_GATEWAY", ms.Gateway(
        fetch=up, config=Cfg("on"), calendar=FakeCal, record=lambda *a: None,
        clock=clock, now_ct=lambda: dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)))
    return TestClient(schwab_proxy.app), up, clock


def _kind(resp):
    assert resp.status_code == 200, resp.text
    return resp.headers["x-store"]


BAD_AGES = ["abc", "", "nan", "inf", "-inf", "-1", "1,5", "5s"]


@pytest.mark.parametrize("bad", BAD_AGES)
def test_a_bad_quotes_age_limit_uses_the_configured_one(timed, bad):
    client, up, clock = timed
    url = "/quotes?symbols=SPY&maxAge=" + bad
    assert _kind(client.get("/quotes?symbols=SPY")) == "miss"
    clock.now += 3                      # inside the configured 5 seconds
    assert _kind(client.get(url)) == "hit"
    clock.now += 3                      # past it
    assert _kind(client.get(url)) == "miss"
    assert len(up.calls) == 2


@pytest.mark.parametrize("bad", BAD_AGES)
def test_a_bad_single_quote_age_limit_uses_the_configured_one(timed, bad):
    client, up, clock = timed
    url = "/quote?symbol=SPY&maxAge=" + bad
    assert _kind(client.get("/quote?symbol=SPY")) == "miss"
    clock.now += 3
    assert _kind(client.get(url)) == "hit"
    clock.now += 3
    assert _kind(client.get(url)) == "miss"


@pytest.mark.parametrize("bad", BAD_AGES)
def test_a_bad_chains_age_limit_uses_the_configured_one(timed, bad):
    client, up, clock = timed
    url = "/chains?symbol=SPY&fromDate=2026-10-05&toDate=2026-10-12&maxAge=" + bad
    assert _kind(client.get(url)) == "miss"
    clock.now += 44                     # inside the configured 45 seconds
    assert _kind(client.get(url)) == "hit"
    clock.now += 2                      # past it
    assert _kind(client.get(url)) == "miss"
    assert len(up.calls) == 2


def test_a_huge_age_limit_behaves_as_one_hour(timed):
    client, up, clock = timed
    url = "/quotes?symbols=SPY&maxAge=1e12"
    assert _kind(client.get(url)) == "miss"
    clock.now += 3599
    assert _kind(client.get(url)) == "hit"
    clock.now += 2
    assert _kind(client.get(url)) == "miss"


def test_an_age_limit_of_zero_always_fetches_over_http(timed):
    client, up, _ = timed
    for _ in range(3):
        assert _kind(client.get("/quotes?symbols=SPY&maxAge=0")) == "miss"
        assert _kind(client.get(
            "/chains?symbol=SPY&fromDate=2026-10-05&toDate=2026-10-12&maxAge=0")) == "miss"
    assert len(up.calls) == 6


def test_a_usable_age_limit_is_honoured_over_http(timed):
    client, up, clock = timed
    url = "/chains?symbol=SPY&fromDate=2026-10-05&toDate=2026-10-12&maxAge=210"
    assert _kind(client.get(url)) == "miss"
    clock.now += 200                    # far past the configured 45 seconds
    assert _kind(client.get(url)) == "hit"
    clock.now += 11
    assert _kind(client.get(url)) == "miss"
    quote = "/quotes?symbols=SPY&maxAge=210"
    assert _kind(client.get(quote)) == "miss"
    clock.now += 200
    assert _kind(client.get(quote)) == "hit"


def test_the_module_gateway_is_wired_to_the_real_config_and_calendar(monkeypatch):
    # The gateway the proxy actually runs: the shipped config, the real
    # calendar and the real counter, with only Schwab replaced. Whatever mode
    # the config ships in, a first request is one upstream call and Schwab's
    # answer comes back unchanged.
    up = Upstream()
    monkeypatch.setattr(schwab_proxy, "token_mgr", FakeTokenMgr(
        lambda endpoint, params=None: {
            "status_code": 200, "data": up(endpoint, params), "error": None}))
    assert isinstance(schwab_proxy._GATEWAY, ms.Gateway)
    before = dict(schwab_proxy._GATEWAY.degrades)
    client = TestClient(schwab_proxy.app)
    for url, expected in (
            ("/chains?symbol=ZZTEST&fromDate=2026-10-05&toDate=2026-10-12", chain()),
            ("/quotes?symbols=SPY,QQQ", QUOTES),
            ("/pricehistory?symbol=ZZTEST", up("/pricehistory", {"symbol": "ZZTEST"}))):
        calls = len(up.calls)
        resp = client.get(url, headers={"X-Caller": "test"})
        assert resp.status_code == 200 and resp.json() == expected
        assert resp.headers["x-store"] in ("pass", "miss")
        assert len(up.calls) == calls + 1
    # A store bug would have been swallowed and counted here.
    assert schwab_proxy._GATEWAY.degrades == before


def test_a_dead_token_is_one_call_and_a_500_through_the_module_gateway(monkeypatch):
    calls = []

    def api_request(endpoint, params=None):
        calls.append(endpoint)
        raise RuntimeError("Refresh token expired — re-auth required")

    monkeypatch.setattr(schwab_proxy, "token_mgr", FakeTokenMgr(api_request))
    before = dict(schwab_proxy._GATEWAY.degrades)
    client = TestClient(schwab_proxy.app)
    for url, endpoint in (
            ("/chains?symbol=ZZDEAD&fromDate=2026-10-05&toDate=2026-10-12", "/chains"),
            ("/quotes?symbols=ZZDEAD", "/quotes"),
            ("/pricehistory?symbol=ZZDEAD", "/pricehistory")):
        del calls[:]
        resp = client.get(url)
        assert resp.status_code == 500
        assert "Refresh token expired" in resp.json()["detail"]
        assert calls == [endpoint]
    assert schwab_proxy._GATEWAY.degrades == before


def test_pricehistory_passes_the_callers_age_limit_to_the_gateway(monkeypatch):
    """AC-101: ``/pricehistory`` took no age override at all."""
    import schwab_proxy
    from fastapi.testclient import TestClient
    seen = {}

    class _G:
        def pricehistory(self, params, caller, max_age=None):
            seen["max_age"] = max_age
            seen["params"] = dict(params)
            import market_store
            return market_store.Served("pass", 0.0, data={"candles": []})

    monkeypatch.setattr(schwab_proxy, "_GATEWAY", _G())
    r = TestClient(schwab_proxy.app).get("/pricehistory?symbol=SPY&maxAge=0")
    assert r.status_code == 200
    assert seen["max_age"] == "0"
    assert "maxAge" not in seen["params"]            # never forwarded to Schwab
