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

from nicegui import app, ui  # noqa: E402

import proxy  # noqa: E402
from repo_paths import NICEGUI_PORT  # noqa: E402


# Options scanning + auto-scan now live in services/options_svc (Tier 2): the
# service owns the engine and the 08:00–15:15 CT schedule, writes results to the
# Redis bus, and the GUI reads the cache + enqueues rescan commands. Sentiment
# refresh likewise lives in services/sentiment_svc.

# Options is an expandable group; each child is its own route. (route, label, icon)
OPTIONS_CHILDREN = [
    ("/", "Scanner", "radar"),
    ("/options/paper", "Paper Trades", "request_quote"),
    ("/options/captured", "Captured Signals", "bookmark"),
    ("/options/portfolio", "Paper Portfolio", "account_balance_wallet"),
    ("/options/calculator", "Calculator", "calculate"),
    ("/options/swing", "Swing Scanner", "swap_vert"),
    ("/options/gamma", "Gamma", "stacked_line_chart"),
    ("/options/simulator", "Simulator", "science"),
]

# Sentiment is an expandable group; each child is its own route. (route, label, icon)
SENTIMENT_CHILDREN = [
    ("/sentiment", "Sentiment", "insights"),
    ("/sentiment/rotation", "Sector Rotation", "donut_large"),
]

# Flat top-level items (non-Options apps). (route, label, icon)
FLAT_NAV = [
    ("/trade", "Trade", "analytics"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Driver", "smart_toy"),
]

# Persisted left-nav expansion state (single-user); None/absent = use active-route default.
_NAV_OPEN: dict[str, bool] = {}


def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    classes = "w-full no-underline rounded px-3 py-2 items-center"
    if path == active:
        classes += " bg-primary text-white"
    with ui.link(target=path).classes(classes):
        with ui.row().classes("items-center gap-3 w-full"):
            ui.icon(icon)
            ui.label(label)


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
        options_active = active == "/" or active.startswith("/options")
        options_exp = ui.expansion(
            "Options", icon="candlestick_chart", value=_NAV_OPEN.get("Options", options_active)
        ).classes("w-full")
        options_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Options", e.value))
        with options_exp:
            for path, label, icon in OPTIONS_CHILDREN:
                _nav_link(path, label, icon, active)
        sentiment_active = active.startswith("/sentiment")
        sentiment_exp = ui.expansion(
            "Sentiment", icon="insights", value=_NAV_OPEN.get("Sentiment", sentiment_active)
        ).classes("w-full")
        sentiment_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Sentiment", e.value))
        with sentiment_exp:
            for path, label, icon in SENTIMENT_CHILDREN:
                _nav_link(path, label, icon, active)
        for path, label, icon in FLAT_NAV:
            _nav_link(path, label, icon, active)

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


@ui.page("/")
def options_scanner_page() -> None:
    with _layout("/", "Options · Scanner"):
        from pages.options import scanner
        scanner.render()


@ui.page("/options/paper")
def options_paper_page() -> None:
    with _layout("/options/paper", "Options · Paper Trades"):
        from pages.options import paper
        paper.render()


@ui.page("/options/captured")
def options_captured_page() -> None:
    with _layout("/options/captured", "Options · Captured Signals"):
        from pages.options import captured
        captured.render()


@ui.page("/options/portfolio")
def options_portfolio_page() -> None:
    with _layout("/options/portfolio", "Options · Paper Portfolio"):
        from pages.options import portfolio
        portfolio.render()


@ui.page("/options/calculator")
def options_calculator_page() -> None:
    with _layout("/options/calculator", "Options · Calculator"):
        from pages.options import calculator
        calculator.render()


@ui.page("/options/swing")
def options_swing_page() -> None:
    with _layout("/options/swing", "Options · Swing Scanner"):
        from pages.options import swing
        swing.render()


@ui.page("/options/gamma")
def options_gamma_page() -> None:
    with _layout("/options/gamma", "Options · Gamma"):
        from pages.options import gamma
        gamma.render()


@ui.page("/options/simulator")
def options_simulator_page() -> None:
    with _layout("/options/simulator", "Options · Simulator"):
        from pages.options import simulator
        simulator.render()


@ui.page("/sentiment")
def sentiment_page() -> None:
    with _layout("/sentiment", "Sentiment"):
        from pages import sentiment
        sentiment.render()


@ui.page("/sentiment/rotation")
def sentiment_rotation_page() -> None:
    with _layout("/sentiment/rotation", "Sector Rotation"):
        from pages import sentiment_rotation
        sentiment_rotation.render()


@ui.page("/trade")
def trade_page() -> None:
    with _layout("/trade", "Trade"):
        from pages import trade
        trade.render()


@ui.page("/portfolio")
def portfolio_page() -> None:
    with _layout("/portfolio", "Portfolio"):
        from pages import portfolio
        portfolio.render()


@ui.page("/driver")
def driver_page() -> None:
    with _layout("/driver", "Driver"):
        from pages import driver
        driver.render()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(port=NICEGUI_PORT, title="Schwab Trading", dark=True, reload=False, show=False)
