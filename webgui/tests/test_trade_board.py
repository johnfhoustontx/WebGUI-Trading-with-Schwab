"""Tests for the Rank Board page's pure display builders (Phase 5).

The board is a Tier-1 reader of ``cache:trade:rank_board``; only its pure
transforms and the ``render`` callable are exercised here.

The two things the page must get right are both about NOT overclaiming:
an empty or relative short pool has to say which of the several reasons it is,
and a board that ranks by a composite which is 48% volatility has to say so
where someone reading the ordering will see it.
"""
from pages import trade_board


_BOARD = {
    "as_of": "2026-08-22", "model_version": "2026-08-22", "regime_key": "all",
    "risk_share": 0.476, "horizon_days": 20, "n": 20,
    "thin_cross_section": False,
    "rows": [
        {"symbol": "AAA", "composite": 0.81, "percentile": 90, "band": 2,
         "verdict": "BUY", "expected_fwd": 0.016, "hit_rate": 0.523,
         "decile": 10, "pool": "long", "gates": [], "gated_long": False,
         "gated_short": False, "disqualified": False},
        {"symbol": "BBB", "composite": 0.44, "percentile": 70, "band": 1,
         "verdict": "HOLD", "expected_fwd": 0.0, "hit_rate": 0.49,
         "decile": 8, "pool": "", "gates": ["earnings in 3 days"],
         "gated_long": True, "gated_short": True, "disqualified": True},
        {"symbol": "ZZZ", "composite": -0.77, "percentile": 10, "band": 0,
         "verdict": "SELL", "expected_fwd": -0.008, "hit_rate": 0.43,
         "decile": 1, "pool": "short", "gates": ["squeeze risk (17.1 days)"],
         "gated_long": False, "gated_short": True, "disqualified": True},
    ],
    "long_pool": ["AAA"], "short_pool": ["ZZZ"],
    "market_filter": {
        "long": {"state": "cleared", "reasons": []},
        "short": {"state": "relative_only",
                  "reasons": ["SPY above a rising 200-DMA"]}},
    "short_expression": "relative",
    "gates_evaluated": ["earnings inside the 20-day horizon (both sides)"],
}


class TestRows:
    def test_a_row_formats_its_numbers_for_display(self):
        rows = trade_board.board_rows(_BOARD)
        top = rows[0]
        assert top["symbol"] == "AAA"
        assert top["composite"] == "+0.81"
        assert top["percentile"] == "90th"
        assert "+1.6%" in top["expected_fwd"]

    def test_a_gated_row_shows_its_reasons_rather_than_vanishing(self):
        rows = {r["symbol"]: r for r in trade_board.board_rows(_BOARD)}
        assert "earnings in 3 days" in rows["BBB"]["gates"]

    def test_an_ungated_row_renders_an_em_dash_not_an_empty_cell(self):
        rows = {r["symbol"]: r for r in trade_board.board_rows(_BOARD)}
        assert rows["AAA"]["gates"] == "—"

    def test_missing_numbers_degrade_rather_than_crash(self):
        board = {"rows": [{"symbol": "X"}]}
        row = trade_board.board_rows(board)[0]
        assert row["composite"] == "—" and row["percentile"] == "—"

    def test_no_rows_is_an_empty_list(self):
        assert trade_board.board_rows({}) == []
        assert trade_board.board_rows(None) == []


class TestPoolHeadlines:
    def test_the_long_headline_counts_the_pool(self):
        head = trade_board.pool_headline(_BOARD, "long")
        assert "1" in head["title"]

    def test_a_relative_only_short_names_the_MARKET_FILTER_as_the_reason(self):
        """The exit criterion. A bottom-decile name in an uptrend is predicted
        to LAG, not to fall — and if the page does not say so, the pool reads
        as a list of shorts the tape has actually refused."""
        head = trade_board.pool_headline(_BOARD, "short")
        assert "relative" in head["note"].lower()
        assert "SPY above a rising 200-DMA" in head["note"]

    def test_a_cleared_short_side_says_directional_instead(self):
        board = dict(_BOARD, short_expression="directional",
                     market_filter={"long": {"state": "cleared", "reasons": []},
                                    "short": {"state": "cleared", "reasons": []}})
        assert "relative" not in trade_board.pool_headline(board, "short")["note"].lower()

    def test_a_thin_cross_section_is_distinguished_from_an_empty_pool(self):
        """Six names have no bottom decile. Reading that as 'no short
        candidates today' would be a market claim made from a sample size."""
        board = dict(_BOARD, thin_cross_section=True, short_pool=[], long_pool=[])
        note = trade_board.pool_headline(board, "short")["note"].lower()
        assert "cross-section" in note or "too few" in note


class TestTheBoardStatesWhatItRanksBy:
    def test_the_exposure_line_carries_the_share(self):
        line = trade_board.board_exposure_note(_BOARD)
        assert "48%" in line

    def test_it_warns_that_the_TOP_of_the_ranking_is_the_high_beta_end(self):
        line = trade_board.board_exposure_note(_BOARD).lower()
        assert "beta" in line or "volatil" in line

    def test_an_unknown_share_says_nothing_rather_than_implying_zero(self):
        assert trade_board.board_exposure_note({"risk_share": None}) == ""
        assert trade_board.board_exposure_note(None) == ""


class TestTheGateSubsetIsDisclosed:
    def test_the_page_names_which_gates_were_checked(self):
        note = trade_board.gates_note(_BOARD)
        assert "earnings" in note.lower()

    def test_no_gate_list_yields_no_claim(self):
        assert trade_board.gates_note({}) == ""


class TestMeta:
    def test_the_meta_line_carries_version_and_as_of(self):
        line = trade_board.meta_line(_BOARD)
        assert "2026-08-22" in line
        assert "20" in line          # n

    def test_render_is_callable(self):
        assert callable(trade_board.render)


# ── Empty has kinds (found live, 2026-08-22) ─────────────────────────────────
# The first live build rendered zero rows because the cached snapshot was in the
# documented LEGACY flat shape — values but no symbol names. On screen that was
# indistinguishable from "the market offered nothing today", which is the exact
# confusion the pool notes exist to prevent.

class TestEmptyStates:
    def test_a_healthy_board_shows_no_status_banner(self):
        assert trade_board.status_note(_BOARD) == ""

    def test_a_legacy_snapshot_says_the_SHAPE_is_the_problem(self):
        note = trade_board.status_note(dict(_BOARD, status="legacy_snapshot",
                                            rows=[], n=0)).lower()
        assert "snapshot" in note
        assert "market" in note        # explicitly disclaims a market reading

    def test_a_missing_snapshot_is_a_different_message(self):
        a = trade_board.status_note(dict(_BOARD, status="no_snapshot"))
        b = trade_board.status_note(dict(_BOARD, status="legacy_snapshot"))
        assert a and b and a != b

    def test_a_missing_artifact_points_at_the_model_not_the_data(self):
        note = trade_board.status_note(dict(_BOARD, status="no_artifact")).lower()
        assert "model" in note

    def test_an_unknown_status_does_not_invent_a_message(self):
        assert trade_board.status_note(dict(_BOARD, status="wat")) == ""
        assert trade_board.status_note(None) == ""


# ── The model paper book (Phase 6) ───────────────────────────────────────────
# The board says what the model thinks; the book says what following it did.
# Long and short are shown separately because this model's short side is usually
# expressed RELATIVE to SPY, and averaging the two would hide that.

_BOOK = {
    "as_of": "2026-08-22",
    "positions": [
        {"symbol": "MU", "side": "long", "status": "open", "entry": 100.0,
         "last": 108.0, "pnl_pct": 0.08, "expression": "directional",
         "opened_on": "2026-08-01", "time_stop_on": "2026-08-29"},
        {"symbol": "TMO", "side": "short", "status": "closed", "entry": 500.0,
         "last": 505.0, "pnl_pct": -0.01, "expression": "relative",
         "opened_on": "2026-07-01", "close_reason": "time"},
    ],
    "summary": {
        "long": {"n": 4, "mean_pnl": 0.021, "hit_rate": 0.5, "total_pnl": 0.084},
        "short": {"n": 3, "mean_pnl": -0.008, "hit_rate": 0.33, "total_pnl": -0.024},
        "open": 1, "closed": 7,
    },
}


def test_book_rows_format_the_position():
    rows = {r["symbol"]: r for r in trade_board.book_rows(_BOOK)}
    assert rows["MU"]["pnl"] == "+8.0%"
    assert rows["MU"]["status"] == "open"
    assert rows["TMO"]["expression"] == "relative"


def test_a_position_with_no_mark_shows_a_dash():
    rows = trade_board.book_rows({"positions": [{"symbol": "X", "side": "long"}]})
    assert rows[0]["pnl"] == "—"


def test_the_book_summary_reports_each_side_separately():
    line = trade_board.book_summary_line(_BOOK)
    assert "+2.1%" in line and "-0.8%" in line


def test_a_side_with_no_closed_trades_says_so_rather_than_zero():
    book = {"summary": {"long": {"n": 2, "mean_pnl": 0.01, "hit_rate": 0.5},
                        "short": {"n": 0, "mean_pnl": None, "hit_rate": None},
                        "open": 0, "closed": 2}}
    line = trade_board.book_summary_line(book)
    assert "—" in line or "no closed" in line.lower()


def test_an_empty_book_renders_nothing_rather_than_a_row_of_zeros():
    assert trade_board.book_summary_line({}) == ""
    assert trade_board.book_summary_line(None) == ""
    assert trade_board.book_rows(None) == []


def test_the_book_discloses_that_it_trades_the_underlying():
    """A deliberate deviation from the phase plan, so it has to be visible
    where the numbers are — otherwise the P&L reads as options P&L."""
    note = trade_board.book_note().lower()
    assert "underlying" in note or "stock" in note
