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
       "ledger_risk_per_contract": 182.0}


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
    p = book_fit.preview({**SIG, "ledger_risk_per_contract": None}, CAPS, qty=1)
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


def test_the_page_module_imports_nothing_tier1_forbids():
    import inspect
    src = inspect.getsource(book_fit)
    for banned in ("services", "sqlite3", "shared.sectors", "paper_trader", "nicegui"):
        assert banned not in src.replace("shared.book_caps", "")


# --- a malformed caps view is "no preview", never a raise --------------------

def _unavailable(p):
    return (p["available"] is False and p["lines"] == [] and p["breach"] is None
            and p["max_quantity"] is None
            and p["unavailable_text"] == book_fit.UNAVAILABLE)


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
    assert _unavailable(book_fit.preview(SIG, CAPS, qty="lots"))
    assert _unavailable(book_fit.preview(SIG, CAPS, qty=object()))


def test_an_empty_open_book_is_a_real_book():
    p = book_fit.preview(SIG, {**CAPS, "open": []}, qty=1)
    assert p["available"] and p["breach"] is None
