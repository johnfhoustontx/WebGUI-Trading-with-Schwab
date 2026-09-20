"""Tests for the page kit (pages/ui_kit.py) - the one look and behaviour."""
import asyncio
import datetime as dt
import logging

import pytest
from nicegui import ui
from nicegui.events import GenericEventArguments, handle_event

from pages import ui_kit as kit
from pages.options import theme

UTC = dt.timezone.utc


def _utc(day, h, m):
    return dt.datetime(2026, 9, day, h, m, tzinfo=UTC)


# -- freshness: the header's "Updated" stamp ----------------------------------
def test_nothing_published_is_waiting_never_a_time():
    assert kit.freshness(None, _utc(18, 15, 0)) == ("Waiting for data", "waiting")
    assert kit.freshness("not a time", _utc(18, 15, 0)) == ("Waiting for data", "waiting")


def test_freshness_reads_central_time():
    # 15:42 UTC on 2026-09-18 is 10:42 CDT
    assert kit.freshness("2026-09-18T15:42:00+00:00", _utc(18, 15, 50)) == \
        ("Updated 10:42 AM CT", "fresh")


def test_a_naive_stamp_is_utc_not_local():
    assert kit.freshness("2026-09-18T15:42:00", _utc(18, 15, 50))[0] == \
        "Updated 10:42 AM CT"


def test_a_winter_stamp_reads_central_STANDARD_time():
    """⚠ THE ONE THAT NEEDS A JANUARY DATE, AND THE ONLY ONE THAT HAS ONE.

    Every other fixture in this file is a September date, i.e. CDT (UTC-5). So
    a ``CT`` replaced by a fixed ``-5`` offset satisfies the whole suite while
    being wrong for four months of the year — measured: that swap passes all
    5207 tests without this test, and fails only this one with it. In January
    Central is CST (UTC-6), so 22:03 UTC is 4:03 PM, not the 5:03 PM a fixed
    offset gives. The stamp is on every page and on both public origins, so the
    failure would be an hour late everywhere, all winter, with nothing red.
    """
    jan = dt.datetime(2026, 1, 15, 22, 10, tzinfo=UTC)
    assert kit.freshness("2026-01-15T22:03:00+00:00", jan) == \
        ("Updated 4:03 PM CT", "fresh")


def test_a_stamp_from_another_day_names_the_day():
    assert kit.freshness("2026-09-17T20:15:00+00:00", _utc(18, 15, 0)) == \
        ("Updated Sep 17 3:15 PM CT", "fresh")


def test_past_the_threshold_is_stale():
    assert kit.freshness("2026-09-18T15:00:00+00:00", _utc(18, 15, 30),
                         stale_after_sec=600) == ("Stale · updated 10:00 AM CT", "stale")


def test_no_threshold_means_never_stale():
    """None = the view is not due to publish now (e.g. the scanner at night),
    so its age says nothing."""
    assert kit.freshness("2026-09-11T15:00:00+00:00", _utc(18, 15, 0))[1] == "fresh"


def test_a_naive_now_is_utc_as_well():
    """The stamp is UTC when it has no zone, so `now` must be read the same way
    - and subtracting a naive from an aware one raises outright."""
    aware = kit.freshness("2026-09-18T15:00:00+00:00", _utc(18, 15, 30),
                          stale_after_sec=600)
    naive = kit.freshness("2026-09-18T15:00:00+00:00",
                          dt.datetime(2026, 9, 18, 15, 30), stale_after_sec=600)
    assert naive == aware == ("Stale · updated 10:00 AM CT", "stale")


def test_the_scanner_is_not_called_stale_on_a_sunday():
    """Its publisher only runs in the session, so by Sunday its newest write is
    legitimately ~43h old."""
    sunday_noon_ct = dt.datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    assert kit._stale_after("options:scan", sunday_noon_ct) is None


# -- toasts --------------------------------------------------------------------
def test_toast_args_one_position_and_a_type_always():
    assert kit.toast_args("ok", "Saved") == {
        "message": "Saved", "type": "positive", "position": "bottom",
        "timeout": 4000, "multi_line": False}
    assert kit.toast_args("warn", "x")["timeout"] == 8000
    assert kit.toast_args("error", "x")["type"] == "negative"
    assert kit.toast_args("info", "x" * 81)["multi_line"] is True


def test_an_unknown_toast_kind_is_an_error():
    with pytest.raises(ValueError):
        kit.toast_args("loud", "x")


# -- buttons -------------------------------------------------------------------
def test_button_kinds_map_to_the_theme_tokens():
    assert kit.button_classes("primary") == theme.BTN_PRIMARY
    assert kit.button_classes("secondary") == theme.BTN
    assert kit.button_classes("danger") == theme.BTN_DANGER
    assert kit.button_classes("quiet") == theme.BTN_QUIET
    assert kit.button_classes("danger_solid") == theme.BTN_DANGER_SOLID


def test_an_unknown_button_kind_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        kit.button_classes("blue")


def test_preview_tokens_override_the_live_ones():
    toks = dict(theme._TOKENS, BTN_PRIMARY="bg-[#123456]")
    assert kit.button_classes("primary", toks) == "bg-[#123456]"


def test_button_is_sentence_case_and_not_quasar_blue():
    with ui.card():
        b = kit.button("Run scan", kind="primary", icon="play_arrow")
    assert b._props.get("no-caps") is True
    assert "bg-primary" not in b.classes
    assert set(theme.BTN_PRIMARY.split()) <= set(b.classes)


def test_an_icon_button_must_say_what_it_does():
    with pytest.raises(TypeError):
        kit.icon_button("delete")          # tooltip is required


def test_set_busy_spins_and_disables_then_releases():
    with ui.card():
        b = kit.button("Load", kind="primary")
    kit.set_busy(b)
    assert b._props.get("loading") is True and not b.enabled
    kit.set_busy(b, False)
    assert not b._props.get("loading") and b.enabled


def test_the_backstop_releases_a_button_whose_answer_never_came():
    with ui.card():
        b = kit.button("Load")
    kit.set_busy(b, timeout=0)
    assert b._kit_busy["timer"].active is True
    b._kit_busy["tick"]()
    assert b.enabled and not b._props.get("loading")
    assert b._kit_busy["timer"].active is False


# -- page frame and header line ---------------------------------------------------
def test_a_form_page_caps_its_width():
    with ui.card():
        full, form = kit.page(), kit.page("form")
    assert "max-w-3xl" in form.classes and "max-w-3xl" not in full.classes


def test_header_without_a_view_has_no_stamp():
    with ui.card():
        h = kit.header("Paper Ledger")
    assert h.title.text == "Paper Ledger"
    assert not h.stamp.visible


def test_header_stamp_turns_warning_when_stale_and_back():
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades")
    now = _utc(18, 15, 30)
    h.set_stamp("2026-09-18T15:00:00+00:00", 600, now)
    assert h.stamp.text.startswith("Stale") and theme.TXT_WARN in h.stamp.classes
    h.set_stamp("2026-09-18T15:29:00+00:00", 600, now)
    assert h.stamp.text.startswith("Updated") and theme.TXT_WARN not in h.stamp.classes


def test_the_public_origin_drops_the_title():
    """live_main draws the screen's name in its own header."""
    import shell
    shell.publish({})
    try:
        with ui.card():
            h = kit.header("Flow Alerts")
        assert h.title is None
    finally:
        shell.unpublish()


def test_header_actions_sit_right_of_the_stamp():
    with ui.card():
        h = kit.header("X", view="v")
    kids = list(h.row.default_slot.children)
    assert kids.index(h.stamp) < kids.index(h.actions)


def _patch_bus(monkeypatch, read_meta):
    async def io_bound(fn, *a, **kw):
        return fn(*a, **kw)
    monkeypatch.setattr(kit.run, "io_bound", io_bound)
    monkeypatch.setattr(kit.bus_client, "read_meta", read_meta)


def test_an_unknown_page_width_is_an_error_not_a_silent_full_width():
    with pytest.raises(ValueError):
        kit.page("wide")


def test_the_poll_stamps_what_the_bus_reports(monkeypatch):
    now = _utc(18, 15, 30)
    _patch_bus(monkeypatch, lambda _v: (7, "2026-09-18T15:29:00+00:00"))
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades", _now=lambda: now)
    asyncio.run(h.poll())
    assert h.stamp.text == "Updated 10:29 AM CT"


def test_a_scheduled_view_that_falls_behind_turns_amber(monkeypatch):
    now = _utc(18, 15, 30)
    old = (now - dt.timedelta(hours=2, minutes=30)).isoformat()
    _patch_bus(monkeypatch, lambda _v: (7, old))
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades", stale=True,
                       _now=lambda: now)
    asyncio.run(h.poll())
    assert h.stamp.text.startswith("Stale") and theme.TXT_WARN in h.stamp.classes


def test_a_bus_failure_keeps_the_last_stamp_and_still_lets_it_age(monkeypatch):
    """The stamp froze on the last good reading and stayed 'Updated', so a dead
    bus looked exactly like a quiet one."""
    clock = {"now": _utc(18, 15, 30)}
    calls = {"n": 0}

    def read_meta(_view):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("redis is down")
        return (7, "2026-09-18T15:29:00+00:00")

    _patch_bus(monkeypatch, read_meta)
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades", stale=True,
                       _now=lambda: clock["now"])
    asyncio.run(h.poll())
    assert h.stamp.text == "Updated 10:29 AM CT"
    clock["now"] = _utc(18, 18, 30)                  # three hours on, still down
    asyncio.run(h.poll())
    assert h.stamp.text == "Stale · updated 10:29 AM CT"
    assert theme.TXT_WARN in h.stamp.classes


def test_a_view_that_goes_EMPTY_says_waiting_not_the_old_time(monkeypatch):
    """A TTL expiry, a flush or a renamed view is a real absence. Keying the
    fallback on "no ts" rather than on "the read failed" kept showing a time
    nothing publishes any more, and aged it into Stale - a made-up reading,
    which is the one thing the stamp promises never to do."""
    reads = [(7, "2026-09-18T15:29:00+00:00"), (None, None)]
    _patch_bus(monkeypatch, lambda _v: reads.pop(0))
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades",
                       _now=lambda: _utc(18, 15, 30))
    asyncio.run(h.poll())
    assert h.stamp.text == "Updated 10:29 AM CT"
    asyncio.run(h.poll())
    assert h.stamp.text == kit.WAITING_TEXT


def test_a_second_outage_is_logged_too(monkeypatch, caplog):
    """A latch that never resets means a bus that fails, recovers and fails
    again is an outage nobody ever sees."""
    down = object()
    script = [down, (7, "2026-09-18T15:29:00+00:00"), down]

    def read_meta(_view):
        item = script.pop(0)
        if item is down:
            raise ConnectionError("redis is down")
        return item

    _patch_bus(monkeypatch, read_meta)
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades",
                       _now=lambda: _utc(18, 15, 30))
    with caplog.at_level(logging.WARNING, logger="pages.ui_kit"):
        for _ in range(3):
            asyncio.run(h.poll())
    assert len([r for r in caplog.records if "cannot read" in r.getMessage()]) == 2


def test_a_poll_that_reads_nothing_does_not_invent_a_time(monkeypatch):
    """run.io_bound answers None while the app is stopping."""
    async def io_bound(_fn, *_a, **_kw):
        return None
    monkeypatch.setattr(kit.run, "io_bound", io_bound)
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades",
                       _now=lambda: _utc(18, 15, 30))
    asyncio.run(h.poll())
    assert h.stamp.text == kit.WAITING_TEXT


def test_the_header_runs_one_poll_timer(monkeypatch):
    """The repeating timer already fires immediately on connect, so the extra
    0.1s once-timer was a second read of the same key on every page build."""
    made = []
    real = ui.timer
    monkeypatch.setattr(kit.ui, "timer",
                        lambda *a, **kw: made.append(a) or real(*a, **kw))
    with ui.card():
        kit.header("X", view="v", poll_sec=5.0)
    assert [a[0] for a in made] == [5.0]


# -- control bar and fields ---------------------------------------------------------
def test_field_puts_its_label_above():
    with ui.card():
        with kit.field("Expiry") as col:
            s = ui.select(["Oct 17"])
    kids = list(col.default_slot.children)
    assert kids[0].text == "Expiry" and kids[1] is s


def test_symbol_field_is_the_one_symbol_behaviour():
    with ui.card():
        inp = kit.symbol_field(value="SPY", on_load=lambda: None)
    assert "uppercase" in inp.classes                       # select_all_on_focus
    assert inp._symbol_load_last["sym"] == "SPY"            # seeded: tabbing through SPY is no load


def test_symbol_error_shows_under_the_field_and_clears():
    with ui.card():
        inp = kit.symbol_field(on_load=lambda: None)
    kit.symbol_error(inp, "No such ticker")
    assert inp._props["error"] is True and inp._props["error-message"] == "No such ticker"
    kit.symbol_error(inp, None)
    assert not inp._props.get("error")


def _blur(el):
    """Fire every blur listener, in registration order, the way a browser does.

    ``ui.number`` registers its OWN ``sanitize`` on blur inside ``__init__``,
    before anything the kit adds, and that order decides what the reader ends
    up looking at. A test that calls ``validate()`` instead takes a path the
    browser never takes."""
    for listener in list(el._event_listeners.values()):
        if listener.type == "blur":
            handle_event(listener.handler,
                         GenericEventArguments(sender=el, client=el.client, args=None))


def test_number_field_checks_on_leaving_not_per_keystroke():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1, max=100)
    n.value = 0
    assert n.error is None            # nothing said while they are still typing
    _blur(n)
    assert n.error == "At least 1"
    n.value = 101
    _blur(n)
    assert n.error == "At most 100"
    n.value = None
    _blur(n)
    assert n.error == "Enter a number"


def test_gate_holds_go_while_a_field_is_wrong():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    sync = kit.gate(go, n)
    assert go.enabled
    n.value = 0
    sync()
    assert not go.enabled
    n.value = 3
    sync()
    assert go.enabled


def _fire(el, kind):
    """Fire an element's registered handler for ``kind`` (no browser here)."""
    for listener in list(el._event_listeners.values()):
        if listener.type == kind and listener.handler is not None:
            listener.handler(GenericEventArguments(sender=el, client=el.client, args=None))


def test_gate_does_not_paint_an_error_on_a_field_nobody_has_touched():
    """A page opens with its required fields empty. Validating them to decide
    whether Go is live must not turn the form red before the reader has typed."""
    with ui.card():
        n = kit.number_field("Contracts", value=None, min=1)
        go = kit.button("Load", kind="primary")
    kit.gate(go, n)
    assert n.error is None
    assert not go.enabled


def test_a_gate_sync_cannot_wipe_a_symbol_error():
    """``validate()`` on an input with no validators sets error=None, so a sync
    for some OTHER field erased "No such ticker"."""
    with ui.card():
        inp = kit.symbol_field(value="SPY", on_load=lambda: None)
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    sync = kit.gate(go, inp, n)
    kit.symbol_error(inp, "No such ticker")
    sync()
    assert inp.error == "No such ticker"


def test_fixing_a_value_re_enables_go_without_a_second_blur():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    kit.gate(go, n)
    n.value = 0
    assert not go.enabled
    n.value = 4
    assert go.enabled


def test_releasing_the_busy_state_re_applies_the_gate():
    """The answer landing must not hand back a Go the fields do not allow."""
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    kit.gate(go, n)
    kit.set_busy(go)
    n.value = 0
    kit.set_busy(go, False)
    assert not go.enabled


def test_the_backstop_also_re_applies_the_gate():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    kit.gate(go, n)
    kit.set_busy(go, timeout=0)
    n.value = 0
    go._kit_busy["tick"]()
    assert not go.enabled


def test_a_gate_sync_never_re_enables_a_busy_button():
    """A blur while the command is in flight would otherwise allow a second
    submit."""
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    sync = kit.gate(go, n)
    kit.set_busy(go)
    sync()
    assert not go.enabled and go._props.get("loading") is True


def test_the_gate_is_reachable_from_the_button():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    sync = kit.gate(go, n)
    assert go._kit_gate is sync


def test_enter_reloads_even_an_unchanged_symbol():
    """Enter is the Load button typed: it always reloads. Only tab-out dedups."""
    fired = []
    with ui.card():
        inp = kit.symbol_field(value="SPY", on_load=lambda: fired.append(1))
    _fire(inp, "keydown.enter")
    _fire(inp, "keydown.enter")
    assert fired == [1, 1]


def test_tab_out_still_loads_only_a_changed_symbol():
    fired = []
    with ui.card():
        inp = kit.symbol_field(value="SPY", on_load=lambda: fired.append(1))
    _fire(inp, "focusout")
    assert fired == []
    inp.value = "QQQ"
    _fire(inp, "focusout")
    assert fired == [1]


def test_enter_on_an_empty_symbol_loads_nothing():
    fired = []
    with ui.card():
        inp = kit.symbol_field(on_load=lambda: fired.append(1))
    _fire(inp, "keydown.enter")
    assert fired == []


def test_a_reported_error_lets_the_next_tab_out_retry():
    """The dedup remembers the symbol it FIRED on, so a rejected one could never
    be retried from the field itself."""
    fired = []
    with ui.card():
        inp = kit.symbol_field(value="SPY", on_load=lambda: fired.append(1))
    inp.value = "NVDQ"
    _fire(inp, "focusout")
    kit.symbol_error(inp, "No such ticker")
    _fire(inp, "focusout")
    assert fired == [1, 1]
    kit.symbol_error(inp, None)
    _fire(inp, "focusout")
    assert fired == [1, 1]          # clearing the message is not a retry


def test_an_integer_field_refuses_a_fraction():
    """Through ``validate()`` alone this passed while the browser saw something
    else entirely: a ``precision`` made ui.number's own blur ``sanitize`` round
    2.5 to 2 BEFORE the check ran, so the field silently rewrote the reader's
    number and said nothing - under a docstring refusing to do exactly that,
    and in front of a paper-trade Quantity."""
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1, integer=True)
    n.value = 2.5
    _blur(n)
    assert n.value == 2.5                     # their number, untouched
    assert n.error == "Whole numbers only"    # and told what is wrong with it


def test_an_integer_field_passes_a_whole_number_through():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1, integer=True)
    n.value = 3
    _blur(n)
    assert n.value == 3 and n.error is None


def test_a_shown_error_clears_once_the_value_is_valid_again():
    """Otherwise the field stays red over a good number until a second blur."""
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
    n.value = 0
    _blur(n)
    assert n.error == "At least 1"
    n.value = 5
    assert n.error is None


def test_a_raising_validator_cannot_take_the_page_down():
    """``gate`` runs inside ``render()`` and again on every value change, so one
    bad rule would mean the page does not render at all - and would then keep
    breaking every gated button on it."""
    with ui.card():
        bad_dict = ui.input(value="x", validation={"boom": lambda v: 1 / 0}) \
            .without_auto_validation()
        bad_fn = ui.input(value="x", validation=lambda v: 1 / 0) \
            .without_auto_validation()
        go = kit.button("Load", kind="primary")
    assert kit.field_valid(bad_dict) is True     # unanswerable, not invalid
    assert kit.field_valid(bad_fn) is True
    sync = kit.gate(go, bad_dict, bad_fn)        # must not raise
    assert go.enabled                            # its own blur will say otherwise
    sync()
    assert go.enabled


# -- region, empty state, table ---------------------------------------------------
def test_the_region_spinner_survives_a_repaint():
    """Five pages mounted their spinner on the container their repaint clears,
    so it was deleted on the first paint."""
    with ui.card():
        r = kit.region("Loading…")
    with r.content:
        ui.label("old rows")
    r.content.clear()
    assert r.busy.element in r.outer.default_slot.children
    r.busy.show()
    assert r.busy.element.visible


def test_the_region_reserves_height_while_it_spins():
    """The scrim is absolute inset-0, so before the first paint it covers an
    empty div: a spinner nobody can see."""
    with ui.card():
        r = kit.region("Loading…")
    assert kit.REGION_MIN_H not in r.outer.classes
    r.busy.show()
    assert kit.REGION_MIN_H in r.outer.classes
    r.busy.hide()
    assert kit.REGION_MIN_H not in r.outer.classes


def test_the_region_backstop_releases_the_reserved_height_too():
    """The watchdog runs busy.py's own hide(), which knows nothing about the
    height this wrapper added."""
    with ui.card():
        r = kit.region("Loading…", timeout=0)
    r.busy.show()
    r.busy.timer.callback()             # what the 1s watchdog actually runs
    assert not r.busy.element.visible
    assert kit.REGION_MIN_H not in r.outer.classes


def test_empty_state_is_one_muted_line():
    with ui.card():
        e = kit.empty("Nothing traded yet today")
    assert e.text == "Nothing traded yet today" and theme.MUTED in e.classes


def test_table_columns_sortable_and_numbers_right():
    cols = [{"name": "symbol", "label": "Symbol", "field": "symbol"},
            {"name": "pnl", "label": "P&L", "field": "pnl"},
            {"name": "actions", "label": "", "field": "actions"}]
    out = kit.table_columns(cols, numeric=("pnl",))
    assert out[0]["sortable"] is True and out[0]["align"] == "left"
    assert out[1]["align"] == "right"
    assert out[2]["sortable"] is False
    assert "sortable" not in cols[0]                  # the input is not mutated


def test_an_explicit_unsortable_column_stays_unsortable():
    out = kit.table_columns([{"name": "checks", "field": "checks", "sortable": False}])
    assert out[0]["sortable"] is False


def test_mark_selected_stamps_exactly_one_row():
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    kit.mark_selected(rows, 2)
    assert [r["_selected"] for r in rows] == [False, True, False]
    kit.mark_selected(rows, None)
    assert not any(r["_selected"] for r in rows)


def test_table_is_dense_flat_and_draws_the_selected_row():
    with ui.card():
        t = kit.table([{"name": "symbol", "label": "Symbol", "field": "symbol"}], [])
    assert t._props.get("dense") is True and t._props.get("flat") is True
    assert "kit-row-selected" in t._props[":table-row-class-fn"]
    assert "row._row_class" in t._props[":table-row-class-fn"]     # a page's own class survives


def test_showing_every_row_hides_the_records_per_page_footer():
    """rowsPerPage 0 already shows every row; the footer below it then reads
    "Records per page: All" under a table that has no pages."""
    with ui.card():
        every = kit.table([{"name": "a", "label": "A", "field": "a"}], [])
        paged = kit.table([{"name": "a", "label": "A", "field": "a"}], [],
                          rows_per_page=25)
    assert every._props.get("hide-pagination") is True
    assert paged._props.get("hide-pagination") is False
    assert paged._props["pagination"] == {"rowsPerPage": 25}


def test_a_tooltip_caps_its_width_with_a_class_not_a_prop():
    """`max-width=340px` as a PROP lands as an inline style, which the app's
    Tailwind-only standard bans (tests/test_no_inline_style.py)."""
    with ui.card():
        b = kit.button("Run scan", tooltip="Scans the whole watchlist")
    (tip,) = [e for e in b.descendants() if isinstance(e, ui.tooltip)]
    assert "max-w-[340px]" in tip.classes
    assert "max-width" not in str(tip._props)


def test_an_icon_buttons_tooltip_is_capped_as_well():
    """It carries the WHOLE explanation - there is no label beside it - so it is
    the likelier one to run long."""
    with ui.card():
        b = kit.icon_button("delete", tooltip="Delete this trade from the ledger")
    (tip,) = [e for e in b.descendants() if isinstance(e, ui.tooltip)]
    assert "max-w-[340px]" in tip.classes


# -- confirm dialog ---------------------------------------------------------------
def _confirm(**kw):
    fired = []
    with ui.card():
        d = kit.confirm("Delete the NVDA call credit?", "It leaves the ledger for good.",
                        confirm_text="Delete", on_confirm=lambda: fired.append(1), **kw)
    return d, fired


def test_cancel_comes_first_and_the_row_is_right_aligned():
    d, _ = _confirm(danger=True)
    kids = list(d.actions.default_slot.children)
    assert kids.index(d.cancel) < kids.index(d.confirm)
    assert "justify-end" in d.actions.classes


def test_a_destructive_confirm_is_solid_red_and_a_plain_one_primary():
    d, _ = _confirm(danger=True)
    assert set(theme.BTN_DANGER_SOLID.split()) <= set(d.confirm.classes)
    d2, _ = _confirm()
    assert set(theme.BTN_PRIMARY.split()) <= set(d2.confirm.classes)


def test_confirm_runs_once_then_closes():
    d, fired = _confirm()
    d.open()
    asyncio.run(d.run())
    asyncio.run(d.run())          # a queued Enter after the click
    assert fired == [1] and d.dialog.value is False


def test_returning_false_keeps_the_dialog_open():
    fired = []
    with ui.card():
        d = kit.confirm("Open?", confirm_text="Open",
                        on_confirm=lambda: fired.append(1) or False)
    d.open()
    asyncio.run(d.run())
    assert fired == [1] and d.dialog.value is True


def test_a_disabled_confirm_does_nothing():
    d, fired = _confirm()
    d.open()
    d.confirm.disable()
    asyncio.run(d.run())
    assert fired == []


def test_an_async_on_confirm_actually_RUNS():
    """A coroutine is not False, so an unawaited one closed the dialog while the
    action never happened - silently, because nothing raised."""
    ran = []

    async def act():
        ran.append(1)

    with ui.card():
        d = kit.confirm("Go?", confirm_text="Go", on_confirm=act)
    d.open()
    asyncio.run(d.run())
    assert ran == [1] and d.dialog.value is False


def test_an_async_confirm_returning_false_keeps_the_dialog_open():
    async def act():
        return False

    with ui.card():
        d = kit.confirm("Go?", confirm_text="Go", on_confirm=act)
    d.open()
    asyncio.run(d.run())
    assert d.dialog.value is True


def test_a_second_trigger_while_the_action_runs_is_ignored():
    """Click then Enter: the await gives the second trigger a window the ``done``
    latch alone does not close, because ``done`` is only set after it returns."""
    fired = []

    async def act():
        fired.append(1)
        started.set()
        await release.wait()

    started, release = asyncio.Event(), asyncio.Event()
    with ui.card():
        d = kit.confirm("Go?", confirm_text="Go", on_confirm=act)
    d.open()

    async def drive():
        first = asyncio.create_task(d.run())
        await started.wait()
        await d.run()                 # a queued Enter, mid-flight
        release.set()
        await first

    asyncio.run(drive())
    assert fired == [1] and d.dialog.value is False


def test_enter_is_heard_on_the_dialog_not_the_card():
    """Quasar focuses ``q-dialog__inner`` - the card's PARENT - when a dialog
    opens, so a keydown listener on the card never hears Enter."""
    d, _ = _confirm()
    card = [e for e in d.dialog.descendants() if isinstance(e, ui.card)][0]
    assert not [x for x in card._event_listeners.values() if "keydown" in x.type]
    (enter,) = [x for x in d.dialog._event_listeners.values() if x.type == "keydown.enter"]
    assert "e.repeat" in enter.js_handler          # auto-repeat must not re-fire
    assert "isComposing" in enter.js_handler       # an IME commit is not a confirm
    assert "TEXTAREA" in enter.js_handler          # Enter in a textarea is a newline


def test_the_latch_resets_when_the_dialog_is_opened_directly():
    """A page that opens the underlying dialog (or reuses the handle) must get a
    working confirm, not a dead one."""
    d, fired = _confirm()
    d.open()
    asyncio.run(d.run())
    d.dialog.open()
    asyncio.run(d.run())
    assert fired == [1, 1]


def test_a_body_filled_in_later_is_shown_on_open():
    """The dialog is built ONCE and retitled per use, so a body set later must
    become visible rather than staying hidden from the empty first build."""
    with ui.card():
        d = kit.confirm("Go?", confirm_text="Go", on_confirm=lambda: None)
    assert not d.body.visible
    d.body.text = "It cannot be undone."
    d.open()
    assert d.body.visible


def test_the_confirm_card_fits_a_phone():
    assert kit.CONFIRM_CARD.startswith("ns-app ")     # teleported outside .ns-app
    assert "w-[min(520px,calc(100vw-48px))]" in kit.CONFIRM_CARD
    assert "min-w-[360px]" not in kit.CONFIRM_CARD    # 360 + padding overflows a phone


def test_an_ephemeral_dialog_deletes_itself_but_a_reused_one_stays():
    """A dialog built per click would otherwise leave one element behind in the
    page on every click."""
    with ui.card():
        once = kit.confirm("Go?", confirm_text="Go", on_confirm=lambda: None,
                           ephemeral=True)
        kept, _ = kit.confirm("Go?", confirm_text="Go", on_confirm=lambda: None), None
    once.open()
    asyncio.run(once.run())
    assert once.dialog.is_deleted
    kept.open()
    asyncio.run(kept.run())
    assert not kept.dialog.is_deleted


# -- information dialog, section title -------------------------------------------
def test_an_information_dialog_has_a_close_x_and_no_footer():
    with ui.card():
        d = kit.info_dialog("Trade analysis")
    assert d.title.text == "Trade analysis"
    buttons = [e for e in d.dialog.descendants() if isinstance(e, ui.button)]
    assert len(buttons) == 1 and buttons[0]._props.get("icon") == "close"
    d.open()
    d.close()
    assert d.dialog.value is False


def test_section_title_is_the_one_heading_style():
    with ui.card():
        s = kit.section_title("Open positions")
    assert s.text == "Open positions" and theme.LABEL in s.classes
