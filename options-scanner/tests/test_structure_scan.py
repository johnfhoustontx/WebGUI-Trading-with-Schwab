"""``structure_scan`` - the Market Scanner's structures other than credit spreads.

Two halves: ``build_window`` (which candidates one DTE window offers, built by
the Strategy Finder's own builders) and ``select`` (the Scanner's gates, in one
fixed order, each counting what it removed so the funnel bucket partitions).

Design: docs/plans/2026-10-06-scanner-multi-structure-design.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import structure_scan as sx  # noqa: E402
from tests._bs_chain import bs_chain  # noqa: E402

ALL = ("VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR")
BAND = (0.15, 0.27)


def _types(rows):
    return {r["type"] for r in rows}


# ── merge_chains ────────────────────────────────────────────────────────────

def test_merge_chains_holds_both_chains_expirations():
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    merged = sx.merge_chains(front, back)
    assert len(merged["callExpDateMap"]) == 2 and len(merged["putExpDateMap"]) == 2
    assert merged["underlyingPrice"] == front["underlyingPrice"]
    # Neither input is mutated.
    assert len(front["callExpDateMap"]) == 1 and len(back["callExpDateMap"]) == 1


def test_merge_chains_prefers_the_front_chains_copy_of_a_shared_expiration():
    front, back = bs_chain(days=(9,)), bs_chain(days=(9, 37), iv=0.50)
    merged = sx.merge_chains(front, back)
    exp = next(iter(front["callExpDateMap"]))
    assert merged["callExpDateMap"][exp] is front["callExpDateMap"][exp]


def test_merge_chains_survives_a_missing_or_failed_back_chain():
    front = bs_chain(days=(9,))
    assert sx.merge_chains(front, None) is front
    assert sx.merge_chains(front, {}) is front
    assert sx.merge_chains(front, {"status": "FAILED"}) is front


# ── build_window ────────────────────────────────────────────────────────────

def test_the_short_window_builds_two_day_structures():
    rows = sx.build_window(bs_chain(days=(2,)), "T", 100.0, 0.28, 0, 4,
                           families=ALL, short_band=BAND)
    assert {"BULL_CALL", "BEAR_PUT", "LONG_STRADDLE", "LONG_STRANGLE",
            "BUTTERFLY_CALL"} <= _types(rows)
    assert all(r["dte"] == 2 for r in rows)
    assert not any(t.startswith(("CALENDAR", "DIAGONAL")) for t in _types(rows))


def test_the_swing_window_builds_at_its_own_minimum_not_the_finders_seven():
    rows = sx.build_window(bs_chain(days=(5,)), "T", 100.0, 0.28, 5, 15,
                           families=("STRADDLE", "BUTTERFLY"), short_band=BAND)
    assert "LONG_STRADDLE" in _types(rows)
    assert all(r["dte"] == 5 for r in rows)


def test_calendars_need_the_back_chain():
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    without = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                              short_band=BAND)
    with_back = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                                short_band=BAND, back_chain=back,
                                back_dte_max=45)
    assert "CALENDAR_CALL" not in _types(without)
    assert {"CALENDAR_CALL", "CALENDAR_PUT"} <= _types(with_back)
    cal = next(r for r in with_back if r["type"] == "CALENDAR_CALL")
    assert sorted(l["expiration"] for l in cal["legs"])[0] == cal["expiration"]
    assert len({l["expiration"] for l in cal["legs"]}) == 2


def test_the_back_chain_only_feeds_calendars():
    """A straddle must sit on the window's own expiry, never the back month."""
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    rows = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                           short_band=BAND, back_chain=back, back_dte_max=45)
    for r in rows:
        if r["group"] != "CALENDAR":
            assert r["dte"] == 9, r["type"]


def test_every_row_carries_its_build_group():
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    rows = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                           short_band=BAND, back_chain=back, back_dte_max=45)
    assert rows and {r["group"] for r in rows} == set(ALL)
    by = {r["type"]: r["group"] for r in rows}
    assert by["BULL_CALL"] == "VERTICAL" and by["LONG_STRADDLE"] == "STRADDLE"
    assert by["IRON_BUTTERFLY"] == "BUTTERFLY" and by["CALENDAR_PUT"] == "CALENDAR"


def test_a_family_left_out_is_not_built():
    rows = sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                           families=("VERTICAL",), short_band=BAND)
    assert rows and _types(rows) <= {"BULL_CALL", "BEAR_PUT"}
    assert sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                           families=(), short_band=BAND) == []


def test_an_unknown_family_builds_nothing_and_does_not_raise():
    assert sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                           families=("STOCK", "TYPO"), short_band=BAND) == []


def test_the_short_strangle_respects_the_band_ceiling():
    rows = sx.build_window(bs_chain(days=(9,), step=0.5), "T", 100.0, 0.28, 5, 15,
                           families=("STRADDLE",), short_band=BAND)
    strangle = next(r for r in rows if r["type"] == "SHORT_STRANGLE")
    assert all(0.15 <= abs(l["delta"]) <= 0.27 for l in strangle["legs"])


def test_no_expiry_in_the_window_builds_nothing():
    assert sx.build_window(bs_chain(days=(30,)), "T", 100.0, 0.28, 0, 4,
                           families=ALL, short_band=BAND) == []


# ── select ──────────────────────────────────────────────────────────────────

NEUTRAL = {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"}


def _row(t, score, vega, group="X", dte=9, grade="Good"):
    # A pre-scored row: select() is tested with scoring stubbed out.
    return {"type": t, "id": f"T_{t}", "symbol": "T", "dte": dte, "group": group,
            "composite_score": score, "grade": grade, "net_vega": vega,
            "expiration": "2099-01-01", "legs": []}


def _kw(**over):
    kw = dict(view=NEUTRAL, atm_iv=0.28, daily_em=1.5, dte_min=5, iv_rank=50,
              floor=30, ceiling=0, spans_earnings=lambda s: False,
              earnings_date=None, keep_long_through_earnings=True,
              min_score=50.0, excluded_grades=("Weak",), max_per_family=2)
    kw.update(over)
    return kw


def _select(rows, monkeypatch, **over):
    monkeypatch.setattr(sx._ssc, "score_all", lambda sigs, *a, **k: list(sigs))
    bucket = {}
    return sx.select(rows, bucket=bucket, **_kw(**over)), bucket


def test_short_premium_under_the_floor_is_gated_and_long_is_not(monkeypatch):
    rows = [_row("SHORT_STRANGLE", 70, -0.2), _row("LONG_STRADDLE", 60, 0.3)]
    kept, b = _select(rows, monkeypatch, iv_rank=10)
    assert [r["type"] for r in kept] == ["LONG_STRADDLE"]
    assert b["built"] == 2 and b["vol_gate"] == 1


def test_an_unknown_iv_rank_skips_the_volatility_gate(monkeypatch):
    kept, b = _select([_row("SHORT_STRANGLE", 70, -0.2)], monkeypatch, iv_rank=None)
    assert len(kept) == 1 and b.get("vol_gate", 0) == 0


def test_long_premium_through_a_report_is_kept_and_flagged(monkeypatch):
    rows = [_row("LONG_STRADDLE", 60, 0.3), _row("SHORT_STRANGLE", 70, -0.2)]
    kept, b = _select(rows, monkeypatch, spans_earnings=lambda s: True,
                      earnings_date="2099-01-02")
    assert [r["type"] for r in kept] == ["LONG_STRADDLE"]
    assert kept[0]["spans_earnings"] is True
    assert kept[0]["earnings_date"] == "2099-01-02"
    assert b["earnings"] == 1


def test_drop_mode_removes_long_premium_too(monkeypatch):
    kept, b = _select([_row("LONG_STRADDLE", 60, 0.3)], monkeypatch,
                      spans_earnings=lambda s: True,
                      keep_long_through_earnings=False)
    assert kept == [] and b["earnings"] == 1


def test_a_row_with_no_readable_vega_is_dropped_through_a_report(monkeypatch):
    for vega in (None, 0, float("nan")):
        kept, b = _select([_row("BUTTERFLY_CALL", 60, vega)], monkeypatch,
                          spans_earnings=lambda s: True)
        assert kept == [] and b["earnings"] == 1, vega


def test_no_report_means_no_flag(monkeypatch):
    kept, b = _select([_row("LONG_STRADDLE", 60, 0.3)], monkeypatch)
    assert "spans_earnings" not in kept[0] and b.get("earnings", 0) == 0


def test_the_earnings_check_is_asked_per_candidate(monkeypatch):
    rows = [_row("LONG_STRADDLE", 60, 0.3, dte=0), _row("LONG_STRANGLE", 61, 0.3, dte=3)]
    kept, _ = _select(rows, monkeypatch, spans_earnings=lambda s: s["dte"] > 0,
                      earnings_date="2099-01-02")
    by = {r["type"]: r for r in kept}
    assert "spans_earnings" not in by["LONG_STRADDLE"]
    assert by["LONG_STRANGLE"]["spans_earnings"] is True


def test_the_quality_cut(monkeypatch):
    rows = [_row("A", 49.9, 0.1), _row("B", 80, 0.1, grade="Weak"),
            _row("C", 50.0, 0.1)]
    kept, b = _select(rows, monkeypatch)
    assert [r["type"] for r in kept] == ["C"]
    assert b["score_cut"] == 2


def test_the_cap_is_per_family_and_keeps_the_best(monkeypatch):
    rows = [_row("V1", 75, 0.1, "VERTICAL"), _row("V2", 78, 0.1, "VERTICAL"),
            _row("V3", 60, 0.1, "VERTICAL"),
            _row("S1", 55, 0.1, "STRADDLE"), _row("S2", 52, 0.1, "STRADDLE")]
    kept, b = _select(rows, monkeypatch, max_per_family=1)
    # One per family: the long-volatility row survives the higher-scoring family.
    assert [r["type"] for r in kept] == ["V2", "S1"]
    assert b["capped"] == 3


def test_a_cap_of_zero_is_no_cap(monkeypatch):
    rows = [_row(f"V{i}", 60 + i, 0.1, "VERTICAL") for i in range(5)]
    kept, b = _select(rows, monkeypatch, max_per_family=0)
    assert len(kept) == 5 and b.get("capped", 0) == 0


def test_the_survivors_come_back_best_first(monkeypatch):
    rows = [_row("A", 55, 0.1, "X"), _row("B", 75, 0.1, "Y"), _row("C", 65, 0.1, "Z")]
    kept, _ = _select(rows, monkeypatch)
    assert [r["type"] for r in kept] == ["B", "C", "A"]


def test_the_bucket_partitions(monkeypatch):
    rows = [_row("SHORT_STRANGLE", 70, -0.2), _row("A", 40, 0.1),
            _row("B", 60, 0.1), _row("C", 61, 0.1), _row("D", 62, 0.1),
            _row("E", 63, -0.1)]
    kept, b = _select(rows, monkeypatch, iv_rank=10, max_per_family=2,
                      spans_earnings=lambda s: s["type"] == "B")
    assert b["built"] == 6
    assert b["built"] == (b.get("vol_gate", 0) + b.get("earnings", 0)
                          + b.get("score_cut", 0) + b.get("capped", 0) + len(kept))


def test_an_empty_window_still_reports_built_zero(monkeypatch):
    kept, b = _select([], monkeypatch)
    assert kept == [] and b["built"] == 0


def test_a_seeded_bucket_accumulates(monkeypatch):
    """run_full_scan seeds every counter at 0 and may call twice per bucket."""
    monkeypatch.setattr(sx._ssc, "score_all", lambda sigs, *a, **k: list(sigs))
    bucket = {"built": 3, "score_cut": 1}
    sx.select([_row("A", 40, 0.1), _row("B", 60, 0.1)], bucket=bucket, **_kw())
    assert bucket["built"] == 5 and bucket["score_cut"] == 2


def test_no_bucket_changes_nothing(monkeypatch):
    monkeypatch.setattr(sx._ssc, "score_all", lambda sigs, *a, **k: list(sigs))
    rows = [_row("B", 60, 0.1), _row("C", 61, -0.1), _row("D", 30, 0.1)]
    counted = sx.select([dict(r) for r in rows], bucket={}, **_kw(iv_rank=10))
    plain = sx.select([dict(r) for r in rows], **_kw(iv_rank=10))
    assert counted == plain


def test_the_daily_move_reaches_the_scorer(monkeypatch):
    """One candidate must score the same here as on the Strategy Finder, which
    passes ``daily_move`` so each row is judged on the move to ITS OWN expiry."""
    seen = {}

    def _spy(sigs, view, atm_iv, em_1sd, market_state=None, daily_move=None):
        seen.update(view=view, atm_iv=atm_iv, em_1sd=em_1sd, daily_move=daily_move)
        return list(sigs)

    monkeypatch.setattr(sx._ssc, "score_all", _spy)
    sx.select([_row("A", 60, 0.1)], **_kw(daily_em=2.0, dte_min=4))
    assert seen["daily_move"] == 2.0 and seen["em_1sd"] == 4.0   # 2 * sqrt(4)
    assert seen["view"] is NEUTRAL and seen["atm_iv"] == 0.28
    sx.select([_row("A", 60, 0.1)], **_kw(daily_em=2.0, dte_min=0))
    assert seen["em_1sd"] == 2.0                                 # floor of one day


def test_real_builders_and_real_scoring_produce_scored_rows():
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    cands = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                            short_band=BAND, back_chain=back, back_dte_max=45)
    kept = sx.select(cands, **_kw(daily_em=100 * 0.28 * (1 / 365) ** 0.5,
                                  min_score=0.0, excluded_grades=(),
                                  max_per_family=0))
    assert len(kept) == len(cands)
    assert all("composite_score" in r and "grade" in r for r in kept)
    scores = [r["composite_score"] for r in kept]
    assert scores == sorted(scores, reverse=True)


# ── ratio backspreads (the RATIO family) ────────────────────────────────────

def test_the_ratio_family_builds_backspreads_in_both_windows():
    for days, lo, hi in (((2,), 0, 4), ((9,), 5, 15)):
        rows = sx.build_window(bs_chain(days=days, step=0.5), "T", 100.0, 0.28, lo, hi,
                               families=("RATIO",), short_band=BAND,
                               max_debit_frac=1.0)
        assert _types(rows) == {"CALL_BACKSPREAD", "PUT_BACKSPREAD"}, days
        assert all(r["group"] == "RATIO" and r["dte"] == days[0] for r in rows)


def test_the_debit_cap_reaches_the_backspread_builder():
    chain = bs_chain(days=(9,), step=1.0)
    capped = sx.build_window(chain, "T", 100.0, 0.28, 5, 15, families=("RATIO",),
                             short_band=BAND, max_debit_frac=0.0)
    assert all(r["net_debit"] is None for r in capped)
    open_ = sx.build_window(chain, "T", 100.0, 0.28, 5, 15, families=("RATIO",),
                            short_band=BAND, max_debit_frac=5.0)
    assert len(open_) == 2 and len(open_) >= len(capped)


def test_no_cap_named_is_the_builders_own_default():
    chain = bs_chain(days=(9,), step=1.0)
    plain = sx.build_window(chain, "T", 100.0, 0.28, 5, 15, families=("RATIO",),
                            short_band=BAND)
    direct = sx._ssn.build_backspreads(chain, "T", 100.0, 0.28, 5, 15)
    assert [r["id"] for r in plain] == [r["id"] for r in direct] and plain


def test_a_backspread_is_gated_as_long_premium():
    """Long vega: the volatility FLOOR never touches it, and through an earnings
    report it is kept and flagged."""
    rows = sx.build_window(bs_chain(days=(9,), step=2.5), "T", 100.0, 0.28, 5, 15,
                           families=("RATIO",), short_band=BAND, max_debit_frac=1.0)
    kept = sx.select(rows, **_kw(iv_rank=1, floor=99, min_score=0.0,
                                 excluded_grades=(), max_per_family=0,
                                 spans_earnings=lambda s: True,
                                 earnings_date="2099-01-02"))
    assert len(kept) == len(rows) == 2
    assert all(r["spans_earnings"] is True for r in kept)
