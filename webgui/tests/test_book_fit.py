import pytest

from pages.options import book_fit

CAPS = {"limits": {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
                   "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
                   "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
                   "max_risk_per_trade": 250.0},
        "equity": 25000.0,
        "open": [{"symbol": "ORCL", "expiration": "2026-10-17",
                  "max_loss_total": 410.0, "sector": "Information Technology"}] ,
        "sectors": {"ORCL": "Information Technology"}, "unmapped_prefix": "?"}

SIG = {"symbol": "ORCL", "type": "PCS", "expiration": "2026-10-17",
       "ledger_risk_per_contract": 182.0,
       "ledger_risk_basis": {"per_contract": 182.0}}


def test_preview_for_one_contract():
    p = book_fit.preview(SIG, CAPS, qty=1)
    assert p["available"] and p["max_quantity"] == 1 and p["breach"] is None
    assert p["lines"][0]["label"] == "Per trade"
    assert p["lines"][0]["tone"] == "pos"


def test_preview_blocks_at_two_contracts_and_names_why():
    p = book_fit.preview(SIG, CAPS, qty=2)
    assert p["breach"]["code"] == "TRADE_RISK_CAP"
    assert p["block_text"] == "Risks $364, over the $250 per-trade limit"
    assert p["lines"][0]["tone"] == "neg"


def test_no_stamp_means_no_preview_never_a_green():
    p = book_fit.preview({**SIG, "ledger_risk_per_contract": None,
                          "ledger_risk_basis": None}, CAPS, qty=1)
    assert p["available"] is False
    assert p["unavailable_text"] == ("Can't preview this trade here — the paper "
                                     "ledger still checks every cap when you create it.")


def test_no_caps_view_means_no_preview():
    assert book_fit.preview(SIG, None, qty=1)["available"] is False


def test_a_symbol_outside_the_table_is_capped_in_its_own_group():
    """Exactly the service's rule (book_caps.sector_bucket): an unmapped name is
    its own bucket, so its sector rungs are evaluated, never skipped."""
    p = book_fit.preview({**SIG, "symbol": "ZZZZ"}, CAPS, qty=1)
    sector = [l for l in p["lines"] if l["code"] == "SECTOR_POSITION_CAP"][0]
    assert sector["tone"] == "pos"
    assert sector["text"] == "Position 1 of 5 in ZZZZ's own group (no sector on file)"


def test_no_sector_table_means_no_preview():
    """Without the table the page cannot compute the bucket the service will."""
    caps = {k: v for k, v in CAPS.items() if k != "sectors"}
    assert book_fit.preview(SIG, caps, qty=1)["available"] is False


def test_short_reason_for_the_checklist_chip():
    assert book_fit.short_reason({"code": "SECTOR_POSITION_CAP"}) == "sector full"
    assert book_fit.short_reason({"code": "TRADE_RISK_CAP", "cap": 750.0}) == "over $750 per trade"
    assert book_fit.short_reason({"code": "TRADE_RISK_CAP", "cap": 250.0}) == "over $250 per trade"


def test_the_page_module_imports_exactly_book_caps_and_num():
    """An AST walk over book_fit.py: its imports are exactly ``shared.book_caps``
    and ``num`` from the sibling ``..fmt`` - so no service, engine, sqlite3,
    sectors loader or UI framework can arrive, directly or by name. A dynamic
    import (``__import__`` / ``importlib``) would dodge the walk, so those names
    are refused outright."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(book_fit))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(("import", a.name, None, 0))
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imports.add(("from", node.module, a.name, node.level))
    assert imports == {("from", "shared", "book_caps", 0),
                       ("from", "fmt", "num", 2)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not ({"__import__", "importlib", "exec", "eval"} & (names | attrs))


# --- a malformed caps view is "no preview", never a raise --------------------

def _unavailable(p, text=None):
    return (p["available"] is False and p["lines"] == [] and p["breach"] is None
            and p["max_quantity"] is None and p["block_text"] == ""
            and p["unavailable_text"] == (text or book_fit.UNAVAILABLE))


def test_limits_missing_a_required_cap_means_no_preview():
    limits = {k: v for k, v in CAPS["limits"].items()
              if k != "max_positions_per_expiry"}
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "limits": limits}, qty=1))


def test_a_required_cap_of_none_means_no_preview():
    limits = {**CAPS["limits"], "max_risk_per_symbol": None}
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "limits": limits}, qty=1))


def test_limits_that_are_not_a_mapping_mean_no_preview():
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "limits": ["x"]}, qty=1))


def test_open_that_is_not_a_list_means_no_preview():
    """A string would iterate as characters and silently read as an empty book."""
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "open": "ORCL"}, qty=1))
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "open": {"a": 1}}, qty=1))


def test_an_open_row_with_a_non_string_symbol_means_no_preview():
    rows = [{"symbol": 7, "expiration": "2026-10-17", "max_loss_total": 1.0,
             "sector": None}]
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "open": rows}, qty=1))


def test_a_non_numeric_quantity_means_no_preview():
    assert _unavailable(book_fit.preview(SIG, CAPS, qty="lots"), book_fit.BAD_QUANTITY)
    assert _unavailable(book_fit.preview(SIG, CAPS, qty=object()), book_fit.BAD_QUANTITY)


BAD_QTYS = (0, -3, True, False, None, 2.9, "2.5", float("nan"), float("inf"),
            "", " ", "abc", "nan", -1.0, [2])


def test_the_quantity_rule_text_is_the_ledgers_word_for_word():
    assert book_fit.BAD_QUANTITY == "Quantity must be a whole number of at least 1."



@pytest.mark.parametrize("qty", BAD_QTYS)
def test_a_quantity_that_is_not_a_whole_number_of_at_least_one_is_refused(qty):
    assert _unavailable(book_fit.preview(SIG, CAPS, qty=qty), book_fit.BAD_QUANTITY)
    assert book_fit.whole_quantity(qty) is None


@pytest.mark.parametrize("qty", BAD_QTYS)
def test_a_bad_quantity_is_named_even_when_the_caps_view_is_missing(qty):
    assert _unavailable(book_fit.preview(SIG, None, qty=qty), book_fit.BAD_QUANTITY)


@pytest.mark.parametrize("qty", ("2", 2.0, " 2 ", "2.0", 2))
def test_a_whole_number_in_another_type_is_evaluated_as_that_number(qty):
    p = book_fit.preview(SIG, CAPS, qty=qty)
    assert p["available"] and book_fit.whole_quantity(qty) == 2
    assert p["breach"]["code"] == "TRADE_RISK_CAP"
    assert p["block_text"] == "Risks $364, over the $250 per-trade limit"


def test_a_non_positive_per_contract_risk_means_no_preview():
    for per in (0, 0.0, -182.0, float("nan"), True, "junk", None):
        p = book_fit.preview({**SIG, "ledger_risk_per_contract": per,
                              "ledger_risk_basis": {"per_contract": per}}, CAPS, qty=1)
        assert _unavailable(p), per


@pytest.mark.parametrize("basis", [
    None, "182", [182.0], {}, {"per_share": 1.82, "per_contract": 182.0},
    {"per_share": 0.0}, {"per_share": -1.82}, {"per_share": float("inf")},
    {"per_share": 1e307}, {"other": 182.0},
])
def test_an_unusable_risk_basis_means_no_preview_never_a_green(basis):
    """The per-contract figure alone is not enough: without the booking basis the
    page cannot compute the total the Ledger will book at this quantity."""
    p = book_fit.preview({**SIG, "ledger_risk_basis": basis}, CAPS, qty=1)
    assert _unavailable(p), basis


def test_the_preview_reads_the_basis_not_the_per_contract_figure():
    """A stale or contradictory per-contract stamp cannot change the answer."""
    p = book_fit.preview({**SIG, "ledger_risk_per_contract": 1.0}, CAPS, qty=2)
    assert p["block_text"] == "Risks $364, over the $250 per-trade limit"
    p = book_fit.preview({k: v for k, v in SIG.items()
                          if k != "ledger_risk_per_contract"}, CAPS, qty=1)
    assert p["available"] and p["breach"] is None


def test_a_sub_cent_per_share_basis_is_rounded_as_the_ledger_books_it():
    """$1.87504 a share is $187.50 for one contract but $750.02 for four - the
    Ledger rounds the TOTAL. Against a $750 limit four is over, three is the most."""
    caps = {**CAPS, "open": [], "limits": {**CAPS["limits"], "max_risk_per_trade": 750.0}}
    sig = {**SIG, "ledger_risk_per_contract": 187.5,
           "ledger_risk_basis": {"per_share": 1.87504}}
    p = book_fit.preview(sig, caps, qty=4)
    assert p["breach"]["code"] == "TRADE_RISK_CAP"
    assert p["block_text"] == "Risks $750.02, over the $750 per-trade limit"
    assert p["max_quantity"] == 3
    assert book_fit.preview(sig, caps, qty=3)["breach"] is None
    # At one contract the proposal is floor($750 / $187.50) = 4, and only the
    # step-down (4 books $750.02) brings it to 3 - at every quantity.
    for q in range(1, 6):
        assert book_fit.preview(sig, caps, qty=q)["max_quantity"] == 3, q


def test_max_quantity_steps_down_past_a_rounding_edge_as_the_ledger_does():
    """max_quantity's proposal uses total / qty; the step-down re-checks the total
    booked at each size, so a proposal whose booked total crosses the cap drops."""
    from shared import book_caps
    caps = {**CAPS, "open": [], "limits": {**CAPS["limits"], "max_risk_per_trade": 750.0,
                                           "max_risk_per_symbol": 10_000.0,
                                           "max_risk_per_sector": 10_000.0,
                                           "max_deployed_risk_pct": 0}}
    sig = {**SIG, "ledger_risk_basis": {"per_share": 1.874975}}
    for q in range(1, 6):
        p = book_fit.preview(sig, caps, qty=q)
        n = p["max_quantity"]
        assert book_caps.booked_risk(sig["ledger_risk_basis"], n) <= 750.0
        assert book_caps.booked_risk(sig["ledger_risk_basis"], n + 1) > 750.0


def test_empty_limits_mean_no_preview():
    assert _unavailable(book_fit.preview(SIG, {**CAPS, "limits": {}}, qty=1))


def test_an_open_book_of_none_is_an_empty_book():
    p = book_fit.preview(SIG, {**CAPS, "open": None}, qty=1)
    empty = book_fit.preview(SIG, {**CAPS, "open": []}, qty=1)
    assert p["available"] and p == empty
    symbol = [l for l in p["lines"] if l["code"] == "SYMBOL_POSITION_CAP"][0]
    assert symbol["text"] == "Position 1 of 3 in ORCL"
    assert p["max_quantity"] == 1


def test_labels_and_short_reasons_cover_every_rung():
    from shared import book_caps
    assert set(book_fit._LABELS) == set(book_caps.DISPLAY_ORDER)
    assert set(book_fit._SHORT) == set(book_caps.DISPLAY_ORDER)
    for code in book_caps.DISPLAY_ORDER:
        assert book_fit.short_reason({"code": code}) != "blocked"


def test_an_empty_open_book_is_a_real_book():
    p = book_fit.preview(SIG, {**CAPS, "open": []}, qty=1)
    assert p["available"] and p["breach"] is None
