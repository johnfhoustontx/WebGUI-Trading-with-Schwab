"""The public Strategy Finder worker: every refusal, in order, and what it writes.

The scan itself (``handlers.finder_payload``) is stubbed: these tests are about
the gate in front of it. What reaches Schwab is the one thing that costs money,
so each refusal asserts the stub was NOT called.
"""
import ast
import datetime as dt
import pathlib
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import finder_public as fp
from services.options_svc import handlers
from shared import public_scan as ps
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

CT = ZoneInfo("America/Chicago")
OPEN = dt.datetime(2026, 9, 21, 10, 0, tzinfo=CT)       # a Monday, in session


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture
def scans(monkeypatch):
    """Record every scan the worker asks for, and answer with a real-shaped
    payload unless a test overrides ``scans.answer``."""
    calls = []

    class Stub:
        answer = {"signals": [{"type": "PCS", "score": 61}], "spot": 500.0,
                  "chain_missing": False, "no_expiries_in_range": False,
                  "symbol": None}

    def fake(bus, params, *, echo_args, ask_if_large, degrade_area="options.swing"):
        calls.append({"params": params, "echo": echo_args,
                      "ask_if_large": ask_if_large})
        return {**Stub.answer, "symbol": params["symbol"], "params": echo_args}

    monkeypatch.setattr(handlers, "finder_payload", fake)
    monkeypatch.setattr(fp, "_now", lambda: OPEN)
    Stub.calls = calls
    return Stub


def _cmd(symbol="SPY", age_s=0.0, now=OPEN):
    ts = (now - dt.timedelta(seconds=age_s)).astimezone(dt.timezone.utc).isoformat()
    return Command(type=ps.COMMAND_TYPE, args={"symbol": symbol}, ts=ts)


def _status(bus):
    env = bus.cache_get(ps.STATUS_KEY)
    return env.payload if env else None


def _last(bus, symbol):
    return _status(bus)["last"][symbol]["outcome"]


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_request_scans_with_the_public_pin_and_publishes_per_symbol(bus, scans):
    fp.handle(bus, _cmd("spy"))
    assert len(scans.calls) == 1
    call = scans.calls[0]
    assert call["ask_if_large"] is False          # never answer with the chooser
    pin = ps.scan_pin()
    for k, v in pin.items():
        assert call["params"][k] == v
    assert call["params"]["symbol"] == "SPY"
    assert set(call["params"]) == set(handlers._SWING_DEFAULTS)
    env = bus.cache_get(ps.result_key("SPY"))
    assert env.payload["signals"] == [{"type": "PCS", "score": 61}]
    assert env.payload["public"] is True and env.payload["scanned_at"]
    assert _last(bus, "SPY") == "scanned"
    assert _status(bus)["scans_today"] == 1


def test_the_result_key_expires(bus, scans):
    """Visitors choose the symbols, so a key without a TTL grows forever."""
    fp.handle(bus, _cmd("SPY"))
    ttl = bus._r.ttl(ps.result_key("SPY"))
    assert 0 < ttl <= ps.limits()["result_keep_hours"] * 3600


def test_it_never_writes_the_owners_slot_or_the_options_stream(bus, scans):
    fp.handle(bus, _cmd("SPY"))
    assert bus.cache_get(handlers.CACHE_SWING) is None
    assert bus._r.xlen("cmd:options") == 0


# ── the refusals, in order ──────────────────────────────────────────────────

def test_an_invalid_symbol_is_refused_without_a_scan(bus, scans):
    fp.handle(bus, _cmd("spy; flushall"))
    assert scans.calls == []
    assert _status(bus)["invalid_today"] == 1
    assert bus._r.keys("cache:options:swing_pub:*") == []


def test_a_request_that_waited_too_long_is_dropped(bus, scans):
    """Also the replay guard: a backlog replayed at startup is older than this."""
    fp.handle(bus, _cmd("SPY", age_s=ps.limits()["max_wait_sec"] + 5))
    assert scans.calls == []
    assert _last(bus, "SPY") == "expired"


def test_a_fresh_result_is_served_from_cache(bus, scans):
    fp.handle(bus, _cmd("SPY"))
    fp.handle(bus, _cmd("SPY"))
    assert len(scans.calls) == 1
    assert _last(bus, "SPY") == "cached"
    assert _status(bus)["scans_today"] == 1        # a cache hit costs nothing


def test_an_old_result_is_rescanned(bus, scans, monkeypatch):
    fp.handle(bus, _cmd("SPY"))
    later = OPEN + dt.timedelta(minutes=ps.limits()["result_ttl_min"] + 1)
    monkeypatch.setattr(fp, "_now", lambda: later)
    fp.handle(bus, _cmd("SPY", now=later))
    assert len(scans.calls) == 2


def test_a_symbol_whose_scan_just_failed_is_not_rerun_inside_the_minute(bus, scans):
    """A failed scan arms the short dedup window. (A no-options symbol is
    remembered far longer - see the negative-result test below.)"""
    scans.answer = {"signals": [], "spot": 500.0, "error": "ConnectionError"}
    fp.handle(bus, _cmd("SPY"))
    fp.handle(bus, _cmd("SPY"))
    assert len(scans.calls) == 1
    assert _last(bus, "SPY") == "duplicate"


def test_a_cache_hit_does_not_arm_the_duplicate_refusal(bus, scans):
    """A visitor asking twice for a symbol with a good result must see the
    result, never 'just checked, try again in a minute'."""
    fp.handle(bus, _cmd("SPY"))
    fp.handle(bus, _cmd("SPY"))
    fp.handle(bus, _cmd("SPY"))
    assert _last(bus, "SPY") == "cached"


def test_outside_the_window_nothing_is_scanned(bus, scans, monkeypatch):
    evening = dt.datetime(2026, 9, 21, 18, 0, tzinfo=CT)
    monkeypatch.setattr(fp, "_now", lambda: evening)
    fp.handle(bus, _cmd("SPY", now=evening))
    assert scans.calls == []
    assert _last(bus, "SPY") == "closed"


def test_a_weekend_is_closed(bus, scans, monkeypatch):
    saturday = dt.datetime(2026, 9, 19, 10, 0, tzinfo=CT)
    monkeypatch.setattr(fp, "_now", lambda: saturday)
    fp.handle(bus, _cmd("SPY", now=saturday))
    assert scans.calls == [] and _last(bus, "SPY") == "closed"


def test_the_daily_budget_refuses_once_spent(bus, scans, monkeypatch):
    monkeypatch.setattr(ps, "limits", lambda: {**ps.DEFAULTS["limits"],
                                               "daily_budget": 2})
    for sym in ("AAA", "BBB", "CCC"):
        fp.handle(bus, _cmd(sym))
    assert len(scans.calls) == 2
    assert _last(bus, "CCC") == "budget"
    assert _status(bus)["scans_today"] == 2


def test_the_budget_resets_on_a_new_day(bus, scans, monkeypatch):
    monkeypatch.setattr(ps, "limits", lambda: {**ps.DEFAULTS["limits"],
                                               "daily_budget": 1})
    fp.handle(bus, _cmd("AAA"))
    tomorrow = OPEN + dt.timedelta(days=1)
    monkeypatch.setattr(fp, "_now", lambda: tomorrow)
    fp.handle(bus, _cmd("BBB", now=tomorrow))
    assert len(scans.calls) == 2
    assert _status(bus)["scans_today"] == 1


def test_a_symbol_with_no_options_is_worded_and_not_published(bus, scans):
    scans.answer = {"signals": [], "spot": None, "chain_missing": True}
    fp.handle(bus, _cmd("ZZZZ"))
    assert _last(bus, "ZZZZ") == "no_options"
    assert bus.cache_get(ps.result_key("ZZZZ")) is None


def test_a_failed_scan_keeps_the_previous_good_result(bus, scans, monkeypatch):
    fp.handle(bus, _cmd("SPY"))
    later = OPEN + dt.timedelta(minutes=30)
    monkeypatch.setattr(fp, "_now", lambda: later)
    scans.answer = {"signals": [], "spot": None, "error": "ConnectionError"}
    fp.handle(bus, _cmd("SPY", now=later))
    assert _last(bus, "SPY") == "error"
    assert bus.cache_get(ps.result_key("SPY")).payload["signals"]


def test_a_scan_that_raises_is_an_error_outcome_and_clears_busy(bus, scans,
                                                               monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setattr(handlers, "finder_payload", boom)
    fp.handle(bus, _cmd("SPY"))
    st = _status(bus)
    assert st["last"]["SPY"]["outcome"] == "error" and st["busy"] is None


def test_every_recorded_outcome_is_one_the_page_can_word(bus, scans):
    fp.handle(bus, _cmd("SPY"))
    for rec in _status(bus)["last"].values():
        assert rec["outcome"] in ps.OUTCOMES


def test_the_status_view_is_bounded(bus, scans, monkeypatch):
    monkeypatch.setattr(ps, "limits", lambda: {**ps.DEFAULTS["limits"],
                                               "daily_budget": 10_000})
    monkeypatch.setattr(fp, "LAST_KEEP", 5)
    for i in range(12):
        fp.handle(bus, _cmd(f"S{i}"))
    assert len(_status(bus)["last"]) == 5


# ── wiring ──────────────────────────────────────────────────────────────────

def test_the_service_consumes_the_public_stream_on_its_own_loop():
    src = (pathlib.Path(handlers.__file__).parent / "app.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "make_app")
    kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert kw["extra_consumers"] == "((public_scan.STREAM, finder_public.handle),)"


def test_the_worker_writes_only_its_own_keys():
    """Source-level: every cache_set in the module targets a public key."""
    tree = ast.parse(pathlib.Path(fp.__file__).read_text(encoding="utf-8"))
    targets = {ast.unparse(n.args[0]) for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "cache_set"}
    assert targets == {"public_scan.STATUS_KEY", "public_scan.result_key(symbol)"}
    # Its one enqueue is the morning warm-up, onto its OWN stream only.
    enqueues = [ast.unparse(n.args[0]) for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "enqueue_command"]
    assert enqueues == ["public_scan.STREAM"]


# ── review follow-ups (2026-09-21) ──────────────────────────────────────────

def test_a_symbol_with_no_options_is_remembered_for_hours_not_a_minute(
        bus, scans, monkeypatch):
    """Otherwise one visitor typing junk tickers re-runs each one every minute
    and spends the whole day's budget."""
    scans.answer = {"signals": [], "spot": None, "chain_missing": True}
    fp.handle(bus, _cmd("ZZZZ"))
    later = OPEN + dt.timedelta(minutes=30)          # well past dedup_sec
    monkeypatch.setattr(fp, "_now", lambda: later)
    fp.handle(bus, _cmd("ZZZZ", now=later))
    assert len(scans.calls) == 1
    assert _last(bus, "ZZZZ") == "no_options"
    assert _status(bus)["scans_today"] == 1          # the repeat cost nothing
    much_later = OPEN + dt.timedelta(minutes=ps.limits()["negative_ttl_min"] + 1)
    monkeypatch.setattr(fp, "_now", lambda: much_later)
    fp.handle(bus, _cmd("ZZZZ", now=much_later))
    assert len(scans.calls) == 2


def test_a_failed_scan_may_be_retried_after_the_dedup_window(bus, scans,
                                                             monkeypatch):
    """An error is an outage, not an answer: it gets the short window only."""
    scans.answer = {"signals": [], "spot": 500.0, "error": "ConnectionError"}
    fp.handle(bus, _cmd("SPY"))
    later = OPEN + dt.timedelta(seconds=ps.limits()["dedup_sec"] + 1)
    monkeypatch.setattr(fp, "_now", lambda: later)
    fp.handle(bus, _cmd("SPY", now=later))
    assert len(scans.calls) == 2


def test_budget_and_busy_are_written_before_the_scan_runs(bus, monkeypatch):
    """A crash mid-scan must not refund the budget, and the page must see the
    scan running while it runs."""
    seen = {}

    def fake(b, params, **kw):
        seen.update(_status(b))
        return {"signals": [{"type": "PCS"}], "spot": 1.0}

    monkeypatch.setattr(handlers, "finder_payload", fake)
    monkeypatch.setattr(fp, "_now", lambda: OPEN)
    fp.handle(bus, _cmd("SPY"))
    assert seen["scans_today"] == 1
    assert seen["busy"]["symbol"] == "SPY" and seen["busy"]["since"]
    assert _status(bus)["busy"] is None


def test_a_busy_marker_left_by_a_crash_goes_stale(bus, scans, monkeypatch):
    bus.cache_set(ps.STATUS_KEY, {"date": OPEN.date().isoformat(),
                                  "scans_today": 3, "invalid_today": 0,
                                  "busy": {"symbol": "SPY",
                                           "since": (OPEN - dt.timedelta(
                                               seconds=fp.BUSY_STALE_SEC + 1)
                                           ).isoformat()},
                                  "last": {}})
    fp.handle(bus, _cmd("QQQ", age_s=ps.limits()["max_wait_sec"] + 5))
    assert _status(bus)["busy"] is None


def test_a_busy_marker_does_not_survive_the_day(bus, scans, monkeypatch):
    yesterday = OPEN - dt.timedelta(days=1)
    bus.cache_set(ps.STATUS_KEY, {"date": yesterday.date().isoformat(),
                                  "scans_today": 3, "invalid_today": 0,
                                  "busy": {"symbol": "SPY",
                                           "since": OPEN.isoformat()},
                                  "last": {}})
    fp.handle(bus, _cmd("QQQ", age_s=ps.limits()["max_wait_sec"] + 5))
    assert _status(bus)["busy"] is None


def test_a_future_dated_request_is_refused(bus, scans):
    """The replay guard reads the request's own stamp; a stamp from the future
    would otherwise never age out."""
    fp.handle(bus, _cmd("SPY", age_s=-(fp.FUTURE_SKEW_SEC + 60)))
    assert scans.calls == []
    assert _last(bus, "SPY") == "expired"


def test_a_little_clock_skew_is_tolerated(bus, scans):
    fp.handle(bus, _cmd("SPY", age_s=-2))
    assert len(scans.calls) == 1


def test_a_write_that_fails_after_the_scan_is_an_error_and_clears_busy(
        bus, scans, monkeypatch):
    """The realistic failure: the scan succeeds and publishing its result
    raises. ``finder_payload`` itself never raises."""
    real = bus.cache_set

    def flaky(key, payload, **kw):
        if key == ps.result_key("SPY"):
            raise ConnectionError("redis blip")
        return real(key, payload, **kw)

    monkeypatch.setattr(bus, "cache_set", flaky)
    fp.handle(bus, _cmd("SPY"))
    st = _status(bus)
    assert st["last"]["SPY"]["outcome"] == "error" and st["busy"] is None


def test_public_failures_are_counted_apart_from_the_owners(bus, monkeypatch):
    seen = {}

    def fake(b, params, *, echo_args, ask_if_large, degrade_area="options.swing"):
        seen["area"] = degrade_area
        return {"signals": [{"type": "PCS"}], "spot": 1.0}

    monkeypatch.setattr(handlers, "finder_payload", fake)
    monkeypatch.setattr(fp, "_now", lambda: OPEN)
    fp.handle(bus, _cmd("SPY"))
    assert seen["area"] == "options.finder_public"


# ── Phase 3: the row cap and the warm-up ────────────────────────────────────

def _sig(typ, score):
    return {"type": typ, "composite_score": score, "id": f"{typ}-{score}"}


def test_a_public_result_keeps_the_best_n_of_each_type(bus, scans, monkeypatch):
    scans.answer = {"signals": [_sig("PCS", s) for s in (50, 90, 70, 60)]
                    + [_sig("IC", 55)], "spot": 500.0, "not_shown": 3}
    monkeypatch.setattr(ps, "rows_per_type", lambda: 2)
    fp.handle(bus, _cmd("SPY"))
    p = bus.cache_get(ps.result_key("SPY")).payload
    assert sorted((s["type"], s["composite_score"]) for s in p["signals"]) == [
        ("IC", 55), ("PCS", 70), ("PCS", 90)]
    assert p["not_shown"] == 3 + 2          # the service's own count, plus ours


def test_the_trim_is_the_services_own_per_type_ranking():
    from services.options_svc import compute
    sigs = [_sig("PCS", s) for s in (50, 90, None, 70)]
    kept, dropped = compute._keep_best_per_type(sigs, 2)
    trimmed = fp.trim_for_public({"signals": sigs}, 2)
    assert trimmed["signals"] == kept and trimmed["not_shown"] == dropped


def test_the_warm_up_queues_its_symbols_on_the_public_stream_only(bus, monkeypatch):
    monkeypatch.setattr(ps, "warm_symbols", lambda: ["SPY", "QQQ"])
    assert fp.warm(bus) == 2
    cmds = bus.consume_commands(ps.STREAM, "g", "c", block_ms=None)
    assert [c.args["symbol"] for _i, c in cmds] == ["SPY", "QQQ"]
    assert bus._r.xlen("cmd:options") == 0


def test_a_warm_request_meets_the_same_rules_as_a_visitor(bus, scans, monkeypatch):
    """Queued, not scanned directly: outside the window it is refused like any
    other request, and spends nothing."""
    monkeypatch.setattr(ps, "warm_symbols", lambda: ["SPY"])
    fp.warm(bus)
    evening = dt.datetime(2026, 9, 21, 18, 0, tzinfo=CT)
    monkeypatch.setattr(fp, "_now", lambda: evening)
    for _i, cmd in bus.consume_commands(ps.STREAM, "g", "c", block_ms=None):
        fp.handle(bus, cmd.model_copy(update={"ts": evening.isoformat()}))
    assert scans.calls == [] and _last(bus, "SPY") == "closed"
