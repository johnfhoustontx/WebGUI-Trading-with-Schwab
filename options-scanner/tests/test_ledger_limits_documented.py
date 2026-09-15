"""The Paper Ledger's risk limits, as the manuals state them, match config_paper.

If a limit moves, this test fails until the manuals follow. The in-app hover
guide (``webgui/page_help.py``, the ``/options/paper`` block) and the User Guide
(its "Risk limits on new trades" section) both quote the numbers to the reader,
and nothing else fails when a quoted number goes stale.

Both files are read as TEXT - no webgui import - so this suite stays free of
Tier-1 dependencies. Only the passage that states the limits is searched in
each - the help entry's limits bullet and the guide's limits table - so a number
that happens to appear elsewhere (an example message, say) cannot satisfy it.
"""
import pathlib
import re

import config_paper

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE_HELP = ROOT / "webgui" / "page_help.py"
USER_GUIDE = ROOT / "docs" / "manuals" / "user-guide" / "user-guide.md"


def _help_block():
    """The limits bullet of the ``/options/paper`` entry of page_help.HELP_MD,
    found by its dict key and then by the bullet's opening words."""
    text = PAGE_HELP.read_text(encoding="utf-8")
    start = text.index('"/options/paper": """') + len('"/options/paper": """')
    entry = text[start:text.index('"""', start)]
    bullet = entry.index("- **New trades must fit")
    end = entry.find("\n- ", bullet + 1)
    return entry[bullet:end if end != -1 else len(entry)]


def _guide_block():
    """The limits TABLE of the User Guide's "Risk limits on new trades" section.

    The section's prose also quotes example refusal messages ("risks $900, over
    the $750 per-trade limit"), whose invented figures could otherwise satisfy a
    check for a limit that had moved - so only the table rows are searched."""
    text = USER_GUIDE.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("**Risk limits on new trades.**")
    section = text[start:text.index("**Automatic exits", start)]
    return "\n".join(line for line in section.split("\n") if line.startswith("|"))


def _dollars(value):
    return f"${value:,.0f}"


def _expected_amounts():
    return [
        _dollars(config_paper.LEDGER_MAX_RISK_PER_TRADE),
        _dollars(config_paper.MAX_RISK_PER_SYMBOL),
        _dollars(config_paper.MAX_RISK_PER_SECTOR),
        f"{config_paper.MAX_DEPLOYED_RISK_PCT:.0%}",
        _dollars(config_paper.STARTING_BALANCE),
    ]


def _count_near(block, count, phrase, window=40):
    """True when ``count`` appears as a whole number within ``window`` characters
    of ``phrase`` - before it ("3 positions and $750 per symbol") or after it
    ("| Per symbol | **3** open positions")."""
    low = block.lower()
    for m in re.finditer(re.escape(phrase), low):
        near = low[max(0, m.start() - window):m.end() + window]
        if re.search(rf"(?<![\d,$]){count}(?![\d,%])", near):
            return True
    return False


def _counts():
    return [
        (config_paper.MAX_POSITIONS_PER_SYMBOL, "per symbol"),
        (config_paper.MAX_POSITIONS_PER_SECTOR, "per sector"),
        (config_paper.MAX_POSITIONS_PER_EXPIRY, "expiration"),
    ]


def test_help_block_quotes_the_current_amounts():
    block = _help_block()
    for amount in _expected_amounts():
        assert amount in block, f"page_help /options/paper does not state {amount}"


def test_help_block_quotes_the_current_position_counts():
    block = _help_block()
    for count, phrase in _counts():
        assert _count_near(block, count, phrase), (
            f"page_help /options/paper does not state {count} near {phrase!r}")


def test_user_guide_quotes_the_current_amounts():
    block = _guide_block()
    for amount in _expected_amounts():
        assert amount in block, f"User Guide risk limits do not state {amount}"


def test_user_guide_quotes_the_current_position_counts():
    block = _guide_block()
    for count, phrase in _counts():
        assert _count_near(block, count, phrase), (
            f"User Guide risk limits do not state {count} near {phrase!r}")
