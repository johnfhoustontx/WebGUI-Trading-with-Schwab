"""A large chain answers with choices before any fetch (2026-09-14).

$SPX lists 56 expirations and its whole-chain scan takes about 40 s. With more
than LARGE_CHAIN_EXPIRIES in the requested range and no ``expiry_choice``, the
Strategy Finder's scan answers with the four choices and fetches NO chain. With a
choice, only that choice's expirations are fetched and built - plus, when the
choice leaves it out, the one expiration the whole scan would have read its ATM IV
from, which is fetched for the IV analysis and never becomes a candidate.

Design: docs/plans/2026-09-14-strategy-finder-large-chain-chooser-design.md.
"""
import datetime as dt
import types

import pytest

from services import _degrade
from services.options_svc import compute, handlers
from services.options_svc.tests.conftest import _whole_chain
from shared.bus import Bus

BANDS = (-0.2, -0.1, 0.1, 0.2, 0.1)
TODAY = dt.date.today()


def _d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


# Shaped like $SPX: weeklies every other day out to 43 days, standard monthlies
# (S) out to a LEAPS, quarterlies (Q), a month-end (M), and two far weeklies that
# keep the far monthlies from being neighbours in the listing. 34 rows.
_TYPED_DTES = ([("W", n) for n in range(1, 44, 2)]
               + [("S", n) for n in (10, 46, 74, 102, 137, 165, 400)]
               + [("Q", 56), ("Q", 120), ("M", 88), ("W", 150), ("W", 300)])
SPX_ROWS = sorted(((_d(n), t, n) for t, n in _TYPED_DTES), key=lambda r: r[0])
MONTHLY = [r[0] for r in SPX_ROWS if r[1] == "S"]
# The whole scan reads IV from the expiry nearest 30 DTE inside 7-60: 29 and 31
# tie, and the earlier wins.
IV_REFERENCE = _d(29)


@pytest.fixture
def large_env(monkeypatch):
    """``swing_scan`` over ``SPX_ROWS`` with a spy on every chain fetch.

    The spy answers each ``(from, to)`` with a real Black-Scholes chain holding
    exactly the listed expiries in that window, so the builders produce rows. Each
    expiry is priced at its own volatility, so the ATM IV names the expiry it
    was read from."""
    env = types.SimpleNamespace(rows=list(SPX_ROWS), fetches=[], listings=0,
                                plain_listings=0, histories=0, iv_chain=None)
    per_date = {}

    def _chain_for(date):
        if date not in per_date:
            n = (dt.date.fromisoformat(date) - TODAY).days
            price_iv = 0.20 + n * 0.0005
            per_date[date] = _whole_chain(dtes=(n,), lo=500.0, hi=580.0,
                                          price_iv=price_iv, delta_iv=price_iv / 1.3)
        return per_date[date]

    def _rows(api):
        env.listings += 1
        return env.rows

    def _plain(api):
        env.plain_listings += 1
        return [r[0] for r in env.rows]

    def _fetch(client, symbol, from_date=None, to_date=None):
        env.fetches.append((str(from_date), str(to_date)))
        dates = [r[0] for r in env.rows if str(from_date) <= r[0] <= str(to_date)]
        if not dates:
            return None
        return compute.merge_raw_chains([_chain_for(d) for d in dates])

    def _history(client, symbol):
        env.histories += 1
        return {"h": 1}

    def _iv(client, symbol, price=None, hist=None, chain=None):
        env.iv_chain = chain
        return {"iv_rank": 50.0, "expected_moves": {"daily": {"move_dollars": 5.0}}}

    monkeypatch.setattr(compute, "option_expiration_rows", _rows)
    monkeypatch.setattr(compute, "option_expirations", _plain)
    monkeypatch.setattr(compute.se, "fetch_option_chain", _fetch)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": 540.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", _history)
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda hist: {"trend": "NEUTRAL", "rsi14": 50,
                                      "price": 540.0, "sma20": 540.0})
    monkeypatch.setattr(compute, "run_iv_analysis", _iv)
    monkeypatch.setattr(compute.se, "_is_options_market_open", lambda: False)
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 0.0)
    monkeypatch.setattr(compute, "SWING_EXCLUDED_GRADES", ())
    return env


def _scan(dte_max=None, families=("DIRECTIONAL",), **kw):
    kw.setdefault("ask_if_large", True)
    return compute.swing_scan("$SPX", 0, dte_max, *BANDS, families=families,
                              every_expiry=True, earnings_mode="flag", payoff=False, **kw)


def _fetched_dates(env):
    """Every listed expiry the spy was asked for, in listing order."""
    return [r[0] for r in env.rows
            if any(f <= r[0] <= t for f, t in env.fetches)]


def _signal_expiries(out):
    return {e for s in out["signals"]
            for e in [s.get("expiration")] + [l.get("expiration") for l in s.get("legs") or []]
            if e}


# ── the chooser answer ───────────────────────────────────────────────────────

def test_a_large_range_with_no_choice_asks_and_fetches_no_chain(large_env):
    out = _scan()
    assert out["needs_choice"] is True
    assert out["choices"] == compute.choice_summary(SPX_ROWS, 0, None)
    assert out["expiration_count"] == len(SPX_ROWS) == 34
    assert large_env.fetches == []                  # no chain, not even a run
    assert large_env.histories == 0 and large_env.iv_chain is None
    assert out["signals"] == [] and out["spot"] == 540.0
    # Nothing was attempted, so nothing failed: 0, and the page prints no
    # failed-expirations line on a falsy count.
    assert out["expiries_failed"] == 0
    assert out["expiry_choice"] is None and out["expirations_scanned"] is None
    assert out["chain_missing"] is False and out["no_expiries_in_range"] is False
    assert large_env.listings == 1 and large_env.plain_listings == 0


@pytest.mark.parametrize("n_rows, asks", [(30, False), (31, True)])
def test_the_threshold_is_more_than_thirty_in_range(large_env, n_rows, asks):
    large_env.rows = SPX_ROWS[:n_rows]
    out = _scan()
    assert out["needs_choice"] is asks
    assert out["expiration_count"] == n_rows
    assert bool(large_env.fetches) is not asks


def test_the_count_is_taken_inside_the_requested_range(large_env):
    """A dte_max that leaves 30 or fewer in range scans straight away, however
    many the symbol lists beyond it."""
    out = _scan(dte_max=60)
    in_range = compute.rows_in_range(SPX_ROWS, 0, 60)
    assert len(in_range) <= compute.LARGE_CHAIN_EXPIRIES < len(SPX_ROWS)
    assert out["needs_choice"] is False and out["expiration_count"] == len(in_range)


def test_the_income_window_never_asks(large_env):
    """ask_if_large=False on a 34-expiry range scans everything, as today."""
    out = _scan(ask_if_large=False)
    assert out["needs_choice"] is False and out["choices"] is None
    assert _fetched_dates(large_env) == [r[0] for r in SPX_ROWS]
    assert out["expiry_choice"] is None and out["expirations_scanned"] is None


def test_a_choice_without_ask_if_large_is_ignored(large_env):
    out = _scan(ask_if_large=False, expiry_choice="monthly")
    assert _fetched_dates(large_env) == [r[0] for r in SPX_ROWS]
    assert out["expiry_choice"] is None and out["choices"] is None


# ── a choice applied ─────────────────────────────────────────────────────────

def test_monthly_fetches_each_monthly_as_its_own_run_plus_the_iv_reference(large_env):
    out = _scan(families=("DIRECTIONAL", "VERTICAL"), expiry_choice="monthly")
    # Not neighbours in the listing, so each monthly is a one-date run; the IV
    # reference (a 29-DTE weekly) is fetched too.
    assert sorted(large_env.fetches) == sorted((d, d) for d in MONTHLY + [IV_REFERENCE])
    assert out["expiry_choice"] == "monthly"
    assert out["expiration_count"] == 34 and out["expirations_scanned"] == len(MONTHLY)
    assert out["choices"] == compute.choice_summary(SPX_ROWS, 0, None)
    assert out["needs_choice"] is False
    assert out["signals"], "the fixture must build rows or the subset check is vacuous"
    assert _signal_expiries(out) <= set(MONTHLY)


def test_the_iv_reference_reaches_the_iv_analysis_and_never_a_candidate(large_env):
    out = _scan(families=("DIRECTIONAL", "VERTICAL"), expiry_choice="monthly")
    iv_expiries = {k.split(":")[0] for k in large_env.iv_chain["callExpDateMap"]}
    assert IV_REFERENCE in iv_expiries
    assert IV_REFERENCE not in _signal_expiries(out)


def test_the_iv_reference_is_the_one_the_whole_scan_would_read(large_env):
    """The IV input must not change with the choice: the same expiry the
    everything scan hands extract_atm_iv, nearest 30 DTE inside 7-60."""
    import iv_analysis

    _scan(ask_if_large=False)
    whole = iv_analysis.extract_atm_iv(large_env.iv_chain)
    for choice in ("next_30", "next_90", "monthly", "all"):
        large_env.fetches.clear()
        _scan(expiry_choice=choice)
        assert iv_analysis.extract_atm_iv(large_env.iv_chain) == whole, choice


def test_next_30_fetches_contiguous_runs_of_at_most_eight(large_env):
    out = _scan(expiry_choice="next_30")
    wanted = compute.choice_dates(SPX_ROWS, "next_30", 0, None)
    listing = [r[0] for r in SPX_ROWS]
    assert _fetched_dates(large_env) == wanted       # the reference is inside it
    for f, t in large_env.fetches:
        run = listing[listing.index(f):listing.index(t) + 1]
        assert 1 <= len(run) <= compute.SCAN_RUN_EXPIRIES and set(run) <= set(wanted)
    assert len(large_env.fetches) == -(-len(wanted) // compute.SCAN_RUN_EXPIRIES)
    assert out["expirations_scanned"] == len(wanted)


def test_a_choice_that_keeps_nothing_answers_without_a_chain(large_env):
    large_env.rows = [r for r in SPX_ROWS if r[1] != "S"]          # 27 ...
    large_env.rows += [(_d(n), "W", n) for n in (200, 210, 220, 230)]  # ... 31
    out = _scan(expiry_choice="monthly")
    assert large_env.fetches == [] and out["signals"] == []
    assert out["no_expiries_in_range"] is True and out["needs_choice"] is False
    assert out["expiry_choice"] == "monthly" and out["expirations_scanned"] == 0
    assert out["expiration_count"] == 31 and out["choices"][2]["count"] == 0


def test_a_choice_on_a_small_range_is_ignored(large_env):
    large_env.rows = SPX_ROWS[:20]
    out = _scan(expiry_choice="monthly")
    assert _fetched_dates(large_env) == [r[0] for r in SPX_ROWS[:20]]
    assert out["expiry_choice"] is None and out["expirations_scanned"] is None
    assert out["choices"] is None and out["expiration_count"] == 20


@pytest.mark.parametrize("choice", [None, "monthly", "next_30"])
def test_one_expiration_list_per_scan(large_env, choice):
    large_env.rows = SPX_ROWS[:20] if choice is None else SPX_ROWS
    _scan(expiry_choice=choice)
    assert large_env.listings == 1 and large_env.plain_listings == 0


# ── refusals and the degraded fallback ───────────────────────────────────────

@pytest.mark.parametrize("bad", ["weekly", "", 30, True])
def test_an_unknown_choice_is_refused_before_anything_is_read(large_env, bad):
    with pytest.raises(ValueError):
        _scan(expiry_choice=bad)
    assert large_env.listings == 0 and large_env.fetches == []


@pytest.mark.parametrize("bad", [None, 1, "yes"])
def test_ask_if_large_must_be_a_bool(large_env, bad):
    with pytest.raises(ValueError):
        _scan(ask_if_large=bad)


def test_no_expiration_list_never_asks(large_env, monkeypatch):
    _degrade.reset()
    large_env.rows = []
    monkeypatch.setattr(compute, "option_expirations", lambda api: [])
    out = _scan()
    assert out["needs_choice"] is False and out["expiration_count"] is None
    assert out["expiry_choice"] is None and out["choices"] is None
    # The bounded single fetch, whose count was never taken.
    assert large_env.fetches == [(TODAY.isoformat(),
                                  (TODAY + dt.timedelta(days=compute._FALLBACK_MAX_DTE
                                                        + 2)).isoformat())]
    assert out["expiries_failed"] is None
    assert _degrade.counts().get("options.scan_expirations", 0) >= 1


def test_a_listing_that_raises_is_a_degrade_and_never_asks(large_env, monkeypatch):
    _degrade.reset()

    def _boom(api):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(compute, "option_expiration_rows", _boom)
    monkeypatch.setattr(compute, "option_expirations", lambda api: [])
    out = _scan()
    assert out["needs_choice"] is False and out["expiration_count"] is None
    assert _degrade.counts().get("options.scan_expirations", 0) >= 1


def test_the_conftest_stub_keeps_the_typed_listing_off_the_proxy():
    assert compute.option_expiration_rows("SPY") == []


# ── fetch_scan_chain with rows handed in ─────────────────────────────────────

def test_fetch_with_rows_does_not_list_again(large_env):
    chain, failed = compute.fetch_scan_chain("$SPX", None, rows=SPX_ROWS)
    assert large_env.listings == 0 and large_env.plain_listings == 0
    assert _fetched_dates(large_env) == [r[0] for r in SPX_ROWS] and failed == 0


def test_fetch_with_dates_splits_a_long_consecutive_run(large_env):
    dates = [r[0] for r in SPX_ROWS[:20]]
    compute.fetch_scan_chain("$SPX", None, rows=SPX_ROWS, dates=dates)
    listing = [r[0] for r in SPX_ROWS]
    assert sorted(large_env.fetches) == sorted(
        (dates[i], dates[min(i + 7, 19)]) for i in range(0, 20, 8))
    assert all(listing.index(t) - listing.index(f) < 8 for f, t in large_env.fetches)


def test_fetch_with_dates_counts_a_failed_run_in_expiries(large_env, monkeypatch):
    real = compute.se.fetch_option_chain

    def _fail_first(client, symbol, from_date=None, to_date=None):
        out = real(client, symbol, from_date=from_date, to_date=to_date)
        return None if str(from_date) == MONTHLY[0] else out
    monkeypatch.setattr(compute.se, "fetch_option_chain", _fail_first)
    chain, failed = compute.fetch_scan_chain("$SPX", None, rows=SPX_ROWS, dates=MONTHLY)
    assert failed == 1 and len(chain["callExpDateMap"]) == len(MONTHLY) - 1


# ── the handler ──────────────────────────────────────────────────────────────

@pytest.fixture
def handler_seam(monkeypatch):
    seen = {}

    def _spy(**kwargs):
        seen.update(kwargs)
        return {"signals": [], "view": {}, "spot": 6600.0, "expiries_failed": 0,
                "needs_choice": True, "expiration_count": 56,
                "expirations_scanned": None, "expiry_choice": None,
                "choices": [{"key": "all", "label": "Everything", "count": 56,
                             "est_seconds": 42}]}

    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan", _spy)
    return seen


def test_the_handler_asks_and_threads_the_choice(handler_seam):
    handlers.swing_scan(Bus(fake=True), {"symbol": "$SPX", "expiry_choice": "monthly"})
    assert handler_seam["ask_if_large"] is True
    assert handler_seam["expiry_choice"] == "monthly"


def test_the_handler_defaults_the_choice_to_None(handler_seam):
    handlers.swing_scan(Bus(fake=True), {"symbol": "$SPX"})
    assert handler_seam["expiry_choice"] is None


def test_the_handler_publishes_the_chooser_keys(handler_seam):
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "$SPX"})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["needs_choice"] is True and p["expiration_count"] == 56
    assert p["expirations_scanned"] is None and p["expiry_choice"] is None
    assert p["choices"] == [{"key": "all", "label": "Everything", "count": 56,
                             "est_seconds": 42}]


def test_the_handler_defaults_the_chooser_keys_when_compute_omits_them(monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan", lambda **k: {"signals": [], "view": {}})
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "SPY"})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["needs_choice"] is False and p["choices"] is None
    assert p["expiration_count"] is None and p["expirations_scanned"] is None
    assert p["expiry_choice"] is None


def test_an_unknown_choice_is_an_error_answer(large_env, monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "$SPX", "expiry_choice": "weekly"})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["error"] == "ValueError" and p["signals"] == []
    assert p["needs_choice"] is False and p["choices"] is None
    assert p["expiry_choice"] is None
    assert large_env.listings == 0 and large_env.fetches == []


def test_income_scan_passes_neither_keyword(monkeypatch):
    seen = {}

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        return {"signals": [], "view": {}}

    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan", _spy)
    compute.income_scan("AAPL")
    assert seen and "expiry_choice" not in seen and "ask_if_large" not in seen
