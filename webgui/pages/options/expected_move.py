"""Expected Move page (Tier-3 reader) — candlestick history + ATM-IV cone.

Engine-free: render() enqueues an ``expected_move`` command on ``cmd:options``
and version-polls ``options:expected_move``; the cone + candles + ATM IV are all
computed in ``services/options_svc``. Pure figure builders are unit-tested.

Reached via a new-browser-tab handoff (handoff.send_to_expected_move) from the
Scanner / Paper / Captured / Calculator pages, or standalone from the nav.
Chart is Highcharts candlestick (extras=["stock"], which also provides the axis
crosshair label boxes)."""

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
EM_UP_COLOR = "#66bb6a"
EM_DOWN_COLOR = "#ef5350"
PUT_COLOR = "#ef9a9a"
CALL_COLOR = "#90caf9"

_DARK_AXIS = {"labels": {"style": {"color": "#bdbdbd"}},
              "gridLineColor": "rgba(255,255,255,0.06)",
              "lineColor": "rgba(255,255,255,0.15)"}


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
    if em_upper:
        series.append({"type": "line", "name": "Upper EM", "data": em_upper,
                       "color": EM_UP_COLOR, "dashStyle": "Dash",
                       "marker": {"enabled": False}})
    if em_lower:
        series.append({"type": "line", "name": "Lower EM", "data": em_lower,
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
        "xAxis": {**_DARK_AXIS, "type": "datetime",
                  "crosshair": {"label": {"enabled": True}, "snap": False}},
        "yAxis": {**_DARK_AXIS, "title": {"text": "Price"}, "opposite": False,
                  "crosshair": {"label": {"enabled": True}, "snap": False},
                  "plotLines": leg_lines(p.get("legs"))},
        "tooltip": {"shared": True},
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

    state = {"ver": None}

    with ui.row().classes("items-end gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-28"))
        expiry_in = ui.input("Expiry (YYYY-MM-DD)").classes("w-44")
        strike_in = ui.number("Strike (optional)", format="%.2f").classes("w-36")
        type_tog = ui.toggle(["put", "call"], value="put")
        draw_btn = ui.button("Draw", icon="show_chart")
        status = ui.label("").classes("opacity-70 text-sm")

    chart = ui.highchart(expected_move_figure({}), extras=["stock"]).classes("w-full")

    def _repaint(payload):
        status.text = (payload or {}).get("error") or ""
        chart.options = expected_move_figure(payload or {})
        chart.update()

    @guard
    def _enqueue(payload):
        if not payload or not payload.get("symbol") or not payload.get("expiry"):
            ui.notify("Symbol + expiry required.", type="warning")
            return
        bus_client.request("options", {"type": "expected_move", "args": payload})
        status.text = f"Computing expected move for {payload['symbol']}…"

    @guard
    def _draw():
        legs = []
        if strike_in.value:
            legs = [{"strike": float(strike_in.value),
                     "option_type": type_tog.value, "side": "short"}]
        _enqueue({"symbol": (symbol_in.value or "").replace("$", "").upper(),
                  "expiry": (expiry_in.value or "").strip(), "legs": legs})

    draw_btn.on_click(_draw)

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
