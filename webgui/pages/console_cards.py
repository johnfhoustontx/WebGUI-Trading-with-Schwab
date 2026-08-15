"""The three top cards of the Market Regime Console — Sentiment, Trend, Signals.

Design: docs/design/2026-08-14-market-regime-console/README.md.

Pure decision helpers first, then ``render_*`` builders that paint into the
current NiceGUI container. The card contents are REBUILT on each repaint rather
than updated element-by-element: the handoff recommends not re-mounting rows,
but this page repaints every 120 s, the console carries no interactive state
(no hover-held tooltips, no focus, no expanders), and the alternative is ~40
element references threaded through ``_apply``. The page already rebuilds its
evidence chips the same way.

This module must NOT import ``pages.sentiment`` — that module imports this one.
Everything it needs arrives as plain data.
"""
from pages import console as K
from pages.options import theme
from pages.options.theme import (CONSOLE_CARD, CONSOLE_CELL, CONSOLE_DISPLAY,
                                 CONSOLE_DIVIDER, CONSOLE_HAIRLINE, CON_ACCENT,
                                 CON_TXT, CON_TXT_DIM, CON_TXT_LABEL,
                                 CON_TXT_MUTED, CON_TXT_SECONDARY)

_C = theme.CONSOLE_COLORS

# Card chrome. Square corners are the point — `rounded-none` is explicit so a
# global default can never soften them.
CARD_SHELL = (f"{CONSOLE_CARD} rounded-none overflow-hidden p-[20px] "
              f"pt-[20px] pb-[22px] gap-[18px] w-full h-full")
HEAD_TITLE = f"{CONSOLE_DISPLAY} text-[19px] font-bold tracking-[.16em] {CON_TXT}"
HEAD_META = f"text-[10px] tracking-[.18em] {CON_TXT_DIM}"
HERO_VALUE = "text-[76px] font-semibold leading-[.85] tracking-[-.02em]"
KICKER = f"text-[10px] tracking-[.28em] {CON_TXT_MUTED}"
LINK = f"text-[11px] tracking-[.22em] {CON_ACCENT}"


# --- pure helpers -----------------------------------------------------------
def hero_parts(value):
    """``(text, hex)`` for a card's hero number. A missing read is an em-dash in
    the muted tint — never a 0, which on a 0-100 score means 'maximally
    bearish' rather than 'no reading'."""
    v = K._safe(value)
    if v is None:
        return "—", _C["dim"]
    return f"{v:.0f}", K.band_hex(K.score_band(v))


def delta_parts(day, other, label):
    """``(arrow, text, hex)`` for "▲ +13 vs WEEK", or None when either side is
    missing. Sign drives both glyph and colour."""
    a, b = K._safe(day), K._safe(other)
    if a is None or b is None:
        return None
    d = a - b
    if d >= 0:
        return "▲", f"+{d:.0f} vs {label}", _C["positive"]
    return "▼", f"−{abs(d):.0f} vs {label}", _C["warning"]


def pill_classes(hexv):
    return (f"border border-[{hexv}]/[0.45] bg-[{theme._alpha_hex(hexv, 0.16)}] "
            f"px-[10px] py-[4px] text-[12px] tracking-[.16em] text-[{hexv}]")


def tone_hex(tone):
    """Signals tone -> colour.

    DEVIATION from the handoff, deliberate: it hard-codes a hue per CELL (bias
    green, signal teal, yesterday amber), which encodes nothing — on today's
    live data, bias and signal are both *Neutral*, and a fixed green/teal would
    paint a Neutral reading in the colours of Long/Bullish. Colouring by the
    tone the page already computes says something true instead."""
    return {"pos": _C["positive"], "neg": _C["negative"],
            "warn": _C["warning"], "flat": _C["muted"]}.get(tone, _C["muted"])


def cell_tint(hexv):
    """A cell's background: near-black lifted a tenth toward its value colour —
    derived, so a new tone cannot arrive without its tint."""
    return K._mix("#080c11", hexv, 0.10)


DIVERGENCE_BAR_H = 30       # px at a full 10/10 component score


def divergence_bars(detail):
    """``[(label, score, height_px, opacity)]`` for the two-bar mini chart, or []
    when Tier 2 published no divergence. Heights are proportional to the 0-10
    component scores, so the pair reads as the DISAGREEMENT it represents."""
    d = detail if isinstance(detail, dict) else {}
    hi, lo = d.get("high"), d.get("low")
    if not isinstance(hi, dict) or not isinstance(lo, dict):
        return []
    out = []
    for side, opacity in ((hi, 0.85), (lo, 0.45)):
        score = K._safe(side.get("score"))
        if score is None:
            return []
        h = max(2, round(K._clamp(score, 0.0, 10.0) / 10.0 * DIVERGENCE_BAR_H))
        out.append((str(side.get("name") or ""), score, h, opacity))
    return out


def divergence_text(detail):
    """"Market Breadth 10 vs Rotation 5" from the structured pair."""
    bars = divergence_bars(detail)
    if not bars:
        return ""
    return f"{bars[0][0]} {bars[0][1]:.0f} vs {bars[1][0]} {bars[1][1]:.0f}"


# --- shared chrome ----------------------------------------------------------
def _card_head(title, meta, meta_class=None):
    from nicegui import ui
    with ui.row().classes("items-baseline justify-between w-full"):
        ui.label(title).classes(HEAD_TITLE)
        if meta:
            ui.label(meta).classes(meta_class or HEAD_META)


def _hero(value, pill_text, pill_hex, kicker, delta):
    """The big number + its pill and delta line."""
    from nicegui import ui
    text, hexv = hero_parts(value)
    with ui.row().classes("items-end gap-4 w-full"):
        ui.label(text).classes(f"{HERO_VALUE} text-[{hexv}]")
        with ui.column().classes("gap-[6px]"):
            ui.label(kicker).classes(KICKER)
            with ui.row().classes("items-center gap-2"):
                if pill_text:
                    ui.label(pill_text).classes(pill_classes(pill_hex or hexv))
                if delta:
                    arrow, dtext, dhex = delta
                    ui.label(f"{arrow} {dtext}").classes(
                        f"text-[12px] text-[{dhex}]")


def _meters(arcs):
    """The Day/Week/Month meter stack + its shared ruler."""
    from nicegui import ui
    with ui.column().classes("gap-3 w-full"):
        for arc in arcs or []:
            K.mount_timeframe_meter(K.meter_row(arc.get("caption", ""),
                                                arc.get("value")))
        K.mount_ruler()


def _link(text):
    from nicegui import ui
    ui.label(text).classes(f"{LINK} border-t {CONSOLE_DIVIDER} pt-3 w-full")


# --- 4.1 Sentiment ----------------------------------------------------------
def render_sentiment_card(arcs, bias, total, confidence):
    """Hero + bias pill + Day/Week/Month meters + model-confidence footer."""
    from nicegui import ui
    day = (arcs[0].get("value") if arcs else None)
    week = (arcs[1].get("value") if arcs and len(arcs) > 1 else None)
    _, hero_hex = hero_parts(day)
    conf = K._safe(confidence)
    with ui.column().classes(CARD_SHELL):
        _card_head("MARKET SENTIMENT", "SCALE 0—100")
        pill = f"{str(bias or '').upper()} {total}".strip() if bias else ""
        _hero(day, pill, hero_hex, "DAY READ",
              delta_parts(day, week, "WEEK"))
        _meters(arcs)
        with ui.column().classes("mt-auto gap-[9px] w-full"):
            with ui.row().classes("items-baseline justify-between w-full"):
                ui.label("MODEL CONFIDENCE").classes(
                    f"text-[10px] tracking-[.22em] {CON_TXT_DIM}")
                ui.label("—" if conf is None else f"{conf * 100:.0f}%").classes(
                    f"text-[13px] text-[{hero_hex}]")
            K.mount_segmented(conf, hero_hex)
            _link("COMPONENTS →")


# --- 4.2 Trend --------------------------------------------------------------
def render_trend_card(arcs, short_state, verdict, guidance):
    """Hero + state pill + meters (incl. the NO READ horizon) + verdict block."""
    from nicegui import ui
    day = (arcs[0].get("value") if arcs else None)
    month = (arcs[2].get("value") if arcs and len(arcs) > 2 else None)
    _, hero_hex = hero_parts(day)
    with ui.column().classes(CARD_SHELL):
        _card_head("MARKET TREND", "SCALE 0—100")
        _hero(day, str(short_state or "").upper(), hero_hex, "DAY READ",
              delta_parts(day, month, "MONTH"))
        _meters(arcs)
        with ui.column().classes("mt-auto gap-[9px] w-full"):
            if verdict or guidance:
                with ui.column().classes(
                        f"gap-[6px] w-full border-l-[3px] border-[{hero_hex}] "
                        f"bg-[linear-gradient(90deg,"
                        f"{theme._alpha_hex(hero_hex, 0.14)},transparent)] "
                        f"px-[14px] py-[12px]"):
                    if verdict:
                        ui.label(str(verdict).upper()).classes(
                            f"{CONSOLE_DISPLAY} text-[19px] font-bold "
                            f"tracking-[.1em] text-[{hero_hex}]")
                    if guidance:
                        ui.label(guidance).classes(
                            f"text-[11.5px] leading-[1.55] {CON_TXT_SECONDARY}")
            _link("TREND DETAIL →")


# --- 4.3 Signals ------------------------------------------------------------
def render_signals_card(rows, velocity_values, divergence_detail):
    """2x2 readout matrix + the three signed ROC/z meters + divergence alert.

    The 2x2 restores the handoff's layout. The page's own 1x4 stack exists
    because a 2x2 left a void beside the 460px-tall rings — and the console
    replaces those rings with short meter stacks, so that reason is gone.
    """
    from nicegui import ui
    rows = list(rows or [])
    reads = sum(1 for r in rows if str(r.get("value", "—")) != "—")
    with ui.column().classes(f"{CARD_SHELL} gap-4"):
        _card_head("SIGNALS", f"{reads} READS · LIVE",
                   f"text-[10px] tracking-[.18em] {CON_ACCENT}")
        # The 1px gap IS the hairline grid — the handoff's own technique.
        with ui.element("div").classes(
                f"grid grid-cols-2 gap-px w-full {CONSOLE_HAIRLINE} "
                f"border {CONSOLE_DIVIDER}"):
            for r in rows:
                hexv = tone_hex(r.get("tone"))
                with ui.column().classes(
                        f"gap-[6px] px-4 pt-4 pb-[14px] "
                        f"bg-[{cell_tint(hexv)}]"):
                    ui.label(str(r.get("label", ""))).classes(
                        f"text-[9.5px] tracking-[.24em] {CON_TXT_LABEL}")
                    ui.label(str(r.get("value", "—"))).classes(
                        f"{CONSOLE_DISPLAY} text-[32px] font-bold "
                        f"tracking-[.06em] text-[{hexv}]")
                    ui.label(str(r.get("descriptor", ""))).classes(
                        f"text-[9.5px] tracking-[.16em] {CON_TXT_MUTED}")
        vals = velocity_values if isinstance(velocity_values, dict) else {}
        with ui.column().classes("gap-3 w-full"):
            ui.label("RATE OF CHANGE · Z-SCORE").classes(
                f"text-[10px] tracking-[.24em] {CON_TXT_DIM}")
            for label, key in (("3D ROC", "roc_3d"), ("5D ROC", "roc_5d"),
                               ("20D Z", "z_20d")):
                K.mount_bipolar(label, K.bipolar_geometry(vals.get(key)))
        bars = divergence_bars(divergence_detail)
        if bars:
            neg = _C["negative"]
            with ui.row().classes(
                    f"mt-auto items-center gap-4 w-full px-[14px] py-[12px] "
                    f"border border-[{neg}]/[0.3] "
                    f"bg-[{theme._alpha_hex(neg, 0.10)}]"):
                with ui.column().classes("gap-1 flex-1"):
                    ui.label("DIVERGENCE · LOW CONVICTION").classes(
                        f"text-[9.5px] tracking-[.24em] text-[{neg}]")
                    ui.label(divergence_text(divergence_detail)).classes(
                        f"text-[11.5px] text-[{K._mix(neg, '#ffffff', 0.6)}]")
                with ui.row().classes("items-end gap-1 h-[30px]"):
                    for _name, _score, h, opacity in bars:
                        ui.element("div").classes(
                            f"w-[9px] h-[{h}px] bg-[{neg}] "
                            f"opacity-[{opacity}]")
