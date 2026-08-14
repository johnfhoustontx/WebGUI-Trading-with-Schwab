"""Pure SVG builder for the Market Regime membership panel on ``/sentiment``.

Replaces the percent-stacked area chart that used to live in
``sentiment.build_regime_mix_figure``. The stack was the wrong encoding for this
data and the live numbers say so plainly: over a full session the memberships
barely move (measured 2026-08-14, 78 samples — widest swing 9pp on Balanced,
2pp on Trending, and Breakout pinned at exactly 0.000 all day while still
holding a fifth of the legend). Percent-stacking then *guarantees* the bands
fill the height, so essentially all the ink goes to the part that does not
change, and the two things that did happen were invisible:

  * the lead changed hands once, out of a **0.2pp** gap at the open;
  * Stressed rose from literally zero to 7.5pp by midday.

So this ranks instead. Each regime gets its own row, and each sparkline is
scaled to its OWN range — a 2pp move reads as clearly as a 9pp one, which is the
whole point when the between-regime differences are this static. The footer
carries the leader's margin over the runner-up, a "how firm is this read"
signal the panel never surfaced: at 0.2pp the committed label is very nearly a
coin toss, and ``unclear`` does not cover that (it measures evidence strength,
not how close the top two are).

Deliberate trade-off: the old chart's fixed regime order gave a stable reading
position across repaints, and ranking gives that up. Kept anyway — with five
rows and a lead change being both rare and the most interesting event of the
day, the ORDER is signal rather than noise. Ties break on the fixed order so an
exact tie cannot jitter between repaints.

Pure functions, no NiceGUI import — mounted by the page via ``ui.html`` and
updated with ``el.content`` (the same idiom as ``pages.rings``). Everything it
emits must survive ``ui.html``'s sanitizer: no ``<style>``, no ``<filter>``, no
``dominant-baseline`` — see ``rings._BASELINE_DY`` and the DOMPurify test.
"""
from pages.gauge import _esc
from pages.options.theme import THEME

# Same 5-color value palette as the rest of /sentiment (config/theme.toml
# [charts]) — imported here rather than from ``sentiment`` because that module
# imports THIS one, and the labels/colors belong with the builder that draws them.
_CH = THEME["charts"]

# Fixed key order. No longer a reading position (the rows are ranked), but still
# the deterministic tie-break, and still the order the service publishes in.
REGIME_ORDER = ("mean_reversion", "trending", "breakout", "choppy", "crisis")
# Mirrors ``sentiment-dashboard/scoring/market_regime.REGIME_DISPLAY``, which the
# webgui cannot import (Tier-1 takes no engine imports).
REGIME_LABELS = {"mean_reversion": "Balanced", "trending": "Trending",
                 "breakout": "Breakout", "choppy": "Whipsaw", "crisis": "Stressed"}
REGIME_COLORS = {"mean_reversion": _CH["cyan"], "trending": _CH["green"],
                 "breakout": _CH["yellow"], "choppy": _CH["flat"],
                 "crisis": _CH["red"]}

# --- geometry (fixed 640-wide coordinate space; the SVG scales itself) -------
VIEWBOX_W = 640
ROW_H = 30
HEAD_H = 16                  # column captions
FOOT_H = 26                  # lead-margin line
X_NAME = 16
X_VALUE = 150                # right-aligned
X_BAR, BAR_W, BAR_H = 162, 210, 8
X_SPARK, SPARK_W, SPARK_H = 388, 118, 20
X_DELTA = VIEWBOX_W          # right-aligned to the edge

CHIP = 9                     # colour chip square
TRACK = "#1b2233"            # dim bar track — the page's chip background
MUTED = "#7f8db0"            # captions + no-data glyphs
TEXT = "#cdd8ee"             # base row text
BASELINE_DY = "0.35em"       # NOT dominant-baseline — see rings._BASELINE_DY

# A session boundary. Same constant as ``sentiment._INTRADAY_GAP_MS`` (4h), and
# the same reason: deltas must be measured from THIS session's open, not from
# whatever the previous session happened to end on. The published history is
# one session (``handlers._record_regime`` loads ``n_days=1``), so this only
# ever fires near a day boundary — but it is one comparison to be right.
SESSION_GAP_SEC = 4 * 60 * 60
# Below this a series is flat to the eye; draw the dashed "did not move" rule
# instead of a line, so a dead-flat Breakout reads as dead flat rather than as
# noise amplified to full scale by its own auto-scaling.
_FLAT_EPS = 1e-6


def _safe_frac(v):
    """Membership as a finite float in [0, 1]; junk/NaN/inf -> 0.0.

    Zero rather than None: a regime the classifier scored at nothing IS zero
    here (Breakout sits there for whole sessions), and the panel has a separate
    channel — the dashed flat rule — for "no movement to show"."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, min(1.0, f))


def _memberships(point):
    """The membership dict off a history point, defensively."""
    if not isinstance(point, dict):
        return {}
    m = point.get("memberships")
    return m if isinstance(m, dict) else {}


def _point_ts(point):
    if not isinstance(point, dict):
        return None
    try:
        return float(point.get("ts"))
    except (TypeError, ValueError):
        return None


def session_points(points):
    """The trailing run of points belonging to the CURRENT session.

    Splits on ``SESSION_GAP_SEC`` and keeps the last segment, so "since open"
    means this session's open. Unparseable timestamps do not split (they cannot
    be positioned), they simply stay with the run.
    """
    pts = [p for p in (points or []) if isinstance(p, dict)]
    start, prev = 0, None
    for i, p in enumerate(pts):
        ts = _point_ts(p)
        if ts is not None and prev is not None and (ts - prev) > SESSION_GAP_SEC:
            start = i
        if ts is not None:
            prev = ts
    return pts[start:]


def series_for(points, key):
    """One regime's membership series over ``points``."""
    return [_safe_frac(_memberships(p).get(key)) for p in points]


def rank_rows(points):
    """Ranked rows for the panel, richest-first.

    ``[{key, label, color, now, change, series, flat}, ...]`` sorted by current
    membership descending, ties broken on ``REGIME_ORDER`` so a repaint of
    identical data cannot reorder the rows.
    """
    pts = session_points(points)
    rows = []
    for i, key in enumerate(REGIME_ORDER):
        ser = series_for(pts, key)
        now = ser[-1] if ser else 0.0
        change = (ser[-1] - ser[0]) if len(ser) >= 2 else 0.0
        span = (max(ser) - min(ser)) if ser else 0.0
        rows.append({"key": key, "label": REGIME_LABELS[key],
                     "color": REGIME_COLORS[key], "now": now, "change": change,
                     "series": ser, "flat": span < _FLAT_EPS})
    rows.sort(key=lambda r: (-r["now"], REGIME_ORDER.index(r["key"])))
    return rows


def lead_margin(points):
    """``(leader_row_key, margin_now, margin_min)`` over the session.

    ``margin_min`` is the tightest the top two ever got today — the number that
    says whether the day's committed label was ever a coin toss. All three are
    None when there is nothing to rank.
    """
    pts = session_points(points)
    if not pts:
        return None, None, None
    mins = []
    for p in pts:
        vals = sorted((_safe_frac(_memberships(p).get(k)) for k in REGIME_ORDER),
                      reverse=True)
        mins.append(vals[0] - vals[1])
    rows = rank_rows(points)
    return rows[0]["key"], (rows[0]["now"] - rows[1]["now"]), min(mins)


def _fmt_pct(frac, decimals=1):
    return f"{frac * 100:.{decimals}f}%"


def _fmt_delta(frac, flat):
    """Signed change in percentage points; an em-dash for a series that never
    moved (a "+0.0pp" would read as a measurement rather than as absence)."""
    if flat:
        return "—"
    sign = "+" if frac >= 0 else "−"
    return f"{sign}{abs(frac) * 100:.1f}pp"


def _text(x, y, body, size, fill, anchor="start", weight=None):
    extra = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'dy="{BASELINE_DY}" font-size="{size}"{extra} '
            f'fill="{fill}">{body}</text>')


def _spark_path(series, x, y, w, h):
    """Polyline for one regime's own range, scaled to its OWN min/max.

    Returns "" for a series that cannot make a line (0 or 1 points) — the caller
    draws the flat rule instead.
    """
    if len(series) < 2:
        return ""
    lo, hi = min(series), max(series)
    span = hi - lo
    if span < _FLAT_EPS:
        return ""
    dx = w / (len(series) - 1)
    pts = []
    for i, v in enumerate(series):
        py = y + h * (1.0 - (v - lo) / span)
        pts.append(f"{'L' if i else 'M'} {x + i * dx:.1f} {py:.1f}")
    return " ".join(pts)


def _id_token(uid):
    """``uid`` reduced to characters legal in a DOM id (mirrors rings)."""
    return "".join(c for c in str(uid or "") if c.isalnum() or c in "_-")


def regime_mix_svg(points, uid="regime"):
    """The ranked membership panel as one inline SVG string. Never raises.

    ``points`` is ``cache:sentiment:regime_history``'s ``points`` list. An empty
    or wholly unusable history renders a waiting placeholder rather than an
    empty frame, so the panel never looks broken before the first sample.
    """
    rows = rank_rows(points) if session_points(points) else []
    height = HEAD_H + ROW_H * len(REGIME_ORDER) + FOOT_H
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {VIEWBOX_W} {height}" width="100%" '
            f'id="regime-mix-{_id_token(uid)}">')
    if not rows:
        return (head + _text(VIEWBOX_W / 2, height / 2, "Waiting for regime…",
                             13, MUTED, anchor="middle") + "</svg>")

    parts = [head]
    # Column captions. "share" and "today" name what the two graphic columns
    # mean, because a bar scaled to the leader and a sparkline scaled to itself
    # are both relative and neither is self-evident.
    parts.append(_text(X_VALUE, HEAD_H / 2, "share", 10, MUTED, anchor="end"))
    parts.append(_text(X_SPARK, HEAD_H / 2, "today", 10, MUTED))
    parts.append(_text(X_DELTA, HEAD_H / 2, "change", 10, MUTED, anchor="end"))

    lead = rows[0]["now"]
    for i, r in enumerate(rows):
        top = HEAD_H + i * ROW_H
        mid = top + ROW_H / 2
        strong = (i == 0)
        parts.append(f'<rect x="0" y="{mid - CHIP / 2:.1f}" width="{CHIP}" '
                     f'height="{CHIP}" rx="2" fill="{r["color"]}"/>')
        parts.append(_text(X_NAME, mid, _esc(r["label"]), 13, TEXT,
                           weight=600 if strong else None))
        parts.append(_text(X_VALUE, mid, _fmt_pct(r["now"]), 13, TEXT,
                           anchor="end", weight=600 if strong else None))
        # Bar length is share OF THE LEADER, so the leader's row is always full
        # and the others read as "how close is this to winning" — the contest,
        # which is the question the panel exists to answer. (Share of the total
        # would compress everything into the same narrow band, which is exactly
        # the failure the stacked chart had.)
        parts.append(f'<rect x="{X_BAR}" y="{mid - BAR_H / 2:.1f}" '
                     f'width="{BAR_W}" height="{BAR_H}" rx="4" fill="{TRACK}"/>')
        if lead > 0 and r["now"] > 0:
            parts.append(f'<rect x="{X_BAR}" y="{mid - BAR_H / 2:.1f}" '
                         f'width="{BAR_W * r["now"] / lead:.1f}" height="{BAR_H}" '
                         f'rx="4" fill="{r["color"]}" '
                         f'opacity="{1 if strong else 0.55}"/>')
        d = _spark_path(r["series"], X_SPARK, mid - SPARK_H / 2, SPARK_W, SPARK_H)
        if d:
            parts.append(f'<path d="{d}" fill="none" stroke="{r["color"]}" '
                         f'stroke-width="1.75" stroke-linejoin="round" '
                         f'stroke-linecap="round"/>')
        else:
            parts.append(f'<path d="M {X_SPARK} {mid:.1f} '
                         f'L {X_SPARK + SPARK_W} {mid:.1f}" fill="none" '
                         f'stroke="{r["color"]}" stroke-width="1.5" '
                         f'opacity="0.45" stroke-dasharray="3 3"/>')
        parts.append(_text(X_DELTA, mid, _fmt_delta(r["change"], r["flat"]),
                           12, MUTED, anchor="end"))

    _, margin, margin_min = lead_margin(points)
    if margin is not None:
        foot = (f'{rows[0]["label"]} leads {rows[1]["label"]} by '
                f'{margin * 100:.1f}pp')
        if margin_min is not None:
            foot += f" · tightest today {margin_min * 100:.1f}pp"
        parts.append(_text(0, HEAD_H + ROW_H * len(rows) + FOOT_H / 2,
                           _esc(foot), 11, MUTED))
    parts.append("</svg>")
    return "".join(parts)
