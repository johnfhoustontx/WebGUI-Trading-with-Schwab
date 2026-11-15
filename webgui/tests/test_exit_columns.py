"""The Stop, Stop rule and Target columns on the three paper books.

The service computes the levels (services/options_svc/exit_levels.py); these
tests pin that each table shows them, beside its P&L, from the row's own keys.
"""
from pages.options import captured, exit_columns, paper, portfolio


def _names(cols):
    return [c["name"] for c in cols]


def test_each_book_puts_the_three_columns_right_after_its_pnl():
    for cols, pnl in ((paper.paper_columns(), "pnl"),
                      (captured.captured_columns(), "unrealized_pnl"),
                      (portfolio.position_columns(), "unrealized_pnl")):
        names = _names(cols)
        at = names.index(pnl)
        assert names[at + 1:at + 4] == ["stop_level", "stop_kind", "target_level"]


def test_the_cells_carry_the_services_levels_in_the_readers_words():
    row = {"stop_level": -200.0, "stop_kind": "money", "target_level": 50.0}
    assert exit_columns.cells(row) == {"stop_level": -200.0, "stop_kind": "Loss stop",
                                       "target_level": 50.0}
    assert exit_columns.cells({"stop_kind": "trail", "stop_level": 22.5})["stop_kind"] \
        == "Trailing"


def test_no_level_is_a_blank_never_a_zero():
    """A Ledger credit trade with no trail armed has no stop; a closed row has
    neither. A 0 would read as "stops at break-even"."""
    assert exit_columns.cells({}) == {"stop_level": None, "stop_kind": "",
                                      "target_level": None}


def test_every_row_builder_passes_the_levels_through():
    lvl = {"stop_level": -150.0, "stop_kind": "money", "target_level": 37.5}
    ledger = paper.paper_rows([{"trade_id": "t", "status": "OPEN", **lvl}])[0]
    cap = captured.captured_rows([{"signal_id": "s", **lvl}])[0]
    acct = portfolio.position_rows([{"position_id": 1, **lvl}])[0]
    for row in (ledger, cap, acct):
        assert (row["stop_level"], row["stop_kind"], row["target_level"]) == \
            (-150.0, "Loss stop", 37.5)


def test_every_kind_the_service_sends_has_words():
    """The same set is pinned on the service side
    (services/options_svc/tests/test_exit_levels.py::STOP_KINDS)."""
    assert set(exit_columns.KIND_WORDS) == {"money", "break-even", "trail", "debit", "idea"}
