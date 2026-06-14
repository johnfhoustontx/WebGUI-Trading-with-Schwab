"""Tests for webgui/main.py — the NiceGUI nav shell.

These import the module (which runs the @ui.page decorators) and inspect the
NiceGUI page registry; they do NOT start the server.
"""
from nicegui import Client


def test_shell_registers_five_pages():
    """The shell registers one route per feature page."""
    import main  # noqa: F401  -- importing registers the @ui.page routes

    routes = set(Client.page_routes.values())
    for path in ("/", "/sentiment", "/trade", "/portfolio", "/driver"):
        assert path in routes, f"missing page route {path}; have {sorted(routes)}"


def test_shell_imports_proxy_for_banner():
    """The shell wires the proxy health helper for the down-banner."""
    import main

    assert hasattr(main, "proxy")
    assert callable(main.proxy.health)
