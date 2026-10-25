"""The PUBLIC Expected Move chart (``live.neuralstrike.co/expected-move``).

The third of the site's Strategy Tools, beside the public Calculator and
Simulator (2026-10-24). A visitor loads a symbol, picks an expiration, and sees
the implied-volatility cone drawn forward over the symbol's price history, with
a strike line of their own on it. Reached through
``expected_move.render(public=True)``, which hands off here before the private
page builds anything.

The chart is the private page's own: ``expected_move.expected_move_figure``,
``summary_text`` and ``expiry_options``, built the same way (``type="stockChart"``,
``extras=["stock"]``, updated in place - settled for THIS chart in commit
4980539). What differs is where the numbers come from:

* **Every write is ``bus_client.request_public_tool``**, on the tools stream the
  public Calculator already writes: a symbol's chain (``chain``), one more
  expiration (``expiry``), and the cone itself (``em``). The owner's
  ``em_chain`` / ``expected_move`` commands never exist here.
* **The strikes and expirations are the public chain**, the one key per symbol
  the Calculator and the Rescue form share, so a visitor here and one there
  share a fetch. The cone needs that expiration's strikes HELD by the worker -
  its implied volatility is read off them - so a pick of an expiration whose
  strikes are not loaded asks for them first.
* **The strike line is drawn here, not asked for.** The request names a symbol,
  an expiration and a look-back and nothing else, so it costs the same whatever
  line the visitor draws and two visitors share its result.
* **A visitor reads only their own answers** (``public_tools.request_key``),
  and the hourly limit is the Calculator's own ``TOOLS`` counter: loads and
  cones on the two pages count against one allowance.
* **A page load sends nothing.** It shows SPY's chain if a visitor loaded it
  recently; a Calculator position handed across in the same browser tab
  (``public_handoff``, READ-only here, as on the Simulator) is an action, and
  loads that position's symbol and draws its strikes.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import bus_client
from nicegui import context, run, ui
from shared import public_tools as pt
from shared.symbols import clean_symbol

from pages import busy as _busy
from pages import ui_kit as kit
from pages.ui_guard import guard, guard_async

from . import after_hours as _after_hours
from . import calc_live as _calc_live
from . import expected_move as _em
from . import public_handoff
from . import strategy_strip
from . import theme as _t
from .pub_chain_view import ladder_strikes, listed_expirations, loaded_expirations

log = logging.getLogger(__name__)

ROUTE = "/options/expected-move"     # the PRIVATE route; shell maps it
TITLE = "Expected Move"
DEFAULT_SYMBOL = "SPY"
POLL_SEC = 1.0
WINDOW = _calc_live.WINDOW
CONNECT_TIMEOUT_SEC = 10.0
LOAD_PROMPT = "Load a symbol, then pick an expiration to draw its expected move."
PICK_EXPIRY = "Pick an expiration to draw its expected move."


# ── pure: what the page says and asks ────────────────────────────────────────

def intro_text(window) -> str:
    return ("The move the options market is pricing for a symbol by an "
            "expiration: one implied volatility drawn forward as a cone over the "
            "price history. Nothing is placed anywhere. Loading and drawing run "
            f"{window['start']}–{window['end']} CT on trading days.")


def em_request(symbol, expiry, lookback):
    """The ``em`` request, or None for one the builder refuses."""
    req = {"kind": "em", "symbol": symbol, "expiry": expiry, "lookback": lookback}
    return req if pt.tools_command(req) is not None else None


def seed_lines(legs):
    """``(expiry, lines)`` for a Calculator position handed across: its option
    legs' strikes as the chart's strike lines, and the expiration of the first
    of them. Share legs have no strike to draw."""
    lines, expiry = [], None
    for leg in legs or []:
        if leg.get("option_type") not in ("call", "put") or leg.get("strike") is None:
            continue
        lines.append({"strike": float(leg["strike"]),
                      "option_type": leg["option_type"], "side": leg.get("side")})
        expiry = expiry or leg.get("expiry")
    return expiry, lines


# ── the page ─────────────────────────────────────────────────────────────────

async def _client_connected():
    """Wait for this page's socket: ``app.storage.tab`` raises until then."""
    await context.client.connected(timeout=CONNECT_TIMEOUT_SEC)


def render():
    visitor = _calc_live._visitor()
    window = _calc_live._window()
    state: dict = {
        "chain": None,        # the published public chain (strikes, no quotes read)
        "symbol": None,       # the symbol that chain belongs to
        "payload": None,      # the cone on screen
        "drawn": None,        # (symbol, expiry, lookback) the cone was asked for
        "lines": None,        # handed strike lines, while they still stand
        "lines_symbol": None,
        "pending": {},        # answer key -> what to do when it lands
        "reloaded": False,    # one automatic reload per Draw, never a loop
        "seeding": False,     # this code, not the visitor, is setting a select
    }

    with kit.page():
        strategy_strip.render(ROUTE)
        kit.header(TITLE)
        ui.label(intro_text(window)).classes(f"text-sm {_t.MUTED}")
        _after_hours.mount(WINDOW, window)
        with kit.control_bar():
            symbol_in = kit.symbol_field(value=DEFAULT_SYMBOL, on_load=lambda: _load())
            expiry_sel = kit.select_field("Expiry", {}, with_input=True, width="w-56")
            strike_sel = kit.select_field("Strike (optional)", {}, with_input=True,
                                          clearable=True, width="w-40")
            type_tog = ui.toggle(["put", "call"], value="put")
            lookback_sel = kit.select_field("Look-back", _em.em_lookback_options(),
                                            value="auto", width="w-40")
            draw_btn = kit.button("Draw", kind="primary", icon="show_chart")
        status = kit.status_line(LOAD_PROMPT)
        summary_lbl = ui.label("").classes(f"text-sm {_t.MUTED}")
        chart_box = ui.element("div").classes("w-full")
        with chart_box:
            chart = ui.highchart(_em.expected_move_figure({}), type="stockChart",
                                 extras=["stock"]).classes("w-full")
        chart_busy = _busy.build_busy(chart_box, "Loading…")

    # The private page's gate rules, for the same reason: ``kit.gate`` asks each
    # field's own validation, and a field with none is always valid. Silent, so
    # a visitor mid-retype is not painted red; the held Draw is the signal.
    for _f, _rule in ((symbol_in, {"Enter a symbol": lambda v: bool((v or "").strip())}),
                      (expiry_sel, {"Pick an expiry": lambda v: bool(v)})):
        _f.validation = _rule
        _f.without_auto_validation()
        _f.error = None

    # ------------------------------------------------------------ drawing

    def _lines():
        if state["lines"] and state["lines_symbol"] == state["symbol"]:
            return [dict(l) for l in state["lines"]]
        if strike_sel.value is None:
            return []
        return [{"strike": float(strike_sel.value), "option_type": type_tog.value,
                 "side": "short"}]

    def _repaint():
        chart.options = _em.expected_move_figure(state["payload"] or {},
                                                 legs=_lines())
        chart.update()
        summary_lbl.text = _em.summary_text(state["payload"])

    def _fill_strikes():
        ladder = ladder_strikes(state["chain"], expiry_sel.value, type_tog.value) \
            if expiry_sel.value in loaded_expirations(state["chain"]) else []
        keep = strike_sel.value
        state["seeding"] = True
        try:
            strike_sel.options = _em.strike_options(ladder)
            strike_sel.value = keep if keep in strike_sel.options else None
            strike_sel.update()
        finally:
            state["seeding"] = False

    # ------------------------------------------------------------ requests

    def _send(key, req, on_done, on_fail, ok=("done", "cached")):
        """Send one tools request unless this visitor is over the hourly limit
        the Calculator shares; return whether it went."""
        if key is None:
            status.text = _calc_live.outcome_text("invalid")
            return False
        if key in state["pending"]:
            return True
        if not _calc_live.TOOLS.allow(visitor):
            status.text = _calc_live.limit_text("tools")
            return False
        try:
            bus_client.request_public_tool(req)
        except ValueError:
            status.text = _calc_live.outcome_text("invalid")
            return False
        except Exception:  # noqa: BLE001 - worded for the visitor, logged for us
            log.warning("public expected move request failed", exc_info=True)
            status.text = _calc_live.REQUEST_FAILED
            return False
        state["pending"][key] = {"since": dt.datetime.now(dt.timezone.utc),
                                 "t0": time.monotonic(), "on_done": on_done,
                                 "on_fail": on_fail, "ok": ok}
        return True

    def _key(req):
        command = pt.tools_command(req) if req else None
        return pt.request_key(command) if command else None

    def _refused(outcome, _answer=None):
        chart_busy.hide()
        status.text = (_answer or {}).get("error_text") or (
            _calc_live.outcome_text(outcome) if outcome else _calc_live.NO_ANSWER)

    # ---- the chain

    @guard
    def _load(then=None):
        symbol = clean_symbol(symbol_in.value)
        if symbol is None:
            kit.symbol_error(symbol_in, _calc_live.BAD_SYMBOL)
            return
        kit.symbol_error(symbol_in, None)
        req = {"kind": "chain", "symbol": symbol}
        status.text = f"Loading {symbol}…"
        if _send(_key(req), req, lambda o, a: _on_chain(symbol, then), _refused):
            chart_busy.show(f"Loading {symbol}…")

    async def _on_chain(symbol, then=None):
        chain = await run.io_bound(bus_client.read, pt.chain_view(symbol))
        chart_busy.hide()
        if not chain or chain.get("no_options") or not listed_expirations(chain):
            status.text = _calc_live.outcome_text("no_options")
            return
        _apply_chain(chain)
        if then is not None:
            then()

    def _apply_chain(chain):
        state["chain"], state["symbol"] = chain, chain.get("symbol")
        symbol_in.value = state["symbol"]
        keep = expiry_sel.value
        listed = listed_expirations(chain)
        state["seeding"] = True
        try:
            expiry_sel.options = _em.expiry_options(listed)
            expiry_sel.value = keep if keep in expiry_sel.options else None
            expiry_sel.update()
        finally:
            state["seeding"] = False
        _fill_strikes()
        status.text = (f"{state['symbol']}: {len(listed)} expirations. "
                       + PICK_EXPIRY if expiry_sel.value is None
                       else f"{state['symbol']}: {len(listed)} expirations.")

    # ---- one more expiration, then the cone

    def _fetch_expiry(expiry, then):
        symbol = state["symbol"]
        req = {"kind": "expiry", "symbol": symbol, "expiry": expiry}
        status.text = "Loading strikes for that expiration…"

        async def _landed(_o, _a):
            chain = await run.io_bound(bus_client.read, pt.chain_view(symbol))
            if state["symbol"] != symbol:
                return                      # another symbol loaded meanwhile
            if not chain or expiry not in loaded_expirations(chain):
                _refused("error")
                return
            state["chain"] = chain
            _fill_strikes()
            then()

        if _send(_key(req), req, _landed, _refused):
            chart_busy.show("Loading strikes…")

    @guard
    def _draw():
        symbol, expiry = state["symbol"], expiry_sel.value
        if clean_symbol(symbol_in.value) != symbol:
            _load()                     # a typed symbol not loaded yet: load it
            return
        if not expiry:
            return
        if expiry not in loaded_expirations(state["chain"]):
            _fetch_expiry(expiry, _draw)
            return
        lookback = lookback_sel.value or "auto"
        req = em_request(symbol, expiry, lookback)
        key = _key(req)
        status.text = f"Computing the expected move for {symbol}…"

        async def _landed(_o, _a):
            payload = await run.io_bound(bus_client.read, pt.result_view(key))
            chart_busy.hide()
            if not isinstance(payload, dict):
                _refused("error")
                return
            state["payload"], state["drawn"] = payload, (symbol, expiry, lookback)
            state["reloaded"] = False
            spec = (payload.get("lookback") or {}).get("label") or ""
            status.text = f"Look-back: {spec}" if spec else ""
            _repaint()

        def _failed(outcome, answer):
            # The worker forgets a chain on a restart while the published one
            # stays fresh: reload it once and ask again, never in a loop.
            if outcome == "load_first" and not state["reloaded"]:
                state["reloaded"] = True
                _load(then=_draw)
                return
            _refused(outcome, answer)

        if _send(key, req, _landed, _failed):
            chart_busy.show(f"Computing the expected move for {symbol}…")

    @guard
    def _expiry_changed():
        if state["seeding"]:
            return
        _fill_strikes()
        if expiry_sel.value:
            _draw()

    @guard
    def _strike_changed():
        if state["seeding"]:
            return
        if strike_sel.value is not None:
            state["lines"] = None           # the visitor's own line from here
        _repaint()

    @guard
    def _type_changed():
        _fill_strikes()
        _strike_changed()

    @guard
    def _lookback_changed():
        if state["payload"] is not None:
            _draw()

    draw_btn.on_click(_draw)
    kit.gate(draw_btn, symbol_in, expiry_sel)
    expiry_sel.on_value_change(lambda e: _expiry_changed())
    strike_sel.on_value_change(lambda e: _strike_changed())
    type_tog.on_value_change(lambda e: _type_changed())
    lookback_sel.on_value_change(lambda e: _lookback_changed())

    # ------------------------------------------------------------ the poll

    @guard_async
    async def _poll():
        for key in list(state["pending"]):
            job = state["pending"].get(key)
            if job is None:
                continue
            answer = await run.io_bound(bus_client.read, pt.answer_view(key))
            done, ans = _calc_live.answer_state(answer, job["since"])
            if not done:
                if time.monotonic() - job["t0"] > _calc_live._wait_sec():
                    state["pending"].pop(key, None)
                    job["on_fail"](None, None)
                continue
            state["pending"].pop(key, None)
            outcome = (ans or {}).get("outcome")
            handler = job["on_done"] if outcome in job["ok"] else job["on_fail"]
            result = handler(outcome, ans)
            if hasattr(result, "__await__"):
                await result

    ui.timer(POLL_SEC, _poll)

    # ------------------------------------------------------------ the seed

    @guard_async
    async def _open():
        """A Calculator position handed across in this tab loads its symbol and
        draws its strikes; otherwise a page load sends nothing, and shows the
        default symbol's chain if a visitor loaded it recently."""
        seed = None
        try:
            await _client_connected()
            seed = public_handoff.read()
        except Exception:  # noqa: BLE001 - no socket: nothing to seed from
            seed = None
        if seed:
            symbol, legs = seed
            expiry, lines = seed_lines(legs)
            state["lines"], state["lines_symbol"] = (lines or None), symbol
            symbol_in.value = symbol

            def _then():
                if expiry and expiry in listed_expirations(state["chain"]):
                    state["seeding"] = True
                    try:
                        expiry_sel.value = expiry
                    finally:
                        state["seeding"] = False
                    _draw()

            _load(then=_then)
            return
        chain = await run.io_bound(bus_client.read, pt.chain_view(DEFAULT_SYMBOL))
        if chain and not chain.get("no_options") and listed_expirations(chain):
            _apply_chain(chain)

    ui.timer(0.1, _open, once=True)
