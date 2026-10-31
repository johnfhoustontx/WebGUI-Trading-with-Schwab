"""The /ES and /NQ chain adapter: validate a captured snapshot, calculate each
contract's gamma and hand the page's engine a chain it reads like any other.

Runs the real ``GammaEngine``, ``flow_skew`` and ``iv_analysis`` (the conftest's
import of ``compute`` puts ``options-scanner`` on the path). Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.4 and 5.5.
"""
import ast
import math
import pathlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute  # noqa: F401  puts options-scanner on sys.path
from services.options_svc import futures_chain_adapter
from services.options_svc.futures_chain_adapter import Refused, build_chain
from shared import futures_chain
from shared.contracts.futures_chain import FuturesChainSnapshot, FuturesContractRow

import flow_skew
import iv_analysis
from gamma_tool import GammaEngine

_CT = ZoneInfo("America/Chicago")
EXPIRY = date(2026, 11, 2)                     # a Monday


def ct(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_CT)


# ── builders ─────────────────────────────────────────────────────────────────

def desc(series="E1AX26", *, date_text="2 NOV 26", underlying="/ESZ26:XCME",
         mult_text="1/50", settled="", strike=7600, side="CALL"):
    """A ThinkorSwim DESCRIPTION, as the capture reads it off one contract."""
    return (f"{underlying} {mult_text} {date_text} (Monday){settled} "
            f"/{series}:XCME {strike} {side}")


def ok():
    return {"E1AX26": desc()}


def two_series():
    return {"E1AX26": desc(), "EWX26": desc("EWX26")}


def am_settled():
    return desc("EWX26", settled=" (AM Settled)")


def row(strike, side, *, series="E1AX26", mark=None, bid=None, ask=None, volume=5,
        open_int=10, iv="15%", delta=None):
    return FuturesContractRow(series=series, side=side, strike=strike, mark=mark, bid=bid,
                              ask=ask, volume=volume, open_int=open_int, iv=iv, delta=delta)


def snap(*, instrument="ES", multiplier=None, price=7600, rows=None, descriptions=None,
         expiry=EXPIRY, captured=None, underlying=None):
    """A snapshot of one series at the strike nearest the price, unless told."""
    mult = multiplier or {"ES": 50, "NQ": 20}[instrument]
    root = f"/{instrument}Z26:XCME"
    if descriptions is None:
        descriptions = {"E1AX26": desc(underlying=root, mult_text=f"1/{mult}")}
    if rows is None:
        rows = [row(7600, "C"), row(7600, "P")]
    when = captured or ct(2026, 11, 2, 9, 0)
    return FuturesChainSnapshot(
        instrument=instrument, expiry=expiry, captured_at=when.timestamp(),
        descriptions=descriptions, underlying_symbol=underlying or root,
        underlying_price=price, contracts=rows)


def adapt(snapshot, hours=None):
    """``build_chain``; given ``hours`` the capture is that long before the close."""
    if hours is not None:
        close = futures_chain.session_bounds(snapshot.expiry)[1]
        snapshot = snapshot.model_copy(update={"captured_at": close - hours * 3600.0})
    return build_chain(snapshot)


def contracts_of(adapted):
    return [c for side in ("callExpDateMap", "putExpDateMap")
            for strikes in adapted.chain[side].values()
            for cs in strikes.values() for c in cs]


def contract_at(adapted, strike, side):
    side_map = adapted.chain["callExpDateMap" if side == "C" else "putExpDateMap"]
    (strikes,) = side_map.values()
    return strikes[f"{float(strike)!r}"][0]


def full_ladder_fixture():
    """Eleven strikes both sides with marks, volume and a volatility."""
    rows = []
    for k in range(7575, 7630, 5):
        call = max(7600 - k, 0) + 6.0
        put = max(k - 7600, 0) + 6.0
        rows += [row(k, "C", mark=call, volume=100, open_int=50, iv="14%"),
                 row(k, "P", mark=put, volume=80, open_int=60, iv="14%")]
    return snap(price=7600, rows=rows)


def bad_snapshot(field, value):
    """The one-series snapshot with a single thing wrong."""
    if field == "description_date":
        return snap(descriptions={"E1AX26": desc(date_text=value)})
    if field == "description_settled":
        return snap(descriptions={"E1AX26": desc(settled=f" {value}")})
    if field == "description_multiplier":
        return snap(descriptions={"E1AX26": desc(mult_text=value)})
    if field == "underlying_symbol":
        return snap(underlying=value)
    if field == "underlying_price":
        return snap(price=value)
    raise AssertionError(field)


# ── gamma ────────────────────────────────────────────────────────────────────

def test_gamma_is_calculated_from_the_contracts_own_volatility():
    # F=7600, K=7600, sigma=0.15, 6.5 hours: phi(d1) / (F sigma sqrt(T))
    c = contract_at(adapt(snap(price=7600, rows=[row(7600, "C", iv="15%")]), hours=6.5), 7600, "C")
    assert c["gamma"] == pytest.approx(0.012847, rel=1e-3)   # T = 6.5/8760, by hand


def test_gamma_is_the_same_formula_off_the_money():
    # d1 = (ln(F/K) + sigma^2 T / 2) / (sigma sqrt T); gamma = phi(d1) / (F sigma sqrt T)
    f, k, sigma, hours = 7600.0, 7625.0, 0.20, 3.0
    t = hours / 8760.0
    d1 = (math.log(f / k) + 0.5 * sigma ** 2 * t) / (sigma * math.sqrt(t))
    want = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (f * sigma * math.sqrt(t))
    c = contract_at(adapt(snap(price=f, rows=[row(k, "C", iv="20%")]), hours=hours), k, "C")
    assert c["gamma"] == pytest.approx(want, rel=1e-9)


def test_a_call_and_a_put_at_one_strike_share_a_gamma():
    a = adapt(snap(rows=[row(7600, "C", iv="15%"), row(7600, "P", iv="15%")]), hours=6.5)
    assert contract_at(a, 7600, "C")["gamma"] == contract_at(a, 7600, "P")["gamma"]


@pytest.mark.parametrize("iv", ["N/A", "0%", "-1%", None])
def test_no_usable_volatility_is_no_gamma_not_zero(iv):
    c = contract_at(adapt(snap(rows=[row(7600, "C", iv=iv), row(7600, "P")])), 7600, "C")
    assert c["gamma"] is None or "gamma" not in c
    assert c["volatility"] == 0     # the engine's own charm and vanna skip


def test_no_usable_volatility_still_runs_through_the_engine():
    gex, *_ = GammaEngine().calc_all_from_chain(
        adapt(snap(rows=[row(7600, "C", iv="N/A", open_int=10), row(7600, "P", open_int=10)])).chain,
        now=ct(2026, 11, 2, 9, 0), hours_left=6.0)
    assert gex is not None
    assert gex["gex"][7600.0]["call"] == 0.0     # no gamma contributes nothing


def test_the_result_counts_the_contracts_that_have_a_gamma():
    a = adapt(snap(rows=[row(7600, "C", iv="N/A"), row(7600, "P", iv="15%"),
                         row(7605, "C", iv="15%")]))
    assert a.with_gamma == 2 and a.listed == 3


@pytest.mark.parametrize("iv", ["N/A", "0%", "-1%", None])
def test_a_chain_where_no_contract_has_a_volatility_is_refused(iv):
    """Every gamma absent would publish a net gamma of zero at every strike: a
    flat chart that reads as a finding. It is refused instead."""
    with pytest.raises(Refused, match="volatility"):
        adapt(snap(rows=[row(7600, "C", iv=iv), row(7600, "P", iv=iv)]))


def test_an_underflowing_volatility_is_no_gamma_and_does_not_divide_by_zero():
    """sigma * sqrt(T) underflows to zero for a volatility of 1e-322 percent: a
    number ``_volatility`` calls usable and the division could not take."""
    a = adapt(snap(rows=[row(7600, "C", iv="1e-322%"), row(7600, "P", iv="15%")]), hours=6.5)
    assert contract_at(a, 7600, "C")["gamma"] is None
    assert contract_at(a, 7600, "P")["gamma"] is not None
    assert a.with_gamma == 1


def test_a_chain_whose_only_volatility_underflows_is_refused():
    with pytest.raises(Refused, match="volatility"):
        adapt(snap(rows=[row(7600, "C", iv="1e-322%")]), hours=6.5)


@pytest.mark.parametrize("open_int", [None, "N/A"])
def test_a_chain_where_no_contract_has_open_interest_is_refused(open_int):
    """Open interest weights every dollar figure: with none, zero at every strike
    would read as a real flat reading."""
    with pytest.raises(Refused, match="open interest"):
        adapt(snap(rows=[row(7600, "C", open_int=open_int), row(7600, "P", open_int=open_int)]))


def test_a_zero_open_interest_is_a_reading_and_is_not_refused():
    a = adapt(snap(rows=[row(7600, "C", open_int=0), row(7600, "P", open_int=0)]))
    assert a.listed == 2


def test_one_contract_with_open_interest_is_enough():
    adapt(snap(rows=[row(7600, "C", open_int=None), row(7600, "P", open_int=10)]))


def test_the_chain_carries_the_volatility_in_percent_as_the_engine_reads_it():
    c = contract_at(adapt(snap(rows=[row(7600, "C", iv="12.51%")])), 7600, "C")
    assert c["volatility"] == pytest.approx(12.51)


# ── contract size ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("inst,mult,scale", [("ES", 50, 0.5), ("NQ", 20, 0.2)])
def test_counts_are_scaled_by_contract_size(inst, mult, scale):
    c = contract_at(adapt(snap(instrument=inst, multiplier=mult,
                               rows=[row(7600, "C", open_int=100, volume=40)])), 7600, "C")
    assert (c["openInterest"], c["totalVolume"]) == (100 * scale, 40 * scale)


def test_an_absent_count_stays_absent_when_scaled():
    a = adapt(snap(rows=[row(7600, "C", open_int=None, volume=None), row(7600, "P", open_int=10)]))
    c = contract_at(a, 7600, "C")
    assert c["openInterest"] is None and c["totalVolume"] is None


def test_the_engines_dollar_figure_uses_the_true_multiplier():
    # gamma * OI * multiplier * F^2 * 0.01, with the engine's 100 cancelled by the scale
    adapted = adapt(snap(price=7600, rows=[row(7600, "C", iv="15%", open_int=100),
                                           row(7600, "P", iv="15%", open_int=0)]), hours=6.5)
    gex, *_ = GammaEngine().calc_all_from_chain(adapted.chain, now=ct(2026, 11, 2, 8, 30),
                                                hours_left=6.5)
    gamma = contract_at(adapted, 7600, "C")["gamma"]
    assert gex["gex"][7600.0]["call"] == pytest.approx(gamma * 100 * 50 * 7600 ** 2 * 0.01)


def test_the_nq_dollar_figure_uses_its_own_multiplier():
    adapted = adapt(snap(instrument="NQ", price=26000, rows=[row(26000, "C", iv="20%", open_int=100)]),
                    hours=6.5)
    gex, *_ = GammaEngine().calc_all_from_chain(adapted.chain, now=ct(2026, 11, 2, 8, 30),
                                                hours_left=6.5)
    gamma = contract_at(adapted, 26000, "C")["gamma"]
    assert gex["gex"][26000.0]["call"] == pytest.approx(gamma * 100 * 20 * 26000 ** 2 * 0.01)


def test_two_series_on_one_expiry_add_at_a_strike():
    a = adapt(snap(descriptions=two_series(),
                   rows=[row(7600, "C", series="E1AX26", open_int=10),
                         row(7600, "C", series="EWX26", open_int=30), row(7600, "P", open_int=1)]))
    gex, *_ = GammaEngine().calc_all_from_chain(a.chain, now=ct(2026, 11, 2, 9, 0), hours_left=6.0)
    only_first = adapt(snap(rows=[row(7600, "C", series="E1AX26", open_int=40), row(7600, "P", open_int=1)]))
    gex2, *_ = GammaEngine().calc_all_from_chain(only_first.chain, now=ct(2026, 11, 2, 9, 0),
                                                 hours_left=6.0)
    assert gex["gex"][7600.0]["call"] == pytest.approx(gex2["gex"][7600.0]["call"])


def test_two_series_keep_their_own_entries_and_names():
    a = adapt(snap(descriptions=two_series(),
                   rows=[row(7600, "C", series="E1AX26"), row(7600, "C", series="EWX26")]))
    assert sorted(c["series"] for c in contracts_of(a)) == ["E1AX26", "EWX26"]


# ── the expiry key and the time left ────────────────────────────────────────

def test_chain_keeps_the_expiry_at_dte_zero_all_session():
    a = adapt(snap(expiry=EXPIRY, captured=ct(2026, 11, 1, 18, 0)))
    assert list(a.chain["callExpDateMap"]) == ["2026-11-02:0"]
    assert list(a.chain["putExpDateMap"]) == ["2026-11-02:0"]
    assert contract_at(a, 7600, "C")["daysToExpiration"] == 0
    assert a.hours_left == pytest.approx(21.0)


def test_the_engine_finds_the_zero_dte_slice_the_evening_before():
    a = adapt(snap(captured=ct(2026, 11, 1, 18, 0), rows=[row(7600, "C", open_int=10, delta=0.5),
                                                          row(7600, "P", open_int=10, delta=-0.5)]))
    engine = GammaEngine()
    _, _, dex, _ = engine.calc_all_from_chain(a.chain, now=ct(2026, 11, 1, 18, 0),
                                              hours_left=a.hours_left)
    assert engine._last_dte == 0
    assert dex["net_delta_0dte"] is not None


def test_hours_left_is_the_one_helpers_answer_times_8760():
    from services.options_svc.expiry_time import time_to_expiry_years
    when = ct(2026, 11, 2, 11, 17)
    a = adapt(snap(captured=when))
    assert a.hours_left == pytest.approx(time_to_expiry_years(when, EXPIRY) * 8760.0, rel=1e-12)
    assert a.hours_left == pytest.approx(3 + 43 / 60)


def test_hours_left_is_finite_and_positive():
    a = adapt(snap(captured=ct(2026, 11, 2, 14, 59)))
    assert math.isfinite(a.hours_left) and a.hours_left > 0


def test_the_result_names_the_instrument_and_its_capture():
    s = snap()
    a = adapt(s)
    assert (a.symbol, a.expiry, a.captured_at) == ("FOP.ES", EXPIRY, s.captured_at)
    assert a.chain["underlyingPrice"] == 7600
    assert a.listed == 2


# ── the page's other readers ─────────────────────────────────────────────────

def test_the_real_flow_helpers_read_the_chain():
    a = adapt(full_ladder_fixture())
    assert flow_skew.index_call_put_premium(a.chain)["call_prem"] > 0
    # extract_atm_iv reads the expiry nearest 30 DTE and returns None for a chain
    # that holds only DTE 0 (the same for any 0-DTE-only chain); the per-expiry
    # reading is the one that sees this feed's single expiry.
    assert iv_analysis.expiry_atm_iv(a.chain, "2026-11-02", a.chain["underlyingPrice"]) == 14.0


def test_extract_atm_iv_has_no_reading_on_a_zero_dte_only_chain():
    """Pins the finding reported with the adapter: the collector's ``atm_iv`` column
    is empty for these two symbols. If this starts to fail, the reader changed."""
    assert iv_analysis.extract_atm_iv(adapt(full_ladder_fixture()).chain) is None


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value,reason", [
    ("description_date", "3 NOV 26", "expiry"),      # no series states this expiry
    ("description_settled", "(AM Settled)", "morning"),
    ("description_multiplier", "1/20", "multiplier"),
    ("underlying_symbol", "/ESH27:XCME", "underlying"),
    ("underlying_price", 0, "price"),
])
def test_refused_with_a_reason(field, value, reason):
    with pytest.raises(Refused, match=reason):
        adapt(bad_snapshot(field, value))


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_an_unusable_underlying_price_is_refused(price):
    with pytest.raises(Refused, match="price"):
        adapt(snap(price=price))


def test_an_unknown_instrument_is_refused():
    with pytest.raises(Refused, match="instrument"):
        adapt(snap().model_copy(update={"instrument": "CL"}))


def test_the_reason_is_on_the_exception():
    with pytest.raises(Refused) as caught:
        adapt(snap(price=0))
    assert "price" in caught.value.reason and str(caught.value) == caught.value.reason


def test_a_series_that_fails_its_check_is_dropped_when_another_passes():
    a = adapt(snap(descriptions={**ok(), "EWX26": am_settled()},
                   rows=[row(7600, "C"), row(7600, "P"), row(7600, "C", series="EWX26")]))
    assert all(c["series"] != "EWX26" for c in contracts_of(a))
    assert len(contracts_of(a)) == 2


def test_a_dropped_series_is_logged(caplog):
    with caplog.at_level("WARNING"):
        adapt(snap(descriptions={**ok(), "EWX26": am_settled()}, rows=[row(7600, "C")]))
    assert "EWX26" in caplog.text and "morning" in caplog.text


def test_rows_of_a_series_with_no_description_are_dropped():
    a = adapt(snap(rows=[row(7600, "C"), row(7600, "C", series="QQQQ26")]))
    assert [c["series"] for c in contracts_of(a)] == ["E1AX26"]


def test_a_description_under_another_series_name_is_dropped():
    with pytest.raises(Refused, match="series"):
        adapt(snap(descriptions={"EWX26": desc("E1AX26")}, rows=[row(7600, "C", series="EWX26")]))


def test_every_series_failing_names_each_reason():
    with pytest.raises(Refused) as caught:
        adapt(snap(descriptions={"E1AX26": desc(date_text="3 NOV 26"), "EWX26": am_settled()},
                   rows=[row(7600, "C")]))
    assert "E1AX26" in caught.value.reason and "EWX26" in caught.value.reason


def test_no_contract_left_is_refused():
    with pytest.raises(Refused, match="contracts"):
        adapt(snap(rows=[]))


def test_a_contract_that_reads_nothing_is_not_listed():
    a = adapt(snap(rows=[row(7600, "C"), row(7600, "P"),
                         row(7605, "C", volume=None, open_int=None, iv=None)]))
    assert a.listed == 2
    assert "7605.0" not in a.chain["callExpDateMap"]["2026-11-02:0"]


def test_a_repeated_contract_counts_once():
    a = adapt(snap(rows=[row(7600, "C", open_int=10), row(7600, "C", open_int=99)]))
    assert contract_at(a, 7600, "C")["openInterest"] == 5.0
    assert a.listed == 1


# ── parity ───────────────────────────────────────────────────────────────────

def test_parity_disagreement_refuses():
    with pytest.raises(Refused, match="parity"):
        adapt(snap(price=7600, rows=[row(7600, "C", mark=50), row(7600, "P", mark=10)]))    # implies 7640, limit below that


def test_parity_limit_comes_from_the_instrument_config(monkeypatch):
    from shared import futures_chain_config as cfg
    real = cfg.instruments

    def loose():
        out = real()
        out["ES"]["parity_limit_pts"] = 100
        return out
    monkeypatch.setattr(cfg, "instruments", loose)
    assert adapt(snap(price=7600, rows=[row(7600, "C", mark=50), row(7600, "P", mark=10)])
                 ).parity_gap == pytest.approx(40.0)


def test_nq_has_its_own_wider_limit():
    adapt(snap(instrument="NQ", price=26000, rows=[row(26000, "C", mark=50), row(26000, "P", mark=10)]))
    with pytest.raises(Refused, match="parity"):
        adapt(snap(instrument="NQ", price=26000, rows=[row(26000, "C", mark=90), row(26000, "P", mark=10)]))


def test_parity_passes_when_within_the_limit_and_records_the_gap():
    assert adapt(snap(price=7600, rows=[row(7600, "C", mark=11), row(7600, "P", mark=10)])).parity_gap == pytest.approx(1.0)


def test_parity_gap_is_absolute():
    assert adapt(snap(price=7600, rows=[row(7600, "C", mark=10), row(7600, "P", mark=11)])).parity_gap == pytest.approx(1.0)


def test_no_readable_pair_is_not_a_disagreement():
    assert adapt(snap(rows=[row(7600, "C", mark="N/A"), row(7600, "P", mark="N/A")])).parity_gap is None


def test_parity_uses_the_strike_nearest_the_price_that_has_both_marks():
    rows = [row(7600, "C", mark="N/A"), row(7600, "P", mark=10),     # no call at the money
            row(7605, "C", mark=8), row(7605, "P", mark=13)]          # implies 7600
    assert adapt(snap(price=7600, rows=rows)).parity_gap == pytest.approx(0.0)


def test_parity_does_not_search_the_whole_ladder():
    rows = [row(7600, "C"), row(7600, "P")]
    rows += [row(7600 + 5 * i, "C") for i in range(1, 20)] + [row(7600 + 5 * i, "P") for i in range(1, 20)]
    rows += [row(7800, "C", mark=900), row(7800, "P", mark=1)]       # absurd, 40 strikes out
    assert adapt(snap(price=7600, rows=rows)).parity_gap is None


def test_parity_looks_at_as_many_strikes_as_the_config_says(monkeypatch):
    from shared import futures_chain_config as cfg
    rows = [row(7600, "C", mark="N/A"), row(7600, "P", mark=10),     # nearest: no call
            row(7605, "C", mark=8), row(7605, "P", mark=13)]          # second nearest: implies 7600
    assert adapt(snap(price=7600, rows=rows)).parity_gap == pytest.approx(0.0)
    real = cfg.capture
    monkeypatch.setattr(cfg, "capture", lambda: {**real(), "parity_strikes": 1})
    assert adapt(snap(price=7600, rows=rows)).parity_gap is None


def test_parity_falls_back_to_the_bid_ask_midpoint():
    rows = [row(7600, "C", bid=10, ask=12), row(7600, "P", mark=10)]
    assert adapt(snap(price=7600, rows=rows)).parity_gap == pytest.approx(1.0)


def test_a_capture_past_expiry_is_refused():
    with pytest.raises(Refused, match="expiry"):
        adapt(snap(expiry=EXPIRY, captured=ct(2026, 11, 2, 15, 1)))


def test_a_capture_at_the_close_is_refused():
    with pytest.raises(Refused, match="expiry"):
        adapt(snap(expiry=EXPIRY, captured=ct(2026, 11, 2, 15, 0)))


def test_a_capture_a_minute_before_the_close_is_accepted():
    assert adapt(snap(expiry=EXPIRY, captured=ct(2026, 11, 2, 14, 59))).hours_left == pytest.approx(1 / 60)


@pytest.mark.parametrize("captured_at", [float("nan"), float("inf")])
def test_a_capture_time_that_is_not_a_time_is_refused(captured_at):
    with pytest.raises(Refused, match="time"):
        build_chain(snap().model_copy(update={"captured_at": captured_at}))


# ── what the adapter may import ──────────────────────────────────────────────

def imports_of(module):
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_adapter_imports_nothing_from_compute():
    assert not any(n.endswith("compute") for n in imports_of(futures_chain_adapter))
