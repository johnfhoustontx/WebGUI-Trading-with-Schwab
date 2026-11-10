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
    the banner or the unlock, or read a live-order view. The one way in is the
    detail panel's LAZY import of ``live_order_link`` (next test)."""
    allowed = {"send_order.py", "idea_banner.py", "live_unlock.py", "live_orders.py"}
    for path in [ROOT / "live_main.py", ROOT / "live_screens.py", ROOT / "shell.py",
                 *(ROOT / "pages").rglob("*.py")]:
        if path.name in allowed or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "live_unlock" not in text, path
        assert "cache:live_order" not in text, path
        assert "send_order" not in text, path
        assert "idea_banner" not in text, path
        assert "/send-order" not in text, path
        assert "/live-orders" not in text, path
        assert "TODAY_KEY" not in text, path
        if path.name != "detail.py":
            assert "live_order_link" not in text, path
    screens = (ROOT / "live_screens.py").read_text(encoding="utf-8")
    assert "/send-order" not in screens and "/live-orders" not in screens


def test_the_detail_panel_reaches_the_ticket_only_off_the_public_origin():
    """``detail.py`` is drawn by the public Calculator too. Its one live-order
    import sits inside ``_live_button``, which runs only where the Copy
    ThinkorSwim order button was built: never on the public origin."""
    src = (ROOT / "pages" / "options" / "detail.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("live_order_link" in ast.unparse(n) for n in top)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_live_button")
    assert "import live_order_link" in ast.unparse(fn)
    assert "tos = None if _shell.is_public() else tos_order.order(s)" in src
    assert "            if tos:\n                _tos_button(tos)\n                _live_button(s)" in src


def test_the_page_never_names_an_account_number():
    """The dropdown is the aliases; nothing that carries digits is read."""
    assert "last4" not in SRC and "account_hash" not in SRC and "accounts()" not in SRC
    assert "live_orders_config.aliases()" in SRC


# ── Phase 2: every detail panel ──────────────────────────────────────────────

TODAY = __import__("datetime").date(2026, 11, 5)
PCS = {"symbol": "SPY", "type": "PCS", "expiration": EXP, "short_strike": 95,
       "long_strike": 90, "credit": 0.50}


@pytest.fixture
def mode(monkeypatch):
    from shared import live_orders_config
    box = {"mode": "preview"}
    monkeypatch.setattr(live_orders_config, "load",
                        lambda: {**live_orders_config.DEFAULTS, "mode": box["mode"]})
    return box


def test_a_ledger_idea_opens_its_own_ticket(mode):
    import live_order_link as link
    from pages.options import paper
    row = {"trade_id": "ab12cd34", "source": "trade_idea", "symbol": "SPY",
           "expiration": EXP, "legs": QUOTE["legs"]}
    sig = paper.synth_from_trade(row)
    assert link.offer(sig, TODAY) == {"trade": "ab12cd34"}
    # Any other Ledger row travels as its legs.
    mine = paper.synth_from_trade({**row, "source": None})
    assert mine["live_idea"] is None and "spec" in link.offer(mine, TODAY)
    mode["mode"] = "off"
    assert link.offer(sig, TODAY) is None


def test_a_scanner_row_becomes_a_spec_the_service_will_accept(mode):
    import live_order_link as link
    from shared import live_order
    got = link.offer(PCS, TODAY)
    spec = got["spec"]
    assert spec["symbol"] == "SPY" and spec["price"] == 0.5
    assert spec["label"] == "SPY Vertical"
    assert sorted((l["strike"], l["side"]) for l in spec["legs"]) == \
        [(90, "long"), (95, "short")]
    assert live_order.read_spec(spec, TODAY)["legs"]


def test_no_offer_for_what_cannot_be_sent(mode):
    import live_order_link as link
    covered = {"symbol": "INTC", "expiration": EXP, "legs": [
        {"kind": "stock", "side": "long", "qty": 1},
        {"kind": "call", "side": "short", "strike": 30, "qty": 1, "expiration": EXP}]}
    naked_call = {"symbol": "SPY", "type": "NAKED_CALL", "expiration": EXP,
                  "short_strike": 110, "credit": 1.0}
    expired = {**PCS, "expiration": "2026-11-04"}
    feed = {**PCS, "symbol": "FOP.ES"}
    for sig in (covered, naked_call, expired, feed, {}, None):
        assert link.offer(sig, TODAY) is None, sig


def test_the_stash_is_bounded_and_survives_a_reload():
    import live_order_link as link
    link._STASH.clear()
    first = link.stash({"n": 0})
    assert link.take(first) == {"n": 0} and link.take(first) == {"n": 0}
    for i in range(link.STASH_MAX):
        link.stash({"n": i + 1})
    assert link.take(first) is None and len(link._STASH) == link.STASH_MAX
    assert link.take("not-hex") is None and link.take(None) is None
    link._STASH.clear()


def test_the_panel_opens_the_ticket_as_a_window(monkeypatch):
    """Send Live Order opens the ticket OVER the panel: an idea by its id, any
    other trade by a freshly stashed spec. Nothing navigates away."""
    import live_order_link as link
    link._STASH.clear()
    opened = []
    monkeypatch.setattr(so, "open_window", lambda **kw: opened.append(kw))
    link.open_ticket({"trade": "ab12cd34"})
    link.open_ticket({"spec": {"symbol": "SPY"}})
    link.open_ticket(None)
    assert opened[0] == {"trade": "ab12cd34"}
    assert link.take(opened[1]["spec"]) == {"symbol": "SPY"} and len(opened) == 2
    link._STASH.clear()
    detail = (ROOT / "pages" / "options" / "detail.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(detail).body if isinstance(n, ast.FunctionDef)
              and n.name == "_live_button")
    assert "_link.open_ticket(found)" in ast.unparse(fn)
    assert "navigate" not in ast.unparse(fn)
    banner = (ROOT / "pages" / "idea_banner.py").read_text(encoding="utf-8")
    assert "send_order.open_window(trade=" in banner and "ui.navigate" not in banner


def test_the_page_opens_from_an_idea_or_a_held_spec_only():
    import live_order_link as link
    link._STASH.clear()
    assert so.source_for("ab12cd34", None) == ("ab12cd34", {"trade_id": "ab12cd34"})
    token = link.stash({"symbol": "SPY"})
    assert so.source_for(None, token) == (token, {"quote_id": token,
                                                  "spec": {"symbol": "SPY"}})
    view, why = so.source_for(None, "f" * 32)
    assert view is None and "no longer held" in why
    view, why = so.source_for("a b", None)
    assert view is None and "detail panel" in why
    link._STASH.clear()


def test_a_calendar_shows_no_false_unlimited_profit():
    cal = {**QUOTE, "expirations": [EXP, "2026-12-18"],
           "legs": [{"kind": "call", "side": "short", "strike": 100, "qty": 1,
                     "expiration": EXP},
                    {"kind": "call", "side": "long", "strike": 100, "qty": 1,
                     "expiration": "2026-12-18"}]}
    p = so.priced(cal, 2.00, 1)
    assert p["loss"] == 200.0 and p["profit_known"] is False
    assert so.priced(QUOTE, 1.50, 1)["profit_known"] is True


# ── Phase 3: cancel and today's orders ───────────────────────────────────────

def test_cancel_is_offered_only_for_a_sent_order_schwab_has_not_finished():
    assert so.can_cancel({"status": "sent", "order_status": "WORKING"})
    assert so.can_cancel({"status": "sent"})
    assert not so.can_cancel({"status": "sent", "order_status": "FILLED"})
    assert not so.can_cancel({"status": "unknown"})
    assert not so.can_cancel({"status": "previewed"})
    assert not so.can_cancel(None)


def test_todays_orders_rows_read_plainly():
    from pages import live_orders as lo
    row = {"ticket_id": "a" * 32, "alias": "IRA", "symbol": "SPY", "structure": "IRON_CONDOR",
           "contracts": 2, "limit": 1.5, "debit": False, "status": "sent",
           "order_status": "WORKING", "sent_at": "2026-11-06T10:02:11-06:00",
           "can_cancel": True}
    assert lo.order_line(row) == "10:02:11 · IRA · SPY iron condor · 2 at 1.50 credit"
    assert lo.status_words(row) == "Sent: working" and lo.tone(row) == "open"
    assert lo.tone({**row, "order_status": "FILLED"}) == "done"
    assert lo.tone({**row, "order_status": "REJECTED"}) == "bad"
    assert lo.tone({**row, "status": "unknown", "order_status": None}) == "bad"
    assert lo.any_open({"orders": [row]})
    assert not lo.any_open({"orders": [{**row, "can_cancel": False}]})
    assert not lo.any_open(None)


def test_the_pages_enqueue_only_on_the_live_order_stream():
    """Both pages ask the options service through ``bus_client.request`` on
    ``live_order.DOMAIN`` and nowhere else."""
    for name in ("send_order.py", "live_orders.py"):
        tree = ast.parse((ROOT / "pages" / name).read_text(encoding="utf-8"))
        calls = [ast.unparse(n.args[0]) for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "request"
                 and getattr(n.func.value, "id", None) == "bus_client"]
        assert calls == ["live_order.DOMAIN"], (name, calls)
