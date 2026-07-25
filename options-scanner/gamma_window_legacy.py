"""Legacy Tk desktop window for the gamma tool - PARKED, NOT IN USE.

This is the ``GammaWindow(tk.Toplevel)`` GUI that the original options-scanner
desktop app (``dashboard.py``) spawned. The project's UI moved to NiceGUI
(``webgui/pages/options/gamma.py``); nothing constructs this class any more and
it has no live entry point.

It was split out of ``gamma_tool.py`` on 2026-07-25 because its module-level
``tkinter`` / ``matplotlib`` / ``matplotlib.use("TkAgg")`` imports were being
paid by every HEADLESS importer of the engine (``services/options_svc``,
``gex_collector``, ``scanner_engine``) - ~0.7 s and a GUI toolkit loaded into
server processes, plus a process-wide TkAgg backend. ``gamma_tool`` is now the
pure engine; this module is the GUI half.

Kept rather than deleted so the original rendering/layout logic remains readable
if the desktop view is ever revived. Nothing in the running stack imports it -
treat it as reference material.

Test coverage is incidental: ``tests/test_heatmap.py`` borrows
``GammaWindow._fetch_last_close`` (a GUI-only price-history helper) to test its
fetch/cache behaviour. Everything else here is untested.

Note the sibling entrypoint ``dashboard.py`` that constructed this window was
never copied into the webgui monorepo, which is why the ``tests/test_dashboard_*``
modules fail with ``ModuleNotFoundError`` - pre-existing and unrelated.
"""

import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import ttk, colorchooser

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.ticker as mticker
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap, to_rgba
import numpy as np

# Public engine helpers (GammaEngine, build_analysis_dict, draw_term_heatmap,
# calc_flip_point, the colour/theme constants, ...).
from gamma_tool import *  # noqa: F401,F403
# Private module-level helpers this window reaches for (not covered by ``import *``).
from gamma_tool import (  # noqa: F401
    _FIRE_TIME_TO_SLOT,
    _RETROSPECTIVE_SLOTS,
    _drift_headline_text,
    _fetch_market_internals,
    _fmt_dollar_magnitude,
    _format_dollars,
    _history_db,
    _load_vix_today,
    _term_walls_from_rows,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GammaWindow — Toplevel GUI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GammaWindow(tk.Toplevel):
    """GEX visualization window spawned from the dashboard."""

    REFRESH_INTERVAL = 300  # seconds

    def __init__(self, master, client, symbol="$SPX"):
        super().__init__(master)
        self.title("GEX Scanner — Gamma Exposure by Strike")
        self.geometry("1200x720")
        self.configure(bg=BG_MAIN)
        self.minsize(900, 500)
        self._chrome = theme.chrome()
        self._trading = theme.trading()

        self._client = client
        self._engine = GammaEngine()
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()
        self._client_lock = threading.Lock()
        self._collector_thread = None
        self._collector_owner = f"gamma:{os.getpid()}"
        self._collector_external = False
        # Dealer Pinch detector: cached daily candles (vol index + underlying)
        # and the latest computed state (rendered by the status panel).
        self._pinch_hist_cache = {}
        self._last_pinch_state = None
        self._countdown = self.REFRESH_INTERVAL
        self._last_em = None

        # ── Intraday history (read from SQLite, written by gex_collector) ──
        try:
            self._db = _history_db.connect(read_only=True)
        except sqlite3.OperationalError:
            self._db = None  # collector DB doesn't exist yet
        self._show_history_var = tk.BooleanVar(value=True)
        self._show_heatmap_var = tk.BooleanVar(value=True)

        # ── Config vars ──
        self._symbol_var = tk.StringVar(value=symbol)
        self._view_var = tk.StringVar(value="gex")   # "gex" | "charm" | "dex" | "vanna" | "term"
        self._display_var = tk.StringVar(value="net")
        self._grouping_var = tk.DoubleVar(value=1)
        self._show_prev_var = tk.BooleanVar(value=False)
        self._show_open_var = tk.BooleanVar(value=False)
        self._show_em_var = tk.BooleanVar(value=True)
        self._formula_var = tk.StringVar(value="oi")
        self._charm_data = None  # charm snapshot (same structure as GEX)
        self._analyze_inflight = False  # guard against overlapping _analyze runs
        self._dex_data = None   # DEX snapshot (same structure as GEX)
        # Prior + open snapshots for charm/DEX, parallelling engine.previous /
        # engine.market_open (which only track GEX). Used by the Analyze prompt
        # to surface intraday delta-change and open-vs-now context.
        self._prev_charm_data = None
        self._open_charm_data = None
        self._prev_dex_data = None
        self._open_dex_data = None
        self._vanna_data = None  # vanna snapshot (same structure as GEX)
        self._prev_vanna_data = None
        self._open_vanna_data = None
        self._last_close_cache = {}        # {symbol: close_float or None}
        self._last_close_attempted = set() # {symbol} — prevents retry spam on failure
        # Forward-band cache: {(symbol, view): (last_fetch_ts, strikes, times, matrix)}
        self._fwd_cache = {}

        # Scheduled auto-analyze — fires _analyze(auto=True) at fixed times CT.
        # Instance list (not module constant) so future UI work can expose it.
        self._auto_analyze_times = [(8, 19), (8, 44), (9, 59), (12, 59), (14, 59)]
        self._auto_analyze_timer_id = None

        # Bar hover state — populated by _redraw's successful path, consumed by
        # _on_bar_hover. Empty when no data is rendered.
        self._hover_strikes = []       # list[float]
        self._hover_grid = {}          # {strike: {"call", "put", "net"}}
        self._hover_bar_height = 1.0   # float — hit-test tolerance
        self._hover_view = "gex"       # "gex" | "charm" | "dex" | "vanna"
        self._hover_annotation = None  # matplotlib annotation artist (lazy)

        self._setup_win = None  # Tracks the Chart Setup Toplevel for single-instance

        # ── Chart style vars (configurable via Setup popup) ──
        self._stl = {}
        _defaults = {
            "GEX+ Bars":        {"color": self._trading["gex_pos"],   "size": 0.85},
            "Charm+ Bars":      {"color": self._trading["charm_pos"], "size": 0.85},
            "DEX+ Bars":        {"color": self._trading["dex_pos"],   "size": 0.85},
            "Vanna+ Bars":      {"color": self._trading["vanna_pos"], "size": 0.85},
            "Negative Bars":    {"color": self._trading["gex_neg"],   "size": 0.85},
            "Ghost Bars":       {"color": self._trading["dex_pos"],   "size": 0.25},  # size = alpha
            "Spot Line":        {"color": self._trading["spot"],          "thickness": 0.3, "linestyle": "--"},
            "Proj Flip Line":   {"color": self._trading["proj_flip"],     "thickness": 0.6, "linestyle": ":"},
            "DEX Proj Flip":    {"color": self._trading["dex_proj_flip"], "thickness": 0.6, "linestyle": "--"},
            "EM Lines":         {"color": self._trading["em_line"],       "thickness": 0.5, "linestyle": "--"},
            "Max Pain Line":    {"color": "#3fd0c9",                       "thickness": 0.7, "linestyle": "-."},
            "Max Pain Text":    {"color": "#3fd0c9",                       "size": 9},
            "Call Wall Line":   {"color": self._trading["gex_pos"],        "thickness": 0.7, "linestyle": "-"},
            "Put Wall Line":    {"color": self._trading["gex_neg"],        "thickness": 0.7, "linestyle": "-"},
            "Level Label Text": {"size": 7},
            "Term Hover Text":  {"color": "#ffffff",                       "size": 8},
            "Spot Text":        {"color": self._trading["spot"],          "size": 12},
            "Proj Flip Text":   {"color": self._trading["proj_flip"],     "size": 12},
            "DEX Flip Text":    {"color": self._trading["dex_proj_flip"], "size": 12},
            "EM Text":          {"color": self._trading["em_text"],       "size": 7},
            "Title":            {"color": FG_PRIMARY, "size": 12},
            "Axis Ticks":       {"color": FG_DIM,     "size": 10},
            "Axis Labels":      {"color": FG_DIM,     "size": 9},
            "Flip Line":        {"color": WHITE,      "thickness": 1.6, "linestyle": "-"},
            "Top+ Line":        {"color": self._trading["gex_pos"], "thickness": 1.3, "linestyle": ":"},
            "Top- Line":        {"color": self._trading["gex_neg"], "thickness": 1.3, "linestyle": ":"},
            "Zero Line":        {"color": FG_DIM,     "thickness": 0.8},
            "Grid Lines":       {"color": FG_DIM,     "thickness": 0.5},
            "Heatmap Positive": {"color": self._trading["heatmap_pos"]},
            "Heatmap Negative": {"color": self._trading["heatmap_neg"]},
            "Heatmap Midpoint": {"color": self._trading["heatmap_mid"]},
            # Term-structure heatmap — separate palette from the time-evolution
            # heatmap above so users can tune the two views independently.
            "Term Heatmap Negative":  {"color": self._trading["term_heatmap_neg"]},
            "Term Heatmap Midpoint":  {"color": self._trading["term_heatmap_mid"]},
            "Term Heatmap Positive":  {"color": self._trading["term_heatmap_pos"]},
        }
        # Snapshot defaults (shallow-copy each entry) for Reset-to-Defaults
        # support in the Chart Setup popup (Task 3). We copy values because
        # _defaults is a local variable; we need it to survive after the
        # population loop finishes and __init__ returns.
        self._stl_defaults = {
            key: dict(defs) for key, defs in _defaults.items()
        }
        self._stl.update(build_chart_style_vars(_defaults))

        # Convenience aliases
        self._clr_gex_pos = self._stl["GEX+ Bars"]["color"]
        self._clr_charm_pos = self._stl["Charm+ Bars"]["color"]
        self._clr_dex_pos = self._stl["DEX+ Bars"]["color"]
        self._clr_vanna_pos = self._stl["Vanna+ Bars"]["color"]
        self._clr_neg = self._stl["Negative Bars"]["color"]
        self._clr_em = self._stl["EM Lines"]["color"]
        self._clr_spot = self._stl["Spot Line"]["color"]
        self._clr_proj = self._stl["Proj Flip Line"]["color"]

        # Overlay any saved chart-style values from data/chart_style.json
        # over the defaults we just populated. Silent on first-run (no file).
        self._load_chart_style()

        self._build_ui()
        self._start_worker()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Arm the daily auto-analyze scheduler.
        self._schedule_next_auto_analyze()

    # ── UI Construction ──

    def _build_ui(self):
        container = tk.Frame(self, bg=BG_MAIN)
        container.pack(fill="both", expand=True, padx=3, pady=3)

        # Pack all four frames first in visual order so Tk's layout is correct
        # regardless of the order we populate children below.
        top_bar = tk.Frame(container, bg=BG_PANEL)
        top_bar.pack(side="top", fill="x", pady=(0, 2))
        view_bar = tk.Frame(container, bg=BG_PANEL)
        view_bar.pack(side="top", fill="x", pady=(0, 2))
        bottom = tk.Frame(container, bg=BG_PANEL)
        bottom.pack(side="bottom", fill="x", pady=(2, 0))
        chart_frame = tk.Frame(container, bg=BG_MAIN)
        chart_frame.pack(side="top", fill="both", expand=True)

        # Populate in DEPENDENCY order, not visual order. _build_view_toggle
        # ends with self._set_view(...) which fires a full _redraw, and
        # _redraw touches:
        #   - self._ax_bars / self._ax_heat   (from _build_chart)
        #   - self._pressure_frame + _pressure_label_*  (from _build_bottom_strip)
        #   - self._status_label               (from _build_bottom_strip)
        # So chart AND bottom strip must be built before view toggle fires.
        self._build_top_bar(top_bar)
        self._build_chart(chart_frame)     # creates _ax_bars, _ax_heat
        self._build_bottom_strip(bottom)   # creates _pressure_frame, _status_label, buttons
        self._build_view_toggle(view_bar)  # LAST — its _set_view → _redraw is now safe

    def _save_chart_style(self):
        """Flatten self._stl's tk vars into a dict, dump to data/chart_style.json.

        Called from every Chart Setup popup edit callback (Task 3). Failures
        are logged (warning level) but never raised — persistence is
        best-effort and must not break the UI edit flow.
        """
        path = Path(__file__).parent / "data" / "chart_style.json"
        path.parent.mkdir(exist_ok=True)
        dump = {
            key: {prop: var.get() for prop, var in entry.items()}
            for key, entry in self._stl.items()
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dump, f, indent=2)
        except Exception as e:
            log.warning("Failed to save chart style: %s", e)

    def _load_chart_style(self):
        """Overlay saved values from data/chart_style.json onto _stl defaults.

        Silent on first-run (no file). Tolerant to:
          - Missing keys (defaults added after file was saved) — skip, keep default
          - Unknown keys (future file loaded by older code) — skip silently
          - Type mismatches on individual props — skip that prop, continue
          - Corrupt JSON — log warning, fall back to defaults entirely

        Never raises — __init__ must not fail because of a bad config file.
        """
        path = Path(__file__).parent / "data" / "chart_style.json"
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
        except Exception as e:
            log.warning("Failed to load chart style (using defaults): %s", e)
            return
        if not isinstance(saved, dict):
            log.warning("chart_style.json is not a dict; using defaults")
            return
        for key, props in saved.items():
            if key not in self._stl:
                continue  # Unknown element — skip silently
            if not isinstance(props, dict):
                continue  # Malformed entry — skip
            for prop, value in props.items():
                if prop in self._stl[key]:
                    try:
                        self._stl[key][prop].set(value)
                    except Exception:
                        pass  # Type mismatch or tk error — skip this prop

    def _reset_chart_style(self):
        """Restore every _stl var to its default value, redraw, and persist.

        Used by the Reset-to-Defaults button in the Chart Setup popup.
        Overwrites data/chart_style.json with default values so the next
        launch also loads defaults.
        """
        for key, defs in self._stl_defaults.items():
            if key not in self._stl:
                continue
            for prop, value in defs.items():
                if prop in self._stl[key]:
                    try:
                        self._stl[key][prop].set(value)
                    except Exception:
                        pass
        self._redraw()
        self._save_chart_style()

    def _open_chart_setup(self):
        """Open a popup window with dropdown-driven style controls.

        Single-instance: subsequent opens while a popup exists lift the
        existing window. Every edit live-saves to data/chart_style.json
        via self._save_chart_style() in each callback.
        """
        if getattr(self, "_setup_win", None) is not None \
                and self._setup_win.winfo_exists():
            self._setup_win.lift()
            return

        win = tk.Toplevel(self)
        win.title("Chart Setup")
        win.configure(bg=BG_MAIN)
        win.geometry("340x420")
        win.resizable(False, False)
        self._setup_win = win

        tk.Label(win, text="Element:", bg=BG_MAIN, fg=FG_PRIMARY,
                 font=(FONT, 10)).pack(anchor="w", padx=12, pady=(12, 2))
        element_var = tk.StringVar(value=list(self._stl.keys())[0])
        element_cb = ttk.Combobox(win, textvariable=element_var,
                                  values=list(self._stl.keys()),
                                  state="readonly", width=30)
        element_cb.pack(padx=12, pady=(0, 8))

        controls = tk.Frame(win, bg=BG_PANEL)
        controls.pack(fill="both", expand=True, padx=12, pady=4)

        def _populate(event=None):
            """Rebuild the controls frame for the currently-selected element."""
            for w in controls.winfo_children():
                w.destroy()
            key = element_var.get()
            entry = self._stl[key]
            row_pad = {"padx": 8, "pady": 6}
            lbl_kw = {"bg": BG_PANEL, "fg": FG_PRIMARY, "font": (FONT, 9)}

            # Color picker
            if "color" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                tk.Label(row, text="Color", **lbl_kw).pack(side="left")
                swatch = tk.Button(row, width=4, bg=entry["color"].get(),
                                   relief="flat", cursor="hand2",
                                   activebackground=entry["color"].get())
                swatch.pack(side="right")
                cvar = entry["color"]

                def _pick(v=cvar, s=swatch):
                    result = colorchooser.askcolor(
                        color=v.get(), title="Choose color")
                    if result and result[1]:
                        v.set(result[1])
                        s.configure(bg=result[1], activebackground=result[1])
                        self._redraw()
                        self._save_chart_style()

                swatch.configure(command=_pick)

            # Size / Alpha slider
            if "size" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                is_bar = "Bars" in key
                is_ghost = (key == "Ghost Bars")
                lbl_text = "Alpha" if is_ghost else "Size"
                tk.Label(row, text=lbl_text, **lbl_kw).pack(side="left")
                size_var = entry["size"]
                if is_ghost:
                    from_, to_, res = 0.05, 1.0, 0.05
                elif is_bar:
                    from_, to_, res = 0.5, 1.5, 0.05
                else:
                    from_, to_, res = 4, 24, 1
                scale = tk.Scale(
                    row, from_=from_, to=to_, resolution=res,
                    orient="horizontal", variable=size_var,
                    bg=BG_PANEL, fg=FG_PRIMARY, troughcolor=BG_INPUT,
                    highlightthickness=0, length=150,
                    command=lambda v: (self._redraw(),
                                       self._save_chart_style()),
                )
                scale.pack(side="right")

            # Thickness slider
            if "thickness" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                tk.Label(row, text="Thickness", **lbl_kw).pack(side="left")
                thick_var = entry["thickness"]
                scale = tk.Scale(
                    row, from_=0.1, to=4.0, resolution=0.1,
                    orient="horizontal", variable=thick_var,
                    bg=BG_PANEL, fg=FG_PRIMARY, troughcolor=BG_INPUT,
                    highlightthickness=0, length=150,
                    command=lambda v: (self._redraw(),
                                       self._save_chart_style()),
                )
                scale.pack(side="right")

            # Line-style radios
            if "linestyle" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                tk.Label(row, text="Line Style", **lbl_kw).pack(side="left")
                ls_var = entry["linestyle"]
                ls_options = [("-", "Solid"), ("--", "Dashed"),
                              (":", "Dotted"), ("-.", "Dash-Dot")]
                ls_frame = tk.Frame(row, bg=BG_PANEL)
                ls_frame.pack(side="right")
                for val, label in ls_options:
                    tk.Radiobutton(
                        ls_frame, text=label, variable=ls_var, value=val,
                        bg=BG_PANEL, fg=FG_PRIMARY, selectcolor=BG_INPUT,
                        activebackground=BG_PANEL, activeforeground=WHITE,
                        font=(FONT, 8),
                        command=lambda: (self._redraw(),
                                         self._save_chart_style()),
                    ).pack(side="left", padx=2)

        element_cb.bind("<<ComboboxSelected>>", _populate)
        _populate()

        # Bottom button row — Reset on the left, Close on the right.
        btn_row = tk.Frame(win, bg=BG_MAIN)
        btn_row.pack(fill="x", padx=12, pady=(8, 12))
        tk.Button(
            btn_row, text="Reset to Defaults",
            command=lambda: (self._reset_chart_style(), _populate()),
            bg=BG_INPUT, fg=FG_PRIMARY, activebackground=BG_PANEL,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        ).pack(side="left")
        tk.Button(
            btn_row, text="Close", command=win.destroy,
            bg=BG_INPUT, fg=FG_PRIMARY, activebackground=BG_PANEL,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        ).pack(side="right")

    def _build_top_bar(self, parent):
        # Hamburger menu — collapses the former config panel into a single
        # dropdown attached to the top-left menubutton.
        self._hamburger_menu = tk.Menu(
            parent, tearoff=0,
            bg=BG_PANEL, fg=FG_PRIMARY,
            activebackground=BG_INPUT, activeforeground=WHITE,
        )

        # selectcolor for radio/check indicators — Tk defaults to black on
        # every entry, which is invisible against BG_PANEL (theme chrome panel).
        # Must be set per-entry on tk.Menu (not widget-wide). WHITE keeps
        # the currently-selected option clearly marked against the dark theme.
        _sel = {"selectcolor": WHITE}

        # Display radios.
        for text, val in [("Display: Net", "net"),
                          ("Display: Calls Only", "call"),
                          ("Display: Puts Only", "put")]:
            self._hamburger_menu.add_radiobutton(
                label=text, variable=self._display_var, value=val,
                command=self._redraw, **_sel,
            )
        self._hamburger_menu.add_separator()

        # Grouping radios.
        for g in (0.1, 0.5, 1, 5, 10, 25):
            self._hamburger_menu.add_radiobutton(
                label=f"Grouping: {g}", variable=self._grouping_var,
                value=float(g), command=self._redraw, **_sel,
            )
        self._hamburger_menu.add_separator()

        # GEX Formula radios — formula changes require a refresh, not just a
        # redraw, since the underlying exposure numbers are recomputed.
        self._hamburger_menu.add_radiobutton(
            label="GEX Formula: OI", variable=self._formula_var,
            value="oi", command=self._on_formula_change, **_sel,
        )
        self._hamburger_menu.add_radiobutton(
            label="GEX Formula: Volume", variable=self._formula_var,
            value="volume", command=self._on_formula_change, **_sel,
        )
        self._hamburger_menu.add_separator()

        # Overlay toggles — preserve every checkbox from the old config panel
        # so all existing redraw branches keep firing.
        self._hamburger_menu.add_checkbutton(
            label="Show Previous", variable=self._show_prev_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show Market Open", variable=self._show_open_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show +/-1s Straddle", variable=self._show_em_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show History Overlay", variable=self._show_history_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show Heatmap", variable=self._show_heatmap_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_separator()
        self._hamburger_menu.add_command(
            label="\u2699 Chart Setup\u2026",
            command=self._open_chart_setup,
        )
        self._hamburger_menu.add_separator()
        self._hamburger_menu.add_command(
            label="\u2139  What do these levels mean?",
            command=self._open_key_levels_doc,
        )

        # tk.Menubutton + menu= is flaky on Windows (clicking doesn't post the
        # menu on recent Tk builds). Use a regular Button that calls tk_popup()
        # with the button's screen coords — reliable across platforms.
        self._hamburger_btn = tk.Button(
            parent, text="\u2630",
            bg=BG_PANEL, fg=FG_PRIMARY, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 14, "bold"),
            relief="flat", cursor="hand2", width=3,
            command=self._show_hamburger_menu,
        )
        self._hamburger_btn.pack(side="left", padx=(6, 4), pady=3)

        # Symbol combobox.
        sym_cb = ttk.Combobox(
            parent, textvariable=self._symbol_var,
            values=["$SPX", "$VIX", "SPY", "QQQ"],
            state="readonly", width=8,
        )
        sym_cb.pack(side="left", padx=4, pady=3)
        sym_cb.bind("<<ComboboxSelected>>", lambda e: self._on_symbol_change())

        # Header label doubles as the former "config_title" + status summary.
        # _set_view still configures ._config_title.text on view change, so keep
        # that attribute pointing at this label.
        self._config_title = tk.Label(
            parent, text="GEX Settings", bg=BG_PANEL, fg=CYAN,
            font=(FONT, 10, "bold"),
        )
        self._config_title.pack(side="left", padx=(8, 0), pady=3)

        # Countdown on the far right of the top bar.
        self._countdown_lbl = tk.Label(
            parent, text="Next refresh: --:--", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9),
        )
        self._countdown_lbl.pack(side="right", padx=(4, 8), pady=3)

        # Explain action button — a raised, gold 3D button (deliberately NOT
        # styled like the flat view-toggle tabs, since it's an action, not a
        # view). Sits just left of the refresh countdown.
        self._btn_explain = tk.Button(
            parent, text="\u2753 Explain",
            command=self._show_explain,
            bg="#FFD700", fg="#1a1a1a",
            activebackground="#FFE34D", activeforeground="#1a1a1a",
            font=(FONT, 9, "bold"), relief="raised", bd=3,
            padx=10, pady=2, cursor="hand2",
        )
        self._btn_explain.pack(side="right", padx=(0, 4), pady=3)
        # (Dealer Pinch is folded into the Explain page; no separate button.)

        # Status rows promoted from the old bottom strip — packed below the
        # main top-bar controls so the chart can reclaim the vertical space.
        status_frame = tk.Frame(parent, bg=BG_PANEL)
        status_frame.pack(side="bottom", fill="x")

        # Row 1: free-form status (symbol | spot | DTE | strike count | formula)
        # and refresh/analyze feedback. Right-aligned: 0-DTE pressure panel
        # (DEX view only).
        row1 = tk.Frame(status_frame, bg=BG_PANEL)
        row1.pack(side="top", fill="x")

        self._status_lbl = tk.Label(
            row1, text="Initializing...", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9), anchor="w", justify="left",
        )
        self._status_lbl.pack(side="left", anchor="w", padx=8, pady=(2, 0))

        # 0-DTE pressure panel lives on the right side of row 1 (DEX view
        # only). _update_pressure_panel handles pack/pack_forget.
        self._pressure_frame = tk.Frame(row1, bg=BG_PANEL)
        self._pressure_label_hedge = tk.Label(
            self._pressure_frame, text="", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9, "bold"),
        )
        self._pressure_label_proj = tk.Label(
            self._pressure_frame, text="", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9),
        )
        self._pressure_label_now = tk.Label(
            self._pressure_frame, text="", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9),
        )
        # Pack right-to-left so they read left-to-right: now | proj | hedge.
        self._pressure_label_hedge.pack(side="right", padx=(6, 8))
        self._pressure_label_proj.pack(side="right", padx=6)
        self._pressure_label_now.pack(side="right", padx=6)
        # Do NOT pack self._pressure_frame here — deferred to
        # _update_pressure_panel.

        # Term-view header label: 'Underlying X | MVC Y'. Only packed when
        # _show_term_view is active (see _show_term_header / _hide_term_header).
        self._term_header_lbl = tk.Label(
            row1, text="", bg=BG_PANEL, fg=CYAN,
            font=(FONT, 10, "bold"),
        )
        # Not packed by default — only shown in term view.

        # Row 2: collector health status (left) + view-aware key-levels
        # headline (right). Both packed into a shared sub-frame so the
        # headline can sit beside the status label on the same line.
        row2 = tk.Frame(status_frame, bg=BG_PANEL)
        row2.pack(side="top", fill="x")

        self._status_label = tk.Label(
            row2, text="", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9), anchor="w", justify="left",
        )
        self._status_label.pack(side="left", anchor="w", padx=8, pady=(0, 2))

        # Key-levels headline strip retired (values now render on the chart).
        # Reuse the right side of row 2 for the Dealer Pinch flag.
        self._pinch_lbl = tk.Label(
            row2, text="", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9), anchor="e", justify="right",
        )
        self._pinch_lbl.pack(side="right", anchor="e", padx=(6, 12), pady=(0, 2))

    def _pinch_candles(self, symbol):
        """Cached daily candles for ``symbol`` (refetched at most hourly).

        Used for the vol-index IV percentile and the underlying realized-vol
        trend. Network fetch is serialized on the shared client lock; callers
        run on the worker thread so the UI never blocks.
        """
        import time as _t
        ent = self._pinch_hist_cache.get(symbol)
        now = _t.time()
        if ent and (now - ent[0]) < 3600:
            return ent[1]
        candles = []
        try:
            from scanner_engine import fetch_price_history
            with self._client_lock:
                hist = fetch_price_history(self._client, symbol)
            candles = (hist or {}).get("candles") or []
        except Exception:
            log.debug("pinch candle fetch failed for %s", symbol, exc_info=True)
        self._pinch_hist_cache[symbol] = (now, candles)
        return candles

    def _compute_pinch_state(self, chain, spot, dte, expected_move,
                             gex_result=None, forced_hedge_dir=None):
        """Worker-thread Dealer Pinch evaluation. Fetches the vol-index IV
        percentile (SPX/SPY→$VIX, QQQ→$VXN) and the underlying RV trend, then
        calls the pure evaluator. Returns the state dict or None; never raises.
        """
        if chain is None or not spot:
            return None
        try:
            symbol = self._symbol_var.get()
            vix_sym = "$VXN" if symbol.upper() == "QQQ" else "$VIX"
            iv_pctile = None
            vc = self._pinch_candles(vix_sym)
            closes = [c["close"] for c in vc][-30:]
            if closes:
                iv_pctile = percentile_rank(closes, closes[-1])
            rv_trend = None
            uc = self._pinch_candles(symbol)
            if uc:
                rv_trend = realized_vol_trend(uc)
            node = dominant_oi_node(chain).get("node")
            pr = pin_risk(spot, node, expected_move) if node is not None else None
            flip = None
            if gex_result:
                try:
                    flip = GammaEngine.snapshot_summary(gex_result).get("flip")
                except Exception:
                    flip = None
            return evaluate_dealer_pinch(
                symbol=symbol, chain=chain, spot=spot, dte=dte,
                iv_pctile=iv_pctile, rv_trend=rv_trend, gex_flip=flip,
                pin_risk_score=pr, forced_hedge_dir=forced_hedge_dir)
        except Exception:
            log.debug("pinch state compute failed", exc_info=True)
            return None

    def _open_key_levels_doc(self):
        """Render docs/KEY_LEVELS.md to a styled HTML page and open it.

        Falls back to opening the raw markdown if rendering/writing fails.
        """
        import webbrowser
        from pathlib import Path
        md_path = Path(__file__).parent / "docs" / "KEY_LEVELS.md"
        if not md_path.exists():
            return
        try:
            import html_render
            md_text = md_path.read_text(encoding="utf-8")
            out_path = Path(__file__).parent / "data" / "key_levels.html"
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_text(html_render.render_key_levels_html(md_text),
                                encoding="utf-8")
            webbrowser.open(out_path.as_uri())
        except Exception:
            log.exception("Key-levels HTML render failed; opening raw markdown")
            webbrowser.open(md_path.as_uri())

    def _show_hamburger_menu(self):
        """Post the hamburger menu at the bottom-left corner of its button.

        Called from self._hamburger_btn's command callback. Using tk_popup()
        explicitly is more reliable than tk.Menubutton on Windows Tk builds.
        """
        btn = self._hamburger_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        try:
            self._hamburger_menu.tk_popup(x, y)
        finally:
            # Release the grab so keyboard/focus returns to the window even
            # if the user dismisses the menu by clicking outside it.
            self._hamburger_menu.grab_release()

    def _build_view_toggle(self, parent):
        # Reparent GEX/Charm/DEX buttons into the top-level view bar. Styling
        # kwargs copy-pasted verbatim from the old _build_config so _set_view()
        # still toggles bg/fg correctly.
        self._btn_gex = tk.Button(
            parent, text="\u0393 GEX", width=10,
            command=lambda: self._set_view("gex"),
            bg=CYAN, fg=BG_MAIN, activebackground=CYAN,
            activeforeground=BG_MAIN, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_gex.pack(side="left", expand=True, fill="x", padx=(6, 2), pady=3)

        self._btn_charm = tk.Button(
            parent, text="\u2202\u0394 Charm", width=10,
            command=lambda: self._set_view("charm"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_charm.pack(side="left", expand=True, fill="x", padx=2, pady=3)

        self._btn_dex = tk.Button(
            parent, text="\u0394 DEX", width=10,
            command=lambda: self._set_view("dex"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_dex.pack(side="left", expand=True, fill="x", padx=(2, 6), pady=3)

        self._btn_vanna = tk.Button(
            parent, text="\U0001D4B1 Vanna", width=10,
            command=lambda: self._set_view("vanna"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_vanna.pack(side="left", expand=True, fill="x", padx=2, pady=3)

        self._btn_term = tk.Button(
            parent, text="Term", width=10,
            command=lambda: self._set_view("term"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_term.pack(side="left", expand=True, fill="x", padx=(2, 6), pady=3)
        # Fix the _btn_dex padding so Term sits at the right edge
        self._btn_dex.pack_configure(padx=2)
        # Disabled when not on SPXW (the only symbol with collected term data)
        self._refresh_term_button_state()

        # Apply initial active/inactive styling.
        self._set_view(self._view_var.get())

    def _build_bottom_strip(self, parent):
        # Status rows were previously here; they've been promoted into the top
        # status bar so the chart can reclaim the vertical space. This strip
        # now holds only the action buttons.
        btn_frame = tk.Frame(parent, bg=BG_PANEL)
        btn_frame.pack(side="bottom", fill="x", pady=(2, 2))

        self._analyze_btn = tk.Button(
            btn_frame, text="\U0001f916 Analyze", command=self._analyze,
            bg="#2a1a4a", fg="#e0b0ff", activebackground="#3a2a6a",
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._analyze_btn.pack(side="right", padx=(4, 8), pady=2)

        self._refresh_btn = tk.Button(
            btn_frame, text="Refresh Now", command=self._trigger_refresh,
            bg=BG_INPUT, fg=CYAN, activebackground=BG_PANEL,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._refresh_btn.pack(side="right", padx=(4, 2), pady=2)

    def _build_chart(self, parent):
        # High-DPI for crisp text/lines. Side-by-side subplots: bars on the
        # left (narrow), heatmap on the right (wide), sharing the price Y axis.
        self._fig = Figure(figsize=(14, 6), dpi=150, facecolor=BG_MAIN)
        self._ax_bars, self._ax_heat = self._fig.subplots(
            1, 2, sharey=True,
            gridspec_kw={"width_ratios": [1, 3], "wspace": 0.02},
        )
        # Preserve self._ax as alias for _ax_bars so legacy references
        # (save-analysis, pressure panel hooks, etc.) keep working.
        self._ax = self._ax_bars
        # Twin axis removed — time dimension now belongs on the heatmap (Task 9+).
        self._ax2 = None
        self._fig.subplots_adjust(left=0.05, right=0.93, top=0.94, bottom=0.10)

        self._canvas = FigureCanvasTkAgg(self._fig, master=parent)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        # Bar hover tooltip wiring — handler is idempotent against redraws.
        self._canvas.mpl_connect("motion_notify_event", self._on_bar_hover)

    # ── View Toggle ──

    def _set_view(self, view):
        """Toggle between GEX, Charm, DEX, and Term views."""
        if view == "term":
            self._view_var.set(view)
            # Term button highlighted, others dim
            self._btn_gex.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_charm.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_dex.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_vanna.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_term.configure(bg=CYAN, fg=BG_MAIN)
            self._show_term_view()
            return
        # Non-term views: ensure term axis is hidden, side-by-side restored
        self._restore_non_term_view()
        # Term button back to dim
        if hasattr(self, "_btn_term"):
            self._btn_term.configure(bg=BG_INPUT, fg=FG_DIM)
        self._view_var.set(view)
        # Reset all to inactive styling, then highlight the active one.
        self._btn_gex.configure(bg=BG_INPUT, fg=FG_DIM)
        self._btn_charm.configure(bg=BG_INPUT, fg=FG_DIM)
        self._btn_dex.configure(bg=BG_INPUT, fg=FG_DIM)
        self._btn_vanna.configure(bg=BG_INPUT, fg=FG_DIM)
        if view == "gex":
            self._btn_gex.configure(bg=self._clr_gex_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="GEX Settings")
        elif view == "charm":
            self._btn_charm.configure(bg=self._clr_charm_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="Charm Settings")
        elif view == "dex":
            self._btn_dex.configure(bg=self._clr_dex_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="DEX Settings")
        elif view == "vanna":
            self._btn_vanna.configure(bg=self._clr_vanna_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="Vanna Settings")
        self._redraw()

    def _is_spxw(self) -> bool:
        """Term view applies when the symbol picker is on the SPX-family
        underlying. The collector queries the $SPX chain (which includes
        SPXW weeklies); the symbol dropdown doesn't surface a separate
        $SPXW entry, so we enable Term whenever the picker is on $SPX."""
        sym = (self._symbol_var.get() or "").upper()
        return sym in ("$SPX", "$SPXW.X", "SPXW", "$SPXW")

    def _refresh_term_button_state(self):
        if not hasattr(self, "_btn_term"):
            return
        state = "normal" if self._is_spxw() else "disabled"
        self._btn_term.configure(state=state)
        # If currently showing term view for a non-SPX symbol, kick back to gex
        if state == "disabled" and self._view_var.get() == "term":
            self._set_view("gex")

    def _term_colors(self) -> dict:
        """Pull current Term Heatmap colors from the _stl style-var system so
        Chart Setup edits + persistence flow through automatically."""
        return dict(
            colormap_neg=self._stl["Term Heatmap Negative"]["color"].get(),
            colormap_mid=self._stl["Term Heatmap Midpoint"]["color"].get(),
            colormap_pos=self._stl["Term Heatmap Positive"]["color"].get(),
        )

    def _show_term_view(self):
        # Hide the side-by-side bars+heat axes
        if hasattr(self, "_ax_bars"):
            self._ax_bars.set_visible(False)
        if hasattr(self, "_ax_heat"):
            self._ax_heat.set_visible(False)
        # Lazy-create the term axis on first entry
        if not hasattr(self, "_ax_term") or self._ax_term is None:
            self._ax_term = self._fig.add_subplot(111)
        self._ax_term.set_visible(True)
        # Slider: build + show + refresh + render-at-slider-pos
        self._show_term_slider()
        self._refresh_term_slider()
        self._show_term_header()
        self._ensure_term_hover_connected()
        # Render at the current slider position (will be max after refresh)
        idx = self._term_slider_var.get()
        positions = getattr(self, "_term_positions", [])
        if 0 <= idx < len(positions):
            self._load_term_snapshot_at(positions[idx])
        else:
            # No snapshots — draw empty placeholder
            self._term_last_rows = []
            draw_term_heatmap(self._ax_term, [], **self._term_colors(),
                              chrome=self._chrome)
            self._update_term_header([])
            self._canvas.draw_idle()

    def _restore_non_term_view(self):
        """Hide _ax_term and restore _ax_bars/_ax_heat for gex/charm/dex views."""
        if hasattr(self, "_ax_term") and self._ax_term is not None:
            self._ax_term.set_visible(False)
        if hasattr(self, "_ax_bars"):
            self._ax_bars.set_visible(True)
        if hasattr(self, "_ax_heat"):
            self._ax_heat.set_visible(True)
        self._hide_term_slider()
        self._hide_term_header()
        if hasattr(self, "_term_tip") and self._term_tip is not None:
            self._term_tip.set_visible(False)

    def _show_term_header(self):
        if not self._term_header_lbl.winfo_ismapped():
            self._term_header_lbl.pack(side="right", padx=(8, 12), pady=2)

    def _hide_term_header(self):
        self._term_header_lbl.pack_forget()

    def _update_term_header(self, rows: list, hovered_exp: str | None = None):
        if not rows:
            self._term_header_lbl.configure(text="No snapshot data")
            return
        underlying = rows[0]["underlying_price"]
        mvc = compute_mvc(rows, expiration=hovered_exp)
        mvc_txt = f"{int(mvc)}" if mvc is not None else "--"
        self._term_header_lbl.configure(
            text=f"Underlying {underlying:,.1f}  |  MVC {mvc_txt}"
        )

    def _ensure_term_hover_connected(self):
        if getattr(self, "_term_hover_cid", None) is not None:
            return
        self._term_hover_cid = self._canvas.mpl_connect(
            "motion_notify_event", self._on_term_hover)

    def _on_term_hover(self, event):
        # Only active when viewing term and cursor is on _ax_term
        if self._view_var.get() != "term":
            return
        if event.inaxes is not getattr(self, "_ax_term", None):
            if hasattr(self, "_term_tip") and self._term_tip is not None:
                self._term_tip.set_visible(False)
                self._canvas.draw_idle()
            return
        rows = getattr(self, "_term_last_rows", None) or []
        if not rows:
            return
        # Apply the same strike-band filter the renderer uses so cursor
        # coordinates match the displayed grid (renderer drops out-of-band
        # strikes, so the un-filtered rows list has a different y-extent).
        underlying = rows[0]["underlying_price"]
        band = max(underlying * 0.012, 30.0)
        lo, hi = underlying - band, underlying + band
        rows = [r for r in rows if lo <= r["strike"] <= hi]
        if not rows:
            return
        exps = sorted({r["expiration_date"] for r in rows})
        strikes = sorted({r["strike"] for r in rows}, reverse=True)
        if not exps or not strikes or event.xdata is None or event.ydata is None:
            return
        j = int(event.xdata)
        i = int(event.ydata)
        if not (0 <= j < len(exps) and 0 <= i < len(strikes)):
            return
        exp = exps[j]
        K = strikes[i]
        cell = next(
            (r for r in rows
             if r["expiration_date"] == exp and r["strike"] == K),
            None,
        )
        if not cell:
            return
        from datetime import datetime, date
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date()
                   - date.today()).days
        except Exception:
            dte = "?"
        exp_short = datetime.strptime(exp, "%Y-%m-%d").strftime("%b %d")
        txt = (
            f"K {int(K)} | Exp {exp_short} ({dte}d) | "
            f"Net {_format_dollars(cell['net_gex_usd'])}   "
            f"Call {_format_dollars(cell['call_gex_usd'])}   "
            f"Put {_format_dollars(-cell['put_gex_usd'])}"
        )
        # Tooltip size/color are configurable via Chart Setup ("Term Hover
        # Text"); applied every hover so edits take effect immediately.
        hov = self._stl.get("Term Hover Text") if hasattr(self, "_stl") else None
        hov_sz = int(hov["size"].get()) if hov else 8
        hov_clr = hov["color"].get() if hov else "white"
        if not hasattr(self, "_term_tip") or self._term_tip is None:
            self._term_tip = self._ax_term.text(
                0, 0, "", color=hov_clr, fontsize=hov_sz,
                bbox=dict(facecolor="#222", alpha=0.92, edgecolor="#555",
                          boxstyle="round,pad=0.3"),
                zorder=10,
            )
        self._term_tip.set_fontsize(hov_sz)
        self._term_tip.set_color(hov_clr)
        self._term_tip.set_position((event.xdata + 0.4, event.ydata + 0.4))
        self._term_tip.set_text(txt)
        self._term_tip.set_visible(True)
        # Also update MVC header for the hovered expiration
        self._update_term_header(rows, hovered_exp=exp)
        self._canvas.draw_idle()

    def _build_term_slider(self):
        """Build (once) the time-slider strip used by term view. Hidden when
        not in term view; re-packed on _show_term_view entry."""
        if hasattr(self, "_term_slider_frame"):
            return
        import tkinter as tk
        f = tk.Frame(self, bg=BG_PANEL)
        tk.Label(f, text="Time:", bg=BG_PANEL, fg=FG_DIM,
                 font=(FONT, 9)).pack(side="left", padx=(8, 4))
        self._term_slider_var = tk.IntVar(value=0)
        self._term_slider = tk.Scale(
            f, from_=0, to=0, orient="horizontal",
            variable=self._term_slider_var,
            showvalue=False,
            bg=BG_PANEL, fg=FG_PRIMARY, troughcolor=BG_INPUT,
            highlightthickness=0,
            activebackground=CYAN,
            command=self._on_term_slider_change,
        )
        self._term_slider.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._term_slider_lbl = tk.Label(
            f, text="--:--", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9), width=10, anchor="e",
        )
        self._term_slider_lbl.pack(side="right", padx=(0, 8))
        self._term_slider_frame = f
        self._term_positions = []

    def _show_term_slider(self):
        self._build_term_slider()
        # Pack at the bottom of the main window. Use side="bottom" so it
        # sits beneath the chart canvas regardless of other UI.
        if not self._term_slider_frame.winfo_ismapped():
            self._term_slider_frame.pack(side="bottom", fill="x", pady=(2, 6))

    def _hide_term_slider(self):
        if hasattr(self, "_term_slider_frame"):
            self._term_slider_frame.pack_forget()

    def _refresh_term_slider(self):
        """Reload available positions and update the slider extent.

        Live-follow rule: if the user was parked at the previous max
        position, auto-advance to the new max. Otherwise preserve the
        user's scrubbed position.
        """
        import gex_history_db as db
        from datetime import datetime
        self._build_term_slider()
        conn = db.connect(read_only=True)
        try:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            positions = term_slider_positions(conn, today, "SPX")
        finally:
            conn.close()
        prev_positions = getattr(self, "_term_positions", [])
        prev_max_idx = max(0, len(prev_positions) - 1)
        was_at_max = (
            len(prev_positions) > 0
            and self._term_slider_var.get() == prev_max_idx
        )
        self._term_positions = positions
        if not positions:
            self._term_slider.configure(from_=0, to=0)
            self._term_slider_lbl.configure(text="--:--")
            return
        new_max_idx = len(positions) - 1
        self._term_slider.configure(from_=0, to=new_max_idx)
        if was_at_max or len(prev_positions) == 0:
            self._term_slider_var.set(new_max_idx)
        else:
            # Clamp existing position to the new range
            cur = self._term_slider_var.get()
            if cur > new_max_idx:
                self._term_slider_var.set(new_max_idx)
        self._update_term_slider_label()

    def _update_term_slider_label(self):
        idx = self._term_slider_var.get()
        positions = getattr(self, "_term_positions", [])
        if 0 <= idx < len(positions):
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(positions[idx])
                self._term_slider_lbl.configure(text=dt.strftime("%H:%M CT"))
            except ValueError:
                self._term_slider_lbl.configure(text="--:--")

    def _on_term_slider_change(self, _value):
        """Slider command callback. Re-renders the heatmap at the new position."""
        self._update_term_slider_label()
        idx = self._term_slider_var.get()
        positions = getattr(self, "_term_positions", [])
        if 0 <= idx < len(positions):
            self._load_term_snapshot_at(positions[idx])

    def _load_term_snapshot_at(self, ts_iso: str):
        """Load rows for `ts_iso` and re-render. Stores the current rows on
        self._term_last_rows so Task 6 (hover/MVC) can read them without
        re-querying."""
        import gex_history_db as db
        conn = db.connect(read_only=True)
        try:
            rows = db.load_term_snapshot(conn, ts_iso, "SPX")
        finally:
            conn.close()
        self._term_last_rows = rows
        draw_term_heatmap(self._ax_term, rows, **self._term_colors(),
                          chrome=self._chrome)
        self._update_term_header(rows)
        # Tooltip is bound to the previous axis state; clear it so a stale
        # cell doesn't linger after a snapshot reload.
        if hasattr(self, "_term_tip") and self._term_tip is not None:
            self._term_tip = None
        theme.apply_matplotlib(self._fig, [self._ax_term])
        self._canvas.draw_idle()

    def _render_term_now(self):
        import gex_history_db as db
        from datetime import datetime
        conn = db.connect(read_only=True)
        try:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            timestamps = db.list_term_timestamps_for_date(conn, today, "SPX")
            if not timestamps:
                draw_term_heatmap(self._ax_term, [], **self._term_colors(),
                                  chrome=self._chrome)
            else:
                rows = db.load_term_snapshot(conn, timestamps[-1], "SPX")
                draw_term_heatmap(self._ax_term, rows, **self._term_colors(),
                                  chrome=self._chrome)
        finally:
            conn.close()
        theme.apply_matplotlib(self._fig, [self._ax_term])
        self._canvas.draw_idle()

    # ── Chart Drawing ──

    def _redraw(self):
        """Redraw chart from current engine snapshot.

        Side-by-side layout: horizontal bars on the left, heatmap on the right,
        sharing a price Y axis. Task 9 replaces the heatmap stub with the real
        OI / gamma-profile rendering.
        """
        ax = self._ax_bars
        ax.clear()
        self._ax_heat.clear()

        # Task 11: Show Heatmap toggle — when off, hide the heatmap axis and
        # stretch the bars axis across the full figure width. When on, restore
        # the original 1:3 side-by-side layout.
        show_heatmap_on = (
            self._show_heatmap_var.get()
            if hasattr(self, "_show_heatmap_var") else True
        )
        if not show_heatmap_on:
            self._ax_heat.set_visible(False)
            self._fig.subplots_adjust(right=0.97)
            self._ax_bars.set_position([0.05, 0.1, 0.92, 0.84])
        else:
            self._ax_heat.set_visible(True)
            self._fig.subplots_adjust(right=0.93)
            self._ax_bars.set_position([0.05, 0.1, 0.22, 0.84])

        view = self._view_var.get()
        self._update_pressure_panel()  # pack/unpack based on current view
        hist = self._load_history_dicts(view)

        # Select dataset
        if view == "charm":
            data = self._charm_data
            is_charm = True  # used by downstream color/label branches
        elif view == "dex":
            data = self._dex_data
            is_charm = False
        elif view == "vanna":
            data = self._vanna_data
            is_charm = False
        else:
            data = self._engine.current
            is_charm = False

        if not data:
            ax.set_facecolor(BG_MAIN)
            if view == "dex":
                msg = "No DEX data — waiting for fetch..."
            elif view == "vanna":
                msg = "No vanna data — waiting for fetch..."
            elif is_charm:
                msg = "No charm data — waiting for fetch..."
            else:
                msg = "No data — waiting for first fetch..."
            ax.text(0.5, 0.5, msg,
                    ha="center", va="center", color=FG_DIM, fontsize=12,
                    transform=ax.transAxes)
            self._draw_heatmap(self._ax_heat, self._symbol_var.get(), view, None)
            self._hover_strikes = []
            self._hover_grid = {}
            self._hover_annotation = None
            theme.apply_matplotlib(self._fig, [self._ax_bars, self._ax_heat])
            self._canvas.draw_idle()
            return

        spot = data["spot"]
        gex_raw = data["gex"]
        grouping = self._grouping_var.get()
        display = self._display_var.get()

        gex = GammaEngine.group_gex(gex_raw, grouping)

        # Filter to strikes near spot with non-zero values.
        # Use +/-2% for a tight, consistent window across GEX, Charm, and DEX.
        pct = 0.02
        lo, hi = spot * (1 - pct), spot * (1 + pct)
        strikes = sorted([s for s in gex if lo <= s <= hi and gex[s][display] != 0])

        if not strikes:
            ax.set_facecolor(BG_MAIN)
            if view == "dex":
                label = "DEX"
            elif view == "vanna":
                label = "Vanna"
            elif is_charm:
                label = "Charm"
            else:
                label = "GEX"
            ax.text(0.5, 0.5, f"No non-zero {label} within +/-{int(pct*100)}% of spot",
                    ha="center", va="center", color=FG_DIM, fontsize=12,
                    transform=ax.transAxes)
            self._draw_heatmap(self._ax_heat, self._symbol_var.get(), view, spot)
            self._hover_strikes = []
            self._hover_grid = {}
            self._hover_annotation = None
            theme.apply_matplotlib(self._fig, [self._ax_bars, self._ax_heat])
            self._canvas.draw_idle()
            return

        values = [gex[s][display] for s in strikes]
        if view == "dex":
            pos_color = self._clr_dex_pos.get()
            bar_key = "DEX+ Bars"
        elif view == "vanna":
            pos_color = self._clr_vanna_pos.get()
            bar_key = "Vanna+ Bars"
        elif is_charm:
            pos_color = self._clr_charm_pos.get()
            bar_key = "Charm+ Bars"
        else:
            pos_color = self._clr_gex_pos.get()
            bar_key = "GEX+ Bars"
        neg_color = self._clr_neg.get()
        colors = [pos_color if v >= 0 else neg_color for v in values]

        ax.set_facecolor(BG_MAIN)
        if len(strikes) >= 2:
            diffs = sorted(strikes[i + 1] - strikes[i]
                           for i in range(len(strikes) - 1))
            typical_spacing = diffs[len(diffs) // 2]
        else:
            typical_spacing = max(grouping, 1.0)
        bar_ratio = self._stl[bar_key]["size"].get()
        bar_height = typical_spacing * bar_ratio

        # ΔDEX ghost overlay: flat low-alpha bars behind premium bars (DEX only)
        if view == "dex" and self._db is not None:
            try:
                from gex_history_db import first_snapshot_today
                open_grid = first_snapshot_today(
                    self._db, self._symbol_var.get(), "dex",
                )
            except sqlite3.OperationalError:
                open_grid = {}
            if open_grid:
                open_gex = GammaEngine.group_gex(open_grid, grouping)
                open_values = [open_gex.get(s, {}).get(display, 0.0) for s in strikes]
                ghost_clr = self._stl["Ghost Bars"]["color"].get()
                ghost_alpha = self._stl["Ghost Bars"]["size"].get()
                ghost_colors = [ghost_clr if v >= 0 else neg_color for v in open_values]
                ax.barh(strikes, open_values, color=ghost_colors, height=bar_height,
                        alpha=ghost_alpha, edgecolor="none", zorder=1)

        # Premium cylindrical embossed bars (main solid)
        for strike, val, clr in zip(strikes, values, colors):
            self._draw_premium_bar(ax, strike, val, bar_height, clr)

        # Zero-line spine with subtle glow
        zl = self._stl["Zero Line"]
        ax.axvline(x=0, color=zl["color"].get(),
                   linewidth=zl["thickness"].get(), alpha=0.5, zorder=1)
        ax.axvline(x=0, color=zl["color"].get(), linewidth=2.5, alpha=0.08, zorder=0)
        # Faint horizontal gridlines
        gl = self._stl["Grid Lines"]
        ax.yaxis.grid(True, color=gl["color"].get(), alpha=0.06,
                      linewidth=gl["thickness"].get(), linestyle="-")
        ax.set_axisbelow(True)

        # Cache per-redraw state for hover tooltip (_on_bar_hover consumes).
        self._hover_strikes = strikes
        self._hover_grid = gex  # {strike: {"call", "put", "net"}}
        self._hover_bar_height = bar_height
        self._hover_view = view
        # Create/reset the hover annotation artist on the current ax.
        # ax.clear() in the next _redraw destroys it; recreate each time.
        self._hover_annotation = ax.annotate(
            "", xy=(0, 0), xycoords="data",
            xytext=(8, 0), textcoords="offset points",
            ha="left", va="center", fontsize=8, color=FG_PRIMARY,
            bbox=dict(facecolor=BG_PANEL, edgecolor=FG_DIM,
                      boxstyle="round,pad=0.3", alpha=0.92),
            visible=False, zorder=100,
        )

        # Ghost-bar history overlay (uses strike prices directly).
        if self._show_history_var.get() and len(hist) >= 2:
            self._draw_history_overlay(ax, strikes, hist, display, grouping)

        # Y-axis: strike prices with tight bounds.
        y_margin = typical_spacing * 2
        ax.set_ylim(min(strikes) - y_margin, max(strikes) + y_margin)
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=14, steps=[1, 2, 5, 10]))
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _p: f"{v:,.0f}"))
        tick_sz = int(self._stl["Axis Ticks"]["size"].get())
        tick_clr = self._stl["Axis Ticks"]["color"].get()
        ax.tick_params(axis="x", colors=tick_clr, labelsize=tick_sz)
        ax.tick_params(axis="y", colors=tick_clr, labelsize=tick_sz)

        # Primary X formatting (K/M/B)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(self._fmt_gex))

        # X-axis headroom: 2% padding so longest bars don't butt against the spine.
        finite_vals = [float(v) for v in values if v is not None]
        if finite_vals:
            v_min = min(finite_vals + [0.0])
            v_max = max(finite_vals + [0.0])
            v_span = v_max - v_min
            if v_span > 0:
                pad = v_span * 0.02
                ax.set_xlim(v_min - (pad if v_min < 0 else 0),
                            v_max + (pad if v_max > 0 else 0))

        # ── Reference lines with right-justified labels ──
        text_glow = [pe.withStroke(linewidth=3, foreground=BG_MAIN, alpha=0.9)]
        ref_labels = []

        # Spot line — thin, always on top
        sl = self._stl["Spot Line"]
        spot_clr = sl["color"].get()
        ax.axhline(y=spot, color=spot_clr, linestyle=sl["linestyle"].get(),
                   linewidth=sl["thickness"].get(), alpha=0.9, zorder=50)
        st = self._stl["Spot Text"]
        ref_labels.append((spot, f"Spot: {spot:,.1f}",
                           st["color"].get(), int(st["size"].get())))

        # On-chart level labels: (y, text, color) drawn just above each line.
        # These replace the retired top headline strip.
        level_labels = []

        # Current zero-gamma flip line (GEX / Charm / DEX) + label. This is the
        # "Flip" value the headline used to show. (Vanna has no strike flip.)
        if view in ("gex", "charm", "dex"):
            cur_flip = self._calc_flip_point(gex, spot)
            if cur_flip is not None and lo <= cur_flip <= hi:
                fl = self._stl["Flip Line"]
                ax.axhline(y=cur_flip, color=fl["color"].get(),
                           linestyle=fl["linestyle"].get(),
                           linewidth=fl["thickness"].get(), alpha=0.6, zorder=47)
                level_labels.append((cur_flip, f"Flip {cur_flip:,.0f}",
                                     fl["color"].get()))

        # Projected EOD flip (GEX/Charm only)
        if view != "dex":
            proj_flip = self._eod_flip_projection(hist)
            if proj_flip is not None and lo <= proj_flip <= hi:
                pl = self._stl["Proj Flip Line"]
                ax.axhline(y=proj_flip, color=pl["color"].get(),
                           linestyle=pl["linestyle"].get(),
                           linewidth=pl["thickness"].get(), alpha=0.9)
                pt = self._stl["Proj Flip Text"]
                ref_labels.append((proj_flip, f"Proj Flip 15:15  {proj_flip:,.1f}",
                                   pt["color"].get(), int(pt["size"].get())))

        # 0-DTE charm-projected flip line (DEX view only)
        if view == "dex" and data.get("hedge_pressure") is not None:
            projected_flip = self._compute_projected_flip(data, spot)
            if projected_flip is not None and lo <= projected_flip <= hi:
                dpl = self._stl["DEX Proj Flip"]
                ax.axhline(y=projected_flip, color=dpl["color"].get(),
                           linestyle=dpl["linestyle"].get(),
                           linewidth=dpl["thickness"].get(), alpha=0.9)
                dpt = self._stl["DEX Flip Text"]
                ref_labels.append((projected_flip,
                                   f"Proj Flip 15:00  {projected_flip:,.1f}",
                                   dpt["color"].get(), int(dpt["size"].get())))
                level_labels.append((projected_flip,
                                     f"Proj Flip {projected_flip:,.0f}",
                                     dpl["color"].get()))

        # Expected move lines
        if self._show_em_var.get() and self._last_em:
            el = self._stl["EM Lines"]
            em = self._last_em
            et = self._stl["EM Text"]
            for em_price, label in [(spot + em, f"+1s {spot + em:,.1f}"),
                                     (spot - em, f"-1s {spot - em:,.1f}")]:
                if lo <= em_price <= hi:
                    ax.axhline(y=em_price, color=el["color"].get(),
                               linestyle=el["linestyle"].get(),
                               linewidth=el["thickness"].get(), alpha=0.7)
                    ref_labels.append((em_price, label,
                                       et["color"].get(), int(et["size"].get())))

        # Max-pain line (GEX view only) — quick win #1. Computed from the
        # retained chain; gated to the visible ±2% window.
        if view == "gex":
            chain = getattr(self._engine, "_last_chain", None)
            mp_res = calc_max_pain_from_chain(chain) if chain else None
            if mp_res is not None:
                mp_strike = mp_res["max_pain"]
                if lo <= mp_strike <= hi:
                    mpl = self._stl["Max Pain Line"]
                    ax.axhline(y=mp_strike, color=mpl["color"].get(),
                               linestyle=mpl["linestyle"].get(),
                               linewidth=mpl["thickness"].get(), alpha=0.9,
                               zorder=49)
                    mpt = self._stl["Max Pain Text"]
                    ref_labels.append((mp_strike, f"Max Pain {mp_strike:,.0f}",
                                       mpt["color"].get(), int(mpt["size"].get())))
                    level_labels.append((mp_strike, f"Max Pain {mp_strike:,.0f}",
                                         mpl["color"].get()))

        # Directional call/put wall lines (GEX view only) — quick win #2.
        if view == "gex":
            dwalls = get_directional_walls({"gex": gex, "spot": spot}, spot)
            cw, pw = dwalls.get("call_wall"), dwalls.get("put_wall")
            if cw is not None and lo <= cw <= hi:
                cwl = self._stl["Call Wall Line"]
                ax.axhline(y=cw, color=cwl["color"].get(),
                           linestyle=cwl["linestyle"].get(),
                           linewidth=cwl["thickness"].get(), alpha=0.55, zorder=48)
                level_labels.append((cw, f"Call Wall {cw:,.0f}",
                                     cwl["color"].get()))
            if pw is not None and lo <= pw <= hi:
                pwl = self._stl["Put Wall Line"]
                ax.axhline(y=pw, color=pwl["color"].get(),
                           linestyle=pwl["linestyle"].get(),
                           linewidth=pwl["thickness"].get(), alpha=0.55, zorder=48)
                level_labels.append((pw, f"Put Wall {pw:,.0f}",
                                     pwl["color"].get()))

        # De-overlap labels
        y_range = max(strikes) - min(strikes)
        min_gap = y_range * 0.025 if y_range > 0 else 1.0
        ref_labels.sort(key=lambda r: r[0])
        nudged = [r[0] for r in ref_labels]
        for i in range(1, len(nudged)):
            if nudged[i] - nudged[i - 1] < min_gap:
                nudged[i] = nudged[i - 1] + min_gap

        # Spot / Proj Flip / EM lines stay unlabelled (Spot is in the status
        # row; EM lines read as ±1σ). The KEY LEVELS (Call Wall, Put Wall,
        # Flip, Max Pain, …) are labelled directly on the chart — unobtrusive
        # text anchored to the left edge, just above each line — replacing the
        # retired top headline strip.
        _ = (ref_labels, nudged)
        if level_labels:
            lvl_sz = int(self._stl["Level Label Text"]["size"].get())
            level_labels.sort(key=lambda r: r[0])
            y_span = (max(strikes) - min(strikes)) if len(strikes) > 1 else 1.0
            gap = y_span * 0.02 if y_span > 0 else 1.0
            lab_ys = [r[0] for r in level_labels]
            for i in range(1, len(lab_ys)):
                if lab_ys[i] - lab_ys[i - 1] < gap:
                    lab_ys[i] = lab_ys[i - 1] + gap
            for (y_orig, text, color), y in zip(level_labels, lab_ys):
                ax.text(0.015, y, text, transform=ax.get_yaxis_transform(),
                        va="bottom", ha="left", fontsize=lvl_sz, color=color,
                        alpha=0.92, zorder=60, clip_on=True,
                        path_effects=text_glow)

        # Comparison dots
        if self._show_open_var.get() and self._engine.market_open:
            self._draw_comparison_dots(ax, strikes, self._engine.market_open, display, grouping, GOLD)
        if self._show_prev_var.get() and self._engine.previous:
            self._draw_comparison_dots(ax, strikes, self._engine.previous, display, grouping, GRAY)

        # Heatmap panel (right).
        self._draw_heatmap(self._ax_heat, self._symbol_var.get(), view, spot)

        # Styling / titles
        for spine in ax.spines.values():
            spine.set_color(FG_DIM)
            spine.set_linewidth(0.5)
        dte = self._engine._last_dte
        dte_str = "0-DTE" if dte == 0 else f"{dte}-DTE"
        ttl = self._stl["Title"]
        ttl_sz = int(ttl["size"].get())
        ttl_clr = ttl["color"].get()
        al = self._stl["Axis Labels"]
        al_sz = int(al["size"].get())
        al_clr = al["color"].get()
        sym = self._symbol_var.get()
        if view == "dex":
            ax.set_title(f"Delta Exposure (DEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("Delta Exposure ($)", color=al_clr, fontsize=al_sz)
        elif view == "vanna":
            ax.set_title(f"Vanna Exposure (VEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("Vanna Exposure ($)", color=al_clr, fontsize=al_sz)
        elif is_charm:
            ax.set_title(f"Charm Pressure (ChEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("Charm Exposure ($)", color=al_clr, fontsize=al_sz)
        else:
            ax.set_title(f"Gamma Exposure (GEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("GEX ($)", color=al_clr, fontsize=al_sz)

        theme.apply_matplotlib(self._fig, [self._ax_bars, self._ax_heat])
        self._canvas.draw_idle()

        try:
            self._update_collector_status()
        except Exception:
            pass  # never let status-label bugs crash the main redraw

    def _draw_premium_bar(self, ax, y, width, height, color, alpha=0.92):
        """Draw an embossed cylindrical bar with highlight, body, and drop shadow."""
        if width == 0:
            return
        x0 = min(0, width)
        x1 = max(0, width)
        bar_w = x1 - x0
        if bar_w == 0:
            return

        rounding = min(height * 0.45, bar_w * 0.04)
        h2 = height / 2

        # 1. Drop shadow
        shadow = FancyBboxPatch(
            (x0 - bar_w * 0.005, y - h2 * 0.85 - height * 0.08),
            bar_w * 1.01, height * 0.9,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            facecolor="#000000", edgecolor="none", alpha=0.35, zorder=2,
        )
        ax.add_patch(shadow)

        # 2. Darker body
        r, g, b, _ = to_rgba(color)
        dark_color = (r * 0.55, g * 0.55, b * 0.55)
        body = FancyBboxPatch(
            (x0, y - h2), bar_w, height,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            facecolor=dark_color, edgecolor="none", alpha=alpha, zorder=3,
        )
        ax.add_patch(body)

        # 3. Cylindrical gradient
        n_rows = 64
        grad = np.zeros((n_rows, 1, 4))
        for i in range(n_rows):
            t = i / (n_rows - 1)
            brightness = 1.0 - 2.8 * (t - 0.45) ** 2
            brightness = max(0.3, min(1.0, brightness))
            grad[i, 0] = [r * brightness, g * brightness, b * brightness, alpha]
        cmap_v = LinearSegmentedColormap.from_list(
            "cyl", [to_rgba(c) for c in grad[:, 0, :3]], N=n_rows)
        vert_grad = np.linspace(0, 1, n_rows).reshape(-1, 1)
        extent = [x0, x1, y - h2, y + h2]
        clip_box = FancyBboxPatch(
            (x0, y - h2), bar_w, height,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            facecolor="none", edgecolor="none", zorder=4,
        )
        ax.add_patch(clip_box)
        ax.imshow(vert_grad, aspect="auto", cmap=cmap_v, extent=extent,
                  clip_path=clip_box, clip_on=True, zorder=4,
                  interpolation="bicubic", origin="lower")

        # 4. Top highlight
        hi_y = y + h2 * 0.3
        hi_h = height * 0.25
        hi_round = min(hi_h * 0.45, bar_w * 0.04)
        highlight = FancyBboxPatch(
            (x0 + bar_w * 0.01, hi_y), bar_w * 0.98, hi_h,
            boxstyle=f"round,pad=0,rounding_size={hi_round}",
            facecolor="#ffffff", edgecolor="none", alpha=0.18, zorder=5,
        )
        ax.add_patch(highlight)

        # 5. Tip cap
        tip_x = x1 if width >= 0 else x0
        cap_w = bar_w * 0.03
        cap = FancyBboxPatch(
            (tip_x - cap_w if width >= 0 else tip_x, y - h2 * 0.6),
            cap_w, height * 0.6,
            boxstyle=f"round,pad=0,rounding_size={min(cap_w, height * 0.3)}",
            facecolor=color, edgecolor="none", alpha=0.6, zorder=6,
        )
        ax.add_patch(cap)

    def _draw_history_overlay(self, ax, strikes, history, display, grouping):
        """Draw faint ghost bars for historical snapshots.

        Samples up to 6 evenly-spaced snapshots from `history` (oldest -> newest)
        and draws a thin marker at each strike's historical value, with alpha
        fading from 0.15 (oldest) to 0.6 (newest-1). The most recent snapshot
        IS the live bar already, so we skip it. Y coordinates are real strike
        prices now — the chart's Y axis uses price units directly.
        """
        n = len(history)
        if n < 2:
            return
        sample_count = min(6, n - 1)
        if sample_count == 1:
            indices = [0]
        else:
            step = (n - 1) / sample_count
            indices = [int(i * step) for i in range(sample_count)]

        strike_set = set(strikes)
        for rank, idx in enumerate(indices):
            alpha = 0.15 + (0.45 * rank / max(1, sample_count - 1))
            snap_gex_raw = history[idx].get("gex", {})
            snap_grouped = GammaEngine.group_gex(snap_gex_raw, grouping)
            for s in strike_set:
                if s not in snap_grouped:
                    continue
                val = snap_grouped[s][display]
                if val == 0:
                    continue
                ax.plot([0, val], [s, s], color=GRAY, alpha=alpha,
                        linewidth=1.5, solid_capstyle="butt", zorder=1)

    def _eod_flip_projection(self, hist):
        """Linear-extrapolate today's flip series to 15:15 CT close.

        Returns the projected flip price, or None when there isn't enough
        history, the latest snapshot already sits past close, or the fit
        is degenerate. Used both by the main chart (price-scale marker)
        and the EOD panel (dotted projection line + verbose narrative).
        """
        if not hist or len(hist) < 2:
            return None
        def _tod(dt):
            return dt.hour * 3600 + dt.minute * 60 + dt.second
        xs, ys = [], []
        for h in hist:
            flip = h.get("flip")
            ts = h.get("ts")
            if flip is None or ts is None:
                continue
            try:
                xs.append(_tod(ts))
                ys.append(float(flip))
            except (AttributeError, TypeError, ValueError):
                continue
        if len(xs) < 2:
            return None
        close_secs = 15 * 3600 + 15 * 60
        if xs[-1] >= close_secs:
            return None
        window = min(12, len(xs))
        return extrapolate_linear(xs[-window:], ys[-window:], close_secs)

    def _compute_projected_flip(self, data, spot):
        """Thin wrapper over the module-level ``compute_projected_flip``.

        Kept as a method so existing chart-rendering callsites work unchanged.
        """
        return compute_projected_flip(data, spot)

    def _draw_heatmap(self, ax_heat, symbol, view, current_spot):
        """Render the intraday strike × time heatmap on ax_heat.

        Layout:
            - Historical cells (left side): pcolormesh at alpha=1.0 from
              gex_history.snapshots for today.
            - "Now" vertical marker.
            - Forward-projection cells (right side): pcolormesh at alpha=0.7
              computed from self._engine._last_chain + bs_* greeks at future T.
            - White price line traces historical spot values.
            - No forward projection on the price line.

        Falls back to a placeholder when the DB has no rows today or last_chain
        is missing.
        """
        import numpy as np
        from matplotlib.colors import TwoSlopeNorm, LinearSegmentedColormap
        from matplotlib.dates import DateFormatter, MinuteLocator
        from datetime import datetime, timedelta

        ax_heat.set_facecolor(BG_MAIN)

        # Dark-centered diverging colormap: zero maps to BG_MAIN (dark navy)
        # instead of white, so empty/near-zero cells blend with the rest of
        # the dashboard background. Endpoints use brighter coral-red and
        # sky-blue (Tier-1 boost ~65-68% luminance vs ~40-50% prior) so
        # high-magnitude cells pop against the dark dashboard.
        heat_cmap = LinearSegmentedColormap.from_list(
            "gex_heat_dark",
            [
                self._stl["Heatmap Negative"]["color"].get(),
                self._stl["Heatmap Midpoint"]["color"].get(),
                self._stl["Heatmap Positive"]["color"].get(),
            ],
            N=256,
        )

        # Historical data.
        rows = []
        if self._db is not None:
            try:
                from gex_history_db import load_today_with_grid
                rows = load_today_with_grid(self._db, symbol, view)
            except Exception as e:
                log.warning("Heatmap load failed for %s/%s: %s", symbol, view, e)

        if not rows:
            ax_heat.text(0.5, 0.5, "Waiting for first snapshot…",
                         ha="center", va="center",
                         color=FG_DIM, fontsize=12, transform=ax_heat.transAxes)
            ax_heat.tick_params(colors=FG_DIM, labelsize=8)
            return

        display = self._display_var.get() if hasattr(self, "_display_var") else "net"
        strikes, times, hist_matrix = build_historical_matrix(rows, current_spot, display)

        if not strikes:
            ax_heat.text(0.5, 0.5, "No strikes within ±5% of spot",
                         ha="center", va="center",
                         color=FG_DIM, fontsize=12, transform=ax_heat.transAxes)
            return

        # Forward projection cells.
        fwd_strikes = []
        fwd_times = []
        fwd_matrix = None
        last_fetch_ts = times[-1] if times else None
        cache_key = (symbol, view)

        if self._engine._last_chain and last_fetch_ts is not None:
            cached = self._fwd_cache.get(cache_key)
            # Cache key is (symbol, view), but the strike list also depends
            # on live spot (±5% window) which can shift between redraws even
            # when the chain hasn't been re-fetched. Invalidate on strike
            # count change to keep fwd_matrix shape in sync with y_edges.
            cache_valid = (
                cached is not None
                and cached[0] == last_fetch_ts
                and len(cached[1]) == len(strikes)
            )
            if cache_valid:
                _, fwd_strikes, fwd_times, fwd_matrix = cached
            else:
                fwd_strikes, fwd_times, fwd_matrix = self._build_forward_band(
                    strikes, view, current_spot,
                )
                self._fwd_cache[cache_key] = (last_fetch_ts, fwd_strikes, fwd_times, fwd_matrix)

        # Combined min/max for normalization.
        all_vals = [hist_matrix]
        if fwd_matrix is not None and fwd_matrix.size > 0:
            all_vals.append(fwd_matrix)
        combined = np.concatenate([m.ravel() for m in all_vals])
        finite = combined[np.isfinite(combined)]
        if finite.size == 0:
            ax_heat.text(0.5, 0.5, "No data in window", ha="center", va="center",
                         color=FG_DIM, fontsize=12, transform=ax_heat.transAxes)
            return
        vmin = float(finite.min())
        vmax = float(finite.max())
        # TwoSlopeNorm requires vmin < 0 < vmax. Clamp defensively.
        if vmin >= 0:
            vmin = -abs(vmax) * 0.01 - 1e-9
        if vmax <= 0:
            vmax = abs(vmin) * 0.01 + 1e-9
        norm = TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)

        # X-coords (timestamps as matplotlib datetimes).
        hist_xs = [datetime.fromtimestamp(t, TZ) for t in times]
        y = np.array(strikes)

        # Mesh cell edges: add half-interval margins so each snapshot occupies a visible cell.
        half_dt = timedelta(minutes=2.5)  # 5-min polls
        def _compute_x_edges(xs):
            if len(xs) == 1:
                return [xs[0] - half_dt, xs[0] + half_dt]
            edges = [xs[0] - half_dt]
            for i in range(len(xs) - 1):
                edges.append(xs[i] + (xs[i + 1] - xs[i]) / 2)
            edges.append(xs[-1] + half_dt)
            return edges

        x_edges_hist = _compute_x_edges(hist_xs)
        if len(strikes) > 1:
            y_edges = [strikes[0] - (strikes[1] - strikes[0]) / 2]
            for i in range(len(strikes) - 1):
                y_edges.append((strikes[i] + strikes[i + 1]) / 2)
            y_edges.append(strikes[-1] + (strikes[-1] - strikes[-2]) / 2)
        else:
            y_edges = [strikes[0] - 1, strikes[0] + 1]

        ax_heat.pcolormesh(
            x_edges_hist, y_edges, hist_matrix,
            cmap=heat_cmap, norm=norm, alpha=1.0, shading="flat",
        )

        # Forward band.
        if fwd_matrix is not None and fwd_matrix.size > 0 and fwd_times:
            fwd_xs = [datetime.fromtimestamp(t, TZ) for t in fwd_times]
            x_edges_fwd = _compute_x_edges(fwd_xs)
            ax_heat.pcolormesh(
                x_edges_fwd, y_edges, fwd_matrix,
                cmap=heat_cmap, norm=norm, alpha=0.7, shading="flat",
            )
            # "Now" vertical marker at the boundary.
            now_x = datetime.fromtimestamp(last_fetch_ts, TZ) + half_dt
            ax_heat.axvline(now_x, color=FG_DIM, linewidth=0.8, alpha=0.6, linestyle="--")

        # Price line (historical only).
        spots = [row[1] for row in rows]
        ax_heat.plot(hist_xs, spots, color=WHITE, linewidth=1.2, alpha=0.9, zorder=5)

        # Axis formatting.
        ax_heat.xaxis.set_major_locator(MinuteLocator(byminute=[0, 30]))
        ax_heat.xaxis.set_major_formatter(DateFormatter("%H:%M", tz=TZ))
        ax_heat.tick_params(axis="x", colors=FG_DIM, labelsize=8, rotation=0)
        ax_heat.tick_params(axis="y", colors=FG_PRIMARY, labelsize=8)
        ax_heat.set_facecolor(BG_MAIN)

        # Clamp x-range to market hours (08:30 - 15:00 CT).
        today_open = datetime.now(TZ).replace(hour=8, minute=30, second=0, microsecond=0)
        today_close = datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0)
        ax_heat.set_xlim(today_open, today_close)

        # Key-level labels on the right edge.
        # Latest live snapshot per view - freshest for labels.
        live_data = {
            "gex": self._engine.current,
            "charm": self._charm_data,
            "dex": self._dex_data,
            "vanna": self._vanna_data,
        }.get(view)
        if live_data:
            self._draw_heatmap_key_levels(
                ax_heat, symbol, view, live_data, current_spot, strikes,
            )

    def _draw_heatmap_key_levels(self, ax_heat, symbol, view, data, current_spot, strikes):
        """Overlay horizontal lines + right-edge labels for key strikes on the heatmap.

        Label set depends on view; labels outside the Y-range (+/-5%) are silently
        skipped; labels on the same strike are deduplicated (concatenated).
        """
        if not strikes:
            return

        y_lo, y_hi = min(strikes), max(strikes)

        # Dedup by strike: first label wins, same-strike additions are concatenated.
        labels_by_strike = {}

        def _add(strike, label, color, linestyle="-"):
            if strike is None or not (y_lo <= strike <= y_hi):
                return
            if strike in labels_by_strike:
                prev_label, prev_color, prev_style = labels_by_strike[strike]
                labels_by_strike[strike] = (f"{prev_label} / {label}", prev_color, prev_style)
            else:
                labels_by_strike[strike] = (label, color, linestyle)

        summary = data if isinstance(data, dict) else {}
        pos = summary.get("top_pos_strike")
        neg = summary.get("top_neg_strike")
        flip = summary.get("flip")

        if view == "gex":
            _add(pos, "Call Wall", self._clr_gex_pos.get())
            _add(neg, "Put Wall", self._clr_neg.get())
            _add(flip, "Gamma Flip", WHITE)
            key_gamma = find_key_gamma_strike(data.get("gex") or {}, current_spot)
            _add(key_gamma, "Key \u0393 Strike", self._clr_gex_pos.get())
        elif view == "charm":
            _add(pos, "Max Charm+", self._clr_charm_pos.get())
            _add(neg, "Max Charm\u2212", self._clr_neg.get())
            _add(flip, "Charm Flip", WHITE)
        elif view == "dex":
            _add(pos, "Max \u0394+", self._clr_dex_pos.get())
            _add(neg, "Max \u0394\u2212", self._clr_neg.get())
            _add(flip, "\u0394 Flip", WHITE)
            proj_flip = self._compute_projected_flip(data, current_spot)
            _add(proj_flip, "Proj \u0394 Flip 15:00", self._trading["dex_proj_flip"], linestyle="--")
        elif view == "vanna":
            _add(pos, "Max Vanna+", self._clr_vanna_pos.get())
            _add(neg, "Max Vanna\u2212", self._clr_neg.get())
            _add(flip, "Vanna Flip", WHITE)

        # Last Close for all views.
        last_close = self._fetch_last_close(symbol)
        _add(last_close, "Last Close", FG_DIM, linestyle="--")

        # Spot on the heatmap edge — used to overflow into the heatmap from
        # the bar panel; keep it here where it's always readable.
        if current_spot is not None:
            spot_clr = self._stl["Spot Text"]["color"].get()
            _add(current_spot, f"Spot {current_spot:,.1f}", spot_clr)

        # Expected-move +/-1s labels (heatmap edge).
        if self._show_em_var.get() and self._last_em and current_spot is not None:
            em_clr = self._stl["EM Text"]["color"].get()
            _add(current_spot + self._last_em,
                 f"+1s {current_spot + self._last_em:,.1f}", em_clr)
            _add(current_spot - self._last_em,
                 f"-1s {current_spot - self._last_em:,.1f}", em_clr)

        xmin, xmax = ax_heat.get_xlim()

        # Always draw the horizontal lines at their true strike.
        for strike, (label, color, linestyle) in labels_by_strike.items():
            ax_heat.axhline(y=strike, color=color, linewidth=0.7, alpha=0.6,
                            linestyle=linestyle, zorder=4)

        # Cluster labels whose strikes are within ~0.15% of spot (or a small
        # absolute fraction of the visible y-range) so their right-edge text
        # doesn't overlap. Within a cluster, merge labels into one line at
        # the cluster's mean strike. Across clusters, nudge text vertically
        # using offset_points so neighbouring clusters never collide.
        if not labels_by_strike:
            return

        y_span = max(y_hi - y_lo, 1.0)
        # Tolerance: ~1.2% of the visible window — roughly one fontsize-8 line height.
        tol = y_span * 0.012

        sorted_items = sorted(labels_by_strike.items(), key=lambda kv: kv[0])
        clusters = []  # list of [strike_list, [(label, color, style), ...]]
        for strike, payload in sorted_items:
            if clusters and abs(strike - clusters[-1][0][-1]) <= tol:
                clusters[-1][0].append(strike)
                clusters[-1][1].append(payload)
            else:
                clusters.append([[strike], [payload]])

        # Min vertical pixel separation between successive clusters' anchor text.
        from matplotlib import transforms
        line_pad_pts = 11  # ~fontsize 8 + a couple pts
        last_disp_y = None
        renderer = ax_heat.figure.canvas.get_renderer() if hasattr(
            ax_heat.figure.canvas, "get_renderer") else None

        for strike_list, payloads in clusters:
            anchor = sum(strike_list) / len(strike_list)
            # Merge labels at this cluster — first color/style wins for the line,
            # but each label keeps its own color via separate text() calls stacked.
            # Simplest: concatenate with " / " in cluster's first color.
            merged_label = " / ".join(p[0] for p in payloads)
            color = payloads[0][1]

            # Convert anchor strike to display pixels, push down if too close
            # to the previous cluster, then convert back to data coords.
            disp_xy = ax_heat.transData.transform((xmax, anchor))
            if last_disp_y is not None and disp_xy[1] - last_disp_y < line_pad_pts:
                disp_xy = (disp_xy[0], last_disp_y + line_pad_pts)
            last_disp_y = disp_xy[1]
            data_xy = ax_heat.transData.inverted().transform(disp_xy)

            ax_heat.text(xmax, data_xy[1], f"  {merged_label}",
                         color=color, fontsize=8, va="center", ha="left",
                         clip_on=False, zorder=4)

    def _build_forward_band(self, strikes, view, current_spot):
        """Compute per-strike forward-projected exposure at each 5-min slot from
        next_boundary(now) through 15:00 CT.

        Returns (fwd_strikes, fwd_times, matrix). fwd_strikes == the input
        strikes list for alignment with the historical matrix. matrix shape
        is (len(strikes), len(slots)). Empty matrix when no future slots exist.
        """
        import numpy as np
        from datetime import datetime, timedelta

        # Reuse the collector's boundary function via module import (keeps logic in one place).
        try:
            from gex_collector import next_boundary, POLL_INTERVAL_MIN
        except ImportError:
            return strikes, [], np.zeros((len(strikes), 0))

        now = datetime.now(TZ)
        close = now.replace(hour=CLOSE_HOUR_CT, minute=CLOSE_MIN_CT, second=0, microsecond=0)
        if now >= close:
            return strikes, [], np.zeros((len(strikes), 0))

        slots = []
        cursor = next_boundary(now)
        while cursor <= close:
            slots.append(cursor)
            cursor += timedelta(minutes=POLL_INTERVAL_MIN)

        if not slots:
            return strikes, [], np.zeros((len(strikes), 0))

        matrix = np.full((len(strikes), len(slots)), np.nan)
        for col_idx, slot_time in enumerate(slots):
            hours_to_close = (close - slot_time).total_seconds() / 3600.0
            # MVP: treat the forward slots as if nearest expiry is today's close.
            # For multi-DTE chains we'd need per-expiry handling.
            T_future = max(hours_to_close / (365 * 24), 1e-6)
            per_strike = self._engine.project_exposure_forward(view, T_future)
            for row_idx, strike in enumerate(strikes):
                if strike in per_strike:
                    matrix[row_idx, col_idx] = per_strike[strike]

        fwd_times = [int(s.timestamp()) for s in slots]
        return strikes, fwd_times, matrix

    def _draw_comparison_dots(self, ax, strikes, snapshot, display, grouping, color):
        """Overlay small dots for a comparison snapshot (y = strike price)."""
        comp_gex = GammaEngine.group_gex(snapshot["gex"], grouping)
        for s in strikes:
            if s in comp_gex:
                val = comp_gex[s][display]
                if val != 0:
                    ax.plot(val, s, "o", color=color, markersize=4, alpha=0.7)

    @staticmethod
    def _fmt_gex(x, _pos):
        """Format GEX value as K/M/B."""
        ax_val = abs(x)
        if ax_val >= 1e9:
            return f"{x / 1e9:.1f}B"
        if ax_val >= 1e6:
            return f"{x / 1e6:.1f}M"
        if ax_val >= 1e3:
            return f"{x / 1e3:.0f}K"
        return f"{x:.0f}"

    # ── Data Fetch (background thread) ──

    def _do_fetch(self):
        """Fetch option chain once, compute GEX and EM from it.

        Called from the worker thread.  Tkinter variable reads use simple
        string gets which are safe under the GIL.  All UI mutations and
        shared-state writes are dispatched to the main thread via ``after``.
        """
        symbol = self._symbol_var.get()
        today = datetime.now(TZ).date()
        use_volume = (self._formula_var.get() == "volume")

        try:
            # SPX has daily expirations; VIX only Wed/Tue.
            # Widen window to 7 days so the nearest VIX expiration is included.
            # _find_nearest_exp_key picks the closest one.
            to_date = today + timedelta(days=7)
            kwargs = {"contract_type": self._client.Options.ContractType.ALL,
                      "from_date": today, "to_date": to_date}
            with self._client_lock:
                r = self._client.get_option_chain(symbol, **kwargs)
            chain = r.json() if r.status_code == 200 else None
        except Exception as e:
            log.error("GEX fetch failed for %s: %s", symbol, e)
            chain = None

        if not chain:
            self.after(0, lambda: self._status_lbl.configure(
                text=f"Fetch failed for {symbol}"))
            return

        # Single chain, four computations — engine is only touched here
        result = self._engine.calc_from_chain(chain, use_volume=use_volume)
        charm_result = self._engine.calc_charm_from_chain(chain, use_volume=use_volume)
        dex_result = self._engine.calc_dex_from_chain(chain, use_volume=use_volume)
        vanna_result = self._engine.calc_vanna_from_chain(chain, use_volume=use_volume)
        last_em = self._engine.calc_expected_move_from_chain(chain)

        spot = chain.get("underlyingPrice", 0)
        sc = result["strike_count"] if result else 0
        dte = self._engine._last_dte

        # Dealer Pinch state — the IV/RV price-history fetch belongs on this
        # worker thread (never on the UI thread). Stashed for the status panel.
        self._last_pinch_state = self._compute_pinch_state(
            chain=chain, spot=spot, dte=dte, expected_move=last_em,
            gex_result=result)
        dte_label = "0-DTE" if dte == 0 else f"{dte}-DTE"
        formula_label = "Vol-Weighted" if use_volume else "Standard (OI)"

        def _update_ui():
            # Drop stale results: if user switched symbol during the fetch,
            # this result belongs to the wrong buffer — skip the append.
            if self._symbol_var.get() != symbol:
                return
            self._last_em = last_em
            # Update prev/open trackers BEFORE overwriting current.
            # Reset open trackers on day rollover (mirrors engine._today_str logic).
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            if getattr(self, "_open_today_str", None) != today_str:
                self._open_today_str = today_str
                self._open_charm_data = None
                self._open_dex_data = None
                self._open_vanna_data = None
            self._prev_charm_data = self._charm_data
            self._prev_dex_data = self._dex_data
            self._prev_vanna_data = self._vanna_data
            if self._open_charm_data is None and charm_result is not None:
                self._open_charm_data = charm_result
            if self._open_dex_data is None and dex_result is not None:
                self._open_dex_data = dex_result
            if self._open_vanna_data is None and vanna_result is not None:
                self._open_vanna_data = vanna_result
            self._charm_data = charm_result
            self._dex_data = dex_result
            self._vanna_data = vanna_result
            self._status_lbl.configure(
                text=f"{symbol}  |  {spot:,.1f}  |  {dte_label}  |  {sc} strikes  |  {formula_label}")
            self._redraw()
            # Grow the term-view slider as new snapshots arrive (live-follow).
            # Wrapped so any slider issue cannot break the main refresh path.
            try:
                if self._view_var.get() == "term":
                    self._refresh_term_slider()
            except Exception as e:
                log.debug("term slider refresh skipped: %s", e)

        self.after(0, _update_ui)

    def _fetch_symbol_analysis(self, symbol):
        use_volume = (self._formula_var.get() == "volume")
        grouping = self._grouping_var.get()
        return fetch_symbol_analysis(
            self._client, symbol, use_volume=use_volume, grouping=grouping,
        )

    # ── Worker Thread ──

    def _start_worker(self):
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._collector_thread = threading.Thread(
            target=self._collector_loop, daemon=True)
        self._collector_thread.start()
        self._tick_countdown()

    def _collector_loop(self):
        """Daemon thread: own RW DB connection + the embedded GEX collector.
        Guarded by the shared lock file so a standalone collector won't also
        write. Interrupted by self._stop_event on window close."""
        import gex_collector as gc
        import gex_history_db as _hdb
        import time as _time
        # Durable file logging so an in-tool poll failure / crash traceback
        # lands in logs/gex_collector.log instead of being lost to the console.
        try:
            gc.ensure_file_logging()
        except Exception:
            pass
        try:
            # Wait for the lock instead of giving up after one try. This lets a
            # RESTART recover: if a previous instance was killed without
            # releasing the lock, its orphaned-but-fresh lock blocks us only
            # until it ages out (LOCK_TTL_SEC), after which we take over. A
            # genuinely live standalone collector keeps the lock fresh and we
            # stay idle ("external") until it stops. Interruptible by close.
            self._collector_external = True
            acquired = gc.wait_for_lock(
                gc.LOCK_PATH, source="gamma_tool",
                owner=self._collector_owner,
                now_fn=lambda: int(_time.time()),
                interrupted=self._stop_event.wait,
                check_interval=30)
            if not acquired:
                log.info("In-tool collector stopping before lock acquired "
                         "(window closed or live external collector).")
                return
            self._collector_external = False
            log.info("In-tool GEX collector acquired lock; collecting.")
            conn = None
            try:
                conn = _hdb.connect()
                _hdb.init_schema(conn)
                _hdb.purge_old(conn)
                _poll = gc.make_heartbeat_poll(
                    gc.LOCK_PATH, source="gamma_tool",
                    owner=self._collector_owner, client_lock=self._client_lock)
                gc.run_collector_loop(
                    self._client, GammaEngine(), conn,
                    stop_event=self._stop_event, poll=_poll)
            finally:
                if conn is not None:
                    conn.close()
                gc.release_lock(gc.LOCK_PATH, owner=self._collector_owner)
        except Exception:
            log.exception("In-tool GEX collector crashed")

    def _worker_loop(self):
        """Background loop: fetch immediately, then every REFRESH_INTERVAL seconds.

        All fetches run on this single worker thread.  ``_trigger_refresh``
        wakes the worker early via ``_refresh_event`` instead of spawning a
        second thread, which prevents concurrent fetch / snapshot corruption.
        """
        while not self._stop_event.is_set():
            self._do_fetch()
            self._countdown = self.REFRESH_INTERVAL
            # Wait, but wake early if refresh requested or stop signalled
            self._refresh_event.wait(self.REFRESH_INTERVAL)
            self._refresh_event.clear()

    def _tick_countdown(self):
        """Update countdown label every second."""
        if self._stop_event.is_set():
            return
        self._countdown = max(0, self._countdown - 1)
        mins, secs = divmod(self._countdown, 60)
        self._countdown_lbl.configure(text=f"Next refresh: {mins}:{secs:02d}")
        self.after(1000, self._tick_countdown)

    # ── Symbol defaults ──

    # Default strike grouping per symbol
    _SYMBOL_GROUPING = {"$VIX": 0.5}
    _DEFAULT_GROUPING = 1

    def _on_bar_hover(self, event):
        """Show/hide tooltip based on cursor position over left-panel bars.

        Three branches:
          1. Outside _ax_bars — hide if visible
          2. No cache (empty-data redraw) — hide if visible
          3. Inside _ax_bars with cache — find nearest strike within
             bar_height/2 of event.ydata; if found, populate and show;
             if cursor is in a gap, hide.
        """
        annot = self._hover_annotation
        if annot is None:
            return  # no redraw has created it yet (first frame)

        # Branch 1: cursor outside the bars axis (heatmap, title, margins).
        if event.inaxes is not self._ax_bars:
            if annot.get_visible():
                annot.set_visible(False)
                self._canvas.draw_idle()
            return

        # Branch 2: no cached strikes (placeholder/empty-data redraw).
        if not self._hover_strikes or event.ydata is None:
            if annot.get_visible():
                annot.set_visible(False)
                self._canvas.draw_idle()
            return

        # Branch 3: hit-test. Find nearest strike to event.ydata.
        y = event.ydata
        nearest = min(self._hover_strikes, key=lambda s: abs(s - y))
        tolerance = self._hover_bar_height / 2.0
        if abs(nearest - y) > tolerance:
            # Cursor in a gap between bars.
            if annot.get_visible():
                annot.set_visible(False)
                self._canvas.draw_idle()
            return

        # Hit. Build tooltip text.
        # Note: rstrip("0").rstrip(".") is safe here because this project's
        # strikes are always >= ~$18 (VIX floor); it would over-strip
        # hypothetical single-digit strikes like 10.0 -> "1".
        cell = self._hover_grid.get(nearest, {})
        strike_label = f"{nearest:,.1f}".rstrip("0").rstrip(".")
        text = (
            f"{strike_label}\n"
            f"Net:  {_fmt_dollar_magnitude(cell.get('net', 0.0))}\n"
            f"Call: {_fmt_dollar_magnitude(cell.get('call', 0.0))}\n"
            f"Put:  {_fmt_dollar_magnitude(cell.get('put', 0.0))}"
        )

        # Adaptive offset — flip tooltip to the left of the cursor when the
        # cursor is on the left half of the axes (near negative values) so
        # the tooltip doesn't go past the axes' left edge.
        if event.xdata is not None and event.xdata < 0:
            annot.set_position((-8, 0))
            annot.set_horizontalalignment("right")
        else:
            annot.set_position((8, 0))
            annot.set_horizontalalignment("left")

        annot.set_text(text)
        annot.xy = (event.xdata if event.xdata is not None else 0, nearest)
        annot.set_visible(True)
        self._canvas.draw_idle()

    def _on_symbol_change(self):
        """Update default grouping for the selected symbol, then refresh.

        History is per-symbol in the SQLite store, so no buffer to clear —
        switching symbols just re-queries the DB on next redraw.
        """
        sym = self._symbol_var.get()
        default_grp = self._SYMBOL_GROUPING.get(sym, self._DEFAULT_GROUPING)
        self._grouping_var.set(default_grp)
        self._refresh_term_button_state()
        self._trigger_refresh()

    def _on_formula_change(self):
        """Refresh after formula toggle.

        The collector only stores OI-based snapshots; if the user selects the
        volume formula, ``_load_history_dicts`` returns [] so history hides.
        """
        self._trigger_refresh()

    def _update_pressure_panel(self):
        """Refresh the 0-DTE delta-pressure panel based on view and _dex_data.

        Panel is shown only in the DEX and Charm views (hidden in GEX,
        Vanna, and Term). In DEX and Charm views, shows three
        $-magnitudes (now / projected close / hedge pressure) — Charm view
        shares the same projection because charm IS what produces the
        projected-close delta (delta_proj = delta + charm × dt). Falls back
        to a greyed "No 0-DTE" label when the chain has no same-day expiry.
        """
        view = self._view_var.get() if hasattr(self, "_view_var") else "gex"
        if view not in ("dex", "charm"):
            # GEX, Vanna, Term: no pressure/drift panel. (Vanna's drift data now
            # lives in the Explain popup.)
            self._pressure_frame.pack_forget()
            return

        # DEX/Charm path — existing behavior unchanged
        self._pressure_frame.pack(side="right", anchor="e")

        dex = self._dex_data
        now_val = dex.get("net_delta_0dte") if dex else None
        proj_val = dex.get("projected_net_delta_close") if dex else None
        hedge_val = dex.get("hedge_pressure") if dex else None

        if now_val is None:
            self._pressure_label_now.configure(
                text="No 0-DTE contracts", fg=FG_DIM,
            )
            self._pressure_label_proj.configure(text="")
            self._pressure_label_hedge.configure(text="")
            return

        # Projected-close label uses CLOSE_HOUR_CT / CLOSE_MIN_CT constants
        # from Task 3.
        self._pressure_label_now.configure(
            text=f"0-DTE \u0394 now:      {_fmt_dollar_magnitude(now_val)}",
            fg=FG_PRIMARY,
        )
        self._pressure_label_proj.configure(
            text=f"Projected {CLOSE_HOUR_CT:02d}:{CLOSE_MIN_CT:02d}: "
                 f"{_fmt_dollar_magnitude(proj_val)}",
            fg=FG_PRIMARY,
        )
        if hedge_val is None:
            direction = ""
            color = FG_PRIMARY
        elif hedge_val > 0:
            direction = " (buy)"
            color = "#3bd671"  # green
        elif hedge_val < 0:
            direction = " (sell)"
            color = "#e06c75"  # red
        else:
            direction = ""
            color = FG_PRIMARY
        self._pressure_label_hedge.configure(
            text=f"Hedge pressure:  {_fmt_dollar_magnitude(hedge_val)}{direction}",
            fg=color,
        )

    def _show_explain(self):
        """Open/refresh the plain-English Explain popup for the active view.

        Gathers the same in-memory snapshots the status strip uses
        (mirrors ``_update_collector_status`` ~5599) and hands them to the
        pure ``build_explain_text`` builder. All optional-data and
        sentiment/bridge access is guarded so a hiccup can never crash the
        popup.
        """
        view = self._view_var.get()
        gex_summary = (GammaEngine.snapshot_summary(self._engine.current, "gex")
                       if self._engine.current else None)
        charm_summary = (GammaEngine.snapshot_summary(self._charm_data, "charm")
                         if self._charm_data else None)
        dex_summary = (GammaEngine.snapshot_summary(self._dex_data, "dex")
                       if self._dex_data else None)
        spot = (self._engine.current.get("spot")
                if self._engine.current else None)
        vix_now, vix_open = _load_vix_today(self._db)
        vix_delta = (vix_now - vix_open
                     if vix_now is not None and vix_open is not None else None)

        # Build the drift panel whenever vanna data exists (not just on the
        # vanna tab) — the combined Explain page renders every view's section.
        drift_panel = None
        if self._vanna_data:
            try:
                charm_flip = charm_summary.get("flip") if charm_summary else None
                now = datetime.now(TZ)
                hours_to_close = max(
                    0.0,
                    (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
                )
                drift_panel = format_drift_pressure_panel(
                    vanna_data=self._vanna_data, charm_data=self._charm_data,
                    vix_now=vix_now, vix_open=vix_open,
                    spot=spot or 0.0, dte=self._engine._last_dte or 0,
                    expected_move=(spot * 0.005) if spot else 1.0,
                    hours_to_close=hours_to_close,
                    top5_oi=0, charm_flip=charm_flip,
                )
            except Exception:
                drift_panel = None

        try:
            from regime_filter import evaluate_regime
            sentiment = evaluate_regime()
        except Exception:
            sentiment = {"active": False}

        # Term view: derive near/far walls from the snapshot currently shown
        # in the term heatmap (set by _load_term_snapshot_at). None when not
        # on the term view or no SPXW snapshot is loaded.
        term_data = None
        if view == "term":
            try:
                term_data = _term_walls_from_rows(
                    getattr(self, "_term_last_rows", None))
            except Exception:
                term_data = None

        # Max pain / pin risk / magnet from the retained chain (quick win #1).
        max_pain_ctx = None
        try:
            chain = getattr(self._engine, "_last_chain", None)
            if chain is not None:
                mp = calc_max_pain_from_chain(chain)
                if mp is not None:
                    grid = (GammaEngine.group_gex(self._engine.current["gex"],
                                                  self._grouping_var.get())
                            if self._engine.current else {})
                    kg = find_key_gamma_strike(grid, spot or 0.0)
                    max_pain_ctx = {
                        "max_pain": mp["max_pain"],
                        "pin_risk": pin_risk(spot, mp["max_pain"], self._last_em),
                        "magnet": zero_dte_magnet(spot, mp["max_pain"], kg),
                    }
        except Exception:
            max_pain_ctx = None

        # Directional walls (quick win #2): GEX basis from the live grid,
        # OI basis from the retained chain.
        walls_ctx = None
        try:
            grid = (GammaEngine.group_gex(self._engine.current["gex"],
                                          self._grouping_var.get())
                    if self._engine.current else {})
            chain = getattr(self._engine, "_last_chain", None)
            walls_ctx = {
                "gex": get_directional_walls({"gex": grid, "spot": spot}, spot or 0.0),
                "oi": get_oi_walls(chain, spot or 0.0) if chain is not None
                else {"call_wall": None, "put_wall": None},
            }
        except Exception:
            walls_ctx = None

        ctx = {
            "symbol": self._symbol_var.get(), "spot": spot,
            "dte": self._engine._last_dte or 0,
            "vix_now": vix_now, "vix_delta": vix_delta,
            "gex_summary": gex_summary, "charm_summary": charm_summary,
            "dex_summary": dex_summary, "drift_panel": drift_panel,
            "sentiment": sentiment, "term_data": term_data,
            "max_pain": max_pain_ctx, "walls": walls_ctx,
            "pc_ratios": (calc_pc_ratios(getattr(self._engine, "_last_chain", None))
                          if getattr(self._engine, "_last_chain", None) is not None
                          else None),
            "oi_concentration": (
                calc_oi_concentration(getattr(self._engine, "_last_chain", None))
                if getattr(self._engine, "_last_chain", None) is not None else None),
            "hedge_shares": (
                dealer_hedge_shares(gex_summary.get("net_total"), spot)
                if gex_summary else None),
            "gamma_acceleration": (
                (calc_gamma_acceleration(getattr(self._engine, "_last_chain", None))
                 or {}).get("ratio")
                if getattr(self._engine, "_last_chain", None) is not None else None),
        }
        # One combined page covering every view + the Dealer Pinch section,
        # regardless of the active tab.
        text = build_explain_html_text(ctx)
        self._render_explain_popup(text)

    def _render_explain_popup(self, text):
        """Render the combined Explain page (all views + Dealer Pinch) to a
        styled HTML file and open it.

        Matches the Key-Levels page (dark theme, headers, colors, Google-search
        hyperlinks). Falls back to a minimal Tk text popup if HTML render/write
        fails, so the Explain button always shows something.
        """
        import webbrowser
        from pathlib import Path
        try:
            import html_render
            out_path = Path(__file__).parent / "data" / "explain.html"
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_text(
                html_render.render_explain_html(
                    text, pinch_state=getattr(self, "_last_pinch_state", None),
                    symbol=self._symbol_var.get()),
                encoding="utf-8")
            webbrowser.open(out_path.as_uri())
        except Exception:
            log.exception("Explain HTML render failed; falling back to Tk popup")
            self._render_explain_popup_tk("explain", text)

    def _render_explain_popup_tk(self, view, text):
        """Fallback: scrollable Tk Text popup (used only if HTML render fails)."""
        title = {"gex": "GEX", "charm": "Charm", "dex": "DEX",
                 "vanna": "Vanna", "term": "Term"}.get(view, view).upper()
        win = getattr(self, "_explain_win", None)
        if win is None or not win.winfo_exists():
            win = tk.Toplevel(self)
            win.configure(bg=BG_PANEL)
            win.geometry("540x560")
            self._explain_win = win
            txt = tk.Text(win, wrap="word", bg=BG_INPUT, fg=FG_PRIMARY,
                          font=(FONT, 10), relief="flat", padx=12, pady=10,
                          borderwidth=0)
            sb = tk.Scrollbar(win, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="top", fill="both", expand=True)
            btnf = tk.Frame(win, bg=BG_PANEL)
            btnf.pack(side="bottom", fill="x")
            tk.Button(btnf, text="Copy", command=lambda: (
                self.clipboard_clear(),
                self.clipboard_append(self._explain_txt.get("1.0", "end-1c"))),
                bg=BG_INPUT, fg=FG_PRIMARY, relief="flat", cursor="hand2",
                font=(FONT, 9)).pack(side="left", padx=6, pady=4)
            tk.Button(btnf, text="Close", command=win.destroy,
                bg=BG_INPUT, fg=FG_PRIMARY, relief="flat", cursor="hand2",
                font=(FONT, 9)).pack(side="right", padx=6, pady=4)
            self._explain_txt = txt
        win.title(f"Explain \u2014 {title} view")
        self._explain_txt.configure(state="normal")
        self._explain_txt.delete("1.0", "end")
        self._explain_txt.insert("1.0", text)
        self._explain_txt.configure(state="disabled")
        win.deiconify()
        win.lift()

    def _update_collector_status(self):
        """Refresh the collector health status label based on DB + current time."""
        view = self._view_var.get() if hasattr(self, "_view_var") else "gex"
        symbol = self._symbol_var.get() if hasattr(self, "_symbol_var") else "$SPX"

        if getattr(self, "_collector_external", False):
            self._status_label.configure(
                text="Collector: external", foreground="gray")
        else:
            age, last_ts = (None, None)
            has_data = False
            if self._db is not None:
                try:
                    age, last_ts = _history_db.last_snapshot_age(self._db, symbol, view)
                    has_data = last_ts is not None
                except sqlite3.OperationalError:
                    pass

            text, color = classify_collector_status(
                age_seconds=age,
                now_ct=datetime.now(STATUS_TZ),
                has_data=has_data,
                last_ts=last_ts,
            )
            self._status_label.configure(text=text, foreground=color)

        # Key-levels headline (view-aware). Built from current in-memory
        # snapshots; safe to call even when state is partially populated —
        # _drift_headline_text returns "" if the required data is missing.
        gex_summary = (GammaEngine.snapshot_summary(self._engine.current)
                       if self._engine.current else None)
        charm_summary = (GammaEngine.snapshot_summary(self._charm_data)
                         if self._charm_data else None)
        drift_panel_dict = None
        if view == "vanna" and self._vanna_data:
            vix_now, vix_open = _load_vix_today(self._db)
            charm_flip = charm_summary.get("flip") if charm_summary else None
            spot = (self._engine.current.get("spot")
                    if self._engine.current else 0.0) or 0.0
            now = datetime.now(TZ)
            hours_to_close = max(
                0.0,
                (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
            )
            try:
                drift_panel_dict = format_drift_pressure_panel(
                    vanna_data=self._vanna_data, charm_data=self._charm_data,
                    vix_now=vix_now, vix_open=vix_open,
                    spot=spot, dte=self._engine._last_dte or 0,
                    expected_move=(spot * 0.005) if spot else 1.0,
                    hours_to_close=hours_to_close,
                    top5_oi=0, charm_flip=charm_flip,
                )
            except Exception:
                drift_panel_dict = None
        # Dealer Pinch flag (rendered from the worker-computed state; no fetch
        # on this UI-thread path). Fill forced-hedge direction from the drift
        # pair-state when it's available (vanna view).
        try:
            state = getattr(self, "_last_pinch_state", None)
            if state is not None and drift_panel_dict:
                st = drift_panel_dict.get("pair_state", "")
                state["forced_hedge_dir"] = (
                    "up" if st == "AGREE_UP" else
                    "down" if st == "AGREE_DOWN" else
                    state.get("forced_hedge_dir"))
            ptext, pcolor = pinch_flag_text(state)
            if hasattr(self, "_pinch_lbl"):
                self._pinch_lbl.configure(text=ptext, foreground=pcolor or FG_DIM)
        except Exception:
            pass

        headline = _drift_headline_text(
            view, gex_summary, charm_summary, self._dex_data, drift_panel_dict,
        )
        if hasattr(self, "_headline_label"):
            self._headline_label.configure(text=headline)

    def _fetch_last_close(self, symbol):
        """Return yesterday's (or most recent trading day's) close, cached per session.

        One API call per symbol per session. On failure, returns None and avoids
        retry via self._last_close_attempted. Subsequent calls for the same
        symbol during the session hit cache without re-raising.
        """
        if symbol in self._last_close_cache:
            return self._last_close_cache[symbol]
        if symbol in self._last_close_attempted:
            return None
        self._last_close_attempted.add(symbol)
        try:
            from scanner_engine import fetch_price_history
            hist = fetch_price_history(self._client, symbol)
            candles = (hist or {}).get("candles") or []
            if not candles:
                self._last_close_cache[symbol] = None
                return None
            # Most recent completed trading day = last candle in the list.
            last = candles[-1]
            close = float(last.get("close") or 0)
            if close <= 0:
                self._last_close_cache[symbol] = None
                return None
            self._last_close_cache[symbol] = close
            return close
        except Exception as e:
            log.warning("fetch_last_close failed for %s: %s", symbol, e)
            self._last_close_cache[symbol] = None
            return None

    def _load_history_dicts(self, view="gex") -> list[dict]:
        """Load today's snapshots from SQLite as snapshot_summary-shaped dicts.

        Accepts either a view string ('gex', 'charm', 'dex') or a legacy bool
        (True → 'charm', False → 'gex') for back-compat.

        Returns empty list if DB is unavailable, the user chose a formula the
        collector doesn't store (volume), or the query fails.
        """
        # Back-compat: older callers pass is_charm=True/False.
        if view is True:
            view = "charm"
        elif view is False:
            view = "gex"
        if self._db is None:
            return []
        # Collector stores only use_volume=False snapshots. If the user has
        # toggled volume-based formula, history is meaningless — hide it.
        try:
            if str(self._formula_var.get()).lower().startswith("vol"):
                return []
        except Exception:
            pass
        symbol = self._symbol_var.get()
        try:
            rows = _history_db.load_today_with_grid(self._db, symbol, view)
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            ts_raw = r[0]
            try:
                ts_dt = datetime.fromtimestamp(int(ts_raw), TZ)
            except (TypeError, ValueError, OSError):
                continue
            out.append({
                "ts": ts_dt,
                "spot": r[1],
                "flip": r[2],
                "top_pos_strike": r[3],
                "top_neg_strike": r[4],
                "net_total": r[5],
                "gex": r[6],
            })
        return out

    def _trigger_refresh(self):
        """Manual refresh: wake the worker thread to fetch immediately."""
        self._status_lbl.configure(text="Refreshing...")
        self._countdown = 0
        self._refresh_event.set()  # wake worker

    # ── AI Analysis ──

    def _analyze(self, auto=False, slot_tag=None):
        """Slot-routed bundled SPX/SPY/QQQ analysis. Writes two prompt files
        per slot to data/. No PNGs."""
        if slot_tag is None:
            slot_tag = "manual"

        # In-flight guard — prevents overlapping runs if a manual click arrives
        # while a previous (auto or manual) run is still fetching, or vice-versa.
        if getattr(self, "_analyze_inflight", False):
            if auto:
                log.warning("Auto-analyze %s arrived while a run is in flight; skipping", slot_tag)
            return

        spx_engine_data = self._engine.current
        spx_charm = self._charm_data
        spx_dex = self._dex_data
        spx_vanna = self._vanna_data
        if spx_engine_data is None and spx_charm is None and spx_dex is None and spx_vanna is None:
            if not auto:
                self._status_lbl.configure(text="No SPX data to analyze")
            else:
                log.warning("Auto-analyze %s: SPX data missing; skipping", slot_tag)
            return

        # Read Tk vars on the main thread BEFORE dispatching the worker.
        # Tkinter is not thread-safe — touching StringVar/DoubleVar from a
        # daemon thread can raise "main thread is not in main loop".
        use_volume = (self._formula_var.get() == "volume")
        grouping = self._grouping_var.get()
        client = self._client

        self._analyze_inflight = True
        self._analyze_btn.configure(text="\U0001f916 Bundling...", state="disabled")

        spx_blocks = {
            "gex":   self._build_analysis_data(spx_engine_data, view="gex")   if spx_engine_data else None,
            "charm": self._build_analysis_data(spx_charm, view="charm")       if spx_charm       else None,
            "dex":   self._build_analysis_data(spx_dex, view="dex")           if spx_dex         else None,
            "vanna": self._build_analysis_data(spx_vanna, view="vanna")       if spx_vanna       else None,
        }

        def _worker():
            try:
                spy = fetch_symbol_analysis(
                    client, "SPY", use_volume=use_volume, grouping=grouping)
            except Exception:
                log.exception("SPY fetch failed in worker")
                spy = None
            try:
                qqq = fetch_symbol_analysis(
                    client, "QQQ", use_volume=use_volume, grouping=grouping)
            except Exception:
                log.exception("QQQ fetch failed in worker")
                qqq = None
            try:
                internals = _fetch_market_internals(client)
            except Exception:
                log.exception("Market internals fetch failed in worker")
                internals = {}
            self.after(0, lambda: self._finalize_analyze(
                auto, slot_tag, spx_blocks, spy, qqq, internals))

        threading.Thread(target=_worker, daemon=True).start()

    def _finalize_analyze(self, auto, slot_tag, spx_blocks, spy_blocks,
                          qqq_blocks, internals):
        """Main-thread continuation after SPY/QQQ fetches return.

        Builds the detail + summary prompts via the bundled builders, writes
        slot-tagged files under data/, optionally copies/opens for manual
        runs, and updates the status bar. Always re-enables the button.
        """
        try:
            premarket = (slot_tag == "0820")

            # Gather SPX intraday-evolution history (intraday slots only).
            spx_history = None
            if not premarket:
                spx_history = [
                    ("GEX",   self._load_history_dicts("gex")),
                    ("Charm", self._load_history_dicts("charm")),
                    ("DEX",   self._load_history_dicts("dex")),
                    ("Vanna", self._load_history_dicts("vanna")),
                ]

            img_dir = Path(__file__).parent / "data"
            img_dir.mkdir(exist_ok=True)

            # Write JSON sidecar (best-effort — don't kill the whole finalize)
            try:
                write_slot_data_json(img_dir, slot_tag, spx_blocks, spy_blocks,
                                     qqq_blocks, internals)
            except Exception:
                log.exception("Failed to write slot JSON sidecar")

            # For 1500, gather earlier same-day JSONs and build the retrospective.
            path_block = None
            if slot_tag == "1500":
                jsons = {}
                for s in _RETROSPECTIVE_SLOTS:
                    j = read_today_slot_data(img_dir, s)
                    if j:
                        jsons[s] = j

                def _current_spot(blocks):
                    if blocks is None:
                        return None
                    for view in ("gex", "charm", "dex", "vanna"):
                        v = blocks.get(view)
                        if v and v.get("spot") is not None:
                            return v["spot"]
                    return None

                current_spots = {
                    "SPX": _current_spot(spx_blocks),
                    "SPY": _current_spot(spy_blocks),
                    "QQQ": _current_spot(qqq_blocks),
                }
                path_block = build_todays_path_block(jsons, current_spots)

            prompt = build_combined_prompt_bundled(
                spx_blocks, spy_blocks, qqq_blocks,
                premarket=premarket, spx_history=spx_history,
                internals=internals, slot_tag=slot_tag,
                todays_path_block=path_block,
            )
            summary = build_summary_prompt_bundled(
                spx_blocks, spy_blocks, qqq_blocks,
                premarket=premarket, internals=internals,
            )

            detail_name, summary_name = slot_filenames(slot_tag)
            (img_dir / detail_name).write_text(prompt, encoding="utf-8")
            (img_dir / summary_name).write_text(summary, encoding="utf-8")

            if not auto:
                self.clipboard_clear()
                self.clipboard_append(prompt)
                os.startfile(str(img_dir))

            n_ok = sum(1 for b in (spx_blocks, spy_blocks, qqq_blocks) if b)
            if auto:
                self._status_lbl.configure(
                    text=f"Auto {slot_tag.upper()}: {n_ok}/3 symbols, 2 prompts")
            else:
                self._status_lbl.configure(
                    text=f"Manual: {n_ok}/3 symbols · detail copied · folder opened")
            log.info("Slot %s bundle written: SPX=%s SPY=%s QQQ=%s",
                     slot_tag, bool(spx_blocks), bool(spy_blocks), bool(qqq_blocks))
        except Exception as e:
            log.exception("Finalize analyze failed: %s", e)
            try:
                self._status_lbl.configure(text=f"Analyze error: {str(e)[:30]}")
            except Exception:
                pass
        finally:
            self._analyze_inflight = False
            # Re-enable after a grace period, guarded against the window being
            # destroyed during the 5s wait.
            def _reenable():
                try:
                    if self.winfo_exists():
                        self._analyze_btn.configure(
                            text="\U0001f916 Analyze", state="normal")
                except Exception:
                    pass
            self.after(5000, _reenable)

    def _schedule_next_auto_analyze(self):
        """Compute delay to next scheduled auto-analyze and arm self.after().

        Weekday-only (Mon-Fri). If all of today's trigger times are in the
        past, rolls forward through weekend to next Monday's first slot.
        """
        from datetime import timedelta
        now = datetime.now(TZ)

        # Search up to 4 days forward (worst case: Fri evening → Mon morning).
        for day_offset in range(5):
            candidate_dt = now + timedelta(days=day_offset)
            if candidate_dt.weekday() >= 5:
                continue  # Skip Sat/Sun
            candidate_date = candidate_dt.date()
            for (h, m) in self._auto_analyze_times:
                target = datetime(
                    candidate_date.year, candidate_date.month, candidate_date.day,
                    h, m, 0, tzinfo=TZ,
                )
                if target > now:
                    delay_ms = int((target - now).total_seconds() * 1000)
                    self._auto_analyze_timer_id = self.after(
                        delay_ms, self._on_auto_analyze_fire,
                    )
                    log.info("Next auto-analyze scheduled for %s (%.1f min away)",
                             target.strftime("%a %H:%M CT"), delay_ms / 60000)
                    return
        # Safety fallback — should never hit given the 5-day window.
        log.warning("No auto-analyze slot found within 5-day window; not scheduling")

    def _on_auto_analyze_fire(self):
        """Timer-fired auto-analyze. Identifies the slot tag for the current
        fire time and passes it to _analyze. Never propagates exceptions —
        scheduler must survive any single-run failure and queue the next slot.
        """
        self._auto_analyze_timer_id = None
        try:
            now = datetime.now(TZ)
            tag = slot_tag_for_time(now.hour, now.minute)
            if tag is None:
                # Allow up to 90 seconds of drift in either direction.
                for (h, m), candidate in _FIRE_TIME_TO_SLOT.items():
                    target = datetime(now.year, now.month, now.day, h, m, tzinfo=TZ)
                    if abs((now - target).total_seconds()) <= 90:
                        tag = candidate
                        break
            if tag is None:
                log.warning("Auto-analyze fired but no slot tag matched %s", now)
            else:
                self._analyze(auto=True, slot_tag=tag)
        except Exception:
            log.exception("Auto-analyze fire failed; continuing scheduler")
        finally:
            self._schedule_next_auto_analyze()

    def _build_analysis_data(self, data, view="gex", is_charm=None):
        """Extract structured analysis data from a GEX/Charm/DEX snapshot."""
        # Back-compat: older callers pass is_charm=True/False.
        if is_charm is True:
            view = "charm"
        elif is_charm is False and view == "gex":
            view = "gex"
        spot = data["spot"]
        gex_raw = data["gex"]
        grouping = self._grouping_var.get()
        gex = GammaEngine.group_gex(gex_raw, grouping)
        symbol = self._symbol_var.get()
        dte = self._engine._last_dte

        now = datetime.now(TZ)
        close_hour, close_min = 15, 15
        hours_left = max(0, (close_hour - now.hour) + (close_min - now.minute) / 60.0)

        # Top 20 positive/negative strikes + tail aggregate so the LLM sees the
        # full distribution shape from structured data alone (no chart attached).
        top_pos, top_neg, tail = top_strikes_with_tail(gex, n=20)

        # Per-top-strike intraday context: change vs prior snapshot and value
        # at market open. Sources differ by view:
        #   GEX  → engine.previous / engine.market_open
        #   Charm/DEX → window-tracked _prev_*_data / _open_*_data
        if view == "charm":
            prev_data = self._prev_charm_data
            open_data = self._open_charm_data
        elif view == "dex":
            prev_data = self._prev_dex_data
            open_data = self._open_dex_data
        elif view == "vanna":
            prev_data = self._prev_vanna_data
            open_data = self._open_vanna_data
        else:
            prev_data = self._engine.previous
            open_data = self._engine.market_open

        prev_grid = (
            GammaEngine.group_gex(prev_data["gex"], grouping)
            if prev_data and prev_data.get("gex") else None
        )
        open_grid = (
            GammaEngine.group_gex(open_data["gex"], grouping)
            if open_data and open_data.get("gex") else None
        )
        top_strikes = [item["strike"] for item in top_pos] + \
                      [item["strike"] for item in top_neg]
        delta_change = delta_change_for_strikes(top_strikes, gex, prev_grid)
        value_at_open = value_at_open_for_strikes(top_strikes, open_grid)

        # 0-DTE pressure panel (DEX only). Numerical projection of intraday
        # delta state: current 0-DTE delta, projected close delta, hedge
        # pressure direction, and the projected EOD flip strike.
        pressure_panel = format_pressure_panel(data, spot) if view == "dex" else None

        # Flip point: where net crosses zero near spot
        flip_point = self._calc_flip_point(gex, spot)

        # Net by zone
        zones = {"above_0_2pct": 0, "below_0_2pct": 0, "below_2_5pct": 0}
        for s, vals in gex.items():
            net = vals["net"]
            if s > spot and s <= spot * 1.02:
                zones["above_0_2pct"] += net
            elif s < spot and s >= spot * 0.98:
                zones["below_0_2pct"] += net
            elif s < spot * 0.98 and s >= spot * 0.95:
                zones["below_2_5pct"] += net

        # ATM breakdown (5 strikes nearest spot)
        near_strikes = sorted(gex.keys(), key=lambda s: abs(s - spot))[:5]
        atm_breakdown = []
        for s in sorted(near_strikes):
            d = gex[s]
            atm_breakdown.append({
                "strike": s, "call": d["call"], "put": d["put"], "net": d["net"]
            })

        view_label = {"gex": "GEX", "charm": "Charm", "dex": "DEX", "vanna": "Vanna"}.get(view, "GEX")
        return {
            "view": view_label,
            "symbol": symbol,
            "spot": spot,
            "dte": dte,
            "expected_move": self._last_em,
            "em_upper": round(spot + self._last_em, 2) if self._last_em else None,
            "em_lower": round(spot - self._last_em, 2) if self._last_em else None,
            "timestamp": now.strftime("%I:%M %p CT"),
            "hours_to_close": round(hours_left, 2),
            "top_positive": top_pos,
            "top_negative": top_neg,
            "tail_summary": tail,
            "delta_change": delta_change,
            "value_at_open": value_at_open,
            "pressure_panel": pressure_panel,
            "flip_point": flip_point,
            "net_by_zone": zones,
            "atm_breakdown": atm_breakdown,
            "grouping": grouping,
        }

    @staticmethod
    def _calc_flip_point(gex, spot):
        """Find the strike near spot where net GEX/Charm crosses from positive to negative."""
        strikes = sorted(gex.keys())
        if len(strikes) < 2:
            return None

        # Look for zero-crossing near spot (within +/-3%)
        lo, hi = spot * 0.97, spot * 1.03
        nearby = [(s, gex[s]["net"]) for s in strikes if lo <= s <= hi]
        if len(nearby) < 2:
            return None

        # Find where sign changes
        for i in range(len(nearby) - 1):
            s1, v1 = nearby[i]
            s2, v2 = nearby[i + 1]
            if v1 * v2 < 0:  # sign change
                # Linear interpolation
                if v2 - v1 != 0:
                    flip = s1 + (s2 - s1) * (-v1) / (v2 - v1)
                    return round(flip, 1)
                return round((s1 + s2) / 2, 1)

        return None

    # ── Cleanup ──

    def _on_close(self):
        self._stop_event.set()
        self._refresh_event.set()          # wake worker so it exits promptly
        # Join the collector thread briefly so its lock is released before the
        # process may exit.
        if getattr(self, "_collector_thread", None) is not None:
            # If the join times out mid-poll the lock may not be released here, but the
            # lock TTL (gex_collector.LOCK_TTL_SEC) backstops it — the next collector reclaims a stale lock.
            self._collector_thread.join(timeout=2.0)
        # Cancel pending auto-analyze timer so it doesn't fire on a dead widget.
        if self._auto_analyze_timer_id is not None:
            try:
                self.after_cancel(self._auto_analyze_timer_id)
            except Exception:
                pass
            self._auto_analyze_timer_id = None
        if getattr(self, "_db", None) is not None:
            try:
                self._db.close()
            except Exception:
                pass
        import matplotlib.pyplot as plt
        plt.close(self._fig)
        self.destroy()
