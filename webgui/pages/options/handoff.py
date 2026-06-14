"""Cross-page signal handoff + right-click actions for signal tables.

Lets the Scanner / Swing Scanner send a selected signal to the Calculator
(prefill) or create a Paper Trade from it — mirroring the legacy right-click
menu. Single-user app, so a module-level stash is fine for the calculator
hand-off across the page navigation.
"""
import sys

from nicegui import ui

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

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
    import paper_trader

    with ui.dialog() as dlg, ui.card():
        ui.label(f"Paper trade {signal.get('symbol')} {signal.get('type')} "
                 f"{signal.get('expiration', '')}").classes("text-subtitle1")
        qty = ui.number("Quantity", value=1, min=1, max=100)

        def confirm():
            try:
                trade = paper_trader.create_paper_trade(signal, int(qty.value or 1))
                paper_trader.add_trade(trade)
            except Exception as exc:
                ui.notify(f"Paper trade failed: {exc}", type="negative")
                return
            dlg.close()
            ui.notify(f"Paper trade {trade['trade_id']} created.", type="positive")

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
