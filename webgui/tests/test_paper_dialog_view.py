"""The Paper dialog's decisions - PURE (``handoff.paper_dialog_view``).

Everything the dialog decides lives in one pure function and ``send_to_paper``
only paints it. Fixtures are in the shape the options service publishes at
``options:ledger_caps``; every expected number is worked by hand in a comment.
The behaviour tests at the foot build the REAL dialog (``kit.confirm``) and
drive its ``run`` coroutine, so they read the elements the reader sees rather
than a hand-written stand-in for them.
"""
import ast
import asyncio
import inspect

from nicegui import ui

from pages.options import book_fit, handoff
from pages.options.theme import MUTED, TXT_NEG, TXT_POS

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
          "max_risk_per_trade": 750.0}

# One open MSFT spread in the same sector and expiry as the candidate: room on
# every rung, but not an empty book (so the sector/expiry lines count something).
CAPS = {"limits": LIMITS, "equity": 25000.0,
        "open": [{"symbol": "MSFT", "expiration": "2026-10-17",
                  "max_loss_total": 300.0, "sector": "Information Technology"}],
        "sectors": {"ORCL": "Information Technology",
                    "MSFT": "Information Technology"},
        "unmapped_prefix": "?"}

# A $1.90-per-share put credit spread: booked_risk = round(1.90 * q * 100, 2),
# so $190 at one contract, $570 at three, $760 at four.
SIG = {"symbol": "ORCL", "type": "PCS", "expiration": "2026-10-17",
       "ledger_risk_basis": {"per_share": 1.90}}

KNOWN_CLASSES = {TXT_POS, TXT_NEG, MUTED}


def test_a_fitting_trade_on_a_book_with_room():
    v = handoff.paper_dialog_view(SIG, CAPS, 1)
    assert v["title"] == "Paper trade ORCL Credit spread — put · 2026-10-17"
    assert v["risk_text"] == "Risk $190 per contract"
    # Seven rungs: per trade $190 <= $750; deployment $300 + $190 = $490 <= 20%
    # of $25,000 = $5,000; ORCL position 1 of 3; ORCL risk $190 <= $750; sector
    # position 2 of 5; sector risk $300 + $190 = $490 <= $1,500; expiry 2 of 5.
    assert len(v["lines"]) == 7
    assert all(line["class"] in KNOWN_CLASSES for line in v["lines"])
    assert all(line["class"] == TXT_POS for line in v["lines"])
    assert v["can_create"] is True
    assert v["block_text"] == "" and v["fits_text"] == "" and v["note"] == ""
    # max quantity: per trade floor(750 / 190) = 3; ORCL risk floor(750 / 190)
    # = 3; sector floor((1500 - 300) / 190) = 6; deployment floor(4700 / 190)
    # = 24 -> 3.
    p = book_fit.preview(SIG, CAPS, 1)
    assert p["max_quantity"] == 3
    assert v["qty_max"] == p["max_quantity"] == 3


def test_a_quantity_that_breaches_per_trade_blocks_and_says_what_fits():
    # Four contracts: $760 > the $750 per-trade limit (ORCL's $750 symbol-risk
    # line binds too; per trade comes first in display order). Three fit: $570.
    v = handoff.paper_dialog_view(SIG, CAPS, 4)
    p = book_fit.preview(SIG, CAPS, 4)
    assert v["can_create"] is False
    assert v["block_text"] == p["block_text"]
    assert v["block_text"] == "Risks $760, over the $750 per-trade limit"
    assert v["fits_text"] == "Up to 3 contracts fit."
    assert v["qty_max"] == 3
    assert v["lines"][0]["class"] == TXT_NEG


def test_exactly_one_contract_fitting_uses_the_singular():
    # Per-trade limit $250 and a $190 contract: two is $380, so exactly one fits.
    caps = {**CAPS, "limits": {**LIMITS, "max_risk_per_trade": 250.0}}
    v = handoff.paper_dialog_view(SIG, caps, 2)
    assert v["fits_text"] == "Up to 1 contract fits."
    assert v["can_create"] is False


def test_a_breach_where_nothing_fits():
    # ORCL already holds 3 positions against a cap of 3: a count rung that binds
    # breaks at any size, so max_quantity is 0.
    orcl = {"symbol": "ORCL", "expiration": "2026-11-21", "max_loss_total": 100.0,
            "sector": "Information Technology"}
    caps = {**CAPS, "open": [dict(orcl), dict(orcl), dict(orcl)]}
    v = handoff.paper_dialog_view(SIG, caps, 1)
    assert v["can_create"] is False
    assert v["block_text"] == "ORCL already holds 3 of 3 positions"
    assert v["fits_text"] == "No quantity fits the paper ledger's limits right now."
    # Nothing fits: the box holds at 1 (review fix; was the 100 ceiling).
    assert v["qty_max"] == 1


def test_an_unusable_quantity_blocks_with_the_quantity_message():
    for bad in (0, 2.9, None):
        v = handoff.paper_dialog_view(SIG, CAPS, bad)
        assert v["can_create"] is False, bad
        assert v["note"] == book_fit.BAD_QUANTITY, bad
        assert v["lines"] == [] and v["block_text"] == "" and v["fits_text"] == ""
        assert v["qty_max"] == 100
        # The per-contract risk is a property of the signal, not the quantity.
        assert v["risk_text"] == "Risk $190 per contract"


def test_no_caps_view_does_not_block_the_trade():
    """The service still checks every cap on the click."""
    v = handoff.paper_dialog_view(SIG, None, 1)
    assert v["can_create"] is True
    assert v["lines"] == []
    assert v["note"] == book_fit.UNAVAILABLE
    assert v["risk_text"] == "Risk $190 per contract"
    assert v["block_text"] == "" and v["fits_text"] == ""
    assert v["qty_max"] == 100


def test_no_risk_stamp_does_not_block_the_trade():
    v = handoff.paper_dialog_view({**SIG, "ledger_risk_basis": None}, CAPS, 1)
    assert v["can_create"] is True
    assert v["risk_text"] == ""
    assert v["note"] == book_fit.UNAVAILABLE
    assert v["lines"] == []


def test_a_fractional_basis_renders_cents():
    # booked_risk({"per_contract": 187.50375}, 1) = round(187.50375, 2) = 187.5
    v = handoff.paper_dialog_view(
        {**SIG, "ledger_risk_basis": {"per_contract": 187.50375}}, CAPS, 1)
    assert v["risk_text"] == "Risk $187.50 per contract"


def test_title_omits_a_missing_expiration():
    sig = {k: v for k, v in SIG.items() if k != "expiration"}
    assert handoff.paper_dialog_view(sig, CAPS, 1)["title"] == \
        "Paper trade ORCL Credit spread — put"


# --- the widget wiring (no harness: read the source) --------------------------

def _fn(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_send_to_paper_reads_caps_once_and_rechecks_on_confirm():
    src = inspect.getsource(handoff.send_to_paper)
    tree = ast.parse(src)
    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "read"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "bus_client"]
    assert len(reads) == 1
    assert handoff.LEDGER_CAPS_VIEW == "options:ledger_caps"
    # the one read is of the ledger caps view, outside every nested handler
    outer = _fn(tree, "send_to_paper")
    nested = [n for n in ast.walk(outer) if isinstance(n, ast.FunctionDef)
              and n is not outer]
    for fn in nested:
        assert not any(r in list(ast.walk(fn)) for r in reads), fn.name
    assert "LEDGER_CAPS_VIEW" in ast.unparse(reads[0])

    assert "paper_dialog_view" in src
    confirm = _fn(tree, "confirm")
    confirm_src = ast.unparse(confirm)
    assert "paper_dialog_view" in confirm_src
    assert "can_create" in confirm_src
    # the re-check returns BEFORE the enqueue
    assert confirm_src.index("can_create") < confirm_src.index("bus_client.request")
    assert "on_value_change" in src
    # Review fix: the block sentence is not painted separately - the red
    # checklist line already says it. (Added assertion; none above changed.)
    # ast.unparse drops comments, so only CODE that mentions it counts.
    assert "block_text" not in ast.unparse(tree)
    # the latch is set and Create disabled BEFORE the enqueue
    assert confirm_src.index("state['sent'] = True") < confirm_src.index("bus_client.request")
    assert confirm_src.index("create.disable()") < confirm_src.index("bus_client.request")
    assert confirm_src.index("if state['sent']") < confirm_src.index("paper_dialog_view")


# --- review fixes: quantity rules -------------------------------------------

def test_a_fitting_quantity_is_never_below_the_box_max(monkeypatch):
    """book_caps.max_quantity can land one short at an exact cap on sub-cent
    risk; the blur clamp must not cut a quantity that fits. Real cases exist (a
    search found 4,291 of 200,000, e.g. per_share 0.69417 at 35 contracts under a
    $2,429.59 per-trade cap); the stubbed preview keeps this test independent of
    which cases a given book_caps happens to produce."""
    def fake(signal, caps, qty):
        return {"available": True, "lines": [], "breach": None, "block_text": "",
                "max_quantity": 3, "unavailable_text": ""}
    monkeypatch.setattr(book_fit, "preview", fake)
    v = handoff.paper_dialog_view(SIG, CAPS, 4)
    assert v["can_create"] is True
    assert v["qty_max"] == 4


def test_above_the_dialog_ceiling_blocks_with_its_own_sentence():
    # 101 contracts with no caps view, so no rung is evaluated and only the
    # dialog's own ceiling can block it.
    v = handoff.paper_dialog_view(SIG, None, 101)
    assert v["can_create"] is False
    assert v["fits_text"] == "The dialog opens at most 100 contracts in one trade."
    assert v["qty_max"] == 100


def test_sent_text_names_the_quantity():
    assert handoff.sent_text(1) == "Sent 1 contract — the paper ledger answers in a moment."
    assert handoff.sent_text(3) == "Sent 3 contracts — the paper ledger answers in a moment."


# --- the real send_to_paper, driven through the kit's dialog -----------------

def _open_dialog(monkeypatch, request, read=lambda view: CAPS):
    """The real send_to_paper, with the bus and the toast stubbed."""
    notes = []
    monkeypatch.setattr(handoff.bus_client, "read", read)
    monkeypatch.setattr(handoff.bus_client, "request", request)
    monkeypatch.setattr(handoff.kit, "toast", lambda kind, text: notes.append((text, kind)))
    with ui.card():
        dlg = handoff.send_to_paper(SIG)
    return dlg, notes


def _qty(dlg):
    return next(e for e in dlg.content.descendants() if isinstance(e, ui.number))


def test_a_second_click_sends_nothing(monkeypatch):
    sent = []
    dlg, notes = _open_dialog(monkeypatch, lambda domain, cmd: sent.append((domain, cmd)))
    # It really OPENED: without this, the closed-check below would also pass on a
    # dialog that was never shown (a closed dialog and an unopened one both read
    # value False), which is the one thing the old fake's `closed` flag caught.
    assert dlg.dialog.value is True
    _qty(dlg).value = 2.0
    asyncio.run(dlg.run())
    asyncio.run(dlg.run())         # the queued double click
    assert len(sent) == 1
    assert sent[0][1]["args"]["qty"] == 2
    assert dlg.confirm.enabled is False
    assert notes == [("Sent 2 contracts — the paper ledger answers in a moment.", "info")]
    assert dlg.dialog.value is False


def test_a_send_that_cannot_reach_the_bus_can_be_retried(monkeypatch):
    calls = []

    def down(domain, cmd):
        calls.append(cmd)
        raise ConnectionError("redis down")

    dlg, notes = _open_dialog(monkeypatch, down)
    asyncio.run(dlg.run())
    assert notes == [("Could not reach the options service — the trade was "
                      "not sent.", "error")]
    assert dlg.confirm.enabled is True and dlg.dialog.value is True
    asyncio.run(dlg.run())         # the retry really runs again
    assert len(calls) == 2


def test_an_unreadable_caps_view_leaves_create_enabled(monkeypatch):
    def boom(view):
        raise ConnectionError("redis down")

    dlg, _notes = _open_dialog(monkeypatch, lambda d, c: None, read=boom)
    assert dlg.confirm.enabled is True
    shown = [e.text for e in dlg.content.descendants()
             if isinstance(e, ui.label) and e.visible]
    assert book_fit.UNAVAILABLE in shown
