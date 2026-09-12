"""Reprice open signals. 0-DTE uses intrinsic vs settlement; swings close at a
realistic limit worked 40% into the net spread market (fill_model.FILL_FRAC),
via the shared fill_model so the paper broker and re-pricer can never diverge."""
import math
import datetime
import logging
import pathlib as _pathlib
import sys as _sys

import fill_model

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import structures as _structures  # noqa: E402

log = logging.getLogger("signal_repricer")

MULTIPLIER = 100

# Per-run cache keyed by (symbol, expiration) — avoids redundant API calls
_chain_cache = {}


def clear_chain_cache():
    """Call at the start of each EOD run to reset the cache."""
    _chain_cache.clear()


def _is_expired(expiration, today=None):
    """True when `expiration` is strictly before `today` (no live chain exists).

    Schwab 400s on a chain request for a past expiration, so re-pricing an
    already-expired trade just burns an API call (and time) every cycle. Mirrors
    the proxy's /track guard: today's 0-DTE (`expiration == today`) is NOT expired
    and still prices. Malformed / missing expirations return False so the normal
    live path still runs. `today` is injectable for tests; defaults to local date.
    """
    if today is None:
        today = datetime.date.today()
    try:
        exp = (expiration if isinstance(expiration, datetime.date)
               else datetime.date.fromisoformat(str(expiration)[:10]))
    except (TypeError, ValueError):
        return False
    return exp < today


def intrinsic_value(trade, settlement):
    """Return (per-contract value, realized PnL dollars) at settlement."""
    strat = trade["strategy"]
    sk = trade["short_strike"]
    lk = trade["long_strike"]
    credit = trade["entry_credit"]
    sp = settlement

    if strat == "PCS":
        net = max(sk - sp, 0) - max(lk - sp, 0)
    elif strat == "CCS":
        net = max(sp - sk, 0) - max(sp - lk, 0)
    elif strat == "IC":
        put_val = max(sk - sp, 0) - max(lk - sp, 0)
        cs = trade.get("call_short") or 0
        cl = trade.get("call_long") or 0
        call_val = max(sp - cs, 0) - max(sp - cl, 0)
        net = put_val + call_val
    else:
        net = 0

    pnl = (credit - net) * MULTIPLIER
    return net, pnl


def _leg_sign(leg):
    return 1.0 if leg.get("side") == "long" else -1.0


def _leg_intrinsic(leg, sp):
    """Per-share intrinsic value of one option leg at underlying ``sp`` (unsigned, ≥0)."""
    k = leg["strike"]
    return max(sp - k, 0.0) if leg.get("kind") == "call" else max(k - sp, 0.0)


def position_intrinsic(legs, sp):
    """Signed NET per-share intrinsic of a legs list (long +, short −), qty-weighted."""
    return sum(_leg_sign(leg) * _leg_intrinsic(leg, sp) * (leg.get("qty") or 1)
               for leg in legs or [])


def legs_intrinsic_value(trade, settlement):
    """(per-contract net value, realized PnL dollars) at settlement for a DEBIT/legs trade.

    Generic analog of ``intrinsic_value`` for the non-credit structures (long single
    options + debit verticals) the swing scanner produces. The position is worth its net
    intrinsic at expiry; P&L = that value − the debit paid. ``entry_debit`` is PER-CONTRACT
    dollars (the scanner's ``net_debit``)."""
    net_per_share = position_intrinsic(trade.get("legs"), settlement)
    net_contract = round(net_per_share * MULTIPLIER, 2)
    entry_debit = trade.get("entry_debit") or 0.0
    pnl = round(net_contract - entry_debit, 2)
    return net_per_share, pnl



def _chain_spot(chain):
    """The underlying's last price off an option chain, or ``None``.

    **Both shapes are real, and the ORDER is load-bearing.** The nested
    ``underlying.last`` is the live quote and is preferred where it exists;
    ``underlyingPrice`` is the fallback because it is **pinned to the prior close
    outside RTH** (a documented Schwab quirk that once froze every GTH gamma
    number). ``atm_iv`` below established that order against a live chain on
    2026-08-25; this function exists so the two cannot disagree about it.

    ⚠ **The two call sites that mark positions had it wrong from the start** —
    ``(chain.get("underlying") or {}).get("last", 0)`` — and ``underlying`` is
    populated only with ``includeUnderlyingQuote=true``, which this app never
    requests. Measured live: a SPY chain came back ``underlying: None`` /
    ``underlyingPrice: 764.29``, so the expression resolved to its DEFAULT on
    every single call, and the default was **0**. Prod's
    ``signal_marks.current_underlying`` is 0.0 across all 58,895 rows while every
    sibling column on the same row is populated. That disabled a RULE, not just a
    display field: ``signal_recommender._recoverable`` early-returns on
    ``spot <= 0``, so the ``RECOVERY_MIN_CUSHION`` deferral was permanently off on
    the captured-signal path — degrading to "the stop fires", which is why nothing
    ever looked wrong.

    ⚠ **A non-positive reading returns ``None``, never 0.** ``scanner_engine``
    defaults a missing ``underlyingPrice`` to 0 in its own chain plumbing, so a 0
    arriving here means "not read" rather than "the stock is worthless" — and a 0
    spot is worse than an absent one: it sorts among real prices, compares as
    below every strike, and renders as a crash. The documented
    "never print a zero you did not read" rule.
    """
    chain = chain or {}
    for value in ((chain.get("underlying") or {}).get("last"),
                  chain.get("underlyingPrice")):
        try:
            spot = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(spot) and spot > 0:
            return spot
    return None

def reprice_legs(trade, client, today=None):
    """Reprice a DEBIT/legs paper trade for display (same return shape as ``reprice_swing``).

    Values each stored leg at its CURRENT mid (long +, short −), sums to a net per-share
    value, and computes the unrealized P&L per contract = ``value×100 − entry_debit``. An
    already-expired trade returns ``error="expired"`` (no doomed chain fetch). Never raises."""
    if _is_expired(trade.get("expiration"), today):
        return {"current_value": None, "unrealized_pnl": None, "pnl_pct_of_credit": None,
                "current_underlying": None, "current_short_delta": None, "error": "expired"}
    try:
        chain = _fetch_chain(client, trade["symbol"], trade["expiration"])
        if chain is None:
            raise RuntimeError("chain None")
        pm = chain.get("putExpDateMap", {})
        cm = chain.get("callExpDateMap", {})
        net_per_share = 0.0
        for leg in trade.get("legs") or []:
            leg_map = cm if leg.get("kind") == "call" else pm
            mid, _ = _leg_mid(leg_map, leg["strike"])
            if mid is None:
                raise RuntimeError("missing leg quote")
            net_per_share += _leg_sign(leg) * mid * (leg.get("qty") or 1)
        entry_debit = trade.get("entry_debit") or 0.0
        pnl = round(net_per_share * MULTIPLIER - entry_debit, 2)   # per contract
        pnl_pct = round(pnl / entry_debit * 100.0, 2) if entry_debit else 0.0
        underlying = _chain_spot(chain)
        return {"current_value": round(net_per_share, 2), "unrealized_pnl": pnl,
                "pnl_pct_of_credit": pnl_pct, "current_underlying": underlying,
                "current_short_delta": None, "error": None}
    except Exception as e:
        log.error(f"reprice_legs failed for {trade.get('symbol')}: {e}")
        return {"current_value": None, "unrealized_pnl": None, "pnl_pct_of_credit": None,
                "current_underlying": None, "current_short_delta": None,
                "error": "repricing failed"}


def _fetch_chain(client, symbol, expiration):
    """Wrapper for Schwab option chain. Cached per (symbol, expiration) per run.

    `expiration` is stored in signals.db as an ISO string ("YYYY-MM-DD"), but
    schwab-py validates from_date/to_date as datetime.date — convert here.
    """
    key = (symbol, expiration)
    if key in _chain_cache:
        return _chain_cache[key]
    exp_date = (
        datetime.date.fromisoformat(expiration)
        if isinstance(expiration, str)
        else expiration
    )
    r = client.get_option_chain(
        symbol,
        from_date=exp_date,
        to_date=exp_date,
        contract_type=client.Options.ContractType.ALL,
    )
    result = r.json() if r.status_code == 200 else None
    _chain_cache[key] = result
    return result


def _leg_mid(leg_map, strike):
    """Return (mid_price, delta) or (None, None) if unquoted."""
    for exp_key, strikes in leg_map.items():
        key = f"{float(strike):.1f}"
        if key in strikes:
            ctr = strikes[key][0]
            bid = ctr.get("bid", 0) or 0
            ask = ctr.get("ask", 0) or 0
            if bid <= 0 or ask <= 0:
                return None, None
            return (bid + ask) / 2, ctr.get("delta")
    return None, None


def _leg_bid_ask(leg_map, strike):
    """Return (bid, ask, delta) or (None, None, None) if unquoted / one-sided."""
    if strike is None:
        return None, None, None
    for _exp_key, strikes in leg_map.items():
        key = f"{float(strike):.1f}"
        if key in strikes:
            ctr = strikes[key][0]
            bid = ctr.get("bid", 0) or 0
            ask = ctr.get("ask", 0) or 0
            if bid <= 0 or ask <= 0:
                return None, None, None
            return bid, ask, ctr.get("delta")
    return None, None, None



#############################################
# NET POSITION GREEKS (gap assessment C4)
#############################################

#: The greek names read off a Schwab contract, and the keys the book sums.
_GREEK_KEYS = ("delta", "gamma", "theta", "vega")

#: Leg layout per structure: ``(map, strike_field, side)`` where side is -1 for a
#: SHORT leg. Keyed on the CANONICAL structure name so ``NAKED_PUT`` and
#: ``SHORT_PUT`` cannot be given different layouts - the same reason
#: ``shared.structures`` exists.
_LEG_LAYOUT = {
    "PCS": (("put", "short_strike", -1), ("put", "long_strike", +1)),
    "CCS": (("call", "call_short", -1), ("call", "call_long", +1)),
    "IC": (("put", "short_strike", -1), ("put", "long_strike", +1),
           ("call", "call_short", -1), ("call", "call_long", +1)),
    "SHORT_PUT": (("put", "short_strike", -1),),
    "COVERED_CALL": (("call", "short_strike", -1),),
}


def _leg_greeks(leg_map, strike):
    """``{delta, gamma, theta, vega}`` for one strike, values possibly ``None``.

    ``None`` for the whole leg when the strike is absent from the map - which is
    what makes an unquotable leg refuse the POSITION rather than contribute zero.
    """
    if strike is None:
        return None
    try:
        key = f"{float(strike):.1f}"
    except (TypeError, ValueError):
        return None
    for _exp_key, strikes in (leg_map or {}).items():
        row = (strikes or {}).get(key)
        if row:
            ctr = row[0] or {}
            return {g: _finite_greek(ctr.get(g)) for g in _GREEK_KEYS}
    return None


def _finite_greek(value):
    """A real number, or ``None``. Rejects bool and every non-finite float - a NaN
    would propagate into the book's sum and make every comparison against it
    False, which is this repo's most-documented bug class."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def position_greeks(trade, chain):
    """Net per-contract Greeks for one position: ``{net_delta, net_gamma,
    net_theta, net_vega}``, any value possibly ``None``.

    **Signs follow the POSITION, not the option.** A short leg contributes MINUS
    its greek, so a put credit spread comes out net POSITIVE delta (it profits as
    the underlying rises), net negative gamma, net POSITIVE theta (a credit book
    earns time - Schwab reports per-option theta as negative) and net negative
    vega. A sign error here would render a premium-selling book as long
    volatility, which is why the tests assert each sign rather than a magnitude.

    Costs no API call: ``chain`` is the one ``reprice_swing`` already fetched.
    Pure and defensive - a display number must never be able to break a mark.

    ⚠ **``None`` means "not computed", never zero.** A zero delta is a real and
    meaningful reading (a balanced iron condor), so a position whose leg was
    unquotable must not join the book's sum as flat. A leg missing ONE greek
    yields ``None`` for that greek only: partial data is normal off-hours, and
    dropping the position entirely would lose a usable direction reading.
    """
    out = {f"net_{g}": None for g in _GREEK_KEYS}
    if not isinstance(trade, dict) or not isinstance(chain, dict):
        return out
    layout = _LEG_LAYOUT.get(_structures.canonical(trade.get("strategy")))
    if not layout:
        return out

    maps = {"put": chain.get("putExpDateMap") or {},
            "call": chain.get("callExpDateMap") or {}}
    legs = []
    for right, field, side in layout:
        got = _leg_greeks(maps[right], trade.get(field))
        if got is None:
            return out            # an unquotable leg refuses the position
        legs.append((side, got))
    if not legs:
        return out

    for g in _GREEK_KEYS:
        parts = [side * leg[g] for side, leg in legs if leg[g] is not None]
        if len(parts) == len(legs):
            out[f"net_{g}"] = round(sum(parts), 6)
    return out


def _greeks_or_none(trade, chain):
    """:func:`position_greeks`, guarded. See the call site in ``reprice_swing``."""
    try:
        return position_greeks(trade, chain)
    except Exception:  # noqa: BLE001 - a display number must not cost the mark.
        log.debug("position_greeks failed for %s",
                  (trade or {}).get("symbol"), exc_info=True)
        return {f"net_{g}": None for g in _GREEK_KEYS}

def reprice_swing(trade, client, today=None):
    """Return a dict with current_value, unrealized_pnl, etc. Never raises.

    An already-expired trade has no live option chain (Schwab 400s on a past
    expiration), so the chain fetch is skipped entirely and an unpriceable mark
    (`error="expired"`) is returned — saving a doomed API call every reprice
    cycle. Downstream (`signal_recommender.build_mark`, `eod_report`) treats any
    truthy `error` the same, so this matches the prior failed-fetch behavior."""
    if _is_expired(trade.get("expiration"), today):
        log.info("reprice_swing: %s exp %s already expired — skipping chain fetch",
                 trade.get("symbol"), trade.get("expiration"))
        return {
            "current_value": None, "unrealized_pnl": None, "pnl_pct_of_credit": None,
            "current_underlying": None, "current_short_delta": None,
            "error": "expired",
        }
    try:
        chain = _fetch_chain(client, trade["symbol"], trade["expiration"])
        if chain is None:
            raise RuntimeError("chain None")

        strat = trade["strategy"]
        short_delta = None
        # Close cost = realistic buy-to-close (limit worked FILL_FRAC into the
        # net spread market), via the shared fill_model used by the broker.
        if strat == "PCS":
            pm = chain.get("putExpDateMap", {})
            sb, sa, short_delta = _leg_bid_ask(pm, trade["short_strike"])
            lb, la, _ = _leg_bid_ask(pm, trade["long_strike"])
            if None in (sb, sa, lb, la):
                raise RuntimeError("missing leg quotes")
            debit = fill_model.realistic_vertical_fill(sb, sa, lb, la, "BUY_TO_CLOSE")
        elif strat == "CCS":
            cm = chain.get("callExpDateMap", {})
            sb, sa, short_delta = _leg_bid_ask(cm, trade["short_strike"])
            lb, la, _ = _leg_bid_ask(cm, trade["long_strike"])
            if None in (sb, sa, lb, la):
                raise RuntimeError("missing leg quotes")
            debit = fill_model.realistic_vertical_fill(sb, sa, lb, la, "BUY_TO_CLOSE")
        elif strat == "IC":
            pm = chain.get("putExpDateMap", {})
            cm = chain.get("callExpDateMap", {})
            psb, psa, short_delta = _leg_bid_ask(pm, trade["short_strike"])
            plb, pla, _ = _leg_bid_ask(pm, trade["long_strike"])
            csb, csa, _ = _leg_bid_ask(cm, trade.get("call_short"))
            clb, cla, _ = _leg_bid_ask(cm, trade.get("call_long"))
            if None in (psb, psa, plb, pla, csb, csa, clb, cla):
                raise RuntimeError("missing leg quotes")
            debit = (fill_model.realistic_vertical_fill(psb, psa, plb, pla, "BUY_TO_CLOSE")
                     + fill_model.realistic_vertical_fill(csb, csa, clb, cla, "BUY_TO_CLOSE"))
        elif _structures.is_single_leg(strat):
            # The Income Window's two structures. Each is ONE short option, so it
            # prices off that leg's own market and never the net-spread form with
            # zero quotes for a leg that does not exist. The strike lives in
            # ``short_strike`` for BOTH - the same field the spreads use for their
            # short leg - and only the side differs, so the taxonomy decides it.
            side = ("putExpDateMap" if _structures.is_put_side(strat)
                    else "callExpDateMap")
            sb, sa, short_delta = _leg_bid_ask(chain.get(side, {}),
                                               trade["short_strike"])
            if None in (sb, sa):
                raise RuntimeError("missing leg quotes")
            debit = fill_model.realistic_single_fill(sb, sa, "BUY_TO_CLOSE")
        else:
            raise RuntimeError(f"unknown strategy {strat}")
        debit = round(debit, 2)

        underlying = _chain_spot(chain)
        pnl = (trade["entry_credit"] - debit) * MULTIPLIER
        # pnl_pct_of_credit is stored as a PERCENT (e.g. 50.0 for 50% of max
        # credit captured), matching the `_pct` convention used by rr_pct,
        # pop_pct, move_pct, etc. Previously stored as a ratio (0.50) which
        # was the odd one out and invited 100x display bugs.
        if trade["entry_credit"]:
            pnl_pct = (trade["entry_credit"] - debit) / trade["entry_credit"] * 100.0
        else:
            pnl_pct = 0.0
        return {
            "current_value": debit,
            "unrealized_pnl": pnl,
            "pnl_pct_of_credit": pnl_pct,
            "current_underlying": underlying,
            "current_short_delta": short_delta,
            # ATM IV off the SAME chain — no extra Schwab call. Feeds the Trade
            # detail panel's Expected Move for captured signals, which had a
            # price but no IV at all and so never rendered that expansion.
            "current_short_iv": atm_iv(chain),
            # Net per-position Greeks (gap assessment C4), off the chain already
            # in hand. Guarded separately: the mark is the money path and these
            # are a display number, so a failure here must not lose the reprice.
            **_greeks_or_none(trade, chain),
            "error": None,
        }
    except Exception as e:
        log.error(f"reprice_swing failed for {trade.get('symbol')}: {e}")
        return {
            "current_value": None, "unrealized_pnl": None, "pnl_pct_of_credit": None,
            "current_underlying": None, "current_short_delta": None,
            "error": "repricing failed",
        }


# ── ATM implied volatility, off a chain already in hand ──────────────────────

# Schwab returns this for a contract it cannot price. It is a SENTINEL, not a
# reading — accepting it as an IV is a documented bug class in this repo
# (flow_skew._as_float took it as usable until 2026-08-20).
_IV_SENTINEL = -999.0


def _usable_iv(v):
    """A real positive IV percentage, or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):      # NaN / inf
        return None
    if f <= 0 or f == _IV_SENTINEL:
        return None
    return f


def atm_iv(chain):
    """ATM implied volatility from an option chain, or None. Never raises.

    Deliberately the strike NEAREST SPOT rather than the position's short leg:
    the short leg is out-of-the-money, so its IV carries skew and would
    systematically overstate the expected move — upward for a put spread, which
    is most of this book.

    Costs no API call: the captured-signal reprice cycle already fetches this
    chain to mark the legs, so the IV rides on data in hand.
    """
    try:
        # Both shapes are real, and the preference order (nested live quote,
        # then the RTH-stale `underlyingPrice`) is this function's own finding
        # from 2026-08-25 — now the shared `_chain_spot`, so the three sites that
        # need a spot off a chain cannot drift. Its `> 0` rule is the same one
        # this block used to carry inline.
        spot = _chain_spot(chain or {})
        if spot is None:
            return None
        best = None
        for map_key in ("putExpDateMap", "callExpDateMap"):
            for _exp, strikes in (chain.get(map_key) or {}).items():
                for k, contracts in (strikes or {}).items():
                    iv = _usable_iv((contracts or [{}])[0].get("volatility"))
                    if iv is None:
                        continue
                    dist = abs(float(k) - float(spot))
                    if best is None or dist < best[0]:
                        best = (dist, iv)
        return best[1] if best else None
    except Exception:       # noqa: BLE001 — a missing EM beats a failed reprice
        return None
