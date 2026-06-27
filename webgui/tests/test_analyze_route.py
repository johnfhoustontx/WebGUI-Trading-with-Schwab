"""Tests for the Gamma Analyze serve-in-new-tab route (main.py)."""
import main
from nicegui import app


def test_analyze_html_prefers_payload():
    assert main.analyze_html({"html": "<x>doc</x>"}) == "<x>doc</x>"


def test_analyze_html_placeholder_when_empty():
    for payload in (None, {}, {"html": ""}, {"html": "   "}):
        assert "No Gamma analysis" in main.analyze_html(payload)


def test_analyze_route_registered():
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/options/analyze" in paths


def test_analyze_view_for_slots():
    assert main.analyze_view_for("premarket") == "options:gamma_analyze_premarket"
    assert main.analyze_view_for("open") == "options:gamma_analyze_open"
    assert main.analyze_view_for("midday") == "options:gamma_analyze_midday"
    assert main.analyze_view_for("close") == "options:gamma_analyze_close"


def test_analyze_view_for_default_is_adhoc():
    # No slot / unknown slot → the ad-hoc Analyze view.
    assert main.analyze_view_for(None) == "options:gamma_analyze"
    assert main.analyze_view_for("bogus") == "options:gamma_analyze"
