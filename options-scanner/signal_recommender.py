"""Rule-based HOLD/TAKE_PROFIT/CUT recommender for open swing signals.

Also exposes `build_mark` — a shared helper that takes a signal row + a
signal_repricer.reprice_swing result and produces a complete signal_marks
row with current_score, drift, and recommendation populated. Used by both
the dashboard's "Refresh marks now" action and the EOD pipeline so they
write structurally identical rows.
"""

import pathlib as _pathlib
import sys as _sys
from datetime import date

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import trade_mgmt as _trade_mgmt  # noqa: E402

MULTIPLIER = 100

# ── The stop rules live in config/trade_mgmt.toml ───────────────────────────
# Read through shared.trade_mgmt because services/options_svc/rescue.py needs the
# SAME numbers to decide what is at risk. It used to restate four of them by hand
# under a comment asking future editors to keep the mirror in step; it now derives
# them from the very dict below. Edit the TOML and restart options_svc.
_STOPS = _trade_mgmt.stops()

TP_FRAC = _STOPS["tp_frac"]                 # >= this credit captured -> ARM break-even
STOP_MULT = _STOPS["stop_mult"]             # cut at >= this x credit, as a loss
DELTA_DRIFT = _STOPS["delta_drift"]         # cut when short delta drifts this far past entry
DELTA_HARD_CEILING = _STOPS["delta_hard_ceiling"]  # ...but never hold past this
DELTA_ABS_FALLBACK = _STOPS["delta_abs_fallback"]  # absolute breach when entry delta unknown
CUT_DTE = _STOPS["cut_dte"]                 # cut when DTE <= this and underwater
RECOVERY_DTE_MIN = _STOPS["recovery_dte_min"]      # min DTE to DEFER a soft delta stop
RECOVERY_MIN_CUSHION = _STOPS["recovery_min_cushion"]  # min spot<->strike cushion to defer

# Peak-driven profit-lock ladder for the armed break-even stop (Rule 3). Each rung
# ``(peak_frac, lock_frac)``: once the trade's PEAK profit reaches ``peak_frac`` of
# the credit, the stop ratchets up to lock in ``lock_frac`` of the credit. The
# DEFAULT is a single break-even rung (lock 0.0) — i.e. exactly the plain
# break-even stop — so the ratchet is INERT until a caller passes a richer ladder
# plus ``peak_pnl_frac`` in ctx. RATCHET_TRAIL_LADDER is the OPT-IN alternative,
# not wired to any caller yet.
DEFAULT_TRAIL_LADDER = _trade_mgmt.default_trail_ladder()
RATCHET_TRAIL_LADDER = _trade_mgmt.ratchet_trail_ladder()


def track_thresholds(entry_credit):
    """Mid-price levels for streaming target/stop detection, derived from the
    canonical TP_FRAC / STOP_MULT so the proxy never hardcodes a rule.

    target_mid: close the spread for <= this debit  => >= TP_FRAC credit captured.
    stop_mid:   close debit >= this                 => >= STOP_MULT credit loss.
    """
    return {
        "target_mid": round(entry_credit * (1 - TP_FRAC), 2),
        "stop_mid": round(entry_credit * (1 + STOP_MULT), 2),
    }


def _recoverable(ctx):
    """True when a SOFT delta stop should be DEFERRED (HOLD) for a recoverable trade.

    Requires enough time (``dte_remaining >= RECOVERY_DTE_MIN``), the short strike
    NOT breached, and spot at least ``RECOVERY_MIN_CUSHION`` away from the short
    strike (per relevant side for an IC). Needs ``strategy`` + ``spot`` +
    ``short_strike`` (+ ``call_short`` for IC) in ``ctx``; when those are absent
    the trade is NOT recoverable (recovery off → the delta stop fires as before),
    preserving back-compat for callers that don't supply them."""
    dte = ctx.get("dte_remaining", 99)
    if dte is None or dte < RECOVERY_DTE_MIN:
        return False
    strategy = (ctx.get("strategy") or "").upper()
    spot = ctx.get("spot")
    short = ctx.get("short_strike")
    if spot is None or short is None or spot <= 0 or strategy not in ("PCS", "CCS", "IC"):
        return False
    cushions = []
    # Put side: PCS short + the IC put short live in ``short_strike``. Breached
    # (and NOT recoverable) once spot trades at/through it.
    if strategy in ("PCS", "IC"):
        if spot <= short:
            return False
        cushions.append(abs(spot - short) / spot)
    # Call side: the CCS short call is ``short_strike``; the IC call short is
    # ``call_short``. Breached once spot trades at/through it.
    if strategy in ("CCS", "IC"):
        call_short = ctx.get("call_short") if strategy == "IC" else short
        if call_short is None:
            return False
        if spot >= call_short:
            return False
        cushions.append(abs(spot - call_short) / spot)
    if not cushions:
        return False
    return min(cushions) >= RECOVERY_MIN_CUSHION


def _locked_profit_level(ctx, credit_total):
    """Dollar profit-lock floor from the peak-driven trailing ladder (the "ratchet").

    ``ctx["trail_ladder"]`` is a list of ``(peak_frac, lock_frac)`` rungs (default
    ``DEFAULT_TRAIL_LADDER``); once the trade's PEAK profit fraction
    (``ctx["peak_pnl_frac"]`` = the best ``pnl/credit_total`` reached) clears a
    rung's ``peak_frac``, the stop locks in ``lock_frac`` of the credit. Returns
    ``lock_frac* * credit_total`` for the highest cleared rung, or 0.0 when nothing
    clears / the peak or a rung is missing/malformed. The DEFAULT ladder (single
    break-even rung, lock 0.0) always returns 0.0, so the break-even stop is
    unchanged unless a richer ladder AND a peak are supplied — the ratchet is inert
    by default."""
    ladder = ctx.get("trail_ladder") or DEFAULT_TRAIL_LADDER
    peak = ctx.get("peak_pnl_frac")
    if peak is None:
        return 0.0
    try:
        peak = float(peak)
    except (TypeError, ValueError):
        return 0.0
    lock = 0.0
    for rung in ladder:
        try:
            peak_frac, lock_frac = rung
            if peak >= peak_frac:
                lock = max(lock, float(lock_frac))
        except (TypeError, ValueError):
            continue
    return lock * credit_total


def recommend(ctx):
    """Return {'action', 'reason', 'code'}. action in HOLD/CUT; code in
    HOLD/BREAKEVEN_STOP/MONEY_STOP/DELTA_STOP/TIME_STOP.

    Lifecycle (first match wins — see the captured-autoclose design):
      1. Money-stop (HARD): pnl <= -STOP_MULT*credit → CUT/MONEY_STOP.
      2. Time-stop (HARD): DTE <= CUT_DTE and underwater → CUT/TIME_STOP.
      3. Break-even stop (only when ``be_armed``): pnl <= ``be_level`` → CUT/BREAKEVEN_STOP.
      4. Delta-stop (SOFT): a delta drift/ceiling breach → CUT/DELTA_STOP, UNLESS
         ``_recoverable(ctx)`` (defers to HOLD).
      5. +50% credit captured and not yet armed: LIFECYCLE callers
         (``ctx["lifecycle"]`` — captured signals, via ``build_mark``) ARM
         break-even and HOLD ("break-even armed"), riding to full credit under the
         break-even stop; the caller persists ``be_armed``. NON-lifecycle callers
         (the manual-paper + driver manage cycles, via
         ``paper_engine.run_manage_cycle``) TAKE_PROFIT (``TARGET_HIT``) — the
         pre-lifecycle behavior, so the captured-autoclose rework never changes how
         those separate books exit.
      6. HOLD with the score-drift note.
    """
    credit_total = ctx["entry_credit"] * MULTIPLIER
    pnl = ctx.get("unrealized_pnl") or 0
    short_delta = ctx.get("current_short_delta")
    dte = ctx.get("dte_remaining", 99)

    # Rule 1: 2x credit money-stop (HARD floor — always fires)
    if pnl <= -STOP_MULT * credit_total:
        return {"action": "CUT", "reason": f"{STOP_MULT:g}x credit stop",
                "code": "MONEY_STOP"}

    # Rule 2: low DTE and underwater (HARD floor). An armed, PROFITABLE trade near
    # expiry is NOT time-stopped (it rides to full credit, protected by the
    # break-even stop below).
    if dte <= CUT_DTE and pnl < 0:
        return {"action": "CUT", "reason": f"DTE <= {CUT_DTE} and underwater",
                "code": "TIME_STOP"}

    # Rule 3: profit-lock stop — only once +50% has been seen (``be_armed``). Cuts
    # when the give-back reaches the locked floor, so the trade never returns a loss
    # after having shown a real profit. The floor is the GREATER of the break-even +
    # round-trip-commissions level and the peak-driven profit lock (the "ratchet",
    # ``_locked_profit_level``). With the DEFAULT ladder the lock is $0, so this is
    # exactly the plain break-even stop; a richer ``trail_ladder`` + ``peak_pnl_frac``
    # ratchets the floor up to protect banked profit.
    if ctx.get("be_armed"):
        be_level = ctx.get("be_level") or 0.0
        stop_level = max(be_level, _locked_profit_level(ctx, credit_total))
        if pnl <= stop_level:
            return {"action": "CUT",
                    "reason": "break-even stop (protecting the +50% give-back)",
                    "code": "BREAKEVEN_STOP"}

    # Rule 4: delta drift from entry, with an absolute hard ceiling. Measures
    # adverse movement relative to where the position was opened — a position
    # entered at 0.30 delta shouldn't be cut on noise the way an absolute 0.35
    # threshold would, but nothing rides past the hard ceiling. When the caller
    # cannot supply the entry delta (e.g. the paper engine, whose position store
    # doesn't record it), fall back to the prior absolute breach so behavior is
    # unchanged for those callers. A SOFT stop: deferred to HOLD when the trade is
    # recoverable (ample time + no imminent strike breach).
    entry_delta = ctx.get("entry_short_delta")
    if short_delta is not None:
        if entry_delta is None:
            breached = abs(short_delta) >= DELTA_ABS_FALLBACK
            reason = (f"short delta {abs(short_delta):.2f} breached "
                      f"{DELTA_ABS_FALLBACK:.2f}")
        else:
            breached = (abs(short_delta) >= abs(entry_delta) + DELTA_DRIFT
                        or abs(short_delta) >= DELTA_HARD_CEILING)
            reason = (f"short delta {abs(short_delta):.2f} drifted from "
                      f"entry {abs(entry_delta):.2f}")
        if breached:
            if _recoverable(ctx):
                return {"action": "HOLD",
                        "reason": f"recovery: {reason}, deferring delta stop",
                        "code": "HOLD"}
            return {"action": "CUT", "reason": reason, "code": "DELTA_STOP"}

    # Rule 5: +50% credit captured, not yet armed. LIFECYCLE callers (captured
    # signals, via build_mark, which sets ctx["lifecycle"]) ARM break-even and HOLD,
    # riding the trade to full credit under the break-even stop. NON-lifecycle
    # callers — the manual paper account AND the driver's isolated account, both via
    # paper_engine.run_manage_cycle with a minimal ctx — keep the pre-lifecycle
    # TAKE_PROFIT: the captured-autoclose rework is scoped to captured signals and
    # must not change how those separate books exit (they have their own managers).
    if pnl >= TP_FRAC * credit_total and not ctx.get("be_armed"):
        if ctx.get("lifecycle"):
            return {"action": "HOLD",
                    "reason": f">={int(TP_FRAC*100)}% credit captured — break-even armed",
                    "code": "HOLD"}
        return {"action": "TAKE_PROFIT",
                "reason": f">={int(TP_FRAC*100)}% credit captured",
                "code": "TARGET_HIT"}

    # Rule 6: default HOLD with score-drift note when available
    es = ctx.get("entry_score")
    cs = ctx.get("current_score")
    if es is not None and cs is not None:
        drift = cs - es
        reason = f"score {es}->{cs} ({drift:+.1f})"
    else:
        reason = "holding"
    return {"action": "HOLD", "reason": reason, "code": "HOLD"}


def _recompute_score(signal_row, repricer_result, iv_data, technicals):
    """Build an enriched signal dict + run scoring.calc_composite_score.

    Returns (current_score | None, grade | None). Imports scoring lazily to
    avoid a hard dependency for callers that only want recommend().
    """
    try:
        from scoring import calc_composite_score
    except ImportError:
        return None, None

    short_delta_now = repricer_result.get("current_short_delta")
    if short_delta_now is None:
        short_delta_now = signal_row.get("entry_short_delta") or 0
    try:
        pop_pct = max(0.0, min(100.0,
            (1.0 - abs(float(short_delta_now))) * 100.0))
    except (TypeError, ValueError):
        pop_pct = 0.0

    entry_credit = signal_row.get("entry_credit") or 0
    max_loss_per = signal_row.get("entry_max_loss") or 0
    rr_pct = (entry_credit / max_loss_per * 100) if max_loss_per > 0 else 0
    enriched = {
        "type": signal_row.get("strategy"),
        "symbol": signal_row.get("symbol", ""),
        "short_strike": signal_row.get("short_strike") or 0,
        "long_strike": signal_row.get("long_strike") or 0,
        "underlying_price": (repricer_result.get("current_underlying")
                             or signal_row.get("entry_underlying") or 0),
        "credit": entry_credit,
        "max_loss": max_loss_per,
        "rr_pct": rr_pct,
        "pop_pct": pop_pct,
        "net_theta": signal_row.get("entry_net_theta") or 0,
        "net_vega": 0,
        "short_delta": short_delta_now,
        "short_iv": (iv_data or {}).get("current_iv") or 0,
        "bid": 0,
        "ask": 0,
    }
    try:
        score_data = calc_composite_score(enriched, iv_data=iv_data,
                                          technicals=technicals)
        cs = score_data.get("composite_score")
        # signal_marks.current_score is INTEGER; coerce so downstream
        # consumers (recommend()'s ":+d" format, dashboard display) work.
        cs = int(round(cs)) if cs is not None else None
        return cs, score_data.get("grade")
    except Exception:
        return None, None


def build_mark(signal_row, repricer_result, now, iv_data=None, technicals=None,
               *, be_level=None, trail_ladder=None, peak_pnl_frac=None):
    """Compose a complete signal_marks row from a repricer result.

    Adds current_score (via scoring.calc_composite_score), score_drift, and the
    LIFECYCLE recommendation (via this module's recommend()). Caller persists with
    signal_db.insert_mark.

    The lifecycle inputs are threaded from the signal row + the reprice: the
    stored ``be_armed`` flag, the ``strategy``, the ``short_strike``/``call_short``
    legs, and the live ``current_underlying`` spot — plus the caller-supplied
    ``be_level`` (the break-even + round-trip-commission close floor, in dollars;
    computed by the options service, which owns the commission model). Absent
    ``be_level`` degrades to 0 (break-even stop at pnl <= $0); absent
    strategy/spot/strike degrades to recovery-off — so a caller that supplies none
    of the new inputs keeps the pre-lifecycle behavior (minus the removed immediate
    TAKE_PROFIT, which now arms break-even instead).

    Args:
        signal_row: dict from signal_db (must include signal_id, strategy,
            expiration, entry_credit, entry_short_delta, entry_score, etc.).
        repricer_result: dict from signal_repricer.reprice_swing.
        now: datetime (tz-aware). Used for mark_ts/mark_date/dte_remaining.
        iv_data: optional dict from iv_analysis.run_iv_analysis (improves score).
        technicals: optional dict from scanner_engine.calc_technicals.
        be_level: optional $ break-even close floor (round-trip commissions).
        trail_ladder: optional peak-driven profit-lock ladder — a list of
            ``(peak_frac, lock_frac)`` rungs. Default None → the plain break-even
            stop (inert ratchet).
        peak_pnl_frac: optional peak profit fraction (best ``pnl/credit_total``
            reached), paired with ``trail_ladder`` to ratchet the stop up. Default
            None → no profit lock beyond break-even.

    Returns:
        A mark dict ready for signal_db.insert_mark. If the repricer reported
        an error, returns None — caller should skip.
    """
    if repricer_result is None or repricer_result.get("error"):
        return None

    cur_score, _grade = _recompute_score(signal_row, repricer_result,
                                         iv_data, technicals)
    entry_score = signal_row.get("entry_score")
    drift = (cur_score - entry_score) if (cur_score is not None
                                          and entry_score is not None) else None

    try:
        exp_d = date.fromisoformat(signal_row.get("expiration") or "")
        dte_remaining = max((exp_d - now.date()).days, 0)
    except (ValueError, TypeError):
        dte_remaining = signal_row.get("dte_at_entry") or 99

    rec = recommend({
        "entry_credit": signal_row.get("entry_credit") or 0,
        "unrealized_pnl": repricer_result.get("unrealized_pnl"),
        "current_short_delta": repricer_result.get("current_short_delta"),
        "entry_short_delta": signal_row.get("entry_short_delta"),
        "dte_remaining": dte_remaining,
        "current_score": cur_score,
        "entry_score": entry_score,
        # Lifecycle inputs (threaded from the row + reprice + caller's be_level).
        # build_mark is the captured-signal path, so it opts INTO the lifecycle:
        # +50% arms break-even (HOLD) rather than the non-lifecycle TAKE_PROFIT that
        # the manual-paper / driver manage cycles keep.
        "lifecycle": True,
        "be_armed": bool(signal_row.get("be_armed")),
        "be_level": be_level,
        # Peak-driven profit-lock ladder (the "ratchet"). Default None → the plain
        # break-even stop; a caller supplies both to ratchet the floor up with peak.
        "trail_ladder": trail_ladder,
        "peak_pnl_frac": peak_pnl_frac,
        "spot": repricer_result.get("current_underlying"),
        "short_strike": signal_row.get("short_strike"),
        "call_short": signal_row.get("call_short"),
        "strategy": signal_row.get("strategy") or signal_row.get("type"),
    })

    return {
        "signal_id": signal_row["signal_id"],
        "mark_ts": now.isoformat(),
        "mark_date": now.date().isoformat(),
        "current_value": repricer_result.get("current_value"),
        "unrealized_pnl": repricer_result.get("unrealized_pnl"),
        "pnl_pct_of_credit": repricer_result.get("pnl_pct_of_credit"),
        "current_underlying": repricer_result.get("current_underlying"),
        "current_short_delta": repricer_result.get("current_short_delta"),
        "current_score": cur_score,
        "score_drift": drift,
        "recommendation": rec["action"],
        "recommendation_reason": rec["reason"],
        "recommendation_code": rec.get("code"),
    }


CLOSE_REASON_CODES = frozenset(
    {"TARGET_HIT", "MONEY_STOP", "DELTA_STOP", "TIME_STOP"}
)


def auto_close_reason(code):
    """Return the exit_reason for a recommendation code that should auto-close,
    or None to keep the signal OPEN.

    code is the stable identifier from recommend()['code']. The four close
    codes pass through unchanged; HOLD / unknown / None return None.
    """
    return code if code in CLOSE_REASON_CODES else None


def plan_auto_closes(marks):
    """From [(signal_id, mark_dict), ...] return [(signal_id, exit_value, reason), ...]
    for the marks that should auto-close. Skips HOLD/unknown and any mark whose
    current_value is None (never close on missing data).
    """
    plan = []
    for signal_id, mark in marks:
        if mark is None:
            continue
        reason = auto_close_reason(mark.get("recommendation_code"))
        if reason is None:
            continue
        exit_value = mark.get("current_value")
        if exit_value is None:
            continue
        plan.append((signal_id, exit_value, reason))
    return plan
