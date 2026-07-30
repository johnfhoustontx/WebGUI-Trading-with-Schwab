"""
nq_signal.py - Dealer-Positioning HUD, pure signal logic
Version: 1.1.0
Last Updated: 2026-07-30

Version 1.1.0 Changes:
- Instrument-agnostic: NQ and ES both flow through this module. Everything that
  differs between them arrives as an ``Instrument`` spec (tools/nq_instruments.py)
  rather than being a module constant.
- ndx_scale -> cash_scale, to_nq -> to_future. The old names named ONE
  instrument's cash index and future, which reads as a lie when the same call
  converts an SPX strike into ES points — and frame confusion is the failure
  mode this code has already had to be fixed for twice.

The PURE half of the HUD: level conversion, regime classification, session
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

# Risk sizing. Stops are ATR-scaled, never fixed. The MULTIPLIER is shared —
# it converts a session range into a per-trade unit, which is a proportion and
# so instrument-independent. The CLAMPS are not: they are denominated in points,
# and NDX trades near 4x SPX, so they live on the Instrument spec.
STOP_ATR_MULT = 1.2

# Minimum reward-to-risk for a mean-reversion fade.
#
# MEASURED, not chosen by feel. Replaying a full live session through this
# module, the fade setups are BIMODAL: either the pin sits almost on top of
# spot — median reward 17.9 NQ points against a median 60-point stop, an R:R of
# 0.31, with 85% of NQ signals below 1:1 — or it is genuinely far, median reward
# 110 points. There is very little in between. Gating at 1.5 keeps 5 of 42 NQ
# signals at a median 1.71, and 9 of 14 ES signals at a median 5.32.
#
# The gate only REFUSES setups. It does not move a stop or substitute a
# different target, because both of those would change what the signal means;
# this just declines to call a 0.31:1 trade a trade.
#
# It is a pure ratio, so unlike the stop clamps it is instrument-independent
# and correctly lives here rather than on the Instrument spec.
MIN_REWARD_RISK = 1.5

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

def cash_scale(source_symbol, cash_spot, source_spot):
    """Multiplier converting a SOURCE-symbol price into a CASH-index price.

    The cash index itself -> 1.0. An ETF proxy -> the live index/ETF ratio
    (~41 for NDX/QQQ, ~10 for SPX/SPY), recomputed every poll because it drifts.
    Returns None when it cannot be established.

    The cash-index test is the "$" prefix rather than a literal "$NDX": that
    prefix IS Schwab's symbology for a cash index ($SPX, $NDX, $VIX), while the
    ETF fallbacks (QQQ, SPY) are plain tickers. So one rule serves every
    instrument instead of needing a hardcoded symbol per pane.
    """
    if source_symbol and source_symbol.startswith("$"):
        return 1.0
    if not cash_spot or not source_spot or source_spot <= 0:
        return None
    return cash_spot / source_spot


def to_index(level, scale):
    """Convert a source-symbol strike into INDEX (cash-equivalent) points.

    This is the frame DECISIONS are made in — see the module docstring and
    design §5. A cash-index strike needs no conversion (scale 1.0); an ETF
    strike is multiplied up to its index equivalent.
    """
    if level is None or scale is None:
        return None
    return level * scale


def to_future(level, scale, basis):
    """Convert a source-symbol strike into FUTURES points, for DISPLAY.

    level -> index-equivalent (x scale) -> future (+ basis). Basis is the live
    future - cash carry spread and jumps at the quarterly roll, so it is
    measured, never assumed.

    The two frames differ by the additive basis and nothing else, so distances
    (ATR, stop size, wall proximity) are identical in both and only LEVELS need
    shifting. That is what makes deciding in cash terms and rendering in NQ
    terms a pure change of units rather than a change of answer.
    """
    if level is None or scale is None or basis is None:
        return None
    return to_index(level, scale) + basis


def shift_verdict_levels(verdict, basis):
    """Return a copy of ``verdict`` with its price fields moved into futures points.

    The verdict is computed in cash terms; entry/stop/target are the three
    fields a trader types into NinjaTrader, so they — and only they — get the
    basis added. A missing or zero basis is a no-op, never a wrong number.
    """
    out = dict(verdict)
    if not basis:
        return out
    for field in ("entry", "stop", "target"):
        if out.get(field) is not None:
            out[field] = out[field] + basis
    return out


#############################################
# REGIME
#############################################

def classify_regime(spot, flip):
    """Dealer-gamma regime from spot vs the flip, BOTH in the same frame.

    Returns (regime, distance_points). Regime is 'positive', 'negative',
    'flip_zone', or 'unknown'.

    The flip zone is checked FIRST and is deliberately local: it is the whipsaw
    band where most losses come from, so it is a first-class state rather than a
    gap between the other two. ``matrix.gex_regime`` has no such band (it is a
    bare ``spot >= flip``), so it cannot express this — but it does own the
    above/below call, which is delegated so the two never drift.
    """
    if spot is None or flip is None:
        return "unknown", None
    dist = spot - flip
    band = spot * FLIP_ZONE_PCT
    if abs(dist) <= band:
        return "flip_zone", dist
    reg = mx.gex_regime(spot, flip)
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


def pick_flip(computed, stored):
    """Choose the gamma flip: prefer the grid-recomputed value.

    The stored ``flip`` column is ``snapshot_summary``'s rule — the zero
    crossing NEAREST TO SPOT. As a regime boundary that is close to circular:
    the nearest crossing is by definition near spot, so asking "is spot inside
    the flip zone?" answers itself whenever the profile crosses zero anywhere
    close. Measured over 55 live $NDX snapshots it read flip_zone on 54 of
    them (98%), i.e. the HUD stood down by construction.

    ``gamma_tool.calc_flip_point`` instead takes the lowest crossing in the
    +/-3% band, which is a structural level rather than a spot-hugging one:
    38% flip_zone over the same snapshots, median disagreement 297 points.

    The stored value remains the fallback so a grid that yields no crossing
    still produces a regime rather than going blank. This mirrors what the HUD
    already does for the walls and the pin, which are likewise recomputed from
    each snapshot's own grid rather than trusted from stored columns.
    """
    return computed if computed is not None else stored


def near(price, level, spot):
    """True when ``price`` sits within WALL_PROXIMITY_PCT of ``level``."""
    if price is None or level is None or not spot:
        return False
    return abs(price - level) <= spot * WALL_PROXIMITY_PCT


#############################################
# RISK
#############################################

def stop_points(atr_proxy, spec):
    """ATR-scaled stop distance in ``spec``'s points, clamped to its band.

    The clamps come from the Instrument spec and are REQUIRED, not defaulted:
    they are denominated in points, and NDX trades near 4x SPX, so silently
    falling back to NQ's band would size an ES stop at roughly four times the
    intended risk. A missing argument should be a TypeError, not a bad fill.

    KNOWN ASYMMETRY (design §6): ``atr_proxy`` is the session range SO FAR, so
    early in the session it is near zero and this clamps to ``spec.min_stop`` —
    tightest exactly when the tape is most volatile. Seeding from the prior
    session is a deferred follow-up; it changes sizing, so it wants logged data.
    """
    if not atr_proxy or atr_proxy <= 0:
        return spec.min_stop
    raw = atr_proxy * STOP_ATR_MULT / 6.0   # session range -> per-trade unit
    return max(spec.min_stop, min(spec.max_stop, raw))


#############################################
# VERDICT
#############################################

def reward_risk(entry, stop, target):
    """Reward-to-risk of a formed setup, or None when it cannot be computed.

    None rather than 0 for an unbounded-target continuation trade: those are
    trailed, so there is no target to divide by and "no ratio" is the honest
    answer. A caller gating on a minimum must treat None as ungated, not as a
    failure — see build_verdict, where the negative-gamma branches are
    deliberately not gated.
    """
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


def _thin_edge(out, rr, side):
    """WAIT because the fade does not pay for its own stop.

    The R:R is NAMED in the reason. "Waiting" with no number reads as the HUD
    being coy; the whole point is that the setup exists but the pin is too close
    to the wall to be worth the risk, and seeing 0.31:1 makes that obvious.
    """
    out["action"] = "WAIT"
    out["rr"] = rr
    ratio = "no target" if rr is None else f"{rr:.2f}:1"
    out["reason"] = (
        f"Positive gamma at the {side} wall, but the pin is too close to pay "
        f"for the stop — {ratio} against a {MIN_REWARD_RISK:.1f}:1 minimum. "
        f"Waiting for spot and the pin to separate.")
    return out


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

def build_verdict(regime, phase, spot, levels, atr_proxy, spec):
    """Produce the trade verdict for one instrument.

    ``spot``, ``levels`` and ``atr_proxy`` must all be in the SAME frame — the
    HUD passes cash. ``spec`` supplies the stop band; see stop_points on why it
    is required rather than defaulted.

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
    out = {"action": "STAND DOWN", "reason": "", "rr": None,
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
    sp = stop_points(atr_proxy, spec)

    if regime == "positive":
        if near(spot, call_wall, spot):
            if pin is None or pin >= spot:
                # No mean-reversion target on the profitable side. The pin is
                # the max-|net| strike ANYWHERE in the grid — nothing pins it
                # between spot and the trade's direction — so this is simply
                # not a setup. Substituting a different level would silently
                # change what the signal means.
                out["action"] = "WAIT"
                out["reason"] = ("Positive gamma at the call wall, but the pin "
                                 "is not below spot — no fade target. Waiting.")
                return out
            stop = _stop_above(spot, call_wall, sp)
            rr = reward_risk(spot, stop, pin)
            if rr is None or rr < MIN_REWARD_RISK:
                return _thin_edge(out, rr, "call")
            out.update(action="SHORT", entry=spot, stop=stop, target=pin, rr=rr,
                       reason=("Positive gamma at the call wall. Dealers sell "
                               "rallies — fade toward the pin. Wait for two "
                               "5-min closes failing above the wall."))
            return out
        if near(spot, put_wall, spot):
            if pin is None or pin <= spot:
                out["action"] = "WAIT"
                out["reason"] = ("Positive gamma at the put wall, but the pin "
                                 "is not above spot — no fade target. Waiting.")
                return out
            stop = _stop_below(spot, put_wall, sp)
            rr = reward_risk(spot, stop, pin)
            if rr is None or rr < MIN_REWARD_RISK:
                return _thin_edge(out, rr, "put")
            out.update(action="LONG", entry=spot, stop=stop, target=pin, rr=rr,
                       reason=("Positive gamma at the put wall. Dealers buy "
                               "dips — fade toward the pin. Wait for two "
                               "5-min closes holding above the wall."))
            return out
        out["action"] = "WAIT"
        out["reason"] = ("Positive gamma, mid-range. Setups only AT the walls "
                         "— chasing mid-range is how you get pinned.")
        return out

    # Negative gamma — continuation only.
    if call_wall is not None and spot > call_wall:
        out.update(action="LONG", entry=spot,
                   stop=call_wall - sp, target=None,
                   reason=("Negative gamma, broken ABOVE the call wall. Dealer "
                           "hedging amplifies — trade continuation, enter on "
                           "the retest that holds. Trail; do not fade."))
        return out
    if put_wall is not None and spot < put_wall:
        out.update(action="SHORT", entry=spot,
                   stop=put_wall + sp, target=None,
                   reason=("Negative gamma, broken BELOW the put wall. Dealer "
                           "hedging amplifies — trade continuation, enter on "
                           "the retest that holds. Trail; do not fade."))
        return out

    out["action"] = "WAIT"
    out["reason"] = ("Negative gamma but still inside the walls. Wait for a "
                     "break — never fade a short-gamma tape.")
    return out
