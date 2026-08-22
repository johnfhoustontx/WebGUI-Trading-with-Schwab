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


def test_net_premium_snapshot_accepts_nested_series():
    from shared.contracts.options import NetPremiumSnapshot

    snap = NetPremiumSnapshot(
        session_date="2026-08-05",
        series={"SPY": [[1, 10.0, 4.0]]},
    )
    assert snap.series["SPY"][0][2] == 4.0
    assert snap.error is None


def test_net_premium_snapshot_defaults_every_field():
    """A payload cached before a field existed must still validate — Redis keeps
    cache:options:net_premium across a service restart."""
    from shared.contracts.options import NetPremiumSnapshot

    snap = NetPremiumSnapshot()
    assert snap.series == {} and snap.session_date is None


def test_net_premium_snapshot_survives_json_round_trip():
    """Surviving Bus.cache_set's JSON encoding is this contract's whole job.

    The round trip is deliberately NOT identity: JSON has no tuple type, so tuple
    rows come back as lists. ``load_flow_series`` — which Task 5's
    ``compute.build_net_premium`` reads — yields tuples, so this pins the
    invariant the page depends on: whatever goes in, the page always receives
    LISTS, which is what makes positional ``row[1]``/``row[2]`` access valid.
    """
    from shared.contracts.options import NetPremiumSnapshot

    snap = NetPremiumSnapshot(
        session_date="2026-08-05",
        ts="2026-08-05T09:15:00",
        series={"SPY": [[1, 10.0, 4.0]], "BIG10": [(2, 20.0, 8.0)]},
    )
    back = NetPremiumSnapshot.from_json(snap.to_json())
    assert back.session_date == "2026-08-05"
    assert back.series["SPY"] == [[1, 10.0, 4.0]]     # lists survive unchanged
    assert back.series["BIG10"] == [[2, 20.0, 8.0]]   # tuples normalize to lists


def test_matrix_snapshot_normalises_date_objects():
    """build_matrix passes ``session_date`` through from
    ``scheduler.active_session_date()``, which returns a ``datetime.date``
    OBJECT - json.dumps stringified it on the way into Redis, so the wire format
    was always a string and the ``str`` annotation looked right. Validating the
    IN-MEMORY payload exposed the mismatch, so the contract normalises instead of
    rejecting: same wire format, and now the same shape on both sides."""
    from datetime import date, datetime

    from shared.contracts.options import MatrixSnapshot

    m = MatrixSnapshot(date=date(2026, 8, 21),
                       session_date=date(2026, 8, 21),
                       ts=datetime(2026, 8, 21, 9, 15))
    assert m.date == "2026-08-21"
    assert m.session_date == "2026-08-21"
    assert m.ts == "2026-08-21T09:15:00"
    # plain strings still pass through untouched
    assert MatrixSnapshot(session_date="2026-08-21").session_date == "2026-08-21"
