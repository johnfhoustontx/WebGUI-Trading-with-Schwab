"""Regression test for build_chart_style_vars.

A size-only chart-style entry (no "color" key) must not raise KeyError — that
crashed GammaWindow init (the 'Level Label Text' entry).
"""
import pytest

import gamma_tool


@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no Tk display available")
    root.withdraw()
    yield root
    root.destroy()


def test_build_style_vars_allows_size_only_entry(tk_root):
    stl = gamma_tool.build_chart_style_vars({
        "Spot Line": {"color": "#ffffff", "thickness": 0.3, "linestyle": "--"},
        "Level Label Text": {"size": 7},                  # size-only (the bug)
        "Term Hover Text": {"color": "#ffffff", "size": 8},
    })
    # size-only entry built without a color var, no KeyError
    assert "color" not in stl["Level Label Text"]
    assert int(stl["Level Label Text"]["size"].get()) == 7
    # full entry still gets every prop
    assert stl["Spot Line"]["color"].get() == "#ffffff"
    assert stl["Spot Line"]["linestyle"].get() == "--"
    assert stl["Term Hover Text"]["color"].get() == "#ffffff"


def test_build_style_vars_real_defaults_have_no_crash(tk_root):
    # Mixed shapes: color-only, size-only, thickness+linestyle.
    stl = gamma_tool.build_chart_style_vars({
        "Heatmap Positive": {"color": "#123456"},          # color-only
        "Level Label Text": {"size": 7},                   # size-only
        "Zero Line": {"color": "#abcdef", "thickness": 0.8},
    })
    assert set(stl["Heatmap Positive"]) == {"color"}
    assert set(stl["Level Label Text"]) == {"size"}
    assert set(stl["Zero Line"]) == {"color", "thickness"}
