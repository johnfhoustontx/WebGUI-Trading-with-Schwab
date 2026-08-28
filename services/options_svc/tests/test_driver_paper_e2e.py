"""Redis-driven end-to-end of the ISOLATION INVARIANT (Phase 8 / Task 8.1).

Drives a ``driver_paper_create`` command through the REAL command path
(``handlers.handle_command`` with a fakeredis ``Bus(fake=True)``) and proves the
autonomous driver's trades land in its OWN paper book — the dedicated
``DRIVER_PAPER_DB`` — and that the user's MANUAL paper account is never touched.

No live proxy / Redis / engine: ``compute.DRIVER_PAPER_DB`` points at a
``tmp_path`` DB, the broker is a deterministic FILLED fake (no network — mirrors
``paper_broker.submit_order`` / ``build_order_response``), and the bus is
fakeredis. The handler ``driver_paper_create`` branch calls
``compute.open_driver_position(signal, qty)`` with NO explicit broker, so we
monkeypatch the module-level ``paper_broker.submit_order`` the wrapper falls back
to (``broker = broker or paper_broker``).

End-to-end this exercises: command → open_driver_position (size → submit → re-size
off the fill → reserve BP → insert into the DRIVER db) → refresh_driver_paper
(publish ``cache:options:driver_paper_account`` + ``cache:options:driver_paper_perf``
with their change events).
"""
from shared.bus import Bus
from shared.contracts.envelope import Command
# Importing the service package first runs its module-top sys.path glue
# (OPTIONS_SCANNER → sys.path), so the ``paper_account_db``/``paper_broker``/
# ``paper_sizing`` engine modules resolve when imported INSIDE the tests below
# (the same idiom as test_driver_account.py — never import them at module top).
from services.options_svc import handlers
from services.options_svc import compute


def _filled_broker(fill_price):
    """A deterministic FILLED ``submit_order`` — no proxy/network. Returns the
    Schwab-shaped dict ``paper_broker.build_order_response`` produces (status /
    price / enteredTime / filledQuantity)."""
    def submit_order(order, client):
        return {"orderId": 1, "status": "FILLED", "price": fill_price,
                "enteredTime": "2026-06-25T14:00:00",
                "filledQuantity": order["quantity"]}
    return submit_order


def _driver_signal(**o):
    # width=2.0 / entry_credit=1.50 → size_contracts(1.5, 2.0) sizes to >=1
    # (max_loss_per=(2-1.5)*100=$50), so the requested qty actually opens a
    # position rather than RISK_TOO_HIGH-rejecting.
    base = {"signal_id": "MU_PCS_e2e", "symbol": "MU", "strategy": "PCS",
            "short_strike": 105.0, "long_strike": 103.0, "call_short": None,
            "call_long": None, "width": 2.0, "expiration": "2026-07-10",
            "dte_at_entry": 15, "entry_credit": 1.50, "source": "driver"}
    base.update(o)
    return base


def test_driver_paper_create_e2e_isolation(tmp_path, monkeypatch):
    """The full ``driver_paper_create`` command path opens into the DRIVER db,
    publishes both driver views reflecting the open position, and leaves the
    MANUAL paper account completely untouched."""
    import paper_account_db
    import paper_broker
    import paper_sizing

    # ── isolate the driver book onto a tmp DB (the manual default is never the
    #    target) and supply a FILLED broker the wrapper's lazy fallback resolves.
    driver_db = tmp_path / "paper_account_driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", driver_db)
    monkeypatch.setattr(paper_broker, "submit_order", _filled_broker(1.50))
    # The MANUAL book is redirected to tmp as well. It used to be the REAL
    # data/paper_account.db: this test proved cross-book isolation by reading
    # production. paper_account_db resolves db_path at CALL time (db_path=None
    # -> DEFAULT_DB_PATH), so patching the attribute redirects the code under
    # test too — a wrongly-written manual position still lands here and still
    # fails the assertion. The invariant is unchanged; only the file moves.
    monkeypatch.setattr(paper_account_db, "DEFAULT_DB_PATH",
                        tmp_path / "paper_account_manual.db")

    # The requested (guardrail-clamped) qty. width 2 / credit 1.5 → the engine
    # sizes to 5 off the fill; qty=1 is below that ceiling so exactly 1 opens.
    requested_qty = 1
    sized = paper_sizing.size_contracts(1.50, 2.0)[0]
    assert sized >= requested_qty            # the request is a real openable qty

    # ── baseline the MANUAL account's open-position count BEFORE the cycle. We
    #    read it via the default path WITHOUT mutating it (fetch only). This is
    #    the read-only isolation reference; we re-read it after and assert == .
    #    (Redirected to tmp above — this no longer reads the production file.)
    manual_db = paper_account_db.DEFAULT_DB_PATH
    manual_open_before = len(paper_account_db.fetch_open_positions(manual_db))

    bus = Bus(fake=True)
    handlers.handle_command(
        bus, Command(type="driver_paper_create",
                     args={"signal": _driver_signal(), "qty": requested_qty}))

    # 1) the position landed in the DRIVER db (symbol/strikes at the clamped qty)
    driver_open = paper_account_db.fetch_open_positions(driver_db)
    assert len(driver_open) == 1
    pos = driver_open[0]
    assert pos["symbol"] == "MU" and pos["strategy"] == "PCS"
    assert pos["short_strike"] == 105.0 and pos["long_strike"] == 103.0
    assert pos["quantity"] == requested_qty
    assert pos["entry_credit"] == 1.50

    # 2) cache:options:driver_paper_account published + reflects the open position
    acct = bus.cache_get("cache:options:driver_paper_account")
    assert acct is not None
    assert acct.payload["has_account"] is True
    assert acct.payload["snapshot"]["open_count"] >= 1
    acct_pos = acct.payload["positions"]
    assert any(p["symbol"] == "MU" and p["quantity"] == requested_qty
               for p in acct_pos)

    # 3) cache:options:driver_paper_perf published + scorecard reflects the trade
    perf = bus.cache_get("cache:options:driver_paper_perf")
    assert perf is not None
    assert perf.payload["total_trades"] >= 1
    assert perf.payload["open"] >= 1

    # 4) ISOLATION: the MANUAL account is untouched — its open-position count is
    #    unchanged, and not one of the published driver positions belongs to it.
    manual_open_after = len(paper_account_db.fetch_open_positions(manual_db))
    assert manual_open_after == manual_open_before
    # and the DRIVER db is genuinely a different file than the manual default.
    assert str(driver_db) != str(manual_db)


def test_driver_paper_create_e2e_does_not_write_manual_db(tmp_path, monkeypatch):
    """A second guard on the same invariant from the other direction: the manual
    DB FILE's open positions for THIS signal's symbol/strikes are absent — proof
    the create wrote to the driver book exclusively (no cross-book leak)."""
    import paper_account_db
    import paper_broker

    driver_db = tmp_path / "paper_account_driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", driver_db)
    monkeypatch.setattr(paper_broker, "submit_order", _filled_broker(1.50))
    # Manual book on tmp — see the sibling test. Was the real production file.
    monkeypatch.setattr(paper_account_db, "DEFAULT_DB_PATH",
                        tmp_path / "paper_account_manual.db")

    bus = Bus(fake=True)
    handlers.handle_command(
        bus, Command(type="driver_paper_create",
                     args={"signal": _driver_signal(signal_id="MU_PCS_leak"),
                           "qty": 1}))

    # the driver book has it...
    assert any(p["signal_id"] == "MU_PCS_leak"
               for p in paper_account_db.fetch_open_positions(driver_db))
    # ...and the manual default book does NOT (this signal never reached it).
    manual = paper_account_db.fetch_open_positions(paper_account_db.DEFAULT_DB_PATH)
    assert not any(p.get("signal_id") == "MU_PCS_leak" for p in manual)
