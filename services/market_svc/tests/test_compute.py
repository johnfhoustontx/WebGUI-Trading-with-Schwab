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
    mag = tiles["MAG7"]
    expected_avg = (-1.0 + 0.6 - 0.4 + 0.0 + 3.0 - 0.6 + 0.4) / 7   # +0.2857
    assert round(mag["change_pct"], 4) == round(expected_avg, 4)
    assert mag["avg_pct"] == mag["change_pct"]
    assert mag["breadth_text"] == "3/7 up"          # MSFT, META, TSLA > 0
    assert mag["basket"] is True
    assert mag["color_state"] == "risk_on_mild"     # +0.29% avg
    # each constituent is also its own tile in the frame
    assert tiles["NVDA"]["change_pct"] == -1.0


def test_mag7_no_data_when_members_absent():
    d = compute.build_dashboard({}, sector_pcr=None, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["MAG7"]["color_state"] == "no_data"


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


def test_read_sector_pcr():
    bus = Bus()  # fakeredis under pytest
    bus.cache_set("cache:sentiment:composite", {"live": {"sector_pcr": 0.97}})
    assert compute.read_sector_pcr(bus) == 0.97


def test_read_sector_pcr_missing_key_is_none():
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
