"""Settings page — GUI preferences (audio alerts, notifications).

Thin render(): each control writes through to app_settings. Extensible — add new
cards/sections here as more settings arrive.
"""
import app_settings
from nicegui import ui

from pages.options.theme import BTN_3D


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

    # Test sound uses the same shared audio element + helper as the live alert.
    def _test():
        from main import play_alert
        play_alert(app_settings.get("alert_sound"), app_settings.get("alert_volume"))
    test.on_click(_test)
