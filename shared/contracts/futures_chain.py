"""The /ES and /NQ 0-DTE chain feed's snapshot, as the capture on the PC sends it.

ThinkorSwim's Excel feed reads ``N/A`` for a contract that is not listed or that
has no value yet, and prints a volatility as ``12.51%``. The row model turns both
into what the service can use: an absent value is ``None`` (never zero) and a
percent string is the number. Everything else about a snapshot is checked by the
adapter (``services/options_svc/futures_chain_adapter.py``), which knows the
instrument's terms; this model only fixes the shapes.

Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md (sections 5.2, 5.5).
"""
import math
from datetime import date
from typing import Literal

from pydantic import field_validator

from .envelope import _Base

_READ_FIELDS = ("mark", "bid", "ask", "volume", "open_int", "iv", "delta")
# Prices and counts cannot be negative; a volatility or a delta is judged by the
# adapter, which has the rule for each.
_NON_NEGATIVE = ("mark", "bid", "ask", "volume", "open_int")


def _reading(raw):
    """``raw`` as a finite float, or ``None`` when it is not a reading: ``N/A``,
    an empty string, a NaN or infinity, a bool, or text that is not a number. A
    trailing ``%`` is dropped (``"12.51%"`` is 12.51)."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            raw = float(text)
        except ValueError:
            return None
    elif not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


class FuturesContractRow(_Base):
    """One option contract's values: ``series`` (e.g. ``E1AX26``), ``side`` and
    ``strike`` identify it; the rest are readings or ``None``."""

    series: str
    side: Literal["C", "P"]
    strike: float
    mark: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    open_int: float | None = None
    iv: float | None = None       # percent, as ThinkorSwim prints it: 12.51
    delta: float | None = None

    @field_validator(*_READ_FIELDS, mode="before")
    @classmethod
    def _to_reading(cls, raw):
        return _reading(raw)

    @field_validator(*_NON_NEGATIVE)
    @classmethod
    def _not_negative(cls, value):
        return None if value is not None and value < 0 else value

    @field_validator("strike")
    @classmethod
    def _a_strike(cls, value):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("a strike is a positive number")
        return value


class FuturesChainSnapshot(_Base):
    """One instrument's capture. ``instrument`` is the root (``ES``), ``expiry``
    the date the capture meant to read, ``descriptions`` one contract's
    ThinkorSwim ``DESCRIPTION`` per series (the adapter reads the terms off it),
    and ``underlying_symbol`` / ``underlying_price`` the future the options are
    written on and what it traded at."""

    instrument: str
    expiry: date
    captured_at: float
    descriptions: dict[str, str]
    underlying_symbol: str
    underlying_price: float
    contracts: list[FuturesContractRow]
