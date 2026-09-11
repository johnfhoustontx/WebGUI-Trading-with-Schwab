"""paper_concentration.py - per-name and per-expiry caps for the paper engine.

The engine's risk envelope had exactly two rungs: ``MAX_RISK_PER_TRADE`` (one
trade) and ``MAX_SESSION_DRAWDOWN`` (the whole account). Nothing sat between
them, so a book could be entirely one name and still look disciplined at both
ends -- which is what happened on 2026-09-08, when all fourteen open positions
were ORCL put credit spreads expiring 2026-09-11, over a report scheduled for
2026-09-10.

Why the caps live here rather than inline in ``run_entry_cycle``: this is a pure
decision over a book and a candidate, so it is unit-testable without a broker, a
client or a database -- the house convention for anything the engine has to get
right (see CLAUDE.md, "Structure for testability").

⚠ A refusal here is TRANSIENT -- it depends on the book at this instant, not on
anything intrinsic to the signal. ``run_entry_cycle`` therefore skips a capped
signal WITHOUT recording a rejected order, unlike ``RISK_TOO_HIGH``: an order row
would make ``has_order_for_signal`` blacklist the signal permanently, so a name
that freed up an hour later could never be entered.
"""
import math
import pathlib as _pathlib
import sys as _sys

import config_paper

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared.driver_policy import open_risk_dollars  # noqa: E402

#############################################
# REASONS
#############################################

SYMBOL_POSITION_CAP = "SYMBOL_POSITION_CAP"
SYMBOL_RISK_CAP = "SYMBOL_RISK_CAP"
EXPIRY_POSITION_CAP = "EXPIRY_POSITION_CAP"

#############################################
# POLICY
#############################################


def default_limits():
    """The shipped policy, read from ``config_paper`` at CALL time.

    Read at call time rather than bound as a module constant so a config edit
    plus a restart moves the engine -- and so a test can monkeypatch the config
    module. (The same reason ``paper_account_db`` resolves ``db_path=None`` in
    the body: Python binds a ``def``-time default once, forever.)"""
    return {
        "max_positions_per_symbol": config_paper.MAX_POSITIONS_PER_SYMBOL,
        "max_risk_per_symbol": config_paper.MAX_RISK_PER_SYMBOL,
        "max_positions_per_expiry": config_paper.MAX_POSITIONS_PER_EXPIRY,
    }


def _key(value):
    """Normalise a symbol for comparison. Signals arrive uppercase from the
    scanner, but a stored row that ever drifted in case would otherwise slip
    past the count while looking identical on screen."""
    return (value or "").strip().upper()


def concentration_reject(positions, symbol, expiration, added_risk,
                         limits=None):
    """Return the reason opening this candidate would breach a cap, else None.

    ``positions`` is the OPEN book (closed rows tie up no capital and must not
    count). ``added_risk`` is the candidate's ``max_loss_total`` in dollars.

    The position cap is reported ahead of the risk cap when both bind: a count
    is the more legible thing to read in a log line, and it is the limit the
    operator set out to enforce.
    """
    limits = limits or default_limits()
    rows = [p for p in positions or () if isinstance(p, dict)]

    sym = _key(symbol)
    same_symbol = [p for p in rows if _key(p.get("symbol")) == sym]

    if len(same_symbol) >= limits["max_positions_per_symbol"]:
        return SYMBOL_POSITION_CAP

    # ⚠ Summed through ``open_risk_dollars`` rather than a local ``sum(...)``:
    # it drops non-finite rows instead of poisoning the total, and a NaN total
    # makes every ``>`` comparison False -- silently switching the ceiling off
    # while the code still reads like a guard. That is the repo's documented
    # pins-the-bound trap; reusing the one hardened summation beats a tenth copy.
    if open_risk_dollars(same_symbol) + _finite(added_risk) > limits["max_risk_per_symbol"]:
        return SYMBOL_RISK_CAP

    exp = (expiration or "").strip()
    same_expiry = [p for p in rows if (p.get("expiration") or "").strip() == exp]
    if len(same_expiry) >= limits["max_positions_per_expiry"]:
        return EXPIRY_POSITION_CAP

    return None


def _finite(value):
    """The candidate's own risk, or 0.0 when it is not a usable number.

    Zero is the right absence value HERE and only here: an unreadable candidate
    risk must not be able to wave itself past the ceiling by arithmetic, and the
    caller has already sized the trade -- a missing number means the book's
    existing risk alone decides."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0
