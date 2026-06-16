"""Shared input helpers for the Options pages."""

def select_all_on_focus(inp):
    """Make a ui.input select its full text on focus/tab-in (type-over UX).

    Uses ``focusin`` (NOT ``focus``): NiceGUI attaches the listener to the
    Quasar q-input's ROOT element, and the native ``focus`` event does not
    bubble, so it would never reach that listener. ``focusin`` bubbles, and its
    ``e.target`` is the real inner ``<input>`` — which we select. Returns the
    input for chaining."""
    inp.on('focusin', js_handler='(e) => { const i = e.target; if (i && i.select) i.select(); }')
    return inp
