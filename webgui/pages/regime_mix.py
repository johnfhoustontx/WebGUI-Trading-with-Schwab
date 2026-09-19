"""Pure ranking model behind the Market Regime membership table on
``/sentiment`` (the Market Regime Console's share table, ``pages/console_regime``)
and the regime vocabulary the Desk shares.

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

Pure functions, no NiceGUI import. The inline-SVG panel this module first
drew was superseded by the console's share table, which renders these rows.
"""
from pages.options.theme import THEME

# Same 5-color value palette as the rest of /sentiment (config/theme.toml
# [charts]) — imported here rather than from ``sentiment`` because that module
# imports THIS one, and the labels/colors belong with the model that ranks them.
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

# A one-line gloss under each regime's name on the console's share table — what
# the tape DOES in that regime, which the display word alone does not say
# ("Whipsaw" names the state; "chop, no edge" says what it means for a trade).
# Static copy, not data.
REGIME_NOTES = {"mean_reversion": "TWO-SIDED FLOW",
                "trending": "DIRECTIONAL PERSIST",
                "breakout": "RANGE EXPANSION",
                "choppy": "CHOP · NO EDGE",
                "crisis": "VOL EXPANSION"}
# What a regime at exactly zero says instead — it is not doing its thing at all,
# so its usual gloss would be a description of something absent.
ZERO_NOTE = "DORMANT"
# Below this a share counts as "was at nothing" for the emerging callout.
_EMERGING_FLOOR = 0.005

# The hover on the Market Regime word, keyed by the DISPLAYED word — the service
# publishes it with its direction already applied (``regime["label"]``), so the
# key is exactly what the reader sees. Covers the five REGIME_DISPLAY words, every
# direction adornment, and "Unclear"; shared/tests/test_cross_tier_mirrors.py
# fails if the service can print a word this table lacks.
REGIME_PICTURE = {
    "Balanced": "Quiet, two-sided tape: price is sitting at its own average, "
                "trend strength is low, and dealers are dampening moves. Neutral "
                "premium selling — iron condors — fits best.",
    "Trending": "Price is moving with persistence, but the two direction reads "
                "disagree on which way, so no direction is named. Follow the "
                "move once it shows; avoid fading it.",
    "Rallying": "A steep, persistent move higher, confirmed by both the price "
                "slope and the Market Trend score. Follow it; don't sell calls "
                "into it.",
    "Firming": "A steady, gentle climb, confirmed by both the price slope and "
               "the Market Trend score. Follow the direction; avoid fading it.",
    "Retreating": "A steep, persistent move lower, confirmed by both the price "
                  "slope and the Market Trend score. Follow it; don't sell puts "
                  "into it.",
    "Softening": "A steady, gentle decline, confirmed by both the price slope "
                 "and the Market Trend score. Follow the direction; avoid "
                 "fading it.",
    "Breakout": "The range is expanding into new ground. Momentum trades fit; "
                "credit spreads against the move are dangerous.",
    "Breakdown": "The range is expanding to the downside. Momentum favors the "
                 "downside; put credit spreads here are dangerous.",
    "Whipsaw": "Plenty of movement, no progress: failed breaks and two-sided "
               "wicks. The hardest regime — reduce size or stand aside.",
    "Stressed": "Fear is driving the tape: elevated VIX, an inverted volatility "
                "curve, gaps that don't fill. Premium is rich but the risk is "
                "real — defined risk only.",
    "Unclear": "No regime has enough evidence to name. Wait for one to form.",
}


def regime_picture(word):
    """The hover sentence for a displayed regime word, or "" for anything else."""
    return REGIME_PICTURE.get(str(word or "").strip(), "")


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


def regime_note(row):
    """The gloss for one ranked row — its dormant note when it holds nothing."""
    if not isinstance(row, dict):
        return ""
    if _safe_frac(row.get("now")) <= 0.0:
        return ZERO_NOTE
    return REGIME_NOTES.get(row.get("key"), "")


def callouts(points):
    """``{dominant, biggest_move, emerging}`` — the console's 3-up summary strip.

    Each is a ranked row (or None), picked by a stated rule rather than by eye:

    * **dominant** — the largest current share.
    * **biggest_move** — the largest ABSOLUTE change since the session open,
      whichever way it went. The day's most-moved band is the news regardless of
      sign; on 2026-08-14 that was Balanced at −8.4pp.
    * **emerging** — a regime that started the session at ~nothing and is now
      rising (Stressed, 0 → +4.9pp). This is the one worth its own tile: a band
      waking from zero is a change of KIND, not of degree, and it is invisible
      in a share ranking because it is still tiny. Falls back to the largest
      riser that is not already the biggest_move, so the tile is not usually
      blank; None when nothing is rising at all.
    """
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


