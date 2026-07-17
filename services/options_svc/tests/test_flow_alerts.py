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
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is False and cfg["spike"]["k"] == 9.0
    assert cfg["crossover"]["band"] == 0.02   # unspecified keys fall back to defaults


# --- Task 2: pure detectors ---

def _row(ts, cv, pv, cp, pp):
    return (ts, 100.0, cv, pv, cp, pp)   # (ts, spot, call_vol, put_vol, call_prem, put_prem)


def test_crossover_calls_overtake_puts():
    # net = call_prem - put_prem flips - → + decisively.
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    a = flow_alerts.detect_crossover(series, band=0.02)
    assert a and a["side"] == "calls_over" and a["type"] == "crossover"


def test_crossover_none_when_no_flip():
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 150.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02) is None


def test_crossover_band_rejects_graze():
    # Flips sign but only by a hair (< 2% of the larger side) → no alert.
    series = [_row(60, 0, 0, 199.0, 200.0), _row(120, 0, 0, 201.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02) is None


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
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    cfg = flow_alerts.load_thresholds()
    cd = {}
    first = flow_alerts.detect_flow_alerts("$SPX", series, cfg, cd, now_ts=120)
    assert any(a["type"] == "crossover" for a in first)
    # Same tick again within cooldown → nothing new.
    second = flow_alerts.detect_flow_alerts("$SPX", series, cfg, cd, now_ts=180)
    assert second == []
