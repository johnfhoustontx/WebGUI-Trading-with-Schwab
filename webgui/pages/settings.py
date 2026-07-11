"""Settings page — GUI preferences (audio alerts, notifications, appearance).

Thin render(): each control writes through to app_settings; the Appearance
section writes through to config/theme.toml (``theme.save_theme_values``,
comment-preserving) and applies on a web-GUI restart — the theme loads once at
startup. Extensible — add new cards/sections here as more settings arrive.
"""
import app_settings
from nicegui import ui

from pages.options import theme
from pages.options.theme import BTN_3D, BTN_3D_DANGER

# Appearance section layout: (toml section, tab label, editor kind).
# "color" → clickable swatch tiles with a color picker; "text" → free-text
# (sizes / font family); "menu" → free-text colors where "" keeps the stock look.
_THEME_SECTIONS = [
    ("palette", "Surfaces", "color"),
    ("semantic", "State colors", "color"),
    ("buttons_3d", "3D buttons", "color"),
    ("gauge", "Gauges", "color"),
    ("charts", "Charts", "color"),
    ("typography", "Text", "text"),
    ("menu", "Menu", "menu"),
]


def render():
    ui.label("Settings").classes("text-h5")
    s = app_settings.load()

    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Scanner alerts").classes("text-subtitle1 font-bold")
        ui.label("Play a sound (and optional desktop notification) when new "
                 "scanner signals appear, on any page.").classes("opacity-70 text-sm")

        enable = ui.switch("Enable audio alert", value=s["alert_enabled"])
        enable.on_value_change(lambda e: app_settings.set("alert_enabled", e.value))

        with ui.row().classes("items-center gap-4"):
            sound = ui.select(["chime", "bell", "ping"], label="Sound",
                              value=s["alert_sound"]).classes("w-40")
            sound.on_value_change(lambda e: app_settings.set("alert_sound", e.value))
            test = ui.button("Test sound", icon="volume_up", color=None).props("no-caps").classes(BTN_3D)

        ui.label("Volume").classes("text-sm opacity-70")
        vol = ui.slider(min=0, max=1, step=0.05, value=s["alert_volume"]).classes("w-64")
        vol.on_value_change(lambda e: app_settings.set("alert_volume", e.value))

        mh = ui.switch("Only alert during market hours (08:00–15:00 CT, weekdays)",
                       value=s["alert_market_hours_only"])
        mh.on_value_change(lambda e: app_settings.set("alert_market_hours_only", e.value))

        with ui.row().classes("items-center gap-2"):
            ui.label("Minimum score to alert").classes("text-sm opacity-70")
            mscore = ui.number(value=s["alert_min_score"], min=0, max=100,
                               step=5).classes("w-28")
            mscore.on_value_change(lambda e: app_settings.set("alert_min_score", e.value or 0))

        ui.label("Tip: your browser blocks sound until you interact with the page — "
                 "clicking Test sound (or any nav link) unlocks it.").classes(
                 "opacity-60 text-xs")

    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Desktop notifications").classes("text-subtitle1 font-bold")
        notif = ui.switch("Show a desktop notification too",
                          value=s["desktop_notifications"])
        notif.on_value_change(lambda e: app_settings.set("desktop_notifications", e.value))
        ui.button("Grant notification permission", icon="notifications", color=None).props(
            "no-caps").classes(BTN_3D).on_click(
            lambda: ui.run_javascript("Notification && Notification.requestPermission()"))

    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Market summary ticker").classes("text-subtitle1 font-bold")
        ui.label("Scrolling market-summary marquee at the bottom of every page "
                 "(live data items + a periodic Claude verdict).").classes(
                 "opacity-70 text-sm")

        tick = ui.switch("Show the ticker", value=s["ticker_enabled"])
        tick.on_value_change(lambda e: app_settings.set("ticker_enabled", e.value))

        with ui.row().classes("items-center gap-2"):
            ui.label("Scroll speed").classes("text-sm opacity-70")
            # Labels map to a marquee duration in seconds (higher = slower).
            _SPEEDS = {"Slow": 90, "Medium": 60, "Fast": 35}
            _cur = {90: "Slow", 60: "Medium", 35: "Fast"}.get(s["ticker_speed"], "Medium")
            speed = ui.select(list(_SPEEDS), label="Speed", value=_cur).classes("w-40")
            speed.on_value_change(
                lambda e: app_settings.set("ticker_speed", _SPEEDS.get(e.value, 60)))

        ui.label("Changes apply on the next page load / navigation.").classes(
                 "opacity-60 text-xs")

    # ── Appearance — every configurable GUI component (config/theme.toml) ────
    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Appearance").classes("text-subtitle1 font-bold")
        ui.label("Colors, fonts and menu styling for the whole app — saved to "
                 "config/theme.toml. Changes apply after the web GUI restarts "
                 "(use Save & restart, then reload the page).").classes(
                 "opacity-70 text-sm")

        t = theme.load_theme()          # current file values merged over defaults
        inputs: dict = {}               # (section, key) -> text input element
        colors: dict = {(sec, k): v for sec, _l, kind in _THEME_SECTIONS
                        if kind == "color" for k, v in t[sec].items()}
        tile_refs: dict = {}            # (section, key) -> (swatch el, hex label)

        def _pick(sec, key, value):
            """Color picked on a tile: update state + repaint swatch/hex in place."""
            old = colors[(sec, key)]
            colors[(sec, key)] = value
            swatch, hex_lbl = tile_refs[(sec, key)]
            # continuous value → runtime arbitrary class, reset via remove/add
            swatch.classes(remove=f"bg-[{old}]", add=f"bg-[{value}]")
            hex_lbl.text = value

        def _tile(sec, key, val):
            """One compact swatch tile (color block + name + hex; click to pick)."""
            with ui.card().tight().classes(
                    "w-[118px] cursor-pointer bg-[#0c1424] border border-white/10"):
                swatch = ui.element("div").classes(f"w-full h-12 bg-[{val}]")
                with ui.column().classes("px-2 py-1 gap-0"):
                    ui.label(theme.knob_label(key)).classes(
                        "text-[11px] font-bold leading-tight")
                    hex_lbl = ui.label(val).classes("text-[10px] opacity-60")
                ui.color_picker(on_pick=lambda e, s=sec, k=key: _pick(s, k, e.color))
            tile_refs[(sec, key)] = (swatch, hex_lbl)

        with ui.tabs().classes("w-full") as tabs:
            tab_els = {sec: ui.tab(label) for sec, label, _k in _THEME_SECTIONS}
        with ui.tab_panels(tabs, value=tab_els["palette"]).classes("w-full"):
            for sec, label, kind in _THEME_SECTIONS:
                with ui.tab_panel(tab_els[sec]).classes("p-2"):
                    if kind == "color":
                        with ui.row().classes("gap-2 flex-wrap"):
                            for key, val in t[sec].items():
                                _tile(sec, key, val)
                    else:
                        if kind == "menu":
                            ui.label("Leave a field empty to keep the stock look "
                                     "(colors, e.g. #2e7d32).").classes(
                                     "opacity-60 text-xs")
                        else:
                            ui.label("Sizes are in pixels — just type a number "
                                     "(e.g. 16 or 16px); bigger number = bigger "
                                     "text.").classes("opacity-60 text-xs")
                        with ui.grid(columns=2).classes("w-full gap-x-4"):
                            for key, val in t[sec].items():
                                ph = ("default" if (kind == "menu" or key == "family")
                                      else "pixels, e.g. 14")
                                el = ui.input(label=theme.knob_label(key), value=val,
                                              placeholder=ph).classes("w-full")
                                inputs[(sec, key)] = el

        def _updates():
            out: dict = {}
            for sec, _label, kind in _THEME_SECTIONS:
                if kind == "color":
                    out[sec] = {k: colors[(sec, k)] for k in t[sec]}
                else:
                    out[sec] = {k: (inputs[(sec, k)].value or "").strip()
                                for k in t[sec] if (sec, k) in inputs}
            return out

        def _save(notify=True):
            theme.save_theme_values(_updates())
            if notify:
                ui.notify("Saved — restart the web GUI to apply (Save & restart, "
                          "or More → System Status)", type="positive")

        def _save_restart():
            _save(notify=False)
            from pages import status
            ui.notify("Saved — restarting the web GUI; reload this page in a few "
                      "seconds", type="warning")
            status._do_restart({"kind": "self"})

        def _reset():
            defaults = {sec: dict(vals) for sec, vals in theme._DEFAULTS.items()}
            theme.save_theme_values(defaults)
            for (sec, key), el in inputs.items():          # text inputs
                el.value = defaults[sec][key]
                el.update()
            for (sec, key) in list(colors):                # swatch tiles
                _pick(sec, key, defaults[sec][key])
            reset_dlg.close()
            ui.notify("Reset to defaults — restart the web GUI to apply",
                      type="positive")

        with ui.dialog() as reset_dlg, ui.card():
            ui.label("Reset every appearance setting to the built-in defaults?")
            with ui.row():
                ui.button("Reset", color=None).props("no-caps").classes(
                    BTN_3D_DANGER).on_click(_reset)
                ui.button("Cancel", color=None).props("no-caps").classes(
                    BTN_3D).on_click(reset_dlg.close)

        with ui.row().classes("items-center gap-3"):
            ui.button("Save", icon="save", color=None).props("no-caps").classes(
                BTN_3D).on_click(_save)
            ui.button("Save & restart web GUI", icon="restart_alt", color=None).props(
                "no-caps").classes(BTN_3D).on_click(_save_restart)
            ui.button("Reset to defaults", color=None).props("no-caps").classes(
                BTN_3D_DANGER).on_click(reset_dlg.open)

    # Test sound uses the same shared audio element + helper as the live alert.
    def _test():
        from main import play_alert
        play_alert(app_settings.get("alert_sound"), app_settings.get("alert_volume"))
    test.on_click(_test)
