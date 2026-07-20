import services.options_svc.matrix as m

# ---- intraday_trend ----
def test_intraday_trend_up_when_spot_rising():
    series = [(0, 100.0), (300, 100.1), (600, 100.3), (900, 100.5)]
    state, direction = m.intraday_trend(series, now_ts=900)
    assert direction > 0
    assert state in ("up", "strong_up")

def test_intraday_trend_flat_on_tiny_move():
    series = [(0, 100.0), (900, 100.02)]
    state, direction = m.intraday_trend(series, now_ts=900)
    assert state == "flat"
    assert abs(direction) < 0.2

def test_intraday_trend_neutral_without_two_points():
    assert m.intraday_trend([], now_ts=0) == ("flat", 0.0)
    assert m.intraday_trend([(0, None)], now_ts=0) == ("flat", 0.0)

# ---- flow_acceleration ----
def test_flow_acceleration_hot_when_recent_slope_exceeds_average():
    series = [(0, 0.0), (600, 100.0), (1200, 200.0), (1500, 1000.0), (1800, 2000.0)]
    state, ratio = m.flow_acceleration(series, now_ts=1800, lookback_s=900)
    assert state == "hot"
    assert ratio > m._ACCEL_HOT

def test_flow_acceleration_steady_and_flat_edges():
    steady = [(0, 0.0), (900, 100.0), (1800, 200.0)]
    assert m.flow_acceleration(steady, now_ts=1800)[0] == "steady"
    assert m.flow_acceleration([], now_ts=0) == ("flat", 0.0)
    assert m.flow_acceleration([(0, 0.0), (900, 0.0)], now_ts=900) == ("flat", 0.0)

# ---- composite_signal ----
def test_composite_buy_when_trend_up_and_calls_dominant():
    sig, strength = m.composite_signal(trend_dir=0.8, call_state="hot", put_state="steady",
                                       call_prem=300.0, put_prem=100.0)
    assert sig == "buy"
    assert strength >= 1

def test_composite_sell_when_trend_down_and_puts_dominant():
    sig, _ = m.composite_signal(trend_dir=-0.7, call_state="steady", put_state="hot",
                                call_prem=80.0, put_prem=260.0)
    assert sig == "sell"

def test_composite_neutral_on_conflict():
    sig, _ = m.composite_signal(trend_dir=0.6, call_state="steady", put_state="hot",
                                call_prem=200.0, put_prem=200.0)
    assert sig == "neutral"

def test_composite_neutral_on_no_data():
    sig, strength = m.composite_signal(trend_dir=0.0, call_state="flat", put_state="flat",
                                       call_prem=0.0, put_prem=0.0)
    assert sig == "neutral" and strength == 0

# ---- pc_ratio / net_premium ----
def test_pc_ratio_and_net_premium():
    assert m.pc_ratio(call_prem=200.0, put_prem=100.0) == 0.5
    assert m.pc_ratio(call_prem=0.0, put_prem=100.0) is None
    assert m.net_premium_m(call_prem=3_000_000.0, put_prem=1_000_000.0) == 2.0

# ---- gex_regime ----
def test_gex_regime_above_below_na():
    assert m.gex_regime(spot=105.0, flip=100.0) == "above"
    assert m.gex_regime(spot=95.0, flip=100.0) == "below"
    assert m.gex_regime(spot=None, flip=100.0) == "na"
    assert m.gex_regime(spot=100.0, flip=None) == "na"

# ---- hotness ----
def test_hotness_rewards_signals_alerts_and_conviction():
    hot = m.hotness(n_signals=4, n_alerts=3, signal_strength=3)
    cold = m.hotness(n_signals=0, n_alerts=0, signal_strength=0)
    assert hot > cold

# ---- build_rows ----
def test_build_rows_assembles_one_row_per_symbol():
    raw = {
        "SPY": {"series": [(0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
                           (900, 100.6, 30, 8, 3_000_000.0, 800_000.0)],
                "flip": 100.0},
    }
    rows = m.build_rows(raw, scan_counts={"SPY": 3}, alert_counts={"SPY": 2}, now_ts=900)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["n_signals"] == 3 and r["n_alerts"] == 2
    assert r["signal"] in ("buy", "neutral", "sell")
    assert r["gex_regime"] == "above"
    assert isinstance(r["hotness"], (int, float))
    assert r["day_pct"] is not None

def test_build_rows_degrades_symbol_with_no_series():
    rows = m.build_rows({"AAPL": {"series": [], "flip": None}},
                        scan_counts={}, alert_counts={}, now_ts=0)
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["spot"] is None
    assert rows[0]["signal"] == "neutral"
    assert rows[0]["n_signals"] == 0
