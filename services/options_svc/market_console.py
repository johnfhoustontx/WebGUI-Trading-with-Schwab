"""The Market Regime Console, rendered as static HTML for the pushed snapshot.

This is a MIRROR of the Tier-1 console that ``/sentiment`` renders — the same
information design, the same hierarchy, the same numbers in the same places —
built as plain HTML/SVG strings so ``briefing_image.render_html_png`` can turn it
into the 30-minute phone image.

WHY MIRROR RATHER THAN IMPORT (the recurring question, answered once)
--------------------------------------------------------------------
``webgui/pages/console*.py`` is Tier 1. It imports ``nicegui`` and paints into a
live container; this module is Tier 2 and returns strings. There is no version of
"just import the page" that works. So the two are kept in step by CONTRACT, and
the contract is this file: every constant and every pure decision below names the
Tier-1 function it mirrors, so a future change on either side has a stated
counterpart to look for. That pattern already existed for the old ring geometry
(``RING_START_DEG``/``RING_RADII`` in ``market_snapshot``, deleted here along
with the rings themselves); this widens it from three numbers to the whole
console, because the whole console is what drifted.

The Tier-1 modules mirrored here, and what each contributes:

  ``pages/console.py``        score bands, meter rows, segmented + bipolar meters,
                              diagnostic-tag splitting
  ``pages/console_cards.py``  hero/delta/pill parts, tone colours, divergence bars
  ``pages/console_regime.py`` regime colours, per-row sparkline, change text
  ``pages/regime_mix.py``     session slicing, ranked rows, callouts, lead margin
  ``pages/console_dial.py``   the confidence dial
  ``pages/console_page.py``   header chips, assembly order, footer summary
  ``config/theme.toml`` ``[console]``   the palette (``PALETTE`` below)

WHAT DELIBERATELY DID NOT COME ACROSS
-------------------------------------
The push is a glanceable image, not a page, so anything whose meaning is an
INTERACTION is dropped rather than faked:

  * the "COMPONENTS →" and "TREND DETAIL →" links (they open press-and-hold
    popups that a PNG has no way to offer);
  * the header dot's ``con-pulse`` animation (a still frame of a pulse is just a
    dot, so it is drawn as a static dot with the same glow);
  * the busy/refresh overlay and the waiting states tied to a live cache poll.

The console's own numbers are all present. The intraday sparkline the OLD push
carried is gone with them: it was never part of the console (on ``/sentiment`` it
lives further down the page, in the "Daily Sentiment & Trend" charts), and the
Day/Week/Month meters plus the vs-WEEK / vs-MONTH deltas carry that story here.

The ONE thing here that the console does NOT have is :func:`transition_line` —
see its docstring for why a still image earns it and a live page does not.

Pure: every function takes plain dicts/lists and returns a string. The one
exception is ``session_chip``, which asks ``shared.market_calendar`` what session
it is — the same single source of truth Tier 1 asks, guarded so it can never
raise, and injectable for tests.
"""
import html as _html
import math

# ── palette ─────────────────────────────────────────────────────────────────
# MIRRORS config/theme.toml [console]. Hard-coded rather than read, because this
# module is Tier 2 and ``market_snapshot`` is documented as reading nothing —
# and because a push that silently restyled itself when someone edited the
# webgui's theme file would be worse than one that stays put until edited here.
PALETTE = {
    "page_bg": "#05070b",
    "wash": "#0b1620",
    "card_from": "#0e161e",
    "card_to": "#070a0f",
    "cell_bg": "#0a0e14",
    "line": "#788ca0",
    "accent": "#22e3d3",
    "text_primary": "#e7edf3",
    "text_secondary": "#a9bac7",
    "text_muted": "#8fa1b0",
    "text_label": "#6b7d8d",
    "text_dim": "#5d6f7e",
    "text_faint": "#4b5a67",
    "positive": "#35d68a",
    "negative": "#f2646b",
    "warning": "#e0b74e",
    "olive": "#b9cf6a",
    "yellow": "#d7d76a",
    "regime_mean_reversion": "#6f86ff",
    "regime_trending": "#35d68a",
    "regime_breakout": "#f0b83c",
    "regime_breakout_zero": "#6a5c33",
    "regime_choppy": "#c3ccd6",
    "regime_crisis": "#f2646b",
}
_P = PALETTE

# MIRRORS theme.CONSOLE_ALPHA.
ALPHA = {"hairline": 0.18, "border": 0.2, "track": 0.09, "track_border": 0.14,
         "rule": 0.18, "card": 0.95}

# The console's display face. Tier 1 loads Rajdhani from Google Fonts; this
# renderer must NOT, because a scheduled push runs inside a subprocess timeout
# and a render-blocking <link> to fonts.googleapis.com turns one DNS hiccup into
# a snapshot that degrades to a TEXT caption. Measured (headless, this machine,
# "TRENDING" at 40px/4px tracking): remote Rajdhani 186 css px, local Bahnschrift
# 214, Segoe UI 231 — so Bahnschrift keeps most of the condensed feel with no
# network at all. A locally-installed Rajdhani still wins if present.
#
# SINGLE quotes are load-bearing: this string is also interpolated into an SVG
# `font-family="…"` ATTRIBUTE, and a double quote inside would terminate it
# early — which it did, silently, until the first render showed the dial's
# regime name in the default face.
DISPLAY_FONT = ("Rajdhani,Bahnschrift,'Segoe UI Semibold','Segoe UI',"
                "system-ui,sans-serif")


def _alpha_hex(value, alpha):
    """'#788ca0' + 0.2 -> '#788ca033'. MIRRORS theme._alpha_hex."""
    a = max(0, min(255, int(round(float(alpha) * 255))))
    return f"{value}{a:02x}"


def _hex_rgb(value):
    h = str(value).lstrip("#")
    if len(h) != 6:
        return 255, 255, 255
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return 255, 255, 255


def _mix(value, other, t):
    """MIRRORS console._mix."""
    a, b = _hex_rgb(value), _hex_rgb(other)
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _glow(value, px=16, alpha=0.45):
    """MIRRORS theme.console_glow, as a CSS declaration rather than a class."""
    r, g, b = _hex_rgb(value)
    return f"box-shadow:0 0 {px}px rgba({r},{g},{b},{alpha})"


def _safe(v):
    """Finite float, else None. MIRRORS console._safe — None is how every
    primitive here says NO DATA, which is never drawn as a zero."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _e(v):
    return _html.escape(str(v if v is not None else ""))


# ── score bands (MIRRORS console._BANDS / score_band / band_hex) ────────────
_BANDS = ((70.0, "positive"), (65.0, "yellow"), (55.0, "olive"), (35.0, "warning"))


def score_band(value):
    v = _safe(value)
    if v is None:
        return "muted"
    for floor, key in _BANDS:
        if v >= floor:
            return key
    return "negative"


def band_hex(key):
    return {"positive": _P["positive"], "yellow": _P["yellow"],
            "olive": _P["olive"], "warning": _P["warning"],
            "negative": _P["negative"], "muted": _P["text_dim"]}.get(
                key, _P["text_dim"])


def marker_hex(value):
    """MIRRORS console.marker_hex — a near-white tint of the series colour."""
    return _mix(value, "#ffffff", 0.8)


# ── hero / delta (MIRRORS console_cards.hero_parts / delta_parts) ───────────
def hero_parts(value):
    v = _safe(value)
    if v is None:
        return "—", _P["text_dim"]
    return f"{v:.0f}", band_hex(score_band(v))


def delta_parts(day, other, label):
    """``(arrow, text, hex)`` for "▲ +13 vs WEEK", or None when either side is
    missing."""
    a, b = _safe(day), _safe(other)
    if a is None or b is None:
        return None
    d = a - b
    if d >= 0:
        return "▲", f"+{d:.0f} vs {label}", _P["positive"]
    return "▼", f"−{abs(d):.0f} vs {label}", _P["warning"]


# ── timeframe meter (MIRRORS console.meter_row + mount_timeframe_meter) ─────
RULER_MARKS = (0, 25, 50, 75, 100)
_FILL_ALPHA = 0.22


def meter_row(label, value):
    """Everything one Day/Week/Month row needs.

    ``value is None`` yields ``no_read``, drawn as a hatched track reading
    "NO READ" — never a fabricated neutral. That is the same honesty rule the
    rings this replaces followed, and it is why ``_trend_arc_value`` keys on
    confidence rather than key presence."""
    v = _safe(value)
    if v is None:
        return {"label": str(label), "value": None, "text": "—",
                "no_read": True, "band": "muted", "hex": _P["text_dim"],
                "pct": 0.0}
    v = _clamp(v, 0.0, 100.0)
    band = score_band(v)
    return {"label": str(label), "value": v, "text": f"{v:.0f}",
            "no_read": False, "band": band, "hex": band_hex(band), "pct": v}


def _meter_html(row):
    if row["no_read"]:
        inner = ('<i class="cn-hatch"></i>'
                 '<span class="cn-noread">NO READ</span>')
    else:
        hexv = row["hex"]
        glow = (f";{_glow(hexv, px=16, alpha=0.45)}"
                if row["band"] == "positive" else "")
        inner = (
            f'<i class="cn-fill" style="width:{row["pct"]:.1f}%;'
            f'background:linear-gradient(90deg,{_alpha_hex(hexv, _FILL_ALPHA)},'
            f'{hexv}){glow}"></i>'
            f'<i class="cn-mark" style="left:{row["pct"]:.1f}%;'
            f'background:{marker_hex(hexv)}"></i>')
    return (f'<div class="cn-meter">'
            f'<span class="cn-meter-l">{_e(row["label"])}</span>'
            f'<span class="cn-track">{inner}</span>'
            f'<span class="cn-meter-v" style="color:{row["hex"]}">'
            f'{_e(row["text"])}</span></div>')


def _meters_html(arcs):
    rows = [meter_row((a or {}).get("caption", ""), (a or {}).get("value"))
            for a in (arcs or [])]
    ticks = "".join(f"<span>{m}</span>" for m in RULER_MARKS)
    return ('<div class="cn-meters">'
            + "".join(_meter_html(r) for r in rows)
            + f'<div class="cn-ruler"><span class="cn-meter-l"></span>'
              f'<span class="cn-ruler-b">{ticks}</span>'
              f'<span class="cn-meter-v"></span></div></div>')


# ── segmented confidence meter (MIRRORS console.segmented_cells) ────────────
SEGMENTS = 10


def segmented_cells(confidence, n=SEGMENTS):
    c = _safe(confidence)
    if c is None:
        return [False] * n
    # Half-UP, not round() — see the Tier-1 note on banker's rounding.
    lit = min(n, int(_clamp(c, 0.0, 1.0) * n + 0.5))
    return [i < lit for i in range(n)]


def _segmented_html(confidence, hexv):
    cells = []
    for lit in segmented_cells(confidence):
        if lit:
            cells.append(f'<i style="background:{hexv};'
                         f'{_glow(hexv, px=12, alpha=0.35)}"></i>')
        else:
            cells.append(f'<i style="background:'
                         f'{_alpha_hex(_P["line"], ALPHA["hairline"])}"></i>')
    return f'<div class="cn-seg">{"".join(cells)}</div>'


# ── bipolar ROC / z meter (MIRRORS console.bipolar_geometry) ────────────────
BIPOLAR_SCALE = 2.5


def bipolar_geometry(value, scale=BIPOLAR_SCALE):
    v = _safe(value)
    if v is None:
        return {"side": "none", "pct": 0.0, "text": "—",
                "hex": _P["text_dim"], "value": None}
    sc = _safe(scale) or BIPOLAR_SCALE
    if sc <= 0:
        sc = BIPOLAR_SCALE
    pct = _clamp(abs(v) / sc, 0.0, 1.0) * 50.0
    return {"side": "right" if v >= 0 else "left", "pct": pct,
            "text": f"{v:+.2f}",
            "hex": _P["positive"] if v >= 0 else _P["negative"], "value": v}


def _bipolar_html(label, geom):
    fill = ""
    if geom["side"] != "none" and geom["pct"] > 0:
        edge = "left:50%" if geom["side"] == "right" else "right:50%"
        fill = (f'<i class="cn-bi-fill" style="{edge};width:{geom["pct"]:.1f}%;'
                f'background:{geom["hex"]};'
                f'{_glow(geom["hex"], px=10, alpha=0.4)}"></i>')
    return (f'<div class="cn-bi">'
            f'<span class="cn-bi-l">{_e(label)}</span>'
            f'<span class="cn-bi-track"><i class="cn-bi-zero"></i>{fill}</span>'
            f'<span class="cn-bi-v" style="color:{geom["hex"]}">'
            f'{_e(geom["text"])}</span></div>')


# ── Signals tones (MIRRORS console_cards.tone_hex / cell_tint) ──────────────
def tone_hex(tone):
    return {"pos": _P["positive"], "neg": _P["negative"],
            "warn": _P["warning"], "flat": _P["text_muted"]}.get(
                tone, _P["text_muted"])


def cell_tint(hexv):
    return _mix("#080c11", hexv, 0.10)


# The four Signals cells. MIRRORS sentiment.SIGNAL_TILE_DEFS (label +
# descriptor); the page's Material icons are dropped — the push has no icon font.
SIGNAL_DEFS = (
    ("bias", "BIAS", "MARKET DIRECTION"),
    ("signal", "SIGNAL", "STRENGTH & MOMENTUM"),
    ("yesterday", "YESTERDAY", "PREVIOUS CLOSE"),
    ("change", "CHANGE", "VS YESTERDAY"),
)

# MIRRORS sentiment._WORD_TONE.
_WORD_TONE = {"long": "pos", "bullish": "pos", "strong bull": "pos",
              "neutral": "warn", "cautious": "warn",
              "short": "neg", "bearish": "neg", "strong bear": "neg"}


def word_tone(word):
    """MIRRORS sentiment._word_tone — colour a BIAS/SIGNAL cell from its OWN
    word, so the colour can never contradict the text beside it."""
    w = str(word or "").strip().lower()
    if not w or w == "—":
        return "flat"
    if w in _WORD_TONE:
        return _WORD_TONE[w]
    if "bull" in w or "long" in w:
        return "pos"
    if "bear" in w or "short" in w:
        return "neg"
    return "warn"


def band_tone(total):
    """MIRRORS sentiment._band_tone (>=6.5 pos / <=4.5 neg / else warn) over the
    0-10 composite."""
    v = _safe(total)
    if v is None:
        return "flat"
    if v >= 6.5:
        return "pos"
    if v <= 4.5:
        return "neg"
    return "warn"


def change_tone(change):
    """MIRRORS sentiment._change_tone."""
    try:
        v = float(str(change).replace("+", "").replace("−", "-"))
    except (TypeError, ValueError):
        return "flat"
    return "pos" if v > 0 else ("neg" if v < 0 else "flat")


def signal_rows(bias, signal, total, prev_total):
    """The 2x2 readout, MIRRORING ``sentiment.tiles`` + ``signal_tile_rows``.

    ``total``/``prev_total`` are the 0-10 composite for today and the prior
    scored session; YESTERDAY and CHANGE read an em-dash (tone ``flat``) when
    there is no prior session rather than inventing a band for it."""
    t, p = _safe(total), _safe(prev_total)
    if p is None:
        yest, chg = "—", "—"
    else:
        yest = f"{p:.2f}"
        chg = "—" if t is None else f"{t - p:+.2f}"
    values = {"bias": str(bias or "—"), "signal": str(signal or "—"),
              "yesterday": yest, "change": chg}
    tones = {"bias": word_tone(bias), "signal": word_tone(signal),
             "yesterday": "flat" if p is None else band_tone(p),
             "change": change_tone(chg)}
    return [{"key": k, "label": lab, "descriptor": desc,
             "value": values[k], "tone": tones[k]}
            for k, lab, desc in SIGNAL_DEFS]


# ── divergence (MIRRORS console_cards.divergence_bars / divergence_text) ────
DIVERGENCE_BAR_H = 30


def divergence_bars(detail):
    d = detail if isinstance(detail, dict) else {}
    hi, lo = d.get("high"), d.get("low")
    if not isinstance(hi, dict) or not isinstance(lo, dict):
        return []
    out = []
    for side, opacity in ((hi, 0.85), (lo, 0.45)):
        score = _safe(side.get("score"))
        if score is None:
            return []
        h = max(2, round(_clamp(score, 0.0, 10.0) / 10.0 * DIVERGENCE_BAR_H))
        out.append((str(side.get("name") or ""), score, h, opacity))
    return out


def divergence_text(detail):
    bars = divergence_bars(detail)
    if not bars:
        return ""
    return f"{bars[0][0]} {bars[0][1]:.0f} vs {bars[1][0]} {bars[1][1]:.0f}"


# ── regime ranking (MIRRORS regime_mix.py) ──────────────────────────────────
REGIME_ORDER = ("mean_reversion", "trending", "breakout", "choppy", "crisis")
# MIRRORS scoring/market_regime.REGIME_DISPLAY (via regime_mix.REGIME_LABELS).
REGIME_LABELS = {"mean_reversion": "Balanced", "trending": "Trending",
                 "breakout": "Breakout", "choppy": "Whipsaw",
                 "crisis": "Stressed"}
REGIME_NOTES = {"mean_reversion": "TWO-SIDED FLOW",
                "trending": "DIRECTIONAL PERSIST",
                "breakout": "RANGE EXPANSION",
                "choppy": "CHOP · NO EDGE",
                "crisis": "VOL EXPANSION"}
ZERO_NOTE = "DORMANT"
_EMERGING_FLOOR = 0.005
SESSION_GAP_SEC = 4 * 60 * 60
_FLAT_EPS = 1e-6

# Direction words. MIRRORS scoring/market_regime — the pushed image must never
# print a raw contract key, which is the bug the rename exists to prevent.
_REGIME_DIRECTIONAL = {
    "trending": {(1, True): "Rallying", (1, False): "Firming",
                 (-1, True): "Retreating", (-1, False): "Softening"},
    "breakout": {(-1, True): "Breakdown", (-1, False): "Breakdown"},
}


def regime_label(key, direction=0, strong=False):
    key = str(key)
    base = REGIME_LABELS.get(key, key)
    words = _REGIME_DIRECTIONAL.get(key)
    if not words or direction not in (-1, 1) or isinstance(direction, bool):
        return base
    return words.get((direction, bool(strong)), base)


def regime_hex(key, share=None):
    """MIRRORS console_regime.regime_hex — muted when the band holds nothing."""
    s = _safe(share)
    if s is not None and s <= 0.0:
        return _P["regime_breakout_zero"]
    return _P.get(f"regime_{key}", _P["text_muted"])


def _frac(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, min(1.0, f))


def _memberships(point):
    if not isinstance(point, dict):
        return {}
    m = point.get("memberships")
    return m if isinstance(m, dict) else {}


def session_points(points):
    """The trailing run belonging to the CURRENT session. MIRRORS
    regime_mix.session_points, so "change vs open" means THIS session's open."""
    pts = [p for p in (points or []) if isinstance(p, dict)]
    start, prev = 0, None
    for i, p in enumerate(pts):
        try:
            ts = float(p.get("ts"))
        except (TypeError, ValueError):
            ts = None
        if ts is not None and prev is not None and (ts - prev) > SESSION_GAP_SEC:
            start = i
        if ts is not None:
            prev = ts
    return pts[start:]


def rank_rows(points):
    """MIRRORS regime_mix.rank_rows — richest first, ties on REGIME_ORDER."""
    pts = session_points(points)
    rows = []
    for key in REGIME_ORDER:
        ser = [_frac(_memberships(p).get(key)) for p in pts]
        now = ser[-1] if ser else 0.0
        change = (ser[-1] - ser[0]) if len(ser) >= 2 else 0.0
        span = (max(ser) - min(ser)) if ser else 0.0
        rows.append({"key": key, "label": REGIME_LABELS[key], "now": now,
                     "change": change, "series": ser, "flat": span < _FLAT_EPS})
    rows.sort(key=lambda r: (-r["now"], REGIME_ORDER.index(r["key"])))
    return rows


def regime_note(row):
    if not isinstance(row, dict):
        return ""
    if _frac(row.get("now")) <= 0.0:
        return ZERO_NOTE
    return REGIME_NOTES.get(row.get("key"), "")


def lead_margin(points):
    """``(leader_key, margin_now, margin_min)``. MIRRORS regime_mix.lead_margin.

    ``margin_min`` is the tightest the top two got today — the number that says
    whether the committed headline was ever a coin toss, which ``unclear`` (an
    evidence-strength measure) does not cover."""
    pts = session_points(points)
    if not pts:
        return None, None, None
    mins = []
    for p in pts:
        vals = sorted((_frac(_memberships(p).get(k)) for k in REGIME_ORDER),
                      reverse=True)
        mins.append(vals[0] - vals[1])
    rows = rank_rows(points)
    return rows[0]["key"], (rows[0]["now"] - rows[1]["now"]), min(mins)


def callouts(points):
    """MIRRORS regime_mix.callouts — DOMINANT / BIGGEST MOVE / EMERGING."""
    rows = rank_rows(points)
    if not rows or not session_points(points):
        return {"dominant": None, "biggest_move": None, "emerging": None}
    dominant = rows[0]
    biggest = max(rows, key=lambda r: abs(r["change"]))
    risers = [r for r in rows if r["change"] > 0]
    from_zero = [r for r in risers
                 if (r["series"][0] if r["series"] else 0.0) <= _EMERGING_FLOOR]
    if from_zero:
        emerging = max(from_zero, key=lambda r: r["change"])
    else:
        others = [r for r in risers if r["key"] != biggest["key"]]
        emerging = max(others, key=lambda r: r["change"]) if others else None
    return {"dominant": dominant, "biggest_move": biggest, "emerging": emerging}


def change_text(row):
    """MIRRORS console_regime.change_text."""
    if not isinstance(row, dict):
        return "—", _P["text_dim"]
    if row.get("flat"):
        return "—", _P["text_dim"]
    change = _safe(row.get("change")) or 0.0
    if change >= 0:
        return f"+{change * 100:.1f}pp", _P["positive"]
    return f"−{abs(change) * 100:.1f}pp", _P["negative"]


SPARK_W, SPARK_H = 132, 34


def sparkline_svg(series, color, dashed=False, width=SPARK_W, height=SPARK_H):
    """One share row's sparkline, scaled to its OWN range. MIRRORS
    console_regime.sparkline_svg — a dead-flat series draws a dashed rule rather
    than amplifying floating-point dust into a convincing squiggle."""
    pts = [_safe(v) or 0.0 for v in (series or [])]
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'preserveAspectRatio="none">')
    lo, hi = (min(pts), max(pts)) if pts else (0.0, 0.0)
    if dashed or len(pts) < 2 or (hi - lo) < 1e-9:
        mid = height / 2.0
        return (head + f'<path d="M 0 {mid} L {width} {mid}" fill="none" '
                f'stroke="{color}" stroke-width="1.6" opacity="0.45" '
                f'stroke-dasharray="3 4"/></svg>')
    dx = width / (len(pts) - 1)
    coords = " ".join(
        f"{i * dx:.1f},{height - (v - lo) / (hi - lo) * height:.1f}"
        for i, v in enumerate(pts))
    return (head + f'<polyline points="{coords}" fill="none" '
            f'stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>'
            f'</svg>')


# ── the confidence dial (MIRRORS console_dial.py) ───────────────────────────
DIAL_VIEWBOX = 244
DIAL_CX = DIAL_CY = 122.0
DIAL_R_OUTER, DIAL_R_TRACK, DIAL_R_INNER = 104.0, 92.0, 74.0
DIAL_STROKE = 16.0
DIAL_HALO_EXTRA, DIAL_HALO_OPACITY = 9.0, 0.22
BASELINE_DY = "0.35em"
# A 360-degree arc's endpoints COINCIDE and an SVG arc between identical points
# draws NOTHING, so a confidence of 1.0 would render an EMPTY ring — the single
# most misleading thing this dial could do. Past this it becomes a <circle>.
FULL_CIRCLE_EPS = 0.999
DIAL_NAME_Y, DIAL_NAME_SIZE = 100.0, 40
DIAL_VALUE_Y, DIAL_VALUE_SIZE = 140.0, 46
DIAL_CAPTION_Y, DIAL_CAPTION_SIZE = 172.0, 9.5


def _dial_point(cx, cy, r, deg):
    rad = math.radians(deg - 90.0)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _dial_arc(cx, cy, r, d0, d1):
    sweep = d1 - d0
    if sweep < 0.5:
        return ""
    x0, y0 = _dial_point(cx, cy, r, d0)
    x1, y1 = _dial_point(cx, cy, r, d1)
    large = 1 if sweep > 180.0 else 0
    return f"M {x0:.2f} {y0:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}"


def _dial_conf(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return max(0.0, min(1.0, f))


def _dial_ring(r, stroke, width, opacity=None):
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<circle cx="{DIAL_CX}" cy="{DIAL_CY}" r="{r}" fill="none" '
            f'stroke="{stroke}" stroke-width="{width}"{op}/>')


def _dial_text(x, y, body, size, fill, weight=None, spacing=None, family=None):
    extra = ((f' font-weight="{weight}"' if weight else "")
             + (f' letter-spacing="{spacing}"' if spacing else "")
             + (f' font-family="{family}"' if family else ""))
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dy="{BASELINE_DY}" font-size="{size}"{extra} '
            f'fill="{fill}">{body}</text>')


def dial_svg(confidence, name, accent=None):
    """The regime confidence dial. MIRRORS console_dial.dial_svg.

    A missing/junk confidence draws the track and an em-dash rather than a zero
    — "no reading" and "zero confidence" are different statements."""
    conf = _dial_conf(confidence)
    accent = accent or _P["accent"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'viewBox="0 0 {DIAL_VIEWBOX} {DIAL_VIEWBOX}" width="100%">',
             _dial_ring(DIAL_R_OUTER, _alpha_hex(_P["line"], 0.14), 2),
             _dial_ring(DIAL_R_TRACK, _alpha_hex(_P["line"], 0.10), DIAL_STROKE)]
    if conf is not None and conf > 0:
        if conf >= FULL_CIRCLE_EPS:
            parts.append(_dial_ring(DIAL_R_TRACK, accent,
                                    DIAL_STROKE + DIAL_HALO_EXTRA,
                                    DIAL_HALO_OPACITY))
            parts.append(_dial_ring(DIAL_R_TRACK, accent, DIAL_STROKE))
        else:
            d = _dial_arc(DIAL_CX, DIAL_CY, DIAL_R_TRACK, 0.0, 360.0 * conf)
            if d:
                parts.append(f'<path d="{d}" fill="none" stroke="{accent}" '
                             f'stroke-width="{DIAL_STROKE + DIAL_HALO_EXTRA}" '
                             f'opacity="{DIAL_HALO_OPACITY}"/>')
                parts.append(f'<path d="{d}" fill="none" stroke="{accent}" '
                             f'stroke-width="{DIAL_STROKE}"/>')
    parts.append(_dial_ring(DIAL_R_INNER, _alpha_hex(_P["line"], 0.12), 1))
    parts.append(_dial_text(DIAL_CX, DIAL_NAME_Y, _e(str(name or "").upper()),
                            DIAL_NAME_SIZE, _P["text_primary"], weight=700,
                            spacing=4, family=DISPLAY_FONT))
    parts.append(_dial_text(DIAL_CX, DIAL_VALUE_Y,
                            "—" if conf is None else f"{conf * 100:.0f}%",
                            DIAL_VALUE_SIZE,
                            accent if conf is not None else _P["text_dim"],
                            weight=600))
    parts.append(_dial_text(DIAL_CX, DIAL_CAPTION_Y, "CONFIDENCE",
                            DIAL_CAPTION_SIZE, _P["text_label"], spacing=2))
    parts.append("</svg>")
    return "".join(parts)


# ── diagnostic tags (MIRRORS console.split_tag / chip_classes) ──────────────
def split_tag(text):
    """``"Balanced profile 0.53"`` -> ``("BALANCED PROFILE", "0.53")``."""
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


def _chip_html(text, severity="info"):
    hexv = _P["negative"] if severity == "warn" else _P["accent"]
    label, value = split_tag(text)
    body = f'<span>{_e(label)}</span>'
    if value:
        body += f'<b style="color:{hexv}">{_e(value)}</b>'
    return (f'<span class="cn-chip" style="border-color:{_alpha_hex(hexv, 0.34)};'
            f'background:{_alpha_hex(hexv, 0.12)};'
            f'color:{_mix(hexv, "#ffffff", 0.72)}">{body}</span>')


def evidence_detail(regime):
    """MIRRORS console_page._evidence — Tier 2's structured evidence, falling
    back to the flat list for a payload published before it existed."""
    r = regime if isinstance(regime, dict) else {}
    detail = r.get("evidence_detail")
    if isinstance(detail, list) and detail:
        return [d for d in detail if isinstance(d, dict)]
    return [{"text": str(t), "regime": "", "severity": "info"}
            for t in (r.get("evidence") or [])]


# ── header chips + footer (MIRRORS console_page) ────────────────────────────
STALE_AFTER_SEC = 420


def as_of_parts(composite_at, now=None):
    """``(text, stale)`` for the DATA AS OF chip. MIRRORS console_page.

    Worth surfacing in a push for the same reason it is on the page: every
    number here comes from one cache, and a stalled publisher would otherwise
    render a confident image of an old snapshot with nothing saying so."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = _parse_iso(composite_at)
    if stamp is None:
        return "NO DATA", True
    age = (now - stamp).total_seconds()
    local = stamp.astimezone(ZoneInfo("America/Chicago"))
    return (f"{local:%H:%M} CT · {'STALE' if age > STALE_AFTER_SEC else 'LIVE'}",
            age > STALE_AFTER_SEC)


def _parse_iso(value):
    import datetime as _dt
    if not value:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def session_chip(now=None):
    """"US EQUITIES · RTH" / "· EXT" / "· CLOSED". MIRRORS
    console_page.session_label, over the SAME ``shared.market_calendar``.

    The one impure call in this module. Guarded: a chip must never break the
    push, and ``now`` is injectable so tests stay deterministic."""
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo

        from shared import market_calendar as cal
        now = now or _dt.datetime.now(ZoneInfo("America/Chicago"))
        if not cal.is_trading_day(now.date()):
            return "US EQUITIES · CLOSED"
        if cal.is_regular_hours(now):
            return "US EQUITIES · RTH"
        if cal.is_extended_hours(now):
            return "US EQUITIES · EXT"
    except Exception:  # noqa: BLE001
        return "US EQUITIES"
    return "US EQUITIES · CLOSED"


def footer_summary(points):
    """MIRRORS console_page.footer_summary — leader, margin, and any band
    sitting dormant."""
    rows = rank_rows(points) if session_points(points) else []
    if not rows:
        return "Waiting for the regime classifier"
    _key, margin, tightest = lead_margin(points)
    parts = []
    if len(rows) > 1 and margin is not None:
        parts.append(f"{rows[0]['label']} leads {rows[1]['label']} "
                     f"by {margin * 100:.1f} pp")
    if tightest is not None:
        parts.append(f"tightest spread today {tightest * 100:.1f} pp")
    dormant = [r["label"].lower() for r in rows if r["now"] <= 0.0]
    if dormant:
        parts.append(f"{', '.join(dormant)} dormant")
    return " · ".join(parts)


def _chip_box(label, value, hexv, accent=True):
    lab_col = hexv if accent else _P["text_dim"]
    val_col = hexv if accent else _P["text_primary"]
    return (f'<div class="cn-chipbox" style="border-color:{_alpha_hex(hexv, 0.35)};'
            f'background:{_alpha_hex(hexv, 0.08)}">'
            f'<span class="cn-chipbox-l" style="color:{lab_col}">{_e(label)}</span>'
            f'<span class="cn-chipbox-v" style="color:{val_col}">{_e(value)}</span>'
            f'</div>')


# ── cards ───────────────────────────────────────────────────────────────────
def _card_head(title, meta, meta_color=None):
    meta_html = ""
    if meta:
        col = f";color:{meta_color}" if meta_color else ""
        meta_html = f'<span class="cn-card-meta" style="{col.lstrip(";")}">{_e(meta)}</span>'
    return (f'<div class="cn-card-h"><span class="cn-card-t">{_e(title)}</span>'
            f'{meta_html}</div>')


def _hero(value, pill_text, pill_hex, kicker, delta):
    text, hexv = hero_parts(value)
    pill = ""
    if pill_text:
        ph = pill_hex or hexv
        pill = (f'<span class="cn-pill" style="border-color:{_alpha_hex(ph, 0.45)};'
                f'background:{_alpha_hex(ph, 0.16)};color:{ph}">'
                f'{_e(pill_text)}</span>')
    dtxt = ""
    if delta:
        arrow, body, dhex = delta
        dtxt = (f'<span class="cn-delta" style="color:{dhex}">'
                f'{_e(arrow)} {_e(body)}</span>')
    return (f'<div class="cn-hero">'
            f'<span class="cn-hero-v" style="color:{hexv}">{_e(text)}</span>'
            f'<span class="cn-hero-r"><span class="cn-kicker">{_e(kicker)}</span>'
            f'<span class="cn-hero-row">{pill}{dtxt}</span></span></div>')


def sentiment_card_html(arcs, bias, total, confidence):
    """MIRRORS console_cards.render_sentiment_card.

    The scale bug this fixes: the old push put the 0-10 composite ("5.0") in the
    ring centre while its own WEEK/MONTH legend read 0-100 (57 / 53). Here the
    hero, the meters and the ruler are all 0-100, and the 0-10 composite appears
    ONLY inside the bias pill ("NEUTRAL 5.04") — exactly as the console does it,
    so the two scales are never mistaken for one."""
    arcs = list(arcs or [])
    day = arcs[0].get("value") if arcs else None
    week = arcs[1].get("value") if len(arcs) > 1 else None
    _t, hero_hex = hero_parts(day)
    conf = _safe(confidence)
    pill = f"{str(bias or '').upper()} {total}".strip() if bias else ""
    return (
        '<div class="cn-card">'
        + _card_head("MARKET SENTIMENT", "SCALE 0—100")
        + _hero(day, pill, hero_hex, "DAY READ", delta_parts(day, week, "WEEK"))
        + _meters_html(arcs)
        + '<div class="cn-foot">'
          '<div class="cn-foot-h"><span>MODEL CONFIDENCE</span>'
          f'<span style="color:{hero_hex}">'
          f'{"—" if conf is None else f"{conf * 100:.0f}%"}</span></div>'
        + _segmented_html(conf, hero_hex)
        + '</div></div>')


def trend_card_html(arcs, short_state, verdict, guidance):
    """MIRRORS console_cards.render_trend_card."""
    arcs = list(arcs or [])
    day = arcs[0].get("value") if arcs else None
    month = arcs[2].get("value") if len(arcs) > 2 else None
    _t, hero_hex = hero_parts(day)
    block = ""
    if verdict or guidance:
        inner = ""
        if verdict:
            inner += (f'<span class="cn-verdict" style="color:{hero_hex}">'
                      f'{_e(str(verdict).upper())}</span>')
        if guidance:
            inner += f'<span class="cn-guidance">{_e(guidance)}</span>'
        block = (f'<div class="cn-verdict-box" style="border-left-color:{hero_hex};'
                 f'background:linear-gradient(90deg,'
                 f'{_alpha_hex(hero_hex, 0.14)},transparent)">{inner}</div>')
    return (
        '<div class="cn-card">'
        + _card_head("MARKET TREND", "SCALE 0—100")
        + _hero(day, str(short_state or "").upper(), hero_hex, "DAY READ",
                delta_parts(day, month, "MONTH"))
        + _meters_html(arcs)
        + f'<div class="cn-foot">{block}</div></div>')


def signals_card_html(rows, velocity_values, divergence_detail):
    """MIRRORS console_cards.render_signals_card — the 2x2 matrix, the three
    signed ROC/z meters, and the divergence alert.

    The page's meta reads "N READS · LIVE"; a still frame drops the LIVE (the
    DATA AS OF chip in the header is where freshness belongs in an image)."""
    rows = list(rows or [])
    reads = sum(1 for r in rows if str(r.get("value", "—")) != "—")
    cells = []
    for r in rows:
        hexv = tone_hex(r.get("tone"))
        cells.append(
            f'<div class="cn-sig" style="background:{cell_tint(hexv)}">'
            f'<span class="cn-sig-l">{_e(r.get("label", ""))}</span>'
            f'<span class="cn-sig-v" style="color:{hexv}">'
            f'{_e(r.get("value", "—"))}</span>'
            f'<span class="cn-sig-d">{_e(r.get("descriptor", ""))}</span></div>')
    vals = velocity_values if isinstance(velocity_values, dict) else {}
    meters = "".join(
        _bipolar_html(label, bipolar_geometry(vals.get(key)))
        for label, key in (("3D ROC", "roc_3d"), ("5D ROC", "roc_5d"),
                           ("20D Z", "z_20d")))
    bars = divergence_bars(divergence_detail)
    alert = ""
    if bars:
        neg = _P["negative"]
        bar_html = "".join(
            f'<i style="height:{h}px;background:{neg};opacity:{op}"></i>'
            for _n, _s, h, op in bars)
        alert = (f'<div class="cn-diverge" style="border-color:{_alpha_hex(neg, 0.3)};'
                 f'background:{_alpha_hex(neg, 0.10)}">'
                 f'<span class="cn-diverge-t">'
                 f'<span class="cn-diverge-h" style="color:{neg}">'
                 f'DIVERGENCE · LOW CONVICTION</span>'
                 f'<span class="cn-diverge-b" '
                 f'style="color:{_mix(neg, "#ffffff", 0.6)}">'
                 f'{_e(divergence_text(divergence_detail))}</span></span>'
                 f'<span class="cn-diverge-bars">{bar_html}</span></div>')
    return (
        '<div class="cn-card cn-card-sig">'
        + _card_head("SIGNALS", f"{reads} READS", _P["accent"])
        + f'<div class="cn-siggrid">{"".join(cells)}</div>'
        + '<div class="cn-roc"><span class="cn-roc-h">'
          'RATE OF CHANGE · Z-SCORE</span>'
        + meters + '</div>'
        + f'<div class="cn-foot">{alert}</div></div>')


# ── regime block ────────────────────────────────────────────────────────────
def transition_line(regime):
    """"Balanced → Rallying · 60%", or "" when no flip is in progress.

    An ADDITION to the mirrored design, and the one place this file deliberately
    carries something the console does not: ``/sentiment`` dropped the transition
    line, but it can be re-read at any moment, while a 30-minute image is a
    single glance — "the regime is mid-flip" is worth its one line there.

    It renders DISPLAY words, never the raw contract keys. A
    "mean_reversion → trending" reaching a phone is exactly the bug the regime
    rename exists to prevent, and it is what this line used to say."""
    r = regime if isinstance(regime, dict) else {}
    tr = r.get("transition")
    if not isinstance(tr, dict) or not tr.get("to"):
        return ""
    d = r.get("direction")
    d = d if d in (-1, 1) and not isinstance(d, bool) else 0
    strong = r.get("direction_strong") is True
    prog = _safe(tr.get("progress"))
    tail = f" · {prog * 100:.0f}%" if prog is not None else ""
    return (f'{regime_label(tr.get("from", ""), d, strong)} → '
            f'{regime_label(tr.get("to", ""), d, strong)}{tail}')


def dial_card_html(regime, points):
    """MIRRORS console_regime.render_dial_card (+ the transition line above)."""
    r = regime if isinstance(regime, dict) else {}
    rows = rank_rows(points)
    _key, margin, tightest = lead_margin(points)
    name = r.get("label") or REGIME_LABELS.get(r.get("committed_label")) or "Unclear"
    runner = rows[1]["label"] if len(rows) > 1 else "—"
    conf = None if r.get("unclear") else r.get("confidence")
    trans = transition_line(r)
    trans_html = (f'<span class="cn-trans">TRANSITION · {_e(trans)}</span>'
                  if trans else "")
    return (
        '<div class="cn-card cn-dialcard">'
        '<span class="cn-eyebrow">REGIME IDENTIFIED</span>'
        f'<div class="cn-dial">{dial_svg(conf, name)}</div>'
        '<div class="cn-statgrid">'
        + _stat_html("LEAD",
                     "—" if margin is None else f"+{margin * 100:.1f} pp",
                     f"over {runner}")
        + _stat_html("TIGHTEST TODAY",
                     "—" if tightest is None else f"{tightest * 100:.1f} pp",
                     "intraday minimum")
        + '</div>' + trans_html + '</div>')


def _stat_html(label, value, note):
    return (f'<div class="cn-stat"><span class="cn-stat-l">{_e(label)}</span>'
            f'<span class="cn-stat-v">{_e(value)}</span>'
            f'<span class="cn-stat-n">{_e(note)}</span></div>')


def tags_card_html(tags):
    """MIRRORS console_regime.render_tags_card."""
    tags = [t for t in (tags or []) if isinstance(t, dict)]
    body = ('<span class="cn-empty">No active tags</span>' if not tags
            else '<div class="cn-chips">'
                 + "".join(_chip_html(t.get("text"), t.get("severity", "info"))
                           for t in tags) + '</div>')
    return ('<div class="cn-card cn-tagcard">'
            '<div class="cn-card-h"><span class="cn-eyebrow">DIAGNOSTIC TAGS</span>'
            f'<span class="cn-active" style="color:{_P["accent"]}">'
            f'{len(tags)} ACTIVE</span></div>' + body + '</div>')


def share_table_html(points):
    """MIRRORS console_regime.render_share_table — one ranked row per regime with
    its share bar (normalised to the LEADER, so the rows read as the contest),
    its own-range sparkline, and change vs the session open."""
    rows = rank_rows(points) if session_points(points) else []
    head = ('<div class="cn-share-h">'
            '<span class="cn-share-t">REGIME SHARE</span>'
            '<span class="cn-share-m">BY SHARE · CHANGE VS OPEN</span></div>'
            '<div class="cn-srow cn-srow-head"><span></span>'
            '<span class="cn-col">SHARE</span><span class="cn-col">TODAY</span>'
            '<span class="cn-col cn-right">CHANGE</span></div>')
    if not rows:
        return ('<div class="cn-card cn-sharecard">' + head
                + '<span class="cn-empty">Waiting for regime…</span></div>')
    lead = rows[0]["now"]
    body = []
    for row in rows:
        hexv = regime_hex(row["key"], row["now"])
        zero = row["now"] <= 0.0
        glow = "" if zero else f";{_glow(hexv, px=10, alpha=0.45)}"
        bar = ""
        if not zero and lead > 0:
            bar = (f'<i style="width:{row["now"] / lead * 100:.1f}%;'
                   f'background:{hexv};{_glow(hexv, px=14, alpha=0.45)}"></i>')
        ctext, chex = change_text(row)
        body.append(
            '<div class="cn-srow">'
            f'<span class="cn-sname">'
            f'<i class="cn-swatch" style="background:{hexv}{glow}"></i>'
            f'<span class="cn-sname-t"><b>{_e(row["label"].upper())}</b>'
            f'<em>{_e(regime_note(row))}</em></span></span>'
            f'<span class="cn-sshare">'
            f'<b style="color:{hexv}">{row["now"] * 100:.1f}%</b>'
            f'<i class="cn-sbar">{bar}</i></span>'
            f'<span class="cn-sspark">'
            f'{sparkline_svg(row["series"], hexv, dashed=row["flat"] or zero)}'
            f'</span>'
            f'<span class="cn-schange" style="color:{chex}">{_e(ctext)}</span>'
            '</div>')
    return ('<div class="cn-card cn-sharecard">' + head + "".join(body)
            + '</div>')


def callouts_html(points):
    """MIRRORS console_regime.render_callouts — the day's regime story in three."""
    c = callouts(points)
    _key, margin, _t = lead_margin(points)
    lead_txt = "" if margin is None else f" · leads by {margin * 100:.1f} pp"
    items = [
        ("DOMINANT", c["dominant"],
         "no reading yet" if not c["dominant"]
         else f"{c['dominant']['now'] * 100:.1f}% share{lead_txt}"),
        ("BIGGEST MOVE", c["biggest_move"], _move_note(c["biggest_move"])),
        ("EMERGING", c["emerging"], _emerging_note(c["emerging"])),
    ]
    cells = []
    for kicker, row, note in items:
        col = (regime_hex(row["key"], row["now"]) if row else _P["text_muted"])
        cells.append(
            f'<div class="cn-callout"><span class="cn-co-k">{_e(kicker)}</span>'
            f'<span class="cn-co-v" style="color:{col}">'
            f'{_e((row["label"] if row else "—").upper())}</span>'
            f'<span class="cn-co-n">{_e(note)}</span></div>')
    return f'<div class="cn-callouts">{"".join(cells)}</div>'


def _move_note(row):
    if not row:
        return "—"
    text, _hex = change_text(row)
    return (f"{text} · share rotating out" if row["change"] < 0
            else f"{text} · share building")


def _emerging_note(row):
    if not row:
        return "nothing rising"
    text, _hex = change_text(row)
    opened = row["series"][0] if row["series"] else 0.0
    return (f"{text} from zero · watch" if opened <= _EMERGING_FLOOR
            else f"{text} · building")


# ── assembly (MIRRORS console_page.apply) ───────────────────────────────────
def console_html(ctx):
    """The whole Market Read section, from the same ``ctx`` shape
    ``console_page.apply`` consumes (see ``sentiment._apply``).

    Order is the console's: header · three cards · regime block · footer."""
    ctx = ctx or {}
    as_of_text, stale = as_of_parts(ctx.get("as_of"), ctx.get("now_utc"))
    warn = _P["warning"] if stale else _P["accent"]
    points = ctx.get("regime_points") or []
    header = (
        '<div class="cn-head">'
        '<div class="cn-head-l">'
        f'<i class="cn-dot" style="background:{_P["accent"]};'
        f'{_glow(_P["accent"], px=12, alpha=0.9)}"></i>'
        # "MARKET READ" keeps the section's own name (what the doc has always
        # called it); the eyebrow names the screen it mirrors so a reader can
        # find the same numbers in the app.
        '<span class="cn-title">MARKET READ</span>'
        '<span class="cn-sub">MARKET REGIME CONSOLE · SENTIMENT · TREND · '
        'SIGNALS · REGIME SHARE</span>'
        '</div><div class="cn-head-r">'
        + _chip_box("SESSION", ctx.get("session") or session_chip(ctx.get("now_ct")),
                    _P["line"], accent=False)
        + _chip_box("DATA AS OF", as_of_text, warn)
        + '</div></div>')
    cards = (
        '<div class="cn-cards">'
        + sentiment_card_html(ctx.get("sent_arcs"), ctx.get("bias"),
                              ctx.get("total"), ctx.get("confidence"))
        + trend_card_html(ctx.get("trend_arcs"), ctx.get("trend_short"),
                          ctx.get("trend_verdict"), ctx.get("trend_guidance"))
        + signals_card_html(ctx.get("signal_rows"), ctx.get("velocity_values"),
                            ctx.get("divergence_detail"))
        + '</div>')
    block = (
        '<div class="cn-regime">'
        '<div class="cn-regime-l">'
        + dial_card_html(ctx.get("regime"), points)
        + tags_card_html(evidence_detail(ctx.get("regime")))
        + '</div><div class="cn-regime-r">'
        + share_table_html(points) + callouts_html(points)
        + '</div></div>')
    footer = (f'<div class="cn-footer"><span>{_e(footer_summary(points))}</span>'
              f'<span>FOR INFORMATIONAL PURPOSES ONLY · NOT FINANCIAL ADVICE'
              f'</span></div>')
    return f'<div class="cn">{header}{cards}{block}{footer}</div>'


# ── CSS ─────────────────────────────────────────────────────────────────────
# The console's own measurements, unchanged: it is designed on a 1440px canvas
# and the doc's content box is 1320px, so the px sizes transfer 1:1. Square
# corners throughout — that is the console's language, not a default.
_L = _P["line"]
CONSOLE_CSS = f"""
.cn{{background:{_P['page_bg']};
  background-image:radial-gradient(1200px 700px at 22% 10%,{_P['wash']},{_P['page_bg']} 62%);
  border:1px solid {_alpha_hex(_L, ALPHA['border'])};
  padding:26px;margin-top:14px;display:flex;flex-direction:column;gap:22px;
  color:{_P['text_primary']};line-height:1.5}}
/* Every <span>/<i>/<b> below that needs a box gets its OWN display rule. A
   blanket `.cn span{{display:inline-block}}` was tried first and is a TRAP: at
   specificity (0,1,1) it out-ranks every single-class rule after it, so
   `.cn-sname{{display:flex}}` silently lost and the share rows stacked their
   swatch above the name — correct HTML, correct-looking CSS, wrong render. */
/* header */
.cn-head{{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;
  border-bottom:1px solid {_alpha_hex(_P['accent'], ALPHA['rule'])};padding-bottom:16px}}
.cn-head-l{{display:flex;flex-wrap:wrap;align-items:center;gap:0 12px}}
.cn-dot{{width:10px;height:10px;border-radius:50%;flex:0 0 auto}}
.cn-title{{font-family:{DISPLAY_FONT};font-size:30px;font-weight:700;
  letter-spacing:.16em;color:{_P['text_primary']};line-height:1.1}}
.cn-sub{{width:100%;padding-left:22px;font-size:11.5px;letter-spacing:.24em;
  color:{_P['text_muted']};margin-top:3px}}
.cn-head-r{{display:flex;gap:10px;flex:0 0 auto}}
.cn-chipbox{{display:flex;flex-direction:column;gap:2px;padding:9px 14px;
  border:1px solid}}
.cn-chipbox-l{{font-size:9.5px;letter-spacing:.22em}}
.cn-chipbox-v{{font-size:13px}}
/* cards row */
.cn-cards{{display:flex;gap:20px;align-items:stretch}}
.cn-card{{flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:18px;
  padding:20px 20px 22px;
  background:linear-gradient(160deg,{_alpha_hex(_P['card_from'], ALPHA['card'])},
    {_alpha_hex(_P['card_to'], ALPHA['card'])});
  border:1px solid {_alpha_hex(_L, ALPHA['border'])}}}
.cn-card-sig{{flex:1.05 1 0;gap:16px}}
.cn-card-h{{display:flex;align-items:baseline;justify-content:space-between;gap:10px}}
.cn-card-t{{font-family:{DISPLAY_FONT};font-size:19px;font-weight:700;
  letter-spacing:.16em;color:{_P['text_primary']}}}
.cn-card-meta{{font-size:10px;letter-spacing:.18em;color:{_P['text_dim']}}}
/* hero */
.cn-hero{{display:flex;align-items:flex-end;gap:16px}}
.cn-hero-v{{font-size:76px;font-weight:600;line-height:.85;letter-spacing:-.02em}}
.cn-hero-r{{display:flex;flex-direction:column;gap:6px;min-width:0}}
.cn-kicker{{font-size:10px;letter-spacing:.28em;color:{_P['text_muted']}}}
.cn-hero-row{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.cn-pill{{border:1px solid;padding:4px 10px;font-size:12px;letter-spacing:.16em}}
.cn-delta{{font-size:12px;white-space:nowrap}}
/* timeframe meters */
.cn-meters{{display:flex;flex-direction:column;gap:12px}}
.cn-meter,.cn-ruler{{display:flex;align-items:center;gap:12px}}
.cn-meter-l{{width:52px;flex:0 0 52px;font-size:10px;letter-spacing:.2em;
  color:{_P['text_muted']}}}
.cn-meter-v{{width:34px;flex:0 0 34px;text-align:right;font-size:17px;font-weight:500}}
.cn-track{{position:relative;display:block;flex:1 1 auto;height:18px;
  background:{_alpha_hex(_L, ALPHA['track'])};
  border:1px solid {_alpha_hex(_L, ALPHA['track_border'])}}}
.cn-fill{{position:absolute;left:0;top:0;bottom:0}}
.cn-mark{{position:absolute;width:2px;top:-3px;bottom:-3px}}
.cn-hatch{{position:absolute;inset:0;background:repeating-linear-gradient(135deg,
  {_alpha_hex(_L, 0.10)} 0 6px,transparent 6px 12px)}}
.cn-noread{{position:absolute;inset:0;display:flex!important;align-items:center;
  justify-content:center;font-size:9.5px;letter-spacing:.22em;color:{_P['text_dim']}}}
.cn-ruler-b{{flex:1 1 auto;display:flex;justify-content:space-between;
  border-top:1px solid {_alpha_hex(_L, ALPHA['border'])};padding-top:5px;
  font-size:9.5px;color:{_P['text_faint']}}}
/* card footers — mt:auto keeps the three cards' baselines aligned */
.cn-foot{{margin-top:auto;display:flex;flex-direction:column;gap:9px}}
.cn-foot-h{{display:flex;align-items:baseline;justify-content:space-between;
  font-size:10px;letter-spacing:.22em;color:{_P['text_dim']}}}
.cn-foot-h span:last-child{{font-size:13px;letter-spacing:0}}
.cn-seg{{display:flex;gap:3px;height:10px}}
.cn-seg i{{flex:1 1 0;height:100%}}
/* trend verdict */
.cn-verdict-box{{display:flex;flex-direction:column;gap:6px;padding:12px 14px;
  border-left:3px solid}}
.cn-verdict{{font-family:{DISPLAY_FONT};font-size:19px;font-weight:700;
  letter-spacing:.1em}}
.cn-guidance{{font-size:11.5px;line-height:1.55;color:{_P['text_secondary']}}}
/* signals 2x2 */
.cn-siggrid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;
  background:{_alpha_hex(_L, ALPHA['hairline'])};
  border:1px solid {_alpha_hex(_L, ALPHA['border'])}}}
.cn-sig{{display:flex;flex-direction:column;gap:6px;padding:16px 16px 14px;min-width:0}}
.cn-sig-l{{font-size:9.5px;letter-spacing:.24em;color:{_P['text_label']}}}
.cn-sig-v{{font-family:{DISPLAY_FONT};font-size:32px;font-weight:700;
  letter-spacing:.06em;line-height:1.1;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:100%}}
.cn-sig-d{{font-size:9.5px;letter-spacing:.16em;color:{_P['text_muted']}}}
/* signed ROC / z meters */
.cn-roc{{display:flex;flex-direction:column;gap:12px}}
.cn-roc-h{{font-size:10px;letter-spacing:.24em;color:{_P['text_dim']}}}
.cn-bi{{display:flex;align-items:center;gap:12px}}
.cn-bi-l{{width:46px;flex:0 0 46px;font-size:10px;color:{_P['text_muted']}}}
.cn-bi-v{{width:46px;flex:0 0 46px;text-align:right;font-size:13px}}
.cn-bi-track{{position:relative;display:block;flex:1 1 auto;height:8px;
  background:{_alpha_hex(_L, ALPHA['track'])};
  border:1px solid {_alpha_hex(_L, ALPHA['track_border'])}}}
.cn-bi-zero{{position:absolute;left:50%;width:1px;top:-3px;bottom:-3px;
  background:{_alpha_hex(_L, 0.5)}}}
.cn-bi-fill{{position:absolute;top:0;bottom:0}}
/* divergence alert */
.cn-diverge{{display:flex;align-items:center;gap:16px;padding:12px 14px;
  border:1px solid}}
.cn-diverge-t{{display:flex;flex-direction:column;gap:4px;flex:1 1 auto;min-width:0}}
.cn-diverge-h{{font-size:9.5px;letter-spacing:.24em}}
.cn-diverge-b{{font-size:11.5px}}
.cn-diverge-bars{{display:flex;align-items:flex-end;gap:4px;height:30px;flex:0 0 auto}}
.cn-diverge-bars i{{width:9px}}
/* regime block */
.cn-regime{{display:flex;gap:22px;align-items:flex-start}}
.cn-regime-l{{flex:0 0 396px;display:flex;flex-direction:column;gap:22px}}
.cn-regime-r{{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:22px}}
.cn-eyebrow{{font-size:10px;letter-spacing:.26em;color:{_P['text_dim']}}}
.cn-dialcard{{padding:22px 24px 24px;gap:16px}}
.cn-dial{{width:100%;max-width:244px;align-self:center}}
.cn-statgrid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;
  background:{_alpha_hex(_L, ALPHA['hairline'])};
  border:1px solid {_alpha_hex(_L, ALPHA['border'])}}}
.cn-stat{{display:flex;flex-direction:column;gap:4px;padding:10px 12px;
  background:{_P['cell_bg']}}}
.cn-stat-l{{font-size:9.5px;letter-spacing:.2em;color:{_P['text_dim']}}}
.cn-stat-v{{font-size:17px;color:{_P['text_primary']}}}
.cn-stat-n{{font-size:10px;color:{_P['text_muted']}}}
.cn-trans{{font-size:10px;letter-spacing:.18em;color:{_P['text_muted']};
  padding-top:2px}}
.cn-tagcard{{padding:18px 20px 20px;gap:12px}}
.cn-active{{font-size:10px}}
.cn-chips{{display:flex;flex-wrap:wrap;gap:8px}}
.cn-chip{{display:flex;align-items:center;gap:8px;border:1px solid;padding:7px 11px;
  font-size:11.5px;letter-spacing:.08em}}
.cn-empty{{font-size:11.5px;color:{_P['text_muted']}}}
/* regime share table */
.cn-sharecard{{padding:20px 24px 8px;gap:0}}
.cn-share-h{{display:flex;align-items:baseline;justify-content:space-between;gap:12px}}
.cn-share-t{{font-family:{DISPLAY_FONT};font-size:20px;font-weight:700;
  letter-spacing:.16em}}
.cn-share-m{{font-size:10px;letter-spacing:.2em;color:{_P['text_dim']};
  white-space:nowrap}}
.cn-srow{{display:grid;grid-template-columns:190px 1fr 132px 74px;gap:26px;
  align-items:center;padding:15px 0;
  border-bottom:1px solid {_alpha_hex(_L, ALPHA['border'])}}}
.cn-srow-head{{padding:12px 0 10px}}
.cn-col{{font-size:10px;letter-spacing:.2em;color:{_P['text_dim']}}}
.cn-right{{text-align:right}}
.cn-sname{{display:flex;align-items:center;gap:12px;min-width:0}}
.cn-swatch{{width:8px;height:26px;flex:0 0 8px}}
.cn-sname-t{{display:flex;flex-direction:column;gap:2px;min-width:0}}
.cn-sname-t b{{font-family:{DISPLAY_FONT};font-size:19px;font-weight:600;
  letter-spacing:.1em;color:{_P['text_primary']}}}
.cn-sname-t em{{font-style:normal;font-size:9.5px;letter-spacing:.16em;
  color:{_P['text_dim']}}}
.cn-sshare{{display:flex;align-items:center;gap:12px;min-width:0}}
.cn-sshare b{{width:74px;flex:0 0 74px;font-size:20px;font-weight:500}}
.cn-sbar{{position:relative;display:block;flex:1 1 auto;height:12px;
  background:{_alpha_hex(_L, ALPHA['track'])};
  border:1px solid {_alpha_hex(_L, ALPHA['track_border'])}}}
.cn-sbar i{{position:absolute;left:0;top:0;bottom:0}}
.cn-sspark{{display:block;width:100%}}
.cn-schange{{font-size:15px;text-align:right}}
/* callouts */
.cn-callouts{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;
  background:{_alpha_hex(_L, ALPHA['hairline'])};
  border:1px solid {_alpha_hex(_L, ALPHA['border'])}}}
.cn-callout{{display:flex;flex-direction:column;gap:4px;padding:16px 18px;
  background:{_P['cell_bg']};min-width:0}}
.cn-co-k{{font-size:9.5px;letter-spacing:.22em;color:{_P['text_dim']}}}
.cn-co-v{{font-family:{DISPLAY_FONT};font-size:22px;font-weight:700;
  letter-spacing:.12em}}
.cn-co-n{{font-size:11px;color:{_P['text_muted']}}}
/* footer */
.cn-footer{{display:flex;align-items:center;justify-content:space-between;gap:16px;
  border-top:1px solid {_alpha_hex(_P['accent'], ALPHA['rule'])};padding-top:14px;
  font-size:10.5px;letter-spacing:.2em}}
.cn-footer span:first-child{{color:{_P['text_dim']}}}
.cn-footer span:last-child{{color:{_P['text_faint']}}}
"""
