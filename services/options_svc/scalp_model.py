"""The Opening Scalp's model: alignment buckets judged walk-forward.

Declared before any outcome was seen (design §5.2): a morning's alignment score
falls in one of three buckets, and a bucket's win rate is shown as a call only
if, walk-forward, it predicted better than the plain base rate. This module is
that judgement and nothing else - pure arithmetic on rows the study hands it, no
store, no config, no import from ``compute``.

Why walk-forward, and why so strict about "earlier": with ~140 mornings a rate
that has seen the morning it predicts flatters itself, and the flattering is
exactly the size of the edges being looked for. So a morning is predicted only
from mornings on EARLIER days (never the same day: the three symbols share a
tape), and a bucket is judged only on mornings where it had something to say,
against the base rate on those SAME mornings, so neither Brier score gets an
easier set of days than the other.

The scale rule (``edge_across_scales``) exists because the premiums are modelled
at an assumed volatility: an edge that appears at one volatility multiplier and
vanishes at the next is an artefact of the assumption, not of the mornings.
"""
import math

#: The alignment buckets, lowest first (``bucket_of``'s answers).
BUCKETS = ("low", "mid", "high")


def bucket_of(alignment, cuts):
    """``"low"`` below ``cuts[0]``, ``"mid"`` from ``cuts[0]`` up to ``cuts[1]``,
    ``"high"`` from ``cuts[1]``. ``None`` for no alignment (absent stays absent)."""
    if alignment is None:
        return None
    a = float(alignment)
    if not math.isfinite(a):
        return None
    if a < cuts[0]:
        return BUCKETS[0]
    if a < cuts[1]:
        return BUCKETS[1]
    return BUCKETS[2]


def wilson(wins, n, z=1.96):
    """The Wilson score interval for ``wins`` of ``n``; ``(0.0, 1.0)`` for no
    sample (nothing is known)."""
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def walk_forward(rows):
    """Each row's prediction from the rows on EARLIER days only.

    ``rows`` are one (symbol, role, iv_scale)'s labelled mornings, sorted by
    ``day``: ``{day, bucket, win}`` with ``win`` 0 or 1 and ``bucket`` possibly
    ``None`` (the morning counts toward the base rate but has no bucket rate).
    Returns, in the same order, ``{day, p_bucket, n_bucket, p_base, n_base}``: the
    win rate of earlier rows in the row's bucket and of all earlier rows, each
    ``None`` while its count is 0. Rows sharing a day never see each other.
    Raises ``ValueError`` on unsorted days or a label that is not 0/1.
    """
    out = []
    base_n = base_w = 0
    by_n, by_w = {}, {}
    pending = []           # this day's rows, folded in once the day is over
    prev_day = None
    for row in rows:
        day, bucket, win = row["day"], row["bucket"], row["win"]
        if win not in (0, 1):
            raise ValueError(f"win must be 0 or 1, got {win!r}")
        if prev_day is not None and day < prev_day:
            raise ValueError(f"rows are not sorted by day ({day} after {prev_day})")
        if day != prev_day:
            for b, w in pending:
                base_n += 1
                base_w += w
                if b is not None:
                    by_n[b] = by_n.get(b, 0) + 1
                    by_w[b] = by_w.get(b, 0) + w
            pending = []
            prev_day = day
        n_b = by_n.get(bucket, 0) if bucket is not None else 0
        out.append({
            "day": day,
            "p_bucket": by_w[bucket] / n_b if n_b else None,
            "n_bucket": n_b,
            "p_base": base_w / base_n if base_n else None,
            "n_base": base_n,
        })
        pending.append((bucket, win))
    return out


def verdict(scored, rows, min_samples):
    """Per bucket ``{brier_bucket, brier_base, n_scored, edge}``.

    ``scored`` is ``walk_forward(rows)``. A morning is scored for its bucket only
    when ``p_bucket`` exists (the bucket had an earlier morning), and both Brier
    scores are means over exactly those mornings. ``edge`` iff ``n_scored >=
    min_samples`` and the bucket's Brier is strictly below the base's. Every bucket
    in ``BUCKETS`` is answered; one never scored has ``None`` Briers and no edge.
    """
    if len(scored) != len(rows):
        raise ValueError("scored and rows must be the same mornings")
    sums = {}
    for s, r in zip(scored, rows):
        if s["p_bucket"] is None:
            continue
        acc = sums.setdefault(r["bucket"], [0, 0.0, 0.0])
        acc[0] += 1
        acc[1] += (s["p_bucket"] - r["win"]) ** 2
        acc[2] += (s["p_base"] - r["win"]) ** 2
    out = {}
    for b in list(BUCKETS) + sorted(k for k in sums if k not in BUCKETS):
        n, sb, sbase = sums.get(b, (0, 0.0, 0.0))
        if n == 0:
            out[b] = {"brier_bucket": None, "brier_base": None,
                      "n_scored": 0, "edge": False}
            continue
        bb, bbase = sb / n, sbase / n
        out[b] = {"brier_bucket": bb, "brier_base": bbase, "n_scored": n,
                  "edge": bool(n >= min_samples and bb < bbase)}
    return out


# ── the day as the unit (the intraday study) ─────────────────────────────────
# The intraday study scores up to 21 slots a day, and rows of one day share one
# tape over overlapping hours: they are not independent draws. So the evidence a
# cell holds is counted in DAYS (design 2026-10-20-scalp-intraday-calls §4): the
# rate and the Brier means stay over rows, but ``n``, the ``min_samples`` gate and
# the Wilson interval's ``n`` are distinct days.

def verdict_by_day(scored, rows, min_samples):
    """``verdict`` with the DAY as the unit: per bucket ``{brier_bucket,
    brier_base, n_scored, n_rows, edge}``.

    The Brier scores are means over the scored ROWS, exactly as ``verdict``'s;
    ``n_scored`` is the number of DISTINCT days with a scored row in the bucket
    (``n_rows`` the rows), and ``edge`` iff ``n_scored >= min_samples`` and the
    bucket's Brier is strictly below the base's. ``scored`` is
    ``walk_forward(rows)``, whose rows sharing a day never see each other."""
    if len(scored) != len(rows):
        raise ValueError("scored and rows must be the same rows")
    sums = {}
    for s, r in zip(scored, rows):
        if s["p_bucket"] is None:
            continue
        acc = sums.setdefault(r["bucket"], [set(), 0, 0.0, 0.0])
        acc[0].add(r["day"])
        acc[1] += 1
        acc[2] += (s["p_bucket"] - r["win"]) ** 2
        acc[3] += (s["p_base"] - r["win"]) ** 2
    out = {}
    for b in list(BUCKETS) + sorted(k for k in sums if k not in BUCKETS):
        if b not in sums:
            out[b] = {"brier_bucket": None, "brier_base": None,
                      "n_scored": 0, "n_rows": 0, "edge": False}
            continue
        days, n, sb, sbase = sums[b]
        bb, bbase = sb / n, sbase / n
        out[b] = {"brier_bucket": bb, "brier_base": bbase, "n_scored": len(days),
                  "n_rows": n, "edge": bool(len(days) >= min_samples and bb < bbase)}
    return out


def wilson_days(wins, rows_n, days_n, z=1.96):
    """The Wilson score interval around the ROW mean ``wins / rows_n`` with ``n =
    days_n``: as wide as the days' evidence, not the rows'. ``(0.0, 1.0)`` when
    either count is 0 (nothing is known)."""
    if rows_n <= 0 or days_n <= 0:
        return 0.0, 1.0
    p = wins / rows_n
    n = days_n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def edge_across_scales(verdicts_by_scale):
    """``{bucket: bool}``: an edge only if the bucket has one at EVERY scale given
    (a bucket missing at a scale has none there)."""
    if not verdicts_by_scale:
        return {}
    buckets = set()
    for v in verdicts_by_scale.values():
        buckets.update(v)
    ordered = [b for b in BUCKETS if b in buckets] + sorted(buckets - set(BUCKETS))
    return {b: all(bool(v.get(b, {}).get("edge")) for v in verdicts_by_scale.values())
            for b in ordered}


__all__ = ["BUCKETS", "bucket_of", "wilson", "walk_forward", "verdict",
           "verdict_by_day", "wilson_days", "edge_across_scales"]
