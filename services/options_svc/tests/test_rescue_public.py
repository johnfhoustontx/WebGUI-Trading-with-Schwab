"""The public Rescue worker: each refusal, in order, and what it writes.

The three calls that reach Schwab - the Calculator's lazy chain load, the
one-expiration fetch, and the rescue engine - are stubbed and recorded. Each
refusal asserts none of them ran: a public request that costs nothing is the
whole point of the gate.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute
from services.options_svc import rescue_public as rp
from shared import public_rescue as pr
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

CT = ZoneInfo("America/Chicago")
OPEN = dt.datetime(2026, 9, 21, 10, 0, tzinfo=CT)        # a Monday, in session
CLOSED = dt.datetime(2026, 9, 21, 16, 30, tzinfo=CT)
NEAR, MID, FAR = "2026-10-02", "2026-10-09", "2027-01-15"
LISTED = [NEAR, MID, "2026-10-16", FAR]


def _chain(expiries):
    """A thinned chain carrying QUOTES, so a test can prove none are published."""
    maps = {f"{e}:9": {f"{k:.1f}": [{"bid": 1.0, "ask": 1.2, "mark": 1.1,
                                     "delta": -0.3}]
                       for k in (495.0, 500.0, 505.0)} for e in expiries}
    return {"callExpDateMap": dict(maps), "putExpDateMap": dict(maps)}


@pytest.fixture
def bus():
    reset_fake_bus()
    rp.reset_memory()
    return Bus(fake=True)


@pytest.fixture
def schwab(monkeypatch):
    calls = []

    class Stub:
        now = OPEN
        expirations = list(LISTED)
        advisory = {"position_id": "adhoc", "source": "adhoc", "symbol": "SPY",
                    "candidates": [{"action": "close", "apply_kind": "execute",
                                    "label": "Close now"}],
                    "apply_result": {"ok": True}}
        fetch_fails = False

    def load(symbol, lazy=False, expiries=None):
        calls.append(("load", symbol, tuple(expiries or ())))
        loaded = [NEAR, MID] + [e for e in (expiries or []) if e in Stub.expirations]
        return {"symbol": symbol, "api": symbol, "price": 502.37,
                "expirations": list(Stub.expirations),
                "chain": _chain(loaded) if Stub.expirations else None}

    def fetch(api, runs):
        calls.append(("fetch", api, tuple(tuple(r) for r in runs)))
        return None if Stub.fetch_fails else _chain(runs[0])

    def advise(spec):
        calls.append(("compute", spec["symbol"]))
        return dict(Stub.advisory)

    monkeypatch.setattr(compute, "calc_load_symbol", load)
    monkeypatch.setattr(compute, "_fetch_thin_runs", fetch)
    monkeypatch.setattr(compute, "compute_rescue_adhoc", advise)
    monkeypatch.setattr(rp, "_now", lambda: Stub.now)
    Stub.calls = calls
    return Stub


def _ts(age_s=0.0, now=OPEN):
    return (now - dt.timedelta(seconds=age_s)).astimezone(dt.timezone.utc).isoformat()


def _ladder_cmd(symbol="SPY", expiry=None, age_s=0.0, now=OPEN):
    args = {"symbol": symbol}
    if expiry is not None:
        args["expiry"] = expiry
    return Command(type=pr.LADDER_TYPE, args=args, ts=_ts(age_s, now))


def _spec(**over):
    spec = {"symbol": "SPY", "strategy": "PCS", "short_strike": 500.0,
            "long_strike": 495.0, "expiration": NEAR, "quantity": 1,
            "entry_credit": 1.2}
    spec.update(over)
    return spec


def _compute_cmd(spec=None, age_s=0.0, now=OPEN):
    return Command(type=pr.COMPUTE_TYPE, args={"spec": spec or _spec()},
                   ts=_ts(age_s, now))


def _answer(bus, key):
    env = bus.cache_get(pr.cache_key(pr.answer_view(key)))
    return env.payload["outcome"] if env else None


def _ladder(bus, symbol="SPY"):
    env = bus.cache_get(pr.cache_key(pr.ladder_view(symbol)))
    return env.payload if env else None


def _status(bus):
    env = bus.cache_get(pr.STATUS_KEY)
    return env.payload if env else None


def _key(spec=None):
    return pr.spec_key(pr.clean_spec(spec or _spec(), OPEN.date()))


# ── the strikes list ────────────────────────────────────────────────────────

def test_a_ladder_publishes_strikes_and_no_quotes(bus, schwab):
    rp.handle(bus, _ladder_cmd("spy"))
    ladder = _ladder(bus)
    assert ladder["expirations"] == LISTED
    assert ladder["strikes"][NEAR] == {"call": [495.0, 500.0, 505.0],
                                       "put": [495.0, 500.0, 505.0]}
    assert ladder["spot"] == 502.37
    text = repr(ladder)
    for quote in ("bid", "ask", "mark", "delta"):
        assert quote not in text, f"the public strikes list carries {quote}"
    assert _answer(bus, pr.ladder_key("SPY")) == "done"


def test_a_fresh_ladder_is_served_with_no_call(bus, schwab):
    rp.handle(bus, _ladder_cmd())
    schwab.calls.clear()
    rp.handle(bus, _ladder_cmd())
    assert schwab.calls == []
    assert _answer(bus, pr.ladder_key("SPY")) == "cached"


def test_one_more_expiration_is_one_fetch_merged_into_the_list(bus, schwab):
    rp.handle(bus, _ladder_cmd())
    first = _ladder(bus)["loaded_at"]
    schwab.calls.clear()
    rp.handle(bus, _ladder_cmd(expiry=FAR))
    assert schwab.calls == [("fetch", "SPY", ((FAR,),))]
    ladder = _ladder(bus)
    assert set(ladder["strikes"]) == {NEAR, MID, FAR}
    assert ladder["loaded_at"] == first, "a merge made the expirations look newer"
    assert _answer(bus, pr.ladder_key("SPY", FAR)) == "done"


def test_an_unlisted_expiration_is_refused_from_the_list_held(bus, schwab):
    rp.handle(bus, _ladder_cmd())
    schwab.calls.clear()
    rp.handle(bus, _ladder_cmd(expiry="2026-10-05"))
    assert schwab.calls == []
    assert _answer(bus, pr.ladder_key("SPY", "2026-10-05")) == "not_listed"


def test_a_failed_expiration_fetch_answers_error_and_keeps_the_list(bus, schwab):
    rp.handle(bus, _ladder_cmd())
    schwab.fetch_fails = True
    rp.handle(bus, _ladder_cmd(expiry=FAR))
    assert _answer(bus, pr.ladder_key("SPY", FAR)) == "error"
    assert FAR not in _ladder(bus)["strikes"]


def test_a_symbol_with_no_options_is_remembered(bus, schwab):
    schwab.expirations = []
    rp.handle(bus, _ladder_cmd("ZZZZ"))
    assert _answer(bus, pr.ladder_key("ZZZZ")) == "no_options"
    schwab.calls.clear()
    rp.handle(bus, _ladder_cmd("ZZZZ", expiry=NEAR))
    assert schwab.calls == [], "a junk ticker spent a second load"
    assert _answer(bus, pr.ladder_key("ZZZZ", NEAR)) == "no_options"


def test_the_ladder_budget_is_its_own(bus, schwab, monkeypatch):
    monkeypatch.setattr(pr, "load", lambda: {"limits": {"ladder_budget": 1}})
    rp.handle(bus, _ladder_cmd("SPY"))
    schwab.calls.clear()
    rp.handle(bus, _ladder_cmd("QQQ"))
    assert schwab.calls == []
    assert _answer(bus, pr.ladder_key("QQQ")) == "budget"
    assert _status(bus)["computes_left"] == pr.DEFAULTS["limits"]["daily_budget"]


# ── the rescue menu ─────────────────────────────────────────────────────────

def test_a_compute_publishes_an_advisory_only_menu_under_the_trade_hash(bus, schwab):
    rp.handle(bus, _compute_cmd(_spec(symbol="spy", junk=1)))
    assert schwab.calls == [("compute", "SPY")]
    env = bus.cache_get(pr.cache_key(pr.result_view(_key())))
    adv = env.payload
    assert [c["apply_kind"] for c in adv["candidates"]] == ["advisory"]
    assert "apply_result" not in adv
    assert adv["public"] is True and adv["computed_at"]
    assert _answer(bus, _key()) == "done"
    assert bus.cache_get("cache:options:rescue:adhoc") is None, \
        "a public compute wrote the owner's slot"


def test_a_fresh_menu_is_served_with_no_call(bus, schwab):
    rp.handle(bus, _compute_cmd())
    schwab.calls.clear()
    rp.handle(bus, _compute_cmd())
    assert schwab.calls == []
    assert _answer(bus, _key()) == "cached"


def test_a_repeat_after_an_error_is_deduplicated(bus, schwab, monkeypatch):
    def boom(spec):
        schwab.calls.append(("compute", spec["symbol"]))
        raise RuntimeError("engine down")
    monkeypatch.setattr(compute, "compute_rescue_adhoc", boom)
    rp.handle(bus, _compute_cmd())
    assert _answer(bus, _key()) == "error"
    schwab.calls.clear()
    rp.handle(bus, _compute_cmd())
    assert schwab.calls == []
    assert _answer(bus, _key()) == "duplicate"


def test_a_compute_on_an_unlisted_expiration_is_refused_from_the_list(bus, schwab):
    rp.handle(bus, _ladder_cmd())
    schwab.calls.clear()
    spec = _spec(expiration="2026-10-05")
    rp.handle(bus, _compute_cmd(spec))
    assert schwab.calls == []
    assert _answer(bus, _key(spec)) == "not_listed"


def test_the_compute_budget(bus, schwab, monkeypatch):
    monkeypatch.setattr(pr, "load", lambda: {"limits": {"daily_budget": 1}})
    rp.handle(bus, _compute_cmd())
    schwab.calls.clear()
    other = _spec(entry_credit=1.3)
    rp.handle(bus, _compute_cmd(other))
    assert schwab.calls == []
    assert _answer(bus, _key(other)) == "budget"
    assert _status(bus)["computes_left"] == 0


# ── refusals shared by both ─────────────────────────────────────────────────

@pytest.mark.parametrize("make, key", [
    (lambda **k: _ladder_cmd(**k), lambda: pr.ladder_key("SPY")),
    (lambda **k: _compute_cmd(**k), lambda: _key()),
])
def test_outside_the_window_nothing_runs(bus, schwab, make, key):
    schwab.now = CLOSED
    rp.handle(bus, make(now=CLOSED))
    assert schwab.calls == []
    assert _answer(bus, key()) == "closed"


@pytest.mark.parametrize("age", [10_000, -3600])
def test_an_old_or_future_request_expires_unrun(bus, schwab, age):
    rp.handle(bus, _compute_cmd(age_s=age))
    rp.handle(bus, _ladder_cmd(age_s=age))
    assert schwab.calls == []
    assert _answer(bus, _key()) == "expired"
    assert _answer(bus, pr.ladder_key("SPY")) == "expired"


@pytest.mark.parametrize("cmd", [
    Command(type=pr.COMPUTE_TYPE, args={"spec": _spec(short_strike=float("nan"))}),
    Command(type=pr.COMPUTE_TYPE, args={"spec": "x"}),
    Command(type=pr.LADDER_TYPE, args={"symbol": "../x"}),
    Command(type=pr.LADDER_TYPE, args={"symbol": "SPY", "expiry": "soon"}),
    Command(type="rescue_apply", args={"position_id": 1}),
])
def test_an_invalid_request_is_counted_and_runs_nothing(bus, schwab, cmd):
    rp.handle(bus, cmd)
    assert schwab.calls == []
    assert _status(bus)["invalid_today"] == 1


def test_the_status_view_holds_counts_and_nothing_about_requests(bus, schwab):
    rp.handle(bus, _ladder_cmd("SPY"))
    rp.handle(bus, _compute_cmd())
    status = _status(bus)
    assert status["computes_today"] == 1 and status["ladders_today"] == 1
    assert status["busy"] is None
    assert "SPY" not in repr(status) and "500" not in repr(status)


def test_a_crash_in_the_worker_never_raises_and_clears_busy(bus, schwab, monkeypatch):
    monkeypatch.setattr(rp, "_handle_compute",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    rp.handle(bus, _compute_cmd())          # must not raise
    status = _status(bus)
    assert status is None or status.get("busy") is None


def test_the_worker_is_registered_on_its_own_stream():
    from services.options_svc import app as app_mod
    import inspect
    src = inspect.getsource(app_mod)
    assert "(public_rescue.STREAM, rescue_public.handle)" in src
