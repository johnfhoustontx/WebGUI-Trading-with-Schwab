"""tools/seed_alert_log.py: the held contract-day alerts copied into the record once."""
import sqlite3

from tools import seed_alert_log

gh = seed_alert_log.gh  # the tool puts options-scanner on sys.path


def _source(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(gh.FLOW_DAY_SCHEMA_SQL)
    for r in rows:
        conn.execute(
            "INSERT INTO flow_contract_days (session_date, alert_id, symbol, osi, side, "
            "strike, expiry, alert_type, fired_ts, volume, poll_bought, poll_sold, "
            "poll_unlabelled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)", r)
    conn.commit()
    return conn


_ROWS = [
    ("2026-11-12", "SPY|uoa|call|500|2026-11-14", "SPY", "SPY  261114C00500000", "call",
     500.0, "2026-11-14", "uoa", 1794499200, 900.0),
    ("2026-11-13", "QQQ|big_delta|put|400|2026-11-21", "QQQ", "QQQ  261121P00400000",
     "put", 400.0, "2026-11-21", "big_delta", 1794585600, 5000.0),
]


def test_seed_copies_each_alert_once(tmp_path):
    src = _source(tmp_path / "gex.db", _ROWS)
    out = tmp_path / "alert_log.db"
    assert seed_alert_log.seed(src, db_path=out) == 2
    assert seed_alert_log.seed(src, db_path=out) == 0
    rows = seed_alert_log.alert_log.load(db_path=out)
    assert [(r["alert_id"], r["type"], r["ts"], r["source"]) for r in rows] == [
        ("SPY|uoa|call|500|2026-11-14", "uoa", 1794499200, "seed"),
        ("QQQ|big_delta|put|400|2026-11-21", "big_delta", 1794585600, "seed")]
    assert rows[1]["side"] == "put" and rows[1]["session_date"] == "2026-11-13"


def test_a_row_with_no_fired_time_is_skipped(tmp_path):
    bad = [("2026-11-12", "SPY|uoa|put|1|x", "SPY", "O", "put", 1.0, "x", "uoa",
            None, 1.0)]
    src = _source(tmp_path / "gex.db", bad + _ROWS[:1])
    assert seed_alert_log.seed(src, db_path=tmp_path / "out.db") == 1


def test_main_reads_a_copy_and_prints_the_count(tmp_path, capsys):
    _source(tmp_path / "copy.db", _ROWS).close()
    out = tmp_path / "out.db"
    assert seed_alert_log.main(["--db", str(tmp_path / "copy.db"),
                                "--log", str(out)]) == 0
    assert "2 alerts added" in capsys.readouterr().out


def test_the_same_contract_on_two_days_is_seeded_twice(tmp_path):
    again = ("2026-11-13",) + _ROWS[0][1:8] + (1794585600, 700.0)
    src = _source(tmp_path / "gex.db", [_ROWS[0], again])
    assert seed_alert_log.seed(src, db_path=tmp_path / "out.db") == 2
