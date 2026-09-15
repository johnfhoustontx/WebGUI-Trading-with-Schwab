"""The Paper button's answer becomes a toast. PURE part only."""
from pages.options import handoff


def test_opened_names_the_structure_in_words():
    assert handoff.paper_result_toast({"status": "opened", "symbol": "SPY", "qty": 2,
                                       "type": "PCS"}) == (
        "Paper ledger: opened 2 × SPY Credit spread — put.", "positive")


def test_opened_never_prints_a_raw_code():
    text, _ = handoff.paper_result_toast({"status": "opened", "symbol": "NVDA", "qty": 1,
                                          "type": "LONG_CALL"})
    assert text == "Paper ledger: opened 1 × NVDA Long call."


def test_refused_names_the_cap_and_the_quantity_that_fits():
    text, kind = handoff.paper_result_toast({
        "status": "refused", "symbol": "ORCL", "code": "SYMBOL_POSITION_CAP",
        "message": "ORCL already holds 3 of 3 positions", "max_quantity": 0})
    assert kind == "warning"
    assert text == "Paper ledger: not opened — ORCL already holds 3 of 3 positions."


def test_refused_with_room_for_a_smaller_quantity_says_so():
    text, _ = handoff.paper_result_toast({
        "status": "refused", "symbol": "SPY", "code": "TRADE_RISK_CAP",
        "message": "Risks $900, over the $750 per-trade limit", "max_quantity": 1})
    assert text == "Paper ledger: not opened — risks $900, over the $750 per-trade limit. Up to 1 contract fits."


def test_refused_with_room_for_several_uses_the_plural():
    text, _ = handoff.paper_result_toast({
        "status": "refused", "message": "Risks $760, over the $750 per-trade limit",
        "max_quantity": 3})
    assert text.endswith("Up to 3 contracts fit.")


def test_stale_and_error():
    assert handoff.paper_result_toast({
        "status": "stale",
        "message": "The request waited too long to be processed. Try again."}) == (
        "Paper ledger: not opened — the request waited too long to be processed. Try again.",
        "warning")
    assert handoff.paper_result_toast({"status": "error", "message": "boom"}) == (
        "Paper ledger: not opened — boom.", "negative")


def test_a_missing_message_still_says_something():
    assert handoff.paper_result_toast({"status": "error"}) == (
        "Paper ledger: not opened — the service gave no reason.", "negative")


def test_a_message_opening_on_a_ticker_keeps_its_capitals():
    assert handoff.paper_result_toast({
        "status": "refused",
        "message": "IONQ's own group (no sector on file) is full (5 of 5 positions)",
        "max_quantity": 0})[0] == (
        "Paper ledger: not opened — IONQ's own group (no sector on file) is full (5 of 5 positions).")


def test_a_sector_name_keeps_its_capitals():
    text, _ = handoff.paper_result_toast({
        "status": "refused", "message": "Information Technology is full (5 of 5 positions)",
        "max_quantity": 0})
    assert text == "Paper ledger: not opened — Information Technology is full (5 of 5 positions)."
    text, _ = handoff.paper_result_toast({
        "status": "refused", "message": "Energy risk would reach $1,550 of $1,500",
        "max_quantity": 0})
    assert text == "Paper ledger: not opened — Energy risk would reach $1,550 of $1,500."


def test_our_own_sentence_openers_are_lowered():
    text, _ = handoff.paper_result_toast({
        "status": "refused",
        "message": "Open risk across the book would reach $5,080 of $5,000",
        "max_quantity": 0})
    assert text == "Paper ledger: not opened — open risk across the book would reach $5,080 of $5,000."
    assert handoff.paper_result_toast({
        "status": "error", "message": "Quantity must be a whole number of at least 1."}) == (
        "Paper ledger: not opened — quantity must be a whole number of at least 1.", "negative")


def test_nothing_to_say_for_an_empty_payload():
    assert handoff.paper_result_toast(None) is None
    assert handoff.paper_result_toast({}) is None


def test_the_watcher_uses_watch_view_on_the_paper_create_view():
    import inspect
    src = inspect.getsource(handoff.watch_paper_results)
    assert "watch_view" in src and "PAPER_CREATE_VIEW" in src
    assert handoff.PAPER_CREATE_VIEW == "options:paper_create"


def test_both_signal_pages_mount_the_watcher():
    import inspect
    from pages.options import scanner, swing
    assert "watch_paper_results()" in inspect.getsource(scanner.render)
    assert "watch_paper_results()" in inspect.getsource(swing.render)
