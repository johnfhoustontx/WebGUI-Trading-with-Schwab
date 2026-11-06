"""pages/send_order.py: the ticket page's pure pieces, and what it may reach."""
import ast
import pathlib

import pytest

from pages import send_order as so

EXP = "2026-11-06"
QUOTE = {
    "trade_id": "ab12cd34", "symbol": "SPY", "structure": "VERTICAL",
    "strategy": "BULL_CALL_SPREAD", "expiration": EXP, "debit": True,
    "mode": "live", "in_session": True, "live_price": 1.50, "posted_price": 1.40,
    "legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1, "expiration": EXP,
              "symbol": "SPY   261106C00100000", "mid": 2.5},
             {"kind": "call", "side": "short", "strike": 105, "qty": 1, "expiration": EXP,
              "symbol": "SPY   261106C00105000", "mid": 1.0}],
    "exits": {"pcts": {"target_pct": 100.0, "stop_pct": 50.0}, "stop_on_order": False,
              "exit_tif": "GTC"},
}
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = pathlib.Path(so.__file__).read_text(encoding="utf-8")


def test_trade_ids_are_allow_listed():
    assert so.valid_trade("ab12cd34")
    for bad in (None, "", "ab:cd", "a b", "x" * 41, 123):
        assert not so.valid_trade(bad)


@pytest.mark.parametrize("total, tone", [(900.0, "ok"), (1000.0, "ok"), (1000.01, "warn"),
                                         (5000.0, "warn"), (5000.01, "over"), (None, "over")])
def test_risk_tone(total, tone):
    """The operator's rule: past $1,000 is allowed, in the warning colour."""
    assert so.risk_tone(total, 1000.0, 5000.0) == tone


@pytest.mark.parametrize("raw, want", [(3, 3), (3.0, 3), (2.5, None), (0, None),
                                       (True, None), (None, None), (float("nan"), None)])
def test_as_contracts(raw, want):
    assert so.as_contracts(raw) == want


def test_priced_follows_the_typed_limit_and_size():
    p = so.priced(QUOTE, 1.50, 4)
    assert p["loss"] == 600.0 and p["profit"] == 1400.0
    assert (p["target"], p["stop"]) == (3.00, 0.75)
    assert p["stop_on_order"] is False
    assert so.priced(QUOTE, None, 4) is None
    assert so.priced({}, 1.5, 4) is None


def test_the_paste_text_is_the_scripts():
    text = so.paste_text(QUOTE, 1.50, 3)
    assert text.startswith("BUY +3 VERTICAL SPY 100") and "@1.50 LMT" in text


@pytest.mark.parametrize("ticket, quote, now, ready, said", [
    (None, QUOTE, 0, False, "Preview"),
    ({"status": "refused"}, QUOTE, 0, False, "Preview"),
    ({"status": "previewed", "expires_epoch": 100}, {**QUOTE, "mode": "preview"}, 0,
     False, "not on"),
    ({"status": "previewed", "expires_epoch": 100}, {**QUOTE, "in_session": False}, 0,
     False, "regular session"),
    ({"status": "previewed", "expires_epoch": 100}, QUOTE, 100, False, "expired"),
    ({"status": "previewed", "expires_epoch": 100}, QUOTE, 99, True, ""),
])
def test_send_lights_only_on_a_live_passed_preview(ticket, quote, now, ready, said):
    got, why = so.send_ready(ticket, quote, now)
    assert got is ready and said in why


def test_a_sent_ticket_locks_the_page_and_is_followed_until_final():
    assert so.locked({"status": "sent"}) and so.locked({"status": "unknown"})
    assert not so.locked({"status": "previewed"}) and not so.locked(None)
    assert so.still_working({"status": "sent", "order_status": "WORKING"})
    assert not so.still_working({"status": "sent", "order_status": "FILLED"})
    assert so.still_working({"status": "unknown"})
    assert not so.still_working({"status": "rejected"})


def test_status_text():
    assert so.status_text({"status": "sent", "order_id": "9001",
                           "order_status": "PENDING_ACTIVATION"}) == \
        "Sent to Schwab: order 9001 (pending activation)"


def test_it_enqueues_only_on_the_live_order_stream():
    tree = ast.parse(SRC)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "bus_client.request"]
    assert [ast.unparse(c.args[0]) for c in calls] == ["live_order.DOMAIN"]
    assert "enqueue_command" not in SRC


def test_no_public_module_reaches_live_orders():
    """The public process renders page modules: none may import the ticket page,
    the banner or the unlock, or read a live-order view."""
    allowed = {"send_order.py", "idea_banner.py", "live_unlock.py"}
    for path in [ROOT / "live_main.py", ROOT / "live_screens.py", ROOT / "shell.py",
                 *(ROOT / "pages").rglob("*.py")]:
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert "live_unlock" not in text, path
        assert "cache:live_order" not in text, path
        assert "send_order" not in text, path
        assert "idea_banner" not in text, path
    assert "/send-order" not in (ROOT / "live_screens.py").read_text(encoding="utf-8")


def test_the_page_never_names_an_account_number():
    """The dropdown is the aliases; nothing that carries digits is read."""
    assert "last4" not in SRC and "account_hash" not in SRC and "accounts()" not in SRC
    assert "live_orders_config.aliases()" in SRC


def test_the_ledger_offers_the_ticket_for_an_idea_row_only(monkeypatch):
    from pages.options import paper
    from shared import live_orders_config
    monkeypatch.setattr(live_orders_config, "load",
                        lambda: {**live_orders_config.DEFAULTS, "mode": "preview"})
    idea = {"trade_id": "ab12cd34", "source": "trade_idea"}
    assert paper.offers_live_order(idea)
    assert not paper.offers_live_order({**idea, "source": "daily_condor"})
    assert not paper.offers_live_order({**idea, "source": None})
    assert not paper.offers_live_order({**idea, "trade_id": "a b"})
    monkeypatch.setattr(live_orders_config, "load",
                        lambda: {**live_orders_config.DEFAULTS, "mode": "off"})
    assert not paper.offers_live_order(idea)
