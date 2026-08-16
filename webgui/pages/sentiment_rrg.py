"""RRG page — the Relative Rotation Graph vs SPY (under Market Trend & Sentiment).

Tier-3 reader: no engine calls, no app ``scoring`` import. The rotation
assessment is computed in ``services/sentiment_svc`` and cached; this page only
**formats** it. Cache view read:

* ``sentiment:rotation`` → ``{"assessment", "weights", "risk_threshold", "error"}``
  (see ``services/sentiment_svc/handlers.refresh_rotation``).

The RRG figure builder + headline helpers live in ``pages.sentiment_rotation``
(shared, unit-tested in ``tests/test_sentiment_rotation.py``). This page shows the
full-width RRG plus the Risk-ON/OFF headline for context, a Refresh button that
enqueues a ``cmd:sentiment`` ``refresh_rotation`` command, and a fetch-free
version-poll ``ui.timer``.
"""
import bus_client
from pages import busy as _busy
from pages.options.theme import BTN_3D
from pages.sentiment_rotation import (
    SENT_TEXT_CLASSES, headline_parts, regime_text_class, rrg_scatter_figure,
)
from pages.ui_guard import guard


def render():
    from nicegui import ui

    state = {"ver": None}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("RRG").classes("text-h6")
        ui.label("Relative Rotation Graph vs SPY").classes("opacity-60 text-sm")
        as_of = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        ui.button("Refresh", icon="refresh", color=None,
                  on_click=lambda: _request_refresh()).props("no-caps").classes(BTN_3D)

    headline_lbl = ui.label("").classes("text-subtitle1 text-bold")
    detail_lbl = ui.label("").classes("opacity-70 text-sm")
    msg_lbl = ui.label("").classes("text-warning text-sm")
    # The quadrant names render as faint corner labels INSIDE the chart
    # (quadrant_label_bands in pages.sentiment_rotation) — no caption needed.
    rrg_box = ui.column().classes("w-full q-mt-sm")

    def _render(a, weights, risk_threshold):
        regime, color, text, detail = headline_parts(a, risk_threshold)
        as_of.text = f"as of {a.get('date')}"
        headline_lbl.text = f"{regime} — {text}"
        headline_lbl.classes(remove=SENT_TEXT_CLASSES, add=regime_text_class(regime))
        detail_lbl.text = detail
        msg_lbl.text = ""
        rrg_box.clear()
        with rrg_box:
            # Hover-isolation is native (states.inactive in the figure) — no
            # client→server hover round-trip to wire.
            ui.highchart(rrg_scatter_figure(a)).classes("w-full")
    rrg_busy = _busy.build_busy(rrg_box, "Refreshing rotation…")


    @guard
    def _apply():
        rrg_busy.hide()
        rot = bus_client.read("sentiment:rotation") or {}
        a = rot.get("assessment")
        weights = rot.get("weights") or {}
        rt = rot.get("risk_threshold")
        err = rot.get("error")
        if a:
            _render(a, weights, rt)
        elif err:
            as_of.text = ""
            headline_lbl.text = ""
            detail_lbl.text = ""
            msg_lbl.text = err
            rrg_box.clear()
        else:
            as_of.text = ""
            headline_lbl.text = "Waiting for sentiment service…"
            detail_lbl.text = ""
            msg_lbl.text = ""
            rrg_box.clear()

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_rotation"})
        ui.notify("Refresh requested")
        rrg_busy.show()

    @guard
    def _maybe_repaint():
        ver = bus_client.read_version("sentiment:rotation")
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _apply()

    state["ver"] = bus_client.read_version("sentiment:rotation")
    _apply()
    ui.timer(2.0, _maybe_repaint)
