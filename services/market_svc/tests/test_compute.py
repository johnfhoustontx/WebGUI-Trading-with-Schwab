from shared.bus import Bus

from services.market_svc import compute


def _raw():
    return {
        "$VIX": {"assetMainType": "INDEX",
                 "quote": {"lastPrice": 16.13, "netChange": 0.56, "netPercentChange": 3.6}},
        "$SPX": {"assetMainType": "INDEX",
                 "quote": {"lastPrice": 7503.85, "netChange": -33.6, "netPercentChange": -0.44}},
        "/ESU26": {"assetMainType": "FUTURE",
                   "quote": {"lastPrice": 7560.75, "netChange": 9.5,
                             "futurePercentChange": 0.126, "closePrice": 7551.25}},
        "$ADVN": {"assetMainType": "EQUITY",
                  "quote": {"lastPrice": 1160.0, "netPercentChange": 0.0, "closePrice": 0.0}},
        "$DECN": {"assetMainType": "EQUITY",
                  "quote": {"lastPrice": 1625.0, "netPercentChange": 0.0, "closePrice": 0.0}},
        "HYG": {"assetMainType": "EQUITY", "quote": {"lastPrice": 81.0, "netPercentChange": 0.5}},
        "LQD": {"assetMainType": "EQUITY", "quote": {"lastPrice": 110.0, "netPercentChange": 0.2}},
        "UUP": {"assetMainType": "EQUITY", "quote": {"lastPrice": 28.4, "netPercentChange": 0.26}},
        "errors": {"invalidSymbols": ["NOPE"]},
    }


def _mag_raw():
    px = {"NVDA": -1.0, "MSFT": 0.6, "GOOGL": -0.4, "AMZN": 0.0,
          "META": 3.0, "AAPL": -0.6, "TSLA": 0.4}
    return {s: {"assetMainType": "EQUITY", "quote": {"lastPrice": 100.0, "netPercentChange": p}}
            for s, p in px.items()}


def test_mag7_basket_avg_and_breadth():
    d = compute.build_dashboard(_mag_raw(), sector_pcr=None, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    mag = tiles["BIG10"]
    expected_avg = (-1.0 + 0.6 - 0.4 + 0.0 + 3.0 - 0.6 + 0.4) / 7   # +0.2857
    assert round(mag["change_pct"], 4) == round(expected_avg, 4)
    assert mag["avg_pct"] == mag["change_pct"]
    assert mag["breadth_text"] == "3/7 up"          # MSFT, META, TSLA > 0
    assert mag["basket"] is True
    assert mag["color_state"] == "risk_on_mild"     # +0.29% avg
    # each constituent is also its own tile in the frame
    assert tiles["NVDA"]["change_pct"] == -1.0


def test_mag7_basket_aggregates_all_ten_members():
    # All 10 present -> avg + breadth span 10 (the 7 + AVGO/PLTR/AMD).
    px = {"NVDA": 1.0, "MSFT": 1.0, "GOOGL": 1.0, "AMZN": 1.0, "META": 1.0,
          "AAPL": 1.0, "TSLA": 1.0, "AVGO": 1.0, "PLTR": -3.0, "AMD": -1.0}
    raw = {s: {"assetMainType": "EQUITY", "quote": {"lastPrice": 100.0, "netPercentChange": p}}
           for s, p in px.items()}
    d = compute.build_dashboard(raw, sector_pcr=None, proxy_up=True)
    mag = {t["display"]: t for c in d["categories"] for t in c["tiles"]}["BIG10"]
    assert mag["breadth_text"] == "8/10 up"          # 8 of the 10 are green
    assert round(mag["avg_pct"], 4) == round(sum(px.values()) / 10, 4)


def test_mag7_no_data_when_members_absent():
    d = compute.build_dashboard({}, sector_pcr=None, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["BIG10"]["color_state"] == "no_data"


def test_build_dashboard_shapes_categories_in_order():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True)
    cats = [c["category"] for c in d["categories"]]
    assert cats[0] == "Volatility"          # frame order preserved
    assert d["proxy_up"] is True
    assert "errors" not in {c["category"] for c in d["categories"]}


def test_vix_tile_is_risk_off_and_spx_risk_off_down():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["VIX"]["color_state"] == "risk_off_strong"   # +3.6% inverted
    assert tiles["SPX"]["color_state"] == "risk_off_mild"     # -0.44% normal
    assert tiles["/ES[U26]"]["color_state"] == "risk_on_mild" # +0.126% future


def test_spread_tile_computed():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    # $ADVN-$DECN net-breadth spread is colored by SIGN (a count, not a %).
    assert tiles["$ADVN-$DECN"]["last"] == -465.0            # 1160 - 1625
    assert tiles["$ADVN-$DECN"]["color_state"] == "risk_off_mild"
    # HYG-LQD was intentionally removed (redundant with the individual HYG/LQD tiles).
    assert "HYG-LQD" not in tiles


def test_putcall_tile_from_sentiment_pcr_inverted():
    d = compute.build_dashboard(_raw(), sector_pcr=1.10, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    pc = tiles["Put/Call"]
    assert round(pc["last"], 2) == 1.10
    assert pc["color_state"] == "risk_off_mild"   # pcr>1 = more puts = risk-off


def test_net_prem_tile_call_heavy_is_risk_on():
    agg = {"skew_pct": 49.0, "net_m": 2983.3, "symbols": 44}
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True, net_prem=agg)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    np_ = tiles["Net Prem"]
    assert np_["net_prem"] is True
    assert np_["skew_pct"] == 49.0 and np_["net_m"] == 2983.3 and np_["symbols"] == 44
    assert np_["color_state"] == "risk_on_mild"     # call-money dominant = green


def test_net_prem_tile_put_heavy_is_risk_off():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True,
                                net_prem={"skew_pct": -22.0, "net_m": -540.0, "symbols": 40})
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["Net Prem"]["color_state"] == "risk_off_mild"


def test_net_prem_tile_no_data_when_missing():
    # None aggregate, or skew_pct None (no premium yet) -> no_data tile.
    for agg in (None, {"skew_pct": None, "net_m": 0.0, "symbols": 0}):
        d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True, net_prem=agg)
        tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
        assert tiles["Net Prem"]["color_state"] == "no_data"


def test_net_prem_tile_shares_options_sentiment_category():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True,
                                net_prem={"skew_pct": 10.0, "net_m": 100.0, "symbols": 44})
    cat = next(c for c in d["categories"] if c["category"] == "Options Sentiment")
    displays = [t["display"] for t in cat["tiles"]]
    assert "Put/Call" in displays and "Net Prem" in displays


def test_read_net_prem():
    bus = Bus()
    compute.reset_net_prem_cache()
    bus.cache_set("cache:options:matrix",
                  {"rows": [], "premium": {"skew_pct": 49.3, "net_m": 2983.3, "symbols": 44}})
    assert compute.read_net_prem(bus) == {"skew_pct": 49.3, "net_m": 2983.3, "symbols": 44}


def test_read_net_prem_missing_is_none():
    bus = Bus()
    compute.reset_net_prem_cache()
    assert compute.read_net_prem(bus) is None                 # no matrix key
    bus.cache_set("cache:options:matrix", {"rows": []})       # no premium block
    compute.reset_net_prem_cache()
    assert compute.read_net_prem(bus) is None


def test_symbol_premium_skew():
    assert compute.symbol_premium_skew(600_000.0, 240_000.0) == 42.9   # (6-2.4)/8.4
    assert compute.symbol_premium_skew(1.0, 3.0) == -50.0              # put-heavy
    assert compute.symbol_premium_skew(0.0, 0.0) is None               # no premium
    assert compute.symbol_premium_skew(None, None) is None


def test_per_symbol_prem_subline_on_flagged_tiles():
    sp = {"$SPX": (600_000_000.0, 240_000_000.0), "SPY": (2.0e8, 2.0e8),
          "DIA": (None, None)}
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True, symbol_prem=sp)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["SPX"]["prem_skew_pct"] == 42.9         # call-heavy
    assert tiles["SPY"]["prem_skew_pct"] == 0.0          # even (both 200M)
    assert tiles["DIA"]["prem_skew_pct"] is None         # no premium collected
    # a NON-flagged tile carries no prem key at all
    assert "prem_skew_pct" not in tiles["RSP"]


def test_mag7_basket_prem_is_dollar_weighted_net_of_members():
    # Members: big call-heavy NVDA + small put-heavy MSFT -> net still call-heavy.
    sp = {"NVDA": (900_000_000.0, 100_000_000.0), "MSFT": (1_000.0, 9_000.0)}
    d = compute.build_dashboard(_mag_raw(), sector_pcr=None, proxy_up=True, symbol_prem=sp)
    mag7 = next(t for c in d["categories"] for t in c["tiles"] if t["display"] == "BIG10")
    # Σcall=900,001,000 Σput=100,009,000 -> skew ~ +80%
    assert mag7["prem_skew_pct"] is not None and mag7["prem_skew_pct"] > 70


def test_read_symbol_premiums():
    bus = Bus()
    compute.reset_symbol_premiums_cache()
    bus.cache_set("cache:options:matrix", {"rows": [
        {"symbol": "$SPX", "call_prem": 600.0, "put_prem": 240.0},
        {"symbol": "SPY", "call_prem": 200.0, "put_prem": 100.0}]})
    m = compute.read_symbol_premiums(bus)
    assert m["$SPX"] == (600.0, 240.0) and m["SPY"] == (200.0, 100.0)


def test_read_symbol_premiums_missing_is_empty():
    bus = Bus()
    compute.reset_symbol_premiums_cache()
    assert compute.read_symbol_premiums(bus) == {}


def test_read_sector_pcr():
    bus = Bus()  # fakeredis under pytest
    bus.cache_set("cache:sentiment:composite", {"live": {"sector_pcr": 0.97}})
    assert compute.read_sector_pcr(bus) == 0.97


def test_read_sector_pcr_version_gated(monkeypatch):
    """read_sector_pcr runs every ~2s poll but the composite changes every ~120s.
    It deserializes the full composite payload ONLY when the version changes;
    otherwise it serves the memoized float off a cheap :ver probe."""
    compute.reset_pcr_cache()
    bus = Bus()
    bus.cache_set("cache:sentiment:composite", {"live": {"sector_pcr": 0.97}})
    deserializes = {"n": 0}
    real_get = bus.cache_get

    def counting_get(key):
        if key == compute.CACHE_SENTIMENT:
            deserializes["n"] += 1
        return real_get(key)

    monkeypatch.setattr(bus, "cache_get", counting_get)
    assert compute.read_sector_pcr(bus) == 0.97
    assert compute.read_sector_pcr(bus) == 0.97   # unchanged version -> memoized
    assert deserializes["n"] == 1                  # payload deserialized once
    bus.cache_set("cache:sentiment:composite", {"live": {"sector_pcr": 0.80}})
    assert compute.read_sector_pcr(bus) == 0.80    # version bumped -> re-read
    assert deserializes["n"] == 2


def test_read_sector_pcr_missing_key_is_none():
    compute.reset_pcr_cache()
    bus = Bus()  # no cache:sentiment:composite seeded
    assert compute.read_sector_pcr(bus) is None


def test_read_sector_pcr_missing_or_empty_value_is_none():
    for val in (None, ""):
        bus = Bus()
        bus.cache_set("cache:sentiment:composite", {"live": {"sector_pcr": val}})
        assert compute.read_sector_pcr(bus) is None
    # live present but sector_pcr key absent entirely
    bus = Bus()
    bus.cache_set("cache:sentiment:composite", {"live": {}})
    assert compute.read_sector_pcr(bus) is None


def test_missing_symbol_is_no_data_not_a_crash():
    raw = _raw()
    del raw["$SPX"]
    d = compute.build_dashboard(raw, sector_pcr=None, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["SPX"]["color_state"] == "no_data"
    assert tiles["Put/Call"]["color_state"] == "no_data"  # pcr None
