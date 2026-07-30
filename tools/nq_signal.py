"""
nq_signal.py - NQ Dealer-Positioning HUD, pure signal logic
Version: 1.0.0
Last Updated: 2026-07-29

The PURE half of the NQ HUD: level conversion, regime classification, session
gating, stop sizing, and the trade verdict. No I/O, no Redis, no SQLite, no
tkinter — every function takes scalars/dicts and returns scalars/dicts, so the
whole decision surface is unit-testable without the stack running.

`tools/nq_hud.py` owns the readers and the CustomTkinter shell and imports from
here. Design: docs/plans/2026-07-29-nq-dealer-positioning-hud-design.md

WHY THE SPLIT: this module decides whether to risk money. It has one invariant
that must never regress (never fade a short-gamma tape) and a target-side rule
that is easy to get subtly wrong, so it is kept free of anything that needs a
running service to exercise.

REUSE: the above/below gamma call is delegated to
``services.options_svc.matrix.gex_regime`` rather than duplicated — that module
is pure (imports nothing but ``__future__``), already CI-covered by
``test_matrix.py``, and owns the same concept for the Options Matrix. The ±0.30%
FLIP ZONE stays local: ``gex_regime`` is a bare ``spot >= flip`` with no such
band, and the band is this HUD's primary risk control.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date as _date, time as _time

# Repo root on sys.path so ``services.options_svc.matrix`` resolves regardless of
# the cwd the tests or the HUD are launched from (same idiom as the other
# tools/ tests and tools/dry_run_dealer_regime.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.options_svc import matrix as mx  # noqa: E402  (pure, no I/O)

#############################################
# CONFIGURATION
#############################################

# Regime classification.
FLIP_ZONE_PCT = 0.003        # +/-0.30% of spot around flip = no-trade band
WALL_PROXIMITY_PCT = 0.0015  # within 0.15% of a wall = "at" the wall

# RTH trading windows (CT). The HUD is day-trade only and never signals
# outside these. 08:30-08:45 is skipped (opening rotation), flat by 14:55.
RTH_OPEN = _time(8, 30)
TRADE_START = _time(8, 45)   # skip the first 15 minutes
PIN_WINDOW_START = _time(11, 30)   # best mean-reversion window
PIN_WINDOW_END = _time(14, 0)
FLATTEN_BY = _time(14, 55)
RTH_CLOSE = _time(15, 0)

# Risk sizing. Stops are ATR-scaled, never fixed — NQ needs more room than ES.
STOP_ATR_MULT = 1.2
MIN_STOP_POINTS = 15.0
MAX_STOP_POINTS = 45.0

# Market holidays — kept in sync with services/*/scheduler.py _HOLIDAYS.
HOLIDAYS = {
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
}


#############################################
# TIME / SESSION HELPERS
#############################################

def is_trading_day(now) -> bool:
    return now.weekday() < 5 and now.date() not in HOLIDAYS


def session_phase(now):
    """Classify the current point in the RTH session.

    Returns one of: 'closed', 'premarket', 'opening', 'morning', 'pin',
    'afternoon', 'flatten'. Drives both the trade gate and the time-of-day
    guidance line.
    """
    if not is_trading_day(now):
        return "closed"
    t = now.time()
    if t < RTH_OPEN:
        return "premarket"
    if t >= RTH_CLOSE:
        return "closed"
    if t < TRADE_START:
        return "opening"
    if t >= FLATTEN_BY:
        return "flatten"
    if PIN_WINDOW_START <= t < PIN_WINDOW_END:
        return "pin"
    if t < PIN_WINDOW_START:
        return "morning"
    return "afternoon"


PHASE_NOTE = {
    "closed": "Market closed — levels shown are last session's.",
    "premarket": "Pre-open. Gamma map loading; no signals before 08:30 CT.",
    "opening": "Opening rotation — overnight hedges unwinding. No trade until 08:45.",
    "morning": "Morning session. Trend resolution often visible after 10:00 CT.",
    "pin": "Lunch drift — strongest pinning window. Best mean-reversion setups.",
    "afternoon": "Afternoon. Charm/vanna accelerate into the close.",
    "flatten": "Flatten now — day-trade only. No new entries.",
}


def tradeable(phase) -> bool:
    return phase in ("morning", "pin", "afternoon")


#############################################
# CONVERSION
#############################################

def ndx_scale(source_symbol, ndx_spot, source_spot):
    """Multiplier converting a SOURCE-symbol price into an NDX-equivalent price.

    $NDX -> 1.0. QQQ -> the live NDX/QQQ ratio (~41), recomputed every poll
    because it drifts. Returns None when it cannot be established.
    """
    if source_symbol == "$NDX":
        return 1.0
    if not ndx_spot or not source_spot or source_spot <= 0:
        return None
    return ndx_spot / source_spot


def to_nq(level, scale, basis):
    """Convert a source-symbol strike into NQ futures points.

    level -> NDX-equivalent (x scale) -> NQ (+ basis). Basis is the live
    NQ - NDX carry spread and jumps at the quarterly roll, so it is measured,
    never assumed.
    """
    if level is None or scale is None or basis is None:
        return None
    return level * scale + basis


#############################################
# REGIME
#############################################

def classify_regime(nq_spot, nq_flip):
    """Dealer-gamma regime from NQ spot vs the NQ-converted flip.

    Returns (regime, distance_points). Regime is 'positive', 'negative',
    'flip_zone', or 'unknown'.

    The flip zone is checked FIRST and is deliberately local: it is the whipsaw
    band where most losses come from, so it is a first-class state rather than a
    gap between the other two. ``matrix.gex_regime`` has no such band (it is a
    bare ``spot >= flip``), so it cannot express this — but it does own the
    above/below call, which is delegated so the two never drift.
    """
    if nq_spot is None or nq_flip is None:
        return "unknown", None
    dist = nq_spot - nq_flip
    band = nq_spot * FLIP_ZONE_PCT
    if abs(dist) <= band:
        return "flip_zone", dist
    reg = mx.gex_regime(nq_spot, nq_flip)
    if reg == "na":                      # defensive: unreachable given the guard
        return "unknown", dist
    return ("positive" if reg == "above" else "negative"), dist


# Phases in which the $NDX CASH index is actually trading (08:30-15:00 CT).
# "opening" and "flatten" are inside RTH — they are excluded from *trading* by
# tradeable(), not from cash being live.
CASH_LIVE_PHASES = ("opening", "morning", "pin", "afternoon", "flatten")


def cash_stale_reason(phase, snap_age_s, stale_after):
    """Why the regime cannot be trusted right now, or None when it can.

    THE PROBLEM THIS GUARDS. Basis is measured as ``NQ - NDX``, so the regime
    distance algebraically reduces to ``NDX - flip`` — the live futures price
    cancels out completely (verified: moving NQ 950 points leaves the distance
    unchanged). The regime is therefore anchored to the CASH index, which stops
    ticking outside 08:30-15:00 CT. Premarket the HUD would otherwise show a
    confident band computed from yesterday's close while NQ has moved overnight:
    live-looking, actually frozen.

    Two independent failure modes, because neither subsumes the other:

    * cash not trading — covers the overnight/weekend case;
    * gamma map stale — covers a dead collector during RTH.

    The 08:00-08:30 collection window needs the first: snapshots are FRESH there
    (the collector starts at 08:00) but cash has not opened, so an age check
    alone would pass it.
    """
    # Kept SHORT: this is appended to the regime line, which has no wraplength,
    # so a long reason would clip horizontally rather than wrap.
    if phase not in CASH_LIVE_PHASES:
        return "cash index closed"
    if snap_age_s is not None and snap_age_s > stale_after:
        return f"gamma map {int(snap_age_s)}s stale"
    return None


def near(price, level, spot):
    """True when ``price`` sits within WALL_PROXIMITY_PCT of ``level``."""
    if price is None or level is None or not spot:
        return False
    return abs(price - level) <= spot * WALL_PROXIMITY_PCT


#############################################
# RISK
#############################################

def stop_points(atr_proxy_nq):
    """ATR-scaled stop distance in NQ points, clamped to a sane band.

    KNOWN ASYMMETRY (design §6): ``atr_proxy_nq`` is the session range SO FAR, so
    early in the session it is near zero and this clamps to MIN_STOP_POINTS —
    tightest exactly when the tape is most volatile. Seeding from the prior
    session is a deferred follow-up; it changes sizing, so it wants logged data.
    """
    if not atr_proxy_nq or atr_proxy_nq <= 0:
        return MIN_STOP_POINTS
    raw = atr_proxy_nq * STOP_ATR_MULT / 6.0   # session range -> per-trade unit
    return max(MIN_STOP_POINTS, min(MAX_STOP_POINTS, raw))


#############################################
# VERDICT
#############################################

def _stop_above(entry, wall, sp):
    """Stop for a SHORT: beyond the wall, but never at or below the entry.

    Spot can sit ABOVE the call wall and still be "at" it — the proximity band
    is ~35 NQ points at 23,000, which exceeds MIN_STOP_POINTS. The wall-derived
    stop would then land below the entry, i.e. a short already stopped out the
    moment it is taken. Floor it at ``sp`` beyond the entry so the ATR-derived
    distance is always honoured.
    """
    return max(wall + sp, entry + sp)


def _stop_below(entry, wall, sp):
    """Stop for a LONG: beyond the wall, but never at or above the entry."""
    return min(wall - sp, entry - sp)

def build_verdict(regime, phase, nq_spot, levels, atr_nq):
    """Produce the trade verdict.

    Returns {"action", "reason", "entry", "stop", "target"}. Action is
    LONG / SHORT / STAND DOWN / WAIT.

    Returns a SEMANTIC action only — no colour. The HUD maps action->colour at
    paint time; keeping presentation out of here is what lets this module be
    imported without tkinter.

    Rules mirror the strategy:
      * positive gamma -> FADE the walls toward the pin (mean reversion)
      * negative gamma -> only CONTINUATION on a wall break; never fade
      * flip zone      -> STAND DOWN, always
    """
    out = {"action": "STAND DOWN", "reason": "",
           "entry": None, "stop": None, "target": None}

    # Phase is checked FIRST so its specific note wins. The cash-freshness guard
    # forces regime to "unknown" outside RTH, and "no gamma map" would be a lie
    # there — there IS a map, it is just the last session's. Both outcomes mean
    # don't trade; the phase note is the one that explains why.
    if not tradeable(phase):
        out["action"] = "WAIT"
        out["reason"] = PHASE_NOTE.get(phase, "Outside the trading window.")
        return out

    if regime == "unknown":
        out["reason"] = "No gamma map — cannot classify regime."
        return out

    if regime == "flip_zone":
        out["reason"] = ("Spot is inside the flip zone (±0.30%). This is the "
                         "whipsaw band — no trade.")
        return out

    call_wall = levels.get("call_wall")
    put_wall = levels.get("put_wall")
    pin = levels.get("pin")
    sp = stop_points(atr_nq)

    if regime == "positive":
        if near(nq_spot, call_wall, nq_spot):
            if pin is None or pin >= nq_spot:
                # No mean-reversion target on the profitable side. The pin is
                # the max-|net| strike ANYWHERE in the grid — nothing pins it
                # between spot and the trade's direction — so this is simply
                # not a setup. Substituting a different level would silently
                # change what the signal means.
                out["action"] = "WAIT"
                out["reason"] = ("Positive gamma at the call wall, but the pin "
                                 "is not below spot — no fade target. Waiting.")
                return out
            out.update(action="SHORT", entry=nq_spot,
                       stop=_stop_above(nq_spot, call_wall, sp), target=pin,
                       reason=("Positive gamma at the call wall. Dealers sell "
                               "rallies — fade toward the pin. Wait for two "
                               "5-min closes failing above the wall."))
            return out
        if near(nq_spot, put_wall, nq_spot):
            if pin is None or pin <= nq_spot:
                out["action"] = "WAIT"
                out["reason"] = ("Positive gamma at the put wall, but the pin "
                                 "is not above spot — no fade target. Waiting.")
                return out
            out.update(action="LONG", entry=nq_spot,
                       stop=_stop_below(nq_spot, put_wall, sp), target=pin,
                       reason=("Positive gamma at the put wall. Dealers buy "
                               "dips — fade toward the pin. Wait for two "
                               "5-min closes holding above the wall."))
            return out
        out["action"] = "WAIT"
        out["reason"] = ("Positive gamma, mid-range. Setups only AT the walls "
                         "— chasing mid-range is how you get pinned.")
        return out

    # Negative gamma — continuation only.
    if call_wall is not None and nq_spot > call_wall:
        out.update(action="LONG", entry=nq_spot,
                   stop=call_wall - sp, target=None,
                   reason=("Negative gamma, broken ABOVE the call wall. Dealer "
                           "hedging amplifies — trade continuation, enter on "
                           "the retest that holds. Trail; do not fade."))
        return out
    if put_wall is not None and nq_spot < put_wall:
        out.update(action="SHORT", entry=nq_spot,
                   stop=put_wall + sp, target=None,
                   reason=("Negative gamma, broken BELOW the put wall. Dealer "
                           "hedging amplifies — trade continuation, enter on "
                           "the retest that holds. Trail; do not fade."))
        return out

    out["action"] = "WAIT"
    out["reason"] = ("Negative gamma but still inside the walls. Wait for a "
                     "break — never fade a short-gamma tape.")
    return out
