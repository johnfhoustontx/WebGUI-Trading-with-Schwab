"""Multi-strategy candidate builders for the Swing Scanner (pure).

Given a Schwab option chain + spot, build NORMALIZED candidate signals across
families (directional, verticals, neutral, diagonal). Each candidate carries a
canonical ``legs`` list + payoff economics (max P/L, breakevens, PoP, capital).
Credit verticals (PCS/CCS) are produced by ``scanner_engine.screen_spreads`` and
adapted here; this module owns the new families.
"""
import datetime as _dt
import math

_GRID_LO, _GRID_HI, _GRID_N = 0.5, 1.5, 401   # ±50% of spot payoff grid


def _norm_mark(c):
    m = c.get("mark") or 0
    bid, ask = c.get("bid") or 0, c.get("ask") or 0
    if m <= 0 and bid > 0 and ask > 0:
        m = round((bid + ask) / 2, 4)
    if m <= 0:
        m = c.get("close") or c.get("theoreticalOptionValue") or 0
    return m


def extract_options(chain, kind, dte_min, dte_max):
    """{exp_str: {dte, strikes: {strike: leg_data}}} for one option kind."""
    key = "callExpDateMap" if kind == "call" else "putExpDateMap"
    out = {}
    for exp_key, strikes in (chain.get(key) or {}).items():
        exp_str, dte = exp_key.split(":")[0], int(float(exp_key.split(":")[1]))
        if not (dte_min <= dte <= dte_max):
            continue
        sd = {}
        for sk, contracts in strikes.items():
            if not contracts:
                continue
            c = contracts[0]
            if c.get("delta") is None:
                continue
            sd[float(sk)] = {
                "strike": float(sk), "delta": c.get("delta"),
                "mark": _norm_mark(c), "bid": c.get("bid") or 0, "ask": c.get("ask") or 0,
                "theta": c.get("theta") or 0, "vega": c.get("vega") or 0,
                "gamma": c.get("gamma") or 0, "iv": c.get("volatility") or 0,
                "volume": c.get("totalVolume") or 0, "oi": c.get("openInterest") or 0,
            }
        if sd:
            out[exp_str] = {"dte": dte, "strikes": sd}
    return out


def nearest_by_delta(strikes, target_abs_delta):
    """Leg whose |delta| is closest to target_abs_delta (None if empty)."""
    if not strikes:
        return None
    return min(strikes.values(), key=lambda v: abs(abs(v["delta"]) - target_abs_delta))


def _intrinsic(leg, S):
    if leg["kind"] == "call":
        return max(0.0, S - leg["strike"])
    return max(0.0, leg["strike"] - S)


def _sign(leg):
    return 1.0 if leg["side"] == "long" else -1.0


def _pl_at(legs, entry_cost, S):
    v = sum(_sign(l) * _intrinsic(l, S) * l.get("qty", 1) for l in legs)
    return v - entry_cost


def payoff_metrics(legs, spot):
    entry_cost = sum(_sign(l) * l["mark"] * l.get("qty", 1) for l in legs)   # +debit
    net = round(entry_cost, 4)

    # --- Tail analysis (structure-driven, not grid-driven) ---
    # As S->inf the payoff slope equals call_coeff = sum(sign*qty) over CALL legs.
    #   > 0 unbounded PROFIT (long call) ; < 0 unbounded LOSS (naked short call) ;
    #   == 0 bounded on the upside (verticals/condors/flies). The downside (S->0)
    # is ALWAYS bounded (puts floor at S=0), so never flag unbounded from below.
    call_coeff = sum(_sign(l) * l.get("qty", 1) for l in legs if l["kind"] == "call")
    unbounded = (call_coeff != 0)

    # --- Bounded extrema at payoff BREAKPOINTS (S=0, each strike, a far-high pt) ---
    strikes = [l["strike"] for l in legs]
    far_high = 2.0 * max(strikes) if strikes else spot * 2.0
    points = sorted({0.0, far_high} | set(strikes))
    pls = [_pl_at(legs, entry_cost, S) for S in points]
    bounded_max = max(pls)
    bounded_min = min(pls)

    # Override the extremum on whichever side is unbounded.
    if call_coeff > 0:          # unbounded upside profit
        max_profit = None
    else:
        max_profit = round(bounded_max, 2)

    margin_proxy = round(abs(net) if net > 0 else spot * 0.20, 2)
    if call_coeff < 0:          # unbounded upside loss -> can't read off the grid
        max_loss = margin_proxy
        capital = margin_proxy
    else:
        max_loss = abs(round(bounded_min, 2))
        capital = max_loss if not unbounded else margin_proxy

    # --- Breakevens: scan a fine grid for sign changes + interpolate ---
    grid = [spot * (_GRID_LO + (_GRID_HI - _GRID_LO) * i / (_GRID_N - 1))
            for i in range(_GRID_N)]
    gpls = [_pl_at(legs, entry_cost, S) for S in grid]
    breakevens = []
    for i in range(1, len(grid)):
        if (gpls[i - 1] <= 0 < gpls[i]) or (gpls[i - 1] >= 0 > gpls[i]):
            t = gpls[i - 1] / (gpls[i - 1] - gpls[i])
            breakevens.append(round(grid[i - 1] + t * (grid[i] - grid[i - 1]), 2))

    return {
        "net_debit": net if net > 0 else None,
        "net_credit": round(-net, 4) if net < 0 else None,
        "max_profit": max_profit, "max_loss": max_loss,
        "breakevens": breakevens, "unbounded": unbounded,
        "capital": capital,
        "rr": (round(max_profit / max_loss, 3) if (max_profit and max_loss) else None),
        "net_delta": round(sum(_sign(l) * l["delta"] * l.get("qty", 1) for l in legs), 4),
        "net_theta": round(sum(_sign(l) * l["theta"] * l.get("qty", 1) for l in legs), 4),
        "net_vega":  round(sum(_sign(l) * l["vega"]  * l.get("qty", 1) for l in legs), 4),
        "net_gamma": round(sum(_sign(l) * l["gamma"] * l.get("qty", 1) for l in legs), 4),
    }


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def pop_from_payoff(legs, spot, atm_iv, dte):
    sigma = spot * max(atm_iv, 1e-6) * math.sqrt(max(dte, 0.5) / 365.0)
    if sigma <= 0:
        return None
    entry_cost = sum(_sign(l) * l["mark"] * l.get("qty", 1) for l in legs)
    n = 801
    lo, hi = spot - 6 * sigma, spot + 6 * sigma
    prob = 0.0
    prev_S = lo
    prev_cdf = _norm_cdf((lo - spot) / sigma)
    for i in range(1, n):
        S = lo + (hi - lo) * i / (n - 1)
        cdf = _norm_cdf((S - spot) / sigma)
        mid = (S + prev_S) / 2
        v = sum(_sign(l) * _intrinsic(l, mid) * l.get("qty", 1) for l in legs)
        if v - entry_cost > 0:
            prob += (cdf - prev_cdf)
        prev_S, prev_cdf = S, cdf
    return round(prob * 100, 1)


_LONG_DELTA, _SHORT_DELTA = 0.55, 0.28

_DIRECTIONAL = [
    ("LONG_CALL",  "call", "long",  "bullish", "Long Call",  _LONG_DELTA),
    ("LONG_PUT",   "put",  "long",  "bearish", "Long Put",   _LONG_DELTA),
    ("SHORT_CALL", "call", "short", "bearish", "Short Call", _SHORT_DELTA),
    ("SHORT_PUT",  "put",  "short", "bullish", "Short Put",  _SHORT_DELTA),
]


def _front_exp(opts_by_exp):
    return min(opts_by_exp.items(), key=lambda kv: kv[1]["dte"]) if opts_by_exp else None


def _leg_from(leg_data, kind, side, exp):
    return {"kind": kind, "side": side, "strike": leg_data["strike"], "expiration": exp,
            "qty": 1, "mark": leg_data["mark"], "delta": leg_data["delta"],
            "theta": leg_data["theta"], "vega": leg_data["vega"],
            "gamma": leg_data["gamma"], "iv": leg_data["iv"]}


def _dte_for(exp_str):
    try:
        return max(0, (_dt.date.fromisoformat(exp_str) - _dt.date.today()).days)
    except Exception:
        return 0


def _assemble(stype, family, label, bias, legs, symbol, spot, atm_iv):
    m = payoff_metrics(legs, spot)
    front = min(legs, key=lambda l: l["expiration"])
    dte = _dte_for(front["expiration"])
    pop = pop_from_payoff(legs, spot, atm_iv, dte)
    sk = "_".join(str(l["strike"]) for l in legs)
    return {"id": f"{symbol}_{stype}_{front['expiration']}_{sk}",
            "symbol": symbol, "type": stype, "family": family,
            "strategy_label": label, "bias": bias, "legs": legs,
            "expiration": front["expiration"], "dte": dte,
            "pop_pct": pop, "underlying_price": spot,
            "timestamp": _dt.datetime.now().isoformat(), **m}


def build_directional(chain, symbol, spot, atm_iv, dte_min, dte_max):
    out = []
    by_kind = {k: extract_options(chain, k, dte_min, dte_max) for k in ("call", "put")}
    for stype, kind, side, bias, label, target in _DIRECTIONAL:
        fe = _front_exp(by_kind[kind])
        if not fe:
            continue
        exp, data = fe
        leg_data = nearest_by_delta(data["strikes"], target)
        if not leg_data:
            continue
        legs = [_leg_from(leg_data, kind, side, exp)]
        out.append(_assemble(stype, "DIRECTIONAL", label, bias, legs, symbol, spot, atm_iv))
    return out


_DEBIT_BUY, _DEBIT_SELL = 0.60, 0.30


def build_debit_verticals(chain, symbol, spot, atm_iv, dte_min, dte_max):
    out = []
    for stype, kind, bias, label in [("BULL_CALL", "call", "bullish", "Bull Call Spread"),
                                      ("BEAR_PUT", "put", "bearish", "Bear Put Spread")]:
        fe = _front_exp(extract_options(chain, kind, dte_min, dte_max))
        if not fe:
            continue
        exp, data = fe
        buy = nearest_by_delta(data["strikes"], _DEBIT_BUY)
        sell = nearest_by_delta(data["strikes"], _DEBIT_SELL)
        if not buy or not sell or buy["strike"] == sell["strike"]:
            continue
        legs = [_leg_from(buy, kind, "long", exp), _leg_from(sell, kind, "short", exp)]
        out.append(_assemble(stype, "VERTICAL", label, bias, legs, symbol, spot, atm_iv))
    return out


def _credit_leg(kind, side, strike, mark, src, delta_key=None):
    """Build a normalized leg from a credit-spread source dict (greeks default 0)."""
    return {"kind": kind, "side": side, "strike": strike,
            "expiration": src.get("expiration"), "qty": 1, "mark": mark or 0,
            "delta": src.get(delta_key, 0) or 0 if delta_key else 0,
            "theta": 0, "vega": 0, "gamma": 0, "iv": 0}


def _normalize_credit(sig, family, label, bias, legs):
    """Fill the full normalized contract for an adapted credit structure.

    Structural keys (breakevens/capital/rr/net_delta/net_gamma) are computed
    from the reconstructed legs via payoff_metrics; the source dict's
    authoritative economics (credit -> net_credit/max_profit, max_loss) and any
    real source greeks (net_theta/net_vega/pop_pct) then RE-OVERRIDE so they win
    over the leg-reconstructed zeros.
    """
    credit = sig.get("credit")
    spot = sig.get("underlying_price") or 0
    m = payoff_metrics(legs, spot)

    out = dict(sig)            # preserve source fields
    out.update(m)              # structural keys from the legs
    out.update({
        "family": family, "strategy_label": label, "bias": bias, "legs": legs,
        "net_credit": credit, "net_debit": None,
        "max_profit": credit, "max_loss": sig.get("max_loss"),
        "unbounded": False,
        "timestamp": sig.get("timestamp") or _dt.datetime.now().isoformat(),
    })
    # Keep authoritative source greeks/pop where present (don't let leg=0 clobber).
    for k in ("net_theta", "net_vega", "pop_pct"):
        if sig.get(k) is not None:
            out[k] = sig[k]
    return out


def adapt_credit_spread(sig):
    """Adapt a screen_spreads PCS/CCS dict into the normalized signal shape.

    Preserves every source field; adds the full normalized contract
    (legs/family/strategy_label/bias/breakevens/capital/rr/net_* /timestamp).
    PCS -> bullish, CCS -> bearish; source economics stay authoritative.
    """
    is_pcs = sig.get("type") == "PCS"
    kind = "put" if is_pcs else "call"
    bias = "bullish" if is_pcs else "bearish"
    label = "Put Credit Spread" if is_pcs else "Call Credit Spread"
    legs = [
        _credit_leg(kind, "short", sig.get("short_strike"), sig.get("short_mark"),
                    sig, delta_key="short_delta"),
        _credit_leg(kind, "long", sig.get("long_strike"), sig.get("long_mark"), sig),
    ]
    return _normalize_credit(sig, "VERTICAL", label, bias, legs)


def adapt_iron_condor(sig):
    """Adapt a build_iron_condors IC dict into the normalized signal shape.

    Put side = short_strike / long_strike; call side = call_short / call_long.
    """
    legs = [
        _credit_leg("put", "short", sig.get("short_strike"), sig.get("short_mark"), sig),
        _credit_leg("put", "long", sig.get("long_strike"), sig.get("long_mark"), sig),
        _credit_leg("call", "short", sig.get("call_short"), sig.get("call_short_mark"), sig),
        _credit_leg("call", "long", sig.get("call_long"), sig.get("call_long_mark"), sig),
    ]
    return _normalize_credit(sig, "NEUTRAL", "Iron Condor", "neutral", legs)
