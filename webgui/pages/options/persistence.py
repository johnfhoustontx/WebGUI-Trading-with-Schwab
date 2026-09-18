"""Signal age + score-trend vocabulary over the day union's ``setups`` map.

PURE — no widgets, no bus. Both the Market Scanner and the Symbol Dossier render
from these, so the two cannot describe the same setup differently.

The map is built Tier-2-side by ``options_svc.compute.merge_setups``; this module
never derives a ``setup_key`` (rows carry one), so there is no cross-tier mirror.
"""
import datetime as _dt

from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)

# One hour at the 15-minute autoscan cadence. Under this, no direction is named.
TREND_WINDOW = 4

# The composite is recomputed from scratch every scan, so a small move between
# scans is noise rather than a fade.
TREND_DEADBAND = 2.0

DASH = "—"


def _hhmm(value):
    """``HH:MM`` for a timestamp, or :data:`DASH` when there is no time in it.

    Replaces a positional ``str(value)[11:16]`` slice, which was correct for
    every shape Tier 2 emits but whose FAILURE mode was an empty string — a
    malformed value where this module reserves a dash for "nothing was read".

    ⚠ ``datetime.fromisoformat`` accepts a DATE-ONLY string and invents
    midnight for it, so the obvious parse-and-format shape renders ``00:00`` —
    trading an empty string for a confidently wrong time, which is worse.
    ``date.fromisoformat`` succeeds on exactly the date-only strings, so the
    stdlib is the oracle for "did this carry a time at all" rather than another
    character count. A real 00:00 is left alone: it is a reading.
    """
    # datetime FIRST — it subclasses date, so the order is load-bearing.
    if isinstance(value, _dt.datetime):
        return value.strftime("%H:%M")
    if isinstance(value, _dt.date):     # a bare date carries no time to show
        return DASH
    text = str(value)
    try:
        _dt.date.fromisoformat(text)
    except (ValueError, TypeError):
        pass                            # not date-only: it may carry a time
    else:
        return DASH                     # date-only: never fabricate midnight
    try:
        return _dt.datetime.fromisoformat(text).strftime("%H:%M")
    except (ValueError, TypeError):
        return DASH


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

    ⚠ ``scores`` must be a RE-ITERABLE sequence, never a generator: it is read
    twice, by ``score_trend`` and by ``score_delta``, and that pair of calls is
    the whole reason ``delta`` cannot be None on the formatted branch below —
    both branch on the same length of the same series. A one-shot iterator
    would drain on the first call, yielding a non-``new`` trend beside a None
    delta, which is a TypeError in the format. A JSON decode always hands back
    a list, so this cannot arise today; the note is so nobody introduces one.
    """
    if not isinstance(setup, dict):
        # ⚠ gaps None, not 0 — ONE meaning for one key across both exits: None is
        # "no claim", an int is a reading. This branch returned 0 while the
        # unreadable-value branch below returned None, so `facts["gaps"] > 0`
        # raised on one path through this function and passed on the other.
        return {"since": DASH, "trend": "new", "trend_text": DASH,
                "gaps": None, "detail": ""}

    # ⚠ num, NOT float_or: float_or is PERMISSIVE by design and passes NaN
    # through, and int(float("nan")) raises ValueError — which would propagate
    # out of here into the row stamper. fmt.py's own rule: when the question is
    # "is this a real reading", use num.
    seen = _fmt.num(setup.get("seen"))
    first = setup.get("first_seen")
    stamp = DASH if setup.get("age_unknown") else _hhmm(first)
    if stamp == DASH:
        since = DASH
    elif seen is None:
        # ⚠ NEVER "09:15 · 0x". An unreadable count rendered as zero claims the
        # setup was seen no times, beside a stamp saying it was live at 09:15 —
        # a confident zero contradicting the very line it sits in. Say only the
        # part that was actually read.
        since = stamp
    else:
        since = f"{stamp} · {int(seen)}x"

    trend = score_trend(setup.get("scores"))
    delta = score_delta(setup.get("scores"))
    if trend == "new":
        trend_text = "new"
    else:
        trend_text = f"{_MARKS[trend]} {delta:+.1f}"

    # ⚠ None, not 0: zero gaps is a positive claim of unbroken continuity, so
    # folding "unreadable" into it is the same error one layer down. A falsy
    # value is omitted from the sentence either way, so only a consumer of the
    # returned dict can tell them apart — which is exactly who must.
    gaps_read = _fmt.num(setup.get("gaps"))
    gaps = None if gaps_read is None else int(gaps_read)
    detail = ""
    if since != DASH:
        detail = f"Live since {stamp}"
        if gaps:
            detail += f" · {gaps} gap" + ("s" if gaps != 1 else "")
    return {"since": since, "trend": trend, "trend_text": trend_text,
            "gaps": gaps, "detail": detail}
