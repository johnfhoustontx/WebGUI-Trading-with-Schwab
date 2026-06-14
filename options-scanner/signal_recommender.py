"""Rule-based HOLD/TAKE_PROFIT/CUT recommender for open swing signals.

Also exposes `build_mark` — a shared helper that takes a signal row + a
signal_repricer.reprice_swing result and produces a complete signal_marks
row with current_score, drift, and recommendation populated. Used by both
the dashboard's "Refresh marks now" action and the EOD pipeline so they
write structurally identical rows.
"""

from datetime import date

MULTIPLIER = 100
TP_FRAC = 0.50            # take profit at >= 50% credit captured
STOP_MULT = 2.0           # cut at >= 2x credit loss
DELTA_DRIFT = 0.12        # cut when short delta drifts this far past entry
DELTA_HARD_CEILING = 0.45 # ...but never hold past this, regardless of entry
DELTA_ABS_FALLBACK = 0.35 # absolute breach when entry delta is unknown
CUT_DTE = 2               # cut when DTE <= this and underwater


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


def recommend(ctx):
    """Return {'action', 'reason', 'code'}. action in HOLD/TAKE_PROFIT/CUT; code in HOLD/TARGET_HIT/MONEY_STOP/DELTA_STOP/TIME_STOP."""
    credit_total = ctx["entry_credit"] * MULTIPLIER
    pnl = ctx.get("unrealized_pnl") or 0
    short_delta = ctx.get("current_short_delta")
    dte = ctx.get("dte_remaining", 99)

    # Rule 1: take profit (highest precedence)
    if pnl >= TP_FRAC * credit_total:
        return {"action": "TAKE_PROFIT",
                "reason": f">={int(TP_FRAC*100)}% credit captured",
                "code": "TARGET_HIT"}

    # Rule 2: 2x credit stop
    if pnl <= -STOP_MULT * credit_total:
        return {"action": "CUT", "reason": f"{STOP_MULT:g}x credit stop",
                "code": "MONEY_STOP"}

    # Rule 3: delta drift from entry, with an absolute hard ceiling. Measures
    # adverse movement relative to where the position was opened — a position
    # entered at 0.30 delta shouldn't be cut on noise the way an absolute 0.35
    # threshold would, but nothing rides past the hard ceiling. When the caller
    # cannot supply the entry delta (e.g. the paper engine, whose position store
    # doesn't record it), fall back to the prior absolute breach so behavior is
    # unchanged for those callers.
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
            return {"action": "CUT", "reason": reason, "code": "DELTA_STOP"}

    # Rule 4: low DTE and underwater
    if dte <= CUT_DTE and pnl < 0:
        return {"action": "CUT", "reason": f"DTE <= {CUT_DTE} and underwater",
                "code": "TIME_STOP"}

    # Default: HOLD with score-drift note when available
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


def build_mark(signal_row, repricer_result, now, iv_data=None, technicals=None):
    """Compose a complete signal_marks row from a repricer result.

    Adds current_score (via scoring.calc_composite_score), score_drift, and
    recommendation (via this module's recommend()). Caller persists with
    signal_db.insert_mark.

    Args:
        signal_row: dict from signal_db (must include signal_id, strategy,
            expiration, entry_credit, entry_short_delta, entry_score, etc.).
        repricer_result: dict from signal_repricer.reprice_swing.
        now: datetime (tz-aware). Used for mark_ts/mark_date/dte_remaining.
        iv_data: optional dict from iv_analysis.run_iv_analysis (improves score).
        technicals: optional dict from scanner_engine.calc_technicals.

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
