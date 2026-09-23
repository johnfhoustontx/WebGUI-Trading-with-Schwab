"""User Manuals page — links to the online (HTML) documentation.

Thin render(): lists the generated manuals (built under ``docs/manuals/`` by
``docs/manuals/build_docs.py``) and opens each one's self-contained HTML in a new
browser tab via the ``/manuals/file`` route registered in ``main.py``.

``MANUALS`` is the single catalog shared by the page and the file-serving route —
keep new manuals in sync here. ``file`` is relative to ``docs/manuals/`` and is a
whitelist key (the route refuses anything not in this dict), so there is no path
traversal.

The page itself is the kit's form width: a header line and one card per manual,
with no description sentence of its own — ``page_help["/manuals"]`` carries that,
including the Word (.docx) copies the HTML links do not mention.
"""
from nicegui import ui

from pages import ui_kit as kit
from pages.options import theme

# slug -> {title, desc, icon, file}. `file` is relative to docs/manuals/.
MANUALS = {
    "user-guide": {
        "title": "User Guide",
        "desc": "How to operate every page of the app — scanning, paper trading, "
                "sentiment, portfolio, reports, and troubleshooting.",
        "icon": "menu_book",
        "file": "user-guide/user-guide.html",
    },
    "reference-guide": {
        "title": "Reference Guide",
        "desc": "What every tab and sub-tab does, why it matters and when to reach "
                "for it — opening with a one-page orientation to the whole app.",
        "icon": "explore",
        "file": "reference-guide/reference-guide.html",
    },
    "technical-reference": {
        "title": "Technical Reference",
        "desc": "How every number is derived — formulas, weights, thresholds, and "
                "cadences across the sentiment, options, trade, and portfolio engines.",
        "icon": "functions",
        "file": "technical-reference/technical-reference.html",
    },
    "api-reference": {
        "title": "API / Developer Reference",
        "desc": "The 3-tier integration surface — contracts, the Redis bus API, each "
                "service's commands and published views, and the Schwab proxy endpoints.",
        "icon": "data_object",
        "file": "api-reference/api-reference.html",
    },
    "glossary": {
        "title": "Options Glossary",
        "desc": "Plain-English definitions of the terms the app uses — contracts, the "
                "Greeks, volatility, strategies, and dealer positioning and flow.",
        # NOT menu_book: the User Guide has that one, and the cards are told
        # apart by their icon.
        "icon": "school",
        "file": "glossary/glossary.html",
    },
}


def _open(slug: str) -> None:
    ui.navigate.to(f"/manuals/file?name={slug}", new_tab=True)


def render():
    """One card per manual, each opening its own HTML in a new browser tab.

    The card IS the row: a second wrapper would only add a frame the kit's card
    token already draws. ``min-w-0`` on the text column is what lets a long
    description wrap instead of pushing Open off the right edge.

    ⚠ The button needs ``shrink-0`` beside it. A Quasar button's own content row
    WRAPS, so its min-content width is one word - and a flex item may shrink to
    min-content. Measured here: against the growing text column the button came
    out 52.8px wide inside, with the icon stacked ABOVE the label on all five
    cards. The kit's buttons normally sit in ``head.actions``, where nothing
    grows against them, so this is the first page to meet it.

    The row WRAPS below ``sm``, the same decision (and the same reason) as
    ``appearance.py``'s editor/preview split: at phone width a no-wrap row left
    the description nine words tall in a column two inches wide, because the
    icon and the button were taking their space off the top. Open drops to its
    own line instead, ``ml-auto`` keeping it on the right."""
    with kit.page(width="form"):
        kit.header("User Manuals")
        for slug, m in MANUALS.items():
            with ui.row().classes(f"{theme.CARD} w-full items-center gap-3 "
                                  "flex-wrap sm:flex-nowrap"):
                ui.icon(m["icon"]).classes(f"text-3xl {theme.MUTED}")
                with ui.column().classes("gap-0 grow min-w-0"):
                    ui.label(m["title"]).classes(
                        f"text-subtitle1 font-semibold {theme.LABEL}")
                    ui.label(m["desc"]).classes(f"text-sm {theme.MUTED}")
                kit.button("Open", kind="secondary", icon="open_in_new",
                           on_click=lambda _e, s=slug: _open(s)) \
                    .classes("shrink-0 ml-auto")
