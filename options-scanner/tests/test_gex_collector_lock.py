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

def test_wait_for_lock_acquires_immediately_when_free(tmp_path):
    p = tmp_path / "g.lock"
    waited = []
    ok = gc.wait_for_lock(
        p, source="gamma_tool", owner="NEW", now_fn=lambda: 1000,
        interrupted=lambda t: (waited.append(t), False)[1], check_interval=30)
    assert ok is True
    assert waited == []                 # no waiting needed
    assert json.loads(p.read_text())["owner"] == "NEW"


def test_wait_for_lock_takes_over_once_foreign_lock_goes_stale(tmp_path):
    p = tmp_path / "g.lock"
    # A previous instance (killed, never released) owns a still-fresh lock.
    gc.acquire_collector_lock(p, source="gamma_tool", owner="OLD", now=1000)
    # now advances: first check still fresh (1100), second check stale (2000).
    nows = iter([1100, 2000])
    waited = []
    ok = gc.wait_for_lock(
        p, source="gamma_tool", owner="NEW", now_fn=lambda: next(nows),
        interrupted=lambda t: (waited.append(t), False)[1],
        check_interval=30, ttl=600)
    assert ok is True
    assert len(waited) == 1             # waited once, then took over
    assert json.loads(p.read_text())["owner"] == "NEW"


def test_wait_for_lock_returns_false_when_interrupted(tmp_path):
    p = tmp_path / "g.lock"
    # A live foreign owner keeps the lock fresh; we must NOT steal it — and
    # when stop is requested we bail out with False.
    gc.acquire_collector_lock(p, source="standalone", owner="LIVE", now=1000)
    ok = gc.wait_for_lock(
        p, source="gamma_tool", owner="NEW", now_fn=lambda: 1100,  # always fresh
        interrupted=lambda t: True, check_interval=30, ttl=600)
    assert ok is False
    assert json.loads(p.read_text())["owner"] == "LIVE"   # not stolen
