"""
test_gex_collector_prem.py — the per-strike premium ("prem") snapshot view.

The strike ladder on the Premium Divergence panel reads a FIFTH view string,
``"prem"``, written by the same ``insert_snapshot`` as the four Greek views.
There is no schema change behind it: ``snapshots.view`` is free-form, and a
premium cell is ``{call, put, net}`` floats — exactly the shape the columnar
float32 packer gates on.

Its own module rather than more cases in test_gex_collector.py: that file's
``_make_engine`` fixture returns a chain-agnostic Greek result, while these
cases need a chain that carries real per-strike premium.
"""
from unittest.mock import MagicMock

import gex_collector as gc
import gex_history_db as db


#############################################
# FIXTURES
#############################################

def _make_client(chain_by_symbol):
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"

    def get_chain(symbol, **kwargs):
        val = chain_by_symbol.get(symbol)
        resp = MagicMock()
        resp.status_code = 200 if val is not None else 500
        resp.json.return_value = val
        return resp

    client.get_option_chain.side_effect = get_chain
    return client


def _make_engine():
    engine = MagicMock()
    engine._last_dte = 0
    result = {
        "gex": {"5000": {"call": 1.0, "put": -0.5, "net": 0.5}},
        "spot": 5000.0, "flip": 4995.0,
        "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
        "net_total": 1.0e9, "ts": 1_700_000_000,
    }
    engine.calc_all_from_chain.return_value = (result, None, None, None)
    return engine


def _prem_chain():
    """A chain carrying real per-strike premium on both sides."""
    return {
        "callExpDateMap": {"2026-07-11:0": {
            "5000.0": [{"strike": 5000.0, "totalVolume": 10, "mark": 2.0}],
            "5010.0": [{"strike": 5010.0, "totalVolume": 4, "mark": 1.0}],
        }},
        "putExpDateMap": {"2026-07-11:0": {
            "5000.0": [{"strike": 5000.0, "totalVolume": 6, "mark": 3.0}],
        }},
    }


def _conn(tmp_path, monkeypatch):
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
        }),
    )
    return conn


#############################################
# TESTS
#############################################

def test_poll_once_writes_prem_view_grid(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    gc.poll_once(_make_client({"SPY": _prem_chain()}), _make_engine(), conn,
                 symbols=["SPY"], poll_term=False)

    rows = db.load_today_with_grid(conn, "SPY", "prem")
    assert len(rows) == 1
    grid = rows[0][6]
    # Strike keys survive the encode/decode round-trip as floats.
    assert grid[5000.0] == {"call": 2000.0, "put": 1800.0, "net": 200.0}
    assert grid[5010.0] == {"call": 400.0, "put": 0.0, "net": 400.0}


def test_prem_row_shares_the_ts_and_spot_of_the_greek_views(tmp_path, monkeypatch):
    """The ladder is read at the SAME timestamps as the flow series and drawn
    against the same spot. A prem row on its own clock would put the ladder out
    of step with the cursor it is supposed to follow."""
    conn = _conn(tmp_path, monkeypatch)
    gc.poll_once(_make_client({"SPY": _prem_chain()}), _make_engine(), conn,
                 symbols=["SPY"], poll_term=False)

    gex = db.load_today_with_grid(conn, "SPY", "gex")[0]
    prem = db.load_today_with_grid(conn, "SPY", "prem")[0]
    assert prem[0] == gex[0]        # ts
    assert prem[1] == gex[1]        # spot


def test_prem_view_absent_when_the_chain_carries_no_premium(tmp_path, monkeypatch):
    """No row at all, rather than an empty grid. The panel distinguishes "not
    collected yet" from "collected, nothing traded"; an empty row reads as the
    latter and would show a blank ladder as though it were a real reading."""
    conn = _conn(tmp_path, monkeypatch)
    gc.poll_once(_make_client({"SPY": {"symbol": "SPY"}}), _make_engine(), conn,
                 symbols=["SPY"], poll_term=False)

    assert db.load_today_with_grid(conn, "SPY", "prem") == []
    assert db.load_today_with_grid(conn, "SPY", "gex")   # the Greek views still wrote


def test_prem_failure_cannot_break_the_greek_views(tmp_path, monkeypatch):
    """The ladder is additive. A premium-compute failure must cost the ladder,
    never the heatmap the whole page is built around."""
    conn = _conn(tmp_path, monkeypatch)

    def _boom(_chain):
        raise RuntimeError("premium exploded")
    monkeypatch.setattr(gc.flow_skew, "premium_by_strike", _boom)

    gc.poll_once(_make_client({"SPY": _prem_chain()}), _make_engine(), conn,
                 symbols=["SPY"], poll_term=False)

    assert db.load_today_with_grid(conn, "SPY", "prem") == []
    assert len(db.load_today_with_grid(conn, "SPY", "gex")) == 1
