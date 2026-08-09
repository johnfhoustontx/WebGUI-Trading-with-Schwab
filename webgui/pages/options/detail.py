"""Shared, persistent Trade detail panel.

One panel, reused by every signal table (scanner, captured, paper, swing). Each
table synthesizes a signal-like dict and calls ``handle.update(signal)``. The
**header** (signal title + composite-score speedometer + 2x2 tiles) is built ONCE
in ``render()`` and updated in place; the five cards (Trade Info, Greeks, Composite
Score factor bars, IV Analysis, Expected Move) rebuild per selection. The
speedometer is the shared Highcharts angular gauge (``gauge.py`` — painted
rainbow face + needle); the factor/IV bars + range markers are SVG (``svg.py``).
Robust to missing keys (fields vary by trade type / source).

The gauge is persistent (not recreated per selection) so the Highcharts ESM is
registered at initial page render — a gauge added only on selection, on a page
with no other chart at load, fails with "Failed to resolve module specifier".
"""
from nicegui import ui

from ..gauge import gauge_figure
from . import svg
from .theme import (TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL, STATE_TEXT_CLASSES,
                    TILE_3D, CARD)

# Semantic state-color class tokens (Tailwind text-[...] arbitrary values). Names
# kept (many refs) but the VALUES are now class strings applied via .classes().
GREEN, AMBER, RED, NEUTRAL = TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL


def _set_color(lbl, cls):
    """Reactively swap a label's state-color class. .classes() ACCUMULATES, so we
    must remove the whole finite state set before adding, or repeated repaints
    stack conflicting text-[...] classes (equal specificity → unpredictable)."""
    lbl.classes(remove=STATE_TEXT_CLASSES, add=cls)

FACTOR_LABELS = [
    ("rr", "R:R"), ("pop", "PoP"), ("theta", "Theta"), ("iv", "IV Rank"),
    ("iv_hv", "IV/HV"), ("vega", "Vega Risk"), ("em", "EM Buffer"),
    ("liq", "Liquidity"), ("trend", "Trend"), ("gex", "GEX"), ("dex", "DEX"),
]

_PLACEHOLDER = "Select a signal to view details…"

# 2x2 header tiles: (key, label, value-fn, color-fn).
_TILES = [
    ("credit", "Credit", lambda s: _money(s.get("credit")), lambda s: GREEN),
    ("pop", "PoP", lambda s: _pct(s.get("pop_pct")), lambda s: pop_color(s.get("pop_pct"))),
    ("breakeven", "Breakeven", lambda s: _money(s.get("breakeven")), lambda s: NEUTRAL),
    ("dte", "DTE", lambda s: (f"{s.get('dte')} days" if s.get("dte") is not None else "—"),
     lambda s: NEUTRAL),
]


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


def factor_rows(factor_scores, trade_type, unavailable=None):
    """[(label, value, known), ...] for the Score factors card.

    ``known`` is False when the factor was never measured. This matters because
    scoring.py collapses "unavailable" into a real-looking number in BOTH
    directions -- 0.0 for rr/pop/theta and 50.0 for the other eight -- and the
    panel previously drew both as confident bars. A calm mid-grey 50 could mean
    "never measured", which for triage is false reassurance.

    Absence from the dict is proof. ``unavailable`` is the additive
    ``factors_unavailable`` list from Tier 2 when present -- exact rather than
    inferred. A value that IS present and is not listed is treated as real,
    including a genuine 0 (a real wide-spread liquidity reading).
    """
    fs = factor_scores or {}
    missing = set(unavailable or ())
    if trade_type == "IC":
        keys = [("pcs_leg", "Put leg"), ("ccs_leg", "Call leg"),
                ("delta_bonus", "Delta bonus")]
    else:
        keys = FACTOR_LABELS
    rows = []
    for key, label in keys:
        raw = fs.get(key)
        known = raw is not None and key not in missing
        rows.append((label, raw if known else None, known))
    return rows


def factor_value_text(value, known):
    """Numeric text for a factor bar, or an em-dash when it was never measured."""
    if not known or not isinstance(value, (int, float)):
        return "—"
    return f"{value:g}"


def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _pct(v):
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"


# Every adapter emits PER-SHARE dollars; the panel displays per-contract. One
# option contract covers 100 shares. Keeping the conversion in exactly one place
# is the fix for the unit collision documented in the design doc -- the paper
# adapter previously mixed per-share credit with whole-position max loss in the
# same row.
CONTRACT_MULTIPLIER = 100


def per_contract(per_share):
    """Per-share dollars -> per-contract dollars. None for anything non-numeric."""
    if isinstance(per_share, bool) or not isinstance(per_share, (int, float)):
        return None
    return float(per_share) * CONTRACT_MULTIPLIER


def money_per_contract(per_share):
    """Formatted per-contract dollars carrying an explicit unit, or an em-dash."""
    v = per_contract(per_share)
    return "—" if v is None else f"${v:,.2f} per contract"


def breakevens(raw):
    """Normalize a breakeven field to a list of floats.

    Iron condors store TWO breakevens as the string "put_be/call_be"
    (scanner_engine.py:1069). The old code formatted with ``_money``, which
    requires a number, so every IC rendered an em-dash. Credit spreads store a
    plain float. Both shapes are handled; anything unparseable yields [].
    """
    if raw is None:
        return []
    if isinstance(raw, bool):
        return []
    if isinstance(raw, (int, float)):
        return [float(raw)]
    out = []
    for part in str(raw).split("/"):
        try:
            out.append(float(part.strip()))
        except (TypeError, ValueError):
            return []
    return out


def breakeven_text(raw):
    """Display string for one or two breakevens, or an em-dash when absent."""
    vals = breakevens(raw)
    if not vals:
        return "—"
    return " / ".join(f"${v:,.2f}" for v in vals)


def _signal_title(s):
    return " · ".join(x for x in (s.get("symbol", ""), s.get("type", ""),
                                  s.get("trade_type", "")) if x) or "Signal"


def _tile_slot(label):
    """A header tile (label + value); returns the value label to update in place.

    ``min-w-0 w-full`` — the tile fills its grid cell and may SHRINK below its
    content's natural width, so the 2×2 grid always fits the panel (at 290px the
    old ``min-w-[92px]`` tiles + the gauge overflowed the panel edge)."""
    with ui.card().classes(f"p-2 min-w-0 w-full {TILE_3D}"):
        ui.label(label).classes("text-xs opacity-60")
        return ui.label("—").classes("text-base font-bold")


def _kv(label, value, color=None):
    with ui.row().classes("justify-between w-full"):
        ui.label(label).classes("opacity-70 text-sm")
        lbl = ui.label(value).classes("text-sm")
        if color:
            lbl.classes(add=color)


def _strikes_text(s):
    if s.get("type") == "IC":
        return (f"P {s.get('short_strike','?')}/{s.get('long_strike','?')}  "
                f"C {s.get('call_short','?')}/{s.get('call_long','?')}")
    sk, lk, w = s.get("short_strike"), s.get("long_strike"), s.get("width")
    if sk is None:
        return "—"
    wtxt = f" ({w}-wide)" if isinstance(w, (int, float)) else ""
    return f"${sk} - ${lk}{wtxt}"


def _build_cards(s):
    """Build the five expansion cards (run inside the cleared body container)."""
    # Card 1 — Trade Info
    with ui.expansion("Trade Info", value=True).classes("w-full"):
        _kv("Expiration", f"{s.get('expiration','—')} ({s.get('dte','?')} DTE)")
        if isinstance(s.get("underlying_price"), (int, float)):
            _kv("Current price", _money(s.get("underlying_price")))
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
        for label, val, known in factor_rows(s.get("factor_scores"), s.get("type"),
                                             s.get("factors_unavailable")):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(label).classes("text-xs w-20 opacity-80")
                ui.html(svg.gradient_bar_svg(val if known else 0))
                ui.label(factor_value_text(val, known)).classes("text-xs w-8 text-right")

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
    with ui.card().classes(f"p-2 items-center {TILE_3D}"):
        ui.label(label).classes("text-xs opacity-60")
        txt = fmt.format(value) if isinstance(value, (int, float)) else "—"
        lbl = ui.label(txt).classes("text-sm font-bold")
        if color and isinstance(value, (int, float)):
            lbl.classes(add=color)


class _Handle:
    def __init__(self, state, header, sig_title, gauge_el, tiles, body):
        self._state = state          # shared with the collapse toggle
        self._header = header        # persistent header (title + gauge + tiles)
        self._sig_title = sig_title
        self._gauge = gauge_el
        self._tiles = tiles          # {key: value-label}
        self._body = body            # cleared + rebuilt per selection

    def clear(self):
        self._state["has_signal"] = False
        self._header.set_visibility(False)
        self._body.clear()
        with self._body:
            ui.label(_PLACEHOLDER).classes("opacity-60")

    def update(self, signal):
        if not signal:
            self.clear()
            return
        s = signal
        self._state["has_signal"] = True
        self._header.set_visibility(self._state["open"])
        self._sig_title.text = _signal_title(s)
        # Gauge value: the composite score when the signal has one (scanner/swing/
        # captured); for a paper trade — which never stored a composite score, so
        # the gauge used to sit at 0 — fall back to PoP, a real 0-100 quality read.
        score = s.get("composite_score")
        if score is None:
            score = s.get("pop_pct")
        self._gauge.options = gauge_figure(score or 0, s.get("grade", ""), height=104)
        self._gauge.update()
        for key, _label, value_fn, color_fn in _TILES:
            lbl = self._tiles[key]
            lbl.text = value_fn(s)
            _set_color(lbl, color_fn(s))
        self._body.clear()
        with self._body:
            _build_cards(s)


def render(width: int = 360):
    """Build the collapsible detail panel; returns a handle with update()/clear().

    The panel owns its own column so it can collapse to a thin strip (reclaiming
    horizontal space) and expand again via the header toggle.
    """
    expanded_w = f"w-[{width}px]"
    # The panel is a Deep Slate CARD (navy bg + hairline border + radius + padding)
    # so it reads as a bordered panel like the rest of the app / the mockup.
    col = ui.column().classes(f"shrink-0 gap-2 {CARD}").classes(expanded_w)
    with col:
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            title = ui.label("Trade detail").classes("text-subtitle1 font-bold")
            toggle_btn = ui.button(icon="last_page").props("flat round dense") \
                .tooltip("Collapse panel")
        # Persistent signal header (built once → registers the Highcharts ESM at
        # page load; updated in place per selection). Hidden until a signal lands.
        header = ui.column().classes("w-full gap-1")
        tiles = {}
        with header:
            sig_title = ui.label("").classes("text-subtitle1 font-bold")
            # Gauge STACKED above a full-width 2×2 tile grid: side-by-side the
            # gauge (160px) + min-width tiles overflowed the 290px panel (tiles
            # bled past the right edge). Stacking fits every panel width.
            gauge_el = ui.highchart(gauge_figure(0, "", height=104)) \
                .classes("self-center w-[160px] h-[104px]")
            with ui.grid(columns=2).classes("gap-2 w-full"):
                for key, label, _vf, _cf in _TILES:
                    tiles[key] = _tile_slot(label)
        header.set_visibility(False)
        body = ui.column().classes("w-full gap-2")
        with body:
            ui.label(_PLACEHOLDER).classes("opacity-60")

    state = {"open": True, "has_signal": False}

    def toggle():
        state["open"] = not state["open"]
        title.visible = state["open"]
        body.visible = state["open"]
        header.visible = state["open"] and state["has_signal"]
        if state["open"]:
            col.classes(remove="w-11", add=expanded_w)
            toggle_btn.props("icon=last_page").tooltip("Collapse panel")
        else:
            col.classes(remove=expanded_w, add="w-11")
            toggle_btn.props("icon=first_page").tooltip("Expand panel")

    toggle_btn.on_click(toggle)
    return _Handle(state, header, sig_title, gauge_el, tiles, body)
