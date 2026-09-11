"""Simulator page (Tier-3 reader) — Replay, What-if price sweep + IV-shock.

This page holds **no engine call**: the ChainSnapshot fetch + both sweep engines
(``WhatIfEngine``/``IVShockEngine`` over the snapshot) live in
``services/options_svc/compute`` (``sim_fetch``/``sim_run``). The snapshot is a
Python object that can't be JSON-serialized whole, so it stays in-process in the
service; this page only ever sees the JSON-safe selector **meta** and the
computed **sweep rows**.

Interaction model:

* **Load chain** → enqueue ``sim_fetch``; a version-poll on
  ``options:sim_meta`` populates the **leg editor** (the shared multi-leg editor
  from ``pages.options.leg_editor``) — its per-leg expiry/strike selects pull
  from the cached meta.
* Picking a **strategy** (the dropdown) or any **leg edit** (add/remove/type/
  side/strike/expiry/qty) → enqueue both ``sim_run`` + ``sim_replay`` with the
  current legs (discrete, immediate); a version-poll on ``options:sim_result`` /
  ``options:sim_replay`` repaints the figures from the cached rows.
* A Δt / IV-mult **slider** change → enqueue ``sim_run``; the What-if ``dt`` is
  ELAPSED days from now (per-leg decay).
* The **ΔS** slider is purely a CLIENT-SIDE overlay line on the what-if chart
  (``target_s = spot*(1+ΔS/100)``) — it NEVER enqueues a command.

Sliders fire on every drag step, so ``sim_run`` is **debounced**: a slider change
only stashes the latest params; a short ``ui.timer`` flushes the most recent
params at most ~every 0.4 s. Strategy/leg edits enqueue immediately (they're
discrete). The pure figure builders (``whatif_figure``/``replay_figure`` +
``_records``/``_plotline``) are unit-tested. Charts render via Highcharts
(``ui.highchart``). The leg payload sent to the SIMULATOR commands uses ``kind``
(NOT ``option_type``) — the editor returns ``option_type``, so it is mapped.

**Readouts (2026-09-11).** Every number the page states — the six position
tiles, the What-if readout line, the Days-slider range and snaps, the structure
warnings, the IV-shock table, the Replay cursor line — comes from the PURE
``sim_view`` module. This file holds widgets and wiring only.
"""
import bus_client
import page_help as _page_help
from nicegui import ui

from pages.ui_guard import guard

from .inputs import select_all_on_focus, should_load
# Shared dark-navy "dashboard" theme (same CSS the Calculator injects, so the two
# pages never drift).
from .theme import (QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, BTN, BTN_PRIMARY, LABEL,
                    MUTED, TXT_POS, TXT_NEG, TXT_WARN)
from . import page_state as _ps

# Persisted (single-user) Simulator input snapshot — survives navigation + browser
# reload, resets on a webgui restart (same as the other persisting pages). The pure
# snapshot/merge/precedence helpers live in page_state.py.
_SIM_KEYS = ("symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab")
_SIM_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "dt": 5.0,
                 "mult": 1.5, "lookback": "auto", "ds": 0.0, "active_tab": "Replay"}
_LAST_SIM: dict = {}

SPOT_COLOR = "#ffd54f"
TARGET_COLOR = "#42a5f5"

# Position tiles (2026-09-11). Tone maps the finite {pos, neg, neutral} set onto
# fixed classes (the Tailwind-first rule: never a runtime-built colour); repaints
# swap them with ``.classes(remove=_TONE_REMOVE, add=…)`` so they never stack.
_TILE = "border border-[#213152] rounded-md bg-[#0c1426] px-3 py-2 min-w-0"
_TILE_VALUE = "text-lg font-semibold tabular-nums leading-tight"
_TONE_CLASS = {"pos": TXT_POS, "neg": TXT_NEG, "neutral": "text-[#eaf0fb]"}
_TONE_REMOVE = " ".join(_TONE_CLASS.values())

# Profit / loss payoff palette (What-if) — green profit zone above zero, red loss
# zone below, on the dark dashboard navy. The fills are vertical gradients
# (strongest at the extremes, fading toward the zero/breakeven line); the faint
# *_BAND colors wash the full profit/loss half-planes behind the curve.
PNL_GREEN = "#34d399"
PNL_RED = "#f87171"
_PNL_GREEN_FILL = {"linearGradient": {"x1": 0, "y1": 0, "x2": 0, "y2": 1},
                   "stops": [[0, "rgba(52,211,153,0.45)"], [1, "rgba(52,211,153,0.04)"]]}
_PNL_RED_FILL = {"linearGradient": {"x1": 0, "y1": 0, "x2": 0, "y2": 1},
                 "stops": [[0, "rgba(248,113,113,0.04)"], [1, "rgba(248,113,113,0.45)"]]}
_BAND_GREEN = "rgba(52,211,153,0.06)"
_BAND_RED = "rgba(248,113,113,0.06)"


def _records(df):
    """Normalize a DataFrame or list-of-dicts to a list of dict rows."""
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return list(df or [])


def _plotline(value, color, dash=None, width=2):
    """Highcharts plotLine dict (vertical on an xAxis, horizontal on a yAxis)."""
    pl = {"value": value, "color": color, "width": width, "zIndex": 3}
    if dash:
        pl["dashStyle"] = dash
    return pl


_DARK_AXIS = {"labels": {"style": {"color": "#bdbdbd"}},
              "gridLineColor": "rgba(255,255,255,0.06)",
              "lineColor": "rgba(255,255,255,0.15)"}


def whatif_pnl(df, spot, baseline=None):
    """``[S, P/L]`` pairs where ``P/L = theo_price(S) − baseline``.

    The What-if rows carry the position's signed theo *value* in DOLLARS (the ×100
    contract multiplier is applied by the service). When ``baseline`` is given (the
    service's ``whatif_baseline`` — the position's value at the current spot AND
    current time = the entry mark), the curve is **profit / loss from entry**: it
    matches the Calculator's ``entry_credit + value(S,t)`` (profit caps at the net
    credit, loss floors at width−credit), splitting cleanly into a green profit zone
    above zero and a red loss zone below.

    With no ``baseline`` it falls back to the legacy "zero at spot" subtraction (the
    row nearest spot) — kept for back-compat and pre-update cached results."""
    rows = _records(df)
    if not rows:
        return []
    if baseline is None:
        baseline = (min(rows, key=lambda r: abs(r["S"] - spot)).get("theo_price", 0)
                    if spot is not None else 0)
    return [[r["S"], r.get("theo_price", 0) - baseline] for r in rows]


def whatif_figure(df, spot, target_s=None, baseline=None):
    """Profit/loss payoff: underlying price (S) vs position **P/L** in dollars
    (theo value relative to the entry ``baseline`` — see ``whatif_pnl``).

    Styled to match the dashboard payoff mock: an **area** with Highcharts y-zones
    at the ``0`` threshold paints a green profit fill above zero and a red loss fill
    below (the line itself switches green↔red at the breakevens); faint Profit/Loss
    background bands wash each half-plane; the gold **spot** + blue dashed **ΔS
    target** verticals and the solid zero/breakeven line are kept."""
    data = whatif_pnl(df, spot, baseline)
    xplotlines = [_plotline(spot, SPOT_COLOR)]
    if target_s is not None:
        xplotlines.append(_plotline(target_s, TARGET_COLOR, dash="Dash"))
    yplotlines = [_plotline(0, "rgba(255,255,255,0.35)", width=1)]  # zero / breakeven
    # Full-height profit (green) / loss (red) washes behind the curve, labelled on
    # the right like the mock. The ±1e7 extents clip to the visible y-axis.
    yplotbands = [
        {"from": 0, "to": 1e7, "color": _BAND_GREEN,
         "label": {"text": "Profit", "align": "right", "verticalAlign": "top",
                   "x": -8, "y": 18, "style": {"color": PNL_GREEN, "fontWeight": "600"}}},
        {"from": -1e7, "to": 0, "color": _BAND_RED,
         "label": {"text": "Loss", "align": "right", "verticalAlign": "bottom",
                   "x": -8, "y": -8, "style": {"color": PNL_RED, "fontWeight": "600"}}},
    ]
    return {
        # Explicit height: this chart mounts inside an inactive tab panel, and
        # NiceGUI's highchart only reflows once at mount (no ResizeObserver). Without
        # a fixed height it measures the hidden 0-height container and collapses to
        # title-height when the tab is shown.
        "chart": {"type": "area", "backgroundColor": "transparent", "height": 420},
        "title": {"text": "What-if: profit / loss", "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {**_DARK_AXIS, "title": {"text": "Underlying price"}, "plotLines": xplotlines},
        "yAxis": {**_DARK_AXIS, "title": {"text": "Profit / loss"}, "plotLines": yplotlines,
                  "plotBands": yplotbands},
        "tooltip": {"pointFormat": "Price {point.x:g}: profit / loss <b>${point.y:,.0f}</b>"},
        # Smooth transition when the chart is updated in place on a slider change.
        "plotOptions": {"series": {"animation": {"duration": 500}}},
        # Area filled to the 0 threshold: the part above zero is green (profit), the
        # part below red (loss) — both line AND fill — split at the breakeven
        # crossings via color/negativeColor. Setting an explicit base ``color`` +
        # ``fillColor`` stops Highcharts painting a default-blue base path under the
        # zones.
        "series": [{"name": "Profit / loss", "type": "area", "data": data,
                    "threshold": 0, "lineWidth": 2, "marker": {"enabled": False},
                    "color": PNL_GREEN, "fillColor": _PNL_GREEN_FILL,
                    "negativeColor": PNL_RED, "negativeFillColor": _PNL_RED_FILL}],
    }


# Replay panels, top to bottom: the underlying, the POSITION's own P/L (the
# question the tab exists to answer), then four Greeks in position units. Rho is
# left out of the panels (it stays in the payload): it matters least at these
# tenors, and a seventh panel in the same height crowded all six.
_REPLAY_PANELS = [("price", "Price"), ("pnl", "Profit / loss"), ("delta", "Delta"),
                  ("gamma", "Gamma"), ("theta", "Theta per day"), ("vega", "Vega")]
CURSOR_COLOR = "#ef5350"
PRICE_COLOR = "#66bb6a"
GREEK_COLOR = "#42a5f5"

# Replay look-back override menu (key → label). "auto" lets the service pick the
# window from the selected contract's DTE; the rest force a fixed window.
REPLAY_LOOKBACKS = [
    ("auto", "Auto (by DTE)"),
    ("1m_1d", "1-minute bars, 1 day"),
    ("5m_3d", "5-minute bars, 3 days"),
    ("5m_5d", "5-minute bars, 5 days"),
    ("15m_10d", "15-minute bars, 10 days"),
    ("1d_20d", "Daily bars, 20 days"),
]


def lookback_options():
    """{key: label} dict for the Replay look-back ui.select."""
    return {key: label for key, label in REPLAY_LOOKBACKS}


def replay_figure(trace, cursor=None):
    """Stacked price + position P/L + 4-Greek replay chart over an integer
    (gap-compressed) x.

    One Highcharts element with six stacked yAxes sharing the integer x-axis
    (overnight/weekend breaks already collapsed by ``compute.sim_replay``). The
    axis carries one CATEGORY per bar — its real time — so the tooltip header and
    the tick labels (on session starts) read as dates, not bar numbers. A
    category axis still takes numeric plotLines, so session boundaries render as
    dashed lines and ``cursor`` (an int x-index) as the scrub line; categories
    also sidestep the datetime-crosshair epoch-ms gotcha. The P/L panel is the
    What-if payoff's green-above / red-below area. Greeks arrive in position
    units; a pre-upgrade per-share trace is scaled by
    ``sim_view.replay_in_position_units``. Returns an empty-but-valid chart when
    ``trace`` is missing."""
    from . import sim_view
    trace = sim_view.replay_in_position_units(trace)
    x = trace.get("x") or []
    greeks = trace.get("greeks") or {}
    sessions = trace.get("sessions") or []

    n = len(_REPLAY_PANELS)
    gap = 3                                  # % vertical gap between panels
    h = (100 - gap * (n - 1)) / n
    yaxes, series = [], []
    for i, (panel, title) in enumerate(_REPLAY_PANELS):
        top = i * (h + gap)
        yaxis = {**_DARK_AXIS,
                 "title": {"text": title, "style": {"color": "#bdbdbd"}},
                 "top": f"{top}%", "height": f"{h}%", "offset": 0, "lineWidth": 1}
        if panel == "price":
            col = trace.get("prices") or []
        elif panel == "pnl":
            col = trace.get("pnl") or []
            yaxis["plotLines"] = [_plotline(0, "rgba(255,255,255,0.35)", width=1)]
        else:
            col = greeks.get(panel) or []
        yaxes.append(yaxis)
        data = [[xi, v] for xi, v in zip(x, col)]
        if panel == "pnl":
            series.append({"name": title, "type": "area", "yAxis": i, "data": data,
                           "threshold": 0, "lineWidth": 1.5, "marker": {"enabled": False},
                           "color": PNL_GREEN, "fillColor": _PNL_GREEN_FILL,
                           "negativeColor": PNL_RED, "negativeFillColor": _PNL_RED_FILL})
        else:
            series.append({"name": title, "type": "line", "yAxis": i, "data": data,
                           "color": PRICE_COLOR if panel == "price" else GREEK_COLOR,
                           "marker": {"enabled": False}})

    # Dashed session boundaries (skip the first session's start) + scrub cursor.
    xplotlines = [_plotline(s["start"] - 0.5, "#777777", dash="Dot", width=1)
                  for s in sessions[1:]]
    if cursor is not None:
        xplotlines.append(_plotline(cursor, CURSOR_COLOR, width=1))

    return {
        "chart": {"height": 640, "backgroundColor": "transparent"},
        "title": {"text": f"Replay — {trace.get('resolution', '')}".rstrip(" —"),
                  "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {**_DARK_AXIS, "plotLines": xplotlines,
                  "categories": sim_view.replay_categories(trace.get("timestamps")),
                  "tickPositions": sim_view.replay_tick_positions(trace),
                  "labels": {"style": {"color": "#bdbdbd"}}},
        "yAxis": yaxes,
        # valueDecimals caps the hover readout at 2dp (raw float precision is noise).
        "tooltip": {"shared": True, "valueDecimals": 2},
        "plotOptions": {"series": {"animation": False}},
        "series": series,
    }


def render():
    """Simulator page: chain load + strategy/leg editor + position tiles +
    Replay / What-if / IV-shock tabs."""
    from . import handoff
    from . import leg_editor
    from . import strategy_menu
    from . import overlay as _overlay
    from . import sim_view as sv

    ui.add_css(QUASAR_INTERNAL_CSS)

    # Full-screen wait overlay shown while a user-initiated chain load is in flight.
    wait = _overlay.build_loading_overlay()

    # Page state (local closure, not module globals — built per request).
    state: dict = {
        "meta": None,        # last sim_meta payload (selector source)
        "result": None,      # last sim_result payload (sweep rows)
        "meta_ver": None,    # last-seen sim_meta cache version
        "result_ver": None,  # last-seen sim_result cache version
        "pending": None,     # latest sim_run params awaiting the debounce flush
        "replay": None,      # last sim_replay payload (price/Greek trace)
        "replay_ver": None,  # last-seen sim_replay cache version
        "restoring": False,  # True while restoring a persisted snapshot (suppress enqueues)
        "last_loaded": None,   # last symbol a load was triggered for (tab/Enter dedup)
        "loading": False,      # True while a user-initiated load is in flight (overlay up)
        "days_key": None,      # last Days-slider range applied (skip no-op rebuilds)
        "scrub_sync": False,   # True while the page itself moves the replay scrubber
    }

    # Replay / What-if / IV-shock as SUBTABS directly under the main tab strip
    # (2026-07-11 — like Gamma/Scanner): rendered into shell.subtab_slot(), folder-
    # styled by .compact-subtabs, so the view tabs sit as high as possible. Falls
    # back inline if the slot is absent. Same value/on_value_change API as before.
    import shell as _shell
    _slot = _shell.subtab_slot()

    def _build_sim_tabs():
        with ui.tabs().classes("compact-subtabs").props(
                "dense no-caps inline-label align=left") as t:
            t_replay = ui.tab("Replay")
            t_whatif = ui.tab("What-if")
            t_ivshock = ui.tab("IV shock")
            for _tab, _key in ((t_replay, "Replay"), (t_whatif, "What-if"),
                               (t_ivshock, "IV shock")):
                with _tab:
                    ui.tooltip(_page_help.subtab_help("/options/simulator", _key)
                               ).props("delay=350 max-width=340px")
        return t, t_replay, t_whatif, t_ivshock

    if _slot is not None:
        with _slot:
            tabs, tab_replay, tab_whatif, tab_ivshock = _build_sim_tabs()
    else:
        tabs, tab_replay, tab_whatif, tab_ivshock = _build_sim_tabs()
    _shell.bind_breadcrumb_leaf(tabs, initial="Replay")   # default set on tab_panels

    tile_refs = {}      # tile key -> (label, value, sub) labels, built once
    shock_cells = []    # one (base, shock, change) label triple per IV-shock row

    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-3"):
        # No page title — the tab strip names the page (2026-07-11 cleanup).

        # One card, three columns (2026-09-11): the symbol controls; the strategy
        # and its legs; and the POSITION tiles, which fill the width the legs
        # (capped at 440px) used to leave empty — so the readouts cost no height.
        # flex-wrap drops the tiles under the legs on a narrow screen.
        with ui.column().classes(f"{CARD} w-full gap-2"):
            with ui.row().classes("w-full gap-6 items-start flex-wrap"):
                with ui.column().classes("gap-3 shrink-0"):
                    symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-40"))
                    fetch_btn = ui.button("Load chain", icon="download", color=None) \
                        .props("no-caps").classes(BTN_PRIMARY)
                    ui.button("Copy to Calculator", icon="calculate", color=None,
                              on_click=lambda: handoff.send_to_calculator_legs(
                                  leg_editor.legs_to_payload(
                                      (state.get("meta") or {}).get("symbol")
                                      or symbol_in.value or "",
                                      editor.get_legs(), keep_premium=False))) \
                        .props("no-caps").classes(BTN)
                    status = ui.label("Load a symbol to begin.").classes(EYEBROW)
                # Strategy + legs. ``show_premium=False`` — the simulator prices
                # each leg from the chain's IV, so no manual premium input.
                with ui.column().classes("gap-2 w-[440px] max-w-full shrink-0"):
                    with ui.row().classes("items-end gap-2 w-full no-wrap"):
                        strategy_sel = strategy_menu.build_strategy_menu(
                            value="PCS", classes="w-52", boxed=True)
                        # dense, so the box sits level with the boxed strategy trigger
                        expiry_all = ui.select([], label="Set all legs to") \
                            .props("dense options-dense").classes("w-40 sim-expiry-all")
                    edited_chip = ui.label(
                        "Edited: these legs no longer match the strategy's shape") \
                        .classes(f"sim-edited {TXT_WARN} text-xs border border-[#5a4a1f] "
                                 "rounded px-2 py-0.5 self-start")
                    edited_chip.set_visibility(False)
                    warn_box = ui.column().classes("gap-1 w-full")
                    legs_box = ui.column().classes("gap-2 w-full")
                with ui.column().classes("gap-2 flex-grow min-w-[280px]"):
                    ui.label("Position").classes(EYEBROW)
                    with ui.element("div").classes("grid grid-cols-2 2xl:grid-cols-3 gap-2 w-full"):
                        for t in sv.position_tiles([], None):
                            with ui.column().classes(f"sim-tile {_TILE} gap-0.5"):
                                t_lbl = ui.label(t["label"]).classes(EYEBROW)
                                t_val = ui.label(t["value"]).classes(
                                    f"{_TILE_VALUE} {_TONE_CLASS['neutral']}")
                                t_sub = ui.label(t["sub"]).classes(f"{MUTED} text-xs")
                            tile_refs[t["key"]] = (t_lbl, t_val, t_sub)

        # Chart card: the panels follow the subtabs mounted under the main strip.
        with ui.column().classes(f"{CARD} w-full gap-2"):
            with ui.tab_panels(tabs, value=tab_replay).classes("w-full flush-panels"):
                with ui.tab_panel(tab_replay):
                    with ui.row().classes("items-center gap-4 w-full"):
                        lookback_sel = ui.select(lookback_options(), value="auto",
                                                 label="Look-back").classes("w-44")
                        scrub_slider = ui.slider(min=0, max=1, value=0).classes("w-80 sim-scrub")
                        scrub_lbl = ui.label("Drag the slider to step through time")
                    # Persistent chart built ONCE (present at first render for the ESM
                    # import map) and updated in place. Empty-state label toggled until
                    # the first replay trace arrives.
                    replay_empty = ui.label("").classes("opacity-70")
                    replay_chart = ui.highchart(replay_figure({}, None)).classes("w-full")
                with ui.tab_panel(tab_whatif):
                    with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                        ds_lbl = ui.label("Price change: 0%").tooltip(
                            "Move the stock or index price up or down by this percent")
                        ds_slider = ui.slider(min=-20, max=20, value=0).classes("w-48")
                        dt_lbl = ui.label("Time passed: 5 days").tooltip(
                            "Calendar time forward from now — each leg loses that much time value")
                        dt_slider = ui.slider(min=0, max=30, value=5).classes("w-48 sim-days")
                        snap_row = ui.row().classes("items-center gap-1 sim-snaps")
                    readout_lbl = ui.label("").classes(
                        f"sim-readout text-subtitle1 font-semibold {_TONE_CLASS['neutral']}")
                    # Persistent charts built ONCE (present at first render for the ESM
                    # import map) and updated in place so slider changes ANIMATE instead
                    # of flickering through a clear()/recreate. Empty-state label toggled
                    # alongside until the first sweep result arrives.
                    whatif_empty = ui.label("").classes("opacity-70")
                    whatif_chart = ui.highchart(whatif_figure([], 0)).classes("w-full")
                with ui.tab_panel(tab_ivshock):
                    with ui.row().classes("items-center gap-4 w-full"):
                        mult_lbl = ui.label("Volatility multiplier: 1.5").tooltip(
                            "Multiplies each leg's implied volatility — 1.5 means 50% higher")
                        mult_slider = ui.slider(min=0.5, max=3.0, step=0.1, value=1.5).classes("w-64")
                    # A table, not a chart (2026-09-11): the old column chart put
                    # dollars and Greeks on one axis, so four of five categories
                    # drew as flat lines. Each row now reads at its own scale.
                    ivshock_head = ui.label("").classes(
                        f"sim-shock-head text-subtitle1 font-semibold {_TONE_CLASS['neutral']}")
                    ivshock_empty = ui.label("").classes("opacity-70")
                    with ui.element("div").classes(
                            "sim-shock-grid grid grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,1fr))] "
                            "gap-x-6 gap-y-2 w-full max-w-[760px] items-baseline") as ivshock_grid:
                        ui.label("")
                        ui.label("Today's volatility").classes(f"{EYEBROW} text-right")
                        shock_hdr = ui.label("Volatility times 1.5").classes(f"{EYEBROW} text-right")
                        ui.label("Change").classes(f"{EYEBROW} text-right")
                        for row_label in sv.SHOCK_ROW_LABELS:
                            ui.label(row_label).classes(LABEL)
                            cells = tuple(
                                ui.label(sv.NO_READING).classes(
                                    f"text-right tabular-nums {_TONE_CLASS['neutral']}")
                                for _ in range(3))
                            shock_cells.append(cells)

    # ── leg editor mounted with meta-backed option sources ───────────────────
    # Strikes/expiries come from the cached sim_meta snapshot (``strikes`` is a
    # nested {expiry: {call:[...], put:[...]}} map). When no expiry is set yet we
    # union strikes across expiries (used by apply_template before a per-leg expiry
    # is chosen).
    def _strikes_for(expiry, otype):
        sm = ((state.get("meta") or {}).get("strikes") or {})
        if expiry:
            return (sm.get(str(expiry)) or {}).get(otype) or []
        out = set()
        for e in (state.get("meta") or {}).get("expiries") or []:
            out.update((sm.get(str(e)) or {}).get(otype) or [])
        return sorted(out)

    def _expiries_for():
        return (state.get("meta") or {}).get("expiries") or []

    # ``layout="card"`` — the shared two-line leg card, in the app-wide dark navy:
    # NO ``tokens``, so the near-black CALC_* language stays the Calculator's alone.
    # ``header`` is dropped with it (the card carries its own eyebrow captions), and
    # no ``delta_for`` is passed — ``sim_meta`` is spot/expiries/strikes with no
    # greeks, so DELTA reads an em-dash rather than a made-up 0.00.
    # ``min_legs`` stays at the default 1: a zero-leg simulator enqueues nothing
    # (``_current_params`` returns None) and silently freezes the charts on the
    # previous sweep, so the last ✕ is better locked than live. ``on_reset`` stays
    # None — the strategy picker already re-seeds the template on every pick.
    editor = leg_editor.build_leg_editor(
        legs_box, strikes_for=_strikes_for, expiries_for=_expiries_for,
        show_premium=False,
        on_change=lambda: (_on_legs_changed(), _capture(), _legs_ui()),
        layout="card",
        spot_getter=lambda: (state.get("meta") or {}).get("spot") or 0)

    # Seed the default template (PCS) so a cold page shows the strategy's legs
    # immediately. Tolerates empty strikes/expiries pre-load; strikes snap to the
    # real ladder once a chain arrives (see ``_apply_meta``). ``apply_template`` →
    # ``set_legs`` does NOT fire ``on_change``, so no premature command enqueues.
    editor.apply_template(strategy_sel.value)

    # ── persist + restore full UI state across navigation (single-user) ───────
    def _capture():
        """Snapshot the current inputs into the module-level _LAST_SIM (cheap dict
        write; wired to every input change). No-op while restoring."""
        if state.get("restoring"):
            return
        _LAST_SIM.clear()
        _LAST_SIM.update(_ps.snapshot({
            "symbol": (symbol_in.value or "").strip().upper(),
            "strategy": strategy_sel.value,
            "legs": editor.get_legs(),
            "dt": float(dt_slider.value), "mult": float(mult_slider.value),
            "lookback": lookback_sel.value, "ds": float(ds_slider.value),
            "active_tab": tabs.value,
        }, _SIM_KEYS))

    def _restore(snap):
        """Apply a persisted snapshot to the widgets under the restoring guard
        (so wiring fires no stray commands). Legs ride ``pending_legs`` so the
        post-load ``_apply_meta`` applies them (beating the template re-seed) and
        runs with the restored sliders."""
        s = _ps.merge_restore(snap, _SIM_DEFAULTS)
        state["restoring"] = True
        try:
            symbol_in.value = s["symbol"]
            strategy_sel.value = s["strategy"]
            dt_slider.value = s["dt"]
            mult_slider.value = s["mult"]
            lookback_sel.value = s["lookback"]
            ds_slider.value = s["ds"]
            tabs.value = s["active_tab"]
            state["pending_legs"] = s["legs"] or None
        finally:
            state["restoring"] = False

    # ── the readouts: tiles, structure line, Days range ──────────────────────
    def _set_tone(el, tone):
        el.classes(remove=_TONE_REMOVE, add=_TONE_CLASS.get(tone, _TONE_CLASS["neutral"]))

    def _paint_tiles():
        """The six position tiles. The result counts only if it was priced for the
        legs on screen (``sim_run`` echoes them); otherwise the tiles say they are
        waiting rather than pair new legs with the previous legs' price."""
        res = state.get("result")
        ok = sv.result_matches(res, _sym(), _legs_payload())
        for t in sv.position_tiles(editor.get_legs(), res if ok else None):
            lbl, val, sub = tile_refs[t["key"]]
            lbl.text, val.text, sub.text = t["label"], t["value"], t["sub"]
            _set_tone(val, t["tone"])

    def _paint_structure():
        """The Edited chip, the structure warnings and the Set-all-legs options."""
        legs = editor.get_legs()
        edited_chip.set_visibility(not sv.matches_template(strategy_sel.value, legs))
        warn_box.clear()
        with warn_box:
            for w in sv.structure_warnings(legs):
                with ui.row().classes("items-start gap-1 no-wrap sim-warning"):
                    ui.icon("warning").classes(f"{TXT_WARN} text-base")
                    ui.label(w).classes(f"{TXT_WARN} text-xs")
        exps = _expiries_for()
        if list(expiry_all.options or []) != list(exps):
            expiry_all.set_options(list(exps))

    def _apply_days_range():
        """Fit the Days slider to the legs: its max is the longest leg's time to its
        close, its step a quarter day near expiry, plus Now / Halfway / Expiry
        snaps. ``ui.slider`` keeps min/max/step in ``_props`` (a plain attribute
        assignment never reaches the browser), hence the direct writes."""
        r = sv.days_range(editor.get_legs())
        key = (r["max"], r["step"], tuple(r["snaps"]))
        if key == state.get("days_key"):
            return
        state["days_key"] = key
        dt_slider._props["max"] = r["max"]
        dt_slider._props["step"] = r["step"]
        dt_slider.update()
        if float(dt_slider.value or 0) > r["max"]:
            dt_slider.value = r["max"]
        snap_row.clear()
        with snap_row:
            for label, days in r["snaps"]:
                ui.button(label, color=None, on_click=lambda e, d=days: _snap_days(d)) \
                    .props("flat dense no-caps").classes(f"{BTN} px-2 min-h-0 text-xs")

    @guard
    def _snap_days(days):
        dt_slider.value = days      # fires on_value_change → debounced sim_run

    def _legs_ui():
        """Everything that reads the legs and not the result."""
        _paint_structure()
        _apply_days_range()
        _paint_tiles()
        _paint_empty_states()

    # ── render figures from the cached sweep result ──────────────────────────
    def _paint_empty_states():
        text = sv.empty_state_text(state.get("meta"), editor.get_legs())
        whatif_empty.text = text
        ivshock_empty.text = text
        tr = state.get("replay") or {}
        replay_empty.text = tr.get("error") or text

    def _render_ivshock(result):
        mult = float(mult_slider.value)
        mult_lbl.text = f"Volatility multiplier: {mult:g}"
        priced = (result or {}).get("mult")
        shown = priced if isinstance(priced, (int, float)) else mult
        table = sv.ivshock_table((result or {}).get("ivshock"), shown)
        has = bool(table["rows"])
        ivshock_empty.set_visibility(not has)
        ivshock_grid.set_visibility(has)
        ivshock_head.set_visibility(has)
        if not has:
            return
        shock_hdr.text = f"Volatility times {shown:g}"
        ivshock_head.text = table["headline"]
        _set_tone(ivshock_head, table["tone"])
        for (b, s, c), row in zip(shock_cells, table["rows"]):
            b.text, s.text, c.text = row["base"], row["shock"], row["change"]
            _set_tone(c, row["tone"])

    def _render_figures():
        ds_lbl.text = f"Price change: {ds_slider.value:+g}%"
        dt_lbl.text = f"Time passed: {sv.days_text(dt_slider.value)}"
        _paint_empty_states()

        result = state["result"]
        _render_ivshock(result)
        if not result:
            whatif_empty.set_visibility(True)
            whatif_chart.set_visibility(False)
            readout_lbl.text = ""
            return
        whatif_empty.set_visibility(False)
        whatif_chart.set_visibility(True)

        spot = result.get("spot")
        # ΔS is a CLIENT-SIDE overlay line only — no command, computed here.
        target_s = spot * (1 + ds_slider.value / 100.0) if spot is not None else None
        rows = result.get("whatif_rows") or []
        baseline = result.get("whatif_baseline")
        # Update in place so Highcharts animates the transition (no clear/recreate).
        whatif_chart.options = whatif_figure(rows, spot, target_s, baseline=baseline)
        whatif_chart.update()
        # The readout reads the curve the chart just drew, at the result's OWN Δt
        # when it carries one — the slider may be mid-drag ahead of the pricing.
        priced_dt = result.get("dt")
        days = priced_dt if isinstance(priced_dt, (int, float)) else dt_slider.value
        text, tone = sv.whatif_readout(whatif_pnl(rows, spot, baseline), target_s, days,
                                       legs=editor.get_legs())
        readout_lbl.text = text
        _set_tone(readout_lbl, tone)

    # ── render the replay trace + client-side scrub cursor ───────────────────
    def _sync_scrubber():
        """A NEW trace resizes the scrubber and parks it on the latest bar.

        ``ui.slider`` keeps ``max`` in ``_props``: the old ``scrub_slider.max = …``
        set a plain Python attribute the browser never saw, so the cursor could
        only ever reach bars 0 and 1."""
        tr = state.get("replay") or {}
        n = len(tr.get("x") or [])
        scrub_slider._props["max"] = max(n - 1, 1)
        state["scrub_sync"] = True
        try:
            scrub_slider.value = max(n - 1, 0)
        finally:
            state["scrub_sync"] = False
        scrub_slider.update()

    def _render_replay():
        tr = state["replay"]
        if not tr or tr.get("error") or not tr.get("x"):
            replay_empty.text = (tr or {}).get("error") or \
                sv.empty_state_text(state.get("meta"), editor.get_legs())
            replay_empty.set_visibility(True)
            replay_chart.set_visibility(False)
            scrub_lbl.text = "Drag the slider to step through time"
            return
        replay_empty.set_visibility(False)
        replay_chart.set_visibility(True)
        n = len(tr["x"])
        cur = int(min(max(scrub_slider.value or 0, 0), n - 1))
        scrub_lbl.text = sv.replay_cursor_text(tr, cur) or "Drag the slider to step through time"
        # Update in place (chart already present for the ESM import map).
        replay_chart.options = replay_figure(tr, cursor=cur)
        replay_chart.update()

    # ── command enqueue (sim_run / sim_replay) ───────────────────────────────
    def _sym():
        return ((state.get("meta") or {}).get("symbol") or symbol_in.value or "").upper()

    def _legs_payload():
        """Editor legs → the SIMULATOR leg shape. NOTE: the simulator commands key
        the option type as ``kind`` (NOT ``option_type``) — map it here. Legs with
        no chosen strike are skipped."""
        legs = []
        for l in editor.get_legs():
            if l.get("strike") is None:
                continue
            legs.append({"kind": l["option_type"], "strike": float(l["strike"]),
                         "expiry": l.get("expiry"), "side": l["side"],
                         "qty": int(l.get("qty", 1) or 1)})
        return legs

    def _current_params():
        if not state.get("meta") or not _legs_payload():
            return None
        return {
            "symbol": _sym(),
            "legs": _legs_payload(),
            "dt": float(dt_slider.value),
            "mult": float(mult_slider.value),
        }

    @guard
    def _enqueue_run():
        """Enqueue a sim_run immediately (used for discrete strategy/leg edits)."""
        if state.get("restoring"):
            return
        params = _current_params()
        if params is None:
            return
        bus_client.request("options", {"type": "sim_run", "args": params})

    @guard
    def _enqueue_replay():
        """Enqueue a sim_replay — fires on discrete strategy/leg edits + look-back
        changes (not the dt/mult sliders), since the replay trace depends only on
        the legs + the look-back window."""
        if state.get("restoring"):
            return
        if not state.get("meta") or not _legs_payload():
            return
        bus_client.request("options", {"type": "sim_replay", "args": {
            "symbol": _sym(), "legs": _legs_payload(),
            "lookback": lookback_sel.value}})

    def _on_legs_changed():
        """A strategy pick or any leg edit changes both the sweep + the replay —
        enqueue both immediately (discrete, like the old selector path)."""
        _enqueue_run()
        _enqueue_replay()

    @guard
    def _slider_changed():
        """Sliders fire often during drag → stash latest params; the debounce
        timer flushes the most recent at most ~every 0.4s. Also repaint the ΔS
        overlay immediately (client-side, no command needed)."""
        if state.get("restoring"):
            return
        params = _current_params()
        if params is not None:
            state["pending"] = params
        _render_figures()

    @guard
    def _flush_pending():
        if state["pending"] is not None:
            bus_client.request("options", {"type": "sim_run", "args": state["pending"]})
            state["pending"] = None

    # ── load ─────────────────────────────────────────────────────────────────
    @guard
    def _request_fetch(show_wait=False):
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        if show_wait and state.get("loading"):
            # Collapses the focusout-then-button-click double fire while a load is in
            # flight. (The Load button still force-reloads once loading clears — it
            # bypasses should_load, unlike the Trade page's button-through-dedup.)
            return
        state["last_loaded"] = sym
        if show_wait:
            state["loading"] = True
            wait.show(f"Loading {sym}…")
            ui.timer(_overlay.LOAD_TIMEOUT_SEC, _fetch_timeout, once=True)
        bus_client.request("options", {"type": "sim_fetch", "args": {"symbol": sym}})
        status.text = "Loading chain…"

    @guard
    def _fetch_timeout():
        """Safety net: if the chain never arrived, drop the overlay + reset the
        dedup so a retry re-triggers (e.g. the service is down)."""
        if state.get("loading"):
            state["loading"] = False
            wait.hide()
            state["last_loaded"] = None

    @guard
    def _symbol_submit():
        """Tab-out / Enter on the symbol field → load (deduped: only when changed)."""
        if not should_load((symbol_in.value or "").strip().upper(), state.get("last_loaded")):
            return
        _request_fetch(show_wait=True)

    @guard
    def _set_all_expiry(e):
        """Set every leg to one expiry (``leg_editor.apply_expiry`` fires the
        editor's on_change, which re-runs and repaints), then clear the picker so it
        reads as an action rather than as a claim about the legs."""
        if not e.value or state.get("restoring"):
            return
        editor.apply_expiry(e.value)
        expiry_all.set_value(None)

    @guard
    def _reflow_charts():
        # Charts created inside an inactive tab panel measure a hidden (0×0)
        # container at mount, and NiceGUI's highchart never re-measures afterwards
        # (one reflow at mount, no ResizeObserver). When a tab becomes visible, ask
        # each chart to reflow so it picks up the now-real container width/height.
        for el in (replay_chart, whatif_chart):
            ui.run_javascript(f"getElement({el.id})?.chart?.reflow()")

    # Reflow after the newly-selected panel has actually become visible (+ persist
    # the active tab).
    tabs.on_value_change(lambda e: (ui.timer(0.05, _reflow_charts, once=True), _capture()))

    fetch_btn.on_click(lambda e: _request_fetch(show_wait=True))
    # Strategy pick → re-seed the editor from the template, then enqueue both runs.
    # Suppressed while restoring (the restored legs come via pending_legs, not the
    # template).
    strategy_sel.on_value_change(
        lambda e: None if state.get("restoring")
        else (editor.apply_template(strategy_sel.value), _on_legs_changed(), _capture(),
              _legs_ui()))
    expiry_all.on_value_change(_set_all_expiry)
    # ΔS is client-side only (overlay) — re-render, never enqueue.
    ds_slider.on_value_change(lambda e: (_render_figures(), _capture()))
    # Δt + IV-mult drive the sweep → debounced enqueue.
    dt_slider.on_value_change(lambda e: (_slider_changed(), _capture()))
    mult_slider.on_value_change(lambda e: (_slider_changed(), _capture()))
    # The scrub cursor is client-side only — move the cursor, never enqueue (not
    # persisted). A move the page makes itself (a new trace) repaints once, below.
    scrub_slider.on_value_change(
        lambda e: None if state.get("scrub_sync") else _render_replay())
    # Look-back override re-runs the replay with a different window.
    lookback_sel.on_value_change(lambda e: (_enqueue_replay(), _capture()))
    symbol_in.on_value_change(lambda e: _capture())
    symbol_in.on("keydown.enter", lambda e: _symbol_submit())
    symbol_in.on("focusout", lambda e: _symbol_submit())

    # ── version-poll repaint (fetch-free) ────────────────────────────────────
    def _apply_meta(meta):
        state["loading"] = False
        wait.hide()
        state["meta"] = meta or None
        # Repopulate the editor's per-leg expiry/strike selects from the new
        # chain. Pending legs copied in from the Calculator win; else when the
        # user hasn't touched the legs, re-seed the template so strikes snap to the
        # real ladder (mirrors the calculator's chain-load behavior); else just
        # refresh the option lists (preserve in-progress edits).
        pending = state.pop("pending_legs", None)
        if pending:
            editor.set_legs(pending)
        elif not editor.is_dirty():
            editor.apply_template(strategy_sel.value)
        else:
            editor.refresh_options()
        if meta:
            spot = meta.get("spot")
            spot_txt = f"{spot:,.2f}" if isinstance(spot, (int, float)) else "—"
            n = meta.get("n_contracts")
            n_txt = f"{n:,}" if isinstance(n, int) else "—"
            status.text = f"{meta.get('symbol')} spot {spot_txt} — {n_txt} contracts"
            # Kick off the first sweep + replay for the current legs.
            _enqueue_run()
            _enqueue_replay()
        _legs_ui()

    @guard
    def _poll_meta():
        version = bus_client.read_version("options:sim_meta")
        if version == state["meta_ver"]:
            return
        state["meta_ver"] = version
        _apply_meta(bus_client.read("options:sim_meta"))

    @guard
    def _poll_result():
        version = bus_client.read_version("options:sim_result")
        if version == state["result_ver"]:
            return
        state["result_ver"] = version
        state["result"] = bus_client.read("options:sim_result") or None
        _render_figures()
        _paint_tiles()

    @guard
    def _poll_replay():
        version = bus_client.read_version("options:sim_replay")
        if version == state["replay_ver"]:
            return
        state["replay_ver"] = version
        state["replay"] = bus_client.read("options:sim_replay") or None
        _sync_scrubber()
        _render_replay()

    # Initial paint: paint the cached result/replay instantly (no empty flash), then
    # seed. We track the current meta version WITHOUT applying the stale meta — the
    # seed below re-loads, and _poll_meta applies the fresh one (with the restored /
    # copied legs riding pending_legs).
    state["result_ver"] = bus_client.read_version("options:sim_result")
    state["result"] = bus_client.read("options:sim_result") or None
    state["replay_ver"] = bus_client.read_version("options:sim_replay")
    state["replay"] = bus_client.read("options:sim_replay") or None
    _legs_ui()
    _render_figures()
    _sync_scrubber()
    _render_replay()
    state["meta_ver"] = bus_client.read_version("options:sim_meta")

    ui.timer(2.0, _poll_meta)
    ui.timer(2.0, _poll_result)
    ui.timer(2.0, _poll_replay)
    ui.timer(0.4, _flush_pending)  # debounce flush for slider-driven sweeps

    # Seed precedence: an explicit Copy-to-Simulator handoff > the persisted snapshot
    # > SPY/PCS defaults. Both the handoff + restore paths stash their legs in
    # ``pending_legs`` and re-load; ``_apply_meta`` applies them when the fresh meta
    # lands and runs (auto-refresh) with the restored sliders.
    p = handoff.take_pending_simulator()
    seed = _ps.pick_seed(p, _LAST_SIM)
    if seed == "handoff":
        symbol_in.value = p.get("symbol") or symbol_in.value
        state["pending_legs"] = p.get("legs") or []
        _request_fetch()
    elif seed == "restore":
        _restore(_LAST_SIM)
        _request_fetch()   # auto-refresh: re-load + re-run with the restored legs/sliders
