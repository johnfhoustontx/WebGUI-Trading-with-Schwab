import json
import time
from pathlib import Path

import gex_collector as gc


def test_read_lock_missing_returns_none(tmp_path):
    assert gc.read_lock(tmp_path / "nope.lock") is None


def test_read_lock_corrupt_returns_none(tmp_path):
    p = tmp_path / "c.lock"
    p.write_text("not json{{{")
    assert gc.read_lock(p) is None


def test_is_lock_fresh_true_within_ttl():
    assert gc.is_lock_fresh({"heartbeat": 1000}, now=1300, ttl=600) is True


def test_is_lock_fresh_false_when_stale():
    assert gc.is_lock_fresh({"heartbeat": 1000}, now=2000, ttl=600) is False


def test_acquire_when_absent_writes_lock(tmp_path):
    p = tmp_path / "g.lock"
    assert gc.acquire_collector_lock(p, source="gamma_tool", owner="123",
                                     now=1000) is True
    data = json.loads(p.read_text())
    assert data["owner"] == "123" and data["source"] == "gamma_tool"
    assert data["heartbeat"] == 1000


def test_acquire_defers_when_fresh_other_owner(tmp_path):
    p = tmp_path / "g.lock"
    gc.acquire_collector_lock(p, source="standalone", owner="A", now=1000)
    # Different owner, still inside LOCK_TTL_SEC -> defer. Derived from the TTL:
    # the fixed +200s was written when LOCK_TTL_SEC was 240 (POLL_INTERVAL_MIN=2)
    # and silently became a STALE lock - i.e. the opposite test - when the
    # collector moved to 1-minute polls and the TTL halved to 120.
    still_fresh = 1000 + gc.LOCK_TTL_SEC // 2
    assert gc.acquire_collector_lock(p, source="gamma_tool", owner="B",
                                     now=still_fresh) is False


def test_acquire_takes_over_stale_lock(tmp_path):
    p = tmp_path / "g.lock"
    gc.acquire_collector_lock(p, source="standalone", owner="A", now=1000)
    assert gc.acquire_collector_lock(p, source="gamma_tool", owner="B",
                                     now=9999) is True
    assert json.loads(p.read_text())["owner"] == "B"


def test_acquire_reacquire_same_owner_ok(tmp_path):
    p = tmp_path / "g.lock"
    gc.acquire_collector_lock(p, source="gamma_tool", owner="B", now=1000)
    assert gc.acquire_collector_lock(p, source="gamma_tool", owner="B",
                                     now=1100) is True


def test_touch_updates_heartbeat(tmp_path):
    p = tmp_path / "g.lock"
    gc.acquire_collector_lock(p, source="gamma_tool", owner="B", now=1000)
    gc.touch_lock(p, source="gamma_tool", owner="B", now=1300)
    assert json.loads(p.read_text())["heartbeat"] == 1300


def test_release_only_when_owner(tmp_path):
    p = tmp_path / "g.lock"
    gc.acquire_collector_lock(p, source="gamma_tool", owner="B", now=1000)
    gc.release_lock(p, owner="OTHER")   # not owner -> no-op
    assert p.exists()
    gc.release_lock(p, owner="B")       # owner -> deletes
    assert not p.exists()


# ── wait_for_lock: take over an orphaned (killed-instance) lock on restart ──


