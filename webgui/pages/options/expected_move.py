"""Expected Move page (Tier-3 reader) — candlestick history + ATM-IV cone.

Engine-free: render() enqueues an ``expected_move`` command on ``cmd:options``
and version-polls ``options:expected_move``; the cone + candles + ATM IV are all
computed in ``services/options_svc``. Pure figure builders are unit-tested.

Reached via a new-browser-tab handoff (handoff.send_to_expected_move) from the
Scanner / Paper / Captured / Calculator pages, or standalone from the nav.
Chart is Highcharts candlestick (extras=["stock"], which also provides the axis
crosshair label boxes)."""

import math

from .theme import BTN_3D, MUTED

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


def nearest_strike(strikes, spot):
    """The strike in ``strikes`` closest to ``spot`` — the pre-selection for
    the Strike dropdown so the user isn't scrolling a 181-strike ladder.

    ``None`` for an empty/all-junk ladder or a missing/non-finite spot. Ties
    break toward the LOWER strike so the result is deterministic regardless
    of input order. Total over junk input — never raises."""
    if not isinstance(spot, (int, float)) or isinstance(spot, bool) or not math.isfinite(spot):
        return None
    candidates = [float(s) for s in (strikes or [])
                  if isinstance(s, (int, float)) and not isinstance(s, bool)
                  and math.isfinite(s)]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (abs(s - spot), s))


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


def summary_text(payload):
    """A compact "Spot … · ATM IV …% · Expected move ±$ (±%) to <expiry>" line
    shown above the chart. The move's half-width is read off the LAST point of
    the payload's own ``em_upper`` cone (the series actually plotted) rather
    than recomputed independently, so the number can never disagree with the
    drawn cone. Each clause degrades independently — a missing/non-finite
    ``atm_iv``, an empty cone, or a missing spot just omits that clause, not
    the whole line. Returns "" when there is nothing to say. Total over junk
    input (a non-dict payload, wrong-shaped cone points, etc.) — never
    raises."""
    p = payload if isinstance(payload, dict) else {}
    parts = []

    spot = p.get("spot")
    spot_ok = (isinstance(spot, (int, float)) and not isinstance(spot, bool)
               and math.isfinite(spot) and spot > 0)
    if spot_ok:
        parts.append(f"Spot {spot:.2f}")

    atm_iv = p.get("atm_iv")
    if (isinstance(atm_iv, (int, float)) and not isinstance(atm_iv, bool)
            and math.isfinite(atm_iv)):
        parts.append(f"ATM IV {atm_iv * 100:.2f}%")

    em_upper = p.get("em_upper") or []
    if spot_ok and em_upper:
        last = em_upper[-1]
        upper = last[1] if isinstance(last, (list, tuple)) and len(last) >= 2 else None
        if (isinstance(upper, (int, float)) and not isinstance(upper, bool)
                and math.isfinite(upper)):
            dollar = upper - spot
            pct = dollar / spot * 100
            clause = f"Expected move ±{dollar:.2f} (±{pct:.2f}%)"
            expiry = p.get("expiry")
            if expiry:
                clause += f" to {expiry}"
            parts.append(clause)

    return " · ".join(parts)


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
    # the (sparse, trading-day) points instead of straight segments. Solid (no
    # dashStyle) — a dashed cone read as visually "broken".
    if em_upper:
        series.append({"type": "spline", "name": "Upper EM", "data": em_upper,
                       "color": EM_UP_COLOR, "marker": {"enabled": False}})
    if em_lower:
        series.append({"type": "spline", "name": "Lower EM", "data": em_lower,
                       "color": EM_DOWN_COLOR, "marker": {"enabled": False}})

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
        # No hover-dim: without this Highcharts fades every non-hovered series
        # (candles or EM lines) to ~0.2 opacity on hover (see the RRG chart in
        # sentiment_rotation.py for the sibling opacity-based use of this hook).
        "plotOptions": {"series": {"states": {"inactive": {"enabled": False}}}},
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
    # "drawn_symbol" is the symbol the chart was last actually drawn FOR (set at
    # the _enqueue choke point) — used to force a redraw on a symbol switch that
    # happens to keep the same expiry string (see _apply_chain).
    # "strike_touched" is True once the user has picked (or explicitly cleared)
    # a strike — distinct from strike_sel.value is None, which is ambiguous
    # between "never touched" and "deliberately cleared" (see _strike_changed).
    # "strike_seeding" is strike_sel's own analog of "seeding" — kept SEPARATE
    # (not reused) so a strike default and an expiry sync can never be confused
    # with each other; it suppresses on_value_change while THIS code (not the
    # user) writes strike_sel.value in _fill_strikes.
    # "preserve_handed_legs" is True while there is a handed (Scanner/Paper/
    # Captured/Calculator) leg list on screen that a silent strike default must
    # not be allowed to clobber — see _fill_strikes + the handoff block below.
    state = {"ver": None, "chain_ver": None, "last": None,
             "payload": None, "chain": {}, "seeding": False,
             "drawn_symbol": None, "strike_touched": False,
             "strike_seeding": False, "preserve_handed_legs": False}

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

    summary_lbl = ui.label("").classes(f"text-sm {MUTED}")

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
        summary_lbl.text = summary_text(state["payload"])

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
        # Single choke point for every draw (Draw button, expiry pick, look-back
        # change, handoff) — records the symbol the chart is now FOR, so a later
        # chain load can tell a real symbol switch apart from a same-symbol
        # refresh even when the expiry string happens to survive unchanged.
        state["drawn_symbol"] = payload["symbol"]
        args = {**payload, "lookback": lookback_sel.value}
        bus_client.request("options", {"type": "expected_move", "args": args})
        status.text = f"Computing expected move for {payload['symbol']}…"

    @guard
    def _draw():
        _enqueue({"symbol": (symbol_in.value or "").replace("$", "").upper(),
                  "expiry": expiry_sel.value or "", "legs": _current_legs()})

    @guard
    def _lookback_changed():
        if not state.get("last"):
            return
        payload = state["last"]
        if state.get("strike_touched"):
            # The user has taken manual control of the leg overlay since this
            # query was last enqueued (state["last"] only ever stores what was
            # sent, never updated by a LOCAL-only strike/type repaint) — resend
            # the CURRENT on-screen selection so a look-back change can't
            # silently revert a picked strike back to the stale legs. Gated on
            # strike_touched so an UNTOUCHED multi-leg handoff (PCS/IC/…) still
            # re-sends its own multi-leg list, not an empty override.
            payload = {**payload, "legs": _current_legs()}
        _enqueue(payload)

    @guard
    def _load_chain():
        sym = (symbol_in.value or "").replace("$", "").upper()
        if not sym:
            return
        bus_client.request("options", {"type": "em_chain", "args": {"symbol": sym}})
        status.text = f"Loading {sym} expirations…"

    @guard
    def _strike_changed():
        if state["strike_seeding"]:
            return  # programmatic default from _fill_strikes, not a user pick
        # Local-only: the strike is a plotLine, and the candles/cone do not
        # depend on it. No command, no chain refetch.
        if strike_sel.value is not None:
            # Only a REAL strike pick counts as "the user took control of the
            # local override" — toggling put/call before ever picking a strike
            # must not latch this, or a later look-back change would resend an
            # empty override in place of a still-untouched handed multi-leg
            # list (see _lookback_changed). Once latched it stays latched
            # (including through an explicit clear back to None), since that
            # too is a deliberate user action, not "never touched".
            state["strike_touched"] = True
            # The user has now demonstrably taken manual control, so a handed
            # payload's legs no longer need silent-default protection either
            # (see _fill_strikes) — a later symbol switch is free to re-default.
            state["preserve_handed_legs"] = False
        _repaint_local()

    def _fill_strikes():
        """Repopulate the strike ladder for the current expiry and, when
        nothing is already kept, pre-select the strike nearest spot — so the
        user isn't scrolling a 181-strike ladder.

        The default is SKIPPED while ``state["preserve_handed_legs"]`` is set
        (a Scanner/Paper/Captured/Calculator handoff's own multi-leg — or
        single-leg — list is on screen and the user hasn't touched the strike
        dropdown yet): silently pre-filling strike_sel.value there wouldn't
        itself repaint the chart (that only happens via a REAL, unguarded
        on_value_change), but it WOULD make a later put/call toggle look like
        "the user already picked a strike" (``strike_sel.value is not None``
        in ``_strike_changed``), latching strike_touched and replacing the
        handed lines with a single-strike override the user never asked for.

        The write itself is wrapped in ``state["strike_seeding"]`` (strike_sel's
        own guard, kept separate from expiry_sel's ``state["seeding"]`` so the
        two programmatic syncs can't be confused) so it can never fire
        on_value_change -> _strike_changed on its own."""
        ladder = (state["chain"].get("strikes") or {}).get(expiry_sel.value) or []
        keep = strike_sel.value
        strike_sel.options = strike_options(ladder)
        new_value = keep if keep in strike_sel.options else None
        if new_value is None and not state["preserve_handed_legs"]:
            new_value = nearest_strike(ladder, state["chain"].get("spot"))
        state["strike_seeding"] = True
        try:
            strike_sel.value = new_value
            strike_sel.update()
        finally:
            state["strike_seeding"] = False

    @guard
    def _expiry_changed():
        if state["seeding"]:
            return  # programmatic sync (handoff seed / chain poll), not a user pick
        _fill_strikes()
        if expiry_sel.value:
            _draw()

    def _apply_chain(version):
        """Apply a freshly-landed ``options:em_chain`` payload. Not decorated with
        ``@guard`` itself — called only from the already-guarded coalesced
        ``_poll`` tick."""
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
        # A standard monthly expiry (e.g. "2026-09-18") is commonly listed for
        # BOTH the old and the new symbol, so the reassignment above can be a
        # value-unchanged no-op — on_value_change never fires, _expiry_changed
        # never runs, and _draw() never runs FOR THE NEW SYMBOL. Left alone,
        # the chart would keep showing the OLD symbol's candles/cone paired
        # with the NEW symbol's strike ladder (silently mismatched data — the
        # exact class of bug the chain-driven dropdowns exist to remove).
        # Force the redraw here, independent of whether the expiry string
        # moved, whenever the just-loaded chain names a different symbol than
        # the one the chart is currently drawn for. No-op until an expiry is
        # actually selected (nothing to draw yet).
        chain_symbol = meta.get("symbol")
        if chain_symbol and chain_symbol != state["drawn_symbol"] and expiry_sel.value:
            _draw()

    draw_btn.on_click(_draw)
    bind_symbol_load(symbol_in, _load_chain)          # Enter OR tab-out
    expiry_sel.on_value_change(lambda e: _expiry_changed())
    strike_sel.on_value_change(lambda e: _strike_changed())
    type_tog.on_value_change(lambda e: _strike_changed())
    lookback_sel.on_value_change(lambda e: _lookback_changed())

    @guard
    def _poll():
        # One coalesced 1s tick: read both view versions in a single pipelined
        # round-trip (cheap :ver counters, no payload deserialize) and dispatch
        # only whichever changed — the house pattern (see pages/options/gamma.py).
        v = bus_client.read_versions(["options:expected_move", "options:em_chain"])
        if v["options:expected_move"] != state["ver"]:
            state["ver"] = v["options:expected_move"]
            _repaint(bus_client.read("options:expected_move"))
        if v["options:em_chain"] != state["chain_ver"]:
            _apply_chain(v["options:em_chain"])

    pending = handoff.take_pending_expected_move()
    if pending:
        symbol_in.value = pending.get("symbol") or symbol_in.value
        if pending.get("legs"):
            # A handed leg list (single OR multi) must survive the chain load
            # that's about to run (_load_chain -> _apply_chain -> _fill_strikes)
            # untouched by the strike-nearest-spot default — see _fill_strikes.
            state["preserve_handed_legs"] = True
        if pending.get("expiry"):
            # Seed the select with the handed expiry so it shows BEFORE the
            # chain lands; _apply_chain keeps it if the real list contains it.
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
