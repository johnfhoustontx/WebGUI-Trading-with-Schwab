"""Gamma page — stub.

Full port of the Tk gamma tool (GEX / Charm / DEX heatmaps + forward-projection
band) lands in a later phase; see options-scanner/gamma_tool.py.
"""
from nicegui import ui


def render():
    ui.label("Gamma").classes("text-h5")
    ui.label("GEX / Charm / DEX heatmaps and forward-projection band.").classes("opacity-70")
    ui.label("Port of gamma_tool.py — coming in a later phase.").classes("text-sm opacity-50")
