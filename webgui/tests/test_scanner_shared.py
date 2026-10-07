"""The one-slot build every visitor on the public origin draws from.

Keyed on object IDENTITY, not equality: ``bus_client.read_shared`` and the
checklist's gated reads hand back the same object until a view's version
moves, so "is this the same object" is exactly "has this input changed".
"""
import threading

import pytest

from pages.options import scanner_shared


@pytest.fixture(autouse=True)
def _empty_slot():
    scanner_shared.reset()
    yield
    scanner_shared.reset()


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _counting():
    calls = []

    def build():
        calls.append(1)
        return {"n": len(calls)}

    return build, calls


def test_the_same_inputs_are_built_once():
    build, calls = _counting()
    day, live = {"date": "x"}, {"timestamp": "y"}
    clock = _Clock()
    first = scanner_shared.get((day, live), build, max_age=300, now=clock)
    second = scanner_shared.get((day, live), build, max_age=300, now=clock)
    assert first is second
    assert len(calls) == 1


def test_an_equal_but_different_object_is_a_new_input():
    """Identity, not equality. A new scan that happens to equal the last one
    is still a new parse, and two payloads comparing equal by value would cost
    a deep compare of megabytes on every call."""
    build, calls = _counting()
    clock = _Clock()
    live = {"timestamp": "y"}
    scanner_shared.get(({"date": "x"}, live), build, max_age=300, now=clock)
    scanner_shared.get(({"date": "x"}, live), build, max_age=300, now=clock)
    assert len(calls) == 2


def test_a_different_number_of_inputs_is_a_new_input():
    build, calls = _counting()
    clock = _Clock()
    day = {"date": "x"}
    scanner_shared.get((day,), build, max_age=300, now=clock)
    scanner_shared.get((day, day), build, max_age=300, now=clock)
    assert len(calls) == 2


def test_a_build_is_reused_until_it_is_max_age_old():
    build, calls = _counting()
    parts = ({"date": "x"},)
    clock = _Clock()
    scanner_shared.get(parts, build, max_age=300, now=clock)
    clock.t += 299.0
    scanner_shared.get(parts, build, max_age=300, now=clock)
    assert len(calls) == 1
    clock.t += 1.0                       # exactly max_age old: no longer fresh
    scanner_shared.get(parts, build, max_age=300, now=clock)
    assert len(calls) == 2


def test_the_age_counts_from_the_rebuild():
    build, calls = _counting()
    parts = ({"date": "x"},)
    clock = _Clock()
    scanner_shared.get(parts, build, max_age=300, now=clock)
    clock.t += 300.0
    scanner_shared.get(parts, build, max_age=300, now=clock)
    clock.t += 299.0
    scanner_shared.get(parts, build, max_age=300, now=clock)
    assert len(calls) == 2


def test_visitors_arriving_together_wait_on_one_build():
    parts = ({"date": "x"},)
    started, release = threading.Event(), threading.Event()
    calls, results = [], []

    def slow():
        calls.append(1)
        started.set()
        assert release.wait(5), "the test never released the build"
        return {"built": True}

    def visitor():
        results.append(scanner_shared.get(parts, slow, max_age=300))

    first = threading.Thread(target=visitor)
    first.start()
    assert started.wait(5), "the first build never started"
    second = threading.Thread(target=visitor)
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert len(calls) == 1
    assert len(results) == 2 and results[0] is results[1]


def test_a_failed_build_is_not_remembered():
    parts = ({"date": "x"},)
    clock = _Clock()

    def broken():
        raise ValueError("no")

    with pytest.raises(ValueError):
        scanner_shared.get(parts, broken, max_age=300, now=clock)
    build, calls = _counting()
    assert scanner_shared.get(parts, build, max_age=300, now=clock) == {"n": 1}
    assert len(calls) == 1


def test_a_failed_rebuild_keeps_the_last_good_build_out_of_reach():
    """After a failure the slot does not go on serving the OLD build under the
    NEW inputs: the next caller builds again."""
    clock = _Clock()
    build, calls = _counting()
    old = ({"date": "x"},)
    scanner_shared.get(old, build, max_age=300, now=clock)
    new = ({"date": "y"},)

    def broken():
        raise ValueError("no")

    with pytest.raises(ValueError):
        scanner_shared.get(new, broken, max_age=300, now=clock)
    scanner_shared.get(new, build, max_age=300, now=clock)
    assert len(calls) == 2
