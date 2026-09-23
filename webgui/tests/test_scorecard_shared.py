"""The shared scorecard vocabulary, and the manual book's own scorecard (C5).

Design: docs/plans/2026-09-12-manual-scorecard-design.md.

The formatters live in ``pages/scorecard.py``. The manual book is the one that
trades every captured signal, and it had no track record on screen at all.

⚠ `by_exit_reason` is the new axis, and it is not decoration: replaying the
profit-lock ladder turned up that `MANUAL_CLOSE` accounts for **+$50,102** of the
captured book's reported P&L against +$11,664 for every other reason combined,
and that 130 of its 388 rows book exactly the full credit. Split by symbol and
strategy, that is invisible; split by how a trade ENDED, it is the first row.
"""
import pytest

from pages import scorecard


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


# ── the formatters ──────────────────────────────────────────────────────────

def test_money_renders_an_explicit_sign_except_at_zero():
    assert scorecard.money(120.5) == "+$120.50"
    assert scorecard.money(-120.5) == "-$120.50"
    assert scorecard.money(0) == "$0.00"
    assert scorecard.money(None) == "$0.00"


# ── the Paper Account page's own scorecard (C5) ─────────────────────────────

def test_the_paper_account_page_builds_a_scorecard_line():
    """A one-line summary the page can render above the tables, from the SAME
    scorecard dict ``book_perf`` builds. Its subject is the manual book, which
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
