"""The Paper ACCOUNT's risk limits, as the manuals state them, match config_paper.

The sibling of ``test_ledger_limits_documented.py``. The two books' dollar caps
have been apart since 2026-11-21 (the Account's raised to $1,500 / $1,500 /
$3,000, the Ledger's left at $750 / $750 / $1,500), and the Account's are quoted
in two places nothing else checks: the Reference Guide's "What the engine will
refuse" table and the in-app hover guide's ``/options/portfolio`` bullet "Why a
good signal sometimes never opens".

Read as TEXT, like the sibling, and each value is looked for on the row (or
comma-separated clause) whose label names its limit, so one limit's figure can
never stand in for another's.
"""
import pathlib
import re

import config_paper

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE_HELP = ROOT / "webgui" / "page_help.py"
REFERENCE_GUIDE = ROOT / "docs" / "manuals" / "reference-guide" / "reference-guide.md"


def _read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _dollars(value):
    return f"${value:,.0f}"


def _states(row, value):
    """``value`` appears in ``row`` as itself, not as part of a larger number."""
    if value.startswith("$"):
        return re.search(re.escape(value) + r"(?![\d,])", row) is not None
    return re.search(r"(?<![\d,.$])" + re.escape(value) + r"(?![\d,.%])", row) is not None


def _one(rows, label):
    matches = [r for r in rows if label in r.lower()]
    assert len(matches) == 1, f"expected one row labelled {label!r}, found {matches}"
    return matches[0]


def _reference_rows():
    text = _read(REFERENCE_GUIDE)
    start = text.index("### What the engine will refuse")
    end = text.index("\n### ", start + 1)
    return [line for line in text[start:end].split("\n") if line.startswith("|")]


def test_reference_guide_states_the_accounts_limits():
    rows = _reference_rows()
    for label, value in [
        ("risk per trade", _dollars(config_paper.MAX_RISK_PER_TRADE)),
        ("positions in one symbol", str(config_paper.MAX_POSITIONS_PER_SYMBOL)),
        ("risk in one symbol", _dollars(config_paper.MAX_RISK_PER_SYMBOL)),
        ("positions in one sector", str(config_paper.MAX_POSITIONS_PER_SECTOR)),
        ("risk in one sector", _dollars(config_paper.MAX_RISK_PER_SECTOR)),
        ("positions in one expiry", str(config_paper.MAX_POSITIONS_PER_EXPIRY)),
        ("open risk across the book", f"{config_paper.MAX_DEPLOYED_RISK_PCT:.0%}"),
        ("session drawdown", _dollars(config_paper.MAX_SESSION_DRAWDOWN)),
    ]:
        row = _one(rows, label)
        assert _states(row, value), f"the {label!r} row does not state {value}: {row}"


def _help_clauses():
    """The ``/options/portfolio`` entry's "Why a good signal" bullet, by clause."""
    text = _read(PAGE_HELP)
    start = text.index('"/options/portfolio": """') + len('"/options/portfolio": """')
    entry = text[start:text.index('"""', start)]
    bullet = entry.index("- **Why a good signal sometimes never opens.**")
    end = entry.find("\n- ", bullet + 1)
    # A clause starts at a bold figure ("**5 positions and $3,000** in one sector"):
    # split before each, never on the comma inside "$1,500".
    return re.split(r",\s+(?=\*\*)", entry[bullet:end if end != -1 else len(entry)])


def test_help_states_the_accounts_symbol_and_sector_limits():
    clauses = _help_clauses()
    for label, values in [
        ("underlying", (str(config_paper.MAX_POSITIONS_PER_SYMBOL),
                        _dollars(config_paper.MAX_RISK_PER_SYMBOL))),
        ("in one sector", (str(config_paper.MAX_POSITIONS_PER_SECTOR),
                           _dollars(config_paper.MAX_RISK_PER_SECTOR))),
    ]:
        clause = _one(clauses, label)
        for value in values:
            assert _states(clause, value), f"the {label!r} clause does not state {value}: {clause}"
