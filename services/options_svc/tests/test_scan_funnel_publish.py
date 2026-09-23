"""``cache:options:scan_funnel`` — the per-symbol account of why a symbol did or
did not produce a Market Scanner signal.

Its OWN view, not another field on ``cache:options:scan``: the ``ScanResult``
projection would drop it, and every scan reader — the Scanner page, the day
union — would otherwise pay for bytes it never shows.

The invariant these tests exist for is that a funnel fault can never cost the
scan. It is published AFTER the scan and the day union, inside its own guard,
because the funnel is instrumentation and the signals are the product.
"""
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.options_svc import handlers


def _funnel():
    return {
        "SPY": {"price": 500.0, "iv_rank": 100, "earnings_date": None,
                "stop": None,
                "buckets": {"0DTE": {"chain": True, "strikes": {"delta_pass": 54},
                                     "spreads": {"built": 8, "emitted": 8}},
                            "SWING": {"chain": True, "strikes": {"delta_pass": 54},
                                      "spreads": {"built": 10, "emitted": 8}},
                            "DIRECTIONAL": {"built": 8, "emitted": 2}}},
        "NOPE": {"price": None, "iv_rank": None, "earnings_date": None,
                 "stop": "no_quote", "buckets": {}},
    }


def _scan_result(funnel=None, ts="2026-09-15T08:00:00"):
    row = {"id": "a", "symbol": "SPY", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0,
           "long_strike": 97.5, "width": 2.5, "credit": 0.60, "max_loss": 1.90}
    return {"signals_0dte": [], "signals_swing": [row], "signals_directional": [],
            "iv_data": {"SPY": {"iv_rank": 100}}, "timestamp": ts,
            "errors": [], "warnings": [],
            "funnel": _funnel() if funnel is None else funnel}


def _rescan(monkeypatch, funnel=None):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_result(funnel))
    monkeypatch.setattr(handlers.compute, "scan_earnings",
                        lambda s: ("none_scheduled", None))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    return bus


def test_rescan_publishes_the_funnel_to_its_own_view(monkeypatch):
    bus = _rescan(monkeypatch)
    payload = bus.cache_get(handlers.CACHE_SCAN_FUNNEL).payload
    assert payload["timestamp"] == "2026-09-15T08:00:00"
    assert sorted(payload["symbols"]) == ["NOPE", "SPY"]
    assert payload["symbols"]["SPY"]["buckets"]["0DTE"]["spreads"]["emitted"] == 8
    assert payload["symbols"]["NOPE"]["stop"] == "no_quote"


def test_the_funnel_stays_out_of_the_scan_view(monkeypatch):
    """The reason it is a separate key at all — ``ScanResult`` does not declare
    it, so the projection drops it and no scan reader pays for it."""
    bus = _rescan(monkeypatch)
    assert "funnel" not in bus.cache_get(handlers.CACHE_SCAN).payload
    assert "funnel" not in bus.cache_get(handlers.CACHE_SCAN_DAY).payload


def test_a_malformed_funnel_degrades_without_losing_the_scan(monkeypatch):
    """A list where a mapping belongs. The contract refuses it, the guard counts
    it, and the signals — the thing the scan is FOR — are published regardless."""
    calls = []
    monkeypatch.setattr(handlers._degrade, "degraded",
                        lambda area, **kw: calls.append(area))
    bus = _rescan(monkeypatch, funnel=["not", "a", "mapping"])
    assert "options.scan_funnel" in calls
    assert bus.cache_get(handlers.CACHE_SCAN).payload["signals_swing"]
    assert bus.cache_get(handlers.CACHE_SCAN_FUNNEL) is None


def test_a_scan_with_no_funnel_publishes_an_empty_one(monkeypatch):
    """``collect_funnel=False`` — and any engine older than the funnel — yields no
    key at all. An empty view is a true statement ("nothing was collected"); a
    degrade would be a false one."""
    bus = _rescan(monkeypatch, funnel={})
    assert bus.cache_get(handlers.CACHE_SCAN_FUNNEL).payload["symbols"] == {}


def test_every_republish_bumps_the_version_because_the_timestamp_moves(monkeypatch):
    """⚠ ``skip_unchanged`` CANNOT suppress this view, and the code says so.

    ``ScanFunnel.timestamp`` is the scan's own stamp, fresh on every pass, so the
    payload is never byte-identical and the flag can never fire — it is kept as
    the safe default on a republisher, the same thing ``publish_income``
    documents about its own ``ts``. The timestamp STAYS: ``funnel_view.stale_note``
    compares it with the live scan's to print "From an earlier scan."

    This test replaced one that pinned the opposite and passed only because the
    fixture held the timestamp still — a suppression a future reader would have
    believed in and built on.
    """
    bus = _rescan(monkeypatch)
    first = bus.cache_version(handlers.CACHE_SCAN_FUNNEL)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan_result(ts="2026-09-15T08:15:00"))
    handlers.rescan(bus)
    assert bus.cache_version(handlers.CACHE_SCAN_FUNNEL) > first
    assert (bus.cache_get(handlers.CACHE_SCAN_FUNNEL).payload["timestamp"]
            == "2026-09-15T08:15:00")


def test_a_changed_funnel_reaches_the_view(monkeypatch):
    """The counters themselves are republished, not only the stamp."""
    bus = _rescan(monkeypatch)
    moved = _funnel()
    moved["SPY"]["buckets"]["0DTE"]["spreads"]["emitted"] = 0
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: _scan_result(moved))
    handlers.rescan(bus)
    payload = bus.cache_get(handlers.CACHE_SCAN_FUNNEL).payload
    assert payload["symbols"]["SPY"]["buckets"]["0DTE"]["spreads"]["emitted"] == 0


def test_the_view_keys_are_the_ones_the_page_will_read(monkeypatch):
    assert handlers.CACHE_SCAN_FUNNEL == "cache:options:scan_funnel"
    assert handlers.EVENT_SCAN_FUNNEL == "events:options:scan_funnel"
