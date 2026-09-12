"""The shared scorecard vocabulary, and the manual book's own scorecard (C5).

Design: docs/plans/2026-09-12-manual-scorecard-design.md.

Two pages draw the same scorecard now — Claude Trades and Paper Account — so the
PURE builders moved out of ``pages/driver.py`` into ``pages/scorecard.py``. The
manual book is the one that trades every captured signal, and it had no track
record on screen at all.

⚠ `by_exit_reason` is the new axis, and it is not decoration: replaying the
profit-lock ladder turned up that `MANUAL_CLOSE` accounts for **+$50,102** of the
captured book's reported P&L against +$11,664 for every other reason combined,
and that 130 of its 388 rows book exactly the full credit. Split by symbol and
strategy, that is invisible; split by how a trade ENDED, it is the first row.
"""
import pytest

from pages import driver, scorecard


def _perf():
    return {
        "total_trades": 10, "open": 3, "closed": 7, "wins": 5, "losses": 2,
        "win_rate": 0.7143, "realized_pnl": 420.0, "open_unrealized": -15.0,
        "total_pnl": 405.0, "avg_win": 120.0, "avg_loss": -60.0,
        "profit_factor": 2.0,
        "best": {"symbol": "MU", "realized_pnl": 300.0},
        "worst": {"symbol": "ORCL", "realized_pnl": -80.0},
        "by_symbol": [{"symbol": "MU", "trades": 4, "pnl": 380.0, "win_rate": 0.75}],
        "by_strategy": [{"strategy": "PCS", "trades": 7, "pnl": 420.0, "win_rate": 0.71}],
        "by_exit_reason": [
            {"exit_reason": "MANUAL_CLOSE", "trades": 4, "pnl": 500.0, "win_rate": 1.0},
            {"exit_reason": "MONEY_STOP", "trades": 3, "pnl": -80.0, "win_rate": 0.0},
        ],
    }


# ── the move kept the driver page working ───────────────────────────────────

def test_the_driver_page_still_exposes_every_builder():
    """Imported by NAME, so ``driver.<fn>`` resolves for the page body and the 25
    existing assertions in test_driver_monitor.py."""
    for name in ("scorecard_headline_chips", "scorecard_quality_chips",
                 "scorecard_symbol_rows", "scorecard_strategy_rows",
                 "best_worst_text", "pnl_color", "pnl_class"):
        assert getattr(driver, name) is getattr(scorecard, name), name


def test_the_palette_moved_VERBATIM():
    """⚠ The one thing a shared module can quietly break. These are the driver
    page's own hexes, not the Simulator's payoff green/red."""
    assert (scorecard.PNL_GREEN, scorecard.PNL_RED, scorecard.PNL_NEUTRAL) == \
        ("#66bb6a", "#ef5350", "#bdbdbd")
    assert driver.PNL_GREEN == scorecard.PNL_GREEN


def test_the_driver_page_no_longer_DEFINES_the_builders():
    """A second definition would shadow the import and diverge silently — which is
    exactly what a stray ``PNL_GREEN = ...`` did on the first attempt at this."""
    import inspect
    src = inspect.getsource(driver)
    for banned in ("def scorecard_headline_chips", "def pnl_color(",
                   "PNL_GREEN, PNL_RED, PNL_NEUTRAL ="):
        assert banned not in src, banned


# ── the new exit-reason axis ────────────────────────────────────────────────

def test_exit_reason_rows_render_like_the_other_breakdowns():
    rows = scorecard.scorecard_exit_reason_rows(_perf())
    assert [r["exit_reason"] for r in rows] == ["MANUAL_CLOSE", "MONEY_STOP"]
    assert rows[0]["pnl"] == "+$500.00"
    assert rows[1]["pnl"] == "-$80.00"
    assert rows[0]["win_rate"] == "100.0%"


def test_exit_reason_rows_are_empty_when_the_payload_predates_the_field():
    """Redis persists these views across a restart, and the driver's scorecard is
    read from a cache that may have been written before this field existed."""
    p = _perf()
    p.pop("by_exit_reason")
    assert scorecard.scorecard_exit_reason_rows(p) == []
    assert scorecard.scorecard_exit_reason_rows({}) == []
    assert scorecard.scorecard_exit_reason_rows(None) == []


def test_a_missing_exit_reason_renders_as_a_question_mark_not_blank():
    rows = scorecard.scorecard_exit_reason_rows(
        {"by_exit_reason": [{"trades": 1, "pnl": 5.0, "win_rate": 1.0}]})
    assert rows[0]["exit_reason"] == "?"


# ── the formatters ──────────────────────────────────────────────────────────

def test_zero_and_unknown_pnl_are_neither_green_nor_red():
    """A fresh account must read as flat, not as a loss."""
    for v in (0, 0.0, None, "x", [], True):
        assert scorecard.pnl_color(v) == scorecard.PNL_NEUTRAL, v


def test_money_renders_an_explicit_sign_except_at_zero():
    assert scorecard.money(120.5) == "+$120.50"
    assert scorecard.money(-120.5) == "-$120.50"
    assert scorecard.money(0) == "$0.00"
    assert scorecard.money(None) == "$0.00"


def test_a_profit_factor_of_None_is_an_em_dash_not_zero():
    """None means "no losses yet"; 0.00 would read as "no edge"."""
    chips = dict(scorecard.scorecard_quality_chips({**_perf(), "profit_factor": None}))
    assert chips["Profit factor"] == "—"


def test_best_worst_is_empty_when_nothing_has_closed():
    assert scorecard.best_worst_text({"total_trades": 2}) == ""


def test_best_worst_reads_both_ends():
    assert scorecard.best_worst_text(_perf()) == \
        "Best MU +$300.00 · Worst ORCL -$80.00"


# ── the Paper Account page's own scorecard (C5) ─────────────────────────────

def test_the_paper_account_page_builds_a_scorecard_line():
    """A one-line summary the page can render above the tables, from the SAME
    scorecard dict the driver page draws. Its subject is the manual book, which
    had no track record on screen at all."""
    from pages.options import portfolio
    line = portfolio.scorecard_text(_perf())
    assert "7 closed" in line
    assert "71.4%" in line
    assert "+$420.00" in line


def test_the_scorecard_line_is_blank_before_anything_closes():
    """No closed trades → no track record. A "0.0% win rate" would read as a
    losing book rather than an empty one."""
    from pages.options import portfolio
    assert portfolio.scorecard_text({"total_trades": 3, "closed": 0}) == ""
    assert portfolio.scorecard_text({}) == ""
    assert portfolio.scorecard_text(None) == ""


def test_the_scorecard_line_names_the_profit_factor_when_there_is_one():
    from pages.options import portfolio
    assert "2.00" in portfolio.scorecard_text(_perf())


def test_the_scorecard_line_omits_an_undefined_profit_factor():
    """None means "no losses yet" — printing "—" mid-sentence reads as an error."""
    from pages.options import portfolio
    line = portfolio.scorecard_text({**_perf(), "profit_factor": None})
    assert "profit factor" not in line.lower()
    assert "71.4%" in line


# ── C4: the book's Greeks line ───────────────────────────────────────────────

def _greeks(**over):
    base = {"net_delta": 1.44, "net_gamma": -0.048, "net_theta": 24.0,
            "net_vega": -0.48, "positions_priced": 12, "positions_total": 12}
    base.update(over)
    return base


def test_the_greeks_line_names_theta_in_DOLLARS_A_DAY():
    """Theta is the additive one and the number a premium seller reads daily."""
    from pages.options import portfolio
    line = portfolio.greeks_text(_greeks())
    assert "+$24.00 a day" in line


def test_the_greeks_line_gives_delta_a_DIRECTION_not_a_hedge_ratio():
    """⚠ Without a beta there is no sense in which MU's delta and PG's delta add
    up, so the line must not present the total as a hedge ratio."""
    from pages.options import portfolio
    line = portfolio.greeks_text(_greeks())
    assert "hedge" not in line.lower()
    assert "long" in line.lower()


def test_a_negative_delta_book_reads_as_SHORT():
    from pages.options import portfolio
    assert "short" in portfolio.greeks_text(_greeks(net_delta=-1.2)).lower()


def test_a_flat_delta_book_reads_as_FLAT_not_as_missing():
    """Zero is a real reading — a balanced condor — and must not look absent."""
    from pages.options import portfolio
    line = portfolio.greeks_text(_greeks(net_delta=0.0))
    assert "flat" in line.lower()


def test_the_line_says_when_it_covers_only_PART_of_the_book():
    """A total that silently omits positions is worse than one that says so."""
    from pages.options import portfolio
    line = portfolio.greeks_text(_greeks(positions_priced=9, positions_total=12))
    assert "9 of 12" in line


def test_the_line_stays_quiet_when_the_whole_book_is_priced():
    from pages.options import portfolio
    assert "of 12" not in portfolio.greeks_text(_greeks())


def test_the_line_is_blank_when_nothing_is_priced():
    """An unpriced book must not read as a flat one."""
    from pages.options import portfolio
    assert portfolio.greeks_text(
        {"net_delta": None, "net_theta": None,
         "positions_priced": 0, "positions_total": 3}) == ""
    assert portfolio.greeks_text({}) == ""
    assert portfolio.greeks_text(None) == ""
