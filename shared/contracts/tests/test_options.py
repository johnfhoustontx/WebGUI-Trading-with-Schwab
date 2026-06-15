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
