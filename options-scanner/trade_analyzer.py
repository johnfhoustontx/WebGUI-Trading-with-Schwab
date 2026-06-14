"""
trade_analyzer.py - Deep Trade Analysis Engine
Version: 1.0.0
Last Updated: 2026-04-12

Analyzes an open paper trade against live market data.
Returns a structured dict consumed by the dashboard popup.
"""

import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/Chicago")


def _find_contract_in_chain(chain, strike, option_type):
    """Find a specific contract in a Schwab option chain."""
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    date_map = chain.get(map_key, {})
    for exp_key, strikes_data in date_map.items():
        for strike_str, contracts in strikes_data.items():
            try:
                sk = float(strike_str)
            except (ValueError, TypeError):
                continue
            if abs(sk - strike) < 0.51:
                if isinstance(contracts, list) and contracts:
                    c = contracts[0]
                    bid = c.get("bid", 0) or 0
                    ask = c.get("ask", 0) or 0
                    mark = c.get("mark", 0)
                    if not mark or mark <= 0:
                        if bid > 0 and ask > 0:
                            mark = (bid + ask) / 2.0
                    return {
                        "mark": mark or 0,
                        "bid": bid,
                        "ask": ask,
                        "delta": c.get("delta", 0) or 0,
                        "theta": c.get("theta", 0) or 0,
                        "vega": c.get("vega", 0) or 0,
                        "gamma": c.get("gamma", 0) or 0,
                        "iv": c.get("volatility", 0) or 0,
                    }
    return None


def _fetch_live_data(client, trade):
    """Fetch current underlying price and option chain for a trade."""
    sym = trade["symbol"]
    api_sym = "$SPX" if sym == "SPX" else sym

    r = client.get_quotes([api_sym])
    if r.status_code != 200:
        raise RuntimeError(f"Quote fetch failed for {sym} (HTTP {r.status_code})")
    quotes = r.json()
    q = quotes.get(api_sym, quotes.get(sym, {}))
    ref = q.get("quote", q.get("reference", {}))
    price = ref.get("lastPrice") or ref.get("mark") or ref.get("closePrice")
    if not price:
        raise RuntimeError(f"No price data for {sym}")
    price = float(price)

    from scanner_engine import fetch_option_chain
    exp_str = trade["expiration"]
    try:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise RuntimeError(f"Invalid expiration date: {exp_str}")

    chain = fetch_option_chain(client, api_sym,
                               from_date=exp_date, to_date=exp_date)
    if not chain or chain.get("status") == "FAILED":
        chain = fetch_option_chain(client, sym,
                                   from_date=exp_date, to_date=exp_date)
    if not chain or chain.get("status") == "FAILED":
        raise RuntimeError(f"No option chain for {sym} exp {exp_date}")

    return price, chain, ref


def _calc_adaptive_thresholds(dte_at_entry, dte_remaining):
    """Calculate adaptive profit target and loss threshold."""
    if dte_at_entry <= 0:
        dte_frac = 0.0
    else:
        dte_frac = max(0.0, dte_remaining / dte_at_entry)

    if dte_frac > 0.6:
        target_pct = 80.0
    elif dte_frac > 0.3:
        target_pct = 65.0
    else:
        target_pct = 50.0

    if dte_frac > 0.6:
        loss_pct = 100.0
    elif dte_frac > 0.3:
        loss_pct = 75.0
    else:
        loss_pct = 50.0

    return target_pct, loss_pct, dte_frac


def _calc_current_spread_value(chain, trade):
    """Calculate current debit-to-close, greeks, and spread bid/ask from live chain.

    For a credit spread the spread quotes (per share) are:
        spread_bid = max(short.bid - long.ask, 0)   # least debit to close
        spread_ask = max(short.ask - long.bid, 0)   # most debit to close
    For an Iron Condor the put-spread and call-spread quotes are summed.
    """
    strategy = trade["strategy"]
    short_strike = trade["short_strike"]
    long_strike = trade["long_strike"]

    if strategy == "PCS":
        short_type, long_type = "put", "put"
    elif strategy == "CCS":
        short_type, long_type = "call", "call"
    elif strategy == "IC":
        short_type, long_type = "put", "put"
    else:
        raise RuntimeError(f"Unknown strategy: {strategy}")

    short_c = _find_contract_in_chain(chain, short_strike, short_type)
    long_c = _find_contract_in_chain(chain, long_strike, long_type)

    if not short_c:
        raise RuntimeError(f"No live data for short {short_type} @ {short_strike}")
    if not long_c:
        raise RuntimeError(f"No live data for long {long_type} @ {long_strike}")

    debit = short_c["mark"] - long_c["mark"]
    spread_bid = max(short_c["bid"] - long_c["ask"], 0.0)
    spread_ask = max(short_c["ask"] - long_c["bid"], 0.0)

    net_delta = long_c["delta"] - short_c["delta"]
    net_theta = long_c["theta"] - short_c["theta"]
    net_vega  = long_c["vega"]  - short_c["vega"]
    net_gamma = long_c["gamma"] - short_c["gamma"]

    if strategy == "IC":
        call_short = trade.get("call_short", 0)
        call_long = trade.get("call_long", 0)
        cs = _find_contract_in_chain(chain, call_short, "call")
        cl = _find_contract_in_chain(chain, call_long, "call")
        if not cs:
            raise RuntimeError(f"No live data for call short @ {call_short}")
        if not cl:
            raise RuntimeError(f"No live data for call long @ {call_long}")
        debit += cs["mark"] - cl["mark"]
        spread_bid += max(cs["bid"] - cl["ask"], 0.0)
        spread_ask += max(cs["ask"] - cl["bid"], 0.0)
        net_delta += cl["delta"] - cs["delta"]
        net_theta += cl["theta"] - cs["theta"]
        net_vega  += cl["vega"]  - cs["vega"]
        net_gamma += cl["gamma"] - cs["gamma"]

    greeks = {
        "delta": round(net_delta, 4),
        "theta": round(net_theta, 3),
        "vega": round(net_vega, 3),
        "gamma": round(net_gamma, 4),
    }
    quotes = {
        "bid": round(spread_bid, 4),
        "ask": round(spread_ask, 4),
        "mark": round(max(debit, 0), 4),
    }

    return round(max(debit, 0), 4), greeks, quotes


def _calc_verdict(unrealized_pnl, max_profit, max_loss, target_pct,
                  loss_pct, dte_remaining, underlying_now, trade):
    """Determine TAKE PROFIT / HOLD / CLOSE verdict."""
    if max_profit > 0:
        capture_pct = (unrealized_pnl / max_profit) * 100.0
    else:
        capture_pct = 0.0

    short_strike = trade["short_strike"]
    strategy = trade["strategy"]

    strike_breached = False
    if strategy == "PCS" and underlying_now <= short_strike:
        strike_breached = True
    elif strategy == "CCS" and underlying_now >= short_strike:
        strike_breached = True
    elif strategy == "IC":
        call_short = trade.get("call_short", 0)
        if underlying_now <= short_strike or underlying_now >= call_short:
            strike_breached = True

    if capture_pct >= target_pct:
        return {
            "action": "TAKE PROFIT",
            "color": "green",
            "rationale": (f"{capture_pct:.0f}% of max profit captured with "
                         f"{dte_remaining} DTE remaining \u2014 take the win"),
            "profit_capture_pct": round(capture_pct, 1),
        }

    if strike_breached:
        return {
            "action": "CLOSE",
            "color": "red",
            "rationale": "Short strike breached \u2014 cut losses to limit damage",
            "profit_capture_pct": round(capture_pct, 1),
        }

    if dte_remaining <= 1 and capture_pct < 90:
        # Check moneyness before recommending close — deep OTM spreads
        # expiring today/tomorrow have negligible assignment risk.
        dist = abs(underlying_now - short_strike)
        dist_pct = (dist / underlying_now * 100) if underlying_now else 0
        if strategy == "CCS":
            dist = short_strike - underlying_now
            dist_pct = (dist / underlying_now * 100) if underlying_now else 0
        elif strategy == "IC":
            call_short = trade.get("call_short", 0)
            dist_put = underlying_now - short_strike
            dist_call = call_short - underlying_now
            dist = min(dist_put, dist_call)
            dist_pct = (dist / underlying_now * 100) if underlying_now else 0

        day_label = "today" if dte_remaining == 0 else "tomorrow"

        if dist_pct >= 3.0:
            # Deep OTM — safe to let expire
            return {
                "action": "HOLD",
                "color": "green",
                "rationale": (f"Expiring {day_label} but short strike is "
                              f"{dist_pct:.1f}% OTM \u2014 safe to let expire"),
                "profit_capture_pct": round(capture_pct, 1),
            }
        else:
            return {
                "action": "CLOSE",
                "color": "red",
                "rationale": (f"Expiring {day_label} with short strike only "
                              f"{dist_pct:.1f}% away \u2014 close to avoid assignment"),
                "profit_capture_pct": round(capture_pct, 1),
            }

    loss_amount = -unrealized_pnl if unrealized_pnl < 0 else 0
    if max_loss > 0 and loss_amount >= (loss_pct / 100.0) * max_loss:
        return {
            "action": "CLOSE",
            "color": "red",
            "rationale": (f"Loss at {loss_amount / max_loss * 100:.0f}% of max loss "
                         f"with {dte_remaining} DTE \u2014 cut to preserve capital"),
            "profit_capture_pct": round(capture_pct, 1),
        }

    if capture_pct > 0:
        if dte_remaining <= 21:
            msg = (f"Trade on track \u2014 {capture_pct:.0f}% of max profit captured, "
                   f"theta decay accelerating")
        else:
            msg = (f"Trade on track \u2014 {capture_pct:.0f}% of max profit captured, "
                   f"hold for {target_pct:.0f}% target")
    else:
        msg = f"Position underwater but within tolerance \u2014 {dte_remaining} DTE remaining"

    return {
        "action": "HOLD",
        "color": "amber",
        "rationale": msg,
        "profit_capture_pct": round(capture_pct, 1),
    }


def _compute_breakeven(trade):
    """Return breakeven price(s). Uses stored value if parseable, else computes.

    PCS: short - credit_per_share  -> float
    CCS: short + credit_per_share  -> float
    IC:  both sides                -> "$put_be / $call_be" string
    """
    stored = trade.get("breakeven", "")
    if stored not in ("", None):
        try:
            return float(stored)
        except (ValueError, TypeError):
            if isinstance(stored, str) and "/" in stored:
                return stored

    strategy = trade["strategy"]
    qty = trade.get("quantity", 1) or 1
    credit_total = trade.get("entry_credit_total", 0) or 0
    credit_per_share = credit_total / qty / 100
    short_k = trade["short_strike"]

    if strategy == "PCS":
        return round(short_k - credit_per_share, 2)
    if strategy == "CCS":
        return round(short_k + credit_per_share, 2)
    if strategy == "IC":
        put_be = round(short_k - credit_per_share, 2)
        call_be = round(trade["call_short"] + credit_per_share, 2)
        return f"${put_be:.2f} / ${call_be:.2f}"
    return ""


def _classify_strike_vs_em(distance, em_scaled):
    """Classify short strike distance vs expected-move band (position-perspective).

    distance: absolute value in $. em_scaled: 1\u03c3 move in $ over the relevant horizon.
    """
    if em_scaled is None or em_scaled <= 0:
        return "N/A"
    d = abs(distance)
    if d > em_scaled * 2:
        return "Outside 2\u03c3"
    if d > em_scaled:
        return "Outside 1\u03c3"
    return "Inside expected move"


def _expiration_pnl(trade, price):
    """Expiration P&L at underlying price (dollars, credit-positive).

    Computes intrinsic value of each leg at expiry, combines into spread debit,
    and returns credit_total - debit_total. Works for PCS/CCS/IC.
    """
    strategy = trade["strategy"]
    qty = trade.get("quantity", 1) or 1
    credit_total = trade.get("entry_credit_total", 0) or 0
    short_k = trade["short_strike"]
    long_k = trade["long_strike"]

    if strategy == "PCS":
        debit = max(short_k - price, 0) - max(long_k - price, 0)
    elif strategy == "CCS":
        debit = max(price - short_k, 0) - max(price - long_k, 0)
    elif strategy == "IC":
        put_debit = max(short_k - price, 0) - max(long_k - price, 0)
        call_short = trade["call_short"]
        call_long = trade["call_long"]
        call_debit = max(price - call_short, 0) - max(price - call_long, 0)
        debit = put_debit + call_debit
    else:
        debit = 0
    return round(credit_total - debit * qty * 100, 2)


def _check_scenario_strike_proximity(trade, scenarios):
    """Flag when a ±1σ scenario lands within one strike-width of a max-loss pin.

    For a PCS the max-loss pin is the long put strike; a −1σ move landing
    near/below it means a standard expected move essentially eliminates the
    long-leg protection. Symmetric for CCS (long call) and IC (either wing).
    """
    warnings = []
    strategy = trade["strategy"]
    width = abs(trade.get("width", 0) or 0)
    # Buffer: within 20% of spread width of the pin counts as "near"
    buffer = max(width * 0.20, 1.0)

    def _near_pin(price, pin, direction):
        """direction: 'below' for PCS-long, 'above' for CCS-long."""
        if direction == "below":
            return price <= pin + buffer
        return price >= pin - buffer

    for sc in scenarios:
        if strategy in ("PCS", "IC") and "\u22121\u03c3" in sc["label"]:
            pin = trade["long_strike"]  # PCS long put
            if _near_pin(sc["price"], pin, "below"):
                warnings.append(
                    f"−1σ MOVE AT MAX-LOSS PIN: ${sc['price']:,.2f} is within "
                    f"${buffer:.2f} of the long put strike (${pin:,.2f}). "
                    "A standard 1-sigma move essentially eliminates the "
                    "long-leg protection."
                )
        if strategy in ("CCS", "IC") and "+1\u03c3" in sc["label"]:
            pin = trade.get("call_long") if strategy == "IC" else trade["long_strike"]
            if pin and _near_pin(sc["price"], pin, "above"):
                warnings.append(
                    f"+1σ MOVE AT MAX-LOSS PIN: ${sc['price']:,.2f} is within "
                    f"${buffer:.2f} of the long call strike (${pin:,.2f}). "
                    "A standard 1-sigma move essentially eliminates the "
                    "long-leg protection."
                )
    return warnings


def _collect_event_context(trade, dte_remaining, quote_data):
    """Return events_in_window list-of-dicts and first-event metadata."""
    import event_calendar

    today = datetime.now(ZoneInfo("America/Chicago")).date()
    try:
        exp = datetime.strptime(trade["expiration"], "%Y-%m-%d").date()
    except (ValueError, TypeError, KeyError):
        exp = today + timedelta(days=max(dte_remaining, 1))

    events = event_calendar.get_events_between(
        trade["symbol"], today, exp, quote_data=quote_data,
        include_earnings=True,
    )
    return {
        "events_in_window": [
            {**e.as_dict(), "days_until": (e.date - today).days}
            for e in events
        ],
    }


def _iv_reexpansion_warnings(current_vega, iv_rank, events_in_window):
    """Warn when short-vega position sits in compressed IV with event in window."""
    if iv_rank is None:
        return []
    if current_vega is None or current_vega >= -0.01:
        return []
    if iv_rank >= 25:
        return []
    risk_events = [e for e in events_in_window
                   if e["category"] in ("FOMC", "CPI", "PPI", "PCE", "GDP",
                                         "NFP", "FED_SPEAKER", "EARNINGS")]
    if not risk_events:
        return []
    first_event_label = f"{risk_events[0]['label']} ({risk_events[0]['date']})"
    return [
        f"IV re-expansion risk: short vega ({current_vega:+.3f}) in compressed "
        f"IV environment (rank {iv_rank:.0f}) with {first_event_label} inside "
        "expiration window. An IV spike can move the spread against you even "
        "if price barely moves."
    ]


def analyze_trade(client, trade, iv_data=None):
    """Run deep analysis on an open paper trade.

    Args:
        client: SchwabPyProxyClient instance (must be connected)
        trade: paper trade dict (from paper_trader.py)
        iv_data: optional dict from last_iv_data for the symbol

    Returns:
        analysis dict

    Raises:
        RuntimeError if live data cannot be fetched
    """
    from iv_analysis import calc_expected_move

    sym = trade["symbol"]
    strategy = trade["strategy"]
    qty = trade["quantity"]
    multiplier = 100

    # Fetch live data
    underlying_now, chain, quote_data = _fetch_live_data(client, trade)

    # Current spread value, greeks, and bid/ask quotes
    current_debit, current_greeks, current_quotes = _calc_current_spread_value(
        chain, trade)

    # Position metrics
    entry_credit = trade["entry_credit"]
    unrealized_pnl = (entry_credit - current_debit) * qty * multiplier
    max_profit = trade["entry_credit_total"]
    max_loss = trade["max_loss_total"]

    today = datetime.now(TZ).date()
    try:
        exp_date = datetime.strptime(trade["expiration"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        exp_date = today
    dte_remaining = max((exp_date - today).days, 0)
    dte_at_entry = trade.get("dte_at_entry", 0) or dte_remaining

    # Distance to short strike
    short_strike = trade["short_strike"]
    distance = underlying_now - short_strike
    if strategy == "CCS":
        distance = short_strike - underlying_now
    elif strategy == "IC":
        call_short = trade.get("call_short", 0)
        dist_put = underlying_now - short_strike
        dist_call = call_short - underlying_now
        distance = min(dist_put, dist_call)
    distance_pct = (distance / underlying_now * 100) if underlying_now else 0

    # ── Data quality validation ──
    data_warnings = []
    data_captured_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    # Suspect: debit ≈ entry credit on 0-DTE far OTM (likely stale data)
    if (dte_remaining == 0 and distance_pct >= 3.0
            and entry_credit > 0
            and abs(current_debit - entry_credit) / entry_credit < 0.05):
        data_warnings.append(
            "STALE DATA SUSPECT: Current debit ≈ entry credit on 0-DTE "
            f"with short strike {distance_pct:.1f}% OTM. Debit should be "
            "near $0. Verify with live broker quotes."
        )

    # Suspect theta on short credit spread at expiration.
    # Position-perspective net theta should collapse toward 0 at expiry; any
    # sizeable value (either sign) suggests the chain's Greeks haven't updated.
    net_theta = current_greeks.get("theta", 0)
    if dte_remaining == 0 and abs(net_theta) > 0.5:
        data_warnings.append(
            f"SUSPECT THETA: Net theta is {net_theta:+.3f} on expiration day. "
            "For a short credit spread expiring today, position theta should "
            "be near zero. Greeks data may be stale or from prior session."
        )

    # Profit capture sanity: if far OTM on 0-DTE but capture shows ~0%
    if (dte_remaining == 0 and distance_pct >= 5.0
            and max_profit > 0
            and abs(unrealized_pnl / max_profit) < 0.05):
        data_warnings.append(
            f"PROFIT ESTIMATE SUSPECT: Position is {distance_pct:.1f}% OTM "
            "on expiration day but profit capture shows ~0%. Likely caused "
            "by stale debit-to-close data. True profit is likely ~99%+."
        )

    # Adaptive thresholds and verdict
    target_pct, loss_pct, dte_frac = _calc_adaptive_thresholds(
        dte_at_entry, dte_remaining)
    verdict = _calc_verdict(unrealized_pnl, max_profit, max_loss,
                            target_pct, loss_pct, dte_remaining,
                            underlying_now, trade)

    # Profit target
    target_debit = entry_credit * (1.0 - target_pct / 100.0)
    be = _compute_breakeven(trade)
    profit_capture_pct = (unrealized_pnl / max_loss * 100) if max_loss > 0 else 0

    # Entry greeks
    entry_greeks = {
        "delta": trade.get("entry_delta"),
        "theta": trade.get("entry_theta"),
        "vega": trade.get("entry_vega"),
        "gamma": trade.get("entry_gamma"),
    }
    theta_accelerating = dte_remaining <= 21
    gamma_risk = None
    if distance_pct < 2.0:
        gamma_risk = f"Short strike only {distance_pct:.1f}% away \u2014 high gamma risk"
    elif distance_pct < 5.0:
        gamma_risk = f"Short strike {distance_pct:.1f}% away \u2014 elevated gamma risk"

    # Market conditions
    underlying_entry = trade.get("underlying_at_entry", 0)
    price_change = underlying_now - underlying_entry
    price_change_pct = (price_change / underlying_entry * 100) if underlying_entry else 0

    iv_rank_now = None
    iv_expanding = None
    trend = None
    if iv_data:
        iv_rank_now = iv_data.get("iv_rank")

    # Extract ATM IV from chain
    atm_iv = None
    for map_key in ("callExpDateMap", "putExpDateMap"):
        dm = chain.get(map_key, {})
        best_diff = float("inf")
        for ek, sks in dm.items():
            for sk_str, conts in sks.items():
                try:
                    sk = float(sk_str)
                except (ValueError, TypeError):
                    continue
                diff = abs(sk - underlying_now)
                if diff < best_diff and isinstance(conts, list) and conts:
                    vol = conts[0].get("volatility")
                    if vol is not None:
                        best_diff = diff
                        atm_iv = vol if vol < 5.0 else vol / 100.0
                        atm_iv *= 100.0

    em_daily = None
    em_monthly = None
    em_exp_dollars = None
    if atm_iv and underlying_now:
        em_d = calc_expected_move(underlying_now, atm_iv, 1)
        em_m = calc_expected_move(underlying_now, atm_iv, 30)
        em_exp = calc_expected_move(underlying_now, atm_iv, max(dte_remaining, 1))
        if em_d:
            em_daily = em_d["move_dollars"]
        if em_m:
            em_monthly = em_m["move_dollars"]
        if em_exp:
            em_exp_dollars = em_exp["move_dollars"]
    strike_vs_em = _classify_strike_vs_em(distance, em_exp_dollars)

    # Scenario analysis (expiration intrinsic payoff, not grid-based).
    # pnl_pct is expressed as a percentage of max_loss (capital at risk) —
    # not max_profit — so a full-profit scenario shows credit/risk, not 100%.
    scenarios = []
    scenario_warnings = []
    if em_exp_dollars or em_daily:
        em_move = em_exp_dollars if em_exp_dollars else em_daily
        for label, offset in [("Price stays flat", 0),
                               ("Price +1\u03c3", em_move),
                               ("Price \u22121\u03c3", -em_move)]:
            scenario_price = underlying_now + offset
            pnl_val = _expiration_pnl(trade, scenario_price)
            pnl_pct_val = (pnl_val / max_loss * 100) if max_loss > 0 else 0
            scenarios.append({
                "label": label,
                "price": round(scenario_price, 2),
                "pnl": pnl_val,
                "pnl_pct": round(pnl_pct_val, 1),
            })
        scenario_warnings = _check_scenario_strike_proximity(
            trade, scenarios)

    # Event calendar integration
    event_ctx = _collect_event_context(trade, dte_remaining, quote_data)
    events_in_window = event_ctx["events_in_window"]

    iv_reexpansion_warnings = _iv_reexpansion_warnings(
        current_greeks.get("vega"), iv_rank_now, events_in_window)

    # Verdict note: flag-only, no decision change.
    if events_in_window and dte_remaining <= 14:
        labels = ", ".join(e["label"] for e in events_in_window[:3])
        verdict["reason"] = verdict.get("reason", "") + (
            f" EVENT RISK: {labels} inside expiration window.")

    # Build strikes display string
    strikes_str = f"{trade['short_strike']}/{trade['long_strike']}"
    if strategy == "IC":
        strikes_str += f"\u2014{trade.get('call_short','')}/{trade.get('call_long','')}"

    return {
        "symbol": sym,
        "strategy": strategy,
        "strikes": strikes_str,
        "expiration": trade["expiration"],

        "verdict": verdict,

        "position": {
            "entry_credit": trade["entry_credit_total"],
            "current_debit": round(current_debit * qty * multiplier, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(verdict["profit_capture_pct"], 1),
            "max_profit": max_profit,
            "max_loss": max_loss,
            "dte_at_entry": dte_at_entry,
            "dte_remaining": dte_remaining,
            "dte_frac": round(dte_frac, 3),
            "underlying_at_entry": underlying_entry,
            "underlying_now": underlying_now,
            "distance_to_short": round(distance, 2),
            "distance_to_short_pct": round(distance_pct, 1),
            # Per-share bid/ask of the spread at the time of analysis.
            # bid = least debit to close, ask = most debit to close.
            "bid": current_quotes["bid"],
            "ask": current_quotes["ask"],
            "mark": current_quotes["mark"],
        },

        "profit_target": {
            "target_pct": target_pct,
            "target_debit": round(target_debit * qty * multiplier, 2),
            "current_debit": round(current_debit * qty * multiplier, 2),
            "breakeven": be,
            "profit_capture_pct": round(profit_capture_pct, 1),
        },

        "greeks": {
            "entry": entry_greeks,
            "current": current_greeks,
            "theta_accelerating": theta_accelerating,
            "gamma_risk": gamma_risk,
        },

        "market": {
            "price_change": round(price_change, 2),
            "price_change_pct": round(price_change_pct, 2),
            "iv_rank_now": iv_rank_now,
            "iv_expanding": iv_expanding,
            "em_1sd_daily": em_daily,
            "em_1sd_monthly": em_monthly,
            "strike_vs_em": strike_vs_em,
            "strike_vs_em_horizon_days": dte_remaining,
            "trend": trend,
        },

        "scenarios": scenarios,
        "scenario_warnings": scenario_warnings,
        "events_in_window": events_in_window,
        "iv_reexpansion_warnings": iv_reexpansion_warnings,
        "quote_data": quote_data,
        "data_captured_at": data_captured_at,
        "data_warnings": data_warnings,
    }
