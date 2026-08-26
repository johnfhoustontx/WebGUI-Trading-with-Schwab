"""Reprice open signals. 0-DTE uses intrinsic vs settlement; swings close at a
realistic limit worked 40% into the net spread market (fill_model.FILL_FRAC),
via the shared fill_model so the paper broker and re-pricer can never diverge."""
import datetime
import logging

import fill_model

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
        underlying = (chain.get("underlying") or {}).get("last", 0)
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
        else:
            raise RuntimeError(f"unknown strategy {strat}")
        debit = round(debit, 2)

        underlying = (chain.get("underlying") or {}).get("last", 0)
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
        # ⚠ Both shapes are real. Measured against a live Schwab chain on
        # 2026-08-25, `underlying` came back NULL while the top-level
        # `underlyingPrice` carried the spot and the contracts carried real
        # volatilities — so reading only the nested form refused a perfectly
        # good IV for want of a price. The nested `last` is preferred where it
        # exists because it is the live quote; `underlyingPrice` is pinned to
        # the prior close outside RTH.
        chain = chain or {}
        spot = ((chain.get("underlying") or {}).get("last")
                or chain.get("underlyingPrice"))
        if isinstance(spot, bool) or not isinstance(spot, (int, float)) or spot <= 0:
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
