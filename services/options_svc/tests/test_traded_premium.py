"""traded_premium: new volume priced once, kept as a running total per strike.

Design: docs/plans/2026-10-09-traded-premium-increment-design.md.
"""
import ast
import pathlib

import numpy as np
import pytest

from services.options_svc import traded_premium as tp


def _c(osi, vol, mark=1.00, **more):
    c = {"symbol": osi, "totalVolume": vol, **more}
    if mark is not None:
        c["mark"] = mark
    return c


def _chain(calls=None, puts=None, spot=500.0):
    """``calls`` / ``puts``: {strike text: [contract, ...]} in one expiration."""
    return {"underlyingPrice": spot,
            "callExpDateMap": {"2026-10-16:11": calls or {}},
            "putExpDateMap": {"2026-10-16:11": puts or {}}}


def _advance(state, chain, ts, late_sec=90):
    return tp.advance(state, chain, ts, late_sec=late_sec)


# ---- the arithmetic -----------------------------------------------------------

def test_the_first_reading_sets_baselines_and_books_nothing():
    s = tp.new_state()
    assert _advance(s, _chain({"100.0": [_c("C1", 1000, 5.0)]}), 60) == 0.0
    assert s["total"] == {} and s["hw"] == {"C1": 1000.0}
    assert s["seeded"] is True and s["since_ts"] == 60 and s["first_vol"] == 1000.0


def test_new_volume_is_priced_once_at_the_mark_it_arrived_at():
    """The design's example: 1,000 contracts at $5.00, then the mark decays."""
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0, 5.0)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 1000, 5.0)]}), 60) == 500_000.0
    for ts, mark in ((120, 2.0), (180, 0.2)):
        assert _advance(s, _chain({"100.0": [_c("C1", 1000, mark)]}), ts) == 0.0
    assert tp.grid(s) == {100.0: {"call": 500_000.0, "put": 0.0, "net": 500_000.0}}


def test_a_contract_first_seen_later_counts_from_zero():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 10)]}), 0)
    booked = _advance(s, _chain({"100.0": [_c("C1", 10)],
                                 "105.0": [_c("C2", 7, 2.0)]}), 60)
    assert booked == 1400.0
    assert tp.grid(s)[105.0]["call"] == 1400.0


def test_a_glitch_read_of_zero_books_nothing_and_cannot_rebook_the_day():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 1000)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 1010)]}), 60)        # 10 x $1 x 100
    assert _advance(s, _chain({"100.0": [_c("C1", 0)]}), 120) == 0.0
    assert _advance(s, _chain({"100.0": [_c("C1", 1010)]}), 180) == 0.0
    assert s["booked"] == 1000.0


def test_volume_with_no_usable_mark_waits_for_one():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 100)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 130, mark=None)]}), 60) == 0.0
    assert s["hw"]["C1"] == 100.0                  # the baseline did not move
    assert _advance(s, _chain({"100.0": [_c("C1", 150, 2.0)]}), 120) == 10_000.0


def test_the_mark_falls_back_to_the_middle_of_the_quote():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    chain = _chain({"100.0": [_c("C1", 10, mark=None, bid=1.0, ask=1.2)]})
    assert _advance(s, chain, 60) == pytest.approx(1100.0)


@pytest.mark.parametrize("mark", [float("inf"), float("nan"), 0, -1.0])
def test_an_unusable_mark_is_never_priced(mark):
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 10, mark=mark)]}), 60) == 0.0
    assert s["total"] == {}


def test_strikes_add_up_across_expirations_and_the_sides_stay_apart():
    def chain(a, b, p):
        return {"underlyingPrice": 500.0,
                "callExpDateMap": {"2026-10-09:4": {"100.0": [a]},
                                   "2026-10-16:11": {"100.0": [b]}},
                "putExpDateMap": {"2026-10-09:4": {"100.0": [p]}}}
    s = tp.new_state()
    _advance(s, chain(_c("A", 0), _c("B", 0), _c("P", 0)), 0)
    assert _advance(s, chain(_c("A", 1, 1.0), _c("B", 2, 3.0), _c("P", 5, 0.5)),
                    60) == 950.0
    assert tp.grid(s) == {100.0: {"call": 700.0, "put": 250.0, "net": 450.0}}


@pytest.mark.parametrize("chain", [None, "x", {}, {"error": "Bad Request"},
                                   {"callExpDateMap": {}, "putExpDateMap": {}},
                                   {"callExpDateMap": 5}])
def test_a_chain_with_no_contracts_changes_nothing(chain):
    """An error body must not count as the first reading: the next real chain
    would then read the whole day's volume as new."""
    s = tp.new_state()
    assert _advance(s, chain, 60) is None
    assert s == tp.new_state()


def test_volume_first_seen_after_a_long_step_is_counted_as_priced_late():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 10)]}), 60)           # one minute on
    _advance(s, _chain({"100.0": [_c("C1", 15)]}), 260)          # 200 s on
    assert (s["booked_vol"], s["late_vol"]) == (15.0, 5.0)


def test_the_same_volume_is_also_priced_at_last_where_there_is_one():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0), _c("C2", 0)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 10, 2.0, last=2.5),
                                  _c("C2", 4, 1.0)]}), 60)
    assert s["booked"] == 2400.0                        # everything, at the mark
    assert (s["last_vol"], s["at_last"], s["at_mark_same"]) == (10.0, 2500.0, 2000.0)


def test_the_chains_own_volume_is_read_apart_from_the_booking():
    """What the day's check sets the booking against."""
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 100)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 130, mark=None)]}), 60)
    assert (s["first_vol"], s["chain_vol"], s["booked_vol"]) == (100.0, 130.0, 0.0)


# ---- resuming a stored total --------------------------------------------------

def test_a_resumed_total_lands_in_the_cell_new_volume_uses():
    """The store keeps strikes as float32: 17.63 reads back as 17.6299991607666.
    Two cells for one strike pack to ONE stored key, and one would be lost."""
    stored_key = float(np.float32(17.63))
    assert stored_key != 17.63
    s = tp.new_state()
    tp._resume(s, (0, 500.0, 0.0,
                   {stored_key: {"call": 1000.0, "put": 0.0, "net": 1000.0}}))
    _advance(s, _chain({"17.63": [_c("X", 0)]}), 0)
    _advance(s, _chain({"17.63": [_c("X", 5)]}), 60)
    assert tp.grid(s) == {17.63: {"call": 1500.0, "put": 0.0, "net": 1500.0}}


def test_resume_takes_what_is_usable_and_nothing_else():
    s = tp.new_state()
    for prior in (None, (0, 1.0, 0.0, None), (0, 1.0, 0.0, "x")):
        tp._resume(s, prior)
    assert s["total"] == {}
    tp._resume(s, (0, 1.0, 0.0, {100.0: {"call": 5.0, "put": float("nan")},
                                 "bad": {"call": 1.0}, 105.0: "x"}))
    assert tp.grid(s) == {100.0: {"call": 5.0, "put": 0.0, "net": 5.0}}


# ---- the module ---------------------------------------------------------------

def test_the_module_imports_nothing_from_compute():
    tree = ast.parse(pathlib.Path(tp.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    assert not any(n.endswith("compute") for n in names), names
