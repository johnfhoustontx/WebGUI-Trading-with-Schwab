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
# The page-to-shell seam. Every function here is a "go to page X" action, and X
# is the PRIVATE app's route: the public origin serves most of those pages
# somewhere else and most of them nowhere at all, so the route is RESOLVED
# rather than emitted. In the private app the resolution is the identity.
import shell as _shell
from pages import ui_kit as kit

from .theme import MUTED, TXT_NEG, TXT_POS

_pending = {"calculator": None, "expected_move": None, "swing": None,
            "calculator_legs": None, "gamma": None}


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
        kit.toast("warn", "No symbol for expected move.")
        return
    set_pending_expected_move(payload)
    _shell.navigate_to("/options/expected-move", new_tab=True)


def set_pending_calculator(signal):
    _pending["calculator"] = signal


def take_pending_calculator():
    """Return and clear the pending calculator signal (one-shot)."""
    sig = _pending.get("calculator")
    _pending["calculator"] = None
    return sig


def send_to_calculator(signal):
    if not signal:
        kit.toast("warn", "Select a signal first.")
        return
    set_pending_calculator(signal)
    _shell.navigate_to("/options/calculator")


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
        kit.toast("warn", "No legs to copy.")
        return
    set_pending_calculator_legs(payload)
    _shell.navigate_to("/options/calculator")


def set_pending_gamma(symbol):
    _pending["gamma"] = symbol


def take_pending_gamma():
    """Return and clear the pending Dealer Positioning symbol (one-shot).

    One-shot matters: a symbol left in the stash would silently re-hijack the
    gamma dropdown the next time that page is built."""
    s = _pending.get("gamma")
    _pending["gamma"] = None
    return s


GAMMA_ROUTE = "/options/gamma"


def send_to_gamma(symbol):
    """Stash a symbol and open Dealer Positioning on it (same browser tab).

    Used by the Flow Alerts tape: every alert type — a premium crossover, unusual
    contract activity, a gamma-regime flip — is asking you to look at that
    symbol's dealer positioning, which is one page away.

    ⚠ Routed through the shell, because ``/options/gamma`` is the PRIVATE app's
    path: the public origin serves that page at ``/gamma``, and a bare navigate
    is a 404 there. ``shell.navigate_to`` resolves it. Known and accepted: the
    published board PINS its symbol, so the stash cannot be honoured there and
    the visitor lands on the pinned board rather than on this symbol — the page
    says which symbol it is showing, and a 404 is the worse of the two."""
    if not symbol:
        kit.toast("warn", "No symbol for dealer positioning.")
        return
    if not _shell.can_navigate(GAMMA_ROUTE):
        return                      # nowhere to send it — and nothing stashed
    set_pending_gamma(symbol)
    _shell.navigate_to(GAMMA_ROUTE)


def set_pending_swing(symbol):
    _pending["swing"] = symbol


def take_pending_swing():
    """Return and clear the pending Strategy Finder symbol (one-shot).

    One-shot for the same reason the gamma stash is: a symbol left behind would
    silently re-hijack the Finder's input the next time that page is built."""
    s = _pending.get("swing")
    _pending["swing"] = None
    return s


def send_to_swing(symbol):
    """Stash a symbol and open the Strategy Finder on it.

    The step between a Trade Plan and a paper trade. The plan names a STRUCTURE
    ("call debit spread, 30-45 DTE") and deliberately not strikes; the Finder
    turns that into concrete multi-leg candidates, and its rows already carry
    the Send-to-Paper action. Wiring the plan straight to paper would mean
    inventing the strikes it declines to specify."""
    if not symbol:
        kit.toast("warn", "No symbol for the Strategy Finder.")
        return
    set_pending_swing(symbol)
    _shell.navigate_to("/options/swing")


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


PAPER_CREATE_VIEW = "options:paper_create"
# Its OWN cadence, deliberately not the pages' 2 s repaint poll: the repaint
# timer stays the only 2 s timer on the page (tests find it by interval), and a
# toast about a second after the click reads as the button's answer. The probe
# is the cheap ``:ver`` counter, so the faster tick costs nothing measurable.
PAPER_RESULT_POLL_SEC = 1.0


def paper_result_toast(payload):
    """``(text, notify type)`` for one ``paper_create`` outcome, or None. PURE.

    Refusals keep the service's own sentence (shared.book_caps.describe), so the
    toast and the Paper dialog preview can never word the same cap differently.
    Every toast leads with "Paper ledger:"; a service sentence continues after the
    dash with its first letter lowered - unless it opens on a ticker ("ORCL
    already holds...", "IONQ's own group..."), which keeps its capitals.
    An opened trade names its structure in words (``strategy_label``), never the
    raw code, and leads with "Paper ledger:" so a label like "Credit spread —
    put" cannot run into the rest of the sentence."""
    if not isinstance(payload, dict) or not payload.get("status"):
        return None
    status = payload["status"]
    if status == "opened":
        from .strategies import strategy_label
        qty = payload.get("qty") or 1
        label = strategy_label(payload.get("type") or "")
        return (f"Paper ledger: opened {qty} × {payload.get('symbol', '')} {label}.",
                "positive")
    message = _continue_sentence(
        (payload.get("message") or "the service gave no reason").rstrip("."))
    lead = "Paper ledger: not opened — "
    if status == "refused":
        text = f"{lead}{message}."
        fits = payload.get("max_quantity")
        if isinstance(fits, int) and not isinstance(fits, bool) and fits > 0:
            text += (f" Up to {fits} contract fits." if fits == 1
                     else f" Up to {fits} contracts fit.")
        return text, "warning"
    if status == "stale":
        return f"{lead}{message}.", "warning"
    return f"{lead}{message}.", "negative"


# The sentence-opening words our OWN services emit, and the only first words a
# toast lowers. An explicit list because letter case cannot tell a sentence word
# from a proper noun: book_caps' sector refusals open on the sector name
# ("Information Technology is full ...", "Energy risk would reach ..."), and a
# case heuristic lowered those to "information Technology". Sources:
# shared.book_caps.describe ("Risks $X, over ...", "Open risk across the book
# ...") and the options service ("The paper ledger could not ...", "The request
# waited ...", "The signal is missing ...", "The trade's max loss ...",
# "Quantity must be ..."). A message opening on anything else keeps its capitals.
_SENTENCE_OPENERS = frozenset({"Risks", "Open", "The", "Quantity"})


def _continue_sentence(message):
    """Lower the first letter so a service sentence reads on after a dash. PURE.

    Only when the first word is one of ``_SENTENCE_OPENERS``: "Risks $900" becomes
    "risks $900", while a sector, a ticker or anything else ("Information
    Technology is full ...", "ORCL already holds ...") keeps its capitals."""
    words = message.split(None, 1)
    first = words[0].rstrip(",") if words else ""
    if first in _SENTENCE_OPENERS:
        return message[0].lower() + message[1:]
    return message


# ``paper_result_toast`` answers in Quasar's own vocabulary, because it is the
# PURE decision and its tests pin those words; the kit speaks the page's four
# kinds. One map between them, rather than rewording the pure function.
_TOAST_KIND = {"positive": "ok", "warning": "warn", "negative": "error",
               "info": "info"}


def watch_paper_results():
    """Toast every ``paper_create`` answer on this page. ``watch_view`` seeds the
    version (an answer published before the page opened is not replayed) and
    compares with ``!=`` - required, because the view's TTL can reset its version
    counter to 1."""
    from pages.view_watch import watch_view

    def _on_change():
        toast = paper_result_toast(bus_client.read(PAPER_CREATE_VIEW))
        if toast:
            kit.toast(_TOAST_KIND.get(toast[1], "info"), toast[0])

    return watch_view(PAPER_CREATE_VIEW, _on_change, interval=PAPER_RESULT_POLL_SEC)


LEDGER_CAPS_VIEW = "options:ledger_caps"
# The dialog's own quantity ceiling: the most contracts it opens in one trade.
PAPER_QTY_CEILING = 100
CEILING_TEXT = f"The dialog opens at most {PAPER_QTY_CEILING} contracts in one trade."
NOTHING_FITS_TEXT = "No quantity fits the paper ledger's limits right now."
SEND_FAILED_TEXT = "Could not reach the options service — the trade was not sent."

# A preview line's tone -> the theme's text token. A fixed map (the finite-set
# rule): a tone the map does not know renders muted, never unstyled.
_TONE_CLASS = {"pos": TXT_POS, "neg": TXT_NEG, "muted": MUTED}


def paper_dialog_view(signal, caps, qty):
    """Everything the Paper dialog shows for ``qty`` contracts. PURE.

    ``caps`` is the ``options:ledger_caps`` view as read when the dialog opened.
    The preview is ``book_fit.preview`` - the service's own rungs, line for line.

    ``can_create`` blocks only what the page KNOWS it must not send: a breach, a
    quantity that is not a whole number of at least 1, or one above the dialog's
    own ceiling. A preview that is unavailable for any other reason (no caps
    view, no risk stamp) does NOT block - the service checks every cap on the
    click, so a trade the page merely cannot preview is still the Ledger's to
    decide.

    ``qty_max`` is the Quantity box's max, which the box clamps to on blur - so
    it must never sit below a quantity that FITS. ``book_caps.max_quantity`` can
    land one short at an exact cap on sub-cent risk, hence ``max(fits, q)`` when
    nothing breaches.
    """
    from shared import book_caps

    from . import book_fit
    from .strategies import strategy_label

    sig = signal if isinstance(signal, dict) else {}
    p = book_fit.preview(sig, caps, qty)

    label = strategy_label(sig.get("type") or "") or ""
    title = " ".join(part for part in ("Paper trade", str(sig.get("symbol") or ""),
                                       label) if part)
    if sig.get("expiration"):
        title += f" · {sig['expiration']}"

    one = book_caps.booked_risk(sig.get("ledger_risk_basis"), 1)
    risk_text = (f"Risk {book_caps.money(one)} per contract"
                 if one is not None and one > 0 else "")

    q = book_fit.whole_quantity(qty)
    available = bool(p.get("available"))
    breach = p.get("breach") if available else None
    fits = p.get("max_quantity")
    fits = fits if isinstance(fits, int) and not isinstance(fits, bool) else None
    over_ceiling = q is not None and q > PAPER_QTY_CEILING

    fits_text = ""
    if breach is not None:
        fits_text = ((("Up to 1 contract fits." if fits == 1
                       else f"Up to {fits} contracts fit."))
                     if fits is not None and fits > 0 else NOTHING_FITS_TEXT)
    elif over_ceiling:
        fits_text = CEILING_TEXT

    if breach is not None:
        qty_max = fits if fits is not None and fits > 0 else 1
    elif over_ceiling or q is None or not available or fits is None:
        qty_max = PAPER_QTY_CEILING
    else:
        qty_max = min(max(fits, q), PAPER_QTY_CEILING)

    lines = [{**line, "class": _TONE_CLASS.get(line.get("tone"), MUTED)}
             for line in (p.get("lines") or [])]
    return {
        "title": title,
        "risk_text": risk_text,
        "lines": lines,
        "note": "" if available else (p.get("unavailable_text") or ""),
        "block_text": p.get("block_text") or "",
        "fits_text": fits_text,
        "can_create": not (q is None or over_ceiling or breach is not None),
        "qty_max": max(1, qty_max),
    }


def sent_text(n):
    """The confirm toast, naming the quantity that went out. PURE."""
    unit = "contract" if n == 1 else "contracts"
    return f"Sent {n} {unit} — the paper ledger answers in a moment."


def send_to_paper(signal):
    """Ask, then enqueue a ``paper_create``. Returns the dialog handle."""
    if not signal:
        kit.toast("warn", "Select a signal first.")
        return None

    # Read ONCE when the dialog opens: the preview recomputes on every quantity
    # change against this snapshot, and the service re-checks on the click. A
    # bus that cannot be read is "no preview", which leaves Create enabled.
    try:
        caps = bus_client.read(LEDGER_CAPS_VIEW)
    except Exception:  # noqa: BLE001 - no preview is the degrade; the service still checks
        caps = None
    state = {"view": paper_dialog_view(signal, caps, 1), "sent": False}

    view = state["view"]
    # ephemeral: this dialog is built per click, so it removes itself on close
    # rather than leaving one element behind in the page each time.
    dlg = kit.confirm(view["title"], confirm_text="Create", ephemeral=True,
                      on_confirm=lambda: confirm())
    create = dlg.confirm
    with dlg.content:
        risk = ui.label(view["risk_text"]).classes(f"text-sm {MUTED}")
        lines_box = ui.column().classes("gap-1 w-full")
        note = ui.label("").classes(f"text-xs {MUTED}")
        # No separate block sentence: the binding checklist line already says
        # it in red. ``block_text`` stays in the view for the tests; the refusal
        # toast uses the service's own ``message``, not this.
        fits = ui.label("").classes(f"text-xs {MUTED}")
        # min only: the ceiling MOVES with the quantity (``qty_max`` is never
        # below a typed quantity that fits), so ``paint`` writes it into
        # ``_props["max"]`` rather than freezing it into the field's own check.
        qty = kit.number_field("Quantity", value=1, min=1, integer=True)

    def confirm():
        # A latch, not just the button: a queued second click still runs
        # after the first closes the dialog, and must not enqueue twice.
        if state["sent"]:
            return
        # Re-check at click time: the button's enabled state is a display,
        # never the gate.
        current = paper_dialog_view(signal, caps, qty.value)
        if not current["can_create"]:
            return False              # keep the dialog open; the red line says why
        n = int(qty.value)
        state["sent"] = True
        create.disable()
        # Engine-free: enqueue a paper_create command for the options service
        # to build + persist the trade (then refresh the Paper Trades ledger
        # view). The signal dict is a plain dict of strings/numbers, so it is
        # JSON-serializable onto the command stream.
        try:
            bus_client.request("options", {
                "type": "paper_create",
                "args": {"signal": signal, "qty": n},
            })
        except Exception:  # noqa: BLE001 - said on screen; the reader retries
            state["sent"] = False
            create.enable()
            kit.toast("error", SEND_FAILED_TEXT)
            return False
        kit.toast("info", sent_text(n))

    def paint(v):
        risk.set_text(v["risk_text"])
        risk.set_visibility(bool(v["risk_text"]))
        lines_box.clear()
        with lines_box:
            for line in v["lines"]:
                with ui.row().classes("gap-2 items-baseline no-wrap"):
                    ui.label(line["label"]).classes(f"text-xs {MUTED} w-24 shrink-0")
                    ui.label(line["text"]).classes(f"text-sm {line['class']}")
        for el, key in ((note, "note"), (fits, "fits_text")):
            el.set_text(v[key])
            el.set_visibility(bool(v[key]))
        # ``ui.number`` keeps ``max`` in ``_props``. Written straight to the
        # prop + update(), NOT through the ``max`` setter: the setter also
        # runs ``sanitize()``, which would clamp the value the reader just
        # typed before the red line and "Up to N fit" could say why. The
        # element's own blur handler still clamps to this max on leaving
        # the field.
        if qty._props.get("max") != v["qty_max"]:
            qty._props["max"] = v["qty_max"]
            qty.update()
        if not state["sent"]:
            create.set_enabled(v["can_create"])

    def on_qty(e):
        state["view"] = paper_dialog_view(signal, caps, e.value)
        paint(state["view"])

    qty.on_value_change(on_qty)
    paint(view)
    dlg.open()
    return dlg


# ── the Income board's open action ──────────────────────────────────────────
# ⚠ This targets the paper ACCOUNT, not the paper LEDGER ``send_to_paper``
# writes, and they are two different books: the ledger tracks a trade's marks,
# the account holds cash, reserved collateral and — the point of this action —
# share lots. A cash-secured put opened here is what eventually becomes stock;
# the same trade sent to the ledger never can.
#
# The two structures below are the only ones the ACCOUNT can hold from this
# board. The two credit spreads already have a route (the ledger), and the
# service refuses anything else anyway; the gate here just keeps a button off a
# row it would only be refused on. Spelled as row ``type`` values, matching
# ``income.SIDE_LABELS``.
INCOME_OPENABLE_TYPES = ("SHORT_PUT", "COVERED_CALL")


def income_openable(row) -> bool:
    """True when an income row can be opened into the paper ACCOUNT (PURE).

    An unreadable row is not openable — absent reads as falsy, which fails safe
    in the direction that matters for a button that books a trade.
    """
    return str((row or {}).get("type") or "").strip().upper() in INCOME_OPENABLE_TYPES


def open_in_paper_account(row):
    """Confirm-then-enqueue an ``income_open`` for one Income-board row.

    The quantity defaults to what the row itself says it supports — for a
    covered call that is the whole lot, which is the only quantity the book can
    deliver — so the common case is one click and a confirm.

    The toast here promises only that the request went out; the ACCOUNT's answer
    (opened, or refused and why) comes back asynchronously on
    ``cache:options:income_open`` and the page shows it. Claiming a fill here
    would be the ``driver-executed-but-nothing-opened`` shape: reporting the
    enqueue as the outcome.

    Returns the dialog handle.
    """
    if not row:
        kit.toast("warn", "Select a row first.")
        return None
    if not income_openable(row):
        kit.toast("warn", "Only a cash-secured put or a covered call can be "
                          "opened into the paper account.")
        return None
    default_qty = 1
    try:
        default_qty = max(1, int(float(row.get("quantity") or 1)))
    except (TypeError, ValueError):
        default_qty = 1

    kind = "cash-secured put" if row.get("type") == "SHORT_PUT" else "covered call"
    # ephemeral: built per click, like the Paper dialog above.
    dlg = kit.confirm(
        f"Open {row.get('symbol')} {kind} {row.get('expiration', '')}",
        "This opens into the paper account: collateral is reserved, and an "
        "assignment becomes shares.",
        confirm_text="Open", ephemeral=True, on_confirm=lambda: _open())
    with dlg.content:
        qty = kit.number_field("Contracts", value=default_qty, min=1, max=100,
                               integer=True)

    def _open():
        if not qty.validate():
            return False          # the field says what is wrong with the number
        bus_client.request("options", {
            "type": "income_open",
            "args": {"row": row, "qty": int(qty.value or 1)},
        })
        kit.toast("info", "Sent to the paper account — the result appears here "
                          "in a moment.")

    dlg.open()
    return dlg


# One per-row action for the Income board: open into the paper account. Gated on
# ``props.row._allow_open`` — every caller MUST stamp it (an absent field reads
# as falsy → no button, which fails safe, exactly as ``_allow_paper`` does).
_INCOME_ACTION_SLOT = """
<q-td :props="props" auto-width>
  <q-btn v-if="props.row._allow_open" dense flat round size="sm" icon="account_balance_wallet"
         color="secondary"
         @click.stop="() => $parent.$emit('to_account', props.row)">
    <q-tooltip>Open in the paper account</q-tooltip>
  </q-btn>
</q-td>
"""


def add_income_row_actions(table, get_row):
    """Add the per-row 'open in the paper account' button to the Income board.

    ``get_row(display_row)`` maps a clicked display row back to its raw
    candidate — the display row carries formatted strings, and the service needs
    the numbers.
    """
    table.add_slot("body-cell-actions", _INCOME_ACTION_SLOT)
    table.on("to_account", lambda e: open_in_paper_account(get_row(e.args)))


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


def add_row_actions(table, get_signal):
    """Add per-row Calculator / Paper-trade / Expected Move buttons to a signal table.

    ``get_signal(row)`` maps a clicked display row to its raw engine signal.
    """
    table.add_slot("body-cell-actions", _ACTIONS_SLOT)
    table.on("to_calc", lambda e: send_to_calculator(get_signal(e.args)))
    table.on("to_paper", lambda e: send_to_paper(get_signal(e.args)))
    table.on("to_em", lambda e: send_to_expected_move(signal_to_em_payload(get_signal(e.args))))


# The single per-row Expected Move action (``_EM_ACTION_SLOT`` /
# ``add_expected_move_action``) lived here for the two tables that wanted that
# button alone - Paper Trades and Captured Signals. Both moved Expected Move
# into the detail panel's footer on 2026-09-19 (the row-actions column went with
# it), leaving the helper with no callers, so it was deleted rather than left as
# a slot nothing mounts. The multi-button slots below are still live: the Market
# Scanner and the Strategy Finder mount them.

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
