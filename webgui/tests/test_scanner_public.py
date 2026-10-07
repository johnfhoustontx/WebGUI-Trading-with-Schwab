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


def _forget_everything():
    bus_client.reset()
    scanner_shared.reset()
    scanner._reset_shared_reads()
    for memo in checks_feed._memos.values():
        memo.clear()


@pytest.fixture
def clean():
    _forget_everything()
    yield
    _forget_everything()


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
    # As the public origin holds it: no caps, and a published calibration with
    # no bucket for this row (cold, the track record would read as a feed that
    # has not loaded and the chip as Partly checked).
    ctx = {**_ctx(), "caps": None, "calibration": {"by_bucket": {}}}
    row = scanner._build_populate(_day(sig), {}, ctx, public=True)[
        "rows"]["signals_swing"][0]
    assert row["_checks_state"] == "pos", row["checks"]
    assert row["checks"].startswith("Clear")
    assert checks_table.only_clear([row]) == [row]


def test_a_public_row_reads_clear_with_its_cost_line_withheld(published, monkeypatch):
    """The cost-to-trade line greys out while quotes are withheld. That must
    not read as a feed that has not loaded: the row is still Clear."""
    from shared import public_scan
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    sig = _pcs_signal()
    ctx = {**_ctx(), "caps": None, "calibration": {"by_bucket": {}}}
    row = scanner._build_populate(_day(sig), {}, ctx, public=True)[
        "rows"]["signals_swing"][0]
    assert row["_checks_state"] == "pos", row["checks"]
    assert checks_table.only_clear([row]) == [row]


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


def test_a_public_build_leaves_what_it_was_built_from_untouched():
    """The payloads are shared too, and rebuilt FROM every five minutes: a
    builder that wrote into a signal would compound, in every visitor's tab."""
    import copy
    leg = {"side": "long", "kind": "call", "strike": 110.0,
           "expiration": "2026-10-17", "qty": 1}
    directional = {"id": "ORCL_LONG_CALL_2026-10-17_110", "symbol": "ORCL",
                   "type": "LONG_CALL", "family": "DIRECTIONAL",
                   "strategy_label": "Long Call", "bias": "bullish", "legs": [leg],
                   "expiration": "2026-10-17", "dte": 12, "net_debit": 240.0,
                   "net_credit": None, "max_profit": None, "unbounded_profit": True,
                   "max_loss": 240.0, "breakevens": [112.4], "pop_pct": 38.0,
                   "composite_score": 71, "grade": "Good", "live": True}
    structure = {**directional, "id": "ORCL_BUTTERFLY_CALL_2026-10-17",
                 "type": "BUTTERFLY_CALL", "group": "BUTTERFLY", "family": "NEUTRAL",
                 "live": False, "stale_since": "2026-10-07T10:15:00-05:00"}
    day = {**_day(_pcs_signal()), "signals_directional": [directional],
           "structures_swing": [structure]}
    live = {"timestamp": "x", "signals_swing": [_pcs_signal()]}
    ctx = {**_ctx(), "caps": None, "calibration": {"by_bucket": {}}}
    before = copy.deepcopy((day, live, ctx))
    built = scanner._build_populate(day, live, ctx, public=True)
    assert sum(len(rows) for rows in built["rows"].values()) == 3
    assert (day, live, ctx) == before


def test_the_shared_build_survives_a_busy_gamma_page(clean, published):
    """``bus_client.read_shared`` keeps the 48 views read most recently and
    drops the rest. The public Gamma page alone can read that many (eight
    symbols, a snapshot and five histories each), so a day union read through
    it could be dropped between two visitors' reads - a fresh parse, a
    different object, a second build, and by the end one build per visitor:
    exactly what the shared build exists to prevent."""
    _seed()
    first = scanner._read_and_build_shared()
    bus = bus_client.bus()
    for i in range(bus_client.SHARED_MAX_VIEWS + 2):
        bus.cache_set(f"cache:options:gamma_pub:SYM{i}", {"i": i})
        bus_client.read_shared(f"options:gamma_pub:SYM{i}")
    assert scanner._read_and_build_shared() is first


def test_a_counter_that_moved_ahead_of_its_payload_is_not_a_new_scan(
        clean, published, monkeypatch):
    """``Bus.cache_set`` moves ``:ver`` and then writes the envelope. A reader
    in between probes N+1 and reads N: the scan it already holds. Handed a
    fresh parse of it, the slot would build that old scan again for every
    visitor who polled in the gap."""
    _seed()
    first = scanner._read_and_build_shared()
    real = bus_client.read_version
    monkeypatch.setattr(
        bus_client, "read_version",
        lambda view: (real(view) or 0) + 1 if view == "options:scan_day" else real(view))
    assert scanner._read_and_build_shared() is first


def test_a_build_made_before_midnight_is_not_served_as_todays(clean, published,
                                                             monkeypatch):
    """The build gates the day union on TODAY's date (``day_is_today``: "a
    GATE, not decoration"). Nothing is republished at midnight, so the date has
    to be part of what the build is keyed on."""
    _seed()
    first = scanner._read_and_build_shared()
    assert first["have"] is True
    monkeypatch.setattr(scanner, "today_ct", lambda: "2099-01-01")
    second = scanner._read_and_build_shared()
    assert second is not first
    assert second["have"] is False
    assert all(rows == [] for rows in second["rows"].values())


def test_turning_the_quotes_switch_rebuilds_the_rows(clean, published, monkeypatch):
    """The cost-to-trade line is baked into each row's chip and tooltip when
    the rows are built. Withheld after the fact, it has to leave the table at
    the next read, not when the build happens to age out."""
    from shared import public_scan
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    _seed(_day(_pcs_signal(friction_pct=18.0)))
    shown = scanner._read_and_build_shared()
    assert "18.00%" in shown["rows"]["signals_swing"][0]["_checks_tip"]
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    withheld = scanner._read_and_build_shared()
    assert withheld is not shown
    assert "%" not in withheld["rows"]["signals_swing"][0]["_checks_tip"]


def test_the_age_limit_is_half_a_tabs_own_tick(clean, published, monkeypatch):
    """Each tab asks again every ``TABLE_REFRESH_SEC``. With the limit EQUAL to
    that, the tab's next tick finds its own build a few milliseconds under it
    and waits a second period (test_scanner_shared has the arithmetic)."""
    asked = {}
    real = scanner_shared.get

    def _spy(parts, build, **kw):
        asked.update(kw)
        return real(parts, build, **kw)

    monkeypatch.setattr(scanner_shared, "get", _spy)
    _seed()
    scanner._read_and_build_shared()
    assert asked["max_age"] == checks_feed.TABLE_REFRESH_SEC / 2


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


def _mark_selected_calls(monkeypatch):
    calls = []
    real = scanner.kit.mark_selected

    def _spy(rows, row_id, **kw):
        calls.append(row_id)
        return real(rows, row_id, **kw)

    monkeypatch.setattr(scanner.kit, "mark_selected", _spy)
    return calls


def test_the_private_render_marks_its_own_rows(clean, monkeypatch):
    calls = _mark_selected_calls(monkeypatch)
    _render()
    assert calls, "the private page stopped marking its selected row"


def test_the_public_render_never_stamps_the_rows_it_paints(clean, published,
                                                           monkeypatch):
    """``kit.mark_selected`` writes ``_selected`` onto the rows it is given,
    and on the public origin those are every visitor's. The page paints at
    build, so a call here would be a call on every repaint."""
    calls = _mark_selected_calls(monkeypatch)
    _render()
    assert calls == []


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


# ── "Why no trade?" ──────────────────────────────────────────────────────────

def _funnel_payload():
    """A Swing window where every width cost more than the per-trade cap, on an
    entry that names the cap's figure (no publisher stamps it today; the
    sentence gains it the day one does - ``funnel_view._cap_phrase``)."""
    # The tally partitions, as ``screen_spreads`` promises: delta_pass ==
    # mark_fail + delta_ceiling + em_fail + liq_fail_short + width_found
    # + sum(width_reasons.values()).
    strikes = {"expiration_sides_in_window": 4,
               "expiration_sides_skipped_earnings": 0,
               "delta_reject": 120, "delta_pass": 60,
               "mark_fail": 2, "delta_ceiling": 5, "em_fail": 9,
               "liq_fail_short": 6, "width_found": 0,
               "strikes_dropped_no_delta": 0, "strikes_dropped_off_increment": 0,
               "width_reasons": {"over_trade_cap": 38}}
    spreads = {"built": 0, "momentum_veto": 0, "iron_condors": 0,
               "kept_after_cap": 0, "regime_pass_added": 0, "regime_filter": 0,
               "below_iv_floor": 0, "no_iv_history": 0, "gamma_gate": 0,
               "outside_rth": 0, "emitted": 0}
    entry = {"price": 178.42, "iv_rank": 41.0, "stop": None,
             "max_risk_dollars": 750,
             "buckets": {"SWING": {"chain": True, "strikes": strikes,
                                   "spreads": spreads}}}
    return {"timestamp": "2026-10-07T09:31:00-05:00", "symbols": {"MU": entry}}


def _swing_headline(**kw):
    cards = scanner.funnel_cards(_funnel_payload(), "MU", **kw)
    return next(c["headline"] for c in cards if c["headline"].startswith("MU · Swing:"))


def test_the_private_funnel_names_the_cap_figure_it_is_given():
    assert "$750.00 per-trade risk cap" in _swing_headline()


def test_the_public_funnel_never_names_the_owners_cap_figure():
    """The per-trade limit is the owner's. The sentence still says a width cost
    more than the cap; it does not say what the cap is."""
    headline = _swing_headline(public=True)
    assert "per-trade risk cap" in headline
    assert "$" not in headline


def test_the_public_funnel_does_not_change_the_payload_it_was_given():
    payload = _funnel_payload()
    scanner.funnel_cards(payload, "MU", public=True)
    assert payload["symbols"]["MU"]["max_risk_dollars"] == 750


def test_render_asks_for_the_public_funnel():
    assert "public=_public" in inspect.getsource(scanner.render)


# ── the quotes switch ────────────────────────────────────────────────────────
# ``config/finder_public.toml [display] show_leg_quotes`` is the site's one
# switch for figures read off a contract's own quote, off until Schwab's
# redistribution terms are settled (decision D2). While it is off the public
# Calculator draws no delta and its checklist's cost-to-trade line is not
# measured; Option Signals follows the same switch.

def _quotes(monkeypatch, on):
    from shared import public_scan
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: on)


def _cost_line(sig):
    cand = {**sig, "_allow_paper": False}
    return next(c for c in checks_feed.checks_for(cand, {**_ctx(), "caps": None})
                if c["key"] == "cost")


def test_the_private_checklist_measures_the_cost_to_trade(monkeypatch):
    _quotes(monkeypatch, False)              # the switch is the public origin's
    line = _cost_line(_pcs_signal())
    assert line["tone"] == "pos" and "8.00%" in line["text"]


def test_the_public_checklist_withholds_the_cost_to_trade(monkeypatch, published):
    """The round trip as a share of the credit, beside the credit, is the
    spread's bid-ask width."""
    _quotes(monkeypatch, False)
    line = _cost_line(_pcs_signal())
    assert line["tone"] == "muted" and "%" not in line["text"]


def test_the_public_checklist_measures_it_once_quotes_are_on(monkeypatch, published):
    _quotes(monkeypatch, True)
    assert "8.00%" in _cost_line(_pcs_signal())["text"]


def test_withholding_does_not_change_the_signal_it_was_given(monkeypatch, published):
    _quotes(monkeypatch, False)
    sig = _pcs_signal()
    _cost_line(sig)
    assert sig["friction_pct"] == 8.0


def _expansions(signal):
    from nicegui import ui
    with ui.column() as box:
        detail._build_cards(signal)
    found = list(box.descendants())
    return ([str(e._props.get("label")) for e in found
             if type(e).__name__ == "Expansion"],
            [str(e.text) for e in found if isinstance(e, ui.label)])


_GREEK_SIGNAL = {"short_delta": -0.18, "net_theta": 0.021, "net_vega": -0.04,
                 "short_iv": 31.2, "current_iv": 29.8, "iv_rank": 55.0}


def test_the_private_panel_draws_the_greeks(clean, monkeypatch):
    _quotes(monkeypatch, False)
    sections, labels = _expansions(_pcs_signal(**_GREEK_SIGNAL))
    assert "Greeks" in sections and "-0.1800" in labels


def test_the_public_panel_withholds_the_greeks(clean, monkeypatch, published):
    """Delta, theta, vega and the short leg's own IV are per-contract figures;
    the public Calculator and Simulator withhold them under the same switch."""
    _quotes(monkeypatch, False)
    sections, labels = _expansions(_pcs_signal(**_GREEK_SIGNAL))
    assert "Score factors" in sections, "the panel built nothing - vacuous"
    assert "Greeks" not in sections
    assert "-0.1800" not in labels and "31.20%" not in labels
    # The underlying's own ATM volatility is not a contract's, and stays.
    assert "Implied volatility" in sections and "29.80%" in labels


def test_the_public_panel_never_passes_a_contracts_iv_off_as_the_atm_iv(
        clean, monkeypatch, published):
    """``ATM IV`` falls back to the short leg's IV when the signal carries no
    ``current_iv``. Withheld, it reads as not known."""
    _quotes(monkeypatch, False)
    sig = _pcs_signal(**{**_GREEK_SIGNAL, "current_iv": None})
    _sections, labels = _expansions(sig)
    assert "31.20%" not in labels


def test_the_public_panel_draws_the_greeks_once_quotes_are_on(clean, monkeypatch,
                                                              published):
    _quotes(monkeypatch, True)
    sections, labels = _expansions(_pcs_signal(**_GREEK_SIGNAL))
    assert "Greeks" in sections and "-0.1800" in labels


def test_the_private_panel_prints_theta_and_vega(clean, monkeypatch):
    _quotes(monkeypatch, True)
    _sections, labels = _expansions(_pcs_signal(**_GREEK_SIGNAL))
    assert "0.021" in labels and "-0.040" in labels


def test_the_public_panel_never_prints_theta_or_vega(clean, monkeypatch, published):
    """The public Calculator never publishes them, switch on or off
    (``tools_public.ROW_NEVER``). Turning the switch on for its bid, ask and
    delta must not quietly add them here."""
    _quotes(monkeypatch, True)
    _sections, labels = _expansions(_pcs_signal(**_GREEK_SIGNAL))
    assert "-0.1800" in labels, "the Greeks were not drawn - vacuous"
    assert "0.021" not in labels and "-0.040" not in labels
