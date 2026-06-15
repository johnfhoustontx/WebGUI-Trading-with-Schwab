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

_pending = {"calculator": None}


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
            ui.button("Create", on_click=confirm).props("color=primary")
            ui.button("Cancel", on_click=dlg.close).props("flat")
    dlg.open()


# Two tiny per-row action buttons (Send to Calculator / Paper trade) for a
# signal table's "actions" column. Emits to_calc / to_paper with the row dict.
_ACTIONS_SLOT = """
<q-td :props="props" auto-width>
  <q-btn dense flat round size="sm" icon="calculate" color="primary"
         @click.stop="() => $parent.$emit('to_calc', props.row)">
    <q-tooltip>Send to Calculator</q-tooltip>
  </q-btn>
  <q-btn dense flat round size="sm" icon="request_quote" color="secondary"
         @click.stop="() => $parent.$emit('to_paper', props.row)">
    <q-tooltip>Send to Paper trade</q-tooltip>
  </q-btn>
</q-td>
"""


def add_row_actions(table, get_signal):
    """Add per-row Calculator / Paper-trade buttons to a signal table.

    ``get_signal(row)`` maps a clicked display row to its raw engine signal.
    """
    table.add_slot("body-cell-actions", _ACTIONS_SLOT)
    table.on("to_calc", lambda e: send_to_calculator(get_signal(e.args)))
    table.on("to_paper", lambda e: send_to_paper(get_signal(e.args)))
