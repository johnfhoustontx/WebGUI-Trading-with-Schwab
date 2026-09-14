"""The Strategy Finder's whole-chain fetch (2026-09-14).

One /chains call for SPY's whole chain timed out at the proxy's 30 s; groups of 8
consecutive expiries, 4 at a time, returned it in 6.5 s ($SPX's 56 expiries in 11 s).
"""
import datetime as dt

from services.options_svc import compute

TODAY = dt.date.today()


def _d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def test_runs_hold_at_most_eight_consecutive_expiries():
    exps = [_d(i) for i in range(1, 21)]                 # 20 listed
    runs = compute.scan_expiry_runs(exps, dte_max=None)
    assert [len(r) for r in runs] == [8, 8, 4]
    assert [e for r in runs for e in r] == exps


def test_runs_stop_at_dte_max_plus_the_two_day_slack_and_start_today():
    exps = [_d(0), _d(5), _d(30), _d(31), _d(32), _d(33)]
    runs = compute.scan_expiry_runs(exps, dte_max=30)
    assert [e for r in runs for e in r] == [_d(0), _d(5), _d(30), _d(31), _d(32)]


def test_runs_skip_an_expiry_already_past():
    exps = [_d(-1), _d(0), _d(3)]
    assert [e for r in compute.scan_expiry_runs(exps, None) for e in r] == [_d(0), _d(3)]


def test_no_dte_max_takes_every_listed_expiry():
    exps = [_d(1), _d(400), _d(2000)]
    assert [e for r in compute.scan_expiry_runs(exps, None) for e in r] == exps


def test_raw_merge_unions_expiry_maps_and_keeps_top_level_fields():
    a = {"underlyingPrice": 540.0, "volatility": 22.0,
         "callExpDateMap": {"x:1": {"1.0": []}}, "putExpDateMap": {"x:1": {"1.0": []}}}
    b = {"underlyingPrice": 541.0,
         "callExpDateMap": {"y:9": {"2.0": []}}, "putExpDateMap": {}}
    out = compute.merge_raw_chains([a, None, b])
    assert out["underlyingPrice"] == 540.0 and out["volatility"] == 22.0
    assert set(out["callExpDateMap"]) == {"x:1", "y:9"}
    assert set(out["putExpDateMap"]) == {"x:1"}
    assert a["callExpDateMap"] == {"x:1": {"1.0": []}}      # inputs not mutated


def test_merge_of_nothing_is_None():
    assert compute.merge_raw_chains([None, None]) is None


def test_fetch_groups_runs_and_counts_a_failed_one(monkeypatch):
    exps = [_d(i) for i in range(1, 18)]                 # 17 -> runs of 8, 8, 1
    monkeypatch.setattr(compute, "option_expirations", lambda api: exps)
    seen = []

    def _fetch(client, symbol, from_date=None, to_date=None):
        seen.append((str(from_date), str(to_date)))
        if str(from_date) == exps[8]:
            return None                                   # the second run fails
        return {"underlyingPrice": 1.0,
                "callExpDateMap": {f"{from_date}:1": {}}, "putExpDateMap": {}}

    monkeypatch.setattr(compute.se, "fetch_option_chain", _fetch)
    chain, failed = compute.fetch_scan_chain("NVDA", dte_max=None)
    assert sorted(seen) == sorted([(exps[0], exps[7]), (exps[8], exps[15]),
                                   (exps[16], exps[16])])
    assert failed == 8                                    # expiries, not runs
    assert chain is not None and len(chain["callExpDateMap"]) == 2


def test_no_expiration_list_falls_back_to_the_single_fetch(monkeypatch):
    monkeypatch.setattr(compute, "option_expirations", lambda api: [])
    seen = []
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None:
                        seen.append((from_date, to_date)) or {"underlyingPrice": 1.0})
    chain, failed = compute.fetch_scan_chain("SPY", dte_max=30)
    assert seen == [(TODAY, TODAY + dt.timedelta(days=32))] and failed == 0
    seen.clear()
    compute.fetch_scan_chain("SPY", dte_max=None)
    assert seen == [(TODAY, None)]


def test_an_expiration_list_that_raises_falls_back_rather_than_failing(monkeypatch):
    def _boom(api):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(compute, "option_expirations", _boom)
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None:
                        {"underlyingPrice": 1.0})
    chain, failed = compute.fetch_scan_chain("SPY", dte_max=30)
    assert chain == {"underlyingPrice": 1.0} and failed == 0


def test_every_run_failing_is_no_chain(monkeypatch):
    monkeypatch.setattr(compute, "option_expirations", lambda api: [_d(1), _d(2)])
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: None)
    chain, failed = compute.fetch_scan_chain("SPY", dte_max=None)
    assert chain is None and failed == 2


def test_swing_scan_accepts_no_dte_max(monkeypatch):
    """``dte_max=None`` reaches the fetch as None; with no chain the scan is the
    explicit empty result, carrying the (zero) failed-expiry count."""
    seen = {}

    def _fetch(symbol, dte_max):
        seen["dte_max"] = dte_max
        return None, 0

    monkeypatch.setattr(compute, "fetch_scan_chain", _fetch)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": 540.0})
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1)
    assert seen["dte_max"] is None and out["signals"] == []
    assert out["expiries_failed"] == 0


def test_swing_scan_hands_the_builders_a_number_when_there_is_no_dte_max(monkeypatch):
    """Every builder and ``screen_spreads`` compares DTE against a number, so
    ``dte_max=None`` reaches them as ``_NO_DTE_MAX`` - never as None."""
    import strategy_scanner as ssn

    chain = {"underlyingPrice": 540.0, "callExpDateMap": {}, "putExpDateMap": {}}
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda symbol, dte_max: (chain, 3))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": 540.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda c, s: None)
    monkeypatch.setattr(compute, "run_iv_analysis", lambda *a, **k: {})
    got = {}
    monkeypatch.setattr(compute.se, "screen_spreads",
                        lambda chain, symbol, dte_min, dte_max, *a, **k:
                        got.__setitem__("screen_spreads", dte_max) or [])
    for name in ("build_directional", "build_debit_verticals",
                 "build_straddles_strangles", "build_butterflies_condors",
                 "build_calendars", "build_stock_structures"):
        monkeypatch.setattr(ssn, name,
                            lambda chain, symbol, spot, atm_iv, dte_min, dte_max,
                            *a, _n=name, **k: got.__setitem__(_n, dte_max) or [])
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1)
    assert got and set(got.values()) == {compute._NO_DTE_MAX}
    assert len(got) == 7
    assert out["expiries_failed"] == 3
