from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List

import numpy as np
import pandas as pd

from options_calculator import bs_greeks


@dataclass
class ContractRow:
    strike: float
    kind: str            # "call" | "put"
    bid: float
    ask: float
    mid: float
    iv: float
    expiry: date


@dataclass
class Leg:
    contract: ContractRow
    sign: int  # +1 = long (bought), -1 = short (sold)
    ratio: int = 1  # per-leg contract multiple (butterfly body = 2)


@dataclass
class Position:
    """A simulated options position — one or more legs of the same expiry."""
    legs: List[Leg]
    label: str  # e.g. "Long TSLA 425C 2026-05-26" or "TSLA 425/430 Call Spread"

    @property
    def primary(self) -> ContractRow:
        """First leg's contract — used as the reference for things like IV display."""
        return self.legs[0].contract

    @property
    def expiry(self) -> date:
        return self.primary.expiry

    @property
    def is_multi_leg(self) -> bool:
        return len(self.legs) > 1

    @classmethod
    def single(cls, contract: ContractRow, direction: str, symbol: str = "") -> "Position":
        """Direction is 'buy' (long) or 'sell' (naked short)."""
        d = direction.lower().strip()
        sign = +1 if d == "buy" else -1
        prefix = "Long" if sign > 0 else "Short (naked)"
        sym = f"{symbol} " if symbol else ""
        label = (f"{prefix} {sym}{contract.strike:g}{contract.kind[0].upper()} "
                 f"exp {contract.expiry}")
        return cls(legs=[Leg(contract=contract, sign=sign)], label=label)

    @classmethod
    def vertical(cls, long_contract: ContractRow, short_contract: ContractRow,
                 symbol: str = "") -> "Position":
        """Vertical spread: long one strike, short another, same kind + expiry."""
        if long_contract.kind != short_contract.kind:
            raise ValueError("vertical spread legs must be the same kind (both calls or both puts)")
        if long_contract.expiry != short_contract.expiry:
            raise ValueError("vertical spread legs must have the same expiry")
        kind_letter = long_contract.kind[0].upper()
        sym = f"{symbol} " if symbol else ""
        label = (f"{sym}{long_contract.strike:g}/{short_contract.strike:g} "
                 f"{long_contract.kind.title()} Spread exp {long_contract.expiry}")
        return cls(
            legs=[
                Leg(contract=long_contract, sign=+1),
                Leg(contract=short_contract, sign=-1),
            ],
            label=label,
        )

    @classmethod
    def from_legs(cls, legs, label: str) -> "Position":
        """Build from an iterable of (contract, sign, ratio) tuples."""
        return cls(legs=[Leg(contract=c, sign=int(s), ratio=int(r))
                         for c, s, r in legs], label=label)


_GREEK_COLS = ["theo_price", "delta", "gamma", "theta", "vega", "rho"]


def aggregate_position(position: Position, per_leg_fn) -> pd.DataFrame:
    """Run ``per_leg_fn(contract)`` on each leg, sign-flip the Greeks per leg,
    and sum them. ``per_leg_fn`` must return a DataFrame with at least
    ``theo_price`` and the five Greek columns; any other columns (e.g. ``S``,
    timestamps in the index) are preserved from the first leg.

    Returns an empty DataFrame if any leg's result is empty."""
    agg = None
    for leg in position.legs:
        df = per_leg_fn(leg.contract)
        if df is None or df.empty:
            return df if df is not None else pd.DataFrame()
        signed = df.copy()
        scale = leg.sign * leg.ratio
        for col in _GREEK_COLS:
            if col in signed.columns:
                signed[col] = signed[col] * scale
        if agg is None:
            agg = signed
        else:
            for col in _GREEK_COLS:
                if col in signed.columns and col in agg.columns:
                    agg[col] = agg[col].values + signed[col].values
    return agg if agg is not None else pd.DataFrame()


@dataclass
class ChainSnapshot:
    spot: float
    as_of: datetime
    r: float
    symbol: str = ""
    contracts: List[ContractRow] = field(default_factory=list)
    price_history: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


class ReplayEngine:
    """Re-prices a contract along the underlying's recent price path."""

    def __init__(self, snapshot: ChainSnapshot):
        self.snap = snapshot

    def full_trace(self, contract: ContractRow) -> pd.DataFrame:
        hist = self.snap.price_history
        if hist.empty:
            return pd.DataFrame(columns=["theo_price", "delta", "gamma", "theta", "vega", "rho"])

        # T at each bar: time from that bar's timestamp to expiry end-of-day (15:00).
        expiry_dt = datetime.combine(contract.expiry, datetime.min.time()).replace(hour=15)
        years_to_expiry = np.array([
            max((expiry_dt - ts.to_pydatetime()).total_seconds() / (365 * 86400), 1e-6)
            for ts in hist.index
        ])

        rows = []
        for S, T in zip(hist.values, years_to_expiry):
            g = bs_greeks(S=float(S), K=contract.strike, T=float(T),
                          r=self.snap.r, sigma=contract.iv, option_type=contract.kind)
            rows.append({
                "theo_price": g["price"],
                "delta": g["delta"], "gamma": g["gamma"],
                "theta": g["theta"], "vega": g["vega"], "rho": g["rho"],
            })
        return pd.DataFrame(rows, index=hist.index)


class WhatIfEngine:
    """Sweep underlying price S at fixed T."""

    def __init__(self, snapshot: ChainSnapshot):
        self.snap = snapshot

    def sweep(self, contract: ContractRow, s_range, t_days: float) -> pd.DataFrame:
        T = max(t_days / 365.0, 1e-6)
        s_arr = np.asarray(s_range, dtype=float)
        g = bs_greeks(S=s_arr, K=contract.strike, T=T,
                      r=self.snap.r, sigma=contract.iv, option_type=contract.kind)
        return pd.DataFrame({
            "S": s_arr,
            "theo_price": g["price"],
            "delta": g["delta"], "gamma": g["gamma"],
            "theta": g["theta"], "vega": g["vega"], "rho": g["rho"],
        })


class IVShockEngine:
    """Sweep IV at fixed S, T."""

    def __init__(self, snapshot: ChainSnapshot):
        self.snap = snapshot

    def sweep(self, contract: ContractRow, multipliers) -> pd.DataFrame:
        expiry_dt = datetime.combine(contract.expiry, datetime.min.time()).replace(hour=15)
        T = max((expiry_dt - self.snap.as_of).total_seconds() / (365 * 86400), 1e-6)
        mults = np.asarray(multipliers, dtype=float)
        sigmas = mults * contract.iv
        rows = []
        for m, sig in zip(mults, sigmas):
            g = bs_greeks(S=self.snap.spot, K=contract.strike, T=T,
                          r=self.snap.r, sigma=float(sig), option_type=contract.kind)
            rows.append({
                "sigma_mult": m, "sigma": sig,
                "theo_price": g["price"],
                "delta": g["delta"], "gamma": g["gamma"],
                "theta": g["theta"], "vega": g["vega"], "rho": g["rho"],
            })
        return pd.DataFrame(rows)
