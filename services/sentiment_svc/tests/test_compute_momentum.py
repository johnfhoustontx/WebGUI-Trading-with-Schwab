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
            {"sector": "Tech", "industry": "Chips", "code": "45301020",
             "members": ["AAA", "BBB"]},
            {"sector": "Tech", "industry": "Software", "code": "45103010",
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

def test_universe_is_the_gics_map():
    uni = compute._momentum_universe()

    # The GICS Map tab: 725 symbols over 163 sub-industries, one of which
    # (Drug Retail) lists no symbol at all and is reported rather than lost.
    assert len(uni["stocks"]) == 725
    assert len(uni["industries"]) == 162
    assert uni["orphans"] == [{"sector": "Consumer Staples",
                               "industry": "Drug Retail", "code": "30101010",
                               "reason": "no_members"}]
    assert len(uni["sectors"]) == 11


def test_every_stock_sits_in_exactly_one_sub_industry_of_its_sector():
    uni = compute._momentum_universe()

    placed = [m for i in uni["industries"] for m in i["members"]]
    assert sorted(placed) == sorted(uni["stocks"])
    by_sector = {s["sector"]: set(s["members"]) for s in uni["sectors"]}
    for ind in uni["industries"]:
        assert set(ind["members"]) <= by_sector[ind["sector"]]
        assert len(ind["code"]) == 8


def test_fetch_universe_is_stocks_plus_sector_etfs_plus_benchmark():
    uni = compute._momentum_universe()

    symbols = compute._momentum_fetch_symbols(uni)

    assert len(symbols) == len(set(symbols))
    assert compute.MOMENTUM_BENCHMARK in symbols
    assert set(uni["stocks"]) <= set(symbols)
    assert {s["etf"] for s in uni["sectors"]} <= set(symbols)
    # A sub-industry is a basket of its members — it has no price series of its
    # own to fetch, and the 8-digit code must never reach the proxy.
    assert not {i["code"] for i in uni["industries"]} & set(symbols)


# --- delta fetch ------------------------------------------------------------

def test_first_run_fetches_every_symbol(conn, tiny):
    client = FakeClient()

    compute.compute_momentum(session_date="2026-07-28", conn=conn, client=client)

    fetched = {s for s, _ in client.requested}
    assert {"AAA", "BBB", "CCC", "DDD", "XLK",
            compute.MOMENTUM_BENCHMARK} <= fetched
    assert not {"45301020", "45103010"} & fetched


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

def _industry(payload, label):
    return next((r for r in payload["levels"]["industry"]
                 if r["label"] == label), None)


def test_a_thin_stock_still_measures_its_basket_but_is_not_ranked(conn, tiny):
    # The $5M floor asks "can I hold a position" — right for a stock row, wrong
    # for a basket member, which is only measuring its sub-industry. So a thin
    # name is dropped from the stock level but still counts in its basket.
    client = FakeClient()
    thin = FakeClient(volume=400_000.0)
    original = client.get_daily_history

    def mixed(symbol, months=12):
        return thin.get_daily_history(symbol, months) \
            if symbol == "BBB" else original(symbol, months)

    client.get_daily_history = mixed

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    assert "BBB" not in {r["symbol"] for r in payload["levels"]["stock"]}
    assert _industry(payload, "Chips")["members"] == ["AAA", "BBB"]


def test_an_untradeable_member_leaves_the_basket_and_a_short_basket_is_excluded(
        conn, tiny):
    client = FakeClient()
    dead = FakeClient(volume=100.0)
    original = client.get_daily_history

    def mixed(symbol, months=12):
        return dead.get_daily_history(symbol, months) if symbol == "BBB" \
            else original(symbol, months)

    client.get_daily_history = mixed

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=client)

    # Chips keeps one usable member, below the shipped minimum of two.
    assert _industry(payload, "Chips") is None
    assert _excluded(payload)["Chips"] == "too_few_members"
    # AAA is still scored as a stock, and Software is unaffected.
    assert "AAA" in {r["symbol"] for r in payload["levels"]["stock"]}
    assert _industry(payload, "Software") is not None


def test_the_minimum_basket_size_comes_from_config(conn, tiny, monkeypatch):
    from services.sentiment_svc import momentum_config
    monkeypatch.setattr(momentum_config, "min_basket_members", lambda: 3)

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    assert payload["levels"]["industry"] == []
    assert _excluded(payload)["Chips"] == "too_few_members"


def test_a_sub_industry_row_is_a_basket_keyed_by_its_gics_code(conn, tiny):
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    chips = _industry(payload, "Chips")
    assert chips["symbol"] == "45301020"
    assert chips["sector"] == "Tech"
    assert chips["basket"] is True
    assert chips["members"] == ["AAA", "BBB"]
    assert chips["raw"]["trend"] is not None


def test_stock_rows_name_their_sub_industry(conn, tiny):
    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    by_symbol = {r["symbol"]: r for r in payload["levels"]["stock"]}
    assert by_symbol["AAA"]["industry"] == "Chips"
    assert by_symbol["CCC"]["industry"] == "Software"


def test_rank_history_only_names_rows_scored_this_session(conn, tiny):
    # The universe changed on 2026-09-27 (ETF industries -> GICS sub-industry
    # baskets). A prior session's rows for names no longer scored must not be
    # charted beside today's, where an old rank 1 would read as a leader.
    momentum_db.write_scores(conn, "2026-07-27", "industry", [
        {"symbol": "SMH", "score": 1.0, "percentile": 100.0, "rank": 1,
         "components": {}, "participation": 0.5}])

    payload = compute.compute_momentum(session_date="2026-07-28", conn=conn,
                                       client=FakeClient())

    assert "SMH" not in payload["rank_history"]["industry"]
    assert "45301020" in payload["rank_history"]["industry"]


def test_baskets_still_build_when_the_benchmark_fails(conn, tiny):
    payload = compute.compute_momentum(
        session_date="2026-07-28", conn=conn,
        client=FakeClient(fail={compute.MOMENTUM_BENCHMARK}))

    assert {r["label"] for r in payload["levels"]["industry"]} == \
        {"Chips", "Software"}


# --- the equal-weight basket (pure) -----------------------------------------

def test_basket_is_the_mean_of_member_daily_returns():
    grid = ["d0", "d1", "d2"]
    members = {"A": ([100.0, 110.0, 110.0], grid),
               "B": ([50.0, 50.0, 55.0], grid)}

    level = compute._momentum_basket(members, grid)

    # d1: (+10% + 0%) / 2 = +5%; d2: (0% + 10%) / 2 = +5%.
    assert level == pytest.approx([100.0, 105.0, 110.25])


def test_basket_weights_are_equal_not_price_weighted():
    grid = ["d0", "d1"]
    members = {"PRICEY": ([1000.0, 1100.0], grid),   # +10%
               "CHEAP": ([10.0, 9.0], grid)}         # -10%

    assert compute._momentum_basket(members, grid) == pytest.approx([100.0, 100.0])


def test_a_member_joins_the_basket_when_its_history_starts():
    grid = ["d0", "d1", "d2"]
    members = {"OLD": ([100.0, 100.0, 100.0], grid),
               "NEW": ([20.0, 22.0], ["d1", "d2"])}

    # d1: only OLD has a return (0%); d2: (0% + 10%) / 2.
    assert compute._momentum_basket(members, grid) == pytest.approx(
        [100.0, 100.0, 105.0])


def test_basket_starts_at_the_first_date_any_member_has():
    members = {"A": ([10.0, 11.0], ["d1", "d2"])}

    assert compute._momentum_basket(members, ["d0", "d1", "d2"]) == \
        pytest.approx([100.0, 110.0])


def test_an_empty_basket_is_empty():
    assert compute._momentum_basket({}, ["d0", "d1"]) == []


def test_the_two_floors_are_separate_named_constants():
    assert compute.MOMENTUM_MIN_DOLLAR_VOLUME > compute.MOMENTUM_MIN_ETF_DOLLAR_VOLUME
