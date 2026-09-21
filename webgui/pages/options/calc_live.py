"""The PUBLIC Calculator (``live.neuralstrike.co/calculator``).

A visitor loads a symbol, builds a position in the leg table, and sees it
priced against the live market: six metric cards and the P&L matrix, plus Rate
my trade. Reached through ``calculator.render(public=True)``, which hands off
here before the private page builds anything. Design:
docs/plans/2026-09-21-public-calculator-simulator-design.md, section 3.

What the visitor uses is the private page's own machinery - the shared entry
panel, the leg editor's ``table`` layout, ``calculator``'s metric-card and
matrix renderers, and the private Rate my trade's banner and detail panel - so
a position reads the same on both origins. What they do NOT get:

* the Expected Move hand-off, Paper, and any restoring of an earlier visit;
* the checklist's Paper book line: the rating is drawn with ``allow_paper``
  False, which drops the line (``checks._book`` returns nothing) rather than
  greying it, and the checklist context carries no ledger caps at all;
* a quoted chain while the one quotes switch (``public_scan.show_leg_quotes``)
  is off. The published chain then carries strikes only, so the page builds no
  chain grid, no Bid / Mark / Ask select and no delta column, and the visitor
  types each leg's price. With the switch on, the published chain carries a
  small quotes block and the grid, the price source and the delta come back.

Three rules, and where each lives:

* **Every write goes through the two public request functions.** The page
  calls ``bus_client.request_public_tool`` / ``request_public_math`` and nothing
  else that enqueues; a test pins it at source level.
* **A visitor reads only their own answers.** Each request's answer and result
  keys are derived from the request itself (``public_tools.request_key``).
* **One visitor cannot fill either queue.** Two ``visitor_limit`` counters -
  loads and ratings (Schwab work), and pricing (CPU) - are checked before
  anything is sent.

The Simulator hand-off is WRITE-only here: every leg change and load writes
``public_handoff``; this page never reads it and always opens fresh.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import bus_client
import shell as _shell
import visitor_limit
from nicegui import context, run, ui
from shared import public_tools as pt
from shared.symbols import clean_symbol

from pages import ui_kit as kit
from pages.ui_guard import guard, guard_async

from . import calculator as _calc
from . import checks as _checks
from . import checks_feed as _checks_feed
from . import detail as _detail
from . import entry as _entry
from . import entry_panel
from . import leg_editor
from . import public_handoff
from . import rate_trade as _rate_trade
from . import sim_view as _sim_view
from . import strategy_table as _strategy_table
from . import theme as _t
from .chain_grid import extract_price, leg_delta
from .pub_chain_view import (grid_chain, has_quotes, ladder_strikes,
                             listed_expirations, loaded_expirations, quotes_as_of)

log = logging.getLogger(__name__)

TITLE = "Calculator"
DEFAULT_SYMBOL = "SPY"
DEFAULT_STRATEGY = "PCS"
POLL_SEC = 1.0
#: Quiet time after the last edit before the page asks for a price - the
#: private page's own debounce.
RECALC_DELAY_SEC = _calc.RECALC_DELAY_SEC
# How long a request is waited on beyond the service's own queue limit.
_ALLOWANCE_SEC = 60
NO_ANSWER = ("No answer yet. The service may be busy or offline; please try "
             "again in a few minutes.")
REQUEST_FAILED = "The request could not be sent. Please try again later."
BAD_SYMBOL = "That is not a symbol this page can load."
LOAD_PROMPT = "Load a symbol to build a position on its real strikes."
TYPED_PRICE_NOTE = ("Price is what you paid (long) or received (short), per share. "
                    "Share legs left at 0 are priced at the current share price.")
PICK_STRIKES = "Pick a strike for every option leg to see it priced."
TYPE_PRICES_PROMPT = "Type a price for each leg to see the P&L."
EDIT_PROMPT = "Change any leg or assumption to see the P&L."
DEFAULT_WINDOW = {"start": "08:40", "end": "15:00"}

#: The grid's columns with quotes on: exactly the four fields the published
#: quotes block carries (``public_chain.quotes_from_chain``). Any other column
#: would be a column of dashes.
GRID_COLUMNS = ("delta", "mark", "bid", "ask")

TOOLS = visitor_limit.Limiter(pt.tools_per_hour)
MATH = visitor_limit.Limiter(pt.math_per_hour)

#: Rows either side of spot in the P&L matrix - the private page's default.
_NUM_STRIKES_DEFAULT = 24


# ── pure: what the page says ─────────────────────────────────────────────────

def intro_text(window) -> str:
    return ("Build an options position, price it against the live market and see "
            "its P&L across prices and dates. Nothing is placed anywhere. Loading "
            f"and rating run {window['start']}–{window['end']} CT on trading days.")


def limit_text(kind) -> str:
    if kind == "math":
        return (f"You have reached the limit of {pt.math_per_hour()} price updates "
                "an hour. Please try again later.")
    return (f"You have reached the limit of {pt.tools_per_hour()} symbol and "
            "expiration loads, and ratings, an hour. Please try again later.")


def outcome_text(outcome) -> str:
    return pt.OUTCOME_TEXT.get(outcome, pt.OUTCOME_TEXT["error"])


def _when(iso):
    try:
        when = dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def answer_state(answer, since):
    """``(done, answer)`` for a request sent at ``since`` (aware datetime). An
    answer older than the request belongs to an earlier ask of the same thing.
    ``rescue_live.answer_state``'s rule, returning the whole answer so an
    ``error_text`` can be shown."""
    ans = answer if isinstance(answer, dict) else {}
    at = _when(ans.get("at"))
    if at is not None and at >= since - dt.timedelta(seconds=2):
        return True, ans
    return False, None


# ── pure: the requests ───────────────────────────────────────────────────────

def _usable(p):
    return (not isinstance(p, bool) and isinstance(p, (int, float))
            and p == p and p >= 0)


def _is_stock(leg):
    return str((leg or {}).get("option_type") or "").lower() == "stock"


def request_legs(legs):
    """The legs as a request carries them, or None when an OPTION leg has no
    usable price. A share leg with none goes as 0: the service prices it at
    spot (``tools_public._fill_share_premiums``)."""
    out = []
    for leg in legs or []:
        premium = leg.get("premium")
        if _is_stock(leg):
            premium = premium if _usable(premium) else 0.0
        elif not _usable(premium):
            return None
        out.append({"option_type": leg.get("option_type"), "side": leg.get("side"),
                    "qty": int(leg.get("qty", 1) or 1), "premium": float(premium),
                    "strike": leg.get("strike"), "expiry": leg.get("expiry")})
    return out


def price_request(symbol, strategy, legs, dirty, *, spot, iv_pct, rate_pct,
                  ivadj_pct, contracts, num_strikes, expiry):
    """``(request, None)`` - a ``price`` request built as the private
    ``do_calc`` builds ``calc_compute``'s params - or ``(None, reason)``:
    ``"not_ready"`` (a leg without a strike), ``"price_needed"`` (an option leg
    without a price) or ``"invalid"`` (a value the builder refuses)."""
    if not leg_editor.legs_ready(legs):
        return None, "not_ready"
    sent = request_legs(legs)
    if sent is None:
        return None, "price_needed"
    front = expiry or next((l["expiry"] for l in sent if l.get("expiry")), None)
    for leg in sent:
        if not _is_stock(leg):
            leg["expiry"] = leg.get("expiry") or front
    try:
        req = {"kind": "price", "symbol": symbol,
               "strategy": _calc._summary_strategy(strategy, legs, dirty),
               "spot": float(spot), "iv": float(iv_pct) / 100.0,
               "rate": float(rate_pct) / 100.0, "ivadj": float(ivadj_pct) / 100.0,
               "qty": int(contracts or 1), "expiry": front, "legs": sent,
               "num_strikes": max(pt.NUM_STRIKES[0],
                                  min(pt.NUM_STRIKES[1], int(num_strikes or 0)))}
    except (TypeError, ValueError):
        return None, "invalid"
    if pt.math_command(req) is None:
        return None, "invalid"
    return req, None


def iv_target(legs, spot):
    """``(option_type, strike, expiry)`` of the OPTION leg nearest spot - the
    most liquid mark, the private ``fetch_iv``'s pick - or None."""
    try:
        s = float(spot)
    except (TypeError, ValueError):
        return None
    if s != s or s <= 0:
        return None
    best = None
    for leg in legs or []:
        k = leg.get("strike")
        if _is_stock(leg) or isinstance(k, bool) or not isinstance(k, (int, float)) \
                or not leg.get("expiry") or leg.get("option_type") not in ("call", "put"):
            continue
        d = abs(float(k) - s)
        if best is None or d < best[0]:
            best = (d, (leg["option_type"], float(k), leg["expiry"]))
    return None if best is None else best[1]


def rating_context():
    """The checklist's context WITHOUT the owner's ledger caps.

    ``checks_feed.read_context`` also reads ``options:ledger_caps`` - the owner's
    paper book - for the Paper book line. A public rating draws no such line,
    so it asks with ``caps=False``: the three market views only, caps None.
    **Blocking**: call it through ``run.io_bound``."""
    return _checks_feed.read_context(caps=False)


# ── the page ─────────────────────────────────────────────────────────────────

def _visitor():
    try:
        return visitor_limit.client_key(context.client.request)
    except Exception:  # noqa: BLE001 - no request is one bucket, not a crash
        return "unknown"


def render():
    visitor = _visitor()
    window = _window()
    state: dict = {
        "chain": None,          # the published public chain (strikes [+ quotes])
        "grid": None,           # its quotes block in chain shape, or None
        "symbol": None,         # the symbol the chain belongs to
        "quotes": None,         # the mode the panel was built for
        "pending": {},          # answer key -> what to do when it lands
        "pending_move": None,   # an expiry the legs move to once it loads
        "price_key": None,      # the newest price request sent
        "shown_key": None,      # the price request whose result is on screen
        "iv_target": None,      # the contract the IV was last implied from
        "iv_manual": False,     # the visitor typed the IV: never overwrite it
        "rating_key": None,     # the newest rating request sent
        # False until the visitor's first action: a page load - a crawler, a
        # visit from the Tools menu - must send nothing at all, not even a
        # price or an implied volatility for a chain someone else loaded.
        "engaged": False,
        "applying": False,
    }
    refs: dict = {"panel": None, "editor": None}
    recalc = _entry.Debounce(RECALC_DELAY_SEC)

    # Rate my trade: built at the page's root, OUTSIDE the panel, because a
    # dialog deletes itself when the slot it was built in is cleared, and the
    # panel is rebuilt when the quotes mode changes.
    rating = kit.info_dialog("Rate my trade", width="w-[480px]")
    with rating.content:
        rating_status = ui.label("").classes(f"rate-status text-sm {_t.MUTED}")
        rating_banner = ui.column().classes("rate-banner w-full gap-1")
        rating_panel = _detail.render(width=440)

    with kit.page():
        head = kit.header(TITLE)
        with head.actions:
            kit.button("Open in Simulator", kind="secondary", icon="open_in_new",
                       on_click=lambda: _open_simulator(),
                       tooltip="Open this position in the Simulator")
            rate_btn = kit.button("Rate my trade", kind="primary", icon="verified",
                                  on_click=lambda: _rate(),
                                  tooltip="Grade these legs with the Strategy "
                                          "Finder's scorer and checklist")
        ui.label(intro_text(window)).classes(f"text-sm {_t.MUTED}")
        status = kit.status_line(LOAD_PROMPT)
        quotes_lbl = ui.label("").classes(f"calc-quotes-stamp text-xs {_t.MUTED}")
        panel_box = ui.column().classes("w-full min-w-0 gap-2")

        with ui.expansion("PRICING ASSUMPTIONS").props("dense") \
                .classes(f"w-full {_t.MUTED} text-[10px] tracking-[.14em]"):
            with ui.row().classes("w-full items-end gap-2 flex-wrap pt-1"):
                price_in = kit.number_field("Price", value=100.0, width="w-28")
                iv_in = kit.number_field("IV %", value=20.0, width="w-28")
                iv_in.classes("calc-iv")
                rate_in = kit.number_field("Rate %", value=4.5, width="w-28")
                ivchg_in = kit.number_field("IV change %", value=0.0, width="w-28")
                contracts_in = kit.number_field("Contracts", value=1, min=1,
                                                max=100, integer=True, width="w-28")
                nstrikes_in = kit.number_field(
                    "Strikes", value=_NUM_STRIKES_DEFAULT, min=pt.NUM_STRIKES[0],
                    max=pt.NUM_STRIKES[1], integer=True, width="w-28")

        results_note = ui.label(LOAD_PROMPT).classes(f"calc-results-note text-sm {_t.MUTED}")
        metrics_box = ui.element("div").classes(
            "calc-live-metrics grid grid-cols-[repeat(auto-fit,minmax(148px,1fr))] "
            "gap-2.5 w-full")
        matrix_card = ui.column().classes(f"{_t.CARD} w-full min-w-0 gap-2")
        with matrix_card:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                kit.section_title("P&L matrix")
                ui.space()
                matrix_note = ui.label("").classes(
                    f"{_t.MUTED} text-[10px] tracking-[.14em] truncate min-w-0")
            # The matrix is wide; it scrolls inside its card on a phone.
            grid_box = ui.column().classes("w-full min-w-0 overflow-x-auto")
    metrics_box.set_visibility(False)
    matrix_card.set_visibility(False)
    contracts = {"n": 1}

    # ------------------------------------------------------------ the panel

    def _strikes_for(expiry, otype):
        return ladder_strikes(state["chain"], expiry, otype)

    def _price_for(leg, source="mark"):
        """A quoted leg's price from the published quotes, at the row's side.
        A SHARE leg costs spot."""
        if leg_editor.is_stock_leg(leg):
            spot = _num(price_in.value)
            return spot if spot is not None and spot > 0 else None
        strike = leg.get("strike")
        if state["grid"] is None or isinstance(strike, bool) \
                or not isinstance(strike, (int, float)):
            return None
        return extract_price(state["grid"], leg.get("option_type"), float(strike),
                             leg.get("expiry"), source)

    def _mount(quotes):
        """Build the entry panel and the leg editor for one quotes mode,
        carrying the symbol, strategy and legs across a rebuild."""
        old_panel, old_editor = refs["panel"], refs["editor"]
        symbol = old_panel.symbol_in.value if old_panel else DEFAULT_SYMBOL
        strategy = old_panel.strategy_sel.value if old_panel else DEFAULT_STRATEGY
        legs = old_editor.get_legs() if old_editor else None
        dirty = old_editor.is_dirty() if old_editor else False
        state["quotes"] = quotes
        panel_box.clear()
        with panel_box:
            panel = entry_panel.build_entry_panel(
                strategy_value=strategy, grid=quotes, stack=True,
                columns=GRID_COLUMNS if quotes else None)
        panel.symbol_in.value = symbol
        if not quotes:
            with panel.legs_footer:
                ui.label(TYPED_PRICE_NOTE).classes(f"text-xs {_t.MUTED}")
        editor = leg_editor.build_leg_editor(
            panel.legs_box, strikes_for=_strikes_for,
            expiries_for=lambda: loaded_expirations(state["chain"]),
            listed_expiries_for=lambda: listed_expirations(state["chain"]),
            on_expiry_needed=lambda e: _fetch_expiry(e, move_all=False),
            show_premium=True, on_change=lambda: _on_legs_changed(),
            spot_getter=lambda: float(_num(price_in.value) or 0.0),
            layout="table",
            delta_for=(lambda leg: leg_delta(state["grid"], leg)) if quotes else None,
            price_for=_price_for if quotes else None,
            price_sources=quotes, allow_stock=True, min_legs=1,
            on_reset=lambda: _engage(_seed))
        refs["panel"], refs["editor"] = panel, editor
        panel.refresh_btn.on_click(lambda: _load())
        panel.symbol_in.on("keydown.enter", lambda e: _load())
        panel.strategy_sel.on_value_change(lambda e: _engage(_seed))
        panel.on_expiry(guard(lambda e: _engage(_on_expiry, e)))
        panel.on_pick(guard(_on_pick))
        if legs and dirty:
            # ``set_legs`` clears the edited flag, and an edited position must
            # keep pricing through the generic summary: re-adding the last leg
            # is an edit, which sets it again.
            editor.set_legs(legs[:-1])
            editor.add_leg(legs[-1])
        elif legs:
            editor.set_legs(legs)
        return panel, editor

    def _set_quietly(field, value):
        """Write an assumption the PAGE chose (the chain's spot, the implied
        IV) without it counting as the visitor's edit."""
        state["applying"] = True
        try:
            field.value = value
        finally:
            state["applying"] = False

    def _engage(fn, *args):
        state["engaged"] = True
        return fn(*args)

    def _panel():
        return refs["panel"]

    def _editor():
        return refs["editor"]

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f else None

    # ------------------------------------------------------------- the legs

    def _seed():
        editor, panel = _editor(), _panel()
        editor.apply_template(panel.strategy_sel.value, near=panel.selected_expiry())
        _scale(max(1, int(_num(contracts_in.value) or 1)))
        if state["quotes"]:
            editor.refill_prices()
        _after_legs()

    def _scale(factor):
        if factor == 1:
            return
        legs = _editor().get_legs()
        for leg in legs:
            leg["qty"] = max(1, min(100, round(int(leg.get("qty", 1) or 1) * factor)))
        _editor().set_legs(legs)

    @guard
    def _on_contracts(_e=None):
        new = max(1, min(100, int(_num(contracts_in.value) or 1)))
        if new != contracts["n"]:
            _scale(new / contracts["n"])
            _after_legs()
        contracts["n"] = new

    def _on_legs_changed():
        state["engaged"] = True             # only the visitor edits the legs
        _after_legs()

    def _after_legs():
        """Every change to the position: write the hand-off, re-imply the IV
        when the leg nearest spot moved, and price again once edits pause."""
        _write_handoff()
        _sync_rate_button()
        _maybe_iv()
        recalc.poke(time.monotonic())

    def _write_handoff():
        symbol = state["symbol"] or clean_symbol(_panel().symbol_in.value)
        if symbol:
            public_handoff.write(symbol, _editor().get_legs())

    def _sync_rate_button():
        rate_btn.set_enabled(state["chain"] is not None
                             and leg_editor.legs_ready(_editor().get_legs()))

    @guard
    def _on_pick(column, option_type, strike, expiry):
        _editor().place_pick(_entry.leg_from_pick(column, option_type, strike,
                                                  expiry, price=None))

    @guard
    def _open_simulator():
        symbol = state["symbol"] or clean_symbol(_panel().symbol_in.value)
        if symbol:
            public_handoff.write(symbol, _editor().get_legs())
        # Through the seam, naming the PRIVATE route: the public origin serves
        # the Simulator at /simulator (live_screens.PUBLIC_ROUTES), and a bare
        # ui.navigate.to is refused by tests/test_live_navigation.py.
        _shell.navigate_to("/options/simulator")

    # ------------------------------------------------------------- requests

    def _send(kind, key, fn, on_done, on_fail, ok=("done", "cached"), note=None):
        """Send one request unless this visitor is over their hourly limit;
        return whether it went. ``on_done(outcome, answer)`` runs when the
        answer is one of ``ok``; ``on_fail(outcome, answer)`` on anything else,
        or ``on_fail(None, None)`` when no answer came in time. ``note`` is the
        label a refusal to send is written to (the status line by default)."""
        where = note or status
        limiter = MATH if kind == "math" else TOOLS
        if key is None:
            where.text = outcome_text("invalid")
            return False
        if key in state["pending"]:
            return True                               # already waiting on it
        if not limiter.allow(visitor):
            where.text = limit_text(kind)
            return False
        try:
            fn()
        except ValueError:
            where.text = outcome_text("invalid")
            return False
        except Exception:  # noqa: BLE001 - worded for the visitor, logged for us
            log.warning("public calculator %s request failed", kind, exc_info=True)
            where.text = REQUEST_FAILED
            return False
        state["pending"][key] = {"since": dt.datetime.now(dt.timezone.utc),
                                 "t0": time.monotonic(), "on_done": on_done,
                                 "on_fail": on_fail, "ok": ok}
        return True

    # ---- loading a symbol

    @guard
    def _load():
        state["engaged"] = True
        panel = _panel()
        symbol = clean_symbol(panel.symbol_in.value)
        if symbol is None:
            kit.symbol_error(panel.symbol_in, BAD_SYMBOL)
            return
        kit.symbol_error(panel.symbol_in, None)
        req = {"kind": "chain", "symbol": symbol}
        command = pt.tools_command(req)
        status.text = f"Loading {symbol}…"
        sent = _send("tools", pt.request_key(command) if command else None,
                     lambda: bus_client.request_public_tool(req),
                     lambda outcome, _a: _on_chain(symbol, outcome),
                     lambda outcome, _a: _chain_refused(outcome))
        if sent:
            kit.set_busy(panel.refresh_btn, True, timeout=_wait_sec())
        elif status.text == outcome_text("invalid"):
            kit.symbol_error(panel.symbol_in, BAD_SYMBOL)

    def _chain_refused(outcome):
        kit.set_busy(_panel().refresh_btn, False)
        status.text = outcome_text(outcome) if outcome else NO_ANSWER

    async def _on_chain(symbol, outcome):
        kit.set_busy(_panel().refresh_btn, False)
        chain = await run.io_bound(bus_client.read, pt.chain_view(symbol))
        if not chain or chain.get("no_options") or not listed_expirations(chain):
            status.text = outcome_text("no_options")
            return
        _apply_chain(chain)

    def _apply_chain(chain):
        quotes = has_quotes(chain)
        same = state["symbol"] == chain.get("symbol")
        if not same:
            state["iv_manual"] = False      # a typed IV belonged to the old symbol
        state["chain"], state["symbol"] = chain, chain.get("symbol")
        state["grid"] = grid_chain(chain)
        state["iv_target"] = None
        state["shown_key"] = None
        if quotes != state["quotes"]:
            _mount(quotes)
        panel, editor = _panel(), _editor()
        panel.symbol_in.value = state["symbol"]
        spot = _num(chain.get("spot"))
        if spot is not None and spot > 0:
            _set_quietly(price_in, round(spot, 2))
        panel.spot_lbl.text = f"{spot:,.2f}" if spot else "—"
        listed = listed_expirations(chain)
        panel.set_chain(state["grid"], spot, expirations=listed)
        quotes_lbl.text = quotes_as_of(chain) or ""
        if same and editor.is_dirty():
            editor.refresh_options()
            if quotes:
                editor.refill_prices(only_missing=True)
            _after_legs()
        else:
            _seed()
        if not state["engaged"]:
            results_note.text = EDIT_PROMPT if quotes else TYPE_PRICES_PROMPT
        loaded = loaded_expirations(chain)
        status.text = (f"{state['symbol']}: {len(listed)} expirations, strikes for "
                       f"{len(loaded)} so far; the rest load when you pick one."
                       if len(loaded) < len(listed)
                       else f"{state['symbol']}: {len(listed)} expirations.")

    # ---- one more expiration

    @guard
    def _on_expiry(expiry):
        if state["chain"] is None:
            return
        if expiry in loaded_expirations(state["chain"]):
            state["pending_move"] = None
            _editor().apply_expiry(expiry)          # fires on_change
            if state["quotes"]:
                _editor().refill_prices()
            return
        _fetch_expiry(expiry, move_all=True)

    def _fetch_expiry(expiry, *, move_all):
        symbol = state["symbol"]
        if not symbol:
            return
        if move_all:
            state["pending_move"] = expiry
        req = {"kind": "expiry", "symbol": symbol, "expiry": expiry}
        command = pt.tools_command(req)
        status.text = f"Loading strikes for {_entry.expiry_label(expiry)}…"
        sent = _send("tools", pt.request_key(command) if command else None,
                     lambda: bus_client.request_public_tool(req),
                     lambda outcome, _a: _on_expiry_loaded(symbol, expiry, outcome),
                     lambda outcome, _a: _expiry_refused(expiry, outcome))
        if not sent:
            said = status.text
            _expiry_refused(expiry, None)
            status.text = said

    async def _on_expiry_loaded(symbol, expiry, outcome):
        chain = await run.io_bound(bus_client.read, pt.chain_view(symbol))
        if state["symbol"] != symbol:
            return                          # another symbol loaded meanwhile
        if not chain or expiry not in loaded_expirations(chain):
            _expiry_refused(expiry, outcome if outcome != "done" else "error")
            return
        state["chain"], state["grid"] = chain, grid_chain(chain)
        panel = _panel()
        panel.set_chain(state["grid"], _num(chain.get("spot")),
                        expiry=panel.selected_expiry(),
                        expirations=listed_expirations(chain))
        if state["pending_move"] == expiry:
            state["pending_move"] = None
            _editor().apply_expiry(expiry)
        else:
            _editor().refresh_options()
        if state["quotes"]:
            _editor().refill_prices(only_missing=True)
        status.text = f"Strikes loaded for {_entry.expiry_label(expiry)}."

    def _expiry_refused(expiry, outcome):
        """The strikes are not coming: put the strip and any leg parked on that
        expiration back on one that has strikes."""
        loaded = loaded_expirations(state["chain"])
        if state["pending_move"] == expiry:
            state["pending_move"] = None
        editor, panel = _editor(), _panel()
        legs = editor.get_legs()
        on = next((l.get("expiry") for l in legs if l.get("expiry") in loaded), None)
        if panel.selected_expiry() == expiry and (on or loaded):
            panel.set_expiry(on or loaded[0])
        if loaded and any(l.get("expiry") == expiry for l in legs):
            for leg in legs:
                if leg.get("expiry") == expiry:
                    leg["expiry"] = loaded[0]
            editor.set_legs(legs)
            _after_legs()
        status.text = (f"Could not load strikes for {_entry.expiry_label(expiry)}. "
                       + (outcome_text(outcome) if outcome else NO_ANSWER))

    # ---- implied volatility

    def _maybe_iv():
        if state["chain"] is None or not state["engaged"] or state["iv_manual"]:
            return
        target = iv_target(_editor().get_legs(), _num(price_in.value))
        if target is None or target == state["iv_target"]:
            return
        option_type, strike, expiry = target
        symbol = state["symbol"]
        req = {"kind": "iv", "symbol": symbol, "option_type": option_type,
               "strike": strike, "expiry": expiry}
        command = pt.math_command(req)
        if command is None:
            return
        key = pt.request_key(command)
        # The target is recorded only once the request is on its way, and
        # forgotten when it is refused or times out, so the next edit retries.
        if _send("math", key, lambda: bus_client.request_public_math(req),
                 lambda outcome, _a: _on_iv(key, symbol, target),
                 lambda outcome, _a: _iv_failed(target), note=results_note):
            state["iv_target"] = target

    def _iv_failed(target):
        if state["iv_target"] == target:
            state["iv_target"] = None

    async def _on_iv(key, symbol, target):
        res = await run.io_bound(bus_client.read, pt.result_view(key))
        # An answer for another symbol or another contract than the one the IV
        # is now implied from - or one arriving after the visitor typed an IV -
        # must not land in the field.
        if state["symbol"] != symbol or state["iv_target"] != target \
                or state["iv_manual"]:
            return
        iv = _num((res or {}).get("iv"))
        if iv is not None and iv > 0:
            _set_quietly(iv_in, round(iv, 1))
            recalc.poke(time.monotonic())  # price again at the implied IV
        # A failed implication keeps the field: the visitor can type one.

    # ---- pricing

    @guard
    def _recalc_tick():
        if not recalc.ready(time.monotonic()):
            return
        _price()

    def _price():
        if not state["engaged"]:
            return
        if state["chain"] is None:
            results_note.text = LOAD_PROMPT
            return
        editor, panel = _editor(), _panel()
        legs = editor.get_legs()
        req, why = price_request(
            state["symbol"], panel.strategy_sel.value, legs, editor.is_dirty(),
            spot=_num(price_in.value), iv_pct=_num(iv_in.value),
            rate_pct=_num(rate_in.value), ivadj_pct=_num(ivchg_in.value) or 0.0,
            contracts=_num(contracts_in.value) or 1,
            num_strikes=_num(nstrikes_in.value) or _NUM_STRIKES_DEFAULT,
            expiry=panel.selected_expiry())
        if req is None:
            results_note.text = (PICK_STRIKES if why == "not_ready"
                                 else outcome_text(why))
            return
        key = pt.request_key(pt.math_command(req))
        # Recorded BEFORE the early returns: after an edit and back, the
        # position on screen is this one, so an answer to the edit that is
        # still on its way must not paint over it.
        state["price_key"] = key
        if key == state["shown_key"]:
            results_note.text = ""     # its result is already on screen
            return
        if key in state["pending"]:
            results_note.text = "Pricing…"
            return
        spot = req["spot"]
        sent_legs = legs
        if _send("math", key, lambda: bus_client.request_public_math(req),
                 lambda outcome, _a: _on_price(key, spot, sent_legs),
                 lambda outcome, _a: _price_refused(key, outcome),
                 note=results_note):
            results_note.text = "Pricing…"

    async def _on_price(key, spot, legs):
        result = await run.io_bound(bus_client.read, pt.result_view(key))
        if key != state["price_key"]:
            return                     # a newer edit is on its way
        if not isinstance(result, dict):
            results_note.text = outcome_text("error")
            return
        summary = result.get("summary") or {}
        pnl_data = result.get("pnl_data") or []
        _calc._render_metrics(metrics_box, summary, legs, spot,
                              _calc.max_dte_from_legs(legs))
        _calc._render_grid(grid_box, result.get("eval_labels") or [], pnl_data, spot,
                           summary, legs)
        matrix_note.text = _calc.matrix_note_text(pnl_data,
                                                  _calc.matrix_basis(summary, legs))
        state["shown_key"] = key
        results_note.text = ""
        metrics_box.set_visibility(True)
        matrix_card.set_visibility(True)

    def _price_refused(key, outcome):
        if key != state["price_key"]:
            return
        results_note.text = outcome_text(outcome) if outcome else NO_ANSWER

    # ---- Rate my trade

    @guard
    def _rate():
        if state["chain"] is None:
            status.text = LOAD_PROMPT
            return
        legs = _editor().get_legs()
        if not leg_editor.legs_ready(legs):
            status.text = PICK_STRIKES
            return
        if state["quotes"]:
            sent = [dict(l, premium=l["premium"] if _usable(l.get("premium")) else 0.0,
                         qty=int(l.get("qty", 1) or 1)) for l in legs]
        elif any(not _is_stock(l) and not (_usable(l.get("premium"))
                                           and l["premium"] > 0) for l in legs):
            # Quotes off: the service rates the visitor's OWN prices and refuses
            # an option leg priced at 0 (price_needed). Said here, so no request
            # is spent learning it.
            sent = None
        else:
            sent = request_legs(legs)
        rating_banner.clear()
        rating_panel.clear()
        if sent is None:
            state["rating_key"] = None
            rating_status.text = outcome_text("price_needed")
            rating.open()
            return
        req = {"kind": "rate", "symbol": state["symbol"],
               "structure": _sim_view.template_for(legs) or "CUSTOM", "legs": sent}
        command = pt.tools_command(req)
        key = pt.request_key(command) if command else None
        # The newest rating is the only one the dialog shows: a slower answer
        # to an earlier trade must not paint over it.
        state["rating_key"] = key
        rating_status.text = "Rating…"
        rating.open()
        _send("tools", key, lambda: bus_client.request_public_tool(req),
              lambda outcome, _a: _on_rating(key),
              lambda outcome, answer: _rating_refused(key, outcome, answer),
              note=rating_status)

    def _rating_refused(key, outcome, answer):
        if key != state["rating_key"]:
            return
        text = (answer or {}).get("error_text")
        rating_status.text = text if isinstance(text, str) and text else (
            outcome_text(outcome) if outcome else NO_ANSWER)

    async def _on_rating(key):
        if key != state["rating_key"]:
            return
        payload = await run.io_bound(bus_client.read, pt.result_view(key))
        if key != state["rating_key"]:
            return
        row = (payload or {}).get("row") if isinstance(payload, dict) else None
        if not isinstance(row, dict):
            rating_status.text = "The trade could not be rated."
            return
        rating_status.text = "Checking…"
        ctx = await run.io_bound(rating_context)
        if key != state["rating_key"]:
            return
        # allow_paper False: the Paper book line is ABSENT, not greyed.
        candidate = _detail.checklist_candidate(row, False)
        items = _checks_feed.checks_for(candidate, ctx)
        view = _rate_trade.banner_view(row, _checks.summary(items).get("state"), items)
        rating_status.text = ""
        rating_banner.clear()
        with rating_banner:
            with ui.row().classes("items-baseline gap-3 no-wrap"):
                ui.label(view["word"]).classes(
                    f"rate-word text-[28px] font-bold tracking-[.12em] {view['tone']}")
                ui.label(f"{view['grade']} · {view['score']}").classes(
                    f"rate-grade text-sm {_t.LABEL}")
            for reason in view["reasons"]:
                ui.label(reason).classes(f"rate-reason text-xs {_t.LABEL}")
            ui.label("Graded with the Strategy Finder's scorer and Go/No-Go checklist. "
                     "The word combines the two; it is not fitted to past outcomes.") \
                .classes(f"text-[10px] {_t.MUTED}")
        rating_panel.update(_strategy_table.detail_signal(row), candidate=candidate,
                            ctx=ctx)

    # ------------------------------------------------------------ the poll

    @guard_async
    async def _poll():
        for key in list(state["pending"]):
            job = state["pending"].get(key)
            if job is None:
                continue
            answer = await run.io_bound(bus_client.read, pt.answer_view(key))
            done, ans = answer_state(answer, job["since"])
            if not done:
                if time.monotonic() - job["t0"] > _wait_sec():
                    state["pending"].pop(key, None)
                    job["on_fail"](None, None)
                continue
            state["pending"].pop(key, None)
            outcome = (ans or {}).get("outcome")
            handler = job["on_done"] if outcome in job["ok"] else job["on_fail"]
            result = handler(outcome, ans)
            if hasattr(result, "__await__"):
                await result

    # ------------------------------------------------------------ wiring

    _mount(False)
    _seed()
    contracts_in.on_value_change(lambda e: _engage(_on_contracts))
    for w in (price_in, iv_in, rate_in, ivchg_in, nstrikes_in):
        w.on_value_change(lambda e: None if state["applying"]
                          else _engage(recalc.poke, time.monotonic()))
    # A typed IV is the visitor's own assumption: no implied value replaces it.
    iv_in.on_value_change(lambda e: None if state["applying"]
                          else state.__setitem__("iv_manual", True))

    ui.timer(POLL_SEC, _poll)
    ui.timer(0.1, _recalc_tick)

    @guard_async
    async def _load_default():
        # A page load never spends a request: it shows the default symbol's
        # chain if a visitor loaded it recently, and waits otherwise.
        chain = await run.io_bound(bus_client.read, pt.chain_view(DEFAULT_SYMBOL))
        if chain and not chain.get("no_options") and listed_expirations(chain):
            _apply_chain(chain)

    ui.timer(0.1, _load_default, once=True)


def _window():
    try:
        from shared import market_calendar
        start, end = market_calendar.window_bounds("tools_public")
        return {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    except Exception:  # noqa: BLE001 - the words, not the gate; the service decides
        return dict(DEFAULT_WINDOW)


def _wait_sec():
    return pt.limits()["max_wait_sec"] + _ALLOWANCE_SEC
