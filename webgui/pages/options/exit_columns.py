"""The Stop and Target columns the three paper books share: the Paper Ledger,
Captured Signals and the paper Account.

The service puts the levels on each OPEN row (``services/options_svc/
exit_levels.py``, read off the rule engine itself): ``stop_level`` and
``target_level`` are POSITION P&L in dollars, on the same basis as the row's P&L
column, so a trade closes when its P&L reaches either. ``stop_kind`` says which
stop is in force; it moves as the trade gains (break-even, then the trail).

A dash means no level: a closed row, a Ledger credit trade with no trail armed
(it has no loss stop), or a position that rides past its target. The time and
delta stops cannot be shown here: they fire on time and delta, not on P&L.
"""
from pages.fmt import round_or_none as _round

#: Right-aligned and printed to two places.
NUMERIC = ("stop_level", "target_level")
DECIMALS = NUMERIC

#: ``stop_kind`` in the reader's words.
KIND_WORDS = {
    "money": "Loss stop",
    "break-even": "Break-even",
    "trail": "Trailing",
    "debit": "Loss stop",
    "idea": "Idea's stop",
}


#: A level is a P&L, so it carries its sign: ``+36.00`` is a stop that locks a
#: profit, ``-200.00`` one that takes a loss. Two places, a dash for none.
SIGNED_FORMAT = ("(val) => (val === null || val === undefined || val === '') ? '—' "
                 ": (val > 0 ? '+' : '') + Number(val).toFixed(2)")


def columns():
    """The three columns, in reading order: the stop, what it is, the target."""
    cols = [{"name": n, "label": lbl, "field": n, "sortable": True, "align": "left"}
            for n, lbl in (("stop_level", "Stop"), ("stop_kind", "Stop rule"),
                           ("target_level", "Target"))]
    for c in cols:
        if c["name"] in NUMERIC:
            c[":format"] = SIGNED_FORMAT
    return cols


def cells(src) -> dict:
    """The three cells for one row of the service's view. PURE."""
    src = src or {}
    return {"stop_level": _round(src.get("stop_level")),
            "stop_kind": KIND_WORDS.get(src.get("stop_kind"), ""),
            "target_level": _round(src.get("target_level"))}
