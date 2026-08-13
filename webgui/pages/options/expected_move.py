"""Expected Move page (Tier-3 reader) — candlestick history + ATM-IV cone.

Engine-free: render() enqueues an ``expected_move`` command on ``cmd:options``
and version-polls ``options:expected_move``; the cone + candles + ATM IV are all
computed in ``services/options_svc``. Pure figure builders are unit-tested.

Reached via a new-browser-tab handoff (handoff.send_to_expected_move) from the
Scanner / Paper / Captured / Calculator pages, or standalone from the nav.
Chart is Highcharts candlestick (extras=["stock"], which also provides the axis
crosshair label boxes)."""

from .theme import BTN_3D

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
EM_UP_COLOR = "#66bb6a"
EM_DOWN_COLOR = "#ef5350"
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


def strike_options(strikes):
    """{strike_float: label} for the strike ui.select — trailing .0 trimmed."""
    return {float(s): f"{float(s):g}" for s in (strikes or [])}


def expiry_options(expirations, today=None):
    """{expiry: "YYYY-MM-DD  (Nd)"} for the expiry ui.select.

    The DTE suffix is what makes the list scannable — a bare date column of 50
    weeklies does not tell you which one is the 0-DTE. Unparseable entries fall
    back to the raw string rather than being dropped."""
    import datetime as dt

    today = today or dt.date.today()
    out = {}
    for e in expirations or []:
        try:
            out[e] = f"{e}  ({(dt.date.fromisoformat(str(e)) - today).days}d)"
        except (ValueError, TypeError):
            out[e] = str(e)
    return out


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


def expected_move_figure(payload, timeframe="daily", legs=None):
    """Highcharts options for the candlestick + EM cone + leg lines.

    ``legs`` overrides the payload's own legs when given (INCLUDING an empty
    list, which clears the lines) — the page passes its current strike/type
    selection so a strike change repaints locally instead of re-running the
    service. ``timeframe`` is accepted for future intraday support."""
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
                  "plotLines": leg_lines(p.get("legs") if legs is None else legs)},
        # Shared tooltip carries the DATE (header) + OHLC at the cursor on hover.
        # valueDecimals=2 limits the EM/price values to 2 decimals in the tooltip
        # regardless of the underlying float precision.
        "tooltip": {"shared": True, "xDateFormat": "%a, %b %e, %Y",
                    "valueDecimals": 2},
        "series": series,
    }


def render():
    """Build the Expected Move page: input row + persistent candlestick chart.

    Expiry and strike are now **chain-driven dropdowns**: typing a symbol loads
    its live expirations (an ``em_chain`` command → ``cache:options:em_chain``),
    picking an expiry populates that expiry's strike ladder and redraws, and
    picking a strike/put-call is a **local-only** repaint (no round trip — the
    strike is just a plotLine, see ``expected_move_figure``'s ``legs`` override).

    Handoff flow: a stashed payload (from Scanner/Paper/Captured/Calculator) is
    consumed once on load, its command enqueued immediately, and its chain
    loaded so the dropdowns populate. Standalone flow: the user types a symbol
    (chain loads on tab-out/Enter), picks an expiry (draws), and optionally
    picks a strike + put/call (local repaint only)."""
    from nicegui import ui

    import bus_client

    from pages.ui_guard import guard

    from . import handoff
    from .inputs import bind_symbol_load, select_all_on_focus

    # No page title — the tab strip names the page (2026-07-11 cleanup).

    # "chain" holds the last-loaded ``cache:options:em_chain`` payload (expiries
    # + per-expiry strike ladders); "payload" holds the last expected-move result
    # (candles/cone/legs) — the two are polled + repainted independently.
    # "seeding" suppresses on_value_change while THIS code (not the user) is
    # writing expiry_sel.value, so a programmatic sync can never fire a spurious
    # _draw() — see the on_value_change re-entrancy note below.
    state = {"ver": None, "chain_ver": None, "last": None,
             "payload": None, "chain": {}, "seeding": False}

    with ui.row().classes("items-end gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-28"))
        expiry_sel = ui.select({}, label="Expiry", with_input=True).classes("w-56")
        strike_sel = ui.select({}, label="Strike (optional)",
                               with_input=True, clearable=True).classes("w-40")
        type_tog = ui.toggle(["put", "call"], value="put")
        lookback_sel = ui.select(em_lookback_options(), value="auto",
                                 label="Look-back").classes("w-40")
        draw_btn = ui.button("Draw", icon="show_chart", color=None).props("no-caps").classes(BTN_3D)
        status = ui.label("").classes("opacity-70 text-sm")

    # stockChart gives an ordinal x-axis (collapses non-trading-day gaps); the
    # stock module also provides candlestick + crosshair label boxes.
    chart = ui.highchart(expected_move_figure({}), type="stockChart",
                         extras=["stock"]).classes("w-full")

    def _current_legs():
        """The leg list for the CURRENT strike/type selection (may be empty)."""
        if strike_sel.value is None:
            return []
        return [{"strike": float(strike_sel.value),
                 "option_type": type_tog.value, "side": "short"}]

    def _repaint(payload=None):
        """Repaint from the SERVICE's own legs (a poll landing a fresh result).

        Deliberately does NOT apply the local strike/type override here: a
        freshly-enqueued payload — including a multi-leg handoff (PCS/IC/…) —
        already carries its own authoritative ``legs`` list, and the user has
        not touched the strike dropdown for this result yet. Applying
        ``_current_legs()`` unconditionally would replace those handed lines
        with an empty single-strike selection the instant the result lands.
        The local override only engages once the user picks a strike/type —
        see ``_repaint_local``."""
        if payload is not None:
            state["payload"] = payload
            err = (payload or {}).get("error")
            spec = ((payload or {}).get("lookback") or {}).get("label") or ""
            status.text = err or (f"Look-back: {spec}" if spec else "")
        chart.options = expected_move_figure(state["payload"] or {})
        chart.update()

    def _repaint_local():
        """Repaint with the CURRENT strike/type selection as a local override —
        no service round trip (the plotline only needs the already-loaded
        chain/spot, not a re-run of the cone/candle compute)."""
        chart.options = expected_move_figure(state["payload"] or {},
                                             legs=_current_legs())
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
        _enqueue({"symbol": (symbol_in.value or "").replace("$", "").upper(),
                  "expiry": expiry_sel.value or "", "legs": _current_legs()})

    @guard
    def _lookback_changed():
        if state.get("last"):
            _enqueue(state["last"])

    @guard
    def _load_chain():
        sym = (symbol_in.value or "").replace("$", "").upper()
        if not sym:
            return
        bus_client.request("options", {"type": "em_chain", "args": {"symbol": sym}})
        status.text = f"Loading {sym} expirations…"

    @guard
    def _strike_changed():
        # Local-only: the strike is a plotLine, and the candles/cone do not
        # depend on it. No command, no chain refetch.
        _repaint_local()

    def _fill_strikes():
        ladder = (state["chain"].get("strikes") or {}).get(expiry_sel.value) or []
        keep = strike_sel.value
        strike_sel.options = strike_options(ladder)
        strike_sel.value = keep if keep in strike_sel.options else None
        strike_sel.update()

    @guard
    def _expiry_changed():
        if state["seeding"]:
            return  # programmatic sync (handoff seed / chain poll), not a user pick
        _fill_strikes()
        if expiry_sel.value:
            _draw()

    @guard
    def _poll_chain():
        version = bus_client.read_version("options:em_chain")
        if version == state["chain_ver"]:
            return
        state["chain_ver"] = version
        meta = bus_client.read("options:em_chain") or {}
        state["chain"] = meta
        if meta.get("error"):
            status.text = meta["error"]
            return
        keep = expiry_sel.value
        # Re-pointing the select at its OWN current value is a no-op in NiceGUI
        # (on_value_change only fires on an actual change), so this seeding guard
        # only matters the rare time the kept expiry drops out of a refreshed
        # chain (value flips to None) — belt-and-braces against ever firing a
        # spurious _draw() from a background poll.
        state["seeding"] = True
        try:
            expiry_sel.options = expiry_options(meta.get("expirations") or [])
            expiry_sel.value = keep if keep in expiry_sel.options else None
            expiry_sel.update()
        finally:
            state["seeding"] = False
        _fill_strikes()

    draw_btn.on_click(_draw)
    bind_symbol_load(symbol_in, _load_chain)          # Enter OR tab-out
    expiry_sel.on_value_change(lambda e: _expiry_changed())
    strike_sel.on_value_change(lambda e: _strike_changed())
    type_tog.on_value_change(lambda e: _strike_changed())
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
            # Seed the select with the handed expiry so it shows BEFORE the
            # chain lands; _poll_chain keeps it if the real list contains it.
            # Guarded (seeding=True) so this programmatic set can't fire
            # _expiry_changed -> a premature _draw() with the WRONG (empty
            # single-strike) legs, ahead of the _enqueue(pending) below which
            # carries the real (possibly multi-leg) payload.
            state["seeding"] = True
            try:
                expiry_sel.options = expiry_options([pending["expiry"]])
                expiry_sel.value = pending["expiry"]
                expiry_sel.update()
            finally:
                state["seeding"] = False
        state["ver"] = bus_client.read_version("options:expected_move")
        state["chain_ver"] = bus_client.read_version("options:em_chain")
        _enqueue(pending)
        _load_chain()
    else:
        state["ver"] = bus_client.read_version("options:expected_move")
        state["chain_ver"] = bus_client.read_version("options:em_chain")
        _load_chain()

    ui.timer(1.0, _poll)
    ui.timer(1.0, _poll_chain)
