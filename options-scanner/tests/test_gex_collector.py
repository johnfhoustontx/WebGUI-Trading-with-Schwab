import logging
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import gex_collector as gc
import gex_history_db as db


def test_next_boundary_mid_interval():
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 13, 10, 2, 30, tzinfo=tz)
    target = gc.next_boundary(now)
    assert target == datetime(2026, 4, 13, 10, 5, 0, tzinfo=tz)


def test_next_boundary_on_boundary_rolls_forward():
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 13, 10, 5, 0, tzinfo=tz)
    target = gc.next_boundary(now)
    assert target == datetime(2026, 4, 13, 10, 10, 0, tzinfo=tz)


def test_next_boundary_rolls_over_hour():
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 13, 10, 57, 0, tzinfo=tz)
    target = gc.next_boundary(now)
    assert target == datetime(2026, 4, 13, 11, 0, 0, tzinfo=tz)


def test_next_boundary_rolls_over_day():
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 4, 13, 23, 58, 0, tzinfo=tz)
    target = gc.next_boundary(now)
    assert target == datetime(2026, 4, 14, 0, 0, 0, tzinfo=tz)


def _make_client(chain_by_symbol):
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"

    def get_chain(symbol, **kwargs):
        val = chain_by_symbol.get(symbol)
        if isinstance(val, Exception):
            raise val
        resp = MagicMock()
        resp.status_code = 200 if val is not None else 500
        resp.json.return_value = val
        return resp

    client.get_option_chain.side_effect = get_chain
    return client


def _make_engine():
    engine = MagicMock()
    engine._last_dte = 2
    gex_result = {
        "gex": {"5000": {"call": 1.0, "put": -0.5, "net": 0.5}},
        "spot": 5000.0, "flip": 4995.0,
        "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
        "net_total": 1.0e9, "ts": 1_700_000_000,
    }
    charm_result = {
        "gex": {"5000": {"call": 0.1, "put": -0.1, "net": 0.0}},
        "spot": 5000.0, "flip": 4998.0,
        "top_pos_strike": 5020.0, "top_neg_strike": 4970.0,
        "net_total": 2.0e8, "ts": 1_700_000_000,
    }
    dex_result = {
        "gex": {"5000": {"call": 1.0, "put": -0.5, "net": 0.5}},
        "spot": 5000.0, "flip": 4995.0,
        "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
        "net_total": 5.0e8, "ts": 1_700_000_000,
        "net_delta_0dte": None,
        "projected_net_delta_close": None,
        "hedge_pressure": None,
    }
    # calc_all_from_chain is the batched method used by gex_collector.
    # Individual calc_*_from_chain still stubbed for any direct callers.
    engine.calc_from_chain.return_value = gex_result
    engine.calc_charm_from_chain.return_value = charm_result
    engine.calc_dex_from_chain.return_value = dex_result
    engine.calc_all_from_chain.return_value = (gex_result, charm_result, dex_result, None)
    return engine


def test_poll_once_writes_all_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    from gamma_tool import GammaEngine
    monkeypatch.setattr(
        GammaEngine, "snapshot_summary",
        staticmethod(lambda r, view="gex": {
            "spot": r["spot"], "flip": r["flip"],
            "top_pos_strike": r["top_pos_strike"],
            "top_neg_strike": r["top_neg_strike"],
            "net_total": r["net_total"],
            **({"net_delta_0dte": r.get("net_delta_0dte"),
                "projected_net_delta_close": r.get("projected_net_delta_close"),
                "hedge_pressure": r.get("hedge_pressure")}
               if view == "dex" else {}),
        }),
    )

    chain = {"symbol": "X"}
    client = _make_client({s: chain for s in gc.SYMBOLS})
    engine = _make_engine()
    gc.poll_once(client, engine, conn)
    rows = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert rows == 12


def test_poll_once_continues_on_symbol_failure(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    from gamma_tool import GammaEngine
    monkeypatch.setattr(
        GammaEngine, "snapshot_summary",
        staticmethod(lambda r, view="gex": {
            "spot": r["spot"], "flip": r["flip"],
            "top_pos_strike": r["top_pos_strike"],
            "top_neg_strike": r["top_neg_strike"],
            "net_total": r["net_total"],
            **({"net_delta_0dte": r.get("net_delta_0dte"),
                "projected_net_delta_close": r.get("projected_net_delta_close"),
                "hedge_pressure": r.get("hedge_pressure")}
               if view == "dex" else {}),
        }),
    )

    chain = {"symbol": "X"}
    chains = {s: chain for s in gc.SYMBOLS}
    chains["$SPX"] = RuntimeError("boom")
    client = _make_client(chains)
    engine = _make_engine()

    with caplog.at_level(logging.ERROR, logger="gex_collector"):
        gc.poll_once(client, engine, conn)

    rows = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert rows == 9
    assert any("$SPX" in rec.message for rec in caplog.records)


def test_poll_once_skips_empty_chain(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    from gamma_tool import GammaEngine
    monkeypatch.setattr(
        GammaEngine, "snapshot_summary",
        staticmethod(lambda r, view="gex": {
            "spot": r["spot"], "flip": r["flip"],
            "top_pos_strike": r["top_pos_strike"],
            "top_neg_strike": r["top_neg_strike"],
            "net_total": r["net_total"],
            **({"net_delta_0dte": r.get("net_delta_0dte"),
                "projected_net_delta_close": r.get("projected_net_delta_close"),
                "hedge_pressure": r.get("hedge_pressure")}
               if view == "dex" else {}),
        }),
    )
    chains = {s: {"sym": "X"} for s in gc.SYMBOLS}
    chains["QQQ"] = None
    client = _make_client(chains)
    engine = _make_engine()

    with caplog.at_level(logging.WARNING, logger="gex_collector"):
        gc.poll_once(client, engine, conn)

    rows = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert rows == 9


def test_main_exits_past_stop_time(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    tz = ZoneInfo("America/Chicago")
    fake_now = datetime(2026, 4, 13, 15, 25, 0, tzinfo=tz)
    polls = []

    def clock():
        return fake_now

    gc.main(
        client=MagicMock(), engine=MagicMock(),
        clock=clock, sleeper=lambda s: None,
        poll=lambda c, e, conn: polls.append(1),
    )
    assert polls == []


def test_main_skips_before_market_open(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    tz = ZoneInfo("America/Chicago")
    times = iter([
        datetime(2026, 4, 13, 8, 20, 0, tzinfo=tz),
        datetime(2026, 4, 13, 8, 25, 0, tzinfo=tz),
        datetime(2026, 4, 13, 8, 30, 0, tzinfo=tz),
        datetime(2026, 4, 13, 8, 30, 0, tzinfo=tz),
        datetime(2026, 4, 13, 15, 25, 0, tzinfo=tz),
    ])
    polls = []
    def clock():
        return next(times)
    gc.main(
        client=MagicMock(), engine=MagicMock(),
        clock=clock, sleeper=lambda s: None,
        poll=lambda c, e, conn: polls.append(1),
    )
    assert len(polls) == 1


def test_poll_once_ts_snapped_to_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    from gamma_tool import GammaEngine
    monkeypatch.setattr(
        GammaEngine, "snapshot_summary",
        staticmethod(lambda r, view="gex": {
            "spot": r["spot"], "flip": r["flip"],
            "top_pos_strike": r["top_pos_strike"],
            "top_neg_strike": r["top_neg_strike"],
            "net_total": r["net_total"],
            **({"net_delta_0dte": r.get("net_delta_0dte"),
                "projected_net_delta_close": r.get("projected_net_delta_close"),
                "hedge_pressure": r.get("hedge_pressure")}
               if view == "dex" else {}),
        }),
    )
    chain = {"sym": "X"}
    client = _make_client({s: chain for s in gc.SYMBOLS})
    engine = _make_engine()
    gc.poll_once(client, engine, conn)
    # All ts values should be divisible by POLL_INTERVAL_MIN*60 (300)
    tss = [r[0] for r in conn.execute("SELECT DISTINCT ts FROM snapshots").fetchall()]
    assert all(t % (gc.POLL_INTERVAL_MIN * 60) == 0 for t in tss)
    # All 4 symbols x 2 views should share the SAME ts (same poll cycle)
    assert len(set(tss)) == 1


def test_run_collector_loop_stops_on_event(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    stop = threading.Event()
    polls = []

    # Clock always in-hours so the loop would otherwise run forever.
    tz = ZoneInfo("America/Chicago")
    fixed = datetime(2026, 4, 13, 10, 4, 59, tzinfo=tz)

    def poll(c, e, cn):
        polls.append(1)
        stop.set()                           # ask to stop after first poll

    gc.run_collector_loop(
        client=None, engine=None, conn=conn,
        stop_event=stop, clock=lambda: fixed,
        sleeper=lambda s: None,
        poll=poll,
    )
    assert len(polls) == 1                    # stopped promptly after 1 poll


def test_run_collector_loop_exits_past_stop_time(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    tz = ZoneInfo("America/Chicago")
    after_close = datetime(2026, 4, 13, 15, 30, 0, tzinfo=tz)
    polls = []
    gc.run_collector_loop(
        client=None, engine=None, conn=conn,
        clock=lambda: after_close, sleeper=lambda s: None,
        poll=lambda c, e, cn: polls.append(1),
    )
    assert polls == []


def test_run_collector_loop_survives_poll_exception(tmp_path, monkeypatch, caplog):
    """A single failing poll must NOT kill the loop — it should log and
    continue to the next boundary. (Regression: an uncaught poll exception
    silently stopped the in-tool collector for the rest of the session.)"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    tz = ZoneInfo("America/Chicago")
    in_hours = datetime(2026, 4, 13, 10, 4, 59, tzinfo=tz)
    after_close = datetime(2026, 4, 13, 15, 30, 0, tzinfo=tz)
    # Two clock reads per poll iteration (top-of-loop + post-sleep), then a
    # final read past stop time to terminate.
    times = iter([in_hours, in_hours, in_hours, in_hours, after_close])

    attempts = []

    def poll(c, e, cn):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient boom")

    with caplog.at_level(logging.ERROR, logger="gex_collector"):
        gc.run_collector_loop(
            client=None, engine=None, conn=conn,
            clock=lambda: next(times), sleeper=lambda s: None,
            poll=poll,
        )

    # The loop attempted a SECOND poll after the first one raised.
    assert len(attempts) == 2
    # And the failure was logged, not swallowed silently.
    assert any("boom" in rec.getMessage() or "Poll failed" in rec.getMessage()
               for rec in caplog.records)


def test_ensure_file_logging_attaches_handler_once(tmp_path):
    """The in-tool collector must be able to attach durable file logging so a
    crash/poll-failure traceback is captured (previously lost)."""
    log_path = tmp_path / "logs" / "gex_collector.log"
    before = list(gc.log.handlers)
    try:
        gc.ensure_file_logging(log_path)
        gc.ensure_file_logging(log_path)  # idempotent
        file_handlers = [h for h in gc.log.handlers
                         if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert log_path.exists()
    finally:
        # Restore handler state so we don't leak handlers into other tests.
        for h in list(gc.log.handlers):
            if h not in before:
                gc.log.removeHandler(h)
                h.close()


def test_poll_once_acquires_client_lock_around_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    # Use the existing _make_client helper shape from this file:
    client = _make_client({s: None for s in gc.SYMBOLS})  # empty chains ok
    engine = MagicMock()
    engine.calc_all_from_chain.return_value = (None, None, None, None)

    events = []

    class TrackLock:
        def __enter__(self): events.append("acquire"); return self
        def __exit__(self, *a): events.append("release")

    # Patch the term poll to a no-op so we only measure poll_once's own calls
    monkeypatch.setattr(gc, "poll_term_once", lambda *a, **k: None)
    gc.poll_once(client, engine, conn, lock=TrackLock())
    # One acquire/release per symbol fetched
    assert events.count("acquire") == len(gc.SYMBOLS)
    assert events.count("release") == len(gc.SYMBOLS)


def test_poll_once_writes_dex_rows_with_0dte_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    from gamma_tool import GammaEngine
    monkeypatch.setattr(
        GammaEngine, "snapshot_summary",
        staticmethod(lambda r, view="gex": {
            "spot": r["spot"], "flip": r.get("flip"),
            "top_pos_strike": r.get("top_pos_strike"),
            "top_neg_strike": r.get("top_neg_strike"),
            "net_total": r.get("net_total", 0.0),
            **({"net_delta_0dte": r.get("net_delta_0dte"),
                "projected_net_delta_close": r.get("projected_net_delta_close"),
                "hedge_pressure": r.get("hedge_pressure")}
               if view == "dex" else {}),
        }),
    )

    engine = _make_engine()
    # Override the DEX slot of the batched tuple to populate pressure fields.
    dex_with_pressure = {
        "gex": {"5000": {"call": 1.0, "put": -0.5, "net": 0.5}},
        "spot": 5000.0, "flip": 4995.0,
        "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
        "net_total": 5.0e8, "ts": 1_700_000_000,
        "net_delta_0dte": -1.2e9,
        "projected_net_delta_close": -0.87e9,
        "hedge_pressure": 3.3e8,
    }
    engine.calc_dex_from_chain.return_value = dex_with_pressure
    gex_batch, charm_batch, _, _ = engine.calc_all_from_chain.return_value
    engine.calc_all_from_chain.return_value = (gex_batch, charm_batch, dex_with_pressure, None)
    chain = {"symbol": "X"}
    client = _make_client({s: chain for s in gc.SYMBOLS})
    gc.poll_once(client, engine, conn)

    dex_count = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE view = 'dex'"
    ).fetchone()[0]
    assert dex_count == 4  # one per symbol

    row = conn.execute(
        "SELECT net_delta_0dte, projected_net_delta_close, hedge_pressure "
        "FROM snapshots WHERE view = 'dex' AND symbol = '$SPX'"
    ).fetchone()
    assert row == (-1.2e9, -0.87e9, 3.3e8)


def test_entrypoint_defers_when_lock_held(tmp_path, monkeypatch):
    lock = tmp_path / "gex.lock"
    # Pretend an in-tool collector owns a fresh lock.
    gc.acquire_collector_lock(lock, source="gamma_tool", owner="OTHER",
                              now=int(__import__("time").time()))
    ran = []
    monkeypatch.setattr(gc, "main", lambda **k: ran.append(1))
    gc._entrypoint(lock_path=lock, owner="ME")
    assert ran == []        # deferred: main() never called


def test_entrypoint_runs_when_lock_free(tmp_path, monkeypatch):
    lock = tmp_path / "gex.lock"
    ran = []
    monkeypatch.setattr(gc, "main", lambda **k: ran.append(1))
    monkeypatch.setattr(gc, "_build_live_deps", lambda: (None, None))
    gc._entrypoint(lock_path=lock, owner="ME")
    assert ran == [1]
    # lock released after main() returns
    assert not lock.exists()


def test_make_heartbeat_poll_polls_and_touches_lock(tmp_path, monkeypatch):
    import json
    lock = tmp_path / "gex.lock"
    # Seed an owned lock with an old heartbeat so we can see it advance.
    gc.acquire_collector_lock(lock, source="gamma_tool", owner="ME", now=1000)

    calls = []
    sentinel = object()
    monkeypatch.setattr(
        gc, "poll_once",
        lambda c, e, cn, lock=None: calls.append((c, e, cn, lock)))

    poll = gc.make_heartbeat_poll(
        lock, source="gamma_tool", owner="ME", client_lock=sentinel)
    assert callable(poll)

    poll("client", "engine", "conn")

    # poll_once was called once, with the client_lock threaded through.
    assert len(calls) == 1
    assert calls[0] == ("client", "engine", "conn", sentinel)
    # The lock heartbeat was refreshed (advanced past the seeded 1000).
    data = json.loads(lock.read_text())
    assert data["owner"] == "ME" and data["source"] == "gamma_tool"
    assert data["heartbeat"] > 1000


def test_sentiment_hook_never_raises(monkeypatch):
    import gex_collector as gc
    # Force the subprocess call to raise; the helper must swallow it.
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    gc._publish_sentiment_bridge()   # must NOT raise
