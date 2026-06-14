"""Shared, persistent Trade detail panel.

One panel, reused by every signal table (scanner, captured, paper, swing). Each
table synthesizes a signal-like dict and calls ``handle.update(signal)``; the
panel rebuilds with a speedometer header, 2x2 tiles, and five cards (Trade Info,
Greeks, Composite Score factor bars, IV Analysis, Expected Move). Graphics come
from ``svg.py``. Robust to missing keys (fields vary by trade type / source).
"""
from nicegui import ui

from . import svg

GREEN = "#66bb6a"
AMBER = "#ffa726"
RED = "#ef5350"
NEUTRAL = "#bdbdbd"

FACTOR_LABELS = [
    ("rr", "R:R"), ("pop", "PoP"), ("theta", "Theta"), ("iv", "IV Rank"),
    ("iv_hv", "IV/HV"), ("vega", "Vega Risk"), ("em", "EM Buffer"),
    ("liq", "Liquidity"), ("trend", "Trend"), ("gex", "GEX"), ("dex", "DEX"),
]

_PLACEHOLDER = "Select a signal to view details…"


def pop_color(pop):
    try:
        p = float(pop)
    except (TypeError, ValueError):
        return NEUTRAL
    if p >= 70:
        return GREEN
    if p >= 50:
        return AMBER
    return RED


def factor_rows(factor_scores, trade_type):
    """[(label, value), ...] for the Composite Score card."""
    fs = factor_scores or {}
    if trade_type == "IC":
        return [
            ("Put leg", fs.get("pcs_leg", 0) or 0),
            ("Call leg", fs.get("ccs_leg", 0) or 0),
            ("Delta bonus", fs.get("delta_bonus", 0) or 0),
        ]
    return [(label, fs.get(key, 0) or 0) for key, label in FACTOR_LABELS]


def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _pct(v):
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"


def _tile(label, value, color):
    with ui.card().classes("p-2 min-w-[92px]"):
        ui.label(label).classes("text-xs opacity-60")
        ui.label(value).classes("text-base font-bold").style(f"color:{color}")


def _kv(label, value, color=None):
    with ui.row().classes("justify-between w-full"):
        ui.label(label).classes("opacity-70 text-sm")
        lbl = ui.label(value).classes("text-sm")
        if color:
            lbl.style(f"color:{color}")


def _strikes_text(s):
    if s.get("type") == "IC":
        return (f"P {s.get('short_strike','?')}/{s.get('long_strike','?')}  "
                f"C {s.get('call_short','?')}/{s.get('call_long','?')}")
    sk, lk, w = s.get("short_strike"), s.get("long_strike"), s.get("width")
    if sk is None:
        return "—"
    wtxt = f" ({w}-wide)" if isinstance(w, (int, float)) else ""
    return f"${sk} - ${lk}{wtxt}"


def _build(s):
    title = " · ".join(x for x in (s.get("symbol", ""), s.get("type", ""),
                                   s.get("trade_type", "")) if x)
    ui.label(title or "Signal").classes("text-subtitle1 font-bold")

    with ui.row().classes("items-center gap-3 w-full no-wrap"):
        ui.html(svg.speedometer_svg(s.get("composite_score") or 0, s.get("grade", "")))
        with ui.grid(columns=2).classes("gap-2"):
            _tile("Credit", _money(s.get("credit")), GREEN)
            _tile("PoP", _pct(s.get("pop_pct")), pop_color(s.get("pop_pct")))
            _tile("Breakeven", _money(s.get("breakeven")), NEUTRAL)
            dte = s.get("dte")
            _tile("DTE", f"{dte} days" if dte is not None else "—", NEUTRAL)

    # Card 1 — Trade Info
    with ui.expansion("Trade Info", value=True).classes("w-full"):
        _kv("Expiration", f"{s.get('expiration','—')} ({s.get('dte','?')} DTE)")
        if isinstance(s.get("underlying_price"), (int, float)):
            _kv("Underlying", _money(s.get("underlying_price")))
        _kv("Strikes", _strikes_text(s))
        _kv("Max Loss", _money(s.get("max_loss")), RED)
        if s.get("max_contracts") is not None:
            _kv("Max Contracts", str(s.get("max_contracts")))
        if isinstance(s.get("expected_pnl_10"), (int, float)):
            v = s["expected_pnl_10"]
            _kv("E[P&L] (10ct)", f"${v:+,.0f}", GREEN if v >= 0 else RED)
        if s.get("rr_pct") is not None:
            _kv("R:R Ratio", _pct(s.get("rr_pct")))

    # Card 2 — Greeks
    with ui.expansion("Greeks", value=True).classes("w-full"):
        with ui.grid(columns=4).classes("gap-2 w-full"):
            _greek("Δ", s.get("short_delta"), fmt="{:+.4f}")
            theta = s.get("net_theta")
            _greek("Θ", theta, color=(GREEN if isinstance(theta, (int, float)) and theta > 0 else RED))
            _greek("Vega", s.get("net_vega"), fmt="{:+.3f}")
            _greek("IV", s.get("short_iv"), fmt="{:.1f}%")

    # Card 3 — Composite Score (factor bars)
    with ui.expansion(f"Composite Score: {s.get('composite_score','?')} "
                      f"({s.get('grade','?')})").classes("w-full"):
        for label, val in factor_rows(s.get("factor_scores"), s.get("type")):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(label).classes("text-xs w-20 opacity-80")
                ui.html(svg.gradient_bar_svg(val))
                ui.label(f"{val:g}").classes("text-xs w-8 text-right")

    # Card 4 — IV Analysis (best-effort from available keys)
    if any(s.get(k) is not None for k in ("current_iv", "iv_rank", "iv_percentile", "short_iv")):
        with ui.expansion("IV Analysis").classes("w-full"):
            _kv("ATM IV", _pct(s.get("current_iv") if s.get("current_iv") is not None else s.get("short_iv")))
            if isinstance(s.get("iv_low_52w"), (int, float)) and isinstance(s.get("iv_high_52w"), (int, float)):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("52w").classes("text-xs w-12 opacity-80")
                    ui.html(svg.range_marker_svg(s["iv_low_52w"], s["iv_high_52w"],
                                                 s.get("current_iv") or s["iv_low_52w"]))
            if s.get("iv_rank") is not None:
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("Rank").classes("text-xs w-12 opacity-80")
                    ui.html(svg.gradient_bar_svg(s["iv_rank"]))
                    ui.label(f"{s['iv_rank']:g}").classes("text-xs w-8 text-right")

    # Card 5 — Expected Move (best-effort)
    em = s.get("expected_moves")
    if isinstance(em, dict):
        with ui.expansion("Expected Move").classes("w-full"):
            for key, label in (("daily", "1-day"), ("weekly", "1-week"), ("monthly", "30-day")):
                blk = em.get(key) or {}
                d = blk.get("move_dollars")
                p = blk.get("move_percent", blk.get("move_pct"))
                if isinstance(d, (int, float)):
                    pct = f" ({p:.2f}%)" if isinstance(p, (int, float)) else ""
                    _kv(label, f"±${d:,.2f}{pct}")


def _greek(label, value, fmt="{:.3f}", color=None):
    with ui.card().classes("p-2 items-center"):
        ui.label(label).classes("text-xs opacity-60")
        txt = fmt.format(value) if isinstance(value, (int, float)) else "—"
        lbl = ui.label(txt).classes("text-sm font-bold")
        if color and isinstance(value, (int, float)):
            lbl.style(f"color:{color}")


class _Handle:
    def __init__(self, container):
        self._container = container

    def clear(self):
        self._container.clear()
        with self._container:
            ui.label(_PLACEHOLDER).classes("opacity-60")

    def update(self, signal):
        self._container.clear()
        if not signal:
            self.clear()
            return
        with self._container:
            _build(signal)


def render():
    """Build the (empty) detail panel; returns a handle with update()/clear()."""
    ui.label("Trade detail").classes("text-subtitle1 font-bold")
    container = ui.column().classes("w-full gap-2")
    with container:
        ui.label(_PLACEHOLDER).classes("opacity-60")
    return _Handle(container)
