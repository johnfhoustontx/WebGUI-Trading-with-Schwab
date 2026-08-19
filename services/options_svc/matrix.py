"""Pure derivation logic for the Options Matrix Display (cache:options:matrix).

No I/O: every function takes plain data so it is unit-tested in isolation. The
I/O orchestration lives in services/options_svc/compute.build_matrix.
"""
from __future__ import annotations

import math

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

# ---- IV-direction regime (the missing linchpin for the vol-crush / cascade setups) ----
_IV_LOOKBACK_S = 900           # 15 min trailing window
_IV_SPIKE = 0.04               # +4% relative IV change over the window -> spiking
_IV_CRUSH = -0.04              # -4% -> collapsing

# ---- fused dealer-flow regime (named playbook labels) ----
_PIN_PROX_PCT = 0.4            # spot within 0.4% of a wall -> pin candidate
_AFTERNOON_MINS = 180         # <=180 min to close = after ~1pm ET (charm grind)
_LATE_MINS = 75               # <=75 min to close = last ~75 min (pin / MOC ramp)
_TRENDING = ("strong_up", "strong_down")   # excluded from the range-bound grind


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


def iv_regime(iv_series, now_ts, lookback_s=_IV_LOOKBACK_S):
    """iv_series = [(ts, atm_iv)] where atm_iv is an IV *level* (decimal or
    vol-points; unit-agnostic as long as it is positive).

    Return (state, rel_change). state in {spiking, collapsing, stable, na};
    rel_change = (iv_now - iv_ref) / iv_ref over the trailing lookback window
    (ref = the last sample at/before the cutoff, else the first sample).

    This is the axis the app does NOT yet emit: whether ATM IV is *rising* or
    *falling*. The vol-crush squeeze (positive gamma + IV collapsing) and the
    negative-gamma cascade (below flip + IV spiking) both hinge on it. Feed it a
    per-snapshot ATM-IV series -- a new forward-only ``atm_iv`` column on the
    ``snapshots`` table (the poll already computes ATM IV via the engine's
    ``_get_atm_iv``; the DB stores only ``rr_25d`` skew, which is signed and can
    cross zero, so it is NOT a substitute here). Never raises.
    """
    pts = [(t, s) for t, s in iv_series if s is not None]
    if len(pts) < 2:
        return ("na", 0.0)
    cur = pts[-1][1]
    ref = pts[0][1]
    cutoff = pts[-1][0] - lookback_s
    for t, s in pts:
        if t <= cutoff:
            ref = s
    if ref is None or ref <= 0:
        return ("na", 0.0)
    rel = (cur - ref) / ref
    if rel >= _IV_SPIKE:
        state = "spiking"
    elif rel <= _IV_CRUSH:
        state = "collapsing"
    else:
        state = "stable"
    return (state, rel)


def _wall_dist_pct(spot, call_wall, put_wall):
    """|spot - NEAREST wall| / spot * 100, or None when it cannot be known.

    Feeds ``dealer_regime``'s ``delta_wall_pin`` gate. Returns None -- never 0.0
    -- when no wall is available: 0.0 reads as "spot is exactly ON the wall", the
    maximally pin-like value, so degrading to it would fabricate the strongest
    possible signal out of missing data.

    A non-finite input is treated as MISSING, not as a number. ``nan <= 0`` and
    ``nan > 0`` are both False, so a plain positivity guard waves a NaN through
    and the arithmetic yields nan -- which then survives every ``is not None``
    check downstream, compares False against every threshold, and serialises as
    the invalid JSON literal ``NaN``. Same clamp-to-the-extreme trap that bit
    ``sentiment_svc`` twice. Never raises.
    """
    try:
        if spot is None or not math.isfinite(spot) or spot <= 0:
            return None
        dists = [abs(spot - w) for w in (call_wall, put_wall)
                 if w is not None and math.isfinite(w) and w > 0]
        if not dists:
            return None
        return round(min(dists) / spot * 100.0, 3)
    except Exception:
        return None


def _latest_atm_iv(iv_series):
    """Last finite positive ATM-IV level from [(ts, atm_iv)], else None.

    ``atm_iv`` is forward-only, so legacy/early rows come back as (ts, None). A
    non-finite sample is skipped like a null one rather than returned: ``inf``
    passes ``> 0`` and would be handed on as a real -- and maximal -- IV reading.
    Never raises.
    """
    try:
        for _ts, iv in reversed(list(iv_series or ())):
            if iv is not None and math.isfinite(iv) and iv > 0:
                return round(float(iv), 2)
    except Exception:
        pass
    return None


def dealer_regime(spot, flip, iv_state, trend_state, mins_to_close,
                  wall_dist_pct=None):
    """Fuse gamma regime x IV direction x time-of-day x wall proximity into ONE
    named dealer-flow playbook label. Returns one of:

      'gamma_cascade'  - below flip + IV spiking            (setup 3; dangerous)
      'vanna_squeeze'  - above flip + IV collapsing         (setup 1)
      'delta_wall_pin' - above flip + late + spot hugs a wall (setup 4)
      'charm_grind'    - above flip + afternoon + range-bound (setup 2)
      'neutral'        - positive/negative gamma, no setup active
      'na'             - spot/flip unavailable

    Context/label only -- it never sizes or places a trade (mirrors the driver's
    ``market_read``). Precedence is by urgency: the negative-gamma cascade wins,
    then the vol-crush, then the pin, then the grind. ``mins_to_close`` is minutes
    until the 4pm-ET cash close (None off-hours -> the time-gated setups can't
    fire). ``wall_dist_pct`` = |spot - nearest big net-delta strike| / spot * 100
    (None when the DEX wall isn't loaded -> the pin can't fire). Never raises.
    """
    reg = gex_regime(spot, flip)
    if reg == "na":
        return "na"
    if reg == "below":
        return "gamma_cascade" if iv_state == "spiking" else "neutral"
    # reg == "above" (positive gamma)
    if iv_state == "collapsing":
        return "vanna_squeeze"
    late = mins_to_close is not None and mins_to_close <= _LATE_MINS
    if late and wall_dist_pct is not None and wall_dist_pct <= _PIN_PROX_PCT:
        return "delta_wall_pin"
    afternoon = mins_to_close is not None and mins_to_close <= _AFTERNOON_MINS
    if afternoon and trend_state not in _TRENDING:
        return "charm_grind"
    return "neutral"


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


def _nearest_wall_dist_pct(spot, top_pos, top_neg):
    """|spot - nearest net-delta wall| / spot * 100; None when unavailable."""
    if spot is None or spot <= 0:
        return None
    walls = [w for w in (top_pos, top_neg) if w is not None]
    if not walls:
        return None
    return min(abs(spot - w) / spot * 100.0 for w in walls)


def dealer_regime_from_rows(rows, atm_rows, now_ts, close_ts):
    """Assemble the ``dealer_regime`` inputs from stored gex-view rows and label.

    ``rows`` = ``load_today`` shape ``[(ts, spot, flip, top_pos_strike,
    top_neg_strike, net_total)]`` (chronological). ``atm_rows`` =
    ``load_atm_iv_series`` shape ``[(ts, atm_iv)]``. ``now_ts`` / ``close_ts`` =
    unix seconds (``close_ts`` = the 4pm-ET cash close for the row's date).

    Returns ``{spot, flip, trend_state, iv_state, wall_dist_pct, mins_to_close,
    regime}``. Pure — the dry-run tool (and, if surfaced later, ``build_matrix``)
    both call it. A close already in the past → ``mins_to_close`` None so the
    time-gated setups (pin / grind) can't fire. Never raises.
    """
    if not rows:
        return {"spot": None, "flip": None, "trend_state": "flat",
                "iv_state": "na", "wall_dist_pct": None,
                "mins_to_close": None, "regime": "na"}
    last = rows[-1]
    spot, flip, top_pos, top_neg = last[1], last[2], last[3], last[4]
    t_state, _ = intraday_trend([(r[0], r[1]) for r in rows], now_ts)
    iv_state, _ = iv_regime(atm_rows, now_ts)
    wall_dist_pct = _nearest_wall_dist_pct(spot, top_pos, top_neg)
    mins = (close_ts - now_ts) / 60.0 if close_ts is not None else None
    if mins is not None and mins < 0:
        mins = None
    regime = dealer_regime(spot, flip, iv_state, t_state, mins, wall_dist_pct)
    return {
        "spot": round(spot, 2) if spot is not None else None,
        "flip": round(flip, 2) if flip is not None else None,
        "trend_state": t_state,
        "iv_state": iv_state,
        "wall_dist_pct": round(wall_dist_pct, 3) if wall_dist_pct is not None else None,
        "mins_to_close": round(mins, 1) if mins is not None else None,
        "regime": regime,
    }


def market_premium_aggregate(raw):
    """Dollar-weighted call/put PREMIUM skew across the collected universe.

    ``raw`` = ``{symbol: {"series": [flow-row tuples], ...}}`` (the same dict
    ``build_matrix`` feeds ``build_rows``). For each symbol take its LATEST
    snapshot's cumulative call/put premium (row cols 4/5, forward-only → may be
    None), then sum the raw dollars across every symbol and reduce to a signed
    skew ``(Σcall − Σput) / (Σcall + Σput)`` in ``[-1, 1]`` (>0 = more money
    through calls). Dollar-weighted, so index/mega-cap premium dominates — this
    is the market-money read, NOT an equal-weighted per-name average.

    Because Schwab has no time-&-sales tape, premium is UNSIGNED cumulative
    (total traded), so the skew is a money-weighted Put/Call, NOT net buying.

    Returns ``{call_total, put_total, net_m, skew, skew_pct, symbols}``; ``skew``
    / ``skew_pct`` are ``None`` when no premium has accrued yet (early session /
    off-hours). Never raises.
    """
    call_total = put_total = 0.0
    n = 0
    for blob in (raw or {}).values():
        series = (blob or {}).get("series") or []
        if not series:
            continue
        last = series[-1]
        c = last[4] or 0.0
        p = last[5] or 0.0
        if c <= 0 and p <= 0:          # forward-only None / genuinely no flow yet
            continue
        call_total += c
        put_total += p
        n += 1
    total = call_total + put_total
    if total <= 0:
        return {"call_total": 0.0, "put_total": 0.0, "net_m": 0.0,
                "skew": None, "skew_pct": None, "symbols": 0}
    skew = (call_total - put_total) / total
    return {"call_total": round(call_total, 2), "put_total": round(put_total, 2),
            "net_m": round((call_total - put_total) / 1_000_000.0, 2),
            "skew": round(skew, 4), "skew_pct": round(skew * 100, 1),
            "symbols": n}


def build_rows(raw, scan_counts, alert_counts, now_ts, eth_symbols=None):
    """raw = {symbol: {"series": [flow-row tuples], "flip": float|None}}.

    Returns a list of per-symbol row dicts (order = raw insertion order).

    ``eth_symbols`` is the set of Cboe extended-hours-eligible tickers, THREADED
    IN rather than read from a cache here so this stays pure. It drives the
    additive ``eth_eligible`` flag, which exists to explain the GTH picture: at
    07:00 CT on a post-activation day only the ~7 eligible names have live rows
    while the other ~38 still show the prior session, and without a marker the
    frozen majority reads as stale/broken rather than as "not eligible".
    ``None`` (no eligibility known) flags every row False -- the fail-safe
    direction for a badge, since a false ETH badge on a genuinely stale row
    would explain away a real fault.
    """
    eth = eth_symbols or ()
    rows = []
    for symbol, blob in raw.items():
        n_sig = int(scan_counts.get(symbol, 0))
        n_alr = int(alert_counts.get(symbol, 0))
        is_eth = symbol in eth
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
                # Raw cumulative premium $ (forward-only → 0.0 on early snapshots),
                # exposed so a cross-service reader (the market dashboard) can show a
                # per-symbol call/put skew + dollar-weight-aggregate a basket (BIG10).
                "call_prem": round(call_prem, 2),
                "put_prem": round(put_prem, 2),
                "flip": round(flip, 2) if flip is not None else None,
                "gex_regime": gex_regime(spot, flip),
                "_open_spot": round(open_spot, 2) if open_spot is not None else None,
                "n_signals": n_sig,
                "n_alerts": n_alr,
                "signal": sig,
                "signal_strength": strength,
                "hotness": hotness(n_sig, n_alr, strength),
                "eth_eligible": is_eth,
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
                "call_prem": 0.0,
                "put_prem": 0.0,
                "flip": None,
                "gex_regime": "na",
                "_open_spot": None,
                "n_signals": n_sig,
                "n_alerts": n_alr,
                "signal": "neutral",
                "signal_strength": 0,
                "hotness": hotness(n_sig, n_alr, 0),
                # Carried on the DEGRADED row too: these are the rows most
                # likely to look broken, so the "not eligible" explanation
                # matters here most.
                "eth_eligible": is_eth,
            })
    return rows
