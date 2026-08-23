"""Tests for the Gamma briefing history store (gamma_briefing_history_db)."""
import gamma_briefing_history_db as gdb


def _analysis(bias=-25, headline="SPX pinned."):
    return {"regime": "Short gamma", "bias": bias, "headline": headline,
            "narrative": "n", "why": "w",
            "indices": [{"symbol": "$SPX", "spot": 7400, "gamma_flip": 7395}]}


def _ins(conn, date, slot, bias=-25, ts="2026-07-02T08:00:00-05:00", headline="h"):
    gdb.insert_briefing(conn, date=date, slot=slot, generated_at=ts,
                        symbol_scope="$SPX/SPY/QQQ", model="claude-sonnet-5",
                        bias=bias, headline=headline, analysis=_analysis(bias, headline))


def test_insert_get_roundtrip(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    _ins(conn, "2026-07-02", "midday", bias=-30, headline="Pinned")
    got = gdb.get_briefing(conn, "2026-07-02", "midday")
    assert got["slot"] == "midday" and got["bias"] == -30.0
    assert got["headline"] == "Pinned"
    assert got["analysis"]["indices"][0]["gamma_flip"] == 7395  # full dict preserved


def test_get_missing_returns_none(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    assert gdb.get_briefing(conn, "2026-07-02", "close") is None


def test_replace_on_same_date_slot(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    _ins(conn, "2026-07-02", "open", bias=10, headline="first")
    _ins(conn, "2026-07-02", "open", bias=20, headline="second")  # re-run same slot
    rows = gdb.briefings_for_date(conn, "2026-07-02")
    assert len(rows) == 1 and rows[0]["headline"] == "second" and rows[0]["bias"] == 20.0


def test_distinct_slots_coexist(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    _ins(conn, "2026-07-02", "premarket", ts="2026-07-02T08:00:00-05:00")
    _ins(conn, "2026-07-02", "close", ts="2026-07-02T14:58:00-05:00")
    _ins(conn, "2026-07-02", "manual-1432", ts="2026-07-02T14:32:00-05:00")
    rows = gdb.briefings_for_date(conn, "2026-07-02")
    # chronological within the day (by generated_at): premarket, manual-1432, close
    assert [r["slot"] for r in rows] == ["premarket", "manual-1432", "close"]


def test_list_date_filter_and_order(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    _ins(conn, "2026-07-01", "midday")
    _ins(conn, "2026-07-02", "midday")
    _ins(conn, "2026-07-03", "midday")
    rows = gdb.list_briefings(conn, start_date="2026-07-02")
    assert [r["date"] for r in rows] == ["2026-07-03", "2026-07-02"]  # newest first
    # metadata-only by default (no analysis payload)
    assert "analysis" not in rows[0]
    assert "analysis" in gdb.list_briefings(conn, with_analysis=True)[0]


def test_bad_bias_stored_null(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    gdb.insert_briefing(conn, date="2026-07-02", slot="open", generated_at="t",
                        symbol_scope=None, model=None, bias="oops",
                        headline="h", analysis=_analysis())
    assert gdb.get_briefing(conn, "2026-07-02", "open")["bias"] is None


def test_purge_keeps_recent_dates(tmp_path):
    conn = gdb.connect(tmp_path / "b.db")
    for d in ("2026-06-28", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"):
        _ins(conn, d, "midday")
    deleted = gdb.purge(conn, keep_days=2)
    assert deleted == 3
    remaining = {r["date"] for r in gdb.list_briefings(conn)}
    assert remaining == {"2026-07-01", "2026-07-02"}
    assert gdb.purge(conn, keep_days=0) == 0  # no-op keeps everything
