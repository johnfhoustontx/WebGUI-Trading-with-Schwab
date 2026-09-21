"""Tests for the Settings page pure helpers."""
from pages import settings as S


def test_api_stats_rows_formats_counts():
    rows = S.api_stats_rows({"today": 1234, "last_7_days": 56789,
                             "last_30_days": 250000, "since": "2026-07-12"})
    assert rows == [("Today", "1,234"), ("Last 7 days", "56,789"),
                    ("Last 30 days", "250,000")]


def test_api_stats_rows_placeholder_when_proxy_down():
    assert S.api_stats_rows(None) == [
        ("Today", "—"), ("Last 7 days", "—"), ("Last 30 days", "—")]
    # malformed values degrade per-field, never raise
    rows = S.api_stats_rows({"today": "x"})
    assert rows[0] == ("Today", "—") and rows[1] == ("Last 7 days", "0")


# ── ticker toggle ────────────────────────────────────────────────────────────
# Since 2026-09-10 the toggle only hides the marquee: the Claude summary also
# feeds the Desk's MARKET SUMMARY frame, so switching the marquee off must NOT
# stop it. The toggle therefore sends market_svc nothing.


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(S.bus_client, "request",
                        lambda domain, cmd: sent.append((domain, cmd)))
    return sent


def test_apply_ticker_enabled_persists_and_commands_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()
    sent = _capture(monkeypatch)
    S.apply_ticker_enabled(False)
    assert S.app_settings.load()["ticker_enabled"] is False
    S.apply_ticker_enabled(True)
    assert S.app_settings.load()["ticker_enabled"] is True
    assert sent == []


# ── captured auto-close toggle → options_svc ────────────────────────────────
def test_apply_captured_autoclose_persists_and_commands_the_service(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()
    sent = _capture(monkeypatch)

    S.apply_captured_autoclose(False)
    assert S.app_settings.load()["captured_autoclose_enabled"] is False
    assert sent == [("options", {"type": "set_autoclose", "args": {"enabled": False}})]

    S.apply_captured_autoclose(True)
    assert S.app_settings.load()["captured_autoclose_enabled"] is True
    assert sent[-1] == ("options", {"type": "set_autoclose", "args": {"enabled": True}})


def test_apply_captured_autoclose_persists_even_if_the_bus_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()

    def _boom(domain, cmd):
        raise RuntimeError("redis down")

    monkeypatch.setattr(S.bus_client, "request", _boom)
    S.apply_captured_autoclose(False)  # must not raise out of the click handler
    assert S.app_settings.load()["captured_autoclose_enabled"] is False


# ── manual-paper break-even lifecycle toggle → options_svc (Task 3) ─────────
# Flag default OFF (the inverse of captured autoclose) — an inert placeholder
# until explicitly enabled.
def test_apply_manual_paper_lifecycle_persists_and_commands_the_service(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()
    sent = _capture(monkeypatch)

    S.apply_manual_paper_lifecycle(True)
    assert S.app_settings.load()["manual_paper_lifecycle_enabled"] is True
    assert sent == [("options", {"type": "set_manual_paper_lifecycle",
                                 "args": {"enabled": True}})]

    S.apply_manual_paper_lifecycle(False)
    assert S.app_settings.load()["manual_paper_lifecycle_enabled"] is False
    assert sent[-1] == ("options", {"type": "set_manual_paper_lifecycle",
                                    "args": {"enabled": False}})


def test_apply_manual_paper_lifecycle_persists_even_if_the_bus_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()

    def _boom(domain, cmd):
        raise RuntimeError("redis down")

    monkeypatch.setattr(S.bus_client, "request", _boom)
    S.apply_manual_paper_lifecycle(True)  # must not raise out of the click handler
    assert S.app_settings.load()["manual_paper_lifecycle_enabled"] is True


def test_every_desk_voice_section_has_a_settings_switch():
    """The Settings card's switches and the Desk's section map must cover the
    same keys, or a section speaks with no way to silence it."""
    from pages import desk
    from pages import settings as st
    assert ({k for k, _ in st.VOICE_SECTION_SWITCHES}
            == set(desk.VOICE_SECTIONS.values()))


def test_settings_has_three_tabs_in_order():
    from pages import settings
    assert settings.SUBTABS == ("General", "Appearance", "Configuration")


def test_appearance_is_its_own_tab_rendered_from_its_own_module():
    """Appearance was a card on General until the tabs split. The old form of
    this test asserted the word was ABSENT from ``_render_general``, which a
    page that had never carried the card passes just as well — it says nothing
    about where Appearance actually lives. This is the positive form."""
    import inspect
    from pages import settings
    src = inspect.getsource(settings.render)
    assert 'ui.tab_panel("Appearance")' in src and "appearance.render()" in src
    assert "Appearance" not in inspect.getsource(settings._render_general)


# ── Phase 6, Task 8: the General tab on the page kit ─────────────────────────
# The render tests below drive the page the way the browser does - a button's
# own click listener, a field's own blur listener, a dialog's own confirm - and
# every one of them was proved red against the pre-migration page first.
def _render(monkeypatch, tmp_path):
    """The General tab, rendered against a settings store in ``tmp_path``.

    Scoped to its OWN render: every assertion reads ``host.descendants()``, not
    the auto-index client the whole module shares (rule 10). Dialogs are the one
    exception - they live on the client LAYOUT - so ``_dialog_with`` takes the
    most recent match instead."""
    from nicegui import ui
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()
    with ui.card() as host:
        S._render_general()
    return host


def _texts(host):
    return [getattr(e, "text", None) for e in host.descendants()]


def _buttons(host):
    from nicegui import ui
    return [b for b in host.descendants() if isinstance(b, ui.button)]


def _fire(el, kind, args=None):
    """Fire an element's OWN registered listeners - what the browser would send.

    ``expects_arguments`` mirrors nicegui's own ``handle_event``: ``ui.number``
    registers its ``sanitize`` on blur and that one takes NO argument, so a
    helper that always passes one dies on the very listener this page's number
    field exists to talk about."""
    from nicegui import helpers
    from nicegui.events import GenericEventArguments
    e = GenericEventArguments(sender=el, client=el.client, args=args)
    fired = [h(e) if helpers.expects_arguments(h) else h()
             for li in list(el._event_listeners.values())
             for h in [li.handler]
             if li.type.split(".")[0] == kind and h is not None]
    assert fired, f"no {kind} listener to fire"


def _click(host, text):
    btn = [b for b in _buttons(host) if b.text == text]
    assert btn, f"no {text!r} button on the page"
    _fire(btn[-1], "click")
    return btn[-1]


def _click_async(btn):
    """Press a button whose handler is a COROUTINE.

    nicegui hands an awaitable handler to ``background_tasks.create_or_defer``,
    which without ``core.loop`` set only DEFERS it to app startup - so the
    handler never runs and the test asserts nothing. Pointing ``core.loop`` at
    the loop this helper runs under makes it a real task; the ticks then let it
    finish, since the page's own awaits are short."""
    import asyncio

    import pytest as _pytest
    from nicegui import core
    mp = _pytest.MonkeyPatch()

    async def _drive():
        mp.setattr(core, "loop", asyncio.get_running_loop())
        _fire(btn, "click")
        for _ in range(20):
            await asyncio.sleep(0)

    try:
        asyncio.run(_drive())
    finally:
        mp.undo()


def _control(host, label_text):
    """The control sitting under (or beside) the label ``label_text``."""
    from nicegui import ui
    lbl = [e for e in host.descendants()
           if isinstance(e, ui.label) and e.text == label_text]
    assert lbl, f"no {label_text!r} label on the page"
    box = lbl[-1].parent_slot.parent
    found = [e for e in box.descendants()
             if isinstance(e, (ui.number, ui.select, ui.slider, ui.input))]
    assert found, f"no control under {label_text!r}"
    return found[0]


def _switch(host, label_text):
    from nicegui import ui
    found = [e for e in host.descendants()
             if isinstance(e, ui.switch) and e.text == label_text]
    assert found, f"no {label_text!r} switch on the page"
    return found[-1]


def _dialog_with(confirm_text):
    """The most recently built dialog whose confirm button says ``confirm_text``.

    Scoped by RECENCY rather than by the page: a dialog lives on the client
    LAYOUT (NiceGUI 3.x), which the whole test module shares."""
    from nicegui import context, ui
    found = [d for d in context.client.layout.descendants()
             if isinstance(d, ui.dialog)
             and any(isinstance(e, ui.button) and e.text == confirm_text
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


def _timer(host, name):
    """The page's ``ui.timer`` whose callback is ``name`` (``functools.wraps``
    keeps the name through ``guard_async``)."""
    from nicegui import ui
    found = [e for e in host.descendants()
             if isinstance(e, ui.timer)
             and getattr(e.callback, "__name__", "") == name]
    assert found, f"no ui.timer registered for {name}()"
    return found[-1]


def _run_timer(host, name):
    """Drive a page's once-timer the way the browser's first tick would."""
    import asyncio
    import inspect
    t = _timer(host, name)

    async def _drive():
        with t.parent_slot:
            result = t.callback()
            if inspect.isawaitable(result):
                await result

    asyncio.run(_drive())


def _said(monkeypatch):
    """Everything the page reports, in order: ``(kind, text)``. Both spellings,
    so a stray ``ui.notify`` is caught rather than missed."""
    seen = []
    monkeypatch.setattr(S.ui, "notify",
                        lambda msg="", **kw: seen.append((kw.get("type"), msg)))
    monkeypatch.setattr(S.kit, "toast", lambda kind, text: seen.append((kind, text)))
    return seen


def test_the_general_tab_is_titled_after_its_TAB_not_the_page(monkeypatch, tmp_path):
    """"Settings" is the breadcrumb already; a second one under it reads as a
    heading for the whole page rather than for this tab. Appearance and
    Configuration set the precedent."""
    host = _render(monkeypatch, tmp_path)
    texts = _texts(host)
    assert "General" in texts, "the kit header is missing"
    assert "Settings" not in texts, "the old page title is still a second heading"


def test_vacuum_body_names_the_purge_only_when_it_is_armed():
    """PURE. The switch above the button arms ``--purge``, which DELETES all but
    the last five sessions of GEX history - so a question that only mentions the
    lock lets a reader confirm a deletion it never named."""
    plain = S.vacuum_body(False)
    armed = S.vacuum_body(True)
    assert "purge" not in plain.lower() and "5" not in plain
    assert "5" in armed and "delet" in armed.lower()
    assert plain in armed, "the armed sentence dropped what the plain one says"


def test_the_vacuum_confirm_says_whether_the_purge_is_armed(monkeypatch, tmp_path):
    host = _render(monkeypatch, tmp_path)
    _click(host, "Vacuum GEX history DB")
    body = _dialog_body(_dialog_with("Run VACUUM"))
    assert body.text == S.vacuum_body(False)

    _switch(host, "Also purge old sessions first (keep last 5)").value = True
    _click(host, "Vacuum GEX history DB")
    assert body.text == S.vacuum_body(True)
    assert "delet" in body.text.lower(), "the dialog still never says what --purge does"


def test_the_vacuum_confirm_asks_cancel_first(monkeypatch, tmp_path):
    """Confirm-then-Cancel, with Cancel styled as a full primary, is the exact
    inconsistency the standard names."""
    from nicegui import ui
    host = _render(monkeypatch, tmp_path)
    _click(host, "Vacuum GEX history DB")
    dlg = _dialog_with("Run VACUUM")
    labels = [b.text for b in dlg.descendants() if isinstance(b, ui.button)]
    assert labels == ["Cancel", "Run VACUUM"], labels


def test_the_minimum_score_says_why_instead_of_silently_clamping(monkeypatch,
                                                                 tmp_path):
    """``min``/``max`` on the widget make Quasar's own blur ``sanitize`` rewrite
    what the reader typed, with nothing said; the kit refuses it and says what
    was wrong. An out-of-range score must not be written through either."""
    host = _render(monkeypatch, tmp_path)
    n = _control(host, "Minimum score to alert")
    n.value = 150
    _fire(n, "blur", {})
    assert n.error == "At most 100", n.error
    assert n.value == 150, "the typed value was silently rewritten"
    assert S.app_settings.load()["alert_min_score"] == 0

    n.value = 60
    _fire(n, "blur", {})
    assert n.error is None
    assert S.app_settings.load()["alert_min_score"] == 60


def test_test_voice_holds_its_button_and_reports_a_dead_voice_as_a_toast(
        monkeypatch, tmp_path):
    """``voice.ensure`` BLOCKS for 0.9-2.4 s on a cache miss and the button said
    nothing while it did. The spinner is released in a ``finally``, so a failure
    does not leave it spinning."""
    host = _render(monkeypatch, tmp_path)
    said = _said(monkeypatch)
    btn = [b for b in _buttons(host) if b.text == "Test voice"][-1]
    seen = {}

    def _ensure(_phrase, _name):
        seen["busy"] = btn._props.get("loading")
        return None                      # no network / no edge-tts

    monkeypatch.setattr(S.voice, "ensure", _ensure)
    _click_async(btn)
    assert seen.get("busy") is True, "the button never showed its own spinner"
    assert not btn._props.get("loading"), "the spinner was left running"
    assert said and said[-1][0] == "warn", said


def test_vacuum_holds_its_button_and_the_page_keeps_the_output(monkeypatch,
                                                              tmp_path):
    """The confirm arms the page's own button and hands the work back to the
    loop: ``kit.confirm`` holds its dialog open until ``on_confirm`` returns,
    and VACUUM runs for minutes."""
    host = _render(monkeypatch, tmp_path)
    btn = [b for b in _buttons(host) if b.text == "Vacuum GEX history DB"][-1]
    seen = {}

    def _run(purge):
        seen["purge"] = purge
        seen["busy"] = btn._props.get("loading")
        return "  reclaimed 412 MB\n"

    monkeypatch.setattr(S, "run_vacuum", _run)
    _click(host, "Vacuum GEX history DB")
    _confirm(_dialog_with("Run VACUUM"))
    assert btn._props.get("loading") is True, "the button never spun"
    _run_timer(host, "_vacuum")
    assert seen == {"purge": False, "busy": True}
    assert "reclaimed 412 MB" in _texts(host)
    assert not btn._props.get("loading"), "the spinner was left running"


def test_vacuum_command_adds_the_purge_flag_only_when_asked():
    """PURE, and the reason the confirm can name it: one place decides."""
    plain, cwd = S.vacuum_command(False)
    armed, _cwd = S.vacuum_command(True)
    assert plain[-1].endswith("vacuum_gex.py") and "--purge" not in plain
    assert armed[-1] == "--purge" and armed[:-1] == plain
    assert cwd


# ── the public Strategy Finder's usage row ──────────────────────────────────

def test_public_scan_rows_read_the_budget_and_nothing_else():
    from pages import settings
    rows = settings.public_scan_rows({"scans_today": 12, "daily_budget": 200,
                                      "invalid_today": 3,
                                      "last": {"NVDA": {"outcome": "scanned"}}})
    assert rows == [("Scans today", "12 of 200"), ("Refused as not a symbol", "3")]
    assert "NVDA" not in repr(rows)            # never which symbols were asked


def test_public_scan_rows_with_nothing_read_show_dashes_not_zero():
    from pages import settings
    assert settings.public_scan_rows(None) == [("Scans today", "—"),
                                               ("Refused as not a symbol", "—")]
