"""tools/breadth_backfill.py: Schwab's daily history and TradingView CSVs into the
breadth store, and the status listing."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.market_svc import breadth_daily as bd
from shared import breadth_history as bh
from tools import breadth_backfill as tool

CT = ZoneInfo("America/Chicago")


def bar(day, close, **extra):
    """A Schwab daily candle: stamped midnight Central of its session."""
    y, m, d = (int(x) for x in day.split("-"))
    ms = int(dt.datetime(y, m, d, tzinfo=CT).timestamp() * 1000)
    return {"datetime": ms, "close": close, "high": extra.get("high", close),
            "low": extra.get("low", close), "volume": extra.get("volume", 0)}


def test_schwab_backfill_writes_history(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    data = {"$SPX": [bar("2026-11-24", 7201.09), bar("2026-11-25", 7182.78)],
            "$ADVN": [bar("2026-11-24", 1415.0), bar("2026-11-25", 1886.0)],
            "$UVOL": [bar("2026-11-25", 953043.0)], "$DVOL": [bar("2026-11-25", 265676.0)]}
    got = tool.backfill_schwab(conn, lambda s, **k: data.get(s), 20, dt.date(2026, 11, 27))
    # $SPX: close, high and low on two days (6); advancers on two days (2); one up share (1)
    assert got["written"] == 9 and got["invalid"] == 0
    assert bh.series_values(conn, "nyse_up_share") == [
        ("2026-11-25", pytest.approx(0.7820, abs=1e-4))]


def test_schwab_backfill_asks_for_the_years_given(tmp_path):
    asked = []

    def fetch(s, **k):
        asked.append((s, k))
        return [bar("2026-11-25", 1.0)]

    tool.backfill_schwab(bh.connect(tmp_path / "b.db"), fetch, 7, dt.date(2026, 11, 27))
    assert asked[0] == ("$SPX", {"period_type": "year", "period": 7})
    assert [s for s, _ in asked] == list(bd.HISTORY_SYMBOLS)


def test_schwab_backfill_refuses_to_run_without_the_index(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    with pytest.raises(RuntimeError):
        tool.backfill_schwab(conn,
                             lambda s, **k: None if s == "$SPX" else [bar("2026-11-25", 1.0)],
                             20, dt.date(2026, 11, 27))
    assert bh.coverage(conn) == []


def test_schwab_backfill_refuses_an_empty_index(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    with pytest.raises(RuntimeError):
        tool.backfill_schwab(conn,
                             lambda s, **k: [] if s == "$SPX" else [bar("2026-11-25", 1.0)],
                             20, dt.date(2026, 11, 27))
    assert bh.coverage(conn) == []


def test_schwab_backfill_names_a_symbol_that_failed(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    data = {"$SPX": [bar("2026-11-25", 7182.78)], "$ADVN": [bar("2026-11-25", 1886.0)]}
    got = tool.backfill_schwab(conn, lambda s, **k: data.get(s), 20, dt.date(2026, 11, 27))
    assert "$ADVN" not in got["missing"] and "$DECN" in got["missing"]
    assert "$SPX" not in got["missing"]


def test_read_csv(tmp_path):
    f = tmp_path / "lows.csv"
    f.write_text("day,value\n2020-03-23,0.01\n2020-03-24,412\n2020-03-25,\n2020-03-26,x\n",
                 encoding="utf-8")
    assert tool.read_csv(f, "tv_nyse_3m_lows") == [
        ("tv_nyse_3m_lows", "2020-03-23", 0.0, "tradingview"),
        ("tv_nyse_3m_lows", "2020-03-24", 412.0, "tradingview")]


def test_read_csv_keeps_a_small_ratio(tmp_path):
    f = tmp_path / "pc.csv"
    f.write_text("day,value\n2020-03-23,0.01\n", encoding="utf-8")
    assert tool.read_csv(f, "tv_equity_put_call")[0][2] == 0.01     # only counts get the zero rule


def test_read_csv_refuses_a_series_that_is_not_tradingviews(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("day,value\n2020-03-23,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        tool.read_csv(f, "nyse_adv")


def test_status_lists_each_series(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], dt.date(2026, 11, 27))
    assert tool.status_lines(conn) == ["nyse_adv  2026-11-25  2026-11-25  1"]


def test_main_loads_a_csv(tmp_path, capsys):
    f = tmp_path / "p.csv"
    f.write_text("day,value\n2020-03-23,4.2\n", encoding="utf-8")
    db = tmp_path / "b.db"
    assert tool.main(["--source", "csv", "--series", "tv_pct_above_50", str(f),
                      "--db", str(db)]) == 0
    assert "written 1" in capsys.readouterr().out
    conn = bh.connect(db)
    assert bh.series_values(conn, "tv_pct_above_50") == [("2020-03-23", 4.2)]


def test_main_status(tmp_path, capsys):
    db = tmp_path / "b.db"
    bh.upsert(bh.connect(db), [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")],
              dt.date(2026, 11, 27))
    assert tool.main(["--status", "--db", str(db)]) == 0
    assert "nyse_adv  2026-11-25  2026-11-25  1" in capsys.readouterr().out
