"""The pure rules-based impact scorer (design §2, plan Task 2).

``CFG`` is built here rather than read from ``config/news.toml``: the scorer
takes its config as an argument, and these tests pin the RULES, not the
shipped values."""
import pytest

from services.news_svc import impact

CFG = {"high_at": 6, "med_at": 3, "stale_after_h": 24, "multi_source": 1, "watchlist": 2,
       "match_teaser": False,
       "keywords": {"tier1": {"points": 5, "words": ["FOMC", "rate cut", "CPI"]},
                    "tier2": {"points": 3, "words": ["beats", "downgrade"]}},
       "source_points": {"Federal Reserve": 3, "ZeroHedge": -1},
       "form4": {"small_usd": 250_000, "small": 1, "large_usd": 1_000_000, "large": 3,
                 "huge_usd": 10_000_000, "huge": 6, "officer": 1},
       "filings": {"424B5": 3, "S-3": 2, "S-1": 1, "S-3ASR": 1, "untracked": -1}}


def row(**kw):
    base = {"title": "", "teaser": "", "source": "CNBC", "sources": ["CNBC"],
            "tickers": [], "kind": "rss", "detail": {}}
    base.update(kw)
    return base


def test_word_boundaries():
    assert impact.score(row(title="CPIX rallies"), CFG, [])[0] == 0
    assert impact.score(row(title="Heartbeats of the market"), CFG, [])[0] == 0
    s, why = impact.score(row(title="Hot CPI print"), CFG, [])
    assert s == 5 and "kw:tier1:CPI" in why


def test_phrase_matches_across_case_and_spacing():
    assert impact.score(row(title="Fed signals RATE  CUT"), CFG, [])[0] == 5


def test_a_tier_counts_once_and_tiers_add():
    assert impact.score(row(title="FOMC FOMC CPI"), CFG, [])[0] == 5
    assert impact.score(row(title="FOMC: Apple beats"), CFG, [])[0] == 8


def test_the_reason_names_the_first_word_in_list_order():
    _, why = impact.score(row(title="CPI ahead of FOMC"), CFG, [])
    assert why == ["kw:tier1:FOMC"]


def test_teaser_ignored_unless_configured():
    assert impact.score(row(teaser="FOMC"), CFG, [])[0] == 0
    assert impact.score(row(teaser="FOMC"), {**CFG, "match_teaser": True}, [])[0] == 5


def test_source_points_take_the_max_not_the_sum_and_multi_source_adds_once():
    s, why = impact.score(row(sources=["Federal Reserve", "ZeroHedge", "CNBC"]), CFG, [])
    assert s == 3 + 1 and "source:Federal Reserve" in why and "sources:3" in why


def test_a_negative_source_alone_scores_negative():
    s, why = impact.score(row(source="ZeroHedge", sources=["ZeroHedge"]), CFG, [])
    assert s == -1 and why == ["source:ZeroHedge"]


def test_watchlist_boost_names_no_ticker():
    s, why = impact.score(row(tickers=["NVDA"]), CFG, ["NVDA"])
    assert s == 2 and "watchlist" in why and not any("NVDA" in r for r in why)


def test_a_ticker_outside_the_universe_takes_no_boost():
    assert impact.score(row(tickers=["NVDA"]), CFG, ["AAPL"])[0] == 0


@pytest.mark.parametrize("value,points", [(100_000, 0), (250_000, 1), (1_000_000, 3),
                                          (12e6, 6), (float("nan"), 0), (float("inf"), 0),
                                          (True, 0), (None, 0), (-5e6, 0)])
def test_form4_bands(value, points):
    r = row(kind="edgar_form4", tickers=["X"], detail={"total_value": value})
    assert impact.score(r, {**CFG, "watchlist": 0}, ["X"])[0] == points


def test_form4_reason_carries_the_money():
    _, why = impact.score(row(kind="edgar_form4", detail={"total_value": 1_200_000}),
                          CFG, [])
    assert "form4:$1.2M" in why


def test_form4_officer_bonus():
    r = row(kind="edgar_form4", detail={"total_value": 300_000, "relationship": "Director, CEO"})
    s, why = impact.score(r, {**CFG, "watchlist": 0}, [])
    assert s == 2 and "officer" in why


def test_a_ten_percent_owner_is_not_an_officer():
    r = row(kind="edgar_form4", detail={"total_value": 300_000, "relationship": "10% Owner"})
    assert impact.score(r, CFG, [])[0] == 1


def test_filing_points_are_exact_and_untracked_penalised():
    def f(form, t):
        return impact.score(row(kind="edgar_filings", tickers=t, detail={"form": form}),
                            {**CFG, "watchlist": 0}, t)[0]
    assert f("424B5", ["X"]) == 3 and f("S-3", ["X"]) == 2 and f("S-3/A", ["X"]) == 0
    assert f("424B5", []) == 2


def test_band_thresholds():
    assert [impact.band(s, CFG) for s in (6, 5, 3, 2)] == ["high", "med", "med", "low"]


def test_apply_returns_the_payload_shape():
    got = impact.apply(row(title="FOMC: Apple beats"), CFG, [])
    assert got == {"band": "high", "score": 8,
                   "reasons": ["kw:tier1:FOMC", "kw:tier2:beats"]}


def test_stale_high_caps_at_med_only_when_older_than_the_window():
    now = "2026-09-26T15:00:00+00:00"
    assert impact.cap_stale("high", "2026-09-25T14:00:00+00:00", now, CFG) == ("med", True)
    assert impact.cap_stale("high", "2026-09-26T14:00:00+00:00", now, CFG) == ("high", False)
    assert impact.cap_stale("high", "undated", now, CFG) == ("high", False)
    assert impact.cap_stale("low", "2026-01-01T00:00:00+00:00", now, CFG) == ("low", False)


def test_fingerprint_moves_with_config_and_universe_not_order():
    a = impact.fingerprint(CFG, ["A", "B"])
    assert a == impact.fingerprint(CFG, ["B", "A"])
    assert a != impact.fingerprint({**CFG, "high_at": 7}, ["A", "B"])
    assert a != impact.fingerprint(CFG, ["A"])


def test_a_changed_word_list_recompiles():
    alt = {**CFG, "keywords": {"tier1": {"points": 5, "words": ["merger"]}}}
    assert impact.score(row(title="FOMC"), alt, [])[0] == 0
    assert impact.score(row(title="Big merger"), alt, [])[0] == 5
    assert impact.score(row(title="FOMC"), CFG, [])[0] == 5


def test_junk_rows_score_zero_never_raise():
    for junk in (None, {}, {"title": 5, "sources": "CNBC", "detail": []}):
        assert impact.score(junk, CFG, [])[0] == 0


def test_a_junk_config_never_raises():
    for cfg in ({}, {"keywords": {"tier1": ["FOMC"]}, "source_points": [],
                     "form4": None, "filings": "x", "high_at": "six"}):
        assert impact.score(row(title="FOMC", kind="edgar_form4",
                                detail={"total_value": 5e6}), cfg, [])[0] == 0


# ── review findings (2026-09-26) ──────────────────────────────────────────────
def test_num_rejects_a_huge_int_without_raising():
    assert impact._num(10 ** 400) is None
    assert impact._num(-(10 ** 400)) is None
    assert impact._num(7) == 7


def test_a_huge_form4_value_or_threshold_never_raises():
    r = row(kind="edgar_form4", detail={"total_value": 10 ** 400, "relationship": "CEO"})
    assert impact.score(r, CFG, [])[0] == 0
    cfg = {**CFG, "form4": {**CFG["form4"], "huge_usd": 10 ** 400}}
    r = row(kind="edgar_form4", detail={"total_value": 2_000_000})
    assert impact.score(r, cfg, [])[0] == 3
    assert impact.band(10 ** 400, CFG) == "low"


def test_a_phrase_never_matches_across_the_title_teaser_join():
    cfg = {**CFG, "match_teaser": True}
    r = row(title="Traders trim rate", teaser="cut odds after the data")
    assert impact.score(r, cfg, [])[0] == 0
    # each field still matches on its own
    assert impact.score(row(title="Fed", teaser="a rate cut looms"), cfg, [])[0] == 5
    assert impact.score(row(title="Fed rate cut", teaser="more"), cfg, [])[0] == 5


def test_an_aware_non_utc_now_is_compared_as_the_same_instant():
    from datetime import datetime, timedelta, timezone
    ct = timezone(timedelta(hours=-5))
    now = datetime(2026, 9, 26, 10, 0, tzinfo=ct)          # = 15:00Z
    assert impact.cap_stale("high", "2026-09-25T14:00:00+00:00", now, CFG) == ("med", True)
    assert impact.cap_stale("high", "2026-09-25T16:00:00+00:00", now, CFG) == ("high", False)
    # a naive now is still read as UTC (documented)
    naive = datetime(2026, 9, 26, 15, 0)
    assert impact.cap_stale("high", "2026-09-25T14:00:00+00:00", naive, CFG) == ("med", True)


def test_a_non_positive_or_non_finite_stale_window_caps_nothing():
    old = "2020-01-01T00:00:00+00:00"
    now = "2026-09-26T15:00:00+00:00"
    for bad in (0, -1, -0.5, float("nan"), float("inf"), float("-inf"), True, "24", None):
        assert impact.cap_stale("high", old, now, {**CFG, "stale_after_h": bad}) == \
            ("high", False), bad


def test_the_officer_bonus_needs_a_scoring_form4_value():
    r = row(kind="edgar_form4", detail={"total_value": 1_000, "relationship": "Director"})
    s, why = impact.score(r, {**CFG, "watchlist": 0}, [])
    assert s == 0 and "officer" not in why
    r = row(kind="edgar_form4", detail={"total_value": 300_000, "relationship": "Director"})
    assert impact.score(r, {**CFG, "watchlist": 0}, [])[0] == 2


def test_a_bare_string_universe_is_one_ticker_not_its_letters():
    r = row(tickers=["S"])
    assert impact.score(r, CFG, "SPY")[0] == 0
    assert impact.score(row(tickers=["SPY"]), CFG, "SPY")[0] == 2
    assert impact.fingerprint(CFG, "SPY") == impact.fingerprint(CFG, ["SPY"])


def test_a_non_iterable_universe_is_empty_and_never_raises():
    for bad in (5, 1.5, True, object()):
        assert impact.score(row(tickers=["SPY"]), CFG, bad)[0] == 0
        assert impact.fingerprint(CFG, bad) == impact.fingerprint(CFG, [])


def test_fingerprint_never_raises_on_mixed_type_keys():
    cfg = {**CFG, "source_points": {"WSJ": 1, 2: 3, (1, 2): 4}}
    a = impact.fingerprint(cfg, ["A", 5, None])
    assert a == impact.fingerprint(cfg, ["A"])
    assert isinstance(a, str) and len(a) == 16
    # the ordinary path is unchanged, so stored rows are not all rescored
    assert impact.fingerprint(CFG, ["A"]) == impact.fingerprint(dict(CFG), ("A",))
