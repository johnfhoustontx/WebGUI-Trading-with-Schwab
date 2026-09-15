"""The published views carry the stamps."""
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.options_svc import handlers


def _scan_result():
    row = {"id": "a", "symbol": "ORCL", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0,
           "long_strike": 97.5, "width": 2.5, "credit": 0.60, "max_loss": 1.90,
           "spread_bid": 0.55, "spread_ask": 0.65}
    return {"signals_0dte": [], "signals_swing": [row],
            "signals_directional": [{"id": "d", "symbol": "ORCL", "type": "LONG_CALL",
                                     "dte": 2, "net_debit": 300.0, "legs": []}],
            "iv_data": {"ORCL": {"iv_rank": None}}, "timestamp": "t", "errors": [],
            "warnings": []}


def test_rescan_stamps_every_list_before_publishing(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", _scan_result)
    monkeypatch.setattr(handlers.compute, "scan_earnings", lambda s: ("upcoming", "2026-10-09"))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    p = bus.cache_get(handlers.CACHE_SCAN).payload
    swing = p["signals_swing"][0]
    assert swing["ledger_risk_per_contract"] == 190.0
    assert swing["earnings_date"] == "2026-10-09"
    assert swing["iv_rank_known"] is False
    directional = p["signals_directional"][0]
    assert "vol_floor" in directional          # windowed by DTE: 0-DTE floor


def test_a_stamp_failure_never_costs_the_scan(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", _scan_result)
    monkeypatch.setattr(handlers.compute, "stamp_candidate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    assert bus.cache_get(handlers.CACHE_SCAN).payload["signals_swing"]


def test_the_day_union_carries_the_stamps_too(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", _scan_result)
    monkeypatch.setattr(handlers.compute, "scan_earnings", lambda s: ("none_scheduled", None))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    day = bus.cache_get(handlers.CACHE_SCAN_DAY).payload
    assert day["signals_swing"][0]["ledger_risk_per_contract"] == 190.0


def test_directional_floor_follows_the_dte_window(monkeypatch):
    from shared import scanner_config
    reset_fake_bus()
    bus = Bus(fake=True)

    def _result():
        r = _scan_result()
        r["signals_directional"].append({"id": "e", "symbol": "ORCL", "type": "LONG_PUT",
                                         "dte": 9, "net_debit": 300.0, "legs": []})
        return r
    monkeypatch.setattr(handlers.compute, "run_scan", _result)
    monkeypatch.setattr(handlers.compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    rows = {r["id"]: r for r in bus.cache_get(handlers.CACHE_SCAN).payload["signals_directional"]}
    floors = scanner_config.min_iv_rank()
    assert rows["d"]["vol_floor"] == floors.get("0-DTE")
    assert rows["e"]["vol_floor"] == floors.get("SWING")


def test_earnings_are_read_once_per_symbol(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    calls = []

    def _earn(sym):
        calls.append(sym)
        return ("none_scheduled", None)
    monkeypatch.setattr(handlers.compute, "run_scan", _scan_result)
    monkeypatch.setattr(handlers.compute, "scan_earnings", _earn)
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    assert calls == ["ORCL"]


def test_the_finder_answer_carries_the_stamps(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    row = {"id": "f", "symbol": "ORCL", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0,
           "long_strike": 97.5, "width": 2.5, "credit": 0.60, "max_loss": 1.90,
           "iv_rank": None, "daily_em": 2.0}
    monkeypatch.setattr(handlers.compute, "scan_earnings", lambda s: ("upcoming", "2026-10-09"))
    monkeypatch.setattr(handlers.compute, "swing_scan", lambda **kw: {"signals": [row], "view": {}})
    handlers.swing_scan(bus, {"symbol": "ORCL"})
    sig = bus.cache_get(handlers.CACHE_SWING).payload["signals"][0]
    assert sig["ledger_risk_per_contract"] == 190.0
    assert sig["earnings_date"] == "2026-10-09" and sig["earnings_status"] == "upcoming"
    assert sig["iv_rank_known"] is False and sig["em_to_expiry"] is not None


def test_the_income_board_carries_the_stamps_without_touching_its_earnings(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    row = {"id": "i", "symbol": "IREN", "type": "PCS", "trade_type": "INCOME",
           "expiration": "2026-10-30", "dte": 40, "short_strike": 10.0,
           "long_strike": 9.0, "width": 1.0, "credit": 0.30, "max_loss": 0.70,
           "earnings_status": "not_listed", "iv_rank": 55.0,
           "composite_score": 55.0, "net_credit": 30.0}
    monkeypatch.setattr(handlers, "_income_symbols", lambda: ["IREN"])
    monkeypatch.setattr(handlers, "_income_lots", lambda: [])
    monkeypatch.setattr(handlers.compute, "income_scan", lambda symbol, **kw: {"signals": [row]})
    monkeypatch.setattr(handlers, "record_income_signals", lambda cands: None)
    handlers.publish_income(bus)
    cand = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"][0]
    assert cand["ledger_risk_per_contract"] == 70.0
    assert cand["earnings_status"] == "not_listed"
    from shared import scanner_config
    assert cand["vol_floor"] == scanner_config.min_iv_rank().get("INCOME")
