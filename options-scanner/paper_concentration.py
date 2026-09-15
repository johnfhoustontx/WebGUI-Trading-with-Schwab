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
import pathlib as _pathlib
import sys as _sys

import config_paper

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import sectors as _sectors  # noqa: E402
from shared import book_caps as _book_caps  # noqa: E402

#############################################
# REASONS
#############################################

# The reason codes live in shared.book_caps since 2026-09-15 and are re-exported
# here so every existing caller and log line keeps its spelling.
SYMBOL_POSITION_CAP = _book_caps.SYMBOL_POSITION_CAP
SYMBOL_RISK_CAP = _book_caps.SYMBOL_RISK_CAP
EXPIRY_POSITION_CAP = _book_caps.EXPIRY_POSITION_CAP
SECTOR_POSITION_CAP = _book_caps.SECTOR_POSITION_CAP
SECTOR_RISK_CAP = _book_caps.SECTOR_RISK_CAP
DEPLOYMENT_CAP = _book_caps.DEPLOYMENT_CAP

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

    A thin adapter over ``shared.book_caps`` since 2026-09-15: sectors are
    resolved here exactly as before (``_group_of``, including the ``?SYMBOL``
    bucket and raise-means-no-grouping), then every rung is evaluated there and
    the first breach is reported in the Account's historical order - deployment,
    symbol, sector, expiry. ``tests/test_book_caps_equivalence.py`` holds the
    pre-change function frozen and proves the decisions are identical.

    (The rest of the old docstring's reasoning - why the deployment cap reports
    first, why sector precedes expiry, why no equity skips - still holds and is
    recorded on ``shared.book_caps``.)
    """
    limits = limits or default_limits()
    book = [dict(p, sector=_group_of(p.get("symbol"), sector_of))
            for p in positions or () if isinstance(p, dict)]
    candidate = {"symbol": symbol, "expiration": expiration,
                 "sector": _group_of(symbol, sector_of)}
    rungs = _book_caps.evaluate(book, candidate, added_risk, limits, equity)
    breach = _book_caps.first_breach(rungs, _book_caps.ACCOUNT_ORDER)
    return breach["code"] if breach else None


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
