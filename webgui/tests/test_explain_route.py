"""Tests for the Gamma Explain serve-in-new-tab route (main.py)."""
import main
from nicegui import app


def test_explain_html_prefers_payload():
    assert main.explain_html({"html": "<x>doc</x>"}) == "<x>doc</x>"


def test_explain_html_placeholder_when_empty():
    for payload in (None, {}, {"html": ""}, {"html": "   "}):
        assert "No Gamma Explain" in main.explain_html(payload)


def test_explain_route_registered():
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/options/explain" in paths
