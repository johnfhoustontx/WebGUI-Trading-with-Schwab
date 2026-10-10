"""The gravity well: net gamma by strike drawn as terrain, with price as a ball
on it.

Positive dealer gamma damps moves, so a strike that holds it is drawn as a
VALLEY; negative gamma amplifies them, so it is drawn as a HILL. The height of
the ground at a strike is simply minus its net gamma. Where price sits on that
ground, and which way the ground slopes from there, is the whole read.

It is a picture of the gamma profile as it stands, not a model of where price
will go: nothing here knows about order flow, time or volatility.

PURE. No NiceGUI, no bus, and nothing imported from ``gamma`` (``gamma`` imports
this). Every function builds new values and writes to none of its inputs.

Design: docs/plans/2026-10-10-gamma-contours-and-well-design.md
"""
import math

from pages import fmt as _fmt

STRIKE, HEIGHT, NET = range(3)
# Built from their code points so the source holds no character an editor
# could silently change.
_LEFT_ARROW, _RIGHT_ARROW = chr(0x2190), chr(0x2192)


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _height(net, scale):
    """Minus the net, or minus its signed square root on the ``"root"`` scale.
    Either way a strike keeps its side of zero and its order among the rest."""
    if scale == "root":
        return 0.0 - math.copysign(math.sqrt(abs(net)), net)
    return 0.0 - net


def terrain(strikes, nets, scale="linear"):
    """``[(strike, height, net), …]`` in strike order. ``height`` is minus the
    strike's net gamma, or on ``scale="root"`` minus its signed square root:
    one strike can hold fifty times its neighbours, and drawn in proportion it
    is a spike on flat ground. ``net`` is always the real figure. A pair that
    is not two finite numbers is skipped."""
    return sorted((float(k), _height(float(n), scale), float(n))
                  for k, n in zip(strikes or (), nets or ())
                  if _number(k) and _number(n))


def _at(points, spot, column):
    """``column`` at ``spot``: on the straight line between the two strikes
    around it. None off the terrain (nothing is extrapolated)."""
    if not _number(spot) or len(points) < 2:
        return None
    for low, high in zip(points, points[1:]):
        if low[STRIKE] <= spot <= high[STRIKE]:
            if high[STRIKE] == low[STRIKE]:
                return low[column]
            share = (spot - low[STRIKE]) / (high[STRIKE] - low[STRIKE])
            return low[column] + (high[column] - low[column]) * share
    return None


def height_at(points, spot):
    """The ground's height at ``spot``, as drawn."""
    return _at(points, spot, HEIGHT)


def _downhill_from(points, spot):
    """The strike the ground first falls toward from ``spot``, as an index, or
    None on level ground. On a strike that is the steeper of its two sides."""
    strikes = [p[STRIKE] for p in points]
    heights = [p[HEIGHT] for p in points]
    if spot in strikes:
        at = strikes.index(spot)
        lower = [((heights[at] - heights[j]) / abs(strikes[j] - strikes[at]), j)
                 for j in (at - 1, at + 1)
                 if 0 <= j < len(points) and heights[j] < heights[at]]
        return max(lower)[1] if lower else None
    left = max(j for j, k in enumerate(strikes) if k < spot)
    if heights[left] == heights[left + 1]:
        return None
    return left if heights[left] < heights[left + 1] else left + 1


def read(points, spot):
    """Where price sits and which way the ground slopes, or None when ``spot``
    is off the terrain.

    ``ground``     ``"valley"`` (positive net gamma at spot), ``"hill"``
                   (negative) or ``"level"`` (zero).
    ``direction``  -1 when downhill is toward lower strikes, +1 toward higher,
                   0 when price is at a low point or on level ground.
    ``floor``      the strike where that downhill run ends: the first local low
                   reached. On level ground, the nearer strike.
    ``floor_net``  that strike's net gamma. Not always positive: between two
                   hills the low point is still negative gamma.
    """
    here = height_at(points, spot)
    if here is None:
        return None
    start = _downhill_from(points, spot)
    if start is None:
        end = min(range(len(points)), key=lambda j: abs(points[j][STRIKE] - spot))
        direction = 0
    else:
        direction = -1 if points[start][STRIKE] < spot else 1
        end = start
        while (0 <= end + direction < len(points)
               and points[end + direction][HEIGHT] < points[end][HEIGHT]):
            end += direction
    # From the nets, not the height: on a root scale the two differ, and which
    # side of zero price is on must be the real figure's.
    net = _at(points, spot, NET)
    return {"height": here, "net": net,
            "ground": "valley" if net > 0 else "hill" if net < 0 else "level",
            "direction": direction, "floor": points[end][STRIKE],
            "floor_height": points[end][HEIGHT], "floor_net": points[end][NET]}


def arrow(result):
    """The ball's label: an arrow toward the floor and the floor's price. Empty
    when there is no downhill."""
    if not result or not result["direction"]:
        return ""
    floor = _fmt.price(result["floor"])
    return (f"{_LEFT_ARROW} {floor}" if result["direction"] < 0
            else f"{floor} {_RIGHT_ARROW}")


def caption(result, spot):
    """The sentence under the panel. Empty with no read."""
    if not result:
        return ""
    where = {"valley": "Price sits in positive gamma, where dealer hedging damps moves.",
             "hill": "Price sits in negative gamma, where dealer hedging amplifies moves.",
             "level": "Price sits where net gamma is zero."}[result["ground"]]
    floor = _fmt.price(result["floor"])
    if not result["direction"]:
        slope = f"It is at the low point, {floor}."
    else:
        gap = _fmt.price(abs(result["floor"] - spot))
        side = "below" if result["floor"] < spot else "above"
        slope = f"The ground slopes toward {floor}, {gap} {side}."
        if result["floor_net"] <= 0:
            slope += " That low point is still negative gamma."
    return f"{where} {slope} A picture of the gamma profile, not a forecast."
