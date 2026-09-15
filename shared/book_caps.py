"""shared/book_caps.py - the paper books' risk caps, evaluated in ONE place.

Three callers share this module and must never disagree:

* ``options-scanner/paper_concentration.concentration_reject`` - the Paper
  ACCOUNT's automatic entry cycle (a thin adapter over this since 2026-09-15);
* ``services/options_svc/compute.create_paper_trade`` - the Paper LEDGER, which
  the Paper button opens into and which enforced no cap at all before this;
* ``webgui/pages/options/book_fit`` - the Paper dialog's preview.

Pure: ``math`` and ``shared.driver_policy.open_risk_dollars`` (itself math-only).
Sectors arrive RESOLVED on every row as ``sector``, so this module never reads
config, and Tier 1 may import it.

Every rung is reported, not only the first breach, because the preview shows
headroom. A rung that cannot be evaluated is ``skipped`` - never passed.

The CANDIDATE's own risk is not a rung input that can be skipped. An
``added_risk`` that is None, NaN, non-numeric or zero counts as 0.0 - a pass on
every risk rung - and a negative number is used as given, exactly as
``concentration_reject`` always did, because the Account's entry cycle has
sized the trade before calling. Any other caller (the Ledger, the preview) must
refuse a candidate whose risk is not a positive finite number BEFORE calling
``evaluate``.

The skip rules copy ``concentration_reject`` exactly and differ by rung. Opt-in
rungs (per trade, deployment, both sector rungs) skip when their key is missing
or zero, and deployment also without a usable equity. The three original rungs
(symbol positions, symbol risk, expiry positions) are always evaluated: a zero
cap refuses everything.

Where this RAISES and the old early-return function did not: ``evaluate``
computes every rung before a breach is chosen, so a required cap
(``max_positions_per_symbol``, ``max_risk_per_symbol``,
``max_positions_per_expiry``) that is missing raises ``KeyError``, and one that
is None raises ``TypeError``, REGARDLESS of which rung binds - and a truthy
non-string symbol (on the candidate or on a book row) or a truthy non-string
expiration raises ``AttributeError``. The old ``concentration_reject``
returned at its first breach, so an earlier binding rung could answer before
it ever reached the bad value. Production limits come from ``config_paper``'s
literal constants (pinned by a test in
``options-scanner/tests/test_book_caps_equivalence.py``) and symbols and
expirations are strings or None, so no production call reaches any of these.
"""
import math

from shared.driver_policy import open_risk_dollars

TRADE_RISK_CAP = "TRADE_RISK_CAP"
DEPLOYMENT_CAP = "DEPLOYMENT_CAP"
SYMBOL_POSITION_CAP = "SYMBOL_POSITION_CAP"
SYMBOL_RISK_CAP = "SYMBOL_RISK_CAP"
SECTOR_POSITION_CAP = "SECTOR_POSITION_CAP"
SECTOR_RISK_CAP = "SECTOR_RISK_CAP"
EXPIRY_POSITION_CAP = "EXPIRY_POSITION_CAP"

# The screen and the Ledger: per trade FIRST, because "lower the quantity" is
# the fix a reader can act on immediately (operator decision, 2026-09-15).
DISPLAY_ORDER = (TRADE_RISK_CAP, DEPLOYMENT_CAP, SYMBOL_POSITION_CAP,
                 SYMBOL_RISK_CAP, SECTOR_POSITION_CAP, SECTOR_RISK_CAP,
                 EXPIRY_POSITION_CAP)
# The Account's order, unchanged from ``concentration_reject``. It has no
# per-trade rung: the entry cycle's sizing refuses that as RISK_TOO_HIGH.
# This is concentration_reject's historical early-return order, written out
# as a literal on purpose: it must NOT follow a reorder of DISPLAY_ORDER.
#
# Why this order (carried over from concentration_reject's docstring):
#
# The position cap is reported ahead of the risk cap when both bind: a count
# is the more legible thing to read in a log line, and it is the limit the
# operator set out to enforce.
#
# The BOOK-WIDE deployment cap (gap assessment B3) - total open max loss as a
# fraction of equity - is reported FIRST when it binds: if the book as a whole
# is full, which symbol was asked for is irrelevant, and the broader reason is
# the more useful log line.
#
# The sector rungs (gap assessment B4) are reported after the symbol ones and
# before expiry - see the comment at that rung in ``evaluate``.
ACCOUNT_ORDER = (DEPLOYMENT_CAP, SYMBOL_POSITION_CAP, SYMBOL_RISK_CAP,
                 SECTOR_POSITION_CAP, SECTOR_RISK_CAP, EXPIRY_POSITION_CAP)

# The Paper dialog's own maximum quantity.
QTY_CEILING = 100

# Prefix of the single-symbol bucket an UNMAPPED name gets. No real sector name
# contains ``?``, so the bucket can never collide with one.
UNMAPPED_PREFIX = "?"


def sector_bucket(table, symbol):
    """The sector bucket ``symbol`` is capped in, over a symbol -> sector ``table``.

    THE bucket rule: ``shared.sectors.group_key`` delegates here with the loaded
    ``config/sectors.toml`` table, and the Paper dialog calls it over the table
    the service publishes, so the preview and the click cannot disagree about
    which bucket a symbol is in. Pure - the caller supplies the table.

    Not a string -> ``None``. The key is the stripped, uppercased symbol, and an
    empty one -> ``None`` ("no grouping possible", never a bucket). A mapped key
    whose value is a non-empty string -> that sector; anything else (unmapped,
    a non-dict table, a non-string value) -> ``"?" + key``, a bucket of its own.
    """
    if not isinstance(symbol, str):
        return None
    key = symbol.strip().upper()
    if not key:
        return None
    value = table.get(key) if isinstance(table, dict) else None
    if isinstance(value, str) and value:
        return value
    return UNMAPPED_PREFIX + key


# Shares per option contract - the Ledger's ``paper_trader._CONTRACT_MULT``.
_CONTRACT_MULT = 100


def _real(value):
    """An int or float that is not a bool and is finite - kept in its OWN type,
    because ``booked_risk`` must return exactly what the Ledger books, and an int
    per-share figure books an int total."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def booked_risk(basis, qty):
    """The max loss the Paper LEDGER books for ``qty`` contracts, or None.

    THE risk rounding rule, shared by ``paper_trader`` (which books with it), the
    service's candidate stamp and the Paper dialog preview - so a sub-cent
    per-share figure can never be rounded one way for the preview and another
    for the booking. Measured before this existed: a $1.87504 per-share spread
    stamped as $187.50 a contract previewed four contracts at $750.00 (inside a
    $750 limit) while the Ledger booked $750.02 and refused.

    ``basis`` is ``paper_trader.risk_basis(signal)``:

    * ``{"per_share": x}`` (credit structures) -> ``round(x * qty * 100, 2)``,
      the credit branch's ``round(max_loss_per * quantity * multiplier, 2)``
      with the same operand order, so the float is byte-identical;
    * ``{"per_contract": y}`` (debit structures) -> ``round(y * qty, 2)``, the
      debit branch's ``round(max_loss * quantity, 2)``.

    Anything else - not a dict, neither key or both, a bool, a non-number, a
    non-finite value or result - is None. A zero basis books 0.0; deciding
    whether that is usable risk is the caller's job.
    """
    if not isinstance(basis, dict) or not _real(qty):
        return None
    has_share, has_contract = "per_share" in basis, "per_contract" in basis
    if has_share == has_contract:
        return None
    if has_share:
        x = basis["per_share"]
        if not _real(x):
            return None
        total = round(x * qty * _CONTRACT_MULT, 2)
    else:
        y = basis["per_contract"]
        if not _real(y):
            return None
        total = round(y * qty, 2)
    return total if math.isfinite(total) else None


def _finite(value):
    """A usable number, or 0.0. An unreadable candidate risk counts as zero
    here, so callers that did not size the trade must reject it first (see the
    module docstring)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _key(value):
    return (value or "").strip().upper()


def _count(code, scope, used, cap):
    return {"code": code, "kind": "count", "scope": scope, "used": used,
            "after": used + 1, "cap": cap, "binds": used >= cap, "skipped": None}


def _risk(code, scope, used, added, cap):
    after = used + added
    return {"code": code, "kind": "risk", "scope": scope, "used": used,
            "after": after, "cap": cap, "binds": after > cap, "skipped": None}


def _skip(code, kind, scope, reason):
    return {"code": code, "kind": kind, "scope": scope, "used": None,
            "after": None, "cap": None, "binds": False, "skipped": reason}


def evaluate(book, candidate, added_risk, limits, equity=None):
    """Every rung for opening ``candidate`` into ``book``, in DISPLAY_ORDER.

    ``book`` rows: ``{symbol, expiration, max_loss_total, sector}`` for OPEN
    positions (closed rows tie up no capital and must not count; ``max_loss`` ×
    ``quantity`` is the fallback ``open_risk_dollars`` already understands).
    ``candidate``: ``{symbol, expiration, sector}``.
    ``added_risk``: the candidate's total max loss in dollars (an unusable
    value counts as 0.0 - see the module docstring).

    ``sector`` on book rows and on the candidate must be the canonical
    ``shared.sectors.group_key`` string, matched by exact equality: an unmapped
    symbol's bucket is ``"?SYMBOL"``, and None means unknown - a None candidate
    sector skips both sector rungs, and a None row never counts toward a sector.

    Each rung is a dict with keys ``code``, ``kind`` (``"count"`` or
    ``"risk"``), ``scope`` (the symbol, sector bucket or expiry, or None),
    ``used``, ``after``, ``cap``, ``binds`` and ``skipped`` (a reason string, or
    None). On a skipped rung ``used``, ``after`` and ``cap`` are None and
    ``binds`` is False.
    """
    rows = [p for p in book or () if isinstance(p, dict)]
    cand = candidate if isinstance(candidate, dict) else {}
    added = _finite(added_risk)
    out = []

    per_trade = limits.get("max_risk_per_trade")
    if per_trade:
        out.append(_risk(TRADE_RISK_CAP, None, 0.0, added, per_trade))
    else:
        out.append(_skip(TRADE_RISK_CAP, "risk", None, "no per-trade limit"))

    # Book-wide (gap assessment B3).
    #
    # ⚠ **No equity, or a non-finite one, SKIPS this cap rather than treating it
    # as zero.** A fraction of an unknown cannot be enforced, and a zero
    # denominator would refuse every trade forever — which reads as a broken
    # engine, not as a cap. The cap is likewise opt-in by DATA: a ``limits`` dict
    # without ``max_deployed_risk_pct``, or a zero, skips this rung.
    #
    # ``open_risk_dollars`` for the same reason the symbol sum uses it: it
    # drops a non-finite row instead of poisoning the total, and a NaN total
    # makes every ``>`` False — silently switching the ceiling off.
    pct = limits.get("max_deployed_risk_pct")
    eq = _finite(equity)
    if not pct:
        out.append(_skip(DEPLOYMENT_CAP, "risk", None, "no deployment limit"))
    elif not eq > 0:
        out.append(_skip(DEPLOYMENT_CAP, "risk", None, "no equity figure"))
    else:
        out.append(_risk(DEPLOYMENT_CAP, None, open_risk_dollars(rows), added,
                         pct * eq))

    sym = _key(cand.get("symbol"))
    same_symbol = [p for p in rows if _key(p.get("symbol")) == sym]
    out.append(_count(SYMBOL_POSITION_CAP, sym, len(same_symbol),
                      limits["max_positions_per_symbol"]))
    # ⚠ Summed through ``open_risk_dollars`` rather than a local ``sum(...)``:
    # it drops non-finite rows instead of poisoning the total, and a NaN total
    # makes every ``>`` comparison False -- silently switching the ceiling off
    # while the code still reads like a guard. That is the repo's documented
    # pins-the-bound trap; reusing the one hardened summation beats a tenth copy.
    out.append(_risk(SYMBOL_RISK_CAP, sym, open_risk_dollars(same_symbol), added,
                     limits["max_risk_per_symbol"]))

    # SECTOR (gap assessment B4) - the rung between the per-symbol caps and the
    # book-wide one. Four DIFFERENT semiconductors at the full symbol cap breach
    # nothing above this, which is exactly the correlated book the playbook warns
    # about; measured, the driver's book once held $21,531 across 15 Information
    # Technology positions, 86% of a $25,000 account in one sector.
    #
    # In ACCOUNT_ORDER, the sector rungs are reported AFTER the symbol rungs and
    # BEFORE expiry, deliberately: when both a symbol cap and the sector cap
    # bind, "you already hold three MU" is the actionable sentence - the
    # operator can pick another name - while "tech is full" is the answer only
    # once the symbol has room. A shared expiry is the weaker coincidence of the
    # two, so it stays last.
    #
    # Opt-in by DATA like the deployment cap above: a ``limits`` dict without the
    # keys, or a cap of 0, skips these rungs.
    #
    # The CALLER resolves sectors with ``shared.sectors.group_key``, which gives
    # an unmapped symbol a bucket of its OWN (``"?SYMBOL"``), so an unknown name
    # is still capped against itself and can neither borrow another sector's
    # allowance nor drag unrelated names in. Here None means unknown: a None
    # candidate sector skips both rungs, and a None row is never counted.
    bucket = cand.get("sector")
    same_sector = ([p for p in rows if p.get("sector") == bucket]
                   if bucket is not None else [])
    max_n = limits.get("max_positions_per_sector")
    if not max_n:
        out.append(_skip(SECTOR_POSITION_CAP, "count", bucket, "no sector limit"))
    elif bucket is None:
        out.append(_skip(SECTOR_POSITION_CAP, "count", None, "sector unknown"))
    else:
        out.append(_count(SECTOR_POSITION_CAP, bucket, len(same_sector), max_n))
    # Through ``open_risk_dollars`` for the same reason as the deployment and
    # symbol sums: a NaN row would poison the sum, and a NaN total makes every
    # ``>`` False - switching the ceiling off silently.
    max_risk = limits.get("max_risk_per_sector")
    if not max_risk:
        out.append(_skip(SECTOR_RISK_CAP, "risk", bucket, "no sector limit"))
    elif bucket is None:
        out.append(_skip(SECTOR_RISK_CAP, "risk", None, "sector unknown"))
    else:
        out.append(_risk(SECTOR_RISK_CAP, bucket, open_risk_dollars(same_sector),
                         added, max_risk))

    exp = (cand.get("expiration") or "").strip()
    same_expiry = [p for p in rows if (p.get("expiration") or "").strip() == exp]
    out.append(_count(EXPIRY_POSITION_CAP, exp, len(same_expiry),
                      limits["max_positions_per_expiry"]))
    return out


def first_breach(rungs, order):
    """The first binding rung in ``order``, or None."""
    by_code = {r["code"]: r for r in rungs or ()}
    for code in order:
        r = by_code.get(code)
        if r is not None and r["binds"]:
            return r
    return None


def max_quantity(book, candidate, per_contract, limits, equity=None,
                 ceiling=QTY_CEILING):
    """The largest quantity that clears every rung, or None when the per-contract
    risk is unusable. A binding COUNT rung means 0: adding one position breaks it
    whatever the size."""
    try:
        per = float(per_contract)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(per) and per > 0):
        return None
    best = ceiling
    for r in evaluate(book, candidate, 0.0, limits, equity):
        if r["skipped"]:
            continue
        if r["kind"] == "count":
            if r["binds"]:
                return 0
            continue
        room = r["cap"] - r["used"]
        if room < 0:
            return 0
        n = math.floor(room / per + 1e-9)
        while n > 0 and r["used"] + n * per > r["cap"]:
            n -= 1
        best = min(best, n)
    return max(0, best)


def _money(v):
    return f"${v:,.0f}" if abs(v - round(v)) < 0.005 else f"${v:,.2f}"


def scope_label(scope):
    """A sector bucket for a reader: ``?IONQ`` is an unmapped symbol's own bucket."""
    if isinstance(scope, str) and scope.startswith("?"):
        return f"{scope[1:]}'s own group (no sector on file)"
    return scope or ""


def describe(rung):
    """One plain sentence for a rung.

    Every line reads from the trade's side. A count line that passes states the
    position THIS trade would be ("Position 2 of 3 in ORCL"); a binding count
    line states what the book already holds. Risk lines state the total the
    book would reach with this trade in it.
    """
    if rung.get("skipped"):
        return f"Not checked: {rung['skipped']}"
    code, scope = rung["code"], scope_label(rung.get("scope"))
    used, after, cap = rung["used"], rung["after"], rung["cap"]
    if code == TRADE_RISK_CAP:
        word = "over" if rung["binds"] else "within"
        return f"Risks {_money(after)}, {word} the {_money(cap)} per-trade limit"
    if code == DEPLOYMENT_CAP:
        return (f"Open risk across the book would reach {_money(after)} "
                f"of {_money(cap)}")
    if code == SYMBOL_POSITION_CAP:
        if rung["binds"]:
            return f"{scope} already holds {used} of {cap} positions"
        return f"Position {after} of {cap} in {scope}"
    if code == SYMBOL_RISK_CAP:
        return f"{scope} risk would reach {_money(after)} of {_money(cap)}"
    if code == SECTOR_POSITION_CAP:
        if rung["binds"]:
            return f"{scope} is full ({used} of {cap} positions)"
        return f"Position {after} of {cap} in {scope}"
    if code == SECTOR_RISK_CAP:
        return f"{scope} risk would reach {_money(after)} of {_money(cap)}"
    if code == EXPIRY_POSITION_CAP:
        if rung["binds"]:
            return f"{used} of {cap} positions across the book already expire {scope}"
        return f"Position {after} of {cap} expiring {scope} across the book"
    return "Over a risk limit" if rung.get("binds") else "Within a risk limit"
