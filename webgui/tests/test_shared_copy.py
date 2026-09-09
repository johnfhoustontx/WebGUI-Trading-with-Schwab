"""The shared user-facing sentences in ``pages/copy.py``, and the tree-wide
invariants that keep them shared.

Six of the seven per-page passes in this campaign found the SAME sentence
restated with different words on a different screen. These tests are what stops
the seventh: they read the source of every page module and fail on a
reintroduced literal, which no per-page test can do.
"""
import pathlib
import re

from pages import copy as shared_copy

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def _page_sources():
    for path in sorted(PAGES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8")


def test_the_shared_sentences_name_no_internal_service():
    """Off-hours these are the most-read text in the app, and naming a service
    makes a closed market read as a fault worth chasing."""
    for name in ("WAITING_OPTIONS", "WAITING_SENTIMENT", "WAITING_MARKET"):
        line = getattr(shared_copy, name)
        assert "service" not in line.lower(), name
        assert "hasn't published" in line, name


def test_the_three_sentences_are_distinct():
    """One per domain. A shared sentence that collapsed them would stop a
    reader telling which feed is cold."""
    lines = {shared_copy.WAITING_OPTIONS, shared_copy.WAITING_SENTIMENT,
             shared_copy.WAITING_MARKET}
    assert len(lines) == 3


def test_no_page_restates_a_waiting_line_as_a_literal():
    """The guard the per-page tests cannot be.

    ``pages/copy.py`` itself is skipped — it defines them, and its docstring
    quotes the superseded wording on purpose."""
    pattern = re.compile(r'"[^"]*[Ww]aiting for (the )?\w+ service')
    offenders = []
    for path, src in _page_sources():
        if path.name == "copy.py":
            continue
        if pattern.search(src):
            offenders.append(path.name)
    assert not offenders, offenders


def test_no_page_reports_that_a_command_was_merely_requested():
    """"X requested" is honest about the enqueue and useless to a reader: it
    says nothing about what to expect or how long. Every such toast now names
    what is happening and what confirms it."""
    pattern = re.compile(r'notify\(\s*f?"[^"]*requested', re.I)
    offenders = []
    for path, src in _page_sources():
        if pattern.search(src):
            offenders.append(path.name)
    assert not offenders, offenders


# ── the tree-wide label vocabulary ───────────────────────────────────────────
def test_no_page_still_abbreviates_strategy_or_expiry_in_a_label():
    """Two of the campaign's most-repeated shortenings, guarded at the source.

    ``/desk`` is the documented exception: its Positions grid has measured
    per-string ``minmax()`` floors and those two tracks are LABEL-bound, so the
    words would clip the panel at the 1920px it is read at. It is excluded by
    name rather than by pattern, so a new page cannot inherit the exemption."""
    pattern = re.compile(r'"label":\s*"(Strat|Exp)"|,\s*"(Strat|Exp)"\)')
    offenders = []
    for path, src in _page_sources():
        if path.name == "desk.py":
            continue
        if pattern.search(src):
            offenders.append(path.name)
    assert not offenders, offenders


def test_the_leg_editor_keeps_Qty_and_that_is_a_width_deferral():
    """The other documented exception, and it gets a test for the same reason
    ``/desk``'s does: a comment saying "this is deliberate" is the kind nobody
    reads before "fixing" the inconsistency.

    ``Qty`` sits in a ``w-16`` (64px) track in a dense multi-leg widget mounted
    by the Calculator, the Simulator and Rescue. "Contracts" does not fit, and
    widening the track reflows a shared component on three pages."""
    src = (PAGES / "options" / "leg_editor.py").read_text(encoding="utf-8")
    assert '"Qty"' in src
    assert "w-16" in src


def test_the_equity_book_counts_SHARES_not_contracts():
    """``/portfolio`` is the equity portfolio. Every options page in this
    campaign renamed ``Qty`` to "Contracts"; doing that here would have been
    consistency at the cost of being wrong."""
    from pages import portfolio
    labels = {c["label"] for c in portfolio.HOLDINGS_COLS}
    assert "Shares" in labels
    assert "Contracts" not in labels
