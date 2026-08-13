from services.options_svc import flow_alerts


# --- Task 1: config loader ---

def test_load_thresholds_defaults(tmp_path, monkeypatch):
    # Missing file → built-in defaults, never raises.
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", tmp_path / "nope.toml")
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is True
    assert cfg["uoa"]["k"] == 3.0 and cfg["crossover"]["band"] == 0.02


def test_load_thresholds_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "flow_alerts.toml"
    p.write_text("enabled = false\n[uoa]\nk = 9.0\n", encoding="utf-8")
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", p)
    flow_alerts.reset_thresholds_cache()
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is False and cfg["uoa"]["k"] == 9.0
    assert cfg["crossover"]["band"] == 0.02   # unspecified keys fall back to defaults


def test_load_thresholds_caches_by_mtime(tmp_path, monkeypatch):
    """The TOML is re-parsed only when its mtime changes — it's read every minute
    on the flow-alert tick, but the file rarely changes."""
    p = tmp_path / "flow_alerts.toml"
    p.write_text("[uoa]\nk = 3.0\n", encoding="utf-8")
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", p)
    flow_alerts.reset_thresholds_cache()

    parses = {"n": 0}
    real_load = flow_alerts.tomllib.load
    monkeypatch.setattr(flow_alerts.tomllib, "load",
                        lambda fh: (parses.__setitem__("n", parses["n"] + 1)
                                    or real_load(fh)))
    a = flow_alerts.load_thresholds()
    b = flow_alerts.load_thresholds()
    assert a["uoa"]["k"] == 3.0 and b["uoa"]["k"] == 3.0
    assert parses["n"] == 1                       # second call served from cache
    # A newer mtime forces a re-parse.
    import os
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    p.write_text("[uoa]\nk = 7.0\n", encoding="utf-8")
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    c = flow_alerts.load_thresholds()
    assert c["uoa"]["k"] == 7.0 and parses["n"] == 2


def test_load_thresholds_has_big_delta_defaults(tmp_path, monkeypatch):
    # With NO toml, defaults are present and sane.
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", tmp_path / "missing.toml")
    flow_alerts.reset_thresholds_cache()
    cfg = flow_alerts.load_thresholds()
    bd = cfg["big_delta"]
    assert bd["enabled"] is True and bd["push"] is False
    assert bd["rel_threshold"] == 0.20
    assert bd["min_contract_notional"] == 10_000_000
    assert bd["delta_lo"] == 0.05 and bd["delta_hi"] == 0.85 and bd["delta_max"] == 1.0
    assert bd["top_n"] == 3


def test_detect_flow_alerts_normalizes_series_once(monkeypatch):
    """The crossover pass normalizes the series exactly ONCE per call."""
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 10, 5, 260.0, 200.0)]
    calls = {"n": 0}
    real = flow_alerts._norm
    monkeypatch.setattr(flow_alerts, "_norm",
                        lambda s: (calls.__setitem__("n", calls["n"] + 1) or real(s)))
    cfg = flow_alerts.load_thresholds()
    flow_alerts.detect_flow_alerts("SPY", series, cfg, {}, 1000)
    assert calls["n"] == 1


# --- Task 2: pure detectors ---

def _row(ts, cv, pv, cp, pp):
    return (ts, 100.0, cv, pv, cp, pp)   # (ts, spot, call_vol, put_vol, call_prem, put_prem)


def test_crossover_calls_overtake_puts():
    # net = call_prem - put_prem flips - → + decisively.
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    a = flow_alerts.detect_crossover(series, band=0.02, min_premium=0)
    assert a and a["side"] == "calls_over" and a["type"] == "crossover"


def test_crossover_none_when_no_flip():
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 150.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02) is None


def test_crossover_band_rejects_graze():
    # Flips sign but only by a hair (< 2% of the larger side) → no alert.
    series = [_row(60, 0, 0, 199.0, 200.0), _row(120, 0, 0, 201.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02, min_premium=0) is None


def test_detect_flow_alerts_cooldown_suppresses_repeat():
    # Large premiums so the crossover clears the default min_premium floor.
    series = [_row(60, 0, 0, 100000.0, 200000.0), _row(120, 0, 0, 260000.0, 200000.0)]
    cfg = flow_alerts.load_thresholds()
    cd = {}
    first = flow_alerts.detect_flow_alerts("$SPX", series, cfg, cd, now_ts=120)
    assert any(a["type"] == "crossover" for a in first)
    # Same tick again within cooldown → nothing new.
    second = flow_alerts.detect_flow_alerts("$SPX", series, cfg, cd, now_ts=180)
    assert second == []


def test_crossover_skipped_when_premium_below_min():
    # A decisive sign flip but both premiums tiny → skipped by min_premium.
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02, min_premium=10000) is None
    # Same relative flip at large premiums → fires.
    series = [_row(60, 0, 0, 100000.0, 200000.0), _row(120, 0, 0, 260000.0, 200000.0)]
    a = flow_alerts.detect_crossover(series, band=0.02, min_premium=10000)
    assert a and a["side"] == "calls_over"


# --- Task 1: contract-level UOA ---

def _chain_uoa():
    exp = "2026-07-18:2"   # dated
    z = "2026-07-15:0"     # 0DTE
    def c(strike, vol, oi, mark):
        return {"totalVolume": vol, "openInterest": oi, "mark": mark}
    return {"underlyingPrice": 450.0,
            "callExpDateMap": {
                exp: {"450.0": [c(450.0, 8200, 1300, 1.85)],   # 6.3x OI, $1.52M — qualifies
                      "460.0": [c(460.0, 100, 50, 0.20)]},     # tiny — below floors
                z:   {"451.0": [c(451.0, 9000, 0, 0.50)]}},    # oi=0 → skipped
            "putExpDateMap": {
                exp: {"440.0": [c(440.0, 6000, 900, 2.10)]}}}  # 6.7x OI, $1.26M — qualifies


def test_detect_uoa_qualifies_and_extracts_fields():
    cfg = {"uoa": {"k": 3.0, "vol_floor": 500, "premium_floor": 250000, "top_n": 3}}
    out = flow_alerts.detect_uoa("SPY", _chain_uoa(), cfg)
    ids = {(a["side"], a["strike"], a["expiry"]) for a in out}
    assert ("call", 450.0, "2026-07-18") in ids
    assert ("put", 440.0, "2026-07-18") in ids
    # the tiny 460 (below vol/premium floor) and the oi=0 451 are excluded
    assert all(not (a["strike"] == 460.0 or a["oi"] == 0) for a in out)
    a = next(a for a in out if a["strike"] == 450.0)
    assert a["type"] == "uoa" and a["symbol"] == "SPY" and a["dte"] == 2
    assert a["cost"] == 1.85 and a["volume"] == 8200 and a["oi"] == 1300
    assert round(a["vol_oi"], 1) == 6.3 and round(a["premium"]) == 1517000


def test_detect_uoa_top_n_by_premium():
    # 4 qualifiers, top_n=2 → only the 2 richest by premium survive.
    exp = "2026-07-18:2"
    def c(v, oi, m): return {"totalVolume": v, "openInterest": oi, "mark": m}
    chain = {"callExpDateMap": {exp: {
        "1.0": [c(1000, 100, 1.0)], "2.0": [c(1000, 100, 2.0)],
        "3.0": [c(1000, 100, 3.0)], "4.0": [c(1000, 100, 4.0)]}}, "putExpDateMap": {}}
    cfg = {"uoa": {"k": 3.0, "vol_floor": 500, "premium_floor": 1, "top_n": 2}}
    out = flow_alerts.detect_uoa("X", chain, cfg)
    assert sorted(a["strike"] for a in out) == [3.0, 4.0]   # richest premiums


def test_alert_text_uoa_has_all_fields():
    a = {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 450.0,
         "expiry": "2026-07-18", "dte": 2, "cost": 1.85, "volume": 8200,
         "oi": 1300, "vol_oi": 6.3, "premium": 1517000.0}
    t = flow_alerts.alert_text(a)
    assert "SPY" in t and "07/18" in t and "450" in t and "C" in t
    assert "$1.85" in t and "8,200" in t and "1,300" in t and "6.3" in t
    assert "1.5M" in t or "1.52M" in t   # premium humanized


def test_alert_text_uoa_0dte_tag():
    a = {"type": "uoa", "side": "put", "symbol": "SPY", "strike": 451.0,
         "expiry": "2026-07-15", "dte": 0, "cost": 0.5, "volume": 9000,
         "oi": 400, "vol_oi": 22.5, "premium": 450000.0}
    assert "0DTE" in flow_alerts.alert_text(a)


def test_alert_text_crossover_shows_premiums():
    a = {"type": "crossover", "side": "calls_over", "symbol": "$SPX",
         "call_prem": 2100000.0, "put_prem": 1950000.0}
    t = flow_alerts.alert_text(a)
    assert "$SPX" in t and ("2.1M" in t or "2.10M" in t) and ("1.9M" in t or "1.95M" in t)
    assert "bullish" in t.lower()


def test_human_money_boundary_no_1000k():
    assert flow_alerts._human_money(999_700) == "$1.00M"   # not "$1000k"
    assert flow_alerts._human_money(1_520_000) == "$1.52M"
    assert flow_alerts._human_money(250_000) == "$250k"
    assert flow_alerts._human_money(999_000) == "$999k"
    assert flow_alerts._human_money(0) == "$0" and flow_alerts._human_money(None) == "$0"


# --- Gamma-flip regime detector ---

def test_gamma_regime_hard_classify_baseline():
    # No prior regime → hard classify at the flip level.
    assert flow_alerts.gamma_regime(spot=5510, flip=5500, prev=None, band_pct=0.0) == "positive"
    assert flow_alerts.gamma_regime(spot=5490, flip=5500, prev=None, band_pct=0.0) == "negative"


def test_gamma_regime_na_on_missing_data():
    assert flow_alerts.gamma_regime(spot=None, flip=5500, prev=None) == "na"
    assert flow_alerts.gamma_regime(spot=5500, flip=None, prev=None) == "na"


def test_gamma_regime_hysteresis_holds_in_dead_zone():
    # prev positive, spot dips just below flip but within the band → stays positive.
    band = 0.002  # 0.2%
    assert flow_alerts.gamma_regime(spot=5495, flip=5500, prev="positive", band_pct=band) == "positive"
    # only once spot clears the lower band does it flip negative.
    assert flow_alerts.gamma_regime(spot=5488, flip=5500, prev="positive", band_pct=band) == "negative"
    # symmetric from negative.
    assert flow_alerts.gamma_regime(spot=5505, flip=5500, prev="negative", band_pct=band) == "negative"
    assert flow_alerts.gamma_regime(spot=5512, flip=5500, prev="negative", band_pct=band) == "positive"


def test_detect_gamma_flip_baseline_no_alert():
    alert, regime = flow_alerts.detect_gamma_flip("$SPX", spot=5510, flip=5500,
                                                  prev_regime=None, band_pct=0.0, ts=120)
    assert alert is None and regime == "positive"


def test_detect_gamma_flip_no_change_no_alert():
    alert, regime = flow_alerts.detect_gamma_flip("$SPX", spot=5510, flip=5500,
                                                  prev_regime="positive", band_pct=0.0, ts=120)
    assert alert is None and regime == "positive"


def test_detect_gamma_flip_positive_to_negative():
    alert, regime = flow_alerts.detect_gamma_flip("$SPX", spot=5480, flip=5500,
                                                  prev_regime="positive", band_pct=0.0, ts=120)
    assert regime == "negative"
    assert alert["type"] == "gamma_flip" and alert["side"] == "to_negative"
    assert alert["symbol"] == "$SPX" and alert["spot"] == 5480 and alert["flip"] == 5500


def test_detect_gamma_flip_negative_to_positive():
    alert, regime = flow_alerts.detect_gamma_flip("SPY", spot=560, flip=555,
                                                  prev_regime="negative", band_pct=0.0, ts=99)
    assert regime == "positive" and alert["side"] == "to_positive"


def test_detect_gamma_flip_na_keeps_prev():
    alert, regime = flow_alerts.detect_gamma_flip("SPY", spot=None, flip=555,
                                                  prev_regime="positive", band_pct=0.0, ts=99)
    assert alert is None and regime == "positive"   # unclassifiable → keep prior


def test_alert_text_gamma_flip():
    neg = flow_alerts.alert_text({"type": "gamma_flip", "side": "to_negative", "symbol": "$SPX",
                                  "spot": 5480, "flip": 5500})
    assert "$SPX" in neg and "NEGATIVE" in neg and "5480" in neg and "5500" in neg
    pos = flow_alerts.alert_text({"type": "gamma_flip", "side": "to_positive", "symbol": "SPY",
                                  "spot": 560, "flip": 555})
    assert "POSITIVE" in pos


def test_gamma_flip_config_defaults():
    cfg = flow_alerts._DEFAULTS
    assert cfg["gamma_flip"]["enabled"] is True
    assert "$SPX" in cfg["gamma_flip"]["symbols"]


# --- Task 2: detect_big_delta ---

def _chain(spot, contracts):
    # contracts: list of (side, strike, expiry, dte, delta, vol)
    m = {"call": {}, "put": {}}
    for side, strike, expiry, dte, delta, vol in contracts:
        m[side].setdefault(f"{expiry}:{dte}", {}).setdefault(f"{strike}", []).append(
            {"delta": delta, "totalVolume": vol, "mark": 1.0})
    return {"underlyingPrice": spot, "callExpDateMap": m["call"], "putExpDateMap": m["put"]}


_CFG = {"big_delta": {"enabled": True, "rel_threshold": 0.20,
        "min_contract_notional": 10_000_000, "delta_lo": 0.05, "delta_hi": 0.85,
        "delta_max": 1.0, "top_n": 3}}


def test_big_delta_fires_top_share_not_sub_share():
    # A carries 60% of gross (fires), B ~13% each of the rest (below 20% -> no).
    # deltas 0.5; vols chosen so A dominates; spot 100 -> notional |d|*vol*100*spot.
    ch = _chain(100.0, [
        ("call", 100, "2026-08-14", 3, 0.5, 300_000),   # A: |0.5|*300k*100*100 = $1.5B
        ("call", 101, "2026-08-14", 3, 0.5, 50_000),    # B: $250M
        ("call", 102, "2026-08-14", 3, 0.5, 50_000),    # C: $250M
    ])
    out = flow_alerts.detect_big_delta("SPY", ch, _CFG)
    fired = {a["strike"] for a in out}
    assert 100 in fired               # 1.5B / 2.0B = 75% >= 20% and >= $10M
    assert 101 not in fired and 102 not in fired  # 250M / 2.0B = 12.5% < 20%


def test_big_delta_abs_floor_drops_tiny_name():
    # One contract = 100% of a tiny gross but only $2M notional -> below the $10M floor.
    ch = _chain(50.0, [("put", 20, "2026-08-14", 3, 0.4, 1000)])  # 0.4*1000*100*50 = $2M
    assert flow_alerts.detect_big_delta("XLC", ch, _CFG) == []


def test_big_delta_drops_sentinel_and_band():
    ch = _chain(100.0, [
        ("call", 100, "2026-08-14", 3, -999.0, 900_000),  # sentinel |d|>1 -> excluded from gross+fire
        ("call", 101, "2026-08-14", 3, 0.95, 900_000),    # deep-ITM > delta_hi -> excluded
        ("call", 102, "2026-08-14", 3, 0.01, 900_000),    # near-zero < delta_lo -> excluded
        ("call", 103, "2026-08-14", 3, 0.5, 300_000),     # only real contract -> 100% of gross, $1.5B
    ])
    out = flow_alerts.detect_big_delta("SPY", ch, _CFG)
    assert [a["strike"] for a in out] == [103]


def test_big_delta_topn_and_pct_of_gross():
    ch = _chain(100.0, [("call", 100 + i, "2026-08-14", 3, 0.5, 200_000) for i in range(5)])
    out = flow_alerts.detect_big_delta("SPY", ch, {"big_delta": {**_CFG["big_delta"], "top_n": 2}})
    assert len(out) == 2
    assert all(a["type"] == "big_delta" for a in out)
    assert 0 < out[0]["pct_of_gross"] <= 1.0 and out[0]["delta_notional"] >= out[1]["delta_notional"]


def test_big_delta_defensive():
    assert flow_alerts.detect_big_delta("SPY", None, _CFG) == []
    assert flow_alerts.detect_big_delta("SPY", {}, _CFG) == []


def test_alert_text_big_delta_has_all_fields():
    a = {"type": "big_delta", "side": "call", "symbol": "SPY", "strike": 450.0,
         "expiry": "2026-07-18", "dte": 2, "delta": 0.42, "volume": 12000,
         "delta_notional": 312_000_000.0, "pct_of_gross": 0.24}
    t = flow_alerts.alert_text(a)
    assert "SPY" in t and "07/18" in t and "450" in t and "C" in t
    assert "$312.00M" in t
    assert "24%" in t


# ── big_delta_should_push: PHONE gate, separate from the screen fire bar ──────
def test_big_delta_should_push_gates_on_flag_and_share():
    cfg = {"big_delta": {"push": True, "push_threshold": 0.35}}
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.40}, cfg) is True
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.35}, cfg) is True   # boundary inclusive
    # Fires (screen) but below the push bar -> NOT pushed. The whole point.
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.20}, cfg) is False


def test_big_delta_should_push_off_never_pushes():
    off = {"big_delta": {"push": False, "push_threshold": 0.35}}
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.99}, off) is False


def test_big_delta_should_push_defaults_threshold_to_0_35():
    cfg = {"big_delta": {"push": True}}                 # push_threshold absent
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.40}, cfg) is True
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.30}, cfg) is False


def test_big_delta_should_push_defensive():
    cfg = {"big_delta": {"push": True, "push_threshold": 0.35}}
    assert flow_alerts.big_delta_should_push({}, cfg) is False                  # no share
    assert flow_alerts.big_delta_should_push({"pct_of_gross": None}, cfg) is False
    assert flow_alerts.big_delta_should_push(None, cfg) is False
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.5}, {}) is False    # no big_delta cfg
    assert flow_alerts.big_delta_should_push({"pct_of_gross": 0.5}, None) is False
