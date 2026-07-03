"""Simulator page (Tier-3 reader) — What-if price sweep + IV-shock.

This page holds **no engine call**: the ChainSnapshot fetch + both sweep engines
(``WhatIfEngine``/``IVShockEngine`` over the snapshot) live in
``services/options_svc/compute`` (``sim_fetch``/``sim_run``). The snapshot is a
Python object that can't be JSON-serialized whole, so it stays in-process in the
service; this page only ever sees the JSON-safe selector **meta** and the
computed **sweep rows**.

Interaction model:

* **Fetch snapshot** → enqueue ``sim_fetch``; a version-poll on
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
discrete). The pure figure builders (``whatif_figure``/``ivshock_figure`` +
``_records``/``_plotline``) are unit-tested. Charts render via Highcharts
(``ui.highchart``). The leg payload sent to the SIMULATOR commands uses ``kind``
(NOT ``option_type``) — the editor returns ``option_type``, so it is mapped.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from .inputs import select_all_on_focus, should_load
# Shared dark-navy "dashboard" theme (same CSS the Calculator injects, so the two
# pages never drift).
from pages.options import theme
from .theme import QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, BTN, BTN_PRIMARY, LABEL
from . import page_state as _ps

# Persisted (single-user) Simulator input snapshot — survives navigation + browser
# reload, resets on a webgui restart (same as the other persisting pages). The pure
# snapshot/merge/precedence helpers live in page_state.py.
_SIM_KEYS = ("symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab")
_SIM_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "dt": 5.0,
                 "mult": 1.5, "lookback": "auto", "ds": 0.0, "active_tab": "Replay"}
_LAST_SIM: dict = {}

SPOT_COLOR = theme.hex_of("yellow")
TARGET_COLOR = theme.hex_of("blue")
BASE_COLOR = theme.hex_of("blue")
SHOCK_COLOR = theme.hex_of("amber")

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
        "xAxis": {**_DARK_AXIS, "title": {"text": "Underlying"}, "plotLines": xplotlines},
        "yAxis": {**_DARK_AXIS, "title": {"text": "P / L"}, "plotLines": yplotlines,
                  "plotBands": yplotbands},
        "tooltip": {"pointFormat": "S {point.x:g} → P/L <b>${point.y:,.0f}</b>"},
        # Smooth transition when the chart is updated in place on a slider change.
        "plotOptions": {"series": {"animation": {"duration": 500}}},
        # Area filled to the 0 threshold: the part above zero is green (profit), the
        # part below red (loss) — both line AND fill — split at the breakeven
        # crossings via color/negativeColor. Setting an explicit base ``color`` +
        # ``fillColor`` stops Highcharts painting a default-blue base path under the
        # zones.
        "series": [{"name": "P/L", "type": "area", "data": data,
                    "threshold": 0, "lineWidth": 2, "marker": {"enabled": False},
                    "color": PNL_GREEN, "fillColor": _PNL_GREEN_FILL,
                    "negativeColor": PNL_RED, "negativeFillColor": _PNL_RED_FILL}],
    }


def ivshock_figure(base, shock, mult=1.5):
    """Grouped base-vs-shock columns across the key metrics."""
    cats = ["Price", "Delta", "Gamma×100", "Theta", "Vega"]

    def vals(row):
        return [row.get("theo_price", 0), row.get("delta", 0),
                (row.get("gamma", 0) or 0) * 100, row.get("theta", 0), row.get("vega", 0)]

    return {
        # Explicit height (see whatif_figure): this chart mounts in an inactive tab
        # panel and would otherwise collapse to title-height — the IV-shock bug.
        "chart": {"type": "column", "backgroundColor": "transparent", "height": 420},
        "title": {"text": "IV shock", "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#bdbdbd"}},
        "xAxis": {**_DARK_AXIS, "categories": cats},
        "yAxis": {**_DARK_AXIS, "title": {"text": "Value"}},
        "plotOptions": {"column": {"grouping": True, "borderWidth": 0},
                        "series": {"animation": {"duration": 500}}},
        "series": [
            {"name": "base (×1.0)", "type": "column", "data": vals(base), "color": BASE_COLOR},
            {"name": f"shock (×{mult:g})", "type": "column", "data": vals(shock), "color": SHOCK_COLOR},
        ],
    }


_GREEK_PANELS = ["delta", "gamma", "theta", "vega", "rho"]
_PANEL_TITLES = ["Price", "Delta", "Gamma", "Theta", "Vega", "Rho"]
CURSOR_COLOR = theme.hex_of("red")
PRICE_COLOR = theme.hex_of("green")
GREEK_COLOR = theme.hex_of("blue")

# Replay look-back override menu (key → label). "auto" lets the service pick the
# window from the selected contract's DTE; the rest force a fixed window.
REPLAY_LOOKBACKS = [
    ("auto", "Auto (by DTE)"),
    ("1m_1d", "1-min · 1d"),
    ("5m_3d", "5-min · 3d"),
    ("5m_5d", "5-min · 5d"),
    ("15m_10d", "15-min · 10d"),
    ("1d_20d", "Daily · 20d"),
]


def lookback_options():
    """{key: label} dict for the Replay look-back ui.select."""
    return {key: label for key, label in REPLAY_LOOKBACKS}


def replay_figure(trace, cursor=None):
    """Stacked price + 5-Greek replay chart over an integer (gap-compressed) x.

    One Highcharts element with six stacked yAxes sharing the integer x-axis
    (overnight/weekend breaks already collapsed by ``compute.sim_replay``).
    Session boundaries render as dashed xAxis plotLines; ``cursor`` (an int
    x-index) draws one more vertical plotLine — the client-side scrub cursor. The
    x-axis stays NUMERIC (dates live in the tooltip / tick labels) to avoid the
    datetime-crosshair epoch-ms gotcha. Returns an empty-but-valid chart when
    ``trace`` is missing."""
    trace = trace or {}
    x = trace.get("x") or []
    prices = trace.get("prices") or []
    greeks = trace.get("greeks") or {}
    sessions = trace.get("sessions") or []

    panels = ["price"] + _GREEK_PANELS
    n = len(panels)
    gap = 3                                  # % vertical gap between panels
    h = (100 - gap * (n - 1)) / n
    yaxes, series = [], []
    for i, (panel, title) in enumerate(zip(panels, _PANEL_TITLES)):
        top = i * (h + gap)
        yaxes.append({**_DARK_AXIS,
                      "title": {"text": title, "style": {"color": "#bdbdbd"}},
                      "top": f"{top}%", "height": f"{h}%", "offset": 0,
                      "lineWidth": 1})
        col = prices if panel == "price" else (greeks.get(panel) or [])
        data = [[xi, v] for xi, v in zip(x, col)]
        series.append({"name": title, "type": "line", "yAxis": i, "data": data,
                       "color": PRICE_COLOR if panel == "price" else GREEK_COLOR,
                       "marker": {"enabled": False}})

    # Dashed session boundaries (skip the first session's start) + scrub cursor.
    xplotlines = [_plotline(s["start"] - 0.5, "#777777", dash="Dot", width=1)
                  for s in sessions[1:]]
    if cursor is not None:
        xplotlines.append(_plotline(cursor, CURSOR_COLOR, width=1))

    return {
        "chart": {"height": 600, "backgroundColor": "transparent"},
        "title": {"text": f"Replay — {trace.get('resolution', '')}".rstrip(" —"),
                  "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {**_DARK_AXIS, "plotLines": xplotlines,
                  "labels": {"style": {"color": "#bdbdbd"}}},
        "yAxis": yaxes,
        # valueDecimals caps the hover readout at 2dp (raw float precision is noise).
        "tooltip": {"shared": True, "valueDecimals": 2},
        "plotOptions": {"series": {"animation": False}},
        "series": series,
    }


def render():
    """Simulator page: fetch + strategy/leg editor + Replay / What-if / IV-shock tabs."""
    from . import handoff
    from . import leg_editor
    from . import strategies as S
    from . import strategy_menu
    from . import overlay as _overlay

    ui.add_css(QUASAR_INTERNAL_CSS)

    # Full-screen wait overlay shown while a user-initiated Fetch is in flight.
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
        "last_loaded": None,   # last symbol a Fetch was triggered for (tab/Enter dedup)
        "loading": False,      # True while a user-initiated fetch is in flight (overlay up)
    }

    # ── Dark "dashboard" shell (page-scoped, .calc-v2) — the SAME navy theme as the
    # Calculator (shared ``theme.py``), wrapping the Simulator's existing structure
    # in three cards: controls, strategy+legs, and the tabbed chart panel. The
    # Highcharts panels are already dark-transparent, so they sit on the navy. ──────
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        ui.label("Simulator").classes(f"text-h6 {LABEL}")

        # Controls card: symbol + fetch + copy + status.
        with ui.column().classes(f"{CARD} w-full gap-3"):
            with ui.row().classes("items-end gap-4 flex-wrap"):
                symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-40"))
                fetch_btn = ui.button("Fetch snapshot", icon="download", color=None) \
                    .props("no-caps").classes(BTN_PRIMARY)
                ui.button("Copy to Calculator", icon="calculate", color=None,
                          on_click=lambda: handoff.send_to_calculator_legs(
                              leg_editor.legs_to_payload(
                                  (state.get("meta") or {}).get("symbol")
                                  or symbol_in.value or "",
                                  editor.get_legs(), keep_premium=False))) \
                    .props("no-caps").classes(BTN)
            status = ui.label("Fetch a snapshot to begin.").classes(EYEBROW)

        # Strategy + legs card. Cascading Strategy picker (family → variant, boxed to
        # match the navy inputs) + the shared multi-leg editor (header table).
        # ``show_premium=False`` — the simulator prices each leg from the chain's IV,
        # so there is no manual premium input.
        with ui.column().classes(f"{CARD} w-full gap-3"):
            with ui.row().classes("items-end gap-3 flex-wrap"):
                strategy_sel = strategy_menu.build_strategy_menu(value="PCS", classes="w-52", boxed=True)
            legs_box = ui.column().classes("gap-2 w-full")

        # Tabbed chart card: Replay / What-if / IV-shock.
        with ui.column().classes(f"{CARD} w-full gap-2"):
            with ui.tabs() as tabs:
                tab_replay = ui.tab("Replay")
                tab_whatif = ui.tab("What-if")
                tab_ivshock = ui.tab("IV shock")
            with ui.tab_panels(tabs, value=tab_replay).classes("w-full"):
                with ui.tab_panel(tab_replay):
                    with ui.row().classes("items-center gap-4 w-full"):
                        lookback_sel = ui.select(lookback_options(), value="auto",
                                                 label="Look-back").classes("w-44")
                        scrub_lbl = ui.label("Cursor —")
                        scrub_slider = ui.slider(min=0, max=1, value=0).classes("w-80")
                    # Persistent chart built ONCE (present at first render for the ESM
                    # import map) and updated in place. Empty-state label toggled until
                    # the first replay trace arrives.
                    replay_empty = ui.label("Select a contract to run the replay.").classes("opacity-70")
                    replay_chart = ui.highchart(replay_figure({}, None)).classes("w-full")
                with ui.tab_panel(tab_whatif):
                    with ui.row().classes("items-center gap-4 w-full"):
                        ds_lbl = ui.label("ΔS 0%")
                        ds_slider = ui.slider(min=-20, max=20, value=0).classes("w-48")
                        dt_lbl = ui.label("Δt 5d elapsed").tooltip(
                            "Calendar days elapsed from now (per-leg time decay)")
                        dt_slider = ui.slider(min=0, max=30, value=5).classes("w-48")
                    # Persistent charts built ONCE (present at first render for the ESM
                    # import map) and updated in place so slider changes ANIMATE instead
                    # of flickering through a clear()/recreate. Empty-state label toggled
                    # alongside until the first sweep result arrives.
                    whatif_empty = ui.label("Select a contract to run the sweep.").classes("opacity-70")
                    whatif_chart = ui.highchart(whatif_figure([], 0)).classes("w-full")
                with ui.tab_panel(tab_ivshock):
                    with ui.row().classes("items-center gap-4 w-full"):
                        mult_lbl = ui.label("IV ×1.5")
                        mult_slider = ui.slider(min=0.5, max=3.0, step=0.1, value=1.5).classes("w-64")
                    ivshock_chart = ui.highchart(ivshock_figure({}, {}, 1.5)).classes("w-full")

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

    editor = leg_editor.build_leg_editor(
        legs_box, strikes_for=_strikes_for, expiries_for=_expiries_for,
        show_premium=False, on_change=lambda: (_on_legs_changed(), _capture()), header=True,
        spot_getter=lambda: (state.get("meta") or {}).get("spot") or 0)

    # Seed the default template (PCS) so a cold page shows the strategy's legs
    # immediately. Tolerates empty strikes/expiries pre-fetch; strikes snap to the
    # real ladder once a snapshot arrives (see ``_apply_meta``). ``apply_template``
    # → ``set_legs`` does NOT fire ``on_change``, so no premature command enqueues.
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
        post-fetch ``_apply_meta`` applies them (beating the template re-seed) and
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

    # ── render figures from the cached sweep result ──────────────────────────
    def _render_figures():
        ds_lbl.text = f"ΔS {ds_slider.value:+g}%"
        dt_lbl.text = f"Δt {dt_slider.value:g}d elapsed"
        mult = float(mult_slider.value)
        mult_lbl.text = f"IV ×{mult:g}"

        result = state["result"]
        if not result:
            whatif_empty.set_visibility(True)
            whatif_chart.set_visibility(False)
            return
        whatif_empty.set_visibility(False)
        whatif_chart.set_visibility(True)

        spot = result.get("spot")
        # ΔS is a CLIENT-SIDE overlay line only — no command, computed here.
        target_s = spot * (1 + ds_slider.value / 100.0) if spot is not None else None
        # Update in place so Highcharts animates the transition (no clear/recreate).
        whatif_chart.options = whatif_figure(result.get("whatif_rows") or [], spot,
                                             target_s, baseline=result.get("whatif_baseline"))
        whatif_chart.update()
        shock = result.get("ivshock")
        if shock:
            ivshock_chart.options = ivshock_figure(shock["base"], shock["shock"], mult)
            ivshock_chart.update()

    # ── render the replay trace + client-side scrub cursor ───────────────────
    def _render_replay():
        tr = state["replay"]
        if not tr or tr.get("error") or not tr.get("x"):
            replay_empty.text = (tr or {}).get("error") or "Select a contract to run the replay."
            replay_empty.set_visibility(True)
            replay_chart.set_visibility(False)
            return
        replay_empty.set_visibility(False)
        replay_chart.set_visibility(True)
        n = len(tr["x"])
        scrub_slider.max = max(n - 1, 1)
        cur = int(min(max(scrub_slider.value, 0), n - 1))
        ts = tr["timestamps"][cur] if cur < len(tr.get("timestamps") or []) else ""
        spec_lbl = (tr.get("lookback") or {}).get("label") or ""
        cursor_txt = f"Cursor {ts.replace('T', ' ')}" if ts else "Cursor —"
        scrub_lbl.text = f"{cursor_txt}   ·   {spec_lbl}" if spec_lbl else cursor_txt
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
        # ΔS only moves the overlay; mult/dt labels update on the next repaint.
        ds_lbl.text = f"ΔS {ds_slider.value:+g}%"
        _render_figures()

    @guard
    def _flush_pending():
        if state["pending"] is not None:
            bus_client.request("options", {"type": "sim_run", "args": state["pending"]})
            state["pending"] = None

    # ── fetch ────────────────────────────────────────────────────────────────
    @guard
    def _request_fetch(show_wait=False):
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        if show_wait and state.get("loading"):
            # Collapses the focusout-then-button-click double fire while a fetch is in
            # flight. (The Fetch button still force-reloads once loading clears — it
            # bypasses should_load, unlike the Trade page's button-through-dedup.)
            return
        state["last_loaded"] = sym
        if show_wait:
            state["loading"] = True
            wait.show(f"Fetching {sym}…")
            ui.timer(_overlay.LOAD_TIMEOUT_SEC, _fetch_timeout, once=True)
        bus_client.request("options", {"type": "sim_fetch", "args": {"symbol": sym}})
        status.text = "Fetching snapshot…"

    @guard
    def _fetch_timeout():
        """Safety net: if the snapshot never arrived, drop the overlay + reset the
        dedup so a retry re-triggers (e.g. the service is down)."""
        if state.get("loading"):
            state["loading"] = False
            wait.hide()
            state["last_loaded"] = None

    @guard
    def _symbol_submit():
        """Tab-out / Enter on the symbol field → Fetch (deduped: only when changed)."""
        if not should_load((symbol_in.value or "").strip().upper(), state.get("last_loaded")):
            return
        _request_fetch(show_wait=True)

    @guard
    def _reflow_charts():
        # Charts created inside an inactive tab panel measure a hidden (0×0)
        # container at mount, and NiceGUI's highchart never re-measures afterwards
        # (one reflow at mount, no ResizeObserver). When a tab becomes visible, ask
        # each chart to reflow so it picks up the now-real container width/height.
        for el in (replay_chart, whatif_chart, ivshock_chart):
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
        else (editor.apply_template(strategy_sel.value), _on_legs_changed(), _capture()))
    # ΔS is client-side only (overlay) — re-render, never enqueue.
    ds_slider.on_value_change(lambda e: (_render_figures(), _capture()))
    # Δt + IV-mult drive the sweep → debounced enqueue.
    dt_slider.on_value_change(lambda e: (_slider_changed(), _capture()))
    mult_slider.on_value_change(lambda e: (_slider_changed(), _capture()))
    # The scrub cursor is client-side only — move the cursor, never enqueue (not persisted).
    scrub_slider.on_value_change(lambda e: _render_replay())
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
        # snapshot. Pending legs copied in from the Calculator win; else when the
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
            status.text = (f"{meta.get('symbol')} spot {spot_txt} · "
                           f"{meta.get('n_contracts')} contracts")
            # Kick off the first sweep + replay for the current legs.
            _enqueue_run()
            _enqueue_replay()

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

    @guard
    def _poll_replay():
        version = bus_client.read_version("options:sim_replay")
        if version == state["replay_ver"]:
            return
        state["replay_ver"] = version
        state["replay"] = bus_client.read("options:sim_replay") or None
        _render_replay()

    # Initial paint: paint the cached result/replay instantly (no empty flash), then
    # seed. We track the current meta version WITHOUT applying the stale meta — the
    # seed below re-fetches, and _poll_meta applies the fresh one (with the restored /
    # copied legs riding pending_legs).
    state["result_ver"] = bus_client.read_version("options:sim_result")
    state["result"] = bus_client.read("options:sim_result") or None
    _render_figures()
    state["replay_ver"] = bus_client.read_version("options:sim_replay")
    state["replay"] = bus_client.read("options:sim_replay") or None
    _render_replay()
    state["meta_ver"] = bus_client.read_version("options:sim_meta")

    ui.timer(2.0, _poll_meta)
    ui.timer(2.0, _poll_result)
    ui.timer(2.0, _poll_replay)
    ui.timer(0.4, _flush_pending)  # debounce flush for slider-driven sweeps

    # Seed precedence: an explicit Copy-to-Simulator handoff > the persisted snapshot
    # > SPY/PCS defaults. Both the handoff + restore paths stash their legs in
    # ``pending_legs`` and re-fetch; ``_apply_meta`` applies them when the fresh meta
    # lands and runs (auto-refresh) with the restored sliders.
    p = handoff.take_pending_simulator()
    seed = _ps.pick_seed(p, _LAST_SIM)
    if seed == "handoff":
        symbol_in.value = p.get("symbol") or symbol_in.value
        state["pending_legs"] = p.get("legs") or []
        _request_fetch()
    elif seed == "restore":
        _restore(_LAST_SIM)
        _request_fetch()   # auto-refresh: re-fetch + re-run with the restored legs/sliders
