"""Shared Symbol-field helpers for the Options (and Trade) pages.

The three behaviors every Symbol field wants — uppercase entry, type-over on
focus, and load-on-Enter/Tab — live here so they stay identical app-wide.
"""

def select_all_on_focus(inp):
    """Standard Symbol-field entry UX: show the ticker in **UPPERCASE** (tickers are
    all-caps) AND select the full text on focus/tab-in so any keypress overwrites it
    (type-over). Returns the input for chaining.

    ``focusin`` (NOT ``focus``): NiceGUI attaches the listener to the Quasar q-input's
    ROOT element, and the native ``focus`` event does not bubble, so it would never
    reach that listener. ``focusin`` bubbles, and its ``e.target`` is the real inner
    ``<input>`` — which we select. The select is **deferred one tick** (``setTimeout``):
    on a MOUSE click the browser fires focus, then the subsequent mouseup drops the
    caret and clears any selection made synchronously — so an immediate ``select()``
    only ever survived tab-in, not a click. Deferring it past the mouseup makes
    click-to-highlight work too (and tab-in is unaffected). The ``uppercase`` class is
    ``text-transform: uppercase`` (a display transform, which is why the load handlers
    still read ``value.upper()`` — they own the value's correctness; this only makes
    the entry LOOK like the caps ticker it is)."""
    inp.classes("uppercase")
    inp.on('focusin', js_handler='(e) => { const i = e.target; if (i && i.select) setTimeout(() => i.select(), 0); }')
    return inp


def should_load(current, last_loaded):
    """True when a tab-out / Enter should (re)trigger Load: a non-empty symbol that
    differs from the one already loaded. The Load / Fetch BUTTON bypasses this and
    always loads (e.g. to refresh price); this only gates the symbol-field triggers
    so tabbing through an unchanged symbol won't re-fetch."""
    return bool(current) and current != last_loaded


def bind_symbol_load(inp, load, *, tab=True):
    """Wire the Symbol field's load triggers: pressing **Enter** (and, when ``tab``,
    **tabbing/clicking out**) fires ``load()`` — the same load/fetch/scan the page's
    button runs — but only when the uppercased symbol CHANGED since it last fired
    (via ``should_load``), so tabbing through an unchanged symbol won't re-fetch.
    Seeds the dedup from the field's INITIAL value, so a default symbol (e.g. ``SPY``)
    does not auto-load the first time focus leaves the field. Returns ``inp``.

    ``focusout`` (NOT ``blur``): NiceGUI attaches the listener to the q-input ROOT,
    where ``blur`` does not bubble but ``focusout`` does (the same reason
    ``select_all_on_focus`` uses ``focusin``). ``tab=False`` wires Enter only — for a
    field whose tab-out must NOT itself submit (e.g. a symbol immediately followed by
    a required sibling field that still needs filling in). Expected Move uses the
    default ``tab=True``: tabbing out of its Symbol field loads that symbol's option
    CHAIN (populating the Expiry/Strike dropdowns), not a draw — so a bare tab-out is
    safe there. The page's Load BUTTON should call ``load`` directly to force a
    refresh even when the symbol is unchanged."""
    last = {"sym": (inp.value or "").strip().upper()}

    def _fire(*_):
        cur = (inp.value or "").strip().upper()
        if should_load(cur, last["sym"]):
            last["sym"] = cur
            load()

    inp.on('keydown.enter', _fire)
    if tab:
        inp.on('focusout', _fire)
    return inp
