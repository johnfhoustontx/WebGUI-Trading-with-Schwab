"""Every price, strike, ratio, percentage and dollar total prints two decimals.

The defect (2026-10-04): a symbol whose price was a whole number printed as
``450``, not ``450.00``. Two causes, and a guard for each:

* ``:g`` in a page's format string drops trailing zeros. A page now says what a
  number IS through ``pages/fmt.py`` (``price``, ``strike``, ``ratio``, ``pct``,
  ``money``) and uses ``fmt.plain`` for the things that are none of those (a
  count, a day count, a score). So ``:g`` appears in ``fmt.py`` and nowhere else.
* A table cell holding a raw number is printed by the browser as it is. The
  tables below name their price columns in ``kit.table(decimals=...)``.

Design: ``docs/plans/2026-10-04-two-decimal-display-design.md``.
"""
import pathlib
import re

from pages import fmt
from pages import ui_kit as kit
from pages.options import matrix, scanner

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"

# ``{x:g}``, ``{x:+g}``, ``{x:,g}`` - the general format, in an f-string or a
# ``str.format`` template.
_GENERAL = re.compile(r":[+,]?g\}")


def _page_sources():
    files = sorted(p for p in PAGES.rglob("*.py") if p.name != "fmt.py")
    assert len(files) > 80, "found too few page modules - the scan is vacuous"
    return files


def test_no_page_formats_a_number_with_the_general_format():
    """``:g`` prints 450.0 as ``450``. A price goes through ``fmt.price`` or
    ``fmt.strike``; a number that really is a count goes through ``fmt.plain``,
    which is the one place the format is allowed."""
    hits = []
    for path in _page_sources():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _GENERAL.search(line):
                hits.append(f"{path.relative_to(PAGES)}:{n}: {line.strip()[:90]}")
    assert not hits, "use pages/fmt.py instead of ':g':\n" + "\n".join(hits)


def test_the_guard_can_see_the_format_it_bans():
    """A regex that matched nothing would pass on any source."""
    assert _GENERAL.search('f"spot {spot:g}"')
    assert _GENERAL.search('f"{value:+g}%"')
    assert not _GENERAL.search('f"{value:.2f}"')
    assert _GENERAL.search((PAGES / "fmt.py").read_text(encoding="utf-8")), \
        "fmt.plain is where the general format lives"


def test_the_one_constant_is_two():
    assert fmt.DECIMALS == 2
    assert fmt.price(450) == "450.00"
    assert fmt.strike(450) == "450.00"


def test_the_opportunity_board_prints_its_prices_to_two_places():
    """The Matrix ``Price`` column was the reported case: the row holds the
    number, so the browser printed ``450``."""
    cols = kit.table_columns(matrix.matrix_columns(), decimals=matrix._DECIMALS)
    by_name = {c["name"]: c for c in cols}
    for name in ("spot", "pc_ratio", "net_prem_m"):
        assert by_name[name][":format"] == kit.DECIMAL_FORMAT
    # Counts and the score stay whole.
    for name in ("n_signals", "n_alerts", "hotness"):
        assert ":format" not in by_name[name]
    # The row still carries the NUMBER, so the column sorts numerically.
    row = matrix.matrix_rows({"rows": [{"symbol": "SPY", "spot": 450}]})[0]
    assert row["spot"] == 450


def test_the_day_percent_cell_prints_two_places_itself():
    """Its slot colours the cell, so it formats the number rather than taking
    the column format."""
    assert "toFixed(2) + '%'" in matrix._DAYPCT_SLOT


def test_a_scanner_strike_pair_prints_two_places():
    row = scanner.signal_rows([{"symbol": "SPY", "type": "PCS",
                                "short_strike": 450, "long_strike": 445.5}])[0]
    assert row["strikes"] == "450.00/445.50"
