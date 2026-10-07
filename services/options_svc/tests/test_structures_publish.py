"""The Market Scanner's structure lists on the scan view and the day union.

``scanner_engine.run_full_scan`` builds structures other than credit spreads
into ``structures_0dte`` / ``structures_swing``. These tests pin that the two
lists survive the ``ScanResult`` projection, are stamped for the checklist, join
the day union, and reach NOTHING ELSE: not the phone push, not the Opportunity
Board's scan count.

Design: docs/plans/2026-10-06-scanner-multi-structure-design.md.
"""
import pytest

from shared.bus import Bus
from shared.contracts.options import ScanResult
from shared import scanner_config
from services.options_svc import compute, handlers


def _leg(kind, side, strike, exp="2026-11-06", qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": exp,
            "qty": qty, "mark": 5.4, "delta": 0.5, "theta": -0.1, "vega": 0.2,
            "gamma": 0.02, "iv": 28.0, "bid": 5.3, "ask": 5.5,
            "volume": 500, "oi": 2000}


def _row(row_id="s1", structure="LONG_STRADDLE", symbol="SPY", dte=7,
         exp="2026-11-06"):
    return {"id": row_id, "type": structure, "symbol": symbol, "dte": dte,
            "group": "STRADDLE", "family": "VOLATILITY",
            "strategy_label": "Long Straddle", "bias": "neutral",
            "expiration": exp,
            "legs": [_leg("call", "long", 500.0, exp), _leg("put", "long", 500.0, exp)],
            "net_debit": 1080.0, "net_credit": None, "max_profit": None,
            "max_loss": 1082.6, "capital": 1082.6, "breakevens": [489.2, 510.8],
            "unbounded": True, "unbounded_profit": True, "unbounded_loss": False,
            "rr": None, "pop_pct": 41.0, "net_delta": 0.0, "net_theta": -0.2,
            "net_vega": 0.4, "net_gamma": 0.04, "underlying_price": 500.0,
            "composite_score": 55.0, "grade": "Marginal", "iv_rank": 50}


def _scan(zero=(), swing=(), credit=()):
    return {"signals_0dte": list(credit), "signals_swing": [],
            "signals_directional": [],
            "structures_0dte": list(zero), "structures_swing": list(swing),
            "vix_term_structure": {}, "timestamp": "2026-10-06T10:02:00",
            "errors": [], "warnings": [],
            "iv_data": {"SPY": {"iv_rank": 50}, "QQQ": {"iv_rank": None}}}


# ── the contract ────────────────────────────────────────────────────────────

def test_a_scan_view_cached_before_the_lists_existed_still_validates():
    """Redis keeps cache:options:scan across a restart."""
    old = ScanResult(signals_0dte=[{"id": "a"}], timestamp="2026-10-05T10:00:00")
    dumped = old.model_dump()
    assert dumped["structures_0dte"] == [] and dumped["structures_swing"] == []


def test_the_contract_carries_both_lists_through_a_dump():
    snap = ScanResult(structures_0dte=[_row("a")], structures_swing=[_row("b")])
    dumped = snap.model_dump()
    assert dumped["structures_0dte"][0]["id"] == "a"
    assert dumped["structures_swing"][0]["legs"][0]["strike"] == 500.0


def test_a_list_that_is_not_a_list_is_refused():
    with pytest.raises(Exception):
        ScanResult(structures_swing="oops")


# ── the publish ─────────────────────────────────────────────────────────────

def test_rescan_publishes_both_lists(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(zero=[_row("z1", dte=1)], swing=[_row("w1")]))
    handlers.rescan(bus)
    payload = bus.cache_get("cache:options:scan").payload
    assert [r["id"] for r in payload["structures_0dte"]] == ["z1"]
    assert [r["id"] for r in payload["structures_swing"]] == ["w1"]
    assert payload["structures_swing"][0]["legs"][1]["kind"] == "put"


def test_rescan_without_the_lists_still_publishes(monkeypatch):
    """New service code against an engine that does not build them."""
    bus = Bus(fake=True)
    result = _scan(credit=[{"id": "c1", "symbol": "SPY", "type": "PCS"}])
    del result["structures_0dte"], result["structures_swing"]
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: result)
    handlers.rescan(bus)
    payload = bus.cache_get("cache:options:scan").payload
    assert payload["structures_0dte"] == [] and payload["structures_swing"] == []
    assert payload["signals_0dte"][0]["id"] == "c1"


def test_a_malformed_structure_list_caches_nothing(monkeypatch):
    bus = Bus(fake=True)
    bad = _scan()
    bad["structures_swing"] = "not-a-list"
    monkeypatch.setattr(handlers.compute, "run_scan", lambda: bad)
    with pytest.raises(Exception):
        handlers.rescan(bus)
    assert bus.cache_get("cache:options:scan") is None


def test_each_list_is_stamped_with_its_own_windows_floor(monkeypatch):
    """The checklist reads ``vol_floor``; the 0-DTE and Swing floors differ."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(zero=[_row("z1", dte=1)], swing=[_row("w1")]))
    handlers.rescan(bus)
    payload = bus.cache_get("cache:options:scan").payload
    floors = scanner_config.min_iv_rank()
    assert floors["0-DTE"] != floors["SWING"]                     # vacuity
    assert payload["structures_0dte"][0]["vol_floor"] == floors["0-DTE"]
    assert payload["structures_swing"][0]["vol_floor"] == floors["SWING"]
    for key in ("structures_0dte", "structures_swing"):
        row = payload[key][0]
        assert row["iv_rank_known"] is True
        assert "ledger_risk_basis" in row and "earnings_status" in row


def test_an_unknown_iv_rank_is_stamped_as_unknown(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(swing=[_row("q1", symbol="QQQ")]))
    handlers.rescan(bus)
    row = bus.cache_get("cache:options:scan").payload["structures_swing"][0]
    assert row["iv_rank_known"] is False


def test_structures_never_reach_the_phone_push(monkeypatch):
    bus = Bus(fake=True)
    credit = {"id": "c1", "symbol": "SPY", "type": "PCS", "short_strike": 500,
              "long_strike": 495, "expiration": "2026-11-06", "composite_score": 80}
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(zero=[_row("z1", dte=1)], swing=[_row("w1")],
                                      credit=[credit]))
    pushed = []
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda bus, sigs, **kw: pushed.extend(s["id"] for s in sigs))
    handlers.rescan(bus)
    assert pushed == ["c1"]


# ── the day union ───────────────────────────────────────────────────────────

def test_the_day_union_carries_both_lists(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(zero=[_row("z1", dte=1)], swing=[_row("w1")]))
    handlers.rescan(bus)
    day = bus.cache_get("cache:options:scan_day").payload
    assert [r["id"] for r in day["structures_0dte"]] == ["z1"]
    assert [r["id"] for r in day["structures_swing"]] == ["w1"]
    assert day["structures_swing"][0]["live"] is True
    assert day["structures_swing"][0]["setup_key"] == "SPY|LONG_STRADDLE|2026-11-06"
    assert "SPY|LONG_STRADDLE|2026-11-06" in day["setups"]


def test_a_structure_that_stops_qualifying_is_carried_as_dropped(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(swing=[_row("w1")]))
    handlers.rescan(bus)
    monkeypatch.setattr(handlers.compute, "run_scan",
                        lambda: _scan(swing=[_row("w2", structure="LONG_STRANGLE")]))
    handlers.rescan(bus)
    live = bus.cache_get("cache:options:scan").payload["structures_swing"]
    day = {r["id"]: r for r in
           bus.cache_get("cache:options:scan_day").payload["structures_swing"]}
    assert [r["id"] for r in live] == ["w2"]          # the live key is replaced
    assert set(day) == {"w1", "w2"}                   # the day key accumulates
    assert day["w1"]["live"] is False and day["w1"]["stale_since"]
    assert day["w2"]["live"] is True and day["w2"]["stale_since"] is None


def test_merge_day_signals_lists_are_the_five(monkeypatch):
    out = compute.merge_day_signals(None, _scan(swing=[_row("w1")]), "2026-10-06")
    for key in ("signals_0dte", "signals_swing", "signals_directional",
                "structures_0dte", "structures_swing"):
        assert isinstance(out[key], list), key


def test_the_opportunity_board_does_not_count_structures():
    """Its scan count is the credit and single-leg lists', as before."""
    day = {"date": "2026-10-06",
           "signals_0dte": [{"symbol": "SPY"}],
           "structures_0dte": [{"symbol": "SPY"}, {"symbol": "QQQ"}],
           "structures_swing": [{"symbol": "IWM"}]}
    assert compute._count_scan_signals(day, "2026-10-06") == {"SPY": 1}


# ── the Strategy Finder builds ratio spreads too (2026-10-07) ───────────────

def test_ratio_is_one_of_the_finders_build_groups():
    assert "RATIO" in compute._SWING_FAMILIES
    # The seven it had keep their order; the new one is appended.
    assert compute._SWING_FAMILIES[:7] == (
        "DIRECTIONAL", "VERTICAL", "NEUTRAL", "STRADDLE", "BUTTERFLY",
        "CALENDAR", "STOCK")
