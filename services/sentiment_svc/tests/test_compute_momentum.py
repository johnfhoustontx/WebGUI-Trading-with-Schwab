"""compute_momentum orchestration — universe, delta fetch, filters, payload."""
import pandas as pd
import pytest

from services.sentiment_svc import compute, momentum_db


@pytest.fixture()
def conn():
    c = momentum_db.connect(":memory:")
    yield c
    c.close()


class FakeClient:
    """Records every price-history request and serves generated bars."""

    def __init__(self, series=None, fail=(), volume=10_000_000.0, bars=260,
                 end="2026-07-28"):
        self.series = series or {}
        self.fail = set(fail)
        self.volume = volume
        self.bars = bars
        self.end = end
        self.requested = []
        self.quotes = {"$VIX": 16.0, "$VIX9D": 14.0}

    def get_daily_history(self, symbol, months=12):
        self.requested.append((symbol, months))
        if symbol in self.fail:
            raise RuntimeError("no quote")
        closes = self.series.get(symbol)
        if closes is None:
            closes = [100.0 * (1.0 + 0.001) ** i for i in range(self.bars)]
        dates = pd.date_range(end=self.end, periods=len(closes), freq="D")
        vol = self.volume / closes[0] if closes[0] else 1.0
        return pd.DataFrame({
            "datetime": dates, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": [vol] * len(closes),
        })

    def get_quotes(self, symbols):
        return {s: {"lastPrice": self.quotes.get(s, 1.0)} for s in symbols}


def _tiny_universe():
    return {
        "stocks": ["AAA", "BBB", "CCC", "DDD"],
        "industries": [
            {"sector": "Tech", "industry": "Chips", "etf": "SMH",
             "members": ["AAA", "BBB"]},
            {"sector": "Tech", "industry": "Software", "etf": "IGV",
             "members": ["CCC", "DDD"]},
        ],
        "sectors": [{"sector": "Tech", "etf": "XLK",
                     "members": ["AAA", "BBB", "CCC", "DDD"]}],
        "orphans": [],
    }


@pytest.fixture()
def tiny(monkeypatch):
    monkeypatch.setattr(compute, "_momentum_universe", _tiny_universe)
    return _tiny_universe()


# --- universe ---------------------------------------------------------------

def test_universe_covers_every_constituent_and_industry_etf():
    uni = compute._momentum_universe()

    assert len(uni["stocks"]) == 311
    # 74 industries on the Stocks tab, but 4 share an ETF another industry
    # already owns — scoring the same price series twice would put duplicate
    # rows in the cross-section and invent a "two industries agree" signal.
    assert len(uni["industries"]) == 70
    assert len(uni["orphans"]) == 4
    assert len(uni["sectors"]) == 11


def test_orphan_industries_still_feed_their_sector():
    uni = compute._momentum_universe()
    tech = {s["sector"]: s for s in uni["sectors"]}

    every_member = {m for s in tech.values() for m in s["members"]}
    assert len(every_member) > 0
    for orphan in uni["orphans"]:
        assert orphan["reason"] == "duplicate_etf"


def test_fetch_universe_is_constituents_plus_etfs_plus_benchmark():
    uni = compute._momentum_universe()

    symbols = compute._momentum_fetch_symbols(uni)

    assert len(symbols) == len(set(symbols))
    assert compute.MOMENTUM_BENCHMARK in symbols
    assert set(uni["stocks"]) <= set(symbols)
    assert {i["etf"] for i in uni["industries"]} <= set(symbols)


# --- delta fetch ------------------------------------------------------------

def test_first_run_fetches_every_symbol(conn, tiny):
    client = FakeClient()

    compute.compute_momentum(session_date="2026-07-28", conn=conn, client=client)

    fetched = {s for s, _ in client.requested}
    assert {"AAA", "BBB", "CCC", "DDD", "SMH", "IGV", "XLK",
            compute.MOMENTUM_BENCHMARK} <= fetched


def test_a_symbol_already_current_is_not_refetched(conn, tiny):
    client = FakeClient()
    compute.compute_momentum(session_date="2026-07-28", conn=conn, client=client)
    stored_max = momentum_db.max_date(conn, "AAA")

    second = FakeClient()
    compute.compute_momentum(session_date=stored_max, conn=conn, client=second)

    assert "AAA" not in {s for s, _ in second.requested}


def test_a_stale_symbol_asks_for_a_short_window_not_a_full_year(conn, tiny):
    client = FakeClient()
    compute.compute_momentum(session_date="2026-07-28", conn=conn, client=client)

    second = FakeClient()
    compute.compute_momentum(session_date="2026-08-04", conn=conn, client=second)

    months = dict(second.requested)
    assert months["AAA"] == 1


def test_first_backfill_asks_for_a_full_year(conn, tiny):
    client = FakeClient()

    compute.compute_momentum(session_date="2026-07-28", conn=conn, client=client)

    assert dict(client.requested)["AAA"] == 12


# --- exclusions -------------------------------------------------------------

def _excluded(payload):
    return {e["symbol"]: e["reason"] for e in payload["excluded"]}


def _level_symbols(payload, level):
    return {r["symbol"] for r in payload["levels"][level]}


def test_a_failing_fetch_is_excluded_and_absent_from_the_levels(conn, tiny):
    client = FakeClient(fail={"AAA"})

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    assert _excluded(payload)["AAA"] == "no_quote"
    assert "AAA" not in _level_symbols(payload, "stock")


def test_a_thin_volume_symbol_is_excluded_for_liquidity(conn, tiny):
    client = FakeClient()
    thin = FakeClient(volume=1_000.0)
    # Only BBB is thin; everything else keeps the healthy default.
    original = client.get_daily_history

    def mixed(symbol, months=12):
        return thin.get_daily_history(symbol, months) if symbol == "BBB" \
            else original(symbol, months)

    client.get_daily_history = mixed

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    assert _excluded(payload)["BBB"] == "liquidity"
    assert "BBB" not in _level_symbols(payload, "stock")


def test_a_short_history_symbol_is_excluded_for_insufficient_bars(conn, tiny):
    short = [100.0 + i for i in range(30)]
    client = FakeClient(series={"CCC": short})

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    assert _excluded(payload)["CCC"] == "insufficient_bars"
    assert "CCC" not in _level_symbols(payload, "stock")


def test_excluded_symbols_do_not_enter_the_zscore_population(conn, tiny):
    # A dropped symbol must not become a zero in the distribution.
    client = FakeClient(fail={"AAA"})

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    scores = [r["score"] for r in payload["levels"]["stock"]]
    assert all(s is not None for s in scores)
    assert len(scores) == 3


# --- payload ----------------------------------------------------------------

def test_payload_carries_the_three_levels_and_a_regime(conn, tiny):
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    assert payload["schema"] == 1
    assert payload["session_date"] == "2026-07-28"
    assert set(payload["levels"]) == {"sector", "industry", "stock"}
    assert payload["regime"]["state"] in {"favorable", "neutral", "suppressed"}
    assert payload["computed_at"]


def test_rows_are_ranked_best_first_with_a_percentile(conn, tiny):
    fast = [100.0 * 1.004 ** i for i in range(260)]
    slow = [100.0 * 1.0001 ** i for i in range(260)]
    client = FakeClient(series={"AAA": fast, "BBB": slow, "CCC": slow, "DDD": slow})

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)
    rows = payload["levels"]["stock"]

    assert rows[0]["symbol"] == "AAA"
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    assert 0.0 <= rows[0]["percentile"] <= 100.0


def test_stock_rows_carry_the_three_block_alignment(conn, tiny):
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())
    row = payload["levels"]["stock"][0]

    assert len(row["alignment"]) == 3
    assert all(isinstance(b, bool) for b in row["alignment"])
    assert row["sector"] and row["industry"]


def test_industry_rows_carry_participation_but_stocks_do_not(conn, tiny):
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    industry = payload["levels"]["industry"][0]
    stock = payload["levels"]["stock"][0]

    assert industry["participation"] is not None
    assert stock["participation"] is None
    assert "participation" not in stock["components"]


def test_scores_are_persisted_for_the_session(conn, tiny):
    compute.compute_momentum(session_date="2026-07-28", conn=conn,
                             client=FakeClient())

    assert momentum_db.scores(conn, "2026-07-28", "industry")
    assert momentum_db.scores(conn, "2026-07-28", "stock")


def test_rank_prev_comes_from_the_previous_stored_session(conn, tiny):
    fast = [100.0 * 1.004 ** i for i in range(260)]
    slow = [100.0 * 1.0001 ** i for i in range(260)]
    compute.compute_momentum(
        session_date="2026-07-27", conn=conn,
        client=FakeClient(end="2026-07-27",
                          series={"AAA": slow, "BBB": fast, "CCC": slow, "DDD": slow}))

    payload = compute.compute_momentum(
        session_date="2026-07-28", conn=conn,
        client=FakeClient(end="2026-07-28",
                          series={"AAA": fast, "BBB": slow, "CCC": slow, "DDD": slow}))

    top = payload["levels"]["stock"][0]
    assert top["symbol"] == "AAA"
    assert top["rank_prev"] is not None and top["rank_prev"] > top["rank"]


def test_compute_never_raises_when_the_proxy_is_dead(conn, tiny):
    class Dead:
        def get_daily_history(self, symbol, months=12):
            raise RuntimeError("proxy down")

        def get_quotes(self, symbols):
            raise RuntimeError("proxy down")

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=Dead())

    assert payload["levels"]["stock"] == []
    assert payload["regime"]["state"] == "neutral"
    assert len(payload["excluded"]) >= 4


# --- rank history (the ribbon's input) --------------------------------------

def test_payload_carries_rank_history_per_level(conn, tiny):
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    hist = payload["rank_history"]
    assert set(hist) == {"sector", "industry", "stock"}
    # Today's own session is included, so the ribbon is never empty on day one.
    assert hist["stock"]["AAA"][-1][0] == "2026-07-28"


def test_rank_history_accumulates_across_sessions(conn, tiny):
    compute.compute_momentum(session_date="2026-07-27", conn=conn,
                             client=FakeClient(end="2026-07-27"))
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient(end="2026-07-28"))

    assert [d for d, _ in payload["rank_history"]["stock"]["AAA"]] == \
        ["2026-07-27", "2026-07-28"]


# --- liquidity floors differ by role ----------------------------------------

def test_a_thin_industry_etf_is_kept_but_a_thin_stock_is_not(conn, tiny):
    # The $5M floor asks "can I hold a position" — right for a stock, wrong for
    # an industry ETF, which is a measurement instrument for its constituents.
    client = FakeClient()
    thin = FakeClient(volume=400_000.0)
    original = client.get_daily_history

    def mixed(symbol, months=12):
        return thin.get_daily_history(symbol, months) \
            if symbol in {"SMH", "BBB"} else original(symbol, months)

    client.get_daily_history = mixed

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    assert "SMH" in {r["symbol"] for r in payload["levels"]["industry"]}
    assert "BBB" not in {r["symbol"] for r in payload["levels"]["stock"]}


def test_an_untradeable_etf_price_series_is_still_dropped(conn, tiny):
    client = FakeClient()
    dead = FakeClient(volume=100.0)
    original = client.get_daily_history

    def mixed(symbol, months=12):
        return dead.get_daily_history(symbol, months) if symbol == "SMH" \
            else original(symbol, months)

    client.get_daily_history = mixed

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    assert "SMH" not in {r["symbol"] for r in payload["levels"]["industry"]}


def test_the_two_floors_are_separate_named_constants():
    assert compute.MOMENTUM_MIN_DOLLAR_VOLUME > compute.MOMENTUM_MIN_ETF_DOLLAR_VOLUME
