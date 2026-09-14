"""The Strategy Finder keeps the best N of each strategy type (2026-09-14).

A whole-chain scan on every expiry leaves over a thousand rows after the quality
cut on an index. ``swing_scan(per_type_limit=N)`` keeps the best N of each
``type`` by ``composite_score`` and counts the rest as ``not_shown``; ids and
payoff curves are built after the limit, so a dropped row costs nothing more.
Design §7.
"""
import math

import pytest

from services.options_svc import compute

BANDS = (-0.2, -0.1, 0.1, 0.2, 0.1)


def _row(typ, score, n):
    return {"type": typ, "composite_score": score, "grade": "Good", "dte": 10,
            "expiration": "2099-01-01", "short_strike": n,
            "legs": [{"kind": "call", "strike": 100.0 + n, "mark": 1.0,
                      "expiration": "2099-01-01"}]}


@pytest.fixture
def rows_scan(monkeypatch):
    """``run(rows, **kw)`` -> swing_scan over exactly ``rows``, scores untouched
    and in the given order (no rescoring, no re-sort, no quality cut)."""
    import strategy_scanner as ssn
    import strategy_scoring as ssc

    monkeypatch.setattr(compute, "fetch_scan_chain",
                        lambda s, d: ({"underlyingPrice": 100.0, "putExpDateMap": {},
                                       "callExpDateMap": {}}, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda *a, **k: {"last": 100.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **k: {"h": 1})
    monkeypatch.setattr(compute.se, "calc_technicals", lambda *a, **k: {})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda *a, **k: {"iv_rank": 50.0, "expected_moves":
                                         {"daily": {"move_dollars": 1.0}}})
    monkeypatch.setattr(ssc, "score_all", lambda signals, *a, **k: signals)
    monkeypatch.setattr(compute, "_passes_swing_cut", lambda s: True)
    curves = []
    monkeypatch.setattr(ssn, "payoff_curve",
                        lambda legs, *a, **k: curves.append(legs[0]["strike"]) or [])

    def run(rows, **kw):
        monkeypatch.setattr(ssn, "build_directional",
                            lambda *a, **k: [dict(r) for r in rows])
        kw.setdefault("payoff", False)
        return compute.swing_scan("SPY", 0, None, *BANDS, families=("DIRECTIONAL",),
                                  **kw)

    run.curves = curves
    return run


def _thirty_and_three():
    # Thirty LONG_CALL scores 1..30 in a scrambled order, then three PCS.
    order = [(7 * i) % 30 + 1 for i in range(30)]
    return ([_row("LONG_CALL", float(s), i) for i, s in enumerate(order)]
            + [_row("PCS", 10.0 + i, 100 + i) for i in range(3)])


def test_the_finder_limit_is_twenty_five():
    assert compute.FINDER_PER_TYPE_LIMIT == 25


def test_keeps_the_best_n_of_each_type_and_counts_the_rest(rows_scan):
    out = rows_scan(_thirty_and_three(), per_type_limit=25)
    calls = [s for s in out["signals"] if s["type"] == "LONG_CALL"]
    assert len(calls) == 25
    assert sorted(s["composite_score"] for s in calls) == [float(s) for s in range(6, 31)]
    assert len([s for s in out["signals"] if s["type"] == "PCS"]) == 3
    assert out["not_shown"] == 5


def test_the_kept_rows_keep_their_relative_order(rows_scan):
    rows = _thirty_and_three()
    out = rows_scan(rows, per_type_limit=25)
    want = [r["short_strike"] for r in rows
            if not (r["type"] == "LONG_CALL" and r["composite_score"] <= 5)]
    assert len(want) == 28
    assert [s["short_strike"] for s in out["signals"]] == want


def test_no_limit_keeps_everything(rows_scan):
    out = rows_scan(_thirty_and_three())
    assert len(out["signals"]) == 33
    assert out["not_shown"] == 0


def test_a_missing_or_non_finite_score_ranks_last(rows_scan):
    rows = [_row("LONG_PUT", None, 0), _row("LONG_PUT", math.nan, 1),
            _row("LONG_PUT", 0.0, 2), _row("LONG_PUT", math.inf, 3),
            _row("LONG_PUT", 40.0, 4)]
    out = rows_scan(rows, per_type_limit=2)
    # 0.0 is a real score and outranks a missing one; an infinite score is not a
    # reading and does not take the top place.
    assert [s["short_strike"] for s in out["signals"]] == [2, 4]
    assert out["not_shown"] == 3


def test_a_limit_the_type_does_not_reach_drops_nothing(rows_scan):
    rows = [_row("LONG_PUT", None, 0), _row("LONG_PUT", 10.0, 1)]
    out = rows_scan(rows, per_type_limit=2)
    assert [s["short_strike"] for s in out["signals"]] == [0, 1]
    assert out["not_shown"] == 0


def test_payoff_curves_are_built_only_for_kept_rows(rows_scan):
    out = rows_scan(_thirty_and_three(), per_type_limit=25, payoff=True)
    assert len(out["signals"]) == 28
    assert sorted(rows_scan.curves) == sorted(s["legs"][0]["strike"]
                                              for s in out["signals"])


def test_ids_are_unique_after_the_limit(rows_scan):
    rows = [_row("LONG_CALL", float(i), 1) for i in range(30)]   # one strike on all
    out = rows_scan(rows, per_type_limit=25)
    ids = [s["id"] for s in out["signals"]]
    assert len(ids) == 25 and len(set(ids)) == 25


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "25"])
def test_a_limit_that_is_not_a_positive_count_is_refused(bad, rows_scan):
    with pytest.raises(ValueError):
        rows_scan([_row("LONG_CALL", 1.0, 0)], per_type_limit=bad)


def test_the_early_returns_carry_not_shown(monkeypatch):
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda s, d: (None, 3))
    assert compute.swing_scan("SPY", 0, None, *BANDS, per_type_limit=25)["not_shown"] == 0
    monkeypatch.setattr(compute, "fetch_scan_chain",
                        lambda s, d: ({"putExpDateMap": {}, "callExpDateMap": {}}, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: {})
    assert compute.swing_scan("SPY", 0, None, *BANDS, per_type_limit=25)["not_shown"] == 0


def test_every_expiry_with_the_limit_on_the_real_builders(scan_env):
    full = compute.swing_scan("SPY", 0, None, *BANDS, every_expiry=True, payoff=False)
    capped = compute.swing_scan("SPY", 0, None, *BANDS, every_expiry=True, payoff=False,
                                per_type_limit=2)
    counts = {}
    for s in capped["signals"]:
        counts[s["type"]] = counts.get(s["type"], 0) + 1
    assert counts and max(counts.values()) == 2
    assert capped["not_shown"] == len(full["signals"]) - len(capped["signals"]) > 0
