"""What an open position knows about itself — gap assessment B5 and B7.

Design: docs/plans/2026-09-12-position-awareness-design.md.

Two additions to the Rescue board's read of an open position, and the weight of
each is the whole point:

**B7 — a report inside the position's life** is a heat MODIFIER, beside the GEX
and regime ones the function's docstring already calls *"never standalone
triggers"*. Deliberately not an escalation and not a push. Measured over the 132
closed captured signals whose whole life sits inside the earnings calendar's
coverage window, the ones that spanned a report did **better** — mean R −0.059 at
a 14.9% win rate against −0.241 and 12.3% for the ones that did not — so the
sourced claim is not reproduced in this book, and the weight reflects that. (The
raw all-time split, +0.254/75.6% vs −0.032/16.2%, was a calendar-coverage
confound: the calendar starts 2026-08-24, so everything before it was filed as
"no report".)

**B5 — `assignment_risk` becomes moneyness-aware.** It was set unconditionally
`True` for every equity or ETF short, which is assessment defect 13: a flag that
is always on carries no information, and the futures branch of the same function
had gated it on moneyness all along. Expiry-day proximity amplifies it. The
"close it by a set CT time" half of B5 is deliberately NOT built — see the design
doc: the paper book settles at intrinsic against the 15:00 CT close and models no
after-hours leg, so the exposure that rule defends against cannot occur here.
"""
import datetime as dt

import pytest

from services.options_svc import rescue


def _pos(**over):
    base = {"symbol": "ORCL", "strategy": "PCS", "short_strike": 100.0,
            "long_strike": 98.0, "entry_credit": 0.40, "quantity": 1,
            "expiration": "2026-10-16"}
    base.update(over)
    return base


def _mark(**over):
    base = {"current_underlying": 120.0, "current_short_delta": -0.10,
            "unrealized_pnl": 5.0, "dte": 34}
    base.update(over)
    return base


TODAY = dt.date(2026, 9, 12)


# ── B7: the earnings modifier ────────────────────────────────────────────────

def test_a_report_inside_the_position_raises_heat():
    calm = rescue.assess_position_risk(_pos(), _mark(), today=TODAY)
    hot = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date="2026-10-01")
    assert hot["heat"] > calm["heat"]


def test_a_report_AFTER_the_expiration_does_not_raise_heat():
    """The position is closed before the print — that is the whole reason the
    entry gate's ``After expiry`` status exists."""
    got = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date="2026-11-20")
    calm = rescue.assess_position_risk(_pos(), _mark(), today=TODAY)
    assert got["heat"] == calm["heat"]


def test_a_report_already_PAST_does_not_raise_heat():
    got = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date="2026-09-01")
    calm = rescue.assess_position_risk(_pos(), _mark(), today=TODAY)
    assert got["heat"] == calm["heat"]


def test_a_report_ON_the_expiration_date_counts():
    """Inclusive at both ends: a report the morning of expiry is the most
    gate-worthy case there is, and ``shared.earnings`` makes the same choice
    about a report TODAY."""
    got = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date="2026-10-16")
    calm = rescue.assess_position_risk(_pos(), _mark(), today=TODAY)
    assert got["heat"] > calm["heat"]


def test_the_earnings_modifier_NEVER_escalates_state_on_its_own():
    """⚠ The load-bearing assertion. This book's own data does not reproduce an
    earnings penalty, so the factor may reorder a ranked list and must never call
    a calm position tested. Same contract as the GEX and regime modifiers."""
    calm = rescue.assess_position_risk(_pos(), _mark(), today=TODAY)
    assert calm["state"] == "ok"
    hot = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date="2026-10-01")
    assert hot["state"] == "ok"


def test_the_earnings_flag_is_reported_so_the_board_can_show_it():
    got = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date="2026-10-01")
    assert got["earnings_inside"] is True
    assert rescue.assess_position_risk(
        _pos(), _mark(), today=TODAY)["earnings_inside"] is False


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-45", 20261001])
def test_an_unreadable_earnings_date_is_treated_as_no_report(bad):
    """A malformed date must not raise inside the board's per-position read, and
    must not invent a report either."""
    got = rescue.assess_position_risk(_pos(), _mark(), today=TODAY,
                                      earnings_date=bad)
    calm = rescue.assess_position_risk(_pos(), _mark(), today=TODAY)
    assert got["heat"] == calm["heat"]
    assert got["earnings_inside"] is False


def test_the_context_note_names_the_report_date():
    ctx = rescue.strategic_context(_pos(), underlying=120.0,
                                   earnings_date="2026-10-01", today=TODAY)
    assert ctx["earnings_inside"] is True
    assert any("2026-10-01" in n for n in ctx["notes"]), ctx["notes"]


def test_the_context_says_nothing_about_earnings_when_there_is_no_report():
    ctx = rescue.strategic_context(_pos(), underlying=120.0, today=TODAY)
    assert ctx["earnings_inside"] is False
    assert not any("earnings" in n.lower() or "report" in n.lower()
                   for n in ctx["notes"]), ctx["notes"]


def test_an_UNKNOWN_calendar_status_is_not_reported_as_no_report():
    """``not_listed`` means the vendor does not carry the symbol — the documented
    trap in ``shared.earnings.coverage``. A board that renders it as "no earnings"
    walks the reader into an unlisted report wearing the look of protection."""
    ctx = rescue.strategic_context(_pos(), underlying=120.0, today=TODAY,
                                   earnings_status="not_listed")
    assert any("not checked" in n.lower() or "does not carry" in n.lower()
               for n in ctx["notes"]), ctx["notes"]


# ── B5: assignment risk that means something ────────────────────────────────

def test_an_equity_short_OUT_of_the_money_is_no_longer_flagged_assignable():
    """Assessment defect 13: this was unconditionally True for every equity
    short, so the flag carried no information at all."""
    ctx = rescue.strategic_context(_pos(short_strike=100.0), underlying=120.0,
                                   today=TODAY)
    assert ctx["assignment_risk"] is False


def test_an_equity_short_IN_the_money_IS_flagged_assignable():
    ctx = rescue.strategic_context(_pos(short_strike=100.0), underlying=95.0,
                                   today=TODAY)
    assert ctx["assignment_risk"] is True


def test_a_short_CALL_in_the_money_is_flagged():
    ctx = rescue.strategic_context(_pos(strategy="CCS", short_strike=100.0,
                                        call_short=100.0),
                                   underlying=105.0, today=TODAY)
    assert ctx["assignment_risk"] is True


def test_with_NO_underlying_the_flag_stays_conservative():
    """Unknown moneyness must not clear a risk. Absent a spot the honest answer
    is the old one: American options can be assigned."""
    ctx = rescue.strategic_context(_pos(), underlying=None, today=TODAY)
    assert ctx["assignment_risk"] is True


def test_an_index_short_is_never_flagged_however_deep(monkeypatch):
    """European, cash-settled. The function already got this right; pinned so a
    moneyness rule cannot accidentally reach it."""
    ctx = rescue.strategic_context(_pos(symbol="$SPX", short_strike=6000.0),
                                   underlying=5000.0, today=TODAY)
    assert ctx["assignment_risk"] is False
    assert any("cash-settled" in n for n in ctx["notes"])


def test_the_equity_note_no_longer_claims_an_ex_dividend_check():
    """⚠ There is no ex-dividend DATE anywhere in this repo — the chain carries
    ``dividendYield`` and nothing else — so the old note described a check that
    does not exist. Naming an untested risk is the failure mode this audit keeps
    finding."""
    ctx = rescue.strategic_context(_pos(short_strike=100.0), underlying=95.0,
                                   today=TODAY)
    joined = " ".join(ctx["notes"]).lower()
    assert "ex-dividend" not in joined, ctx["notes"]


# ── B5: the expiry-day pin ──────────────────────────────────────────────────

def test_a_pin_on_EXPIRY_DAY_raises_heat_and_sets_the_flag():
    pos = _pos(expiration="2026-09-12")
    got = rescue.assess_position_risk(pos, _mark(current_underlying=100.4, dte=0),
                                      today=TODAY)
    assert got["pinned_at_expiry"] is True


def test_the_same_proximity_EARLIER_in_the_week_is_not_a_pin():
    """Pin risk is specifically an expiration-day fact. Ordinary proximity is
    already rule 1's job and already escalates."""
    pos = _pos(expiration="2026-09-18")
    got = rescue.assess_position_risk(pos, _mark(current_underlying=100.4, dte=6),
                                      today=TODAY)
    assert got["pinned_at_expiry"] is False


def test_a_comfortable_short_on_expiry_day_is_not_a_pin():
    pos = _pos(expiration="2026-09-12")
    got = rescue.assess_position_risk(pos, _mark(current_underlying=140.0, dte=0),
                                      today=TODAY)
    assert got["pinned_at_expiry"] is False


def test_a_cash_settled_index_pin_is_not_flagged():
    """The exemption B5 itself calls for: an index short cannot be assigned, so a
    pin there is a settlement price, not an assignment risk."""
    pos = _pos(symbol="$SPX", expiration="2026-09-12", short_strike=6000.0)
    got = rescue.assess_position_risk(pos, _mark(current_underlying=6010.0, dte=0),
                                      today=TODAY)
    assert got["pinned_at_expiry"] is False


def test_a_pin_needs_no_spot_to_avoid_raising():
    pos = _pos(expiration="2026-09-12")
    got = rescue.assess_position_risk(pos, _mark(current_underlying=None, dte=0),
                                      today=TODAY)
    assert got["pinned_at_expiry"] is False


# ── back-compatibility: every existing caller passes neither argument ───────

def test_the_pre_B7_call_signature_still_works_unchanged():
    """Four call sites in ``compute`` pass positionally with no earnings date, and
    the webgui reads these dicts. Both new keys must be present and False rather
    than absent, so a reader never special-cases them."""
    got = rescue.assess_position_risk(_pos(), _mark())
    assert got["earnings_inside"] is False
    assert got["pinned_at_expiry"] is False
    assert set(("state", "heat", "dte")) <= set(got)
