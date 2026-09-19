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

# A note under a group whose change the preview CANNOT show. Data rather than a
# label test inside render(): the note belongs to the group, and a group that
# grows a preview drops its entry here. Charts colours are Highcharts option
# dicts and the menu is the shell around this page, so neither is on screen.
GROUP_NOTES = {
    "Charts": "Chart colours are not in the preview — they show after a web GUI restart.",
    "Menu": "The menu is not in the preview — it shows after a web GUI restart.",
}
# What a restart costs, on its own so the dialog and the warning that names the
# unsaved edits cannot drift apart.
RESTART_BODY = "Every open page reloads in a few seconds."
_SIZE_KEYS = ("titles", "subtitles", "sections", "body", "small")
_SIZE_RE = re.compile(r"^(\d+(\.\d+)?)(px|rem|em)?$")
# The size bounds. A 0 reaches `body{font-size:0px!important}` app-wide on the
# next restart, and the only way back is a blind Reset on an invisible page.
MIN_SIZE, MAX_SIZE = 8, 48
# These land RAW in app-wide CSS (build_typography_css / build_nav_css), so one
# of these characters ends the declaration and takes the whole stylesheet with
# it - again only visible after the restart that applies it.
_CSS_TEXT_KEYS = (("typography", "family"),) + tuple(("menu", k) for k in (
    "accent", "header_bg", "drawer_bg", "text", "hover_bg", "title"))
_CSS_BANNED = ("{", "}", ";", "<", "@import")
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def size_error(value):
    """The message for a text size that is not a usable size, else ``None``. PURE.

    The bound is on the NUMBER, whatever unit follows it: this field's
    convention is pixels (``theme.normalize_size`` reads a bare number as px),
    so ``1.1rem`` reads as 1.1 and is refused."""
    v = str(value or "").strip()
    if not v:
        return "Enter a size, like 14"
    m = _SIZE_RE.match(v)
    if not m:
        return "A number of pixels, like 14 or 14px"
    if not (MIN_SIZE <= float(m.group(1)) <= MAX_SIZE):
        return f"A size between {MIN_SIZE} and {MAX_SIZE} pixels"
    return None


def css_text_error(value):
    """The message for a free-text value that would break app-wide CSS. PURE.

    Empty is fine - every one of these knobs defaults to "" = the stock look."""
    v = str(value or "")
    bad = [c for c in _CSS_BANNED if c in v.lower()]
    if bad:
        return f"Remove {' '.join(bad)} — this text goes straight into the app's stylesheet"
    return None


def field_error(section, key, value):
    """The message for one edited field, else ``None``. PURE - the one place
    that says which check a key gets, so the editor and its tests agree."""
    if section == "typography" and key in _SIZE_KEYS:
        return size_error(value)
    if (section, key) in _CSS_TEXT_KEYS:
        return css_text_error(value)
    return None


def is_hex_color(value):
    """``#abc`` / ``#a1b2c3`` only. PURE.

    A colour becomes a Tailwind arbitrary class (``bg-[#101a30]``), and
    ``classes(remove=…)`` splits on whitespace - so an ``rgba(12, 34, 56, .5)``
    from a picker in another format would silently split into four classes and
    the swatch could never be repainted."""
    return bool(_HEX_RE.match(str(value or "").strip()))


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
        # Wraps below lg: at 380px fixed and no-wrap, the preview squeezed the
        # editor to nothing on a phone, and this app is used from one.
        with ui.row().classes("w-full items-start gap-4 flex-wrap lg:flex-nowrap"):
            editor = ui.column().classes("grow min-w-0 gap-4")
            with ui.column().classes("w-full lg:w-[380px] lg:shrink-0 gap-2 "
                                     "lg:sticky lg:top-2"):
                ui.label("Preview — unsaved colours show here first").classes(theme.EYEBROW)
                preview = ui.column().classes("w-full gap-3")
        footer = ui.row().classes(
            f"w-full items-center gap-3 sticky bottom-0 z-10 bg-[{P['page_bg2']}] "
            f"border-t border-[{P['card_border']}] px-4 py-2 rounded-t-lg")
        # Built ONCE and mutated (_paint_footer). Rebuilding them swallowed the
        # click that caused a field's blur: mousedown → blur → repaint → the
        # button the reader pressed no longer exists when mouseup lands.
        with footer:
            kit.button("Reset to shipped values", kind="danger",
                       on_click=lambda: reset_dlg.open())
            status = ui.label("").classes(f"text-sm grow {theme.MUTED}")
            discard = kit.button("Discard", kind="secondary",
                                 on_click=lambda: _discard())
            save = kit.button("Save changes", kind="primary", icon="save",
                              on_click=lambda: _save())

    # ── editing ────────────────────────────────────────────────────────────
    def _set(sec, key, value):
        if value == state["edits"].get((sec, key), state["base"][sec][key]):
            return                      # a tab-through must not repaint anything
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
        if not is_hex_color(value):
            kit.toast("warn", "Pick a colour like #1a2b3c — that one cannot be used.")
            return
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
            err = field_error(s, k, v)
            el.error = err
            if err:
                state["errors"][(s, k)] = err
                _paint_footer()
                return
            cleared = state["errors"].pop((s, k), None) is not None
            _set(s, k, v)
            if cleared:        # _set repaints only a CHANGE; the count moved too
                _paint_footer()

        inp.on("blur", _commit)
        inp.on("keydown.enter", _commit)
        inputs[(sec, key)] = inp

    with editor:
        for label, kind, keys in GROUPS:
            with ui.column().classes(f"{theme.CARD} w-full gap-2"):
                ui.label(label).classes(f"text-subtitle2 font-semibold {theme.LABEL}")
                if label in GROUP_NOTES:
                    ui.label(GROUP_NOTES[label]).classes(f"text-xs {theme.MUTED}")
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
            # The PAGE token, not a flat fill: the real ground is a three-stop
            # radial, so a flat one left page_bg1 and page_bg3 unpreviewable.
            # It already carries the border, the body text colour and the pad.
            with ui.column().classes(f"{tok['PAGE']} w-full gap-3"):
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
                           on_click=_open_restart)

    @guard
    def _open_restart():
        """Name what the restart costs. The banner can be up from the
        Configuration tab's own pending restart while edits are pending HERE, and
        a restart takes the page down with them."""
        n = len(state["edits"])
        restart_dlg.body.text = (
            f"{n} unsaved change{'s' if n != 1 else ''} will be lost. Save them "
            f"first if you want to keep them. {RESTART_BODY}" if n else RESTART_BODY)
        restart_dlg.open()

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
        try:
            state["base"] = theme.save_theme_values(updates_from(state["edits"]))
        except Exception as exc:  # noqa: BLE001 - shown; nothing half-written
            # write_overrides is atomic and round-trip-checks itself, so a raise
            # means nothing was written: keep the edits and say so, rather than
            # logging a traceback nobody sees under a footer that still counts
            # them as unsaved.
            kit.toast("error", f"Could not save the appearance: {exc}")
            return
        state["edits"].clear()
        _repaint_values()
        _mark_restart()
        kit.toast("ok", "Saved. Restart the web GUI to see it on every screen.")

    def _reset():
        try:
            state["base"] = theme.reset_theme()
        except Exception as exc:  # noqa: BLE001 - shown; the override still stands
            kit.toast("error", f"Could not reset the appearance: {exc}")
            return
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
        "Restart the web GUI?", RESTART_BODY,
        confirm_text="Restart now", on_confirm=_restart)

    _FOOTER_CLASSES = " ".join((theme.TXT_NEG, theme.TXT_WARN, theme.MUTED))

    def _paint_footer():
        """MUTATE the footer - never rebuild it. See the note where it is built."""
        n, errs = len(state["edits"]), len(state["errors"])
        if errs:
            text = f"{errs} value{'s' if errs != 1 else ''} to fix before saving"
            cls = theme.TXT_NEG
        elif n:
            text, cls = f"{n} unsaved change{'s' if n != 1 else ''}", theme.TXT_WARN
        else:
            text, cls = "All changes saved", theme.MUTED
        status.text = text
        # a finite class set, removed then added: the repo's reactive-colour rule
        status.classes(remove=_FOOTER_CLASSES, add=cls)
        discard.set_enabled(bool(n or errs))
        save.set_enabled(bool(n) and not errs)

    _paint_preview()
    _paint_footer()
    _paint_banner()
