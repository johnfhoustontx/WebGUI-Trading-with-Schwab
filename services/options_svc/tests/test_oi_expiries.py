"""The Open Interest tab's expirations (``services/options_svc/oi_expiries``)."""
import inspect

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
    """The listed-expirations fetch every other symbol uses
    (``compute.fetch_scan_chain``): ``(chain, expirations_not_fetched)``."""
    def __init__(self, count=30, failed=0):
        self.calls, self.count, self.failed = [], count, failed

    def __call__(self, symbol, dte_max):
        self.calls.append((symbol, dte_max))
        if self.count is None:
            return None, self.failed
        return _chain(f"whole-{len(self.calls)}", self.count), self.failed


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    oi_expiries.reset()
    monkeypatch.setattr(gamma_heat_config, "open_interest", lambda: {
        "limited_symbols": ("$SPX",), "expiries": 8, "refresh_min": 30,
        "retry_min": 10})
    yield
    oi_expiries.reset()


def _ask(symbol, whole, widen=None, base=5, at=0.0):
    return oi_expiries.grid(_Engine(), symbol, _chain("base", base),
                            widen or _Widen(), whole, clock=lambda: at)


# ── every expiration ─────────────────────────────────────────────────────────

def test_a_symbol_gets_every_listed_expiration():
    whole, widen = _Whole(), _Widen()
    out = _ask("SPY", whole, widen)
    assert out["from"] == "whole-1" and len(out["expirations"]) == 30
    assert whole.calls == [("SPY", None)] and widen.calls == []


def test_the_whole_grid_is_kept_until_the_refresh_interval_passes():
    whole = _Whole()
    assert _ask("SPY", whole, at=1000.0)["from"] == "whole-1"
    assert _ask("SPY", whole, at=1000.0 + 29 * 60)["from"] == "whole-1"
    assert len(whole.calls) == 1
    assert _ask("SPY", whole, at=1000.0 + 30 * 60 + 1)["from"] == "whole-2"


def test_each_symbol_keeps_its_own_grid():
    whole = _Whole()
    for symbol in ("SPY", "QQQ", "SPY"):
        _ask(symbol, whole, at=5.0)
    assert whole.calls == [("SPY", None), ("QQQ", None)]


def test_a_whole_chain_with_some_expirations_missing_is_still_used():
    out = _ask("SPY", _Whole(count=20, failed=3))
    assert out["from"] == "whole-1" and len(out["expirations"]) == 20


@pytest.mark.parametrize("broken", [
    _Whole(count=None, failed=25),            # every run failed
    _Whole(count=2),                          # shorter than the chain in hand
    lambda symbol, dte_max: 1 / 0,            # the fetch itself raised
])
def test_a_failed_whole_fetch_falls_back_and_waits_before_it_is_tried_again(broken):
    """Seen on prod, 2026-11-14: a whole-chain request that failed was made
    again on the page's next refresh, half a minute of waiting each time."""
    good = _Whole()
    out = _ask("SPY", broken, at=100.0)
    assert out["from"] == "base" and len(out["expirations"]) == 5
    assert _ask("SPY", good, at=100.0 + 9 * 60)["from"] == "base"
    assert good.calls == []                              # not asked yet
    assert _ask("SPY", good, at=100.0 + 10 * 60 + 1)["from"] == "whole-1"


def test_the_whole_chain_is_never_one_request():
    """The first version asked Schwab for the whole chain in one call, which
    the proxy cut off at 30 seconds every time. This module makes no request
    of its own; compute hands it the Finder's fetch, which asks in runs."""
    source = inspect.getsource(oi_expiries)
    assert "get_option_chain" not in source and "_proxy" not in source
    from services.options_svc import compute
    assert "_oi_expiries.grid(eng, symbol, chain, _term_chain, fetch_scan_chain)" \
        in inspect.getsource(compute.gamma_snapshot)


# ── the limited symbols ──────────────────────────────────────────────────────

def test_a_limited_symbol_whose_chain_covers_its_count_fetches_nothing():
    whole, widen = _Whole(), _Widen()
    out = _ask("$SPX", whole, widen, base=9)
    assert out["from"] == "base" and len(out["expirations"]) == 8
    assert widen.calls == [] and whole.calls == []


def test_a_limited_symbol_is_widened_to_its_count_and_never_asked_for_whole():
    whole, widen = _Whole(), _Widen()
    out = _ask("$spx", whole, widen)
    assert out["from"] == "wide-1" and len(out["expirations"]) == 8
    assert widen.calls == [("$spx", 8)] and whole.calls == []


def test_the_limited_list_and_count_are_read_from_the_setting(monkeypatch):
    monkeypatch.setattr(gamma_heat_config, "open_interest", lambda: {
        "limited_symbols": ("SPY",), "expiries": 3, "refresh_min": 30,
        "retry_min": 10})
    out = _ask("SPY", _Whole())
    assert len(out["expirations"]) == 3 and out["from"] == "base"
    assert _ask("$SPX", _Whole())["from"] == "whole-1"


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
