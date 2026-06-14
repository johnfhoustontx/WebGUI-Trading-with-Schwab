"""NiceGUI multi-page shell for the Trading With Schwab webgui app.

A single NiceGUI server with a left-nav per feature. Each feature gets its own
page route; in Phase 2 the page bodies are stubs that Phase 3 replaces with the
ported engines. A proxy-down banner is shown on every page when the
schwab-proxy (:8100) is unreachable.

Run order: start schwab-proxy first, then ``python webgui/main.py``.
"""
import pathlib
import sys
from contextlib import contextmanager

# Runtime sys.path glue (mirrors conftest for non-test execution).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from nicegui import ui  # noqa: E402

import proxy  # noqa: E402
from repo_paths import NICEGUI_PORT  # noqa: E402

# (route, label, material icon) — order = nav order. Options is the home page.
NAV = [
    ("/", "Options", "candlestick_chart"),
    ("/sentiment", "Sentiment", "insights"),
    ("/trade", "Trade", "analytics"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Driver", "smart_toy"),
]


@contextmanager
def _layout(active: str, title: str):
    """Render shared chrome (header, nav drawer, proxy banner).

    Yields the content container the page should populate.
    """
    # Drawer starts open and is toggleable via the header menu button, so the
    # nav stays reachable at any viewport width (Quasar hides it as an overlay
    # below the layout breakpoint otherwise).
    drawer = ui.left_drawer(value=True, bordered=True).classes("gap-1").props("behavior=desktop")
    with drawer:
        for path, label, icon in NAV:
            classes = "w-full no-underline rounded px-3 py-2 items-center"
            if path == active:
                classes += " bg-primary text-white"
            with ui.link(target=path).classes(classes):
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.icon(icon)
                    ui.label(label)

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round color=white")
            ui.label("Schwab Trading").classes("text-lg font-bold")
        ui.label(title).classes("text-base opacity-80")

    with ui.column().classes("w-full p-4 gap-3") as content:
        health = proxy.health()
        if not health.get("up"):
            with ui.row().classes(
                "w-full bg-red-2 text-red-10 rounded p-3 items-center gap-2"
            ):
                ui.icon("warning")
                ui.label(
                    f"schwab-proxy is not reachable at {proxy.PROXY_URL} — "
                    "start it first (python schwab-proxy/schwab_proxy.py)."
                )
        yield content


def _stub(title: str, blurb: str) -> None:
    ui.label(title).classes("text-h5")
    ui.label(blurb).classes("opacity-70")
    ui.label("(page under construction — Phase 3)").classes("text-sm opacity-50")


@ui.page("/")
def options_page() -> None:
    with _layout("/", "Options"):
        from pages.options import scanner
        scanner.render()


@ui.page("/sentiment")
def sentiment_page() -> None:
    with _layout("/sentiment", "Sentiment"):
        _stub("Market Sentiment", "Composite score, sub-scores, and sector rotation.")


@ui.page("/trade")
def trade_page() -> None:
    with _layout("/trade", "Trade"):
        _stub("Trade Analyzer", "Symbol MTF analysis, verdicts, and fundamentals.")


@ui.page("/portfolio")
def portfolio_page() -> None:
    with _layout("/portfolio", "Portfolio"):
        _stub("Portfolio Analyzer", "Sector breakdown, vs-sector performance, live streaming.")


@ui.page("/driver")
def driver_page() -> None:
    with _layout("/driver", "Driver"):
        _stub("Claude Driver", "Orchestration controls and the order approval queue.")


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(port=NICEGUI_PORT, title="Schwab Trading", dark=True, reload=False, show=False)
