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
cap refuses everything and a missing key raises ``KeyError``. (One deliberate
difference: the old function reached a missing required key only AFTER the
deployment rung, so a binding deployment cap masked the ``KeyError``. Here it
always raises. Every production caller passes complete limits.)
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
ACCOUNT_ORDER = (DEPLOYMENT_CAP, SYMBOL_POSITION_CAP, SYMBOL_RISK_CAP,
                 SECTOR_POSITION_CAP, SECTOR_RISK_CAP, EXPIRY_POSITION_CAP)

# The Paper dialog's own maximum quantity.
QTY_CEILING = 100


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
    positions (``max_loss`` × ``quantity`` is the fallback ``open_risk_dollars``
    already understands). ``candidate``: ``{symbol, expiration, sector}``.
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
    out.append(_risk(SYMBOL_RISK_CAP, sym, open_risk_dollars(same_symbol), added,
                     limits["max_risk_per_symbol"]))

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
        return f"{scope[1:]} (no sector on file)"
    return scope or ""


def describe(rung):
    """One plain sentence for a rung."""
    if rung.get("skipped"):
        return f"Not checked: {rung['skipped']}"
    code, scope = rung["code"], scope_label(rung.get("scope"))
    used, after, cap = rung["used"], rung["after"], rung["cap"]
    if code == TRADE_RISK_CAP:
        word = "over" if rung["binds"] else "within"
        return f"Risks {_money(after)}, {word} the {_money(cap)} per-trade limit"
    if code == DEPLOYMENT_CAP:
        return f"Open risk would reach {_money(after)} of {_money(cap)}"
    if code == SYMBOL_POSITION_CAP:
        return f"Already {used} of {cap} positions in {scope}"
    if code == SYMBOL_RISK_CAP:
        return f"{scope} risk would reach {_money(after)} of {_money(cap)}"
    if code == SECTOR_POSITION_CAP:
        if rung["binds"]:
            return f"{scope} is full ({used} of {cap} positions)"
        return f"{scope}: {used} of {cap} positions"
    if code == SECTOR_RISK_CAP:
        return f"{scope} risk would reach {_money(after)} of {_money(cap)}"
    if code == EXPIRY_POSITION_CAP:
        return f"Already {used} of {cap} positions expiring {scope}"
    return code
