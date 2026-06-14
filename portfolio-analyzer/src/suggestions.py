"""Advisory-only suggestions rules engine (pure logic).

``suggest(scorecard, ctx, thresholds)`` turns one position's scorecard into a
list of suggestion dicts: ``{"action", "severity", "reason", "shares"?,
"level"?}``. Actions: TRIM, EXIT, HOLD, REVIEW, SET_STOP, SCALE_OUT,
SCALE_IN (SCALE_IN subsumes ADD). Rules are deterministic and every boundary
comes from the injected ``Thresholds`` so users can tune them (Settings
dialog persists the values).

Design requirements honored here:
- Concrete sizing: TRIM/SCALE_IN compute share counts from weight deltas at
  the live price.
- Opportunity cost: every lagging position states what the same capital
  earned in its sector ETF, SPY, and (when the caller supplies it) the user's
  top-ranked sector over the same window, with the dollar gap vs the sector.
- Capital efficiency: bottom-quartile capital efficiency is a trim/review
  signal (``Thresholds.bottom_quartile``).
- No order placement — prose only.

Caveat on very young positions (days_held 1-5): annualized return and Sharpe
are extrapolated from a handful of sessions and can look extreme, so a
freshly opened position may fire SCALE_OUT/REVIEW prose that reads hotter
than it is. The thresholds are user-tunable; no rule here divides by
days_held, so a None/zero holding period is always safe.

``ctx`` keys: portfolio_value (float), avg_weight (float), sector_etf (str),
top_sector (tuple[name, window_return] or None).
"""
from __future__ import annotations

from src.thresholds import Thresholds


def _pct(x) -> str:
    return f"{x * 100:+.1f}%"


def _shares_for_weight_delta(weight, target, portfolio_value, last):
    if None in (weight, target, portfolio_value, last) or last <= 0:
        return None, None
    dollars = (weight - target) * portfolio_value
    return max(0, round(dollars / last)), dollars


def _opportunity_gap(card):
    """Dollar gap vs the sector ETF over the holding window, or None."""
    cost_basis = (card.get("entry_price") or 0) * (card.get("quantity") or 0)
    sector_ret = card.get("sector_ret")
    ret = card.get("total_return")
    if not cost_basis or sector_ret is None or ret is None:
        return None
    return cost_basis * (sector_ret - ret)


def _spy_clause(card) -> str:
    """``", SPY +3.0%"`` for the benchmark comparison, or ``""`` if unknown."""
    spy_ret = card.get("spy_ret")
    return f", SPY {_pct(spy_ret)}" if spy_ret is not None else ""


def _top_sector_sentence(card, ctx) -> str:
    """`` Your top sector (Energy, +12.0%) did even better.`` or ``""``.

    Rendered only when the caller supplied ``ctx["top_sector"]`` and its
    window return actually beats the position's own sector — otherwise the
    sector clause already tells the whole story.
    """
    top = ctx.get("top_sector")
    sector_ret = card.get("sector_ret")
    if not top or sector_ret is None:
        return ""
    name, top_ret = top
    if top_ret is None or top_ret <= sector_ret:
        return ""
    return f" Your top sector ({name}, {_pct(top_ret)}) did even better."


def suggest(card: dict, ctx: dict, t: Thresholds) -> list[dict]:
    out: list[dict] = []
    sym = card.get("symbol")
    last = card.get("last")
    ret = card.get("total_return")
    vs_sector = card.get("vs_sector")
    weight = card.get("weight")
    pv = ctx.get("portfolio_value")

    # --- data-gap REVIEW: nothing else can be trusted without a return ------
    if ret is None:
        return [{"action": "REVIEW", "severity": "low",
                 "reason": f"{sym}: insufficient data to evaluate (missing "
                           "history or entry record) — review manually."}]

    # --- EXIT: deep loss AND lagging its sector -----------------------------
    # When EXIT fires, the opportunity-cost figure (REVIEW's dollar gap) is
    # folded into the EXIT reason and the separate REVIEW is suppressed.
    exit_fired = (ret <= -t.exit_loss_pct
                  and vs_sector is not None and vs_sector <= -t.sector_lag_pct)
    if exit_fired:
        gap = _opportunity_gap(card)
        etf = ctx.get("sector_etf") or "its sector ETF"
        gap_txt = ""
        if gap:
            # _opportunity_gap guarantees sector_ret is not None here.
            gap_txt = (f" The same capital in {etf} "
                       f"({_pct(card['sector_ret'])}{_spy_clause(card)}) "
                       f"would be ≈${gap:,.0f} ahead over the same period."
                       + _top_sector_sentence(card, ctx))
        out.append({
            "action": "EXIT", "severity": "high",
            "reason": (f"{sym}: down {abs(ret) * 100:.1f}% since entry and "
                       f"lagging its sector by {abs(vs_sector) * 100:.1f}% — "
                       "the loss is not sector-wide weakness. Consider "
                       f"exiting.{gap_txt}"),
        })

    # --- TRIM: overweight ----------------------------------------------------
    if weight is not None and weight > t.weight_cap:
        shares, dollars = _shares_for_weight_delta(weight, t.weight_cap, pv, last)
        if shares:
            out.append({
                "action": "TRIM", "severity": "medium", "shares": shares,
                "reason": (f"{sym}: weight {weight * 100:.1f}% exceeds your "
                           f"{t.weight_cap * 100:.0f}% cap — trim ~{shares} sh "
                           f"(≈${dollars:,.0f}) to get back under it."),
            })

    # --- SET_STOP: drawdown from held peak -----------------------------------
    dd = card.get("drawdown")
    atr = card.get("atr")
    entry = card.get("entry_price")
    qty = card.get("quantity")
    if (dd is not None and dd >= t.dd_trigger and last and atr
            and entry and qty and pv):
        peak = last / (1 - dd)
        entry_stop = entry - (t.max_position_loss_pct * pv) / qty
        chandelier = peak - t.atr_mult * atr
        level = max(entry_stop, chandelier)
        if level >= last:
            # A sell stop at/above market would trigger instantly — the price
            # has already fallen through the trailing stop. Be honest: no
            # level, escalate severity.
            out.append({
                "action": "SET_STOP", "severity": "high",
                "reason": (f"{sym}: price has already fallen through a "
                           f"{t.atr_mult:g}×ATR trailing stop from the held "
                           "peak — treat as an exit signal and reassess "
                           "immediately."),
            })
        else:
            # Describe only the leg that won the max().
            leg = (f"caps loss at {t.max_position_loss_pct * 100:g}% of "
                   "portfolio" if entry_stop >= chandelier
                   else f"{t.atr_mult:g}×ATR below the held peak")
            out.append({
                "action": "SET_STOP", "severity": "medium",
                "level": round(level, 2),
                "reason": (f"{sym}: {dd * 100:.1f}% off its held peak — set a "
                           f"stop at ${level:,.2f} ({leg})."),
            })

    # --- SCALE_OUT: big winner — bank tranches -------------------------------
    if ret >= t.take_profit_pct and atr and last:
        r_unit = t.atr_mult * atr
        target = last + 2 * r_unit
        out.append({
            "action": "SCALE_OUT", "severity": "low",
            "reason": (f"{sym}: up {_pct(ret)} — consider selling ⅓ near "
                       f"${target:,.2f} (+2R), ⅓ on a close below the 20-day "
                       "low, and reassess the rest."),
        })

    # --- REVIEW + opportunity cost: lagging the sector ------------------------
    if (not exit_fired and vs_sector is not None
            and vs_sector <= -t.sector_lag_pct
            and card.get("sector_ret") is not None):
        gap = _opportunity_gap(card)
        etf = ctx.get("sector_etf") or "its sector ETF"
        days = card.get("days_held")
        gap_txt = f" (≈${gap:,.0f} left on the table vs sector)" if gap else ""
        out.append({
            "action": "REVIEW", "severity": "medium",
            "reason": (f"{sym}: returned {_pct(ret)} vs {etf} "
                       f"{_pct(card['sector_ret'])}{_spy_clause(card)} over "
                       f"the same {days or '—'} days{gap_txt} — the same "
                       "capital worked harder in the sector itself."
                       + _top_sector_sentence(card, ctx)),
        })

    # --- SCALE_IN: strong, underweight ----------------------------------------
    comp = card.get("composite")
    avg_w = ctx.get("avg_weight")
    if (comp is not None and comp >= 3.0 and weight is not None
            and avg_w is not None and weight < avg_w and last):
        shares, dollars = _shares_for_weight_delta(avg_w, weight, pv, last)
        if shares:
            out.append({
                "action": "SCALE_IN", "severity": "low", "shares": shares,
                "reason": (f"{sym}: composite grade "
                           f"{comp:.1f}/4 but only {weight * 100:.1f}% weight "
                           f"(your average is {avg_w * 100:.1f}%) — adding "
                           f"~{shares} sh (≈${abs(dollars):,.0f}) in 2 tranches "
                           "would bring it to an average-size position."),
            })

    # --- capital efficiency: bottom quartile of the portfolio ------------------
    # An EXIT already covers the position; don't pile a second sell signal on.
    capital_pct = card.get("capital_pct")
    if (capital_pct is not None and capital_pct <= t.bottom_quartile
            and not any(s["action"] == "EXIT" for s in out)):
        pct_txt = (f"capital efficiency in the bottom "
                   f"{t.bottom_quartile * 100:.0f}% of your portfolio")
        ann = card.get("ann_return")
        ann_txt = (f"annualized return {_pct(ann)}" if ann is not None
                   else "annualized return unavailable")
        avg_w = ctx.get("avg_weight")
        shares = dollars = None
        if (ret <= 0 and weight is not None and avg_w is not None
                and weight > avg_w):
            shares, dollars = _shares_for_weight_delta(weight, avg_w, pv, last)
        if ret <= 0 and shares:
            out.append({
                "action": "TRIM", "severity": "medium", "shares": shares,
                "reason": (f"{sym}: {pct_txt} ({ann_txt}) while flat-or-down "
                           f"{_pct(ret)} — trim ~{shares} sh "
                           f"(≈${dollars:,.0f}) back to an average-size "
                           "position and redeploy the capital."),
            })
        else:
            # Winner, or no overweight slice to cut: prose-only TRIM advice
            # would be wrong, so flag it for review instead.
            out.append({
                "action": "REVIEW", "severity": "low",
                "reason": (f"{sym}: {pct_txt} ({ann_txt}) — this capital is "
                           "working the least; review whether the position "
                           "still earns its slot."),
            })

    # --- default ---------------------------------------------------------------
    if not out:
        out.append({"action": "HOLD", "severity": "low",
                    "reason": f"{sym}: performing in line or better — no "
                              "changes suggested."})
    return out
