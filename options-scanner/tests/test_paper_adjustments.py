def test_insert_and_list_adjustment(tmp_path):
    from paper_account_db import init_db, insert_adjustment, list_adjustments
    db = tmp_path / "pa.db"
    init_db(db)
    aid = insert_adjustment(db, position_id=42, action="convert_ic",
                            legs=[{"side": "SELL", "right": "CALL", "strike": 505}],
                            gross_cash=120.0, commission=2.6, net_cash=117.4,
                            reason="rescue")
    assert aid >= 1
    rows = list_adjustments(db, 42)
    assert len(rows) == 1
    assert rows[0]["action"] == "convert_ic"
    assert rows[0]["legs"][0]["right"] == "CALL"
    assert rows[0]["net_cash"] == 117.4
    assert list_adjustments(db, 999) == []


def test_parent_position_id_column_exists(tmp_path):
    from paper_account_db import init_db, connect
    db = tmp_path / "pa.db"
    init_db(db)
    conn = connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)")}
    finally:
        conn.close()
    assert "parent_position_id" in cols
