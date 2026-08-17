"""Pure display language for the Sector & Industry heat grid (/sentiment/sectors).

Everything the grid paints is decided here, off the event loop and away from any
widget, so it can be pinned by ``tests/test_sector_heat.py``: the oklch heat
ramp, the per-column normalisation, the flat bands, ranking, sorting and the two
header strings. ``pages/sentiment_sectors.py`` holds only widgets and wiring.

**The design in one paragraph.** Day / Week / Month render as three adjacent
filled tiles flush to the right edge, so the colour band is continuous both
across a row and down the page, and the figure sits *inside* its tile. Intensity
is normalised **per column** against that column's own largest magnitude across
sectors *and* industries — a column therefore always uses its full range, and
expanding a sector cannot repaint the rows above it. Below a per-horizon flat
band (±0.50% Day, ±1.00% Week, ±1.50% Month) a cell drops to neutral, so a quiet
month does not glow merely because a month drifts further than a day does.

**Why the ramp is code and not ``config/theme.toml``.** It is a data-driven cell
map, the category CLAUDE.md explicitly excludes from the config-driven palette
(alongside the gauge face and the score/heat/P&L zone maps). What a restyle would
actually want to reach — the page ground, the text greys, the amber — *is* in
``[sectors]``.

**Why it is stepped and not continuous.** The Tailwind-first standard requires a
data-driven colour to map onto a fixed finite palette of static classes rather
than a per-datum arbitrary value. Thirteen steps (six per direction plus
neutral) is indistinguishable from a continuous ramp at tile size and keeps the
vocabulary deduped — see ``test_heat_palette_is_a_finite_deduped_vocabulary``.
"""
import math
from datetime import datetime, timezone

# ── oklch ────────────────────────────────────────────────────────────────────
# The ramp is authored in oklch because that is the space in which "one step
# brighter" is one step brighter to the *eye*; interpolating the same endpoints
# in sRGB bunches the dark steps together, which is precisely the end of the
# range this grid spends most of its time in.
LEVELS = 6                       # intensity steps per direction
HUE_UP, HUE_DN = 158.0, 22.0     # green / red
RAMP_L = (0.175, 0.300)          # tile lightness: faintest step → strongest
RAMP_C = (0.022, 0.110)          # tile chroma
FLAT_BG = (0.155, 0.004, 90.0)   # a neutral one hair above the page ground
TXT_L = (0.660, 0.965)           # the figure lifts as its tile saturates …
TXT_C = (0.030, 0.055)
FLAT_TXT = (0.580, 0.006, 90.0)  # … and recedes to grey when the cell is flat


def oklch_hex(lightness, chroma, hue):
    """``oklch(L C H)`` → ``#RRGGBB``, gamut-clamped.

    Out-of-gamut requests clamp per channel rather than raising or wrapping, so
    a mis-tuned ramp degrades to a flat-looking colour instead of taking the
    page down (the house rule for anything on the styling path)."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    lin = (
        4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
    )
    out = []
    for x in lin:
        x = min(1.0, max(0.0, x))
        x = 1.055 * x ** (1 / 2.4) - 0.055 if x > 0.0031308 else 12.92 * x
        out.append(round(min(1.0, max(0.0, x)) * 255))
    return "#%02X%02X%02X" % tuple(out)


def _lerp(pair, f):
    return pair[0] + (pair[1] - pair[0]) * f


def _ramp(level):
    """(background hex, text hex) for a signed level in ``-LEVELS..LEVELS``."""
    if level == 0:
        return oklch_hex(*FLAT_BG), oklch_hex(*FLAT_TXT)
    hue = HUE_UP if level > 0 else HUE_DN
    f = (abs(level) - 1) / (LEVELS - 1)
    return (oklch_hex(_lerp(RAMP_L, f), _lerp(RAMP_C, f), hue),
            oklch_hex(_lerp(TXT_L, f), _lerp(TXT_C, f), hue))


# The fixed finite palette. Built once at import from the closed level
# enumeration above — never per datum — so the vocabulary stays deduped and the
# Tailwind JIT sees a small stable set of arbitrary-value classes.
HEAT_BG = {}
HEAT_TXT = {}
for _lvl in range(-LEVELS, LEVELS + 1):
    _bg, _tx = _ramp(_lvl)
    HEAT_BG[_lvl] = f"bg-[{_bg}]"
    HEAT_TXT[_lvl] = f"text-[{_tx}]"
del _lvl, _bg, _tx

# The full vocabularies, for ``.classes(remove=…)`` on an in-place recolour: a
# partial remove set leaves two fills fighting after the second repaint.
HEAT_BG_CLASSES = " ".join(dict.fromkeys(HEAT_BG.values()))
HEAT_TXT_CLASSES = " ".join(dict.fromkeys(HEAT_TXT.values()))


# ── normalisation ────────────────────────────────────────────────────────────
# Below these a cell reads flat. They widen with the horizon because a month is
# *expected* to have travelled further than a day; a single band would paint an
# unremarkable month as a move.
FLAT_BAND = {"day": 0.50, "week": 1.00, "month": 1.50}
COLUMNS = ("day", "week", "month")
# The quantile of |value| a column treats as full intensity — see column_scale
# for why this is not 1.0. Measured against the live 81-row set: 0.90 is the
# highest value that still spends all thirteen steps on every column, and lower
# values start collapsing the tails of Day into a single saturated step.
SCALE_QUANTILE = 0.90


def _num(v):
    """``v`` as a float, or None for anything that isn't a real reading."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def all_heat_values(sector_rows, industries):
    """Every row that shares the colour scale — sectors plus all industries.

    Industries count whether or not they are expanded, so opening a sector adds
    rows to the grid without restating the colour of the rows above it."""
    rows = list(sector_rows or [])
    for ind in (industries or {}).values():
        rows.extend(ind or [])
    return rows


def column_scale(rows, field):
    """The magnitude one column treats as full intensity, or 0.0 if unreadable.

    This is the column's 90th percentile, **not** its maximum, and that is a
    deliberate departure from the reference design. That prototype normalised on
    the max, which works on its synthetic industry placeholders because they
    cluster near their sectors. Real industry ETFs have a fat right tail: one
    +27% Month reading against a 3.2% median (measured live, 81 rows) pinned the
    whole Month column into four of the thirteen steps — every sector rendered
    the same near-flat green, which is precisely the "a column always uses its
    full range" property the design exists to get. Normalising on p90 and
    letting the handful above it saturate restores the spread; ``heat_level``
    already clamps, so nothing overflows."""
    vals = sorted(abs(v) for v in (_num(r.get(field)) for r in rows or [])
                  if v is not None)
    if not vals:
        return 0.0
    i = (len(vals) - 1) * SCALE_QUANTILE
    lo, hi = math.floor(i), math.ceil(i)
    return vals[lo] + (vals[hi] - vals[lo]) * (i - lo)


def column_scales(rows):
    """``{"day": …, "week": …, "month": …}`` — each column's own range."""
    return {f: column_scale(rows, f) for f in COLUMNS}


def heat_level(pct, scale, band):
    """Signed intensity in ``-LEVELS..LEVELS``, 0 for flat, None for no reading.

    ``scale`` is the column's largest magnitude and ``band`` its flat threshold,
    so the step size is whatever that column's own range happens to be."""
    v = _num(pct)
    if v is None:
        return None
    mag, sign = abs(v), (1 if v > 0 else -1)
    if mag <= band:
        return 0
    span = scale - band
    if span <= 0:            # every reading is flat but this one — it *is* the max
        return sign * LEVELS
    f = (mag - band) / span
    return sign * min(LEVELS, max(1, math.ceil(f * LEVELS)))


def heat_classes(pct, scale, band):
    """``(background class, text class)`` for one tile. No reading → neutral."""
    lvl = heat_level(pct, scale, band)
    lvl = 0 if lvl is None else lvl
    return HEAT_BG[lvl], HEAT_TXT[lvl]


# ── sorting + ranking ────────────────────────────────────────────────────────
def sort_rows(rows, field, descending=True):
    """A new list ordered on one column, rows with no reading always last."""
    def key(r):
        v = _num(r.get(field))
        if v is None:
            return (1, 0.0)
        return (0, -v if descending else v)
    return sorted(rows or [], key=key)


def rank_line(index, total, field):
    """``RANK 1 OF 11 · DAY`` — position in the pack, restated in words."""
    return f"RANK {index + 1} OF {total} · {str(field).upper()}"


# ── formatting ───────────────────────────────────────────────────────────────
DASH = "—"


def fmt_pct(v):
    """``+1.00%`` / ``-0.07%``, or a dash. A missing reading is never ``+0.00%``
    — that would read as "flat", which is a different claim from "unknown"."""
    n = _num(v)
    return DASH if n is None else f"{n:+.2f}%"


def fmt_pcr(v):
    """Put/call as a plain two-place number. Zero means "not computed"."""
    n = _num(v)
    return DASH if n is None or n <= 0 else f"{n:.2f}"


def pcr_tone(v):
    """``warn`` above 1.5 (put-heavy), ``plain`` otherwise, ``muted`` if absent.

    Put/call gets a tint rather than a heat tile on purpose: it is a ratio, not a
    percentage change, so painting it on the same scale as the three timeframe
    columns would invite reading it as a fourth one."""
    n = _num(v)
    if n is None or n <= 0:
        return "muted"
    return "warn" if n > 1.5 else "plain"


# ── header strings ───────────────────────────────────────────────────────────
def eyebrow(sector_at):
    """``MARKET STRUCTURE · AUG 17, 2026 · 16:00 ET`` from the cache's stamp.

    The stamp is written UTC by ``sentiment_svc``; the market it describes runs
    on Eastern, so it is rendered there. Anything unparseable says so — a
    made-up "now" on a cold cache is worse than an admitted gap."""
    stamp = _eastern(sector_at)
    if stamp is None:
        return "MARKET STRUCTURE · AWAITING DATA"
    return (f"MARKET STRUCTURE · {stamp.strftime('%b %d, %Y').upper()} · "
            f"{stamp.strftime('%H:%M')} ET")


def _eastern(iso):
    """An ISO stamp as tz-aware Eastern, or None if it will not parse."""
    if not iso:
        return None
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(iso))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 — a header must not be able to fail a page.
        return None


# spread → (headline, tone). Same thresholds as the shared ``rotation_banner``
# so this page and /sentiment/rotation cannot disagree about the day's regime.
_REGIME_BANDS = (
    (1.0, "Strong risk-on regime", "up"),
    (0.3, "Risk-on regime", "up"),
    (-0.3, "Mixed regime", "warn"),
    (-1.0, "Risk-off regime", "down"),
)


def regime_headline(rot):
    """``(headline, tone, detail)`` for the line under the title.

    ``detail`` is the cyclical-vs-defensive pair the headline rests on, which is
    the one number a reader needs to check the word against."""
    rot = rot if isinstance(rot, dict) else {}
    tf = spread = None
    for cand in ("day", "3d", "week"):
        s = _num(rot.get(f"{cand}_spread"))
        if s is not None:
            tf, spread = cand, s
            break
    if tf is None:
        return "No regime read", "muted", "Refresh to compute sector rotation"
    word, tone = "Strong risk-off regime", "down"
    for lo, w, t in _REGIME_BANDS:
        if spread >= lo:
            word, tone = w, t
            break
    cyc, dfn = fmt_pct(rot.get(f"{tf}_cyc")), fmt_pct(rot.get(f"{tf}_def"))
    # Spelled out, not the reference mock's "cyc / def": the house rule is whole
    # words for casual shortenings (trader acronyms and tickers keep theirs).
    return word, tone, f"cyclicals {cyc} vs defensives {dfn}"


def summary_line(sector_data, quotes, summary):
    """``62% green · cap-weighted +0.70% · score 7.8/10``, or "" with no data.

    Carried over from the previous table's summary line. The reference design
    drops it, but it is the only place the *breadth* of the move and the
    cap-weighted number live, and neither is recoverable from the grid — the
    grid is unweighted, so eight green micro-sectors and three red mega-caps
    look bullish there and are not."""
    pcts = [p for p in (_num(((quotes or {}).get(r.get("etf")) or {})
                             .get("change_pct"))
                        for r in sector_data or []
                        if r.get("kind") == "sector") if p is not None]
    if not pcts:
        return ""
    green = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    summary = summary if isinstance(summary, dict) else {}
    wpct = _num(summary.get("wpct"))
    score = _num(summary.get("score"))
    parts = [f"{green:.0f}% green",
             f"cap-weighted {fmt_pct(wpct) if wpct is not None else DASH}"]
    if score is not None:
        parts.append(f"score {score:.1f}/10")
    return " · ".join(parts)
