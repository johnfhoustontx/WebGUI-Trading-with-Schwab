"""The PUBLIC Simulator (``live.neuralstrike.co/simulator``) - Price & Time only.

A visitor loads a symbol, builds a position in the leg table, and sees how its
value moves with the underlying price and the days ahead: the what-if chart,
the four position tiles (Delta and Theta are left out, below) and the readout line. Reached through
``simulator.render(public=True)``, which hands off here before the private page
builds anything. Design:
docs/plans/2026-09-21-public-calculator-simulator-design.md, section 3.

What the visitor uses is the private page's own machinery - the shared entry
panel (with no chain grid), the leg editor's ``table`` layout, and
``simulator.whatif_figure`` / ``sim_view``'s readouts - so a position reads the
same on both origins. What they do NOT get: the tab strip, the Volatility tab
(no multiplier, no IV-shock table), History (no replay), the Calculator
hand-offs, and any restoring of an earlier visit.

The rules, and where each lives:

* **Every write goes through the two public request functions.** The page
  calls ``bus_client.request_public_tool`` / ``request_public_math`` and
  nothing else that enqueues; a test pins it at source level.
* **A visitor reads only their own answers.** Each request's answer and result
  keys are derived from the request itself (``public_tools.request_key``).
* **One visitor cannot fill either queue.** The Calculator's two
  ``visitor_limit`` counters, shared (``TOOLS`` / ``MATH`` below).
* **A page load spends nothing.** Only a visitor's action sends - including
  arriving from the Calculator with a position, which seeds this page once.
* **An older answer never paints over a newer request** (``sweep_key``).

The hand-off is READ-only here: the page reads ``public_handoff`` once, after
the client connects, and never writes it.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import bus_client
from nicegui import context, run, ui
from shared import public_tools as pt
from shared.symbols import clean_symbol

from pages import ui_kit as kit
from pages.ui_guard import guard, guard_async

from . import calc_live as _calc_live
from . import entry as _entry
from . import entry_panel
from . import leg_editor
from . import public_handoff
from . import sim_view as sv
from . import simulator as _sim
from . import strategies
from . import theme as _t
from .pub_chain_view import (LEG_SCROLL, LEG_TABLE_MIN, ladder_strikes,
                             snapshot_listed, snapshot_loaded)

log = logging.getLogger(__name__)

TITLE = "Simulator"
DEFAULT_STRATEGY = "PCS"
POLL_SEC = 1.0
#: Quiet time after the last edit or slider step before a sweep is asked for -
#: the private page's 0.4 s flush.
SWEEP_DELAY_SEC = 0.4
#: How long the hand-off read waits for the browser's socket.
CONNECT_TIMEOUT_SEC = 30.0
DEFAULT_DAYS = 5.0
LOAD_PROMPT = "Load a symbol to see how a position's value changes."
SHARES_DROPPED = "Share legs aren't simulated here."

#: Shared with the public Calculator: one visitor, one hourly allowance of
#: loads and one of price updates, whichever page they are on.
TOOLS = _calc_live.TOOLS
MATH = _calc_live.MATH

outcome_text = _calc_live.outcome_text
limit_text = _calc_live.limit_text
NO_ANSWER = _calc_live.NO_ANSWER
REQUEST_FAILED = _calc_live.REQUEST_FAILED
BAD_SYMBOL = _calc_live.BAD_SYMBOL

# Tile tone -> fixed classes (the private page's map).
_TONE_CLASS = _sim._TONE_CLASS
_TONE_REMOVE = _sim._TONE_REMOVE
_TILE = _sim._TILE
_TILE_VALUE = _sim._TILE_VALUE

#: Tiles this page does not draw. Delta and Theta read the greeks, and the
#: public sweep carries none (``tools_public._math_sweep`` keeps the what-if
#: half only): publishing them is delta exposure the owner has not approved.
#: ``sim_view.position_tiles`` stays six for the private page.
_OMIT = {"delta", "theta"}


# ── pure ─────────────────────────────────────────────────────────────────────

def intro_text(window) -> str:
    return ("See how an options position's value changes with the underlying price "
            "and the days ahead, priced from the live option chain. Nothing is "
            f"placed anywhere. Loading runs {window['start']}–{window['end']} CT on "
            "trading days.")


def _is_stock(leg):
    return str((leg or {}).get("option_type") or "").lower() == "stock"


def public_tiles(legs, result):
    """``sim_view.position_tiles`` without the ``_OMIT`` tiles - used by the
    build AND every repaint, so the two cannot disagree about which exist."""
    return [t for t in sv.position_tiles(legs, result) if t["key"] not in _OMIT]


def missing_expiries(legs, meta):
    """The OPTION legs' expirations the snapshot holds no strikes for, nearest
    first. A sweep over one would be refused ``off_ladder``."""
    loaded = set(snapshot_loaded(meta))
    return sorted({str(leg["expiry"]) for leg in legs or []
                   if not _is_stock(leg) and leg.get("expiry")
                   and str(leg["expiry"]) not in loaded})


def split_seed(legs):
    """``(option_legs, dropped_share_count)`` from hand-off legs. The Simulator's
    engines have no share concept, so a share leg is dropped, and counted so the
    page can say so."""
    options = [dict(leg) for leg in legs or [] if not _is_stock(leg)]
    return options, sum(1 for leg in legs or [] if _is_stock(leg))


def leg_expiries(legs, limit: int | None = pt.MAX_SNAPSHOT_EXPIRIES):
    """The distinct expirations of the OPTION legs, nearest first, at most
    ``limit`` (None: all) - what a snapshot request asks to bring back."""
    exps = sorted({str(leg["expiry"]) for leg in legs or []
                   if not _is_stock(leg) and leg.get("expiry")})
    return exps[:limit]


def seed_ladder(legs):
    """A snapshot-meta-shaped ladder built from the seeded legs alone, so the
    leg table can show a Calculator position before its snapshot lands (the
    editor clears a strike not on its ladder)."""
    exps = leg_expiries(legs, limit=None)
    strikes = {e: {"call": [], "put": []} for e in exps}
    for leg in legs or []:
        k, e, side = leg.get("strike"), leg.get("expiry"), leg.get("option_type")
        if e in strikes and side in ("call", "put") and isinstance(k, (int, float)) \
                and not isinstance(k, bool) and k not in strikes[e][side]:
            strikes[e][side].append(float(k))
    for per in strikes.values():
        for side in per:
            per[side].sort()
    return {"expiries": exps, "expirations": exps, "strikes": strikes}


def sim_legs(legs):
    """Editor legs -> the Simulator's leg shape (``kind``, not ``option_type``),
    or None when any OPTION leg has no strike or expiration yet. Share legs
    never reach this page, and would be skipped."""
    if not leg_editor.legs_ready(legs):
        return None
    out = []
    for leg in legs:
        if _is_stock(leg):
            continue
        out.append({"kind": leg["option_type"], "strike": float(leg["strike"]),
                    "expiry": leg["expiry"], "side": leg["side"],
                    "qty": int(leg.get("qty", 1) or 1)})
    return out or None


def sweep_request(symbol, legs, days, max_days):
    """A ``sweep`` request for these legs at ``days`` ahead (clamped into
    ``[0, max_days]`` - the service refuses days past every leg's expiry), or
    None when the legs are not complete or the builder refuses it."""
    sent = sim_legs(legs)
    if not symbol or sent is None:
        return None
    try:
        d = min(max(float(days or 0.0), 0.0), float(max_days), pt.MAX_SWEEP_DAYS)
    except (TypeError, ValueError):
        return None
    req = {"kind": "sweep", "symbol": symbol, "dt": d, "legs": sent}
    return req if pt.math_command(req) is not None else None


def _at(answer):
    return _calc_live._when((answer or {}).get("at")) if isinstance(answer, dict) else None


def answer_state(answer, since, after=None):
    """``(done, answer)`` - the Calculator's rule, plus ``after``: the time of
    the last answer this page already consumed for the same key. A request sent
    again on that key (the sweep after an automatic reload) must not take the
    OLD answer, which the two-second allowance would otherwise accept."""
    done, ans = _calc_live.answer_state(answer, since)
    if done and after is not None:
        at = _at(ans)
        if at is None or at <= after:
            return False, None
    return done, ans


# ── the page ─────────────────────────────────────────────────────────────────

def _clock():
    return time.monotonic()


async def _client_connected():
    """Wait for this page's socket: ``app.storage.tab`` raises until then."""
    await context.client.connected(timeout=CONNECT_TIMEOUT_SEC)


def render():
    visitor = _calc_live._visitor()
    window = _calc_live._window()
    state: dict = {
        "meta": None,           # the landed snapshot's public meta
        "ladder": None,         # what the leg table reads: meta, or a seed ladder
        "symbol": None,         # the symbol the snapshot belongs to
        "loading": None,        # the symbol a snapshot request is out for
        "pending_legs": None,   # legs to put on the table when a snapshot lands
        "pending_move": None,   # an expiry the legs move to once it loads
        "pending": {},          # answer key -> what to do when it lands
        "answered": {},         # answer key -> time of the last answer consumed
        "sweep_key": None,      # the newest sweep request for the position
        "shown_key": None,      # the sweep whose result is on screen
        "result": None,         # that result, with the symbol it was asked for
        "reloaded": False,      # an automatic reload since the last action
        # False until the visitor's first action - arriving with a Calculator
        # position counts - so a page load sends nothing at all.
        "engaged": False,
        "applying": False,
        "days_key": None,
        "seeded": None,         # the symbol a Calculator position arrived with
    }
    sweep_due = _entry.Debounce(SWEEP_DELAY_SEC)
    tile_refs: dict = {}

    with kit.page():
        kit.header(TITLE)
        ui.label(intro_text(window)).classes(f"text-sm {_t.MUTED}")
        status = kit.status_line(LOAD_PROMPT)
        # ⚠ No chain grid: the Simulator prices each leg off the snapshot's IV
        # and never shows a quote. The three share structures are excluded, as
        # on the private page: the engines have no share concept.
        panel = entry_panel.build_entry_panel(
            strategy_value=DEFAULT_STRATEGY, grid=False, stack=True,
            strategy_exclude=strategies.STOCK_STRATEGIES)
        with panel.legs_footer:
            shares_note = ui.label(SHARES_DROPPED).classes(
                f"sim-shares-note text-xs {_t.MUTED}")
            shares_note.set_visibility(False)
            warn_box = ui.column().classes("gap-1 w-full")

        with ui.column().classes(f"{_t.CARD} w-full gap-2"):
            ui.label("Position").classes(_t.EYEBROW)
            with ui.element("div").classes(
                    "grid grid-cols-2 md:grid-cols-4 gap-2 w-full"):
                for tile in public_tiles([], None):
                    with ui.column().classes(f"sim-tile {_TILE} gap-0.5"):
                        t_lbl = ui.label(tile["label"]).classes(_t.EYEBROW)
                        t_val = ui.label(tile["value"]).classes(
                            f"{_TILE_VALUE} {_TONE_CLASS['neutral']}")
                        t_sub = ui.label(tile["sub"]).classes(f"{_t.MUTED} text-xs")
                    tile_refs[tile["key"]] = (t_lbl, t_val, t_sub)

        with ui.column().classes(f"{_t.CARD} w-full min-w-0 gap-2"):
            kit.section_title("Price & Time")
            with ui.row().classes("items-center gap-4 w-full flex-wrap"):
                ds_lbl = ui.label("Price change: 0%").tooltip(
                    "Move the stock or index price up or down by this percent")
                ds_slider = ui.slider(min=-20, max=20, value=0).classes("w-48 sim-price")
                dt_lbl = ui.label(f"Time passed: {sv.days_text(DEFAULT_DAYS)}").tooltip(
                    "Calendar time forward from now — each leg loses that much time value")
                dt_slider = ui.slider(min=0, max=30, value=DEFAULT_DAYS) \
                    .classes("w-48 sim-days")
                snap_row = ui.row().classes("items-center gap-1 sim-snaps")
            readout_lbl = ui.label("").classes(
                f"sim-readout text-subtitle1 font-semibold {_TONE_CLASS['neutral']}")
            whatif_empty = kit.empty(LOAD_PROMPT).classes("sim-whatif-empty")
            # Built ONCE, at page build (a chart added later would miss the ESM
            # import map), with the figure's explicit height, and updated in
            # place. It mounts hidden until a result lands, so revealing it
            # reflows it (``_show_chart``).
            whatif_chart = ui.highchart(_sim.whatif_figure([], 0)) \
                .classes("sim-whatif w-full")
            # Where the one-tick reflow timer is built: a repaint can run from an
            # awaited callback, whose task carries no slot of its own.
            timer_host = ui.element("div").classes("hidden")

    # ------------------------------------------------------------ the legs

    def _strikes_for(expiry, otype):
        return ladder_strikes(state["ladder"], expiry, otype)

    # On a phone the table keeps a readable width and scrolls inside its own
    # box rather than squeezing its dropdowns (pub_chain_view.LEG_TABLE_MIN).
    with panel.legs_box, ui.element("div").classes(LEG_SCROLL):
        leg_table = ui.column().classes(f"{LEG_TABLE_MIN} w-full gap-1")
    editor = leg_editor.build_leg_editor(
        leg_table, strikes_for=_strikes_for,
        expiries_for=lambda: snapshot_loaded(state["ladder"]),
        listed_expiries_for=lambda: snapshot_listed(state["ladder"]),
        on_expiry_needed=lambda e: _fetch_expiry(e, move_all=False),
        show_premium=False, on_change=lambda: _on_legs_changed(),
        spot_getter=lambda: float(((state["meta"] or {}).get("spot")) or 0.0),
        layout="table", delta_for=None, min_legs=1)

    def _engage():
        state["engaged"] = True
        state["reloaded"] = False           # a new action may reload once again

    def _on_legs_changed():
        _engage()                           # only the visitor edits the legs
        _after_legs()

    def _after_legs():
        _paint_structure()
        _apply_days_range()
        _repaint()
        sweep_due.poke(_clock())

    def _paint_structure():
        warn_box.clear()
        with warn_box:
            for w in sv.structure_warnings(editor.get_legs()):
                with ui.row().classes("items-start gap-1 no-wrap sim-warning"):
                    ui.icon("warning").classes(f"{_t.TXT_WARN} text-base")
                    ui.label(w).classes(f"{_t.TXT_WARN} text-xs")

    def _apply_days_range():
        """Fit the Days slider to the legs (``ui.slider`` keeps min/max/step in
        ``_props``; an attribute assignment never reaches the browser)."""
        r = sv.days_range(editor.get_legs())
        key = (r["max"], r["step"], tuple(r["snaps"]))
        if key == state["days_key"]:
            return
        state["days_key"] = key
        dt_slider._props["max"] = r["max"]
        dt_slider._props["step"] = r["step"]
        dt_slider.update()
        if float(dt_slider.value or 0) > r["max"]:
            state["applying"] = True
            try:
                dt_slider.value = r["max"]
            finally:
                state["applying"] = False
        snap_row.clear()
        # QUIET kit buttons: a stepper for the slider beside them, not page
        # actions. (The private page keeps raw ones under the ui-kit guard's
        # written exception; this page is not one, and needs none.)
        with snap_row:
            for label, days in r["snaps"]:
                kit.button(label, kind="quiet",
                           on_click=lambda e, d=days: _snap_days(d)).classes("sim-snap")

    @guard
    def _snap_days(days):
        dt_slider.value = days              # fires on_value_change -> a sweep

    def _days_max():
        return sv.days_range(editor.get_legs())["max"]

    def _set_legs(legs):
        state["applying"] = True
        try:
            editor.set_legs(legs)
            code = sv.template_for(legs)
            if code and code != panel.strategy_sel.value:
                panel.strategy_sel.value = code
        finally:
            state["applying"] = False

    # ------------------------------------------------------------ the readouts

    def _set_tone(el, tone):
        el.classes(remove=_TONE_REMOVE, add=_TONE_CLASS.get(tone, _TONE_CLASS["neutral"]))

    def _for_screen():
        """The shown result, only while it was priced for the symbol and legs on
        screen (its own days may lag the slider mid-drag: the readout says
        which)."""
        res = state["result"]
        if not res or state["meta"] is None:
            return None
        return res if sv.result_matches(res, state["symbol"],
                                        sim_legs(editor.get_legs()) or []) else None

    _chart_shown = {"on": None}

    @guard
    def _reflow_chart():
        ui.run_javascript(f"getElement({whatif_chart.id})?.chart?.reflow()")

    def _show_chart(visible):
        whatif_chart.set_visibility(visible)
        was, _chart_shown["on"] = _chart_shown["on"], visible
        if visible and was is not True:
            # One tick after the browser lays the element out (simulator.py).
            with timer_host:
                ui.timer(0.05, _reflow_chart, once=True)

    def _repaint():
        ds_lbl.text = f"Price change: {ds_slider.value:+g}%"
        dt_lbl.text = f"Time passed: {sv.days_text(dt_slider.value)}"
        legs = editor.get_legs()
        result = _for_screen()
        for tile in public_tiles(legs, result):
            lbl, val, sub = tile_refs[tile["key"]]
            lbl.text, val.text, sub.text = tile["label"], tile["value"], tile["sub"]
            _set_tone(val, tile["tone"])
        if not result:
            # Listed but not held: its strikes are on their way (or can be).
            waiting = ([e for e in missing_expiries(legs, state["meta"])
                        if e in snapshot_listed(state["meta"])]
                       if state["meta"] is not None else [])
            if state["meta"] is None:
                whatif_empty.text = LOAD_PROMPT
            elif waiting:
                whatif_empty.text = ("Loading strikes for "
                                     + ", ".join(map(_entry.expiry_label, waiting))
                                     + "…")
            else:
                whatif_empty.text = sv.empty_state_text(state["meta"], legs)
            whatif_empty.set_visibility(True)
            _show_chart(False)
            readout_lbl.text = ""
            return
        whatif_empty.set_visibility(False)
        _show_chart(True)
        spot = result.get("spot")
        # The price offset is drawn HERE, never asked for.
        offset = float(ds_slider.value or 0.0)
        target = spot * (1 + offset / 100.0) if isinstance(spot, (int, float)) else None
        rows = result.get("whatif_rows") or []
        baseline = result.get("whatif_baseline")
        whatif_chart.options = _sim.whatif_figure(rows, spot, target, baseline=baseline)
        whatif_chart.update()
        priced = result.get("dt")
        days = priced if isinstance(priced, (int, float)) else dt_slider.value
        text, tone = sv.whatif_readout(_sim.whatif_pnl(rows, spot, baseline), target,
                                       days, legs=legs)
        readout_lbl.text = text
        _set_tone(readout_lbl, tone)

    # ------------------------------------------------------------ requests

    def _send(kind, key, fn, on_done, on_fail, ok=("done", "cached")):
        """The Calculator's ``_send``: one request unless this visitor is over
        their hourly limit, never a second while one is waiting on the key."""
        limiter = MATH if kind == "math" else TOOLS
        if key is None:
            status.text = outcome_text("invalid")
            return False
        if key in state["pending"]:
            return True                               # already waiting on it
        if not limiter.allow(visitor):
            status.text = limit_text(kind)
            return False
        try:
            fn()
        except ValueError:
            status.text = outcome_text("invalid")
            return False
        except Exception:  # noqa: BLE001 - worded for the visitor, logged for us
            log.warning("public simulator %s request failed", kind, exc_info=True)
            status.text = REQUEST_FAILED
            return False
        state["pending"][key] = {"since": dt.datetime.now(dt.timezone.utc),
                                 "t0": time.monotonic(), "on_done": on_done,
                                 "on_fail": on_fail, "ok": ok,
                                 "after": state["answered"].get(key)}
        return True

    # ---- a snapshot

    def _snapshot(symbol, expiries, *, keep_legs):
        """Ask for ``symbol``'s snapshot, bringing ``expiries`` (at most two)
        with it. ``keep_legs`` puts the given legs back on the table when it
        lands; otherwise the strategy's template is laid on it."""
        req = {"kind": "sim_snapshot", "symbol": symbol}
        if expiries:
            req["expiries"] = list(expiries)[:pt.MAX_SNAPSHOT_EXPIRIES]
        command = pt.tools_command(req)
        key = pt.request_key(command) if command else None
        state["loading"] = symbol
        state["pending_legs"] = keep_legs
        status.text = f"Loading {symbol}…"
        sent = _send("tools", key, lambda: bus_client.request_public_tool(req),
                     lambda outcome, _a: _on_snapshot(symbol, key),
                     lambda outcome, _a: _snapshot_refused(symbol, outcome))
        if not sent and state["loading"] == symbol:
            state["loading"] = None         # nothing is coming: don't block sweeps
            state["pending_legs"] = None
        if sent:
            kit.set_busy(panel.refresh_btn, True, timeout=_calc_live._wait_sec())
        return sent

    @guard
    def _load():
        _engage()
        symbol = clean_symbol(panel.symbol_in.value)
        if symbol is None:
            kit.symbol_error(panel.symbol_in, BAD_SYMBOL)
            return
        kit.symbol_error(panel.symbol_in, None)
        legs = editor.get_legs()
        # The legs stay when they are the visitor's own for this symbol: edited
        # on its snapshot, or the Calculator position they arrived with - landed
        # or not (``_set_legs`` leaves the editor unedited, so ``is_dirty`` alone
        # would lay the template over it).
        mine = ((symbol == state["symbol"] and editor.is_dirty())
                or symbol == state["seeded"])
        keep = legs if mine and legs else None
        _snapshot(symbol, leg_expiries(keep) if keep else None, keep_legs=keep)

    def _snapshot_refused(symbol, outcome):
        kit.set_busy(panel.refresh_btn, False)
        if state["loading"] == symbol:
            state["loading"] = None
        status.text = outcome_text(outcome) if outcome else NO_ANSWER

    async def _on_snapshot(symbol, key):
        kit.set_busy(panel.refresh_btn, False)
        meta = await run.io_bound(bus_client.read, pt.result_view(key))
        legs_to_keep = state["pending_legs"]
        if state["loading"] != symbol:
            return                          # another symbol was asked for since
        state["loading"] = None
        state["pending_legs"] = None
        if not isinstance(meta, dict) or not snapshot_loaded(meta):
            # An answer with nothing to read (its result expired, a reload that
            # came back empty) is not "no options": the service says that as an
            # outcome. What is on screen stays.
            status.text = (outcome_text("no_options")
                           if isinstance(meta, dict) and state["symbol"] != symbol
                           else NO_ANSWER)
            return
        if symbol != state["seeded"]:
            state["seeded"] = None          # another symbol: that position is gone
        same = state["symbol"] == symbol
        state["meta"], state["ladder"], state["symbol"] = meta, meta, symbol
        state["pending_move"] = None
        panel.symbol_in.value = symbol
        spot = meta.get("spot")
        spot_txt = f"{spot:,.2f}" if isinstance(spot, (int, float)) else "—"
        panel.spot_lbl.text = spot_txt
        listed = snapshot_listed(meta)
        panel.set_chain(None, spot if isinstance(spot, (int, float)) else None,
                        expirations=listed)
        pending = legs_to_keep
        if pending:
            _set_legs(pending)
        elif same and editor.is_dirty():
            editor.refresh_options()
        else:
            editor.apply_template(panel.strategy_sel.value, near=panel.selected_expiry())
        loaded = snapshot_loaded(meta)
        status.text = (f"{symbol} spot {spot_txt}: {len(listed)} expirations, strikes "
                       f"for {len(loaded)} so far; the rest load when you pick one."
                       if len(loaded) < len(listed)
                       else f"{symbol} spot {spot_txt}: {len(listed)} expirations.")
        _after_legs()
        for expiry in missing_expiries(editor.get_legs(), meta):
            if expiry in listed:
                _fetch_expiry(expiry, move_all=False)

    # ---- one more expiration

    @guard
    def _on_expiry(expiry):
        if state["meta"] is None:
            return
        _engage()
        if expiry in snapshot_loaded(state["meta"]):
            state["pending_move"] = None
            editor.apply_expiry(expiry)     # fires on_change
            return
        _fetch_expiry(expiry, move_all=True)

    def _fetch_expiry(expiry, *, move_all):
        symbol = state["symbol"]
        if not symbol or state["meta"] is None:
            return
        if move_all:
            state["pending_move"] = expiry
        req = {"kind": "sim_expiry", "symbol": symbol, "expiry": expiry}
        command = pt.tools_command(req)
        key = pt.request_key(command) if command else None
        status.text = f"Loading strikes for {_entry.expiry_label(expiry)}…"
        sent = _send("tools", key, lambda: bus_client.request_public_tool(req),
                     lambda outcome, _a: _on_expiry_loaded(symbol, expiry, key),
                     lambda outcome, _a: _expiry_refused(expiry, outcome))
        if not sent:
            said = status.text
            _expiry_refused(expiry, None)
            status.text = said

    async def _on_expiry_loaded(symbol, expiry, key):
        meta = await run.io_bound(bus_client.read, pt.result_view(key))
        if state["symbol"] != symbol:
            return
        if not isinstance(meta, dict) or expiry not in snapshot_loaded(meta):
            _expiry_refused(expiry, "error")
            return
        state["meta"] = state["ladder"] = meta
        panel.set_chain(None, meta.get("spot"), expiry=panel.selected_expiry(),
                        expirations=snapshot_listed(meta))
        if state["pending_move"] == expiry:
            state["pending_move"] = None
            editor.apply_expiry(expiry)     # fires on_change -> a sweep
        else:
            editor.refresh_options()
            _after_legs()
        status.text = f"Strikes loaded for {_entry.expiry_label(expiry)}."

    def _expiry_refused(expiry, outcome):
        """The strikes are not coming: the strip and any leg parked on that
        expiration go back to one that has strikes."""
        if outcome == "load_first" and _reload():
            return
        loaded = snapshot_loaded(state["meta"])
        if state["pending_move"] == expiry:
            state["pending_move"] = None
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

    # ---- the snapshot went (evicted, expired, a restart)

    def _reload():
        """Ask for the snapshot again, ONCE per visitor action, with the legs'
        expirations, keeping the legs. Returns whether it went."""
        if state["reloaded"] or not state["symbol"]:
            return False
        state["reloaded"] = True
        legs = editor.get_legs()
        return _snapshot(state["symbol"], leg_expiries(legs), keep_legs=legs)

    # ---- the sweep

    @guard
    def _sweep_tick():
        if sweep_due.ready(_clock()):
            _sweep()

    def _sweep():
        if not state["engaged"] or state["meta"] is None or state["loading"]:
            return
        legs = editor.get_legs()
        if missing_expiries(legs, state["meta"]):
            _repaint()                      # "Loading strikes for ..." - no request
            return
        req = sweep_request(state["symbol"], legs, dt_slider.value, _days_max())
        if req is None:
            _repaint()                      # the empty line says what is missing
            return
        key = pt.request_key(pt.math_command(req))
        # Recorded before the early returns: an answer to an edit the visitor
        # has since undone must not paint over the position on screen.
        state["sweep_key"] = key
        if key == state["shown_key"] or key in state["pending"]:
            return
        symbol = state["symbol"]
        _send("math", key, lambda: bus_client.request_public_math(req),
              lambda outcome, _a: _on_sweep(key, symbol),
              lambda outcome, _a: _sweep_refused(key, outcome))

    async def _on_sweep(key, symbol):
        result = await run.io_bound(bus_client.read, pt.result_view(key))
        if key != state["sweep_key"]:
            return                          # a newer position or day is on its way
        if not isinstance(result, dict):
            status.text = outcome_text("error")
            return
        # The public result echoes its legs but not its symbol; the request did.
        state["result"] = {**result, "symbol": symbol}
        state["shown_key"] = key
        _repaint()

    def _sweep_refused(key, outcome):
        if key != state["sweep_key"]:
            return
        if outcome == "load_first" and _reload():
            return
        status.text = outcome_text(outcome) if outcome else NO_ANSWER

    # ------------------------------------------------------------ the poll

    @guard_async
    async def _poll():
        for key in list(state["pending"]):
            job = state["pending"].get(key)
            if job is None:
                continue
            answer = await run.io_bound(bus_client.read, pt.answer_view(key))
            done, ans = answer_state(answer, job["since"], job["after"])
            if not done:
                if time.monotonic() - job["t0"] > _calc_live._wait_sec():
                    state["pending"].pop(key, None)
                    job["on_fail"](None, None)
                continue
            state["pending"].pop(key, None)
            at = _at(ans)
            if at is not None:
                state["answered"][key] = at
                while len(state["answered"]) > 64:
                    state["answered"].pop(next(iter(state["answered"])))
            outcome = (ans or {}).get("outcome")
            handler = job["on_done"] if outcome in job["ok"] else job["on_fail"]
            result = handler(outcome, ans)
            if hasattr(result, "__await__"):
                await result

    # ------------------------------------------------------------ the seed

    @guard_async
    async def _seed_from_handoff():
        """Arriving from the Calculator: its position, and ONE snapshot request
        for that symbol. Nothing stored means a template and nothing sent."""
        try:
            await _client_connected()
        except Exception:  # noqa: BLE001 - no socket: nothing to seed from
            return
        seed = public_handoff.read()
        if not seed:
            return
        symbol, legs = seed
        options, dropped = split_seed(legs)
        shares_note.set_visibility(dropped > 0)
        state["engaged"] = True             # arriving with a position is an action
        panel.symbol_in.value = symbol
        state["seeded"] = symbol
        if options:
            state["ladder"] = seed_ladder(options)
            _set_legs(options)
            _paint_structure()
            _apply_days_range()
            _repaint()
        _snapshot(symbol, leg_expiries(options), keep_legs=options or None)

    # ------------------------------------------------------------ wiring

    editor.apply_template(DEFAULT_STRATEGY)
    _paint_structure()
    _apply_days_range()
    _repaint()
    panel.refresh_btn.on_click(lambda: _load())
    panel.symbol_in.on("keydown.enter", lambda e: _load())
    panel.strategy_sel.on_value_change(
        lambda e: None if state["applying"] else _on_strategy())
    panel.on_expiry(_on_expiry)

    @guard
    def _on_strategy():
        _engage()
        editor.apply_template(panel.strategy_sel.value, near=panel.selected_expiry())
        _after_legs()

    @guard
    def _on_days(_e=None):
        if state["applying"]:
            return
        _engage()
        _repaint()
        sweep_due.poke(_clock())

    # The price offset redraws the overlay and sends NOTHING.
    ds_slider.on_value_change(lambda e: _repaint())
    dt_slider.on_value_change(_on_days)

    ui.timer(POLL_SEC, _poll)
    ui.timer(0.1, _sweep_tick)
    ui.timer(0.1, _seed_from_handoff, once=True)
