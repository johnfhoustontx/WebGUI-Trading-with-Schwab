"""Signal age + score-trend vocabulary over the day union's ``setups`` map.

PURE — no widgets, no bus. Both the Market Scanner and the Symbol Dossier render
from these, so the two cannot describe the same setup differently.

The map is built Tier-2-side by ``options_svc.compute.merge_setups``; this module
never derives a ``setup_key`` (rows carry one), so there is no cross-tier mirror.
"""
from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)

# One hour at the 15-minute autoscan cadence. Under this, no direction is named.
TREND_WINDOW = 4

# The composite is recomputed from scratch every scan, so a small move between
# scans is noise rather than a fade.
TREND_DEADBAND = 2.0

DASH = "—"


def _usable(scores):
    """The series as real floats, dropping anything ``num`` rejects.

    ⚠ It returns the COERCED value, not the original. Filtering with ``num`` and
    then keeping the raw entry is the trap: ``num("60.0")`` is 60.0, so a string
    survives the filter and then raises ``TypeError`` on the subtraction below —
    a parsing guard mistaken for a value guard, the same shape as the ``_num``
    family in ``scoring/``.
    """
    out = []
    for value in scores or []:
        number = _fmt.num(value)
        if number is not None:
            out.append(number)
    return out


def score_trend(scores, window=TREND_WINDOW, deadband=TREND_DEADBAND):
    """``"new" | "rising" | "steady" | "fading"`` for a setup's score series.

    Fewer than ``window`` readings -> ``"new"``, claiming NO direction. The
    comparison reads from the END of the series, so a setup that collapsed this
    morning and has climbed for an hour reads as rising rather than fading.
    """
    values = _usable(scores)
    if len(values) < window:
        return "new"
    delta = values[-1] - values[-window]
    if abs(delta) <= deadband:
        return "steady"
    return "rising" if delta > 0 else "fading"


def score_delta(scores, window=TREND_WINDOW):
    """The signed move over the trend window, or ``None`` when undefined."""
    values = _usable(scores)
    if len(values) < window:
        return None
    return values[-1] - values[-window]


_MARKS = {"rising": "▲", "fading": "▼", "steady": "▬"}


def persistence_facts(setup):
    """Display facts for one setup entry. Total — an absent entry is a dash.

    ⚠ Three different absences must not render alike: no entry at all (the row
    has no derivable setup_key, or the map failed to build), a known-present
    setup whose age cannot be claimed (``age_unknown`` — a cold start), and a
    setup with too few readings to name a direction.
    """
    if not isinstance(setup, dict):
        return {"since": DASH, "trend": "new", "trend_text": DASH,
                "gaps": 0, "detail": ""}

    # ⚠ num, NOT float_or: float_or is PERMISSIVE by design and passes NaN
    # through, and int(float("nan")) raises ValueError — which would propagate
    # out of here into the row stamper. fmt.py's own rule: when the question is
    # "is this a real reading", use num.
    seen = int(_fmt.num(setup.get("seen")) or 0)
    first = setup.get("first_seen")
    if setup.get("age_unknown") or not first:
        since = DASH
    else:
        since = f"{str(first)[11:16]} · {seen}x"

    trend = score_trend(setup.get("scores"))
    delta = score_delta(setup.get("scores"))
    if trend == "new":
        trend_text = "new"
    else:
        trend_text = f"{_MARKS[trend]} {delta:+.1f}"

    gaps = int(_fmt.num(setup.get("gaps")) or 0)
    detail = ""
    if since != DASH:
        detail = f"Live since {str(first)[11:16]}"
        if gaps:
            detail += f" · {gaps} gap" + ("s" if gaps != 1 else "")
    return {"since": since, "trend": trend, "trend_text": trend_text,
            "gaps": gaps, "detail": detail}
