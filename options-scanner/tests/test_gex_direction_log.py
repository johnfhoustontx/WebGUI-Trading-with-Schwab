"""
test_gex_direction_log.py - Tests for GEX direction instrumentation
Version: 1.0.0
Last Updated: 2026-06-13

Covers entry-timestamp selection, regime feature extraction (0-DTE filtering,
net GEX, gamma center-of-mass, call/put walls), and candidate-rule mapping.
"""

import gex_direction_log as gdl


def _strike(exp, k, net, spot):
    return {"expiration_date": exp, "strike": k, "call_gex_usd": 0.0,
            "put_gex_usd": 0.0, "net_gex_usd": net, "underlying_price": spot}


def test_pick_entry_ts_closest_to_one_pm():
    ts = ["2026-06-11T08:30:00-05:00", "2026-06-11T12:55:00-05:00",
          "2026-06-11T13:05:00-05:00", "2026-06-11T15:10:00-05:00"]
    # 13:00 target -> 12:55 is 5min away, 13:05 is 5min away; min() picks first
    chosen = gdl.pick_entry_ts(ts, entry_hour=13.0)
    assert chosen in ("2026-06-11T12:55:00-05:00", "2026-06-11T13:05:00-05:00")


def test_regime_features_filters_0dte_and_computes_walls():
    on = "2026-06-11"
    rows = [
        _strike(on, 7300, -2.0e9, 7350),   # put wall (most negative)
        _strike(on, 7400, +3.0e9, 7350),   # call wall (most positive)
        _strike(on, 7350, +1.0e9, 7350),
        _strike("2026-06-12", 7350, +9.9e9, 7350),  # next expiry -> excluded
    ]
    f = gdl.regime_features(rows, on)
    assert f["entry_spot"] == 7350
    assert f["net_gex"] == (-2.0e9 + 3.0e9 + 1.0e9)   # 0DTE only
    assert f["top_pos_strike"] == 7400
    assert f["top_neg_strike"] == 7300
    # COM is |gex|-weighted, must lie within the strike range
    assert 7300 <= f["gex_com"] <= 7400


def test_regime_features_falls_back_to_nearest_expiry_when_no_0dte():
    rows = [_strike("2026-06-12", 7350, 1.0e9, 7350)]
    f = gdl.regime_features(rows, "2026-06-11")  # no 0DTE rows
    assert f is not None and f["entry_spot"] == 7350


def test_rule_predictions_magnet_directions():
    # entry below COM/wall -> rules predict UP (1); above -> DOWN (0)
    up = {"entry_spot": 7300, "gex_com": 7380, "top_pos_strike": 7400,
          "net_gex": 5.0e9, "open_spot": 7290}
    dn = {"entry_spot": 7450, "gex_com": 7380, "top_pos_strike": 7400,
          "net_gex": 5.0e9, "open_spot": 7460}
    pu, pd = gdl._rule_predictions(up), gdl._rule_predictions(dn)
    assert pu["magnet_com"] == 1 and pu["magnet_wall"] == 1
    assert pd["magnet_com"] == 0 and pd["magnet_wall"] == 0
    # positive gamma -> regime_cond uses COM magnet
    assert pu["regime_cond"] == 1


def test_rule_predictions_negative_gamma_uses_momentum():
    # net_gex < 0 -> regime_cond follows morning move (entry vs open)
    row = {"entry_spot": 7400, "gex_com": 7300, "top_pos_strike": 7350,
           "net_gex": -5.0e9, "open_spot": 7350}  # entry > open -> up momentum
    assert gdl._rule_predictions(row)["regime_cond"] == 1
