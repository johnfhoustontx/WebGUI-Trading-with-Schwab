"""The autoscan can fetch one wide chain and cut its three windows locally."""
import datetime as dt

import scanner_engine as se

D = dt.date(2026, 10, 5)


def _chain():
    days = [0, 2, 4, 7, 14, 21, 30, 44]
    exp = {f"{(D + dt.timedelta(days=n)).isoformat()}:{n}": {"100.0": [{"x": n}]}
           for n in days}
    return {"symbol": "AAPL", "underlyingPrice": 100.0,
            "callExpDateMap": dict(exp), "putExpDateMap": dict(exp)}


def _dtes(chain, side="callExpDateMap"):
    return sorted(int(k.split(":")[1]) for k in chain[side])


def test_a_window_keeps_only_its_expirations_on_both_sides():
    out = se.slice_chain(_chain(), D, D + dt.timedelta(days=4))
    assert _dtes(out) == [0, 2, 4] and _dtes(out, "putExpDateMap") == [0, 2, 4]


def test_the_three_scan_windows_partition_as_three_fetches_would():
    wide = _chain()
    assert _dtes(se.slice_chain(wide, D + dt.timedelta(days=5),
                                D + dt.timedelta(days=15))) == [7, 14]
    assert _dtes(se.slice_chain(wide, D + dt.timedelta(days=20),
                                D + dt.timedelta(days=45))) == [21, 30, 44]


def test_everything_else_on_the_chain_is_kept_and_the_source_untouched():
    wide = _chain()
    out = se.slice_chain(wide, D, D)
    assert out["underlyingPrice"] == 100.0 and out["symbol"] == "AAPL"
    assert len(wide["callExpDateMap"]) == 8


def test_a_window_with_no_expirations_has_empty_maps_not_missing_ones():
    out = se.slice_chain(_chain(), D + dt.timedelta(days=50), D + dt.timedelta(days=60))
    assert out["callExpDateMap"] == {} and out["putExpDateMap"] == {}


def test_no_chain_stays_no_chain():
    assert se.slice_chain(None, D, D) is None


# ── the header a cut carries ────────────────────────────────────────────────
# Measured against Schwab: a window holding no expiration is answered HTTP 200,
# status SUCCESS, both maps empty, numberOfContracts 0 and underlyingPrice 0.0.
# The scan's funnel reads that price (``chain_has_underlying``), so a cut has to
# carry the header a fetch of the same window would have.

def _schwab_chain():
    """``_chain()`` with the header fields a real Schwab chain carries."""
    return dict(_chain(), status="SUCCESS", numberOfContracts=16)


def _schwab_empty_window():
    """What Schwab itself answers for a window with no expirations."""
    return {"symbol": "AAPL", "status": "SUCCESS", "underlyingPrice": 0.0,
            "numberOfContracts": 0, "callExpDateMap": {}, "putExpDateMap": {}}


def test_a_cut_recounts_the_contracts_it_kept():
    wide = _schwab_chain()
    out = se.slice_chain(wide, D, D + dt.timedelta(days=4))
    assert out["numberOfContracts"] == 6          # 3 expirations x 2 sides
    assert out["underlyingPrice"] == 100.0        # a window with contracts keeps the price
    assert wide["numberOfContracts"] == 16        # the source header is untouched


def test_the_recount_counts_contracts_not_strikes():
    wide = _schwab_chain()
    key = f"{D.isoformat()}:0"
    wide["callExpDateMap"] = {key: {"100.0": [{"x": 0}, {"x": 1}], "105.0": [{"x": 2}]}}
    wide["putExpDateMap"] = {key: {"100.0": [{"x": 3}]}}
    assert se.slice_chain(wide, D, D)["numberOfContracts"] == 4


def test_an_empty_window_carries_the_header_schwab_answers_one_with():
    wide = _schwab_chain()
    out = se.slice_chain(wide, D + dt.timedelta(days=50), D + dt.timedelta(days=60))
    assert out == _schwab_empty_window()
    assert wide["underlyingPrice"] == 100.0 and wide["numberOfContracts"] == 16


def test_an_empty_cut_reads_to_the_scan_exactly_as_schwabs_empty_answer_does():
    """The funnel's ``underlying_zero`` and ``screen_spreads``' early return both
    ask ``chain_has_underlying``; a cut that kept the wide chain's real price
    would answer it differently from the fetch it stands in for."""
    cut = se.slice_chain(_schwab_chain(), D + dt.timedelta(days=50),
                         D + dt.timedelta(days=60))
    assert se.chain_has_underlying(cut) is se.chain_has_underlying(_schwab_empty_window())
    assert se.chain_has_underlying(cut) is False


def test_a_one_sided_window_is_not_an_empty_one():
    """Only a window with NO expiration on either side loses its price."""
    wide = _schwab_chain()
    wide["putExpDateMap"] = {}
    out = se.slice_chain(wide, D, D + dt.timedelta(days=4))
    assert out["underlyingPrice"] == 100.0 and out["numberOfContracts"] == 3


# ── Task 16: the scan fetches one wide chain per symbol ─────────────────────

from shared import marketdata_config as mdc  # noqa: E402


class _Client:
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self):
        self.windows = []

    def get_option_chain(self, symbol, contract_type=None, from_date=None,
                         to_date=None, **kw):
        self.windows.append(((from_date - D).days, (to_date - D).days))

        class R:
            status_code = 200

            @staticmethod
            def json():
                return _chain()
        return R()


def _cfg(monkeypatch, wide, exclude=("SPY",)):
    monkeypatch.setattr(mdc, "section", lambda name: {
        "wide_fetch": wide, "wide_fetch_exclude": list(exclude)})


def test_wide_fetch_off_keeps_three_fetches(monkeypatch):
    _cfg(monkeypatch, wide=False)
    c = _Client()
    out = se.scan_chains(c, "AAPL", D)
    assert sorted(c.windows) == [(0, 4), (5, 15), (20, 45)]
    assert set(out) == {"iv", "swing", "zero"}


def test_wide_fetch_on_makes_one_fetch_and_cuts_three_windows(monkeypatch):
    _cfg(monkeypatch, wide=True)
    c = _Client()
    out = se.scan_chains(c, "AAPL", D)
    assert c.windows == [(0, 45)]
    assert _dtes(out["zero"]) == [0, 2, 4]
    assert _dtes(out["swing"]) == [7, 14]
    assert _dtes(out["iv"]) == [21, 30, 44]


def test_an_excluded_symbol_keeps_three_fetches(monkeypatch):
    _cfg(monkeypatch, wide=True, exclude=("SPY", "$SPX"))
    c = _Client()
    se.scan_chains(c, "SPY", D)
    assert len(c.windows) == 3


def test_when_the_wide_fetch_and_its_three_fallback_fetches_all_fail_nothing_is_returned(
        monkeypatch):
    """A failed wide fetch falls back to the three window fetches (see the
    section further down); only when those fail too is the result three missing
    chains - exactly what three failed fetches give with the switch off."""
    _cfg(monkeypatch, wide=True)
    asked = []

    def _fails(client, symbol, from_date=None, to_date=None):
        asked.append(((from_date - D).days, (to_date - D).days))
        return None

    monkeypatch.setattr(se, "fetch_option_chain", _fails)
    assert se.scan_chains(_Client(), "AAPL", D) == {"iv": None, "swing": None, "zero": None}
    assert asked == [(0, 45), (20, 45), (5, 15), (0, 4)]


def test_unreadable_config_keeps_three_fetches(monkeypatch):
    monkeypatch.setattr(mdc, "section",
                        lambda name: (_ for _ in ()).throw(RuntimeError("x")))
    c = _Client()
    se.scan_chains(c, "AAPL", D)
    assert len(c.windows) == 3


# ── the exclusion list is hand-typed ────────────────────────────────────────

def test_the_exclusion_list_matches_whatever_the_case(monkeypatch):
    _cfg(monkeypatch, wide=True, exclude=("spy", "$spx"))
    for symbol in ("SPY", "$SPX", "Spy"):
        c = _Client()
        se.scan_chains(c, symbol, D)
        assert len(c.windows) == 3, symbol


def test_a_stray_item_in_the_exclusion_list_neither_raises_nor_excludes(monkeypatch):
    _cfg(monkeypatch, wide=True, exclude=(7, None, 2.5, True, ["SPY"], "qqq"))
    c = _Client()
    se.scan_chains(c, "AAPL", D)
    assert c.windows == [(0, 45)]             # the junk excluded nothing
    c = _Client()
    se.scan_chains(c, "QQQ", D)
    assert len(c.windows) == 3                # and the real entry beside it still works


def test_an_exclusion_list_that_is_not_a_list_keeps_three_fetches(monkeypatch):
    """A bare string would otherwise be read letter by letter and exclude nothing."""
    monkeypatch.setattr(mdc, "section", lambda name: {
        "wide_fetch": True, "wide_fetch_exclude": "SPY"})
    c = _Client()
    se.scan_chains(c, "SPY", D)
    assert len(c.windows) == 3


def test_only_a_literal_true_switches_the_wide_fetch_on(monkeypatch):
    for value in ("true", 1, None):
        _cfg(monkeypatch, wide=value)
        c = _Client()
        se.scan_chains(c, "AAPL", D)
        assert len(c.windows) == 3, value


def test_the_shipped_setting_is_off():
    """Nothing has switched this on: the tracked file ships three fetches."""
    # Pins the SHIPPED value on purpose. A later rollout step changes the tracked
    # default in config/marketdata.toml; update this test in that same change.
    assert mdc.section("scan")["wide_fetch"] is False
    c = _Client()
    se.scan_chains(c, "AAPL", D)
    assert len(c.windows) == 3


# ── off is exactly what the scan did before ─────────────────────────────────

class _Recorder(_Client):
    """Records every request whole: positional args and every keyword."""

    def __init__(self):
        super().__init__()
        self.requests = []

    def get_option_chain(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return super().get_option_chain(*args, **kwargs)


def _day(n):
    return D + dt.timedelta(days=n)


def test_wide_fetch_off_sends_the_same_three_requests_as_before(monkeypatch):
    """The three requests ``_fetch_symbol_data`` made before ``scan_chains``
    existed, argument for argument and in the order it made them: the +20..+45
    IV window, the +5..+15 swing window, the 0..+4 window."""
    _cfg(monkeypatch, wide=False)
    c = _Recorder()
    se.scan_chains(c, "AAPL", D)
    assert c.requests == [
        (("AAPL",), {"contract_type": "ALL", "from_date": _day(20), "to_date": _day(45)}),
        (("AAPL",), {"contract_type": "ALL", "from_date": _day(5), "to_date": _day(15)}),
        (("AAPL",), {"contract_type": "ALL", "from_date": D, "to_date": _day(4)}),
    ]


def test_the_wide_request_differs_from_a_window_request_only_in_its_dates(monkeypatch):
    _cfg(monkeypatch, wide=True)
    c = _Recorder()
    se.scan_chains(c, "AAPL", D)
    assert c.requests == [
        (("AAPL",), {"contract_type": "ALL", "from_date": D, "to_date": _day(45)})]


def test_the_scan_windows_are_the_ones_the_scan_has_always_used():
    assert se.scan_windows(D) == {
        "zero": (D, _day(4)), "swing": (_day(5), _day(15)), "iv": (_day(20), _day(45))}


# ── a cut window equals the fetch it stands in for ──────────────────────────

def _schwab_answer(master, lo, hi):
    """What Schwab answers for the window ``[lo, hi]`` of ``master``. Written
    WITHOUT ``slice_chain``, so the comparison below is not the function against
    itself. An empty window comes back as Schwab's own: price 0.0, no contracts."""
    out = dict(master, status="SUCCESS")
    count = 0
    for side in ("callExpDateMap", "putExpDateMap"):
        kept = {}
        for key, strikes in master[side].items():
            if lo <= dt.date.fromisoformat(key.split(":")[0]) <= hi:
                kept[key] = strikes
                count += sum(len(c) for c in strikes.values())
        out[side] = kept
    out["numberOfContracts"] = count
    if count == 0:
        out["underlyingPrice"] = 0.0
    return out


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Schwab(_Client):
    """Serves each requested window of one master chain, as Schwab would."""

    def __init__(self, master):
        super().__init__()
        self.master = master

    def get_option_chain(self, symbol, contract_type=None, from_date=None,
                         to_date=None, **kw):
        self.windows.append(((from_date - D).days, (to_date - D).days))
        return _Response(_schwab_answer(self.master, from_date, to_date))


def _master(days):
    exp = {f"{_day(n).isoformat()}:{n}": {"100.0": [{"x": n}], "105.0": [{"x": -n}]}
           for n in days}
    return {"symbol": "AAPL", "underlyingPrice": 100.0,
            "callExpDateMap": dict(exp), "putExpDateMap": dict(exp)}


def _both_ways(monkeypatch, days):
    _cfg(monkeypatch, wide=False)
    three = _Schwab(_master(days))
    fetched = se.scan_chains(three, "AAPL", D)
    _cfg(monkeypatch, wide=True)
    one = _Schwab(_master(days))
    cut = se.scan_chains(one, "AAPL", D)
    assert len(three.windows) == 3 and one.windows == [(0, 45)]
    return fetched, cut


def test_cut_windows_equal_what_three_fetches_return(monkeypatch):
    fetched, cut = _both_ways(monkeypatch, [0, 2, 4, 7, 14, 21, 30, 44])
    assert cut == fetched
    assert cut["zero"]["numberOfContracts"] == 12    # vacuity: 3 exp x 2 strikes x 2 sides


def test_cut_windows_equal_three_fetches_when_a_window_lists_nothing(monkeypatch):
    """A monthlies-only name: nothing in 0..+4 or +5..+15. Schwab answers those
    two windows with price 0.0 and no contracts, and so must the cut."""
    fetched, cut = _both_ways(monkeypatch, [30])
    assert cut == fetched
    for name in ("zero", "swing"):
        assert cut[name]["callExpDateMap"] == {} and cut[name]["underlyingPrice"] == 0.0
        assert not se.chain_has_underlying(cut[name])
    assert se.chain_has_underlying(cut["iv"])


def test_cut_windows_equal_three_fetches_when_the_iv_window_lists_nothing(monkeypatch):
    fetched, cut = _both_ways(monkeypatch, [1, 7])
    assert cut == fetched
    assert cut["iv"]["callExpDateMap"] == {} and cut["iv"]["putExpDateMap"] == {}


# ── a failed wide fetch falls back to the three window fetches ──────────────
# The only bound on a wide chain's size is a hand-typed exclusion list, so a
# name that outgrows one request (a new daily-expiry listing, say) would
# otherwise lose its 0-DTE and swing buckets on every scan, silently. The rule
# is "never worse than before": the wide fetch costs extra calls only when it
# fails, and it says so once.

import logging  # noqa: E402

LISTED = [0, 2, 4, 7, 14, 21, 30, 44]


class _Requests(_Schwab):
    """``_Schwab`` that also records every request whole, and can refuse the
    wide (today..+45) one with an HTTP error while answering the rest."""

    def __init__(self, master, wide_status=200):
        super().__init__(master)
        self.requests = []
        self.wide_status = wide_status

    def get_option_chain(self, *args, **kwargs):
        self.requests.append((args, dict(kwargs)))
        response = super().get_option_chain(*args, **kwargs)
        if self.windows[-1] == (0, 45) and self.wide_status != 200:
            return _Response({"message": "upstream error"}, self.wide_status)
        return response


def _scanner_warnings(caplog):
    return [r for r in caplog.records
            if r.name == "scanner" and r.levelno >= logging.WARNING]


def _switch_off_run(monkeypatch, days=LISTED):
    _cfg(monkeypatch, wide=False)
    client = _Requests(_master(days))
    return client, se.scan_chains(client, "AAPL", D)


def test_a_failed_wide_fetch_is_followed_by_the_three_window_fetches(monkeypatch, caplog):
    off, _ = _switch_off_run(monkeypatch)
    _cfg(monkeypatch, wide=True)
    c = _Requests(_master(LISTED), wide_status=502)
    with caplog.at_level(logging.DEBUG, logger="scanner"):
        se.scan_chains(c, "AAPL", D)
    assert c.requests[0] == (("AAPL",), {"contract_type": "ALL", "from_date": D,
                                         "to_date": _day(45)})
    assert len(off.requests) == 3                  # vacuity: there is something to equal
    assert c.requests[1:] == off.requests          # same arguments, same order, as switch-off


def test_a_failed_wide_fetch_returns_what_the_three_window_fetches_returned(monkeypatch):
    _, fetched = _switch_off_run(monkeypatch)
    _cfg(monkeypatch, wide=True)
    got = se.scan_chains(_Requests(_master(LISTED), wide_status=502), "AAPL", D)
    assert got == fetched
    assert _dtes(got["zero"]) == [0, 2, 4] and _dtes(got["swing"]) == [7, 14]
    assert _dtes(got["iv"]) == [21, 30, 44]


def test_a_failed_wide_fetch_logs_exactly_one_warning_naming_the_symbol(monkeypatch, caplog):
    _cfg(monkeypatch, wide=True)
    with caplog.at_level(logging.DEBUG, logger="scanner"):
        se.scan_chains(_Requests(_master(LISTED), wide_status=502), "AAPL", D)
    warnings = _scanner_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    text = warnings[0].getMessage()
    assert "AAPL" in text and "wide" in text and "three" in text
    assert "scan.wide_fetch_exclude" in text


def test_the_warning_is_logged_once_even_when_the_fallback_fails_too(monkeypatch, caplog):
    _cfg(monkeypatch, wide=True)
    monkeypatch.setattr(se, "fetch_option_chain", lambda *a, **k: None)
    with caplog.at_level(logging.DEBUG, logger="scanner"):
        got = se.scan_chains(_Client(), "AAPL", D)
    assert got == {"iv": None, "swing": None, "zero": None}
    assert len(_scanner_warnings(caplog)) == 1


def test_an_empty_but_successful_wide_answer_is_not_a_failure(monkeypatch, caplog):
    """Nothing listed out to +45 days: Schwab answers 200 with empty maps. That
    is its real answer, so it is cut as usual - no fallback, no warning."""
    _cfg(monkeypatch, wide=True)
    c = _Requests(_master([]))
    with caplog.at_level(logging.DEBUG, logger="scanner"):
        got = se.scan_chains(c, "AAPL", D)
    assert c.windows == [(0, 45)]
    assert _scanner_warnings(caplog) == []
    for name in ("zero", "swing", "iv"):
        assert got[name]["callExpDateMap"] == {} and got[name]["putExpDateMap"] == {}
        assert got[name] is not None


def test_a_successful_wide_fetch_logs_no_warning(monkeypatch, caplog):
    _cfg(monkeypatch, wide=True)
    c = _Requests(_master(LISTED))
    with caplog.at_level(logging.DEBUG, logger="scanner"):
        se.scan_chains(c, "AAPL", D)
    assert c.windows == [(0, 45)] and _scanner_warnings(caplog) == []


def test_the_switch_off_path_never_warns_about_a_wide_fetch(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG, logger="scanner"):
        _switch_off_run(monkeypatch)
    assert _scanner_warnings(caplog) == []


# ── run_iv_analysis' own fallback fetch ─────────────────────────────────────
# Handed a +20..+45 chain with no expirations, ``run_iv_analysis`` re-asks for
# that window and then for 0..+60. It decides on ``callExpDateMap`` alone, so a
# cut window with nothing in it must set off exactly the fetches Schwab's own
# empty answer does.

import iv_analysis  # noqa: E402


def _real_today():
    """The date ``run_iv_analysis`` and ``run_full_scan`` read for themselves."""
    return dt.datetime.now(se.TZ).date()


class _IvClient(_Client):
    """Answers the 0..+60 fallback with a real ladder and everything narrower
    with Schwab's empty window."""

    def __init__(self, today):
        super().__init__()
        self.today = today

    def get_option_chain(self, symbol, contract_type=None, from_date=None,
                         to_date=None, **kw):
        window = ((from_date - self.today).days, (to_date - self.today).days)
        self.windows.append(window)
        if window != (0, 60):
            return _Response(_schwab_empty_window())
        key = f"{(self.today + dt.timedelta(days=30)).isoformat()}:30"
        ladder = {key: {"100.0": [{"volatility": 22.0}]}}
        return _Response({"underlyingPrice": 100.0, "status": "SUCCESS",
                          "callExpDateMap": ladder, "putExpDateMap": ladder})


def _iv_run(chain):
    client = _IvClient(_real_today())
    result = iv_analysis.run_iv_analysis(client, "AAPL", price=100.0,
                                         hist={"candles": []}, chain=chain)
    return client.windows, result


def test_a_cut_empty_iv_window_sets_off_the_same_fallback_as_schwabs_empty_answer():
    cut = se.slice_chain(_schwab_chain(), _day(50), _day(60))
    assert cut["callExpDateMap"] == {}                 # the window really is empty
    cut_windows, cut_result = _iv_run(cut)
    schwab_windows, schwab_result = _iv_run(_schwab_empty_window())
    assert cut_windows == schwab_windows == [(20, 45), (0, 60)]
    assert cut_result == schwab_result
    assert cut_result["current_iv"] == 22.0            # the fallback's chain was used


def test_a_failed_wide_fetch_sets_off_the_same_fallback_as_a_failed_iv_fetch(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(mdc, "section", lambda name: {"wide_fetch": True,
                                                "wide_fetch_exclude": []})
        m.setattr(se, "fetch_option_chain", lambda *a, **k: None)
        chain_iv = se.scan_chains(_Client(), "AAPL", D)["iv"]
    assert chain_iv is None
    windows, result = _iv_run(chain_iv)
    assert windows == [(20, 45), (0, 60)] and result["current_iv"] == 22.0


def test_a_cut_iv_window_with_expirations_needs_no_fallback():
    today = _real_today()
    key = f"{(today + dt.timedelta(days=30)).isoformat()}:30"
    ladder = {key: {"100.0": [{"volatility": 31.0}]}}
    wide = {"underlyingPrice": 100.0, "status": "SUCCESS",
            "callExpDateMap": ladder, "putExpDateMap": ladder}
    cut = se.slice_chain(wide, today + dt.timedelta(days=20),
                         today + dt.timedelta(days=45))
    windows, result = _iv_run(cut)
    assert windows == [] and result["current_iv"] == 31.0


# ── through run_full_scan ───────────────────────────────────────────────────

import pytest  # noqa: E402

from tests.test_scanner_engine import (  # noqa: E402,F401  (fake_client is a fixture)
    _FAKE_SYMBOLS, _chain_at, fake_client)

SYMBOLS = ["SPY", "QQQ"]


def _scan_master(spot, today, days):
    """One symbol's whole listed chain: a ``_chain_at`` ladder per expiration."""
    master = {"underlyingPrice": spot, "putExpDateMap": {}, "callExpDateMap": {}}
    for n in days:
        one = _chain_at(spot, (today + dt.timedelta(days=n)).isoformat(), n)
        master["putExpDateMap"].update(one["putExpDateMap"])
        master["callExpDateMap"].update(one["callExpDateMap"])
    return master


@pytest.fixture
def scan(fake_client, monkeypatch, tmp_path):  # noqa: F811
    """``run(wide, days)`` -> ``(results, {symbol: [window, ...]})``.

    ``fake_client`` with its chain endpoint swapped for one that serves each
    requested window of a master chain as Schwab would, and records the window.
    """
    monkeypatch.setattr(se, "IV_HISTORY_DB", tmp_path / "iv.db")
    today = _real_today()

    def run(wide, days, wide_status=200):
        _cfg(monkeypatch, wide=wide, exclude=())
        masters = {sym: _scan_master(spot, today, days)
                   for sym, (spot, _trend) in _FAKE_SYMBOLS.items()}
        calls = {sym: [] for sym in SYMBOLS}

        def get_option_chain(symbol, contract_type=None, from_date=None,
                             to_date=None, **kw):
            window = ((from_date - today).days, (to_date - today).days)
            calls[symbol].append(window)
            if window == (0, 45) and wide_status != 200:
                return _Response({"message": "upstream error"}, wide_status)
            return _Response(_schwab_answer(masters[symbol], from_date, to_date))

        fake_client.get_option_chain = get_option_chain
        return se.run_full_scan(fake_client, symbols=SYMBOLS), calls

    return run


def _identity(signals):
    """Which trades a list holds, leaving out everything that moves with the
    clock between two runs (scores, marks worked against time to expiry)."""
    keys = ("symbol", "type", "strategy", "expiration", "short_strike",
            "long_strike", "put_short", "put_long", "call_short", "call_long")
    return sorted(
        repr([s.get(k) for k in keys]
             + [(leg.get("strike"), leg.get("option_type"), leg.get("side"))
                for leg in (s.get("legs") or [])])
        for s in signals)


def test_a_scan_with_the_wide_fetch_off_makes_its_three_fetches_per_symbol(scan):
    results, calls = scan(False, [1, 7, 30])
    for symbol in SYMBOLS:
        assert sorted(calls[symbol]) == [(0, 4), (5, 15), (20, 45)], symbol
    assert results["signals_0dte"] and results["signals_swing"]


def test_a_scan_with_the_wide_fetch_on_makes_one_fetch_per_symbol(scan):
    results, calls = scan(True, [1, 7, 30])
    for symbol in SYMBOLS:
        assert calls[symbol] == [(0, 45)], symbol
    assert results["signals_0dte"] and results["signals_swing"]


def test_a_scan_finds_the_same_trades_either_way(scan):
    off, _ = scan(False, [1, 7, 30])
    on, _ = scan(True, [1, 7, 30])
    for key in ("signals_0dte", "signals_swing", "signals_directional"):
        assert _identity(on[key]) == _identity(off[key]), key
        assert off[key], key                           # vacuity: there were trades
    assert on["iv_data"] == off["iv_data"]
    for symbol in SYMBOLS:
        assert on["funnel"][symbol]["buckets"] == off["funnel"][symbol]["buckets"]


def test_a_scan_with_no_expiration_in_the_iv_window_falls_back_either_way(scan):
    """``run_iv_analysis`` re-asks for +20..+45 and then 0..+60 whether the
    empty IV chain was fetched or cut, and measures the same volatility."""
    off, off_calls = scan(False, [1, 7])
    on, on_calls = scan(True, [1, 7])
    for symbol in SYMBOLS:
        assert sorted(off_calls[symbol]) == [(0, 4), (0, 60), (5, 15), (20, 45), (20, 45)]
        assert sorted(on_calls[symbol]) == [(0, 45), (0, 60), (20, 45)]
        assert on["iv_data"][symbol]["current_iv"] is not None
    assert on["iv_data"] == off["iv_data"]


def test_a_scan_reports_an_empty_window_the_same_either_way(scan):
    """A name with nothing listed in 0..+4: the funnel's ``underlying_zero`` is
    what the page words its reason from, and it must not depend on whether the
    window was fetched or cut."""
    off, _ = scan(False, [7, 30])
    on, _ = scan(True, [7, 30])
    for symbol in SYMBOLS:
        assert on["funnel"][symbol]["buckets"]["0DTE"] == \
            off["funnel"][symbol]["buckets"]["0DTE"]
        assert on["funnel"][symbol]["buckets"]["0DTE"]["underlying_zero"] is True
        assert on["funnel"][symbol]["buckets"]["SWING"]["underlying_zero"] is False


def test_a_scan_whose_wide_fetches_fail_still_finds_the_same_trades(scan, caplog):
    """The symbol keeps its 0-DTE and swing buckets: one wasted request, then the
    three it made before, and one warning per symbol saying so."""
    off, _ = scan(False, [1, 7, 30])
    with caplog.at_level(logging.WARNING, logger="scanner"):
        failed, calls = scan(True, [1, 7, 30], wide_status=502)
    for symbol in SYMBOLS:
        assert calls[symbol][0] == (0, 45), symbol
        assert sorted(calls[symbol][1:]) == [(0, 4), (5, 15), (20, 45)], symbol
        named = [r for r in _scanner_warnings(caplog)
                 if "wide_fetch_exclude" in r.getMessage() and symbol in r.getMessage()]
        assert len(named) == 1, symbol
    for key in ("signals_0dte", "signals_swing", "signals_directional"):
        assert _identity(failed[key]) == _identity(off[key]), key
        assert off[key], key                           # vacuity: there were trades
    assert failed["iv_data"] == off["iv_data"]
