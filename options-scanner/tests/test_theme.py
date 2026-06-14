import theme


def test_detect_windows_theme_returns_light_or_dark():
    result = theme.detect_windows_theme()
    assert result in ("light", "dark")


def test_detect_windows_theme_falls_back_to_dark_on_registry_error(monkeypatch):
    import winreg
    def _raise(*a, **kw): raise OSError("no registry")
    monkeypatch.setattr(winreg, "OpenKey", _raise)
    # Bust the cache so detect runs fresh
    theme._MODE_CACHE = None
    assert theme.detect_windows_theme() == "dark"
    theme._MODE_CACHE = None


def test_chrome_returns_dict_for_each_mode():
    light = theme.chrome("light")
    dark = theme.chrome("dark")
    assert isinstance(light, dict)
    assert isinstance(dark, dict)
    # Same key set in both
    assert set(light.keys()) == set(dark.keys())


def test_chrome_has_required_keys():
    required = {
        "bg", "bg_alt", "bg_panel", "bg_input", "plot_bg", "ticker_bg",
        "fg", "fg_dim", "lf_fg",
        "border",
        "btn_bg", "btn_fg", "btn_hover", "btn_active",
        "tree_bg", "tree_fg", "tree_sel_bg", "tree_head_bg", "tree_head_fg",
        "tab_bg", "tab_sel_bg", "tab_sel_fg",
        "status_bg",
    }
    chrome = theme.chrome("dark")
    missing = required - set(chrome.keys())
    assert missing == set(), f"missing keys: {missing}"


def test_chrome_default_uses_current_mode(monkeypatch):
    theme._MODE_CACHE = "light"
    light_default = theme.chrome()
    theme._MODE_CACHE = "dark"
    dark_default = theme.chrome()
    theme._MODE_CACHE = None
    assert light_default["bg"] != dark_default["bg"]


def test_trading_returns_dict():
    t = theme.trading()
    assert isinstance(t, dict)


def test_trading_endpoints_constant_across_themes():
    theme._MODE_CACHE = "light"
    light = theme.trading()
    theme._MODE_CACHE = "dark"
    dark = theme.trading()
    theme._MODE_CACHE = None
    # Endpoint colors (positive/negative) must be identical across themes —
    # they encode meaning, not theme.
    for key in ("gex_pos", "gex_neg", "charm_pos", "dex_pos", "em_line",
                "spot", "proj_flip", "dex_proj_flip",
                "heatmap_neg", "heatmap_pos",
                "term_heatmap_neg", "term_heatmap_pos"):
        assert light[key] == dark[key], f"{key} differs between themes"


def test_trading_midpoint_flips_with_theme():
    theme._MODE_CACHE = "light"
    light = theme.trading()
    theme._MODE_CACHE = "dark"
    dark = theme.trading()
    theme._MODE_CACHE = None
    # Diverging-heatmap midpoint must read against the surrounding chrome —
    # one of the few theme-dependent trading colors (documented in design §5).
    assert light["heatmap_mid"] != dark["heatmap_mid"]
    assert light["term_heatmap_mid"] != dark["term_heatmap_mid"]


def test_apply_matplotlib_sets_facecolor():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_hex
    fig, ax = plt.subplots()
    theme.apply_matplotlib(fig, [ax], mode="dark")
    assert to_hex(fig.get_facecolor()) == theme.chrome("dark")["bg"]
    assert to_hex(ax.get_facecolor()) == theme.chrome("dark")["plot_bg"]
    plt.close(fig)


def test_apply_matplotlib_sets_tick_colors():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    theme.apply_matplotlib(fig, [ax], mode="light")
    fg_dim = theme.chrome("light")["fg_dim"]
    # tick params propagates to all tick labels
    for lbl in ax.get_xticklabels():
        assert lbl.get_color() == fg_dim or lbl.get_color().lower() == fg_dim.lower()
        break  # one is enough
    plt.close(fig)


def test_apply_matplotlib_sets_spine_color():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_hex
    fig, ax = plt.subplots()
    theme.apply_matplotlib(fig, [ax], mode="dark")
    border = theme.chrome("dark")["border"]
    for spine in ax.spines.values():
        assert to_hex(spine.get_edgecolor()) == border
        break
    plt.close(fig)


def test_apply_ttk_does_not_raise():
    import tkinter as tk
    from tkinter import ttk
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest; pytest.skip("No Tk display")
    try:
        style = ttk.Style(root)
        # Should not raise on either mode
        theme.apply_ttk(style, mode="dark")
        theme.apply_ttk(style, mode="light")
    finally:
        root.destroy()
