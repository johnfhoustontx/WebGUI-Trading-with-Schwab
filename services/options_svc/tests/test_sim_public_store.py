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


def test_concurrent_put_and_get_smoke_test():
    """A SMOKE test only: under the GIL each dict operation is atomic, so this
    cannot detect a missing lock. It catches a crash or a broken bound."""
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


# ── review fixes ─────────────────────────────────────────────────────────────

def _patch_fetch(monkeypatch, during=None):
    """A fake options_simulator.data whose fetch_snapshot returns ONE expiry's
    contracts and runs ``during()`` mid-call (to simulate another visitor)."""
    def fetch_snapshot(client, symbol, expiry=None, with_history=True, on_chain=None, **kw):
        if during is not None:
            during()
        if on_chain is not None:
            on_chain(None)
        return _snap(symbol, (str(expiry),))

    fake = types.ModuleType("options_simulator.data")
    fake.fetch_snapshot = fetch_snapshot
    pkg = types.ModuleType("options_simulator")
    pkg.data = fake
    monkeypatch.setitem(sys.modules, "options_simulator", pkg)
    monkeypatch.setitem(sys.modules, "options_simulator.data", fake)


def test_an_extend_whose_snapshot_was_evicted_mid_fetch_returns_none(monkeypatch):
    store = _public(limit=1)
    old = _snap("SPY", ("2026-09-25",))
    store.put("SPY", old)
    store.set_expirations("SPY", ["2026-09-25", "2026-10-02"])
    _patch_fetch(monkeypatch, during=lambda: store.put("QQQ", _snap("QQQ")))

    assert compute.sim_fetch_expiry("SPY", "2026-10-02", store=store) is None
    assert "SPY" not in store.snapshots            # nothing resurrected
    assert store.expirations_of("SPY") is None
    assert len(old.contracts) == 2                 # the orphan was not extended


def test_an_extend_whose_snapshot_was_replaced_mid_fetch_returns_none(monkeypatch):
    store = _public()
    old, newer = _snap("SPY", ("2026-09-25",)), _snap("SPY", ("2026-09-25",))
    store.put("SPY", old)
    store.set_expirations("SPY", ["2026-09-25", "2026-10-02"])
    _patch_fetch(monkeypatch, during=lambda: store.put("SPY", newer))

    assert compute.sim_fetch_expiry("SPY", "2026-10-02", store=store) is None
    assert store.get("SPY") is newer
    assert len(newer.contracts) == 2


def test_a_successful_extend_stores_a_new_object_and_leaves_the_old_alone(monkeypatch):
    store = _public()
    old = _snap("SPY", ("2026-09-25",))
    old_list = old.contracts
    store.put("SPY", old)
    store.set_expirations("SPY", ["2026-09-25", "2026-10-02"])
    _patch_fetch(monkeypatch)

    meta = compute.sim_fetch_expiry("SPY", "2026-10-02", store=store)
    new = store.get("SPY")
    assert meta["added"] == "2026-10-02"
    assert meta["expiries"] == ["2026-09-25", "2026-10-02"]
    assert new is not old
    assert len(new.contracts) == 4
    assert old.contracts is old_list and len(old_list) == 2


def test_an_extend_keeps_the_owners_eviction_order(monkeypatch):
    compute.reset_sim_snapshots()
    try:
        for sym in ("A", "B", "C"):
            compute._stash_sim_snapshot(sym, _snap(sym, ("2026-09-25",)))
        compute._SIM_EXPIRATIONS["A"] = ["2026-09-25", "2026-10-02"]
        _patch_fetch(monkeypatch)
        assert compute.sim_fetch_expiry("A", "2026-10-02") is not None
        assert list(compute._SIM_SNAPSHOTS) == ["A", "B", "C"]   # A still oldest
    finally:
        compute.reset_sim_snapshots()


def test_the_private_limit_is_read_when_a_snapshot_is_stored(monkeypatch):
    compute.reset_sim_snapshots()
    monkeypatch.setattr(compute, "SIM_SNAPSHOT_LIMIT", 2)
    try:
        for sym in ("A", "B", "C"):
            compute._stash_sim_snapshot(sym, _snap(sym))
        assert list(compute._SIM_SNAPSHOTS) == ["B", "C"]
    finally:
        compute.reset_sim_snapshots()


def test_the_eager_fallback_drops_a_stale_expiration_list(monkeypatch):
    store = _public()
    results = iter([(_snap("SPY"), ["2026-10-16"], {}), (_snap("SPY"), None, {})])
    monkeypatch.setattr(compute, "_fetch_sim_snapshot",
                        lambda symbol, lazy, expiries: next(results))
    compute.sim_fetch("SPY", lazy=True, store=store)
    assert store.expirations_of("SPY") == ["2026-10-16"]
    meta = compute.sim_fetch("SPY", lazy=True, store=store)
    assert store.expirations_of("SPY") is None
    assert "expirations" not in meta


def test_reset_clears_the_private_load_times_but_keeps_the_dict_objects():
    snaps, exps = compute._SIM_SNAPSHOTS, compute._SIM_EXPIRATIONS
    compute._stash_sim_snapshot("A", _snap("A"))
    compute.reset_sim_snapshots()
    assert compute.PRIVATE_SIM._loaded == {}
    assert compute._SIM_SNAPSHOTS is snaps and compute._SIM_EXPIRATIONS is exps
    assert compute.PRIVATE_SIM.snapshots is snaps


def test_the_clock_is_a_constructor_argument():
    clock = _Clock()
    store = compute.SimStore(limit=4, ttl_sec=10.0, clock=clock)
    store.put("SPY", _snap("SPY"))
    clock.t += 11
    assert store.get("SPY") is None
