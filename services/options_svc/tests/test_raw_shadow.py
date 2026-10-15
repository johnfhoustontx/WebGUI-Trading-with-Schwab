"""``tee``'s shadow check: derive a stored raw minute again and compare it, field
by field, with the rows the live collector wrote for that minute.

It is the proof that derive-from-raw is identical before anything relies on it
(docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §7). It must never
delay the live collector: a busy shadow skips a minute and counts the skip."""
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import raw_shadow, raw_tee
from shared import feed_config, raw_store
from shared.raw_minute import RawMinute

sys.path.insert(0, str(__import__("repo_paths").OPTIONS_SCANNER))
import gex_collector as gc  # noqa: E402
import gex_history_db as gh  # noqa: E402
from gamma_tool import GammaEngine  # noqa: E402

TZ = ZoneInfo("America/Chicago")
MINUTE = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)
TS = int(MINUTE.timestamp())
D = "2026-10-14"
EXP = "2026-10-16:2"


def _leg(iv, oi, delta, gamma, vol, mark):
    return [{"volatility": iv, "openInterest": oi, "delta": delta, "gamma": gamma,
             "daysToExpiration": 2, "totalVolume": vol, "mark": mark,
             "bid": mark - 0.05, "ask": mark + 0.05}]


def _chain(symbol="SPY"):
    return {
        "symbol": symbol, "underlyingPrice": 500.0,
        "callExpDateMap": {EXP: {"495.0": _leg(22.0, 1000, 0.70, 0.05, 300, 7.1),
                                 "500.0": _leg(20.0, 2000, 0.52, 0.06, 900, 4.2),
                                 "505.0": _leg(21.0, 1500, 0.34, 0.05, 400, 2.0)}},
        "putExpDateMap": {EXP: {"495.0": _leg(23.0, 1100, -0.30, 0.05, 350, 1.9),
                                "500.0": _leg(21.0, 2200, -0.48, 0.06, 800, 4.0),
                                "505.0": _leg(22.0, 1300, -0.66, 0.05, 200, 6.8)}},
    }


def _db():
    conn = sqlite3.connect(":memory:")
    gh.init_schema(conn)
    gh.init_term_schema(conn)
    return conn


@pytest.fixture
def minute():
    return RawMinute(ts=TS, chains=[("SPY", _chain()), ("QQQ", _chain("QQQ"))],
                     term=_chain("$SPX"), symbols=["SPY", "QQQ"])


@pytest.fixture
def live_conn(minute):
    conn = _db()
    gc.derive_minute(GammaEngine(), conn, minute)
    conn.commit()
    return conn


def test_identical_rows_compare_clean(tmp_path, live_conn, minute):
    raw_store.write_minute(D, minute, root=tmp_path)
    out = raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)
    assert out["rows"] >= 8                   # two symbols, four Greek views each
    assert out["diffs"] == []
    assert out["ms"] >= 0


def test_a_changed_summary_value_is_reported(tmp_path, live_conn, minute):
    raw_store.write_minute(D, minute, root=tmp_path)
    live_conn.execute("UPDATE snapshots SET spot = spot + 1 WHERE view = 'gex' AND symbol = 'SPY'")
    out = raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)
    assert any("SPY/gex" in d and "spot" in d for d in out["diffs"])


def test_a_changed_grid_cell_is_reported(tmp_path, live_conn, minute):
    raw_store.write_minute(D, minute, root=tmp_path)
    gh.insert_snapshot(live_conn, "QQQ", "charm", {"ts": TS, "spot": 500.0},
                       {495.0: {"call": 1.0, "put": 2.0, "net": 3.0}}, 2)
    out = raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)
    assert any("QQQ/charm" in d for d in out["diffs"])


def test_a_row_only_one_side_has_is_reported(tmp_path, live_conn, minute):
    raw_store.write_minute(D, minute, root=tmp_path)
    live_conn.execute("DELETE FROM snapshots WHERE symbol = 'QQQ' AND view = 'dex'")
    out = raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)
    assert any("QQQ/dex" in d and "missing" in d for d in out["diffs"])


def test_rows_the_collector_did_not_derive_are_not_compared(tmp_path, live_conn, minute):
    # tprem and HIRO rows are written beside the poll, not by derive_minute.
    raw_store.write_minute(D, minute, root=tmp_path)
    gh.insert_snapshot(live_conn, "SPY", "tprem", {"ts": TS}, {}, 2)
    assert raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)["diffs"] == []


def test_a_changed_term_row_is_reported(tmp_path, live_conn, minute):
    raw_store.write_minute(D, minute, root=tmp_path)
    live_conn.execute("UPDATE gex_term_snapshots SET net_gex_usd = net_gex_usd + 1")
    out = raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)
    assert any(d.startswith("term") for d in out["diffs"])


def test_a_minute_missing_from_the_raw_store_is_not_a_difference(tmp_path, live_conn):
    out = raw_shadow.compare_minute(D, TS, live_conn, root=tmp_path)
    assert out["rows"] == 0 and out["diffs"] == [] and out["missing_raw"] is True


def test_a_busy_shadow_skips_rather_than_queueing(monkeypatch):
    raw_shadow.reset_stats()
    monkeypatch.setattr(raw_shadow, "_busy", lambda: True)
    monkeypatch.setattr(raw_tee, "last_written", lambda: (D, TS))
    assert raw_shadow.submit_latest() is False
    assert raw_shadow.stats()["skipped"] == 1


def test_the_same_minute_is_never_compared_twice(monkeypatch):
    raw_shadow.reset_stats()
    ran = []
    monkeypatch.setattr(raw_shadow, "_busy", lambda: False)
    monkeypatch.setattr(raw_shadow, "_run", lambda d, ts: ran.append((d, ts)))
    monkeypatch.setattr(raw_tee, "last_written", lambda: (D, TS))
    assert raw_shadow.submit_latest(wait=True) is True
    assert raw_shadow.submit_latest(wait=True) is False
    assert ran == [(D, TS)]


def test_nothing_written_yet_submits_nothing(monkeypatch):
    raw_shadow.reset_stats()
    monkeypatch.setattr(raw_tee, "last_written", lambda: None)
    assert raw_shadow.submit_latest() is False


def test_stats_count_minutes_diffs_and_percentiles():
    raw_shadow.reset_stats()
    for ms, diffs in ((100.0, []), (300.0, ["SPY/gex spot"]), (200.0, [])):
        raw_shadow.record({"ts": TS, "rows": 8, "diffs": diffs, "ms": ms, "missing_raw": False})
    s = raw_shadow.stats()
    assert (s["minutes"], s["minutes_with_diffs"], s["last_diff"]) == (3, 1, "SPY/gex spot")
    assert s["p50_ms"] == 200.0 and s["p95_ms"] == 300.0


def test_the_handler_runs_the_shadow_only_in_tee(monkeypatch):
    from services.options_svc import compute, handlers
    calls = []
    monkeypatch.setattr(compute, "collect_gex_snapshots", lambda capture_symbols=None: 0)
    monkeypatch.setattr(raw_shadow, "submit_latest", lambda: calls.append(1) or True)
    monkeypatch.setattr(feed_config, "mode", lambda: "off")
    handlers.collect_gex_history(None)
    assert calls == []
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    handlers.collect_gex_history(None)
    assert calls == [1]


def test_the_gate_figure_is_the_whole_per_minute_cost():
    raw_shadow.reset_stats()
    raw_shadow.record({"ts": TS, "rows": 8, "diffs": [], "ms": 900.0, "missing_raw": False})
    raw_shadow.record_cost("hooks", 300.0)
    raw_shadow.record_cost("alerts", 150.0)
    raw_shadow.record_cost("tee", 400.0)
    s = raw_shadow.stats()
    assert (s["hooks_p95_ms"], s["alerts_p95_ms"], s["tee_p95_ms"]) == (300.0, 150.0, 400.0)
    # derive + hooks + alerts: what the deriver will spend on one minute. The
    # tee write is the collector's own cost and is reported apart.
    assert s["gate_p95_ms"] == 1350.0


def test_an_unknown_cost_is_ignored():
    raw_shadow.reset_stats()
    raw_shadow.record_cost("nonsense", 5.0)
    assert "nonsense_p95_ms" not in raw_shadow.stats()


def test_the_tee_write_is_timed(tmp_path, monkeypatch):
    raw_shadow.reset_stats()
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    raw_tee.maybe_write(RawMinute(ts=TS, chains=[("SPY", _chain())], symbols=["SPY"]),
                        root=tmp_path)
    assert raw_shadow.stats()["tee_p95_ms"] is not None


def test_the_handler_times_the_flow_alerts_in_tee(monkeypatch):
    from services.options_svc import compute, handlers
    raw_shadow.reset_stats()
    monkeypatch.setattr(compute, "collect_gex_snapshots", lambda capture_symbols=None: 0)
    monkeypatch.setattr(raw_shadow, "submit_latest", lambda: False)
    for name in ("publish_flow_skew", "publish_matrix", "publish_net_premium", "run_flow_alerts"):
        monkeypatch.setattr(handlers, name, lambda bus: None)
    monkeypatch.setattr(handlers.gamma_public, "begin_tick", lambda bus: [])
    monkeypatch.setattr(handlers, "_current_gamma_symbol", lambda bus: "SPY")
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")

    class _Bus:
        def cache_set(self, *a, **k):
            return 1

    handlers.collect_gex_history(_Bus())
    assert raw_shadow.stats()["alerts_p95_ms"] is not None
