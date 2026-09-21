"""One daily Schwab budget, shared by every public worker thread."""
import datetime as dt
import threading
import types
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import public_budget as pb
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 21, 10, 0, tzinfo=CT)


def _bus():
    reset_fake_bus()
    return Bus(fake=True)


def test_spend_stops_at_the_limit_and_counts_by_kind():
    bus = _bus()
    assert [pb.spend(bus, "rescue_compute", 3, NOW) for _ in range(4)] == \
        [True, True, True, False]
    st = pb.status(bus, NOW)
    assert st["spent"] == 3 and st["by_kind"] == {"rescue_compute": 3}


def test_the_budget_resets_on_a_new_ct_day():
    bus = _bus()
    pb.spend(bus, "chain", 1, NOW)
    assert pb.spend(bus, "chain", 1, NOW + dt.timedelta(days=1)) is True


def test_concurrent_threads_never_overspend():
    bus = _bus()
    wins = []

    def go():
        wins.append(pb.spend(bus, "chain", 50, NOW))
    ts = [threading.Thread(target=go) for _ in range(200)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(wins) == 50


def test_status_on_an_empty_bus_is_a_fresh_day():
    st = pb.status(_bus(), NOW)
    assert st["spent"] == 0 and st["date"] == "2026-09-21" and st["by_kind"] == {}


def test_kinds_share_one_count():
    bus = _bus()
    assert pb.spend(bus, "chain", 2, NOW) is True
    assert pb.spend(bus, "rescue_compute", 2, NOW) is True
    assert pb.spend(bus, "chain", 2, NOW) is False
    assert pb.status(bus, NOW)["by_kind"] == {"chain": 1, "rescue_compute": 1}


@pytest.mark.parametrize("limit", [0, -5, "lots", None, float("nan"), True])
def test_an_unusable_limit_refuses_and_never_spends(limit):
    bus = _bus()
    assert pb.spend(bus, "chain", limit, NOW) is False
    assert pb.status(bus, NOW)["spent"] == 0


@pytest.mark.parametrize("stored", [
    "not a dict",
    [1, 2],
])
def test_a_non_dict_payload_is_a_fresh_day(stored, monkeypatch):
    """The bus refuses to WRITE a non-dict, so the corrupt read is simulated."""
    bus = _bus()
    monkeypatch.setattr(bus, "cache_get",
                        lambda key: types.SimpleNamespace(payload=stored))
    assert pb.status(bus, NOW) == {"date": "2026-09-21", "spent": 0, "by_kind": {}}


@pytest.mark.parametrize("stored", [
    {"date": "2026-09-21", "spent": "many", "by_kind": {}},
    {"date": "2026-09-21", "spent": 1.5, "by_kind": {}},
    {"date": "2026-09-21", "spent": True, "by_kind": {}},
    {"date": "2026-09-21", "spent": 2, "by_kind": "x"},
])
def test_a_corrupt_stored_payload_is_a_fresh_day(stored):
    bus = _bus()
    bus.cache_set(pb.BUDGET_KEY, stored)
    assert pb.status(bus, NOW)["spent"] == 0
    assert pb.spend(bus, "chain", 1, NOW) is True
    assert pb.status(bus, NOW) == {"date": "2026-09-21", "spent": 1,
                                   "by_kind": {"chain": 1}}
