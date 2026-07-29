from pages import market


def test_bg_class_maps_every_state():
    for state in ("risk_on_strong", "risk_on_mild", "flat",
                  "risk_off_mild", "risk_off_strong", "no_data"):
        cls = market.bg_class(state)
        assert isinstance(cls, str) and cls  # non-empty fixed class
    # green vs red are distinct
    assert market.bg_class("risk_on_strong") != market.bg_class("risk_off_strong")
    # unknown → neutral fallback
    assert market.bg_class("bogus") == market.bg_class("no_data")


def test_tile_text_formats_last_and_change():
    t = {"display": "VIX", "last": 16.13, "change": None, "change_pct": 3.6,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "16.13"
    assert "3.6" in txt["change"] and "%" in txt["change"]


def test_tile_text_negative_change_signs():
    t = {"display": "SPX", "last": 7503.85, "change": -1.5, "change_pct": -0.8,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "7503.85"
    # negatives: no leading '+', explicit '-' from the float formatting
    assert "+" not in txt["change"]
    assert "-1.50" in txt["change"]
    assert "-0.80%" in txt["change"]


def test_tile_text_value_only_hides_change():
    t = {"display": "$TICK", "last": 300.0, "change": None, "change_pct": None,
         "value_only": True}
    txt = market.tile_text(t)
    assert txt["last"] == "300"
    assert txt["change"] == ""      # no change line for value-only


def test_tile_text_no_data():
    t = {"display": "SPX", "last": None, "change_pct": None, "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "—"


def test_tile_text_net_prem_call_and_put_heavy():
    call = {"display": "Net Prem", "net_prem": True, "skew_pct": 49.0, "net_m": 2983.3}
    txt = market.tile_text(call)
    assert txt["last"] == "Call 49%"
    assert txt["change"] == "+$2.98B"       # net-$ subline (>= $1000M -> billions)
    put = {"display": "Net Prem", "net_prem": True, "skew_pct": -22.0, "net_m": -540.0}
    txt2 = market.tile_text(put)
    assert txt2["last"] == "Put 22%"
    assert txt2["change"] == "-$540M"


def test_tile_text_net_prem_even_and_missing():
    assert market.tile_text({"net_prem": True, "skew_pct": 0.4, "net_m": 5.0})["last"] == "Even"
    assert market.tile_text({"net_prem": True, "skew_pct": None})["last"] == "—"


def test_prem_line_per_symbol_subline():
    # prem-flagged tile -> a "Call/Put x%" subline; missing data -> "—".
    assert market.prem_line({"prem_skew_pct": 42.9}) == "Call 43%"
    assert market.prem_line({"prem_skew_pct": -22.0}) == "Put 22%"
    assert market.prem_line({"prem_skew_pct": 0.4}) == "Even"
    assert market.prem_line({"prem_skew_pct": None}) == "—"     # flagged, no data
    assert market.prem_line({"display": "XLB"}) == ""           # not a prem tile


def test_tile_text_includes_prem_field():
    # A normal quote tile that is prem-flagged shows price + change + a prem line.
    t = {"display": "SPY", "last": 748.39, "change": 6.30, "change_pct": 0.85,
         "value_only": False, "prem_skew_pct": 31.0}
    txt = market.tile_text(t)
    assert txt["last"] == "748.39"
    assert txt["prem"] == "Call 31%"
    # BIG10 basket carries its aggregate premium as the prem line
    mag = {"display": "BIG10", "basket": True, "avg_pct": 0.34,
           "breadth_text": "8/10 up", "prem_skew_pct": 42.0}
    mtxt = market.tile_text(mag)
    assert mtxt["last"] == "+0.34%" and mtxt["change"] == "8/10 up"
    assert mtxt["prem"] == "Call 42%"


def test_tile_text_basket_shows_avg_and_breadth():
    t = {"display": "BIG10", "basket": True, "avg_pct": 0.32,
         "breadth_text": "8/10 up", "change_pct": 0.32, "color_state": "risk_on_mild"}
    txt = market.tile_text(t)
    assert txt["last"] == "+0.32%"      # equal-weighted avg day move (headline)
    assert txt["change"] == "8/10 up"   # breadth subline
    # a negative avg keeps the sign
    t2 = {"display": "BIG10", "basket": True, "avg_pct": -1.5, "breadth_text": "2/10 up"}
    assert market.tile_text(t2)["last"] == "-1.50%"


def test_poll_reads_payload_off_the_event_loop():
    """The dashboard service publishes every ~2s during RTH, so the version gate
    passes nearly every poll → the full ~48-tile payload read must run OFF the
    event loop (the poll is async, routing bus_client.read through run.io_bound)."""
    import inspect
    src = inspect.getsource(market.render)
    assert "async def _poll" in src
    assert "run.io_bound(bus_client.read" in src
