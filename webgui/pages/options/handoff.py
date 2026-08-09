"""Cross-page signal handoff + right-click actions for signal tables.

Lets the Scanner / Swing Scanner send a selected signal to the Calculator
(prefill) or create a Paper Trade from it — mirroring the legacy right-click
menu. Single-user app, so a module-level stash is fine for the calculator
hand-off across the page navigation.

Paper-trade creation no longer imports the options engine: it enqueues a
``paper_create`` command on the options service bus (Tier 2 → Tier 3), mirroring
the other migrated pages — so this module is fully engine-free.
"""
from nicegui import ui

import bus_client

from .theme import BTN_3D

_pending = {"calculator": None, "expected_move": None,
            "simulator": None, "calculator_legs": None, "gamma": None}


# Per signal-type: list of (field_name, option_type, side) for the strike legs.
_EM_LEG_FIELDS = {
    "PCS":        [("short_strike", "put",  "short"), ("long_strike", "put",  "long")],
    "CCS":        [("short_strike", "call", "short"), ("long_strike", "call", "long")],
    "IC":         [("short_strike", "put",  "short"), ("long_strike", "put",  "long"),
                   ("call_short",   "call", "short"), ("call_long",   "call", "long")],
    "LONG_PUT":   [("long_strike",  "put",  "long")],
    "NAKED_PUT":  [("short_strike", "put",  "short")],
    "LONG_CALL":  [("long_strike",  "call", "long")],
    "NAKED_CALL": [("short_strike", "call", "short")],
}


def _legs_from_fields(sig, specs):
    legs = []
    for field, otype, side in specs:
        v = sig.get(field)
        if v in (None, 0, ""):
            continue
        try:
            legs.append({"strike": float(v), "option_type": otype, "side": side})
        except (TypeError, ValueError):
            continue
    return legs


def signal_to_em_payload(signal):
    """Normalize a scanner/captured/paper signal dict to {symbol, expiry, legs}.

    Prefers a normalized multi-leg ``legs`` list when present (the new
    multi-strategy swing signals); falls back to the legacy per-field strike specs
    (``_EM_LEG_FIELDS``) for the old single-shape spread dicts without ``legs``."""
    sig = signal or {}
    symbol = (sig.get("symbol") or "").replace("$", "").upper()
    expiry = sig.get("expiration") or sig.get("expiry")
    if sig.get("legs"):
        legs = [{"strike": l.get("strike"), "option_type": l.get("kind"), "side": l.get("side")}
                for l in sig["legs"] if l.get("strike") not in (None, 0, "")]
        return {"symbol": symbol, "expiry": expiry, "legs": legs}
    specs = _EM_LEG_FIELDS.get(sig.get("type"), [])
    return {"symbol": symbol, "expiry": expiry, "legs": _legs_from_fields(sig, specs)}


def set_pending_expected_move(payload):
    _pending["expected_move"] = payload


def take_pending_expected_move():
    p = _pending.get("expected_move")
    _pending["expected_move"] = None
    return p


def send_to_expected_move(payload):
    """Stash the payload and open the Expected Move page in a NEW browser tab."""
    if not payload or not payload.get("symbol"):
        ui.notify("No symbol for expected move.", type="warning")
        return
    set_pending_expected_move(payload)
    ui.navigate.to("/options/expected-move", new_tab=True)


def set_pending_calculator(signal):
    _pending["calculator"] = signal


def take_pending_calculator():
    """Return and clear the pending calculator signal (one-shot)."""
    sig = _pending.get("calculator")
    _pending["calculator"] = None
    return sig


def send_to_calculator(signal):
    if not signal:
        ui.notify("Select a signal first.", type="warning")
        return
    set_pending_calculator(signal)
    ui.navigate.to("/options/calculator")


def set_pending_simulator(payload):
    _pending["simulator"] = payload


def take_pending_simulator():
    """Return and clear the pending simulator leg payload (one-shot)."""
    p = _pending.get("simulator")
    _pending["simulator"] = None
    return p


def send_to_simulator(payload):
    """Stash a {symbol, legs} payload and open the Simulator page."""
    if not payload or not payload.get("symbol"):
        ui.notify("No legs to copy.", type="warning")
        return
    set_pending_simulator(payload)
    ui.navigate.to("/options/simulator")


def set_pending_calculator_legs(payload):
    _pending["calculator_legs"] = payload


def take_pending_calculator_legs():
    """Return and clear the pending calculator leg payload (one-shot). Separate
    from the scanner-signal ``calculator`` stash."""
    p = _pending.get("calculator_legs")
    _pending["calculator_legs"] = None
    return p


def send_to_calculator_legs(payload):
    """Stash a {symbol, legs} payload and open the Calculator page."""
    if not payload or not payload.get("symbol"):
        ui.notify("No legs to copy.", type="warning")
        return
    set_pending_calculator_legs(payload)
    ui.navigate.to("/options/calculator")


def set_pending_gamma(symbol):
    _pending["gamma"] = symbol


def take_pending_gamma():
    """Return and clear the pending Dealer Positioning symbol (one-shot).

    One-shot matters: a symbol left in the stash would silently re-hijack the
    gamma dropdown the next time that page is built."""
    s = _pending.get("gamma")
    _pending["gamma"] = None
    return s


def send_to_gamma(symbol):
    """Stash a symbol and open Dealer Positioning on it (same browser tab).

    Used by the Flow Alerts tape: every alert type — a premium crossover, unusual
    contract activity, a gamma-regime flip — is asking you to look at that
    symbol's dealer positioning, which is one page away."""
    if not symbol:
        ui.notify("No symbol for dealer positioning.", type="warning")
        return
    set_pending_gamma(symbol)
    ui.navigate.to("/options/gamma")


def _signal_legs_payload(sig):
    """Normalized multi-leg signal → the Calculator's {symbol, legs} copy payload."""
    legs = []
    for l in (sig or {}).get("legs") or []:
        legs.append({"option_type": l.get("kind"), "side": l.get("side"),
                     "strike": l.get("strike"), "expiry": l.get("expiration"),
                     "qty": l.get("qty", 1), "premium": l.get("mark")})
    return {"symbol": ((sig or {}).get("symbol") or "").replace("$", "").upper(),
            "legs": legs}


def send_signal_to_calculator(sig):
    """Send a normalized multi-leg signal to the Calculator via its legs payload;
    fall back to the legacy single-signal stash for old spread dicts without legs."""
    if sig and sig.get("legs"):
        send_to_calculator_legs(_signal_legs_payload(sig))
    else:
        send_to_calculator(sig)


def send_to_paper(signal):
    if not signal:
        ui.notify("Select a signal first.", type="warning")
        return

    with ui.dialog() as dlg, ui.card():
        ui.label(f"Paper trade {signal.get('symbol')} {signal.get('type')} "
                 f"{signal.get('expiration', '')}").classes("text-subtitle1")
        qty = ui.number("Quantity", value=1, min=1, max=100)

        def confirm():
            # Engine-free: enqueue a paper_create command for the options service
            # to build + persist the trade (then refresh the Paper Trades ledger
            # view). The signal dict is a plain dict of strings/numbers, so it is
            # JSON-serializable onto the command stream.
            bus_client.request("options", {
                "type": "paper_create",
                "args": {"signal": signal, "qty": int(qty.value or 1)},
            })
            ui.notify("Paper trade requested.", type="positive")
            dlg.close()

        with ui.row():
            ui.button("Create", color=None, on_click=confirm).props("no-caps").classes(BTN_3D)
            ui.button("Cancel", on_click=dlg.close).props("flat")
    dlg.open()


# Three tiny per-row action buttons (Send to Calculator / Paper trade / Expected
# Move) for a signal table's "actions" column. Emits to_calc / to_paper / to_em
# (Calculator / Paper trade / Expected Move) with the row dict.
_ACTIONS_SLOT = """
<q-td :props="props" auto-width>
  <q-btn dense flat round size="sm" icon="calculate" color="primary"
         @click.stop="() => $parent.$emit('to_calc', props.row)">
    <q-tooltip>Send to Calculator</q-tooltip>
  </q-btn>
  <q-btn v-if="props.row._allow_paper" dense flat round size="sm" icon="request_quote" color="secondary"
         @click.stop="() => $parent.$emit('to_paper', props.row)">
    <q-tooltip>Send to Paper trade</q-tooltip>
  </q-btn>
  <q-btn dense flat round size="sm" icon="show_chart" color="accent"
         @click.stop="() => $parent.$emit('to_em', props.row)">
    <q-tooltip>Expected Move</q-tooltip>
  </q-btn>
</q-td>
"""


_ROW_ACTIONS_DOC = """\
Both row-action slots gate the Paper button on ``props.row._allow_paper`` — the
ONLY action that is gated, because Calculator/Expected-Move merely inspect a
signal while Paper BOOKS it. Two hazards share the flag:

* an **undefined-risk** structure (a naked short) — stamped by
  ``strategy_table.strategy_rows``;
* a **dropped-out** day-union signal, frozen at an hours-old price — stamped by
  ``scanner.stamp_stale``, which only ever NARROWS the flag.

``paper_create`` records ``signal['credit']`` verbatim with no re-pricing, so an
ungated button on a stale row writes a fictional entry into the manual paper book.
Every caller of these slots MUST stamp ``_allow_paper`` (an absent field reads as
falsy → no button, which fails safe).
"""


def add_row_actions(table, get_signal):
    """Add per-row Calculator / Paper-trade / Expected Move buttons to a signal table.

    ``get_signal(row)`` maps a clicked display row to its raw engine signal.
    """
    table.add_slot("body-cell-actions", _ACTIONS_SLOT)
    table.on("to_calc", lambda e: send_to_calculator(get_signal(e.args)))
    table.on("to_paper", lambda e: send_to_paper(get_signal(e.args)))
    table.on("to_em", lambda e: send_to_expected_move(signal_to_em_payload(get_signal(e.args))))


# Single per-row Expected Move action (for tables that only want this one button,
# e.g. Paper Trades / Captured Signals — where Send-to-Calculator / Send-to-Paper
# don't belong). Emits to_em with the row dict.
_EM_ACTION_SLOT = """
<q-td :props="props" auto-width>
  <q-btn dense flat round size="sm" icon="show_chart" color="accent"
         @click.stop="() => $parent.$emit('to_em', props.row)">
    <q-tooltip>Expected Move</q-tooltip>
  </q-btn>
</q-td>
"""


def add_expected_move_action(table, get_signal):
    """Add a per-row Expected Move button only (not the Calculator/Paper actions).

    ``get_signal(row)`` maps a clicked display row to its raw engine signal."""
    table.add_slot("body-cell-actions", _EM_ACTION_SLOT)
    table.on("to_em", lambda e: send_to_expected_move(signal_to_em_payload(get_signal(e.args))))


# Per-row actions for the multi-strategy swing table: Calculator + Expected Move
# for ALL rows; Paper trade ONLY when the row is a credit-creditable structure
# (``props.row._allow_paper``). Sends multi-leg signals via the legs-aware paths.
_STRATEGY_ACTIONS_SLOT = """
<q-td :props="props" auto-width>
  <q-btn dense flat round size="sm" icon="calculate" color="primary"
         @click.stop="() => $parent.$emit('to_calc', props.row)">
    <q-tooltip>Send to Calculator</q-tooltip>
  </q-btn>
  <q-btn v-if="props.row._allow_paper" dense flat round size="sm" icon="request_quote" color="secondary"
         @click.stop="() => $parent.$emit('to_paper', props.row)">
    <q-tooltip>Send to Paper trade</q-tooltip>
  </q-btn>
  <q-btn dense flat round size="sm" icon="show_chart" color="accent"
         @click.stop="() => $parent.$emit('to_em', props.row)">
    <q-tooltip>Expected Move</q-tooltip>
  </q-btn>
</q-td>
"""


def add_strategy_row_actions(table, get_signal):
    """Per-row Calculator / Paper (gated) / Expected-Move actions for the
    multi-strategy swing table. ``get_signal(row)`` maps a clicked display row to
    its raw normalized signal (carrying ``legs``)."""
    table.add_slot("body-cell-actions", _STRATEGY_ACTIONS_SLOT)
    table.on("to_calc", lambda e: send_signal_to_calculator(get_signal(e.args)))
    table.on("to_paper", lambda e: send_to_paper(get_signal(e.args)))
    table.on("to_em", lambda e: send_to_expected_move(signal_to_em_payload(get_signal(e.args))))
