"""Paper Portfolio page — placeholder (implemented in Batch C)."""
from nicegui import ui


def render():
    ui.label("Paper Portfolio").classes("text-h5")
    ui.label("Paper account snapshot, open positions, and fills log.").classes("opacity-70")
    ui.label("(under construction — Batch C)").classes("text-sm opacity-50")
