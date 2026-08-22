"""Tests for the trade service compute orchestration (Task #25).

We monkeypatch ``compute._proxy.schwab_client`` with a fake that serves
synthetic OHLCV candles so nothing touches a live proxy. The synthetic series
is a gentle uptrend with enough bars (300 daily / 200 intraday) to satisfy every
indicator's lookback, so ``analyze`` exercises the full orchestration.
"""
import datetime as _dt

import pandas as pd
import pytest

from services.trade_svc import compute


def _candles(n, start_ms=1_600_000_000_000, step_ms=86_400_000,
             base=100.0, drift=0.001):
    """n synthetic candles: a smooth uptrend, strictly positive OHLCV."""
    out = []
    prev = base
    for i in range(n):
        close = base * (1 + drift * i)
        out.append({
            "datetime": start_ms + i * step_ms,
            "open": prev,
            "high": close * 1.01,
            "low": min(prev, close) * 0.99,
            "close": close,
            "volume": 1_000_000 + (i % 7) * 10_000,
        })
        prev = close
    return out


_UNSET = object()


class FakeClient:
    """Stand-in for ``_proxy.schwab_client`` returning synthetic data.

    The analyzed symbol gets a strong uptrend; SPY and the sector ETFs (symbols
    starting ``XL``) get a mild uptrend, so the analyzed symbol outperforms on
    relative strength (drives the RS-vs-SPY/sector factors positive).
    """

    def __init__(self, quote_last=120.0, quote=_UNSET, fundamentals=None):
        self.quote_last = quote_last
        self._quote = ({"last": quote_last, "symbol": "TEST", "volume": 5_000_000}
                       if quote is _UNSET else quote)
        self._fundamentals = fundamentals

    def get_quote(self, symbol):
        if self._quote is None:
            return None
        q = dict(self._quote)
        q.setdefault("symbol", symbol)
        return q

    def get_fundamentals(self, symbol):
        return self._fundamentals

    def _request(self, endpoint, params):
        sym = params.get("symbol", "")
        drift = 0.0003 if (sym == "SPY" or sym.startswith("XL")) else 0.004
        if params.get("frequencyType") == "daily":
            return {"candles": _candles(300, step_ms=86_400_000,
                                        base=self.quote_last, drift=drift)}
        # intraday 5-min bars spread across a few days
        return {"candles": _candles(200, step_ms=300_000,
                                    base=self.quote_last, drift=drift)}


def _patch(monkeypatch, client):
    monkeypatch.setattr(compute._proxy, "schwab_client", client)


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_rs_percentile_neutral_when_ref_missing():
    s = pd.Series(range(100))
    assert compute.rs_percentile(s, None, 63) == 0.5
    assert compute.rs_percentile(s, pd.Series(range(10)), 63) == 0.5  # ref too short


def test_rs_percentile_outperformance_above_half():
    strong = pd.Series([100 * (1.005 ** i) for i in range(70)])
    flat = pd.Series([100.0] * 70)
    assert compute.rs_percentile(strong, flat, 63) > 0.5


def test_resolve_sector_known_and_unknown():
    assert compute.resolve_sector("AAPL") == {"name": "Technology", "etf": "XLK"}
    assert compute.resolve_sector("nvda") == {"name": "Technology", "etf": "XLK"}
    assert compute.resolve_sector("ZZZZ") == {"name": "", "etf": ""}


# ── orchestration ────────────────────────────────────────────────────────────

def test_analyze_returns_full_structure(monkeypatch):
    _patch(monkeypatch, FakeClient(quote_last=120.0))
    res = compute.analyze("AAPL")

    assert res is not None
    assert res["symbol"] == "AAPL"
    assert res["price"] == 120.0
    assert res["errors"] == []
    assert res["fundamentals_available"] is False

    # Both verdicts present and well-formed.
    for key in ("position_verdict", "investor_verdict"):
        v = res[key]
        assert v["verdict"] in ("BUY", "HOLD", "SELL")
        assert isinstance(v["breakdown"], list)
        assert isinstance(v["gates_triggered"], list)

    # No fundamentals wired -> investor degrades to insufficient-data HOLD.
    assert res["investor_verdict"]["verdict"] == "HOLD"
    assert "Insufficient fundamental data" in res["investor_verdict"]["top_reasons"]

    # Momentum + alignment + sector populated.
    assert set(res["momentum"]) >= {"rsi", "adx", "macd_hist", "relative_volume"}
    assert "alignment_percentage" in res["ema_alignment"]
    assert res["sector"]["etf"] == "XLK"
    assert "score" in res["sector"]["strength"]


def test_analyze_result_is_contract_valid(monkeypatch):
    """The compute dict must project cleanly onto the TradeAnalysis contract."""
    from shared.contracts.trade import TradeAnalysis

    _patch(monkeypatch, FakeClient())
    res = compute.analyze("MSFT")
    ta = TradeAnalysis(**{k: res.get(k) for k in TradeAnalysis.model_fields
                          if k in res})
    assert TradeAnalysis.from_json(ta.to_json()).symbol == "MSFT"


def test_analyze_without_fundamentals_degrades_to_hold(monkeypatch):
    """No fundamentals feed -> InvestorVerdict insufficient-data HOLD, flag False."""
    _patch(monkeypatch, FakeClient(quote_last=120.0))  # get_fundamentals -> None
    res = compute.analyze("AAPL")
    assert res["fundamentals_available"] is False
    assert res["investor_verdict"]["verdict"] == "HOLD"
    assert "Insufficient fundamental data" in res["investor_verdict"]["top_reasons"]
    # Display view present but empty of real values.
    assert res["fundamentals"]["pe_ratio"] is None


def test_analyze_with_fundamentals_runs_investor(monkeypatch):
    """Sufficient Schwab fundamentals -> InvestorVerdict runs on real data."""
    strong = {
        "peRatio": 28.0, "pegRatio": 0.8, "revChangeTTM": 20.0,
        "epsChangePercentTTM": 25.0, "returnOnEquity": 30.0,
        "operatingMarginTTM": 25.0, "operatingMarginMRQ": 28.0,
    }
    _patch(monkeypatch, FakeClient(quote_last=120.0, fundamentals=strong))
    res = compute.analyze("AAPL")

    assert res["fundamentals_available"] is True
    iv = res["investor_verdict"]
    # Real computation, not the insufficient-data shortcut.
    assert iv["breakdown"]
    assert "Insufficient fundamental data" not in iv["top_reasons"]
    assert iv["gates_triggered"] != ["No fundamentals"]
    assert iv["verdict"] == "BUY"  # strong fundamentals + RS outperformance

    # Display dict carries the parsed (percent->fraction) values.
    fd = res["fundamentals"]
    assert fd["pe_ratio"] == 28.0
    assert abs(fd["rev_growth_ttm"] - 0.20) < 1e-9
    assert abs(fd["eps_growth_ttm"] - 0.25) < 1e-9
    assert abs(fd["roe"] - 0.30) < 1e-9
    assert fd["margin_expanding"] is True


def test_analyze_fundamentals_fetch_failure_degrades(monkeypatch):
    """A get_fundamentals exception must degrade to insufficient, not raise."""
    class BoomFund(FakeClient):
        def get_fundamentals(self, symbol):
            raise RuntimeError("instruments endpoint down")

    _patch(monkeypatch, BoomFund(quote_last=120.0))
    res = compute.analyze("AAPL")
    assert res["errors"] == []  # analysis still succeeds
    assert res["fundamentals_available"] is False
    assert res["investor_verdict"]["verdict"] == "HOLD"


def test_analyze_empty_symbol_returns_none(monkeypatch):
    _patch(monkeypatch, FakeClient())
    assert compute.analyze("") is None
    assert compute.analyze("   ") is None


def test_analyze_no_quote_degrades(monkeypatch):
    _patch(monkeypatch, FakeClient(quote=None))
    res = compute.analyze("AAPL")
    assert res["symbol"] == "AAPL"
    assert res["errors"] and "quote" in res["errors"][0].lower()
    assert "position_verdict" not in res or res.get("position_verdict") in (None, {})


def test_analyze_thin_history_degrades(monkeypatch):
    class ThinClient(FakeClient):
        def _request(self, endpoint, params):
            return {"candles": _candles(10)}  # < 50 daily bars

    _patch(monkeypatch, ThinClient())
    res = compute.analyze("AAPL")
    assert res["errors"] and "history" in res["errors"][0].lower()


def test_analyze_never_raises_on_client_explosion(monkeypatch):
    class BoomClient:
        def get_quote(self, symbol):
            raise RuntimeError("proxy down")

        def _request(self, endpoint, params):
            raise RuntimeError("proxy down")

    _patch(monkeypatch, BoomClient())
    # get_quote raising is caught -> treated as no quote -> graceful error dict.
    res = compute.analyze("AAPL")
    assert res["symbol"] == "AAPL"
    assert res["errors"]


# ── sector P/E median (Phase 1) ──────────────────────────────────────────────

class _PeerPEClient(FakeClient):
    """Serves a P/E per symbol and counts how many fundamental fetches happen."""

    def __init__(self, pes):
        super().__init__()
        self._pes = pes
        self.fetches = 0

    def get_fundamentals(self, symbol):
        self.fetches += 1
        pe = self._pes.get(symbol)
        return {"peRatio": pe} if pe is not None else None


def test_sector_pe_median_is_the_median_of_that_sectors_peers(monkeypatch):
    """The Investor `valuation` component compares a symbol's P/E to its
    SECTOR's median, but live ``analyze`` passed ``sector_pe_median=None``
    unconditionally — so that half never scored, and averaging its structural 0
    halved the surviving PEG score for every symbol ever analyzed.

    The peers are the ones the static sector map already names."""
    client = _PeerPEClient({"AAPL": 35.0, "MSFT": 30.0, "NVDA": 50.0, "AVGO": 40.0})
    _patch(monkeypatch, client)
    compute.reset_sector_pe_cache()
    assert compute.sector_pe_median("AAPL") == 37.5      # median of 30/35/40/50


def test_sector_pe_median_ignores_non_positive_and_missing(monkeypatch):
    """A loss-making peer reports a negative P/E, which is not a valuation the
    median should be dragged by; a peer with no fundamentals contributes
    nothing rather than a zero."""
    client = _PeerPEClient({"AAPL": 20.0, "MSFT": 30.0, "NVDA": -12.0, "AVGO": None})
    _patch(monkeypatch, client)
    compute.reset_sector_pe_cache()
    assert compute.sector_pe_median("AAPL") == 25.0      # median of 20/30 only


def test_sector_pe_median_is_memoized_per_sector(monkeypatch):
    """One fan-out per sector per day — not one per analyze. Without the memo
    every analysis would re-fetch a dozen peers to compute a number that moves
    once a quarter."""
    client = _PeerPEClient({"AAPL": 35.0, "MSFT": 30.0})
    _patch(monkeypatch, client)
    compute.reset_sector_pe_cache()
    compute.sector_pe_median("AAPL")
    first = client.fetches
    assert first > 1                                     # it really did fan out
    compute.sector_pe_median("MSFT")                     # same sector
    assert client.fetches == first                       # served from the memo


def test_sector_pe_median_none_for_an_unmapped_symbol(monkeypatch):
    """An unknown symbol has no sector, so there is no peer set — return None
    (the Investor verdict then scores valuation on PEG alone) rather than a
    median of everything."""
    _patch(monkeypatch, _PeerPEClient({"ZZZZ": 11.0}))
    compute.reset_sector_pe_cache()
    assert compute.sector_pe_median("ZZZZ") is None


# ── recommendation-journal wiring (Phase 1) ──────────────────────────────────

def _analysis_result():
    return {
        "symbol": "AAPL", "price": 309.69,
        "swing_model": {"verdict": "HOLD", "score": 0.096, "percentile": 70,
                        "model_version": "2026-08-22"},
        "position_verdict": {"verdict": "HOLD", "gates_triggered": ["ADX<15: no trend, capped at HOLD"]},
        "investor_verdict": {"verdict": "HOLD", "score": 17},
    }


def test_journal_reading_maps_an_analysis_onto_a_row(tmp_path):
    """The journal row is what Phase 6's IC monitor will read back, so the
    mapping from an ``analyze`` result is worth pinning now."""
    from services.trade_svc import rec_journal

    assert compute.journal_reading(_analysis_result(),
                                   db_path=tmp_path / "j.db") is True
    conn = rec_journal.init_db(tmp_path / "j.db")
    try:
        row = rec_journal.readings(conn)[0]
        assert row["symbol"] == "AAPL"
        assert row["percentile"] == 70
        assert row["composite"] == pytest.approx(0.096)
        assert row["swing_verdict"] == "HOLD"
        assert row["investor_score"] == 17
        assert row["model_version"] == "2026-08-22"
        assert "ADX<15" in row["gates"]
    finally:
        rec_journal.close_db(conn)


def test_journal_reading_records_a_degraded_analysis_too(tmp_path):
    """An analysis with no swing block is exactly the reading worth keeping —
    it records that the model could not speak, which a gap in the series
    could not distinguish from 'nobody looked'."""
    from services.trade_svc import rec_journal

    res = {"symbol": "ZZZZ", "price": 4.2, "errors": ["No quote / price for symbol"]}
    assert compute.journal_reading(res, db_path=tmp_path / "j.db") is True
    conn = rec_journal.init_db(tmp_path / "j.db")
    try:
        row = rec_journal.readings(conn)[0]
        assert row["symbol"] == "ZZZZ" and row["composite"] is None
    finally:
        rec_journal.close_db(conn)


def test_journal_reading_writes_nothing_to_the_real_store_under_pytest():
    """This repo has a documented incident where a suite wrote into live data:
    the bus is fakeredis, SQLite is not. With no explicit path the write is
    SKIPPED under pytest rather than landing in services/trade_svc/data/."""
    assert compute.journal_reading(_analysis_result()) is False


def test_journal_reading_never_raises(tmp_path):
    """``analyze`` owes the user an analysis whether or not the journal took
    the row — an unwritable path must be swallowed, not propagated."""
    assert compute.journal_reading(_analysis_result(),
                                   db_path=tmp_path / "no" / "such" / "dir" / "\0bad.db") is False


# ── short-interest enrichment (Phase 1, task 1.2) ────────────────────────────

def test_fundamentals_get_short_interest_from_finra_not_schwab(monkeypatch, tmp_path):
    """Schwab ships both short-interest fields as a 0.0 sentinel for EVERY
    symbol, so `parse_schwab_fundamentals` maps them to None. FINRA supplies
    the real numerator and a pre-computed days-to-cover; Schwab still supplies
    the float denominator via `marketCapFloat` (which is float in SHARES).

    This is the join the whole short side rests on."""
    from services.trade_svc import short_interest as si

    db = tmp_path / "si.db"
    conn = si.init_db(db)
    si.store_cycle(conn, [{"symbol": "GME", "short_qty": 53736062,
                           "days_to_cover": 17.06, "avg_daily_volume": 3150012,
                           "settlement_date": "2026-07-31"}])
    si.close_db(conn)

    class FinraClient(FakeClient):
        def get_fundamentals(self, symbol):
            # Schwab's real shape: the fields exist and are always zero.
            return {"peRatio": 13.5, "marketCapFloat": 408810860.0,
                    "shortIntToFloat": 0.0, "shortIntDayToCover": 0.0}

    _patch(monkeypatch, FinraClient())
    monkeypatch.setattr(compute, "_short_interest_db_path", lambda: db)
    monkeypatch.setattr(compute, "_refresh_short_interest", lambda conn: None)

    f = compute._fetch_fundamentals("GME")
    assert f.short_int_to_float == pytest.approx(13.14, abs=0.01)
    assert f.short_int_day_to_cover == pytest.approx(17.06)


def test_short_interest_enrichment_degrades_to_none_not_to_schwabs_zero(monkeypatch, tmp_path):
    """A symbol FINRA does not carry (a rename, most often) must leave the
    fields None. Falling back to Schwab's 0.0 would silently reinstate the
    sentinel this whole module exists to escape."""
    from services.trade_svc import short_interest as si

    db = tmp_path / "si.db"
    si.close_db(si.init_db(db))          # empty store

    class Client(FakeClient):
        def get_fundamentals(self, symbol):
            return {"peRatio": 20.0, "marketCapFloat": 1_000_000.0,
                    "shortIntToFloat": 0.0, "shortIntDayToCover": 0.0}

    _patch(monkeypatch, Client())
    monkeypatch.setattr(compute, "_short_interest_db_path", lambda: db)
    monkeypatch.setattr(compute, "_refresh_short_interest", lambda conn: None)

    f = compute._fetch_fundamentals("SQ")
    assert f.short_int_to_float is None
    assert f.short_int_day_to_cover is None


def test_short_interest_enrichment_never_breaks_an_analysis(monkeypatch):
    """The store being unreachable must cost the short-interest fields and
    nothing else — the fundamentals themselves still come back."""
    class Client(FakeClient):
        def get_fundamentals(self, symbol):
            return {"peRatio": 20.0, "marketCapFloat": 1_000_000.0}

    _patch(monkeypatch, Client())
    monkeypatch.setattr(compute, "_short_interest_db_path",
                        lambda: (_ for _ in ()).throw(RuntimeError("no store")))

    f = compute._fetch_fundamentals("AAPL")
    assert f.pe_ratio == 20.0
    assert f.short_int_to_float is None


def test_short_interest_enrichment_is_skipped_under_pytest_by_default(monkeypatch):
    """With no explicit store path the enrichment must not run under pytest.

    Unguarded it opens a SQLite file inside the repo AND triggers a LIVE FINRA
    fetch: measured, a single suite run downloaded 22,341 rows into
    services/trade_svc/data/short_interest.db. That is the documented
    "pytest must isolate on-disk stores" trap plus an unwanted network call,
    and `analyze` is exercised by many tests that know nothing about this
    store. Tests that DO want the join pass their own path, which opts back in.
    """
    from services.trade_svc import short_interest as si

    opened = []

    def spy(path):
        opened.append(path)
        raise RuntimeError("must not be reached under pytest")

    monkeypatch.setattr(si, "init_db", spy)

    class Client(FakeClient):
        def get_fundamentals(self, symbol):
            return {"peRatio": 20.0, "marketCapFloat": 1_000_000.0}

    _patch(monkeypatch, Client())
    f = compute._fetch_fundamentals("AAPL")

    assert opened == [], f"enrichment opened the real store: {opened}"
    assert f.pe_ratio == 20.0
    assert f.short_int_to_float is None


def test_analyze_feeds_the_squeeze_reason_into_the_position_gate(monkeypatch):
    """The engine cannot reach FINRA, so `analyze` computes the squeeze reason
    and hands it down. Without this wiring the gate exists but never fires."""
    from src.analysis.fundamentals import Fundamentals

    _patch(monkeypatch, FakeClient())
    monkeypatch.setattr(
        compute, "_fetch_fundamentals",
        lambda sym: Fundamentals(pe_ratio=13.5, rev_growth_ttm=0.1,
                                 eps_growth_ttm=0.1, roe=0.2,
                                 short_int_to_float=31.0,
                                 short_int_day_to_cover=17.1))
    res = compute.analyze("GME")
    short_gates = res["position_verdict"]["short_gates"]
    assert any("squeeze" in g.lower() for g in short_gates)
    assert any("31.0% of float short" in g for g in short_gates)


def test_analyze_leaves_the_squeeze_gate_quiet_without_short_interest(monkeypatch):
    from src.analysis.fundamentals import Fundamentals

    _patch(monkeypatch, FakeClient())
    monkeypatch.setattr(
        compute, "_fetch_fundamentals",
        lambda sym: Fundamentals(pe_ratio=13.5, rev_growth_ttm=0.1,
                                 eps_growth_ttm=0.1, roe=0.2))
    res = compute.analyze("AAPL")
    assert not any("squeeze" in g.lower()
                   for g in res["position_verdict"]["short_gates"])


# ── direction clearance wiring (Phase 2, task 2.2) ───────────────────────────

def test_analyze_carries_direction_clearance_for_both_sides(monkeypatch):
    """The clearance block is what tells the page whether a bottom-band read is
    a directional short or a relative one — it must reach the payload."""
    _patch(monkeypatch, FakeClient())
    monkeypatch.setattr(compute, "_read_regime", lambda: {
        "committed_label": "trending", "label": "Softening", "direction": -1,
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat()})
    res = compute.analyze("AAPL")
    dc = res["direction_clearance"]
    assert set(dc) == {"market", "long", "short"}
    assert dc["short"]["state"] in {"cleared", "relative_only", "blocked"}
    assert dc["short"]["reasons"]


def test_analyze_survives_a_regime_the_bus_cannot_supply(monkeypatch):
    """A sentiment outage must cost the clearance nuance, not the analysis —
    and the short side must fall to relative_only, never to cleared."""
    _patch(monkeypatch, FakeClient())
    monkeypatch.setattr(compute, "_read_regime", lambda: None)
    res = compute.analyze("AAPL")
    assert res["errors"] == []
    assert res["direction_clearance"]["short"]["state"] == "relative_only"


def test_read_regime_never_raises_when_the_bus_is_down(monkeypatch):
    import services.trade_svc.compute as _c

    def boom():
        raise RuntimeError("memurai down")

    monkeypatch.setattr(_c, "_bus", boom)
    assert _c._read_regime() is None


# ── earnings-date enrichment (Phase 1, task 1.2 completed) ───────────────────

def test_an_earnings_date_inside_the_horizon_caps_both_verdicts(monkeypatch, tmp_path):
    """The gate that has NEVER fired. Schwab carries no earnings date, so
    days_to_earnings was always None and a multi-week hold could span a report
    with nothing said about it."""
    from services.trade_svc import earnings_calendar as ec

    db = tmp_path / "ec.db"
    conn = ec.init_db(db)
    soon = (_dt.date.today() + _dt.timedelta(days=9)).isoformat()
    ec.store_calendar(conn, [{"symbol": "AAPL", "report_date": soon,
                              "fiscal_date_ending": "", "estimate": None}])
    ec.close_db(conn)

    class Client(FakeClient):
        def get_fundamentals(self, symbol):
            return {"peRatio": 30.0, "revChangeTTM": 10.0,
                    "epsChangePercentTTM": 12.0, "returnOnEquity": 20.0}

    _patch(monkeypatch, Client())
    monkeypatch.setattr(compute, "_earnings_db_path", lambda: db)
    monkeypatch.setattr(compute, "_refresh_earnings_calendar", lambda conn: None)

    f = compute._fetch_fundamentals("AAPL")
    assert f.days_to_earnings == 9

    res = compute.analyze("AAPL")
    assert any("earnings" in g.lower()
               for g in res["position_verdict"]["gates_triggered"])


def test_no_api_key_leaves_the_earnings_gate_exactly_as_quiet_as_before(monkeypatch, tmp_path):
    """Without a key the calendar is empty, and an empty calendar must read as
    'unknown', never as 'nobody reports soon'."""
    from services.trade_svc import earnings_calendar as ec

    db = tmp_path / "ec.db"
    ec.close_db(ec.init_db(db))          # empty store

    class Client(FakeClient):
        def get_fundamentals(self, symbol):
            return {"peRatio": 30.0}

    _patch(monkeypatch, Client())
    monkeypatch.setattr(compute, "_earnings_db_path", lambda: db)
    monkeypatch.setattr(compute, "_refresh_earnings_calendar", lambda conn: None)

    f = compute._fetch_fundamentals("AAPL")
    assert f.days_to_earnings is None


def test_earnings_enrichment_is_skipped_under_pytest_by_default(monkeypatch):
    """Same isolation rule as the other stores: unguarded it opens a SQLite
    file in the repo AND issues a live vendor request during the suite."""
    from services.trade_svc import earnings_calendar as ec

    opened = []

    def spy(path):
        opened.append(path)
        raise RuntimeError("must not be reached under pytest")

    monkeypatch.setattr(ec, "init_db", spy)

    class Client(FakeClient):
        def get_fundamentals(self, symbol):
            return {"peRatio": 30.0}

    _patch(monkeypatch, Client())
    compute._fetch_fundamentals("AAPL")
    assert opened == [], f"enrichment opened the real store: {opened}"
