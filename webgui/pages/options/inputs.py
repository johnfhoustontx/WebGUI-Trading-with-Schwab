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


def bind_symbol_load(inp, load, *, tab=True, enter_always=False):
    """Wire the Symbol field's load triggers: pressing **Enter** (and, when ``tab``,
    **tabbing/clicking out**) fires ``load()`` — the same load/fetch/scan the page's
    button runs — but only when the uppercased symbol CHANGED since it last fired
    (via ``should_load``), so tabbing through an unchanged symbol won't re-fetch.

    ``enter_always=True`` exempts **Enter** from that dedup: pressing Enter is the
    reader typing the page's Load button, an explicit instruction to load *now*,
    and the button itself has always bypassed the dedup for exactly that reason
    (refreshing an unchanged symbol is the main thing it is for). A deliberate
    re-press otherwise did nothing at all, with no way to tell it from a slow
    fetch. Tab-out keeps the dedup either way — that one is incidental, not an
    instruction. An EMPTY field still loads nothing. Off by default, so every
    existing caller is unchanged.
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

    def _fire_enter(*_):
        if not enter_always:
            _fire()
            return
        cur = (inp.value or "").strip().upper()
        if not cur:
            return
        last["sym"] = cur       # mark it loaded, then load it regardless
        load()

    inp.on('keydown.enter', _fire_enter)
    if tab:
        inp.on('focusout', _fire)
    inp._symbol_load_last = last        # for mark_symbol_loaded
    return inp


def mark_symbol_loaded(inp, value):
    """Tell ``bind_symbol_load``'s dedup that ``value`` is already loaded.

    For a page that writes the Symbol field from CODE and loads that symbol itself
    (a hand-off, a pick naming another symbol): the dedup still remembers the
    previous symbol, so the next tab-out would read the new one as unloaded and
    load it a second time. A field that was never bound is left alone."""
    last = getattr(inp, "_symbol_load_last", None)
    if last is not None:
        last["sym"] = (value or "").strip().upper()
