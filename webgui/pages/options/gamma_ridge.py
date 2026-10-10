"""The ridge plot: one by-strike profile every so many minutes, stacked front
to back, so how the profile has changed through the session reads at a glance.

It is the by-strike bar panel laid out in time. Each ridge is a moment's
profile drawn as a hill over its own baseline: the height is the size of the
value at that strike, the colour its sign. The earliest ridge is at the top
and the latest in front.

PURE. No NiceGUI, no bus, and nothing imported from ``gamma`` (``gamma`` imports
this). Every function builds new values and writes to none of its inputs.

Design: docs/plans/2026-10-10-gamma-ridge-plot-design.md
"""
import math

# The regular session, in minutes. The number of ridges a session can hold
# follows from it, and that number fixes the chart's series count.
SESSION_MIN = 390


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def slots(every_min):
    """The most ridges one session draws at this spacing: one a bucket, one for
    the bucket the close starts, and one for "now". The chart always holds this
    many ridge series (empty until their time comes), because it updates in
    place and its series count must not change."""
    return SESSION_MIN // max(int(every_min), 1) + 2


def pick(times, every_min, most=None):
    """The columns to draw: the FIRST reading of each clock bucket of
    ``every_min`` minutes, and always the latest reading, so the front ridge is
    now. ``times`` are epoch seconds in order; a reading that is not a number is
    skipped. Past ``most`` the earliest are dropped."""
    span = max(int(every_min), 1) * 60
    out, seen = [], set()
    for i, ts in enumerate(times):
        if not _number(ts):
            continue
        bucket = int(ts) // span
        if bucket not in seen:
            seen.add(bucket)
            out.append(i)
    last = next((i for i in range(len(times) - 1, -1, -1) if _number(times[i])), None)
    if last is not None and last not in out:
        out.append(last)
    return out[-most:] if most else out


def _size(value, scale):
    """How tall a value is drawn: its size, or the square root of its size."""
    size = abs(value)
    return math.sqrt(size) if scale == "root" else size


def _zones(points):
    """Where a ridge changes colour along the strike axis: ``[(up_to, sign), …,
    (None, sign)]`` for ``points`` = ``[(strike, value), …]``. A boundary sits
    where the value crosses zero, on the straight line between two strikes. A
    zero takes the sign beside it, so it never makes a zone of its own."""
    out, sign, prev = [], 0, None
    for strike, value in points:
        here = (value > 0) - (value < 0)
        if here and sign and here != sign and prev is not None:
            k0, v0 = prev
            out.append((k0 + (strike - k0) * (0.0 - v0) / (value - v0), sign))
        if here:
            sign, prev = here, (strike, value)
    out.append((None, sign or 1))
    return out


def ridges(strikes, z, times, spots, columns, *, lo, hi, scale="root", overlap=3.0):
    """The ridges for ``columns`` of the grid ``z[yi][xi]`` (rows are
    ``strikes``, ascending; columns are the readings), earliest first.

    Each is ``{"ts", "baseline", "points": [(strike, low, high, value), …],
    "zones", "spot"}``. Baselines are one unit apart with the EARLIEST on top,
    so the latest ridge is in front. ``high − low`` is the value's size (or its
    square root on ``scale="root"``) on ONE scale shared by every ridge: a ridge
    growing through the day is the profile growing, not the scale moving.

    The scale is set by the TYPICAL ridge, not the tallest: the median of the
    ridges' peaks spans ``overlap`` baselines. One reading is routinely several
    times the rest (an expiry close pinned on a strike), and scaled to it every
    other ridge is flat. That one is drawn in proportion and rises past the
    others.

    Only strikes inside ``[lo, hi]`` are drawn; a strike with no reading at a
    column is left out of that ridge. ``value`` is the real figure, for the
    tooltip. Nothing is written to ``z``."""
    rows = [(k, row) for k, row in zip(strikes, z) if lo <= k <= hi]
    profiles = []
    for xi in columns:
        profiles.append([(k, row[xi]) for k, row in rows
                         if xi < len(row) and _number(row[xi])])
    peaks = sorted(peak for peak in (max((_size(v, scale) for _k, v in p), default=0.0)
                                     for p in profiles) if peak > 0)
    unit = overlap / peaks[len(peaks) // 2] if peaks else 0.0
    out = []
    for i, (xi, profile) in enumerate(zip(columns, profiles)):
        baseline = float(len(columns) - 1 - i)
        spot = spots[xi] if xi < len(spots) and _number(spots[xi]) else None
        out.append({
            "ts": times[xi], "baseline": baseline,
            "points": [(k, baseline, baseline + _size(v, scale) * unit, v)
                       for k, v in profile],
            "zones": _zones(profile),
            "spot": spot if spot is not None and lo <= spot <= hi else None})
    return out
