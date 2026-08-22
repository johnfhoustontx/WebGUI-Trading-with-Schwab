"""Tests for the point-in-time fundamentals store (Phase 1, task 1.4).

The Investor verdict's weights have never been validated, and the audit had to
mark that impossible: live-parsed ratios describe TODAY, so scoring history with
them leaks today's data into the past. The only honest fix is to start
remembering what each field read on the day it was read.

This store keeps the INPUTS, not the score — that is the whole point. A score
can be recomputed from stored inputs under new weights; inputs cannot be
recovered from a stored score.
"""
import pytest

from services.trade_svc import fundamentals_history as fh


@pytest.fixture
def conn(tmp_path):
    c = fh.init_db(tmp_path / "f.db")
    yield c
    fh.close_db(c)


def _snap(**over):
    base = dict(symbol="AAPL", snapshot_date="2026-08-22",
                pe_ratio=35.49, peg_ratio=1.83, rev_growth_ttm=0.1424,
                eps_growth_ttm=0.2270, roe=1.4875, margin_expanding=False,
                fcf=None, short_int_to_float=None, short_int_day_to_cover=None,
                days_to_earnings=None, sector="Technology",
                sector_pe_median=32.88)
    base.update(over)
    return base


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "f.db"
    c1 = fh.init_db(path)
    fh.record(c1, _snap())
    fh.close_db(c1)
    c2 = fh.init_db(path)
    assert len(fh.snapshots(c2)) == 1
    fh.close_db(c2)


def test_record_round_trips_the_inputs(conn):
    fh.record(conn, _snap())
    r = fh.snapshots(conn)[0]
    assert r["symbol"] == "AAPL"
    assert r["pe_ratio"] == pytest.approx(35.49)
    assert r["roe"] == pytest.approx(1.4875)
    assert r["sector_pe_median"] == pytest.approx(32.88)


def test_margin_expanding_survives_the_three_way_distinction(conn):
    """``margin_expanding`` is True / False / None and the third case is not
    the same as the second — None means the pair of margins needed to decide
    was absent. Storing it as an integer must not collapse them."""
    fh.record(conn, _snap(symbol="A", margin_expanding=True))
    fh.record(conn, _snap(symbol="B", margin_expanding=False))
    fh.record(conn, _snap(symbol="C", margin_expanding=None))
    got = {r["symbol"]: r["margin_expanding"] for r in fh.snapshots(conn)}
    assert got["A"] == 1
    assert got["B"] == 0
    assert got["C"] is None


def test_one_snapshot_per_symbol_per_day(conn):
    fh.record(conn, _snap(pe_ratio=35.49))
    fh.record(conn, _snap(pe_ratio=35.60))
    rows = fh.snapshots(conn)
    assert len(rows) == 1
    assert rows[0]["pe_ratio"] == pytest.approx(35.60)


def test_a_symbol_with_no_fundamentals_is_still_worth_recording(conn):
    """Recording the absence is what distinguishes 'the source served nothing'
    from 'nobody looked' — and the audit's whole finding was about components
    that silently score 0 when their input never arrived."""
    assert fh.record(conn, {"symbol": "ZZZZ",
                            "snapshot_date": "2026-08-22"}) is True
    r = fh.snapshots(conn)[0]
    assert r["symbol"] == "ZZZZ" and r["pe_ratio"] is None


def test_record_never_raises_into_the_caller(conn):
    conn.close()
    assert fh.record(conn, _snap()) is False


def test_history_for_one_symbol_comes_back_oldest_first(conn):
    """A point-in-time series is read forward in time — that is its only use."""
    for d in ("2026-08-20", "2026-08-22", "2026-08-21"):
        fh.record(conn, _snap(snapshot_date=d))
    dates = [r["snapshot_date"] for r in fh.history(conn, "AAPL")]
    assert dates == ["2026-08-20", "2026-08-21", "2026-08-22"]


def test_the_default_path_sits_beside_the_other_trade_svc_stores():
    p = fh.DEFAULT_DB_PATH
    assert p.name == "fundamentals_history.db"
    assert p.parent.name == "data" and p.parent.parent.name == "trade_svc"
