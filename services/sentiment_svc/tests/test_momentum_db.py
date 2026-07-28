"""Momentum SQLite store — bars + scores."""
import json

import pytest

from services.sentiment_svc import momentum_db


@pytest.fixture()
def conn():
    c = momentum_db.connect(":memory:")
    yield c
    c.close()


def _bar(symbol, date, close=100.0):
    return {"symbol": symbol, "date": date, "open": close - 1, "high": close + 1,
            "low": close - 2, "close": close, "volume": 1_000_000}


# --- bars -------------------------------------------------------------------

def test_upsert_and_read_bars_back_in_date_order(conn):
    momentum_db.upsert_bars(conn, [_bar("AAPL", "2026-07-02", 102.0),
                                   _bar("AAPL", "2026-07-01", 101.0)])

    rows = momentum_db.bars(conn, "AAPL")

    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02"]
    assert rows[-1]["close"] == 102.0


def test_upsert_is_idempotent_on_symbol_and_date(conn):
    momentum_db.upsert_bars(conn, [_bar("AAPL", "2026-07-01", 101.0)])
    momentum_db.upsert_bars(conn, [_bar("AAPL", "2026-07-01", 999.0)])

    rows = momentum_db.bars(conn, "AAPL")

    assert len(rows) == 1
    assert rows[0]["close"] == 999.0


def test_bars_limit_keeps_the_most_recent(conn):
    momentum_db.upsert_bars(
        conn, [_bar("AAPL", f"2026-07-{d:02d}", float(d)) for d in range(1, 11)])

    rows = momentum_db.bars(conn, "AAPL", limit=3)

    assert [r["date"] for r in rows] == ["2026-07-08", "2026-07-09", "2026-07-10"]


def test_bars_of_unknown_symbol_is_empty(conn):
    assert momentum_db.bars(conn, "NOPE") == []


def test_max_date_drives_the_delta_fetch(conn):
    momentum_db.upsert_bars(conn, [_bar("AAPL", "2026-07-01"),
                                   _bar("AAPL", "2026-07-03")])

    assert momentum_db.max_date(conn, "AAPL") == "2026-07-03"


def test_max_date_of_unknown_symbol_is_none_not_a_raise(conn):
    # The first-backfill path — every symbol looks like this on run one.
    assert momentum_db.max_date(conn, "AAPL") is None


def test_upsert_of_nothing_is_a_noop(conn):
    momentum_db.upsert_bars(conn, [])

    assert momentum_db.bars(conn, "AAPL") == []


def test_upsert_skips_rows_without_a_symbol_or_date(conn):
    momentum_db.upsert_bars(conn, [{"symbol": "", "date": "2026-07-01", "close": 1.0},
                                   {"symbol": "AAPL", "date": None, "close": 1.0}])

    assert momentum_db.max_date(conn, "AAPL") is None


# --- scores -----------------------------------------------------------------

def _score(symbol, score, rank, pct=50.0):
    return {"symbol": symbol, "score": score, "percentile": pct, "rank": rank,
            "components": {"trend": 1.0, "rs": 0.5}, "participation": 0.6}


def test_write_and_read_scores_by_level(conn):
    momentum_db.write_scores(conn, "2026-07-28", "industry",
                             [_score("SMH", 1.5, 1), _score("XBI", -0.2, 2)])

    rows = momentum_db.scores(conn, "2026-07-28", "industry")

    assert [r["symbol"] for r in rows] == ["SMH", "XBI"]
    assert rows[0]["components"] == {"trend": 1.0, "rs": 0.5}
    assert rows[0]["participation"] == 0.6


def test_scores_are_returned_in_rank_order(conn):
    momentum_db.write_scores(conn, "2026-07-28", "industry",
                             [_score("B", 0.1, 2), _score("A", 0.9, 1)])

    assert [r["symbol"] for r in momentum_db.scores(conn, "2026-07-28", "industry")] \
        == ["A", "B"]


def test_levels_do_not_bleed_into_each_other(conn):
    momentum_db.write_scores(conn, "2026-07-28", "sector", [_score("XLK", 1.0, 1)])
    momentum_db.write_scores(conn, "2026-07-28", "stock", [_score("AAPL", 1.0, 1)])

    assert [r["symbol"] for r in momentum_db.scores(conn, "2026-07-28", "sector")] \
        == ["XLK"]


def test_rewriting_a_session_replaces_it(conn):
    momentum_db.write_scores(conn, "2026-07-28", "sector", [_score("XLK", 1.0, 1)])
    momentum_db.write_scores(conn, "2026-07-28", "sector", [_score("XLK", 2.0, 1)])

    rows = momentum_db.scores(conn, "2026-07-28", "sector")

    assert len(rows) == 1
    assert rows[0]["score"] == 2.0


def test_scores_of_an_unknown_session_is_empty(conn):
    assert momentum_db.scores(conn, "1999-01-01", "sector") == []


def test_components_survive_a_json_round_trip(conn):
    momentum_db.write_scores(conn, "2026-07-28", "stock", [_score("AAPL", 1.0, 1)])

    raw = conn.execute(
        "SELECT components_json FROM momentum_scores").fetchone()[0]

    assert json.loads(raw)["trend"] == 1.0


def test_write_scores_tolerates_missing_optional_fields(conn):
    momentum_db.write_scores(conn, "2026-07-28", "stock",
                             [{"symbol": "AAPL", "score": 1.0}])

    row = momentum_db.scores(conn, "2026-07-28", "stock")[0]

    assert row["participation"] is None
    assert row["components"] == {}


# --- rank history (feeds the ribbon) ---------------------------------------

def test_rank_history_groups_by_symbol_across_sessions(conn):
    momentum_db.write_scores(conn, "2026-07-27", "industry", [_score("SMH", 1.0, 2)])
    momentum_db.write_scores(conn, "2026-07-28", "industry", [_score("SMH", 1.5, 1)])

    hist = momentum_db.rank_history(conn, "industry")

    assert hist["SMH"] == [("2026-07-27", 2), ("2026-07-28", 1)]


def test_rank_history_is_bounded_to_the_most_recent_sessions(conn):
    for d in range(1, 6):
        momentum_db.write_scores(conn, f"2026-07-{d:02d}", "industry",
                                 [_score("SMH", 1.0, d)])

    hist = momentum_db.rank_history(conn, "industry", days=2)

    assert [d for d, _ in hist["SMH"]] == ["2026-07-04", "2026-07-05"]


def test_rank_history_of_an_unknown_level_is_empty(conn):
    assert momentum_db.rank_history(conn, "sector") == {}


# --- prune ------------------------------------------------------------------

def test_prune_drops_bars_and_scores_beyond_the_window(conn):
    momentum_db.upsert_bars(conn, [_bar("AAPL", "2020-01-01"),
                                   _bar("AAPL", "2026-07-28")])
    momentum_db.write_scores(conn, "2020-01-01", "sector", [_score("XLK", 1.0, 1)])
    momentum_db.write_scores(conn, "2026-07-28", "sector", [_score("XLK", 1.0, 1)])

    momentum_db.prune(conn, keep_days=400)

    assert [r["date"] for r in momentum_db.bars(conn, "AAPL")] == ["2026-07-28"]
    assert momentum_db.scores(conn, "2020-01-01", "sector") == []
    assert momentum_db.scores(conn, "2026-07-28", "sector") != []


def test_prune_on_an_empty_db_is_a_noop(conn):
    momentum_db.prune(conn, keep_days=400)

    assert momentum_db.bars(conn, "AAPL") == []


def test_connect_under_pytest_defaults_to_memory():
    # Test rows must never land in the real rolling DB (the leak that put
    # fixture spikes on the intraday graphs in 2026-07-07).
    c = momentum_db.connect()
    try:
        momentum_db.upsert_bars(c, [_bar("AAPL", "2026-07-01")])
        assert momentum_db.max_date(c, "AAPL") == "2026-07-01"
    finally:
        c.close()

    fresh = momentum_db.connect()
    try:
        assert momentum_db.max_date(fresh, "AAPL") is None
    finally:
        fresh.close()
