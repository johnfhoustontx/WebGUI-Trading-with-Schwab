"""Multi-strategy candidate builders for the Swing Scanner (pure).

Given a Schwab option chain + spot, build NORMALIZED candidate signals across
families (directional, verticals, neutral, diagonal). Each candidate carries a
canonical ``legs`` list + payoff economics (max P/L, breakevens, PoP, capital).
Credit verticals (PCS/CCS) are produced by ``scanner_engine.screen_spreads`` and
adapted here; this module owns the new families.
"""
import datetime as _dt
import math

import commissions as _cm
import options_calculator as _oc

_GRID_LO, _GRID_HI, _GRID_N = 0.5, 1.5, 401   # ±50% of spot payoff grid

# Contract multiplier: option economics are per-share; a standard US equity/index
# option controls 100 shares. ALL normalized families report per-CONTRACT dollars
# (matching the calculator + the ×100 scanner convention), so a directional row's
# max_loss and a credit row's max_loss compare on the same scale.
_CONTRACT_MULT = 100.0


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


def _is_stock(leg):
    """A 100-share-lot leg (the Calculator's D4 convention), not an option."""
    return leg.get("kind") == "stock"


def _option_contracts(legs):
    """Option CONTRACTS in a leg set: each option leg's qty, share legs excluded.

    ``commissions.round_trip_commission`` bills per contract but was handed
    ``len(legs)``, which is right only while every leg is one contract. A
    butterfly's body is one dict at qty 2, and Schwab charges nothing for stock.
    For every all-options qty-1 leg set this equals ``len(legs)``, so the existing
    nine structures are billed exactly as before.
    """
    return sum(int(l.get("qty", 1) or 1) for l in legs if not _is_stock(l))


def _option_legs(legs):
    return [l for l in legs if not _is_stock(l)]


def _front_expiration(legs):
    """Earliest expiration among the OPTION legs (a share leg never expires)."""
    exps = [l["expiration"] for l in _option_legs(legs) if l.get("expiration")]
    return min(exps) if exps else None


def _needs_front_valuation(legs):
    """True when intrinsic-at-one-expiry is WRONG for this leg set: it holds a
    share leg, or its option legs span more than one expiration.

    Everything else - every structure the Finder built before 2026-09-13 - takes
    the untouched intrinsic path, which is what keeps those numbers byte-identical.
    """
    if any(_is_stock(l) for l in legs):
        return True
    return len({l.get("expiration") for l in legs}) > 1


def _front_value(leg, S, front):
    """Per-share value of one leg at the FRONT expiration with the underlying at S.

    A share is worth S. A leg expiring at the front is worth its intrinsic. A
    later leg keeps time value: Black-Scholes at its OWN IV (the chain's
    ``volatility`` is a percent) over the calendar days between the two
    expirations, floored at intrinsic, and an unusable IV RAISES. Both settle at
    16:00 ET, so whole days / 365 is exact here and is not the inline
    time-to-expiry CLAUDE.md forbids (that rule is about a wall-clock ``now``,
    which does not enter this calculation).
    """
    if _is_stock(leg):
        return float(S)
    exp = leg.get("expiration")
    if not front or not exp or exp == front:
        return _intrinsic(leg, S)
    days = (_dt.date.fromisoformat(exp) - _dt.date.fromisoformat(front)).days
    if days <= 0:
        return _intrinsic(leg, S)
    iv = leg.get("iv")
    try:
        iv = float(iv)
    except (TypeError, ValueError):
        iv = float("nan")
    if not math.isfinite(iv) or iv <= 0:
        # Schwab's -999 sentinel, a NaN, or no IV at all. Pricing anyway would turn
        # a missing input into a confident payoff (-999 clamped to a 1% vol, NaN
        # making max() order-dependent), so refuse loudly.
        raise ValueError(f"unpriceable later leg: iv={leg.get('iv')!r}")
    # The floor is INTRINSIC, not European BS: equity options are American, so a
    # long put deep in the money is worth at least K - S (it can be exercised).
    # European BS gives K*e^(-rT) - S there - below intrinsic - which would book a
    # put calendar a loss of more than its whole debit on every downside point.
    if S <= 0:
        # bs_price takes log(S/K). At a stock price of zero a call is worthless and
        # a put is worth its intrinsic, the whole strike.
        return 0.0 if leg["kind"] == "call" else float(leg["strike"])
    theo = _oc.bs_price(S, leg["strike"], days / 365.0, _oc.RISK_FREE_RATE,
                        iv / 100.0, leg["kind"])      # chain IV is always a percent
    return max(theo, _intrinsic(leg, S))


def _pl_at(legs, entry_cost, S, front=None):
    if front is None:
        v = sum(_sign(l) * _intrinsic(l, S) * l.get("qty", 1) for l in legs)
    else:
        v = sum(_sign(l) * _front_value(l, S, front) * l.get("qty", 1) for l in legs)
    return v - entry_cost


def payoff_metrics(legs, spot, symbol=None):
    entry_cost = sum(_sign(l) * l["mark"] * l.get("qty", 1) for l in legs)   # +debit
    net = round(entry_cost, 4)
    # None on every single-expiry options set -> the unchanged intrinsic path.
    front = _front_expiration(legs) if _needs_front_valuation(legs) else None

    # --- Tail analysis (structure-driven, not grid-driven) ---
    # As S->inf the payoff slope equals call_coeff = sum(sign*qty) over CALL legs.
    #   > 0 unbounded PROFIT (long call) ; < 0 unbounded LOSS (naked short call) ;
    #   == 0 bounded on the upside (verticals/condors/flies). The downside (S->0)
    # is ALWAYS bounded (puts floor at S=0), so never flag unbounded from below.
    # A long share lot slopes like a long call, so it counts here too.
    call_coeff = sum(_sign(l) * l.get("qty", 1) for l in legs
                     if l["kind"] == "call" or _is_stock(l))
    # Emit that SIDE explicitly: `unbounded` alone is True for both cases, so a
    # caller rendering a max-profit/max-loss cell cannot tell a long call from a
    # naked short. Kept as the OR of the two for back-compat (paper_trader reads it).
    unbounded_profit = (call_coeff > 0)
    unbounded_loss = (call_coeff < 0)
    unbounded = (call_coeff != 0)

    # --- Bounded extrema at payoff BREAKPOINTS (S=0, each strike, a far-high pt) ---
    strikes = [l["strike"] for l in legs if l.get("strike") is not None]
    far_high = 2.0 * max(strikes) if strikes else spot * 2.0
    points = {0.0, far_high} | set(strikes)
    if front is not None:
        # A Black-Scholes-valued curve peaks BETWEEN breakpoints, so sample it.
        points |= {far_high * i / 800 for i in range(801)}
        # ...and past it. A later leg keeps time value beyond 2x the top strike, so
        # a put diagonal's worst case (S -> infinity: the back put decays to zero
        # and the whole debit is lost) sits out there. Measured at IV 150 on a
        # 7/35-DTE ladder, stopping at far_high understated max loss by ~$117.
        points |= {far_high * 2 ** j for j in range(1, 5)}      # out to 32x the top strike
    pls = [_pl_at(legs, entry_cost, S, front) for S in sorted(points)]
    bounded_max = max(pls)
    bounded_min = min(pls)

    # Round-trip (open+close) commission in per-CONTRACT dollars — modeled as a
    # real cost against reward: subtract from max_profit, ADD to max_loss/capital
    # so R:R / capital-efficiency / grade all see net-of-commission economics.
    # Never subtracted from an UNBOUNDED profit (None). Breakevens are the
    # gross-payoff crossing levels (display convention); left unshifted.
    comm = _cm.round_trip_commission(_option_contracts(legs), symbol, 1)

    # Override the extremum on whichever side is unbounded. Everything ×100
    # (per-contract dollars), commission then folded in.
    if call_coeff > 0:          # unbounded upside profit
        max_profit = None
    else:
        max_profit = round(bounded_max * _CONTRACT_MULT - comm, 2)

    margin_proxy = round((abs(net) if net > 0 else spot * 0.20) * _CONTRACT_MULT, 2)
    if call_coeff < 0:          # unbounded upside loss -> can't read off the grid
        max_loss = round(margin_proxy + comm, 2)
        capital = max_loss
    else:
        max_loss = round(abs(bounded_min) * _CONTRACT_MULT + comm, 2)
        capital = max_loss if not unbounded else round(margin_proxy + comm, 2)

    net_debit = round(net * _CONTRACT_MULT, 2) if net > 0 else None
    net_credit = round(-net * _CONTRACT_MULT, 2) if net < 0 else None

    # --- Breakevens: scan a fine grid for sign changes + interpolate ---
    grid = [spot * (_GRID_LO + (_GRID_HI - _GRID_LO) * i / (_GRID_N - 1))
            for i in range(_GRID_N)]
    gpls = [_pl_at(legs, entry_cost, S, front) for S in grid]
    breakevens = []
    for i in range(1, len(grid)):
        if (gpls[i - 1] <= 0 < gpls[i]) or (gpls[i - 1] >= 0 > gpls[i]):
            t = gpls[i - 1] / (gpls[i - 1] - gpls[i])
            breakevens.append(round(grid[i - 1] + t * (grid[i] - grid[i - 1]), 2))

    return {
        "net_debit": net_debit,
        "net_credit": net_credit,
        "max_profit": max_profit, "max_loss": max_loss,
        "breakevens": breakevens, "unbounded": unbounded,
        "unbounded_profit": unbounded_profit, "unbounded_loss": unbounded_loss,
        "capital": capital,
        "commission": comm,
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
    front = _front_expiration(legs) if _needs_front_valuation(legs) else None
    n = 801
    lo, hi = spot - 6 * sigma, spot + 6 * sigma
    prob = 0.0
    prev_S = lo
    prev_cdf = _norm_cdf((lo - spot) / sigma)
    for i in range(1, n):
        S = lo + (hi - lo) * i / (n - 1)
        cdf = _norm_cdf((S - spot) / sigma)
        mid = (S + prev_S) / 2
        v = _pl_at(legs, 0.0, mid, front)
        if v - entry_cost > 0:
            prob += (cdf - prev_cdf)
        prev_S, prev_cdf = S, cdf
    return round(prob * 100, 1)


_LONG_DELTA, _SHORT_DELTA = 0.55, 0.28


def _band_abs(band):
    """A caller's (lo, hi) short-delta band as ABSOLUTE, ordered floats, or None.

    ``compute.INCOME_PUT_DELTA`` is SIGNED (-0.25, -0.15) while the call band is
    positive, and ``nearest_by_delta`` works on ``abs`` - so normalising here is
    what stops a caller getting the sign or the order wrong. Anything unusable
    degrades to None, i.e. the legacy fixed target, never to a band of (0, 0)
    that would aim every short at the far wing.
    """
    try:
        lo, hi = (abs(float(band[0])), abs(float(band[1])))
    except (TypeError, ValueError, IndexError):
        return None
    lo, hi = min(lo, hi), max(lo, hi)
    return None if hi <= 0 else (lo, hi)


_DIRECTIONAL = [
    ("LONG_CALL",  "call", "long",  "bullish", "Long Call",  _LONG_DELTA),
    ("LONG_PUT",   "put",  "long",  "bearish", "Long Put",   _LONG_DELTA),
    ("SHORT_CALL", "call", "short", "bearish", "Short Call", _SHORT_DELTA),
    ("SHORT_PUT",  "put",  "short", "bullish", "Short Put",  _SHORT_DELTA),
]


def _front_exp(opts_by_exp):
    return min(opts_by_exp.items(), key=lambda kv: kv[1]["dte"]) if opts_by_exp else None


def _atm_strike(strikes, spot):
    """The listed strike nearest spot, or None for an empty ladder.

    An exact tie (spot midway between two strikes) goes to the LOWER strike, so
    the choice never depends on set iteration order."""
    return min(strikes, key=lambda k: (abs(k - spot), k)) if strikes else None


def _half_em(spot, atm_iv, dte):
    """Half the 1-sigma expected move to ``dte`` - the wing target (design doc)."""
    iv = atm_iv if (atm_iv is not None and math.isfinite(atm_iv)) else 0.0   # `nan or 0` is nan
    return spot * max(iv, 0.0) * math.sqrt(max(dte, 1) / 365.0) / 2.0


def _symmetric_wing(strikes, center, target):
    """A wing DISTANCE listed on BOTH sides of ``center``, nearest ``target``.

    Butterflies and condors are built symmetric on purpose: a broken wing is a
    different risk profile, and a ladder that happens to lack the mirror strike
    must not quietly produce one. None when no distance exists on both sides.
    """
    have = {round(k, 4) for k in strikes}
    c = round(center, 4)
    dists = [round(k - c, 4) for k in have if k > c and round(2 * c - k, 4) in have]
    return min(dists, key=lambda d: abs(d - target)) if dists else None


def _leg_from(leg_data, kind, side, exp):
    return {"kind": kind, "side": side, "strike": leg_data["strike"], "expiration": exp,
            "qty": 1, "mark": leg_data["mark"], "delta": leg_data["delta"],
            "theta": leg_data["theta"], "vega": leg_data["vega"],
            "gamma": leg_data["gamma"], "iv": leg_data["iv"],
            "bid": leg_data.get("bid"), "ask": leg_data.get("ask"),
            "volume": leg_data.get("volume"), "oi": leg_data.get("oi")}


def _dte_for(exp_str):
    try:
        return max(0, (_dt.date.fromisoformat(exp_str) - _dt.date.today()).days)
    except Exception:
        return 0


def _assemble(stype, family, label, bias, legs, symbol, spot, atm_iv):
    m = payoff_metrics(legs, spot, symbol)
    front_exp = _front_expiration(legs)
    dte = _dte_for(front_exp)
    pop = pop_from_payoff(legs, spot, atm_iv, dte)
    sk = "_".join("SH" if _is_stock(l) else str(l["strike"]) for l in legs)
    return {"id": f"{symbol}_{stype}_{front_exp}_{sk}",
            "symbol": symbol, "type": stype, "family": family,
            "strategy_label": label, "bias": bias, "legs": legs,
            "expiration": front_exp, "dte": dte,
            "pop_pct": pop, "underlying_price": spot,
            "timestamp": _dt.datetime.now().isoformat(), **m}


def build_directional(chain, symbol, spot, atm_iv, dte_min, dte_max,
                      put_band=None, call_band=None):
    """Single-leg directional candidates: long/short call and put.

    ``put_band`` / ``call_band`` are the caller's SHORT-delta bands, e.g.
    ``compute.INCOME_PUT_DELTA``. They do two things and nothing else:

    * **aim** a short at the band's MIDPOINT instead of the fixed
      ``_SHORT_DELTA``. Measured on the live XOM 2026-10-16 ladder, a $5-wide
      chain offering |delta| 0.131 / 0.218 / 0.328, the 0.28 target picked 0.328
      while the 0.15-0.25 band's midpoint picks 0.218 - a third less assignment
      risk on the same chain. The midpoint convention mirrors
      ``compute._COVERED_TARGET_DELTA``, so a band edit moves both.
    * **enforce the CEILING only.** A short whose |delta| lands above ``hi`` is
      dropped; one below ``lo`` is kept. That asymmetry is deliberate: "richer
      premium and more assignment than the window documents" is the harm, and
      escaping the band DOWNWARD is a thin credit that the delta-aware edge floor
      (``credit/width >= |delta| + EDGE_MARGIN``) and the credit floor already
      refuse. A symmetric drop would add a second gate that can only empty the
      board for a reason something else already covers.

    ⚠ **A band never touches the LONG legs.** It says where the caller is willing
    to SELL premium and nothing about where to buy it - a 0.20-delta long call is
    a lottery ticket, not the 0.55 directional bet this builder intends.

    No band (the default, and every caller before 2026-09-11) keeps the fixed
    ``_SHORT_DELTA`` target with no ceiling, so behaviour is unchanged.
    """
    out = []
    bands = {"put": _band_abs(put_band), "call": _band_abs(call_band)}
    by_kind = {k: extract_options(chain, k, dte_min, dte_max) for k in ("call", "put")}
    for stype, kind, side, bias, label, target in _DIRECTIONAL:
        fe = _front_exp(by_kind[kind])
        if not fe:
            continue
        exp, data = fe
        band = bands[kind] if side == "short" else None
        if band:
            target = (band[0] + band[1]) / 2.0
        leg_data = nearest_by_delta(data["strikes"], target)
        if not leg_data:
            continue
        if band and abs(leg_data.get("delta") or 0) > band[1]:
            continue    # richer than the band's ceiling - see the docstring
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


def _short_target(band):
    return (band[0] + band[1]) / 2.0 if band else _SHORT_DELTA


def _front_pair(chain, dte_min, dte_max):
    """(exp, call_strikes, put_strikes) for the nearest expiry both maps list."""
    calls = extract_options(chain, "call", dte_min, dte_max)
    puts = extract_options(chain, "put", dte_min, dte_max)
    common = sorted(set(calls) & set(puts), key=lambda e: calls[e]["dte"])
    if not common:
        return None
    e = common[0]
    return e, calls[e]["strikes"], puts[e]["strikes"]


def build_straddles_strangles(chain, symbol, spot, atm_iv, dte_min, dte_max,
                              put_band=None, call_band=None):
    """Long/short straddle (ATM) and long/short strangle (band-midpoint shorts).

    The short-delta band's CEILING binds the short STRANGLE only - a straddle's
    shorts are ~0.50 delta by definition and applying it would delete the
    structure every time. The long strangle buys the same strikes the short
    strangle sells, so the two rows compare like for like.
    """
    fp = _front_pair(chain, dte_min, dte_max)
    if not fp:
        return []
    exp, cs, ps = fp
    out = []
    # The ATM over the UNION of both maps: extract_options drops a strike with no
    # delta, so the true ATM can be missing from one side. Recentring on the next
    # COMMON strike would build an off-centre "straddle" (a 0.30 call against a
    # -0.70 put); a hole at the money builds no straddle instead.
    k = _atm_strike(set(cs) | set(ps), spot)
    if k is not None and k in cs and k in ps:
        for stype, side, label in (("LONG_STRADDLE", "long", "Long Straddle"),
                                   ("SHORT_STRADDLE", "short", "Short Straddle")):
            legs = [_leg_from(cs[k], "call", side, exp), _leg_from(ps[k], "put", side, exp)]
            out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))
    cb, pb = _band_abs(call_band), _band_abs(put_band)
    c = nearest_by_delta({s: v for s, v in cs.items() if s > spot}, _short_target(cb))
    p = nearest_by_delta({s: v for s, v in ps.items() if s < spot}, _short_target(pb))
    if c and p:
        for stype, side, label in (("LONG_STRANGLE", "long", "Long Strangle"),
                                   ("SHORT_STRANGLE", "short", "Short Strangle")):
            if side == "short" and ((cb and abs(c["delta"]) > cb[1])
                                    or (pb and abs(p["delta"]) > pb[1])):
                continue
            legs = [_leg_from(c, "call", side, exp), _leg_from(p, "put", side, exp)]
            out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))
    return out


_LISTED_TOL = 5e-5   # half of _symmetric_wing's round(..., 4) step


def _listed(strikes, target):
    """The listed strike KEY equal to ``target`` up to float error, else None.

    Strike keys are floats parsed from the chain while wing distances are rounded,
    so ``k - d`` on a fractional ladder need not hash to the listed key. The
    tolerance is HALF the 4-dp rounding step ``_symmetric_wing`` applies (5e-5):
    that rounding can leave ``k - d`` up to that far off a listed key, and a
    tighter tolerance would silently build nothing on a 5-decimal ladder.
    """
    if not strikes:
        return None
    best = min(strikes, key=lambda x: abs(x - target))
    return best if abs(best - target) <= _LISTED_TOL else None


def build_butterflies_condors(chain, symbol, spot, atm_iv, dte_min, dte_max):
    """Call/put butterfly, iron butterfly (ATM body) and call/put condor.

    Wings sit at the listed SYMMETRIC distance nearest half the 1-sigma expected
    move to the front expiry; a condor's shorts sit one wing either side of ATM and
    its longs two. All carry ``family="NEUTRAL"`` so ``q_breakeven_vs_em`` rewards a
    wide profit zone rather than a breakeven near spot.
    """
    fp = _front_pair(chain, dte_min, dte_max)
    if not fp:
        return []
    exp, cs, ps = fp
    both = set(cs) & set(ps)
    # ATM over the UNION, and it must be listed on BOTH sides - see
    # build_straddles_strangles. Recentring on a common strike after a hole at the
    # money built a 95/105/115 "neutral" butterfly.
    k = _atm_strike(set(cs) | set(ps), spot)
    if k is None or k not in both:
        return []
    dte = _dte_for(exp)
    d = _symmetric_wing(both, k, _half_em(spot, atm_iv, dte))
    if not d:
        return []
    lo, hi = _listed(both, k - d), _listed(both, k + d)
    if lo is None or hi is None:
        return []
    out = []

    def body(leg, qty):
        leg["qty"] = qty
        return leg

    for stype, kind, m, label in (("BUTTERFLY_CALL", "call", cs, "Call Butterfly"),
                                  ("BUTTERFLY_PUT", "put", ps, "Put Butterfly")):
        legs = [_leg_from(m[lo], kind, "long", exp), body(_leg_from(m[k], kind, "short", exp), 2),
                _leg_from(m[hi], kind, "long", exp)]
        out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))

    legs = [_leg_from(ps[lo], "put", "long", exp), _leg_from(ps[k], "put", "short", exp),
            _leg_from(cs[k], "call", "short", exp), _leg_from(cs[hi], "call", "long", exp)]
    out.append(_assemble("IRON_BUTTERFLY", "NEUTRAL", "Iron Butterfly", "neutral", legs,
                         symbol, spot, atm_iv))

    lo2, hi2 = _listed(both, k - 2 * d), _listed(both, k + 2 * d)
    if lo2 is not None and hi2 is not None:
        for stype, kind, m, label in (("CONDOR_CALL", "call", cs, "Call Condor"),
                                      ("CONDOR_PUT", "put", ps, "Put Condor")):
            legs = [_leg_from(m[lo2], kind, "long", exp),
                    _leg_from(m[lo], kind, "short", exp),
                    _leg_from(m[hi], kind, "short", exp),
                    _leg_from(m[hi2], kind, "long", exp)]
            out.append(_assemble(stype, "NEUTRAL", label, "neutral", legs, symbol, spot, atm_iv))
    return out


_CAL_BACK_OFFSET, _CAL_MIN_GAP = 28, 7
# The front leg sits at least a week out. The page's default DTE window starts at
# 0, so "nearest expiry" was a 0-2 DTE front whose calendar can barely profit -
# measured call-calendar R:R -0.004 at 0/28, 0.18 at 1/29, 0.55 at 7/35 - and every
# such row was cut, leaving the Calendars checkbox showing nothing.
_CAL_MIN_FRONT_DTE = 7
_DIAG_SHORT_DELTA, _DIAG_LONG_DELTA = 0.30, 0.70


def _can_profit(sig):
    """A structure whose best case is not a positive dollar figure is not a trade."""
    mp = sig.get("max_profit")
    return isinstance(mp, (int, float)) and math.isfinite(mp) and mp > 0


def _usable_iv(iv):
    """A chain IV (percent) that _front_value can price: finite and positive.
    Schwab's -999 sentinel, NaN and 0 are not."""
    try:
        v = float(iv)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def _nearest_delta_strike(strikes, target):
    """The strike whose |delta| is nearest ``target``; a tie goes to the LOWER
    strike, so the choice never depends on dict order. None for no strikes."""
    if not strikes:
        return None
    return min(strikes, key=lambda s: (abs(abs(strikes[s]["delta"]) - target), s))


def _diagonal(kind, label, direction, f_exp, f, b_exp, b, symbol, spot, atm_iv):
    """The standard diagonal (the poor man's covered call and its put mirror):
    SELL the out-of-the-money front strike nearest 0.30 delta, BUY the
    in-the-money back strike nearest 0.70 delta, both judged against SPOT.

    Skipped when the net debit reaches the strike width. With an AT-the-money short
    that rule could never pass for calls on a flat term structure - debit = width +
    (back time value - front time value) - and passed for puts only by the sign of
    the interest rate; the out-of-the-money short puts the spot-to-short distance
    inside the width, which is what the practitioner rule assumes. Also skipped
    when max profit is not positive.
    """
    otm = {s: leg for s, leg in f["strikes"].items() if (s - spot) * direction > 0}
    itm = {s: leg for s, leg in b["strikes"].items() if (spot - s) * direction > 0}
    ks = _nearest_delta_strike(otm, _DIAG_SHORT_DELTA)
    kb = _nearest_delta_strike(itm, _DIAG_LONG_DELTA)
    if ks is None or kb is None:
        return None
    legs = [_leg_from(f["strikes"][ks], kind, "short", f_exp),
            _leg_from(b["strikes"][kb], kind, "long", b_exp)]
    if (legs[1]["mark"] - legs[0]["mark"]) >= abs(ks - kb):
        return None     # costs its width: no upside beyond rate carry
    diag = _assemble(f"DIAGONAL_{kind.upper()}", "DIRECTIONAL", f"{label} Diagonal",
                     "bullish" if direction > 0 else "bearish", legs, symbol, spot, atm_iv)
    return diag if _can_profit(diag) else None


def build_calendars(chain, symbol, spot, atm_iv, dte_min, dte_max):
    """Call/put calendar (short and long at the same ATM strike) and call/put
    diagonal (see ``_diagonal``), inside the scan's own DTE window.

    Front = nearest expiry at least ``_CAL_MIN_FRONT_DTE`` (7) days out; back = the
    expiry whose DTE is nearest front + 28 with at least 7 days between them. A
    candidate whose max profit is not a positive number is dropped. No second
    chain fetch: a window without two such expiries builds nothing, and the user
    widens DTE max for longer calendars.
    """
    out = []
    for kind, label, direction in (("call", "Call", 1), ("put", "Put", -1)):
        # A back leg without a usable IV cannot be priced at the front expiry
        # (_front_value raises on it), so it is never a candidate. The filter also
        # drops such strikes from the FRONT expiry, which is valued at intrinsic and
        # would not need one - deliberately conservative: a leg with no IV is a leg
        # whose quote is not trustworthy either.
        raw = extract_options(chain, kind, dte_min, dte_max)
        by_exp = {e: {**v, "strikes": {k: leg for k, leg in v["strikes"].items()
                                       if _usable_iv(leg.get("iv"))}}
                  for e, v in raw.items()}
        by_exp = {e: v for e, v in by_exp.items() if v["strikes"]}
        ordered = sorted(by_exp.items(), key=lambda kv: kv[1]["dte"])
        fronts = [(e, v) for e, v in ordered
                  if v["dte"] >= max(dte_min, _CAL_MIN_FRONT_DTE)]
        if not fronts:
            continue
        f_exp, f = fronts[0]
        backs = [(e, v) for e, v in ordered if v["dte"] - f["dte"] >= _CAL_MIN_GAP]
        if not backs:
            continue
        b_exp, b = min(backs, key=lambda kv: abs(kv[1]["dte"] - (f["dte"] + _CAL_BACK_OFFSET)))
        # The ATM is taken over the UNION of the two expiries' strikes BEFORE the
        # IV filter, and must survive the filter in BOTH. A true ATM dropped for a
        # missing delta or an unusable IV is a hole at the money: recentring on the
        # next common strike would build an off-centre "calendar", so that kind
        # builds none. Taking it after the filter would let a sentinel-IV ATM on
        # BOTH expiries silently recentre. The diagonal does not use this strike.
        k = _atm_strike(set(raw[f_exp]["strikes"]) | set(raw[b_exp]["strikes"]), spot)
        if k is not None and k in f["strikes"] and k in b["strikes"]:
            legs = [_leg_from(f["strikes"][k], kind, "short", f_exp),
                    _leg_from(b["strikes"][k], kind, "long", b_exp)]
            cal = _assemble(f"CALENDAR_{kind.upper()}", "NEUTRAL", f"{label} Calendar",
                            "neutral", legs, symbol, spot, atm_iv)
            if _can_profit(cal):
                out.append(cal)
        diag = _diagonal(kind, label, direction, f_exp, f, b_exp, b, symbol, spot, atm_iv)
        if diag is not None:
            out.append(diag)
    return out


def _credit_leg(kind, side, strike, mark, src, delta_key=None, carry_liq=False,
                liq_keys=("bid", "ask", "volume")):
    """Build a normalized leg from a credit-spread source dict (greeks default 0).

    When ``carry_liq`` is set (a SHORT leg — the one whose liquidity the source
    dict describes), copy the source dict's liquidity fields (``liq_keys``, in the
    normalized leg's ``bid``/``ask``/``volume`` order) onto the leg when present so
    ``q_liq`` can gate on real liquidity. ``liq_keys`` lets the CALL-short leg of an
    iron condor pull the call-side fields (``call_bid``/``call_ask``/``call_volume``)
    while the put-short uses the top-level put-side ones. Missing values (and long
    legs) stay absent so ``norm_liquidity`` degrades to a neutral 50 rather than
    false-failing — do NOT fabricate.
    """
    leg = {"kind": kind, "side": side, "strike": strike,
           "expiration": src.get("expiration"), "qty": 1, "mark": mark or 0,
           "delta": src.get(delta_key, 0) or 0 if delta_key else 0,
           "theta": 0, "vega": 0, "gamma": 0, "iv": 0}
    if carry_liq:
        for leg_field, src_key in zip(("bid", "ask", "volume"), liq_keys):
            if src.get(src_key) is not None:
                leg[leg_field] = src[src_key]
    return leg


def _normalize_credit(sig, family, label, bias, legs, source_breakevens):
    """Fill the full normalized contract for an adapted credit structure.

    Structural keys (breakevens/capital/rr/net_delta/net_gamma) are computed
    from the reconstructed legs via payoff_metrics; the source dict's
    authoritative economics (credit -> net_credit/max_profit, max_loss) and any
    real source greeks (net_theta/net_vega/pop_pct) then RE-OVERRIDE so they win
    over the leg-reconstructed zeros.

    ``source_breakevens`` are the structure-derived breakevens (short_strike
    -/+ credit). When the reconstructed legs have NO usable marks (all marks
    <= 0 -> payoff_metrics would see a zero-cost spread and produce garbage
    breakevens/capital/rr), the structural economics fall back to these
    source-derived values instead. Production IC/spread dicts always carry
    marks, so the marks-present path (computed from legs) is the normal one.

    UNITS + COMMISSION: the source ``credit`` / ``max_loss`` are PER-SHARE
    dollars; they are scaled to per-CONTRACT dollars (x100) here so credit
    families report on the SAME scale as the natively-built directional/debit
    families. Round-trip (open+close) commission is then folded in — subtracted
    from max_profit, added to max_loss — and ``capital`` is set = the (dollar,
    commission-inclusive) max_loss (a defined-risk credit spread risks exactly
    its max loss; the old per-share ``capital`` was a latent unit bug).
    """
    symbol = sig.get("symbol")
    credit = sig.get("credit")
    max_loss = sig.get("max_loss")
    spot = sig.get("underlying_price") or 0
    m = payoff_metrics(legs, spot, symbol)

    # Round-trip commission (per-contract dollars), same scale as the x100 economics.
    comm = _cm.round_trip_commission(legs, symbol, 1)

    # Authoritative source economics -> per-contract dollars, net of commission.
    credit_d = (credit * _CONTRACT_MULT) if isinstance(credit, (int, float)) else None
    max_loss_d = (max_loss * _CONTRACT_MULT) if isinstance(max_loss, (int, float)) else None
    net_credit = round(credit_d, 2) if credit_d is not None else None
    max_profit = round(credit_d - comm, 2) if credit_d is not None else None
    max_loss_net = round(max_loss_d + comm, 2) if max_loss_d is not None else None
    capital_net = max_loss_net   # defined-risk: capital == (dollar) max loss
    rr_net = (round(max_profit / max_loss_net, 3)
              if (max_profit and max_loss_net) else None)

    has_marks = any((l.get("mark") or 0) > 0 for l in legs)
    if not has_marks:
        # Override the marks-derived structural economics with source-derived ones.
        m = dict(m)
        m["breakevens"] = [round(b, 2) for b in source_breakevens]

    out = dict(sig)            # preserve source fields
    out.update(m)              # structural keys from the legs (or source fallback)
    out.update({
        "family": family, "strategy_label": label, "bias": bias, "legs": legs,
        "net_credit": net_credit, "net_debit": None,
        "max_profit": max_profit, "max_loss": max_loss_net,
        "capital": capital_net, "rr": rr_net, "commission": comm,
        # Defined risk, declared from the AUTHORITATIVE source economics — not
        # from the reconstructed legs. All three flags must be normalized here
        # together: a consumer that reads a side flag first (the webgui's max-loss
        # cell does) would let a leg-derived True outrank the `unbounded` False.
        "unbounded": False, "unbounded_profit": False, "unbounded_loss": False,
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
                    sig, delta_key="short_delta", carry_liq=True),
        _credit_leg(kind, "long", sig.get("long_strike"), sig.get("long_mark"), sig),
    ]
    # PCS breakeven = short - credit (below); CCS = short + credit (above).
    short_k = sig.get("short_strike") or 0
    credit = sig.get("credit") or 0
    src_be = [short_k - credit] if is_pcs else [short_k + credit]
    return _normalize_credit(sig, "VERTICAL", label, bias, legs, src_be)


def adapt_iron_condor(sig):
    """Adapt a build_iron_condors IC dict into the normalized signal shape.

    Put side = short_strike / long_strike; call side = call_short / call_long.
    """
    legs = [
        _credit_leg("put", "short", sig.get("short_strike"), sig.get("short_mark"), sig,
                    carry_liq=True),
        _credit_leg("put", "long", sig.get("long_strike"), sig.get("long_mark"), sig),
        _credit_leg("call", "short", sig.get("call_short"), sig.get("call_short_mark"), sig,
                    carry_liq=True, liq_keys=("call_bid", "call_ask", "call_volume")),
        _credit_leg("call", "long", sig.get("call_long"), sig.get("call_long_mark"), sig),
    ]
    # IC breakevens: put_short - credit (lower) and call_short + credit (upper).
    credit = sig.get("credit") or 0
    src_be = [(sig.get("short_strike") or 0) - credit,
              (sig.get("call_short") or 0) + credit]
    return _normalize_credit(sig, "NEUTRAL", "Iron Condor", "neutral", legs, src_be)
