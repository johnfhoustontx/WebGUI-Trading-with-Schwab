import order_executor


def test_successful_bucket_a_calls_track(monkeypatch):
    calls = {}
    def fake_post(url, json=None, timeout=None):
        calls["url"] = url; calls["json"] = json
        class R:
            def raise_for_status(self): pass
            def json(self): return {"status": "ok"}
        return R()
    monkeypatch.setattr(order_executor.requests, "post", fake_post)

    trade = {"bucket": "A", "instrument": "SPX", "structure": "iron_condor",
             "contracts": 1,
             "strikes": {"put_short": 4950, "put_long": 4925,
                         "call_short": 5050, "call_long": 5075}}
    order_executor._track_bucket_a(trade, "2026-05-30-A-093000")
    assert calls["url"].endswith("/track")
    body = calls["json"]
    assert body["trade_id"] == "2026-05-30-A-093000"
    assert body["symbol"] == "SPX"
    # Structure mapped to the proxy's strategy code, put legs -> short/long_strike.
    assert body["strategy"] == "IC"
    assert body["short_strike"] == 4950
    assert body["long_strike"] == 4925
    assert body["call_short"] == 5050
    assert body["call_long"] == 5075
    assert body["expiration"]  # 0-DTE default fills in today


def test_vertical_maps_short_long_strikes(monkeypatch):
    calls = {}
    def fake_post(url, json=None, timeout=None):
        calls["json"] = json
        class R:
            def raise_for_status(self): pass
            def json(self): return {"status": "ok"}
        return R()
    monkeypatch.setattr(order_executor.requests, "post", fake_post)

    trade = {"bucket": "A", "instrument": "SPX", "structure": "put_credit_spread",
             "contracts": 1, "strikes": {"short": 4950, "long": 4925}}
    order_executor._track_bucket_a(trade, "id-pcs")
    assert calls["json"]["strategy"] == "PCS"
    assert calls["json"]["short_strike"] == 4950
    assert calls["json"]["long_strike"] == 4925


def test_unmapped_structure_skips_track(monkeypatch):
    posted = {"called": False}
    def fake_post(*a, **k):
        posted["called"] = True
        class R:
            def raise_for_status(self): pass
            def json(self): return {"status": "ok"}
        return R()
    monkeypatch.setattr(order_executor.requests, "post", fake_post)

    trade = {"bucket": "A", "instrument": "SPX", "structure": "iron_butterfly",
             "contracts": 1, "strikes": {"short": 4950, "long": 4925}}
    order_executor._track_bucket_a(trade, "id-unknown")
    assert posted["called"] is False  # never POSTs an unresolvable strategy


def test_track_never_raises_on_post_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(order_executor.requests, "post", boom)
    trade = {"bucket": "A", "instrument": "SPX", "structure": "iron_condor",
             "contracts": 1, "strikes": {"put_short": 4950, "put_long": 4925}}
    # Must not raise.
    order_executor._track_bucket_a(trade, "id-fail")
