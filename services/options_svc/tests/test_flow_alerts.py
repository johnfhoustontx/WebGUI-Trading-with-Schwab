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
