"""Project-wide light/dark theme.

Single source of truth for chrome colors (bg/fg/border/etc.) and
trading semantic colors. Theme is auto-detected from the Windows
registry once at launch; no manual override. Relaunch the app after
changing the Windows setting.

Design: docs/plans/2026-05-15-unified-theme-design.md
"""
from __future__ import annotations

from typing import Literal

ThemeMode = Literal["light", "dark"]

_MODE_CACHE: ThemeMode | None = None


# ── Chrome palette ──
_CHROME_DARK = {
    "bg":           "#1e1e1e",
    "bg_alt":       "#252526",
    "bg_panel":     "#2d2d30",
    "bg_input":     "#3c3c3c",
    "plot_bg":      "#252526",
    "ticker_bg":    "#0d1b2a",
    "fg":           "#d4d4d4",
    "fg_dim":       "#9e9e9e",
    "lf_fg":        "#569cd6",
    "border":       "#3e3e42",
    "btn_bg":       "#0e639c",
    "btn_fg":       "#ffffff",
    "btn_hover":    "#1177bb",
    "btn_active":   "#094771",
    "tree_bg":      "#1e1e1e",
    "tree_fg":      "#d4d4d4",
    "tree_sel_bg":  "#094771",
    "tree_head_bg": "#333333",
    "tree_head_fg": "#d4d4d4",
    "tab_bg":       "#2d2d30",
    "tab_sel_bg":   "#007acc",
    "tab_sel_fg":   "#ffffff",
    "status_bg":    "#252526",
}

_CHROME_LIGHT = {
    "bg":           "#f0f0f0",
    "bg_alt":       "#e8e8e8",
    "bg_panel":     "#ffffff",
    "bg_input":     "#ffffff",
    "plot_bg":      "#fafafa",
    "ticker_bg":    "#1b2a4a",
    "fg":           "#212121",
    "fg_dim":       "#555555",
    "lf_fg":        "#0078d4",
    "border":       "#c0c0c0",
    "btn_bg":       "#0078d4",
    "btn_fg":       "#ffffff",
    "btn_hover":    "#1a88e0",
    "btn_active":   "#005a9e",
    "tree_bg":      "#ffffff",
    "tree_fg":      "#212121",
    "tree_sel_bg":  "#cce5ff",
    "tree_head_bg": "#e8e8e8",
    "tree_head_fg": "#212121",
    "tab_bg":       "#e0e0e0",
    "tab_sel_bg":   "#0078d4",
    "tab_sel_fg":   "#ffffff",
    "status_bg":    "#e0e0e0",
}


# ── Trading semantic palette ──
# Same in both themes EXCEPT diverging-heatmap midpoints (see design §5).
_TRADING_COMMON = {
    "gex_pos":         "#4169E1",  # CYAN — royal blue
    "gex_neg":         "#B22222",  # firebrick dark red
    "charm_pos":       "#9370DB",  # medium purple
    "dex_pos":         "#ffd700",  # gold
    "vanna_pos":       "#a78bfa",  # soft violet (distinct from charm purple)
    "em_line":         "#80d8ff",  # light sky blue
    "em_text":         "#80d8ff",
    "spot":            "#ffffff",
    "proj_flip":       "#7CFC00",
    "dex_proj_flip":   "#ffb300",
    "heatmap_neg":     "#ff4d4d",
    "heatmap_pos":     "#4da6ff",
    "term_heatmap_neg": "#c0392b",
    "term_heatmap_pos": "#2980b9",
}

_TRADING_DARK_MIDPOINTS = {
    "heatmap_mid":      "#0a0e27",  # vanish against dark chrome
    "term_heatmap_mid": "#ffffff",  # contrast on dark plot bg
}

_TRADING_LIGHT_MIDPOINTS = {
    "heatmap_mid":      "#ffffff",
    "term_heatmap_mid": "#f0f0f0",
}


def detect_windows_theme() -> ThemeMode:
    """Read Windows AppsUseLightTheme. Caches result; returns 'dark' on error."""
    global _MODE_CACHE
    if _MODE_CACHE is not None:
        return _MODE_CACHE
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        _MODE_CACHE = "light" if val == 1 else "dark"
    except Exception:
        _MODE_CACHE = "dark"
    return _MODE_CACHE


def current_mode() -> ThemeMode:
    return detect_windows_theme()


def chrome(mode: ThemeMode | None = None) -> dict:
    if mode is None:
        mode = current_mode()
    return dict(_CHROME_LIGHT) if mode == "light" else dict(_CHROME_DARK)


def trading(mode: ThemeMode | None = None) -> dict:
    if mode is None:
        mode = current_mode()
    out = dict(_TRADING_COMMON)
    if mode == "light":
        out.update(_TRADING_LIGHT_MIDPOINTS)
    else:
        out.update(_TRADING_DARK_MIDPOINTS)
    return out


def apply_matplotlib(fig, axes, mode: ThemeMode | None = None) -> None:
    """Apply chrome to a matplotlib Figure + iterable of Axes.

    Sets figure facecolor, axes facecolor, tick colors, axis-label colors,
    title color, and spine colors. Does NOT touch artists rendered in
    trading() colors — those encode meaning, not theme.
    """
    c = chrome(mode)
    fig.set_facecolor(c["bg"])
    for ax in axes:
        ax.set_facecolor(c["plot_bg"])
        ax.tick_params(axis="both", colors=c["fg_dim"])
        ax.xaxis.label.set_color(c["fg_dim"])
        ax.yaxis.label.set_color(c["fg_dim"])
        ax.title.set_color(c["fg"])
        for spine in ax.spines.values():
            spine.set_color(c["border"])


def apply_ttk(style, mode: ThemeMode | None = None) -> None:
    """Apply chrome to a ttk.Style.

    Generalisation of dashboard.py's existing _configure_styles body so
    any subsystem with a ttk.Style can reuse the styling. Idempotent —
    calling twice does not error.
    """
    c = chrome(mode)
    try:
        style.theme_use("clam")  # clam is most customisable for theming
    except Exception:
        pass  # already set, or unavailable

    # Global defaults
    style.configure(".", background=c["bg"], foreground=c["fg"], borderwidth=0)
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("TLabelframe", background=c["bg"], foreground=c["lf_fg"],
                    bordercolor=c["border"])
    style.configure("TLabelframe.Label", background=c["bg"], foreground=c["lf_fg"])
    style.configure("TEntry", fieldbackground=c["bg_input"], foreground=c["fg"],
                    insertcolor=c["fg"], bordercolor=c["border"])
    style.configure("TSpinbox", fieldbackground=c["bg_input"], foreground=c["fg"],
                    arrowcolor=c["fg"], bordercolor=c["border"])
    style.configure("TCombobox", fieldbackground=c["bg_input"], foreground=c["fg"],
                    arrowcolor=c["fg"], bordercolor=c["border"])
    style.configure("TPanedwindow", background=c["bg"])
    style.configure("TSeparator", background=c["border"])

    # Buttons
    style.configure("TButton", background=c["btn_bg"], foreground=c["btn_fg"],
                    bordercolor=c["btn_bg"], focusthickness=0)
    style.map("TButton",
              background=[("active", c["btn_hover"]), ("pressed", c["btn_active"])],
              foreground=[("disabled", c["fg_dim"])])

    # Notebook tabs
    style.configure("TNotebook", background=c["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=c["tab_bg"], foreground=c["fg"],
                    padding=[12, 4], borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", c["tab_sel_bg"])],
              foreground=[("selected", c["tab_sel_fg"])])

    # Treeview
    style.configure("Treeview", background=c["tree_bg"], foreground=c["tree_fg"],
                    fieldbackground=c["tree_bg"], bordercolor=c["border"])
    style.configure("Treeview.Heading", background=c["tree_head_bg"],
                    foreground=c["tree_head_fg"], borderwidth=0)
    style.map("Treeview", background=[("selected", c["tree_sel_bg"])])

    # Toolbar + status bar named styles (dashboard-specific)
    style.configure("Toolbar.TFrame", background=c["bg_alt"])
    style.configure("Status.TFrame", background=c["status_bg"])
