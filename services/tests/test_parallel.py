"""Tests for the services parallel-map helper (I/O-bound proxy fan-out)."""
import threading
import time

from services._parallel import parallel_map


def test_parallel_map_preserves_order():
    assert parallel_map(lambda x: x * 2, [1, 2, 3, 4], workers=3) == [2, 4, 6, 8]


def test_parallel_map_empty_returns_empty():
    assert parallel_map(lambda x: x, [], workers=4) == []


def test_parallel_map_single_item():
    assert parallel_map(lambda x: x + 1, [10], workers=4) == [11]


def test_parallel_map_runs_concurrently():
    lock = threading.Lock()
    state = {"cur": 0, "max": 0}

    def work(i):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        return i

    out = parallel_map(work, range(6), workers=4)
    assert out == list(range(6))
    assert state["max"] >= 2  # genuinely overlapped, not serial


def test_parallel_map_caps_workers_at_item_count():
    # Fewer items than workers must not error (pool sized to item count).
    assert parallel_map(lambda x: x, [1, 2], workers=8) == [1, 2]
