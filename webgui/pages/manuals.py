"""User Manuals page — links to the online (HTML) documentation.

Thin render(): lists the generated manuals (built under ``docs/manuals/`` by
``docs/manuals/build_docs.py``) and opens each one's self-contained HTML in a new
browser tab via the ``/manuals/file`` route registered in ``main.py``.

``MANUALS`` is the single catalog shared by the page and the file-serving route —
keep new manuals in sync here. ``file`` is relative to ``docs/manuals/`` and is a
whitelist key (the route refuses anything not in this dict), so there is no path
traversal.
"""
from nicegui import ui

from pages.options.theme import BTN_3D

# slug -> {title, desc, icon, file}. `file` is relative to docs/manuals/.
MANUALS = {
    "user-guide": {
        "title": "User Guide",
        "desc": "How to operate every page of the app — scanning, paper trading, "
                "sentiment, portfolio, the driver, reports, and troubleshooting.",
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
}


def _open(slug: str) -> None:
    ui.navigate.to(f"/manuals/file?name={slug}", new_tab=True)


def render():
    ui.label("User Manuals").classes("text-h5")
    ui.label("Online documentation for the app. Each manual opens in a new browser "
             "tab. Word (.docx) copies live alongside the HTML under "
             "docs/manuals/.").classes("opacity-70 text-sm")

    with ui.column().classes("w-full max-w-2xl gap-3"):
        for slug, m in MANUALS.items():
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                    ui.icon(m["icon"]).classes("text-3xl opacity-80")
                    with ui.column().classes("gap-0 grow"):
                        ui.label(m["title"]).classes("text-subtitle1 font-bold")
                        ui.label(m["desc"]).classes("opacity-70 text-sm")
                    ui.button("Open", icon="open_in_new", color=None,
                              on_click=lambda _, s=slug: _open(s)).props("no-caps").classes(BTN_3D)
