"""Expected Move page (Tier-3 reader) — candlestick history + ATM-IV cone.

Engine-free: render() enqueues an ``expected_move`` command on ``cmd:options``
and version-polls ``options:expected_move``; the cone + candles + ATM IV are all
computed in ``services/options_svc``. Pure figure builders are unit-tested.

Reached via a new-browser-tab handoff (handoff.send_to_expected_move) from the
Scanner / Paper / Captured / Calculator pages, or standalone from the nav.
Chart is Highcharts candlestick (extras=["stock"], which also provides the axis
crosshair label boxes)."""

from pages.options import theme
from .theme import BTN_3D

UP_COLOR = "#26a69a"
DOWN_COLOR = theme.hex_of("red")
EM_UP_COLOR = theme.hex_of("green")
EM_DOWN_COLOR = theme.hex_of("red")
PUT_COLOR = "#ef9a9a"
CALL_COLOR = "#90caf9"

# Crosshair date readout note: this Highcharts build renders the datetime X-axis
# crosshair LABEL box as the raw epoch-ms value and ignores both ``label.format``
# (date tokens) and a ``label.formatter`` function (verified on plain chart AND
# stockChart). So we keep the X crosshair LINE but disable its (raw-ms) label box,
# show the PRICE on the Y crosshair label, and put the DATE in the shared tooltip
# header (``tooltip.xDateFormat``) which appears at the cursor on hover.

_DARK_AXIS = {"labels": {"style": {"color": "#bdbdbd"}},
              "gridLineColor": "rgba(255,255,255,0.06)",
              "lineColor": "rgba(255,255,255,0.15)"}

# Expected Move trailing-history override menu (key → label). "auto" lets the
# service size the window to ~3× DTE (short DTE → intraday); the rest force a
# fixed daily window.
EM_LOOKBACKS = [
    ("auto", "Auto (≈3× DTE)"),
    ("1mo", "Daily · 1mo"),
    ("3mo", "Daily · 3mo"),
    ("6mo", "Daily · 6mo"),
    ("1y", "Daily · 1y"),
]


def em_lookback_options():
    """{key: label} dict for the Expected Move look-back ui.select."""
    return {key: label for key, label in EM_LOOKBACKS}


def leg_lines(legs):
    """yAxis plotLines for each leg: short solid / long dashed, put/call colored."""
    lines = []
    for leg in legs or []:
        strike = leg.get("strike")
        if not isinstance(strike, (int, float)):
            continue
        otype = leg.get("option_type", "")
        side = leg.get("side", "")
        color = CALL_COLOR if otype == "call" else PUT_COLOR
        pl = {"value": float(strike), "color": color, "width": 1.5, "zIndex": 4,
              "label": {"text": f"{side} {otype} {strike:g}",
                        "style": {"color": color, "fontSize": "10px"}}}
        if side == "long":
            pl["dashStyle"] = "Dash"
        lines.append(pl)
    return lines


def expected_move_figure(payload, timeframe="daily"):
    """Highcharts options for the candlestick + EM cone + leg lines.

    ``timeframe`` is accepted for future intraday support (daily only for now)."""
    p = payload or {}
    candles = p.get("candles") or []
    em_upper = p.get("em_upper") or []
    em_lower = p.get("em_lower") or []
    title = p.get("symbol") or "Expected Move"
    if p.get("expiry"):
        title = f"{title} — Expected Move to {p['expiry']}"

    series = [{
        "type": "candlestick", "name": p.get("symbol") or "Price", "data": candles,
        "color": DOWN_COLOR, "upColor": UP_COLOR,
        "lineColor": DOWN_COLOR, "upLineColor": UP_COLOR,
    }]
    # spline (not line) so the sqrt-time cone renders as a smooth parabola through
    # the (sparse, trading-day) points instead of straight segments.
    if em_upper:
        series.append({"type": "spline", "name": "Upper EM", "data": em_upper,
                       "color": EM_UP_COLOR, "dashStyle": "Dash",
                       "marker": {"enabled": False}})
    if em_lower:
        series.append({"type": "spline", "name": "Lower EM", "data": em_lower,
                       "color": EM_DOWN_COLOR, "dashStyle": "Dash",
                       "marker": {"enabled": False}})

    return {
        "chart": {"backgroundColor": "transparent", "height": 540},
        "title": {"text": title, "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#bdbdbd"}},
        "rangeSelector": {"enabled": False},
        "navigator": {"enabled": False},
        "scrollbar": {"enabled": False},
        # X crosshair: keep the vertical line, drop the (raw-ms) label box — the
        # date is shown in the tooltip header instead (see note above).
        # ordinal=True (the stockChart default, set explicitly) collapses
        # non-trading days (weekends/holidays) so there are no blank gaps — the
        # candles + trading-day-only cone render contiguously.
        "xAxis": {**_DARK_AXIS, "type": "datetime", "ordinal": True,
                  "crosshair": {"label": {"enabled": False}, "snap": False}},
        # Y crosshair: line + a 2dp PRICE label box.
        "yAxis": {**_DARK_AXIS, "title": {"text": "Price"}, "opposite": False,
                  "crosshair": {"label": {"enabled": True,
                                          "format": "{value:.2f}"},
                                "snap": False},
                  "plotLines": leg_lines(p.get("legs"))},
        # Shared tooltip carries the DATE (header) + OHLC at the cursor on hover.
        # valueDecimals=2 limits the EM/price values to 2 decimals in the tooltip
        # regardless of the underlying float precision.
        "tooltip": {"shared": True, "xDateFormat": "%a, %b %e, %Y",
                    "valueDecimals": 2},
        "series": series,
    }


def render():
    """Build the Expected Move page: input row + persistent candlestick chart.

    Handoff flow: a stashed payload (from Scanner/Paper/Captured/Calculator) is
    consumed once on load and its command enqueued immediately. Standalone flow:
    the user types a symbol + expiry (+ optional strike) and clicks Draw."""
    import datetime as dt

    from nicegui import ui

    import bus_client

    from pages.ui_guard import guard

    from . import handoff
    from .inputs import select_all_on_focus

    ui.label("Expected Move").classes("text-h5")

    state = {"ver": None, "last": None}

    with ui.row().classes("items-end gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-28"))
        expiry_in = ui.input("Expiry (YYYY-MM-DD)").classes("w-44")
        strike_in = ui.number("Strike (optional)", format="%.2f").classes("w-36")
        type_tog = ui.toggle(["put", "call"], value="put")
        lookback_sel = ui.select(em_lookback_options(), value="auto",
                                 label="Look-back").classes("w-40")
        draw_btn = ui.button("Draw", icon="show_chart", color=None).props("no-caps").classes(BTN_3D)
        status = ui.label("").classes("opacity-70 text-sm")

    # stockChart gives an ordinal x-axis (collapses non-trading-day gaps); the
    # stock module also provides candlestick + crosshair label boxes.
    chart = ui.highchart(expected_move_figure({}), type="stockChart",
                         extras=["stock"]).classes("w-full")

    def _repaint(payload):
        err = (payload or {}).get("error")
        spec = ((payload or {}).get("lookback") or {}).get("label") or ""
        status.text = err or (f"Look-back: {spec}" if spec else "")
        chart.options = expected_move_figure(payload or {})
        chart.update()

    @guard
    def _enqueue(payload):
        if not payload or not payload.get("symbol") or not payload.get("expiry"):
            ui.notify("Symbol + expiry required.", type="warning")
            return
        # Remember the query (sans look-back) so a look-back change can re-run it.
        state["last"] = {k: payload.get(k) for k in ("symbol", "expiry", "legs")}
        args = {**payload, "lookback": lookback_sel.value}
        bus_client.request("options", {"type": "expected_move", "args": args})
        status.text = f"Computing expected move for {payload['symbol']}…"

    @guard
    def _draw():
        legs = []
        if strike_in.value:
            legs = [{"strike": float(strike_in.value),
                     "option_type": type_tog.value, "side": "short"}]
        _enqueue({"symbol": (symbol_in.value or "").replace("$", "").upper(),
                  "expiry": (expiry_in.value or "").strip(), "legs": legs})

    @guard
    def _lookback_changed():
        if state.get("last"):
            _enqueue(state["last"])

    draw_btn.on_click(_draw)
    lookback_sel.on_value_change(lambda e: _lookback_changed())

    @guard
    def _poll():
        version = bus_client.read_version("options:expected_move")
        if version == state["ver"]:
            return
        state["ver"] = version
        _repaint(bus_client.read("options:expected_move"))

    pending = handoff.take_pending_expected_move()
    if pending:
        symbol_in.value = pending.get("symbol") or symbol_in.value
        if pending.get("expiry"):
            expiry_in.value = pending["expiry"]
        state["ver"] = bus_client.read_version("options:expected_move")
        _enqueue(pending)
    else:
        if not expiry_in.value:
            expiry_in.value = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        state["ver"] = bus_client.read_version("options:expected_move")

    ui.timer(1.0, _poll)
