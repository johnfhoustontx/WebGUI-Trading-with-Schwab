"""The captured-trade score: the read window and the row builder.

``/options/captured`` tracks Market Scanner signals to see whether they would
have worked, and until now it reported only TODAY. These are the pieces that let
the EOD report score them over Daily / Weekly (WTD) / MTD, from 2026-09-01.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\options_svc\\tests\\test_captured_performance.py -v
"""
import datetime as dt

from services.options_svc import compute


# ── the window ───────────────────────────────────────────────────────────────
def test_the_window_is_the_EARLIER_of_the_week_and_month_start():
    """⚠ The trap this feature turns on.

    Month-to-date LOOKS like the right bound, because MTD is the widest row in
    the table. It is wrong: on Thursday 1 October the WTD row starts Monday
    28 September, which is before the month began. Bounding the read at the
    month start would return no rows for 28-30 September, and the weekly row
    would silently under-count on the first days of every month with nothing on
    screen to say it had.
    """
    lo, hi = compute.captured_score_window(dt.date(2026, 10, 1))
    assert lo == dt.date(2026, 9, 28)          # the Monday, not 1 October
    assert hi == dt.date(2026, 10, 1)


def test_the_window_is_the_month_start_when_that_is_the_earlier_one():
    """Mid-month the month start wins, which is the common case."""
    lo, hi = compute.captured_score_window(dt.date(2026, 9, 16))
    assert lo == dt.date(2026, 9, 1)           # week began Mon 09-14
    assert hi == dt.date(2026, 9, 16)


def test_a_monday_reads_from_itself_not_the_week_before():
    """The week starts ON Monday; an off-by-one here would pull in the previous
    week's closes and inflate every weekly row."""
    lo, _hi = compute.captured_score_window(dt.date(2026, 9, 14))
    assert lo == dt.date(2026, 9, 1)           # month start still earlier
    lo2, _ = compute.captured_score_window(dt.date(2026, 10, 5))   # a Monday
    assert lo2 == dt.date(2026, 10, 1)         # month start, not 09-28


def test_the_window_never_reaches_before_the_epoch():
    """868 outcomes reach back to 2026-06-15 and none of them belong to this
    score. The period windows never reach them on their own; this floor is what
    guarantees they cannot."""
    assert compute.CAPTURED_SCORE_EPOCH == dt.date(2026, 9, 1)
    lo, _hi = compute.captured_score_window(dt.date(2026, 9, 2))
    assert lo == dt.date(2026, 9, 1)
    # A date whose week began in August still cannot read into August.
    lo2, _ = compute.captured_score_window(dt.date(2026, 9, 1))     # a Tuesday
    assert lo2 >= compute.CAPTURED_SCORE_EPOCH


# ── the rows ─────────────────────────────────────────────────────────────────
_RAW = {
    "signal_id": "s1", "symbol": "SPY", "strategy": "PCS",
    "scanner_type": "0DTE", "first_seen_ts": "2026-09-02T10:00:00-05:00",
    "close_ts": "2026-09-03T14:00:00-05:00", "close_date": "2026-09-03",
    "entry_credit": 0.55, "realized_pnl": 25.0, "exit_reason": "MONEY_STOP",
}


def test_rows_put_the_credit_on_the_SAME_one_contract_basis_as_the_pnl():
    """``close_signal_manually`` computes ``(entry_credit - exit_value) * 100``,
    so realized P&L is one-contract dollars. The credit column must share that
    basis or the two numbers on one row describe different position sizes.

    A captured signal is never sized — /desk refuses to print a quantity for one
    on the grounds that a printed number would be inventing a position — so one
    contract is the only honest basis either figure has.
    """
    row = compute.captured_perf_rows([_RAW])[0]
    assert row["entry_credit_total"] == 55.0        # 0.55 * 100
    assert row["realized_pnl"] == 25.0              # already one-contract


def test_rows_carry_both_dates_and_the_scanner_type():
    row = compute.captured_perf_rows([_RAW])[0]
    assert row["first_seen_ts"] == "2026-09-02T10:00:00-05:00"
    assert row["close_ts"] == "2026-09-03T14:00:00-05:00"
    assert row["trade_type"] == "0DTE"
    assert row["status"] == "CLOSED"


def test_a_row_with_no_credit_yields_no_credit_rather_than_zero():
    """$0.00 would be a reading nobody took — the rule the whole app runs on."""
    row = compute.captured_perf_rows([{**_RAW, "entry_credit": None}])[0]
    assert row["entry_credit_total"] is None
    assert row["realized_pnl"] == 25.0


def test_rows_degrade_rather_than_raise_on_a_malformed_outcome():
    """This feeds a nightly report; one bad row must not cost the section."""
    assert compute.captured_perf_rows(None) == []
    assert compute.captured_perf_rows([]) == []
    assert compute.captured_perf_rows([None, "nonsense", 7]) == []
    assert len(compute.captured_perf_rows([_RAW, None, _RAW])) == 2


def test_a_non_finite_pnl_is_dropped_to_None_not_carried():
    """A NaN summed into a realized total makes every comparison after it False
    — the documented failure mode this repo keeps re-finding."""
    row = compute.captured_perf_rows([{**_RAW, "realized_pnl": float("nan")}])[0]
    assert row["realized_pnl"] is None


# ── publishing ───────────────────────────────────────────────────────────────
def test_the_manage_cycle_publishes_the_score_beside_the_closed_view():
    """It rides the path that already republishes the captured views, so a close
    updates the score in the same breath it updates the closed-today list. A
    score on its own schedule could sit beside a closed-today view it disagreed
    with, and a reader would have no way to tell which was stale."""
    import inspect

    from services.options_svc import handlers
    src = inspect.getsource(handlers.run_captured_manage_and_publish)
    assert "publish_captured_closed(bus)" in src
    assert "publish_captured_performance(bus)" in src


def test_the_published_view_carries_the_rows_and_the_window(monkeypatch):
    from shared.bus import Bus

    from services.options_svc import handlers
    monkeypatch.setattr(
        handlers.compute, "captured_performance",
        lambda: {"rows": [{"symbol": "SPY"}],
                 "window": {"start": "2026-09-01", "end": "2026-09-04"}})
    bus = Bus(fake=True)
    handlers.publish_captured_performance(bus)
    payload = bus.cache_get(handlers.CACHE_CAPTURED_PERF).payload
    assert payload["rows"] == [{"symbol": "SPY"}]
    assert payload["window"] == {"start": "2026-09-01", "end": "2026-09-04"}
