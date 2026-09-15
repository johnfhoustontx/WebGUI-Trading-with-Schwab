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


def _unavailable():
    return {"available": False, "lines": [], "breach": None, "block_text": "",
            "max_quantity": None, "unavailable_text": UNAVAILABLE}


def preview(signal, caps, qty=1):
    """``{available, lines, breach, block_text, max_quantity, unavailable_text}``."""
    sig = signal if isinstance(signal, dict) else {}
    per = num(sig.get("ledger_risk_per_contract"))
    if (not isinstance(caps, dict) or not isinstance(caps.get("limits"), dict)
            or not caps.get("limits")
            or not isinstance(caps.get("sectors"), dict) or per is None or per <= 0):
        return _unavailable()
    book = caps.get("open")
    if book is None:
        book = []
    if not isinstance(book, (list, tuple)):
        return _unavailable()
    try:
        q = max(1, int(qty or 1))
    except (TypeError, ValueError, OverflowError):
        return _unavailable()
    raw_symbol = sig.get("symbol")
    symbol = raw_symbol.strip().upper() if isinstance(raw_symbol, str) else ""
    candidate = {"symbol": symbol, "expiration": sig.get("expiration"),
                 "sector": book_caps.sector_bucket(caps.get("sectors"), symbol)}
    limits, equity = caps["limits"], caps.get("equity")
    try:
        rungs = book_caps.evaluate(book, candidate, per * q, limits, equity)
        breach = book_caps.first_breach(rungs, book_caps.DISPLAY_ORDER)
        lines = [{"code": r["code"], "label": _LABELS[r["code"]],
                  "text": book_caps.describe(r),
                  "tone": "muted" if r["skipped"] else ("neg" if r["binds"] else "pos")}
                 for r in rungs]
        return {"available": True, "lines": lines, "breach": breach,
                "block_text": book_caps.describe(breach) if breach else "",
                "max_quantity": book_caps.max_quantity(book, candidate, per,
                                                       limits, equity),
                "unavailable_text": ""}
    except (KeyError, TypeError, AttributeError, ValueError):
        return _unavailable()
