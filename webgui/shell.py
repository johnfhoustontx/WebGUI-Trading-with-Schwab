"""The page-to-shell seam: the handful of shell services a PAGE may call.

⚠ This module exists so that a page never has to ``import main``. main.py's
module body registers every ``@_page`` route, so importing it from a second
NiceGUI process -- the public live screens on ``live_main.py`` -- would publish
``/terminate`` and ``/settings`` to the internet while looking correct. Keeping
the seam here means the public process is STRUCTURALLY incapable of holding the
app's route table, rather than incapable by inspection.

Both entrypoints provide it: ``main.py`` populates the slots from ``_layout``;
``live_main.py`` leaves them empty, and every function here is already a no-op
without a mounted header. Not to be confused with ``pages/trade_shell.py``,
which is the Trade section's shared page body.
"""
from nicegui import ui

from pages.ui_guard import guard


def play_alert(sound: str, volume: float) -> None:
    """Play a bundled alert WAV in the connected browser at the given volume."""
    sound = sound if sound in ("chime", "bell", "ping") else "chime"
    vol = max(0.0, min(1.0, float(volume if volume is not None else 0.6)))
    ui.run_javascript(
        f"(() => {{ const a = document.getElementById('alert-audio'); if (!a) return; "
        f"a.src = '/static/sounds/{sound}.wav'; a.volume = {vol}; "
        f"a.play().catch(() => {{}}); }})()")



# Breadcrumb crumb styling — the trailing crumb is the thing you are looking at,
# everything before it is context. Named because the LEAF swaps them at runtime.
_CRUMB_LEAF = "text-[13px] text-[#e6ecf9] font-semibold"
_CRUMB_CONTEXT = "text-[13px] text-[#5d6a88]"

# The optional FOURTH crumb: a page's own active view (Dealer Positioning ›
# Gamma, Simulator › Replay …). Single-user module state, rebuilt per layout like
# the badge refs. "parent" is the last trail crumb, which has to be demoted to
# context when a view is named after it.
_breadcrumb_leaf: dict = {}


def _view_name(value):
    """A subtab's name from whatever a ``ui.tabs`` element holds.

    Pages build their tabs either by NAME (``ui.tab("Replay")`` → the value is
    the string) or by ELEMENT (Rescue passes the tab object to ``ui.tab_panels``,
    so the value can be the element). Reading the element's ``name`` prop covers
    both without every call site having to know which it is."""
    if value is None:
        return ""
    props = getattr(value, "_props", None)
    if isinstance(props, dict) and props.get("name"):
        return str(props["name"])
    return str(value)


def set_breadcrumb_leaf(label, refs=None) -> None:
    """Show ``label`` as the last breadcrumb crumb, or hide the leaf when falsy.

    ``refs`` targets a SPECIFIC header's elements instead of whichever page built
    the layout most recently. That matters because ``_breadcrumb_leaf`` is
    module-level, and unlike the badge refs — which every client rewrites with the
    same numbers, so the sharing is invisible — each page writes a DIFFERENT view
    name here. With two tabs open the second page's build reassigns the module
    state and the first tab's tab-change handler then writes into the second
    tab's header: caught in prod, where the promote script opens a tab on the
    Scanner and Dealer Positioning's header went on to read "› 0-DTE".

    Never raises and is a no-op without a mounted header, so a page may call it
    unconditionally."""
    refs = _breadcrumb_leaf if refs is None else refs
    if not refs.get("label"):
        return
    text = str(label or "").strip()
    refs["label"].text = text
    refs["label"].set_visibility(bool(text))
    refs["caret"].set_visibility(bool(text))
    parent = refs.get("parent")
    if parent is not None:
        # The page name stops being the leaf the moment a view is named after it.
        parent.classes(remove=f"{_CRUMB_LEAF} {_CRUMB_CONTEXT}",
                       add=_CRUMB_CONTEXT if text else _CRUMB_LEAF)


def bind_breadcrumb_leaf(tabs, labeller=None, initial=None) -> None:
    """Track a page's own view tabs in the breadcrumb: ``… › Page › View``.

    A page's subtabs ARE a level of the hierarchy — Dealer Positioning read the
    same in the header whether you were on Gamma or on Net Prem — but they switch
    CLIENT-side without rebuilding the layout, so the crumb has to ride the same
    event that switches the view.

    Registers an ADDITIONAL ``on_value_change`` handler (NiceGUI appends them), so
    a page's existing handler is untouched, and paints the initial value at build
    time so the first render is already correct rather than correcting itself on
    the first click. ``labeller`` maps the raw tab value to what the header should
    read — Gamma needs it, since its "GEX" tab is displayed as "Gamma".

    ``initial`` is required by every page that builds a bare ``ui.tabs()`` and
    names its default on the ``ui.tab_panels`` instead — four of the five do. That
    default reaches the tabs element through NiceGUI's BINDING, which propagates
    on a later cycle, so ``tabs.value`` is still None while the page is being
    built and the crumb would sit blank until the first click.

    The header elements are CAPTURED here rather than looked up when the handler
    fires: ``_breadcrumb_leaf`` is module-level, so a second tab's page build
    reassigns it and this page's handler would otherwise write its view name into
    the other tab's header (see ``set_breadcrumb_leaf``)."""
    fmt = labeller or _view_name
    refs = dict(_breadcrumb_leaf)
    set_breadcrumb_leaf(fmt(tabs.value if tabs.value is not None else initial), refs)

    @guard
    def _sync(e) -> None:
        set_breadcrumb_leaf(fmt(e.value), refs)

    tabs.on_value_change(_sync)


# Per-page-build slot directly under the top tab strip, where a page can mount
# its own view SUBTABS (see _layout; e.g. the Gamma GEX/Charm/... row). Rebuilt
# on every _layout; None on pages without a strip.
_SUBTAB_SLOT: dict = {"el": None}


def subtab_slot():
    """The container under the main tab strip for a page's view subtabs (or None)."""
    return _SUBTAB_SLOT["el"]
