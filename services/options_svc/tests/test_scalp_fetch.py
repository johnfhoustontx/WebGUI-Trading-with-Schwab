"""The Opening Scalp's bar fetcher (scalp_fetch): Schwab candles -> scalp.db rows.

No network and no live store: every call takes a fake client and a ``tmp_path``
database. The fake answers like Schwab's ``/pricehistory`` through the proxy's
``/passthrough`` (``datetime`` = bar start in epoch milliseconds UTC)."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import scalp_fetch as sf
from services.options_svc import scalp_store as st

_CT = ZoneInfo("America/Chicago")
_UTC = dt.timezone.utc
DAY_MS = 86_400_000


def _parse(params):
    """The ``params`` string the fetcher sends -> a dict."""
    return dict(kv.split("=", 1) for kv in params.split(","))


class FakeClient:
    """Records every ``_get`` and answers from ``answer(call_index, query)``."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []          # (path, outer_params, parsed query, kwargs)

    def _get(self, path, params=None, **kw):
        query = _parse(params["params"])
        self.calls.append((path, params, query, kw))
        return _Resp(200, self.answer(len(self.calls) - 1, query))


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def _candle(ms, price=100.0):
    return {"datetime": ms, "open": price, "high": price + 1, "low": price - 1,
            "close": price + 0.5, "volume": 10}


def _one_candle_in_window(query, price=100.0):
    return {"candles": [_candle(int(query["endDate"]) - 60_000, price)]}


EMPTY = {"symbol": "SPY", "empty": True, "candles": []}


@pytest.fixture
def one_symbol(monkeypatch):
    monkeypatch.setattr(sf.scalp_config, "bar_symbols", lambda: ["SPY"])


def test_candles_to_rows_converts_ms_and_drops_nan():
    payload = {"candles": [
        _candle(1_700_000_000_000, 50.0),
        {"datetime": 1_700_000_060_000, "open": 1.0, "high": float("nan"),
         "low": 1.0, "close": 1.0, "volume": 5},
        {"datetime": 1_700_000_120_000, "open": 1.0, "high": 2.0, "low": 0.5,
         "close": None, "volume": 5},
        {"datetime": 1_700_000_180_000, "open": 1.0, "high": 2.0, "low": 0.5,
         "close": float("inf"), "volume": 5},
        _candle(1_700_000_240_000, 51.0),
    ]}
    rows = sf.candles_to_rows("SPY", 5, payload)
    assert rows == [
        ("SPY", 5, 1_700_000_000, 50.0, 51.0, 49.0, 50.5, 10.0),
        ("SPY", 5, 1_700_000_240, 51.0, 52.0, 50.0, 51.5, 10.0),
    ]
    assert all(isinstance(r[2], int) for r in rows)


def test_candles_to_rows_tolerates_an_empty_or_odd_payload():
    assert sf.candles_to_rows("SPY", 1, EMPTY) == []
    assert sf.candles_to_rows("SPY", 1, {}) == []
    assert sf.candles_to_rows("SPY", 1, None) == []
    assert sf.candles_to_rows("SPY", 1, {"candles": [{"open": 1}]}) == []


def test_fetch_range_sends_the_passthrough_shape():
    client = FakeClient(lambda i, q: {"candles": [_candle(1_700_000_000_000)]})
    rows = sf.fetch_range("$SPX", 5, 1_000, 2_000, client=client)
    path, outer, query, _ = client.calls[0]
    assert path == "/passthrough"
    assert outer["endpoint"] == "/pricehistory"
    assert query == {"symbol": "$SPX", "frequencyType": "minute", "frequency": "5",
                     "startDate": "1000", "endDate": "2000",
                     "needExtendedHoursData": "true"}
    assert rows and rows[0][:3] == ("$SPX", 5, 1_700_000_000)


def test_fetch_range_keeps_a_futures_slash():
    client = FakeClient(lambda i, q: EMPTY)
    sf.fetch_range("/ES", 1, 0, 1, client=client)
    assert client.calls[0][2]["symbol"] == "/ES"


def test_fetch_range_never_uses_priority_lane():
    client = FakeClient(lambda i, q: {"candles": [_candle(1_700_000_000_000)]})
    sf.fetch_range("SPY", 1, 0, 10, client=client)
    for _, _, _, kw in client.calls:
        assert kw.get("priority", False) is False


@pytest.mark.parametrize("status,data", [(502, None), (200, "garbage"),
                                         (200, {"candles": "no"})])
def test_fetch_range_returns_none_on_any_failure(status, data):
    class Bad:
        def _get(self, path, params=None, **kw):
            return _Resp(status, data)

    assert sf.fetch_range("SPY", 1, 0, 10, client=Bad()) is None


def test_fetch_range_returns_none_when_the_client_raises():
    class Boom:
        def _get(self, path, params=None, **kw):
            raise RuntimeError("proxy down")

    assert sf.fetch_range("SPY", 1, 0, 10, client=Boom()) is None


def test_fetch_range_returns_empty_list_for_a_genuine_empty_answer():
    client = FakeClient(lambda i, q: EMPTY)
    assert sf.fetch_range("SPY", 1, 0, 10, client=client) == []


def test_backfill_stops_after_two_empty_windows(tmp_path, one_symbol):
    # Three windows of data, then nothing: the walk stops after TWO empties.
    client = FakeClient(lambda i, q: (_one_candle_in_window(q)
                                      if i % 5 < 3 else EMPTY))
    # per interval: windows 0-2 data, 3-4 empty -> 5 calls, then the next interval
    out = sf.backfill(db_path=tmp_path / "s.db", client=client,
                      today=dt.date(2026, 10, 16))
    assert len(client.calls) == 10                 # 5 per interval, two intervals
    assert out == {"SPY/1": 3, "SPY/5": 3}
    assert st.latest_ts("SPY", 1, db_path=tmp_path / "s.db") is not None


def test_backfill_one_empty_window_does_not_stop_the_walk(tmp_path, one_symbol):
    pattern = [True, False, True, False, False]    # data, hole, data, hole, hole
    client = FakeClient(lambda i, q: (_one_candle_in_window(q)
                                      if pattern[i % 5] else EMPTY))
    out = sf.backfill(db_path=tmp_path / "s.db", client=client,
                      today=dt.date(2026, 10, 16))
    assert len(client.calls) == 10
    assert out == {"SPY/1": 2, "SPY/5": 2}


def test_backfill_windows_stay_under_candle_cap(tmp_path, one_symbol):
    client = FakeClient(lambda i, q: (_one_candle_in_window(q) if i % 6 < 4
                                      else EMPTY))
    sf.backfill(db_path=tmp_path / "s.db", client=client,
                today=dt.date(2026, 10, 16))
    spans = {1: [], 5: []}
    for _, _, q, _ in client.calls:
        spans[int(q["frequency"])].append(
            (int(q["endDate"]) - int(q["startDate"])) / DAY_MS)
    assert spans[1] and spans[5]
    assert max(spans[1]) <= 10 + 1e-9
    assert max(spans[5]) <= 30 + 1e-9


def test_backfill_walks_back_contiguously_from_today(tmp_path, one_symbol):
    client = FakeClient(lambda i, q: _one_candle_in_window(q) if i < 2 else EMPTY)
    sf.backfill(db_path=tmp_path / "s.db", client=client,
                today=dt.date(2026, 10, 16))
    one_min = [q for _, _, q, _ in client.calls if q["frequency"] == "1"]
    # each window ends where the previous one began: no gap, no overlap
    for newer, older in zip(one_min, one_min[1:]):
        assert int(older["endDate"]) == int(newer["startDate"])
    # the first window reaches the end of the given day (CT), so today's bars are in
    end_first = dt.datetime.fromtimestamp(int(one_min[0]["endDate"]) / 1000, tz=_CT)
    assert end_first.date() == dt.date(2026, 10, 17) and end_first.hour == 0


def test_backfill_writes_the_rows_it_reports(tmp_path, one_symbol):
    db = tmp_path / "s.db"
    seen = set()

    def first_window_only(i, q):
        if q["frequency"] in seen:
            return EMPTY
        seen.add(q["frequency"])
        return _one_candle_in_window(q)

    client = FakeClient(first_window_only)
    out = sf.backfill(db_path=db, client=client, today=dt.date(2026, 10, 16))
    assert out == {"SPY/1": 1, "SPY/5": 1}
    assert len(st.bars("SPY", 1, 0, 10**12, db_path=db)) == 1
    assert len(st.bars("SPY", 5, 0, 10**12, db_path=db)) == 1


def test_top_up_starts_one_interval_before_latest_ts(tmp_path, one_symbol):
    # The latest bar AND the one before it are fetched again: a bar fetched
    # seconds after its close may have been a partial.
    db = tmp_path / "s.db"
    st.upsert_bars([("SPY", 1, 1_700_000_000, 1, 1, 1, 1, 1),
                    ("SPY", 5, 1_700_000_300, 1, 1, 1, 1, 1)], db_path=db)
    client = FakeClient(lambda i, q: EMPTY)
    sf.top_up(db_path=db, client=client, today=dt.date(2023, 11, 15))
    starts = {q["frequency"]: int(q["startDate"]) for _, _, q, _ in client.calls}
    assert starts == {"1": (1_700_000_000 - 60) * 1000,
                      "5": (1_700_000_300 - 300) * 1000}


def test_top_up_replaces_the_bar_before_the_latest(tmp_path, one_symbol):
    db = tmp_path / "s.db"
    latest = 1_700_000_000
    st.upsert_bars([("SPY", 1, latest - 60, 1, 1, 1, 1, 1),        # a partial
                    ("SPY", 1, latest, 1, 1, 1, 1, 1)], db_path=db)

    def corrected(i, q):
        if q["frequency"] != "1":
            return EMPTY
        return {"candles": [dict(_candle((latest - 60) * 1000), close=9.0)]}

    sf.top_up(db_path=db, client=FakeClient(corrected), today=dt.date(2023, 11, 15))
    got = st.bars("SPY", 1, latest - 60, latest - 59, db_path=db)
    assert [b.close for b in got] == [9.0]


def test_top_up_without_history_goes_three_trading_days_back(tmp_path, one_symbol):
    client = FakeClient(lambda i, q: EMPTY)
    # Monday 2026-10-19: three trading days back is Wednesday 2026-10-14
    sf.top_up(db_path=tmp_path / "s.db", client=client, today=dt.date(2026, 10, 19))
    start = dt.datetime.fromtimestamp(int(client.calls[0][2]["startDate"]) / 1000,
                                      tz=_CT)
    assert (start.date(), start.hour, start.minute) == (dt.date(2026, 10, 14), 0, 0)


def test_top_up_writes_and_counts_rows_per_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sf.scalp_config, "bar_symbols", lambda: ["SPY", "/ES"])
    client = FakeClient(lambda i, q: {"candles": [_candle(1_700_000_000_000 + i * 60_000)]})
    out = sf.top_up(db_path=tmp_path / "s.db", client=client,
                    today=dt.date(2023, 11, 15))
    assert out == {"SPY/1": 1, "SPY/5": 1, "/ES/1": 1, "/ES/5": 1}


def test_a_failing_symbol_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(sf.scalp_config, "bar_symbols", lambda: ["BAD", "SPY"])

    class Mixed:
        def _get(self, path, params=None, **kw):
            q = _parse(params["params"])
            if q["symbol"] == "BAD":
                raise RuntimeError("boom")
            return _Resp(200, {"candles": [_candle(1_700_000_000_000)]})

    out = sf.top_up(db_path=tmp_path / "s.db", client=Mixed(),
                    today=dt.date(2023, 11, 15))
    assert out["BAD/1"] == sf.FAILED and out["BAD/5"] == sf.FAILED
    assert out["SPY/1"] == 1


def test_top_up_today_is_one_request_per_symbol_for_the_day(tmp_path):
    # The intraday call's fetch: today's 5-minute bars only, one request each,
    # from midnight CT to the end of the day; a failed symbol is FAILED alone.
    db = tmp_path / "s.db"
    day = dt.date(2026, 10, 19)
    midnight = int(dt.datetime(2026, 10, 19, tzinfo=_CT).timestamp() * 1000)

    class Mixed(FakeClient):
        def _get(self, path, params=None, **kw):
            if _parse(params["params"])["symbol"] == "BAD":
                self.calls.append((path, params, _parse(params["params"]), kw))
                raise RuntimeError("boom")
            return super()._get(path, params, **kw)

    client = Mixed(lambda i, q: {"candles": [_candle(midnight + 9 * 3_600_000),
                                             _candle(midnight + 9 * 3_600_000 + 300_000)]})
    out = sf.top_up_today(["SPY", "/ES", "BAD", "SPY"], db_path=db, client=client,
                          today=day)
    assert out == {"SPY/5": 2, "/ES/5": 2, "BAD/5": sf.FAILED}
    assert [q["symbol"] for _, _, q, _ in client.calls] == ["SPY", "/ES", "BAD"]
    for path, outer, q, kw in client.calls:
        assert path == "/passthrough" and q["frequency"] == "5"
        assert int(q["startDate"]) == midnight
        assert int(q["endDate"]) == midnight + DAY_MS
        assert "priority" not in kw and "priority" not in outer
    assert len(st.bars("SPY", 5, 0, 10**12, db_path=db)) == 2
    assert st.bars("SPY", 1, 0, 10**12, db_path=db) == []


def _ct_ts(*args):
    return int(dt.datetime(*args, tzinfo=_CT).timestamp())


def test_top_up_today_refetches_an_earlier_days_hole_first(tmp_path):
    # Friday's bars end 13:50 (the nightly top-up never ran); Monday's intraday
    # top-up starts at Friday 13:45 and fills the hole, then today's bars.
    db = tmp_path / "s.db"
    fri_1350 = _ct_ts(2026, 10, 16, 13, 50)
    st.upsert_bars([("SPY", 5, fri_1350, 1, 1, 1, 1, 1)], db_path=db)
    missing = [_ct_ts(2026, 10, 16, 13, 55) + 300 * k for k in range(14)]  # 13:55..15:00
    monday = [_ct_ts(2026, 10, 19, 8, 30), _ct_ts(2026, 10, 19, 8, 35)]

    def answer(i, q):
        lo, hi = int(q["startDate"]) // 1000, int(q["endDate"]) // 1000
        return {"candles": [_candle(t * 1000) for t in [fri_1350] + missing + monday
                            if lo <= t < hi]}

    client = FakeClient(answer)
    out = sf.top_up_today(["SPY"], db_path=db, client=client,
                          today=dt.date(2026, 10, 19))
    q = client.calls[0][2]
    assert int(q["startDate"]) == _ct_ts(2026, 10, 16, 13, 45) * 1000
    assert int(q["endDate"]) == _ct_ts(2026, 10, 20, 0, 0) * 1000
    stored = [b.ts for b in st.bars("SPY", 5, 0, 10**12, db_path=db)]
    assert stored == [fri_1350] + missing + monday
    assert out == {"SPY/5": 1 + len(missing) + len(monday)}


def test_top_up_today_from_today_when_the_store_already_holds_today(tmp_path):
    db = tmp_path / "s.db"
    st.upsert_bars([("SPY", 5, _ct_ts(2026, 10, 19, 8, 30), 1, 1, 1, 1, 1)], db_path=db)
    client = FakeClient(lambda i, q: EMPTY)
    sf.top_up_today(["SPY"], db_path=db, client=client, today=dt.date(2026, 10, 19))
    assert int(client.calls[0][2]["startDate"]) == _ct_ts(2026, 10, 19, 0, 0) * 1000


def test_top_up_today_failure_mid_walk_keeps_latest_ts_before_the_hole(tmp_path):
    # A long gap pages in 30-day windows; the second fails: nothing after it is
    # fetched or written, so latest_ts stays before the hole and today's bars
    # (which would have jumped it) are not written.
    db = tmp_path / "s.db"
    old = _ct_ts(2026, 9, 1, 12, 0)
    st.upsert_bars([("SPY", 5, old, 1, 1, 1, 1, 1)], db_path=db)

    class Flaky:
        def __init__(self):
            self.calls = []

        def _get(self, path, params=None, **kw):
            q = _parse(params["params"])
            self.calls.append(q)
            if len(self.calls) == 2:
                return _Resp(502, None)
            return _Resp(200, _one_candle_in_window(q))

    client = Flaky()
    out = sf.top_up_today(["SPY"], db_path=db, client=client,
                          today=dt.date(2026, 10, 19))
    assert out == {"SPY/5": sf.FAILED}
    assert len(client.calls) == 2                       # stopped AT the failure
    assert int(client.calls[0]["startDate"]) == (old - 300) * 1000
    failed_start = int(client.calls[1]["startDate"]) // 1000
    assert st.latest_ts("SPY", 5, db_path=db) < failed_start
    assert st.latest_ts("SPY", 5, db_path=db) < _ct_ts(2026, 10, 19, 0, 0)


def test_backfill_failure_between_data_windows_is_not_the_end_of_history(
        tmp_path, one_symbol):
    db = tmp_path / "s.db"
    seen = {}

    class Flaky:
        """1-minute: data, then HTTP 429 (twice would have meant 'empty'), data
        that must never be fetched. 5-minute: one data window, then empties."""

        def __init__(self):
            self.calls = []

        def _get(self, path, params=None, **kw):
            q = _parse(params["params"])
            self.calls.append(q)
            n = seen[q["frequency"]] = seen.get(q["frequency"], 0) + 1
            if q["frequency"] == "1":
                if n == 2:
                    return _Resp(429, None)
                return _Resp(200, _one_candle_in_window(q))
            return _Resp(200, _one_candle_in_window(q) if n == 1 else EMPTY)

    client = Flaky()
    out = sf.backfill(db_path=db, client=client, today=dt.date(2026, 10, 16))
    one_min_calls = [q for q in client.calls if q["frequency"] == "1"]
    assert len(one_min_calls) == 2                      # stopped AT the failure
    assert out["SPY/1"] == sf.FAILED                    # reported, not "done"
    assert len(st.bars("SPY", 1, 0, 10**12, db_path=db)) == 1   # first window kept
    assert out["SPY/5"] == 1                            # the other key is unharmed


def test_top_up_failed_middle_window_leaves_no_hole_behind_it(tmp_path, one_symbol):
    db = tmp_path / "s.db"
    latest = int(dt.datetime(2026, 9, 1, 12, 0, tzinfo=_UTC).timestamp())
    st.upsert_bars([("SPY", 1, latest, 1, 1, 1, 1, 1),
                    ("SPY", 5, latest, 1, 1, 1, 1, 1)], db_path=db)

    class Flaky:
        def __init__(self):
            self.calls = []

        def _get(self, path, params=None, **kw):
            q = _parse(params["params"])
            self.calls.append(q)
            if q["frequency"] == "1" and sum(
                    c["frequency"] == "1" for c in self.calls) == 2:
                return _Resp(502, None)
            return _Resp(200, _one_candle_in_window(q))

    client = Flaky()
    out = sf.top_up(db_path=db, client=client, today=dt.date(2026, 10, 16))
    one_min = [q for q in client.calls if q["frequency"] == "1"]
    assert len(one_min) == 2                            # nothing after the failed one
    assert out["SPY/1"] == sf.FAILED
    # only the first window's candle was written: latest_ts stops BEFORE the hole
    failed_start = int(one_min[1]["startDate"]) // 1000
    assert st.latest_ts("SPY", 1, db_path=db) < failed_start
    assert out["SPY/5"] > 0                             # the other key carried on


def test_top_up_pages_a_long_gap_in_contiguous_windows(tmp_path, one_symbol):
    db = tmp_path / "s.db"
    latest = int(dt.datetime(2026, 9, 1, 12, 0, tzinfo=_UTC).timestamp())
    st.upsert_bars([("SPY", 1, latest, 1, 1, 1, 1, 1)], db_path=db)
    client = FakeClient(lambda i, q: EMPTY)
    sf.top_up(db_path=db, client=client, today=dt.date(2026, 10, 16))
    wins = [(int(q["startDate"]), int(q["endDate"]))
            for _, _, q, _ in client.calls if q["frequency"] == "1"]
    assert len(wins) > 3                                # > 10 days back: paged
    assert wins[0][0] == (latest - 60) * 1000            # one interval back
    for (s0, e0), (s1, _e1) in zip(wins, wins[1:]):
        assert s1 == e0                                 # contiguous, no overlap
    assert all(e - s <= 10 * DAY_MS for s, e in wins)
    end_bound = dt.datetime(2026, 10, 17, tzinfo=_CT)
    assert wins[-1][1] == int(end_bound.timestamp() * 1000)


def test_ct_keeps_0830_across_dst():
    # 2026-10-30 is CDT (UTC-5); 2026-11-02 is CST (UTC-6) - DST ended 2026-11-01
    before = dt.datetime(2026, 10, 30, 13, 30, tzinfo=_UTC).timestamp()
    after = dt.datetime(2026, 11, 2, 14, 30, tzinfo=_UTC).timestamp()
    for ts in (before, after):
        got = sf.ct(int(ts))
        assert (got.hour, got.minute) == (8, 30)
        assert got.tzinfo is not None and got.utcoffset() is not None
    assert sf.ct(int(before)).utcoffset() == dt.timedelta(hours=-5)
    assert sf.ct(int(after)).utcoffset() == dt.timedelta(hours=-6)


def test_scalp_fetch_imports_the_proxy_client_only_lazily():
    """A module that needs only ``ct`` must not construct the proxy client at
    import: the ``_proxy`` import may sit inside a function and nowhere else."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(sf))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [getattr(node, "module", None) or ""] + [a.name for a in node.names]
            assert not any("_proxy" in n for n in names), names
    lazy = [n for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            and any("_proxy" in (getattr(n, "module", None) or "") + a.name
                    for a in n.names)]
    assert lazy, "the default client must still be built from services._proxy"
