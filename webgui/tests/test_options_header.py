"""Tests for the compact Options header strip (Tier-3 reader).

The pure helpers (``sentiment_dot``/``quote_last``/``vix_regime`` + the
``refresh_header`` view builder) moved into ``services/options_svc/compute.py``
in Task 2.6a — their tests now live in
``services/options_svc/tests/test_compute.py``. The strip now only reads the
``options:header`` bus view and paints it, so the page tests assert it imports
no proxy/engine modules and that ``_fmt_price`` (the one remaining pure display
helper) still formats correctly.
"""
import bus_client
from pages.options import header


def test_fmt_price_formats_numbers():
    assert header._fmt_price(742.361) == "742.36"
    assert header._fmt_price(5400) == "5,400.00"


def test_fmt_price_dash_for_non_numeric():
    assert header._fmt_price(None) == "—"
    assert header._fmt_price("n/a") == "—"


def test_header_imports_no_proxy_or_engine():
    # Tier-3 reader: the strip must not pull in the proxy or options engine — those
    # now live in the options service. Guards the migration from regressing.
    for attr in ("proxy", "scanner_engine", "vix_regime",
                 "regime_filter", "evaluate_regime", "OPTIONS_SCANNER", "sys"):
        assert not hasattr(header, attr), f"header still references {attr}"


def test_header_helpers_moved_to_service():
    # quote_last/sentiment_dot were ported to the service; not on the page anymore.
    assert not hasattr(header, "quote_last")
    assert not hasattr(header, "sentiment_dot")


def test_render_is_callable():
    assert callable(header.render)


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service not running / cold start) — the Tier-3 graceful-empty path.

    Mirrors the scanner page test: rendering inside a slot context (a card) is
    enough to exercise the widget wiring + the initial fetch-free paint.
    """
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:header") is None  # confirm empty
    with ui.card():
        ref = header.render()  # must not raise
    assert callable(ref)  # call contract: render returns a repaint callable
