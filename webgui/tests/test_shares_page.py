"""The Shares page's PURE builders — /options/shares, a second reader of
``cache:options:paper_account``.

Three things this file exists to prevent:

1. **A number nobody read.** The published paper-account view carries no live
   equity quote, so Mark and Unrealized have no source. Rendering cost basis in
   the Mark column, or a 0.00 unrealized, would invent a reading — and on a page
   whose entire job is "what do I own and what is it worth", that is the most
   expensive lie available.

2. **Four empty states, not one.** A cold feed, a book that was never opened, a
   payload written before the inventory field existed, and an account that
   genuinely holds no shares are four different facts. Collapsing any of them
   into the shared cold-feed sentence reports a healthy account as an outage —
   the distinction ``pages/copy.py``'s own docstring is about.

3. **Provenance.** A lot that arrived by assignment and one entered by hand are
   not the same fact, and a reader deciding whether to write a call against the
   shares needs to know which it is.

⚠ VACUITY: ``lot_rows`` returning ``[]`` for input it does not understand would
satisfy every ``all(...)`` / ``not any(...)`` assertion trivially, so each test
asserts the row COUNT alongside the content.
"""
from pages import copy as _copy
from pages import fmt as _fmt
from pages.options import shares


# Two lots of one symbol plus a second symbol — the multi-lot case is not an
# edge here: one assignment per expiry is exactly how the inventory accumulates.
_ASSIGNED = {"lot_id": 1, "symbol": "AAPL", "shares": 100, "cost_basis": 195.0,
             "opened_ts": "2026-08-21T15:00:03", "source": "assignment",
             "source_position_id": 7, "status": "OPEN"}
_BOUGHT = {"lot_id": 2, "symbol": "MSFT", "shares": 250, "cost_basis": 400.0,
           "opened_ts": "2026-09-01T10:12:00", "source": "manual",
           "source_position_id": None, "status": "OPEN"}

# An open covered call on AAPL, and a spread on MSFT that must NOT be mistaken
# for one.
_COVERING = {"position_id": 31, "symbol": "AAPL", "strategy": "COVERED_CALL",
             "short_strike": 210.0, "long_strike": None, "call_short": None,
             "call_long": None, "expiration": "2026-10-16", "quantity": 1}
_UNRELATED = {"position_id": 32, "symbol": "MSFT", "strategy": "CCS",
              "short_strike": 420.0, "long_strike": 425.0,
              "expiration": "2026-10-16", "quantity": 2}


# ── the four empty states ───────────────────────────────────────────────────

def test_a_cold_feed_says_nothing_has_published():
    """No payload at all — the options feed has never written the account view."""
    assert shares.status_text(None) == _copy.WAITING_OPTIONS
    assert shares.status_text({}) == _copy.WAITING_OPTIONS


def test_an_account_holding_no_shares_is_not_the_cold_feed_line():
    """The book is open and empty. Saying "hasn't published" would report a
    perfectly healthy account as an outage."""
    text = shares.status_text({"has_account": True, "lots": []})
    assert text != _copy.WAITING_OPTIONS
    assert "no shares" in text.lower()


def test_no_account_yet_is_its_own_sentence():
    """Never opened is not "opened and empty" — the fix for one is not the other."""
    text = shares.status_text({"has_account": False, "lots": []})
    assert text != _copy.WAITING_OPTIONS
    assert text != shares.status_text({"has_account": True, "lots": []})
    assert "account" in text.lower()


def test_a_payload_written_before_the_field_existed_is_not_read_as_empty():
    """``lots`` absent means the inventory was never published, which is NOT the
    same fact as an account holding nothing — and claiming the latter would be
    printing a zero nobody read."""
    text = shares.status_text({"has_account": True})
    assert text != shares.status_text({"has_account": True, "lots": []})
    assert text != _copy.WAITING_OPTIONS


def test_the_summary_counts_lots_shares_and_cost():
    text = shares.status_text({"has_account": True, "lots": [_ASSIGNED, _BOUGHT]})
    assert "2 lots" in text
    # 100 + 250 shares, and 100*195 + 250*400 = 119,500 at cost.
    assert "350" in text
    assert "119,500" in text


def test_one_lot_is_singular():
    text = shares.status_text({"has_account": True, "lots": [_ASSIGNED]})
    assert "1 lot " in text or text.startswith("1 lot")
    assert "1 lots" not in text


# ── provenance ──────────────────────────────────────────────────────────────

def test_assignment_and_manual_are_visibly_different_words():
    assert shares.source_label(_ASSIGNED) != shares.source_label(_BOUGHT)
    assert shares.source_label(_ASSIGNED)
    assert shares.source_label(_BOUGHT)


def test_an_unknown_source_is_a_dash_not_a_guess():
    """A lot with no recorded source is not "bought by hand" by default."""
    assert shares.source_label({"symbol": "AAPL"}) == _fmt.NO_READING
    assert shares.source_label({"source": "wire_transfer"}) == _fmt.NO_READING


# ── the covering call ───────────────────────────────────────────────────────

def test_a_covered_call_on_the_same_symbol_is_found():
    pos = shares.covering_call("AAPL", [_UNRELATED, _COVERING])
    assert pos is _COVERING


def test_a_credit_spread_on_the_symbol_is_not_a_covering_call():
    """MSFT has an open call spread, not a call written against its shares.
    Matching on symbol alone would report the shares as covered when they are
    not — which is the reading a covered-call screen turns on."""
    assert shares.covering_call("MSFT", [_UNRELATED, _COVERING]) is None


def test_no_positions_means_no_covering_call():
    assert shares.covering_call("AAPL", []) is None
    assert shares.covering_call("AAPL", None) is None
    assert shares.covering_call("", [_COVERING]) is None


def test_the_covering_cell_names_the_strike_and_the_expiry():
    text = shares.covering_text(_COVERING)
    assert "210" in text
    assert "10/16" in text


def test_the_covering_cell_shows_the_contract_count_only_when_it_is_more_than_one():
    assert "×" not in shares.covering_text(_COVERING)
    assert "×3" in shares.covering_text(dict(_COVERING, quantity=3))


def test_no_covering_call_is_a_dash():
    assert shares.covering_text(None) == _fmt.NO_READING


# ── the rows ────────────────────────────────────────────────────────────────

def test_every_lot_becomes_exactly_one_row():
    rows = shares.lot_rows([_ASSIGNED, _BOUGHT], [_COVERING])
    assert len(rows) == 2
    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]


def test_rows_carry_shares_basis_and_total_cost():
    rows = shares.lot_rows([_ASSIGNED, _BOUGHT], [])
    assert len(rows) == 2
    assert rows[0]["shares"] == "100"
    assert rows[0]["basis"] == "195.00"
    # 100 x 195 — the capital committed, so the reader does not multiply.
    assert rows[0]["cost"] == "19,500.00"
    assert rows[1]["cost"] == "100,000.00"


def test_the_mark_and_unrealized_are_dashes_when_no_quote_was_published():
    """The account view carries no equity quote. Rendering the basis as a mark,
    or a 0.00 unrealized, would fabricate the reading the page is read for."""
    rows = shares.lot_rows([_ASSIGNED, _BOUGHT], [])
    assert len(rows) == 2
    assert all(r["mark"] == _fmt.NO_READING for r in rows)
    assert all(r["unrealized"] == _fmt.NO_READING for r in rows)


def test_a_mark_that_IS_published_is_used():
    """The builder is written so a later service change that attaches a quote
    fills these columns with no page edit — and so this test pins which key."""
    rows = shares.lot_rows([dict(_ASSIGNED, mark=205.0)], [])
    assert len(rows) == 1
    assert rows[0]["mark"] == "205.00"
    # (205 - 195) x 100
    assert rows[0]["unrealized"] == "1,000.00"


def test_the_unrealized_colour_comes_from_a_fixed_palette():
    up = shares.lot_rows([dict(_ASSIGNED, mark=205.0)], [])
    down = shares.lot_rows([dict(_ASSIGNED, mark=185.0)], [])
    flat = shares.lot_rows([_ASSIGNED], [])
    assert len(up) == len(down) == len(flat) == 1
    assert up[0]["_unrealized_class"] != down[0]["_unrealized_class"]
    # An absent reading is neither a gain nor a loss.
    assert flat[0]["_unrealized_class"] == shares.UNREALIZED_CLASSES["none"]
    # Every stamped class comes from the fixed map — never built at runtime.
    for r in up + down + flat:
        assert r["_unrealized_class"] in set(shares.UNREALIZED_CLASSES.values())


def test_a_nonfinite_mark_is_an_absent_reading_not_an_extreme_one():
    """This repo's most-documented bug class: a NaN survives every comparison
    and renders as a confident number."""
    rows = shares.lot_rows([dict(_ASSIGNED, mark=float("nan"))], [])
    assert len(rows) == 1
    assert rows[0]["mark"] == _fmt.NO_READING
    assert rows[0]["unrealized"] == _fmt.NO_READING


def test_rows_carry_the_provenance_and_the_covering_call():
    rows = shares.lot_rows([_ASSIGNED, _BOUGHT], [_COVERING, _UNRELATED])
    assert len(rows) == 2
    assert rows[0]["source"] == shares.source_label(_ASSIGNED)
    assert "210" in rows[0]["covering"]
    # MSFT's open spread is not a covering call.
    assert rows[1]["covering"] == _fmt.NO_READING


def test_every_lot_of_a_symbol_shows_the_call_written_against_that_symbol():
    """Coverage is recorded per SYMBOL, not per lot — the book has no link from
    a call back to the lot it was written against. Showing it on each lot of the
    symbol is the honest rendering of what is stored; hiding it on the second
    lot would imply those shares are uncovered."""
    second = dict(_ASSIGNED, lot_id=3, cost_basis=198.0)
    rows = shares.lot_rows([_ASSIGNED, second], [_COVERING])
    assert len(rows) == 2
    assert all("210" in r["covering"] for r in rows)


def test_row_ids_are_unique_even_when_a_lot_id_is_missing():
    """``row_key`` is the table's identity; two rows sharing one would collapse."""
    rows = shares.lot_rows([dict(_ASSIGNED, lot_id=None),
                            dict(_BOUGHT, lot_id=None)], [])
    assert len(rows) == 2
    assert len({r["id"] for r in rows}) == 2


def test_no_lots_means_no_rows():
    assert shares.lot_rows([], []) == []
    assert shares.lot_rows(None, None) == []


def test_a_lot_with_unreadable_numbers_renders_dashes_not_zeros():
    rows = shares.lot_rows([{"lot_id": 9, "symbol": "AMD",
                             "shares": None, "cost_basis": None}], [])
    assert len(rows) == 1
    assert rows[0]["shares"] == _fmt.NO_READING
    assert rows[0]["basis"] == _fmt.NO_READING
    assert rows[0]["cost"] == _fmt.NO_READING


# ── the columns ─────────────────────────────────────────────────────────────

def test_the_columns_cover_every_stamped_row_field():
    cols = shares.share_columns()
    fields = {c["field"] for c in cols}
    row = shares.lot_rows([_ASSIGNED], [_COVERING])[0]
    rendered = {k for k in row if not k.startswith("_") and k != "id"}
    assert rendered == fields


def test_the_column_labels_say_the_words():
    """The tree-wide vocabulary rule: no casual shortenings in a header."""
    labels = {c["label"] for c in shares.share_columns()}
    for banned in ("Qty", "Exp", "Strat", "Basis"):
        assert banned not in labels
    assert "Shares" in labels
    assert any(l.startswith("Cost basis") for l in labels)


def test_the_mark_column_says_the_shares_are_not_repriced():
    """The dash needs an explanation somewhere the reader is looking. The header
    is the closest place to the empty cell."""
    label = next(c["label"] for c in shares.share_columns() if c["field"] == "mark")
    assert label != "Mark"
    assert "not" in label.lower() or "tracked" in label.lower()


def test_page_help_explains_why_the_mark_is_blank():
    import page_help

    md = page_help.help_md("/options/shares")
    assert md != page_help._DEFAULT
    assert "mark" in md.lower()


# ── the tier boundary ───────────────────────────────────────────────────────

def test_the_page_reads_the_paper_account_view_not_a_second_one():
    """The lots ride the existing account view. A second view would give one
    database two publish cadences."""
    assert shares.VIEW == "options:paper_account"
