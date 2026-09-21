"""The PUBLIC Rescue form (``live.neuralstrike.co/rescue``).

A visitor loads a symbol, lays out a trade they hold elsewhere, and asks for
the ranked list of ways to repair it. Reached through
``rescue.render(public=True)``, which hands off here before the private page
builds anything. Blueprint: docs/plans/2026-09-21-public-rescue-adhoc-roadmap.md.

What the visitor uses is the private form's own machinery - the leg editor in
its ``row`` layout, ``adhoc_spec_from_legs`` to read the legs, and the private
page's card renderer for the answer - so a trade reads and is answered the same
on both origins. What they do NOT get:

* the owner's at-risk board, or any read of the owner's paper book;
* Apply, Paper, or any hand-off to another page - every card is advisory;
* a quoted chain: the strikes list carries no bid, ask or mark (decision D1),
  and the cards' per-leg fill prices follow ``public_scan.show_leg_quotes``,
  the same switch as the Finder (decision D2).

Three rules, and where each lives:

* **Every write goes through the two public request functions.** The page
  calls ``bus_client.request_public_ladder`` / ``request_public_rescue`` and
  nothing else that enqueues; a test pins it at source level.
* **A visitor reads only their own answers.** Each request has an answer key
  derived from the request itself (``public_rescue.request_key``); the page
  polls those keys and never a shared map of everyone's requests.
* **One visitor cannot fill the queue.** ``visitor_limit`` counts each
  visitor's strikes loads and rescues per hour before anything is sent.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

import bus_client
import visitor_limit
from nicegui import context, run, ui
from shared import public_rescue as pr
from shared import public_scan
from shared.symbols import clean_symbol

from pages import ui_kit as kit
from pages.ui_guard import guard, guard_async

from . import leg_editor
from . import strategies as _strategies
# The public chain's readers, shared with the public Calculator.
from .pub_chain_view import (ladder_strikes, listed_expirations,
                             loaded_expirations)
from .rescue import (RESCUE_ADHOC_SUPPORTED, adhoc_spec_from_legs,
                     candidate_card_rows, render_candidate_card, summary_line)
from .strategies import strategy_label
from .strategy_menu import build_strategy_menu
from .theme import MUTED

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
TITLE = "Rescue my Sh*tty trade"
DEFAULT_SYMBOL = "SPY"
POLL_SEC = 1.0
# How long a request is waited on: the longest it may sit in the queue plus a
# slow compute. The service answers every request it reads, so this fires only
# when the service is down.
_ALLOWANCE_SEC = 60
NO_ANSWER = ("No answer yet. The service may be busy or offline; please try "
             "again in a few minutes.")
REQUEST_FAILED = "The request could not be sent. Please try again later."
EMPTY_ADVICE = "Lay out your trade and press Compute to see ways to repair it."
LOAD_PROMPT = "Load a symbol to see its expirations and strikes."

LADDERS = visitor_limit.Limiter(pr.ladders_per_hour)
COMPUTES = visitor_limit.Limiter(pr.computes_per_hour)


# ── pure: what the page says ─────────────────────────────────────────────────

def intro_text(window) -> str:
    return ("Describe a trade you hold and get a ranked list of ways to repair "
            "it: roll it, widen it, close it, or leave it. The trade is priced "
            "against the live market; nothing is placed anywhere. Rescues run "
            f"{window['start']}–{window['end']} CT on trading days.")


def closed_line(open_now, window) -> str:
    if open_now:
        return ""
    return (f"Rescue is paused. It runs {window['start']}–{window['end']} CT on "
            "trading days.")


def _when(iso):
    try:
        when = dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def answer_state(answer, since):
    """``(done, outcome)`` for a request sent at ``since`` (aware datetime).
    An answer older than the request belongs to an earlier ask of the same
    thing, so it is not this one's."""
    ans = answer if isinstance(answer, dict) else {}
    at = _when(ans.get("at"))
    if at is not None and at >= since - dt.timedelta(seconds=2):
        return True, ans.get("outcome")
    return False, None


def outcome_text(outcome) -> str:
    return pr.OUTCOME_TEXT.get(outcome, pr.OUTCOME_TEXT["error"])


def computed_at_text(advisory):
    when = _when((advisory or {}).get("computed_at"))
    return None if when is None else f"Computed {when.astimezone(CT):%H:%M} CT"


def limit_text(kind) -> str:
    n = pr.computes_per_hour() if kind == "compute" else pr.ladders_per_hour()
    what = "rescues" if kind == "compute" else "symbol loads"
    return f"You have reached the limit of {n} {what} an hour. Please try again later."


# ── the page ─────────────────────────────────────────────────────────────────

def _visitor():
    try:
        return visitor_limit.client_key(context.client.request)
    except Exception:  # noqa: BLE001 - no request is one bucket, not a crash
        return "unknown"


def render():
    visitor = _visitor()
    window = _window()
    # ``pending`` maps an answer key to what to do when it lands.
    state = {"ladder": None, "symbol": None, "contracts": 1, "pending": {},
             "pending_move": None, "applying": False}

    with kit.page():
        kit.header(TITLE)
        ui.label(intro_text(window)).classes(f"text-sm {MUTED}")
        # Stacked below ``xl``, side by side from it: the public screens
        # are read on phones, where the private page's fixed two columns would
        # squeeze the leg table to nothing.
        with ui.element("div").classes(
                "w-full flex flex-col xl:flex-row gap-4 items-start"):
            with ui.column().classes("w-full min-w-0 xl:grow-[3] xl:basis-0 gap-3"):
                with kit.control_bar():
                    with kit.field("Strategy"):
                        strat = build_strategy_menu(
                            value="PCS", classes="w-52", boxed=True, caption=False,
                            exclude=_strategies.STOCK_STRATEGIES)
                    sym = kit.symbol_field(value=DEFAULT_SYMBOL, tab=False,
                                           on_load=lambda: _load(), width="w-40")
                    load_btn = kit.button("Load", kind="primary", icon="cloud_upload",
                                          on_click=lambda: _load())
                    exp_sel = kit.select_field("Expiry", [], width="w-44")
                    contracts = kit.number_field("Contracts", value=1, min=1,
                                                 max=pr.MAX_QTY, integer=True)
                status = kit.status_line(closed_line(_open_now(), window) or LOAD_PROMPT)
                ui.label("Premium is the price per share you received for a short "
                         "leg or paid for a long one.").classes(f"text-xs {MUTED}")
                # The row layout's fields are fixed widths; on a narrow screen
                # the table scrolls sideways rather than spilling off the page.
                leg_box = ui.column().classes("gap-2 w-full overflow-x-auto")
                compute_btn = kit.button("Compute rescue options", kind="primary",
                                         icon="healing", on_click=lambda: _compute())
            with ui.column().classes("w-full min-w-0 xl:grow-[2] xl:basis-0 gap-2"):
                head = kit.section_title(EMPTY_ADVICE)
                stamp = ui.label("").classes(f"text-xs {MUTED}")
                advice = kit.region("Building the rescue menu…")
                cards_col = advice.content

    # ------------------------------------------------------------ the legs

    editor = leg_editor.build_leg_editor(
        leg_box,
        strikes_for=lambda e, t: ladder_strikes(state["ladder"], e, t),
        expiries_for=lambda: loaded_expirations(state["ladder"]),
        show_premium=True, header=True,
        spot_getter=lambda: float((state["ladder"] or {}).get("spot") or 0.0),
        listed_expiries_for=lambda: listed_expirations(state["ladder"]),
        on_expiry_needed=lambda e: _fetch_expiry(e, move_all=False))

    def _seed():
        editor.apply_template(strat.value)
        _scale(max(1, int(contracts.value or 1)))
        if exp_sel.value and exp_sel.value in loaded_expirations(state["ladder"]):
            editor.apply_expiry(exp_sel.value)

    def _scale(factor):
        if factor == 1:
            return
        legs = editor.get_legs()
        for leg in legs:
            leg["qty"] = max(1, min(pr.MAX_QTY,
                                    round(int(leg.get("qty", 1) or 1) * factor)))
        editor.set_legs(legs)

    def _set_expiry(value):
        state["applying"] = True
        try:
            exp_sel.value = value
        finally:
            state["applying"] = False
        exp_sel.update()

    @guard
    def _on_strategy(_e=None):
        if strat.value not in RESCUE_ADHOC_SUPPORTED:
            kit.toast("warn", f"Rescue for '{strategy_label(strat.value)}' "
                              "is not available yet.")
        _seed()

    @guard
    def _on_expiry(_e=None):
        if state["applying"] or not exp_sel.value:
            return
        if exp_sel.value in loaded_expirations(state["ladder"]):
            state["pending_move"] = None
            editor.apply_expiry(exp_sel.value)
            return
        _fetch_expiry(exp_sel.value, move_all=True)

    @guard
    def _on_contracts(_e=None):
        new = max(1, min(pr.MAX_QTY, int(contracts.value or 1)))
        if new != state["contracts"]:
            _scale(new / state["contracts"])
        state["contracts"] = new

    strat.on_value_change(_on_strategy)
    exp_sel.on_value_change(_on_expiry)
    contracts.on_value_change(_on_contracts)
    _seed()

    # ------------------------------------------------------------- requests

    def _send(kind, key, fn, on_done, on_fail, ok=("done", "cached")):
        """Send one request unless this visitor is over their hourly limit;
        return whether it went. ``on_done(outcome)`` runs when the answer is one
        of ``ok``; ``on_fail(outcome)`` on anything else, or ``on_fail(None)``
        when no answer came in time."""
        limiter = COMPUTES if kind == "compute" else LADDERS
        if key in state["pending"]:
            return True                               # already waiting on it
        if not limiter.allow(visitor):
            status.text = limit_text(kind)
            return False
        try:
            fn()
        except Exception:  # noqa: BLE001 - worded for the visitor, logged for us
            log.warning("public rescue %s request failed", kind, exc_info=True)
            status.text = REQUEST_FAILED
            return False
        state["pending"][key] = {"since": dt.datetime.now(dt.timezone.utc),
                                 "t0": time.monotonic(), "on_done": on_done,
                                 "on_fail": on_fail, "ok": ok}
        return True

    @guard
    def _load():
        symbol = clean_symbol(sym.value)
        if symbol is None:
            kit.symbol_error(sym, "That is not a symbol this page can load.")
            return
        kit.symbol_error(sym, None)
        status.text = f"Loading {symbol}…"
        sent = _send("ladder", pr.ladder_key(symbol),
                     lambda: bus_client.request_public_ladder(symbol),
                     lambda outcome: _on_ladder(symbol, outcome),
                     lambda outcome: _ladder_refused(outcome),
                     ok=("done", "cached", "not_listed"))
        if sent:
            kit.set_busy(load_btn, True, timeout=_wait_sec())

    def _ladder_refused(outcome):
        kit.set_busy(load_btn, False)
        status.text = outcome_text(outcome) if outcome else NO_ANSWER

    async def _on_ladder(symbol, outcome):
        kit.set_busy(load_btn, False)
        ladder = await run.io_bound(bus_client.read, pr.ladder_view(symbol))
        if not ladder or ladder.get("no_options"):
            status.text = outcome_text("no_options")
            return
        _apply_ladder(ladder)
        if outcome == "not_listed":
            status.text = outcome_text(outcome)

    def _apply_ladder(ladder):
        same = state["symbol"] == ladder.get("symbol")
        state["ladder"], state["symbol"] = ladder, ladder.get("symbol")
        listed = listed_expirations(ladder)
        exp_sel.options = listed
        if listed and exp_sel.value not in listed:
            _set_expiry(listed[0])
        else:
            exp_sel.update()
        if same and editor.is_dirty():
            editor.refresh_options()
        else:
            _seed()
        loaded = loaded_expirations(ladder)
        status.text = (f"{ladder.get('symbol')}: {len(listed)} expirations, "
                       f"strikes for {len(loaded)} so far; the rest load when "
                       "you pick one.") if len(loaded) < len(listed) else \
            f"{ladder.get('symbol')}: {len(listed)} expirations."

    def _fetch_expiry(expiry, *, move_all):
        symbol = state["symbol"]
        if not symbol:
            return
        if move_all:
            state["pending_move"] = expiry
        status.text = f"Loading strikes for {expiry}…"
        sent = _send("ladder", pr.ladder_key(symbol, expiry),
                     lambda: bus_client.request_public_ladder(symbol, expiry),
                     lambda outcome: _on_expiry_loaded(symbol, expiry, outcome),
                     lambda outcome: _expiry_refused(expiry, outcome))
        if not sent:
            # Over the hourly limit, or the request could not be written: the
            # strikes are not coming, so the leg must not stay parked on an
            # expiration it has no strikes for. ``_send`` already said why.
            said = status.text
            _expiry_refused(expiry, None)
            status.text = said

    async def _on_expiry_loaded(symbol, expiry, outcome):
        ladder = await run.io_bound(bus_client.read, pr.ladder_view(symbol))
        if state["symbol"] != symbol:
            return                          # another symbol loaded meanwhile
        if not ladder:
            _expiry_refused(expiry, "error")
            return
        state["ladder"] = ladder
        if expiry not in loaded_expirations(ladder):
            _expiry_refused(expiry, outcome if outcome != "done" else "error")
            return
        if state["pending_move"] == expiry:
            state["pending_move"] = None
            editor.apply_expiry(expiry)
        else:
            editor.refresh_options()
        status.text = f"Strikes loaded for {expiry}."

    def _expiry_refused(expiry, outcome):
        """The strikes are not coming: put any leg parked on that expiration
        back on one that has strikes, before a placeholder strike is priced."""
        loaded = loaded_expirations(state["ladder"])
        if state["pending_move"] == expiry:
            state["pending_move"] = None
        if exp_sel.value == expiry:
            on = next((l.get("expiry") for l in editor.get_legs()
                       if l.get("expiry") in loaded), None)
            _set_expiry(on or (loaded[0] if loaded else None))
        legs = editor.get_legs()
        if loaded and any(l.get("expiry") == expiry for l in legs):
            for leg in legs:
                if leg.get("expiry") == expiry:
                    leg["expiry"] = loaded[0]
            editor.set_legs(legs)
        status.text = (f"Could not load strikes for {expiry}. "
                       + (outcome_text(outcome) if outcome else NO_ANSWER))

    @guard
    def _compute():
        if strat.value not in RESCUE_ADHOC_SUPPORTED:
            kit.toast("warn", f"Rescue for '{strategy_label(strat.value)}' "
                              "is not available yet.")
            return
        if state["ladder"] is None:
            status.text = LOAD_PROMPT
            return
        spec = adhoc_spec_from_legs(state["symbol"], editor.get_legs())
        if spec.get("error"):
            kit.toast("warn", spec["error"])
            return
        command = pr.compute_command(spec)
        if command is None:
            kit.toast("warn", pr.OUTCOME_TEXT["invalid"])
            return
        key = pr.request_key(command)
        sent = _send("compute", key,
                     lambda: bus_client.request_public_rescue(spec),
                     lambda outcome: _on_advice(key, spec["symbol"], outcome),
                     lambda outcome: _advice_refused(outcome))
        if sent:
            head.text = f"Computing rescue options for {spec['symbol']}…"
            stamp.text = ""
            cards_col.clear()
            advice.busy.show()
            kit.set_busy(compute_btn, True, timeout=_wait_sec())

    def _advice_refused(outcome):
        kit.set_busy(compute_btn, False)
        advice.busy.hide()
        head.text = outcome_text(outcome) if outcome else NO_ANSWER

    async def _on_advice(key, symbol, outcome):
        adv = await run.io_bound(bus_client.read, pr.result_view(key))
        kit.set_busy(compute_btn, False)
        advice.busy.hide()
        cards_col.clear()
        if not adv:
            head.text = outcome_text("error")
            return
        head.text = summary_line(adv)
        stamp.text = computed_at_text(adv) or ""
        cards = candidate_card_rows(adv, leg_prices=public_scan.show_leg_quotes())
        if adv.get("error") or not cards:
            with cards_col:
                kit.empty(adv.get("error") or "No rescue candidates available.")
            return
        for card in cards:
            render_candidate_card(cards_col, card)

    # ------------------------------------------------------------ the poll

    @guard_async
    async def _poll():
        for key in list(state["pending"]):
            job = state["pending"].get(key)
            if job is None:
                continue
            answer = await run.io_bound(bus_client.read, pr.answer_view(key))
            done, outcome = answer_state(answer, job["since"])
            if not done:
                if time.monotonic() - job["t0"] > _wait_sec():
                    state["pending"].pop(key, None)
                    job["on_fail"](None)
                continue
            state["pending"].pop(key, None)
            handler = job["on_done"] if outcome in job["ok"] else job["on_fail"]
            result = handler(outcome)
            if hasattr(result, "__await__"):
                await result

    ui.timer(POLL_SEC, _poll)

    @guard_async
    async def _load_default():
        # A page load never spends a request: it shows the default symbol's
        # strikes if a visitor loaded them recently, and waits otherwise.
        ladder = await run.io_bound(bus_client.read, pr.ladder_view(DEFAULT_SYMBOL))
        if ladder and not ladder.get("no_options") and listed_expirations(ladder):
            _apply_ladder(ladder)

    ui.timer(0.1, _load_default, once=True)


def _window():
    try:
        from shared import market_calendar
        start, end = market_calendar.window_bounds("rescue_public")
        return {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    except Exception:  # noqa: BLE001 - the words, not the gate; the service decides
        return {"start": "08:40", "end": "15:00"}


def _open_now() -> bool:
    try:
        from shared import market_calendar
        return market_calendar.in_window("rescue_public", dt.datetime.now(CT))
    except Exception:  # noqa: BLE001 - the words, not the gate; the service decides
        return True


def _wait_sec():
    return pr.limits()["max_wait_sec"] + _ALLOWANCE_SEC
