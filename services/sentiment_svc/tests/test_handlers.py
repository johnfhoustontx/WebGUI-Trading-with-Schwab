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
    # Reset the per-session-day backfill cache so module-level state can't leak
    # between tests (the cache is keyed by the real local date).
    handlers._SNAPSHOTS.update(date=None, snaps=None, spy=None)
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
                        lambda *a, **k: {"state": "neutral",
                                         "state_history": [], "smoothed_score": 50.0})
    monkeypatch.setattr(handlers.compute, "compute_30d_trend",
                        lambda *a, **k: {"state": "neutral"})
    # sector_pc_delta is read inside _maybe_recompute_trend (aggression axis) —
    # stub it so tests never touch the real on-disk sector-P/C store.
    monkeypatch.setattr(handlers.compute, "sector_pc_delta", lambda: None)

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
    sentinel = {"state": "bullish", "smoothed_score": 88.0,
                "state_history": ["bullish"], "marker": "intraday"}
    sentinel30 = {"state": "bullish", "marker": "30d"}

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
    assert handlers._TREND["committed"] == "bullish"
    assert handlers._TREND["history"] == ["bullish"]
    assert handlers._TREND["smoothed"] == 88.0

    # Second refresh, clock unchanged -> NOT due -> no recompute, cached trend used.
    handlers.refresh(bus, with_sectors=False)
    assert calls["intraday"] == 1 and calls["thirty"] == 1  # still 1
    comp2 = bus.cache_get("cache:sentiment:composite")
    assert comp2.payload["derived"]["trend"] == sentinel
    _reset_trend()


def test_refresh_feeds_flow_skew_and_pc_delta_into_trend(monkeypatch):
    """``_maybe_recompute_trend`` reads ``cache:options:flow_skew`` from the bus
    and ``compute.sector_pc_delta()`` and threads both into the trend compute."""
    _reset_trend()
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(), snaps=[{"x": 1}], spy=[1.0])

    # The options service publishes the flow-skew view — seed it in the bus.
    flow_skew = {"SPY": {"rr_delta": -1.5, "rr_25d": 2.0}}
    bus.cache_set(handlers.CACHE_OPTIONS_FLOW_SKEW, flow_skew)
    monkeypatch.setattr(handlers.compute, "sector_pc_delta", lambda: 0.12)

    captured = {}

    def _capture(*a, **k):
        captured["flow_skew"] = k.get("flow_skew")
        captured["sector_pc_delta"] = k.get("sector_pc_delta")
        return {"state": "bullish", "state_history": [], "smoothed_score": 70.0}

    monkeypatch.setattr(handlers.compute, "compute_intraday_trend", _capture)
    monkeypatch.setattr(handlers.compute, "compute_30d_trend", lambda *a, **k: {})
    monkeypatch.setattr(handlers.time, "monotonic", lambda: 9000.0)

    handlers.refresh(bus, with_sectors=False)

    assert captured["flow_skew"] == flow_skew
    assert captured["sector_pc_delta"] == 0.12
    _reset_trend()


def test_refresh_feeds_order_flow_into_trend(monkeypatch):
    """``_maybe_recompute_trend`` reads ``cache:sentiment:order_flow`` from the bus
    and threads it into the trend compute as ``order_flow=``."""
    _reset_trend()
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(), snaps=[{"x": 1}], spy=[1.0])

    # The service's own SSE consumer publishes the order-flow view — seed it.
    order_flow = {"SPY": {"aggressor_ratio": 0.8, "n": 50}}
    bus.cache_set(handlers.order_flow_consumer.CACHE_ORDER_FLOW, order_flow)

    captured = {}

    def _capture(*a, **k):
        captured["order_flow"] = k.get("order_flow")
        return {"state": "bullish", "state_history": [], "smoothed_score": 70.0}

    monkeypatch.setattr(handlers.compute, "compute_intraday_trend", _capture)
    monkeypatch.setattr(handlers.compute, "compute_30d_trend", lambda *a, **k: {})
    monkeypatch.setattr(handlers.time, "monotonic", lambda: 9100.0)

    handlers.refresh(bus, with_sectors=False)

    assert captured["order_flow"] == order_flow
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


# --- incremental / cached backfill (P4) ---------------------------------------
# ``load_snapshots`` re-runs the full 35-day backfill + 6-mo SPY fetch (~24 proxy
# calls). It changes at most once per session-day, so ``refresh`` caches it via
# ``_load_snapshots_cached`` and only recomputes on a NEW local date — the 120 s
# ticks in-between reuse the cached (snaps, spy). The live composite + intraday
# recording still refresh every gated tick.

def _reset_snapshots_cache():
    handlers._SNAPSHOTS.update(date=None, snaps=None, spy=None)


def test_backfill_cached_across_same_day_refreshes(monkeypatch):
    """Repeated refreshes on the SAME local date call the heavy backfill ONCE;
    the intervening ticks reuse the cached (snaps, spy)."""
    _reset_snapshots_cache()
    bus = Bus(fake=True)
    snaps = [{"date": "2026-06-15", "composite": {"total_score": "7.80"}}]
    spy = [1.0, 2.0]
    calls = _patch_compute(monkeypatch, live=_fake_live(), snaps=snaps, spy=spy)

    # Count real backfill invocations (the patch above replaces load_snapshots).
    load_calls = {"n": 0}

    def _load(*a, **k):
        load_calls["n"] += 1
        return snaps, spy

    monkeypatch.setattr(handlers.compute, "load_snapshots", _load)
    # Freeze the session date so all three refreshes land on the same day.
    monkeypatch.setattr(handlers, "_session_date", lambda: "2026-06-15")

    handlers.refresh(bus, with_sectors=False)
    handlers.refresh(bus, with_sectors=False)
    handlers.refresh(bus, with_sectors=False)

    assert load_calls["n"] == 1  # heavy backfill computed once, not per tick
    # Composite still written each cycle (live composite is not cached).
    assert bus.cache_get("cache:sentiment:composite") is not None
    assert calls["bridge"] == 3  # bridge dual-write still every cycle
    _reset_snapshots_cache()


def test_backfill_recomputed_on_new_session_day(monkeypatch):
    """A new local session date invalidates the cache and recomputes the backfill."""
    _reset_snapshots_cache()
    bus = Bus(fake=True)
    snaps = [{"date": "d", "composite": {"total_score": "7.80"}}]
    spy = [1.0]
    _patch_compute(monkeypatch, live=_fake_live(), snaps=snaps, spy=spy)

    load_calls = {"n": 0}

    def _load(*a, **k):
        load_calls["n"] += 1
        return snaps, spy

    monkeypatch.setattr(handlers.compute, "load_snapshots", _load)

    dates = iter(["2026-06-15", "2026-06-15", "2026-06-16"])
    monkeypatch.setattr(handlers, "_session_date", lambda: next(dates))

    handlers.refresh(bus, with_sectors=False)  # 06-15: compute
    handlers.refresh(bus, with_sectors=False)  # 06-15: cached
    handlers.refresh(bus, with_sectors=False)  # 06-16: recompute

    assert load_calls["n"] == 2
    _reset_snapshots_cache()


def test_history_cache_set_skips_unchanged_version(monkeypatch):
    """The history view is written with skip_unchanged=True, so a byte-identical
    (snaps, spy) payload across cycles does NOT bump the version (no needless
    GUI repaint). The version is stable across same-day refreshes."""
    _reset_snapshots_cache()
    bus = Bus(fake=True)
    snaps = [{"date": "2026-06-15", "composite": {"total_score": "7.80"}}]
    spy = [1.0, 2.0]
    _patch_compute(monkeypatch, live=_fake_live(), snaps=snaps, spy=spy)
    monkeypatch.setattr(handlers.compute, "load_snapshots",
                        lambda *a, **k: (snaps, spy))
    monkeypatch.setattr(handlers, "_session_date", lambda: "2026-06-15")

    handlers.refresh(bus, with_sectors=False)
    v1 = bus.cache_version("cache:sentiment:history")
    handlers.refresh(bus, with_sectors=False)
    v2 = bus.cache_version("cache:sentiment:history")

    assert v1 == v2  # identical history payload -> no version bump
    _reset_snapshots_cache()


def test_handle_command_refresh_rotation(monkeypatch):
    bus = Bus(fake=True)
    seen = {"rot": 0}
    monkeypatch.setattr(handlers, "refresh_rotation",
                        lambda b: seen.__setitem__("rot", seen["rot"] + 1))
    monkeypatch.setattr(handlers, "refresh", lambda *a, **k: None)

    handlers.handle_command(bus, Command(type="refresh_rotation"))
    assert seen["rot"] == 1


# --- 2-min intraday sentiment+trend recording (Task 3) ------------------------

def test_intraday_values_extracts_sentiment_and_trend():
    live = {"composite": {"total_score": "6.30"}}
    assert handlers._intraday_values(live, {"score": 71.5}) == (6.30, 71.5)


def test_intraday_values_none_when_no_live():
    assert handlers._intraday_values(None, {"score": 70}) is None


def test_intraday_values_none_when_trend_missing_score():
    assert handlers._intraday_values({"composite": {"total_score": "6.0"}}, {}) is None


def test_intraday_values_prefers_smoothed_trend_score():
    live = {"composite": {"total_score": "6.0"}}
    assert handlers._intraday_values(live, {"score": 70.0, "smoothed_score": 68.5}) == (6.0, 68.5)


def test_refresh_records_and_publishes_intraday_during_rth(monkeypatch):
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(total="6.00"), snaps=[{"x": 1}], spy=[1.0])
    monkeypatch.setattr(handlers, "_maybe_recompute_trend", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    monkeypatch.setattr(handlers, "_intraday_conn",
                        handlers.intraday_history_db.connect(":memory:"))
    handlers._TREND["trend"] = {"score": 70.0}

    sub = bus.subscribe("events:sentiment:intraday_history")
    handlers.refresh(bus, with_sectors=False)
    msg = sub.get_message(timeout=1.0)
    sub.close()

    view = bus.cache_get("cache:sentiment:intraday_history")
    assert view is not None
    pts = view.payload["points"]
    assert pts and pts[-1]["sentiment"] == 6.0 and pts[-1]["trend"] == 70.0
    assert msg is not None and "version" in msg


def test_refresh_skips_intraday_off_hours(monkeypatch):
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(), snaps=[{"x": 1}], spy=[1.0])
    monkeypatch.setattr(handlers, "_maybe_recompute_trend", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: False)
    monkeypatch.setattr(handlers, "_intraday_conn",
                        handlers.intraday_history_db.connect(":memory:"))
    handlers._TREND["trend"] = {"score": 70.0}

    handlers.refresh(bus, with_sectors=False)
    view = bus.cache_get("cache:sentiment:intraday_history")
    assert view is None or not view.payload.get("points")


def test_refresh_intraday_record_failure_non_fatal(monkeypatch):
    """A DB failure recording the intraday point is logged and does NOT abort the
    core refresh — the composite view is still written."""
    bus = Bus(fake=True)
    _patch_compute(monkeypatch, live=_fake_live(), snaps=[{"x": 1}], spy=[1.0])
    monkeypatch.setattr(handlers, "_maybe_recompute_trend", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    monkeypatch.setattr(handlers, "_intraday_conn",
                        handlers.intraday_history_db.connect(":memory:"))
    handlers._TREND["trend"] = {"score": 70.0}

    def _boom(*a, **k):
        raise RuntimeError("insert exploded")

    monkeypatch.setattr(handlers.intraday_history_db, "insert_point", _boom)

    handlers.refresh(bus, with_sectors=False)  # must not raise

    comp = bus.cache_get("cache:sentiment:composite")
    assert comp is not None  # core refresh completed despite the intraday failure


# --- sector P/C daily persistence (Part C) ------------------------------------
def _mem_sector_pcr(monkeypatch):
    conn = handlers.sector_pcr_history_db.connect(":memory:")
    monkeypatch.setattr(handlers, "_sector_pcr_conn", conn)
    return conn


def test_record_sector_pcr_records_value(monkeypatch):
    conn = _mem_sector_pcr(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    handlers._record_sector_pcr({"sector_pcr": 0.83})
    rows = conn.execute("SELECT pcr FROM sector_pcr").fetchall()
    assert rows and rows[-1][0] == 0.83


def test_record_sector_pcr_skips_none(monkeypatch):
    conn = _mem_sector_pcr(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    handlers._record_sector_pcr({"sector_pcr": None})
    assert conn.execute("SELECT COUNT(*) FROM sector_pcr").fetchone()[0] == 0


def test_record_sector_pcr_skips_off_hours(monkeypatch):
    conn = _mem_sector_pcr(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: False)
    handlers._record_sector_pcr({"sector_pcr": 0.9})
    assert conn.execute("SELECT COUNT(*) FROM sector_pcr").fetchone()[0] == 0


def test_record_sector_pcr_failure_non_fatal(monkeypatch):
    _mem_sector_pcr(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("record exploded")

    monkeypatch.setattr(handlers.sector_pcr_history_db, "record", _boom)
    handlers._record_sector_pcr({"sector_pcr": 0.9})  # must not raise


def test_refresh_calls_record_sector_pcr(monkeypatch):
    bus = Bus(fake=True)
    live = _fake_live()
    live["sector_pcr"] = 0.77
    _patch_compute(monkeypatch, live=live, snaps=[{"x": 1}], spy=[1.0])
    monkeypatch.setattr(handlers, "_maybe_recompute_trend", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    monkeypatch.setattr(handlers, "_intraday_conn",
                        handlers.intraday_history_db.connect(":memory:"))
    conn = _mem_sector_pcr(monkeypatch)

    handlers.refresh(bus, with_sectors=False)

    rows = conn.execute("SELECT pcr FROM sector_pcr").fetchall()
    assert rows and rows[-1][0] == 0.77


# --- daily committed market-state persistence (validation record) -------------
def _mem_market_state(monkeypatch):
    conn = handlers.market_state_history_db.connect(":memory:")
    monkeypatch.setattr(handlers, "_get_market_state_conn", lambda: conn)
    return conn


def _trend(**over):
    t = {"state": "bullish", "smoothed_score": 72.0, "aggression": 0.4,
         "evidence": ["e1", "e2"], "sub_scores": {"price": 60.0},
         "aggression_confidence": 0.8}
    t.update(over)
    return t


def test_record_market_state_records_row(monkeypatch):
    conn = _mem_market_state(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    handlers._record_market_state(_trend())
    rows = handlers.market_state_history_db.load_recent(conn, n_days=60)
    assert len(rows) == 1
    row = rows[0]
    assert row["committed_state"] == "bullish"
    assert row["direction_score"] == 72.0
    assert row["aggression"] == 0.4
    assert row["components"] == {
        "evidence": ["e1", "e2"], "sub_scores": {"price": 60.0},
        "aggression_confidence": 0.8}


def test_record_market_state_skips_falsy_state(monkeypatch):
    conn = _mem_market_state(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    handlers._record_market_state(_trend(state=None))
    assert conn.execute("SELECT COUNT(*) FROM market_state").fetchone()[0] == 0


def test_record_market_state_skips_off_hours(monkeypatch):
    conn = _mem_market_state(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: False)
    handlers._record_market_state(_trend())
    assert conn.execute("SELECT COUNT(*) FROM market_state").fetchone()[0] == 0


def test_record_market_state_failure_non_fatal(monkeypatch):
    _mem_market_state(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("record exploded")

    monkeypatch.setattr(handlers.market_state_history_db, "record", _boom)
    handlers._record_market_state(_trend())  # must not raise


def test_maybe_recompute_trend_records_market_state(monkeypatch):
    """``_record_market_state`` is called at the end of ``_maybe_recompute_trend``
    with the freshly-updated ``_TREND['trend']`` dict."""
    _reset_trend()
    conn = _mem_market_state(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    bus = Bus(fake=True)

    fresh = _trend(state="bearish", smoothed_score=28.0, aggression=-0.6)
    monkeypatch.setattr(handlers.compute, "compute_intraday_trend",
                        lambda *a, **k: fresh)
    monkeypatch.setattr(handlers.compute, "compute_30d_trend", lambda *a, **k: {})
    monkeypatch.setattr(handlers.compute, "sector_pc_delta", lambda: None)
    monkeypatch.setattr(handlers.time, "monotonic", lambda: 12000.0)

    handlers._maybe_recompute_trend(bus)

    rows = handlers.market_state_history_db.load_recent(conn, n_days=60)
    assert len(rows) == 1
    assert rows[0]["committed_state"] == "bearish"
    assert rows[0]["direction_score"] == 28.0
    assert rows[0]["aggression"] == -0.6
    _reset_trend()


# --- market-state transition push alert ---------------------------------------

def _stub_trend_recompute(monkeypatch, new_state):
    """Make ``_maybe_recompute_trend`` recompute to a given committed state."""
    _mem_market_state(monkeypatch)
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    fresh = {"state": new_state, "state_history": [new_state],
             "smoothed_score": 30.0, "aggression": -0.5,
             "description": "desc", "evidence": ["e1"]}
    monkeypatch.setattr(handlers.compute, "compute_intraday_trend",
                        lambda *a, **k: fresh)
    monkeypatch.setattr(handlers.compute, "compute_30d_trend", lambda *a, **k: {})
    monkeypatch.setattr(handlers.compute, "sector_pc_delta", lambda: None)
    monkeypatch.setattr(handlers.time, "monotonic", lambda: 21000.0)
    return fresh


def test_recompute_notifies_on_committed_state_change(monkeypatch):
    _reset_trend()
    handlers._TREND["committed"] = "bullish"  # prior committed
    fresh = _stub_trend_recompute(monkeypatch, "bearish")

    seen = {}
    monkeypatch.setattr(handlers.state_alert, "send_state_transition",
                        lambda old, new, trend, **k: seen.update(
                            old=old, new=new, trend=trend) or True)

    handlers._maybe_recompute_trend(Bus(fake=True))

    assert seen["old"] == "bullish"
    assert seen["new"] == "bearish"
    assert seen["trend"] == fresh
    _reset_trend()


def test_recompute_sends_notify_outside_the_trend_lock(monkeypatch):
    """send_state_transition does Telegram/Discord/SMTP (8-10s timeouts). It must
    run AFTER _TREND_LOCK is released — otherwise a slow flip-day notify holds the
    lock ~25s and blocks a concurrent manual refresh."""
    _reset_trend()
    handlers._TREND["committed"] = "bullish"
    fresh = _stub_trend_recompute(monkeypatch, "bearish")

    lock_free = {}

    def _spy(old, new, trend, **k):
        # If the lock is free, we can acquire it non-blocking → send is outside it.
        got = handlers._TREND_LOCK.acquire(blocking=False)
        lock_free["free"] = got
        if got:
            handlers._TREND_LOCK.release()
        return True

    monkeypatch.setattr(handlers.state_alert, "send_state_transition", _spy)
    handlers._maybe_recompute_trend(Bus(fake=True))
    assert lock_free.get("free") is True   # lock released before the notify ran
    _reset_trend()


def test_recompute_no_notify_when_state_unchanged(monkeypatch):
    _reset_trend()
    handlers._TREND["committed"] = "bullish"
    _stub_trend_recompute(monkeypatch, "bullish")  # same committed

    calls = {"n": 0}
    monkeypatch.setattr(handlers.state_alert, "send_state_transition",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))

    handlers._maybe_recompute_trend(Bus(fake=True))
    assert calls["n"] == 0
    _reset_trend()


def test_recompute_notify_failure_non_fatal(monkeypatch):
    """A ``send_state_transition`` that raises does NOT abort the recompute —
    ``_TREND`` still updates to the new committed state."""
    _reset_trend()
    handlers._TREND["committed"] = "bullish"
    _stub_trend_recompute(monkeypatch, "bearish")

    def _boom(*a, **k):
        raise RuntimeError("notify exploded")

    monkeypatch.setattr(handlers.state_alert, "send_state_transition", _boom)

    handlers._maybe_recompute_trend(Bus(fake=True))  # must not raise

    assert handlers._TREND["committed"] == "bearish"
    assert handlers._TREND["smoothed"] == 30.0
    _reset_trend()
