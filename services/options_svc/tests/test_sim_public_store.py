"""The Simulator's snapshots go through a store, so the public side has its own.

A public visitor's ``sim_fetch`` written into ``_SIM_SNAPSHOTS`` would REPLACE
the owner's snapshot for that symbol mid-analysis — and the public load brings
different lazily-loaded expirations, so an owner's leg on an expiration missing
from it would stop pricing. ``SimStore`` is the seam: ``PRIVATE_SIM`` wraps the
owner's existing dicts, and a public store is a separate, bounded, expiring one.
"""
import datetime as dt
import sys
import threading
import types

from services.options_svc import compute


class _Row(types.SimpleNamespace):
    pass


def _snap(symbol, expiries=("2026-10-16",)):
    rows = [_Row(expiry=dt.date.fromisoformat(e), kind=k, strike=100.0, iv=0.3)
            for e in expiries for k in ("call", "put")]
    return types.SimpleNamespace(symbol=symbol, spot=101.0, contracts=rows)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _public(limit=8, ttl=900.0):
    return compute.SimStore(snapshots={}, expirations={}, limit=limit, ttl_sec=ttl)


def test_private_store_wraps_the_existing_dicts():
    assert compute.PRIVATE_SIM.snapshots is compute._SIM_SNAPSHOTS
    assert compute.PRIVATE_SIM.expirations is compute._SIM_EXPIRATIONS


def test_a_public_snapshot_never_touches_the_owners(monkeypatch):
    owner = _snap("SPY")
    compute.reset_sim_snapshots()
    compute._SIM_SNAPSHOTS["SPY"] = owner
    compute._SIM_EXPIRATIONS["SPY"] = ["2026-10-16"]
    before_exps = dict(compute._SIM_EXPIRATIONS)
    public_snap = _snap("SPY", ("2026-09-25",))
    monkeypatch.setattr(compute, "_fetch_sim_snapshot",
                        lambda symbol, lazy, expiries: (public_snap, ["2026-09-25"], {}))
    public = _public()
    try:
        meta = compute.sim_fetch("SPY", lazy=True, store=public)
        assert compute._SIM_SNAPSHOTS["SPY"] is owner
        assert compute._SIM_EXPIRATIONS == before_exps
        assert public.get("SPY") is public_snap
        assert public.expirations_of("SPY") == ["2026-09-25"]
        assert meta["expirations"] == ["2026-09-25"]
    finally:
        compute.reset_sim_snapshots()
        compute._SIM_EXPIRATIONS.pop("SPY", None)


def test_sim_fetch_expiry_reads_and_extends_only_the_public_snapshot(monkeypatch):
    log = []

    def fetch_snapshot(client, symbol, expiry=None, with_history=True, on_chain=None, **kw):
        log.append(str(expiry))
        if on_chain is not None:
            on_chain(None)
        return _snap(symbol, (str(expiry),))

    fake = types.ModuleType("options_simulator.data")
    fake.fetch_snapshot = fetch_snapshot
    pkg = types.ModuleType("options_simulator")
    pkg.data = fake
    monkeypatch.setitem(sys.modules, "options_simulator", pkg)
    monkeypatch.setitem(sys.modules, "options_simulator.data", fake)

    owner = _snap("SPY")
    compute.reset_sim_snapshots()
    compute._SIM_SNAPSHOTS["SPY"] = owner
    compute._SIM_EXPIRATIONS["SPY"] = ["2026-10-16", "2026-11-20"]
    public = _public()
    public.put("SPY", _snap("SPY", ("2026-09-25",)))
    public.set_expirations("SPY", ["2026-09-25", "2026-10-02"])
    try:
        # the owner's listing is not the public one: a private-only expiry is refused
        assert compute.sim_fetch_expiry("SPY", "2026-11-20", store=public) is None
        meta = compute.sim_fetch_expiry("SPY", "2026-10-02", store=public)
        assert meta["added"] == "2026-10-02"
        assert meta["expiries"] == ["2026-09-25", "2026-10-02"]
        assert log == ["2026-10-02"]
        assert len(owner.contracts) == 2          # owner's snapshot untouched
        assert len(public.get("SPY").contracts) == 4
    finally:
        compute.reset_sim_snapshots()
        compute._SIM_EXPIRATIONS.pop("SPY", None)


def test_sim_run_on_a_public_store_never_reads_the_private_one():
    compute.reset_sim_snapshots()
    compute._SIM_SNAPSHOTS["SPY"] = _snap("SPY")
    try:
        legs = [{"kind": "call", "strike": 100.0, "expiry": "2026-10-16",
                 "side": "long", "qty": 1}]
        assert compute.sim_run("SPY", legs=legs, store=_public()) == {}
    finally:
        compute.reset_sim_snapshots()


def test_the_store_is_bounded_and_eviction_drops_the_expiration_list():
    store = _public(limit=2)
    for sym in ("A", "B", "C"):
        store.put(sym, _snap(sym))
        store.set_expirations(sym, ["2026-10-16"])
    assert store.get("A") is None
    assert store.expirations_of("A") is None
    assert set(store.snapshots) == {"B", "C"}


def test_the_store_expires_after_its_ttl():
    store = _public(ttl=900.0)
    clock = _Clock()
    store._mono = clock
    snap = _snap("SPY")
    store.put("SPY", snap)
    clock.t += 899
    assert store.get("SPY") is snap
    clock.t += 2                                  # 901 s after the load
    assert store.get("SPY") is None
    assert "SPY" not in store.snapshots


def test_the_store_survives_concurrent_put_and_get():
    store = _public(limit=4, ttl=None)
    errors, peak = [], [0]

    def worker(n):
        try:
            for i in range(300):
                sym = f"S{(n * 7 + i) % 12}"
                store.put(sym, _snap(sym))
                store.get(f"S{i % 12}")
                # read under the store's lock: outside it a put is mid-evict
                with store._lock:
                    peak[0] = max(peak[0], len(store.snapshots))
        except Exception as exc:                  # noqa: BLE001 - the test's point
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(store.snapshots) <= 4
    assert peak[0] <= 4
