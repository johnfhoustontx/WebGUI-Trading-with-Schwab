"""concentration_reject must decide EXACTLY as it did before book_caps.

The frozen copy below is the function as of 2026-09-15 (commit before this
change), trimmed of comments only. Do not edit it to make a test pass: a
mismatch means the adapter changed a money-path decision.
"""
import math
import random

import pytest

import paper_concentration as pc
from shared import sectors as _sectors
from shared.driver_policy import open_risk_dollars


def _frozen_group_of(symbol, sector_of=None):
    try:
        if sector_of is None:
            return _sectors.group_key(symbol)
        key = (symbol or "").strip().upper()
        if key is None:
            return None
        found = sector_of(key)
        return found if found else _sectors.group_key(key)
    except Exception:  # noqa: BLE001
        return None


def _frozen_finite(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def frozen_concentration_reject(positions, symbol, expiration, added_risk,
                                limits, equity=None, sector_of=None):
    rows = [p for p in positions or () if isinstance(p, dict)]
    pct = limits.get("max_deployed_risk_pct")
    eq = _frozen_finite(equity)
    if pct and eq and eq > 0:
        if open_risk_dollars(rows) + _frozen_finite(added_risk) > pct * eq:
            return "DEPLOYMENT_CAP"
    key = lambda v: (v or "").strip().upper()  # noqa: E731
    sym = key(symbol)
    same_symbol = [p for p in rows if key(p.get("symbol")) == sym]
    if len(same_symbol) >= limits["max_positions_per_symbol"]:
        return "SYMBOL_POSITION_CAP"
    if open_risk_dollars(same_symbol) + _frozen_finite(added_risk) > limits["max_risk_per_symbol"]:
        return "SYMBOL_RISK_CAP"
    max_sector_n = limits.get("max_positions_per_sector")
    max_sector_risk = limits.get("max_risk_per_sector")
    if max_sector_n or max_sector_risk:
        bucket = _frozen_group_of(symbol, sector_of)
        if bucket is not None:
            same_sector = [p for p in rows
                           if _frozen_group_of(p.get("symbol"), sector_of) == bucket]
            if max_sector_n and len(same_sector) >= max_sector_n:
                return "SECTOR_POSITION_CAP"
            if max_sector_risk and (open_risk_dollars(same_sector)
                                    + _frozen_finite(added_risk)) > max_sector_risk:
                return "SECTOR_RISK_CAP"
    exp = (expiration or "").strip()
    same_expiry = [p for p in rows if (p.get("expiration") or "").strip() == exp]
    if len(same_expiry) >= limits["max_positions_per_expiry"]:
        return "EXPIRY_POSITION_CAP"
    return None


SYMBOLS = ["ORCL", "orcl ", "MSFT", "AMD", "MU", "XOM", "SPY", "QQQ",
           "NOTAREALSYM", None, ""]
EXPIRIES = ["2026-10-17", " 2026-10-17", "2026-10-24", None, ""]
RISKS = [0.0, 50.0, 125.5, 250.0, 700.0, float("nan"), None, "junk", -10.0]


def _random_limits(rng):
    return {
        "max_positions_per_symbol": rng.choice([0, 1, 2, 3, 5]),
        "max_risk_per_symbol": rng.choice([0.0, 250.0, 750.0, 5000.0]),
        "max_positions_per_expiry": rng.choice([0, 1, 3, 5]),
        **({"max_positions_per_sector": rng.choice([0, 2, 5])} if rng.random() < .8 else {}),
        **({"max_risk_per_sector": rng.choice([0, 500.0, 1500.0])} if rng.random() < .8 else {}),
        **({"max_deployed_risk_pct": rng.choice([0, 0.05, 0.2])} if rng.random() < .8 else {}),
    }


def _random_book(rng):
    book = []
    for _ in range(rng.randint(0, 12)):
        row = {"symbol": rng.choice(SYMBOLS), "expiration": rng.choice(EXPIRIES)}
        if rng.random() < .8:
            row["max_loss_total"] = rng.choice(RISKS)
        else:
            row["max_loss"] = rng.choice(RISKS)
            row["quantity"] = rng.choice([1, 2, None])
        book.append(row)
    if rng.random() < .1:
        book.append("not a dict")
    return book


@pytest.mark.parametrize("seed", range(3000))
def test_adapter_decides_exactly_as_the_frozen_function(seed):
    rng = random.Random(seed)
    limits = _random_limits(rng)
    book = _random_book(rng)
    symbol, expiration = rng.choice(SYMBOLS), rng.choice(EXPIRIES)
    added = rng.choice(RISKS)
    equity = rng.choice([None, 0.0, 25000.0, 5000.0, float("nan")])
    sector_of = rng.choice([None, lambda s: "Tech" if s in ("ORCL", "MSFT", "AMD", "MU") else None])
    expected = frozen_concentration_reject(book, symbol, expiration, added, limits,
                                           equity=equity, sector_of=sector_of)
    got = pc.concentration_reject(book, symbol, expiration, added, limits=limits,
                                  equity=equity, sector_of=sector_of)
    assert got == expected, (seed, limits, book, symbol, expiration, added, equity)
