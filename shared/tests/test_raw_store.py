"""The per-session raw chain store (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §4).

A minute is either complete or absent: its chains and its ``minutes`` row are
one transaction, and the deriver reads ``minutes`` to decide what exists."""
import sqlite3

import pytest

from shared import raw_store as rs
from shared.raw_minute import RawMinute

D = "2026-10-14"


def _m(ts, carried=False, term=False):
    chains = [("SPY", {"symbol": "SPY", "underlyingPrice": 600.0}),
              ("AAPL", {"symbol": "AAPL", "underlyingPrice": 250.0})]
    return RawMinute(ts=ts, chains=chains,
                     carried={"AAPL"} if carried else set(),
                     carried_age={"AAPL": None} if carried else {},
                     stored={"AAPL": {"symbol": "AAPL", "underlyingPrice": 249.0}} if carried else {},
                     term={"symbol": "$SPX"} if term else None,
                     symbols=["SPY", "AAPL", "QQQ"])


def test_round_trip_keeps_order_carry_stored_and_universe(tmp_path):
    rs.write_minute(D, _m(1_760_000_100, carried=True), root=tmp_path)
    got = rs.read_minute(D, 1_760_000_100, root=tmp_path)
    assert [s for s, _ in got.chains] == ["SPY", "AAPL"]
    assert got.chains[0][1] == {"symbol": "SPY", "underlyingPrice": 600.0}
    assert got.carried == {"AAPL"} and got.carried_age == {"AAPL": None}
    assert got.chain_for_skew("AAPL")["underlyingPrice"] == 249.0
    assert got.chain_for_skew("SPY")["underlyingPrice"] == 600.0
    assert got.term is None and got.symbols == ["SPY", "AAPL", "QQQ"]


def test_the_term_chain_round_trips(tmp_path):
    rs.write_minute(D, _m(300, term=True), root=tmp_path)
    assert rs.read_minute(D, 300, root=tmp_path).term == {"symbol": "$SPX"}


def test_an_absent_minute_reads_as_none(tmp_path):
    assert rs.read_minute(D, 60, root=tmp_path) is None
    assert rs.complete_minutes(D, root=tmp_path) == []
    assert rs.last_complete(D, root=tmp_path) is None


def test_a_minute_is_complete_only_once_its_minutes_row_is_written(tmp_path, monkeypatch):
    def _die(*a, **k):
        raise RuntimeError("die")

    monkeypatch.setattr(rs, "_insert_minute_row", _die)
    with pytest.raises(RuntimeError):
        rs.write_minute(D, _m(1_760_000_160), root=tmp_path)
    monkeypatch.undo()
    assert rs.complete_minutes(D, root=tmp_path) == []
    assert rs.read_minute(D, 1_760_000_160, root=tmp_path) is None
    # and the chains of the failed minute were rolled back with it
    conn = sqlite3.connect(rs.session_path(D, root=tmp_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0] == 0
    finally:
        conn.close()


def test_rewriting_a_minute_replaces_it(tmp_path):
    rs.write_minute(D, _m(60), root=tmp_path)
    again = RawMinute(ts=60, chains=[("QQQ", {"symbol": "QQQ"})], symbols=["QQQ"])
    rs.write_minute(D, again, root=tmp_path)
    assert [s for s, _ in rs.read_minute(D, 60, root=tmp_path).chains] == ["QQQ"]


def test_complete_minutes_after_a_cursor(tmp_path):
    for ts in (60, 120, 180):
        rs.write_minute(D, _m(ts), root=tmp_path)
    assert rs.complete_minutes(D, after_ts=60, root=tmp_path) == [120, 180]
    assert rs.last_complete(D, root=tmp_path) == 180


def test_gaps_are_recorded_with_their_reason(tmp_path):
    rs.write_gap(D, 240, "feed down", root=tmp_path)
    rs.write_gap(D, 180, "proxy unreachable", root=tmp_path)
    assert rs.gaps(D, root=tmp_path) == [(180, "proxy unreachable"), (240, "feed down")]
    assert rs.complete_minutes(D, root=tmp_path) == []


def test_prune_keeps_the_newest_sessions(tmp_path):
    for d in ("2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-13", D):
        rs.write_minute(d, _m(60), root=tmp_path)
    assert rs.prune(5, root=tmp_path) == ["2026-10-07"]
    assert rs.sessions(root=tmp_path) == ["2026-10-08", "2026-10-09", "2026-10-10",
                                          "2026-10-13", D]
    assert not list(tmp_path.glob("2026-10-07*"))


def test_a_reader_never_creates_a_store(tmp_path):
    rs.complete_minutes(D, root=tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists()


def test_the_default_root_is_resolved_at_call_time(tmp_path, monkeypatch):
    import repo_paths
    monkeypatch.setattr(repo_paths, "RAW_DIR", tmp_path)
    assert rs.session_path(D) == tmp_path / f"{D}.db"


def test_the_compression_level_comes_from_config(tmp_path, monkeypatch):
    import zlib as _zlib
    from shared import feed_config
    levels = []
    real = _zlib.compress
    monkeypatch.setattr(feed_config, "zlib_level", lambda: 1)
    monkeypatch.setattr(rs.zlib, "compress", lambda data, level=-1: levels.append(level) or real(data, level))
    rs.write_minute(D, _m(60), root=tmp_path)
    assert levels and set(levels) == {1}
    assert rs.read_minute(D, 60, root=tmp_path).chains[0][1]["symbol"] == "SPY"
