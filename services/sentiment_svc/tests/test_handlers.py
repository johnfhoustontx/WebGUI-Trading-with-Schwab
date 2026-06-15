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
                   proxy_up=True):
    calls = {"bridge": 0, "sector_perf": 0}
    monkeypatch.setattr(handlers.compute, "load_snapshots",
                        lambda *a, **k: (snaps, spy))
    monkeypatch.setattr(handlers.compute, "load_live", lambda *a, **k: live)
    monkeypatch.setattr(handlers.compute, "proxy_up", lambda: proxy_up)

    def _sector_perf(_spy):
        calls["sector_perf"] += 1
        return sector

    monkeypatch.setattr(handlers.compute, "load_sector_perf", _sector_perf)

    def _bridge(*a, **k):
        calls["bridge"] += 1

    monkeypatch.setattr(handlers.compute, "build_and_write_bridge", _bridge)
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
    assert msg is not None and "version" in msg
    assert calls["sector_perf"] == 1
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
