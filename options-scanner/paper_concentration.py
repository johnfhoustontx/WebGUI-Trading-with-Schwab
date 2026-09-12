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
from shared import sectors as _sectors  # noqa: E402

#############################################
# REASONS
#############################################

SYMBOL_POSITION_CAP = "SYMBOL_POSITION_CAP"
SYMBOL_RISK_CAP = "SYMBOL_RISK_CAP"
EXPIRY_POSITION_CAP = "EXPIRY_POSITION_CAP"
SECTOR_POSITION_CAP = "SECTOR_POSITION_CAP"
SECTOR_RISK_CAP = "SECTOR_RISK_CAP"
DEPLOYMENT_CAP = "DEPLOYMENT_CAP"

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
        "max_positions_per_sector": config_paper.MAX_POSITIONS_PER_SECTOR,
        "max_risk_per_sector": config_paper.MAX_RISK_PER_SECTOR,
        "max_deployed_risk_pct": config_paper.MAX_DEPLOYED_RISK_PCT,
    }


def _key(value):
    """Normalise a symbol for comparison. Signals arrive uppercase from the
    scanner, but a stored row that ever drifted in case would otherwise slip
    past the count while looking identical on screen."""
    return (value or "").strip().upper()


def concentration_reject(positions, symbol, expiration, added_risk,
                         limits=None, equity=None, sector_of=None):
    """Return the reason opening this candidate would breach a cap, else None.

    ``positions`` is the OPEN book (closed rows tie up no capital and must not
    count). ``added_risk`` is the candidate's ``max_loss_total`` in dollars.

    The position cap is reported ahead of the risk cap when both bind: a count
    is the more legible thing to read in a log line, and it is the limit the
    operator set out to enforce.

    ``equity`` enables the BOOK-WIDE deployment cap (gap assessment B3) — total
    open max loss as a fraction of it. It is reported FIRST when it binds: if the
    book as a whole is full, which symbol was asked for is irrelevant, and the
    broader reason is the more useful log line.

``sector_of`` overrides the sector lookup (gap assessment B4); absent, the
    real ``shared.sectors`` map is used. The sector rungs are reported after the
    symbol ones and before expiry - see the comment at that check.

    ⚠ **No equity, or a non-finite one, SKIPS that cap rather than treating it as
    zero.** A fraction of an unknown cannot be enforced, and a zero denominator
    would refuse every trade forever — which reads as a broken engine, not as a
    cap. The cap is likewise opt-in by DATA: a ``limits`` dict without
    ``max_deployed_risk_pct`` keeps the pre-B3 behaviour, so every existing caller
    is untouched.
    """
    limits = limits or default_limits()
    rows = [p for p in positions or () if isinstance(p, dict)]

    # Book-wide first — see the docstring.
    pct = limits.get("max_deployed_risk_pct")
    eq = _finite(equity)
    if pct and eq and eq > 0:
        # ``open_risk_dollars`` for the same reason the symbol sum uses it: it
        # drops a non-finite row instead of poisoning the total, and a NaN total
        # makes every ``>`` False — silently switching the ceiling off.
        if open_risk_dollars(rows) + _finite(added_risk) > pct * eq:
            return DEPLOYMENT_CAP

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

    # SECTOR (gap assessment B4) - the rung between the per-symbol caps and the
    # book-wide one. Four DIFFERENT semiconductors at the full symbol cap breach
    # nothing above this, which is exactly the correlated book the playbook warns
    # about; measured, the driver's book once held $21,531 across 15 Information
    # Technology positions, 86% of a $25,000 account in one sector.
    #
    # Reported AFTER the symbol rungs and BEFORE expiry, deliberately: when both a
    # symbol cap and the sector cap bind, "you already hold three MU" is the
    # actionable sentence - the operator can pick another name - while "tech is
    # full" is the answer only once the symbol has room. A shared expiry is the
    # weaker coincidence of the two, so it stays last.
    #
    # Opt-in by DATA like the deployment cap above: a ``limits`` dict without the
    # keys, or a cap of 0, keeps the pre-B4 behaviour untouched.
    max_sector_n = limits.get("max_positions_per_sector")
    max_sector_risk = limits.get("max_risk_per_sector")
    if max_sector_n or max_sector_risk:
        bucket = _group_of(symbol, sector_of)
        if bucket is not None:
            # A row whose symbol lands in no bucket is skipped rather than
            # grouped: ``group_key`` gives an unmapped symbol a bucket of its OWN,
            # so an unknown name is still capped against itself and can neither
            # borrow another sector's allowance nor drag unrelated names in.
            same_sector = [p for p in rows
                           if _group_of(p.get("symbol"), sector_of) == bucket]
            if max_sector_n and len(same_sector) >= max_sector_n:
                return SECTOR_POSITION_CAP
            # Through ``open_risk_dollars`` for the third time in this function,
            # and for the same reason: a NaN row would poison the sum, and a NaN
            # total makes every ``>`` False - switching the ceiling off silently.
            if max_sector_risk and (open_risk_dollars(same_sector)
                                    + _finite(added_risk)) > max_sector_risk:
                return SECTOR_RISK_CAP

    exp = (expiration or "").strip()
    same_expiry = [p for p in rows if (p.get("expiration") or "").strip() == exp]
    if len(same_expiry) >= limits["max_positions_per_expiry"]:
        return EXPIRY_POSITION_CAP

    return None


def _group_of(symbol, sector_of=None):
    """The bucket this symbol is capped in, or ``None`` if it cannot be decided.

    ``sector_of`` is injected so the decision stays pure and testable over a
    stated map; absent, it is the real ``shared.sectors`` one, so production needs
    no wiring at the call site and cannot forget it. An injected lookup that
    returns nothing falls back to the shared ``group_key``, so a partial map
    narrows the bucket rather than dissolving it.

    A lookup that RAISES degrades to "no grouping", not to a refusal: the map is a
    config read, and refusing every trade because a TOML went missing would be a
    worse failure than not applying one of six rungs - the five around it still
    decide. ``shared.config_toml`` never raises, so this is belt and braces
    against an injected lookup.

    An UNMAPPED symbol is visible rather than counted: the bucket it gets is
    literally ``"?<SYMBOL>"``, and ``paper_engine._log_capped`` prints the bucket
    in its journal line, so a ``?`` there says "this name has no sector" without
    a second counter to keep.
    """
    try:
        if sector_of is None:
            return _sectors.group_key(symbol)
        key = _key(symbol)
        if key is None:
            return None
        found = sector_of(key)
        return found if found else _sectors.group_key(key)
    except Exception:  # noqa: BLE001 - see the docstring.
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
