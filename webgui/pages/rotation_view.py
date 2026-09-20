"""Pure display language for the Sector Rotation board (/sentiment/rotation).

Everything the board computes lives here so it can be pinned by
``tests/test_rotation_view.py`` without a browser: the diverging spread gauge's
geometry, the weight-proportional flow band, the four quadrant panels, and the
prose derived from the spread. ``pages/sentiment_rotation.py`` keeps the widgets
— and, unchanged, the RRG figure builders that ``pages/sentiment_rrg.py`` shares.

**The design in one paragraph.** Three things replace the old table. A *verdict
strip*: the regime word, a diverging gauge putting the cyclical-vs-defensive
spread on a −3…+3 scale with both ±threshold triggers marked, and the spread
itself with a sentence saying how far past the trigger it is. A *flow band*:
one segment per sector, width proportional to its S&P weight, split into the
side rotating out and the side rotating in — so the question "how much of the
index is actually moving?" is answered by area rather than by reading a column
of percentages. And *four quadrant panels* carrying every sector as a chip with
its RS-Momentum and a weight bar.

**Why the palette is code and not ``config/theme.toml``.** The four quadrant
hues are a data-driven cell map, the category CLAUDE.md excludes from the
config-driven palette. ⚠ **There is no chrome here any more.** The ground, the
panel fill, the hairlines and the warm-neutral grey ladder went on 2026-09-19,
when the last of the four screens that wore them moved onto the page kit
(``pages/ui_kit.py``) — and ``[rotation]`` was deleted from ``theme.toml`` with
them, since ``void``, ``panel`` and ``font_url`` were all it held. What survives
in this module is DATA only: the quadrant hues, the three tones and the prose.

⚠ **This palette is page-scoped and deliberately differs from the RRG tab's.**
``sentiment_rotation.quadrant_color`` (Leading green / Improving cyan /
Weakening yellow / Lagging red) still drives the RRG scatter and the Sector &
Industry quadrant text. The supplied design re-hues them (Improving → blue 232,
Weakening → olive 80) and this module implements that for this screen only.
Unifying the two is a separate decision, not one to make silently here.
"""
import math

from pages.fmt import num as _num  # the ONE copy (pages/fmt.py)
from pages.oklch import oklch_hex

# ── the quadrant palette ─────────────────────────────────────────────────────
# Panel order is the reading order of a rotation, not the alphabet: Improving
# and Leading are where money is going, Lagging and Weakening where it is
# leaving. Hue/chroma pairs come from the supplied design.
QUADRANT_ORDER = ("Improving", "Leading", "Lagging", "Weakening")
FALLBACK_QUADRANT = "Improving"

QUAD_HUE = {"Leading": 158.0, "Improving": 232.0,
            "Weakening": 80.0, "Lagging": 22.0}
QUAD_CHROMA = {"Leading": 0.13, "Improving": 0.11,
               "Weakening": 0.12, "Lagging": 0.16}
QUAD_BLURB = {
    "Leading": "Strong and still strengthening. The destination of this rotation.",
    "Improving": "Still weak on relative strength, but momentum has turned up.",
    "Weakening": "Still strong, but momentum has rolled over. Money is leaving.",
    "Lagging": "Weak and getting weaker. The source of this rotation.",
}


def _accent(quadrant, lightness):
    """A quadrant's hue at full chroma — dots, titles, figures, bars."""
    return oklch_hex(lightness, QUAD_CHROMA[quadrant], QUAD_HUE[quadrant])


def _wash(quadrant, lightness, chroma):
    """A quadrant's hue at reduced chroma — the fills things sit *on*."""
    return oklch_hex(lightness, chroma, QUAD_HUE[quadrant])


# The fixed finite class vocabulary: four quadrants × eight roles, built once at
# import from the closed enumeration above rather than per datum, per the
# Tailwind-first rule on data-driven colour.
QUAD_CLASSES = {
    q: {
        "dot": f"bg-[{_accent(q, 0.70)}]",
        "title": f"text-[{_accent(q, 0.84)}]",
        "mom": f"text-[{_accent(q, 0.86)}]",
        "bar": f"bg-[{_accent(q, 0.62)}]",
        "chip": f"bg-[{_wash(q, 0.19, QUAD_CHROMA[q] * 0.28)}]",
        "seg": f"bg-[{_wash(q, 0.28, QUAD_CHROMA[q] * 0.72)}]",
        "seg_top": f"border-[{_accent(q, 0.68)}]",
        "ticker": f"text-[{_accent(q, 0.88)}]",
    }
    for q in QUADRANT_ORDER
}


def quad_classes(quadrant):
    """The class bundle for a quadrant; an unknown name degrades, never raises."""
    return QUAD_CLASSES.get(quadrant, QUAD_CLASSES[FALLBACK_QUADRANT])


# ── the flat tone's own neutral ──────────────────────────────────────────────
# ⚠ This is the LAST survivor of the warm-neutral ladder this module used to
# export (``NEUTRAL`` / ``NT`` / ``NB`` / ``NE``, one lightness step per role at
# oklch(L, 0.006-0.01, 90)). That ladder was page-scoped SURFACE, and it retired
# on 2026-09-19 with the last of the four screens that wore it — RRG, Sector
# Rotation, Bull/Bear and Momentum, all now on the app's own token vocabulary
# (``pages/ui_kit.py``). ``TONE["flat"]`` was BUILT out of it and is DATA: it is
# what the board wears when the spread has no direction, beside ``up`` and
# ``down``. So it keeps the three steps it actually used, at the same hue and
# chroma, as a neutral of its own — the values below are byte-identical to what
# the ladder gave it.
_FLAT_HUE = 90.0
_FLAT_TXT = oklch_hex(0.93, 0.006, _FLAT_HUE)    # the figure and its footer
_FLAT_MARK = oklch_hex(0.94, 0.006, _FLAT_HUE)   # the gauge's reading mark
_FLAT_RAIL = oklch_hex(0.50, 0.010, _FLAT_HUE)   # the dot, the fill, the rule
_FLAT_AXIS = oklch_hex(0.44, 0.010, _FLAT_HUE)   # ticks and axis text

# The two semantic accents outside the quadrant set: the flow band's footers and
# the gauge's triggers speak "out/in" and "risk-off/risk-on", which share the
# Lagging red (hue 22) and Leading green (hue 158) by design rather than by
# coincidence.
TONE = {
    "down": {"txt": f"text-[{oklch_hex(0.75, 0.14, 22)}]",
             "dot": f"bg-[{oklch_hex(0.65, 0.16, 22)}]",
             "fill": f"bg-[{oklch_hex(0.60, 0.14, 22)}]",
             "mark": f"bg-[{oklch_hex(0.86, 0.10, 22)}]",
             "tick": f"bg-[{oklch_hex(0.40, 0.09, 22)}]",
             "axis": f"text-[{oklch_hex(0.58, 0.08, 22)}]",
             "foot_edge": f"border-[{oklch_hex(0.66, 0.16, 22)}]",
             "foot_pct": f"text-[{oklch_hex(0.78, 0.13, 22)}]",
             "foot_lbl": f"text-[{oklch_hex(0.62, 0.06, 22)}]"},
    "up": {"txt": f"text-[{oklch_hex(0.75, 0.14, 158)}]",
           "dot": f"bg-[{oklch_hex(0.65, 0.16, 158)}]",
           "fill": f"bg-[{oklch_hex(0.60, 0.14, 158)}]",
           "mark": f"bg-[{oklch_hex(0.86, 0.10, 158)}]",
           "tick": f"bg-[{oklch_hex(0.40, 0.09, 158)}]",
           "axis": f"text-[{oklch_hex(0.56, 0.07, 158)}]",
           "foot_edge": f"border-[{oklch_hex(0.66, 0.13, 158)}]",
           "foot_pct": f"text-[{oklch_hex(0.80, 0.11, 158)}]",
           "foot_lbl": f"text-[{oklch_hex(0.64, 0.06, 158)}]"},
    "flat": {"txt": f"text-[{_FLAT_TXT}]", "dot": f"bg-[{_FLAT_RAIL}]",
             "fill": f"bg-[{_FLAT_RAIL}]", "mark": f"bg-[{_FLAT_MARK}]",
             "tick": f"bg-[{_FLAT_AXIS}]", "axis": f"text-[{_FLAT_AXIS}]",
             "foot_edge": f"border-[{_FLAT_RAIL}]",
             "foot_pct": f"text-[{_FLAT_TXT}]",
             "foot_lbl": f"text-[{_FLAT_AXIS}]"},
}
TONE_TXT_CLASSES = " ".join(dict.fromkeys(t["txt"] for t in TONE.values()))


# ── numbers ──────────────────────────────────────────────────────────────────
DASH = "—"
MINUS = "−"      # U+2212, the typographic minus the mono face aligns on


def fmt_mom(v):
    n = _num(v)
    return DASH if n is None else f"{n:.2f}"


def fmt_weight(v):
    n = _num(v)
    return DASH if n is None else f"{n:.1f}%"


def fmt_spread(v):
    """``−1.51`` / ``+1.51`` — a real minus sign, not a hyphen.

    The figure is set in JetBrains Mono at 32px beside a ``±`` threshold; a
    hyphen-minus is visibly shorter and sits at the wrong height next to it."""
    n = _num(v)
    if n is None:
        return DASH
    return f"{MINUS}{abs(n):.2f}" if n < 0 else f"+{n:.2f}"


def eyebrow(date):
    """``RRG vs SPY · as of 2026-08-17`` — the benchmark and the reading's date."""
    return f"RRG vs SPY · as of {date}" if date else "RRG vs SPY · awaiting data"


# ── the diverging spread gauge ───────────────────────────────────────────────
GAUGE_SCALE = 3.0     # the −3…+3 the track spans


def _pos(value, scale=GAUGE_SCALE):
    """A spread → its position along the track as a percentage, clamped."""
    v = min(scale, max(-scale, value))
    return (v + scale) / (2 * scale) * 100.0


def spread_gauge(spread, threshold, scale=GAUGE_SCALE):
    """Geometry for the diverging gauge, or None with no reading.

    The fill spans between the reading and zero rather than growing from one
    end, because the quantity being shown is signed: which side of zero it is on
    *is* the verdict, and a bar growing from the left would encode ``−3`` and
    ``+3`` as "small" and "large" instead of "opposite"."""
    v = _num(spread)
    if v is None:
        return None
    t = abs(_num(threshold) or 0.0)
    value_pct = _pos(v, scale)
    zero_pct = _pos(0.0, scale)
    return {
        "value_pct": value_pct,
        "zero_pct": zero_pct,
        "fill_left_pct": min(value_pct, zero_pct),
        "fill_width_pct": abs(zero_pct - value_pct),
        "lo_pct": _pos(-t, scale),
        "hi_pct": _pos(t, scale),
        "tone": "flat" if v == 0 else ("up" if v > 0 else "down"),
    }


def gauge_axis(threshold, scale=GAUGE_SCALE):
    """The five labels under the track: ends, both triggers, and zero."""
    t = abs(_num(threshold) or 0.0)
    return [
        {"text": f"{MINUS}{scale:.1f}", "tone": "muted"},
        {"text": f"{MINUS}{t:.2f} trigger", "tone": "down"},
        {"text": "0", "tone": "muted"},
        {"text": f"+{t:.2f} trigger", "tone": "up"},
        {"text": f"+{scale:.1f}", "tone": "muted"},
    ]


# ── the prose the spread implies ─────────────────────────────────────────────
# How far past its trigger a spread has to travel before the rotation stops
# being news. 1.5× is a judgement call, stated here rather than buried: at the
# reference reading (−1.51 against ±1.50) the signal had *just* fired, and the
# board should say so rather than presenting a hair over the line the same way
# it presents a rout.
ENTRENCHED_RATIO = 1.5


def trigger_note(spread, threshold):
    """One sentence on how far past the trigger the spread sits."""
    v = _num(spread)
    t = abs(_num(threshold) or 0.0)
    if v is None:
        return ""
    if t <= 0:
        return ""
    mag = abs(v)
    if mag < t:
        return (f"Inside the ±{t:.2f} band — no rotation signal has fired.")
    if mag < t * ENTRENCHED_RATIO:
        return "Just past the trigger — a fresh signal, not an entrenched one."
    return "Well past the trigger — an entrenched rotation, not a fresh one."


_REGIME_WORD = {"Risk-OFF": ("Risk-off", "down"), "Risk-ON": ("Risk-on", "up")}
_REGIME_SENTENCE = {
    "Risk-OFF": "Money is rotating into defensives and out of cyclicals.",
    "Risk-ON": "Money is rotating into cyclicals and out of defensives.",
}


def regime_display(regime):
    """``("Risk-off", "down")`` — the headline word plus its tone."""
    if not regime:
        return DASH, "flat"
    return _REGIME_WORD.get(regime, (str(regime), "flat"))


def regime_sentence(regime, service_text=""):
    """Plain prose for the verdict, falling back to the service's own text.

    The service writes a log line — ``"Risk-OFF rotation - money rotating into
    defensives, out of cyclicals"`` — which repeats the regime already shown
    beside it and uses an ASCII hyphen as a dash. For the two regimes we can
    state cleanly, we do; anything else renders whatever the service said, so a
    new regime can never render as silence."""
    return _REGIME_SENTENCE.get(regime, service_text or "")


# ── the flow band ────────────────────────────────────────────────────────────
# A segment narrower than this share of its own side cannot hold its ticker and
# percentage without clipping, so it renders as a bare colour block. Measured
# against the reference layout: at 7.5% of a half-width band the label box is
# already tighter than the text.
LABEL_MIN_SHARE = 0.075


def _weight(weights, etf):
    return _num((weights or {}).get(etf)) or 0.0


def _side(sectors, weights, direction):
    rows = [s for s in (sectors or []) if s.get("direction") == direction]
    rows = sorted(rows, key=lambda s: -_weight(weights, s.get("etf")))
    total = sum(_weight(weights, s.get("etf")) for s in rows)
    out = []
    for s in rows:
        w = _weight(weights, s.get("etf"))
        share = (w / total) if total > 0 else 0.0
        out.append({
            "name": s.get("name"), "etf": s.get("etf"),
            "quadrant": s.get("quadrant"), "weight": w, "share": share,
            "wide": share >= LABEL_MIN_SHARE,
        })
    return out, total


def flow_sides(sectors, weights):
    """``{"from": …, "into": …}`` — each side's segments, total and caption.

    The split keys on the engine's own ``direction`` field rather than on the
    quadrant, so the band always partitions exactly the sectors the assessment
    called rotating, and the two totals sum to the share of the index in
    motion."""
    out = {}
    for key, direction, verb in (("from", "FROM", "out"), ("into", "INTO", "in")):
        rows, total = _side(sectors, weights, direction)
        n = len(rows)
        out[key] = {
            "rows": rows, "total": total, "count": n,
            "label": f"Rotating {verb} · {n} sector{'' if n == 1 else 's'}",
        }
    return out


# ── the quadrant panels ──────────────────────────────────────────────────────
def quadrant_panels(sectors, weights):
    """One panel per quadrant, always all four, sectors ranked by momentum.

    Every panel renders even when empty: a missing quadrant is information (no
    sector is improving), and dropping the panel would silently reflow the other
    three into its space as though the grid were simply smaller."""
    sectors = sectors or []
    heaviest = max((_weight(weights, s.get("etf")) for s in sectors),
                   default=0.0)
    panels = []
    for name in QUADRANT_ORDER:
        rows = [s for s in sectors if s.get("quadrant") == name]
        rows = sorted(rows, key=lambda s: -(_num(s.get("rs_momentum")) or 0.0))
        panels.append({
            "name": name,
            "blurb": QUAD_BLURB[name],
            "weight": sum(_weight(weights, s.get("etf")) for s in rows),
            "classes": QUAD_CLASSES[name],
            "sectors": [{
                "name": s.get("name"), "etf": s.get("etf"),
                "mom": _num(s.get("rs_momentum")),
                "weight": _weight(weights, s.get("etf")),
                # Bars share ONE scale across all four panels — the heaviest
                # sector on the page. Per-panel scaling would make a 2% sector
                # in an empty quadrant draw the same bar as a 32% one.
                "bar_pct": (_weight(weights, s.get("etf")) / heaviest * 100.0
                            if heaviest > 0 else 0.0),
            } for s in rows],
        })
    return panels
