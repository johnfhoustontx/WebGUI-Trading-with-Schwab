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
def test_fetch_range_returns_empty_on_any_failure(status, data):
    class Bad:
        def _get(self, path, params=None, **kw):
            return _Resp(status, data)

    assert sf.fetch_range("SPY", 1, 0, 10, client=Bad()) == []


def test_fetch_range_returns_empty_when_the_client_raises():
    class Boom:
        def _get(self, path, params=None, **kw):
            raise RuntimeError("proxy down")

    assert sf.fetch_range("SPY", 1, 0, 10, client=Boom()) == []


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


def test_top_up_starts_at_latest_ts(tmp_path, one_symbol):
    db = tmp_path / "s.db"
    st.upsert_bars([("SPY", 1, 1_700_000_000, 1, 1, 1, 1, 1),
                    ("SPY", 5, 1_700_000_300, 1, 1, 1, 1, 1)], db_path=db)
    client = FakeClient(lambda i, q: EMPTY)
    sf.top_up(db_path=db, client=client, today=dt.date(2023, 11, 15))
    starts = {q["frequency"]: int(q["startDate"]) for _, _, q, _ in client.calls}
    assert starts == {"1": 1_700_000_000_000, "5": 1_700_000_300_000}


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
    assert out["BAD/1"] == 0 and out["SPY/1"] == 1


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
