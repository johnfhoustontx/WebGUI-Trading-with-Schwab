"""
ai_prompt_builder.py - Generate AI Analysis Prompt
Version: 1.0.0
Last Updated: 2026-04-12

Builds a structured prompt from trade analysis data for pasting
into Claude.ai or any LLM. No API keys required.
"""

from datetime import datetime


def build_ai_prompt(analysis, quote_data=None):
    """Build a structured AI analysis prompt from trade analysis data.

    Args:
        analysis: dict returned by trade_analyzer.analyze_trade()
        quote_data: optional raw Schwab quote dict for enrichment

    Returns:
        str: formatted prompt ready to paste into an AI chat
    """
    qd = quote_data or analysis.get("quote_data", {}) or {}
    pos = analysis.get("position", {})
    pt = analysis.get("profit_target", {})
    g = analysis.get("greeks", {})
    mkt = analysis.get("market", {})
    v = analysis.get("verdict", {})
    scenarios = analysis.get("scenarios", [])
    entry_g = g.get("entry", {})
    current_g = g.get("current", {})

    sym = analysis.get("symbol", "???")
    strategy = analysis.get("strategy", "???")
    strikes = analysis.get("strikes", "???")
    expiration = analysis.get("expiration", "???")
    today_str = datetime.now().strftime("%Y-%m-%d (%A)")

    lines = []
    lines.append(
        "You are an experienced options trader and portfolio risk manager. "
        "Analyze the following trade and provide a detailed assessment."
    )
    lines.append(f"Today's date is {today_str}.")
    ts = analysis.get("data_captured_at", "")
    if ts:
        lines.append(f"Data captured at: {ts}")
    lines.append("")

    # ── DATA QUALITY WARNINGS ──
    warnings = analysis.get("data_warnings", [])
    if warnings:
        lines.append("!" * 50)
        lines.append("DATA QUALITY WARNINGS (auto-detected)")
        lines.append("!" * 50)
        for w in warnings:
            lines.append(f"  * {w}")
        lines.append("")
        lines.append(
            "NOTE: The data above may contain stale or suspect values. "
            "Factor these warnings into your analysis and flag any "
            "numbers that look inconsistent."
        )
        lines.append("")

    # ── TRADE SETUP ──
    lines.append("=" * 50)
    lines.append("TRADE SETUP")
    lines.append("=" * 50)
    lines.append(f"Symbol: {sym} | Strategy: {strategy} | Strikes: {strikes}")
    lines.append(f"Expiration: {expiration} | DTE Remaining: {pos.get('dte_remaining', '?')}")
    lines.append(f"Entry Credit: ${pos.get('entry_credit', 0):,.2f} | "
                 f"Max Loss: ${pos.get('max_loss', 0):,.2f}")
    lines.append("")

    # ── CURRENT POSITION ──
    lines.append("=" * 50)
    lines.append("CURRENT POSITION")
    lines.append("=" * 50)
    entry_px = pos.get("underlying_at_entry", 0)
    now_px = pos.get("underlying_now", 0)
    px_chg = mkt.get("price_change", 0)
    px_chg_pct = mkt.get("price_change_pct", 0)
    lines.append(f"Underlying: ${now_px:,.2f} (was ${entry_px:,.2f} at entry, "
                 f"{px_chg_pct:+.2f}%)")
    lines.append(f"Current Debit to Close: ${pos.get('current_debit', 0):,.2f}")
    upnl = pos.get("unrealized_pnl", 0)
    upnl_pct = pos.get("unrealized_pnl_pct", 0)
    lines.append(f"Unrealized P&L: ${upnl:+,.2f} ({upnl_pct:.1f}% of max profit captured)")
    dist = pos.get("distance_to_short", 0)
    dist_pct = pos.get("distance_to_short_pct", 0)
    lines.append(f"Distance to Short Strike: ${dist:+,.2f} ({dist_pct:.1f}%)")
    dte_entry = pos.get("dte_at_entry", "?")
    dte_rem = pos.get("dte_remaining", "?")
    dte_frac = pos.get("dte_frac", 0)
    lines.append(f"DTE at Entry: {dte_entry} | DTE Remaining: {dte_rem} "
                 f"({(1 - dte_frac):.0%} elapsed)")
    lines.append("")

    # ── PROFIT TARGET ──
    lines.append("=" * 50)
    lines.append("PROFIT TARGET (Adaptive)")
    lines.append("=" * 50)
    lines.append(f"Target: {pt.get('target_pct', '?')}% of max profit")
    lines.append(f"Target Debit: ${pt.get('target_debit', 0):,.2f} | "
                 f"Current Debit: ${pt.get('current_debit', 0):,.2f}")
    be = pt.get("breakeven", "?")
    be_str = f"${be:,.2f}" if isinstance(be, (int, float)) else str(be)
    lines.append(f"Breakeven: {be_str}")
    lines.append(f"Profit Captured vs Max Loss: {pt.get('profit_capture_pct', 0):.1f}%")
    lines.append("")

    # ── GREEKS ──
    lines.append("=" * 50)
    lines.append("GREEKS")
    lines.append("=" * 50)
    lines.append(f"{'':10s} {'Entry':>10s} {'Current':>10s} {'Change':>10s}")
    for label, key, fmt in [("Delta", "delta", ".4f"),
                            ("Theta", "theta", ".3f"),
                            ("Vega", "vega", ".3f"),
                            ("Gamma", "gamma", ".4f")]:
        e = entry_g.get(key)
        c = current_g.get(key, 0)
        c_str = f"{c:>+10{fmt}}"
        if e is None:
            dash = "\u2014"
            e_str = f"{dash:>10s}"
            ch_str = f"{dash:>10s}"
        else:
            e_str = f"{e:>+10{fmt}}"
            ch_str = f"{(c - e):>+10{fmt}}"
        lines.append(f"{label:10s} {e_str} {c_str} {ch_str}")
    if g.get("theta_accelerating"):
        lines.append("* Theta acceleration zone (DTE < 21)")
    gr = g.get("gamma_risk")
    if gr:
        lines.append(f"* WARNING: {gr}")
    lines.append("")

    # ── MARKET CONDITIONS ──
    lines.append("=" * 50)
    lines.append("MARKET CONDITIONS")
    lines.append("=" * 50)
    lines.append(f"Price Change: ${px_chg:+,.2f} ({px_chg_pct:+.2f}%)")
    iv_rank = mkt.get("iv_rank_now")
    if iv_rank is not None:
        # "Vol Rank" = current IV ranked within the 52w HV-30 distribution.
        # Not a pure IV-vs-IV rank (no persisted IV time series yet).
        lines.append(f"Vol Rank (IV vs 52w HV-30): {iv_rank:.0f}")
    em_d = mkt.get("em_1sd_daily")
    em_m = mkt.get("em_1sd_monthly")
    if em_d:
        lines.append(f"Daily Expected Move (1 sigma): +/-${em_d:,.2f}")
    if em_m:
        lines.append(f"Monthly Expected Move (1 sigma): +/-${em_m:,.2f}")
    horizon = mkt.get("strike_vs_em_horizon_days")
    label = "Strike vs Expected Move"
    if horizon is not None:
        label = f"Strike vs {horizon}-day Expected Move"
    lines.append(f"{label}: {mkt.get('strike_vs_em', 'N/A')}")
    trend = mkt.get("trend")
    if trend:
        lines.append(f"Trend: {trend}")
    lines.append("")

    # ── SCENARIO PROJECTIONS ──
    lines.append("=" * 50)
    lines.append("SCENARIO PROJECTIONS (at expiration)")
    lines.append("=" * 50)
    if scenarios:
        lines.append(f"{'Scenario':25s} {'Price':>10s} {'P&L':>10s} "
                     f"{'% Risk':>8s}")
        for sc in scenarios:
            lines.append(
                f"{sc.get('label', '?'):25s} "
                f"${sc.get('price', 0):>9,.2f} "
                f"${sc.get('pnl', 0):>+9,.0f} "
                f"{sc.get('pnl_pct', 0):>+7.1f}%"
            )
        lines.append("(% Risk = P&L as percentage of max loss)")
    else:
        lines.append("(no scenario data available)")
    for w in analysis.get("scenario_warnings", []):
        lines.append(f"* WARNING: {w}")
    for w in analysis.get("iv_reexpansion_warnings", []):
        lines.append(f"* WARNING: {w}")
    lines.append("")

    # ── EVENTS IN EXPIRATION WINDOW ──
    lines.append("=" * 50)
    lines.append("EVENTS IN EXPIRATION WINDOW")
    lines.append("=" * 50)
    events = analysis.get("events_in_window", [])
    if events:
        for ev in events:
            lines.append(
                f"{ev['date']} (+{ev['days_until']}d): "
                f"{ev['category']} — {ev['label']}"
            )
    else:
        lines.append("(no known events in window)")
    lines.append("")

    # ── UPCOMING EVENTS & KNOWN DATA ──
    lines.append("=" * 50)
    lines.append("UPCOMING EVENTS & KNOWN DATA")
    lines.append("=" * 50)
    hi52 = qd.get("52WkHigh") or qd.get("highPrice52")
    lo52 = qd.get("52WkLow") or qd.get("lowPrice52")
    try:
        if hi52 and lo52:
            lines.append(f"52-Week Range: ${float(lo52):,.2f} - ${float(hi52):,.2f}")
    except (ValueError, TypeError):
        pass
    vol = qd.get("totalVolume")
    try:
        if vol:
            lines.append(f"Today's Volume: {int(vol):,}")
    except (ValueError, TypeError):
        pass
    pe = qd.get("peRatio")
    try:
        if pe:
            lines.append(f"P/E Ratio: {float(pe):.1f}")
    except (ValueError, TypeError):
        pass
    div_date = qd.get("divDate")
    div_amt = qd.get("divAmount") or qd.get("divYield")
    if div_date:
        div_line = f"Next Dividend Date: {div_date}"
        try:
            if div_amt:
                div_line += f" (${float(div_amt):.2f})"
        except (ValueError, TypeError):
            pass
        lines.append(div_line)
    lines.append(
        f"NOTE: This trade expires {expiration} "
        f"({dte_rem} calendar days from today)."
    )
    lines.append("")

    # ── SYSTEM VERDICT ──
    lines.append("=" * 50)
    lines.append("SYSTEM VERDICT (computed by our rule-based engine)")
    lines.append("=" * 50)
    lines.append(f"Action: {v.get('action', '?')} | "
                 f"Profit Capture: {v.get('profit_capture_pct', 0):.1f}% | "
                 f"Target: {pt.get('target_pct', '?')}%")
    lines.append(f"Rationale: {v.get('rationale', 'N/A')}")
    lines.append("")

    # ── ANALYSIS REQUEST ──
    lines.append("=" * 50)
    lines.append("YOUR ANALYSIS")
    lines.append("=" * 50)
    lines.append(
        "Respond in plain English. Explain any technical term you use "
        "the first time, avoid jargon shorthand, and write as if "
        "explaining to a smart retail trader who is not a quant."
    )
    lines.append("")
    lines.append("Please provide:")
    lines.append("")
    lines.append(
        f"1. HEADLINE RISK: What are today's major market headlines, macro "
        f"events, or upcoming catalysts (FOMC, CPI, earnings, ex-dividend, "
        f"etc.) that could impact {sym}? How do they affect this specific "
        f"trade over the next {dte_rem} days?"
    )
    lines.append("")
    lines.append(
        "2. TRADE HEALTH: Is the system verdict above reasonable? What "
        "nuances or risks might a rule-based engine be missing? Consider "
        "the Greeks trajectory and IV environment."
    )
    lines.append("")
    lines.append(
        "3. WHY IS THIS HAPPENING. Two parts:"
        "\n   (a) MARKET CONTEXT — why is the current price action, IV "
        "regime, and dealer positioning showing what they're showing right "
        "now? What macro flows, gamma posture, or catalysts explain it?"
        "\n   (b) POSITION CONTEXT — how did this trade get to its current "
        "state? Walk through the journey from entry to now: what moved in "
        "our favor, what moved against us, and what's the story arc?"
    )
    lines.append("")
    lines.append(
        "4. WHAT IF: Walk through 2-3 plausible scenarios for the rest of "
        "the holding window. You pick the scenarios (e.g. underlying up/"
        "down/sideways, IV crush, gap, catalyst surprise). For each, "
        "describe what the underlying / IV / Greeks do, how this position "
        "reacts in dollars, and what action (if any) I should take."
    )
    lines.append("")
    lines.append(
        "5. EXIT STRATEGY: Give me specific price levels or conditions "
        "under which I should close this trade. Should I adjust my profit "
        "target given current conditions?"
    )
    lines.append("")
    lines.append(
        "6. RISK FACTORS: What could go wrong from here? Provide a "
        "probability-weighted assessment of the key risks."
    )
    lines.append("")
    lines.append(
        "7. ROLL/ADJUST: If the trade should be adjusted or rolled, "
        "suggest specifics (new strikes, new expiration, credit/debit "
        "expected). If no adjustment needed, say so."
    )
    lines.append("")
    lines.append(
        "Be direct and specific. Reference the numbers above. If the "
        "trade should be closed immediately, say so clearly and explain why."
    )

    return "\n".join(lines)
