"""The on-demand per-symbol dossier fetch (Symbol Dossier Task D3).

Every network leg is stubbed in every test: the four private legs are replaced
wholesale where the test is about assembly, and where a test exercises a REAL leg
it stubs the proxy client / compute helper that leg calls. No test reaches the
proxy.
"""
import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute, dossier

_KEYS = set(dossier.DOSSIER_KEYS)
_REAL_QUOTE = dossier._quote

_QUOTE = {"spot": 184.2, "day_pct": None}
_GEX = {"flip": 180.0, "put_wall": 175.0, "call_wall": 190.0}
_VOL = {"iv_rank": 62.5, "current_iv": 48.1, "hv_current": 41.3}
_EARN = {"earnings_status": "upcoming", "earnings_date": "2026-09-24"}


class _Recorder:
    """A leg stub that records its calls and returns (or raises) a fixed value."""

    def __init__(self, value=None, exc=None):
        self.value, self.exc, self.calls = value, exc, []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc is not None:
            raise self.exc
        return dict(self.value) if isinstance(self.value, dict) else self.value


@pytest.fixture
def legs(monkeypatch):
    stubs = {
        "_quote": _Recorder(_QUOTE),
        "_gex": _Recorder(_GEX),
        "_vol": _Recorder(_VOL),
        "_earnings": _Recorder(_EARN),
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(dossier, name, stub)
    return stubs


@pytest.fixture
def degrades(monkeypatch):
    seen = []
    monkeypatch.setattr(dossier._degrade, "degraded",
                        lambda area, **kw: seen.append(area))
    return seen


# ── assembly ────────────────────────────────────────────────────────────────

def test_no_usable_quote_is_an_error_and_spends_no_further_calls(legs):
    legs["_quote"].value = None

    out = dossier.build_dossier("NOPE")

    assert out["error"] == "no_quote"
    assert out["spot"] is None
    assert out["symbol"] == "NOPE"
    # A typo must not spend three more Schwab calls.
    assert legs["_gex"].calls == []
    assert legs["_vol"].calls == []
    assert legs["_earnings"].calls == []


def test_a_raising_quote_leg_reads_as_no_quote_and_speaks(legs, degrades):
    legs["_quote"].exc = RuntimeError("proxy down")

    out = dossier.build_dossier("MU")

    assert out["error"] == "no_quote"
    assert legs["_gex"].calls == [] and legs["_vol"].calls == []
    assert legs["_earnings"].calls == []
    assert "options.dossier_quote" in degrades


def test_a_good_fetch_populates_every_key_from_its_leg(legs):
    out = dossier.build_dossier("MU")

    assert out["error"] is None
    assert out["symbol"] == "MU"
    assert out["fetched_at"]
    for part in (_QUOTE, _GEX, _VOL, _EARN):
        for k, v in part.items():
            assert out[k] == v, k
    # The vol leg is handed the spot the quote leg read.
    assert legs["_vol"].calls[0][0] == ("MU", 184.2)


@pytest.mark.parametrize("leg, own_keys", [
    ("_gex", ("flip", "put_wall", "call_wall")),
    ("_vol", ("iv_rank", "current_iv", "hv_current")),
    ("_earnings", ("earnings_status", "earnings_date")),
])
def test_one_leg_raising_blanks_only_its_own_keys(legs, degrades, leg, own_keys):
    legs[leg].exc = RuntimeError("boom")

    out = dossier.build_dossier("MU")

    assert out["error"] is None
    for k in own_keys:
        assert out[k] is None, k
    siblings = {**_QUOTE, **_GEX, **_VOL, **_EARN}
    for k, v in siblings.items():
        if k not in own_keys:
            assert out[k] == v, k
    assert f"options.dossier{leg}" in degrades


@pytest.mark.parametrize("leg, own_keys", [
    ("_gex", ("flip", "put_wall", "call_wall")),
    ("_vol", ("iv_rank", "current_iv", "hv_current")),
    ("_earnings", ("earnings_status", "earnings_date")),
])
def test_one_leg_with_nothing_blanks_only_its_own_keys(legs, leg, own_keys):
    legs[leg].value = None

    out = dossier.build_dossier("MU")

    assert out["error"] is None
    for k in own_keys:
        assert out[k] is None, k
    assert out["spot"] == 184.2


def test_the_no_quote_and_success_payloads_share_one_key_set(legs):
    ok = dossier.build_dossier("MU")
    legs["_quote"].value = None
    bad = dossier.build_dossier("NOPE")

    assert set(ok) == _KEYS
    assert set(bad) == _KEYS


def test_a_leg_returning_extra_keys_cannot_widen_the_payload(legs):
    legs["_gex"].value = {**_GEX, "walls": [175.0, 190.0]}

    assert set(dossier.build_dossier("MU")) == _KEYS


def test_every_absent_value_is_none_never_zero(legs):
    legs["_quote"].value = None
    out = dossier.build_dossier("NOPE")

    for k in _KEYS - {"symbol", "error", "fetched_at"}:
        assert out[k] is None, k


@pytest.mark.parametrize("status", ["upcoming", "none_scheduled", "not_listed"])
def test_earnings_status_passes_through_three_valued(monkeypatch, status):
    date = "2026-09-24" if status == "upcoming" else None
    monkeypatch.setattr(compute, "scan_earnings", lambda symbol: (status, date))

    assert dossier._earnings("MU") == {"earnings_status": status,
                                       "earnings_date": date}


# ── fetched_at is Central, machine-independently ────────────────────────────

# 13:02:30 UTC == 08:02:30 CT (CDT, UTC-5) on 2026-09-17, seen from a host whose
# wall clock is nine hours ahead — so the test fails on a Central box exactly as
# it fails anywhere else.
_FROZEN_UTC = _dt.datetime(2026, 9, 17, 13, 2, 30, tzinfo=_dt.timezone.utc)
_FAKE_HOST_TZ = _dt.timezone(_dt.timedelta(hours=9))


class _FrozenDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FROZEN_UTC.astimezone(_FAKE_HOST_TZ).replace(tzinfo=None)
        return _FROZEN_UTC.astimezone(tz)


class _FrozenClockModule:
    datetime = _FrozenDatetime

    def __getattr__(self, name):
        return getattr(_dt, name)


@pytest.mark.parametrize("quote", [_QUOTE, None])
def test_fetched_at_is_naive_central_never_the_host_wall_clock(
        monkeypatch, legs, quote):
    monkeypatch.setattr(dossier, "_dt", _FrozenClockModule())
    legs["_quote"].value = quote

    stamp = dossier.build_dossier("MU")["fetched_at"]

    expected = _FROZEN_UTC.astimezone(ZoneInfo("America/Chicago")).replace(
        tzinfo=None).isoformat(timespec="seconds")
    assert stamp == expected == "2026-09-17T08:02:30"


# ── the REAL quote leg, against Schwab's raw nested shape ───────────────────

class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class _QuoteClient:
    def __init__(self, resp):
        self.resp, self.asked = resp, []

    def get_quotes(self, symbols):
        self.asked.append(list(symbols))
        return self.resp


def _raw_quote(symbol, last):
    # What compute.quote_last parses: {SYMBOL: {"quote": {"lastPrice": ...}}},
    # Schwab's own envelope as the proxy passes it through.
    return {symbol: {"assetMainType": "EQUITY", "symbol": symbol,
                     "quote": {"lastPrice": last, "netChange": 1.8,
                               "netPercentChange": 0.99, "closePrice": 182.4},
                     "reference": {"description": "Micron Technology"}}}


def _with_client(monkeypatch, resp):
    client = _QuoteClient(resp)
    monkeypatch.setattr(compute._proxy, "schwab_py_client", client)
    return client


def test_the_real_quote_leg_reads_lastprice_from_the_raw_envelope(monkeypatch):
    client = _with_client(monkeypatch, _Resp(_raw_quote("MU", 184.2)))

    out = dossier._quote("MU")

    assert out["spot"] == 184.2
    assert out["day_pct"] is None
    assert client.asked == [["MU"]]


def test_a_symbol_omitted_from_the_response_is_no_quote(monkeypatch, legs):
    _with_client(monkeypatch, _Resp(_raw_quote("MU", 184.2)))
    # The fixture stubbed the quote leg; put the REAL one back for this test.
    monkeypatch.setattr(dossier, "_quote", _REAL_QUOTE)

    out = dossier.build_dossier("NOPE")

    assert out["error"] == "no_quote"
    assert out["spot"] is None
    assert legs["_gex"].calls == [] and legs["_vol"].calls == []


def test_a_non_200_quote_is_no_quote(monkeypatch):
    _with_client(monkeypatch, _Resp(_raw_quote("MU", 184.2), status=503))

    assert dossier._quote("MU") is None


@pytest.mark.parametrize("last", [0, 0.0, -1.0, float("nan"), float("inf"),
                                  None, True, "184.2"])
def test_an_unusable_last_price_is_no_quote(monkeypatch, last):
    _with_client(monkeypatch, _Resp(_raw_quote("MU", last)))

    assert dossier._quote("MU") is None


# ── the REAL gex leg: walls by side of spot, never by position ──────────────

def test_the_real_gex_leg_assigns_a_lone_wall_by_spot(monkeypatch):
    # gamma_walls filters a missing side OUT, so a lone call wall arrives as a
    # one-element list. Read positionally it would be filed as the PUT wall.
    snap = {"spot": 184.2, "views": {"GEX": {"flip": 180.0, "walls": [190.0]}}}
    monkeypatch.setattr(compute, "_light_gex_context", lambda symbol: snap)

    assert dossier._gex("MU") == {"flip": 180.0, "put_wall": None,
                                  "call_wall": 190.0}


def test_the_real_gex_leg_with_no_context_is_none(monkeypatch):
    monkeypatch.setattr(compute, "_light_gex_context", lambda symbol: None)

    assert dossier._gex("MU") is None


# ── the REAL vol leg ────────────────────────────────────────────────────────

def test_the_real_vol_leg_tolerates_a_result_without_hv_current(monkeypatch):
    seen = {}
    monkeypatch.setattr(compute.se, "fetch_price_history",
                        lambda client, symbol: {"candles": []})

    def _iv(client, symbol, price=None, hist=None, chain=None):
        seen.update(symbol=symbol, price=price, hist=hist, chain=chain)
        # _empty_iv_data's shape: no hv_current key at all.
        return {"current_iv": 48.1, "iv_rank": None}

    monkeypatch.setattr(compute, "run_iv_analysis", _iv)

    out = dossier._vol("MU", 184.2)

    assert out == {"iv_rank": None, "current_iv": 48.1, "hv_current": None}
    assert seen == {"symbol": "MU", "price": 184.2,
                    "hist": {"candles": []}, "chain": None}


def test_the_real_vol_leg_reads_all_three_on_the_scans_definitions(monkeypatch):
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda c, s: None)
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None:
                        {"current_iv": 48.1, "iv_rank": 62.5, "hv_current": 41.3,
                         "iv_percentile": 70.0})

    assert dossier._vol("MU", 184.2) == _VOL
