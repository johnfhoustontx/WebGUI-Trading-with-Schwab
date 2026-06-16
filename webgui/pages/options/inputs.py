"""Shared input helpers for the Options pages."""

def select_all_on_focus(inp):
    """Make a ui.input select its full text on focus/tab-in (type-over UX).

    The Quasar q-input's real <input> is the focus event target; select it.
    Returns the input for chaining."""
    inp.on('focus', js_handler='(e) => { const i = e.target; if (i && i.select) i.select(); }')
    return inp
