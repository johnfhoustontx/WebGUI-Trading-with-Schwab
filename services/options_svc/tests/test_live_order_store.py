"""services/options_svc/live_order_store.py: the ticket journal."""
import pytest

from services.options_svc import live_order_store as store


def _ticket(**kw):
    t = {"ticket_id": "t1", "trade_id": "ab12cd34", "idea_posted": "x", "alias": "IRA",
         "account_hash": "H", "symbol": "SPY", "structure": "VERTICAL", "contracts": 2,
         "limit_price": 1.5, "debit": 1, "max_loss": 300.0,
         "payload": {"price": "1.50"}, "digest": "d", "status": "previewed",
         "created_at": 100.0, "updated_at": 100.0, "expires_at": 220.0}
    t.update(kw)
    return t


@pytest.fixture
def db(tmp_path):
    return tmp_path / "live_orders.db"


def test_insert_and_get_round_trip(db):
    assert store.insert(_ticket(), db_path=db)
    t = store.get("t1", db_path=db)
    assert t["payload"] == {"price": "1.50"} and t["debit"] is True
    assert store.get("nope", db_path=db) is None


def test_an_id_is_used_once(db):
    assert store.insert(_ticket(), db_path=db)
    assert not store.insert(_ticket(contracts=9), db_path=db)
    assert store.get("t1", db_path=db)["contracts"] == 2


def test_a_claim_succeeds_once_and_only_in_time(db):
    store.insert(_ticket(), db_path=db)
    assert not store.claim("t1", 220.0, db_path=db)          # at its expiry: too late
    assert store.claim("t1", 150.0, db_path=db)
    assert not store.claim("t1", 151.0, db_path=db)          # the second delivery
    assert store.get("t1", db_path=db)["status"] == "sending"


def test_update_takes_only_result_columns(db):
    store.insert(_ticket(), db_path=db)
    store.update("t1", db_path=db, status="sent", order_id="9", messages=["a"])
    t = store.get("t1", db_path=db)
    assert (t["status"], t["order_id"], t["messages"]) == ("sent", "9", ["a"])
    for bad in ({"payload": {}}, {"account_hash": "X"}, {"status": "done"}):
        with pytest.raises(ValueError):
            store.update("t1", db_path=db, **bad)


def test_expire_stale(db):
    store.insert(_ticket(), db_path=db)
    store.insert(_ticket(ticket_id="t2", expires_at=999.0), db_path=db)
    assert store.expire_stale(300.0, db_path=db) == 1
    assert store.get("t1", db_path=db)["status"] == "expired"
    assert store.get("t2", db_path=db)["status"] == "previewed"


def test_the_path_is_resolved_when_called(monkeypatch, tmp_path):
    monkeypatch.setattr(store.repo_paths, "LIVE_ORDERS_DB", tmp_path / "x.db")
    store.insert(_ticket())
    assert (tmp_path / "x.db").exists()


def test_the_journal_is_never_excluded_from_the_backup():
    """It is the record of every order sent to the account. ``db_paths`` takes
    every ``*.db`` but those listed in ``DB_EXCLUDE``."""
    import sys
    import repo_paths
    sys.path.insert(0, str(repo_paths.REPO_ROOT / "tools"))
    import backup_local
    rel = repo_paths.LIVE_ORDERS_DB.relative_to(repo_paths.REPO_ROOT).as_posix()
    assert not any(rel == x or rel.startswith(x + "/") for x in backup_local.DB_EXCLUDE)


def test_sent_since_lists_what_went_to_schwab_newest_first(db):
    store.insert(_ticket(), db_path=db)                                   # never sent
    store.insert(_ticket(ticket_id="t2", status="sent", sent_at=150.0), db_path=db)
    store.insert(_ticket(ticket_id="t3", status="unknown", sent_at=170.0), db_path=db)
    store.insert(_ticket(ticket_id="t4", status="sent", sent_at=50.0), db_path=db)
    assert [t["ticket_id"] for t in store.sent_since(100.0, db_path=db)] == ["t3", "t2"]
    assert [t["ticket_id"] for t in store.sent_since(0.0, limit=1, db_path=db)] == ["t3"]
