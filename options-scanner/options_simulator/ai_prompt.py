"""Compose a structured markdown prompt for pasting into Claude.ai.

Mirrors the no-API-call convention of ai_prompt_builder.py at the project root.
"""
from datetime import datetime

import numpy as np

from options_simulator.engine import (
    ChainSnapshot, ContractRow, IVShockEngine, Position, WhatIfEngine,
    aggregate_position,
)
from options_calculator import bs_greeks


SIGMA_MULTIPLIERS = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
S_GRID_PCT = [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20]


def _current_greeks(snap: ChainSnapshot, pos: Position) -> dict:
    """Net Greeks of the position (signed sum across legs) at current spot."""
    expiry_dt = datetime.combine(pos.expiry, datetime.min.time()).replace(hour=15)
    T = max((expiry_dt - snap.as_of).total_seconds() / (365 * 86400), 1e-6)
    out = {"price": 0.0, "delta": 0.0, "gamma": 0.0,
           "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for leg in pos.legs:
        c = leg.contract
        g = bs_greeks(S=snap.spot, K=c.strike, T=T, r=snap.r,
                      sigma=c.iv, option_type=c.kind)
        for k in out:
            out[k] += float(g[k]) * leg.sign
    return out


def _identity_block(snap: ChainSnapshot, pos: Position) -> str:
    primary = pos.primary
    expiry_dt = datetime.combine(primary.expiry, datetime.min.time()).replace(hour=15)
    hours_to_expiry = (expiry_dt - snap.as_of).total_seconds() / 3600.0
    sym = snap.symbol or "(unknown)"

    lines = [
        "## Position",
        f"- Underlying: **{sym}**  (spot ${snap.spot:.2f} as of {snap.as_of:%Y-%m-%d %H:%M})",
        f"- Structure: {pos.label}",
        f"- Expiry: {primary.expiry}",
        f"- Hours to expiry: {hours_to_expiry:.1f}",
    ]
    if pos.is_multi_leg:
        lines.append("- Legs:")
        for leg in pos.legs:
            side = "LONG" if leg.sign > 0 else "SHORT"
            c = leg.contract
            lines.append(
                f"    - {side} {sym} {c.strike:g}{c.kind[0].upper()} "
                f"bid/ask {c.bid:.2f}/{c.ask:.2f} iv={c.iv:.2%}"
            )
        # Net debit/credit for the spread (positive = paid out, negative = received)
        net = sum(leg.contract.mid * leg.sign for leg in pos.legs)
        verb = "Net debit (you pay)" if net > 0 else "Net credit (you receive)"
        lines.append(f"- {verb}: ${abs(net):.2f}")
    else:
        leg = pos.legs[0]
        c = leg.contract
        side = "LONG (bought)" if leg.sign > 0 else "SHORT / NAKED (sold)"
        lines.extend([
            f"- Side: {side}",
            f"- Strike: {c.strike}",
            f"- Kind: {c.kind} ({sym} {c.strike:g}{c.kind[0].upper()} exp {c.expiry})",
            f"- Bid/Ask/Mid: {c.bid:.2f} / {c.ask:.2f} / {c.mid:.2f}",
            f"- Implied volatility: {c.iv:.2%}",
        ])
        if leg.sign < 0:
            lines.append(
                f"- Note: this is a **naked short {c.kind}**. Max gain = premium received "
                f"(${c.mid:.2f} × 100 = ${c.mid*100:.0f}); max loss is "
                + ("unlimited as S → ∞." if c.kind == "call" else f"${c.strike*100 - c.mid*100:.0f} if S → 0.")
            )
    return "\n".join(lines) + "\n"


def _greeks_block(g: dict) -> str:
    return (
        "## Current net Greeks (signed for the whole position)\n"
        f"- Delta: {g['delta']:+.4f}\n"
        f"- Gamma: {g['gamma']:+.4f}\n"
        f"- Theta (per day): {g['theta']:+.4f}\n"
        f"- Vega (per 1 vol pt): {g['vega']:+.4f}\n"
        f"- Rho (per 1% rate): {g['rho']:+.4f}\n"
        f"- Theo position value: {g['price']:+.4f}\n"
    )


def _replay_section(snap: ChainSnapshot, pos: Position) -> str:
    hist = snap.price_history
    if hist.empty:
        return "## Replay\nNo underlying price history available.\n"
    o, h, l, x = hist.iloc[0], hist.max(), hist.min(), hist.iloc[-1]
    rng_pct = (h - l) / o * 100.0
    return (
        "## Replay (recent underlying history)\n"
        f"- Bars: {len(hist)}  ({hist.index[0]:%Y-%m-%d %H:%M} → {hist.index[-1]:%Y-%m-%d %H:%M})\n"
        f"- Open: {o:.2f}\n- High: {h:.2f}\n- Low: {l:.2f}\n- Close: {x:.2f}\n"
        f"- Range: {rng_pct:.2f}%\n"
    )


def _whatif_section(snap: ChainSnapshot, pos: Position) -> str:
    s_arr = np.array([snap.spot * (1 + p) for p in S_GRID_PCT])
    expiry_dt = datetime.combine(pos.expiry, datetime.min.time()).replace(hour=15)
    t_days = max((expiry_dt - snap.as_of).total_seconds() / 86400.0, 0.01)
    eng = WhatIfEngine(snap)
    df = aggregate_position(pos, lambda c: eng.sweep(c, s_range=s_arr, t_days=t_days))
    lines = ["## What-if (underlying price sweep — values are NET for the position)",
             "| ΔS% | S | Position value | Δ | Γ | Θ | ν |",
             "|----:|---:|------:|--:|--:|--:|--:|"]
    for i, p in enumerate(S_GRID_PCT):
        row = df.iloc[i]
        lines.append(
            f"| {p*100:+.0f}% | {row['S']:.2f} | {row['theo_price']:+.2f} | "
            f"{row['delta']:+.3f} | {row['gamma']:+.4f} | "
            f"{row['theta']:+.3f} | {row['vega']:+.3f} |"
        )
    return "\n".join(lines) + "\n"


def _ivshock_section(snap: ChainSnapshot, pos: Position) -> str:
    eng = IVShockEngine(snap)
    df = aggregate_position(pos, lambda c: eng.sweep(c, multipliers=SIGMA_MULTIPLIERS))
    lines = ["## IV-shock (σ sweep — values are NET for the position)",
             "| σ×  | Position value | Δ | Γ | Θ | ν |",
             "|----:|------:|--:|--:|--:|--:|"]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['sigma_mult']:.2f} | {row['theo_price']:+.2f} | "
            f"{row['delta']:+.3f} | {row['gamma']:+.4f} | "
            f"{row['theta']:+.3f} | {row['vega']:+.3f} |"
        )
    return "\n".join(lines) + "\n"


_QUESTION_BLOCK = """
## Please analyze this trade in plain English

I'm thinking about taking this position. Walk me through it like you're
explaining it to a smart retail trader who is not a quant. Use plain
English throughout, explain any technical term the first time you use
it, avoid jargon shorthand, and **at every step, tell me WHY** — don't
just state a fact; explain the reasoning that led to it.

**Two themes must run through the whole answer:**

- **WHY IS THIS HAPPENING.** Cover both market context (what macro,
  IV regime, dealer positioning, recent flow explains the current
  setup?) and position context (how would this contract have been
  acting in the recent past, and why does its current Greek profile
  look the way it does?).
- **WHAT IF.** When you describe scenarios, give 2-3 plausible paths
  you pick (move up, move down, sideways, IV spike, IV crush,
  catalyst surprise, etc.) and walk each through in plain English.

Cover these in plain prose (not bullet points unless a list is genuinely
the clearest format):

1. **What is this trade actually betting on?** In one paragraph: what does
   the buyer/seller of this contract need to happen, and WHY does the
   current setup make that bet attractive or dangerous?

2. **Which Greek matters most right now, and WHY?**  Given the time to
   expiry, moneyness, and implied volatility, which Greek will dominate
   the P&L over the next few hours / days? Explain the reasoning.

3. **What scenarios make money and what scenarios blow up?**  Walk through
   the realistic best case and worst case. For each, say WHY the position
   reacts the way it does — don't just say "you lose money", say which
   Greek did the damage and why it accelerated.

4. **Gamma vs theta tension.**  Explain in plain language: is the gamma
   benefit (convexity) worth the theta cost (decay), and WHY? Reference
   the actual numbers above.

5. **IV crush risk.**  If implied volatility drops 25%, what happens to
   this position, in dollars? WHY? Use the vega number above to show
   your work.

6. **Breakeven and probability.**  At what underlying price does this
   trade break even? Given current IV, how likely is the underlying to
   reach that point before expiry? Explain the reasoning — don't just
   pull a number out of the air.

7. **What could go wrong that isn't obvious?**  Pin risk, weekend gap,
   IV regime shift, earnings, hard-to-borrow, settlement-print quirks,
   liquidity at the bid/ask. Pick the two or three most relevant to
   THIS underlying and THIS expiry, and explain WHY each matters here
   specifically (not in general).

8. **Bottom line in one paragraph.**  Would you take this trade, modify it,
   or pass? WHY?

Plain English throughout. No jargon without explaining it the first time.
If a Greek number above is doing something surprising, call it out and
explain WHY.
"""


def build_simulator_prompt(snap: ChainSnapshot, pos_or_contract, mode_state: dict) -> str:
    """Accepts a Position (preferred) or a bare ContractRow (legacy callers wrap
    in a long-call Position automatically)."""
    if isinstance(pos_or_contract, Position):
        pos = pos_or_contract
    else:
        # Legacy: treat a bare ContractRow as a long position.
        pos = Position.single(pos_or_contract, direction="buy", symbol=snap.symbol)
    parts = [_identity_block(snap, pos), _greeks_block(_current_greeks(snap, pos))]
    mode = mode_state.get("mode", "whatif")
    if mode == "replay":
        parts.append(_replay_section(snap, pos))
    elif mode == "ivshock":
        parts.append(_ivshock_section(snap, pos))
    else:
        parts.append(_whatif_section(snap, pos))
    parts.append(_QUESTION_BLOCK)
    return "\n".join(parts)
