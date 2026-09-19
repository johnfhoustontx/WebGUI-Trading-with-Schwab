"""Settings -> Appearance: every colour and font in the app, with a live preview.

Since 2026-09-19 every screen is built from ``pages/ui_kit.py`` on one palette,
so this editor restyles the WHOLE app - which is also what makes its preview
honest: it draws the kit's pieces from the same tokens, with the unsaved colours
laid over the saved ones.

The groups follow the app's standard (surfaces, text, fields, buttons, status
colours, charts, type, menu), not ``config/theme.toml``'s sections: a group is
a DISPLAY mapping over existing keys, so the file never has to move for the
screen to make sense. Saving writes the operator's override
(``config/local/theme.toml`` via ``theme.save_theme_values``). The theme loads
once at startup, so a saved change shows after a web GUI restart, and this
preview is the only place an unsaved colour can be seen.
"""
import re

from nicegui import ui

import config_schema as _cs
from pages import ui_kit as kit
from pages.options import theme
from pages.ui_guard import guard

# (group, editor kind, [(section, key), ...]). Every key of EDITABLE_SECTIONS
# appears exactly once - test_appearance pins it, so a new theme key cannot
# ship without a place on this screen.
GROUPS = [
    ("Surfaces", "color", [("palette", k) for k in (
        "page_bg1", "page_bg2", "page_bg3", "page_border", "card_bg", "card_border")]),
    ("Text", "color", [("palette", k) for k in ("title", "text", "muted", "icon")]),
    ("Fields", "color", [("palette", k) for k in (
        "input_bg", "input_border", "input_text", "focus")]),
    ("Buttons", "color", [("palette", k) for k in (
        "primary", "primary_hover", "btn_bg", "btn_hover", "btn_border", "danger")]),
    ("Status colours", "color", [("semantic", k) for k in (
        "positive", "warning", "negative", "neutral")]),
    ("Charts", "color", [("charts", k) for k in ("green", "red", "yellow", "flat", "cyan")]),
    ("Type", "text", [("typography", k) for k in (
        "family", "font_url", "numeric", "titles", "subtitles", "sections", "body", "small")]),
    ("Menu", "menu", [("menu", k) for k in (
        "accent", "header_bg", "drawer_bg", "text", "hover_bg", "title")]),
]
EDITABLE_SECTIONS = ("palette", "semantic", "charts", "typography", "menu")
_SIZE_KEYS = ("titles", "subtitles", "sections", "body", "small")
_SIZE_RE = re.compile(r"^\d+(\.\d+)?(px|rem|em)?$")


def size_error(value):
    """The message for a text size that is not a size, else ``None``. PURE."""
    v = str(value or "").strip()
    if not v:
        return "Enter a size, like 14"
    return None if _SIZE_RE.match(v) else "A number of pixels, like 14 or 14px"


def edited_theme(base, edits):
    """``base`` (a ``load_theme`` dict) with ``edits`` ``{(section, key): value}``
    laid over it - what the preview draws. PURE; ``base`` is not mutated."""
    out = {sec: dict(vals) for sec, vals in base.items()}
    for (sec, key), val in edits.items():
        if sec in out:
            out[sec][key] = val
    return out


def updates_from(edits):
    """``{section: {key: value}}`` for ``theme.save_theme_values``. PURE."""
    out = {}
    for (sec, key), val in edits.items():
        out.setdefault(sec, {})[key] = val
    return out


def render():
    """The Appearance tab: grouped editors left, the live preview right, a
    sticky Discard / Save footer, and the shared pending-restart banner."""
    from pages import config_editor      # its pending-restart set is the app's one
    P = theme.THEME["palette"]           # the editor itself wears the RUNNING theme
    state = {"base": theme.load_theme(), "edits": {}, "errors": {}}
    tiles, inputs = {}, {}

    def effective(sec, key):
        return state["edits"].get((sec, key), state["base"][sec][key])

    with kit.page():
        kit.header("Appearance")
        banner = ui.row().classes("w-full")
        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            editor = ui.column().classes("grow min-w-0 gap-4")
            with ui.column().classes("w-[380px] shrink-0 gap-2 sticky top-2"):
                ui.label("Preview — unsaved colours show here first").classes(theme.EYEBROW)
                preview = ui.column().classes("w-full gap-3")
        footer = ui.row().classes(
            f"w-full items-center gap-3 sticky bottom-0 z-10 bg-[{P['page_bg2']}] "
            f"border-t border-[{P['card_border']}] px-4 py-2 rounded-t-lg")

    # ── editing ────────────────────────────────────────────────────────────
    def _set(sec, key, value):
        if value == state["base"][sec][key]:
            state["edits"].pop((sec, key), None)
        else:
            state["edits"][(sec, key)] = value
        _paint_preview()
        _paint_footer()

    def _show_tile(sec, key):
        t = tiles[(sec, key)]
        val = effective(sec, key)
        cls = f"bg-[{val}]"                 # continuous value: runtime class, remove/add
        if cls != t["cls"]:
            t["swatch"].classes(remove=t["cls"], add=cls)
            t["cls"] = cls
        t["hex"].text = val

    @guard
    def _pick(sec, key, value):
        _set(sec, key, value)
        _show_tile(sec, key)

    def _tile(sec, key):
        val = effective(sec, key)
        with ui.column().classes(
                f"w-[118px] gap-0 cursor-pointer overflow-hidden rounded-[10px] "
                f"border border-[{P['card_border']}] bg-[{P['input_bg']}]"):
            swatch = ui.element("div").classes(f"w-full h-12 bg-[{val}]")
            with ui.column().classes("px-2 py-1 gap-0"):
                ui.label(theme.knob_label(key)).classes(
                    f"text-[11px] font-semibold leading-tight {theme.LABEL}")
                hex_lbl = ui.label(val).classes(f"text-[10px] {theme.MUTED}")
            ui.color_picker(on_pick=lambda e, s=sec, k=key: _pick(s, k, e.color))
        tiles[(sec, key)] = {"swatch": swatch, "hex": hex_lbl, "cls": f"bg-[{val}]"}

    def _text(sec, key, kind):
        placeholder = ("Leave empty for the stock look" if kind == "menu"
                       else "14" if key in _SIZE_KEYS else "")
        inp = kit.text_field(theme.knob_label(key), value=effective(sec, key),
                             placeholder=placeholder, width="w-full")

        @guard
        def _commit(_e=None, s=sec, k=key, el=inp):
            v = (el.value or "").strip()
            err = size_error(v) if (s == "typography" and k in _SIZE_KEYS) else None
            el.error = err
            if err:
                state["errors"][(s, k)] = err
                _paint_footer()
                return
            state["errors"].pop((s, k), None)
            _set(s, k, v)

        inp.on("blur", _commit)
        inp.on("keydown.enter", _commit)
        inputs[(sec, key)] = inp

    with editor:
        for label, kind, keys in GROUPS:
            with ui.column().classes(f"{theme.CARD} w-full gap-2"):
                ui.label(label).classes(f"text-subtitle2 font-semibold {theme.LABEL}")
                if kind == "color":
                    with ui.row().classes("gap-2 flex-wrap"):
                        for sec, key in keys:
                            _tile(sec, key)
                    continue
                if kind == "menu":
                    ui.label("Leave a field empty to keep the stock look.") \
                        .classes(f"text-xs {theme.MUTED}")
                with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-2"):
                    for sec, key in keys:
                        _text(sec, key, kind)

    # ── preview: the kit's pieces, drawn from the EDITED tokens ─────────────
    def _paint_preview():
        t = edited_theme(state["base"], state["edits"])
        tok = theme.build_tokens(t)
        p, ty, size = t["palette"], t["typography"], theme.normalize_size
        preview.clear()
        with preview:
            with ui.column().classes(
                    f"w-full gap-3 rounded-[12px] p-3 border border-[{p['page_border']}] "
                    f"bg-[{p['page_bg2']}] text-[{p['text']}]"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label("Strategy Finder").classes(
                        f"font-semibold text-[{p['title']}] text-[{size(ty['titles'])}]")
                    ui.space()
                    ui.label("Updated 10:42 AM CT").classes(
                        f"text-[{p['muted']}] text-[{size(ty['small'])}]")
                    kit.button("Refresh", kind="secondary", icon="refresh", tokens=tok)
                with ui.row().classes(f"{tok['CARD']} w-full items-end gap-3 flex-wrap"):
                    for label, value, focused in (("Symbol", "SPY", False),
                                                  ("Expiry", "Oct 17", True)):
                        with ui.column().classes("gap-1"):
                            ui.label(label).classes(tok["EYEBROW"])
                            edge = p["focus"] if focused else p["input_border"]
                            ui.label(value).classes(
                                f"min-w-[84px] rounded-[8px] px-[10px] py-[7px] "
                                f"bg-[{p['input_bg']}] border border-[{edge}] "
                                f"text-[{p['input_text']}]")
                    kit.button("Load", kind="primary", icon="search", tokens=tok)
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    for kind, text in (("primary", "Primary"), ("secondary", "Secondary"),
                                       ("danger", "Delete"), ("quiet", "Quiet")):
                        kit.button(text, kind=kind, tokens=tok)
                grid = "grid grid-cols-[1fr_1fr_1fr] w-full px-3 py-[6px]"
                with ui.column().classes(f"{tok['CARD']} w-full gap-0 p-0 overflow-hidden"):
                    with ui.element("div").classes(
                            f"{grid} bg-[{p['input_bg']}] text-[{p['icon']}] "
                            "text-[11px] font-semibold"):
                        for head in ("Symbol", "Strategy", "P&L"):
                            ui.label(head)
                    for sym, strat, pnl, key, selected in (
                            ("SPY", "Put credit", "+42.00", "TXT_POS", False),
                            ("NVDA", "Call credit", "-18.50", "TXT_NEG", True)):
                        mark = (f" bg-[{p['focus']}]/[.08] border-l-[3px] border-[{p['focus']}]"
                                if selected else "")
                        with ui.element("div").classes(f"{grid}{mark}"):
                            ui.label(sym)
                            ui.label(strat)
                            ui.label(pnl).classes(tok[key])
                with ui.row().classes("w-full gap-3"):
                    for text, key in (("Profit", "TXT_POS"), ("Warning", "TXT_WARN"),
                                      ("Loss", "TXT_NEG"), ("Neutral", "TXT_NEUTRAL")):
                        ui.label(text).classes(f"{tok[key]} font-semibold "
                                               f"text-[{size(ty['small'])}]")
                ui.label("Nothing to report yet").classes(
                    f"w-full text-center text-[13px] text-[{p['muted']}] py-1")

    # ── actions, footer, restart banner ─────────────────────────────────────
    def _repaint_values():
        for sec, key in tiles:
            _show_tile(sec, key)
        for (sec, key), el in inputs.items():
            el.value = effective(sec, key)
            el.error = None
        _paint_preview()
        _paint_footer()

    def _paint_banner():
        banner.clear()
        if _cs.WEBGUI not in config_editor._PENDING:
            return
        with banner:
            with kit.notice("Saved appearance is waiting for a web GUI restart.",
                            icon="restart_alt"):
                kit.button("Restart now", kind="primary", icon="restart_alt",
                           on_click=restart_dlg.open)

    def _mark_restart():
        config_editor._PENDING.add(_cs.WEBGUI)
        _paint_banner()

    @guard
    def _discard():
        state["edits"].clear()
        state["errors"].clear()
        _repaint_values()
        kit.toast("info", "Unsaved changes discarded.")

    @guard
    def _save():
        if state["errors"]:
            kit.toast("warn", "Fix the highlighted values first.")
            return
        state["base"] = theme.save_theme_values(updates_from(state["edits"]))
        state["edits"].clear()
        _repaint_values()
        _mark_restart()
        kit.toast("ok", "Saved. Restart the web GUI to see it on every screen.")

    def _reset():
        state["base"] = theme.reset_theme()
        state["edits"].clear()
        state["errors"].clear()
        _repaint_values()
        _mark_restart()
        kit.toast("ok", "Back to the shipped values. Restart the web GUI to see it.")

    def _restart():
        config_editor._PENDING.discard(_cs.WEBGUI)
        kit.toast("warn", "Restarting the web GUI. This page reloads in a few seconds.")
        config_editor._restart_webgui()

    reset_dlg = kit.confirm(
        "Reset every colour and font to the shipped values?",
        "Your saved appearance is removed. It shows after a web GUI restart.",
        confirm_text="Reset", danger=True, on_confirm=_reset)
    restart_dlg = kit.confirm(
        "Restart the web GUI?", "Every open page reloads in a few seconds.",
        confirm_text="Restart now", on_confirm=_restart)

    def _paint_footer():
        footer.clear()
        n, errs = len(state["edits"]), len(state["errors"])
        with footer:
            kit.button("Reset to shipped values", kind="danger", on_click=reset_dlg.open)
            if errs:
                text = f"{errs} value{'s' if errs != 1 else ''} to fix before saving"
                cls = theme.TXT_NEG
            elif n:
                text, cls = f"{n} unsaved change{'s' if n != 1 else ''}", theme.TXT_WARN
            else:
                text, cls = "All changes saved", theme.MUTED
            ui.label(text).classes(f"text-sm grow {cls}")
            discard = kit.button("Discard", kind="secondary", on_click=_discard)
            save = kit.button("Save changes", kind="primary", icon="save", on_click=_save)
            if not (n or errs):
                discard.disable()
            if not n or errs:
                save.disable()

    _paint_preview()
    _paint_footer()
    _paint_banner()
