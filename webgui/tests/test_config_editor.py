"""The Configuration page's pure helpers."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config_schema as cs
import config_store as store
from pages import config_editor as ce


def _rows(name, title):
    cfg = cs.BY_NAME[name]
    sec = next(s for s in cfg.sections if s.title == title)
    import tomllib
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "config" / name
    base = store.flatten(tomllib.loads(p.read_text(encoding="utf-8")))
    return ce.expand_fields(cfg, sec, base, {})


def test_slot_wildcards_expand_to_each_named_slot_with_a_readable_label():
    rows = _rows("sessions.toml", "Hourly trade idea (Discord and Telegram)")
    labels = [label for _p, _f, label in rows]
    assert labels[0] == "Fire if late by at most"         # exact entry kept once
    assert "08:35" in labels and labels.count("Fire if late by at most") == 1


def test_optional_per_structure_rules_appear_even_when_unset():
    rows = _rows("trade_mgmt.toml", "Per-structure rules")
    paths = {p for p, _f, _l in rows}
    assert ("structures", "LONG_CALL", "debit_stop_frac") in paths


def test_display_value_speaks_units():
    f = cs.Field("x", "x", kind="fraction", unit="%")
    assert ce.display_value(0.5, 0.5, f) == "50%"
    assert ce.display_value(True, True, cs.Field("b", "b", kind="bool")) == "on"
    assert ce.display_value(None, None, f) == "not set"


def test_restart_targets_follow_section_and_field_overrides():
    cfg = cs.BY_NAME["sessions.toml"]
    assert ce.restart_targets(cfg, [("slots", "eod_report", "at")]) == [cs.TIMERS]
    assert ce.restart_targets(cfg, [("slots", "action_alert", "grace_min")]) == [cs.OPTIONS]


def test_search_needs_every_word_somewhere():
    assert ce.matches("take profit", "Take profit at", "")
    assert not ce.matches("take vix", "Take profit at", "")
    assert not ce.matches("", "anything")


def test_market_busy_is_true_mid_session_and_false_on_a_weekend():
    ct = ZoneInfo("America/Chicago")
    assert ce.market_busy(datetime(2026, 9, 16, 10, 0, tzinfo=ct))
    assert not ce.market_busy(datetime(2026, 9, 19, 10, 0, tzinfo=ct))


# ── Phase 6, Task 9: Configuration on the page kit ──────────────────────────
# The render tests below drive the page the way the browser does - a control's
# own value change, a button's own click listener, a dialog's own confirm - and
# every one of them was proved red against the pre-migration page first.
def _render(monkeypatch, pending=()):
    """The Configuration tab, rendered with the shared pending set replaced.

    Scoped to its OWN render: every assertion reads ``host.descendants()``, not
    the auto-index client the whole module shares (rule 10). Dialogs are the one
    exception - they live on the client LAYOUT - so ``_dialog_with`` takes the
    most recent match instead."""
    from nicegui import ui
    monkeypatch.setattr(ce, "_PENDING", set(pending))
    monkeypatch.setattr(ce.store, "recent_changes", lambda limit=25: [])
    with ui.card() as host:
        ce.render()
    return host


def _texts(host):
    return [getattr(e, "text", None) for e in host.descendants()]


def _buttons(host):
    from nicegui import ui
    return [b for b in host.descendants() if isinstance(b, ui.button)]


def _named(host, text):
    """⚠ A LIST, never a dict keyed by text: this page has two different
    "Restart now" buttons - the banner's and the restart dialog's."""
    return [b for b in _buttons(host) if b.text == text]


def _fire(el, kind, args=None):
    """Fire an element's OWN registered listeners - what the browser would send."""
    from nicegui import helpers
    from nicegui.events import GenericEventArguments
    e = GenericEventArguments(sender=el, client=el.client, args=args)
    fired = [h(e) if helpers.expects_arguments(h) else h()
             for li in list(el._event_listeners.values())
             for h in [li.handler]
             if li.type.split(".")[0] == kind and h is not None]
    assert fired, f"no {kind} listener to fire"


def _click(host, text):
    btn = _named(host, text)
    assert btn, f"no {text!r} button on the page"
    _fire(btn[-1], "click")
    return btn[-1]


def _dialogs():
    from nicegui import context, ui
    return [d for d in context.client.layout.descendants() if isinstance(d, ui.dialog)]


def _dialog_with(confirm_text):
    """The most recently built dialog whose confirm button says ``confirm_text``."""
    from nicegui import ui
    found = [d for d in _dialogs()
             if any(isinstance(e, ui.button) and e.text == confirm_text
                    for e in d.descendants())]
    assert found, f"no dialog confirming with {confirm_text!r}"
    return found[-1]


def _dialog_body(dlg):
    """kit.confirm's one-sentence body label (``text-sm``, under the title)."""
    from nicegui import ui
    found = [e for e in dlg.descendants()
             if isinstance(e, ui.label) and "text-sm" in e.classes]
    assert found, "the dialog has no kit.confirm body label"
    return found[0]


def _confirm(dlg):
    """Run a confirm dialog's action (the test_appearance.py recipe verbatim):
    its ``run`` is a COROUTINE function that nicegui would only DEFER here, so
    drive the dialog's own keydown.enter listener inside a slot context."""
    import asyncio
    (run_,) = [li.handler for li in dlg._event_listeners.values()
               if li.type == "keydown.enter"]

    async def _drive(slot):
        with slot:
            await run_(None)

    asyncio.run(_drive(dlg.parent_slot))


def _said(monkeypatch):
    """Everything the page reports, in order: ``(kind, text)``. Both spellings,
    so a stray ``ui.notify`` is caught rather than missed."""
    seen = []
    monkeypatch.setattr(ce.ui, "notify",
                        lambda msg="", **kw: seen.append((kw.get("type"), msg)))
    monkeypatch.setattr(ce.kit, "toast", lambda kind, text: seen.append((kind, text)))
    return seen


_FOOTER_RE = __import__("re").compile(
    r"^(All changes saved|\d+ unsaved change|\d+ value)")


def _footer_status(host):
    """The footer's one status line. ⚠ Anchored on a COUNT: the left
    navigation's per-file chip carries an "unsaved changes" TOOLTIP, and a
    substring match picks that up as a second footer."""
    return [t for t in _texts(host) if t and _FOOTER_RE.match(t)]


def _edit_a_number(host):
    """Change one numeric setting to a value the schema accepts, the way the
    browser's own value-change event would. Tries each number in turn and keeps
    the first whose new value the page counts as an edit, so this does not rest
    on which file happens to be first."""
    from nicegui import ui
    for n in [e for e in host.descendants() if isinstance(e, ui.number)]:
        if not isinstance(n.value, (int, float)) or isinstance(n.value, bool):
            continue
        was = n.value
        n.value = was + (n._props.get("step") or 1)
        if _footer_status(host) == ["1 unsaved change"]:
            return n
        n.value = was                      # put it back and try the next one
    raise AssertionError("no numeric setting could be edited")


def test_the_configuration_tab_carries_the_kit_header_and_no_second_description(
        monkeypatch):
    """The page's own paragraph moved to page_help - the hover guide already
    explains the tab, and the standard's header line is title + actions only."""
    host = _render(monkeypatch)
    texts = _texts(host)
    assert "Configuration" in texts
    assert not [t for t in texts
                if t and t.startswith("Every setting the trading services read")]


def test_the_footer_buttons_survive_an_edit(monkeypatch):
    """They are MUTATED, never rebuilt - the appearance.py rule, and this page
    still had the bug it was written for. A rebuild on a field's change
    swallowed the very click that caused it: mousedown, blur, repaint, and by
    mouseup the button the reader pressed is gone from the page."""
    host = _render(monkeypatch)
    before = {t: _named(host, t)[-1] for t in ("Save changes", "Discard")}
    _edit_a_number(host)
    for name, was in before.items():
        assert _named(host, name)[-1] is was, f"{name} was rebuilt"


def test_the_footer_follows_the_counts(monkeypatch):
    host = _render(monkeypatch)
    save = _named(host, "Save changes")[-1]
    discard = _named(host, "Discard")[-1]
    assert _footer_status(host) == ["All changes saved"]
    assert save.enabled is False and discard.enabled is False

    _edit_a_number(host)
    assert _footer_status(host) == ["1 unsaved change"]
    assert save.enabled is True and discard.enabled is True

    _click(host, "Discard")
    assert _footer_status(host) == ["All changes saved"]
    assert save.enabled is False and discard.enabled is False


def test_save_writes_through_config_store_and_nothing_else(monkeypatch):
    """The ``config/local/`` write path is guarded below the page by
    shared/tests/test_config_overrides.py::test_the_tracked_file_is_never_written.
    This is the page-level half: Save reaches ``store.save`` with the overrides
    it built, and the page writes no file of its own - a tracked config file
    written from here would dirty the prod checkout, which ``tools/promote.sh``
    refuses."""
    import inspect
    host = _render(monkeypatch)
    said = _said(monkeypatch)
    saved = []
    monkeypatch.setattr(ce.store, "save",
                        lambda name, over, changes=(): saved.append(
                            (name, over, list(changes))))
    _edit_a_number(host)
    _click(host, "Save changes")
    assert len(saved) == 1, saved
    name, over, changes = saved[0]
    assert name.endswith(".toml") and over and len(changes) == 1
    assert said and said[-1][0] == "ok", said
    src = inspect.getsource(ce)
    for writer in ("write_text(", "json.dump", "mkdir("):
        assert writer not in src, f"the page writes on its own: {writer}"


def test_a_missing_sector_pick_is_reported_under_the_field_not_in_a_toast(
        monkeypatch):
    """Both fields are two inches from the button, and the standard shows
    validation inline."""
    from nicegui import ui
    host = _render(monkeypatch)
    said = _said(monkeypatch)
    cfg = next(c for c in ce.cs.EDITABLE if c.editor == "sectors")
    _click_nav(host, cfg.title)
    _click(host, "Add")
    assert said == [], f"it still reports in a toast: {said}"
    errs = [e.error for e in host.descendants()
            if isinstance(e, (ui.input, ui.select)) and e.error]
    assert errs, "nothing was said under either field"


def _click_nav(host, title):
    """Pick a category from the left navigation, the way a click on its row
    would."""
    from nicegui import ui
    rows = [e for e in host.descendants()
            if isinstance(e, ui.row)
            and any(getattr(c, "text", None) == title for c in e.descendants())
            and any(li.type.split(".")[0] == "click"
                    for li in e._event_listeners.values())]
    assert rows, f"no navigation row for {title!r}"
    _fire(rows[-1], "click")


def test_a_cross_check_failure_is_one_toast_not_one_per_problem(monkeypatch):
    """``cross_check`` returns a SENTENCE PER CLASH and sessions.toml can raise
    nine at once - nine stacked 8-second errors competing for the same corner.
    One error carries every one of them instead; nothing is dropped."""
    host = _render(monkeypatch)
    said = _said(monkeypatch)
    monkeypatch.setattr(ce.store, "save", lambda *a, **k: None)
    monkeypatch.setattr(ce.cs, "cross_check",
                        lambda name, values: ["first clash.", "second clash.",
                                              "third clash."])
    _edit_a_number(host)
    _click(host, "Save changes")
    assert len(said) == 1, said
    kind, text = said[0]
    assert kind == "error"
    for p in ("first clash.", "second clash.", "third clash."):
        assert p in text


def test_the_restart_dialog_holds_its_own_confirm_and_reports_once_per_outcome(
        monkeypatch):
    """"Restarting…" is gone: ``dlg.close()`` ran before it, so there was no
    element left to spin. The dialog stays open with its confirm spinning
    instead - which is also what stops a second click firing a second restart -
    and the per-unit loop becomes at most two toasts, one for what came back
    and one for what did not."""
    from nicegui import ui
    host = _render(monkeypatch, pending={ce.cs.OPTIONS, ce.cs.TIMERS})
    said = _said(monkeypatch)
    seen = {}
    _click(host, "Restart now")                      # the banner's
    dlg = _dialog_with("Restart now")
    confirm = [b for b in dlg.descendants()
               if isinstance(b, ui.button) and b.text == "Restart now"][-1]

    def _restart(units):
        seen["busy"] = confirm._props.get("loading")
        seen["units"] = list(units)
        return [(units[0], True, ""), (units[1], False, "unit not found")]

    monkeypatch.setattr(ce, "_restart_units", _restart)
    _confirm(dlg)
    assert seen.get("busy") is True, "the confirm never spun"
    assert not confirm._props.get("loading"), "the spinner was left running"
    assert [k for k, _t in said] == ["ok", "error"], said
    assert "unit not found" in said[-1][1]
    assert not any("Restarting" in t and t.endswith("…") for _k, t in said)


def test_the_reset_dialog_is_destructive_and_asks_cancel_first(monkeypatch):
    """Reset removes every editable override in that file - and it was a
    quiet grey link beside a primary-blue confirm."""
    from nicegui import ui
    host = _render(monkeypatch)
    said = _said(monkeypatch)
    monkeypatch.setattr(ce.store, "save", lambda *a, **k: None)
    reset = [b for b in _buttons(host) if b.text == "Reset to shipped values"]
    assert reset, "no per-file Reset on the page"
    assert "border" in " ".join(reset[-1].classes), "Reset is not the danger kind"
    _fire(reset[-1], "click")
    dlg = _dialog_with("Reset")
    labels = [b.text for b in dlg.descendants() if isinstance(b, ui.button)]
    assert labels == ["Cancel", "Reset"], labels
    assert _dialog_body(dlg).text, "the destructive dialog says only its title"
    _confirm(dlg)
    assert said and said[-1][0] == "ok", said


def test_the_ladder_rung_remove_button_says_what_it_does():
    """``kit.icon_button`` requires a tooltip: an icon alone does not say what
    it does, and this one DELETES a rung of the profit-lock ladder."""
    from nicegui import ui
    fld = ce.cs.Field("trail.ladders.ratchet", "Ratchet", kind="ladder")
    with ui.card() as host:
        ce._build_control({}, fld, [[50, 0], [75, 25]], lambda _raw: None)
    icons = [b for b in host.descendants()
             if isinstance(b, ui.button) and not b.text]
    assert icons, "no remove button on a ladder rung"
    for b in icons:
        assert [t for t in b.descendants() if isinstance(t, ui.tooltip)], \
            "a ladder rung's remove button has no tooltip"


def test_the_pending_set_and_the_webgui_restart_keep_their_names():
    """``pages/appearance.py`` reads and writes ``_PENDING`` in three places and
    calls ``_restart_webgui()``; tests/test_appearance.py monkeypatches
    ``_PENDING`` in four. They are a de-facto public API between the two
    Settings tabs, so they keep their names, their module and the set type."""
    import inspect

    from pages import appearance
    assert isinstance(ce._PENDING, set)
    assert callable(ce._restart_webgui)
    src = inspect.getsource(appearance)
    assert "config_editor._PENDING" in src
    assert "config_editor._restart_webgui()" in src


# ── Phase 6, Task 10: the change-log clock ──────────────────────────────────
def test_a_stamped_change_names_its_zone():
    """An aware row is rendered in Central and SAYS so. The project convention is
    Central everywhere with the zone named; this row was the last clock in the
    app carrying neither."""
    ct = ZoneInfo("America/Chicago")
    assert ce.change_stamp(
        datetime(2026, 9, 20, 14, 3, 22, tzinfo=ct).isoformat(
            timespec="seconds")) == "2026-09-20 14:03 CT"


def test_a_stamp_written_in_another_zone_is_converted_not_relabelled():
    """The reader converts. A UTC stamp is 14:03Z -> 09:03 CT on a September
    day, and the row must not print 14:03 with a CT label on it."""
    from datetime import timezone
    assert ce.change_stamp(
        datetime(2026, 9, 20, 14, 3, 22, tzinfo=timezone.utc).isoformat(
            timespec="seconds")) == "2026-09-20 09:03 CT"


def test_a_naive_legacy_row_still_reads_and_is_never_labelled_ct():
    """⚠ ``changes.jsonl`` already holds rows stamped ``datetime.now()`` with no
    zone. They were written by the HOST clock - which on this box is Central, so
    they sort and read correctly beside the new ones - but nothing recorded that,
    so the row may not claim it. It renders exactly as it always did."""
    assert ce.change_stamp("2026-09-20T14:03:22") == "2026-09-20 14:03"


def test_an_unreadable_stamp_renders_as_nothing_rather_than_raising():
    for bad in ("", None, "sometime", "2026-13-45T99:99"):
        assert ce.change_stamp(bad) == ""


def test_the_recent_changes_row_carries_the_zone(monkeypatch):
    """Driven through ``render()``: the expansion's row is where the operator
    actually reads the clock, and a pure helper nothing calls proves nothing."""
    from nicegui import ui
    ct = ZoneInfo("America/Chicago")
    rows = [{"at": datetime(2026, 9, 20, 14, 3, 22, tzinfo=ct).isoformat(
        timespec="seconds"), "file": "paper.toml", "key": "risk › cap",
        "from": 3000.0, "to": 2000.0}]
    monkeypatch.setattr(ce, "_PENDING", set())
    monkeypatch.setattr(ce.store, "recent_changes", lambda limit=25: rows)
    with ui.card() as host:
        ce.render()
    line = [t for t in _texts(host) if t and "risk › cap" in t]
    assert line, "the change row is not on the page"
    assert line[0].startswith("2026-09-20 14:03 CT · ")


# ── Market news: the feeds list is shown read-only, the switches are edited ──
_INPUTS = ("input", "number", "select", "switch", "input_chips", "checkbox")


def _row_with(host, text):
    """The one field row (``_ROW``: bordered) whose label reads ``text``."""
    rows = [e for e in host.descendants()
            if type(e).__name__ == "Row" and "border-b" in e.classes
            and any(getattr(c, "text", None) == text for c in e.descendants())]
    assert rows, f"no field row labelled {text!r}"
    return rows[-1]


def _controls(row):
    from nicegui import ui
    kinds = tuple(getattr(ui, k) for k in _INPUTS)
    return [e for e in row.descendants() if isinstance(e, kinds)]


def test_a_read_only_feed_field_renders_its_value_without_an_input(monkeypatch):
    host = _render(monkeypatch)
    _click_nav(host, "Market news")
    row = _row_with(host, "MarketWatch — Feed URL")
    assert _controls(row) == [], "a read-only field grew an input"
    texts = [getattr(c, "text", None) for c in row.descendants()]
    assert any(t and t.startswith("https://feeds.content.dowjones.io") for t in texts)


def test_each_feed_switch_is_a_row_named_by_its_feed(monkeypatch):
    from nicegui import ui
    host = _render(monkeypatch)
    _click_nav(host, "Market news")
    for label in ("SEC Insider Buys — Show on the public site",
                  "Federal Reserve — Enabled", "GlobeNewswire — Enabled"):
        (sw,) = _controls(_row_with(host, label))
        assert isinstance(sw, ui.switch), label
    assert _controls(_row_with(host, "GlobeNewswire — Enabled"))[0].value is False


def test_saving_a_switch_writes_only_the_switch_and_keeps_a_hand_written_feed_list(
        monkeypatch):
    """The save path never writes the read-only list: a hand-written
    config/local [[feeds]] override is carried verbatim, and the switch lands as
    a TABLE entry that merges key by key."""
    from nicegui import ui
    real_load = ce.store.load
    hand = {"feeds": [{"name": "Mine", "kind": "rss", "url": "https://mine"}]}

    def _load(name):
        shipped, over = real_load(name)
        return (shipped, hand) if name == "news.toml" else (shipped, over)

    monkeypatch.setattr(ce.store, "load", _load)
    host = _render(monkeypatch)
    _said(monkeypatch)
    saved = []
    monkeypatch.setattr(ce.store, "save", lambda name, over, changes=(): saved.append(
        (name, over, list(changes))))
    _click_nav(host, "Market news")
    (sw,) = _controls(_row_with(host, "ZeroHedge — Show on the public site"))
    assert isinstance(sw, ui.switch) and sw.value is True
    sw.value = False
    _click(host, "Save changes")
    (name, over, changes), = saved
    assert name == "news.toml"
    assert over == {"feeds": hand["feeds"],
                    "feed_flags": {"ZeroHedge": {"public": False}}}
    assert [c[0] for c in changes] == ["feed_flags › ZeroHedge › public"]


def test_overrides_to_save_drops_a_read_only_edit_and_keeps_the_existing_one():
    import tomllib
    import pathlib
    cfg = cs.BY_NAME["news.toml"]
    shipped = tomllib.loads((pathlib.Path(__file__).resolve().parents[2]
                             / "config" / "news.toml").read_text(encoding="utf-8"))
    values = store.flatten(shipped)
    values[("feeds", "0", "url")] = "https://evil"        # somehow edited
    values[("feed_flags", "WSJ", "enabled")] = False
    assert ce.overrides_to_save(cfg, shipped, {}, values) == {
        "feed_flags": {"WSJ": {"enabled": False}}}
    kept = [{"name": "Mine", "kind": "rss", "url": "https://mine"}]
    got = ce.overrides_to_save(cfg, shipped, {"feeds": kept}, values)
    assert got["feeds"] == kept and got["feeds"] is not kept


# ── Reset keeps the read-only part of an override (2026-09-26 decision) ──────
def _shipped_news():
    import tomllib
    import pathlib
    return tomllib.loads((pathlib.Path(__file__).resolve().parents[2]
                          / "config" / "news.toml").read_text(encoding="utf-8"))


def test_reset_plan_keeps_a_hand_written_feed_list_and_drops_the_switches():
    cfg = cs.BY_NAME["news.toml"]
    shipped = _shipped_news()
    kept = [{"name": "Mine", "kind": "rss", "url": "https://mine"}]
    over = {"feeds": kept, "feed_flags": {"ZeroHedge": {"public": False}}}
    values = store.flatten(store.effective(shipped, over))
    new_over, changed = ce.reset_plan(cfg, shipped, over, values)
    assert new_over == {"feeds": kept}
    assert changed == [("feed_flags", "ZeroHedge", "public")]


def test_reset_plan_without_read_only_sections_still_writes_an_empty_override():
    cfg = cs.BY_NAME["scanner.toml"]
    shipped, _ = store.load("scanner.toml")
    base = store.flatten(shipped)
    path = next(p for p, v in base.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                and not cs.is_readonly(cfg, p))
    over = store.build_overrides(shipped, {path: base[path] + 1})
    assert over, "the fixture override is empty"
    values = store.flatten(store.effective(shipped, over))
    new_over, changed = ce.reset_plan(cfg, shipped, over, values)
    assert new_over == {}
    assert changed == [path]


def test_the_reset_button_on_market_news_keeps_the_hand_written_feeds(monkeypatch):
    real_load = ce.store.load
    kept = [{"name": "Mine", "kind": "rss", "url": "https://mine"}]
    hand = {"feeds": kept, "feed_flags": {"ZeroHedge": {"public": False}}}

    def _load(name):
        shipped, over = real_load(name)
        return (shipped, hand) if name == "news.toml" else (shipped, over)

    monkeypatch.setattr(ce.store, "load", _load)
    monkeypatch.setattr(ce, "_PENDING", set())
    host = _render(monkeypatch)
    _said(monkeypatch)
    saved = []
    monkeypatch.setattr(ce.store, "save", lambda name, over, changes=(): saved.append(
        (name, over, list(changes))))
    _click_nav(host, "Market news")
    reset = [b for b in _buttons(host) if b.text == "Reset to shipped values"]
    _fire(reset[-1], "click")
    dlg = _dialog_with("Reset")
    body = _dialog_body(dlg).text
    assert "read-only" in body and "stay" in body, body
    _confirm(dlg)
    (name, over, changes), = saved
    assert name == "news.toml"
    assert over == {"feeds": kept}
    assert [c[0] for c in changes] == ["feed_flags › ZeroHedge › public"]


# ── news v2 (T11): impact keywords, calendar sources and indicators ──────────
def test_a_phrases_field_is_a_chip_editor_that_keeps_case():
    """Keyword phrases are chips like symbols, but never upper-cased: "rate cut"
    matches a headline case-insensitively and reads better as typed."""
    from nicegui import ui
    fld = ce.cs.Field("impact.keywords.tier1.words", "Words", kind="phrases")
    got = []
    with ui.card() as host:
        control = {}
        ce._build_control(control, fld, ["rate cut", "FOMC"], got.append)
    (chips,) = [e for e in host.descendants() if isinstance(e, ui.input_chips)]
    assert chips.value == ["rate cut", "FOMC"]
    chips.value = ["rate cut", "FOMC", "Fed chair"]
    assert got[-1] == ["rate cut", "FOMC", "Fed chair"]
    assert ce.cs.parse(fld, got[-1]) == ["rate cut", "FOMC", "Fed chair"]
    control["set"](["tariff"])
    assert chips.value == ["tariff"]


def test_a_phrases_value_reads_as_a_list_in_the_changed_chip():
    fld = ce.cs.Field("w", "w", kind="phrases")
    assert ce.display_value(["rate cut", "FOMC"], [], fld) == "rate cut, FOMC"
    assert ce.display_value([], [], fld) == "none"


def test_news_v2_wildcard_rows_are_named_after_what_they_belong_to(monkeypatch):
    host = _render(monkeypatch)
    _click_nav(host, "Market news")
    for label in ("Tier 1 — Words", "Tier 3 — Points",
                  "Federal Reserve", "424B5", "S-3ASR",
                  "Nasdaq IPOs — User-Agent", "BLS — Refresh every",
                  "CPI — FRED series", "Jobless claims — FRED release number"):
        _row_with(host, label)
    (chips,) = _controls(_row_with(host, "Tier 1 — Words"))
    assert "rate cut" in chips.value and "FOMC" in chips.value
