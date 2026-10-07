"""The Market Scanner as the public origin serves it: Option Signals.

``live.neuralstrike.co/signals`` is ``scanner.render()`` in the PUBLIC process -
the same module, gated on ``shell.is_public()``. Every test of something the
public page leaves out has a partner on the private page, for two reasons: a
control that vanished from the owner's app is a regression nothing else here
would see, and a test for an absence passes on anything that was never there.

What the public page must not carry, and why:

* **Run scan** - a rescan spends Schwab calls;
* **Paper trade / Calculator / Expected Move** - Paper writes the owner's book,
  and the other two hand off through ``handoff._pending``, one store every
  visitor would share;
* **the paper-result toasts** - the owner's Paper clicks, shown to everyone;
* **the Paper book check line** - it reads the owner's ledger;
* **"new" badges** - ``scanner._SEEN`` is one set per process;
* **Max contracts** - sized from the owner's per-trade limit.
"""
import ast
import inspect
import textwrap

import bus_client
import pytest

from pages.options import (checks_feed, checks_table, detail, scanner,
                           scanner_shared)

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
          "max_risk_per_trade": 250.0}
CAPS = {"limits": LIMITS, "equity": 25000.0, "open": [], "sectors": {"ORCL": "IT"},
        "unmapped_prefix": "?"}
BOARD_ROW = {"symbol": "ORCL", "spot": 110.0, "put_wall": 102.0, "call_wall": 120.0,
             "gex_regime": "above", "trend_dir": 0.4, "trend_state": "up"}


def _pcs_signal(**over):
    """A live scanner PCS signal as the options service publishes it."""
    sig = {"id": "ORCL_PCS_2026-10-17_100_97.5", "symbol": "ORCL", "type": "PCS",
           "trade_type": "SWING", "expiration": "2026-10-17", "dte": 12,
           "short_strike": 100.0, "long_strike": 97.5, "width": 2.5,
           "credit": 0.60, "max_loss": 1.90, "rr_pct": 31.6, "pop_pct": 72.0,
           "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           "ledger_risk_per_contract": 190.0,
           "ledger_risk_basis": {"per_contract": 190.0},
           "underlying_price": 110.0, "composite_score": 70, "grade": "Good",
           "max_contracts": 3, "live": True, "stale_since": None}
    sig.update(over)
    return sig


def _ctx():
    return {"matrix": {"ORCL": BOARD_ROW}, "regime": {"direction": 1},
            "calibration": None, "caps": CAPS}


def _day(*swing):
    return {"date": scanner.today_ct(), "signals_0dte": [],
            "signals_swing": list(swing), "signals_directional": [],
            "structures_0dte": [], "structures_swing": []}


@pytest.fixture
def published():
    """Render as the PUBLIC origin, with the real route map, then put the
    process back - exactly what ``live_main`` does at start."""
    import live_screens
    import shell
    shell.publish(live_screens.PUBLIC_ROUTES)
    yield
    shell.unpublish()


@pytest.fixture
def clean():
    bus_client.reset()
    scanner_shared.reset()
    for memo in checks_feed._memos.values():
        memo.clear()
    yield
    bus_client.reset()
    scanner_shared.reset()
    for memo in checks_feed._memos.values():
        memo.clear()


# ── the build ────────────────────────────────────────────────────────────────

def _book_lines(row, sig, ctx):
    cand = {**sig, "_allow_paper": row["_allow_paper"]}
    return [c for c in checks_feed.checks_for(cand, ctx) if c["key"] == "book"]


def test_the_private_build_keeps_the_paper_gate_and_its_book_line():
    """The partner. With real caps in hand a live row's gate is open and the
    checklist carries a Paper book line."""
    sig, ctx = _pcs_signal(), _ctx()
    row = scanner._build_populate(_day(sig), {}, ctx)["rows"]["signals_swing"][0]
    assert row["_allow_paper"] is True
    assert _book_lines(row, sig, ctx)


def test_the_public_build_closes_every_paper_gate():
    """Even handed the caps (it never is - see test_checks_feed_public), a
    public row draws no Paper book line: ABSENT, not grey."""
    sig, ctx = _pcs_signal(), _ctx()
    row = scanner._build_populate(_day(sig), {}, ctx, public=True)[
        "rows"]["signals_swing"][0]
    assert row["_allow_paper"] is False
    assert _book_lines(row, sig, ctx) == []


def test_the_public_build_stamps_its_checks_after_closing_the_gate():
    """ORDER: a chip stamped before the gate closed would count a Paper book
    line the detail panel then does not draw."""
    sig, ctx = _pcs_signal(), _ctx()
    private = scanner._build_populate(_day(sig), {}, ctx)["rows"]["signals_swing"][0]
    public = scanner._build_populate(_day(sig), {}, ctx, public=True)[
        "rows"]["signals_swing"][0]
    assert "book" in {c["key"] for c in checks_feed.checks_for(
        {**sig, "_allow_paper": True}, ctx)}
    # One line fewer was judged: "Clear · 6 of 7" privately, "… of 6" publicly.
    assert private["checks"] != public["checks"]
    assert public["_checks_clear"] == (public["_checks_state"] == "pos")


def test_a_public_row_can_still_read_clear():
    """With the book line absent "Only clear" has to keep working: a row whose
    remaining checks pass is Clear, not hidden for a line it never had."""
    sig = _pcs_signal()
    ctx = {**_ctx(), "caps": None}
    row = scanner._build_populate(_day(sig), {}, ctx, public=True)[
        "rows"]["signals_swing"][0]
    if row["_checks_state"] == "pos":
        assert checks_table.only_clear([row]) == [row]
    else:                                    # a caution in the fixture, not the gate
        assert row["_checks_state"] in ("warn", "muted")


# ── the shared read ──────────────────────────────────────────────────────────

def _seed(day=None, live=None):
    bus = bus_client.bus()
    bus.cache_set("cache:options:scan_day", day or _day(_pcs_signal()))
    bus.cache_set("cache:options:scan", live or {"timestamp": "x", "signals_swing": []})


def test_every_visitor_gets_the_same_build(clean, published):
    _seed()
    first = scanner._read_and_build_shared()
    second = scanner._read_and_build_shared()
    assert first is second
    assert [r["id"] for r in first["rows"]["signals_swing"]] == [_pcs_signal()["id"]]


def test_a_new_scan_is_a_new_build(clean, published):
    _seed()
    first = scanner._read_and_build_shared()
    other = _pcs_signal(id="ORCL_PCS_2026-10-17_95_92.5", short_strike=95.0,
                        long_strike=92.5)
    bus_client.bus().cache_set("cache:options:scan_day", _day(_pcs_signal(), other))
    second = scanner._read_and_build_shared()
    assert second is not first
    assert len(second["rows"]["signals_swing"]) == 2


def test_the_shared_build_is_a_public_build(clean, published):
    _seed()
    rows = scanner._read_and_build_shared()["rows"]["signals_swing"]
    assert rows and all(r["_allow_paper"] is False for r in rows)
    assert all("_new" not in r for r in rows)


def test_the_shared_build_never_holds_the_ledger_caps(clean, published):
    _seed()
    bus_client.bus().cache_set(f"cache:{checks_feed.CAPS_VIEW}", CAPS)
    assert scanner._read_and_build_shared()["ctx"]["caps"] is None


def test_a_cold_service_builds_empty_tables_and_shares_them(clean, published):
    first = scanner._read_and_build_shared()
    assert first["have"] is False
    assert all(rows == [] for rows in first["rows"].values())
    assert scanner._read_and_build_shared() is first


# ── the rows a table is sent ─────────────────────────────────────────────────

def test_a_shared_page_is_marked_on_copies():
    """One visitor's selection must not light a row in another visitor's tab."""
    rows = [{"id": "a", "symbol": "SPY"}, {"id": "b", "symbol": "QQQ"}]
    sent = scanner.page_rows(rows, "b", shared=True)
    assert [r["_selected"] for r in sent] == [False, True]
    assert all("_selected" not in r for r in rows), "a shared row was stamped"
    assert sent[0] is not rows[0]
    assert {k: v for k, v in sent[1].items() if k != "_selected"} == rows[1]


def test_a_shared_page_with_no_selection_marks_nothing():
    rows = [{"id": None, "symbol": "SPY"}, {"id": "b", "symbol": "QQQ"}]
    assert [r["_selected"] for r in scanner.page_rows(rows, None, shared=True)] == [
        False, False]


def test_a_private_page_is_sent_as_it_is():
    """The private page's rows are its own and were stamped in place by
    ``kit.mark_selected``; copying them would only cost."""
    rows = [{"id": "a", "_selected": True}]
    assert scanner.page_rows(rows, "a", shared=False) is rows


def test_the_public_only_clear_tip_does_not_name_the_paper_book():
    assert "paper" not in checks_table.ONLY_CLEAR_TIP_PUBLIC.lower()
    assert "paper book" in checks_table.ONLY_CLEAR_TIP           # the private one


# ── the render ───────────────────────────────────────────────────────────────

def _render():
    """Every element ``scanner.render()`` builds, with the bus cold."""
    from nicegui import ui
    with ui.column() as box:
        scanner.render()
    return list(box.descendants())


def _texts(elements, kind):
    return [str(getattr(e, "text", "") or "") for e in elements
            if type(e).__name__ == kind]


_OWNER_BUTTONS = ("Run scan", "Paper trade", "Calculator", "Expected Move")


def test_the_private_render_draws_the_owners_buttons(clean):
    buttons = _texts(_render(), "Button")
    for name in _OWNER_BUTTONS:
        assert name in buttons, f"the private scanner lost its {name} button"


def test_the_public_render_draws_none_of_the_owners_buttons(clean, published):
    buttons = _texts(_render(), "Button")
    assert buttons, "no button was built at all - this test would be vacuous"
    for name in _OWNER_BUTTONS:
        assert name not in buttons, f"the public page draws {name}"


def test_both_renders_draw_why_no_trade(clean):
    assert scanner.FUNNEL_TITLE in _texts(_render(), "Button")


def test_the_public_render_draws_why_no_trade(clean, published):
    assert scanner.FUNNEL_TITLE in _texts(_render(), "Button")


def _tab_names(elements):
    return [e._props.get("name") for e in elements if type(e).__name__ == "Tab"]


@pytest.mark.parametrize("public", [False, True])
def test_the_tabs_and_everything_under_them_are_built(clean, public):
    """What moved: the three tabs, each two-table tab's switch, and one
    checkbox per structure family per switch."""
    import live_screens
    import shell

    from pages.options import scanner_structures as ssx
    if public:
        shell.publish(live_screens.PUBLIC_ROUTES)
    try:
        elements = _render()
    finally:
        shell.unpublish()
    assert _tab_names(elements) == ["0-DTE", "Swing", "Directional"]
    assert sum(type(e).__name__ == "Toggle" for e in elements) == 2
    assert sum(type(e).__name__ == "Checkbox" for e in elements) == 2 * len(ssx.GROUPS)
    assert sum(type(e).__name__ == "Table" for e in elements) == 5
    assert sum(type(e).__name__ == "Switch" for e in elements) == 1      # Only clear


def _watch_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(scanner.handoff, "watch_paper_results",
                        lambda: calls.append(1))
    return calls


def test_the_private_render_watches_for_paper_answers(clean, monkeypatch):
    calls = _watch_calls(monkeypatch)
    _render()
    assert calls == [1]


def test_the_public_render_never_watches_for_paper_answers(clean, published,
                                                           monkeypatch):
    """``cache:options:paper_create`` is the answer to the OWNER's Paper click.
    Watched here, it would be toasted to every visitor."""
    calls = _watch_calls(monkeypatch)
    _render()
    assert calls == []


def _new_marker_calls(monkeypatch):
    calls = []
    real = scanner.new_ids_for_paint

    def _spy(ids, today, acknowledge):
        calls.append(acknowledge)
        return real(ids, today, acknowledge)

    monkeypatch.setattr(scanner, "new_ids_for_paint", _spy)
    return calls


def test_the_private_render_keeps_its_new_markers(clean, monkeypatch):
    calls = _new_marker_calls(monkeypatch)
    _render()
    assert calls, "the private page stopped asking which signals are new"


def test_the_public_render_never_touches_the_seen_set(clean, published,
                                                      monkeypatch):
    """``scanner._SEEN`` is one set for the whole process. Read here, one
    visitor's page load would decide every other visitor's badges; written
    here, it would clear them."""
    calls = _new_marker_calls(monkeypatch)
    scanner._reset_seen_state()
    before = dict(scanner._SEEN)
    _render()
    assert calls == []
    assert scanner._SEEN == before


def test_the_public_render_probes_no_ledger_view(clean, published, monkeypatch):
    """Not even the version counter: the page has no use for it there."""
    probed = []
    real = bus_client.read_versions

    def _spy(views):
        views = list(views)
        probed.extend(views)
        return real(views)

    monkeypatch.setattr(bus_client, "read_versions", _spy)
    _render()
    assert "options:scan_day" in probed, "nothing was probed - vacuous"
    assert checks_feed.CAPS_VIEW not in probed


def test_the_private_render_still_probes_the_ledger_view(clean, monkeypatch):
    probed = []
    real = bus_client.read_versions

    def _spy(views):
        views = list(views)
        probed.extend(views)
        return real(views)

    monkeypatch.setattr(bus_client, "read_versions", _spy)
    _render()
    assert checks_feed.CAPS_VIEW in probed


# ── the gate itself, at source level ─────────────────────────────────────────

def test_the_gate_reads_the_process_not_an_argument():
    """A ``public=True`` keyword can be left off a ``Screen`` entry, and the
    result would be the owner's page on an unauthenticated origin. The process
    knows which origin it is; ``render`` takes no say in it."""
    assert list(inspect.signature(scanner.render).parameters) == []
    src = inspect.getsource(scanner.render)
    assert "_shell.is_public()" in src and "_shell.may_enqueue()" in src


def test_the_public_page_loads_through_the_shared_build():
    tree = ast.parse(textwrap.dedent(inspect.getsource(scanner.render)))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_read_and_build_shared" in names
    assert "_read_and_build" in names            # the private page keeps its own


# ── the detail panel ─────────────────────────────────────────────────────────

def _card_labels(signal):
    from nicegui import ui
    with ui.column() as box:
        detail._build_cards(signal)
    return [str(e.text) for e in box.descendants() if isinstance(e, ui.label)]


def test_the_private_panel_prints_max_contracts(clean):
    assert "Max contracts" in _card_labels(_pcs_signal())


def test_the_public_panel_does_not_print_max_contracts(clean, published):
    """It is sized from the owner's per-trade risk limit."""
    labels = _card_labels(_pcs_signal())
    assert "Max loss" in labels, "the panel built nothing - vacuous"
    assert "Max contracts" not in labels


def test_a_panel_built_without_actions_has_a_bare_footer(clean):
    """The footer still exists (``update`` / ``clear`` show and hide it), but
    draws no rule over an empty row."""
    from nicegui import ui
    with ui.column():
        bare = detail.render(width=290, actions=False)
        full = detail.render(width=290)
    assert "border-t" not in bare.actions._classes
    assert "border-t" in full.actions._classes
    bare.update(_pcs_signal())               # still works with no buttons in it
    bare.clear()
