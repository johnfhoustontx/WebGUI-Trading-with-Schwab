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


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    oi_expiries.reset()
    monkeypatch.setattr(gamma_heat_config, "open_interest",
                        lambda: {"expiries": 8, "refresh_min": 30})
    yield
    oi_expiries.reset()


class _Widen:
    def __init__(self, count=12):
        self.calls, self.count = [], count

    def __call__(self, symbol, base_chain, n):
        self.calls.append((symbol, n))
        return _chain(f"wide-{len(self.calls)}", self.count)


def test_a_base_chain_that_covers_the_count_is_used_and_nothing_is_fetched():
    widen = _Widen()
    out = oi_expiries.grid(_Engine(), "$SPX", _chain("base", 9), widen)
    assert out["from"] == "base" and len(out["expirations"]) == 8
    assert widen.calls == []


def test_a_short_base_chain_is_widened_to_the_configured_count():
    widen = _Widen()
    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), widen)
    assert out["from"] == "wide-1" and len(out["expirations"]) == 8
    assert widen.calls == [("SPY", 8)]


def test_the_wide_grid_is_kept_until_the_refresh_interval_passes():
    widen, now = _Widen(), [1000.0]
    ask = lambda: oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), widen,   # noqa: E731
                                   clock=lambda: now[0])
    assert ask()["from"] == "wide-1"
    now[0] += 29 * 60
    assert ask()["from"] == "wide-1" and len(widen.calls) == 1
    now[0] += 61
    assert ask()["from"] == "wide-2" and len(widen.calls) == 2


def test_each_symbol_keeps_its_own_grid():
    widen = _Widen()
    for symbol in ("SPY", "QQQ", "SPY"):
        oi_expiries.grid(_Engine(), symbol, _chain("base", 5), widen, clock=lambda: 5.0)
    assert widen.calls == [("SPY", 8), ("QQQ", 8)]


def test_a_failed_wide_fetch_falls_back_to_the_chain_in_hand_and_is_not_kept():
    def broken(symbol, base_chain, n):
        raise RuntimeError("Schwab said no")

    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), broken)
    assert out["from"] == "base" and len(out["expirations"]) == 5
    widen = _Widen()
    assert oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), widen)["from"] == "wide-1"


def test_the_count_is_read_from_the_setting(monkeypatch):
    monkeypatch.setattr(gamma_heat_config, "open_interest",
                        lambda: {"expiries": 3, "refresh_min": 30})
    out = oi_expiries.grid(_Engine(), "SPY", _chain("base", 5), _Widen())
    assert len(out["expirations"]) == 3 and out["from"] == "base"
