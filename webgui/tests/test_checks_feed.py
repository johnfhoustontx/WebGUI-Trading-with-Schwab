from shared.bus import Bus
from shared.bus.client import reset_fake_bus

import bus_client
from pages.options import checks_feed


def _fresh():
    """Module-level read_gated memos outlive a test; a fresh bus needs fresh memos."""
    reset_fake_bus()
    for memo in checks_feed._memos.values():
        memo.clear()


def test_context_indexes_the_board_by_symbol_and_reads_the_other_views(monkeypatch):
    _fresh()
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:options:matrix", {"rows": [{"symbol": "ORCL", "spot": 110.0}]})
    bus.cache_set("cache:sentiment:regime", {"direction": 1})
    bus.cache_set("cache:options:ledger_caps", {"limits": {}})
    ctx = checks_feed.read_context()
    assert ctx["matrix"]["ORCL"]["spot"] == 110.0
    assert ctx["regime"]["direction"] == 1
    assert ctx["caps"] == {"limits": {}}
    # A cold view is None, never {}: checks marks None as a missing view, so the
    # summary reads "Partly checked" instead of "Clear" (Task 17 review).
    assert ctx["calibration"] is None


def test_a_cold_bus_yields_empty_context_not_a_raise(monkeypatch):
    _fresh()
    monkeypatch.setattr(bus_client, "_bus", Bus(fake=True))
    ctx = checks_feed.read_context()
    assert ctx == {"matrix": None, "regime": None, "calibration": None, "caps": None}


def test_versions_lists_the_views_that_should_refresh_the_column():
    assert set(checks_feed.REFRESH_VIEWS) == {"options:ledger_caps", "sentiment:regime",
                                              "options:calibration"}


def test_a_raising_bus_read_costs_only_that_view(monkeypatch):
    _fresh()
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:sentiment:regime", {"direction": -1})
    real = bus_client.read_gated

    def flaky(view, memo):
        if view == checks_feed.MATRIX_VIEW:
            raise ConnectionError("down")
        return real(view, memo)
    monkeypatch.setattr(bus_client, "read_gated", flaky)
    ctx = checks_feed.read_context()
    assert ctx["matrix"] is None and ctx["regime"] == {"direction": -1}


def test_board_symbols_are_matched_case_insensitively(monkeypatch):
    _fresh()
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:options:matrix", {"rows": [{"symbol": "orcl", "spot": 110.0}, "junk"]})
    ctx = checks_feed.read_context()
    assert set(ctx["matrix"]) == {"ORCL"}


def test_checks_for_passes_a_missing_board_row_as_none_so_the_summary_is_partly_checked(monkeypatch):
    from pages.options import checks
    row = {"id": "a", "symbol": "XOM", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0, "long_strike": 97.5,
           "credit": 0.60, "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0, "earnings_status": "none_scheduled",
           "earnings_date": None, "_allow_paper": False, "underlying_price": 110.0}
    ctx = {"matrix": {"ORCL": {"spot": 110.0}}, "regime": {"direction": 1},
           "calibration": None, "caps": None}
    out = checks_feed.checks_for(row, ctx)
    assert checks.summary(out)["text"].startswith("Partly checked")
    by_key = {c["key"]: c for c in out}
    # The board-row path, pinned on its own: XOM is not on the board, so the two
    # board-only checks are grey BECAUSE a view was missing.
    assert by_key["wall"].get("missing_view") is True
    assert by_key["gamma"].get("missing_view") is True


def test_a_missing_board_row_alone_keeps_the_summary_partly_checked():
    from pages.options import checks
    row = {"id": "a", "symbol": "XOM", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0, "long_strike": 97.5,
           "credit": 0.60, "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0, "earnings_status": "none_scheduled",
           "earnings_date": None, "_allow_paper": False, "underlying_price": 110.0}
    # Calibration is a REAL view (empty buckets grade the record line "not enough
    # history" without calling the view missing) and caps is omitted - with
    # _allow_paper False the book check does not apply - so the board row is the
    # only missing view left.
    ctx = {"matrix": {"ORCL": {"spot": 110.0}}, "regime": {"direction": 1},
           "calibration": {"buckets": {}}}
    out = checks_feed.checks_for(row, ctx)
    by_key = {c["key"]: c for c in out}
    assert "book" not in by_key
    assert not by_key["record"].get("missing_view")
    missing = {k for k, c in by_key.items() if c.get("missing_view")}
    assert missing == {"wall", "gamma"}
    assert checks.summary(out)["text"].startswith("Partly checked")


def test_the_module_imports_no_ui_framework():
    import ast, inspect
    tree = ast.parse(inspect.getsource(checks_feed))
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if m.split(".")[0] in {"nicegui", "services", "sqlite3", "redis"}}


def test_two_threads_never_leave_the_memo_on_an_older_read(monkeypatch):
    """Two tabs' worker threads read one view: the first reads the OLD payload
    and stalls, the view moves, the second reads the new one. Unlocked, the
    second probes while the first is mid-read and the first then stores its
    older version and payload over the second's; locked, the second waits."""
    import threading

    import bus_client
    from pages.options import checks_feed

    monkeypatch.setattr(checks_feed, "_memos", {v: {} for v in checks_feed._memos})
    view = checks_feed.CAPS_VIEW
    store = {"ver": 1, "payload": {"n": 1}}
    first_reading, release, second_probed = (threading.Event(), threading.Event(),
                                             threading.Event())

    def read_version(_view):
        if threading.current_thread().name == "second":
            second_probed.set()
        return store["ver"]

    def read_full(_view):
        # An envelope carries its own version, snapshotted with its payload.
        payload, ver = dict(store["payload"]), store["ver"]
        if threading.current_thread().name == "first":
            first_reading.set()
            release.wait(5)
        return payload, ver

    monkeypatch.setattr(bus_client, "read_version", read_version)
    monkeypatch.setattr(bus_client, "read_full", read_full)
    got = {}
    first = threading.Thread(name="first", target=lambda: got.__setitem__(
        "first", checks_feed._gated(view)))
    second = threading.Thread(name="second", target=lambda: got.__setitem__(
        "second", checks_feed._gated(view)))
    first.start()
    assert first_reading.wait(5)
    store.update(ver=2, payload={"n": 2})               # the service republishes
    second.start()
    assert not second_probed.wait(0.3)                  # held off while first reads
    release.set()
    first.join(5)
    second.join(5)
    assert got == {"first": {"n": 1}, "second": {"n": 2}}
    memo = checks_feed._memos[view]
    assert memo["state"] == (2, {"n": 2})


def test_each_view_has_its_own_lock():
    from pages.options import checks_feed
    assert set(checks_feed._locks) == set(checks_feed._memos)
    assert len({id(lock) for lock in checks_feed._locks.values()}) == len(checks_feed._locks)


def test_read_context_without_caps_never_reads_the_ledger_caps(monkeypatch):
    """The public Calculator's rating draws no Paper book line, so it asks for
    the context WITHOUT the owner's ledger caps: that view is not even read."""
    _fresh()
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:options:matrix", {"rows": [{"symbol": "ORCL", "spot": 110.0}]})
    bus.cache_set("cache:options:ledger_caps", {"limits": {}})
    reads = []
    real = bus_client.read_gated
    monkeypatch.setattr(bus_client, "read_gated",
                        lambda view, memo: (reads.append(view), real(view, memo))[1])
    ctx = checks_feed.read_context(caps=False)
    assert ctx["caps"] is None and ctx["matrix"]["ORCL"]["spot"] == 110.0
    assert checks_feed.CAPS_VIEW not in reads
    reads.clear()
    assert checks_feed.read_context()["caps"] == {"limits": {}}   # the default reads it
    assert checks_feed.CAPS_VIEW in reads
