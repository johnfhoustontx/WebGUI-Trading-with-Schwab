"""The Symbol Dossier's pure fact-builders (``pages/symbol_facts.py``).

Every fixture is shaped like the REAL publisher's payload — the matrix row, the
scan funnel account, the dossier payload (``services/options_svc/dossier.py``
``DOSSIER_KEYS``), the four books, the day union, the flow alerts, the regime and
the bull/bear cascade. Where a Tier-1 builder already exists (``scanner.today_ct``,
``bullbear.quadrant``) the test uses it rather than restating it.

The one rule that recurs through every block: an ABSENT reading is ``None``,
never ``0``.
"""
import math

import pytest

from pages import bullbear
from pages import symbol_facts as sf
from pages.options import scanner

NAN = float("nan")
INF = float("inf")


# ── fixtures shaped like the publishers ─────────────────────────────────────

def _matrix_row(symbol="MU", **over):
    row = {"symbol": symbol, "spot": 184.2, "day_pct": 1.8, "flip": 180.0,
           "call_wall": 190.0, "put_wall": 175.0, "net_gex": 1.2e9,
           "atm_iv": 48.5, "iv_state": "elevated", "dealer_regime": "long",
           "gex_regime": "positive", "n_signals": 2, "n_alerts": 1}
    row.update(over)
    return row


def _degraded_row(symbol):
    """What the matrix publishes for a symbol it could not read."""
    return {"symbol": symbol, "spot": None, "day_pct": None, "flip": None,
            "call_wall": None, "put_wall": None, "net_gex": None,
            "atm_iv": None, "iv_state": "na", "dealer_regime": "na",
            "gex_regime": "na", "n_signals": 0, "n_alerts": 0}


def _matrix(*rows):
    return {"rows": list(rows)}


def _account(**over):
    acct = {"price": 184.1, "iv_rank": 62.0, "current_iv": 48.0,
            "hv_current": 40.0, "earnings_date": "2026-09-24", "stop": None,
            "buckets": {"0DTE": {}, "SWING": {}, "DIRECTIONAL": {}}}
    acct.update(over)
    return acct


def _funnel(**accounts):
    return {"timestamp": "2026-09-18T10:00:00", "symbols": accounts}


def _dossier(symbol="XYZ", **over):
    d = {"symbol": symbol, "error": None, "fetched_at": "2026-09-18T14:32:05",
         "spot": 50.0, "day_pct": None, "flip": 49.0, "put_wall": 45.0,
         "call_wall": 55.0, "iv_rank": 30.0, "current_iv": 35.0,
         "hv_current": 28.0, "earnings_status": "upcoming",
         "earnings_date": "2026-10-02"}
    d.update(over)
    return d


# ── symbol_coverage ─────────────────────────────────────────────────────────

def test_coverage_scanned_needs_a_matrix_row_and_a_funnel_account():
    assert sf.symbol_coverage("MU", _matrix(_matrix_row("MU")),
                              _funnel(MU=_account())) == "scanned"


def test_coverage_collected_is_a_matrix_row_without_a_funnel_account():
    m = _matrix(_matrix_row("$VIX"), _matrix_row("XLK"))
    assert sf.symbol_coverage("$VIX", m, _funnel(MU=_account())) == "collected"
    assert sf.symbol_coverage("XLK", m, _funnel()) == "collected"


def test_coverage_unknown_is_neither():
    assert sf.symbol_coverage("XYZQ", _matrix(_matrix_row("MU")),
                              _funnel(MU=_account())) == "unknown"


def test_coverage_a_funnel_account_alone_is_not_scanned():
    """The universes nest (scan watchlist ⊆ collection), so an account with no
    matrix row is a cold matrix, not a scanned symbol."""
    assert sf.symbol_coverage("MU", _matrix(), _funnel(MU=_account())) == "unknown"


def test_coverage_is_case_and_whitespace_insensitive():
    m, f = _matrix(_matrix_row("MU")), _funnel(MU=_account())
    assert sf.symbol_coverage("  mu ", m, f) == "scanned"
    assert sf.symbol_coverage("$vix", _matrix(_matrix_row("$VIX")), f) == "collected"


@pytest.mark.parametrize("matrix, funnel", [
    (None, None), ({}, {}), ({"rows": None}, {"symbols": None}),
    ("junk", 7), ({"rows": ["junk", None, 3]}, {"symbols": {"MU": "junk"}}),
])
def test_coverage_cold_or_malformed_caches_are_unknown_never_raise(matrix, funnel):
    assert sf.symbol_coverage("MU", matrix, funnel) == "unknown"


def test_coverage_a_non_dict_funnel_account_does_not_count():
    assert sf.symbol_coverage("MU", _matrix(_matrix_row("MU")),
                              {"symbols": {"MU": None}}) == "collected"


@pytest.mark.parametrize("bad", [None, "", "   ", "..", "/ES", "TOOLONGSYM"])
def test_coverage_of_an_invalid_ticker_is_unknown(bad):
    assert sf.symbol_coverage(bad, _matrix(_matrix_row("MU")),
                              _funnel(MU=_account())) == "unknown"


# ── needs_fetch ─────────────────────────────────────────────────────────────

def test_needs_fetch_only_when_the_cache_cannot_supply_every_fact():
    assert sf.needs_fetch("scanned") is False
    assert sf.needs_fetch("collected") is True
    assert sf.needs_fetch("unknown") is True


# ── iv_vs_hv ────────────────────────────────────────────────────────────────

def test_iv_vs_hv_ratio_and_mid_band():
    out = sf.iv_vs_hv(44.0, 40.0)
    assert out["ratio"] == pytest.approx(1.1)
    assert out["band"] == "mid"


def test_iv_vs_hv_boundaries_are_inclusive_like_the_scorer():
    assert sf.iv_vs_hv(48.0, 40.0)["band"] == "high"     # exactly 1.2
    assert sf.iv_vs_hv(36.0, 40.0)["band"] == "low"      # exactly 0.9
    assert sf.iv_vs_hv(48.4, 40.0)["band"] == "high"
    assert sf.iv_vs_hv(35.6, 40.0)["band"] == "low"
    assert sf.iv_vs_hv(47.6, 40.0)["band"] == "mid"
    assert sf.iv_vs_hv(36.4, 40.0)["band"] == "mid"


def test_iv_vs_hv_uses_the_scorers_words():
    assert {sf.iv_vs_hv(60, 40)["band"], sf.iv_vs_hv(20, 40)["band"],
            sf.iv_vs_hv(40, 40)["band"]} == {"high", "low", "mid"}


def test_iv_vs_hv_constants_are_the_scorers_numbers():
    assert sf.IV_HV_HIGH == 1.2
    assert sf.IV_HV_LOW == 0.9


@pytest.mark.parametrize("iv, hv", [
    (None, 40.0), (44.0, None), (None, None), (44.0, 0.0), (44.0, -5.0),
    (NAN, 40.0), (44.0, NAN), (INF, 40.0), (44.0, INF),
    (True, 40.0), (44.0, True), ("44", "junk"), (-999.0, 40.0),
])
def test_iv_vs_hv_absence_is_na_never_a_band(iv, hv):
    assert sf.iv_vs_hv(iv, hv) == {"ratio": None, "band": "na"}


# ── expected_move ───────────────────────────────────────────────────────────

def test_expected_move_reads_iv_as_a_percent():
    out = sf.expected_move(100.0, 20.0)
    assert out["day"] == pytest.approx(100 * 0.20 * math.sqrt(1 / 365))
    assert out["week"] == pytest.approx(100 * 0.20 * math.sqrt(7 / 365))


@pytest.mark.parametrize("spot, iv", [
    (None, 20.0), (100.0, None), (NAN, 20.0), (100.0, NAN), (INF, 20.0),
    (100.0, INF), (True, 20.0), (100.0, True), (0.0, 20.0), (100.0, 0.0),
    (-5.0, 20.0), (100.0, -999.0),
])
def test_expected_move_absence_is_none_never_zero(spot, iv):
    assert sf.expected_move(spot, iv) == {"day": None, "week": None}


# ── cached_facts ────────────────────────────────────────────────────────────

def test_cached_facts_flattens_the_matrix_row_and_funnel_account():
    f = sf.cached_facts("mu", _matrix(_matrix_row("MU")), _funnel(MU=_account()))
    assert f["spot"] == 184.2          # the matrix's one-minute spot wins
    assert f["day_pct"] == 1.8
    assert (f["flip"], f["put_wall"], f["call_wall"]) == (180.0, 175.0, 190.0)
    assert f["net_gex"] == 1.2e9 and f["atm_iv"] == 48.5
    assert (f["iv_state"], f["dealer_regime"], f["gex_regime"]) == (
        "elevated", "long", "positive")
    assert (f["iv_rank"], f["current_iv"], f["hv_current"]) == (62.0, 48.0, 40.0)
    assert f["earnings_date"] == "2026-09-24"


def test_cached_facts_carries_every_fact_key_even_when_cold():
    f = sf.cached_facts("MU", None, None)
    assert set(f) == set(sf.FACT_KEYS)
    assert all(v is None for v in f.values())


def test_cached_facts_missing_row_is_none_never_zero():
    f = sf.cached_facts("ZZZ", _matrix(_matrix_row("MU")), _funnel(MU=_account()))
    assert f["spot"] is None and f["net_gex"] is None and f["iv_rank"] is None


def test_cached_facts_keeps_the_documented_na_degrade_strings():
    f = sf.cached_facts("XLK", _matrix(_degraded_row("XLK")), _funnel())
    assert (f["iv_state"], f["dealer_regime"], f["gex_regime"]) == ("na", "na", "na")
    assert f["spot"] is None and f["flip"] is None and f["atm_iv"] is None


def test_cached_facts_hardens_nan_and_bool_to_none():
    row = _matrix_row("MU", spot=NAN, flip=True, net_gex=INF, atm_iv="junk")
    acct = _account(iv_rank=NAN, current_iv=True, hv_current=-INF)
    f = sf.cached_facts("MU", _matrix(row), _funnel(MU=acct))
    for k in ("flip", "net_gex", "atm_iv", "iv_rank", "current_iv", "hv_current"):
        assert f[k] is None, k


def test_cached_facts_spot_falls_back_to_the_funnel_quote():
    row = _matrix_row("MU", spot=None)
    f = sf.cached_facts("MU", _matrix(row), _funnel(MU=_account(price=184.1)))
    assert f["spot"] == 184.1


def test_cached_facts_funnel_has_no_earnings_status():
    """The funnel carries the date the gate read, never the three-valued
    status — so the cache must not invent one."""
    f = sf.cached_facts("MU", _matrix(_matrix_row("MU")), _funnel(MU=_account()))
    assert f["earnings_status"] is None


# ── merge_facts: cache wins ─────────────────────────────────────────────────

def test_merge_scanned_symbol_keeps_every_cached_value():
    cached = sf.cached_facts("MU", _matrix(_matrix_row("MU")),
                             _funnel(MU=_account()))
    fetched = _dossier("MU", spot=999.0, flip=1.0, put_wall=2.0, call_wall=3.0,
                       iv_rank=4.0, current_iv=5.0, hv_current=6.0,
                       earnings_date="2099-01-01")
    out = sf.merge_facts(cached, fetched)
    for k, v in cached.items():
        if v is not None:
            assert out["facts"][k] == v, k
            assert out["source"][k] == "cache", k


def test_merge_collected_symbol_takes_vol_and_earnings_from_the_fetch():
    cached = sf.cached_facts("$VIX", _matrix(_matrix_row("$VIX")), _funnel())
    out = sf.merge_facts(cached, _dossier("$VIX"))
    f, s = out["facts"], out["source"]
    # structure: the cache's
    assert (f["spot"], f["flip"], f["put_wall"], f["call_wall"]) == (
        184.2, 180.0, 175.0, 190.0)
    assert {s["spot"], s["flip"], s["put_wall"], s["call_wall"]} == {"cache"}
    # vol + earnings: the fetch's
    assert (f["iv_rank"], f["current_iv"], f["hv_current"]) == (30.0, 35.0, 28.0)
    assert (f["earnings_status"], f["earnings_date"]) == ("upcoming", "2026-10-02")
    assert {s["iv_rank"], s["current_iv"], s["hv_current"],
            s["earnings_status"], s["earnings_date"]} == {"fetch"}


def test_merge_a_cached_none_is_filled_by_the_fetch():
    cached = sf.cached_facts("XLK", _matrix(_degraded_row("XLK")), _funnel())
    out = sf.merge_facts(cached, _dossier("XLK"))
    assert out["facts"]["spot"] == 50.0 and out["source"]["spot"] == "fetch"
    assert out["facts"]["flip"] == 49.0 and out["source"]["flip"] == "fetch"


def test_merge_a_fetched_value_never_overrides_a_cached_real_reading():
    out = sf.merge_facts({"spot": 10.0, "iv_rank": 0.0},
                         _dossier(spot=50.0, iv_rank=30.0))
    assert out["facts"]["spot"] == 10.0
    # 0.0 is a real reading — it must not be treated as absent
    assert out["facts"]["iv_rank"] == 0.0 and out["source"]["iv_rank"] == "cache"


def test_merge_source_is_none_where_neither_side_has_a_reading():
    out = sf.merge_facts(sf.cached_facts("XYZ", None, None),
                         _dossier(day_pct=None, flip=None))
    assert out["facts"]["day_pct"] is None and out["source"]["day_pct"] is None
    assert out["facts"]["net_gex"] is None and out["source"]["net_gex"] is None


def test_merge_without_a_fetch_is_the_cache_alone():
    cached = sf.cached_facts("MU", _matrix(_matrix_row("MU")),
                             _funnel(MU=_account()))
    out = sf.merge_facts(cached, None)
    assert out["error"] is None and out["fetched_at"] is None
    assert out["facts"]["spot"] == 184.2
    assert out["facts"]["earnings_status"] is None
    assert out["source"]["earnings_status"] is None


def test_merge_carries_the_fetch_error_and_stamp():
    fetched = dict.fromkeys(("spot", "flip", "iv_rank"))
    fetched.update(symbol="XYZQ", error="no_quote",
                   fetched_at="2026-09-18T14:32:05")
    out = sf.merge_facts(sf.cached_facts("XYZQ", None, None), fetched)
    assert out["error"] == "no_quote"
    assert out["fetched_at"] == "2026-09-18T14:32:05"
    assert all(v is None for v in out["facts"].values())


def test_merge_hardens_a_nan_on_either_side():
    """A NaN in the cache is no reading, so the fetch fills it; a NaN in the
    fetch is no reading either, so it never becomes a fact."""
    out = sf.merge_facts({"spot": NAN, "flip": None},
                         _dossier(spot=50.0, flip=NAN))
    assert out["facts"]["spot"] == 50.0 and out["source"]["spot"] == "fetch"
    assert out["facts"]["flip"] is None and out["source"]["flip"] is None


def test_merge_tolerates_malformed_inputs():
    out = sf.merge_facts(None, "junk")
    assert set(out["facts"]) == set(sf.FACT_KEYS)
    assert out["error"] is None and out["fetched_at"] is None


# ── position_rows ───────────────────────────────────────────────────────────

def _books():
    return {
        "options:paper_account": {"positions": [
            {"position_id": 1, "symbol": "MU", "strategy": "PCS", "status": "OPEN"},
            {"position_id": 2, "symbol": "MU", "strategy": "PCS", "status": "CLOSED"},
            {"position_id": 3, "symbol": "ORCL", "strategy": "CCS", "status": "OPEN"},
        ]},
        "options:paper_trades": {"trades": [
            {"trade_id": 10, "symbol": "mu", "strategy": "LONG_CALL", "status": "OPEN"},
            {"trade_id": 11, "symbol": "MU", "strategy": "PCS", "status": "EXPIRED"},
        ]},
        "options:driver_paper_account": {"positions": [
            {"position_id": 20, "symbol": "MU", "strategy": "IC"},   # no status → open
        ]},
        "options:captured": {"signals": [
            {"signal_id": 30, "symbol": "MU", "strategy": "PCS", "status": "open"},
            {"signal_id": 31, "symbol": "MU", "strategy": "PCS", "status": "closed"},
        ]},
    }


def test_position_rows_open_rows_from_all_four_books_tagged():
    rows = sf.position_rows(" mu ", _books())
    assert [(r["book"], r.get("position_id") or r.get("trade_id")
             or r.get("signal_id")) for r in rows] == [
        ("account", 1), ("ledger", 10), ("driver", 20), ("captured", 30)]


def test_position_rows_drops_closed_and_expired():
    rows = sf.position_rows("MU", _books())
    assert not any(str(r.get("status", "")).upper() in ("CLOSED", "EXPIRED")
                   for r in rows)


def test_position_rows_does_not_mutate_the_payload():
    books = _books()
    sf.position_rows("MU", books)
    assert "book" not in books["options:paper_account"]["positions"][0]


@pytest.mark.parametrize("books", [None, {}, "junk", {
    "options:paper_account": None, "options:paper_trades": {"trades": None},
    "options:driver_paper_account": "junk",
    "options:captured": {"signals": ["junk", None]}}])
def test_position_rows_missing_books_are_empty(books):
    assert sf.position_rows("MU", books) == []


def test_position_rows_other_symbol_or_invalid_ticker_is_empty():
    assert sf.position_rows("NVDA", _books()) == []
    assert sf.position_rows(None, _books()) == []


# ── signals_for ─────────────────────────────────────────────────────────────

def _day_env(date=None):
    return {"date": date or scanner.today_ct(),
            "signals_0dte": [{"symbol": "MU", "type": "PCS", "live": True},
                             {"symbol": "ORCL", "type": "CCS", "live": True}],
            "signals_swing": [{"symbol": "mu", "type": "IC", "live": False}],
            "signals_directional": [{"symbol": "MU", "type": "LONG_CALL",
                                     "live": True}]}


def test_signals_for_filters_all_three_lists_on_symbol():
    rows = sf.signals_for("MU", _day_env())
    assert [(r["list"], r["type"]) for r in rows] == [
        ("signals_0dte", "PCS"), ("signals_swing", "IC"),
        ("signals_directional", "LONG_CALL")]


def test_signals_for_a_wrong_date_envelope_yields_nothing():
    """A failed merge leaves YESTERDAY's envelope — live=True rows included."""
    assert sf.signals_for("MU", _day_env(date="2000-01-01")) == []


@pytest.mark.parametrize("env", [None, {}, "junk", {"date": None}])
def test_signals_for_cold_envelope_is_empty(env):
    assert sf.signals_for("MU", env) == []


def test_signals_for_does_not_mutate_the_payload():
    env = _day_env()
    sf.signals_for("MU", env)
    assert "list" not in env["signals_0dte"][0]


# ── alerts_for ──────────────────────────────────────────────────────────────

def _flow_env():
    # the service appends oldest-first
    return {"alerts": [
        {"id": "a1", "ts": 1000, "symbol": "MU", "type": "crossover"},
        {"id": "a2", "ts": 2000, "symbol": "ORCL", "type": "uoa"},
        {"id": "a3", "ts": 3000, "symbol": "mu", "type": "big_delta"},
    ]}


def test_alerts_for_filters_on_symbol_newest_first():
    assert [r["id"] for r in sf.alerts_for(" Mu", _flow_env())] == ["a3", "a1"]


@pytest.mark.parametrize("env", [None, {}, {"alerts": None}, "junk"])
def test_alerts_for_cold_view_is_empty(env):
    assert sf.alerts_for("MU", env) == []


# ── context_facts ───────────────────────────────────────────────────────────

def _regime_env():
    return {"label": "Rallying", "committed_label": "trending",
            "confidence": 0.8, "direction": 1, "direction_strong": True,
            "unclear": False}


def _bullbear_env():
    return {"levels": {"sector": [], "industry": [], "stock": [
        {"symbol": "MU", "sector": "Information Technology",
         "industry": "Semiconductors", "score": 71.0,
         "components": {"trend": 0.6, "rs": 0.4},
         "raw": {"trend": 0.12, "excess": -0.03}, "percentile": 80,
         "rank": 5, "rank_prev": 7, "day_pct": 1.8, "day_excess": 0.9},
        {"symbol": "THIN", "raw": {"trend": None, "excess": 0.1}},
    ]}}


def test_context_facts_regime_word_and_direction():
    ctx = sf.context_facts("MU", _regime_env(), _bullbear_env())
    assert ctx["regime"]["word"] == "Rallying"
    assert ctx["regime"]["direction"] == 1


def test_context_facts_bullbear_row_and_quadrant_via_the_real_rule():
    env = _bullbear_env()
    ctx = sf.context_facts("mu", _regime_env(), env)
    row = env["levels"]["stock"][0]
    assert ctx["bullbear"] is row
    assert ctx["quadrant"] == bullbear.quadrant(*bullbear.row_axes(row))
    assert ctx["quadrant"] == "rising_lagging"
    assert ctx["quadrant_label"] == "Rising · Lagging"


def test_context_facts_absent_symbol_has_no_quadrant():
    ctx = sf.context_facts("NVDA", _regime_env(), _bullbear_env())
    assert ctx["bullbear"] is None
    assert ctx["quadrant"] is None and ctx["quadrant_label"] is None


def test_context_facts_a_present_row_with_no_axis_reads_unknown():
    ctx = sf.context_facts("THIN", _regime_env(), _bullbear_env())
    assert ctx["quadrant"] == "unknown"


@pytest.mark.parametrize("regime, bb", [(None, None), ({}, {}), ("junk", "junk"),
                                        ({}, {"levels": {"stock": None}})])
def test_context_facts_cold_views(regime, bb):
    ctx = sf.context_facts("MU", regime, bb)
    assert ctx["regime"] is None
    assert ctx["bullbear"] is None and ctx["quadrant"] is None
