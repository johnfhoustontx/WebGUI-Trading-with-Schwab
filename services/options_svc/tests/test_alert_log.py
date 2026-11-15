"""The alert record: every fired flow alert, written once and kept.

Design: docs/plans/2026-11-14-alert-quality-design.md
"""
import json
import sqlite3

from services.options_svc import alert_log

TS = 1794672000          # 2026-11-14 10:00 CT (checked below)


def test_ts_is_ten_central_on_the_test_day():
    import datetime as _d
    from zoneinfo import ZoneInfo
    assert TS == int(_d.datetime(2026, 11, 14, 10, 0,
                                 tzinfo=ZoneInfo("America/Chicago")).timestamp())


def _alert(**over):
    a = {"id": "SPY|uoa|call|500|2026-11-20", "type": "uoa", "side": "call",
         "symbol": "SPY", "ts": TS, "text": "x", "volume": 900}
    a.update(over)
    return a


def test_record_writes_each_alert_once(tmp_path):
    db = tmp_path / "alert_log.db"
    assert alert_log.record([_alert()], db_path=db) == 1
    assert alert_log.record([_alert()], db_path=db) == 0
    (row,) = alert_log.load(db_path=db)
    assert row["alert_id"] == "SPY|uoa|call|500|2026-11-20"
    assert row["session_date"] == "2026-11-14"
    assert row["ts"] == TS and row["symbol"] == "SPY"
    assert row["type"] == "uoa" and row["side"] == "call"
    assert row["source"] == "live"


def test_detail_keeps_everything_but_text(tmp_path):
    db = tmp_path / "alert_log.db"
    alert_log.record([_alert()], db_path=db)
    conn = sqlite3.connect(str(db))
    (raw,) = conn.execute("SELECT detail_json FROM alerts").fetchone()
    conn.close()
    detail = json.loads(raw)
    assert detail == {"volume": 900}


def test_alert_without_id_or_ts_is_skipped(tmp_path):
    db = tmp_path / "alert_log.db"
    bad = [_alert(id=None), _alert(id="a", ts=None), _alert(id="b", ts="soon"),
           "not a dict"]
    assert alert_log.record(bad, db_path=db) == 0
    assert alert_log.load(db_path=db) == []


def test_load_filters_types_and_missing_file(tmp_path):
    db = tmp_path / "alert_log.db"
    alert_log.record([_alert(), _alert(id="SPY|hiro_flip|x|1", type="hiro_flip",
                                       ts=TS + 60)], db_path=db)
    assert [r["type"] for r in alert_log.load(db_path=db)] == ["uoa", "hiro_flip"]
    assert [r["type"] for r in alert_log.load(db_path=db, types=["uoa"])] == ["uoa"]
    assert alert_log.load(db_path=tmp_path / "none.db") == []


def test_seed_source_is_kept(tmp_path):
    db = tmp_path / "alert_log.db"
    alert_log.record([_alert()], source="seed", db_path=db)
    assert alert_log.load(db_path=db)[0]["source"] == "seed"


def test_default_path_sits_beside_the_scalp_store():
    import repo_paths
    from pathlib import Path
    assert Path(alert_log.DB_PATH).parent == Path(repo_paths.SCALP_DB).parent


def test_same_id_on_two_days_is_two_alerts(tmp_path):
    """A contract-level id carries no date (``SPY|uoa|call|600|2026-12-19``) and
    the seen-set is per day, so the same contract can fire again tomorrow."""
    db = tmp_path / "alert_log.db"
    assert alert_log.record([_alert()], db_path=db) == 1
    assert alert_log.record([_alert(ts=TS + 86400)], db_path=db) == 1
    assert alert_log.record([_alert(ts=TS + 86400 + 60)], db_path=db) == 0
    assert [r["session_date"] for r in alert_log.load(db_path=db)] == [
        "2026-11-14", "2026-11-15"]
