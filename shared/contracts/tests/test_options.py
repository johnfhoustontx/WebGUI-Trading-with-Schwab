import pytest
from shared.contracts.options import ScanResult


def test_scanresult_roundtrip():
    sr = ScanResult(signals_0dte=[{"id": "a", "symbol": "SPY", "composite_score": 7.1}],
                    signals_swing=[], vix_term_structure={"structure": "contango"},
                    timestamp="2026-06-15T12:00:00Z", errors=[], warnings=["w"])
    back = ScanResult.from_json(sr.to_json())
    assert back.signals_0dte[0]["symbol"] == "SPY"
    assert back.vix_term_structure["structure"] == "contango"
    assert back.warnings == ["w"]


def test_scanresult_defaults_empty():
    sr = ScanResult()
    assert sr.signals_0dte == [] and sr.signals_swing == [] and sr.timestamp is None


def test_scanresult_rejects_wrong_type():
    with pytest.raises(Exception):
        ScanResult.from_json('{"signals_0dte": "not-a-list"}')


def test_scan_result_accepts_signals_directional():
    r = ScanResult(signals_0dte=[], signals_swing=[],
                   signals_directional=[{"id": "x", "type": "LONG_CALL"}])
    assert r.signals_directional[0]["type"] == "LONG_CALL"


def test_scan_result_back_compat_without_signals_directional():
    """A payload cached before this field existed must still validate.

    Redis persists cache:options:scan across a service restart, so the new code
    WILL read old payloads.
    """
    r = ScanResult(signals_0dte=[], signals_swing=[])
    assert r.signals_directional == []


def test_matrix_snapshot_roundtrip_and_defaults():
    from shared.contracts.options import MatrixSnapshot
    m = MatrixSnapshot(date="2026-07-20", session_date="2026-07-20", ts="2026-07-20T09:15:00",
                       rows=[{"symbol": "SPY", "signal": "buy"}])
    assert m.rows[0]["symbol"] == "SPY"
    # tolerant defaults so older Redis payloads still validate
    assert MatrixSnapshot().rows == []
    assert MatrixSnapshot().error is None
    dumped = m.to_json()
    assert MatrixSnapshot.from_json(dumped).rows == m.rows
