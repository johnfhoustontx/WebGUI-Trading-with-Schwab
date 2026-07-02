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


def test_command_stamps_ts_at_construction():
    """A freshly constructed command carries an ISO-8601 ``ts`` (enqueue time).

    The default_factory fires only when ``ts`` is absent — so a producer that
    doesn't pass it (the common case) still gets a stamp, and it is stamped once
    at enqueue-side construction (``Command(**dict)``)."""
    cmd = Command(type="paper_create")
    assert isinstance(cmd.ts, str) and cmd.ts  # non-empty ISO timestamp
    # Parseable as an aware ISO-8601 datetime.
    from datetime import datetime
    datetime.fromisoformat(cmd.ts)


def test_command_ts_survives_roundtrip():
    """Decoding preserves the ORIGINAL ts — the factory does NOT re-stamp it on
    ``from_json`` (so the age check measures enqueue→dequeue, not decode time)."""
    cmd = Command(type="driver_paper_create", args={"qty": 1})
    original_ts = cmd.ts
    back = Command.from_json(cmd.to_json())
    assert back.ts == original_ts


def test_command_accepts_explicit_none_ts():
    """An OLDER command serialized before the ts field existed (no ts key) decodes
    with ts=None — back-compatible; the age gate treats missing ts as not-stale."""
    back = Command.from_json('{"type": "rescan", "args": {}, "ts": null}')
    assert back.ts is None

def test_composite_roundtrip():
    snap = CompositeSnapshot(total=7.8, bias="Bullish", components={"vix_complex": 5.0})
    assert CompositeSnapshot.from_json(snap.to_json()).total == 7.8

def test_composite_rejects_malformed():
    with pytest.raises(Exception):
        CompositeSnapshot.from_json('{"total": "not-a-number"}')
