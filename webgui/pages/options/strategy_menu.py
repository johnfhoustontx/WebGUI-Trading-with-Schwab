"""Shared cascading **Strategy** picker — a button that opens a nested Quasar
submenu (family → variant), e.g. Credit spread → Call / Put.

Drop-in for the old ``ui.select`` strategy control: exposes ``.value`` (read AND
write — assigning fires the change handlers, like a select) plus
``.on_value_change(handler)``. Both the Calculator and the Simulator mount it, so
the picker never drifts. The hierarchy + display labels come from
``strategies.STRATEGY_MENU`` / ``strategy_label`` (the single source of truth).
"""
from types import SimpleNamespace

from nicegui import ui

from . import strategies as S


class StrategyMenu:
    """Cascading Strategy picker with a ui.select-compatible interface."""

    def __init__(self, value="PCS", *, classes="", boxed=False):
        self._value = value
        self._handlers = []

        with ui.column().classes("gap-0 " + classes):
            ui.label("Strategy").classes("text-xs opacity-60")
            # ``boxed`` (Calculator): drop the Quasar outline + color so a page can
            # style the button like its input boxes (the outline forces a transparent
            # background that page CSS can't override). Default keeps the outline look.
            if boxed:
                self.button = ui.button(S.strategy_label(value), color=None) \
                    .props("no-caps icon-right=arrow_drop_down dense") \
                    .classes("w-full strategy-menu-btn")
            else:
                self.button = ui.button(S.strategy_label(value)) \
                    .props("no-caps outline icon-right=arrow_drop_down dense") \
                    .classes("w-full strategy-menu-btn")
            # The menu popups are teleported to <body> (outside any page wrapper), so
            # a page can't reach them with scoped CSS. In ``boxed`` mode we tag every
            # menu with ``strat-menu-navy`` for the page to theme globally.
            menu_cls = "strat-menu-navy" if boxed else ""
            with self.button:
                # Top-level menu opens under the button; each family row carries a
                # nested submenu that Quasar opens to the side on hover.
                self._menu = ui.menu().props('anchor="bottom left" self="top left"').classes(menu_cls)
                with self._menu:
                    for family, variants in S.STRATEGY_MENU:
                        with ui.item().props("clickable"):
                            with ui.item_section():
                                ui.label(family)
                            with ui.item_section().props("side"):
                                ui.icon("chevron_right").classes("opacity-60")
                            with ui.menu().props('anchor="top end" self="top start"').classes(menu_cls):
                                for vlabel, vcode in variants:
                                    ui.menu_item(
                                        vlabel, auto_close=False,
                                        on_click=lambda _e=None, c=vcode: self._select(c))

    # ── ui.select-compatible interface ───────────────────────────────────────
    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, code):
        self._select(code)

    def on_value_change(self, handler):
        self._handlers.append(handler)
        return self

    # ── internal ─────────────────────────────────────────────────────────────
    def _select(self, code):
        self._value = code
        self.button.text = S.strategy_label(code)
        try:
            self._menu.close()   # close the whole cascade after a leaf is chosen
        except Exception:
            pass
        for handler in self._handlers:
            try:
                handler(SimpleNamespace(value=code))
            except TypeError:
                handler()        # tolerate a no-arg handler


def build_strategy_menu(value="PCS", *, classes="", boxed=False):
    """Mount a cascading Strategy picker and return its ui.select-compatible handle.

    ``boxed=True`` renders an input-box-styled trigger (for the Calculator's dark
    theme); the default keeps the outline-button look."""
    return StrategyMenu(value, classes=classes, boxed=boxed)
