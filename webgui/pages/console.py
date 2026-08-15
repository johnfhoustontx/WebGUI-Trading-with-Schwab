"""Shared primitives for the Market Regime Console on ``/sentiment``.

Design: docs/design/2026-08-14-market-regime-console/README.md.
Plan + measured spikes: docs/plans/2026-08-14-sentiment-console-redesign-plan.md.

The handoff is 100% inline styles; this app bans them (``test_no_inline_style``),
so every declaration here is a Tailwind class built from ``[console]`` tokens.
Phase 0/2 spikes measured that the whole vocabulary this needs generates —
glows, the three gradient families, arbitrary opacity, arbitrary grids, and a
full font stack — so nothing here needs a CSS escape hatch. (The page's one
``ui.add_css`` carries the ``pulseDot`` keyframes and nothing else.)

Split deliberately: the geometry and class decisions are PURE functions, unit
tested; the ``mount_*`` helpers are the thin NiceGUI wrappers the cards call, so
the meters cannot drift between the Sentiment and Trend cards.
"""
from pages.options import theme
from pages.options.theme import CONSOLE_ALPHA, console_glow

_C = theme.CONSOLE_COLORS
_LINE = _C["line"]

# --- scale ------------------------------------------------------------------
# 0-100 score -> band colour. The five thresholds are FITTED to the handoff's
# own five readings (day 73 + month 83 green, trend day 68 yellow, week 60
# olive, month 53 amber) and reproduce all five exactly.
#
# The bottom band is an EXTRAPOLATION, not from the handoff: its lowest sample
# is 53. A 20 reading is not the same "caution" as a 53, so a bearish floor gets
# the negative red rather than sharing amber. Revisit if the design ever shows
# a low value.
_BANDS = ((70.0, "positive"), (65.0, "yellow"), (55.0, "olive"),
          (35.0, "warning"))


def score_band(value):
    """Semantic band key for a 0-100 score — the finite set the colour maps
    index (never a runtime-built colour)."""
    v = _safe(value)
    if v is None:
        return "muted"
    for floor, key in _BANDS:
        if v >= floor:
            return key
    return "negative"


def band_hex(key):
    """Band key -> hex, for the fill gradient / marker / value text."""
    return {"positive": _C["positive"], "yellow": _C["yellow"],
            "olive": _C["olive"], "warning": _C["warning"],
            "negative": _C["negative"], "muted": _C["dim"]}.get(key, _C["dim"])


def _safe(v):
    """Finite float, else None. None is how every primitive here says NO DATA —
    which the console draws as a hatched track, never as a zero-length bar."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _hex_rgb(value):
    h = str(value).lstrip("#")
    if len(h) != 6:
        return 255, 255, 255
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return 255, 255, 255


def _mix(value, other, t):
    a, b = _hex_rgb(value), _hex_rgb(other)
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def marker_hex(value):
    """The end marker is a near-white tint of the series colour. DERIVED rather
    than carrying the handoff's four hand-picked tints, so a new band colour
    cannot arrive without its marker (measured: #35d68a -> #d7f7e8, against the
    handoff's #d9fff0 — imperceptible at a 2px rule)."""
    return _mix(value, "#ffffff", 0.8)


# --- timeframe meter (Sentiment / Trend cards) ------------------------------
RULER_MARKS = (0, 25, 50, 75, 100)
_FILL_ALPHA = 0.22          # the gradient's left stop, per the handoff's 20-25%


def meter_row(label, value):
    """Everything one timeframe row needs, decided here rather than in markup.

    ``value is None`` (a horizon with no usable reading) yields ``no_read``,
    which the console draws as a hatched track + "NO READ" + an em-dash — the
    same honesty rule the rings already follow: never a fabricated neutral.
    """
    v = _safe(value)
    if v is None:
        return {"label": str(label), "value": None, "text": "—",
                "no_read": True, "band": "muted", "hex": _C["dim"],
                "pct": 0.0, "fill": "", "marker": "", "glow": ""}
    v = _clamp(v, 0.0, 100.0)
    band = score_band(v)
    hexv = band_hex(band)
    return {
        "label": str(label), "value": v, "text": f"{v:.0f}", "no_read": False,
        "band": band, "hex": hexv, "pct": v,
        "fill": (f"bg-[linear-gradient(90deg,"
                 f"{theme._alpha_hex(hexv, _FILL_ALPHA)},{hexv})]"),
        "marker": f"bg-[{marker_hex(hexv)}]",
        # Glow only on a STRONG reading — the handoff glows the day meter and
        # leaves week/month flat, so the eye lands on the live number.
        "glow": console_glow(hexv, px=16, alpha=0.45) if band == "positive" else "",
    }


# The "no read" hatch. A diagonal repeating gradient reads as "instrument absent"
# rather than "value is zero", which a flat empty track would not.
NO_READ_HATCH = (f"bg-[repeating-linear-gradient(135deg,"
                 f"{theme._alpha_hex(_LINE, 0.10)}_0_6px,transparent_6px_12px)]")


def track_classes():
    """The shared meter track (both timeframe and bipolar rows sit on it)."""
    a = CONSOLE_ALPHA
    return (f"relative bg-[{_LINE}]/[{a['track']}] "
            f"border border-[{_LINE}]/[{a['track_border']}]")


def width_class(pct):
    """A CONTINUOUS width as a runtime arbitrary value — the documented
    exception to the map-to-a-finite-palette rule (there is no finite set of
    percentages). Rounded to 0.1% so repaints don't churn distinct classes."""
    return f"w-[{_clamp(_safe(pct) or 0.0, 0.0, 100.0):.1f}%]"


def left_class(pct):
    return f"left-[{_clamp(_safe(pct) or 0.0, 0.0, 100.0):.1f}%]"


# --- segmented meter (model confidence) -------------------------------------
SEGMENTS = 10


def segmented_cells(confidence, n=SEGMENTS):
    """``[bool] * n`` — how many discrete cells are lit.

    N cells via flex+gap, which is what the handoff itself recommends over its
    prototype's ``repeating-linear-gradient`` hack. 10 cells = one per 10%, so
    the count is readable at a glance instead of needing the % label."""
    c = _safe(confidence)
    if c is None:
        return [False] * n
    # Half-UP, not round(): Python rounds halves to even, so round(4.5)==4 while
    # round(5.5)==6 — a meter whose midpoint behaviour flips on the parity of
    # the cell count is a surprise nobody wants to debug at 0.45 vs 0.55.
    lit = min(n, int(_clamp(c, 0.0, 1.0) * n + 0.5))
    return [i < lit for i in range(n)]


# --- bipolar meter (ROC / z-score) ------------------------------------------
# The handoff states BOTH a scale ("right half = 0 -> +5 for ROC and 0 -> +2.5
# for z") and the rendered widths (32% / 10% / 22%). THEY DISAGREE: at scale 5
# the ROC values render 16.3% and 4.8%, not 32% and 10%. All three stated widths
# are self-consistent at scale 2.5, and the README calls the rendering the
# visual source of truth — so 2.5 it is, for both. Verified: 1.63 -> 32.6%,
# 0.48 -> 9.6%, 1.11 -> 22.2%.
BIPOLAR_SCALE = 2.5


def bipolar_geometry(value, scale=BIPOLAR_SCALE):
    """``{side, pct, text, hex}`` for one signed meter.

    ``pct`` is a share of the FULL track (the fill starts at the centre and
    reaches at most 50%), so it can be handed straight to ``width_class``."""
    v = _safe(value)
    if v is None:
        return {"side": "none", "pct": 0.0, "text": "—", "hex": _C["dim"],
                "value": None}
    sc = _safe(scale) or BIPOLAR_SCALE
    if sc <= 0:
        sc = BIPOLAR_SCALE
    pct = _clamp(abs(v) / sc, 0.0, 1.0) * 50.0
    return {"side": "right" if v >= 0 else "left", "pct": pct,
            "text": f"{v:+.2f}",
            "hex": _C["positive"] if v >= 0 else _C["negative"], "value": v}


# --- chrome -----------------------------------------------------------------
def chip_classes(severity):
    """Diagnostic-tag chip. Two severities only — the finite set Tier 2 now
    publishes on every evidence line (warn = a choppy/crisis scorer produced
    it)."""
    if severity == "warn":
        hexv = _C["negative"]
        return (f"border border-[{hexv}]/[0.34] bg-[{theme._alpha_hex(hexv, 0.12)}] "
                f"text-[{_mix(hexv, '#ffffff', 0.72)}] px-[11px] py-[7px] "
                f"text-[11.5px] tracking-[.08em]")
    hexv = _C["accent"]
    return (f"border border-[{hexv}]/[0.3] bg-[{theme._alpha_hex(hexv, 0.12)}] "
            f"text-[{_mix(hexv, '#ffffff', 0.72)}] px-[11px] py-[7px] "
            f"text-[11.5px] tracking-[.08em]")


def chip_value_class(severity):
    """The numeric half of a chip, which the design colours more strongly than
    its label."""
    return f"text-[{_C['negative'] if severity == 'warn' else _C['accent']}]"


def split_tag(text):
    """``"Balanced profile 0.53"`` -> ``("BALANCED PROFILE", "0.53")``.

    The design colours a tag's number differently from its words, and the
    classifier emits one string with the number at EITHER end ("3 failed OR
    breaks" vs "Band-hug 50%"). Display-tier concern, so it lives here rather
    than pushing more structure through Tier 2. Returns ``(label, "")`` when
    there is no numeric token to lift."""
    words = str(text or "").split()
    if not words:
        return "", ""
    if len(words) > 1 and _looks_numeric(words[-1]):
        return " ".join(words[:-1]).upper(), words[-1]
    if len(words) > 1 and _looks_numeric(words[0]):
        return " ".join(words[1:]).upper(), words[0]
    return " ".join(words).upper(), ""


def _looks_numeric(token):
    return any(ch.isdigit() for ch in token) and not token.strip("+-").isalpha()


# --- NiceGUI mounts ---------------------------------------------------------
# Thin on purpose: every decision above is already made. These exist so the two
# score cards cannot let their meters drift apart.
def mount_timeframe_meter(row):
    """One timeframe row: label · track · value."""
    from nicegui import ui
    from pages.options.theme import CON_TXT_MUTED, CON_TXT_DIM

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label(row["label"]).classes(
            f"w-[52px] shrink-0 text-[10px] tracking-[.2em] {CON_TXT_MUTED}")
        with ui.element("div").classes(f"flex-1 h-[18px] {track_classes()}"):
            if row["no_read"]:
                ui.element("div").classes(f"absolute inset-0 {NO_READ_HATCH}")
                ui.label("NO READ").classes(
                    "absolute inset-0 flex items-center justify-center "
                    f"text-[9.5px] tracking-[.22em] {CON_TXT_DIM}")
            else:
                ui.element("div").classes(
                    f"absolute left-0 top-0 bottom-0 {width_class(row['pct'])} "
                    f"{row['fill']} {row['glow']}")
                ui.element("div").classes(
                    f"absolute w-[2px] -top-[3px] -bottom-[3px] "
                    f"{left_class(row['pct'])} {row['marker']}")
        ui.label(row["text"]).classes(
            f"w-[34px] shrink-0 text-right text-[17px] font-medium "
            f"text-[{row['hex']}]")


def mount_ruler():
    """The 0/25/50/75/100 rule under a meter stack. Spacers match the meter
    row's label/value columns so the ticks line up with the track, not the row."""
    from nicegui import ui
    from pages.options.theme import CON_TXT_FAINT, CONSOLE_DIVIDER

    with ui.row().classes("items-center gap-3 w-full"):
        ui.element("div").classes("w-[52px] shrink-0")
        with ui.row().classes(
                f"flex-1 justify-between border-t {CONSOLE_DIVIDER} pt-[5px]"):
            for mark in RULER_MARKS:
                ui.label(str(mark)).classes(f"text-[9.5px] {CON_TXT_FAINT}")
        ui.element("div").classes("w-[34px] shrink-0")


def mount_segmented(confidence, hexv=None):
    """The model-confidence meter — N discrete cells."""
    from nicegui import ui

    hexv = hexv or _C["positive"]
    lit_cls = f"flex-1 h-full bg-[{hexv}] {console_glow(hexv, px=12, alpha=0.35)}"
    dim_cls = f"flex-1 h-full bg-[{_LINE}]/[{CONSOLE_ALPHA['hairline']}]"
    with ui.row().classes("gap-[3px] h-[10px] w-full items-stretch"):
        for lit in segmented_cells(confidence):
            ui.element("div").classes(lit_cls if lit else dim_cls)


def mount_bipolar(label, geom):
    """One signed ROC/z row: label · centred track · value."""
    from nicegui import ui
    from pages.options.theme import CON_TXT_MUTED

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label(label).classes(
            f"w-[46px] shrink-0 text-[10px] {CON_TXT_MUTED}")
        with ui.element("div").classes(f"flex-1 h-[8px] {track_classes()}"):
            # The zero line first, so a fill never paints over the reference.
            ui.element("div").classes(
                f"absolute left-1/2 w-px -top-[3px] -bottom-[3px] "
                f"bg-[{_LINE}]/[0.5]")
            if geom["side"] != "none" and geom["pct"] > 0:
                edge = "left-1/2" if geom["side"] == "right" else "right-1/2"
                ui.element("div").classes(
                    f"absolute {edge} top-0 bottom-0 "
                    f"{width_class(geom['pct'])} bg-[{geom['hex']}] "
                    f"{console_glow(geom['hex'], px=10, alpha=0.4)}")
        ui.label(geom["text"]).classes(
            f"w-[46px] shrink-0 text-right text-[13px] text-[{geom['hex']}]")


def mount_chip(text, severity="info"):
    """A diagnostic tag, with its numeric half lifted out and coloured."""
    from nicegui import ui

    label, value = split_tag(text)
    with ui.row().classes(f"items-center gap-2 {chip_classes(severity)}"):
        ui.label(label)
        if value:
            ui.label(value).classes(f"font-medium {chip_value_class(severity)}")
