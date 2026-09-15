"""The Paper Ledger's risk limits, as the manuals state them, match config_paper.

If a limit moves, this test fails until the manuals follow. Three documents
quote the numbers to the reader, and nothing else fails when a quoted number
goes stale: the in-app hover guide (``webgui/page_help.py``, the
``/options/paper`` entry), the User Guide and the Reference Guide (each one's
"Risk limits on new trades" table).

All three are read as TEXT - no webgui import - so this suite stays free of
Tier-1 dependencies. Only the passage that states the limits is searched - the
help entry's limits bullet, each guide's limits table - and each value is looked
for on the ROW (a table row, or a ";"-separated clause of the bullet) whose label
names that limit. So an example message ("risks $900, over the $750 per-trade
limit") cannot satisfy a check, and neither can one limit's value standing in for
another's (a $1,500 per-trade limit is not satisfied by the $1,500 sector row).
"""
import pathlib
import re

import config_paper

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE_HELP = ROOT / "webgui" / "page_help.py"
USER_GUIDE = ROOT / "docs" / "manuals" / "user-guide" / "user-guide.md"
REFERENCE_GUIDE = ROOT / "docs" / "manuals" / "reference-guide" / "reference-guide.md"


def _read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _table_rows(section):
    return [line for line in section.split("\n") if line.startswith("|")]


def _help_rows():
    """The ``/options/paper`` entry's limits bullet, split into its clauses."""
    text = _read(PAGE_HELP)
    start = text.index('"/options/paper": """') + len('"/options/paper": """')
    entry = text[start:text.index('"""', start)]
    bullet = entry.index("- **New trades must fit")
    end = entry.find("\n- ", bullet + 1)
    return entry[bullet:end if end != -1 else len(entry)].split(";")


def _user_guide_rows():
    """The User Guide's "Risk limits on new trades" table rows."""
    text = _read(USER_GUIDE)
    start = text.index("**Risk limits on new trades.**")
    return _table_rows(text[start:text.index("**Automatic exits", start)])


def _reference_guide_rows():
    """The Reference Guide's "### Risk limits on new trades" table rows."""
    text = _read(REFERENCE_GUIDE)
    start = text.index("### Risk limits on new trades")
    end = text.index("\n### ", start + 1)
    return _table_rows(text[start:end])


DOCUMENTS = {
    "page_help /options/paper": _help_rows,
    "User Guide": _user_guide_rows,
    "Reference Guide": _reference_guide_rows,
}


def _dollars(value):
    return f"${value:,.0f}"


def _expectations():
    """``(row label, value text)`` pairs: the label picks the row, the value must
    be on it. A count is a bare whole number; money and percent are as printed."""
    return [
        ("per trade", _dollars(config_paper.LEDGER_MAX_RISK_PER_TRADE)),
        ("per symbol", str(config_paper.MAX_POSITIONS_PER_SYMBOL)),
        ("per symbol", _dollars(config_paper.MAX_RISK_PER_SYMBOL)),
        ("per sector", str(config_paper.MAX_POSITIONS_PER_SECTOR)),
        ("per sector", _dollars(config_paper.MAX_RISK_PER_SECTOR)),
        ("expiration", str(config_paper.MAX_POSITIONS_PER_EXPIRY)),
        ("of equity", f"{config_paper.MAX_DEPLOYED_RISK_PCT:.0%}"),
        ("of equity", _dollars(config_paper.STARTING_BALANCE)),
    ]


def _row_for(rows, label):
    matches = [r for r in rows if label in r.lower()]
    assert len(matches) == 1, f"expected one row labelled {label!r}, found {matches}"
    return matches[0]


def _states(row, value):
    """``value`` appears in ``row`` as itself, not as part of a larger number."""
    if value.startswith("$"):
        pattern = re.escape(value) + r"(?![\d,])"
    else:
        pattern = r"(?<![\d,.$])" + re.escape(value) + r"(?![\d,.%])"
        if value.endswith("%"):
            pattern = r"(?<![\d,.$])" + re.escape(value)
    return re.search(pattern, row) is not None


def _check(name):
    rows = DOCUMENTS[name]()
    for label, value in _expectations():
        row = _row_for(rows, label)
        assert _states(row, value), f"{name}: the {label!r} row does not state {value}: {row}"


def test_help_states_the_current_limits():
    _check("page_help /options/paper")


def test_user_guide_states_the_current_limits():
    _check("User Guide")


def test_reference_guide_states_the_current_limits():
    _check("Reference Guide")
