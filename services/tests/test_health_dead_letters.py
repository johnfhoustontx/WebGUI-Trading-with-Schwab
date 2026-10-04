"""/health never raises on the dead-letter count (a follow-up to audit AR-07).

The count was read straight off the bus inside the health route, so a bus that
could not answer it took /health down with an exception. `tests/` caught it the
day that suite was added to CI.
"""
from services import _scaffold


class _Bus:
    def __init__(self, counts):
        self._counts = counts

    def dead_letter_len(self, stream):
        return self._counts[stream]


def test_the_total_is_the_sum_over_every_stream():
    bus = _Bus({"cmd:options": 2, "cmd:finder_public": 1})
    assert _scaffold._dead_letter_total(bus, ["cmd:options", "cmd:finder_public"]) == 3


def test_a_bus_that_cannot_count_reads_as_unknown_not_zero():
    assert _scaffold._dead_letter_total(object(), ["cmd:options"]) is None


def test_a_failing_count_reads_as_unknown():
    class _Down:
        def dead_letter_len(self, stream):
            raise ConnectionError("redis down")

    assert _scaffold._dead_letter_total(_Down(), ["cmd:options"]) is None
