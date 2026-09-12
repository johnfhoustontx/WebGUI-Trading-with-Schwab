"""The Income board's PURE builders — /options/income, reader of cache:options:income.

Two things this file exists to prevent, both of which a naive test suite would
miss:

1. **The row shapes are heterogeneous.** An adapted credit spread (``PCS``/``CCS``)
   carries BOTH the flat ``short_strike``/``rr_pct`` contract and the normalized
   ``legs`` one; the cash-secured put (``SHORT_PUT``) carries ONLY the normalized
   shape. A builder that reaches for ``short_strike`` renders two of the three
   structures and silently blanks the third — so every row assertion here runs a
   ``PCS`` row and a ``SHORT_PUT`` row through the SAME call.

2. **A dead service and a still tape must not render the same words.** ``status_text``
   has three states, not two, and the middle one (scan ran, found nothing) is the
   one that would otherwise be spelled with the cold-feed line.

⚠ VACUITY: ``candidate_rows`` returning ``[]`` for input it does not understand
would satisfy every ``all(...)``/``not any(...)`` assertion trivially, so each
test asserts the row COUNT alongside the content.
"""
from pages import copy as _copy
from pages.options import income


# One row of each published structure. Field values are the NORMALIZED,
# per-contract ones ``_normalize_credit``/``payoff_metrics`` emit, because those
# are the only fields BOTH shapes carry.
_PCS = {
    "type": "PCS", "symbol": "AAPL", "expiration": "2026-10-16", "dte": 41,
    # The flat spread contract the SHORT_PUT row does not have...
    "short_strike": 195.0, "long_strike": 190.0, "credit": 0.60, "rr_pct": 13.6,
    # ...alongside the normalized one it does.
    "legs": [{"side": "short", "kind": "put", "strike": 195.0},
             {"side": "long", "kind": "put", "strike": 190.0}],
    "net_credit": 60.0, "max_profit": 58.7, "max_loss": 441.3, "capital": 441.3,
    "rr": 0.133, "pop_pct": 78.4, "breakevens": [194.40],
    "composite_score": 72.0, "earnings_status": "none_scheduled",
}
_CCS = dict(_PCS, type="CCS", short_strike=215.0, long_strike=220.0,
            legs=[{"side": "short", "kind": "call", "strike": 215.0},
                  {"side": "long", "kind": "call", "strike": 220.0}],
            breakevens=[215.60], composite_score=64.0,
            earnings_status="upcoming")
# The cash-secured put: NO short_strike, NO credit, NO rr_pct — only the
# normalized shape. Reading it correctly is the whole point of this file.
_CSP = {
    "type": "SHORT_PUT", "symbol": "MSFT", "expiration": "2026-10-16", "dte": 41,
    "legs": [{"side": "short", "kind": "put", "strike": 400.0}],
    "net_credit": 640.0, "max_profit": 638.7, "max_loss": 39361.3,
    "capital": 39361.3, "rr": 0.016, "pop_pct": 74.1, "breakevens": [393.60],
    "composite_score": 58.0, "earnings_status": "not_listed",
}


# ── the three status states ─────────────────────────────────────────────────

def test_a_cold_feed_says_nothing_has_published():
    """No payload at all — the service has never written this view."""
    assert income.status_text(None) == _copy.WAITING_OPTIONS
    assert income.status_text({}) == _copy.WAITING_OPTIONS


def test_a_pass_that_found_nothing_does_not_borrow_the_cold_feed_line():
    """The scan RAN. Saying "hasn't published" would report a healthy pass as an
    outage — the exact confusion pages/copy.py's own docstring warns about."""
    text = income.status_text({"candidates": [], "scanned_symbols": 23})
    assert text != _copy.WAITING_OPTIONS
    # It must name what was actually scanned, not print a bare zero.
    assert "23" in text


def test_a_pass_with_candidates_counts_both_the_rows_and_the_symbols():
    """⚠ Assert the PHRASE, not the digit. ``scanned_symbols`` is 23, so a bare
    ``"2" in text`` is satisfied by the "23" already in the sentence — hard-wiring
    the line to "1 candidate" was measured to pass that form."""
    text = income.status_text({"candidates": [_PCS, _CSP], "scanned_symbols": 23})
    assert text != _copy.WAITING_OPTIONS
    assert "2 candidates" in text
    assert "23 symbols" in text


def test_failed_symbols_are_disclosed_not_swallowed():
    """publish_income lands a per-symbol failure in ``errors`` precisely so the
    page can say so. A whole-watchlist outage otherwise reads as a quiet tape."""
    text = income.status_text({"candidates": [], "scanned_symbols": 23,
                               "errors": ["AAPL: KeyError: x", "MSFT: ValueError: y"]})
    # The phrase, not the digit: "23" carries a "2" of its own, so the looser
    # form passed against a hard-wired literal that named no count at all.
    assert "2 symbols failed" in text


def test_the_scan_time_is_shown_when_the_payload_carries_one():
    """A once-daily view: without a stamp there is no way to tell this morning's
    board from Friday's."""
    text = income.status_text({"candidates": [_PCS], "scanned_symbols": 1,
                               "ts": "2026-09-05T08:35:00-05:00"})
    assert "8:35" in text


def test_an_unparseable_stamp_is_dropped_rather_than_printed_raw():
    text = income.status_text({"candidates": [_PCS], "scanned_symbols": 1,
                               "ts": "not a timestamp"})
    assert "not a timestamp" not in text


# ── the heterogeneous rows ──────────────────────────────────────────────────

def test_every_published_structure_makes_a_row():
    """VACUITY GUARD for every assertion below: they all index into the result,
    and a builder that silently dropped the shape it did not recognise would
    satisfy the negative ones by returning nothing."""
    rows = income.candidate_rows([_PCS, _CCS, _CSP])
    assert len(rows) == 3


def test_rows_label_the_side_a_reader_can_act_on():
    """"PCS" is engine vocabulary. The board is two-sided plus a single, and the
    side is the first thing a reader picks on."""
    rows = income.candidate_rows([_PCS, _CCS, _CSP])
    assert len(rows) == 3
    assert [r["side"] for r in rows] == [
        "Put spread", "Call spread", "Cash-secured put"]


def test_an_unknown_structure_still_makes_a_row_labelled_honestly():
    """A shape drift must not vanish from a board a human picks trades from —
    but it must not be mislabelled as one of the three either."""
    rows = income.candidate_rows([{"type": "WAT", "symbol": "AAPL"}])
    assert len(rows) == 1
    assert rows[0]["side"] == "WAT"


def test_the_strikes_come_from_legs_so_both_shapes_render():
    """The single carries no ``short_strike`` at all. A builder keying off that
    field renders the two spreads and blanks the cash-secured put — which looks
    like a thin row, not like a bug."""
    rows = income.candidate_rows([_PCS, _CSP])
    assert len(rows) == 2
    assert rows[0]["legs"] == "S 195P / L 190P"
    assert rows[1]["legs"] == "S 400P"


def test_the_credit_column_reads_the_field_both_shapes_carry():
    """Per-CONTRACT dollars. The spread ALSO has a per-share ``credit`` of 0.60;
    reading that one would put a $0.60 row next to a $640 row on one board."""
    rows = income.candidate_rows([_PCS, _CSP])
    assert len(rows) == 2
    assert [r["credit"] for r in rows] == ["60.00", "640.00"]


def test_capital_at_risk_renders_for_both_shapes():
    rows = income.candidate_rows([_PCS, _CSP])
    assert len(rows) == 2
    assert [r["capital"] for r in rows] == ["441.30", "39361.30"]


def test_return_on_capital_is_what_makes_the_two_shapes_comparable():
    """A $60 credit on $441 and a $640 credit on $39,361 are not comparable as
    dollars. 13.3% against 1.6% is the reading the window is actually screened
    on."""
    rows = income.candidate_rows([_PCS, _CSP])
    assert len(rows) == 2
    assert rows[0]["roc"] == "13.3%"
    assert rows[1]["roc"] == "1.6%"


def test_the_breakeven_comes_from_the_list_both_shapes_carry():
    rows = income.candidate_rows([_PCS, _CSP])
    assert len(rows) == 2
    assert rows[0]["breakeven"] == "194.40"
    assert rows[1]["breakeven"] == "393.60"


def test_the_columns_and_the_row_keys_agree():
    """A column whose ``field`` no row stamps renders a permanently blank cell,
    and nothing else in the suite would notice.

    ``actions`` is exempt and is the ONLY exemption: its cell is drawn entirely
    by a Quasar body-cell slot, so it has no row field by design — the same
    shape the captured / paper / scanner / strategy tables use.
    """
    rows = income.candidate_rows([_PCS, _CSP])
    assert rows
    for col in income.income_columns():
        if col["name"] == "actions":
            continue
        assert col["field"] in rows[0], f"column {col['name']} has no row field"


def test_the_board_carries_an_action_column_for_the_open_button():
    """Non-vacuity for the exemption above: without this, deleting the column
    entirely would pass the test it is exempt from."""
    names = [c["name"] for c in income.income_columns()]
    assert names[-1] == "actions", "the action column must be last"
    actions = income.income_columns()[-1]
    assert actions["label"] == "", "an action column carries no header text"
    assert not actions.get("sortable"), "a button is not a reading to sort on"


# ── an absent reading is a dash, never a zero ───────────────────────────────

def test_a_missing_credit_renders_a_dash_never_a_zero():
    """Never print a number you did not read: 0.00 claims a measurement."""
    rows = income.candidate_rows([{"type": "PCS", "symbol": "AAPL"}])
    assert len(rows) == 1
    assert rows[0]["credit"] == "—"


def test_every_unread_numeric_cell_is_a_dash():
    rows = income.candidate_rows([{"type": "SHORT_PUT", "symbol": "MSFT"}])
    assert len(rows) == 1
    row = rows[0]
    for field in ("credit", "capital", "roc", "pop", "breakeven", "expiration",
                  "dte"):
        assert row[field] == "—", f"{field} rendered {row[field]!r}, not a dash"


def test_a_nan_is_an_absence_not_a_reading():
    """The documented trap: every comparison against NaN is False, so an
    unguarded NaN sails past a `> 0` guard and formats as 'nan%'."""
    rows = income.candidate_rows([dict(_PCS, net_credit=float("nan"),
                                       capital=float("nan"))])
    assert len(rows) == 1
    assert rows[0]["credit"] == "—"
    assert rows[0]["roc"] == "—"


def test_zero_capital_does_not_divide_and_does_not_render_infinity():
    rows = income.candidate_rows([dict(_PCS, capital=0.0)])
    assert len(rows) == 1
    assert rows[0]["roc"] == "—"


def test_a_real_zero_credit_is_still_printed_as_zero():
    """The other half of the dash rule, and the one that makes it a rule rather
    than a blanket falsy check: 0.00 that was MEASURED is a fact."""
    rows = income.candidate_rows([dict(_PCS, net_credit=0.0)])
    assert len(rows) == 1
    assert rows[0]["credit"] == "0.00"


# ── the earnings check must be visible ──────────────────────────────────────

def test_the_three_earnings_states_read_differently():
    """A row whose earnings check could not RUN must not look like one that
    passed it. There is no Alpha Vantage key in most checkouts, so ``not_listed``
    is the common case — it must read as unknown, not as an alarm."""
    rows = income.candidate_rows([_PCS, _CCS, _CSP])
    assert len(rows) == 3
    labels = [r["earnings"] for r in rows]
    assert len(set(labels)) == 3, f"the three states collapsed to {labels}"
    assert labels == [income.EARNINGS_LABEL["none_scheduled"],
                      income.EARNINGS_LABEL["upcoming"],
                      income.EARNINGS_LABEL["not_listed"]]


def test_an_unchecked_symbol_is_not_coloured_like_an_alarm():
    """``not_listed`` gets the NEUTRAL class, not the warning one — it is the
    common case, and painting the whole board amber trains the reader to ignore
    the colour."""
    rows = income.candidate_rows([_CSP])
    assert len(rows) == 1
    assert rows[0]["_earnings_class"] == income.EARNINGS_CLASS["not_listed"]
    assert (income.EARNINGS_CLASS["not_listed"]
            != income.EARNINGS_CLASS["none_scheduled"])


def test_a_row_with_no_earnings_field_is_unknown_not_clear():
    """Absence of the stamp is absence of the check. Defaulting to the cleared
    label is the fail-open shape shared/earnings.coverage exists to refuse."""
    rows = income.candidate_rows([{"type": "PCS", "symbol": "AAPL"}])
    assert len(rows) == 1
    assert rows[0]["earnings"] != income.EARNINGS_LABEL["none_scheduled"]


def test_the_earnings_classes_are_static_not_runtime_hex():
    """The Tailwind-first standard: a finite state maps to a fixed palette class.
    A runtime-built ``text-[{hex}]`` is what this repo bans."""
    for cls in income.EARNINGS_CLASS.values():
        assert "{" not in cls and "}" not in cls


# ── ordering ────────────────────────────────────────────────────────────────

def test_the_service_ranking_is_preserved():
    """publish_income already sorts the merged board by composite score, with an
    absent/NaN score pinned LAST so a non-reading can never top a board a human
    picks a trade from. Re-sorting here would either duplicate that rule or
    quietly contradict it."""
    rows = income.candidate_rows([_CSP, _PCS, _CCS])
    assert [r["symbol"] for r in rows] == ["MSFT", "AAPL", "AAPL"]


def test_a_covered_call_is_named_not_shouted():
    """The board now carries a fourth structure. Without a label it falls
    through to the raw engine identifier and the Side column reads
    "COVERED_CALL" beside "Put spread" — the one thing ``side_label`` exists to
    prevent."""
    assert income.side_label({"type": "COVERED_CALL"}) == "Covered call"


def test_a_covered_call_row_renders_off_the_shape_both_products_share():
    """It carries the normalized shape (``legs`` / ``net_credit`` / ``capital``),
    so every cell reads without the page learning a fourth row shape."""
    row = income.candidate_rows([{
        "id": "AAPL_COVERED_CALL_2026-10-16_110.0",
        "symbol": "AAPL", "type": "COVERED_CALL",
        "legs": [{"kind": "call", "side": "short", "strike": 110.0, "qty": 1,
                  "expiration": "2026-10-16"}],
        "expiration": "2026-10-16", "dte": 35,
        "net_credit": 160.0, "capital": 9500.0, "max_profit": 1658.7,
        "breakevens": [93.4], "pop_pct": 61.2,
        "earnings_status": "not_listed",
    }])[0]

    assert row["side"] == "Covered call"
    assert row["credit"] == "160.00"
    assert row["capital"] == "9500.00"
    # max_profit / capital, the column that makes the board comparable at all.
    assert row["roc"] == "17.5%"


# ── the two covered-call ratios ─────────────────────────────────────────────
# ``compute.covered_call_candidates`` has emitted ``yield_on_cost`` and
# ``total_return_if_called`` on every covered-call row since it was written, and
# the design doc calls them "the two numbers that actually decide a covered
# call" — the page rendered neither.
#
# ⚠ Both are FRACTIONS in the payload (0.0168), not percents. Rendering them raw
# would put 0.02 beside a 17.5% column and read as a rounding error.

# One covered call with the ratios attached, at the values compute produces for
# a 100-share lot at a 95.00 basis with a 110 strike and a 1.60 mark:
#   yield_on_cost         = 160 / (95 * 100)                 = 0.016842…
#   total_return_if_called = ((110-95)*100 + 160) / 9500      = 0.174736…
_COVERED = {
    "id": "AAPL_COVERED_CALL_2026-10-16_110.0",
    "symbol": "AAPL", "type": "COVERED_CALL", "covered": True,
    "legs": [{"kind": "call", "side": "short", "strike": 110.0, "qty": 1,
              "expiration": "2026-10-16"}],
    "expiration": "2026-10-16", "dte": 35,
    "net_credit": 160.0, "capital": 9500.0, "max_profit": 1658.7,
    "breakevens": [93.4], "pop_pct": 61.2, "earnings_status": "not_listed",
    "cost_basis": 95.0, "shares": 100, "quantity": 1,
    "yield_on_cost": 160.0 / 9500.0,
    "total_return_if_called": ((110.0 - 95.0) * 100 + 160.0) / 9500.0,
}


def test_a_covered_call_shows_both_deciding_ratios_as_percents():
    rows = income.candidate_rows([_COVERED])
    assert len(rows) == 1
    assert rows[0]["yield_on_cost"] == "1.68%"
    assert rows[0]["total_return_if_called"] == "17.47%"


def test_the_two_ratios_are_dashes_on_every_structure_without_a_cost_basis():
    """A put spread, a call spread and a cash-secured put own no shares, so
    neither ratio exists for them. A 0.00% would rank among real readings."""
    rows = income.candidate_rows([_PCS, _CCS, _CSP])
    assert len(rows) == 3
    for row in rows:
        assert row["yield_on_cost"] == "—", row["side"]
        assert row["total_return_if_called"] == "—", row["side"]


def test_the_ratios_are_refused_on_a_non_covered_row_even_if_the_field_is_there():
    """Gated on the STRUCTURE, not merely on the field being readable: a stray
    ratio stamped on a spread would render as a return on shares nobody owns."""
    rows = income.candidate_rows([dict(_PCS, yield_on_cost=0.05,
                                       total_return_if_called=0.09)])
    assert len(rows) == 1
    assert rows[0]["yield_on_cost"] == "—"
    assert rows[0]["total_return_if_called"] == "—"


def test_a_covered_call_missing_a_ratio_renders_a_dash_not_a_zero():
    rows = income.candidate_rows([dict(_COVERED, yield_on_cost=None,
                                       total_return_if_called=float("nan"))])
    assert len(rows) == 1
    assert rows[0]["yield_on_cost"] == "—"
    assert rows[0]["total_return_if_called"] == "—"


def test_both_ratios_have_a_column_of_their_own():
    fields = [c["field"] for c in income.income_columns()]
    assert "yield_on_cost" in fields
    assert "total_return_if_called" in fields
    labels = {c["field"]: c["label"] for c in income.income_columns()}
    assert labels["yield_on_cost"] == "Yield on cost"
    assert labels["total_return_if_called"] == "Total return if called"


def test_return_on_capital_and_total_return_if_called_are_not_duplicates():
    """They read almost the same on a covered call and are NOT the same number:
    Return on capital is net of the opening commission, Total return if called is
    gross. Deleting either as a duplicate loses that distinction — and Return on
    capital is also the only column the other three structures have."""
    roc = income.return_on_capital(_COVERED)
    tric = _COVERED["total_return_if_called"] * 100.0
    # Close enough that a reader would call them the same column...
    assert abs(roc - tric) < 0.5
    # ...and not the same number. Return on capital is the smaller: it has paid
    # the commission that being called away actually costs.
    assert roc < tric


# ── the open action: which rows get a button, and what the answer looks like ──
# The board's one control. Two halves, and each is worthless without the other:
# a button on a row the SERVICE refuses is a dead control, and an outcome the
# page never shows is a button that appears to do nothing.

def test_only_the_two_single_leg_products_get_an_open_button():
    """The paper ACCOUNT can hold a cash-secured put and a covered call. A
    credit spread's route is the paper LEDGER, which is a different book — and
    the service refuses it, so a button here would only ever be refused."""
    rows = income.candidate_rows([_PCS, _CCS, _CSP, _COVERED])
    assert len(rows) == 4
    allowed = {r["side"]: r["_allow_open"] for r in rows}
    assert allowed == {"Put spread": False, "Call spread": False,
                       "Cash-secured put": True, "Covered call": True}


def test_an_unreadable_row_gets_no_button():
    """Absent reads as falsy → no button, which fails safe in the direction that
    matters for a control that books a trade."""
    rows = income.candidate_rows([{"symbol": "AAPL"}, {"type": None}])
    assert len(rows) == 2
    assert not any(r["_allow_open"] for r in rows)


def test_the_page_gate_and_the_service_gate_name_the_same_structures():
    """A button the service will refuse, or a structure with no way to reach it.
    ``shared/tests/test_cross_tier_mirrors.py`` pins the constants themselves;
    this pins that the PAGE actually applies them."""
    from pages.options import handoff

    for kind in handoff.INCOME_OPENABLE_TYPES:
        assert income.candidate_rows([{"type": kind}])[0]["_allow_open"] is True
    assert handoff.income_openable({"type": "pcs"}) is False
    assert handoff.income_openable({"type": " short_put "}) is True
    assert handoff.income_openable(None) is False


def test_an_outcome_is_shown_with_the_services_own_sentence():
    """The service is the only place that knows the numbers behind a refusal —
    what the collateral was, what the account held — so the page shows its
    sentence rather than restating it less truthfully."""
    assert income.open_result_display({
        "status": "opened", "message": "Opened 1 AAPL 100 put at $2.00."}) == (
        "Opened 1 AAPL 100 put at $2.00.", "positive")


def test_a_refusal_is_a_warning_not_an_error():
    """"The account has $8,000 and this needs $10,000" is the system working.
    Painting a rule red trains the reader to read a rule as a fault."""
    _msg, tone = income.open_result_display(
        {"status": "rejected", "reason": "insufficient_cash", "message": "Short."})
    assert tone == "warning"
    _msg, tone = income.open_result_display({"status": "error", "message": "Boom."})
    assert tone == "negative"


def test_a_cold_result_view_is_not_an_outcome():
    """Toasting an empty payload on page build would report a click nobody made."""
    assert income.open_result_display(None) is None
    assert income.open_result_display({}) is None


def test_an_unreadable_result_says_so_rather_than_inventing_one():
    """A payload with no sentence and no status we know is not an "opened" and
    is not a silence — the reader pressed a button and is owed an answer."""
    message, tone = income.open_result_display({"status": "wat"})
    assert message == income.OPEN_RESULT_UNKNOWN
    assert tone == "warning"
    message, _tone = income.open_result_display({"status": "opened"})
    assert message == income.OPEN_RESULT_UNKNOWN


def test_the_result_view_is_not_the_board_view():
    """They repaint for different reasons: the board is a once-daily reading of
    the market, the result is the answer to one click. Sharing a key would
    repaint every reader's table for one reader's button."""
    assert income.OPEN_RESULT_VIEW != income.VIEW
    assert income.OPEN_RESULT_VIEW == "options:income_open"


# ── B2: the volatility floor's effect must be visible, not silent ────────────

def test_status_text_says_when_the_volatility_floor_emptied_the_board():
    """Gap assessment B2. The floor refuses to sell premium below the IV-rank
    minimum, and on a market-wide low-IV day that can empty the board. Without a
    sentence naming it, a short board is indistinguishable from a quiet tape —
    which is the same "invisible refusal" the concentration caps already have and
    the reason this one gets a line.
    """
    line = income.status_text({"candidates": [], "scanned_symbols": 23,
                               "vol_filtered": 9})
    assert "Nothing cleared the 30-45 day income window" in line
    assert "9 too cheap to sell" in line


def test_status_text_names_the_volatility_drops_beside_a_partial_board():
    line = income.status_text({"candidates": [{}, {}], "scanned_symbols": 23,
                               "vol_filtered": 4})
    assert "2 candidates across 23 symbols" in line
    assert "4 too cheap to sell" in line


def test_status_text_stays_quiet_when_the_floor_dropped_nothing():
    line = income.status_text({"candidates": [{}], "scanned_symbols": 3,
                               "vol_filtered": 0})
    assert "too cheap" not in line


def test_status_text_renders_a_payload_written_before_the_field_existed():
    """Redis persists this view across a restart."""
    line = income.status_text({"candidates": [{}], "scanned_symbols": 3})
    assert "too cheap" not in line
    assert "1 candidate across 3 symbols" in line
