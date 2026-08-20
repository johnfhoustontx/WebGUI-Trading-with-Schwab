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
from .theme import STRATEGY_BTN


class StrategyMenu:
    """Cascading Strategy picker with a ui.select-compatible interface."""

    def __init__(self, value="PCS", *, classes="", boxed=False,
                 menu_class=None, btn_class=None, caption=True):
        self._value = value
        self._handlers = []

        # ``btn_class`` / ``menu_class`` let a page substitute its OWN skin for
        # the two surfaces this widget owns, without forking it. Both default to
        # exactly what ``boxed`` produced before they existed, so every existing
        # call site is byte-identical; only an explicit value overrides.
        btn_cls = (STRATEGY_BTN if boxed else "") if btn_class is None else btn_class
        # The menu popups are teleported to <body> (outside any page wrapper), so
        # a page can't reach them with scoped CSS — the hook has to be put on the
        # menu here. A page-scoped CSS rule whose class nothing carries is dead,
        # and no test of the CSS STRING can see that.
        menu_cls = ("strat-menu-navy" if boxed else "") if menu_class is None else menu_class

        with ui.column().classes("gap-0 " + classes):
            # ``caption=False`` for a page that already names the control in its
            # own chrome (the Calculator's ① STRATEGY frame chip), where the
            # caption would say the word twice one line apart. Defaults to True,
            # so the Simulator and Rescue — which have no other label for this
            # control — are byte-identical.
            if caption:
                ui.label("Strategy").classes("text-xs opacity-60")
            # ``boxed`` (Calculator): drop the Quasar outline + color so a page can
            # style the button like its input boxes (the outline forces a transparent
            # background that page CSS can't override). Default keeps the outline look.
            if boxed:
                self.button = ui.button(S.strategy_label(value), color=None) \
                    .props("no-caps icon-right=arrow_drop_down dense") \
                    .classes(f"w-full strategy-menu-btn {btn_cls}".strip())
            else:
                self.button = ui.button(S.strategy_label(value)) \
                    .props("no-caps outline icon-right=arrow_drop_down dense") \
                    .classes(f"w-full strategy-menu-btn {btn_cls}".strip())
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


def build_strategy_menu(value="PCS", *, classes="", boxed=False,
                        menu_class=None, btn_class=None, caption=True):
    """Mount a cascading Strategy picker and return its ui.select-compatible handle.

    ``boxed=True`` renders an input-box-styled trigger (for the Calculator's dark
    theme); the default keeps the outline-button look.

    ``btn_class`` / ``menu_class`` override the trigger skin and the class put on
    every (body-teleported) popup — a page with its own palette passes both, e.g.
    ``btn_class=theme.CALC_STRATEGY_BTN, menu_class="strat-menu-calc"``. Leaving
    them ``None`` reproduces the ``boxed`` defaults exactly.

    ``caption=False`` drops the small "Strategy" label above the trigger, for a
    page whose own chrome already names the control."""
    return StrategyMenu(value, classes=classes, boxed=boxed,
                        menu_class=menu_class, btn_class=btn_class,
                        caption=caption)
