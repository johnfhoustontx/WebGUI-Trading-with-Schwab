"""Tk Toplevel window for the Options Simulator.

Launched from dashboard.py via _open_simulator(). Shares the Schwab client
with the main dashboard; pulls a single chain snapshot per Refresh click.
"""
import logging
import threading
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import theme
from options_calculator import bs_greeks
from options_simulator.ai_prompt import build_simulator_prompt
from options_simulator.data import fetch_snapshot
from options_simulator.engine import (
    ChainSnapshot, ContractRow, Position,
    IVShockEngine, ReplayEngine, WhatIfEngine,
    aggregate_position,
)

log = logging.getLogger(__name__)


def _bs_greeks_at(snap, c, S, T_days):
    T = max(T_days / 365.0, 1e-6)
    return bs_greeks(S=S, K=c.strike, T=T, r=snap.r, sigma=c.iv, option_type=c.kind)


class OptionsSimulatorWindow(tk.Toplevel):
    def __init__(self, parent, client, initial_symbol: str = "SPY"):
        super().__init__(parent)
        self.title("Options Simulator")
        self.geometry("1200x800")

        self._chrome = theme.chrome()
        self.configure(bg=self._chrome["bg"])
        style = ttk.Style(self)
        theme.apply_ttk(style)

        self.client = client
        self.snapshot: ChainSnapshot | None = None
        self.selected_position: Position | None = None
        # Contract dropdown maps display strings → ContractRow.
        # Filtered by Type (Call / Put / Spread) and current Expiry.
        self._contract_index: list[ContractRow] = []
        self._short_leg_index: list[ContractRow] = []  # for the spread short-leg picker

        self._build_top_bar(initial_symbol)
        self._build_notebook()
        self._build_status_row()

    # ---- UI scaffolding ----

    def _build_top_bar(self, initial_symbol: str):
        # Two rows: row1 = symbol/expiry/buttons/status, row2 = type/direction/contract picker(s)
        bar = ttk.Frame(self, padding=6)
        bar.pack(side="top", fill="x")

        # ---- Row 1 ----
        row1 = ttk.Frame(bar)
        row1.pack(side="top", fill="x")

        ttk.Label(row1, text="Symbol:").pack(side="left")
        self.symbol_var = tk.StringVar(value=initial_symbol)
        symbol_entry = ttk.Entry(row1, textvariable=self.symbol_var, width=8)
        symbol_entry.pack(side="left", padx=4)
        symbol_entry.bind("<Return>", lambda _e: self._on_refresh())

        ttk.Label(row1, text="Expiry:").pack(side="left", padx=(12, 0))
        self.expiry_var = tk.StringVar(value="")
        self.expiry_combo = ttk.Combobox(row1, textvariable=self.expiry_var,
                                          width=14, state="readonly")
        self.expiry_combo.pack(side="left", padx=4)
        self.expiry_combo.bind("<<ComboboxSelected>>", self._on_expiry_changed)

        ttk.Button(row1, text="Refresh data", command=self._on_refresh).pack(side="left", padx=8)
        ttk.Button(row1, text="\U0001f916 Ask AI", command=self._on_ask_ai).pack(side="left")

        self.status_var = tk.StringVar(value="Type a symbol and press Enter (or click Refresh).")
        ttk.Label(row1, textvariable=self.status_var).pack(side="left", padx=12)

        # ---- Row 2 ----
        row2 = ttk.Frame(bar)
        row2.pack(side="top", fill="x", pady=(4, 0))

        ttk.Label(row2, text="Type:").pack(side="left")
        self.type_var = tk.StringVar(value="Call")
        self.type_combo = ttk.Combobox(
            row2, textvariable=self.type_var,
            values=["Call", "Put", "Call Spread", "Put Spread"],
            width=12, state="readonly",
        )
        self.type_combo.pack(side="left", padx=4)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_changed)

        # Direction label + combo. Packed/forgotten by _refresh_type_visibility
        # so it disappears entirely when a spread is selected.
        self.direction_label = ttk.Label(row2, text="Direction:")
        self.direction_label.pack(side="left", padx=(12, 0))
        self.direction_var = tk.StringVar(value="Buy")
        self.direction_combo = ttk.Combobox(row2, textvariable=self.direction_var,
                                             values=["Buy", "Sell"],
                                             width=6, state="readonly")
        self.direction_combo.pack(side="left", padx=4)
        self.direction_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_position_inputs_changed())

        self.contract_label = ttk.Label(row2, text="Contract:")
        self.contract_label.pack(side="left", padx=(12, 0))
        self.contract_var = tk.StringVar(value="")
        self.contract_combo = ttk.Combobox(row2, textvariable=self.contract_var,
                                            width=26, state="readonly")
        self.contract_combo.pack(side="left", padx=4)
        self.contract_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_position_inputs_changed())

        # Short-leg picker — only visible when Type is a Spread
        self.short_leg_label = ttk.Label(row2, text="Short Strike:")
        self.short_leg_var = tk.StringVar(value="")
        self.short_leg_combo = ttk.Combobox(row2, textvariable=self.short_leg_var,
                                             width=26, state="readonly")
        self.short_leg_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_position_inputs_changed())
        # initially hidden — packed in _refresh_type_visibility

    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(side="top", fill="both", expand=True)
        self.tab_replay = ttk.Frame(self.nb)
        self.tab_whatif = ttk.Frame(self.nb)
        self.tab_ivshock = ttk.Frame(self.nb)
        self.nb.add(self.tab_replay, text="Replay")
        self.nb.add(self.tab_whatif, text="What-if")
        self.nb.add(self.tab_ivshock, text="IV shock")
        self._build_replay_tab()
        self._build_whatif_tab()
        self._build_ivshock_tab()

    def _build_status_row(self):
        self.bottom_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.bottom_var, padding=4).pack(side="bottom", fill="x")

    # ---- Refresh & contract selection ----

    def _on_refresh(self):
        if self.client is None:
            messagebox.showwarning("Simulator", "Not connected to Schwab API.")
            return
        symbol = self.symbol_var.get().strip().upper()
        if not symbol:
            messagebox.showinfo("Simulator", "Enter a symbol first.")
            return
        self.status_var.set(f"Fetching {symbol}…")
        # Always pull the multi-expiry window; we populate the Expiry picker
        # from whatever Schwab returns.
        threading.Thread(
            target=self._fetch_worker, args=(symbol,), daemon=True
        ).start()

    def _fetch_worker(self, symbol: str):
        try:
            snap = fetch_snapshot(self.client, symbol)
            self.after(0, self._on_snapshot_loaded, snap)
        except Exception as e:
            log.exception("fetch_snapshot failed")
            err = str(e)
            self.after(0, lambda: self.status_var.set(f"Fetch failed: {err}"))

    def _on_snapshot_loaded(self, snap: ChainSnapshot):
        self.snapshot = snap
        if not snap.contracts:
            self.status_var.set("Loaded — but Schwab returned no contracts.")
            self.expiry_combo["values"] = []
            self.contract_combo["values"] = []
            self.short_leg_combo["values"] = []
            self.selected_position = None
            self._render_all_tabs()
            return

        expiries = sorted({c.expiry for c in snap.contracts})
        self.expiry_combo["values"] = [d.isoformat() for d in expiries]
        from datetime import date as _date
        today = _date.today()
        future = [d for d in expiries if d >= today]
        default_expiry = future[0] if future else expiries[0]
        self.expiry_combo.set(default_expiry.isoformat())
        self.status_var.set(
            f"Loaded {len(snap.contracts)} contracts across {len(expiries)} expiries."
        )
        self.bottom_var.set(f"Data as of {snap.as_of:%H:%M:%S} · spot=${snap.spot:.2f}")
        self._refresh_type_visibility()
        self._populate_contracts_for_expiry(default_expiry)

    def _on_expiry_changed(self, _evt=None):
        if not self.snapshot:
            return
        try:
            picked = datetime.strptime(self.expiry_var.get(), "%Y-%m-%d").date()
        except ValueError:
            return
        self._populate_contracts_for_expiry(picked)

    def _on_type_changed(self, _evt=None):
        self._refresh_type_visibility()
        if not self.snapshot:
            return
        try:
            picked = datetime.strptime(self.expiry_var.get(), "%Y-%m-%d").date()
        except ValueError:
            return
        self._populate_contracts_for_expiry(picked)

    def _refresh_type_visibility(self):
        """Show/hide Direction and Short-leg widgets based on Type selection.

        Spreads hide Direction entirely (leg signs are implied) and show the
        Short-leg picker. Single-leg types do the opposite.
        """
        is_spread = self._is_spread()
        if is_spread:
            # Hide Direction label + combo
            if self.direction_label.winfo_ismapped():
                self.direction_label.pack_forget()
                self.direction_combo.pack_forget()
            # Rename Contract → Long Strike so the user knows which leg this is
            self.contract_label.config(text="Long Strike:")
            # Show Short-leg picker
            if not self.short_leg_label.winfo_ismapped():
                self.short_leg_label.pack(side="left", padx=(12, 0))
                self.short_leg_combo.pack(side="left", padx=4)
            self.short_leg_combo.configure(state="readonly")
        else:
            # Show Direction label + combo (re-packed just before the Contract
            # label so order stays: Type · Direction · Contract).
            if not self.direction_label.winfo_ismapped():
                self.direction_label.pack(side="left", padx=(12, 0),
                                          before=self.contract_label)
                self.direction_combo.pack(side="left", padx=4,
                                          before=self.contract_label)
            self.direction_combo.configure(state="readonly")
            # Restore single-leg label
            self.contract_label.config(text="Contract:")
            # Hide Short-leg picker
            if self.short_leg_label.winfo_ismapped():
                self.short_leg_label.pack_forget()
                self.short_leg_combo.pack_forget()

    def _is_spread(self) -> bool:
        return "Spread" in self.type_var.get()

    def _kind_for_type(self) -> str:
        """Map UI Type selection → contract kind ('call' | 'put')."""
        t = self.type_var.get()
        if t in ("Put", "Put Spread"):
            return "put"
        return "call"

    def _populate_contracts_for_expiry(self, picked_expiry: date):
        assert self.snapshot is not None
        kind = self._kind_for_type()
        items = sorted(
            (c for c in self.snapshot.contracts
             if c.expiry == picked_expiry and c.kind == kind),
            key=lambda c: c.strike,
        )
        self._contract_index = items
        self._short_leg_index = items  # same pool — short-leg picker just filters out the long-leg strike
        self.contract_combo["values"] = [self._contract_label(c) for c in items]
        self.short_leg_combo["values"] = [self._contract_label(c) for c in items]

        if items:
            atm = min(items, key=lambda c: abs(c.strike - self.snapshot.spot))
            atm_idx = items.index(atm)
            self.contract_combo.current(atm_idx)
            # Default short-leg to one strike OTM relative to long
            if self._is_spread():
                # for a call spread, short the next-higher strike; for a put spread, the next-lower
                if kind == "call":
                    short_default = items[min(atm_idx + 1, len(items) - 1)]
                else:
                    short_default = items[max(atm_idx - 1, 0)]
                self.short_leg_combo.current(items.index(short_default))
        else:
            self.contract_combo.set("")
            self.short_leg_combo.set("")

        self._rebuild_position_from_inputs()

    def _contract_label(self, c: ContractRow) -> str:
        return (f"{c.strike:.2f} {c.kind.upper()}  iv={c.iv:.2%}"
                + ("  (no IV)" if c.iv <= 0 else ""))

    def _on_position_inputs_changed(self):
        """Called whenever Direction, Contract, or Short-leg picker changes."""
        self._rebuild_position_from_inputs()

    def _rebuild_position_from_inputs(self):
        """Reads Type / Direction / Contract / Short-leg and constructs
        self.selected_position. Then re-renders all tabs."""
        if not self.snapshot:
            return
        idx = self.contract_combo.current()
        if not (0 <= idx < len(self._contract_index)):
            self.selected_position = None
            self.bottom_var.set("Pick a contract.")
            self._render_all_tabs()
            return
        long_leg = self._contract_index[idx]
        symbol = self.snapshot.symbol

        if self._is_spread():
            sidx = self.short_leg_combo.current()
            if not (0 <= sidx < len(self._short_leg_index)) or sidx == idx:
                self.selected_position = None
                self.bottom_var.set("Pick distinct long and short strikes for the spread.")
                self._render_all_tabs()
                return
            short_leg = self._short_leg_index[sidx]
            try:
                self.selected_position = Position.vertical(long_leg, short_leg, symbol=symbol)
            except ValueError as e:
                self.selected_position = None
                self.bottom_var.set(f"Spread error: {e}")
                self._render_all_tabs()
                return
        else:
            direction = self.direction_var.get()  # "Buy" or "Sell"
            self.selected_position = Position.single(long_leg, direction=direction, symbol=symbol)

        self.bottom_var.set(
            f"Position: {self.selected_position.label}  ·  "
            f"spot=${self.snapshot.spot:.2f}  ·  as of {self.snapshot.as_of:%H:%M:%S}"
        )
        self._render_all_tabs()

    # ---- Render hooks (filled in by Tasks 10/11/12) ----

    def _build_replay_tab(self):
        self.replay_fig = Figure(figsize=(8, 6), tight_layout=True, facecolor=self._chrome["bg"])
        self.replay_ax_price = self.replay_fig.add_subplot(111)
        self.replay_ax_price.text(0.5, 0.5, "Load a chain to see the replay panel",
                                   ha="center", va="center",
                                   transform=self.replay_ax_price.transAxes)
        self.replay_ax_greeks = []
        self.replay_canvas = FigureCanvasTkAgg(self.replay_fig, master=self.tab_replay)
        self.replay_canvas.get_tk_widget().pack(fill="both", expand=True)

        # Slider scrubs a vertical cursor through every subplot. Label below
        # the slider shows the timestamp + values at the cursor.
        slider_frame = ttk.Frame(self.tab_replay)
        slider_frame.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Label(slider_frame, text="Scrub:").pack(side="left")
        self.replay_slider_var = tk.DoubleVar(value=0.0)
        self.replay_slider = ttk.Scale(slider_frame, from_=0, to=100,
                                       variable=self.replay_slider_var,
                                       command=self._on_replay_slider,
                                       orient="horizontal")
        self.replay_slider.pack(side="left", fill="x", expand=True, padx=8)
        self.replay_readout = ttk.Label(self.tab_replay, text="",
                                         font=("Consolas", 9))
        self.replay_readout.pack(side="top", fill="x", padx=8)

        self._replay_trace = None
        self._replay_cursors = []   # Line2D handles, one per subplot

    def _render_replay_tab(self):
        if not self.snapshot or not self.selected_position:
            return
        pos = self.selected_position
        if any(leg.contract.iv <= 0 for leg in pos.legs):
            self.replay_fig.clf()
            ax = self.replay_fig.add_subplot(111)
            ax.text(0.5, 0.5, "IV unavailable - cannot simulate",
                    ha="center", va="center", transform=ax.transAxes)
            self.replay_ax_price = ax
            self.replay_ax_greeks = []
            theme.apply_matplotlib(self.replay_fig, [ax])
            self.replay_canvas.draw_idle()
            return
        eng = ReplayEngine(self.snapshot)
        trace = aggregate_position(pos, lambda c: eng.full_trace(c))

        # Rebuild the figure layout: top row price, 5 stacked Greek subplots below.
        self.replay_fig.clf()
        if trace.empty:
            ax = self.replay_fig.add_subplot(111)
            ax.text(0.5, 0.5, "Replay unavailable - no price history",
                    ha="center", va="center", transform=ax.transAxes)
            self.replay_ax_price = ax
            self.replay_ax_greeks = []
            theme.apply_matplotlib(self.replay_fig, [ax])
            self.replay_canvas.draw_idle()
            self._replay_trace = None
            return

        gs = self.replay_fig.add_gridspec(6, 1, height_ratios=[2, 1, 1, 1, 1, 1])
        self.replay_ax_price = self.replay_fig.add_subplot(gs[0])
        hist = self.snapshot.price_history

        # Compress overnight / weekend gaps: plot on an integer x-axis where
        # each consecutive bar gets the next index regardless of clock time.
        # A "gap" is any inter-bar interval bigger than ~3× the typical bar
        # spacing (catches overnight breaks for both 1-min and 5-min data).
        if len(hist) >= 2:
            deltas = (hist.index[1:] - hist.index[:-1]).total_seconds()
            median_delta_s = float(np.median(deltas))
            gap_threshold_s = max(median_delta_s * 3, 60 * 60)  # at least 1h to count as a gap
            gap_indices = [i + 1 for i, d in enumerate(deltas) if d > gap_threshold_s]
        else:
            median_delta_s = 0.0
            gap_indices = []
        x_idx = np.arange(len(hist))

        self.replay_ax_price.plot(x_idx, hist.values, lw=1)

        # Title reflects resolution + session count.
        sessions = len(gap_indices) + 1 if len(hist) else 0
        if len(hist) >= 2:
            if median_delta_s < 120:
                resolution = f"{len(hist)} bars, 1-min × {sessions} sessions"
            elif median_delta_s < 3600:
                resolution = (f"{len(hist)} bars, {int(round(median_delta_s/60))}-min "
                              f"× {sessions} sessions")
            else:
                span_days = (hist.index[-1] - hist.index[0]).days or 1
                resolution = f"{len(hist)} bars, ~{span_days}d daily"
        else:
            resolution = f"{len(hist)} bar"
        self.replay_ax_price.set_title(f"Underlying ({resolution})")

        labels = ["Delta", "Gamma", "Theta", "Vega", "Rho"]
        cols   = ["delta", "gamma", "theta", "vega", "rho"]
        self.replay_ax_greeks = []
        for i, (lbl, col) in enumerate(zip(labels, cols)):
            ax = self.replay_fig.add_subplot(gs[i+1], sharex=self.replay_ax_price)
            ax.plot(x_idx, trace[col].values, lw=1)
            ax.set_ylabel(lbl, fontsize=8)
            self.replay_ax_greeks.append(ax)

        # Dashed session boundaries on every subplot.
        all_axes = [self.replay_ax_price] + self.replay_ax_greeks
        for gap_i in gap_indices:
            # Boundary sits between bar (gap_i - 1) and bar gap_i.
            for ax in all_axes:
                ax.axvline(gap_i - 0.5, color="gray", lw=0.5, ls=":")

        # Date label centered on each session, just under the top of the price plot.
        session_starts = [0] + gap_indices
        session_ends = gap_indices + [len(hist)]
        for s, e in zip(session_starts, session_ends):
            if e <= s:
                continue
            mid = (s + e) / 2.0
            label_date = hist.index[s].strftime("%Y-%m-%d")
            self.replay_ax_price.text(
                mid, 0.97, label_date,
                transform=self.replay_ax_price.get_xaxis_transform(),
                ha="center", va="top", fontsize=7, color="gray",
                clip_on=True,
            )

        # X-tick labels on the bottom-most subplot: show HH:MM at a handful
        # of bars to give the user a sense of time-of-day.
        bottom_ax = self.replay_ax_greeks[-1] if self.replay_ax_greeks else self.replay_ax_price
        if len(hist) >= 4:
            tick_count = min(8, len(hist))
            tick_positions = np.linspace(0, len(hist) - 1, tick_count).astype(int)
            tick_labels = [hist.index[i].strftime("%H:%M") for i in tick_positions]
            bottom_ax.set_xticks(tick_positions)
            bottom_ax.set_xticklabels(tick_labels, fontsize=7)
        # Hide tick labels on all non-bottom subplots so the layout stays clean.
        for ax in all_axes[:-1]:
            ax.tick_params(labelbottom=False)

        # Install vertical cursors + per-subplot marker dots + value annotations.
        # The markers + annotations match the What-if / IV-shock style: a small
        # dot at the line/cursor intersection with the value labeled beside it.
        self._replay_cursors = []
        self._replay_markers = []
        self._replay_annotations = []
        # Series sources for each subplot, in the same order as all_axes
        self._replay_value_series = [hist.values] + [trace[col].values for col in cols]
        # Per-subplot value formatters (price gets 2dp, Greeks get +0.0000)
        self._replay_value_fmt = [
            lambda v: f"{v:.2f}",
            lambda v: f"{v:+.4f}",  # delta
            lambda v: f"{v:+.4f}",  # gamma
            lambda v: f"{v:+.4f}",  # theta
            lambda v: f"{v:+.4f}",  # vega
            lambda v: f"{v:+.4f}",  # rho
        ]
        last_idx = len(trace) - 1
        for ax, values, fmt in zip(all_axes, self._replay_value_series, self._replay_value_fmt):
            cursor = ax.axvline(last_idx, color="tab:red", lw=0.8, alpha=0.7, zorder=4)
            y0 = float(values[last_idx])
            marker, = ax.plot([last_idx], [y0], marker="o", ms=3.5,
                              color="tab:red", zorder=5)
            # xytext in offset-points keeps the label a constant pixel distance
            # from the dot regardless of data scale. _update_replay_cursor flips
            # the offset to the left when the dot is in the right portion of
            # the chart so the label never escapes the axes.
            ann = ax.annotate(
                fmt(y0),
                xy=(last_idx, y0),
                xytext=(6, 4), textcoords="offset points",
                ha="left", va="bottom",
                fontsize=7, color="tab:red", zorder=5,
                annotation_clip=False,
            )
            self._replay_cursors.append(cursor)
            self._replay_markers.append(marker)
            self._replay_annotations.append(ann)

        self.replay_slider.config(from_=0, to=max(len(trace) - 1, 1))
        self.replay_slider_var.set(last_idx)
        self._update_replay_cursor(last_idx)
        theme.apply_matplotlib(self.replay_fig, all_axes)
        self.replay_canvas.draw_idle()
        self._replay_trace = trace

    def _on_replay_slider(self, val):
        try:
            idx = int(round(float(val)))
        except (TypeError, ValueError):
            return
        self._update_replay_cursor(idx)

    def _update_replay_cursor(self, idx: int):
        """Move every replay cursor + marker + annotation to x=idx and refresh
        the timestamp readout label."""
        if not self._replay_cursors or self._replay_trace is None:
            return
        trace = self._replay_trace
        if not (0 <= idx < len(trace)):
            return
        for cursor in self._replay_cursors:
            cursor.set_xdata([idx, idx])
        # If the cursor is past the right-half of the chart, draw the label
        # to the LEFT of the dot so it never spills off the axes.
        flip_left = idx > (len(trace) - 1) * 0.7
        for marker, ann, values, fmt in zip(
            self._replay_markers, self._replay_annotations,
            self._replay_value_series, self._replay_value_fmt,
        ):
            y = float(values[idx])
            marker.set_data([idx], [y])
            ann.xy = (idx, y)
            ann.set_text(fmt(y))
            if flip_left:
                ann.set_position((-6, 4))   # offset-points: left + above
                ann.set_ha("right")
            else:
                ann.set_position((6, 4))    # right + above
                ann.set_ha("left")
        # Timestamp-only readout — values are on the chart now
        ts = trace.index[idx]
        self.replay_readout.config(text=f"Cursor: {ts:%Y-%m-%d %H:%M}")
        self.replay_canvas.draw_idle()

    def _render_all_tabs(self):
        self._render_replay_tab()
        self._render_whatif_tab()
        self._render_ivshock_tab()

    def _build_whatif_tab(self):
        self.whatif_fig = Figure(figsize=(8, 6), tight_layout=True, facecolor=self._chrome["bg"])
        self.whatif_ax_curve = self.whatif_fig.add_subplot(111)
        self.whatif_ax_curve.text(0.5, 0.5, "Load a chain to see the what-if panel",
                                  ha="center", va="center",
                                  transform=self.whatif_ax_curve.transAxes)
        self.whatif_canvas = FigureCanvasTkAgg(self.whatif_fig, master=self.tab_whatif)
        self.whatif_canvas.get_tk_widget().pack(fill="both", expand=True)

        bar = ttk.Frame(self.tab_whatif)
        bar.pack(fill="x", padx=8, pady=4)
        ttk.Label(bar, text="ΔS%:").pack(side="left")
        self.whatif_dS_var = tk.DoubleVar(value=0.0)
        ttk.Scale(bar, from_=-20, to=20, variable=self.whatif_dS_var,
                  command=lambda _v: self._render_whatif_tab(),
                  orient="horizontal").pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(bar, text="Δt (days):").pack(side="left", padx=(12, 0))
        self.whatif_dt_var = tk.DoubleVar(value=0.0)
        ttk.Scale(bar, from_=0, to=30, variable=self.whatif_dt_var,
                  command=lambda _v: self._render_whatif_tab(),
                  orient="horizontal").pack(side="left", fill="x", expand=True, padx=4)

        self.whatif_readout = ttk.Label(self.tab_whatif, text="", font=("Consolas", 10))
        self.whatif_readout.pack(side="bottom", anchor="w", padx=8, pady=4)

    def _render_whatif_tab(self):
        if not self.snapshot or not self.selected_position:
            return
        pos = self.selected_position
        if any(leg.contract.iv <= 0 for leg in pos.legs):
            self.whatif_ax_curve.clear()
            self.whatif_ax_curve.text(0.5, 0.5, "IV unavailable - cannot simulate",
                                      ha="center", va="center",
                                      transform=self.whatif_ax_curve.transAxes)
            theme.apply_matplotlib(self.whatif_fig, [self.whatif_ax_curve])
            self.whatif_canvas.draw_idle()
            self.whatif_readout.config(text="IV unavailable")
            return
        primary = pos.primary
        expiry_dt = datetime.combine(primary.expiry, datetime.min.time()).replace(hour=15)
        base_t_days = max((expiry_dt - self.snapshot.as_of).total_seconds() / 86400.0, 0.01)
        forward_days = max(base_t_days - self.whatif_dt_var.get(), 0.01)

        s_range = np.linspace(self.snapshot.spot * 0.80, self.snapshot.spot * 1.20, 81)
        eng = WhatIfEngine(self.snapshot)
        df = aggregate_position(pos, lambda c: eng.sweep(c, s_range=s_range, t_days=forward_days))

        self.whatif_ax_curve.clear()
        self.whatif_ax_curve.plot(df["S"], df["theo_price"], lw=1.5)
        self.whatif_ax_curve.axhline(0, color="gray", lw=0.5, ls=":")  # P&L = 0 reference
        self.whatif_ax_curve.axvline(self.snapshot.spot, ls="--", lw=0.8, label="spot")
        target_s = self.snapshot.spot * (1 + self.whatif_dS_var.get() / 100.0)
        self.whatif_ax_curve.axvline(target_s, color="orange", lw=1, label="ΔS")
        self.whatif_ax_curve.set_xlabel("Underlying S")
        self.whatif_ax_curve.set_ylabel("Position value")
        self.whatif_ax_curve.set_title(pos.label, fontsize=9)
        self.whatif_ax_curve.legend(loc="upper left", fontsize=8)

        # Unobtrusive intersection annotations: value at spot, value at ΔS.
        s_vals = df["S"].values
        v_vals = df["theo_price"].values
        spot_idx = int(np.argmin(np.abs(s_vals - self.snapshot.spot)))
        target_idx = int(np.argmin(np.abs(s_vals - target_s)))
        for idx, x_val, color in ((spot_idx, self.snapshot.spot, "tab:blue"),
                                   (target_idx, target_s, "orange")):
            y_val = v_vals[idx]
            self.whatif_ax_curve.plot([x_val], [y_val], marker="o", ms=4,
                                       color=color, zorder=5)
            self.whatif_ax_curve.annotate(
                f"  {y_val:+.2f}",
                xy=(x_val, y_val), ha="left", va="bottom",
                fontsize=8, color=color, zorder=5,
            )
        theme.apply_matplotlib(self.whatif_fig, [self.whatif_ax_curve])
        self.whatif_canvas.draw_idle()

        # Readout: net Greeks at target S, summed across legs with their signs
        target_idx = int(np.argmin(np.abs(df["S"].values - target_s)))
        row = df.iloc[target_idx]
        self.whatif_readout.config(text=(
            f"S={target_s:.2f}  T={forward_days:.2f}d  "
            f"value={row['theo_price']:+.3f}  Δ={row['delta']:+.3f}  Γ={row['gamma']:+.4f}  "
            f"Θ={row['theta']:+.3f}  ν={row['vega']:+.3f}"
        ))

    def _build_ivshock_tab(self):
        self.ivshock_fig = Figure(figsize=(8, 6), tight_layout=True, facecolor=self._chrome["bg"])
        self.ivshock_ax_bars = self.ivshock_fig.add_subplot(111)
        self.ivshock_ax_bars.text(0.5, 0.5, "Load a chain to see the IV-shock panel",
                                   ha="center", va="center",
                                   transform=self.ivshock_ax_bars.transAxes)
        self.ivshock_canvas = FigureCanvasTkAgg(self.ivshock_fig, master=self.tab_ivshock)
        self.ivshock_canvas.get_tk_widget().pack(fill="both", expand=True)

        bar = ttk.Frame(self.tab_ivshock)
        bar.pack(fill="x", padx=8, pady=4)
        ttk.Label(bar, text="σ multiplier:").pack(side="left")
        self.ivshock_mult_var = tk.DoubleVar(value=1.0)
        ttk.Scale(bar, from_=0.5, to=3.0, variable=self.ivshock_mult_var,
                  command=lambda _v: self._render_ivshock_tab(),
                  orient="horizontal").pack(side="left", fill="x", expand=True, padx=4)

        self.ivshock_readout = ttk.Label(self.tab_ivshock, text="", font=("Consolas", 10))
        self.ivshock_readout.pack(side="bottom", anchor="w", padx=8, pady=4)

    def _render_ivshock_tab(self):
        if not self.snapshot or not self.selected_position:
            return
        pos = self.selected_position
        if any(leg.contract.iv <= 0 for leg in pos.legs):
            self.ivshock_ax_bars.clear()
            self.ivshock_ax_bars.text(0.5, 0.5, "IV unavailable - cannot simulate",
                                       ha="center", va="center",
                                       transform=self.ivshock_ax_bars.transAxes)
            theme.apply_matplotlib(self.ivshock_fig, [self.ivshock_ax_bars])
            self.ivshock_canvas.draw_idle()
            self.ivshock_readout.config(text="IV unavailable")
            return
        mult = float(self.ivshock_mult_var.get())
        eng = IVShockEngine(self.snapshot)
        df = aggregate_position(pos, lambda c: eng.sweep(c, multipliers=[1.0, mult]))
        base = df.iloc[0]
        shock = df.iloc[1]

        self.ivshock_ax_bars.clear()
        labels = ["Price", "Delta", "Gamma×100", "Theta", "Vega"]
        base_vals  = [base["theo_price"],  base["delta"],  base["gamma"] * 100,
                      base["theta"], base["vega"]]
        shock_vals = [shock["theo_price"], shock["delta"], shock["gamma"] * 100,
                      shock["theta"], shock["vega"]]
        x = np.arange(len(labels))
        w = 0.35
        bars_base  = self.ivshock_ax_bars.bar(x - w / 2, base_vals,  w,
                                               label=f"σ×1.0 ({base['sigma']:.2%})")
        bars_shock = self.ivshock_ax_bars.bar(x + w / 2, shock_vals, w,
                                               label=f"σ×{mult:.2f} ({shock['sigma']:.2%})")
        self.ivshock_ax_bars.set_xticks(x)
        self.ivshock_ax_bars.set_xticklabels(labels)
        self.ivshock_ax_bars.axhline(0, color="gray", lw=0.5, ls=":")
        self.ivshock_ax_bars.legend(fontsize=8)

        # Value labels: above positive bars, below negative bars
        def _annotate(bars, values):
            ymin, ymax = self.ivshock_ax_bars.get_ylim()
            offset = (ymax - ymin) * 0.015
            for rect, val in zip(bars, values):
                if val >= 0:
                    y = rect.get_height() + offset
                    va = "bottom"
                else:
                    y = rect.get_height() - offset
                    va = "top"
                self.ivshock_ax_bars.annotate(
                    f"{val:+.3f}" if abs(val) < 10 else f"{val:+.2f}",
                    xy=(rect.get_x() + rect.get_width() / 2, y),
                    ha="center", va=va, fontsize=8,
                )
        _annotate(bars_base, base_vals)
        _annotate(bars_shock, shock_vals)
        theme.apply_matplotlib(self.ivshock_fig, [self.ivshock_ax_bars])
        self.ivshock_canvas.draw_idle()

        price_chg = shock["theo_price"] - base["theo_price"]
        self.ivshock_readout.config(text=(
            f"σ×{mult:.2f}  Δprice={price_chg:+.3f}  "
            f"Δvega={shock['vega'] - base['vega']:+.4f}"
        ))

    # ---- Ask AI ----

    def _on_ask_ai(self):
        if self.snapshot is None or self.selected_position is None:
            messagebox.showinfo("Ask AI", "Load a chain and pick a position first.")
            return
        mode = ["replay", "whatif", "ivshock"][self.nb.index(self.nb.select())]
        prompt = build_simulator_prompt(
            self.snapshot, self.selected_position, mode_state={"mode": mode}
        )
        self.clipboard_clear()
        self.clipboard_append(prompt)
        self._show_prompt_popup(prompt)

    def _show_prompt_popup(self, prompt: str):
        top = tk.Toplevel(self)
        top.title("Ask AI — prompt copied")
        top.geometry("720x520")
        top.configure(bg=self._chrome["bg"])
        txt = tk.Text(
            top, wrap="word",
            bg=self._chrome["bg_input"], fg=self._chrome["fg"],
            insertbackground=self._chrome["fg"],
        )
        txt.insert("1.0", prompt)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(
            top, text="Copy again",
            command=lambda: (self.clipboard_clear(), self.clipboard_append(prompt))
        ).pack(side="bottom", pady=6)
