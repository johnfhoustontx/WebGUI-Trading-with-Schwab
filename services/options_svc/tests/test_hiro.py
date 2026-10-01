import math

import pytest

from services.options_svc import hiro


@pytest.mark.parametrize("last,bid,ask,want", [
    (1.10, 1.00, 1.10, 1),     # at the ask -> customer bought
    (1.20, 1.00, 1.10, 1),     # through the ask
    (1.00, 1.00, 1.10, -1),    # at the bid -> customer sold
    (0.95, 1.00, 1.10, -1),
    (1.08, 1.00, 1.10, 1),     # above mid
    (1.02, 1.00, 1.10, -1),    # below mid
    (1.05, 1.00, 1.10, 0),     # exactly mid -> unclassified, never guessed
    (0.15, 0.10, 0.20, 0),     # mid that binary floats miss by one ulp
    (2.35, 2.30, 2.40, 0),     # ...and another
    (1.02, 1.00, 1.05, -1),    # control: a real gap below mid still labels
])
def test_classify_side_quote_rule(last, bid, ask, want):
    assert hiro.classify_side(last, bid, ask) == want


@pytest.mark.parametrize("last,bid,ask", [
    (None, 1.0, 1.1), (1.0, None, 1.1), (1.0, 1.0, None),
    (math.nan, 1.0, 1.1), (1.0, math.inf, 1.1), (True, 1.0, 1.1),
    (0.0, 1.0, 1.1),           # no print
    (1.0, 1.2, 1.1),           # crossed quote
    (1.0, -999.0, 1.1),        # sentinel
    (1.00, 1.00, 1.00),        # locked quote -> no side to read
])
def test_classify_side_unusable_quote_is_unclassified(last, bid, ask):
    assert hiro.classify_side(last, bid, ask) == 0


def _c(osi, vol, delta, last, bid=1.00, ask=1.10):
    return {"symbol": osi, "totalVolume": vol, "delta": delta,
            "last": last, "bid": bid, "ask": ask}


def _chain(calls=(), puts=(), spot=500.0):
    def emap(cs, put_call):
        return {"2026-10-02:1": {f"{100 + i}.0": [dict(c, putCall=put_call)]
                                 for i, c in enumerate(cs)}}
    return {"underlyingPrice": spot, "callExpDateMap": emap(calls, "CALL"),
            "putExpDateMap": emap(puts, "PUT")}


def test_measure_first_sight_seeds_baseline_and_books_nothing():
    chain = _chain(calls=[_c("C1", 1000, 0.5, 1.10)])
    row, prev = hiro.measure_chain(chain, {})
    assert prev == {"C1": 1000.0}
    assert row["impact"] == 0.0 and row["classified_vol"] == 0.0
    assert row["unclassified_vol"] == 0.0 and row["spot"] == 500.0


def test_measure_signs_call_and_put_buys_correctly():
    prev = {"C1": 1000.0, "P1": 50.0}
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10)],      # 10 bought at ask
                   puts=[_c("P1", 54, -0.4, 1.10)])        # 4 bought at ask
    row, new_prev = hiro.measure_chain(chain, prev)
    # call: +1 * 0.5 * 10 * 100 * 500 = +250,000 ; put: +1 * -0.4 * 4 * 100 * 500 = -80,000
    assert row["impact"] == pytest.approx(170_000.0)
    assert row["classified_vol"] == 14.0
    assert new_prev == {"C1": 1010.0, "P1": 54.0}


def test_measure_customer_sell_of_call_is_dealer_selling():
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.00)]), {"C1": 1000.0})
    assert row["impact"] == pytest.approx(-250_000.0)


def test_measure_mid_print_is_unclassified_and_contributes_nothing():
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.05)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["unclassified_vol"] == 10.0 and row["classified_vol"] == 0.0


@pytest.mark.parametrize("delta", [math.nan, -999.0, 1.5, None, True])
def test_measure_drops_bad_delta(delta):
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, delta, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 10.0
    assert prev["C1"] == 1010.0           # baseline still advances


def test_measure_wrong_sign_call_delta_is_unlabelled():
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, -0.5, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 10.0
    assert prev["C1"] == 1010.0


def test_measure_wrong_sign_put_delta_is_unlabelled():
    row, _ = hiro.measure_chain(_chain(puts=[_c("P1", 1010, 0.4, 1.10)]), {"P1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 10.0


@pytest.mark.parametrize("delta", [0.0, -0.0])
def test_measure_zero_delta_is_labelled_with_no_impact(delta):
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, delta, 1.10)],
                                       puts=[_c("P1", 1010, delta, 1.10)]),
                                {"C1": 1000.0, "P1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 20.0 and row["unclassified_vol"] == 0.0


def test_measure_volume_drop_books_nothing_and_keeps_high_water():
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 5, 0.5, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0 and prev["C1"] == 1000.0


def test_measure_volume_glitch_does_not_book_the_day_into_the_next_minute():
    """A one-off totalVolume=0 read must not reset the baseline: the next real
    read then books only the true increment, not the contract's whole day."""
    _, prev = hiro.measure_chain(_chain(calls=[_c("C1", 0, 0.5, 1.10)]), {"C1": 1000.0})
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.10)]), prev)
    assert row["classified_vol"] == 10.0
    assert prev["C1"] == 1010.0


@pytest.mark.parametrize("spot", [None, 0, -1, math.nan])
def test_measure_unusable_spot_returns_no_row(spot):
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10)], spot=spot)
    row, prev = hiro.measure_chain(chain, {"C1": 1000.0})
    assert row is None
    assert prev == {"C1": 1000.0}          # nothing consumed: the next good minute books it


@pytest.mark.parametrize("bad", [None, {}, "x", {"callExpDateMap": "x", "underlyingPrice": 500}])
def test_measure_malformed_chain_is_total(bad):
    row, prev = hiro.measure_chain(bad, {})
    assert prev == {}
    if isinstance(bad, dict) and bad.get("underlyingPrice") == 500:
        # a usable spot with no readable contracts is a MEASURED zero, not "no data"
        assert row == {"spot": 500.0, "impact": 0.0, "classified_vol": 0.0,
                       "unclassified_vol": 0.0}


def test_measure_does_not_mutate_callers_prev():
    prev = {"C1": 1000.0}
    hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.10),
                                     _c("C2", 50, 0.3, 1.10)]), prev)
    assert prev == {"C1": 1000.0}


def test_measure_carries_forward_a_contract_absent_this_minute():
    row, new_prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.10)]),
                                       {"C1": 1000.0, "C9": 77.0})
    assert new_prev == {"C1": 1010.0, "C9": 77.0}


def test_measure_mixed_labelled_and_mid_prints_fill_both_counters():
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10),      # 10 bought at ask
                          _c("C2", 506, 0.5, 1.05)])      # 6 at the midpoint
    row, _ = hiro.measure_chain(chain, {"C1": 1000.0, "C2": 500.0})
    assert row["classified_vol"] == 10.0 and row["unclassified_vol"] == 6.0
    assert row["impact"] == pytest.approx(250_000.0)


def test_measure_duplicate_contract_books_once():
    """The same OSI listed twice (under two strikes) books its new volume ONCE:
    the second copy reads the baseline the first just wrote, so it adds 0."""
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10), _c("C1", 1010, 0.5, 1.10)])
    row, prev = hiro.measure_chain(chain, {"C1": 1000.0})
    assert row["classified_vol"] == 10.0
    assert row["impact"] == pytest.approx(250_000.0)
    assert prev == {"C1": 1010.0}


# --- Surge -----------------------------------------------------------------

CFG = {"window_min": 15, "k": 3.0, "min_notional": 1_000_000,
       "max_unclassified": 0.5, "baseline_sessions": 2, "min_minutes": 30,
       "flip_band": 1.0}


def _rows(impacts, t0=36000, spot=500.0, uncl=0.0):
    return [{"ts": t0 + 60 * i, "spot": spot, "impact": float(x),
             "classified_vol": 10.0, "unclassified_vol": uncl}
            for i, x in enumerate(impacts)]


def test_window_sum_covers_last_n_minutes_only():
    rows = _rows([1, 2, 3, 4])
    w = hiro.window_sum(rows, rows[-1]["ts"], 120)      # last 2 minutes
    assert w["impact"] == 7.0 and w["n"] == 2


def test_window_sum_unclassified_share():
    rows = _rows([1, 1], uncl=10.0)
    assert hiro.window_sum(rows, rows[-1]["ts"], 900)["unclassified_share"] == 0.5


def test_full_window_sums_skip_partial_windows():
    rows = _rows([1] * 20)
    sums = hiro.full_window_sums(rows, 900)
    assert len(sums) == 6 and all(s == 15.0 for s in sums)


def test_rms_ignores_nonfinite_and_rejects_zero():
    assert hiro.rms([3.0, -4.0]) == pytest.approx(math.sqrt(12.5))
    assert hiro.rms([math.nan, 3.0]) == 3.0
    assert hiro.rms([]) is None and hiro.rms([0.0, 0.0]) is None


def test_baseline_prefers_prior_sessions():
    prior = [_rows([1e6] * 20), _rows([-1e6] * 20)]
    sigma = hiro.baseline_sigma(prior, _rows([9e9] * 40), CFG)
    assert sigma == pytest.approx(15e6)                # today's spike not used


def test_baseline_uses_only_the_newest_prior_sessions():
    prior = [_rows([1e6] * 20), _rows([-1e6] * 20), _rows([9e9] * 20)]  # newest first
    assert hiro.baseline_sigma(prior, [], CFG) == pytest.approx(15e6)   # oldest excluded


def test_baseline_falls_back_to_today_then_none():
    assert hiro.baseline_sigma([], _rows([1e6] * 30), CFG) == pytest.approx(15e6)
    assert hiro.baseline_sigma([], _rows([1e6] * 29), CFG) is None


def test_baseline_partial_prior_history_is_not_mixed_in():
    """Fewer prior sessions than baseline_sessions -> today alone, never a blend."""
    prior = [_rows([9e9] * 20)]
    assert hiro.baseline_sigma(prior, _rows([1e6] * 30), CFG) == pytest.approx(15e6)


def test_baseline_prior_sessions_with_no_full_window_falls_back_to_today():
    prior = [_rows([1e6] * 5), _rows([1e6] * 5)]       # each shorter than a window
    assert hiro.baseline_sigma(prior, _rows([1e6] * 30), CFG) == pytest.approx(15e6)


def test_baseline_prior_sessions_with_no_full_window_and_short_today_is_none():
    prior = [_rows([1e6] * 5), _rows([1e6] * 5)]
    assert hiro.baseline_sigma(prior, _rows([1e6] * 29), CFG) is None


def test_baseline_nan_row_drops_its_windows_never_poisons_sigma():
    """A NaN minute makes every window containing it NaN; rms drops those
    windows, so sigma comes from the clean ones rather than reading NaN."""
    bad = _rows([1e6] * 20)
    bad[19]["impact"] = math.nan                       # only the LAST window holds it
    assert math.isnan(hiro.window_sum(bad, bad[19]["ts"], 900)["impact"])
    sums = hiro.full_window_sums(bad, 900)
    assert sum(1 for s in sums if math.isnan(s)) == 1
    sigma = hiro.baseline_sigma([bad, _rows([-1e6] * 20)], [], CFG)
    assert sigma == pytest.approx(15e6)


def test_baseline_all_nan_prior_falls_back_to_today():
    prior = [_rows([math.nan] * 20), _rows([math.nan] * 20)]
    assert hiro.baseline_sigma(prior, _rows([1e6] * 30), CFG) == pytest.approx(15e6)


def test_surge_fires_dealers_buying_above_k():
    rows = _rows([0] * 15 + [4e6] * 15)                 # last 15m = 60e6
    a = hiro.detect_surge("SPY", rows, 15e6, CFG)
    assert a["type"] == "hiro_surge" and a["side"] == "dealers_buying"
    assert a["impact"] == 60e6 and a["mult"] == pytest.approx(4.0)
    assert a["ts"] == rows[-1]["ts"] and a["spot"] == 500.0
    assert a["unclassified_share"] == 0.0 and a["window_min"] == 15


def test_surge_selling_side():
    a = hiro.detect_surge("SPY", _rows([-4e6] * 15), 15e6, CFG)
    assert a["side"] == "dealers_selling"


def test_surge_silent_below_k_below_floor_unlabelled_or_no_sigma():
    assert hiro.detect_surge("SPY", _rows([2e6] * 15), 15e6, CFG) is None   # 2x
    small = {**CFG, "min_notional": 1e9}
    assert hiro.detect_surge("SPY", _rows([4e6] * 15), 15e6, small) is None
    assert hiro.detect_surge("SPY", _rows([4e6] * 15, uncl=20.0), 15e6, CFG) is None
    assert hiro.detect_surge("SPY", _rows([4e6] * 15), None, CFG) is None
    assert hiro.detect_surge("SPY", [], 15e6, CFG) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_surge_never_fires_on_a_non_finite_window(bad):
    rows = _rows([4e6] * 15)
    rows[7]["impact"] = bad
    assert hiro.detect_surge("SPY", rows, 15e6, CFG) is None


def test_surge_needs_a_fresh_newest_row_against_now_ts():
    """Rows stop at the close while detection runs on; a stale window must not
    re-fire after its cooldown. A slow poll (newest row 300 s old) still counts;
    a stalled collector (301 s) does not."""
    rows = _rows([4e6] * 15)
    last = rows[-1]["ts"]
    assert hiro.detect_surge("SPY", rows, 15e6, CFG,
                             now_ts=last + hiro.STALE_ROW_SEC) is not None
    assert hiro.detect_surge("SPY", rows, 15e6, CFG,
                             now_ts=last + hiro.STALE_ROW_SEC + 1) is None
    assert hiro.STALE_ROW_SEC == 300


def test_surge_survives_a_slow_tick():
    """The row ts is the minute floor of the collect START and the check runs
    after a 30-90 s poll: a newest row 150 s old against the clock is a slow
    tick, not frozen data, and must still fire."""
    rows = _rows([4e6] * 15)
    assert hiro.detect_surge("SPY", rows, 15e6, CFG,
                             now_ts=rows[-1]["ts"] + 150) is not None


@pytest.mark.parametrize("sigma", [math.nan, math.inf, 0.0, -1.0])
def test_surge_refuses_unusable_sigma(sigma):
    assert hiro.detect_surge("SPY", _rows([4e6] * 15), sigma, CFG) is None


# --- Flip (reversal) ---------------------------------------------------------

def test_ct_ts_is_central_wallclock():
    import datetime as _d
    from zoneinfo import ZoneInfo
    want = _d.datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago")).timestamp()
    assert hiro.ct_ts("2026-10-01", "09:00") == int(want)


def test_flip_transitions_hysteresis_and_baseline():
    # impacts 5,5,-6,-6,-4,-6 -> cum 5,10,4,-2,-6,-12 ; band 5 -> baseline
    # buying at the first row (5 >= 5), selling only once cum <= -5 (at -6)
    rows = _rows([5, 5, -6, -6, -4, -6])
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[0]["ts"])
    assert [(s) for _, s, _ in t] == ["selling"]
    assert t[0][0] == rows[4]["ts"]


def test_flip_transitions_ignore_minutes_before_not_before_but_keep_their_cum():
    rows = _rows([20, -1, -1])                        # cum 20, 19, 18
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[1]["ts"])
    assert t == []                                    # baseline buying, no change


def test_flip_transitions_carry_cum_from_before_not_before():
    """Discriminating: cum 20 -> 10 (baseline buying) -> -6 (selling). An
    implementation that reset the total at not_before would see -10 then -26
    (baseline selling, no change) and return []."""
    rows = _rows([20, -10, -16])
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[1]["ts"])
    assert t == [(rows[2]["ts"], "selling", -6.0)]


def test_detect_flip_age_measured_against_the_newest_row_not_now_ts():
    """The transition's age is measured against the NEWEST ROW, so a slow tick
    (newest row 150 s old against the clock) does not lose the reversal -- the
    report replays with now_ts = row ts and counts it, so live must too. The
    clock only refuses frozen data: a newest row over STALE_ROW_SEC old."""
    rows = _rows([10, -30])                           # the flip is the newest row
    flip_ts = rows[1]["ts"]
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None,
                            now_ts=flip_ts + 150) is not None
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None,
                            now_ts=flip_ts + hiro.STALE_ROW_SEC) is not None
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None,
                            now_ts=flip_ts + hiro.STALE_ROW_SEC + 1) is None


def test_detect_flip_old_transition_is_refused_against_the_newest_row_even_on_a_fresh_clock():
    """A fresh clock cannot rescue a transition that is history against the
    newest row (a restart, or an intraday-moving sigma surfacing an old one)."""
    rows = _rows([10, -30, -1, -1, -1])               # flip at minute 1, newest minute 4
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None,
                            now_ts=rows[-1]["ts"]) is None


def test_detect_flip_fresh_newer_than_seen():
    rows = _rows([10, -30])                           # cum 10 -> -20
    a = hiro.detect_flip("SPY", rows, sigma=5.0, cfg=CFG,
                         not_before_ts=rows[0]["ts"], seen_ts=None)
    assert a["type"] == "hiro_flip" and a["side"] == "to_selling"
    assert a["ts"] == rows[1]["ts"] and a["cum"] == -20.0 and a["spot"] == 500.0
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], seen_ts=a["ts"]) is None


def test_detect_flip_ignores_a_stale_transition():
    rows = _rows([10, -30, -1, -1, -1])               # flip at minute 1, now minute 4
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None) is None


def test_detect_flip_needs_sigma():
    assert hiro.detect_flip("SPY", _rows([10, -30]), None, CFG, 0, None) is None


@pytest.mark.parametrize("sigma", [math.nan, math.inf, 0.0, -5.0])
def test_detect_flip_refuses_unusable_sigma(sigma):
    assert hiro.detect_flip("SPY", _rows([10, -30]), sigma, CFG, 0, None) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_flip_transitions_skip_a_non_finite_minute(bad):
    """A NaN total would make every later comparison False and freeze the state
    for the rest of the day; the bad minute is skipped instead."""
    rows = _rows([10, bad, -30])                       # cum 10 -> (skip) -> -20
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[0]["ts"])
    assert t == [(rows[2]["ts"], "selling", -20.0)]
    a = hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None)
    assert a["side"] == "to_selling" and a["cum"] == -20.0


def test_detect_flip_to_buying_mirror():
    rows = _rows([-10, 30])                           # cum -10 -> +20
    a = hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None)
    assert a["side"] == "to_buying" and a["ts"] == rows[1]["ts"] and a["cum"] == 20.0


def test_flip_dead_zone_never_sets_a_state():
    rows = _rows([1, -2, 3, -4, 2])                   # cum 1,-1,2,-2,0 : inside +-5
    assert hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[0]["ts"]) == []
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None) is None


def test_detect_flip_age_limit_is_inclusive():
    rows = _rows([10, -30, 0])
    flip_ts = rows[1]["ts"]
    rows[2]["ts"] = flip_ts + hiro.FLIP_MAX_AGE_SEC
    a = hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None)
    assert a is not None and a["ts"] == flip_ts
    rows[2]["ts"] = flip_ts + hiro.FLIP_MAX_AGE_SEC + 1
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None) is None


# --- View row ----------------------------------------------------------------

def test_symbol_view_summarises_latest_minute():
    rows = _rows([1e6] * 15)
    v = hiro.symbol_view(rows, 5e6, CFG)
    assert v == {"ts": rows[-1]["ts"], "spot": 500.0, "impact": 1e6, "cum": 15e6,
                 "window_impact": 15e6, "sigma": 5e6, "mult": pytest.approx(3.0),
                 "unclassified_share": 0.0}
    assert hiro.symbol_view(rows, None, CFG)["mult"] is None


def test_symbol_view_non_finite_reads_as_none_never_a_number():
    rows = _rows([1e6] * 15)
    rows[14]["impact"] = math.nan                      # the latest minute, in the window
    v = hiro.symbol_view(rows, 5e6, CFG)
    # cum is the shared running total, which SKIPS the bad minute (14 x 1e6)
    assert v["impact"] is None and v["cum"] == pytest.approx(14e6)
    assert v["window_impact"] is None and v["mult"] is None
    assert v["ts"] == rows[-1]["ts"] and v["spot"] == 500.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_running_total_skips_non_finite_minutes(bad):
    assert hiro.running_total(_rows([10, bad, -30])) == -20.0
    assert hiro.running_total([]) == 0.0


def test_screen_total_and_reversal_total_agree():
    """symbol_view's cum and the flip rule's running total are one computation,
    so the screen cannot show a total the reversal rule did not use."""
    rows = _rows([10, math.nan, -30])
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[0]["ts"])
    assert t[-1][2] == hiro.symbol_view(rows, 5.0, CFG)["cum"] == hiro.running_total(rows)


@pytest.mark.parametrize("sigma", [math.nan, math.inf])
def test_symbol_view_non_finite_sigma_gives_no_mult(sigma):
    v = hiro.symbol_view(_rows([1e6] * 15), sigma, CFG)
    assert v["mult"] is None and v["sigma"] is None
    assert v["window_impact"] == 15e6


# --- prior_sigma: the prior-session half of baseline_sigma, memoizable per day --

def test_prior_sigma_is_the_rms_of_the_newest_prior_sessions():
    prior = [_rows([1e6] * 20), _rows([-1e6] * 20), _rows([9e9] * 20)]  # newest first
    assert hiro.prior_sigma(prior, CFG) == pytest.approx(15e6)        # oldest excluded


def test_prior_sigma_none_with_too_few_sessions():
    assert hiro.prior_sigma([_rows([1e6] * 20)], CFG) is None
    assert hiro.prior_sigma([], CFG) is None


def test_prior_sigma_none_without_a_full_window():
    assert hiro.prior_sigma([_rows([1e6] * 5), _rows([1e6] * 5)], CFG) is None
    assert hiro.prior_sigma([_rows([math.nan] * 20), _rows([math.nan] * 20)], CFG) is None


def test_baseline_sigma_agrees_with_prior_sigma_when_prior_exists():
    prior = [_rows([2e6] * 20), _rows([-1e6] * 20)]
    assert hiro.baseline_sigma(prior, _rows([9e9] * 40), CFG) == hiro.prior_sigma(prior, CFG)
