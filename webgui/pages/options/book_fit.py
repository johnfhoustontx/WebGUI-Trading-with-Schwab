"""The Paper dialog's book-fit preview - PURE (no UI framework, no bus).

Evaluates the SAME rungs the service enforces, through ``shared.book_caps``
(on the Tier-1 allow-list since 2026-09-15). The service re-checks on every
click, because the book can change between opening the dialog and pressing
Create, so this is a preview and never the decision.

A caps view this module cannot read - no view, no sector table, a malformed
limits mapping or book, an unusable quantity - is "no preview", never a raise
and never a green: ``book_caps.evaluate`` raises on a missing or None required
cap and on a non-string symbol, and a page callback that raises is a dialog
that does nothing.
"""
from shared import book_caps

from ..fmt import num

UNAVAILABLE = ("Can't preview this trade here — the paper ledger still checks "
               "every cap when you create it.")

_SHORT = {book_caps.TRADE_RISK_CAP: "over the per-trade limit",
          book_caps.DEPLOYMENT_CAP: "book fully deployed",
          book_caps.SYMBOL_POSITION_CAP: "symbol full",
          book_caps.SYMBOL_RISK_CAP: "symbol risk full",
          book_caps.SECTOR_POSITION_CAP: "sector full",
          book_caps.SECTOR_RISK_CAP: "sector risk full",
          book_caps.EXPIRY_POSITION_CAP: "expiry full"}

_LABELS = {book_caps.TRADE_RISK_CAP: "Per trade",
           book_caps.DEPLOYMENT_CAP: "Deployment",
           book_caps.SYMBOL_POSITION_CAP: "Symbol",
           book_caps.SYMBOL_RISK_CAP: "Symbol risk",
           book_caps.SECTOR_POSITION_CAP: "Sector",
           book_caps.SECTOR_RISK_CAP: "Sector risk",
           book_caps.EXPIRY_POSITION_CAP: "Expiry"}


def short_reason(rung):
    """A few words for the checklist chip. The per-trade wording names the
    rung's own cap, never a hard-coded figure: the Ledger's limit is config."""
    rung = rung or {}
    cap = num(rung.get("cap"))
    if rung.get("code") == book_caps.TRADE_RISK_CAP and cap is not None:
        return f"over ${cap:,.0f} per trade"
    return _SHORT.get(rung.get("code"), "blocked")


BAD_QUANTITY = "Quantity must be a whole number of at least 1."


def whole_quantity(qty):
    """``qty`` as an int >= 1, or ``None`` - the Ledger's own quantity rule.

    A line-for-line mirror of the service's ``compute._whole_quantity`` (Tier 1
    cannot import it): an int that is not a bool, or a float/str holding a whole
    number. None, 2.9, "2.5", 0, a negative, NaN or unparseable text is ``None``.
    ``float.is_integer`` is False for NaN and both infinities, so it carries the
    service's ``math.isfinite`` check without a ``math`` import. The service
    answers every ``None`` here with an error, never a cap check, so the preview
    must not evaluate one either; the options service's agreement test pins the
    parity.
    """
    if isinstance(qty, bool):
        return None
    if isinstance(qty, int):
        n = qty
    elif isinstance(qty, float):
        if not qty.is_integer():
            return None
        n = int(qty)
    elif isinstance(qty, str):
        text = qty.strip()
        try:
            n = int(text)
        except ValueError:
            try:
                f = float(text)
            except ValueError:
                return None
            if not f.is_integer():
                return None
            n = int(f)
    else:
        return None
    return n if n >= 1 else None


def _max_quantity(book, candidate, basis, added, q, limits, equity):
    """The Ledger's own suggested quantity rule (``compute.create_paper_trade``'s
    refusal branch), step for step: the per-contract figure is this quantity's
    booked total over the quantity, ``book_caps.max_quantity`` proposes, and the
    proposal steps down until the total the Ledger would BOOK at that size clears
    every rung."""
    n = book_caps.max_quantity(book, candidate, added / q, limits, equity)
    while n and n > 0 and book_caps.first_breach(
            book_caps.evaluate(book, candidate, book_caps.booked_risk(basis, n),
                               limits, equity),
            book_caps.DISPLAY_ORDER) is not None:
        n -= 1
    return n


def _unavailable(text=UNAVAILABLE):
    return {"available": False, "lines": [], "breach": None, "block_text": "",
            "max_quantity": None, "unavailable_text": text}


def preview(signal, caps, qty=1):
    """``{available, lines, breach, block_text, max_quantity, unavailable_text}``."""
    # Quantity FIRST, as the service checks it first: a bad quantity is the
    # reader's to fix whatever state the caps view is in.
    q = whole_quantity(qty)
    if q is None:
        return _unavailable(BAD_QUANTITY)
    sig = signal if isinstance(signal, dict) else {}
    # The Ledger's own booking basis, never the cent-rounded per-contract figure:
    # every quantity's risk is ``booked_risk(basis, q)``, exactly what the Ledger
    # will book (a $1.87504 per-share spread is $750.02 at four, not 4 x $187.50).
    basis = sig.get("ledger_risk_basis")
    one = book_caps.booked_risk(basis, 1)
    if (not isinstance(caps, dict) or not isinstance(caps.get("limits"), dict)
            or not caps.get("limits")
            or not isinstance(caps.get("sectors"), dict) or one is None or one <= 0):
        return _unavailable()
    book = caps.get("open")
    if book is None:
        book = []
    if not isinstance(book, (list, tuple)):
        return _unavailable()
    raw_symbol = sig.get("symbol")
    symbol = raw_symbol.strip().upper() if isinstance(raw_symbol, str) else ""
    candidate = {"symbol": symbol, "expiration": sig.get("expiration"),
                 "sector": book_caps.sector_bucket(caps.get("sectors"), symbol)}
    limits, equity = caps["limits"], caps.get("equity")
    try:
        added = book_caps.booked_risk(basis, q)
        if added is None or added <= 0:
            return _unavailable()
        rungs = book_caps.evaluate(book, candidate, added, limits, equity)
        breach = book_caps.first_breach(rungs, book_caps.DISPLAY_ORDER)
        lines = [{"code": r["code"], "label": _LABELS[r["code"]],
                  "text": book_caps.describe(r),
                  "tone": "muted" if r["skipped"] else ("neg" if r["binds"] else "pos")}
                 for r in rungs]
        return {"available": True, "lines": lines, "breach": breach,
                "block_text": book_caps.describe(breach) if breach else "",
                "max_quantity": _max_quantity(book, candidate, basis, added, q,
                                              limits, equity),
                "unavailable_text": ""}
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError):
        return _unavailable()
