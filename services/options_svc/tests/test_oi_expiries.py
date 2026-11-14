"""The Open Interest tab's expirations (``services/options_svc/oi_expiries``)."""
import pytest

from services.options_svc import oi_expiries
from shared import gamma_heat_config


class _Engine:
    def compute_term_grid(self, chain, top_n=5):
        dates = sorted(str(k).split(":", 1)[0] for k in chain["callExpDateMap"])
        return {"expirations": dates[:top_n], "from": chain["name"]}


def _chain(name, count):
    return {"name": name,
            "callExpDateMap": {f"2026-12-{d:02d}:{d}": {} for d in range(1, count + 1)},
            "putExpDateMap": {}}


class _Widen:
    """The count-based widening fetch a limited symbol uses."""
    def __init__(self, count=12):
        self.calls, self.count = [], count

    def __call__(self, symbol, base_chain, n):
        self.calls.append((symbol, n))
        return _chain(f"wide-{len(self.calls)}", self.count)


class _Whole:
    """The one-request whole chain every other symbol uses."""
    def __init__(self, count=30):
        self.calls, self.count = [], count

    def __call__(self, symbol, days):
        self.calls.append((symbol, days))
        return _chain(f"whole-{len(self.calls)}", self.count)


@pytest.fixture(autouse=True)
def whole(monkeypatch):
    oi_expiries.reset()
    monkeypatch.setattr(gamma_heat_config, "open_interest", lambda: {
        "limited_symbols": ("$SPX",), "expiries": 8, "all_days": 900,
        "refresh_min": 30})
    fetch = _Whole()
    monkeypatch.setattr(oi_expiries, "fetch_whole", fetch)
    yield fetch
    oi_expiries.reset()


# ── every expiration ─────────────────────────────────────────────────────────

def test_a_symbol_gets_every_expiration_in_one_request(whole):
    widen = _Widen()
    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), widen)
    assert out["from"] == "whole-1" and len(out["expirations"]) == 30
    assert whole.calls == [("SPY", 900)] and widen.calls == []


def test_the_whole_grid_is_kept_until_the_refresh_interval_passes(whole):
    now = [1000.0]
    ask = lambda: oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), _Widen(),   # noqa: E731
                                   clock=lambda: now[0])
    assert ask()["from"] == "whole-1"
    now[0] += 29 * 60
    assert ask()["from"] == "whole-1" and len(whole.calls) == 1
    now[0] += 61
    assert ask()["from"] == "whole-2" and len(whole.calls) == 2


def test_each_symbol_keeps_its_own_grid(whole):
    for symbol in ("SPY", "QQQ", "SPY"):
        oi_expiries.grid(_Engine(), symbol, _chain("base", 5), _Widen(),
                         clock=lambda: 5.0)
    assert whole.calls == [("SPY", 900), ("QQQ", 900)]


def test_a_failed_whole_fetch_falls_back_to_the_chain_in_hand_and_is_not_kept(
        monkeypatch, whole):
    def broken(symbol, days):
        raise RuntimeError("Schwab said no")

    monkeypatch.setattr(oi_expiries, "fetch_whole", broken)
    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), _Widen())
    assert out["from"] == "base" and len(out["expirations"]) == 5
    monkeypatch.setattr(oi_expiries, "fetch_whole", whole)
    assert oi_expiries.grid(_Engine(), "SPY", _chain("base", 5),
                            _Widen())["from"] == "whole-1"


def test_an_answer_shorter_than_the_chain_in_hand_is_not_used_or_kept(
        monkeypatch, whole):
    monkeypatch.setattr(oi_expiries, "fetch_whole", _Whole(count=2))
    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), _Widen())
    assert out["from"] == "base"
    monkeypatch.setattr(oi_expiries, "fetch_whole", whole)
    assert oi_expiries.grid(_Engine(), "SPY", _chain("base", 5),
                            _Widen())["from"] == "whole-1"


# ── the limited symbols ──────────────────────────────────────────────────────

def test_a_limited_symbol_whose_chain_covers_its_count_fetches_nothing(whole):
    widen = _Widen()
    out = oi_expiries.grid(_Engine(), "$SPX", _chain("base", 9), widen)
    assert out["from"] == "base" and len(out["expirations"]) == 8
    assert widen.calls == [] and whole.calls == []


def test_a_limited_symbol_is_widened_to_its_count_and_never_asked_for_whole(whole):
    widen = _Widen()
    out = oi_expiries.grid(_Engine(), "$spx", _chain("base", 5), widen)
    assert out["from"] == "wide-1" and len(out["expirations"]) == 8
    assert widen.calls == [("$spx", 8)] and whole.calls == []


def test_the_limited_list_and_count_are_read_from_the_setting(monkeypatch, whole):
    monkeypatch.setattr(gamma_heat_config, "open_interest", lambda: {
        "limited_symbols": ("SPY",), "expiries": 3, "all_days": 900,
        "refresh_min": 30})
    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), _Widen())
    assert len(out["expirations"]) == 3 and out["from"] == "base"
    assert oi_expiries.grid(_Engine(), "$SPX", _chain("base", 5),
                            _Widen())["from"] == "whole-1"


# ── what is published ────────────────────────────────────────────────────────

def test_the_grid_is_cut_to_the_counts_and_the_strikes_that_hold_any():
    grid = {"underlying_price": 100.0, "expirations": ["2026-12-01"], "cells": {
        "2026-12-01": {
            "100.0": {"call_oi": 5.0, "put_oi": 0.0, "net_gex_usd": 9.0,
                      "call_gex_usd": 9.0, "put_gex_usd": 0.0},
            "105.0": {"call_oi": 0.0, "put_oi": 0.0, "net_gex_usd": 0.0},
            "110.0": {"put_oi": 7.0},
            "115.0": "junk"}}}
    out = oi_expiries.slim(grid)
    assert out["cells"] == {"2026-12-01": {
        "100.0": {"call_oi": 5.0, "put_oi": 0.0},
        "110.0": {"call_oi": 0.0, "put_oi": 7.0}}}
    assert out["expirations"] == ["2026-12-01"] and out["underlying_price"] == 100.0
    assert grid["cells"]["2026-12-01"]["100.0"]["net_gex_usd"] == 9.0   # not edited


def test_something_that_is_not_a_grid_passes_through():
    assert oi_expiries.slim({}) == {} and oi_expiries.slim(None) is None


def test_fetch_whole_asks_once_for_the_days_ahead_and_refuses_a_bad_status(monkeypatch):
    import datetime as dt

    monkeypatch.undo()                       # the real fetch_whole, not the fake
    seen = []

    class _Resp:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            return {"ok": True}

    class _Client:
        status = 200

        def get_option_chain(self, symbol, **kw):
            seen.append((symbol, kw))
            return _Resp(self.status)

    client = _Client()
    monkeypatch.setattr(oi_expiries._proxy, "schwab_py_client", client, raising=False)
    assert oi_expiries.fetch_whole("SPY", 900) == {"ok": True}
    symbol, kw = seen[0]
    assert symbol == "SPY" and kw["contract_type"] == "ALL"
    assert (kw["to_date"] - kw["from_date"]) == dt.timedelta(days=900)
    client.status = 502
    with pytest.raises(RuntimeError):
        oi_expiries.fetch_whole("SPY", 900)
