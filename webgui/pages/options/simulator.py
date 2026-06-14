"""Simulator page — stub.

Full port of the options simulator (replay / what-if / IV-shock with Plotly)
lands in a later phase; see options-scanner/options_simulator/.
"""
from nicegui import ui


def render():
    ui.label("Simulator").classes("text-h5")
    ui.label("Replay, what-if, and IV-shock option simulation.").classes("opacity-70")
    ui.label("Port of options_simulator/ — coming in a later phase.").classes("text-sm opacity-50")
