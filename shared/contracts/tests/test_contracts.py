import pytest
from shared.contracts.envelope import CacheEnvelope, Command
from shared.contracts.sentiment import CompositeSnapshot

def test_envelope_roundtrip():
    env = CacheEnvelope(version=3, ts="2026-06-15T12:00:00Z", payload={"a": 1})
    raw = env.to_json()
    assert CacheEnvelope.from_json(raw) == env

def test_command_roundtrip():
    cmd = Command(type="rescan", args={"force": True})
    assert Command.from_json(cmd.to_json()).type == "rescan"

def test_command_defaults_empty_args():
    cmd = Command(type="refresh")
    assert cmd.args == {}

def test_composite_roundtrip():
    snap = CompositeSnapshot(total=7.8, bias="Bullish", components={"vix_complex": 5.0})
    assert CompositeSnapshot.from_json(snap.to_json()).total == 7.8

def test_composite_rejects_malformed():
    with pytest.raises(Exception):
        CompositeSnapshot.from_json('{"total": "not-a-number"}')
