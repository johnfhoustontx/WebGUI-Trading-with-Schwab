"""Pure derivation logic for the Options Matrix Display (cache:options:matrix).

No I/O: every function takes plain data so it is unit-tested in isolation. The
I/O orchestration lives in services/options_svc/compute.build_matrix.
"""
from __future__ import annotations

# ---- tunable thresholds ----
_TREND_LOOKBACK_S = 900        # 15 min recent-move window
_TREND_MILD = 0.0012           # 0.12% move → mild trend
_TREND_STRONG = 0.004          # 0.40% move → strong trend
_ACCEL_LOOKBACK_S = 900        # 15 min recent-slope window
_ACCEL_HOT = 1.5               # recent slope >= 1.5x day-avg -> accelerating
_ACCEL_COOL = 0.6              # <= 0.6x -> cooling
_SIG_BUY = 0.22                # composite score cut for buy/sell
_SIG_STRONG = 0.55             # |score| for a strong tier
_W_TREND, _W_FLOW, _W_ACCEL = 0.50, 0.35, 0.15


def _spot_points(series):
    """[(ts, spot)] with non-null spots, from a flow series row tuple."""
    return [(row[0], row[1]) for row in series if row[1] is not None]


def intraday_trend(spot_series, now_ts):
    """spot_series = [(ts, spot)]; return (state, direction[-1..1]).

    state in {strong_up, up, flat, down, strong_down}. direction is the % move over the
    trailing window normalized to _TREND_STRONG and clamped to [-1, 1].
    """
    pts = [(t, s) for t, s in spot_series if s is not None]
    if len(pts) < 2:
        return ("flat", 0.0)
    cur_t, cur = pts[-1]
    ref = pts[0][1]
    cutoff = cur_t - _TREND_LOOKBACK_S
    for t, s in pts:
        if t <= cutoff:
            ref = s
    if not ref:
        return ("flat", 0.0)
    pct = (cur - ref) / ref
    direction = max(-1.0, min(1.0, pct / _TREND_STRONG))
    ap = abs(pct)
    if ap < _TREND_MILD:
        state = "flat"
    elif ap < _TREND_STRONG:
        state = "up" if pct > 0 else "down"
    else:
        state = "strong_up" if pct > 0 else "strong_down"
    return (state, direction)


def flow_acceleration(prem_series, now_ts, lookback_s=_ACCEL_LOOKBACK_S):
    """prem_series = [(ts, cumulative_premium)] (monotonic).

    Return (state, ratio) where ratio = recent-slope / day-average-slope.
    state in {hot, cool, steady, flat}. flat = no premium accrued or too few points.
    """
    pts = [(t, p) for t, p in prem_series if p is not None]
    if len(pts) < 2:
        return ("flat", 0.0)
    first_t, first_p = pts[0]
    last_t, last_p = pts[-1]
    total_span = last_t - first_t
    total_added = last_p - first_p
    if total_span <= 0 or total_added <= 0:
        return ("flat", 0.0)
    avg_slope = total_added / total_span
    # Anchor the recent window at the earliest sample still inside [cutoff, last_t]
    # so "recent slope" reflects the acceleration within the lookback window.
    ref_t, ref_p = last_t, last_p
    cutoff = last_t - lookback_s
    for t, p in pts:
        if t >= cutoff:
            ref_t, ref_p = t, p
            break
    recent_span = last_t - ref_t
    if recent_span <= 0:
        return ("steady", 1.0)
    recent_slope = (last_p - ref_p) / recent_span
    ratio = recent_slope / avg_slope if avg_slope > 0 else 0.0
    if ratio >= _ACCEL_HOT:
        state = "hot"
    elif ratio <= _ACCEL_COOL:
        state = "cool"
    else:
        state = "steady"
    return (state, ratio)


def pc_ratio(call_prem, put_prem):
    """Put/Call premium ratio; None when calls are zero (undefined).

    Premium columns are forward-only (None on early snapshots) — never raise.
    """
    call_prem = call_prem or 0.0
    put_prem = put_prem or 0.0
    if not call_prem:
        return None
    return round(put_prem / call_prem, 2)


def net_premium_m(call_prem, put_prem):
    """(call - put) premium in $M; tolerates None (forward-only columns)."""
    call_prem = call_prem or 0.0
    put_prem = put_prem or 0.0
    return round((call_prem - put_prem) / 1_000_000.0, 2)


def gex_regime(spot, flip):
    if spot is None or flip is None:
        return "na"
    return "above" if spot >= flip else "below"


def composite_signal(trend_dir, call_state, put_state, call_prem, put_prem):
    """Return (signal, strength). signal in {buy, neutral, sell}; strength in {0,1,2}."""
    call_prem = call_prem or 0.0
    put_prem = put_prem or 0.0
    total = call_prem + put_prem
    flow_dir = ((call_prem - put_prem) / total) if total > 0 else 0.0
    accel_dir = 0.0
    if call_state == "hot" and put_state != "hot":
        accel_dir = 1.0
    elif put_state == "hot" and call_state != "hot":
        accel_dir = -1.0
    score = _W_TREND * trend_dir + _W_FLOW * flow_dir + _W_ACCEL * accel_dir
    if total <= 0 and trend_dir == 0.0:
        return ("neutral", 0)
    if score >= _SIG_BUY:
        sig = "buy"
    elif score <= -_SIG_BUY:
        sig = "sell"
    else:
        return ("neutral", 1 if abs(score) > 0.1 else 0)
    strength = 2 if abs(score) >= _SIG_STRONG else 1
    return (sig, strength)


def hotness(n_signals, n_alerts, signal_strength):
    """Sort key so opportunities float to the top (higher = hotter)."""
    return 2 * n_signals + 2 * n_alerts + 3 * signal_strength


def build_rows(raw, scan_counts, alert_counts, now_ts):
    """raw = {symbol: {"series": [flow-row tuples], "flip": float|None}}.

    Returns a list of per-symbol row dicts (order = raw insertion order).
    """
    rows = []
    for symbol, blob in raw.items():
        n_sig = int(scan_counts.get(symbol, 0))
        n_alr = int(alert_counts.get(symbol, 0))
        try:
            series = blob.get("series") or []
            flip = blob.get("flip")
            spots = _spot_points(series)
            spot = spots[-1][1] if spots else None
            open_spot = spots[0][1] if spots else None
            day_pct = (((spot - open_spot) / open_spot * 100.0)
                       if (spot is not None and open_spot) else None)

            t_state, t_dir = intraday_trend(spots, now_ts)
            call_series = [(r[0], r[4]) for r in series]   # (ts, call_prem)
            put_series = [(r[0], r[5]) for r in series]    # (ts, put_prem)
            c_state, _ = flow_acceleration(call_series, now_ts)
            p_state, _ = flow_acceleration(put_series, now_ts)
            # forward-only premium columns are None on early snapshots.
            call_prem = (series[-1][4] or 0.0) if series else 0.0
            put_prem = (series[-1][5] or 0.0) if series else 0.0

            sig, strength = composite_signal(t_dir, c_state, p_state, call_prem, put_prem)

            rows.append({
                "symbol": symbol,
                "spot": round(spot, 2) if spot is not None else None,
                "day_pct": round(day_pct, 2) if day_pct is not None else None,
                "trend_state": t_state,
                "trend_dir": round(t_dir, 3),
                "call_accel": c_state,
                "put_accel": p_state,
                "pc_ratio": pc_ratio(call_prem, put_prem),
                "net_prem_m": net_premium_m(call_prem, put_prem),
                "flip": round(flip, 2) if flip is not None else None,
                "gex_regime": gex_regime(spot, flip),
                "_open_spot": round(open_spot, 2) if open_spot is not None else None,
                "n_signals": n_sig,
                "n_alerts": n_alr,
                "signal": sig,
                "signal_strength": strength,
                "hotness": hotness(n_sig, n_alr, strength),
            })
        except Exception:
            # Per-item construction can't sink the batch: one bad symbol must
            # never zero the whole matrix. Append a minimal degraded row.
            rows.append({
                "symbol": symbol,
                "spot": None,
                "day_pct": None,
                "trend_state": "flat",
                "trend_dir": 0.0,
                "call_accel": "flat",
                "put_accel": "flat",
                "pc_ratio": None,
                "net_prem_m": 0.0,
                "flip": None,
                "gex_regime": "na",
                "_open_spot": None,
                "n_signals": n_sig,
                "n_alerts": n_alr,
                "signal": "neutral",
                "signal_strength": 0,
                "hotness": hotness(n_sig, n_alr, 0),
            })
    return rows
