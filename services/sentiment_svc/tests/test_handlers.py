"""Tests for the sentiment service refresh handler (Task 1.2).

The handler is the service-side analog of the page's ``_refresh_cache_sync``:
it computes via ``compute``, writes three cache views into the Redis bus,
publishes change events, and dual-writes the legacy bridge. We monkeypatch
``handlers.compute`` so nothing touches a live proxy, and use a fakeredis
``Bus(fake=True)``.
"""
from shared.bus import Bus
from shared.contracts.envelope import Command
from services.sentiment_svc import handlers


def _fake_live(total="7.80", bias="Long"):
    """A live snapshot shaped like ``live_composite.compute_live`` output."""
    return {
        "date": "2026-06-15",
        "source": "live",
        "composite": {"total_score": total, "bias": bias,
                      "size_modifier": "1.10x", "aggregate_confidence": 0.9},
        "component_scores": {"vix_complex": 5.0, "put_call": 6.0,
                             "breadth": 10.0, "rotation": 7.0,
                             "sector_perf": 8.0, "credit_pulse": 0.0},
    }


def _patch_compute(monkeypatch, *, live, snaps, spy, sector=None,
                   proxy_up=True, industries_fn=None):
    calls = {"bridge": 0, "sector_perf": 0, "industries": []}
    monkeypatch.setattr(handlers.compute, "load_snapshots",
                        lambda *a, **k: (snaps, spy))
    monkeypatch.setattr(handlers.compute, "load_live", lambda *a, **k: live)
    monkeypatch.setattr(handlers.compute, "proxy_up", lambda: proxy_up)

    def _sector_perf(_spy):
        calls["sector_perf"] += 1
        return sector

    monkeypatch.setattr(handlers.compute, "load_sector_perf", _sector_perf)

    def _default_industries(etfs, _spy):
        calls["industries"].append(list(etfs))
        return {"quotes": {}, "trends": {}, "pcr": {}, "quadrants": {}}

    monkeypatch.setattr(handlers.compute, "load_industries",
                        industries_fn or _default_industries)

    def _bridge(*a, **k):
        calls["bridge"] += 1

    monkeypatch.setattr(handlers.compute, "build_and_write_bridge", _bridge)

    # Stub the directional-trend recompute so existing tests never touch a live
    # proxy (the gating/persistence is exercised in the Phase-4 tests below).
    monkeypatch.setattr(handlers.compute, "compute_intraday_trend",
                        lambda *a, **k: {"state": "range",
                                         "state_history": [], "smoothed_score": 50.0})
    monkeypatch.setattr(handlers.compute, "compute_30d_trend",
                        lambda *a, **k: {"state": "range"})

    # Stub the scoring-derive helpers (real ones tested in test_compute.py).
    monkeypatch.setattr(handlers.compute, "derive_composite_extras",
                        lambda *a, **k: {
                            "weights": {"vix_complex": 0.20}, "size": "1.00x",
                            "bias": "Neutral", "signal": "Neutral",
                            "velocity": {"text": "3d ROC: —", "flag": ""},
                            "divergence": "", "trend": None})
    monkeypatch.setattr(handlers.compute, "derive_sector_summary",
                        lambda sector: {"wpct": 0.70, "score": 7.8})
    return calls


def test_refresh_caches_composite_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    live = _fake_live()
    snaps = [{"date": "2026-06-15", "composite": {"total_score": "7.80"}}]
    spy = [1.0, 2.0]
    calls = _patch_compute(monkeypatch, live=live, snaps=snaps, spy=spy)

    sub = bus.subscribe("events:sentiment:composite")
    handlers.refresh(bus, with_sectors=False)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    comp = bus.cache_get("cache:sentiment:composite")
    assert comp is not None
    assert comp.payload["live"] == live
    assert comp.payload["proxy_up"] is True
    assert "composite_at" in comp.payload
    # Scoring-derived extras attached for the GUI to format.
    derived = comp.payload["derived"]
    assert set(derived) == {"weights", "size", "bias", "signal",
                            "velocity", "divergence", "trend"}
    assert msg is not None and "version" in msg

    hist = bus.cache_get("cache:sentiment:history")
    assert hist is not None
    assert hist.payload["snaps"] == snaps
    assert hist.payload["spy"] == spy

    assert bus.cache_get("cache:sentiment:sectors") is None
    assert calls["bridge"] == 1


def test_refresh_with_sectors_writes_sector_view(monkeypatch):
    bus = Bus(fake=True)
    sector = {"sector_data": [], "quotes": {}, "dual": {}}
    calls = _patch_compute(monkeypatch, live=_fake_live(),
                           snaps=[{"x": 1}], spy=[1.0], sector=sector)

    sub = bus.subscribe("events:sentiment:sectors")
    handlers.refresh(bus, with_sectors=True)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    sec = bus.cache_get("cache:sentiment:sectors")
    assert sec is not None
    assert sec.payload["sector"] == sector
    assert "sector_at" in sec.payload
    # Service-computed cap-weighted summary attached for the GUI.
    assert sec.payload["summary"] == {"wpct": 0.70, "score": 7.8}
    # Empty sector_data -> no sectors to enumerate -> empty industries view.
    assert sec.payload["industries"] == {}
    assert msg is not None and "version" in msg
    assert calls["sector_perf"] == 1
    assert calls["bridge"] == 1


def _sector_with_industries():
    """A sector dict shaped like ``compute.load_sector_perf`` output whose
    ``sector_data`` carries sector rows + their industry sub-rows (the same
    shape ``webgui/pages/sentiment.py:sector_industry_etfs`` enumerates)."""
    return {
        "sector_data": [
            {"kind": "sector", "sector": "Technology", "label": "Tech",
             "etf": "XLK", "name": "Technology"},
            {"kind": "industry", "sector": "Technology", "label": "Semis",
             "etf": "SMH", "name": "Semiconductors"},
            {"kind": "industry", "sector": "Technology", "label": "Software",
             "etf": "IGV", "name": "Software"},
            {"kind": "sector", "sector": "Energy", "label": "Energy",
             "etf": "XLE", "name": "Energy"},
            {"kind": "industry", "sector": "Energy", "label": "Oil",
             "etf": "XOP", "name": "Oil & Gas E&P"},
            # invalid industry etfs must be skipped (n/a / too long)
            {"kind": "industry", "sector": "Energy", "label": "Bad",
             "etf": "n/a", "name": "skip"},
            {"kind": "industry", "sector": "Energy", "label": "Long",
             "etf": "TOOLONG", "name": "skip"},
        ],
        "quotes": {}, "dual": {},
    }


def test_refresh_with_sectors_includes_industries(monkeypatch):
    bus = Bus(fake=True)
    sector = _sector_with_industries()

    rows_by_call = []

    def _industries(etfs, _spy):
        etfs = list(etfs)
        rows_by_call.append(etfs)
        return [{"etf": e} for e in etfs]  # fake rows, distinct per call

    calls = _patch_compute(monkeypatch, live=_fake_live(),
                           snaps=[{"x": 1}], spy=[1.0], sector=sector,
                           industries_fn=_industries)

    handlers.refresh(bus, with_sectors=True)

    sec = bus.cache_get("cache:sentiment:sectors")
    assert sec is not None
    assert sec.payload["sector"] == sector
    inds = sec.payload["industries"]
    assert set(inds.keys()) == {"Technology", "Energy"}
    assert inds["Technology"] == [{"etf": "SMH"}, {"etf": "IGV"}]
    assert inds["Energy"] == [{"etf": "XOP"}]  # n/a + TOOLONG skipped
    # load_industries called once per sector.
    assert rows_by_call == [["SMH", "IGV"], ["XOP"]]
    assert calls["sector_perf"] == 1
    assert calls["bridge"] == 1


def test_industries_failure_non_fatal(monkeypatch):
    bus = Bus(fake=True)
    sector = _sector_with_industries()

    def _boom(_etfs, _spy):
        raise RuntimeError("industry load exploded")

    calls = _patch_compute(monkeypatch, live=_fake_live(),
                           snaps=[{"x": 1}], spy=[1.0], sector=sector,
                           industries_fn=_boom)

    handlers.refresh(bus, with_sectors=True)  # must not raise

    sec = bus.cache_get("cache:sentiment:sectors")
    assert sec is not None
    assert sec.payload["sector"] == sector
    # Every sector fails -> industries view is empty (per-sector skips).
    assert sec.payload["industries"] == {}
    assert calls["bridge"] == 1


def test_industries_failure_per_sector_resilient(monkeypatch):
    """One sector's industry compute exploding skips THAT sector only; the
    other sector still populates."""
    bus = Bus(fake=True)
    sector = _sector_with_industries()  # Technology -> [SMH, IGV], Energy -> [XOP]

    def _selective(etfs, _spy):
        etfs = list(etfs)
        if etfs == ["XOP"]:  # Energy's ETF list -> blow up only here
            raise RuntimeError("energy industry load exploded")
        return [{"etf": e} for e in etfs]

    calls = _patch_compute(monkeypatch, live=_fake_live(),
                           snaps=[{"x": 1}], spy=[1.0], sector=sector,
                           industries_fn=_selective)

    handlers.refresh(bus, with_sectors=True)  # must not raise

    sec = bus.cache_get("cache:sentiment:sectors")
    assert sec is not None
    assert sec.payload["sector"] == sector
    inds = sec.payload["industries"]
    # Technology still present; Energy skipped (omitted) on its failure.
    assert inds == {"Technology": [{"etf": "SMH"}, {"etf": "IGV"}]}
    assert "Energy" not in inds
    assert calls["bridge"] == 1


def test_refresh_survives_missing_live(monkeypatch):
    bus = Bus(fake=True)
    calls = _patch_compute(monkeypatch, live=None, snaps=[], spy=[])

    handlers.refresh(bus, with_sectors=False)  # must not raise

    comp = bus.cache_get("cache:sentiment:composite")
    assert comp is not None
    assert comp.payload["live"] is None
    hist = bus.cache_get("cache:sentiment:history")
    assert hist.payload["snaps"] == []
    assert hist.payload["spy"] == []
    assert calls["bridge"] == 1


def test_composite_gate_rejects_malformed(monkeypatch):
    bus = Bus(fake=True)
    bad = _fake_live(total="oops")  # non-numeric composite total -> gate trips
    _patch_compute(monkeypatch, live=bad, snaps=[{"x": 1}], spy=[1.0])

    import pytest
    with pytest.raises(Exception):
        handlers.refresh(bus, with_sectors=False)


def test_handle_command_refresh_triggers_full_refresh(monkeypatch):
    bus = Bus(fake=True)
    seen = {"calls": []}

    def _rec(b, with_sectors=False):
        seen["calls"].append(with_sectors)

    monkeypatch.setattr(handlers, "refresh", _rec)

    handlers.handle_command(bus, Command(type="refresh"))
    assert seen["calls"] == [True]

    handlers.handle_command(bus, Command(type="bogus"))
    assert seen["calls"] == [True]  # unknown type -> no-op


# --- rotation refresh ---------------------------------------------------------

def _rotation_assessment():
    return {
        "date": "2026-06-15",
        "headline": {"regime": "Risk-ON", "text": "Cyclicals leading",
                     "spread": 2.1, "cyclical_mom_mean": 101.2,
                     "defensive_mom_mean": 99.1},
        "sectors": [{"name": "Technology", "etf": "XLK", "rs_ratio": 101.5,
                     "rs_momentum": 102.0, "quadrant": "Leading",
                     "direction": "INTO"}],
        "rotating_from": [], "rotating_into": [],
    }


def test_refresh_rotation_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    a = _rotation_assessment()
    weights = {"XLK": 32.5, "XLU": 2.1}
    monkeypatch.setattr(handlers.compute, "rotation_assessment",
                        lambda: (a, None))
    monkeypatch.setattr(handlers.compute, "rotation_weights", lambda: weights)
    monkeypatch.setattr(handlers.compute, "rotation_risk_threshold", lambda: 1.5)

    sub = bus.subscribe("events:sentiment:rotation")
    handlers.refresh_rotation(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    rot = bus.cache_get("cache:sentiment:rotation")
    assert rot is not None
    assert rot.payload["assessment"] == a
    assert rot.payload["weights"] == weights
    assert rot.payload["risk_threshold"] == 1.5
    assert rot.payload["error"] is None
    assert msg is not None and "version" in msg


def test_refresh_rotation_caches_error(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "rotation_assessment",
                        lambda: (None, "No data from proxy (is schwab-proxy running?)"))
    monkeypatch.setattr(handlers.compute, "rotation_weights", lambda: {})
    monkeypatch.setattr(handlers.compute, "rotation_risk_threshold", lambda: 1.5)

    handlers.refresh_rotation(bus)  # must not raise

    rot = bus.cache_get("cache:sentiment:rotation")
    assert rot is not None
    assert rot.payload["assessment"] is None
    assert rot.payload["error"] == "No data from proxy (is schwab-proxy running?)"
    assert rot.payload["weights"] == {}


def test_refresh_rotation_survives_compute_exception(monkeypatch):
    bus = Bus(fake=True)

    def _boom():
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(handlers.compute, "rotation_assessment", _boom)
    monkeypatch.setattr(handlers.compute, "rotation_weights", lambda: {})
    monkeypatch.setattr(handlers.compute, "rotation_risk_threshold", lambda: 1.5)

    handlers.refresh_rotation(bus)  # must not raise

    rot = bus.cache_get("cache:sentiment:rotation")
    assert rot is not None
    assert rot.payload["assessment"] is None
    assert "engine exploded" in (rot.payload["error"] or "")


# --- 15-min trend recompute gating (Phase 4) ----------------------------------

def _reset_trend():
    handlers._TREND.update(last_ts=None, history=[], committed=None,
                           smoothed=None, trend=None, trend_30d=None)


def test_refresh_recomputes_trend_first_call_then_gates(monkeypatch):
    """First refresh recomputes the directional trend (and the sentinel lands in
    derived.trend); an immediate second refresh (clock unchanged) does NOT
    recompute but still writes the cached trend into the composite payload."""
    _reset_trend()
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(),
                   snaps=[{"date": "2026-06-15",
                           "composite": {"total_score": "7.80"}}], spy=[1.0])

    # Real derive (the stub above replaces it) — restore so trend threads through.
    def _derive(live, snaps, spy, trend=None, trend_30d=None):
        return {"weights": {}, "size": "", "bias": "", "signal": "",
                "velocity": {"text": "", "flag": ""}, "divergence": "",
                "trend": trend, "trend_30d_ago": trend_30d}
    monkeypatch.setattr(handlers.compute, "derive_composite_extras", _derive)

    calls = {"intraday": 0, "thirty": 0}
    sentinel = {"state": "bull_trend", "smoothed_score": 88.0,
                "state_history": ["bull_trend"], "marker": "intraday"}
    sentinel30 = {"state": "bull_trend", "marker": "30d"}

    def _intraday(*a, **k):
        calls["intraday"] += 1
        return sentinel

    def _thirty(*a, **k):
        calls["thirty"] += 1
        return sentinel30

    monkeypatch.setattr(handlers.compute, "compute_intraday_trend", _intraday)
    monkeypatch.setattr(handlers.compute, "compute_30d_trend", _thirty)

    # Freeze the monotonic clock so the second call is within the 15-min window.
    monkeypatch.setattr(handlers.time, "monotonic", lambda: 1000.0)

    handlers.refresh(bus, with_sectors=False)
    assert calls["intraday"] == 1 and calls["thirty"] == 1
    comp = bus.cache_get("cache:sentiment:composite")
    assert comp.payload["derived"]["trend"] == sentinel
    assert comp.payload["derived"]["trend_30d_ago"] == sentinel30
    # state persisted for the next hysteresis read.
    assert handlers._TREND["committed"] == "bull_trend"
    assert handlers._TREND["history"] == ["bull_trend"]
    assert handlers._TREND["smoothed"] == 88.0

    # Second refresh, clock unchanged -> NOT due -> no recompute, cached trend used.
    handlers.refresh(bus, with_sectors=False)
    assert calls["intraday"] == 1 and calls["thirty"] == 1  # still 1
    comp2 = bus.cache_get("cache:sentiment:composite")
    assert comp2.payload["derived"]["trend"] == sentinel
    _reset_trend()


def test_refresh_trend_recompute_failure_non_fatal(monkeypatch):
    """A blowup in the trend recompute is logged and does not abort the refresh
    (the composite still caches with whatever trend was last held — None)."""
    _reset_trend()
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(), snaps=[{"x": 1}], spy=[1.0])

    def _derive(live, snaps, spy, trend=None, trend_30d=None):
        return {"weights": {}, "size": "", "bias": "", "signal": "",
                "velocity": {"text": "", "flag": ""}, "divergence": "",
                "trend": trend, "trend_30d_ago": trend_30d}
    monkeypatch.setattr(handlers.compute, "derive_composite_extras", _derive)

    def _boom(*a, **k):
        raise RuntimeError("trend exploded")

    monkeypatch.setattr(handlers.compute, "compute_intraday_trend", _boom)
    monkeypatch.setattr(handlers.compute, "compute_30d_trend", lambda *a, **k: {})
    monkeypatch.setattr(handlers.time, "monotonic", lambda: 5000.0)

    handlers.refresh(bus, with_sectors=False)  # must not raise

    comp = bus.cache_get("cache:sentiment:composite")
    assert comp is not None
    assert comp.payload["derived"]["trend"] is None
    _reset_trend()


def test_handle_command_refresh_rotation(monkeypatch):
    bus = Bus(fake=True)
    seen = {"rot": 0}
    monkeypatch.setattr(handlers, "refresh_rotation",
                        lambda b: seen.__setitem__("rot", seen["rot"] + 1))
    monkeypatch.setattr(handlers, "refresh", lambda *a, **k: None)

    handlers.handle_command(bus, Command(type="refresh_rotation"))
    assert seen["rot"] == 1
