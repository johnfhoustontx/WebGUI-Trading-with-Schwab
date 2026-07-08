from services.market_svc import classify as C


def test_normalize_equity_uses_net_percent_change():
    q = {"assetMainType": "EQUITY",
         "quote": {"lastPrice": 100.0, "netChange": 1.0, "netPercentChange": 1.0,
                   "closePrice": 99.0}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 100.0 and chg == 1.0 and round(pct, 3) == 1.0


def test_normalize_future_uses_future_percent_change():
    q = {"assetMainType": "FUTURE",
         "quote": {"lastPrice": 7560.75, "netChange": 9.5,
                   "futurePercentChange": 0.1258, "closePrice": 7551.25}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 7560.75 and chg == 9.5 and round(pct, 3) == 0.126


def test_normalize_internal_value_only_no_change():
    q = {"assetMainType": "EQUITY",
         "quote": {"lastPrice": 38.0, "netChange": 0.0, "netPercentChange": 0.0,
                   "closePrice": 0.0}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 38.0 and chg == 0.0 and pct == 0.0


def test_spread_diff_last():
    assert C.spread_value("diff_last", (1160.0, 0.0, 0.0), (1625.0, 0.0, 0.0)) == \
        (-465.0, -465.0, 0.0)  # (last, change, pct) — value = a.last - b.last


def test_spread_diff_pct():
    # HYG +0.5% vs LQD +0.2% → HY outperforms by +0.3
    last, chg, pct = C.spread_value("diff_pct", (81.0, 0.0, 0.5), (110.0, 0.0, 0.2))
    assert round(pct, 3) == 0.3 and round(last, 3) == 0.3


def test_color_state_normal_up_is_risk_on():
    assert C.color_state(2.0, polarity="normal") == "risk_on_strong"
    assert C.color_state(0.4, polarity="normal") == "risk_on_mild"


def test_color_state_inverted_up_is_risk_off():
    # VIX +5% → inverted → risk-off
    assert C.color_state(5.0, polarity="inverted") == "risk_off_strong"


def test_color_state_flat_and_no_data():
    assert C.color_state(0.02, polarity="normal") == "flat"
    assert C.color_state(None, polarity="normal") == "no_data"


def test_color_state_value_only_uses_sign_one_intensity():
    # a value-only internal (e.g. $TICK = +300) → mild risk-on, not "strong"
    assert C.color_state(300.0, polarity="normal", value_only=True) == "risk_on_mild"
    assert C.color_state(-300.0, polarity="normal", value_only=True) == "risk_off_mild"
