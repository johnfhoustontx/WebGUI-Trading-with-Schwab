"""Per-position performance scorecard (pure logic).

Split-speed design (see docs/plans/2026-06-10-position-performance-evaluation-design.md):

- ``compute_baseline`` — SLOW: consumes daily history + the trade-store entry
  once at load (callers run it on a worker thread). Produces a plain dict of
  history-derived stats that do not change intraday.
- ``evaluate_portfolio`` — FAST: pure arithmetic over the live model + cached
  baselines, cheap enough to run on every streamed tick.

All functions tolerate ``None`` / missing inputs by returning ``None`` for the
affected stat — a partially-populated scorecard must never raise.
"""
from __future__ import annotations

import math

from src.live import MULTIPLIER

TRADING_DAYS = 252


def slice_since(df, entry_date: str):
    """Rows of ``df`` with ``datetime`` on/after ``entry_date``; None if empty/None."""
    if df is None or entry_date is None:
        return None
    import pandas as pd
    try:
        cutoff = pd.Timestamp(entry_date)
    except ValueError:
        return None
    out = df[df["datetime"] >= cutoff]
    return out if len(out) else None


def window_return(df, entry_date: str):
    """First-close to last-close return over the holding window, or None."""
    window = slice_since(df, entry_date)
    if window is None:
        return None
    first, last = float(window["close"].iloc[0]), float(window["close"].iloc[-1])
    if first == 0:
        return None
    return last / first - 1.0


def annualized_volatility(df):
    """Sample std of daily close-to-close returns * sqrt(252); None if <3 closes."""
    if df is None or len(df) < 3:
        return None
    rets = df["close"].pct_change().dropna()
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=1)) * math.sqrt(TRADING_DAYS)


def latest_atr(df, period: int = 14):
    """Simple mean of the last ``period`` true ranges; None if not enough rows."""
    if df is None or len(df) < period + 1:
        return None
    high, low = df["high"], df["low"]
    prev_close = df["close"].shift(1)
    tr = (high - low).combine((high - prev_close).abs(), max).combine(
        (low - prev_close).abs(), max
    )
    return float(tr.iloc[-period:].mean())


# Trailing sessions of PRE-entry history the execution grade judges an entry
# against — "was this a good price, given the range on offer at the time".
EXECUTION_LOOKBACK = 60


def slice_before(df, entry_date: str, lookback: int = EXECUTION_LOOKBACK):
    """The last ``lookback`` rows STRICTLY BEFORE ``entry_date``; None if there
    are fewer than that many (an entry with no prior range cannot be graded).

    ⚠ The counterpart to :func:`slice_since`, and the two must not be confused:
    grading an entry against the closes that came AFTER it makes the grade a
    function of the subsequent return, not of the entry (see entry_percentile).
    """
    if df is None or entry_date is None or not len(df):
        return None
    import pandas as pd
    try:
        cutoff = pd.Timestamp(entry_date)
    except ValueError:
        return None
    out = df[df["datetime"] < cutoff]
    if len(out) < lookback:
        return None
    return out.iloc[-lookback:]


def entry_percentile(entry_price, df):
    """Where ``entry_price`` sits in the [min, max] of closes (0=low, 1=high).

    Clamped to [0, 1]; None when inputs are missing or the range is degenerate.

    ⚠ ``df`` must be the PRE-entry window (:func:`slice_before`). Passed the
    post-entry window it measures the subsequent move rather than the entry: a
    position that rose makes its own entry the minimum (0.0 -> grade A) and one
    that fell makes it the maximum (F), which turned the 15%-weighted execution
    dimension into a re-weighted copy of the 35%-weighted return dimension
    (fixed 2026-08-20).
    """
    if entry_price is None or df is None or not len(df):
        return None
    lo, hi = float(df["close"].min()), float(df["close"].max())
    if hi == lo:
        return None
    return min(1.0, max(0.0, (float(entry_price) - lo) / (hi - lo)))


def compute_baseline(holding: dict, stock_df, sector_df, spy_df, entry) -> dict:
    """Assemble the slow, history-derived stats for one holding.

    Args:
        holding: a ``build_portfolio`` holding row.
        stock_df / sector_df / spy_df: daily-history DataFrames (or None) for
            the symbol, its sector ETF, and SPY.
        entry: ``weighted_avg_entry`` record for the symbol, or None.

    Every stat degrades to None independently; callers re-weight around gaps.
    """
    from datetime import date

    entry_date = entry.get("entry_date") if entry else None
    entry_price = (entry or {}).get("avg_price") or holding.get("avg_price")

    window = slice_since(stock_df, entry_date) if entry_date else None
    days_held = None
    trading_days_held = None
    if entry_date is not None:
        try:
            today = date.today()
            entry = date.fromisoformat(entry_date)
            days_held = max(1, (today - entry).days)
            # Business-day (trading-day) count over the holding window so the
            # annualized return is on the SAME 252-day basis as annualized
            # volatility (which uses sqrt(252)). Mixing calendar 365 with
            # trading 252 mis-scales the Sharpe-like ratio by ~365/252 ≈ 1.45x.
            import numpy as np
            trading_days_held = max(1, int(np.busday_count(entry, today)))
        except ValueError:
            days_held = None
            trading_days_held = None

    return {
        "symbol": holding.get("symbol"),
        "entry_date": entry_date,
        "entry_price": entry_price,
        "days_held": days_held,
        "trading_days_held": trading_days_held,
        "ann_vol": annualized_volatility(window),
        "atr": latest_atr(stock_df),
        "peak_close": float(window["close"].max()) if window is not None else None,
        "sector_ret": window_return(sector_df, entry_date) if entry_date else None,
        "spy_ret": window_return(spy_df, entry_date) if entry_date else None,
        # Judged against the range available AT ENTRY, never the window since.
        "entry_pct": entry_percentile(entry_price,
                                      slice_before(stock_df, entry_date)),
    }


# Composite weights per dimension (design: return 35 / capital 25 / risk 25 /
# execution 15). Missing dimensions are dropped and the rest re-weighted.
DIMENSION_WEIGHTS = {"return": 0.35, "capital": 0.25, "risk": 0.25, "execution": 0.15}

# Numeric grade scale: 4=A .. 0=F. grade_letter() maps by rounding bands.
_LETTERS = ["F", "D", "C", "B", "A"]


def grade_letter(score):
    """Map a 0-4 numeric grade to F..A (>=0.5 rounds up); None passes through."""
    if score is None:
        return None
    return _LETTERS[min(4, max(0, int(score + 0.5)))]


def _grade_return(total_return, vs_sector, vs_spy):
    """0-4 from absolute return plus beating/lagging sector and SPY."""
    if total_return is None:
        return None
    score = 2.0
    score += 1.0 if total_return >= 0 else -1.0
    if vs_sector is not None:
        score += 0.5 if vs_sector >= 0 else -0.5
    if vs_spy is not None:
        score += 0.5 if vs_spy >= 0 else -0.5
    return min(4.0, max(0.0, score))


def _grade_risk(sharpe):
    if sharpe is None:
        return None
    if sharpe >= 1.5:
        return 4.0
    if sharpe >= 1.0:
        return 3.0
    if sharpe >= 0.5:
        return 2.0
    if sharpe >= 0.0:
        return 1.0
    return 0.0


def _grade_execution(entry_pct):
    """Entry near the low of its PRE-ENTRY range = good. 0.25 -> bought the
    bottom quartile of the 60 sessions leading up to the fill."""
    if entry_pct is None:
        return None
    return min(4.0, max(0.0, 4.0 * (1.0 - entry_pct)))


def _composite(grades: dict):
    avail = {k: v for k, v in grades.items() if v is not None}
    if not avail:
        return None
    total_w = sum(DIMENSION_WEIGHTS[k] for k in avail)
    return sum(v * DIMENSION_WEIGHTS[k] for k, v in avail.items()) / total_w


def evaluate_portfolio(model: dict, baselines: dict) -> dict:
    """FAST path: score every holding from the live model + cached baselines.

    Returns ``{symbol: scorecard}``. Cheap arithmetic only — safe per tick.
    Capital-efficiency grades are peer percentiles, so this is portfolio-level
    by design (a single position's capital grade is meaningless alone).
    """
    holdings = model.get("holdings") or []
    portfolio_value = sum(h.get("market_value") or 0 for h in holdings)

    cards: dict[str, dict] = {}
    for h in holdings:
        symbol = h.get("symbol")
        b = baselines.get(symbol) or {}
        qty = h.get("quantity") or 0

        # ``last`` only exists after a tick; before that, derive from
        # market_value — which includes the contract multiplier for options,
        # so divide it back out to get the per-share quote.
        if h.get("last") is not None:
            last = h["last"]
        elif qty and h.get("market_value") is not None:
            last = h["market_value"] / (qty * MULTIPLIER(h.get("asset_type")))
        else:
            last = None

        # No baseline -> no trusted entry; emit a minimal card rather than
        # scoring against the broker average price alone.
        entry_price = (b.get("entry_price") or h.get("avg_price")) if b else None
        days = b.get("days_held")
        # Prefer a real business-day count; fall back to converting calendar
        # days at the standard 252/365 ratio when only calendar days are known.
        trading_days = b.get("trading_days_held")
        if trading_days is None and days:
            trading_days = max(1, round(days * TRADING_DAYS / 365))

        total_return = None
        if last is not None and entry_price:
            total_return = last / entry_price - 1.0

        # Annualize on the TRADING-day (252) basis so this ratio's numerator
        # shares a basis with annualized volatility (sqrt(252)); the Sharpe-like
        # ratio below (ann_return / ann_vol) is then scale-consistent.
        ann_return = None
        if total_return is not None and trading_days and (1 + total_return) > 0:
            ann_return = (1 + total_return) ** (TRADING_DAYS / trading_days) - 1

        vs_sector = (total_return - b["sector_ret"]
                     if total_return is not None and b.get("sector_ret") is not None
                     else None)
        vs_spy = (total_return - b["spy_ret"]
                  if total_return is not None and b.get("spy_ret") is not None
                  else None)

        # Sharpe-like ratio: annualized return / annualized vol, with no
        # risk-free leg subtracted (so not a true Sharpe ratio).
        sharpe = (ann_return / b["ann_vol"]
                  if ann_return is not None and b.get("ann_vol") else None)

        drawdown = None
        if last is not None and b.get("peak_close"):
            peak = max(float(b["peak_close"]), last)
            drawdown = 1 - last / peak if peak else None

        # Capital efficiency raw value: annualized return on the capital
        # deployed. Percentile vs peers assigned in the second pass below.
        capital_raw = ann_return

        weight = ((h.get("market_value") or 0) / portfolio_value
                  if portfolio_value else None)

        cards[symbol] = {
            "symbol": symbol,
            "last": last,
            "weight": weight,
            "total_return": total_return,
            "ann_return": ann_return,
            "vs_sector": vs_sector,
            "vs_spy": vs_spy,
            "sharpe": sharpe,
            "drawdown": drawdown,
            "capital_raw": capital_raw,
            "capital_pct": None,
            "atr": b.get("atr"),
            "entry_price": entry_price,
            "entry_pct": b.get("entry_pct"),
            "days_held": days,
            "sector_ret": b.get("sector_ret"),
            "spy_ret": b.get("spy_ret"),
            "quantity": qty,
        }

    # Second pass: peer percentile for capital efficiency (rank / (n-1)).
    ranked = sorted(
        (s for s, c in cards.items() if c["capital_raw"] is not None),
        key=lambda s: cards[s]["capital_raw"],
    )
    n = len(ranked)
    for i, s in enumerate(ranked):
        cards[s]["capital_pct"] = i / (n - 1) if n > 1 else 0.5

    # Third pass: grades + composite.
    for c in cards.values():
        grades = {
            "return": _grade_return(c["total_return"], c["vs_sector"], c["vs_spy"]),
            "capital": (4.0 * c["capital_pct"]
                        if c["capital_pct"] is not None else None),
            "risk": _grade_risk(c["sharpe"]),
            "execution": _grade_execution(c["entry_pct"]),
        }
        c["grades"] = grades
        c["composite"] = _composite(grades)

    return cards
