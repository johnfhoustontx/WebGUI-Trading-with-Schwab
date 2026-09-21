"""The public Calculator -> Simulator hand-off over NiceGUI tab storage."""
import datetime as dt
import pathlib
import re

import pytest

from pages.options import public_handoff as ph

EXP = (dt.date.today() + dt.timedelta(days=30)).isoformat()


def _leg(**over):
    leg = {"option_type": "put", "side": "short", "strike": 500.0,
           "expiry": EXP, "qty": 1, "premium": 1.2}
    leg.update(over)
    return leg


def _legs():
    return [_leg(), _leg(side="long", strike=495.0, premium=0.5)]


# ── the pure half ───────────────────────────────────────────────────────────

def test_the_round_trip():
    payload = ph.position_payload(" spy ", _legs())
    assert payload == {"symbol": "SPY", "legs": _legs()}
    assert ph.seed_from(payload) == ("SPY", _legs())


def test_a_symbol_alone_is_a_position():
    assert ph.position_payload("QQQ", []) == {"symbol": "QQQ", "legs": []}
    assert ph.seed_from({"symbol": "QQQ", "legs": []}) == ("QQQ", [])


def test_an_unpriced_leg_travels_with_no_price():
    leg = _leg()
    del leg["premium"]
    payload = ph.position_payload("SPY", [leg])
    assert payload["legs"][0]["premium"] is None
    assert ph.seed_from(payload)[1][0]["premium"] is None


def test_a_share_leg_carries_no_strike_or_expiry():
    payload = ph.position_payload("SPY", [
        {"option_type": "stock", "side": "long", "qty": 1, "premium": 500.0,
         "strike": 1.0, "expiry": EXP}])
    assert payload["legs"][0]["strike"] is None
    assert payload["legs"][0]["expiry"] is None


@pytest.mark.parametrize("bad", [
    None, "", "spy; flushall", "../x", 5,
])
def test_an_invalid_symbol_returns_none(bad):
    assert ph.position_payload(bad, _legs()) is None


@pytest.mark.parametrize("payload", [
    None, "SPY", [], 7, {}, {"symbol": "SPY"}, {"legs": _legs()},
    {"symbol": "SPY", "legs": "not a list"},
    {"symbol": "SPY", "legs": [None]},
    {"symbol": "SPY", "legs": [{"option_type": "put"}]},
    {"symbol": "../x", "legs": []},
])
def test_a_malformed_payload_returns_none(payload):
    assert ph.seed_from(payload) is None


def test_more_than_eight_legs_returns_none():
    nine = [_leg(strike=400.0 + i) for i in range(9)]
    assert ph.position_payload("SPY", nine) is None
    assert ph.seed_from({"symbol": "SPY", "legs": nine}) is None
    assert ph.position_payload("SPY", nine[:8]) is not None


def test_a_nan_strike_returns_none():
    legs = [_leg(strike=float("nan"))]
    assert ph.position_payload("SPY", legs) is None
    assert ph.seed_from({"symbol": "SPY", "legs": legs}) is None


@pytest.mark.parametrize("premium", [float("nan"), -1.0, True])
def test_an_unusable_price_is_refused_where_a_missing_one_is_not(premium):
    assert ph.position_payload("SPY", [_leg(premium=premium)]) is None


def test_one_bad_leg_refuses_the_whole_position():
    assert ph.position_payload("SPY", [_leg(), _leg(side="sideways")]) is None


def test_a_stored_payload_with_extra_keys_is_stripped():
    stored = {"symbol": "spy", "note": "x",
              "legs": [{**_leg(), "junk": 1, "_manual_premium": True}]}
    symbol, legs = ph.seed_from(stored)
    assert symbol == "SPY"
    assert legs == [_leg()]


# ── the NiceGUI half ────────────────────────────────────────────────────────

def test_read_with_no_client_returns_none_and_does_not_raise():
    assert ph.read() is None


def test_write_with_no_client_is_a_silent_no_op():
    ph.write(ph.position_payload("SPY", _legs()))


def test_write_then_read_over_a_tab_store(monkeypatch):
    store = {}
    monkeypatch.setattr(ph, "_tab", lambda: store)
    ph.write(ph.position_payload("SPY", _legs()))
    assert set(store) == {ph.KEY}
    assert ph.read() == ("SPY", _legs())


def test_read_revalidates_what_is_stored(monkeypatch):
    """``read`` returns ``seed_from(stored)``, never the stored value itself:
    tab storage is memory this process keeps, but it is still data."""
    store = {ph.KEY: {"symbol": "SPY", "legs": [{**_leg(), "junk": 1}]}}
    monkeypatch.setattr(ph, "_tab", lambda: store)
    assert ph.read() == ("SPY", [_leg()])
    store[ph.KEY] = {"symbol": "SPY", "legs": [_leg(strike=float("nan"))]}
    assert ph.read() is None


def test_write_refuses_a_malformed_payload_and_leaves_nothing(monkeypatch):
    store = {}
    monkeypatch.setattr(ph, "_tab", lambda: store)
    ph.write({"symbol": "SPY", "legs": [_leg(strike=float("nan"))]})
    ph.write(None)
    assert store == {}


def test_a_store_that_raises_is_a_silent_no_op(monkeypatch):
    def _boom():
        raise RuntimeError("app.storage.tab can only be used with a client connection")
    monkeypatch.setattr(ph, "_tab", _boom)
    ph.write(ph.position_payload("SPY", _legs()))
    assert ph.read() is None


# ── the tab-storage age on the public process ───────────────────────────────

def test_live_main_caps_tab_storage_at_one_hour_before_it_runs():
    src = (pathlib.Path(__file__).resolve().parents[1] / "live_main.py").read_text(
        encoding="utf-8")
    # NiceGUI 3.13: ``Storage.max_tab_storage_age`` (nicegui/storage.py),
    # read by ``prune_tab_storage`` (nicegui/app/app.py) every 10 s.
    m = re.search(r"^nicegui_app\.storage\.max_tab_storage_age = "
                  r"_TAB_STORAGE_MAX_AGE_SEC$", src, re.MULTILINE)
    assert m, "live_main must set app.storage.max_tab_storage_age"
    assert re.search(r"^_TAB_STORAGE_MAX_AGE_SEC = 60 \* 60 ", src, re.MULTILINE)
    assert m.start() < src.index("ui.run(")
