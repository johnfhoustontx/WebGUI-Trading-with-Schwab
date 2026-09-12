"""Which names move together — the symbol → sector map, from ``config/sectors.toml``.

Read by ``options-scanner/paper_concentration.py`` for the paper engine's sector
cap (gap assessment B4). Kept in ``shared/`` for the usual reason: the map is data
about symbols, the cap is policy over a book, and the engine tier cannot import
``services.*`` while a future Tier-1 reader could not import the engine either.

**Why a file and not a derivation.** Nothing in this repo derives a symbol's
sector. The one source that existed — ``sentiment-dashboard``'s
``Sectors_Industries_ETFs.xlsx``, five constituents per industry — covered **48 of
the 80 watchlist symbols**, and the 32 it missed included MU, AMAT, MRVL, INTC,
TXN, ALAB, SMCI, DELL and SPCX: precisely the semiconductor tilt the cap exists to
control. Measured against the real book, **181 of 273 historical paper positions
(66%) were on symbols it had never heard of**, so a cap keyed on it would have
read as protection while policing a third of the trades. That workbook seeded the
311 names it does carry; the rest were classified from the proxy's own
``/instruments`` descriptions.

⚠ **The grouping's premise was measured, not assumed** — this repo has now had two
audit rationales fail on measurement, so: over six months of daily returns across
the 73 tradeable watchlist names, mean pairwise correlation **within** a sector is
**0.250** against **0.017 across** sectors, 14 of the 15 most-correlated pairs in
the universe share a sector, and the top decile of correlated pairs is 63%
same-sector against a 21% base rate. The grouping is weakest exactly where the
book concentrates: Information Technology is 30 of the 74 tradeable names and its
internal correlation is only 0.240, because it holds IBM and TXN beside IONQ/RGTI
(0.939) and CRWV/NBIS (0.838).
"""
from repo_paths import SECTORS_TOML
from shared.config_toml import toml_loader

#: The closed vocabulary — the 11 GICS sectors as ``sentiment-dashboard``'s
#: ``sectors_ref`` spells them, plus ``INDEX``. Closed on purpose: a typo in the
#: TOML would otherwise create a sector of one member, which refuses nothing and
#: is indistinguishable from a correctly mapped symbol. A test pins every shipped
#: value against this set, and another pins the spelling against ``sectors_ref``
#: so the two halves of the app cannot disagree about what a sector is called.
SECTORS = frozenset({
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
    # ⚠ Broad-market names are a bucket, NOT an exemption, and that is measured:
    # SPY / QQQ / DIA / IWM correlate 0.799 pairwise — the second-tightest group
    # in the universe after Energy (0.846). Nine simultaneous index positions is
    # one market bet; the driver book held nine, for $15,018.
    "INDEX",
})

#: Prefix for the synthetic single-symbol bucket an UNMAPPED name gets. Chosen so
#: it can never collide with a real sector name (none contains ``?``).
_LONE = "?"

DEFAULTS: dict = {"sectors": {}}

load, reset_cache = toml_loader(SECTORS_TOML, DEFAULTS, label="sectors.toml")


def _key(symbol):
    """Uppercased, stripped, or ``None`` for anything that is not a symbol."""
    if not isinstance(symbol, str):
        return None
    s = symbol.strip().upper()
    return s or None


def sector_of(symbol):
    """This symbol's sector, or ``None`` if the map has never heard of it.

    ⚠ ``None`` rather than a placeholder string, because only the CALLER knows
    what "unknown" should mean, and here it means *"give this symbol a bucket of
    its own"* — a shared sentinel would pool every unmapped name into one group
    and cap XOM against PG. See :func:`group_key`.

    Case- and whitespace-insensitive: a stored position row that ever drifted in
    case must still group with the signal, the same reason
    ``paper_concentration._key`` exists.
    """
    key = _key(symbol)
    if key is None:
        return None
    table = load().get("sectors")
    if not isinstance(table, dict):
        return None
    value = table.get(key)
    return value if isinstance(value, str) and value else None


def group_key(symbol):
    """The bucket this symbol is capped in — its sector, or itself if unmapped.

    An unmapped symbol gets ``"?<SYMBOL>"``, which means a new watchlist name is
    capped exactly as it was before the sector cap existed (by the per-symbol
    rungs, which are tighter) and can neither borrow another sector's allowance
    nor drag unrelated names into one bucket. **The alternative — exempting it —
    would be the same behaviour with none of the accounting**, and the alternative
    to that, one shared "unknown" bucket, would cap genuinely unrelated names
    against each other.

    Returns ``None`` only for input that is not a symbol at all, which the caller
    should treat as "no grouping possible" rather than as a bucket.
    """
    key = _key(symbol)
    if key is None:
        return None
    return sector_of(key) or (_LONE + key)


def is_mapped(symbol) -> bool:
    """Whether this symbol has a real sector. The count behind the log line that
    keeps the map's coverage honest — an unmapped name is not an error, but a
    growing number of them means the cap is quietly covering less of the book."""
    return sector_of(symbol) is not None
