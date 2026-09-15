"""shared.book_caps - the paper books' risk caps in one place."""
import math

import pytest

from shared import book_caps as bc

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.20,
          "max_risk_per_trade": 250.0}


def _row(symbol, expiration="2026-10-17", risk=100.0, sector="Information Technology"):
    return {"symbol": symbol, "expiration": expiration, "max_loss_total": risk,
            "sector": sector}


def _cand(symbol="ORCL", expiration="2026-10-17", sector="Information Technology"):
    return {"symbol": symbol, "expiration": expiration, "sector": sector}


def _by_code(rungs):
    return {r["code"]: r for r in rungs}


def test_every_rung_is_reported_in_display_order():
    rungs = bc.evaluate([], _cand(), 100.0, LIMITS, equity=25000.0)
    assert [r["code"] for r in rungs] == list(bc.DISPLAY_ORDER)


def test_an_empty_book_passes_every_rung():
    rungs = bc.evaluate([], _cand(), 100.0, LIMITS, equity=25000.0)
    assert not any(r["binds"] for r in rungs)
    assert bc.first_breach(rungs, bc.DISPLAY_ORDER) is None


def test_per_trade_binds_above_the_limit_and_not_at_it():
    at = _by_code(bc.evaluate([], _cand(), 250.0, LIMITS, 25000.0))
    over = _by_code(bc.evaluate([], _cand(), 250.01, LIMITS, 25000.0))
    assert at[bc.TRADE_RISK_CAP]["binds"] is False
    assert over[bc.TRADE_RISK_CAP]["binds"] is True


def test_symbol_count_binds_when_the_new_position_would_exceed_the_cap():
    book = [_row("ORCL"), _row("ORCL"), _row("ORCL")]
    r = _by_code(bc.evaluate(book, _cand(), 10.0, LIMITS, 25000.0))[bc.SYMBOL_POSITION_CAP]
    assert (r["used"], r["after"], r["cap"], r["binds"]) == (3, 4, 3, True)


def test_symbol_risk_sums_existing_plus_candidate():
    book = [_row("ORCL", risk=600.0)]
    r = _by_code(bc.evaluate(book, _cand(), 200.0, LIMITS, 25000.0))[bc.SYMBOL_RISK_CAP]
    assert (r["used"], r["after"], r["binds"]) == (600.0, 800.0, True)


def test_symbol_matching_ignores_case_and_whitespace():
    book = [_row(" orcl "), _row("Orcl"), _row("ORCL")]
    r = _by_code(bc.evaluate(book, _cand(), 10.0, LIMITS, 25000.0))[bc.SYMBOL_POSITION_CAP]
    assert r["used"] == 3


def test_deployment_uses_twenty_percent_of_equity():
    book = [_row("MSFT", risk=4900.0, sector="X")]
    r = _by_code(bc.evaluate(book, _cand(), 101.0, LIMITS, 25000.0))[bc.DEPLOYMENT_CAP]
    assert (r["cap"], r["after"], r["binds"]) == (5000.0, 5001.0, True)


@pytest.mark.parametrize("equity", [None, 0.0, -5.0, float("nan"), "junk"])
def test_deployment_is_skipped_without_a_usable_equity(equity):
    r = _by_code(bc.evaluate([], _cand(), 1e9, LIMITS, equity))[bc.DEPLOYMENT_CAP]
    assert r["skipped"] and r["binds"] is False


@pytest.mark.parametrize("key", ["max_risk_per_trade", "max_deployed_risk_pct",
                                 "max_positions_per_sector", "max_risk_per_sector"])
def test_opt_in_rungs_skip_when_their_key_is_zero(key):
    limits = {**LIMITS, key: 0}
    code = {"max_risk_per_trade": bc.TRADE_RISK_CAP,
            "max_deployed_risk_pct": bc.DEPLOYMENT_CAP,
            "max_positions_per_sector": bc.SECTOR_POSITION_CAP,
            "max_risk_per_sector": bc.SECTOR_RISK_CAP}[key]
    r = _by_code(bc.evaluate([], _cand(), 1e9, limits, 25000.0))[code]
    assert r["skipped"] and r["binds"] is False


def test_a_zero_symbol_cap_refuses_everything_exactly_as_today():
    limits = {**LIMITS, "max_positions_per_symbol": 0}
    r = _by_code(bc.evaluate([], _cand(), 1.0, limits, 25000.0))[bc.SYMBOL_POSITION_CAP]
    assert r["binds"] is True


def test_a_missing_required_key_raises():
    limits = {k: v for k, v in LIMITS.items() if k != "max_positions_per_expiry"}
    with pytest.raises(KeyError):
        bc.evaluate([], _cand(), 1.0, limits, 25000.0)


def test_sector_rungs_skip_when_the_candidate_has_no_sector():
    rungs = _by_code(bc.evaluate([], _cand(sector=None), 1.0, LIMITS, 25000.0))
    assert rungs[bc.SECTOR_POSITION_CAP]["skipped"]
    assert rungs[bc.SECTOR_RISK_CAP]["skipped"]


def test_a_row_without_a_sector_never_counts_toward_a_sector():
    book = [_row("A", sector=None)] * 9
    r = _by_code(bc.evaluate(book, _cand(), 1.0, LIMITS, 25000.0))[bc.SECTOR_POSITION_CAP]
    assert r["used"] == 0


def test_expiry_counts_across_symbols():
    book = [_row(s, sector=s) for s in ("A", "B", "C", "D", "E")]
    r = _by_code(bc.evaluate(book, _cand(sector="Z"), 1.0, LIMITS, 25000.0))[bc.EXPIRY_POSITION_CAP]
    assert r["binds"] is True


def test_a_nan_book_row_cannot_switch_a_risk_ceiling_off():
    book = [_row("ORCL", risk=float("nan")), _row("ORCL", risk=700.0)]
    r = _by_code(bc.evaluate(book, _cand(), 100.0, LIMITS, 25000.0))[bc.SYMBOL_RISK_CAP]
    assert r["binds"] is True and math.isfinite(r["after"])


def test_first_breach_follows_the_order_it_is_given():
    book = [_row("ORCL")] * 3                       # symbol count binds
    rungs = bc.evaluate(book, _cand(), 300.0, LIMITS, 25000.0)   # per trade binds too
    assert bc.first_breach(rungs, bc.DISPLAY_ORDER)["code"] == bc.TRADE_RISK_CAP
    assert bc.first_breach(rungs, bc.ACCOUNT_ORDER)["code"] == bc.SYMBOL_POSITION_CAP


def test_account_order_is_the_historical_order_and_has_no_per_trade_rung():
    assert bc.ACCOUNT_ORDER == (bc.DEPLOYMENT_CAP, bc.SYMBOL_POSITION_CAP,
                                bc.SYMBOL_RISK_CAP, bc.SECTOR_POSITION_CAP,
                                bc.SECTOR_RISK_CAP, bc.EXPIRY_POSITION_CAP)
    assert bc.TRADE_RISK_CAP not in bc.ACCOUNT_ORDER


def test_sector_positions_bind_at_the_cap_and_not_below_it():
    below = [_row(s, expiration=f"2026-10-{17 + i}") for i, s in enumerate(("A", "B", "C", "D"))]
    at = below + [_row("E", expiration="2026-11-20")]
    r_below = _by_code(bc.evaluate(below, _cand(), 1.0, LIMITS, 25000.0))[bc.SECTOR_POSITION_CAP]
    r_at = _by_code(bc.evaluate(at, _cand(), 1.0, LIMITS, 25000.0))[bc.SECTOR_POSITION_CAP]
    assert (r_below["used"], r_below["binds"]) == (4, False)
    assert (r_at["used"], r_at["binds"]) == (5, True)


def test_sector_risk_sums_other_symbols_in_the_sector_and_ignores_other_sectors():
    book = [_row("MSFT", expiration="2026-10-24", risk=700.0),
            _row("AMD", expiration="2026-10-31", risk=700.0),
            _row("XOM", expiration="2026-11-07", risk=5000.0, sector="Energy")]
    r = _by_code(bc.evaluate(book, _cand(), 150.0, LIMITS, 1e9))[bc.SECTOR_RISK_CAP]
    assert (r["used"], r["after"], r["binds"]) == (1400.0, 1550.0, True)


@pytest.mark.parametrize("key", ["max_risk_per_trade", "max_deployed_risk_pct",
                                 "max_positions_per_sector", "max_risk_per_sector"])
def test_opt_in_rungs_skip_when_their_key_is_absent(key):
    limits = {k: v for k, v in LIMITS.items() if k != key}
    code = {"max_risk_per_trade": bc.TRADE_RISK_CAP,
            "max_deployed_risk_pct": bc.DEPLOYMENT_CAP,
            "max_positions_per_sector": bc.SECTOR_POSITION_CAP,
            "max_risk_per_sector": bc.SECTOR_RISK_CAP}[key]
    r = _by_code(bc.evaluate([], _cand(), 1e9, limits, 25000.0))[code]
    assert r["skipped"] and r["binds"] is False


@pytest.mark.parametrize("risk", [None, float("nan"), "junk", 0])
def test_an_unusable_candidate_risk_counts_as_zero_exactly_as_the_account_always_did(risk):
    """Pinned on purpose: callers that did not size the trade must refuse it first."""
    r = _by_code(bc.evaluate([], _cand(), risk, LIMITS, 25000.0))[bc.TRADE_RISK_CAP]
    assert (r["after"], r["binds"], r["skipped"]) == (0.0, False, None)


def test_max_quantity_is_bounded_by_the_tightest_risk_rung():
    # per trade: floor(250/80)=3; symbol risk: floor((750-600)/80)=1
    book = [_row("ORCL", risk=600.0)]
    assert bc.max_quantity(book, _cand(), 80.0, LIMITS, 25000.0) == 1


def test_max_quantity_is_zero_when_a_count_rung_binds():
    book = [_row("ORCL")] * 3
    assert bc.max_quantity(book, _cand(), 1.0, LIMITS, 25000.0) == 0


def test_max_quantity_is_zero_when_one_contract_is_over_the_trade_cap():
    assert bc.max_quantity([], _cand(), 425.0, LIMITS, 25000.0) == 0


def test_max_quantity_respects_the_dialog_ceiling():
    limits = {**LIMITS, "max_risk_per_trade": 0, "max_risk_per_symbol": 1e12,
              "max_risk_per_sector": 0, "max_deployed_risk_pct": 0}
    assert bc.max_quantity([], _cand(), 1.0, limits, 25000.0) == bc.QTY_CEILING


def test_max_quantity_never_overshoots_on_floating_point():
    q = bc.max_quantity([], _cand(), 83.33, LIMITS, 25000.0)
    assert q == 3 and q * 83.33 <= 250.0


@pytest.mark.parametrize("per", [None, 0, -1, float("nan"), "x"])
def test_max_quantity_is_none_for_an_unusable_contract_risk(per):
    assert bc.max_quantity([], _cand(), per, LIMITS, 25000.0) is None


def test_max_quantity_agrees_with_evaluate():
    book = [_row("ORCL", risk=300.0), _row("MSFT", risk=900.0)]
    per = 70.0
    q = bc.max_quantity(book, _cand(), per, LIMITS, 25000.0)
    assert bc.first_breach(bc.evaluate(book, _cand(), q * per, LIMITS, 25000.0),
                           bc.DISPLAY_ORDER) is None
    assert bc.first_breach(bc.evaluate(book, _cand(), (q + 1) * per, LIMITS, 25000.0),
                           bc.DISPLAY_ORDER) is not None


def test_describe_names_the_cap_in_plain_words():
    book = [_row("ORCL")] * 3
    r = bc.first_breach(bc.evaluate(book, _cand(), 10.0, LIMITS, 25000.0),
                        bc.DISPLAY_ORDER)
    assert bc.describe(r) == "Already 3 of 3 positions in ORCL"


def test_describe_an_unmapped_sector_says_so():
    r = bc._count(bc.SECTOR_POSITION_CAP, "?IONQ", 5, 5)
    assert "IONQ (no sector on file)" in bc.describe(r)


def test_describe_a_trade_risk_breach():
    r = bc._risk(bc.TRADE_RISK_CAP, None, 0.0, 425.0, 250.0)
    assert bc.describe(r) == "Risks $425, over the $250 per-trade limit"


def test_describe_a_skipped_rung():
    r = bc._skip(bc.DEPLOYMENT_CAP, "risk", None, "no equity figure")
    assert bc.describe(r) == "Not checked: no equity figure"
