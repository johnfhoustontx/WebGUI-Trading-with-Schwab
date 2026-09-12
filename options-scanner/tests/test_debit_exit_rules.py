"""D3: exit rules for long options and debit spreads.

⚠ **The credit rules are not merely absent for a debit — they are INVERTED, and
the sign is why.** ``recommend`` computes ``credit_total = entry_credit x 100``,
and a debit trade stores ``entry_credit`` as the NEGATIVE per-share debit. So for
a $2.00 debit (``credit_total = -200``):

    rule 1  pnl <= -stop_mult x credit_total  ->  pnl <= +400   CUT/MONEY_STOP
    rule 5  pnl >= tp_frac x credit_total     ->  pnl >= -100   TAKE_PROFIT

Measured: handing a healthy long call to ``recommend`` returns **CUT/MONEY_STOP at
every P&L from -$199 to +$399** — it would close the position on its first manage
tick — and TAKE_PROFIT above that. Nothing routes a debit through it today (the
ledger has no rule evaluation at all), so it is latent; giving the ledger exits
without fixing this first would have been the bug.

``recommend`` therefore DISPATCHES on direction, so no path reaches the credit
rules with a negative credit. The debit rules are:

1. an OPTIONAL percent-of-debit stop (``debit_stop_frac``, OFF by default —
   the practitioner sources close debit spreads before expiry rather than
   stopping them out, so a level here would be invention);
2. the profit target, on the denominator the structure actually has — a fraction
   of MAX PROFIT for a bounded vertical (which is what ``tp_frac`` of the credit
   already means on the credit side), a fraction of the DEBIT PAID for a long
   option, which has no max profit at all;
3. a time exit at ``exit_dte``, firing whether the trade is up or down — and
   ⚠ ONLY when the position had a longer horizon than that to begin with. Every
   debit the Market Scanner's Directional tab can produce arrives at DTE 0-15
   (the 0-DTE window is 0-4, the swing window 5-15), so an unguarded 21-DTE exit
   would close 100% of them on the tick after they opened.
"""
import pytest

import signal_recommender as sr


def _debit(pnl, *, dte=30, dte_at_entry=45, max_profit=None, debit=200.0,
           strategy="LONG_CALL"):
    """A debit ctx. ``entry_debit``/``max_profit``/``unrealized_pnl`` are all
    PER-CONTRACT dollars, so they are directly comparable."""
    return {"strategy": strategy, "direction": "DEBIT",
            "entry_credit": -debit / 100.0, "entry_debit": debit,
            "max_profit": max_profit, "unrealized_pnl": pnl,
            "dte_remaining": dte, "dte_at_entry": dte_at_entry,
            "current_short_delta": None}


def _credit(pnl, *, dte=30, credit=2.00, strategy="PCS"):
    return {"strategy": strategy, "entry_credit": credit,
            "unrealized_pnl": pnl, "dte_remaining": dte,
            "current_short_delta": None}


# ── the landmine: the credit rules must not reach a debit ───────────────────

@pytest.mark.parametrize("pnl", [0, -50, -99, -100, -150, -199, 0.0])
def test_a_fresh_debit_position_is_NOT_money_stopped(pnl):
    """⚠ The measured bug. Every one of these returned CUT/MONEY_STOP."""
    r = sr.recommend(_debit(pnl))
    assert r["action"] == "HOLD", r
    assert r["code"] != "MONEY_STOP"


def test_a_debit_barely_underwater_is_not_TAKEN_as_a_profit():
    """rule 5 read ``pnl >= 0.5 x -200`` = ``pnl >= -100``, so a trade down $99
    reported >=50% captured."""
    r = sr.recommend(_debit(-99))
    assert r["action"] == "HOLD" and r["code"] != "TARGET_HIT"


def test_a_debit_is_dispatched_on_DIRECTION_not_only_on_a_negative_credit():
    """``direction == "DEBIT"`` is the ledger's own field and the primary key;
    a negative ``entry_credit`` is the belt-and-braces second one, because the
    sign is what actually breaks the arithmetic."""
    no_flag = _debit(-150)
    no_flag.pop("direction")
    assert sr.recommend(no_flag)["action"] == "HOLD"


def test_the_delta_stop_cannot_fire_on_a_debit():
    """A long call's own delta is not a short-strike breach, and a debit trade
    records no short delta — but a debit VERTICAL has a short leg, so a stray
    value must not be read as adverse drift."""
    ctx = _debit(-50, strategy="BULL_CALL", max_profit=300.0)
    ctx["current_short_delta"] = 0.80
    r = sr.recommend(ctx)
    assert r["code"] != "DELTA_STOP", r


# ── rule 2: the profit target, on the right denominator ────────────────────

def test_a_debit_VERTICAL_targets_half_of_its_MAX_PROFIT():
    """$2.00 paid on a $5 width: max profit $300/contract, so the target is
    +$150. This mirrors the credit side, where tp_frac of the credit IS the
    fraction of max profit."""
    assert sr.recommend(_debit(+149, strategy="BULL_CALL",
                               max_profit=300.0))["action"] == "HOLD"
    r = sr.recommend(_debit(+150, strategy="BULL_CALL", max_profit=300.0))
    assert r["action"] == "TAKE_PROFIT" and r["code"] == "TARGET_HIT"


def test_a_LONG_OPTION_targets_half_of_the_DEBIT_PAID():
    """It has no max profit to take a fraction of (``unbounded``), so the only
    denominator that exists is the capital committed: +$100 on $200 paid."""
    assert sr.recommend(_debit(+99))["action"] == "HOLD"
    r = sr.recommend(_debit(+100))
    assert r["action"] == "TAKE_PROFIT" and r["code"] == "TARGET_HIT"


def test_the_two_denominators_are_genuinely_different_numbers():
    """⚠ The reason this is a decision and not an implementation detail: on the
    SAME position, 50% of max profit and 50% of the debit are $150 and $100."""
    at_100 = sr.recommend(_debit(+100, strategy="BULL_CALL", max_profit=300.0))
    assert at_100["action"] == "HOLD"          # 100 is half the DEBIT, not the target
    assert sr.recommend(_debit(+100))["action"] == "TAKE_PROFIT"   # ...but it is for a long call


@pytest.mark.parametrize("bad", [None, 0.0, -50.0, float("nan"), "300", True])
def test_an_UNUSABLE_max_profit_falls_back_to_the_debit_paid(bad):
    """A missing or nonsense max profit must not make the target unreachable (or,
    with a zero, fire at break-even). ``True`` is in here because ``float(True)``
    is 1.0 — the documented bool trap."""
    ctx = _debit(+100, strategy="BULL_CALL", max_profit=bad)
    assert sr.recommend(ctx)["action"] == "TAKE_PROFIT"


# ── rule 3: the time exit, and the at-entry guard ──────────────────────────

def test_the_time_exit_fires_at_the_threshold_even_while_PROFITABLE():
    """Unlike ``manage_dte`` on the credit side, this is not profit-conditional:
    the sourced rule is to be out before the final weeks either way."""
    r = sr.recommend(_debit(+20, dte=21, dte_at_entry=45))
    assert r["code"] == "TIME_EXIT" and r["action"] == "TAKE_PROFIT"


def test_the_time_exit_fires_while_UNDERWATER_and_reads_as_a_CUT():
    r = sr.recommend(_debit(-60, dte=21, dte_at_entry=45))
    assert r["code"] == "TIME_EXIT" and r["action"] == "CUT"


def test_the_time_exit_does_not_fire_above_the_threshold():
    assert sr.recommend(_debit(-60, dte=22, dte_at_entry=45))["action"] == "HOLD"


@pytest.mark.parametrize("entry_dte", [0, 1, 3, 4, 15, 21])
def test_a_position_opened_INSIDE_the_window_gets_NO_time_exit(entry_dte):
    """⚠ The guard that makes the rule shippable. The Directional tab's debits
    arrive at DTE 0-4 and 5-15, so without this every one of them would be closed
    on the tick after it opened — a rule that fires at entry is worse than none.
    Their target and the expiry settlement still bound them."""
    r = sr.recommend(_debit(-10, dte=entry_dte, dte_at_entry=entry_dte))
    assert r["action"] == "HOLD", r


def test_a_short_dated_debit_still_takes_its_TARGET():
    """The short-dated inventory is not unmanaged — the target is what bounds it."""
    r = sr.recommend(_debit(+100, dte=1, dte_at_entry=3))
    assert r["action"] == "TAKE_PROFIT" and r["code"] == "TARGET_HIT"


def test_an_unknown_dte_at_entry_declines_the_time_exit():
    """Absence must not be read as a long horizon. Pre-D3 ledger rows have no
    ``dte_at_entry`` in ctx, and closing them on the first tick would be the
    same failure the guard exists to prevent."""
    ctx = _debit(-10, dte=5)
    ctx["dte_at_entry"] = None
    assert sr.recommend(ctx)["action"] == "HOLD"


# ── rule 1: the optional percent-of-debit stop ─────────────────────────────

def test_there_is_NO_loss_stop_by_default():
    """Sourced: the practitioners close debit spreads before expiry rather than
    stopping them out. A position down 90% of its debit still holds."""
    assert sr.recommend(_debit(-180))["action"] == "HOLD"


def test_a_configured_percent_of_debit_stop_fires(monkeypatch):
    """The lever, off by default. 0.60 = cut once 60% of the debit is gone."""
    import shared.trade_mgmt as tm
    base = tm.structure_rules("LONG_CALL")
    monkeypatch.setattr(sr._trade_mgmt, "structure_rules",
                        lambda s: {**base, "debit_stop_frac": 0.60})
    assert sr.recommend(_debit(-119))["action"] == "HOLD"
    r = sr.recommend(_debit(-120))
    assert r["action"] == "CUT" and r["code"] == "DEBIT_STOP"


def test_the_configured_stop_BEATS_the_time_exit_when_both_hold(monkeypatch):
    """A hard floor is checked first, exactly as on the credit side — and this is
    the one coincidence that can really occur: a trade down 70% of its debit that
    has also reached the time-exit DTE. Both would close it; the reason recorded
    must be the stop.

    (The stop and the TARGET can never hold together — one needs a loss and the
    other a profit — so their order is unobservable and is not asserted.)"""
    import shared.trade_mgmt as tm
    base = tm.structure_rules("LONG_CALL")
    monkeypatch.setattr(sr._trade_mgmt, "structure_rules",
                        lambda s: {**base, "debit_stop_frac": 0.60})
    r = sr.recommend(_debit(-140, dte=21, dte_at_entry=45))
    assert r["action"] == "CUT" and r["code"] == "DEBIT_STOP", r


# ── absence and robustness ─────────────────────────────────────────────────

def test_no_pnl_reading_holds_rather_than_acting():
    """⚠ ``None`` means "not marked", and the repo's rule is that it must never
    read as zero: a 0 P&L would satisfy a 0-threshold stop."""
    ctx = _debit(None)
    assert sr.recommend(ctx)["action"] == "HOLD"


def test_a_zero_debit_row_cannot_make_the_target_fire_at_breakeven():
    """A malformed row (no debit recorded) has no denominator, so it holds."""
    assert sr.recommend(_debit(+1, debit=0.0))["action"] == "HOLD"


def test_it_never_raises_on_a_junk_debit_ctx():
    for bad in ({"direction": "DEBIT"},
                {"direction": "DEBIT", "entry_credit": -2.0},
                {"direction": "DEBIT", "entry_debit": "nope", "entry_credit": -2.0}):
        r = sr.recommend(bad)
        assert r["action"] in ("HOLD", "CUT", "TAKE_PROFIT")


# ── the credit path is untouched (controls) ────────────────────────────────

def test_a_credit_spread_still_money_stops_at_2x():
    assert sr.recommend(_credit(-399))["action"] == "HOLD"
    r = sr.recommend(_credit(-401))
    assert r["action"] == "CUT" and r["code"] == "MONEY_STOP"


def test_a_credit_spread_still_takes_profit_at_half_the_credit():
    assert sr.recommend(_credit(+99))["action"] == "HOLD"
    r = sr.recommend(_credit(+100))
    assert r["action"] == "TAKE_PROFIT" and r["code"] == "TARGET_HIT"


def test_a_credit_spread_never_reports_the_debit_codes():
    for pnl in (-401, -100, 0, +100, +300):
        assert sr.recommend(_credit(pnl))["code"] not in ("TIME_EXIT", "DEBIT_STOP")
