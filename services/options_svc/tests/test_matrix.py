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

def test_flow_acceleration_cool_when_recent_slope_below_average():
    # Fast accrual early, then near-flat in the recent window -> cooling.
    series = [(0, 0.0), (300, 800.0), (600, 1600.0), (1500, 1650.0), (1800, 1700.0)]
    state, ratio = m.flow_acceleration(series, now_ts=1800, lookback_s=900)
    assert state == "cool"
    assert ratio <= m._ACCEL_COOL

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

def test_composite_strong_buy_has_strength_two():
    sig, strength = m.composite_signal(trend_dir=1.0, call_state="hot", put_state="steady",
                                       call_prem=900.0, put_prem=100.0)
    assert sig == "buy"
    assert strength == 2

def test_composite_tolerates_none_premiums():
    sig, strength = m.composite_signal(trend_dir=0.8, call_state="hot", put_state="steady",
                                       call_prem=None, put_prem=None)
    assert sig in ("buy", "neutral", "sell")

# ---- pc_ratio / net_premium ----
def test_pc_ratio_and_net_premium():
    assert m.pc_ratio(call_prem=200.0, put_prem=100.0) == 0.5
    assert m.pc_ratio(call_prem=0.0, put_prem=100.0) is None
    assert m.net_premium_m(call_prem=3_000_000.0, put_prem=1_000_000.0) == 2.0

def test_pc_ratio_and_net_premium_tolerate_none():
    # forward-only premium columns are None on early snapshots — never raise.
    assert m.pc_ratio(call_prem=None, put_prem=100.0) is None
    assert m.pc_ratio(call_prem=200.0, put_prem=None) == 0.0
    assert m.net_premium_m(call_prem=None, put_prem=None) == 0.0
    assert m.net_premium_m(call_prem=3_000_000.0, put_prem=None) == 3.0

# ---- gex_regime ----
def test_gex_regime_above_below_na():
    assert m.gex_regime(spot=105.0, flip=100.0) == "above"
    assert m.gex_regime(spot=95.0, flip=100.0) == "below"
    assert m.gex_regime(spot=None, flip=100.0) == "na"
    assert m.gex_regime(spot=100.0, flip=None) == "na"

def test_gex_regime_boundary_is_above():
    assert m.gex_regime(spot=100.0, flip=100.0) == "above"

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
    assert "_open_spot" in r and r["_open_spot"] == 100.0
    # raw cumulative premium exposed for the market dashboard per-symbol/basket read
    assert r["call_prem"] == 3_000_000.0 and r["put_prem"] == 800_000.0

def test_build_rows_degrades_symbol_with_no_series():
    rows = m.build_rows({"AAPL": {"series": [], "flip": None}},
                        scan_counts={}, alert_counts={}, now_ts=0)
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["spot"] is None
    assert rows[0]["signal"] == "neutral"
    assert rows[0]["n_signals"] == 0

def test_build_rows_tolerates_null_premium_on_latest_row():
    # forward-only premium columns are None on early snapshots — one bad symbol
    # must not zero the whole matrix.
    raw = {
        "SPY": {"series": [(0, 100.0, 10, 5, None, None),
                           (900, 100.6, 30, 8, None, None)],
                "flip": 100.0},
        "QQQ": {"series": [(0, 400.0, 10, 5, 1_000_000.0, 400_000.0),
                           (900, 401.0, 30, 8, 2_000_000.0, 800_000.0)],
                "flip": 399.0},
    }
    rows = m.build_rows(raw, scan_counts={"SPY": 3}, alert_counts={"SPY": 1}, now_ts=900)
    assert len(rows) == 2
    spy = next(r for r in rows if r["symbol"] == "SPY")
    assert spy["signal"] in ("buy", "neutral", "sell")
    assert spy["net_prem_m"] == 0.0
    assert spy["pc_ratio"] is None
    assert spy["n_signals"] == 3 and spy["n_alerts"] == 1

def test_build_rows_zero_spot_is_not_nulled():
    raw = {"ZZZ": {"series": [(0, 0.0, 0, 0, 0.0, 0.0)], "flip": 0.0}}
    rows = m.build_rows(raw, scan_counts={}, alert_counts={}, now_ts=0)
    r = rows[0]
    assert r["spot"] == 0.0
    assert r["flip"] == 0.0
    assert r["gex_regime"] == "above"


# ---- market_premium_aggregate (dollar-weighted net-premium skew) ----
def test_market_premium_aggregate_dollar_weighted_skew():
    # SPY latest: call 3M / put 1M ; QQQ latest: call 1M / put 1M.
    # Dollar sum: call 4M, put 2M -> net +2M ; skew = (4-2)/(4+2) = +0.3333.
    raw = {
        "SPY": {"series": [(0, 100.0, 10, 5, 2_000_000.0, 500_000.0),
                           (900, 100.6, 30, 8, 3_000_000.0, 1_000_000.0)]},
        "QQQ": {"series": [(900, 400.0, 10, 5, 1_000_000.0, 1_000_000.0)]},
    }
    agg = m.market_premium_aggregate(raw)
    assert agg["call_total"] == 4_000_000.0
    assert agg["put_total"] == 2_000_000.0
    assert agg["net_m"] == 2.0
    assert agg["skew"] == round((4 - 2) / (4 + 2), 4)   # +0.3333
    assert agg["skew_pct"] == 33.3
    assert agg["symbols"] == 2


def test_market_premium_aggregate_none_when_no_premium():
    # Forward-only columns None + a genuinely-empty series -> no premium yet.
    raw = {
        "SPY": {"series": [(0, 100.0, 10, 5, None, None)]},
        "AAPL": {"series": []},
    }
    agg = m.market_premium_aggregate(raw)
    assert agg["skew"] is None and agg["skew_pct"] is None
    assert agg["symbols"] == 0
    assert agg["net_m"] == 0.0


def test_market_premium_aggregate_put_heavy_is_negative():
    raw = {"XLF": {"series": [(900, 40.0, 1, 1, 1_000_000.0, 3_000_000.0)]}}
    agg = m.market_premium_aggregate(raw)
    assert agg["skew"] < 0                      # put-dominated
    assert agg["skew_pct"] == -50.0             # (1-3)/(1+3) = -0.5
    assert agg["symbols"] == 1


def test_market_premium_aggregate_index_dollar_dominates():
    # A tiny put-heavy single name can't flip a huge call-heavy index (dollar-wt).
    raw = {
        "$SPX": {"series": [(900, 5000.0, 1, 1, 50_000_000.0, 20_000_000.0)]},
        "TINY": {"series": [(900, 10.0, 1, 1, 1_000.0, 9_000.0)]},
    }
    agg = m.market_premium_aggregate(raw)
    assert agg["skew"] > 0                       # index call-dominance wins
    assert agg["symbols"] == 2


# ---- iv_regime (IV-direction: the missing linchpin for setups 1 & 3) ----
def test_iv_regime_spiking_when_iv_rises_sharply():
    # +8% over the 15-min window -> spiking (cascade / risk-off half).
    series = [(0, 0.20), (300, 0.205), (600, 0.21), (900, 0.216)]
    state, chg = m.iv_regime(series, now_ts=900)
    assert state == "spiking"
    assert chg > m._IV_SPIKE

def test_iv_regime_collapsing_when_iv_falls_sharply():
    # -10% over the window -> collapsing (vol-crush half).
    series = [(0, 0.30), (300, 0.29), (600, 0.28), (900, 0.27)]
    state, chg = m.iv_regime(series, now_ts=900)
    assert state == "collapsing"
    assert chg < m._IV_CRUSH

def test_iv_regime_stable_on_small_change():
    series = [(0, 0.20), (900, 0.201)]
    assert m.iv_regime(series, now_ts=900)[0] == "stable"

def test_iv_regime_na_without_two_valid_points():
    assert m.iv_regime([], now_ts=0) == ("na", 0.0)
    assert m.iv_regime([(0, None)], now_ts=0) == ("na", 0.0)

def test_iv_regime_na_on_nonpositive_reference():
    # IV *level* must be positive; a zero reference can't yield a relative change.
    assert m.iv_regime([(0, 0.0), (900, 0.2)], now_ts=900) == ("na", 0.0)


# ---- dealer_regime (fused named playbook labels) ----
def test_dealer_regime_na_without_spot_or_flip():
    assert m.dealer_regime(None, 100.0, "stable", "flat", 60) == "na"
    assert m.dealer_regime(100.0, None, "stable", "flat", 60) == "na"

def test_dealer_regime_gamma_cascade_below_flip_and_iv_spiking():
    # setup 3: spot broke below the flip while IV spikes.
    assert m.dealer_regime(spot=95.0, flip=100.0, iv_state="spiking",
                           trend_state="strong_down", mins_to_close=120) == "gamma_cascade"

def test_dealer_regime_below_flip_without_spike_is_neutral():
    assert m.dealer_regime(spot=95.0, flip=100.0, iv_state="stable",
                           trend_state="down", mins_to_close=120) == "neutral"

def test_dealer_regime_vanna_squeeze_above_flip_and_iv_collapsing():
    # setup 1: positive gamma + IV crush.
    assert m.dealer_regime(spot=105.0, flip=100.0, iv_state="collapsing",
                           trend_state="up", mins_to_close=200) == "vanna_squeeze"

def test_dealer_regime_delta_wall_pin_above_flip_late_and_near_wall():
    # setup 4: positive gamma, late session, spot hugging a big delta wall.
    assert m.dealer_regime(spot=100.2, flip=99.0, iv_state="stable",
                           trend_state="flat", mins_to_close=60,
                           wall_dist_pct=0.2) == "delta_wall_pin"

def test_dealer_regime_charm_grind_above_flip_afternoon_and_range_bound():
    # setup 2: positive gamma, after ~1pm ET, range-bound (not strongly trending).
    assert m.dealer_regime(spot=105.0, flip=100.0, iv_state="stable",
                           trend_state="flat", mins_to_close=150) == "charm_grind"

def test_dealer_regime_no_grind_when_strongly_trending():
    # a strong afternoon trend in positive gamma isn't the range-bound grind.
    assert m.dealer_regime(spot=105.0, flip=100.0, iv_state="stable",
                           trend_state="strong_up", mins_to_close=150) == "neutral"

def test_dealer_regime_pin_needs_wall_proximity():
    # late + far from any wall -> not a pin (falls through to the afternoon grind).
    assert m.dealer_regime(spot=105.0, flip=100.0, iv_state="stable",
                           trend_state="flat", mins_to_close=60,
                           wall_dist_pct=1.5) == "charm_grind"

def test_dealer_regime_neutral_midday_positive_gamma_stable():
    # positive gamma but no setup active before the afternoon window.
    assert m.dealer_regime(spot=105.0, flip=100.0, iv_state="stable",
                           trend_state="up", mins_to_close=300) == "neutral"

def test_dealer_regime_cascade_precedence_over_everything():
    # below+spiking is the dangerous regime and wins regardless of time/wall.
    assert m.dealer_regime(spot=95.0, flip=100.0, iv_state="spiking",
                           trend_state="flat", mins_to_close=60,
                           wall_dist_pct=0.1) == "gamma_cascade"


def test_dealer_regime_over_real_gex_history_sample(tmp_path):
    """Fuse a regime label from rows loaded out of a real gex_history.db.

    Demonstrates dealer_regime composes with the actual storage layer for the
    pieces the DB already stores (spot/flip -> regime, top_pos_strike -> wall).
    The IV-direction axis is fed 'stable' here because the snapshots table has
    NO ATM-IV *level* column yet -- that's the one storage gap wiring these
    regimes requires (see gap #4 in the assessment).
    """
    import sys, time as _time, sqlite3
    from repo_paths import OPTIONS_SCANNER
    if str(OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(OPTIONS_SCANNER))
    import gex_history_db as gh

    conn = sqlite3.connect(str(tmp_path / "gex_sample.db"))
    gh.init_schema(conn)
    now = int(_time.time())
    # Two 'gex' snapshots today: spot above the flip (positive gamma), and spot
    # hugging the top positive-delta strike (a wall). Late session -> pin.
    for ts in (now - 120, now):
        gh.insert_snapshot(conn, "SPY", "gex",
            {"ts": ts, "spot": 500.2, "flip": 497.0,
             "top_pos_strike": 500.0, "top_neg_strike": 495.0, "net_total": 1.0},
            {}, dte=0)
    conn.commit()

    rows = gh.load_today(conn, "SPY", "gex")   # (ts, spot, flip, top_pos, top_neg, net)
    conn.close()
    assert rows, "expected sample snapshots to load back"
    _ts, spot, flip, top_pos, _top_neg, _net = rows[-1]

    wall_dist_pct = abs(spot - top_pos) / spot * 100.0
    regime = m.dealer_regime(spot, flip, iv_state="stable", trend_state="flat",
                             mins_to_close=45, wall_dist_pct=wall_dist_pct)
    assert regime == "delta_wall_pin"


# ---- dealer_regime_from_rows (assemble inputs from DB-shaped rows) ----
def _rows(triples, top_pos=None, top_neg=None):
    # load_today shape: (ts, spot, flip, top_pos_strike, top_neg_strike, net_total)
    return [(ts, spot, flip, top_pos, top_neg, 1.0) for ts, spot, flip in triples]

def test_dealer_regime_from_rows_na_on_empty():
    out = m.dealer_regime_from_rows([], [], now_ts=900, close_ts=900 + 3600)
    assert out["regime"] == "na"

def test_dealer_regime_from_rows_assembles_vanna_squeeze():
    rows = _rows([(0, 105.0, 100.0), (900, 105.2, 100.0)])
    atm = [(0, 0.30), (900, 0.27)]                     # -10% -> collapsing
    out = m.dealer_regime_from_rows(rows, atm, now_ts=900, close_ts=900 + 7200)
    assert out["iv_state"] == "collapsing"
    assert out["regime"] == "vanna_squeeze"

def test_dealer_regime_from_rows_assembles_gamma_cascade():
    rows = _rows([(0, 96.0, 100.0), (900, 95.0, 100.0)])   # below flip
    atm = [(0, 0.20), (900, 0.22)]                     # +10% -> spiking
    out = m.dealer_regime_from_rows(rows, atm, now_ts=900, close_ts=900 + 3600)
    assert out["iv_state"] == "spiking"
    assert out["regime"] == "gamma_cascade"

def test_dealer_regime_from_rows_assembles_delta_wall_pin():
    rows = _rows([(0, 100.1, 99.0), (900, 100.2, 99.0)], top_pos=100.0, top_neg=95.0)
    atm = [(0, 0.20), (900, 0.201)]                    # stable
    out = m.dealer_regime_from_rows(rows, atm, now_ts=900, close_ts=900 + 3600)  # 60 min
    assert out["wall_dist_pct"] is not None and out["wall_dist_pct"] < m._PIN_PROX_PCT
    assert out["regime"] == "delta_wall_pin"

def test_dealer_regime_from_rows_after_close_disables_time_gates():
    rows = _rows([(0, 105.0, 100.0), (900, 105.1, 100.0)], top_pos=105.0)
    atm = [(0, 0.20), (900, 0.201)]                    # stable
    # close already passed -> mins_to_close None -> pin/grind can't fire -> neutral
    out = m.dealer_regime_from_rows(rows, atm, now_ts=900, close_ts=300)
    assert out["mins_to_close"] is None
    assert out["regime"] == "neutral"
