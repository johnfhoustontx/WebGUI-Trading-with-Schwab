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
