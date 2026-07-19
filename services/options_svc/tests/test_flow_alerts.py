from services.options_svc import flow_alerts


# --- Task 1: config loader ---

def test_load_thresholds_defaults(tmp_path, monkeypatch):
    # Missing file → built-in defaults, never raises.
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", tmp_path / "nope.toml")
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is True
    assert cfg["spike"]["k"] == 4.0 and cfg["crossover"]["band"] == 0.02


def test_load_thresholds_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "flow_alerts.toml"
    p.write_text("enabled = false\n[spike]\nk = 9.0\n", encoding="utf-8")
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", p)
    flow_alerts.reset_thresholds_cache()
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is False and cfg["spike"]["k"] == 9.0
    assert cfg["crossover"]["band"] == 0.02   # unspecified keys fall back to defaults


def test_load_thresholds_caches_by_mtime(tmp_path, monkeypatch):
    """The TOML is re-parsed only when its mtime changes — it's read every minute
    on the flow-alert tick, but the file rarely changes."""
    p = tmp_path / "flow_alerts.toml"
    p.write_text("[spike]\nk = 3.0\n", encoding="utf-8")
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", p)
    flow_alerts.reset_thresholds_cache()

    parses = {"n": 0}
    real_load = flow_alerts.tomllib.load
    monkeypatch.setattr(flow_alerts.tomllib, "load",
                        lambda fh: (parses.__setitem__("n", parses["n"] + 1)
                                    or real_load(fh)))
    a = flow_alerts.load_thresholds()
    b = flow_alerts.load_thresholds()
    assert a["spike"]["k"] == 3.0 and b["spike"]["k"] == 3.0
    assert parses["n"] == 1                       # second call served from cache
    # A newer mtime forces a re-parse.
    import os
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    p.write_text("[spike]\nk = 7.0\n", encoding="utf-8")
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    c = flow_alerts.load_thresholds()
    assert c["spike"]["k"] == 7.0 and parses["n"] == 2


def test_detect_flow_alerts_normalizes_series_once(monkeypatch):
    """The three detector passes (crossover + spike×2) share ONE normalization of
    the series instead of re-normalizing per pass."""
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


def test_spike_fires_above_baseline_and_floor():
    # 5 quiet minutes (+100/min) then a +2000 burst → 20x baseline, over floor.
    cum = 0
    series = []
    for i, inc in enumerate([100, 100, 100, 100, 100, 2000]):
        cum += inc
        series.append(_row((i + 1) * 60, cum, 0, 0.0, 0.0))
    a = flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20, min_points=5)
    assert a and a["side"] == "call" and a["type"] == "spike"


def test_spike_respects_floor():
    # A relatively big jump but below the absolute floor → no alert.
    cum = 0
    series = []
    for inc in [10, 10, 10, 10, 10, 100]:   # 100 < floor 500
        cum += inc
        series.append(_row(len(series) * 60, cum, 0, 0.0, 0.0))
    assert flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20, min_points=5) is None


def test_spike_warmup_needs_min_points():
    series = [_row(60, 100, 0, 0.0, 0.0), _row(120, 5000, 0, 0.0, 0.0)]  # 1 increment
    assert flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20, min_points=5) is None


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


def test_spike_dead_quiet_name_needs_k_times_min_baseline():
    # baseline 0 (all flat) then a burst that clears `floor` but is below k*min_baseline
    # → NO alert (the relative test always applies now).
    cum = 0
    series = []
    for inc in [0, 0, 0, 0, 0, 600]:   # floor 500 cleared, but 600 < 4*200
        cum += inc
        series.append(_row(len(series) * 60, cum, 0, 0.0, 0.0))
    assert flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20,
                                    min_points=5, min_baseline=200) is None
    # A bigger burst that clears k*min_baseline (>=800) AND floor → fires.
    series[-1] = _row(series[-1][0], 900, 0, 0.0, 0.0)
    a = flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20,
                                 min_points=5, min_baseline=200)
    assert a and a["type"] == "spike"


def test_crossover_skipped_when_premium_below_min():
    # A decisive sign flip but both premiums tiny → skipped by min_premium.
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02, min_premium=10000) is None
    # Same relative flip at large premiums → fires.
    series = [_row(60, 0, 0, 100000.0, 200000.0), _row(120, 0, 0, 260000.0, 200000.0)]
    a = flow_alerts.detect_crossover(series, band=0.02, min_premium=10000)
    assert a and a["side"] == "calls_over"
