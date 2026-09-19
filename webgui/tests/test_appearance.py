"""Tests for Settings -> Appearance (pages/appearance.py)."""
import config_schema as _cs
from pages import appearance
from pages.options import theme


def _keys():
    return [(s, k) for _label, _kind, keys in appearance.GROUPS for s, k in keys]


def test_every_editable_key_is_on_the_screen_exactly_once():
    seen = _keys()
    assert len(seen) == len(set(seen)), "a key is on the screen twice"
    want = {(s, k) for s in appearance.EDITABLE_SECTIONS for k in theme._DEFAULTS[s]}
    assert set(seen) == want, (f"missing {sorted(want - set(seen))}, "
                               f"extra {sorted(set(seen) - want)}")


def test_no_retired_or_page_scoped_section_is_editable():
    sections = {s for s, _k in _keys()}
    assert sections.isdisjoint({"buttons_3d", "brand", "console", "macro",
                                "sectors", "rotation", "calc", "flow"})


def test_group_kinds_are_known():
    assert {kind for _l, kind, _k in appearance.GROUPS} <= {"color", "text", "menu"}


def test_size_error():
    for ok in ("14", "14px", " 16 ", "8", "48px"):
        assert appearance.size_error(ok) is None, ok
    assert appearance.size_error("") == "Enter a size, like 14"
    assert appearance.size_error("big") == "A number of pixels, like 14 or 14px"


def test_a_size_outside_the_readable_range_is_refused():
    """``body: 0`` is ``font-size:0px`` app-wide after the next restart, on every
    screen including this one - recoverable only by a blind Reset. The bound is
    on the NUMBER whatever unit follows, so 1.1rem reads as 1.1 and is refused;
    this field's convention is pixels."""
    for bad in ("0", "7", "49", "9999", "1.1rem"):
        assert appearance.size_error(bad) == "A size between 8 and 48 pixels", bad


def test_css_text_error_refuses_what_would_break_the_stylesheet():
    """``family`` and the six ``[menu]`` colours are interpolated RAW into
    app-wide CSS, so one ``;`` ends the declaration and takes the rest with it."""
    for ok in ("", "IBM Plex Sans", "'IBM Plex Sans', sans-serif", "#2e7d32"):
        assert appearance.css_text_error(ok) is None, ok
    for bad in ("red;}", "a{b", "x<y", "@import url(evil)"):
        assert appearance.css_text_error(bad) is not None, bad
    assert "stylesheet" in appearance.css_text_error("red; color: blue")


def test_field_error_gives_each_key_its_own_check():
    assert appearance.field_error("typography", "body", "0") is not None
    assert appearance.field_error("typography", "family", "a;b") is not None
    assert appearance.field_error("menu", "accent", "a;b") is not None
    # a size check must not run on a name, nor a CSS check on a size
    assert appearance.field_error("typography", "family", "Inter") is None
    assert appearance.field_error("typography", "font_url", "https://x/y?a=1") is None
    assert appearance.field_error("palette", "card_bg", "#123456") is None


def test_is_hex_color():
    for ok in ("#abc", "#a1b2c3", "#ABCDEF", " #123456 "):
        assert appearance.is_hex_color(ok), ok
    for bad in ("", None, "rgba(1, 2, 3, .5)", "#12345", "#abcdefff", "red"):
        assert not appearance.is_hex_color(bad), bad


def test_only_the_groups_the_preview_cannot_show_carry_a_note():
    labels = {label for label, _kind, _keys in appearance.GROUPS}
    assert set(appearance.GROUP_NOTES) == {"Charts", "Menu"}
    assert set(appearance.GROUP_NOTES) <= labels, "a note names no group"
    for note in appearance.GROUP_NOTES.values():
        assert "restart" in note


def test_edited_theme_overlays_without_mutating_the_base():
    base = theme.load_theme("Z:/nope.toml")
    out = appearance.edited_theme(base, {("palette", "card_bg"): "#123456"})
    assert out["palette"]["card_bg"] == "#123456"
    assert base["palette"]["card_bg"] == "#101a30"


def test_updates_from_groups_by_section():
    assert appearance.updates_from({("palette", "card_bg"): "#111111",
                                    ("semantic", "positive"): "#222222"}) == {
        "palette": {"card_bg": "#111111"}, "semantic": {"positive": "#222222"}}


def test_render_builds_every_group():
    from nicegui import ui
    with ui.card() as host:
        appearance.render()
    texts = {getattr(e, "text", None) for e in host.descendants()}
    for label, _kind, _keys in appearance.GROUPS:
        assert label in texts, f"group {label!r} did not render"
    assert "Save changes" in texts and "Discard" in texts


# ── the rendered page, driven ────────────────────────────────────────────────
# A real render with load_theme pinned to a COPY of the built-in defaults, so
# nothing here reads or writes the operator's config. The handlers are reached
# the way the browser reaches them: the colour picker's own on_pick, a field's
# own blur listener, a button's own click listener.
def _fixture_theme():
    import copy
    return copy.deepcopy(theme._DEFAULTS)


def _render(monkeypatch, pending=()):
    from nicegui import ui

    from pages import config_editor
    monkeypatch.setattr(theme, "load_theme", lambda *a, **k: _fixture_theme())
    monkeypatch.setattr(config_editor, "_PENDING", set(pending))
    with ui.card() as host:
        appearance.render()
    return host


def _texts(host):
    return [getattr(e, "text", None) for e in host.descendants()]


def _buttons(host):
    from nicegui import ui
    return {b.text: b for b in host.descendants() if isinstance(b, ui.button)}


def _fire(el, kind, args=None):
    """Fire an element's OWN registered listener - what the browser would send."""
    from nicegui.events import GenericEventArguments
    fired = [li.handler(GenericEventArguments(sender=el, client=el.client, args=args))
             for li in list(el._event_listeners.values())
             if li.type.split(".")[0] == kind and li.handler is not None]
    assert fired, f"no {kind} listener to fire"


def _pick(host, index, colour):
    """A colour picked on the tile at ``index`` (GROUPS order)."""
    from nicegui import ui
    from nicegui.events import ColorPickEventArguments, handle_event
    picker = [e for e in host.descendants() if isinstance(e, ui.color_picker)][index]
    handle_event(picker._pick_handlers[0],
                 ColorPickEventArguments(sender=picker, client=picker.client,
                                         color=colour))


def _type(host, label_text, value):
    """Type into the field labelled ``label_text`` and leave it."""
    from nicegui import ui
    col = [e for e in host.descendants()
           if isinstance(e, ui.label) and e.text == label_text][-1].parent_slot.parent
    inp = [e for e in col.descendants() if isinstance(e, ui.input)][0]
    inp.value = value
    _fire(inp, "blur", {})
    return inp


def _dialogs():
    """A dialog lives on the client LAYOUT (NiceGUI 3.x), not in the page slot."""
    from nicegui import context, ui
    return [e for e in context.client.layout.descendants() if isinstance(e, ui.dialog)]


def _toasts(monkeypatch):
    seen = []
    monkeypatch.setattr(appearance.kit, "toast",
                        lambda kind, text: seen.append((kind, text)))
    return seen


def test_only_the_groups_the_preview_cannot_show_draw_a_note(monkeypatch):
    host = _render(monkeypatch)
    texts = _texts(host)
    for label, note in appearance.GROUP_NOTES.items():
        assert note in texts, f"{label} lost its note"
    assert sum(1 for t in texts if t in set(appearance.GROUP_NOTES.values())) == 2


def _preview_root(host):
    """The preview's own wrapper: the header row's parent."""
    lbl = [e for e in host.descendants()
           if getattr(e, "text", None) == "Strategy Finder"][0]
    return lbl.parent_slot.parent.parent_slot.parent


def _preview_classes(host):
    root = _preview_root(host)
    return " ".join([" ".join(root.classes)]
                    + [" ".join(d.classes) for d in root.descendants()])


def test_the_preview_shows_every_surface_colour(monkeypatch):
    """It wears the PAGE token, like every real page. A flat background could
    not show page_bg1 or page_bg3 at all - the ground is a three-stop radial."""
    host = _render(monkeypatch)
    for index, colour in ((0, "#ff0001"), (1, "#ff0002"), (2, "#ff0003"),
                          (3, "#ff0004"), (4, "#ff0005"), (5, "#ff0006")):
        _pick(host, index, colour)
        assert colour in _preview_classes(host), (
            f"Surfaces tile {index} ({colour}) is nowhere in the preview")


def test_the_footer_buttons_survive_an_edit(monkeypatch):
    """They are MUTATED, never rebuilt. A rebuild on a field's blur swallowed the
    very click that caused it: mousedown, blur, repaint, and by mouseup the
    button the reader pressed is gone from the page."""
    host = _render(monkeypatch)
    before = _buttons(host)
    _pick(host, 0, "#ff0000")
    _type(host, "Body", "18")
    after = _buttons(host)
    for name in ("Save changes", "Discard", "Reset to shipped values"):
        assert after[name] is before[name], f"{name} was rebuilt"


def test_the_footer_follows_the_counts(monkeypatch):
    host = _render(monkeypatch)
    save, discard = _buttons(host)["Save changes"], _buttons(host)["Discard"]
    assert "All changes saved" in _texts(host)
    assert save.enabled is False and discard.enabled is False

    _pick(host, 0, "#ff0000")
    assert "1 unsaved change" in _texts(host)
    assert save.enabled is True and discard.enabled is True

    _pick(host, 1, "#00ff00")
    assert "2 unsaved changes" in _texts(host)

    _type(host, "Body", "big")                       # an error blocks saving
    assert "1 value to fix before saving" in _texts(host)
    assert save.enabled is False and discard.enabled is True

    _type(host, "Body", "18")                        # ... and clears again
    assert "3 unsaved changes" in _texts(host)
    assert save.enabled is True

    _pick(host, 0, "#16243f")                        # back to the shipped value
    assert "2 unsaved changes" in _texts(host)


def test_clearing_an_error_with_the_shipped_value_still_repaints(monkeypatch):
    """The VALUE did not change, so _set returns early - but the error count
    did, and the footer would otherwise go on asking for a fix forever."""
    host = _render(monkeypatch)
    _type(host, "Small", "big")
    assert "1 value to fix before saving" in _texts(host)
    _type(host, "Small", "12px")                     # the shipped value, typed back
    assert "All changes saved" in _texts(host)
    assert _buttons(host)["Save changes"].enabled is False


def test_a_bad_colour_is_refused_rather_than_stamped_into_a_class(monkeypatch):
    """``classes(remove=…)`` splits on whitespace, so an ``rgba(1, 2, 3, .5)``
    would split into four classes and the swatch could never be repainted."""
    host = _render(monkeypatch)
    said = _toasts(monkeypatch)
    _pick(host, 0, "rgba(1, 2, 3, .5)")
    assert "All changes saved" in _texts(host), "a non-hex colour became an edit"
    assert said and said[0][0] == "warn"


def test_a_failed_save_says_so_and_keeps_the_edits(monkeypatch):
    """write_overrides raises on an OSError and on its own round-trip check. The
    traceback went to the log while the footer still counted the changes."""
    from pages import config_editor
    host = _render(monkeypatch)
    said = _toasts(monkeypatch)

    def _boom(_updates, path=None):
        raise PermissionError("config/local/theme.toml is read-only")

    monkeypatch.setattr(theme, "save_theme_values", _boom)
    _pick(host, 0, "#ff0000")
    _fire(_buttons(host)["Save changes"], "click")
    assert said and said[-1][0] == "error"
    assert "read-only" in said[-1][1]
    assert "1 unsaved change" in _texts(host), "the edit was dropped after a failure"
    assert _cs.WEBGUI not in config_editor._PENDING, "a restart was asked for anyway"


def test_a_successful_save_writes_the_edits_and_asks_for_a_restart(monkeypatch):
    from pages import config_editor
    host = _render(monkeypatch)
    said = _toasts(monkeypatch)
    saved = {}

    def _save(updates, path=None):
        saved.update(updates)
        out = _fixture_theme()
        for sec, vals in updates.items():
            out[sec].update(vals)
        return out

    monkeypatch.setattr(theme, "save_theme_values", _save)
    _pick(host, 0, "#ff0000")
    _fire(_buttons(host)["Save changes"], "click")
    assert saved == {"palette": {"page_bg1": "#ff0000"}}
    assert said[-1][0] == "ok"
    assert "All changes saved" in _texts(host)
    assert _cs.WEBGUI in config_editor._PENDING


def test_a_failed_reset_says_so_and_changes_nothing(monkeypatch):
    from pages import config_editor
    host = _render(monkeypatch)
    said = _toasts(monkeypatch)

    def _boom(path=None):
        raise OSError("disk is full")

    monkeypatch.setattr(theme, "reset_theme", _boom)
    _pick(host, 0, "#ff0000")
    _confirm(_dialog_with("Reset"))
    assert said and said[-1][0] == "error"
    assert "disk is full" in said[-1][1]
    assert "1 unsaved change" in _texts(host)
    assert _cs.WEBGUI not in config_editor._PENDING


def _dialog_with(confirm_text):
    """The most recently built dialog whose confirm button says ``confirm_text``."""
    from nicegui import ui
    return [d for d in _dialogs()
            if any(isinstance(e, ui.button) and e.text == confirm_text
                   for e in d.descendants())][-1]


def _dialog_body(dlg):
    """kit.confirm's one-sentence body label (``text-sm``, under the title)."""
    from nicegui import ui
    return [e for e in dlg.descendants()
            if isinstance(e, ui.label) and "text-sm" in e.classes][0]


def _confirm(dlg):
    """Run a confirm dialog's action. Its ``run`` is a COROUTINE function, and
    nicegui would only DEFER it here (no running app loop), so drive the
    dialog's own keydown.enter listener - which kit.confirm registers as ``run``
    itself - inside a slot context (a slot stack is per asyncio TASK)."""
    import asyncio
    (run_,) = [li.handler for li in dlg._event_listeners.values()
               if li.type == "keydown.enter"]

    async def _drive(slot):
        with slot:
            await run_(None)

    asyncio.run(_drive(dlg.parent_slot))


def test_restart_names_the_edits_it_would_throw_away(monkeypatch):
    """The banner can be up from the Configuration tab's own pending restart
    while edits are pending here, and the restart takes them with it."""
    host = _render(monkeypatch, pending={_cs.WEBGUI})
    _fire(_buttons(host)["Restart now"], "click")
    body = _dialog_body(_dialog_with("Restart now"))
    assert body.text == appearance.RESTART_BODY

    _pick(host, 0, "#ff0000")
    _fire(_buttons(host)["Restart now"], "click")
    assert body.text.startswith("1 unsaved change will be lost.")
    assert appearance.RESTART_BODY in body.text

    _pick(host, 1, "#00ff00")
    _fire(_buttons(host)["Restart now"], "click")
    assert body.text.startswith("2 unsaved changes will be lost.")
