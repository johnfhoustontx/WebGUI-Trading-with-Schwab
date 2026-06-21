"""Simulator page (Tier-3 reader) — What-if price sweep + IV-shock.

This page holds **no engine call**: the ChainSnapshot fetch + both sweep engines
(``WhatIfEngine``/``IVShockEngine`` over the snapshot) live in
``services/options_svc/compute`` (``sim_fetch``/``sim_run``). The snapshot is a
Python object that can't be JSON-serialized whole, so it stays in-process in the
service; this page only ever sees the JSON-safe selector **meta** and the
computed **sweep rows**.

Interaction model:

* **Fetch snapshot** → enqueue ``sim_fetch``; a version-poll on
  ``options:sim_meta`` populates the expiry/strike selectors.
* A selector change (expiry/kind/strike/dir) or a Δt / IV-mult **slider** change
  → enqueue ``sim_run`` with the current widget params; a version-poll on
  ``options:sim_result`` repaints both figures from the cached rows.
* The **ΔS** slider is purely a CLIENT-SIDE overlay line on the what-if chart
  (``target_s = spot*(1+ΔS/100)``) — it NEVER enqueues a command.

Sliders fire on every drag step, so ``sim_run`` is **debounced**: a slider change
only stashes the latest params; a short ``ui.timer`` flushes the most recent
params at most ~every 0.4 s. Selector changes enqueue immediately (they're
discrete). The pure figure builders (``whatif_figure``/``ivshock_figure`` +
``_records``/``_plotline``) are unit-tested. Charts render via Highcharts
(``ui.highchart``).
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from .inputs import select_all_on_focus

SPOT_COLOR = "#ffd54f"
TARGET_COLOR = "#42a5f5"
BASE_COLOR = "#42a5f5"
SHOCK_COLOR = "#ffa726"


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


def whatif_figure(df, spot, target_s=None):
    """Highcharts curve of underlying price (S) vs position theo price."""
    rows = _records(df)
    data = [[r["S"], r["theo_price"]] for r in rows]
    xplotlines = [_plotline(spot, SPOT_COLOR)]
    if target_s is not None:
        xplotlines.append(_plotline(target_s, TARGET_COLOR, dash="Dash"))
    yplotlines = [_plotline(0, "#888888", dash="Dash", width=1)]  # zero baseline
    return {
        # Explicit height: this chart mounts inside an inactive tab panel, and
        # NiceGUI's highchart only reflows once at mount (no ResizeObserver). Without
        # a fixed height it measures the hidden 0-height container and collapses to
        # title-height when the tab is shown.
        "chart": {"type": "line", "backgroundColor": "transparent", "height": 420},
        "title": {"text": "What-if: price sweep", "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {**_DARK_AXIS, "title": {"text": "Underlying"}, "plotLines": xplotlines},
        "yAxis": {**_DARK_AXIS, "title": {"text": "Theo price"}, "plotLines": yplotlines},
        "tooltip": {"pointFormat": "S {point.x:g} → theo <b>{point.y:.2f}</b>"},
        # Smooth transition when the chart is updated in place on a slider change.
        "plotOptions": {"series": {"animation": {"duration": 500}}},
        "series": [{"name": "Theo", "type": "line", "data": data,
                    "color": "#66bb6a", "marker": {"enabled": False}}],
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
CURSOR_COLOR = "#ef5350"
PRICE_COLOR = "#66bb6a"
GREEK_COLOR = "#42a5f5"

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
    """Simulator page: fetch button + contract selector + Replay / What-if / IV-shock tabs."""
    ui.label("Simulator").classes("text-h5")

    # Page state (local closure, not module globals — built per request).
    state: dict = {
        "meta": None,        # last sim_meta payload (selector source)
        "result": None,      # last sim_result payload (sweep rows)
        "meta_ver": None,    # last-seen sim_meta cache version
        "result_ver": None,  # last-seen sim_result cache version
        "pending": None,     # latest sim_run params awaiting the debounce flush
        "replay": None,      # last sim_replay payload (price/Greek trace)
        "replay_ver": None,  # last-seen sim_replay cache version
    }

    with ui.row().classes("items-center gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-28"))
        fetch_btn = ui.button("Fetch snapshot", icon="download")
        status = ui.label("Fetch a snapshot to begin.").classes("opacity-70 text-sm")

    with ui.row().classes("items-end gap-3 flex-wrap"):
        expiry_sel = ui.select([], label="Expiry").classes("w-40")
        kind_tog = ui.toggle(["call", "put"], value="call")
        strike_sel = ui.select([], label="Strike").classes("w-32")
        dir_tog = ui.toggle(["buy", "sell"], value="buy")

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
                dt_lbl = ui.label("Δt 5d")
                dt_slider = ui.slider(min=0, max=30, value=5).classes("w-48")
            # Persistent charts built ONCE (present at first render for the ESM
            # import map) and updated in place so slider changes ANIMATE instead of
            # flickering through a clear()/recreate. Empty-state label toggled
            # alongside until the first sweep result arrives.
            whatif_empty = ui.label("Select a contract to run the sweep.").classes("opacity-70")
            whatif_chart = ui.highchart(whatif_figure([], 0)).classes("w-full")
        with ui.tab_panel(tab_ivshock):
            with ui.row().classes("items-center gap-4 w-full"):
                mult_lbl = ui.label("IV ×1.5")
                mult_slider = ui.slider(min=0.5, max=3.0, step=0.1, value=1.5).classes("w-64")
            ivshock_chart = ui.highchart(ivshock_figure({}, {}, 1.5)).classes("w-full")

    # ── selector population from meta ────────────────────────────────────────
    def _strikes_for(expiry, kind):
        meta = state["meta"] or {}
        return ((meta.get("strikes") or {}).get(str(expiry)) or {}).get(kind) or []

    def _sync_strikes():
        strikes = _strikes_for(expiry_sel.value, kind_tog.value)
        strike_sel.options = strikes
        spot = (state["meta"] or {}).get("spot") or 0
        if strikes and strike_sel.value not in strikes:
            strike_sel.value = min(strikes, key=lambda s: abs(s - spot))
        strike_sel.update()

    # ── render figures from the cached sweep result ──────────────────────────
    def _render_figures():
        ds_lbl.text = f"ΔS {ds_slider.value:+g}%"
        dt_lbl.text = f"Δt {dt_slider.value:g}d"
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
        whatif_chart.options = whatif_figure(result.get("whatif_rows") or [], spot, target_s)
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

    # ── command enqueue (sim_run) ────────────────────────────────────────────
    def _current_params():
        if not state["meta"] or strike_sel.value is None:
            return None
        return {
            "symbol": (state["meta"].get("symbol") or symbol_in.value or "").upper(),
            "expiry": expiry_sel.value,
            "kind": kind_tog.value,
            "strike": strike_sel.value,
            "direction": dir_tog.value,
            "dt": float(dt_slider.value),
            "mult": float(mult_slider.value),
        }

    @guard
    def _enqueue_run():
        """Enqueue a sim_run immediately (used for discrete selector changes)."""
        params = _current_params()
        if params is None:
            return
        bus_client.request("options", {"type": "sim_run", "args": params})

    @guard
    def _enqueue_replay():
        """Enqueue a sim_replay — fires ONLY on discrete contract-selector
        changes (not the dt/mult sliders), since the replay trace depends only on
        the selected contract."""
        if not state["meta"] or strike_sel.value is None:
            return
        bus_client.request("options", {"type": "sim_replay", "args": {
            "symbol": (state["meta"].get("symbol") or symbol_in.value or "").upper(),
            "expiry": expiry_sel.value, "kind": kind_tog.value,
            "strike": strike_sel.value, "direction": dir_tog.value,
            "lookback": lookback_sel.value}})

    @guard
    def _slider_changed():
        """Sliders fire often during drag → stash latest params; the debounce
        timer flushes the most recent at most ~every 0.4s. Also repaint the ΔS
        overlay immediately (client-side, no command needed)."""
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

    def _on_selector():
        _sync_strikes()
        _enqueue_run()
        _enqueue_replay()

    # ── fetch ────────────────────────────────────────────────────────────────
    @guard
    def _request_fetch():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        bus_client.request("options", {"type": "sim_fetch", "args": {"symbol": sym}})
        status.text = "Fetching snapshot…"

    @guard
    def _reflow_charts():
        # Charts created inside an inactive tab panel measure a hidden (0×0)
        # container at mount, and NiceGUI's highchart never re-measures afterwards
        # (one reflow at mount, no ResizeObserver). When a tab becomes visible, ask
        # each chart to reflow so it picks up the now-real container width/height.
        for el in (replay_chart, whatif_chart, ivshock_chart):
            ui.run_javascript(f"getElement({el.id})?.chart?.reflow()")

    # Reflow after the newly-selected panel has actually become visible.
    tabs.on_value_change(lambda e: ui.timer(0.05, _reflow_charts, once=True))

    fetch_btn.on_click(_request_fetch)
    expiry_sel.on_value_change(lambda e: _on_selector())
    kind_tog.on_value_change(lambda e: _on_selector())
    strike_sel.on_value_change(lambda e: (_enqueue_run(), _enqueue_replay()))
    dir_tog.on_value_change(lambda e: (_enqueue_run(), _enqueue_replay()))
    # ΔS is client-side only (overlay) — re-render, never enqueue.
    ds_slider.on_value_change(lambda e: (_render_figures()))
    # Δt + IV-mult drive the sweep → debounced enqueue.
    dt_slider.on_value_change(lambda e: _slider_changed())
    mult_slider.on_value_change(lambda e: _slider_changed())
    # The scrub cursor is client-side only — move the cursor, never enqueue.
    scrub_slider.on_value_change(lambda e: _render_replay())
    # Look-back override re-runs the replay with a different window.
    lookback_sel.on_value_change(lambda e: _enqueue_replay())

    # ── version-poll repaint (fetch-free) ────────────────────────────────────
    def _apply_meta(meta):
        state["meta"] = meta or None
        exps = (meta or {}).get("expiries") or []
        expiry_sel.options = exps
        if exps and expiry_sel.value not in exps:
            expiry_sel.value = exps[0]
        expiry_sel.update()
        _sync_strikes()
        if meta:
            status.text = (f"{meta.get('symbol')} spot {meta.get('spot'):,.2f} · "
                           f"{meta.get('n_contracts')} contracts")
            # Kick off the first sweep + replay for the auto-selected contract.
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

    # Initial paint (graceful-empty when the service is cold).
    state["meta_ver"] = bus_client.read_version("options:sim_meta")
    state["meta"] = bus_client.read("options:sim_meta")
    if state["meta"]:
        _apply_meta(state["meta"])
    state["result_ver"] = bus_client.read_version("options:sim_result")
    state["result"] = bus_client.read("options:sim_result") or None
    _render_figures()
    state["replay_ver"] = bus_client.read_version("options:sim_replay")
    state["replay"] = bus_client.read("options:sim_replay") or None
    _render_replay()

    ui.timer(2.0, _poll_meta)
    ui.timer(2.0, _poll_result)
    ui.timer(2.0, _poll_replay)
    ui.timer(0.4, _flush_pending)  # debounce flush for slider-driven sweeps
