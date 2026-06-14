import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
import gex_collector
from gamma_tool import GammaEngine
import gex_history_db as db


def _fake_chain_7_exps():
    """Build a minimal chain with 7 expirations, each with 3 strikes."""
    call_exp_map, put_exp_map = {}, {}
    exps = ["2026-04-29:0", "2026-04-30:1", "2026-05-01:2",
            "2026-05-02:3", "2026-05-05:6", "2026-05-09:10", "2026-05-16:17"]
    for exp in exps:
        call_exp_map[exp] = {
            "7100.0": [{"strike": 7100.0, "openInterest": 1000,
                        "gamma": 0.002, "volatility": 18, "delta": 0.6}],
            "7135.0": [{"strike": 7135.0, "openInterest": 5000,
                        "gamma": 0.005, "volatility": 18, "delta": 0.5}],
            "7170.0": [{"strike": 7170.0, "openInterest": 2000,
                        "gamma": 0.003, "volatility": 18, "delta": 0.4}],
        }
        put_exp_map[exp] = {
            "7100.0": [{"strike": 7100.0, "openInterest": 2000,
                        "gamma": 0.002, "volatility": 18, "delta": -0.4}],
            "7135.0": [{"strike": 7135.0, "openInterest": 4500,
                        "gamma": 0.005, "volatility": 18, "delta": -0.5}],
            "7170.0": [{"strike": 7170.0, "openInterest": 800,
                        "gamma": 0.003, "volatility": 18, "delta": -0.6}],
        }
    return {
        "underlyingPrice": 7135.9,
        "underlying": {"last": 7135.9},
        "callExpDateMap": call_exp_map,
        "putExpDateMap": put_exp_map,
    }


def test_compute_term_grid_keeps_top_5_expirations():
    chain = _fake_chain_7_exps()
    eng = GammaEngine()
    grid = eng.compute_term_grid(chain, top_n=5)
    assert len(grid["expirations"]) == 5
    assert grid["expirations"] == [
        "2026-04-29", "2026-04-30", "2026-05-01",
        "2026-05-02", "2026-05-05",
    ]
    assert grid["underlying_price"] == 7135.9


def test_compute_term_grid_call_heavy_strike_is_positive():
    chain = _fake_chain_7_exps()
    eng = GammaEngine()
    grid = eng.compute_term_grid(chain, top_n=5)
    cell = grid["cells"]["2026-04-29"][7170.0]
    assert cell["net_gex_usd"] > 0


def test_compute_term_grid_put_heavy_strike_is_negative():
    chain = _fake_chain_7_exps()
    eng = GammaEngine()
    grid = eng.compute_term_grid(chain, top_n=5)
    cell = grid["cells"]["2026-04-29"][7100.0]
    assert cell["net_gex_usd"] < 0


def test_compute_term_grid_handles_missing_underlying():
    eng = GammaEngine()
    grid = eng.compute_term_grid({}, top_n=5)
    assert grid["expirations"] == []
    assert grid["cells"] == {}


def test_poll_term_once_writes_rows_to_db():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        conn = sqlite3.connect(path)
        try:
            db.init_term_schema(conn)
            client = MagicMock()
            client.get_option_chain.return_value = MagicMock(
                status_code=200, json=lambda: _fake_chain_7_exps())
            # Mock client.Options.ContractType.ALL - collector passes it through
            client.Options.ContractType.ALL = "ALL"
            eng = GammaEngine()
            gex_collector.poll_term_once(
                client, eng, conn,
                ts_iso="2026-04-29T09:30:00-05:00")
            rows = db.load_term_snapshot(conn, "2026-04-29T09:30:00-05:00", "SPX")
            # 5 expirations x 3 strikes = 15 rows
            assert len(rows) == 15
            assert all(r["underlying_price"] == 7135.9 for r in rows)
        finally:
            conn.close()


def test_poll_term_once_no_chain_no_rows():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        conn = sqlite3.connect(path)
        try:
            db.init_term_schema(conn)
            client = MagicMock()
            client.get_option_chain.return_value = MagicMock(
                status_code=500, json=lambda: None)
            client.Options.ContractType.ALL = "ALL"
            eng = GammaEngine()
            # Should not raise
            gex_collector.poll_term_once(
                client, eng, conn,
                ts_iso="2026-04-29T09:30:00-05:00")
            rows = db.load_term_snapshot(conn, "2026-04-29T09:30:00-05:00", "SPX")
            assert rows == []
        finally:
            conn.close()
