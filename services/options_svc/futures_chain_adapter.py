"""Turn a captured /ES or /NQ chain snapshot into a chain the page's engine reads.

The capture (ThinkorSwim through Excel, on the PC) sends one snapshot per
instrument per interval. ``build_chain`` is the one gate between it and everything
the page draws: it checks the snapshot against the terms the contracts state
themselves, calculates each contract's gamma, scales the counts to the engine's
100-share convention and lays the result out the way Schwab's chain is laid out, so
``GammaEngine``, ``flow_skew`` and ``iv_analysis`` read it unchanged. Anything it
cannot vouch for it refuses, with a reason the status view shows; it never repairs a
snapshot into a plausible one. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md (sections 5.2, 5.4, 5.5).

Imports nothing from ``compute`` (which has a line ceiling): the time left comes
from ``expiry_time``, the tier's one helper.
"""
import logging
import math
from datetime import date, datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from services.options_svc.expiry_time import time_to_expiry_years
from shared import futures_chain, futures_chain_config
from shared.contracts.futures_chain import FuturesChainSnapshot, FuturesContractRow

log = logging.getLogger(__name__)

_CENTRAL = ZoneInfo("America/Chicago")

# Hours in the 365-day year the engine and ``expiry_time`` both use: time left in
# years times this is hours, and the engine divides it back.
_HOURS_PER_YEAR = 365.0 * 24.0

# The engine counts every contract as 100 shares; a future's option is the
# instrument's own multiplier, so counts are scaled by multiplier / 100.
_ENGINE_SHARES = 100.0

# The parity check looks at this many strikes, nearest the price first, for a
# call and put that both have a mark. Not wider: far strikes quote wide.
_PARITY_STRIKES = 5


class Refused(Exception):
    """The snapshot cannot be vouched for. ``reason`` is what the status view
    shows the operator; it is also the exception's text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AdaptedChain(NamedTuple):
    symbol: str               # "FOP.ES"
    expiry: date
    chain: dict               # Schwab's layout, one expiry key at DTE 0
    captured_at: float
    hours_left: float         # to the close; finite and positive
    parity_gap: float | None  # |implied future - read future|, None if not checkable
    listed: int               # contracts that read at least one value


# ── the snapshot itself ──────────────────────────────────────────────────────

def _capture_time(snapshot: FuturesChainSnapshot) -> datetime:
    if not math.isfinite(snapshot.captured_at):
        raise Refused("capture time is not a time")
    try:
        return datetime.fromtimestamp(snapshot.captured_at, _CENTRAL)
    except (OverflowError, OSError, ValueError):
        raise Refused("capture time is not a time") from None


def _hours_left(snapshot: FuturesChainSnapshot, captured: datetime) -> float:
    """Hours from the capture to the close. The helper floors at zero past the
    close, so "past expiry" is decided by the instants themselves, and the result
    must still be positive: a config whose close differs from the helper's
    settlement instant cannot slip a zero or a negative into the engine."""
    close = futures_chain.session_bounds(snapshot.expiry)[1]
    if snapshot.captured_at >= close:
        raise Refused(f"captured at or after the {snapshot.expiry} expiry close")
    hours = time_to_expiry_years(captured, snapshot.expiry) * _HOURS_PER_YEAR
    if not math.isfinite(hours) or hours <= 0:
        raise Refused(f"no time left to the {snapshot.expiry} expiry")
    return hours


# ── a series' own description ────────────────────────────────────────────────

def _series_problem(name, text, snapshot, spec) -> str | None:
    """Why this series cannot be used, or ``None`` when its description states the
    terms the snapshot claims."""
    terms = futures_chain.parse_description(text)
    if terms is None:
        return "its description is not readable"
    if terms.series != str(name).upper():
        return f"series name {name} does not match the description's {terms.series}"
    if terms.expiry != snapshot.expiry:
        return f"expiry {terms.expiry} is not {snapshot.expiry}"
    if terms.am_settled:
        return "morning settled (AM Settled): gone before the session"
    if terms.multiplier != spec["multiplier"]:
        return f"multiplier 1/{terms.multiplier} is not the configured 1/{spec['multiplier']}"
    if (terms.underlying != snapshot.underlying_symbol.strip().upper()
            or not terms.underlying.startswith("/" + snapshot.instrument.upper())):
        return (f"underlying {terms.underlying} is not the {snapshot.underlying_symbol} "
                "the price was read from")
    return None


def _usable_series(snapshot, spec) -> set[str]:
    """The series whose descriptions pass. One that fails is dropped and logged
    while another passes; when none passes, the snapshot is refused."""
    kept, problems = set(), []
    for name, text in snapshot.descriptions.items():
        problem = _series_problem(name, text, snapshot, spec)
        if problem is None:
            kept.add(name)
        else:
            problems.append(f"{name}: {problem}")
    if problems and kept:
        log.warning("%s: dropped series %s", snapshot.instrument, "; ".join(problems))
    if not kept:
        raise Refused("no series passed its check (" + "; ".join(problems) + ")"
                      if problems else "no series described")
    return kept


# ── gamma ────────────────────────────────────────────────────────────────────

def _volatility(iv) -> float:
    """The implied volatility in percent when usable, else 0 (the engine's own
    skip for charm and vanna)."""
    return iv if iv is not None and math.isfinite(iv) and iv > 0 else 0.0


def _gamma(future: float, strike: float, iv_pct: float, hours_left: float):
    """``phi(d1) / (F sigma sqrt(T))``: Black-76 gamma with no carry, from this
    contract's own volatility. ``None`` with no usable volatility: absent, never
    zero."""
    if iv_pct <= 0:
        return None
    sigma = iv_pct / 100.0
    root_t = math.sqrt(hours_left / _HOURS_PER_YEAR)
    d1 = (math.log(future / strike) + 0.5 * sigma * sigma * root_t * root_t) / (sigma * root_t)
    value = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) / (future * sigma * root_t)
    return value if math.isfinite(value) else None


def _scaled(count, scale):
    return None if count is None else count * scale


def _contract(row: FuturesContractRow, future: float, hours_left: float, scale: float) -> dict:
    iv_pct = _volatility(row.iv)
    return {
        "putCall": "CALL" if row.side == "C" else "PUT",
        "strikePrice": row.strike,
        "openInterest": _scaled(row.open_int, scale),
        "totalVolume": _scaled(row.volume, scale),
        "volatility": iv_pct,
        "gamma": _gamma(future, row.strike, iv_pct, hours_left),
        "delta": row.delta,
        "mark": row.mark,
        "bid": row.bid,
        "ask": row.ask,
        # One session is one expiry, at DTE 0 from the evening it opens: the
        # engine's 0-DTE slice and nearest-expiry search both rely on it.
        "daysToExpiration": 0,
        "series": row.series,
    }


def _reads_something(row: FuturesContractRow) -> bool:
    return any(value is not None for value in
               (row.mark, row.bid, row.ask, row.volume, row.open_int, row.iv))


# ── parity ───────────────────────────────────────────────────────────────────

def _price(row: FuturesContractRow):
    """The row's mark, else its bid-ask midpoint; ``None`` when neither reads."""
    if row.mark is not None and row.mark > 0:
        return row.mark
    if row.bid is not None and row.ask is not None and row.bid + row.ask > 0:
        return (row.bid + row.ask) / 2.0
    return None


def _implied_future(rows: list[FuturesContractRow], future: float):
    """The future the options themselves imply, ``K + call - put``, at the strike
    nearest ``future`` with both marks (looking at the nearest few), or ``None``."""
    by_strike: dict[float, dict[str, dict[str, float]]] = {}
    for row in rows:
        price = _price(row)
        if price is not None:
            by_strike.setdefault(row.strike, {}).setdefault(row.series, {}).setdefault(
                row.side, price)
    for strike in sorted({r.strike for r in rows}, key=lambda k: (abs(k - future), k))[:_PARITY_STRIKES]:
        for sides in by_strike.get(strike, {}).values():
            if "C" in sides and "P" in sides:
                return strike + sides["C"] - sides["P"]
    return None


# ── the chain ────────────────────────────────────────────────────────────────

def build_chain(snapshot: FuturesChainSnapshot) -> AdaptedChain:
    """The snapshot as a Schwab-shaped chain, or ``Refused`` with the reason."""
    spec = futures_chain_config.instruments().get(snapshot.instrument)
    if spec is None:
        raise Refused(f"unknown instrument {snapshot.instrument!r}")
    future = snapshot.underlying_price
    if not math.isfinite(future) or future <= 0:
        raise Refused(f"underlying price {future} is not a price")
    captured = _capture_time(snapshot)
    hours_left = _hours_left(snapshot, captured)

    kept = _usable_series(snapshot, spec)
    rows, seen = [], set()
    for row in snapshot.contracts:
        identity = (row.series, row.side, row.strike)
        if row.series in kept and identity not in seen and _reads_something(row):
            seen.add(identity)
            rows.append(row)
    if not rows:
        raise Refused("no contracts read a value")

    implied = _implied_future(rows, future)
    gap = None if implied is None else abs(implied - future)
    if gap is not None and gap > spec["parity_limit_pts"]:
        raise Refused(f"parity: the options imply {implied:.2f} but the future reads "
                      f"{future:.2f} (limit {spec['parity_limit_pts']} points)")

    scale = spec["multiplier"] / _ENGINE_SHARES
    key = f"{snapshot.expiry.isoformat()}:0"
    maps: dict[str, dict] = {"C": {}, "P": {}}
    for row in sorted(rows, key=lambda r: (r.strike, r.series)):
        maps[row.side].setdefault(repr(float(row.strike)), []).append(
            _contract(row, future, hours_left, scale))
    chain = {
        "symbol": spec["symbol"],
        "underlyingPrice": future,
        "callExpDateMap": {key: maps["C"]},
        "putExpDateMap": {key: maps["P"]},
    }
    return AdaptedChain(spec["symbol"], snapshot.expiry, chain, snapshot.captured_at,
                        hours_left, gap, len(rows))
