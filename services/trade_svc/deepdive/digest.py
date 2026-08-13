"""Pure quant-digest formatter (extracted from EquityDeepDive's ai_analyst.py).

Formats an ``equity_deep_dive`` result dict into a dense text block for the chat
prompt. No API calls — this is the *formatter* only; the Anthropic client was left
behind by design (the 3-tier button generates a query, it does not call the API).
"""
import datetime as dt


def _fmt(value, spec='.2f', dash='n/a'):
    if value is None:
        return dash
    try:
        return f'{float(value):{spec}}'
    except (TypeError, ValueError):
        return str(value)


def build_quant_digest(data):
    """Compact the analysis JSON into a dense text block for the model

    Deliberately excludes raw chain data - the model needs the derived
    statistics, not 200 contract rows it will pattern-match noise from.
    """
    symbol = data.get('symbol', '?').lstrip('$')
    tech = data.get('technicals', {}) or {}
    fund = data.get('fundamentals', {}) or {}
    opts = data.get('options', {}) or {}
    ranks = data.get('ranks', {}) or {}
    iv_r = ranks.get('iv') or {}
    rv_r = ranks.get('rv') or {}

    lines = [f'TICKER: {symbol}', f'AS OF: {dt.date.today().isoformat()}', '']

    lines.append('--- PRICE & TREND ---')
    lines.append(f'Last: {_fmt(tech.get("last_close"))}')
    for label, key in (('20 SMA', 'sma_20'), ('50 SMA', 'sma_50'), ('200 SMA', 'sma_200')):
        lines.append(f'{label}: {_fmt(tech.get(key))} '
                     f'(price is {_fmt(tech.get(f"dist_{key}"), "+.2f")}% vs it)')
    lines.append(f'MA cross state: {tech.get("ma_cross", "n/a")}')
    lines.append(f'RSI(14): {_fmt(tech.get("rsi_14"), ".1f")}')
    lines.append(f'MACD: {_fmt(tech.get("macd"), "+.3f")} vs signal '
                 f'{_fmt(tech.get("macd_signal"), "+.3f")} [{tech.get("macd_state", "n/a")}]')
    lines.append(f'Bollinger %B: {_fmt(tech.get("bb_pct_b"), ".3f")}'
                 f'{"  [SQUEEZE]" if tech.get("bb_squeeze") else ""}')
    lines.append('')

    lines.append('--- RANGE & VOLATILITY ---')
    lines.append(f'52w range: {_fmt(tech.get("low_52w"))} - {_fmt(tech.get("high_52w"))}')
    lines.append(f'Position in 52w range: {_fmt(tech.get("range_position"), ".1f")}%')
    lines.append(f'Off 52w high: {_fmt(tech.get("pct_off_high"), "+.1f")}%')
    lines.append(f'ATR(14): {_fmt(tech.get("atr_14"))} '
                 f'({_fmt(tech.get("atr_pct"), ".2f")}% of price)  '
                 f'<-- USE THIS FOR SIZING')
    lines.append(f'Realized vol 20d: {_fmt(tech.get("rvol_20d"), ".1f")}%   '
                 f'60d: {_fmt(tech.get("rvol_60d"), ".1f")}%')
    lines.append(f'Relative volume: {_fmt(tech.get("relative_volume"), ".2f")}x')
    lines.append('')

    lines.append('--- RETURNS ---')
    lines.append('  '.join(
        f'{lbl}: {_fmt(tech.get(f"return_{lbl}"), "+.1f")}%'
        for lbl in ('1w', '1m', '3m', '6m', '1y')
        if tech.get(f'return_{lbl}') is not None
    ) or 'n/a')
    lines.append('')

    lines.append('--- STRUCTURE ---')
    if tech.get('support_levels'):
        lines.append(f'Support pivots: {", ".join(_fmt(v) for v in tech["support_levels"])}')
    if tech.get('resistance_levels'):
        lines.append(f'Resistance pivots: {", ".join(_fmt(v) for v in tech["resistance_levels"])}')
    if tech.get('poc'):
        va = tech.get('value_area') or (None, None)
        lines.append(f'Volume POC: {_fmt(tech["poc"])}   '
                     f'Value area: {_fmt(va[0])} - {_fmt(va[1])}')
    lines.append('')

    lines.append('--- FUNDAMENTALS (broker feed) ---')
    for label, key, spec in (
        ('Market cap', 'market_cap', ',.0f'),
        ('Shares outstanding', 'shares_outstanding', ',.0f'),
        ('EPS TTM', 'eps_ttm', '.2f'),
        ('P/E', 'pe_ratio', '.2f'),
        ('P/B', 'pb_ratio', '.2f'),
        ('Book value/share', 'book_value_per_share', '.2f'),
        ('ROE %', 'roe', '.2f'),
        ('Current ratio', 'current_ratio', '.2f'),
        ('Debt/Equity', 'total_debt_to_equity', '.2f'),
        ('Beta', 'beta', '.2f'),
        ('Short % of float', 'short_int_to_float', '.2f'),
        ('Days to cover', 'short_int_day_to_cover', '.2f'),
    ):
        lines.append(f'{label}: {_fmt(fund.get(key), spec)}')
    if fund.get('short_grade'):
        lines.append(f'Short interest grade: {fund["short_grade"]}')
    lines.append('')

    lines.append('--- OPTIONS ---')
    if not opts.get('available'):
        lines.append('No option chain available for this symbol.')
    else:
        lines.append(f'Constant-maturity 30d ATM IV: {_fmt(opts.get("cm30_iv"), ".1f")}%')
        if iv_r.get('sufficient'):
            lines.append(f'IV RANK: {_fmt(iv_r.get("rank"), ".0f")} '
                         f'(percentile {_fmt(iv_r.get("percentile"), ".0f")}) '
                         f'over {iv_r.get("samples")} observations, '
                         f'range {_fmt(iv_r.get("low"), ".1f")}-{_fmt(iv_r.get("high"), ".1f")}')
        else:
            lines.append(f'IV RANK: NOT YET AVAILABLE - only {iv_r.get("samples", 0)} '
                         f'snapshots stored. Do NOT infer an IV rank. Say it is unavailable.')
        if rv_r.get('sufficient'):
            lines.append(f'RV RANK (20d): {_fmt(rv_r.get("rank"), ".0f")} '
                         f'(percentile {_fmt(rv_r.get("percentile"), ".0f")}) '
                         f'over {rv_r.get("samples")} sessions')
        lines.append(f'ATM IV / 20d realized vol: {_fmt(opts.get("iv_rv_ratio_20d"), ".2f")}x '
                     f'(variance risk premium {_fmt(opts.get("vrp_20d"), "+.1f")} vol pts)')
        lines.append(f'Term structure: {opts.get("term_state", "n/a")} '
                     f'({_fmt(opts.get("term_slope"), "+.1f")} vol pts front to back)')
        lines.append(f'Put/Call OI ratio: {_fmt(opts.get("put_call_oi_ratio"), ".3f")}')
        if opts.get('net_gex') is not None:
            lines.append(f'Front-expiry net GEX: {_fmt(opts.get("net_gex"), ",.0f")}   '
                         f'Gamma flip: {_fmt(opts.get("gamma_flip"))}')
        if opts.get('call_walls'):
            lines.append('Call OI walls: ' + ', '.join(
                f'{k:.2f}({v:,.0f})' for k, v in opts['call_walls']))
        if opts.get('put_walls'):
            lines.append('Put OI walls: ' + ', '.join(
                f'{k:.2f}({v:,.0f})' for k, v in opts['put_walls']))

        lines.append('')
        lines.append('Per-expiration:')
        lines.append(f'{"Expiry":<12}{"DTE":>5}{"ATM IV":>9}{"ImpMove":>9}'
                     f'{"Straddle":>10}{"P/C OI":>8}{"MaxPain":>9}{"RR25":>7}')
        for e in (opts.get('expirations') or [])[:8]:
            lines.append(
                f'{e.get("expiration", ""):<12}{_fmt(e.get("dte"), ".0f"):>5}'
                f'{_fmt(e.get("atm_iv"), ".1f"):>9}'
                f'{_fmt(e.get("implied_move_pct"), ".1f"):>9}'
                f'{_fmt(e.get("straddle")):>10}'
                f'{_fmt(e.get("put_call_oi"), ".2f"):>8}'
                f'{_fmt(e.get("max_pain")):>9}'
                f'{_fmt(e.get("risk_reversal_25d"), "+.1f"):>7}'
            )
    lines.append('')

    if data.get('takeaways'):
        lines.append('--- MECHANICAL TAKEAWAYS (rule-based, not model-generated) ---')
        for note in data['takeaways']:
            lines.append(f'- {note}')

    return '\n'.join(lines)
