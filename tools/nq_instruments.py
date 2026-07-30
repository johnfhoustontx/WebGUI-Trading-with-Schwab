"""
nq_instruments.py - per-instrument specs for the dealer-positioning HUD
Version: 1.0.0
Last Updated: 2026-07-30

The HUD renders NQ and ES side by side. Everything that differs between them is
DATA, collected here, so the readers, the pure signal logic, the state export
and the NinjaTrader panel all stay instrument-agnostic and the two panes cannot
drift apart in behaviour.

WHAT ACTUALLY DIFFERS — and why each field has to exist:

  * cash index and its ETF fallback ($NDX/QQQ vs $SPX/SPY). The gamma grids are
    per-symbol; there is no shared one.
  * tape tile names and the futures contract. These are duplicated from
    services/market_svc/symbols.py and change at every quarterly roll, so
    test_nq_instruments.py asserts they still agree.
  * contract point values. NQ is $20/pt, ES is $50/pt — a shared number would
    misstate dollar risk by 2.5x.
  * the STOP BAND, which is the one non-obvious entry. Stops are ATR-scaled but
    clamped, and the clamps are in POINTS. NDX trades near 4x SPX, so the same
    percentage move is ~4x the points on NQ. Reusing NQ's 15-45 band on ES would
    put a floor stop at roughly four times the intended risk. The ES band is
    NQ's scaled by that ratio, preserving the floor-to-ceiling ratio (3x) which
    is what actually governs how much room the ATR scaling has.

The regime constants (FLIP_ZONE_PCT, WALL_PROXIMITY_PCT) are deliberately NOT
here: they are percentages of spot, so they are already instrument-independent
and live with the logic in nq_signal.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    """One tradeable future and the cash index whose gamma map drives it."""

    # Short lowercase id. Doubles as the key prefix in nq_state.json
    # (``nq_cash_flip``), so it must stay lowercase and unique.
    key: str
    label: str                  # display name: "NQ"
    micro_label: str            # "MNQ"

    # Cash side — the frame decisions are made in.
    cash_tile: str              # tile display name in cache:market:dashboard
    sources: tuple              # GEX source symbols, most-preferred first

    # Futures side — the frame prices are displayed in.
    future_tile: str            # tile display name in cache:market:dashboard
    contract: str               # Schwab quote symbol behind that tile

    # Economics.
    point_value: float          # $ per index point, full-size contract
    micro_point_value: float    # $ per index point, micro contract

    # Stop clamps, in this instrument's points.
    min_stop: float
    max_stop: float


# NOTE: future_tile/contract are duplicated from services/market_svc/symbols.py
# and MUST be rolled in lockstep with it each quarter. The duplication is
# deliberate — the HUD is a Tier-1 reader and importing a service module for two
# strings would couple it to that service's import graph — but it is guarded by
# test_nq_instruments.py, which fails loudly when they diverge.

NQ = Instrument(
    key="nq",
    label="NQ",
    micro_label="MNQ",
    cash_tile="NDX",
    sources=("$NDX", "QQQ"),
    future_tile="/NQ[U26]",
    contract="/NQU26",
    point_value=20.0,
    micro_point_value=2.0,
    min_stop=15.0,
    max_stop=45.0,
)

ES = Instrument(
    key="es",
    label="ES",
    micro_label="MES",
    cash_tile="SPX",
    sources=("$SPX", "SPY"),
    future_tile="/ES[U26]",
    contract="/ESU26",
    point_value=50.0,
    micro_point_value=5.0,
    # NQ's band divided by the ~4x NDX/SPX index ratio, rounded to whole points
    # and keeping the 3x floor-to-ceiling ratio. At $50/pt a 4-point floor is
    # $200 of risk against NQ's $300 — the same order, which is the check that
    # matters, since these are the numbers a position gets sized from.
    min_stop=4.0,
    max_stop=12.0,
)

# Render order, left to right. NQ first: it is the instrument this HUD was built
# for and the one whose gamma map ($NDX) is the more direct read.
INSTRUMENTS = (NQ, ES)


def by_key(key):
    """Look up a spec by its ``key``, or None. Used when re-reading an exported
    state document, where the key is all that survives."""
    for spec in INSTRUMENTS:
        if spec.key == key:
            return spec
    return None
